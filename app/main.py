import datetime as dt
import json
import logging
import threading
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import config, db, reference_data, standings, sync

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("soccer-tracker")

app = FastAPI(title="Full Time")
app.add_middleware(GZipMiddleware, minimum_size=1000)

EASTERN = ZoneInfo("America/New_York")


def _today_eastern() -> dt.date:
    return dt.datetime.now(EASTERN).date()


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(config.BASE_DIR / "app" / "templates"))
templates.env.globals["team_label"] = reference_data.get_team_label
templates.env.globals["team_label_responsive"] = reference_data.get_team_label_responsive
templates.env.globals["rank_prefix"] = reference_data.rank_prefix
templates.env.globals["rank_arrow"] = reference_data.rank_arrow


def _querystring_with(request: Request, **overrides) -> str:
    params = dict(request.query_params)
    for key, value in overrides.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = str(value)
    return "?" + "&".join(f"{quote(k)}={quote(v)}" for k, v in params.items())


templates.env.globals["querystring_with"] = _querystring_with
templates.env.filters["name_case"] = reference_data.title_case_name
templates.env.filters["position_short"] = reference_data.position_short


def _pretty_date(date_str: str | None) -> str:
    if not date_str:
        return ""
    try:
        return dt.date.fromisoformat(date_str).strftime("%a %d %b")
    except ValueError:
        return date_str


templates.env.filters["pretty_date"] = _pretty_date


