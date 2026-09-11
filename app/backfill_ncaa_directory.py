"""One-off backfill of NCAA directory data (orgid, athletic_url,
website_url, division, is_private) for every team already in the `teams`
table, one division at a time.

Source: https://web3.ncaa.org/directory/api/directory/memberList?type=12&division=I&sportCode=MSO
(a public, unauthenticated bulk JSON endpoint), first inspected 2026-09-10.
It returned all 213 current D1 men's soccer member schools in one response;
swapping division=I for division=III returns D3's ~100+ in the same shape
(see config.NCAA_DIRECTORY_DIVISIONS). This is a point-in-time snapshot --
directory membership can change year to year (conference realignment,
new/departing programs), so re-run this periodically (with --force to
refresh already-matched rows) rather than treating it as a one-time truth.

Matching `teams.name_full` against the directory's `nameOfficial` got a
213/213 match for D1 when this was prototyped, using the
normalize+exact-first algorithm below. The one real risk found: naive
normalization can make two different schools collide (e.g. "Boston College"
and "Boston University" both reduce to "Boston" once generic words are
stripped) -- build_crosswalk never guesses on a collision, it reports it as
ambiguous instead.

Note a D3 run only has something to match against once D3 games have
actually been synced (config.ENABLED_DIVISIONS) and populated `teams` rows
via upsert_game/upsert_team_basic -- run against a D1-only database, every
D3 directory entry just comes back "unmatched_directory" (harmless, but not
useful yet).

This also runs two log-only sanity-check reports (no files are edited):
- Compares the directory's state per school against app/data/team_states.json,
  so any drift/gaps can be copy-pasted in by hand.
- For a D1 run only: flags any team the app currently treats as D1
  (reference_data.is_d1) that the directory doesn't list, as a possible
  app/data/non_d1.json gap.
"""

import argparse
import logging
import re

from . import config, db, ncaa_directory_client, reference_data

log = logging.getLogger("soccer-tracker.backfill_ncaa_directory")

