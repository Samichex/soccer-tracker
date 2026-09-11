import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Point this at a self-hosted ncaa-api instance later if the public one
# becomes unreliable: docker run -p 3000:3000 henrygd/ncaa-api
NCAA_API_BASE = os.environ.get("NCAA_API_BASE", "https://ncaa-api.henrygd.me")

# ncaa.com sport/division path segment for each division we know how to
# sync. Adding a key here doesn't turn anything on by itself -- see
# ENABLED_DIVISIONS below.
DIVISIONS = {
    "d1": "soccer-men/d1",
    "d3": "soccer-men/d3",
}

# Which of the divisions above the background sync actually pulls. D3
# support (schema, client, sync loop) exists but stays off by default:
# the read-side queries in app/db.py and every page/route in app/main.py
# are not division-aware yet, so enabling "d3" here before that filtering
# lands would silently mix D3 games/rankings into every D1 page. Flip on
# via ENABLED_DIVISIONS="d1,d3" once that work ships.
ENABLED_DIVISIONS = [
    d.strip() for d in os.environ.get("ENABLED_DIVISIONS", "d1").split(",") if d.strip()
]

# Divisions whose rankings feed is a single national poll compatible with
# sync_rankings/team_rankings: one rank per team, with PREVIOUS/POINTS/
# FIRST-PLACE VOTES/RECORD fields (see sync._parse_rankings). D3 men's
# soccer's rankings endpoint instead returns ten separate *regional* NPI
# leaderboards (Region I-X, fields RANK/SCHOOL/IN-DIVISION RECORD/NPI only)
# -- ten different teams all "rank 1", ten "rank 2", etc, and no
# prev_rank/points/votes at all. That's a different data model this app
# doesn't support yet, so rankings sync is deliberately skipped for any
# division not listed here rather than stored as if it were a national
# poll. Confirmed by inspecting the live feed 2026-09-11.
RANKINGS_SUPPORTED_DIVISIONS = {"d1"}

# NCAA directory (web3.ncaa.org) division codes, keyed the same as
# DIVISIONS above -- a different vocabulary ("I"/"III" instead of
# "d1"/"d3") because it's a different upstream host. See
# app/backfill_ncaa_directory.py.
NCAA_DIRECTORY_DIVISIONS = {
    "d1": "I",
    "d3": "III",
}

DB_PATH = Path(os.environ.get("DB_PATH", str(BASE_DIR / "data" / "soccer.db")))

# How far around "today" to keep synced live (scores/times here can change,
# so this window is re-pulled on every sync cycle and on manual refresh).
DAYS_BACK = int(os.environ.get("DAYS_BACK", "3"))
DAYS_FORWARD = int(os.environ.get("DAYS_FORWARD", "4"))

# Further out, fixtures are set but essentially static day-to-day, so that
# window is only worth re-checking occasionally (see SCHEDULE_SYNC_INTERVAL_HOURS)
# rather than every SYNC_INTERVAL_MINUTES. 65 days covers the rest of a
# regular season from an early-September start.
SCHEDULE_DAYS_FORWARD = int(os.environ.get("SCHEDULE_DAYS_FORWARD", "65"))
SCHEDULE_SYNC_INTERVAL_HOURS = int(os.environ.get("SCHEDULE_SYNC_INTERVAL_HOURS", "24"))

SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", "30"))
