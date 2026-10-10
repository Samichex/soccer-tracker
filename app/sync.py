import datetime as dt
import json
import logging

from . import config, db, ncaa_client, normalize, validate

log = logging.getLogger("soccer-tracker.sync")


def _group_by_team_seo(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["team_seo"], []).append(r)
    return groups


def live_window_start() -> str:
    """First date (YYYY-MM-DD) of the window run_full_sync re-pulls every
    cycle. Games older than this are never revisited by the live sync."""
    return (dt.date.today() - dt.timedelta(days=config.DAYS_BACK)).isoformat()


# Every sync step below fetches from upstream *before* touching the DB, then
# does its writes inside `with conn:` -- committed together on success,
# rolled back on error. Python's sqlite3 otherwise holds a write
# transaction open from the first write until an explicit commit, which
# used to mean one run_full_sync kept the DB write-locked across every
# network call it made, so any other writer (the game page's box score
# fetch, a backfill run from the Render shell) waited out busy_timeout and
# then failed with "database is locked".
def sync_date(conn, date: dt.date, division: str = "d1", sport_path: str | None = None):
    sport_path = sport_path or config.DIVISIONS[division]
    data = ncaa_client.get_scoreboard(date, sport_path)
    games = data.get("games", [])
    date_str = date.isoformat()
    with conn:
        seen_ids = {db.upsert_game(conn, game, date_str, division) for game in games}
        removed = db.delete_superseded_games(conn, date_str, division, seen_ids)
    log.info("synced %s %s games for %s", len(games), division, date_str)
    if removed:
        log.info("removed %s superseded %s game(s) for %s", removed, division, date_str)


def _rows_from_boxscore(box: dict) -> list[dict]:
    team_by_id = {str(t["teamId"]): t for t in box.get("teams", [])}
    rows = []
    for team_box in box.get("teamBoxscore", []):
        team_id = team_box.get("teamId")
        team_meta = team_by_id.get(str(team_id), {})
        team_seo = team_meta.get("seoname")
        team_goalie_saves = ((team_box.get("teamStats") or {}).get("goalie") or {}).get("saves")

        team_rows = []
        for p in team_box.get("playerStats", []):
            penalties = p.get("penalties") or {}
            goal_types = p.get("goalTypes") or {}
            team_rows.append(
                {
                    "team_id": team_id,
                    "team_seo": team_seo,
                    "is_home": 1 if team_meta.get("isHome") else 0,
                    "first_name": p.get("firstName"),
                    "last_name": p.get("lastName"),
                    "number": p.get("number"),
                    "position": p.get("position"),
                    "starter": 1 if p.get("starter") else 0,
                    "minutes_played": p.get("minutesPlayed"),
                    "goals": p.get("goals"),
                    "assists": p.get("assists"),
                    "shots": p.get("shots"),
                    "shots_on_goal": p.get("shotsOnGoal"),
                    "saves": p.get("saves"),
                    "yellow_cards": penalties.get("yellowCards"),
                    "red_cards": penalties.get("redCards"),
                    "fouls": penalties.get("fouls"),
                    "green_cards": penalties.get("greenCards"),
                    "game_winning_goals": goal_types.get("gameWinningGoals"),
                    "penalty_goals": p.get("penaltyShotGoals"),
                    "participated": 1 if p.get("participated", True) else 0,
                }
            )

        # The NCAA feed always reports 0 in each player's own "saves" field;
        # the real number only exists team-wide, under teamStats.goalie.saves.
        # That total can't be split between keepers who split minutes, so
        # only substitute it when a single keeper played the whole match.
        goalkeepers = [
            r
            for r in team_rows
            if r["participated"] and normalize.canonical_position(r["position"]) == "GK"
        ]
        if team_goalie_saves is not None and len(goalkeepers) == 1:
            goalkeepers[0]["saves"] = team_goalie_saves

        rows.extend(team_rows)
    return rows


def _sync_teams(conn, box: dict) -> None:
    for t in box.get("teams", []):
        db.upsert_team_detail(
            conn,
            seo=t.get("seoname"),
            team_id=t.get("teamId"),
            name_full=t.get("nameFull"),
            mascot=t.get("teamName"),
            name6_char=t.get("name6Char"),
            color=t.get("color"),
        )


def _sync_boxscore(conn, game_id: str) -> int:
    box = ncaa_client.get_boxscore(game_id)
    with conn:
        db.upsert_raw_boxscore(conn, game_id, json.dumps(box))
        _sync_teams(conn, box)
        rows = _rows_from_boxscore(box)
        for team_seo, team_rows in _group_by_team_seo(rows).items():
            normalize.normalize_rows(conn, team_seo, team_rows)
        if rows:
            db.replace_player_stats(conn, game_id, rows)
    return len(rows)


