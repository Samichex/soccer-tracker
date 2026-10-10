"""One-off backfill of historical games and box scores (player_stats) for a
date range, one or more divisions at a time. This is what originally
populated D1's full season and is the same path to use for D3: games and
their box scores come from the same ncaa-api feed for both (see
config.DIVISIONS), just walked day by day instead of the live/rolling
window sync.py's background loop keeps warm.

Pick the range with --season YYYY (that season's whole window, see
season_window) or with explicit --start/--end dates. With neither, it
backfills the most recent season.

Safe to re-run: sync_date/upsert_game are idempotent per day, and
sync_missing_boxscores only fetches box scores for games that don't have
one yet.
"""

import argparse
import datetime as dt
import logging

from . import config, db, sync

log = logging.getLogger("soccer-tracker.backfill")

# Every men's soccer season runs inside one calendar year: preseason
# rankings and the first exhibitions in early August, the College Cup by
# mid-December. These bounds sit safely outside both ends.
_SEASON_FIRST_DAY = (7, 30)
_SEASON_LAST_DAY = (12, 31)


def most_recent_season(today: dt.date | None = None) -> int:
    """This year's season once it can have started (from late July), else
    last year's -- what a backfill means when no season is given."""
    today = today or dt.date.today()
    return today.year if today >= dt.date(today.year, *_SEASON_FIRST_DAY) else today.year - 1


def season_window(season: int, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """(first, last) day to backfill for `season`, never past today."""
    today = today or dt.date.today()
    start = dt.date(season, *_SEASON_FIRST_DAY)
    end = min(dt.date(season, *_SEASON_LAST_DAY), today)
    return start, end


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
    parser.add_argument(
        "--season", type=int, default=None,
        help="season (year) to backfill in full (default: the most recent season)",
    )
    parser.add_argument("--start", default=None, help="first date, YYYY-MM-DD (overrides --season)")
    parser.add_argument("--end", default=None, help="last date, YYYY-MM-DD (overrides --season)")
    parser.add_argument(
        "--division", default="d1", choices=[*config.DIVISIONS.keys(), "all"],
        help="which division to backfill games/box scores for (default: d1)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    season_start, season_end = season_window(args.season or most_recent_season())
    start = dt.date.fromisoformat(args.start) if args.start else season_start
    end = dt.date.fromisoformat(args.end) if args.end else season_end
    divisions = list(config.DIVISIONS.keys()) if args.division == "all" else [args.division]
    log.info("backfilling %s from %s to %s", ", ".join(divisions), start, end)
    backfill(start, end, divisions)
