"""One-off cleanup for D3 regional NPI snapshots ingested while the upstream
ncaa-api feed was still frozen on last season's final poll (through
2026-10-01, every sync of rankings/soccer-men/d3 silently re-ingested
"Through Games NOV. 2, 2025" data under that day's date, before NCAA started
publishing real 2026-season NPI numbers on 2026-10-06).

Detects those snapshots by their fingerprint -- Region I's #1 team was
frozen at Tufts/61.294 NPI across every affected date -- and deletes every
team_rankings_regional row (all ten regions) for each matching
observed_date. Current-season data (and D1's team_rankings table) is
untouched.
"""

import argparse
import logging

from . import db

log = logging.getLogger("soccer-tracker.cleanup_stale_d3_rankings")

_STALE_SCHOOL = "Tufts"
_STALE_NPI = "61.294"


def find_stale_dates(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT observed_date FROM team_rankings_regional
        WHERE division = 'd3' AND region = 1 AND rank = 1
        AND school = ? AND npi = ?
        ORDER BY observed_date
        """,
        (_STALE_SCHOOL, _STALE_NPI),
    ).fetchall()
    return [r["observed_date"] for r in rows]


def cleanup(conn, apply: bool = False):
    dates = find_stale_dates(conn)
    if not dates:
        log.info("no stale d3 snapshots found")
        return
    log.info("%s stale d3 snapshot date(s): %s", len(dates), dates)
    if not apply:
        log.info("dry run -- pass --apply to delete")
        return
    for d in dates:
        conn.execute(
            "DELETE FROM team_rankings_regional WHERE division = 'd3' AND observed_date = ?",
            (d,),
        )
    log.info("deleted %s stale d3 snapshot date(s)", len(dates))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="actually delete the stale rows (default is dry-run, just lists the dates)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    with db.get_conn() as conn:
        cleanup(conn, apply=args.apply)
