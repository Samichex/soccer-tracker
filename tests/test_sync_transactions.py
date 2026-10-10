"""Each sync step commits its own writes (see the note above
app/sync.py:sync_date), so the DB is never left write-locked across the
network calls that follow it."""

import datetime as dt
import sqlite3

import pytest

from app import config, db, ncaa_client, sync


def _scoreboard_game(id_, home="duke", away="unc"):
    return {
        "game": {
            "gameID": id_,
            "gameState": "final",
            "home": {"names": {"seo": home, "short": home.title()}, "score": "2", "conferences": []},
            "away": {"names": {"seo": away, "short": away.title()}, "score": "1", "conferences": []},
        }
    }


def _boxscore(home="duke"):
    return {
        "teams": [{"teamId": 1, "seoname": home, "isHome": True}],
        "teamBoxscore": [
            {
                "teamId": 1,
                "teamStats": {},
                "playerStats": [{"firstName": "John", "lastName": "Smith", "number": "10"}],
            }
        ],
    }


def _assert_write_lock_free():
    # A second connection that refuses to wait at all can only start a
    # write transaction if nothing else is holding the lock.
    other = sqlite3.connect(config.DB_PATH, timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.rollback()
    finally:
        other.close()


def test_sync_date_commits_and_releases_write_lock(conn, monkeypatch):
    monkeypatch.setattr(
        ncaa_client, "get_scoreboard", lambda date, path: {"games": [_scoreboard_game("g1")]}
    )

    sync.sync_date(conn, dt.date(2026, 9, 1))

    assert not conn.in_transaction
    _assert_write_lock_free()
    assert conn.execute("SELECT 1 FROM games WHERE id = 'g1'").fetchone() is not None


def test_sync_date_rolls_back_the_whole_date_on_error(conn, monkeypatch):
    malformed = {"game": {"gameID": "g2"}}  # no home/away -> upsert_game raises
    monkeypatch.setattr(
        ncaa_client,
        "get_scoreboard",
        lambda date, path: {"games": [_scoreboard_game("g1"), malformed]},
    )

    with pytest.raises(KeyError):
        sync.sync_date(conn, dt.date(2026, 9, 1))

    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"] == 0


def test_sync_boxscores_stores_each_game_independently(conn, monkeypatch):
    def fake_get_boxscore(game_id):
        if game_id == "bad":
            raise RuntimeError("upstream 502")
        return _boxscore()

    monkeypatch.setattr(ncaa_client, "get_boxscore", fake_get_boxscore)

    sync.sync_boxscores(conn, ["bad", "good"])

    assert not conn.in_transaction
    _assert_write_lock_free()
    stored = {r["game_id"] for r in conn.execute("SELECT DISTINCT game_id FROM player_stats")}
    assert stored == {"good"}


def test_sync_missing_boxscores_only_fetches_games_since_date(conn, monkeypatch):
    for id_, date in (("old", "2025-09-03"), ("recent", "2026-10-07")):
        db.upsert_game(conn, _scoreboard_game(id_), date)
    conn.commit()
    fetched = []
    monkeypatch.setattr(
        ncaa_client, "get_boxscore", lambda game_id: fetched.append(game_id) or _boxscore()
    )

    sync.sync_missing_boxscores(conn, "2026-10-06")

    assert fetched == ["recent"]