_GENERIC_WORDS_RE = re.compile(r"\b(university of|university|college|the)\b")
_PUNCT_RE = re.compile(r"[.,'’]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation and generic institution words, collapse
    whitespace -- e.g. 'University of Akron' -> 'akron', 'Boston College'
    -> 'boston'. Aggressive on purpose (see module docstring for the
    collision this can cause, which build_crosswalk guards against)."""
    n = name.lower()
    n = _PUNCT_RE.sub("", n)
    n = _GENERIC_WORDS_RE.sub("", n)
    n = _WHITESPACE_RE.sub(" ", n).strip()
    return n


def build_crosswalk(local_teams: list, directory_entries: list[dict]) -> dict:
    """Match local `teams` rows (dicts/Rows with `seo`, `name_full`) to
    directory entries (dicts with `orgId`, `nameOfficial`) by name.

    Returns {"matched": {seo: orgid}, "ambiguous": [(seo, [orgIds])],
    "unmatched_local": [seo], "unmatched_directory": [orgId]}.

    Exact match (case-insensitive) is tried first. Anything left is matched
    by normalize_name(), but ONLY when a normalized key maps to exactly one
    directory entry -- a key shared by two or more directory entries is
    left unresolved as "ambiguous" rather than guessed at.
    """
    remaining_directory = {e["orgId"]: e for e in directory_entries}
    exact_by_lower = {}
    for orgid, e in remaining_directory.items():
        exact_by_lower.setdefault(e["nameOfficial"].lower(), []).append(orgid)

    matched: dict[str, int] = {}
    ambiguous: list[tuple[str, list[int]]] = []
    unmatched_local: list[str] = []

    remaining_local = []
    for t in local_teams:
        name_full = t["name_full"]
        if not name_full:
            unmatched_local.append(t["seo"])
            continue
        candidates = exact_by_lower.get(name_full.lower())
        if candidates and len(candidates) == 1:
            orgid = candidates[0]
            matched[t["seo"]] = orgid
            remaining_directory.pop(orgid, None)
        else:
            remaining_local.append(t)

    # Normalized fallback, grouped on the directory side so ambiguity is a
    # property of the key, independent of local iteration order.
    norm_groups: dict[str, list[int]] = {}
    for orgid, e in remaining_directory.items():
        norm_groups.setdefault(normalize_name(e["nameOfficial"]), []).append(orgid)

    for t in remaining_local:
        norm = normalize_name(t["name_full"])
        candidates = norm_groups.get(norm, [])
        if len(candidates) == 1:
            orgid = candidates[0]
            matched[t["seo"]] = orgid
            remaining_directory.pop(orgid, None)
        elif len(candidates) > 1:
            ambiguous.append((t["seo"], candidates))
            log.warning(
                "ambiguous match for %s (%r) -- candidates: %s; skipping",
                t["seo"], t["name_full"],
                [remaining_directory[o]["nameOfficial"] for o in candidates],
            )
        else:
            unmatched_local.append(t["seo"])

    return {
        "matched": matched,
        "ambiguous": ambiguous,
        "unmatched_local": unmatched_local,
        "unmatched_directory": list(remaining_directory.keys()),
    }


def _report_state_mismatches(crosswalk: dict, entries_by_orgid: dict):
    local_states = reference_data.get_team_states()
    for seo, orgid in sorted(crosswalk["matched"].items()):
        directory_state = (entries_by_orgid[orgid].get("memberOrgAddress") or {}).get("state")
        local_state = local_states.get(seo)
        if not directory_state:
            continue
        if local_state is None:
            log.info("team_states.json missing %s -- suggest: \"%s\": \"%s\",", seo, seo, directory_state)
        elif local_state != directory_state:
            log.warning(
                "team_states.json mismatch for %s: local=%s directory=%s -- suggest: \"%s\": \"%s\",",
                seo, local_state, directory_state, seo, directory_state,
            )


def _report_d1_classification_gaps(conn, crosswalk: dict):
    resolved = set(crosswalk["matched"]) | {seo for seo, _ in crosswalk["ambiguous"]}
    rows = conn.execute("SELECT seo, conference FROM teams").fetchall()
    for row in rows:
        seo = row["seo"]
        if seo in resolved:
            continue
        if reference_data.is_d1(seo, row["conference"]):
            log.warning(
                "presumed-D1 team not found in NCAA directory (check app/data/non_d1.json): %s",
                seo,
            )


def backfill_ncaa_directory(conn, division: str = "d1", force: bool = False):
    directory_division = config.NCAA_DIRECTORY_DIVISIONS[division]
    directory_entries = ncaa_directory_client.get_member_list(division=directory_division)
    entries_by_orgid = {e["orgId"]: e for e in directory_entries}
    local_teams = conn.execute(
        "SELECT seo, name_full FROM teams WHERE name_full IS NOT NULL"
    ).fetchall()

    crosswalk = build_crosswalk(local_teams, directory_entries)

    written = 0
    for seo, orgid in crosswalk["matched"].items():
        if not force:
            existing = conn.execute("SELECT orgid FROM teams WHERE seo = ?", (seo,)).fetchone()
            if existing and existing["orgid"]:
                continue
        e = entries_by_orgid[orgid]
        private_flag = e.get("privateFlag")
        is_private = {"Y": True, "N": False}.get(private_flag)
        db.upsert_team_directory(
            conn, seo, orgid, e.get("athleticWebUrl"), e.get("webSiteUrl"),
            division=division, is_private=is_private,
        )
        written += 1

    log.info(
        "[%s] matched %s/%s directory schools (%s newly written, %s ambiguous, %s unmatched directory entries)",
        division, len(crosswalk["matched"]), len(directory_entries), written,
        len(crosswalk["ambiguous"]), len(crosswalk["unmatched_directory"]),
    )
    if crosswalk["unmatched_directory"]:
        log.info(
            "[%s] unmatched directory schools: %s",
            division,
            [entries_by_orgid[o]["nameOfficial"] for o in crosswalk["unmatched_directory"]],
        )

    _report_state_mismatches(crosswalk, entries_by_orgid)
    if division == "d1":
        _report_d1_classification_gaps(conn, crosswalk)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--division", default="d1", choices=[*config.DIVISIONS.keys(), "all"],
        help="which division's NCAA directory to backfill (default: d1)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite teams that already have an orgid set",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    divisions = list(config.DIVISIONS.keys()) if args.division == "all" else [args.division]
    with db.get_conn() as conn:
        for division in divisions:
            backfill_ncaa_directory(conn, division=division, force=args.force)
