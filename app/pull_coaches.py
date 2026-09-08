"""One-off pull of head-coach names from Wikipedia, since neither the NCAA
scoreboard/boxscore feed this app syncs from (see ncaa_client.py) nor the
teams table carry any coaching-staff data at all.

Wikipedia's college-soccer team articles use a standardized
`{{Infobox college soccer team}}` template with `|coach=` and `|tenure=`
parameters, e.g.
https://en.wikipedia.org/wiki/North_Carolina_Tar_Heels_men%27s_soccer
That template only ever has the HEAD coach -- assistant coaches aren't part
of it and aren't reliably published anywhere at this scale (~250 teams), so
this pull is head-coach-only by design.

This hits Wikipedia's API live and is meant to be run by hand once in a
while (coaches don't change mid-season), not wired into the regular sync:

    python -m app.pull_coaches

Writes app/data/coaches.json (keyed by team seo) and prints a report of any
teams it couldn't confidently match, for manual follow-up.
"""

import argparse
import json
import logging
import re
import time

import requests

from . import config, db, reference_data

log = logging.getLogger("soccer-tracker.pull_coaches")

_API = "https://en.wikipedia.org/w/api.php"
_UA = "soccer-tracker-coach-pull/0.1 (one-off script for a personal, non-commercial project)"

# Manual corrections found while reviewing the "needs_review" report --
# cases where the DB's name/mascot fields don't produce a title the
# automatic guessing (direct / direct_alt_name / search) can find, but the
# actual Wikipedia article is known. Add to this as more get tracked down.
_TITLE_OVERRIDES = {
    "yale": "Yale Bulldogs men's soccer",  # team.mascot is "Elis" (an old nickname); Wikipedia titles the article "Bulldogs"
}
_OUT_PATH = config.BASE_DIR / "app" / "data" / "coaches.json"
_REQUEST_DELAY_SECONDS = 1.0
_MAX_RETRIES = 4

_session = requests.Session()
_session.headers.update({"User-Agent": _UA})

