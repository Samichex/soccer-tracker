import argparse
import datetime as dt
import logging

from . import db, sync

log = logging.getLogger("soccer-tracker.backfill")

SEASON_START = dt.date(2026, 7, 30)


def backfill(start: dt.date, end: dt.date):
    with db.get_conn() as conn:
        d = start
        while d <= end:
            sync.sync_date(conn, d)
            d += dt.timedelta(days=1)
        log.info("backfilling box scores for finished games...")
        sync.sync_missing_boxscores(conn)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=SEASON_START.isoformat())
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    backfill(start, end)
