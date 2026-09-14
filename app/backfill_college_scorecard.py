"""One-off backfill of city (from the U.S. Department of Education's College
Scorecard) for every team already in the `teams` table.

The NCAA directory (app/backfill_ncaa_directory.py) can't supply this --
its memberOrgAddress.city/address1 are null for all 426 D1 and 812 D3
entries, public and private schools alike (confirmed by inspecting the
live feed 2026-09-12). College Scorecard has city for essentially every
accredited US institution (6,273 as of this writing).

Source: the bulk institution-level CSV (_BULK_ZIP_URL below), not the
api.data.gov JSON API -- the API's shared DEMO_KEY quota wedged for good
partway through a ~63-page pull (confirmed 2026-09-13, an hour+ of 60s
retries never recovered), and a personal data.gov key couldn't be issued
either (signup itself was rate-limited that day). The bulk CSV needs no key
and no rate-limit handling at all: one plain HTTPS download.

That download URL is date-stamped by College Scorecard and will go stale
when they publish a new cohort -- if a run 404s, get the current link from
the "Most Recent Data" download on https://collegescorecard.ed.gov/data/
and update _BULK_ZIP_URL.

Matching runs three passes, least-aggressive first, each only touching
teams the previous one left unmatched -- and each stays as conservative as
build_crosswalk about never guessing across a genuine collision:

1. `backfill_ncaa_directory.build_crosswalk` as-is (exact name, then
   generic-word-stripped normalization). ~75% of teams on the 2026-09-13
   run.
2. `_normalize_campus` -- same idea, but also folds "-"/" at " campus
   separators together (College Scorecard writes a school's main campus
   name three different ways: "University of X, Y" vs "University of
   X-Y" vs "University of X at Y"). Catches e.g. local "University of
   Maryland, Baltimore County" vs Scorecard "University of Maryland-
   Baltimore County".
3. A bare-flagship prefix match for local names with *no* campus
   qualifier at all (e.g. local "University of Michigan" vs Scorecard's
   "University of Michigan-Ann Arbor"/"-Dearborn"/"-Flint"): matches only
   when exactly one MAIN=1 (IPEDS' "main campus" flag) Scorecard entry
   starts with the local name at a word boundary. A school with more than
   one co-equal MAIN campus (Michigan's three, Virginia's two before the
   apostrophe in "Virginia's College at Wise" excludes it) is left
   unmatched rather than guessed.

Output: app/data/team_cities.json, {seo: city}, alongside the existing
app/data/team_states.json.
"""

import argparse
import csv
import io
import json
import logging
import re
import zipfile

import requests

from . import backfill_ncaa_directory, config, db

log = logging.getLogger("soccer-tracker.backfill_college_scorecard")

_BULK_ZIP_URL = "https://ed-public-download.scorecard.network/downloads/Most-Recent-Cohorts-Institution_06102026.zip"
_TEAM_CITIES_PATH = config.BASE_DIR / "app" / "data" / "team_cities.json"

_PUNCT_RE = re.compile(r"[.,'’]")
_SEPARATOR_RE = re.compile(r"-|\s+at\s+")
_GENERIC_WORDS_RE = re.compile(r"\b(university of|university|college|the)\b")
_WHITESPACE_RE = re.compile(r"\s+")
_LEADING_THE_RE = re.compile(r"^the\s+", re.IGNORECASE)


def _normalize_campus(name: str) -> str:
    """Like backfill_ncaa_directory.normalize_name, but also treats "-" and
    " at " as interchangeable campus separators (see module docstring,
    pass 2)."""
    n = name.lower()
    n = _PUNCT_RE.sub("", n)
    n = _SEPARATOR_RE.sub(" ", n)
    n = _GENERIC_WORDS_RE.sub("", n)
    n = _WHITESPACE_RE.sub(" ", n).strip()
    return n


def _is_campus_of(school_name_lower: str, local_name_lower: str) -> bool:
    """True if `school_name_lower` names a specific campus of the bare
    institution `local_name_lower` names -- i.e. it starts with it, then
    either ends or continues with a "-"/" " boundary (not, say, an
    apostrophe: "university of virginia" is NOT a campus-boundary prefix
    of "university of virginia's college at wise", which is a distinct
    accredited school, not a Charlottesville campus)."""
    if not school_name_lower.startswith(local_name_lower):
        return False
    rest = school_name_lower[len(local_name_lower):]
    return rest == "" or rest[0] in (" ", "-")


