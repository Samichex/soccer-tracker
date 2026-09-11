import datetime as dt
import json
import sqlite3
from contextlib import contextmanager

from . import config, validate

# Shared by every query below that returns games rows: prefers a team's full
# name (only known once its boxscore has synced) over the short scoreboard
# name always stored on the game row itself.
_GAME_COLUMNS = """
    g.id, g.date, g.start_time, g.start_epoch, g.status, g.current_period,
    g.home_seo, COALESCE(th.name_full, g.home_name) AS home_name, g.home_name AS home_name_short,
    g.home_score, g.home_conference,
    g.away_seo, COALESCE(ta.name_full, g.away_name) AS away_name, g.away_name AS away_name_short,
    g.away_score, g.away_conference,
    g.network, g.url, g.updated_at
"""
_GAME_JOINS = """
    FROM games g
    LEFT JOIN teams th ON th.seo = g.home_seo
    LEFT JOIN teams ta ON ta.seo = g.away_seo
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,            -- YYYY-MM-DD
    start_time TEXT,
    start_epoch INTEGER,
    status TEXT,                   -- pre | live | final
    current_period TEXT,
    home_seo TEXT,
    home_name TEXT,
    home_score TEXT,
    home_conference TEXT,
    away_seo TEXT,
    away_name TEXT,
    away_score TEXT,
    away_conference TEXT,
    network TEXT,
    url TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_games_date ON games(date);

CREATE TABLE IF NOT EXISTS player_stats (
    game_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    team_seo TEXT,
    is_home INTEGER,
    first_name TEXT,
    last_name TEXT,
    number TEXT,
    position TEXT,
    starter INTEGER,
    minutes_played TEXT,
    goals TEXT,
    assists TEXT,
    shots TEXT,
    shots_on_goal TEXT,
    saves TEXT,
    yellow_cards TEXT,
    red_cards TEXT,
    fouls TEXT,
    green_cards TEXT,
    game_winning_goals TEXT,
    penalty_goals TEXT,
    participated INTEGER,
    PRIMARY KEY (game_id, team_id, number, last_name, first_name)
);

-- Speeds up per-team lookups (team page roster, player game log) as this
-- table grows past a season's worth of box scores.
CREATE INDEX IF NOT EXISTS idx_player_stats_team_seo ON player_stats(team_seo);

CREATE TABLE IF NOT EXISTS team_rankings (
    observed_date TEXT NOT NULL,   -- date of the sync that captured this, not a poll-release date
    school TEXT NOT NULL,          -- raw name as printed in the poll; may not match games.*_name exactly
    seo TEXT,                      -- best-effort match against games.home_seo/away_seo; NULL if unresolved
    rank INTEGER,
    prev_rank TEXT,                -- "NR" when previously unranked, so kept as text rather than INTEGER
    points TEXT,
    first_place_votes TEXT,
    record TEXT,
    PRIMARY KEY (observed_date, school)
);

CREATE INDEX IF NOT EXISTS idx_team_rankings_seo ON team_rankings(seo);

CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Team identity, built up from two sources that arrive at different times:
-- the scoreboard feed (name, conference) as soon as a game is scheduled,
-- and the boxscore feed (team_id, name_full, mascot, color) only once a
-- game goes final. Either upsert leaves columns it doesn't know about alone.
CREATE TABLE IF NOT EXISTS teams (
    seo TEXT PRIMARY KEY,
    team_id TEXT,
    name TEXT,              -- short display name, e.g. "Iona"
    name_full TEXT,         -- e.g. "Iona University"
    mascot TEXT,            -- e.g. "Gaels" (boxscore's "teamName")
    name6_char TEXT,
    color TEXT,
    conference TEXT,
    updated_at TEXT
);

-- Full boxscore API response, untouched. Kept alongside the parsed columns
-- in player_stats so fields we don't parse yet aren't lost to a re-sync.
CREATE TABLE IF NOT EXISTS game_boxscore_raw (
    game_id TEXT PRIMARY KEY,
    raw_json TEXT NOT NULL,
    fetched_at TEXT
);
"""