def _tojson(value) -> Markup:
    """Serialize for embedding in a <script type="application/json"> block
    -- also escape the sequences that would otherwise break out of it."""
    return Markup(
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


templates.env.filters["tojson"] = _tojson


def _last_synced():
    with db.get_conn() as conn:
        return reference_data.format_last_synced(db.get_last_synced(conn))


templates.env.globals["last_synced"] = _last_synced


def _background_sync_loop():
    last_schedule_sync: dt.datetime | None = None
    schedule_interval = dt.timedelta(hours=config.SCHEDULE_SYNC_INTERVAL_HOURS)
    while True:
        try:
            sync.run_full_sync()
        except Exception:
            log.exception("sync failed")

        now = dt.datetime.utcnow()
        if last_schedule_sync is None or now - last_schedule_sync >= schedule_interval:
            try:
                sync.sync_far_schedule()
                last_schedule_sync = now
            except Exception:
                log.exception("far schedule sync failed")

        time.sleep(config.SYNC_INTERVAL_MINUTES * 60)


@app.on_event("startup")
def on_startup():
    db.init_db()
    thread = threading.Thread(target=_background_sync_loop, daemon=True)
    thread.start()


def _parse_date(date_str: str | None) -> dt.date:
    if not date_str:
        return _today_eastern()
    try:
        return dt.date.fromisoformat(date_str)
    except ValueError:
        return _today_eastern()


def _conference_label(seo: str, full: bool = False) -> str:
    label = (
        reference_data.conference_full_name(seo)
        if full
        else reference_data.conference_short_name(seo)
    )
    tag = reference_data.get_conference_tag(seo)
    return f"{label} ({tag})" if tag else label


def _rank_map(rankings) -> dict[str, dict]:
    """{seo: {"rank", "prev_rank"}} for the latest poll, skipping rows where
    seo is unresolved."""
    return {r["seo"]: {"rank": r["rank"], "prev_rank": r["prev_rank"]} for r in rankings if r["seo"]}


def _rank_lookup(rank_map: dict, seo: str | None) -> tuple[int | None, str | None]:
    info = rank_map.get(seo)
    return (info["rank"], info["prev_rank"]) if info else (None, None)


def _is_upset(g: dict) -> bool:
    """True if a ranked team lost to a lower-ranked (or unranked) opponent.

    Uses the *current* poll rather than the rank as of the game's date, so
    this is only meaningful for games from the current week — good enough
    for a scoreboard flag, not for a season-long upset history.
    """
    if g["status"] != "final":
        return False
    try:
        home_score, away_score = int(g["home_score"]), int(g["away_score"])
    except (TypeError, ValueError):
        return False
    if home_score == away_score:
        return False
    if home_score > away_score:
        winner_rank, loser_rank = g["home_rank"], g["away_rank"]
    else:
        winner_rank, loser_rank = g["away_rank"], g["home_rank"]
    if loser_rank is None:
        return False
    return winner_rank is None or winner_rank > loser_rank


def _weekly_standout_label(row) -> str:
    goals, assists = row["goals"], row["assists"]
    if goals >= 3:
        return "Hat Trick" if goals == 3 else f"{goals} Goals"
    if assists >= 2:
        return f"{assists} Assists"
    if goals == 2:
        return "Brace"
    return ""


def _leaderboard(rows, key: str, n: int = 5) -> list[dict]:
    """Top n rows by `key`, descending, extended to include every row tied
    with the row at the cutoff (so ties aren't split arbitrarily). Adds a
    "rank" field using standard competition ranking (tied rows share a rank,
    e.g. 1, 2, 2, 4)."""
    ordered = sorted((dict(r) for r in rows), key=lambda r: r[key] or 0, reverse=True)
    if len(ordered) > n:
        cutoff = ordered[n - 1][key] or 0
        ordered = [r for r in ordered if (r[key] or 0) >= cutoff]
    rank = 0
    prev_value = None
    for i, r in enumerate(ordered, start=1):
        value = r[key] or 0
        if value != prev_value:
            rank = i
            prev_value = value
        r["rank"] = rank
    return ordered


def _is_featured(g: dict, top30_seos: set[str]) -> bool:
    """True if two currently-ranked teams are playing each other, or a
    currently-ranked team is playing a team in the top 30 overall records.
    Never true if either side is a non-D1 (D2/D3/NAIA/NCCAA) program."""
    if not reference_data.is_d1(g["away_seo"], g["away_conference"]):
        return False
    if not reference_data.is_d1(g["home_seo"], g["home_conference"]):
        return False
    away_ranked = g["away_rank"] is not None
    home_ranked = g["home_rank"] is not None
    if away_ranked and home_ranked:
        return True
    if away_ranked and g["home_seo"] in top30_seos:
        return True
    if home_ranked and g["away_seo"] in top30_seos:
        return True
    return False


def _match_badge(g: dict) -> tuple[str, str] | None:
    """(css_class, label) for the compact left-edge status badge, or None
    for a scheduled match, whose slot in the row shows the kickoff time
    instead."""
    if g["status"] == "final":
        return "b-final", "FT"
    if g["status"] == "live":
        label = reference_data.period_short(g["current_period"])
        if label == "HT":
            return "b-ht", "HT"
        return "b-live", label
    return None


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    date: str | None = None,
    conference: str | None = None,
    conf_only: bool = False,
):
    day = _parse_date(date)
    with db.get_conn() as conn:
        games = [dict(g) for g in db.get_games_for_date(conn, day.isoformat(), conference)]
        conferences = db.get_conferences(conn)
        rank_map = _rank_map(db.get_latest_rankings(conn))
        top30_seos = standings.top_teams_by_record(db.get_all_final_games(conn))
    for g in games:
        g["away_rank"], g["away_prev_rank"] = _rank_lookup(rank_map, g["away_seo"])
        g["home_rank"], g["home_prev_rank"] = _rank_lookup(rank_map, g["home_seo"])
        g["is_upset"] = _is_upset(g)
        g["conference_match"] = (
            reference_data.conference_short_name(g["home_conference"])
            if g["home_conference"] and g["home_conference"] == g["away_conference"]
            else ""
        )
        badge = _match_badge(g)
        g["badge_class"], g["badge_label"] = badge if badge else ("", "")
        g["start_time_main"], g["start_time_tz"] = reference_data.split_time_tz(g["start_time"])
    if conf_only:
        games = [g for g in games if g["conference_match"]]
    featured_games = [g for g in games if _is_featured(g, top30_seos)]
    games = [g for g in games if not _is_featured(g, top30_seos)]
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "day": day,
            "is_today": day == _today_eastern(),
            "prev_day": day - dt.timedelta(days=1),
            "next_day": day + dt.timedelta(days=1),
            "games": games,
            "featured_games": featured_games,
            "conferences": conferences,
            "selected_conference": conference,
            "conf_only": conf_only,
            "conference_label": _conference_label,
        },
    )


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    team_results = []
    player_results = []
    if q.strip():
        query = q.strip()
        with db.get_conn() as conn:
            team_results = db.search_teams(conn, query)
            player_results = db.search_players(conn, query)
        if len(team_results) + len(player_results) == 1:
            if team_results:
                return RedirectResponse(f"/team/{team_results[0]['seo']}")
            p = player_results[0]
            return RedirectResponse(
                f"/player?team={p['team_seo']}&first={quote(p['first_name'] or '')}&last={quote(p['last_name'] or '')}"
            )
    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "q": q,
            "team_results": team_results,
            "player_results": player_results,
            "conference_label_fn": _conference_label,
        },
    )


