import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Point this at a self-hosted ncaa-api instance later if the public one
# becomes unreliable: docker run -p 3000:3000 henrygd/ncaa-api
NCAA_API_BASE = os.environ.get("NCAA_API_BASE", "https://ncaa-api.henrygd.me")

SPORT_PATH = "soccer-men/d1"

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