@contextmanager
def get_conn():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL lets page requests read while the background sync thread writes,
    # instead of blocking behind it; busy_timeout retries briefly on the
    # rare write/write collision instead of raising "database is locked".
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(player_stats)")}
        if "participated" not in cols:
            conn.execute("ALTER TABLE player_stats ADD COLUMN participated INTEGER")
        for col in ("fouls", "green_cards", "game_winning_goals", "penalty_goals"):
            if col not in cols:
                conn.execute(f"ALTER TABLE player_stats ADD COLUMN {col} TEXT")


def upsert_game(conn, game: dict, date_str: str):
    g = game["game"]
    game_id = g["gameID"]
    status = validate.validate_game_status(game_id, g.get("gameState"))
    home_score = validate.validate_score(game_id, "home", g["home"].get("score"))
    away_score = validate.validate_score(game_id, "away", g["away"].get("score"))
    conn.execute(
        """
        INSERT INTO games (
            id, date, start_time, start_epoch, status, current_period,
            home_seo, home_name, home_score, home_conference,
            away_seo, away_name, away_score, away_conference,
            network, url, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            start_time=excluded.start_time,
            start_epoch=excluded.start_epoch,
            status=COALESCE(excluded.status, games.status),
            current_period=excluded.current_period,
            home_score=COALESCE(excluded.home_score, games.home_score),
            away_score=COALESCE(excluded.away_score, games.away_score),
            network=excluded.network,
            updated_at=datetime('now')
        """,
        (
            game_id,
            date_str,
            g.get("startTime"),
            _safe_int(g.get("startTimeEpoch")),
            status,
            g.get("currentPeriod"),
            g["home"]["names"].get("seo"),
            g["home"]["names"].get("short"),
            home_score,
            (g["home"].get("conferences") or [{}])[0].get("conferenceSeo"),
            g["away"]["names"].get("seo"),
            g["away"]["names"].get("short"),
            away_score,
            (g["away"].get("conferences") or [{}])[0].get("conferenceSeo"),
            g.get("network"),
            g.get("url"),
        ),
    )
    for side in ("home", "away"):
        team = g[side]
        upsert_team_basic(
            conn,
            seo=team["names"].get("seo"),
            name=team["names"].get("short"),
            conference=(team.get("conferences") or [{}])[0].get("conferenceSeo"),
        )


def upsert_team_basic(conn, seo: str | None, name: str | None, conference: str | None):
    """Fill in what the scoreboard feed knows about a team. Safe to call
    every sync cycle; never touches columns only the boxscore feed fills."""
    if not seo:
        return
    conn.execute(
        """
        INSERT INTO teams (seo, name, conference, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(seo) DO UPDATE SET
            name=excluded.name,
            conference=excluded.conference,
            updated_at=excluded.updated_at
        """,
        (seo, name, conference),
    )


def upsert_team_detail(
    conn,
    seo: str | None,
    team_id: str | None,
    name_full: str | None,
    mascot: str | None,
    name6_char: str | None,
    color: str | None,
):
    """Fill in what the boxscore feed knows about a team, once a game goes
    final. Never touches name/conference — those belong to upsert_team_basic."""
    if not seo:
        return
    conn.execute(
        """
        INSERT INTO teams (seo, team_id, name_full, mascot, name6_char, color, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(seo) DO UPDATE SET
            team_id=excluded.team_id,
            name_full=excluded.name_full,
            mascot=excluded.mascot,
            name6_char=excluded.name6_char,
            color=excluded.color,
            updated_at=excluded.updated_at
        """,
        (seo, team_id, name_full, mascot, name6_char, color),
    )


def games_missing_boxscore(conn):
    rows = conn.execute(
        """
        SELECT g.id FROM games g
        WHERE g.status = 'final'
        AND NOT EXISTS (SELECT 1 FROM player_stats p WHERE p.game_id = g.id)
        """
    ).fetchall()
    return [r["id"] for r in rows]


