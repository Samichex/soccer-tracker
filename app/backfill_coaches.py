"""One-off scrape of the Men's Soccer head coach for every team already
matched to an NCAA directory orgid (see app/backfill_ncaa_directory.py,
which must be run first).

Source: https://web3.ncaa.org/directory/orgDetail?id={orgId} -- a public,
server-rendered HTML page (not JSON) whose "Sponsored Sports" table lists
each sport's head coach in a fixed structure:

    <td>Men&#39;s Soccer</td>
    <td>
        <span>Zach  Samol</span>
        ...

Prototyped 2026-09-10 against several schools, including one with an
apostrophe in the coach's name (HTML-entity-escaped, handled by
html.unescape). This is a point-in-time snapshot -- coaches change
(including mid-season firings) -- so re-run with --force to refresh.

Politeness: this is ~213 individual page fetches (unlike the single bulk
call in backfill_ncaa_directory.py), so requests are spaced out by
--delay seconds (default 1.0) rather than fired back to back.
"""

import argparse
import html
import logging
import re
import time

import requests

from . import db, ncaa_directory_client

log = logging.getLogger("soccer-tracker.backfill_coaches")

_COACH_RE = re.compile(r"<td>Men&#39;s Soccer</td>\s*<td>\s*<span>([^<]*)</span>")


def extract_head_coach(page_html: str) -> str | None:
    """Pull the Men's Soccer head coach name out of an orgDetail page's
    "Sponsored Sports" table. Returns None if the row/name is missing
    (e.g. a coaching vacancy) rather than raising."""
    m = _COACH_RE.search(page_html)
    if not m:
        return None
    name = html.unescape(m.group(1))
    name = " ".join(name.split())
    return name or None


def backfill_coaches(conn, force: bool = False, delay_seconds: float = 1.0):
    rows = db.get_teams_with_orgid(conn)
    if not rows:
        log.error(
            "no teams have an orgid yet -- run `python -m app.backfill_ncaa_directory` first"
        )
        return

    found = 0
    missing = 0
    failed = 0
    for i, row in enumerate(rows):
        seo, orgid = row["seo"], row["orgid"]
        if not force:
            existing = conn.execute(
                "SELECT head_coach FROM teams WHERE seo = ?", (seo,)
            ).fetchone()
            if existing and existing["head_coach"]:
                continue

        try:
            page_html = ncaa_directory_client.get_org_detail_html(orgid)
        except requests.RequestException as e:
            log.warning("failed to fetch orgDetail for %s (orgid=%s): %s", seo, orgid, e)
            failed += 1
            continue

        coach = extract_head_coach(page_html)
        if coach:
            db.set_head_coach(conn, seo, coach)
            found += 1
        else:
            log.warning("no Men's Soccer head coach found for %s (orgid=%s)", seo, orgid)
            missing += 1

        if i < len(rows) - 1:
            time.sleep(delay_seconds)

    log.info("head coach backfill done: %s found, %s missing, %s failed to fetch", found, missing, failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite teams that already have a head_coach set",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="seconds to sleep between orgDetail page fetches (default: 1.0)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    with db.get_conn() as conn:
        backfill_coaches(conn, force=args.force, delay_seconds=args.delay)