_INFOBOX_RE = re.compile(r"\{\{\s*Infobox college soccer team", re.IGNORECASE)
_WIKILINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_REF_RE = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _get(params: dict) -> dict:
    params = {**params, "action": "query", "format": "json"}
    for attempt in range(_MAX_RETRIES):
        resp = _session.get(_API, params=params, timeout=15)
        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", 5 * (attempt + 1)))
            log.warning("rate limited, waiting %.1fs", wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        time.sleep(_REQUEST_DELAY_SECONDS)
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def _fetch_wikitext(title: str) -> str | None:
    data = _get({
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "redirects": 1,
        "titles": title,
    })
    page = next(iter(data["query"]["pages"].values()))
    if "missing" in page or "revisions" not in page:
        return None
    return page["revisions"][0]["slots"]["main"]["*"]


def _search_title(query: str) -> str | None:
    """Fallback when the guessed title doesn't exist: take the top search
    hit whose title itself is a "... men's soccer" article, rather than
    just any hit that happens to mention soccer (a tournament recap, a
    stadium article, etc.) -- those would just fail the infobox check
    anyway, but a wrong title here makes the review report noisier."""
    data = _get({"list": "search", "srsearch": query, "srlimit": 5})
    hits = data["query"]["search"]
    for hit in hits:
        if hit["title"].lower().endswith("men's soccer"):
            return hit["title"]
    return None


def _extract_infobox(wikitext: str) -> str | None:
    """Wikitext templates can nest {{...}}, so find the matching close
    brace by depth rather than a naive non-greedy regex."""
    m = _INFOBOX_RE.search(wikitext)
    if not m:
        return None
    start = m.start()
    depth = 0
    i = start
    while i < len(wikitext) - 1:
        two = wikitext[i:i + 2]
        if two == "{{":
            depth += 1
            i += 2
        elif two == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return wikitext[start:i]
        else:
            i += 1
    return None


def _field(infobox: str, name: str) -> str | None:
    m = re.search(rf"^\s*\|\s*{name}\s*=\s*(.*)$", infobox, re.MULTILINE)
    if not m:
        return None
    value = _clean_wikitext_value(m.group(1))
    return value or None


def _clean_wikitext_value(value: str) -> str:
    value = _REF_RE.sub("", value)
    value = _COMMENT_RE.sub("", value)
    value = _WIKILINK_RE.sub(r"\1", value)
    value = _TAG_RE.sub("", value)
    value = value.replace("'''", "").replace("''", "")
    return value.strip().strip(",")


def _candidate_title(name: str, mascot: str | None) -> str:
    if mascot:
        return f"{name} {mascot} men's soccer"
    return f"{name} men's soccer"


def _short_from_full(name_full: str) -> str:
    """Drop the legal-name scaffolding ("University of X" / "X University"
    / "X College") to approximate the short form Wikipedia article titles
    use, e.g. "Georgia State University" -> "Georgia State" (the DB's own
    `name` for that team is the abbreviated "Georgia St.", which doesn't
    match either)."""
    m = re.match(r"^University of (?:the )?(.+)$", name_full)
    if m:
        return m.group(1)
    n = re.sub(r"\s+University$", "", name_full)
    n = re.sub(r"\s+College$", "", n)
    return n


def _lookup_coach(seo: str, name: str, name_full: str | None, mascot: str | None) -> dict:
    candidates = [name]
    if name_full and name_full != name:
        candidates.append(name_full)
        short = _short_from_full(name_full)
        if short not in candidates:
            candidates.append(short)

    wikitext = None
    match_method = None
    wikipedia_title = None
    primary_candidate = _candidate_title(candidates[0], mascot)

    if seo in _TITLE_OVERRIDES:
        title = _TITLE_OVERRIDES[seo]
        wikitext = _fetch_wikitext(title)
        if wikitext:
            match_method = "override"
            wikipedia_title = title

    for i, candidate_name in enumerate(candidates):
        if wikitext:
            break
        title = _candidate_title(candidate_name, mascot)
        wikitext = _fetch_wikitext(title)
        if wikitext:
            match_method = "direct" if i == 0 else "direct_alt_name"
            wikipedia_title = title
            break

    if wikitext is None:
        searched_title = _search_title(primary_candidate)
        if searched_title:
            wikitext = _fetch_wikitext(searched_title)
            if wikitext:
                match_method = "search"
                wikipedia_title = searched_title

    if wikitext is None:
        return {"status": "no_page", "candidate_query": primary_candidate}

    infobox = _extract_infobox(wikitext)
    if infobox is None:
        return {
            "status": "no_infobox",
            "candidate_query": primary_candidate,
            "wikipedia_title": wikipedia_title,
            "match_method": match_method,
        }

    coach = _field(infobox, "coach")
    tenure = _field(infobox, "tenure")
    if not coach:
        return {
            "status": "no_coach_field",
            "candidate_query": primary_candidate,
            "wikipedia_title": wikipedia_title,
            "match_method": match_method,
        }

    return {
        "status": "ok",
        "candidate_query": primary_candidate,
        "wikipedia_title": wikipedia_title,
        "match_method": match_method,
        "coach": coach,
        "tenure": tenure,
    }


def pull_coaches(conn, limit: int | None = None) -> dict:
    teams = conn.execute(
        "SELECT seo, name, name_full, mascot, conference FROM teams "
        "WHERE name IS NOT NULL ORDER BY name"
    ).fetchall()
    if limit:
        teams = teams[:limit]

    results: dict[str, dict] = {}
    needs_review: list[dict] = []

    for i, team in enumerate(teams, 1):
        seo, name, name_full, mascot, conference = (
            team["seo"], team["name"], team["name_full"], team["mascot"], team["conference"],
        )
        log.info("[%s/%s] %s", i, len(teams), name)

        try:
            result = _lookup_coach(seo, name, name_full, mascot)
        except requests.RequestException as exc:
            log.warning("request failed for %s: %s", name, exc)
            result = {"status": "request_error", "error": str(exc)}

        division_tag = reference_data.get_conference_tag(conference) or (
            reference_data._get_non_d1()["schools"].get(seo)
        )

        entry = {
            "name": name,
            "name_full": name_full,
            "conference": conference,
            "division_tag": division_tag,
            **result,
        }
        results[seo] = entry

        if result["status"] != "ok":
            needs_review.append({"seo": seo, **entry})

    return {"teams": results, "needs_review": needs_review}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None,
        help="only process the first N teams (for a quick test run)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db.init_db()
    with db.get_conn() as conn:
        output = pull_coaches(conn, limit=args.limit)

    _OUT_PATH.write_text(json.dumps(output["teams"], indent=2, sort_keys=True) + "\n")

    ok = sum(1 for t in output["teams"].values() if t["status"] == "ok")
    total = len(output["teams"])
    log.info("\nWrote %s (%s/%s matched)", _OUT_PATH, ok, total)

    if output["needs_review"]:
        log.info("\n%s teams need manual review:", len(output["needs_review"]))
        for entry in output["needs_review"]:
            log.info("  %-20s %-30s status=%s query=%r", entry["seo"], entry["name"],
                      entry["status"], entry.get("candidate_query"))


if __name__ == "__main__":
    main()
