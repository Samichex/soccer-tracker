# Full Time

Local app that syncs NCAA Division I men's soccer scores/schedule/box scores
from the [ncaa-api](https://github.com/henrygd/ncaa-api) public instance into
a local SQLite database, and serves a small dashboard to browse them.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000

On startup it runs a full sync (today +/- a few days) and repeats every
`SYNC_INTERVAL_MINUTES` (default 30). Final games get their box score
(per-player goals/assists/shots/cards) pulled in automatically once synced.

## Config (env vars)

- `NCAA_API_BASE` — defaults to the public `https://ncaa-api.henrygd.me`.
  Point this at a self-hosted instance (`docker run -p 3000:3000 henrygd/ncaa-api`)
  if the public one becomes unreliable or rate limits are an issue.
- `DAYS_BACK` / `DAYS_FORWARD` — sync window around today (default 3 / 7).
- `SYNC_INTERVAL_MINUTES` — background sync frequency (default 30).
