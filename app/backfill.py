"""One-off backfill of historical games and box scores (player_stats) for a
date range, one or more divisions at a time. This is what originally
populated D1's full season and is the same path to use for D3: games and
their box scores come from the same ncaa-api feed for both (see
config.DIVISIONS), just walked day by day instead of the live/rolling
window sync.py's background loop keeps warm.

Safe to re-run: sync_date/upsert_game are idempotent per day, and
sync_missing_boxscores only fetches box scores for games that don't have
one yet.
"""

import argparse
import datetime as dt
import logging

from . import config, db, sync

log = logging.getLogger("soccer-tracker.backfill")

SEASON_START = dt.date(2026, 7, 30)


def backfill(start: dt.date, end: dt.date, divisions: list[str] = ["d1"]):
    with db.get_conn() as conn:
        for division in divisions:
            sport_path = config.DIVISIONS[division]
            d = start
            while d <= end:
                sync.sync_date(conn, d, division, sport_path)
                d += dt.timedelta(days=1)
        log.info("backfilling box scores for finished games...")
        sync.sync_missing_boxscores(conn)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=SEASON_START.isoformat())
    parser.add_argument("--end", default=None)
    parser.add_argument(
        "--division", default="d1", choices=[*config.DIVISIONS.keys(), "all"],
        help="which division to backfill games/box scores for (default: d1)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    divisions = list(config.DIVISIONS.keys()) if args.division == "all" else [args.division]
    backfill(start, end, divisions)