def sync_boxscores(conn, game_ids: list[str]):
    """Fetch and store box scores for `game_ids`, replacing any already
    stored. A failure for one game is logged and skipped, not raised."""
    for game_id in game_ids:
        try:
            count = _sync_boxscore(conn, game_id)
        except Exception:
            log.exception("failed to fetch boxscore for game %s", game_id)
            continue
        log.info("stored boxscore for game %s (%s players)", game_id, count)


def sync_missing_boxscores(conn, since_date: str | None = None):
    """Box scores for final games that don't have one yet -- only those
    played on/after `since_date` when given (see db.games_missing_boxscore)."""
    sync_boxscores(conn, db.games_missing_boxscore(conn, since_date))


def _parse_rank(value) -> int | None:
    """The feed denotes a tie for a rank with a "T" prefix, e.g. "T23" --
    we don't track the tie itself, just the numeric rank, so multiple
    teams can share a rank the same way they can already share a record.
    Without stripping it, int() raises and the team is silently dropped
    from that day's snapshot instead of being stored as rank 23."""
    text = str(value).strip() if value is not None else ""
    if text[:1] in ("T", "t"):
        text = text[1:]
    return validate.safe_int(text)


# The United Soccer Coaches poll is a top 25 (ties share a rank, never
# shrink the list), so fewer rows than this means a partial feed response.
_FULL_POLL_SIZE = 25


def _parse_rankings(data: dict) -> list[dict]:
    rows = []
    for entry in data.get("data", []):
        rank = _parse_rank(entry.get("RANK"))
        if rank is None:
            continue
        rows.append(
            {
                "school": (entry.get("SCHOOL") or "").strip(),
                "rank": rank,
                "prev_rank": entry.get("PREVIOUS"),
                "points": entry.get("POINTS"),
                "first_place_votes": entry.get("FIRST-PLACE VOTES"),
                "record": entry.get("RECORD"),
            }
        )
    return rows


_REGION_ROMAN_TO_INT = {roman: i + 1 for i, roman in enumerate(
    ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
)}


def _parse_regional_rankings(data: dict) -> list[dict]:
    """Parse the ten-region NPI feed (see config.REGIONAL_RANKINGS_DIVISIONS):
    a flat row list where a region boundary is marked by a row with an empty
    SCHOOL and a RANK of "Region I".."Region X", followed by that region's
    ranked teams. Rows before the first region header (there shouldn't be
    any) are dropped rather than guessed at."""
    rows = []
    region = None
    for entry in data.get("data", []):
        school = (entry.get("SCHOOL") or "").strip()
        raw_rank = str(entry.get("RANK") or "").strip()
        if not school and raw_rank.startswith("Region "):
            region = _REGION_ROMAN_TO_INT.get(raw_rank[len("Region "):].strip())
            continue
        if region is None or not school:
            continue
        rank = _parse_rank(raw_rank)
        if rank is None:
            continue
        rows.append(
            {
                "region": region,
                "school": school,
                "rank": rank,
                "npi": entry.get("NPI"),
                "record": entry.get("IN-DIVISION RECORD"),
            }
        )
    return rows


def sync_regional_rankings(
    conn,
    division: str = "d3",
    sport_path: str | None = None,
    observed_date: dt.date | None = None,
):
    """Snapshot the current ten-region NPI leaderboards under `observed_date`
    (default: today). Same overwrite-today's-row-on-rerun semantics as
    sync_rankings, but for the region-scoped shape described in
    config.REGIONAL_RANKINGS_DIVISIONS instead of a single national poll."""
    sport_path = sport_path or config.DIVISIONS[division]
    observed_date = observed_date or dt.date.today()
    data = ncaa_client.get_rankings(sport_path)
    rows = _parse_regional_rankings(data)
    for r in rows:
        r["seo"] = db.resolve_seo_by_name(conn, r["school"])
    with conn:
        db.replace_regional_rankings_for_date(conn, observed_date.isoformat(), rows, division=division)
    unresolved = [r["school"] for r in rows if not r["seo"]]
    if unresolved:
        log.warning("could not resolve seo for regionally-ranked %s schools: %s", division, unresolved)
    log.info(
        "synced %s regional rankings for %s (%s teams across %s regions)",
        division,
        observed_date.isoformat(),
        len(rows),
        len({r["region"] for r in rows}),
    )


