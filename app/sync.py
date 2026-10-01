import datetime as dt
import json
import logging

from . import config, db, ncaa_client, normalize

log = logging.getLogger("soccer-tracker.sync")


def _group_by_team_seo(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["team_seo"], []).append(r)
    return groups


def sync_date(conn, date: dt.date, division: str = "d1", sport_path: str | None = None):
    sport_path = sport_path or config.DIVISIONS[division]
    data = ncaa_client.get_scoreboard(date, sport_path)
    games = data.get("games", [])
    date_str = date.isoformat()
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
    db.upsert_raw_boxscore(conn, game_id, json.dumps(box))
    _sync_teams(conn, box)
    rows = _rows_from_boxscore(box)
    for team_seo, team_rows in _group_by_team_seo(rows).items():
        normalize.normalize_rows(conn, team_seo, team_rows)
    if rows:
        db.replace_player_stats(conn, game_id, rows)
    return len(rows)


def sync_missing_boxscores(conn):
    pending = db.games_missing_boxscore(conn)
    for game_id in pending:
        try:
            count = _sync_boxscore(conn, game_id)
        except Exception:
            log.exception("failed to fetch boxscore for game %s", game_id)
            continue
        log.info("stored boxscore for game %s (%s players)", game_id, count)


def resync_boxscores(conn, game_ids: list[str]):
    """Re-fetch and replace box scores for games already synced.

    Use this to pick up upstream corrections — e.g. the NCAA feed initially
    listing bench players who never entered the match (0 minutes played),
    then trimming them once the box score is finalized.
    """
    for game_id in game_ids:
        try:
            count = _sync_boxscore(conn, game_id)
        except Exception:
            log.exception("failed to resync boxscore for game %s", game_id)
            continue
        log.info("resynced boxscore for game %s (%s players)", game_id, count)


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_rank(value) -> int | None:
    """The feed denotes a tie for a rank with a "T" prefix, e.g. "T23" --
    we don't track the tie itself, just the numeric rank, so multiple
    teams can share a rank the same way they can already share a record.
    Without stripping it, int() raises and the team is silently dropped
    from that day's snapshot instead of being stored as rank 23."""
    text = str(value).strip() if value is not None else ""
    if text[:1] in ("T", "t"):
        text = text[1:]
    return _safe_int(text)


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
    for r in rows:
        r["seo"] = db.resolve_seo_by_name(conn, r["school"])
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
    every background cycle and safe to trigger from the manual refresh
    endpoint. Days further out belong to sync_far_schedule instead.

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
        sync_missing_boxscores(conn)
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
    cadence (SCHEDULE_SYNC_INTERVAL_HOURS) from the background loop only —
    it's not tied to the manual /api/sync-now refresh.
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    run_full_sync()
