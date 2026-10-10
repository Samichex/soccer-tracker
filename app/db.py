import datetime as dt
import json
import sqlite3
from contextlib import contextmanager

from . import config, validate

# Shared by every query below that returns games rows: prefers a team's full
# name (only known once its boxscore has synced) over the short scoreboard
# name always stored on the game row itself.
_GAME_COLUMNS = """
    g.id, g.date, g.season, g.start_time, g.start_epoch, g.status, g.current_period,
    g.home_seo, COALESCE(th.name_full, g.home_name) AS home_name, g.home_name AS home_name_short,
    g.home_score, g.home_conference,
    g.away_seo, COALESCE(ta.name_full, g.away_name) AS away_name, g.away_name AS away_name_short,
    g.away_score, g.away_conference,
    g.network, g.url, g.division, g.updated_at
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
    season TEXT,                   -- year the game was played, e.g. "2026" (see upsert_game)
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

-- D3 men's soccer's rankings feed has no single national poll -- it's ten
-- separate regional NPI leaderboards instead (see
-- config.REGIONAL_RANKINGS_DIVISIONS). A team's slot is identified by
-- (region, rank) rather than by school, so that's the key here instead of
-- team_rankings' (observed_date, division, school). prev_rank is kept
-- (always NULL -- the feed has no PREVIOUS-equivalent field) purely so
-- group_rankings_by_week/build_rank_history can be reused unchanged; its
-- own "fall back to last week's rank" logic fills it in at render time.
CREATE TABLE IF NOT EXISTS team_rankings_regional (
    observed_date TEXT NOT NULL,
    division TEXT NOT NULL,
    region INTEGER NOT NULL,       -- 1-10, parsed from the feed's "Region I".."Region X" headers
    rank INTEGER NOT NULL,
    school TEXT NOT NULL,
    seo TEXT,
    prev_rank TEXT,
    npi TEXT,
    record TEXT,                   -- in-division W-L-T
    PRIMARY KEY (observed_date, division, region, rank)
);

CREATE INDEX IF NOT EXISTS idx_team_rankings_regional_seo ON team_rankings_regional(seo);

CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Team identity, built up from three sources that arrive at different times:
-- the scoreboard feed (name, conference) as soon as a game is scheduled,
-- the boxscore feed (team_id, name_full, mascot, color) only once a game
-- goes final, and the NCAA directory backfill (orgid, athletic_url,
-- website_url, head_coach -- see app/backfill_ncaa_directory.py and
-- app/backfill_coaches.py) run manually, separately from live sync. Each
-- upsert leaves columns it doesn't know about alone.
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

        team_cols = {row["name"] for row in conn.execute("PRAGMA table_info(teams)")}
        for col, coltype in (
            ("orgid", "INTEGER"),
            ("athletic_url", "TEXT"),
            ("website_url", "TEXT"),
            ("head_coach", "TEXT"),
            # Public/private status from the NCAA directory's `privateFlag`
            # ("Y"/"N"), stored as 1/0. NULL until the directory backfill runs.
            ("is_private", "INTEGER"),
            # Authoritative division tag, populated only by the NCAA
            # directory backfill (a team's true division, independent of
            # which scoreboard feed happened to surface it -- see
            # upsert_game's `division` param, which tags the *game* by
            # sync source, not the team). Left NULL until that backfill
            # runs; do not backfill it here, since teams already in this
            # table can include stray non-D1 opponents picked up from a
            # D1 game they played, not D1 members themselves.
            ("division", "TEXT"),
        ):
            if col not in team_cols:
                conn.execute(f"ALTER TABLE teams ADD COLUMN {col} {coltype}")

        game_cols = {row["name"] for row in conn.execute("PRAGMA table_info(games)")}
        if "division" not in game_cols:
            # Which division's scoreboard feed this game was synced from
            # (see config.DIVISIONS/ENABLED_DIVISIONS). Every game already
            # in this table was synced before D3 support existed, so 'd1'
            # is an accurate backfill, not a guess.
            conn.execute("ALTER TABLE games ADD COLUMN division TEXT")
            conn.execute("UPDATE games SET division = 'd1' WHERE division IS NULL")

        if "season" not in game_cols:
            # A game's season is just the calendar year it was played in --
            # D1/D3 men's soccer never crosses a year boundary, so the first
            # 4 characters of `date` (YYYY-MM-DD) are exactly the season for
            # every row already in this table. New rows get it set directly
            # in upsert_game instead of relying on this backfill.
            conn.execute("ALTER TABLE games ADD COLUMN season TEXT")
            conn.execute("UPDATE games SET season = substr(date, 1, 4) WHERE season IS NULL")
        # Deferred until here (rather than in the static SCHEMA above)
        # because an upgrading DB's `games` table already exists by the
        # time executescript(SCHEMA) runs its CREATE TABLE IF NOT EXISTS as
        # a no-op -- `season` wouldn't exist there yet for CREATE INDEX to
        # reference. Safe to run unconditionally: IF NOT EXISTS, and by
        # this point every games table (fresh or migrated) has the column.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_games_season ON games(season)")

        tr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(team_rankings)")}
        if "division" not in tr_cols:
            # A second division's national-poll-shaped rankings (were one
            # ever added) would need its own row alongside D1's here, so the
            # primary key widens to include division alongside
            # (observed_date, school). SQLite can't ALTER a table's primary
            # key in place, so rebuild it -- safe here since every existing
            # row was synced before D3 support existed (same reasoning as
            # the games.division backfill above). D3 itself turned out not
            # to use this table at all -- see team_rankings_regional.
            conn.executescript(
                """
                ALTER TABLE team_rankings RENAME TO team_rankings_pre_division;
                CREATE TABLE team_rankings (
                    observed_date TEXT NOT NULL,
                    division TEXT NOT NULL DEFAULT 'd1',
                    school TEXT NOT NULL,
                    seo TEXT,
                    rank INTEGER,
                    prev_rank TEXT,
                    points TEXT,
                    first_place_votes TEXT,
                    record TEXT,
                    PRIMARY KEY (observed_date, division, school)
                );
                INSERT INTO team_rankings (
                    observed_date, division, school, seo, rank, prev_rank,
                    points, first_place_votes, record
                )
                SELECT observed_date, 'd1', school, seo, rank, prev_rank,
                       points, first_place_votes, record
                FROM team_rankings_pre_division;
                DROP TABLE team_rankings_pre_division;
                CREATE INDEX IF NOT EXISTS idx_team_rankings_seo ON team_rankings(seo);
                """
            )


def upsert_game(conn, game: dict, date_str: str, division: str = "d1"):
    g = game["game"]
    game_id = g["gameID"]
    status = validate.validate_game_status(game_id, g.get("gameState"))
    home_score = validate.validate_score(game_id, "home", g["home"].get("score"))
    away_score = validate.validate_score(game_id, "away", g["away"].get("score"))
    season = date_str[:4]
    conn.execute(
        """
        INSERT INTO games (
            id, date, season, start_time, start_epoch, status, current_period,
            home_seo, home_name, home_score, home_conference,
            away_seo, away_name, away_score, away_conference,
            network, url, division, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            start_time=excluded.start_time,
            start_epoch=excluded.start_epoch,
            status=COALESCE(excluded.status, games.status),
            current_period=excluded.current_period,
            home_score=COALESCE(excluded.home_score, games.home_score),
            away_score=COALESCE(excluded.away_score, games.away_score),
            network=excluded.network,
            -- A cross-division non-conference game can be synced from both
            -- divisions' scoreboards under the same gameID; keep whichever
            -- division tagged it first rather than flip-flopping.
            division=COALESCE(games.division, excluded.division),
            updated_at=datetime('now')
        """,
        (
            game_id,
            date_str,
            season,
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
            division,
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
    return game_id


def delete_superseded_games(conn, date_str: str, division: str, keep_ids: set[str]) -> int:
    """Remove this date/division's games that the scoreboard feed no longer
    returned, as long as they're still just a schedule placeholder.

    The NCAA occasionally reissues a game under a brand-new gameID close to
    kickoff (seen for Creighton @ Marquette on 2026-09-26: id 6616704 sat
    stuck at status='pre' forever once the feed moved the game to id
    6642598), leaving the old id as a duplicate "pre" entry that never
    resolves and pads a team's schedule with a game that already happened
    under a different id. Only a non-final row is ever removed here -- a
    completed result is never deleted just because a later feed call
    omitted it."""
    if keep_ids:
        placeholders = ",".join("?" for _ in keep_ids)
        rows = conn.execute(
            f"""
            SELECT id FROM games
            WHERE date = ? AND division = ? AND id NOT IN ({placeholders})
              AND (
                status != 'final'
                OR EXISTS (
                    -- The reissue can also land after the old id already
                    -- finaled (same Creighton/Marquette case: id 6616704
                    -- reached status='final' -- with Creighton's score
                    -- reversed, since the venue flipped too -- before the
                    -- feed moved on to id 6642598). Once the feed just
                    -- reconfirmed a kept row for the same two teams that
                    -- date, any other row for that matchup is the stale
                    -- leftover, final or not.
                    SELECT 1 FROM games AS kept
                    WHERE kept.id IN ({placeholders})
                      AND kept.date = games.date AND kept.division = games.division
                      AND ((kept.home_seo = games.home_seo AND kept.away_seo = games.away_seo)
                           OR (kept.home_seo = games.away_seo AND kept.away_seo = games.home_seo))
                )
              )
            """,
            (date_str, division, *keep_ids, *keep_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM games WHERE date = ? AND division = ? AND status != 'final'",
            (date_str, division),
        ).fetchall()

    remove_ids = [r["id"] for r in rows]
    if not remove_ids:
        return 0

    placeholders = ",".join("?" for _ in remove_ids)
    # A final row being removed can have its own boxscore already synced
    # under the old id -- drop those too, or they're left orphaned
    # pointing at a game id that no longer exists.
    conn.execute(f"DELETE FROM player_stats WHERE game_id IN ({placeholders})", remove_ids)
    conn.execute(f"DELETE FROM game_boxscore_raw WHERE game_id IN ({placeholders})", remove_ids)
    cur = conn.execute(f"DELETE FROM games WHERE id IN ({placeholders})", remove_ids)
    return cur.rowcount


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


def upsert_team_directory(
    conn,
    seo: str | None,
    orgid: int | None,
    athletic_url: str | None,
    website_url: str | None,
    division: str | None = None,
    is_private: bool | None = None,
):
    """Fill in what the NCAA directory backfill knows (see
    app/backfill_ncaa_directory.py). Never touches name/conference
    (upsert_team_basic) or name_full/mascot/name6_char/color
    (upsert_team_detail).

    `division` is the authoritative division tag (see the `teams.division`
    column comment in init_db) -- COALESCEd rather than overwritten with
    NULL, so a caller that doesn't pass it (or an older backfill run) never
    blows away a value a previous run already set, while a later backfill
    that *does* pass a division always wins, so reclassification (e.g. a
    school moving D2 -> D3) still gets picked up."""
    if not seo:
        return
    conn.execute(
        """
        INSERT INTO teams (seo, orgid, athletic_url, website_url, division, is_private, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(seo) DO UPDATE SET
            orgid=excluded.orgid,
            athletic_url=excluded.athletic_url,
            website_url=excluded.website_url,
            division=COALESCE(excluded.division, teams.division),
            is_private=excluded.is_private,
            updated_at=excluded.updated_at
        """,
        (seo, orgid, athletic_url, website_url, division, is_private),
    )


def set_head_coach(conn, seo: str | None, head_coach: str | None):
    """Fill in the Men's Soccer head coach scraped by
    app/backfill_coaches.py. A plain UPDATE, not an upsert -- a row must
    already exist with an `orgid` (from upsert_team_directory) for there to
    be anything meaningful to attach this to."""
    if not seo:
        return
    conn.execute(
        "UPDATE teams SET head_coach = ?, updated_at = datetime('now') WHERE seo = ?",
        (head_coach, seo),
    )


def get_teams_with_orgid(conn):
    """seo/orgid pairs for app/backfill_coaches.py to iterate -- only teams
    already matched to the NCAA directory by upsert_team_directory."""
    return conn.execute(
        "SELECT seo, orgid FROM teams WHERE orgid IS NOT NULL ORDER BY seo"
    ).fetchall()


def games_missing_boxscore(conn, since_date: str | None = None):
    """Final games with no player_stats yet. `since_date` (YYYY-MM-DD)
    limits this to games played on/after that date -- the background sync
    passes the start of its live window so a box score upstream will never
    serve (e.g. a handful of 2025 D3 games that 502 every time) isn't
    re-fetched every cycle forever. None means every game, however old."""
    date_clause = " AND g.date >= ?" if since_date is not None else ""
    params = (since_date,) if since_date is not None else ()
    rows = conn.execute(
        f"""
        SELECT g.id FROM games g
        WHERE g.status = 'final'{date_clause}
        AND NOT EXISTS (SELECT 1 FROM player_stats p WHERE p.game_id = g.id)
        """,
        params,
    ).fetchall()
    return [r["id"] for r in rows]


def has_live_games(conn, since_date: str) -> bool:
    """True if a game played on/after `since_date` (YYYY-MM-DD) is
    currently in progress. Only the live sync window counts: a game that
    aged out of it while still marked 'live' (the app was down when it
    finished, or upstream never finalized a suspended match) is never
    re-synced, so counting it would pin the background loop to its fast
    live-game interval forever."""
    row = conn.execute(
        "SELECT 1 FROM games WHERE status = 'live' AND date >= ? LIMIT 1", (since_date,)
    ).fetchone()
    return row is not None


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


# Correlated subqueries rather than joined GROUP BYs: each one is an
# index lookup (player_stats' primary key leads with game_id) for just the
# games being returned, where a joined aggregate summed every row in
# player_stats on every call (~185ms vs. <1ms on a two-season DB).
_GAME_COLUMNS_WITH_CARDS = f"""
    {_GAME_COLUMNS},
    (SELECT COALESCE(SUM(CAST(ps.red_cards AS INTEGER)), 0) FROM player_stats ps
     WHERE ps.game_id = g.id AND ps.is_home = 1) AS home_red_cards,
    (SELECT COALESCE(SUM(CAST(ps.red_cards AS INTEGER)), 0) FROM player_stats ps
     WHERE ps.game_id = g.id AND ps.is_home = 0) AS away_red_cards
"""


def get_games_for_date(conn, date_str: str, conference: str | None = None, division: str = "d1"):
    if conference:
        return conn.execute(
            f"""
            SELECT {_GAME_COLUMNS_WITH_CARDS} {_GAME_JOINS}
            WHERE g.date = ? AND g.division = ? AND (g.home_conference = ? OR g.away_conference = ?)
            ORDER BY g.start_epoch
            """,
            (date_str, division, conference, conference),
        ).fetchall()
    return conn.execute(
        f"SELECT {_GAME_COLUMNS_WITH_CARDS} {_GAME_JOINS} WHERE g.date = ? AND g.division = ? ORDER BY g.start_epoch",
        (date_str, division),
    ).fetchall()


def get_game(conn, game_id: str):
    return conn.execute(
        f"SELECT {_GAME_COLUMNS} {_GAME_JOINS} WHERE g.id = ?", (game_id,)
    ).fetchone()


def get_conferences(conn, division: str = "d1", season: str | None = None):
    season_clause = " AND season = ?" if season is not None else ""
    params = (division, division) if season is None else (division, season, division, season)
    rows = conn.execute(
        f"""
        SELECT DISTINCT conference FROM (
            SELECT home_conference AS conference FROM games WHERE division = ?{season_clause}
            UNION
            SELECT away_conference AS conference FROM games WHERE division = ?{season_clause}
        )
        WHERE conference IS NOT NULL AND conference != ''
        ORDER BY conference
        """,
        params,
    ).fetchall()
    return [r["conference"] for r in rows]


def get_available_seasons(conn, division: str = "d1") -> list[str]:
    """Every season with at least one synced game, newest first. The first
    entry is what nav/_resolve_season falls back to when nothing else picks
    a season (mirrors ENABLED_DIVISIONS[0] being the default division)."""
    rows = conn.execute(
        "SELECT DISTINCT season FROM games WHERE division = ? AND season IS NOT NULL ORDER BY season DESC",
        (division,),
    ).fetchall()
    return [r["season"] for r in rows]


def get_team(conn, seo: str):
    return conn.execute("SELECT * FROM teams WHERE seo = ?", (seo,)).fetchone()


def get_team_privacy(conn) -> dict:
    """seo -> is_private (0/1) for every team with a known value, from the
    NCAA directory backfill. Teams not yet backfilled are simply absent --
    treat a missing key as unknown, not as public (0)."""
    rows = conn.execute("SELECT seo, is_private FROM teams WHERE is_private IS NOT NULL").fetchall()
    return {r["seo"]: r["is_private"] for r in rows}


def search_teams(conn, query: str, limit: int = 20):
    """Note: not division-filtered at the SQL level -- `teams.division` is
    only reliably set once the NCAA directory backfill has run for that
    division, so callers that need to scope by division (see
    app/main.py's `search`) filter these rows afterward using
    reference_data.get_team_division, which falls back sensibly when
    `division` is still NULL."""
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
    """See search_teams's note on division filtering -- `team_division`
    here is likewise meant to be resolved through
    reference_data.get_team_division by the caller, not compared directly."""
    like = f"%{query}%"
    return conn.execute(
        """
        SELECT
            ps.first_name, ps.last_name, ps.team_seo,
            COALESCE(MAX(t.name_full), MAX(t.name)) AS team_name, MAX(t.conference) AS team_conference,
            MAX(t.division) AS team_division,
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


def get_team_games(conn, seo: str, season: str | None = None):
    season_clause = " AND g.season = ?" if season is not None else ""
    params = (seo, seo) if season is None else (seo, seo, season)
    return conn.execute(
        f"""
        SELECT {_GAME_COLUMNS} {_GAME_JOINS}
        WHERE (g.home_seo = ? OR g.away_seo = ?){season_clause}
        ORDER BY g.start_epoch
        """,
        params,
    ).fetchall()


def get_previous_meeting(conn, home_seo: str, away_seo: str, exclude_season: str | None = None):
    """Most recent completed game between these two teams, for the
    "played before" indicator shown on a not-yet-played game's page.
    `status = 'final'` already excludes the game being viewed (it can't be
    final yet), but exclude_season also rules out an earlier meeting the
    same season (e.g. a conference tournament rematch) since the feature
    is specifically about *previous-season* history."""
    season_clause = " AND g.season != ?" if exclude_season is not None else ""
    params = (home_seo, away_seo, away_seo, home_seo)
    if exclude_season is not None:
        params += (exclude_season,)
    return conn.execute(
        f"""
        SELECT {_GAME_COLUMNS} {_GAME_JOINS}
        WHERE g.status = 'final'
          AND ((g.home_seo = ? AND g.away_seo = ?) OR (g.home_seo = ? AND g.away_seo = ?)){season_clause}
        ORDER BY g.start_epoch DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def get_conference_games(conn, conference: str, division: str = "d1", season: str | None = None):
    season_clause = " AND g.season = ?" if season is not None else ""
    params = (
        (division, conference, conference)
        if season is None
        else (division, conference, conference, season)
    )
    return conn.execute(
        f"""
        SELECT {_GAME_COLUMNS} {_GAME_JOINS}
        WHERE g.status = 'final' AND g.division = ? AND (g.home_conference = ? OR g.away_conference = ?){season_clause}
        ORDER BY g.start_epoch
        """,
        params,
    ).fetchall()


def get_all_final_games(conn, division: str = "d1", season: str | None = None):
    season_clause = " AND g.season = ?" if season is not None else ""
    params = (division,) if season is None else (division, season)
    return conn.execute(
        f"SELECT {_GAME_COLUMNS} {_GAME_JOINS} WHERE g.status = 'final' AND g.division = ?{season_clause} ORDER BY g.start_epoch",
        params,
    ).fetchall()


def get_weekly_standouts(conn, since_date: str, through_date: str, division: str = "d1"):
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
        WHERE g.status = 'final' AND g.division = ? AND g.date BETWEEN ? AND ?
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
        (division, since_date, through_date),
    ).fetchall()


def get_player_stats(conn, game_id: str):
    return conn.execute(
        """
        SELECT * FROM player_stats
        WHERE game_id = ?
        ORDER BY is_home DESC, starter DESC, CAST(minutes_played AS REAL) DESC
        """,
        (game_id,),
    ).fetchall()


def get_all_players_roster_stats(conn, division: str = "d1", season: str | None = None):
    season_clause = " AND g.season = ?" if season is not None else ""
    params = (division,) if season is None else (division, season)
    return conn.execute(
        f"""
        SELECT
            ps.first_name, ps.last_name, ps.team_seo,
            COALESCE(MAX(t.name_full), MAX(t.name)) AS team_name,
            MAX(t.name) AS team_name_short,
            MAX(t.conference) AS team_conference,
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
        JOIN games g ON g.id = ps.game_id
        LEFT JOIN teams t ON t.seo = ps.team_seo
        WHERE COALESCE(ps.participated, 1) = 1 AND g.division = ?{season_clause}
        GROUP BY ps.first_name, ps.last_name, ps.team_seo
        ORDER BY ps.last_name ASC, ps.first_name ASC
        """,
        params,
    ).fetchall()


def get_clean_sheet_leaders(conn, division: str = "d1", season: str | None = None):
    """Season clean-sheet counts per goalkeeper: a final game they
    participated in where their team conceded 0."""
    season_clause = " AND g.season = ?" if season is not None else ""
    params = (division,) if season is None else (division, season)
    return conn.execute(
        f"""
        SELECT
            ps.first_name, ps.last_name, ps.team_seo,
            COALESCE(MAX(t.name_full), MAX(t.name)) AS team_name,
            MAX(t.name) AS team_name_short,
            MAX(t.conference) AS team_conference,
            COUNT(*) AS clean_sheets
        FROM player_stats ps
        JOIN games g ON g.id = ps.game_id
        LEFT JOIN teams t ON t.seo = ps.team_seo
        WHERE ps.position = 'GK'
          AND CAST(ps.participated AS INTEGER) = 1
          AND g.status = 'final'
          AND g.division = ?{season_clause}
          AND ((ps.is_home = 1 AND CAST(g.away_score AS INTEGER) = 0)
            OR (ps.is_home = 0 AND CAST(g.home_score AS INTEGER) = 0))
        GROUP BY ps.team_id, ps.first_name, ps.last_name
        ORDER BY clean_sheets DESC
        """,
        params,
    ).fetchall()


def get_team_roster_stats(conn, seo: str, season: str | None = None):
    season_clause = " AND g.season = ?" if season is not None else ""
    params = (seo,) if season is None else (seo, season)
    return conn.execute(
        f"""
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
        JOIN games g ON g.id = ps.game_id
        WHERE ps.team_seo = ? AND COALESCE(ps.participated, 1) = 1{season_clause}
        GROUP BY ps.first_name, ps.last_name
        ORDER BY games_played DESC, avg_minutes DESC
        """,
        params,
    ).fetchall()


def get_player_games(conn, team_seo: str, first_name: str, last_name: str, season: str | None = None):
    season_clause = " AND g.season = ?" if season is not None else ""
    params = (
        (team_seo, first_name, last_name)
        if season is None
        else (team_seo, first_name, last_name, season)
    )
    return conn.execute(
        f"""
        SELECT {_GAME_COLUMNS}, ps.*
        FROM player_stats ps
        JOIN games g ON g.id = ps.game_id
        LEFT JOIN teams th ON th.seo = g.home_seo
        LEFT JOIN teams ta ON ta.seo = g.away_seo
        WHERE ps.team_seo = ? AND UPPER(ps.first_name) = UPPER(?) AND UPPER(ps.last_name) = UPPER(?){season_clause}
        ORDER BY g.start_epoch
        """,
        params,
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


def replace_rankings_for_date(conn, observed_date: str, rows: list[dict], division: str = "d1"):
    conn.execute(
        "DELETE FROM team_rankings WHERE observed_date = ? AND division = ?",
        (observed_date, division),
    )
    conn.executemany(
        """
        INSERT INTO team_rankings (
            observed_date, division, school, seo, rank, prev_rank, points, first_place_votes, record
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                observed_date,
                division,
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


def get_regional_ranking_history(conn, seo: str, division: str = "d3"):
    return conn.execute(
        "SELECT * FROM team_rankings_regional WHERE seo = ? AND division = ? ORDER BY observed_date",
        (seo, division),
    ).fetchall()


def get_all_ranking_history(conn, division: str = "d1"):
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
        WHERE tr.seo IS NOT NULL AND tr.division = ?
        ORDER BY tr.seo, tr.observed_date
        """,
        (division,),
    ).fetchall()


def get_latest_rankings(conn, division: str = "d1"):
    """Most recent day's poll snapshot for `division`, with `prev_rank`
    backfilled from the prior snapshot's rank when the feed hasn't reported
    it yet -- e.g. right after a new poll drops, before United Soccer
    Coaches backfills that detail. Without this, rank-change arrows and
    "prev rank" displays would show nothing/NR for every team on the day a
    new poll lands."""
    rows = [dict(r) for r in conn.execute(
        """
        SELECT * FROM team_rankings
        WHERE division = ? AND observed_date = (
            SELECT MAX(observed_date) FROM team_rankings WHERE division = ?
        )
        ORDER BY rank
        """,
        (division, division),
    )]
    missing = [r for r in rows if r["prev_rank"] in (None, "")]
    if rows and missing:
        prior_date = conn.execute(
            "SELECT MAX(observed_date) AS d FROM team_rankings WHERE division = ? AND observed_date < ?",
            (division, rows[0]["observed_date"]),
        ).fetchone()["d"]
        if prior_date:
            prior_ranks = {
                r["seo"]: r["rank"]
                for r in conn.execute(
                    "SELECT seo, rank FROM team_rankings WHERE division = ? AND observed_date = ?",
                    (division, prior_date),
                )
                if r["seo"]
            }
            for r in missing:
                if r["seo"] in prior_ranks:
                    r["prev_rank"] = str(prior_ranks[r["seo"]])
    return rows


def replace_regional_rankings_for_date(conn, observed_date: str, rows: list[dict], division: str = "d3"):
    conn.execute(
        "DELETE FROM team_rankings_regional WHERE observed_date = ? AND division = ?",
        (observed_date, division),
    )
    conn.executemany(
        """
        INSERT INTO team_rankings_regional (
            observed_date, division, region, rank, school, seo, npi, record
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        [
            (
                observed_date,
                division,
                r["region"],
                r["rank"],
                r["school"],
                r["seo"],
                r["npi"],
                r["record"],
            )
            for r in rows
        ],
    )


def get_all_regional_ranking_history(conn, division: str = "d3", region: int | None = None):
    """Same contract as get_all_ranking_history (rows shaped for
    reference_data.build_rank_history), but from team_rankings_regional.
    `region` narrows to one region's leaderboard -- the Rank History page
    only ever shows one at a time."""
    query = """
        SELECT tr.*, t.name AS team_name, t.name_full AS team_name_full,
               t.conference AS team_conference
        FROM team_rankings_regional tr
        LEFT JOIN teams t ON t.seo = tr.seo
        WHERE tr.seo IS NOT NULL AND tr.division = ?
    """
    params: list = [division]
    if region is not None:
        query += " AND tr.region = ?"
        params.append(region)
    query += " ORDER BY tr.seo, tr.observed_date"
    return conn.execute(query, params).fetchall()


def get_latest_regional_rankings(conn, division: str = "d3"):
    """Most recent day's regional snapshots for `division`, across all
    regions -- one row per currently-ranked team. Unlike get_latest_rankings,
    prev_rank has no upstream backfill to fall back to (the feed never
    reports it), so it's always derived here from the prior snapshot's own
    rank for that team, the same fallback group_rankings_by_week applies
    when building the Rank History chart."""
    rows = [dict(r) for r in conn.execute(
        """
        SELECT * FROM team_rankings_regional
        WHERE division = ? AND observed_date = (
            SELECT MAX(observed_date) FROM team_rankings_regional WHERE division = ?
        )
        ORDER BY region, rank
        """,
        (division, division),
    )]
    if rows:
        prior_date = conn.execute(
            "SELECT MAX(observed_date) AS d FROM team_rankings_regional WHERE division = ? AND observed_date < ?",
            (division, rows[0]["observed_date"]),
        ).fetchone()["d"]
        if prior_date:
            prior_ranks = {
                r["seo"]: r["rank"]
                for r in conn.execute(
                    "SELECT seo, rank FROM team_rankings_regional WHERE division = ? AND observed_date = ?",
                    (division, prior_date),
                )
                if r["seo"]
            }
            for r in rows:
                if r["seo"] in prior_ranks:
                    r["prev_rank"] = str(prior_ranks[r["seo"]])
    return rows


# Conferences whose members are assigned to regions school-by-school rather
# than as a block (NCAA D3 soccer pre-championship manual, Appendix B), so a
# single ranked member says nothing about the rest.
_SPLIT_REGION_CONFERENCES = {"uaa", "c2c"}


def get_team_regions(conn, division: str = "d3") -> dict[str, int]:
    """seo -> NPI region (1-10), built only from the feed's own region data.

    Teams that appear in any regional snapshot keep the region of their most
    recent appearance. Every other team inherits its conference's region
    when all ranked members of that conference sit in a single region --
    unless the conference is split school-by-school (_SPLIT_REGION_CONFERENCES)
    or ranked members already disagree. Coverage grows as more teams reach
    the feed's top 7 per region; conferences with no ranked team yet stay
    unmapped."""
    ranked = conn.execute(
        """
        SELECT tr.seo, tr.region, t.conference
        FROM team_rankings_regional tr
        LEFT JOIN teams t ON t.seo = tr.seo
        WHERE tr.division = ? AND tr.seo IS NOT NULL
        ORDER BY tr.observed_date
        """,
        (division,),
    ).fetchall()

    by_team: dict[str, int] = {}
    conference_regions: dict[str, set[int]] = {}
    for r in ranked:
        by_team[r["seo"]] = r["region"]  # ordered by date, so latest wins
        if r["conference"]:
            conference_regions.setdefault(r["conference"], set()).add(r["region"])

    unanimous = {
        conf: next(iter(regions))
        for conf, regions in conference_regions.items()
        if len(regions) == 1 and conf not in _SPLIT_REGION_CONFERENCES
    }
    for t in conn.execute(
        "SELECT seo, conference FROM teams WHERE conference IN (%s)"
        % ",".join("?" * len(unanimous)),
        list(unanimous),
    ) if unanimous else []:
        by_team.setdefault(t["seo"], unanimous[t["conference"]])
    return by_team


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