def replace_player_stats(conn, game_id: str, rows: list[dict]):
    for r in rows:
        validate.sanitize_player_row_stats(game_id, r)
    validate.flag_duplicate_players(game_id, rows)

    conn.execute("DELETE FROM player_stats WHERE game_id = ?", (game_id,))
    conn.executemany(
        """
        INSERT INTO player_stats (
            game_id, team_id, team_seo, is_home, first_name, last_name, number,
            position, starter, minutes_played, goals, assists, shots,
            shots_on_goal, saves, yellow_cards, red_cards, fouls, green_cards,
            game_winning_goals, penalty_goals, participated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                game_id,
                r["team_id"],
                r["team_seo"],
                r["is_home"],
                r["first_name"],
                r["last_name"],
                r["number"],
                r["position"],
                r["starter"],
                r["minutes_played"],
                r["goals"],
                r["assists"],
                r["shots"],
                r["shots_on_goal"],
                r["saves"],
                r["yellow_cards"],
                r["red_cards"],
                r["fouls"],
                r["green_cards"],
                r["game_winning_goals"],
                r["penalty_goals"],
                r["participated"],
            )
            for r in rows
        ],
    )


def upsert_raw_boxscore(conn, game_id: str, raw_json: str):
    conn.execute(
        """
        INSERT INTO game_boxscore_raw (game_id, raw_json, fetched_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(game_id) DO UPDATE SET
            raw_json=excluded.raw_json,
            fetched_at=excluded.fetched_at
        """,
        (game_id, raw_json),
    )


def get_raw_boxscore(conn, game_id: str):
    row = conn.execute(
        "SELECT raw_json FROM game_boxscore_raw WHERE game_id = ?", (game_id,)
    ).fetchone()
    return row["raw_json"] if row else None


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def get_team_stats(conn, game_id: str):
    raw_json = get_raw_boxscore(conn, game_id)
    if not raw_json:
        return None
    box = json.loads(raw_json)
    is_home_by_team_id = {str(t["teamId"]): t.get("isHome") for t in box.get("teams", [])}

    result = {}
    for team_box in box.get("teamBoxscore", []):
        side = "home" if is_home_by_team_id.get(str(team_box.get("teamId"))) else "away"
        ts = team_box.get("teamStats") or {}
        penalties = ts.get("penalties") or {}
        goalie = ts.get("goalie") or {}
        result[side] = {
            "shots": _as_int(ts.get("shots")),
            "shots_on_goal": _as_int(ts.get("shotsOnGoal")),
            "corners": _as_int(ts.get("corners")),
            "offsides": _as_int(ts.get("offsides")),
            "fouls": _as_int(penalties.get("fouls")),
            "yellow_cards": _as_int(penalties.get("yellowCards")),
            "red_cards": _as_int(penalties.get("redCards")),
            "saves": _as_int(goalie.get("saves")),
        }

    if "home" not in result or "away" not in result:
        return None
    return result


_RED_CARD_JOINS = """
    LEFT JOIN (
        SELECT game_id, SUM(CAST(red_cards AS INTEGER)) AS red_cards
        FROM player_stats WHERE is_home = 1 GROUP BY game_id
    ) rh ON rh.game_id = g.id
    LEFT JOIN (
        SELECT game_id, SUM(CAST(red_cards AS INTEGER)) AS red_cards
        FROM player_stats WHERE is_home = 0 GROUP BY game_id
    ) ra ON ra.game_id = g.id
"""
_GAME_COLUMNS_WITH_CARDS = f"""
    {_GAME_COLUMNS},
    COALESCE(rh.red_cards, 0) AS home_red_cards,
    COALESCE(ra.red_cards, 0) AS away_red_cards
"""


def get_games_for_date(conn, date_str: str, conference: str | None = None):
    joins = f"{_GAME_JOINS} {_RED_CARD_JOINS}"
    if conference:
        return conn.execute(
            f"""
            SELECT {_GAME_COLUMNS_WITH_CARDS} {joins}
            WHERE g.date = ? AND (g.home_conference = ? OR g.away_conference = ?)
            ORDER BY g.start_epoch
            """,
            (date_str, conference, conference),
        ).fetchall()
    return conn.execute(
        f"SELECT {_GAME_COLUMNS_WITH_CARDS} {joins} WHERE g.date = ? ORDER BY g.start_epoch",
        (date_str,),
    ).fetchall()


def get_game(conn, game_id: str):
    return conn.execute(
        f"SELECT {_GAME_COLUMNS} {_GAME_JOINS} WHERE g.id = ?", (game_id,)
    ).fetchone()


def get_conferences(conn):
    rows = conn.execute(
        """
        SELECT DISTINCT conference FROM (
            SELECT home_conference AS conference FROM games
            UNION
            SELECT away_conference AS conference FROM games
        )
        WHERE conference IS NOT NULL AND conference != ''
        ORDER BY conference
        """
    ).fetchall()
    return [r["conference"] for r in rows]


def get_team(conn, seo: str):
    return conn.execute("SELECT * FROM teams WHERE seo = ?", (seo,)).fetchone()


def search_teams(conn, query: str, limit: int = 20):
    like = f"%{query}%"
    return conn.execute(
        """
        SELECT * FROM teams
        WHERE name LIKE ? OR name_full LIKE ? OR mascot LIKE ?
        ORDER BY name
        LIMIT ?
        """,
        (like, like, like, limit),
    ).fetchall()


def search_players(conn, query: str, limit: int = 20):
    like = f"%{query}%"
    return conn.execute(
        """
        SELECT
            ps.first_name, ps.last_name, ps.team_seo,
            COALESCE(MAX(t.name_full), MAX(t.name)) AS team_name, MAX(t.conference) AS team_conference,
            MAX(ps.number) AS number,
            MAX(ps.position) AS position
        FROM player_stats ps
        LEFT JOIN teams t ON t.seo = ps.team_seo
        WHERE COALESCE(ps.participated, 1) = 1
          AND (
              ps.first_name LIKE ?
              OR ps.last_name LIKE ?
              OR (ps.first_name || ' ' || ps.last_name) LIKE ?
          )
        GROUP BY ps.first_name, ps.last_name, ps.team_seo
        ORDER BY ps.last_name ASC, ps.first_name ASC
        LIMIT ?
        """,
        (like, like, like, limit),
    ).fetchall()


def get_team_games(conn, seo: str):
    return conn.execute(
        f"""
        SELECT {_GAME_COLUMNS} {_GAME_JOINS}
        WHERE g.home_seo = ? OR g.away_seo = ?
        ORDER BY g.start_epoch
        """,
        (seo, seo),
    ).fetchall()


def get_conference_games(conn, conference: str):
    return conn.execute(
        f"""
        SELECT {_GAME_COLUMNS} {_GAME_JOINS}
        WHERE g.status = 'final' AND (g.home_conference = ? OR g.away_conference = ?)
        ORDER BY g.start_epoch
        """,
        (conference, conference),
    ).fetchall()


def get_all_final_games(conn):
    return conn.execute(
        f"SELECT {_GAME_COLUMNS} {_GAME_JOINS} WHERE g.status = 'final' ORDER BY g.start_epoch"
    ).fetchall()


def get_weekly_standouts(conn, since_date: str, through_date: str):
    """Standout individual box scores (2+ goals or 2+ assists) from final
    games in the rolling [since_date, through_date] window, ranked by
    whichever is higher for that player: goals or assists."""
    return conn.execute(
        f"""
        SELECT {_GAME_COLUMNS}, ps.first_name, ps.last_name, ps.team_seo, ps.is_home,
               CAST(ps.goals AS INTEGER) AS goals,
               CAST(ps.assists AS INTEGER) AS assists,
               COALESCE(t.name_full, t.name) AS player_team_name
        {_GAME_JOINS}
        JOIN player_stats ps ON ps.game_id = g.id
        LEFT JOIN teams t ON t.seo = ps.team_seo
        WHERE g.status = 'final' AND g.date BETWEEN ? AND ?
          AND COALESCE(ps.participated, 1) = 1
          AND (
              CAST(ps.goals AS INTEGER) >= 2
              OR CAST(ps.assists AS INTEGER) >= 2
          )
        ORDER BY
            MAX(CAST(ps.goals AS INTEGER), CAST(ps.assists AS INTEGER)) DESC,
            CAST(ps.goals AS INTEGER) DESC,
            CAST(ps.assists AS INTEGER) DESC,
            g.date DESC
        """,
        (since_date, through_date),
    ).fetchall()


def get_player_stats(conn, game_id: str):
    return conn.execute(
        "SELECT * FROM player_stats WHERE game_id = ? ORDER BY is_home DESC, starter DESC",
        (game_id,),
    ).fetchall()


def get_all_players_roster_stats(conn):
    return conn.execute(
        """
        SELECT
            ps.first_name, ps.last_name, ps.team_seo,
            COALESCE(MAX(t.name_full), MAX(t.name)) AS team_name, MAX(t.conference) AS team_conference,
            MAX(ps.number) AS number,
            MAX(ps.position) AS position,
            COUNT(DISTINCT ps.game_id) AS games_played,
            ROUND(AVG(CAST(ps.minutes_played AS REAL)), 1) AS avg_minutes,
            SUM(CAST(ps.goals AS INTEGER)) AS goals,
            SUM(CAST(ps.assists AS INTEGER)) AS assists,
            SUM(CAST(ps.shots AS INTEGER)) AS shots,
            SUM(CAST(ps.shots_on_goal AS INTEGER)) AS shots_on_goal,
            SUM(CAST(ps.saves AS INTEGER)) AS saves,
            SUM(CAST(ps.yellow_cards AS INTEGER)) AS yellow_cards,
            SUM(CAST(ps.red_cards AS INTEGER)) AS red_cards
        FROM player_stats ps
        LEFT JOIN teams t ON t.seo = ps.team_seo
        WHERE COALESCE(ps.participated, 1) = 1
        GROUP BY ps.first_name, ps.last_name, ps.team_seo
        ORDER BY ps.last_name ASC, ps.first_name ASC
        """
    ).fetchall()


def get_clean_sheet_leaders(conn):
    """Season clean-sheet counts per goalkeeper: a final game they
    participated in where their team conceded 0."""
    return conn.execute(
        """
        SELECT
            ps.first_name, ps.last_name, ps.team_seo,
            COALESCE(MAX(t.name_full), MAX(t.name)) AS team_name,
            MAX(t.conference) AS team_conference,
            COUNT(*) AS clean_sheets
        FROM player_stats ps
        JOIN games g ON g.id = ps.game_id
        LEFT JOIN teams t ON t.seo = ps.team_seo
        WHERE ps.position = 'GK'
          AND CAST(ps.participated AS INTEGER) = 1
          AND g.status = 'final'
          AND ((ps.is_home = 1 AND CAST(g.away_score AS INTEGER) = 0)
            OR (ps.is_home = 0 AND CAST(g.home_score AS INTEGER) = 0))
        GROUP BY ps.team_id, ps.first_name, ps.last_name
        ORDER BY clean_sheets DESC
        """
    ).fetchall()


def get_team_roster_stats(conn, seo: str):
    return conn.execute(
        """
        SELECT
            ps.first_name, ps.last_name,
            MAX(ps.number) AS number,
            MAX(ps.position) AS position,
            COUNT(DISTINCT ps.game_id) AS games_played,
            ROUND(AVG(CAST(ps.minutes_played AS REAL)), 1) AS avg_minutes,
            SUM(CAST(ps.goals AS INTEGER)) AS goals,
            SUM(CAST(ps.assists AS INTEGER)) AS assists,
            SUM(CAST(ps.shots AS INTEGER)) AS shots,
            SUM(CAST(ps.shots_on_goal AS INTEGER)) AS shots_on_goal,
            SUM(CAST(ps.saves AS INTEGER)) AS saves,
            SUM(CAST(ps.yellow_cards AS INTEGER)) AS yellow_cards,
            SUM(CAST(ps.red_cards AS INTEGER)) AS red_cards
        FROM player_stats ps
        WHERE ps.team_seo = ? AND COALESCE(ps.participated, 1) = 1
        GROUP BY ps.first_name, ps.last_name
        ORDER BY CAST(number AS INTEGER) ASC, ps.last_name ASC
        """,
        (seo,),
    ).fetchall()


def get_player_games(conn, team_seo: str, first_name: str, last_name: str):
    return conn.execute(
        f"""
        SELECT {_GAME_COLUMNS}, ps.*
        FROM player_stats ps
        JOIN games g ON g.id = ps.game_id
        LEFT JOIN teams th ON th.seo = g.home_seo
        LEFT JOIN teams ta ON ta.seo = g.away_seo
        WHERE ps.team_seo = ? AND UPPER(ps.first_name) = UPPER(?) AND UPPER(ps.last_name) = UPPER(?)
        ORDER BY g.start_epoch
        """,
        (team_seo, first_name, last_name),
    ).fetchall()



# Poll name -> scoreboard-feed name, for cases too irregular for the
# " State" -> " St." suffix rule below to catch (e.g. the scoreboard feed
# abbreviates "Florida Atlantic" but not "North Florida" or "West Florida").
_SCHOOL_NAME_ALIASES = {
    "Florida Atlantic": "Fla. Atlantic",
}


def resolve_seo_by_name(conn, school_name: str):
    """Best-effort match of a poll's school name against team names already
    seen in synced games. Returns None if no match is found.

    The poll spells out "State" (e.g. "Ohio State") while the scoreboard
    feed abbreviates it ("Ohio St."), so a second lookup swaps that in
    before giving up. A few other schools abbreviate irregularly enough
    that they're just hardcoded in _SCHOOL_NAME_ALIASES instead.
    """
    candidates = [school_name]
    if school_name.endswith(" State"):
        candidates.append(school_name[: -len("State")] + "St.")
    if school_name in _SCHOOL_NAME_ALIASES:
        candidates.append(_SCHOOL_NAME_ALIASES[school_name])

    for name in candidates:
        row = conn.execute(
            "SELECT seo FROM teams WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
        if row:
            return row["seo"]
    return None


def replace_rankings_for_date(conn, observed_date: str, rows: list[dict]):
    conn.execute("DELETE FROM team_rankings WHERE observed_date = ?", (observed_date,))
    conn.executemany(
        """
        INSERT INTO team_rankings (
            observed_date, school, seo, rank, prev_rank, points, first_place_votes, record
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        [
            (
                observed_date,
                r["school"],
                r["seo"],
                r["rank"],
                r["prev_rank"],
                r["points"],
                r["first_place_votes"],
                r["record"],
            )
            for r in rows
        ],
    )


def get_ranking_history(conn, seo: str):
    return conn.execute(
        "SELECT * FROM team_rankings WHERE seo = ? ORDER BY observed_date", (seo,)
    ).fetchall()


def get_all_ranking_history(conn):
    """Every team_rankings row for every team ever ranked, joined with
    `teams` for a display name/conference. Ordered by seo then
    observed_date so callers can group-by-seo and get each team's own
    snapshots in ascending order (same contract group_rankings_by_week
    already assumes for a single team). Rows with seo IS NULL are
    excluded -- no stable id to link/dedupe them to a team page."""
    return conn.execute(
        """
        SELECT tr.*, t.name AS team_name, t.name_full AS team_name_full,
               t.conference AS team_conference
        FROM team_rankings tr
        LEFT JOIN teams t ON t.seo = tr.seo
        WHERE tr.seo IS NOT NULL
        ORDER BY tr.seo, tr.observed_date
        """
    ).fetchall()


def get_latest_rankings(conn):
    """Most recent day's poll snapshot, with `prev_rank` backfilled from the
    prior snapshot's rank when the feed hasn't reported it yet -- e.g. right
    after a new poll drops, before United Soccer Coaches backfills that
    detail. Without this, rank-change arrows and "prev rank" displays would
    show nothing/NR for every team on the day a new poll lands."""
    rows = [dict(r) for r in conn.execute(
        """
        SELECT * FROM team_rankings
        WHERE observed_date = (SELECT MAX(observed_date) FROM team_rankings)
        ORDER BY rank
        """
    )]
    missing = [r for r in rows if r["prev_rank"] in (None, "")]
    if rows and missing:
        prior_date = conn.execute(
            "SELECT MAX(observed_date) AS d FROM team_rankings WHERE observed_date < ?",
            (rows[0]["observed_date"],),
        ).fetchone()["d"]
        if prior_date:
            prior_ranks = {
                r["seo"]: r["rank"]
                for r in conn.execute(
                    "SELECT seo, rank FROM team_rankings WHERE observed_date = ?", (prior_date,)
                )
                if r["seo"]
            }
            for r in missing:
                if r["seo"] in prior_ranks:
                    r["prev_rank"] = str(prior_ranks[r["seo"]])
    return rows


def set_last_synced(conn, when: str):
    conn.execute(
        """
        INSERT INTO sync_meta (key, value) VALUES ('last_synced', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (when,),
    )


def get_last_synced(conn):
    row = conn.execute("SELECT value FROM sync_meta WHERE key = 'last_synced'").fetchone()
    return row["value"] if row else None


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
