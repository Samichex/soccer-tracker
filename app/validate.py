"""Sanity checks on data pulled from the upstream NCAA feed, applied right
before it's written to the DB.

The feed has no schema of its own, and this app's schema enforces almost
nothing (no CHECK/FOREIGN KEY constraints -- see the data-quality review),
so these are the last line of defense against a bad upstream value
silently becoming a "real" stat. Nothing here raises: a bad value is
logged and neutralized (treated as unknown) rather than left to corrupt a
sum/average or crash a sync cycle over one bad row.
"""

import logging

log = logging.getLogger("soccer-tracker.validate")

# Longest real college match: 90 regulation + two 10-minute overtime
# periods = 110, padded for stoppage time. A value above this is almost
# certainly a unit error upstream (e.g. seconds instead of minutes).
MAX_PLAUSIBLE_MINUTES = 130

NONNEGATIVE_STAT_FIELDS = [
    "goals", "assists", "shots", "shots_on_goal", "saves",
    "yellow_cards", "red_cards", "fouls", "green_cards",
    "game_winning_goals", "penalty_goals",
]

KNOWN_GAME_STATUSES = {"pre", "live", "final"}


def _as_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sanitize_player_row_stats(game_id: str, row: dict) -> None:
    """Blank out (in place) any stat value on `row` that can't be trusted."""
    who = f"{row.get('first_name')} {row.get('last_name')} (#{row.get('number')})"

    minutes = _as_int_or_none(row.get("minutes_played"))
    if minutes is not None and not (0 <= minutes <= MAX_PLAUSIBLE_MINUTES):
        log.warning(
            "game %s: %s has implausible minutes_played=%r, discarding",
            game_id, who, row["minutes_played"],
        )
        row["minutes_played"] = ""

    for field in NONNEGATIVE_STAT_FIELDS:
        value = _as_int_or_none(row.get(field))
        if value is not None and value < 0:
            log.warning(
                "game %s: %s has negative %s=%r, discarding",
                game_id, who, field, row[field],
            )
            row[field] = ""


def flag_duplicate_players(game_id: str, rows: list[dict]) -> None:
    """Log (without altering data) when the same player name appears under
    more than one jersey number for the same team in this game -- a known
    upstream quirk (see data-quality review) that silently double-counts a
    player in every roster/leaderboard aggregation grouped by name. There's
    no reliable way to tell which number is right, or whether these are
    actually two different players who share a name, so this only flags
    rather than discarding either row.
    """
    numbers_by_player: dict[tuple, set] = {}
    for r in rows:
        key = (r.get("team_seo"), (r.get("first_name") or "").upper(), (r.get("last_name") or "").upper())
        numbers_by_player.setdefault(key, set()).add(r.get("number"))

    for (team_seo, first, last), numbers in numbers_by_player.items():
        if len(numbers) > 1:
            log.warning(
                "game %s: %s %s (team %s) appears under multiple jersey numbers %s -- "
                "likely double-counted in aggregates that group by name",
                game_id, first, last, team_seo, sorted(numbers),
            )


def validate_game_status(game_id: str, status: str | None) -> str | None:
    """Pass through a known status; log and blank out anything else so an
    unrecognized value can't be mistaken for one of pre/live/final."""
    if status is None or status in KNOWN_GAME_STATUSES:
        return status
    log.warning("game %s: unrecognized status %r, discarding", game_id, status)
    return None


def validate_score(game_id: str, side: str, score) -> str | None:
    """Pass through a blank or non-negative-integer score; log and blank
    out anything else so it can't be silently CAST to an unintended int."""
    if score is None or score == "":
        return score
    value = _as_int_or_none(score)
    if value is None or value < 0:
        log.warning("game %s: unrecognized %s score %r, discarding", game_id, side, score)
        return None
    return score
