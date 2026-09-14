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

# Divisions whose rankings feed is the ten-region NPI split described above,
# synced via sync.sync_regional_rankings into db.team_rankings_regional
# instead of team_rankings. Kept out of RANKINGS_SUPPORTED_DIVISIONS since
# that set specifically means "single national poll shape". This loop runs
# independently of ENABLED_DIVISIONS (see sync.run_full_sync) -- regional
# rankings don't touch the games table at all, so pulling them doesn't carry
# the D1/D3 game-mixing risk ENABLED_DIVISIONS guards against.
REGIONAL_RANKINGS_DIVISIONS = {"d3"}

# NCAA directory (web3.ncaa.org) division codes, keyed the same as
# DIVISIONS above -- a different vocabulary ("I"/"III" instead of
# "d1"/"d3") because it's a different upstream host. See
# app/backfill_ncaa_directory.py.
NCAA_DIRECTORY_DIVISIONS = {
    "d1": "I",
    "d3": "III",
}

DB_PATH = Path(os.environ.get("DB_PATH", str(BASE_DIR / "data" / "soccer.db")))

# Personal key from https://collegescorecard.ed.gov/data/api-documentation/,
# used only by the one-off app/backfill_college_stats.py script -- not read
# anywhere in the request path, so it only needs to be set in the shell (or
# Render env var) a backfill run happens in.
COLLEGE_SCORECARD_API_KEY = os.environ.get("COLLEGE_SCORECARD_API_KEY", "")

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

# When a game is currently live, poll far more often than the idle
# SYNC_INTERVAL_MINUTES cadence so scores/clock update promptly.
LIVE_SYNC_INTERVAL_SECONDS = int(os.environ.get("LIVE_SYNC_INTERVAL_SECONDS", "90"))

# Circuit breaker: after this many consecutive sync failures (e.g. repeated
# 429s from the shared public ncaa-api instance), fall back to the slow
# idle interval regardless of live-game status, until a sync succeeds again.
SYNC_FAILURE_BACKOFF_THRESHOLD = int(os.environ.get("SYNC_FAILURE_BACKOFF_THRESHOLD", "3"))