def fetch_all_schools() -> list[dict]:
    """Every school in College Scorecard's bulk institution CSV, as
    {"name", "city", "state", "main", "unitid"} dicts ("main" is IPEDS'
    "1"/"0" main-campus flag, used by pass 3 above; "unitid" is the same id
    the College Scorecard API keys its records by, used by
    app/backfill_college_stats.py to look up a matched school's live stats
    without a second name-matching pass)."""
    resp = requests.get(_BULK_ZIP_URL, timeout=60)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(csv_name) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
            return [
                {
                    "name": row["INSTNM"], "city": row["CITY"], "state": row["STABBR"],
                    "main": row["MAIN"], "unitid": row["UNITID"],
                }
                for row in reader
            ]


def build_city_crosswalk(local_teams: list, schools: list[dict]) -> dict:
    """Runs all three matching passes described in the module docstring.
    Returns {"matched": {seo: school_index}, "ambiguous": [(seo, [names])],
    "unmatched_local": [seo]}."""
    directory_entries = [{"orgId": i, "nameOfficial": s["name"]} for i, s in enumerate(schools)]
    primary = backfill_ncaa_directory.build_crosswalk(local_teams, directory_entries)
    matched: dict[str, int] = dict(primary["matched"])
    # primary's ambiguous candidates are orgIds (== our school indices, see
    # directory_entries above) -- resolve to names so the combined log
    # below reads consistently with the pass 2/3 entries added below.
    ambiguous: list[tuple[str, list[str]]] = [
        (seo, [schools[i]["name"] for i in orgids]) for seo, orgids in primary["ambiguous"]
    ]

    remaining = [t for t in local_teams if t["seo"] not in matched and t["name_full"]]

    # Pass 2: separator-normalized exact grouping.
    groups: dict[str, list[int]] = {}
    for i, s in enumerate(schools):
        groups.setdefault(_normalize_campus(s["name"]), []).append(i)

    still_remaining = []
    for t in remaining:
        candidates = groups.get(_normalize_campus(t["name_full"]), [])
        if len(candidates) == 1:
            matched[t["seo"]] = candidates[0]
            continue
        if len(candidates) > 1:
            main_only = [c for c in candidates if schools[c]["main"] == "1"]
            if len(main_only) == 1:
                matched[t["seo"]] = main_only[0]
                continue
            ambiguous.append((t["seo"], [schools[c]["name"] for c in candidates]))
            continue
        still_remaining.append(t)

    # Pass 3: bare-flagship name -> unique MAIN campus.
    unmatched_local = []
    for t in still_remaining:
        local_lower = _LEADING_THE_RE.sub("", t["name_full"].lower()).strip()
        candidates = [i for i, s in enumerate(schools) if _is_campus_of(s["name"].lower(), local_lower)]
        if not candidates:
            unmatched_local.append(t["seo"])
            continue
        main_only = [c for c in candidates if schools[c]["main"] == "1"]
        pool = main_only or candidates
        if len(pool) == 1:
            matched[t["seo"]] = pool[0]
        else:
            ambiguous.append((t["seo"], [schools[c]["name"] for c in pool]))

    return {"matched": matched, "ambiguous": ambiguous, "unmatched_local": unmatched_local}


def backfill_college_scorecard(conn, force: bool = False) -> None:
    existing_cities: dict[str, str] = {}
    if _TEAM_CITIES_PATH.exists():
        existing_cities = json.loads(_TEAM_CITIES_PATH.read_text())

    local_teams = conn.execute(
        "SELECT seo, name_full FROM teams WHERE name_full IS NOT NULL"
    ).fetchall()
    schools = fetch_all_schools()
    crosswalk = build_city_crosswalk(local_teams, schools)

    written = 0
    for seo, idx in crosswalk["matched"].items():
        if not force and seo in existing_cities:
            continue
        city = schools[idx].get("city")
        if not city:
            continue
        existing_cities[seo] = city
        written += 1

    _TEAM_CITIES_PATH.write_text(json.dumps(existing_cities, indent=2, sort_keys=True) + "\n")
    log.info(
        "matched %s/%s local teams against %s schools (%s newly written, "
        "%s ambiguous, %s unmatched local)",
        len(crosswalk["matched"]), len(local_teams), len(schools), written,
        len(crosswalk["ambiguous"]), len(crosswalk["unmatched_local"]),
    )
    if crosswalk["unmatched_local"]:
        log.info("unmatched local teams: %s", crosswalk["unmatched_local"])
    if crosswalk["ambiguous"]:
        log.info("ambiguous matches: %s", crosswalk["ambiguous"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite teams that already have a city set",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    with db.get_conn() as conn:
        backfill_college_scorecard(conn, force=args.force)