@app.get("/team/{seo}", response_class=HTMLResponse)
def team_detail(request: Request, seo: str):
    with db.get_conn() as conn:
        team = db.get_team(conn, seo)
        games = db.get_team_games(conn, seo)
        roster = db.get_team_roster_stats(conn, seo)
        rank_history = list(reversed(reference_data.group_rankings_by_week(db.get_ranking_history(conn, seo))))
        current_rank, current_prev_rank = _rank_lookup(_rank_map(db.get_latest_rankings(conn)), seo)
    rows, record = standings.build_team_schedule(games, seo, team["conference"] if team else None)
    team_totals = standings.build_team_totals(roster, rows)
    if rank_history and rank_history[0]["record"] is None:
        # Latest poll snapshot hasn't been backfilled with a record yet --
        # use the team's actual current record instead of leaving it blank.
        rank_history[0] = dict(rank_history[0])
        rank_history[0]["record"] = f"{record['overall_w']}-{record['overall_l']}-{record['overall_d']}"
    return templates.TemplateResponse(
        "team.html",
        {
            "request": request,
            "team": team,
            "seo": seo,
            "rows": rows,
            "record": record,
            "roster": roster,
            "team_totals": team_totals,
            "current_rank": current_rank,
            "current_prev_rank": current_prev_rank,
            "rank_history": rank_history,
            "conference_label_fn": _conference_label,
        },
    )


@app.get("/conference/{conference}", response_class=HTMLResponse)
def conference_detail(request: Request, conference: str):
    with db.get_conn() as conn:
        conferences = db.get_conferences(conn)
        games = db.get_conference_games(conn, conference)
        rank_map = _rank_map(db.get_latest_rankings(conn))
    table = standings.build_conference_table(games, conference)
    team_states = reference_data.get_team_states()
    for t in table:
        t["state"] = team_states.get(t["seo"], "")
        t["rank"], t["prev_rank"] = _rank_lookup(rank_map, t["seo"])

    table.sort(key=lambda t: -(t["overall_w"] * 3 + t["overall_d"]))
    return templates.TemplateResponse(
        "conference.html",
        {
            "request": request,
            "conference": conference,
            "conference_label": _conference_label(conference, full=True),
            "conferences": conferences,
            "conference_label_fn": _conference_label,
            "table": table,
        },
    )


@app.get("/teams", response_class=HTMLResponse)
def teams_list(request: Request, conference: str | None = None, state: str | None = None):
    with db.get_conn() as conn:
        games = db.get_all_final_games(conn)
        conferences = db.get_conferences(conn)
        rank_map = _rank_map(db.get_latest_rankings(conn))
    table = standings.build_all_teams_table(games)
    team_states = reference_data.get_team_states()
    for t in table:
        t["state"] = team_states.get(t["seo"], "")
        t["rank"], t["prev_rank"] = _rank_lookup(rank_map, t["seo"])

    table.sort(
        key=lambda t: (
            -(t["overall_w"] * 3 + t["overall_d"]),
            t["rank"] if t["rank"] is not None else 999,
        )
    )

    states = sorted({t["state"] for t in table if t["state"]})

    if conference:
        table = [t for t in table if t["conference"] == conference]
    if state:
        table = [t for t in table if t["state"] == state]

    return templates.TemplateResponse(
        "teams.html",
        {
            "request": request,
            "table": table,
            "conference_label_fn": _conference_label,
            "conferences": conferences,
            "states": states,
            "selected_conference": conference,
            "selected_state": state,
        },
    )


