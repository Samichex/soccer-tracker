"""One-off load of a past season's NCAA tournament bracket (the live sync
only keeps the current year's warm, from November on -- see
config.BRACKET_SYNC_FIRST_MONTH).

    python -m app.backfill_bracket --season 2025 --division all

The bracket page links each game to its game page, which only exists once
that day's scoreboard has been synced (see app.backfill). Safe to re-run:
the stored bracket is replaced wholesale.
"""

import argparse
import logging

from . import config, db, sync

log = logging.getLogger("soccer-tracker.backfill_bracket")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True, help="season (year) to load, e.g. 2025")
    parser.add_argument(
        "--division", default="d1", choices=[*config.DIVISIONS.keys(), "all"],
        help="which division's bracket to load (default: d1)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    divisions = list(config.DIVISIONS.keys()) if args.division == "all" else [args.division]
    with db.get_conn() as conn:
        for division in divisions:
            sync.sync_bracket(conn, division, args.season)