def sync_rankings(
    conn,
    division: str = "d1",
    sport_path: str | None = None,
    observed_date: dt.date | None = None,
):
    """Snapshot the current poll under `observed_date` (default: today).

    Rankings only change weekly, but this is safe to call every sync cycle:
    it overwrites today's row rather than duplicating it, so re-running just
    keeps today's snapshot current. Called once a day's worth of snapshots
    accumulate across weeks, this is what makes a rank time series possible —
    the API itself exposes no poll date, only "results through" and no
    week number (see ncaa_client.get_rankings).
    """
    sport_path = sport_path or config.DIVISIONS[division]
    observed_date = observed_date or dt.date.today()
    data = ncaa_client.get_rankings(sport_path)
    rows = _parse_rankings(data)
    if len(rows) < _FULL_POLL_SIZE:
        # Seen 2026-09-08/09: mid-update, the feed briefly served 22 teams
        # with every field but rank blank. Keep the last full snapshot
        # rather than overwrite today's with a partial one.
        log.warning(
            "skipping %s rankings for %s: feed returned %s ranked teams, expected %s",
            division, observed_date.isoformat(), len(rows), _FULL_POLL_SIZE,
        )
        return
    for r in rows:
        r["seo"] = db.resolve_seo_by_name(conn, r["school"])
    with conn:
        db.replace_rankings_for_date(conn, observed_date.isoformat(), rows, division=division)
    unresolved = [r["school"] for r in rows if not r["seo"]]
    if unresolved:
        log.warning("could not resolve seo for ranked %s schools: %s", division, unresolved)
    log.info(
        "synced %s rankings for %s (%s teams)", division, observed_date.isoformat(), len(rows)
    )


def run_full_sync():
    """Sync the live window: recent results plus the near-term schedule.

    Scores and game times in this window change, so it's cheap to re-pull on
    every background cycle. Days further out belong to sync_far_schedule
    instead.

    Loops over config.ENABLED_DIVISIONS -- just "d1" by default, so this is
    unchanged in shape and volume from before D3 support existed unless
    that's been explicitly opted into.
    """
    today = dt.date.today()
    with db.get_conn() as conn:
        for division in config.ENABLED_DIVISIONS:
            sport_path = config.DIVISIONS[division]
            for offset in range(-config.DAYS_BACK, config.DAYS_FORWARD + 1):
                date = today + dt.timedelta(days=offset)
                try:
                    sync_date(conn, date, division, sport_path)
                except Exception:
                    log.exception("failed to sync %s %s", division, date.isoformat())
                    continue
        # Only this window's games -- an older one upstream never serves a
        # box score for would otherwise be retried (with backoff) every
        # cycle forever. sync_all_missing_boxscores sweeps the rest daily.
        sync_missing_boxscores(conn, live_window_start())
        for division in config.ENABLED_DIVISIONS:
            if division not in config.RANKINGS_SUPPORTED_DIVISIONS:
                # See config.RANKINGS_SUPPORTED_DIVISIONS -- this division's
                # rankings feed isn't a single national poll, so there's
                # nothing sync_rankings can correctly store for it yet.
                continue
            try:
                sync_rankings(conn, division, config.DIVISIONS[division])
            except Exception:
                log.exception("failed to sync rankings for %s", division)
        for division in config.REGIONAL_RANKINGS_DIVISIONS:
            # Deliberately not gated by ENABLED_DIVISIONS -- unlike the games
            # sync above, this never touches the games table, so it carries
            # none of the D1/D3 mixing risk ENABLED_DIVISIONS guards against.
            try:
                sync_regional_rankings(conn, division, config.DIVISIONS[division])
            except Exception:
                log.exception("failed to sync regional rankings for %s", division)
        db.set_last_synced(conn, dt.datetime.utcnow().isoformat())


def sync_far_schedule():
    """Sync the rest-of-season schedule beyond the live window.

    These fixtures rarely change day to day, so this runs on its own slower
    cadence (SCHEDULE_SYNC_INTERVAL_HOURS) from the background loop.
    """
    today = dt.date.today()
    with db.get_conn() as conn:
        for division in config.ENABLED_DIVISIONS:
            sport_path = config.DIVISIONS[division]
            for offset in range(config.DAYS_FORWARD + 1, config.SCHEDULE_DAYS_FORWARD + 1):
                date = today + dt.timedelta(days=offset)
                try:
                    sync_date(conn, date, division, sport_path)
                except Exception:
                    log.exception("failed to sync %s %s", division, date.isoformat())
                    continue


def sync_all_missing_boxscores():
    """Retry every final game still missing a box score, however old.

    run_full_sync only retries games inside its live window, so this daily
    pass (run alongside sync_far_schedule from the background loop) is what
    picks up a game that aged out of that window without ever getting one,
    e.g. after an outage longer than DAYS_BACK.
    """
    with db.get_conn() as conn:
        sync_missing_boxscores(conn)


def compress_legacy_boxscores(batch_size: int = 200) -> int:
    """Compress raw box scores still stored as plain JSON text, one
    committed batch at a time so the live sync is never locked out for
    long. A no-op once they're all converted.

    When it did convert something (in practice, once: the first daily pass
    after compression shipped), it VACUUMs afterward so the DB file
    actually shrinks -- otherwise the freed space only gets reused by later
    writes. On a two-season DB that's 260MB -> ~57MB in about a second
    locally; page requests keep reading throughout (WAL mode)."""
    total = 0
    with db.get_conn() as conn:
        while True:
            with conn:
                converted = db.compress_raw_boxscores_batch(conn, batch_size)
            if not converted:
                break
            total += converted
        if total:
            log.info("compressed %s legacy raw box scores; vacuuming", total)
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            log.info("vacuum done")
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    run_full_sync()
