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

## Deploying

Run as a **single process/instance**, not multiple workers or autoscaled
replicas. The background sync loop (`app/main.py`'s `_background_sync_loop`)
is an in-process thread with no cross-instance coordination — running more
than one copy means every copy independently polls the upstream NCAA API and
writes to the same SQLite file, multiplying upstream load and increasing the
odds of write contention. For uvicorn this means no `--workers N>1`; for a
hosting platform, no autoscaling/horizontal scaling for this service.

Put a reverse proxy (nginx, Caddy, or your host's built-in one) in front for
TLS — the app itself only speaks plain HTTP.

The site and its JSON endpoints are intentionally open with no
authentication (read-only public scores/stats). The one write-triggering
route, `POST /api/sync-now`, is throttled to at most once a minute so a
public caller can't hammer the upstream NCAA API feed, but it's still an
unauthenticated way to nudge the app to hit an external service — remove it
or gate it behind a shared secret if that becomes a concern.
