import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Point this at a self-hosted ncaa-api instance later if the public one
# becomes unreliable: docker run -p 3000:3000 henrygd/ncaa-api
NCAA_API_BASE = os.environ.get("NCAA_API_BASE", "https://ncaa-api.henrygd.me")

SPORT_PATH = "soccer-men/d1"

DB_PATH = BASE_DIR / "data" / "soccer.db"

# How far around "today" to keep synced
DAYS_BACK = int(os.environ.get("DAYS_BACK", "3"))
DAYS_FORWARD = int(os.environ.get("DAYS_FORWARD", "7"))

SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", "30"))