@app.get("/rank-history", response_class=HTMLResponse)
def rank_history_page(request: Request):
    with db.get_conn() as conn:
        rows = db.get_all_ranking_history(conn)
    history = reference_data.build_rank_history(rows)
    week_index = {w: i for i, w in enumerate(history["weeks"])}
    chart_data = {
        "weeks": history["weeks"],
        "teams": [
            {
                "seo": t["seo"],
                "name": t["name"],
                "points": [[week_index[p["week_start"]], p["rank"]] for p in t["series"]],
            }
            for t in history["teams"]
        ],
    }
    return templates.TemplateResponse(
        "rank-history.html",
        {
            "request": request,
            "weeks": history["weeks"],
            "teams": history["teams"],
            "chart_data": chart_data,
        },
    )


PLAYERS_PER_PAGE = 50

_PLAYER_SORT_KEYS = {
    "name": lambda p: (p["last_name"] or "", p["first_name"] or ""),
    "team": lambda p: p["team_name"] or "",
    "gp": lambda p: p["games_played"] or 0,
    "min": lambda p: p["avg_minutes"] or 0,
    "g": lambda p: p["goals"] or 0,
    "a": lambda p: p["assists"] or 0,
    "s": lambda p: p["shots"] or 0,
    "sot": lambda p: p["shots_on_goal"] or 0,
    "sav": lambda p: p["saves"] or 0,
}


@app.get("/players", response_class=HTMLResponse)
def players_list(
    request: Request,
    conference: str | None = None,
    team: str | None = None,
    state: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
    page: int = 1,
):
    with db.get_conn() as conn:
        roster = [dict(r) for r in db.get_all_players_roster_stats(conn)]
        conferences = db.get_conferences(conn)

    team_states = reference_data.get_team_states()
    for p in roster:
        p["state"] = team_states.get(p["team_seo"], "")

    states = sorted({p["state"] for p in roster if p["state"]})
    teams = sorted(
        {(p["team_seo"], p["team_name"], p["team_conference"]) for p in roster if p["team_seo"]},
        key=lambda x: x[1] or "",
    )

    if conference:
        roster = [p for p in roster if p["team_conference"] == conference]
    if team:
        roster = [p for p in roster if p["team_seo"] == team]
    if state:
        roster = [p for p in roster if p["state"] == state]

    if sort not in _PLAYER_SORT_KEYS:
        sort = "g"
    if dir not in ("asc", "desc"):
        dir = "desc" if sort == "g" else "asc"
    roster.sort(key=_PLAYER_SORT_KEYS[sort], reverse=(dir == "desc"))

    total = len(roster)
    total_pages = max(1, -(-total // PLAYERS_PER_PAGE))
    page = min(max(page, 1), total_pages)
    start = (page - 1) * PLAYERS_PER_PAGE
    roster_page = roster[start : start + PLAYERS_PER_PAGE]

    return templates.TemplateResponse(
        "players.html",
        {
            "request": request,
            "roster": roster_page,
            "conference_label_fn": _conference_label,
            "conferences": conferences,
            "teams": teams,
            "states": states,
            "selected_conference": conference,
            "selected_team": team,
            "selected_state": state,
            "sort": sort,
            "dir": dir,
            "page": page,
            "total_pages": total_pages,
        },
    )


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    today = _today_eastern()
    since_date = today - dt.timedelta(days=6)

    with db.get_conn() as conn:
        standouts = [
            dict(s) for s in db.get_weekly_standouts(conn, since_date.isoformat(), today.isoformat())
        ]
        roster = [dict(r) for r in db.get_all_players_roster_stats(conn)]
        clean_sheets = [dict(r) for r in db.get_clean_sheet_leaders(conn)]

    for s in standouts:
        s["label"] = _weekly_standout_label(s)
        s["opponent_name"] = s["away_name"] if s["is_home"] else s["home_name"]

    for r in roster:
        r["total_cards"] = (r["yellow_cards"] or 0) + (r["red_cards"] or 0)

    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request,
            "standouts": standouts,
            "goals_leaders": _leaderboard(roster, "goals"),
            "assists_leaders": _leaderboard(roster, "assists"),
            "card_leaders": _leaderboard(roster, "total_cards"),
            "clean_sheet_leaders": _leaderboard(clean_sheets, "clean_sheets"),
        },
    )


