# Full Time

Syncs NCAA Division I and III men's soccer scores, schedules, box scores and
rankings from the [ncaa-api](https://github.com/henrygd/ncaa-api) public
instance into a SQLite database, and serves a small site to browse them.

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
`SYNC_INTERVAL_MINUTES` (default 30), or every `LIVE_SYNC_INTERVAL_SECONDS`
(default 90) while a game is in progress. Final games get their box score
(per-player goals/assists/shots/cards) pulled in automatically once synced.

## Config (env vars)

- `NCAA_API_BASE` — defaults to the public `https://ncaa-api.henrygd.me`.
  Point this at a self-hosted instance (`docker run -p 3000:3000 henrygd/ncaa-api`)
  if the public one becomes unreliable or rate limits are an issue.
- `ENABLED_DIVISIONS` — comma-separated list of divisions to sync (default `d1`).
  Setting `d1,d3` syncs D3 games/rosters/standings too; every page and read
  query is division-scoped (default D1, switchable via the nav's Division
  pills once more than one division is enabled), so D3 data won't mix into
  D1 pages. D1 rankings are the United Soccer Coaches national poll; D3's
  feed is instead ten *regional* NPI leaderboards, synced separately and
  shown as e.g. "I-3" (Region I, #3) with a per-region Rank History.
- `DAYS_BACK` / `DAYS_FORWARD` — live sync window around today (default 3 / 4).
  Scores and game times in this window change, so it's re-pulled every
  `SYNC_INTERVAL_MINUTES`.
- `SCHEDULE_DAYS_FORWARD` / `SCHEDULE_SYNC_INTERVAL_HOURS` — further-out
  schedule window (default 65 days forward, i.e. the rest of the regular
  season from an early-September start; the upstream API returns nothing
  past that until postseason brackets are published) and how often it's
  refreshed (default every 24h). Fixtures out there barely change day to
  day, so this runs on its own slower cadence in the background. The same
  daily pass also retries box scores for older games that never got one.
- `CATCHUP_DAYS_BACK` — how far back (default 120 days, about a season) the
  daily pass re-checks past dates that still have a game not marked final,
  e.g. one that finished while the app was down. Cancelled matches never
  finalize upstream, so their dates drop out of this check once they're
  older than this.
- `SYNC_INTERVAL_MINUTES` — background sync frequency (default 30).

## Deploying

Run as a **single process/instance**, not multiple workers or autoscaled
replicas. The background sync loop (`app/main.py`'s `_background_sync_loop`)
is an in-process thread with no cross-instance coordination — running more
than one copy means every copy independently polls the upstream NCAA API and
writes to the same SQLite file, multiplying upstream load and increasing the
odds of write contention. For uvicorn this means no `--workers N>1`; for a
hosting platform, no autoscaling/horizontal scaling for this service.

Currently deployed on [Render](https://render.com) (Starter instance, no
autoscaling available at that tier anyway):

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- A 1 GB persistent disk mounted at `/var/data`, with the `DB_PATH` env var
  set to `/var/data/soccer.db` so the database survives redeploys
- `PYTHON_VERSION` env var pinned to match local dev (see `.venv/pyvenv.cfg`)
- TLS and auto-deploy-on-push to `main` are handled by Render itself — no
  reverse proxy needed on this host. If you deploy elsewhere without that
  built in, put one (nginx, Caddy, etc.) in front for TLS, since the app
  itself only speaks plain HTTP.

The site and its JSON endpoints are intentionally open with no
authentication (read-only public scores/stats). Nothing a visitor can do
triggers a sync; only the background loop calls the upstream NCAA API.

### Adding a historic season

Each game's `season` column (just the year it was played in) is backfilled
automatically from its `date` on every startup (`db.init_db()`), so an
already-synced season needs nothing manual — it just appears in the nav's
season switcher once two or more seasons exist in the database.

A season the live sync never covered (one from before this app existed,
or days missed while it was down) has no rows yet, though, and needs a
one-off backfill to pull it in from the same upstream feed:

```bash
python -m app.backfill --season 2025 --division all
```

`--season` covers that season's whole window (July 30 to December 31, never
past today); leave it out for the most recent season, or pass
`--start`/`--end` (YYYY-MM-DD) for an exact range instead.

Run this from Render's Shell tab for the service, not locally — the
database only exists on the service's persistent disk, and the single-
instance rule above means nothing else can reach it. It's safe to re-run
(idempotent per day) and can run while the live site keeps syncing:
each day and each box score is saved as soon as it's fetched, so the two
never hold the database locked against each other for long, and if the
backfill stops partway, re-running it picks up where it left off. Expect it
to take a while (one full season's worth of upstream NCAA API calls, same
as the original D1 2026 backfill). Once a season is in, it's in for good —
there's nothing to re-run for it on later deploys.

If the Render disk is ever recreated, rebuild history the same way:
`python -m app.backfill --season <year> --division all` for each season's
games and box scores, then `python -m app.backfill_rankings --season <year>`
for the D1 poll weeks stored in `app/backfill_rankings.py` (today: 2026's
weeks before this app's own daily snapshots began). Ranking weeks after
that only existed as the app's own snapshots, so a lost disk loses them.
