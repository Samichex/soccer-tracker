import logging

import pytest

from app import validate


def _row(**overrides):
    row = {
        "team_seo": "duke", "first_name": "John", "last_name": "Smith", "number": "10",
        "minutes_played": "90", "goals": "1", "assists": "0", "shots": "2",
        "shots_on_goal": "1", "saves": "0", "yellow_cards": "0", "red_cards": "0",
    }
    row.update(overrides)
    return row


def test_sanitize_player_row_stats_leaves_plausible_values_untouched():
    row = _row()
    original = dict(row)
    validate.sanitize_player_row_stats("g1", row)
    assert row == original


def test_sanitize_player_row_stats_leaves_blank_values_untouched():
    row = _row(goals="", minutes_played="")
    validate.sanitize_player_row_stats("g1", row)
    assert row["goals"] == ""
    assert row["minutes_played"] == ""


def test_sanitize_player_row_stats_discards_negative_stat(caplog):
    row = _row(goals="-3")
    with caplog.at_level(logging.WARNING):
        validate.sanitize_player_row_stats("g1", row)
    assert row["goals"] == ""
    assert "negative goals" in caplog.text


@pytest.mark.parametrize("minutes", ["-5", "9999", "131"])
def test_sanitize_player_row_stats_discards_implausible_minutes(minutes, caplog):
    row = _row(minutes_played=minutes)
    with caplog.at_level(logging.WARNING):
        validate.sanitize_player_row_stats("g1", row)
    assert row["minutes_played"] == ""
    assert "implausible minutes_played" in caplog.text


def test_sanitize_player_row_stats_allows_max_plausible_minutes():
    row = _row(minutes_played=str(validate.MAX_PLAUSIBLE_MINUTES))
    validate.sanitize_player_row_stats("g1", row)
    assert row["minutes_played"] == str(validate.MAX_PLAUSIBLE_MINUTES)


def test_flag_duplicate_players_logs_when_same_name_has_two_numbers(caplog):
    rows = [
        _row(number="10"),
        _row(number="18"),
    ]
    with caplog.at_level(logging.WARNING):
        validate.flag_duplicate_players("g1", rows)
    assert "multiple jersey numbers" in caplog.text
    assert "JOHN" in caplog.text and "SMITH" in caplog.text


def test_flag_duplicate_players_silent_for_distinct_players(caplog):
    rows = [
        _row(number="10", last_name="Smith"),
        _row(number="11", last_name="Jones"),
    ]
    with caplog.at_level(logging.WARNING):
        validate.flag_duplicate_players("g1", rows)
    assert caplog.text == ""


@pytest.mark.parametrize("status", ["pre", "live", "final", None])
def test_validate_game_status_passes_through_known_values(status):
    assert validate.validate_game_status("g1", status) == status


def test_validate_game_status_discards_unknown_value(caplog):
    with caplog.at_level(logging.WARNING):
        result = validate.validate_game_status("g1", "postponed")
    assert result is None
    assert "unrecognized status" in caplog.text


@pytest.mark.parametrize("score", ["0", "2", "15", "", None])
def test_validate_score_passes_through_valid_values(score):
    assert validate.validate_score("g1", "home", score) == score


@pytest.mark.parametrize("score", ["-1", "TBD", "2-1"])
def test_validate_score_discards_invalid_values(score, caplog):
    with caplog.at_level(logging.WARNING):
        result = validate.validate_score("g1", "home", score)
    assert result is None
    assert "unrecognized home score" in caplog.text