@app.get("/player", response_class=HTMLResponse)
def player_detail(request: Request, team: str, first: str, last: str):
    with db.get_conn() as conn:
        rows = db.get_player_games(conn, team, first, last)
    if not rows:
        return templates.TemplateResponse(
            "player.html",
            {"request": request, "player": None, "log": [], "totals": None},
        )
    log, totals = standings.build_player_game_log(rows)
    is_home = bool(rows[0]["is_home"])
    latest = rows[-1]
    player = {
        "first_name": rows[0]["first_name"],
        "last_name": rows[0]["last_name"],
        "team_seo": team,
        "team_name": rows[0]["home_name"] if is_home else rows[0]["away_name"],
        "team_name_short": rows[0]["home_name_short"] if is_home else rows[0]["away_name_short"],
        "team_conference": rows[0]["home_conference"] if is_home else rows[0]["away_conference"],
        "position": latest["position"],
        "number": latest["number"],
    }
    return templates.TemplateResponse(
        "player.html",
        {"request": request, "player": player, "log": log, "totals": totals},
    )


@app.get("/game/{game_id}", response_class=HTMLResponse)
def game_detail(request: Request, game_id: str):
    with db.get_conn() as conn:
        game = db.get_game(conn, game_id)
        stats = db.get_player_stats(conn, game_id)

        if game is not None and game["status"] == "final" and not stats:
            sync.sync_missing_boxscores(conn)
            stats = db.get_player_stats(conn, game_id)

        if game is not None:
            game = dict(game)
            rank_map = _rank_map(db.get_latest_rankings(conn))
            game["away_rank"], game["away_prev_rank"] = _rank_lookup(rank_map, game["away_seo"])
            game["home_rank"], game["home_prev_rank"] = _rank_lookup(rank_map, game["home_seo"])

        team_stats = db.get_team_stats(conn, game_id)

    home_stats = [s for s in stats if s["is_home"]]
    away_stats = [s for s in stats if not s["is_home"]]

    return templates.TemplateResponse(
        "game.html",
        {
            "request": request,
            "game": game,
            "home_stats": home_stats,
            "away_stats": away_stats,
            "team_stats": team_stats,
        },
    )


@app.get("/api/games")
def api_games(date: str | None = None):
    day = _parse_date(date)
    with db.get_conn() as conn:
        games = db.get_games_for_date(conn, day.isoformat())
    return JSONResponse([dict(g) for g in games])


@app.get("/api/games/{game_id}/boxscore")
def api_boxscore(game_id: str):
    with db.get_conn() as conn:
        stats = db.get_player_stats(conn, game_id)
    return JSONResponse([dict(s) for s in stats])


_last_manual_sync: dt.datetime | None = None
_MANUAL_SYNC_COOLDOWN = dt.timedelta(minutes=1)


@app.post("/api/sync-now")
def api_sync_now():
    """Unauthenticated by design (read-only site, nothing to protect), but
    throttled so a public caller can't hammer the upstream NCAA API feed."""
    global _last_manual_sync
    now = dt.datetime.now(dt.timezone.utc)
    if _last_manual_sync and now - _last_manual_sync < _MANUAL_SYNC_COOLDOWN:
        return JSONResponse({"status": "throttled"}, status_code=429)
    _last_manual_sync = now
    sync.run_full_sync()
    return {"status": "ok"}
