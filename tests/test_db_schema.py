import logging
import sqlite3

import pytest

from app import db


_EXPECTED_TABLES = {
    "games", "player_stats", "team_rankings", "sync_meta", "teams", "game_boxscore_raw",
}


def test_init_db_creates_all_tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows}
    assert _EXPECTED_TABLES <= names


def test_games_date_not_null_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO games (id, date) VALUES (?, ?)", ("g1", None)
        )


def _game(id_="g1", home="duke", away="unc"):
    return {
        "game": {
            "gameID": id_,
            "startTime": "7:00 PM",
            "startTimeEpoch": "1000",
            "gameState": "final",
            "currentPeriod": "2nd",
            "home": {"names": {"seo": home, "short": home.title()}, "score": "2", "conferences": []},
            "away": {"names": {"seo": away, "short": away.title()}, "score": "1", "conferences": []},
            "network": None,
            "url": None,
        }
    }


def test_upsert_game_is_idempotent_by_id(conn):
    db.upsert_game(conn, _game(), "2026-09-01")
    db.upsert_game(conn, _game(), "2026-09-01")
    rows = conn.execute("SELECT * FROM games WHERE id = 'g1'").fetchall()
    assert len(rows) == 1


def test_upsert_game_updates_score_on_conflict(conn):
    db.upsert_game(conn, _game(), "2026-09-01")
    updated = _game()
    updated["game"]["home"]["score"] = "5"
    db.upsert_game(conn, updated, "2026-09-01")
    row = conn.execute("SELECT home_score FROM games WHERE id = 'g1'").fetchone()
    assert row["home_score"] == "5"


def test_upsert_game_discards_unrecognized_status(conn, caplog):
    bad = _game()
    bad["game"]["gameState"] = "postponed"
    with caplog.at_level(logging.WARNING):
        db.upsert_game(conn, bad, "2026-09-01")
    row = conn.execute("SELECT status FROM games WHERE id = 'g1'").fetchone()
    assert row["status"] is None
    assert "unrecognized status" in caplog.text


def test_upsert_game_discards_invalid_score(conn, caplog):
    bad = _game()
    bad["game"]["home"]["score"] = "TBD"
    with caplog.at_level(logging.WARNING):
        db.upsert_game(conn, bad, "2026-09-01")
    row = conn.execute("SELECT home_score FROM games WHERE id = 'g1'").fetchone()
    assert row["home_score"] is None
    assert "unrecognized home score" in caplog.text


def test_upsert_game_keeps_prior_valid_score_when_a_later_sync_sends_garbage(conn):
    # A transient bad value from upstream shouldn't wipe out a
    # previously-good score on the next sync cycle.
    db.upsert_game(conn, _game(), "2026-09-01")
    bad = _game()
    bad["game"]["home"]["score"] = "garbage"
    db.upsert_game(conn, bad, "2026-09-01")
    row = conn.execute("SELECT home_score FROM games WHERE id = 'g1'").fetchone()
    assert row["home_score"] == "2"


def _player_row(**overrides):
    row = {
        "team_id": "1", "team_seo": "duke", "is_home": 1,
        "first_name": "John", "last_name": "Smith", "number": "10",
        "position": "GK", "starter": 1, "minutes_played": "90",
        "goals": "0", "assists": "0", "shots": "0", "shots_on_goal": "0",
        "saves": "0", "yellow_cards": "0", "red_cards": "0",
        "fouls": "0", "green_cards": "0", "game_winning_goals": "0",
        "penalty_goals": "0", "participated": 1,
    }
    row.update(overrides)
    return row


def test_replace_player_stats_replaces_rather_than_appends(conn):
    db.replace_player_stats(conn, "g1", [_player_row()])
    db.replace_player_stats(conn, "g1", [_player_row(last_name="Doe")])
    rows = conn.execute("SELECT last_name FROM player_stats WHERE game_id = 'g1'").fetchall()
    assert [r["last_name"] for r in rows] == ["Doe"]


def test_player_stats_primary_key_rejects_exact_duplicate(conn):
    # Note: because replace_player_stats uses executemany for the whole
    # batch, an exact-duplicate row (same game/team/number/name) doesn't
    # just get skipped -- it raises and aborts inserting every row for
    # this game, not just the duplicate.
    with pytest.raises(sqlite3.IntegrityError):
        db.replace_player_stats(conn, "g1", [_player_row(), _player_row()])


def test_teams_table_has_directory_columns(conn):
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(teams)")}
    assert {"orgid", "athletic_url", "website_url", "head_coach"} <= cols


def test_upsert_team_directory_is_idempotent(conn):
    db.upsert_team_directory(conn, "duke", 123, "goduke.com", "duke.edu")
    db.upsert_team_directory(conn, "duke", 123, "goduke.com", "duke.edu")
    rows = conn.execute("SELECT * FROM teams WHERE seo = 'duke'").fetchall()
    assert len(rows) == 1
    assert rows[0]["orgid"] == 123
    assert rows[0]["athletic_url"] == "goduke.com"
    assert rows[0]["website_url"] == "duke.edu"


def test_upsert_team_directory_does_not_clobber_other_sources_columns(conn):
    db.upsert_team_basic(conn, "duke", "Duke", "acc")
    db.upsert_team_detail(conn, "duke", "1", "Duke University", "Blue Devils", "DUKE", "#003087")
    db.upsert_team_directory(conn, "duke", 123, "goduke.com", "duke.edu")

    row = conn.execute("SELECT * FROM teams WHERE seo = 'duke'").fetchone()
    assert row["name"] == "Duke"
    assert row["conference"] == "acc"
    assert row["name_full"] == "Duke University"
    assert row["mascot"] == "Blue Devils"
    assert row["orgid"] == 123
    assert row["athletic_url"] == "goduke.com"
    assert row["website_url"] == "duke.edu"


def test_set_head_coach_updates_existing_row_only(conn):
    db.set_head_coach(conn, "nonexistent", "Some Coach")
    row = conn.execute("SELECT * FROM teams WHERE seo = 'nonexistent'").fetchone()
    assert row is None

    db.upsert_team_directory(conn, "duke", 123, "goduke.com", "duke.edu")
    db.set_head_coach(conn, "duke", "John Kerr")
    row = conn.execute("SELECT head_coach FROM teams WHERE seo = 'duke'").fetchone()
    assert row["head_coach"] == "John Kerr"


def test_get_teams_with_orgid_only_returns_matched_teams(conn):
    db.upsert_team_basic(conn, "duke", "Duke", "acc")
    db.upsert_team_directory(conn, "duke", 123, "goduke.com", "duke.edu")
    db.upsert_team_basic(conn, "unc", "UNC", "acc")

    rows = db.get_teams_with_orgid(conn)
    assert [r["seo"] for r in rows] == ["duke"]
    assert rows[0]["orgid"] == 123


def test_player_stats_pk_does_not_catch_same_player_under_two_jersey_numbers(conn, caplog):
    # Known gap (see the two confirmed real-world instances found in
    # data/soccer.db, e.g. game 6616816 "Callum Lugton" as both #10 and
    # #18): the PK includes `number`, so the same physical player recorded
    # twice under different numbers in one game is NOT rejected here and
    # produces two rows -- which double-counts them in every roster/
    # leaderboard aggregation that groups by (first_name, last_name, team).
    # There's no reliable way to tell which number is right (or whether
    # these are genuinely two different players), so db.replace_player_stats
    # only flags this with a warning rather than discarding either row --
    # this test documents both halves of that behavior.
    with caplog.at_level(logging.WARNING):
        db.replace_player_stats(conn, "g1", [
            _player_row(number="10"),
            _player_row(number="18"),
        ])
    rows = conn.execute(
        "SELECT DISTINCT number FROM player_stats WHERE game_id = 'g1' "
        "AND first_name = 'John' AND last_name = 'Smith'"
    ).fetchall()
    assert len(rows) == 2
    assert "multiple jersey numbers" in caplog.text
