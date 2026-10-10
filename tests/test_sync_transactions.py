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


def _poll(n):
    return {"data": [{"RANK": str(i), "SCHOOL": f"School {i}"} for i in range(1, n + 1)]}


def test_sync_rankings_skips_a_partial_poll_and_keeps_the_last_full_one(conn, monkeypatch):
    day = dt.date(2026, 9, 8)
    monkeypatch.setattr(ncaa_client, "get_rankings", lambda path: _poll(25))
    sync.sync_rankings(conn, observed_date=day)

    monkeypatch.setattr(ncaa_client, "get_rankings", lambda path: _poll(22))
    sync.sync_rankings(conn, observed_date=day)

    n = conn.execute("SELECT COUNT(*) AS n FROM team_rankings WHERE observed_date = ?", (day.isoformat(),))
    assert n.fetchone()["n"] == 25


def test_compress_legacy_boxscores_converts_in_committed_batches(conn):
    for i in range(5):
        conn.execute(
            "INSERT INTO game_boxscore_raw (game_id, raw_json) VALUES (?, ?)", (f"g{i}", '{"a": 1}')
        )
    conn.commit()

    assert sync.compress_legacy_boxscores(batch_size=2) == 5
    _assert_write_lock_free()
    assert sync.compress_legacy_boxscores(batch_size=2) == 0
    assert db.get_raw_boxscore(conn, "g4") == '{"a": 1}'


def _days_ago(n):
    return dt.date.today() - dt.timedelta(days=n)


def _stuck(id_, home, away, state="pre"):
    game = _scoreboard_game(id_, home, away)
    game["game"]["gameState"] = state
    return game


def test_unfinished_games_by_date_counts_only_non_final_games_in_range(conn):
    db.upsert_game(conn, _stuck("a", "x", "y"), "2026-09-10")
    db.upsert_game(conn, _stuck("b", "x", "z", state="live"), "2026-09-10")
    db.upsert_game(conn, _scoreboard_game("c", "y", "z"), "2026-09-10")  # final
    db.upsert_game(conn, _stuck("d", "y", "x"), "2026-10-07")  # on/after the end bound
    db.upsert_game(conn, _stuck("e", "z", "x"), "2026-05-01")  # before the start bound

    assert db.unfinished_games_by_date(conn, "2026-06-01", "2026-10-06") == {("2026-09-10", "d1"): 2}


def test_catch_up_resyncs_stuck_past_dates_only(conn, monkeypatch):
    stuck_day, old_day, live_day = _days_ago(10), _days_ago(config.CATCHUP_DAYS_BACK + 30), _days_ago(1)
    db.upsert_game(conn, _stuck("stuck", "duke", "unc", state="live"), stuck_day.isoformat())
    db.upsert_game(conn, _stuck("ancient", "duke", "smu"), old_day.isoformat())
    db.upsert_game(conn, _stuck("recent", "unc", "smu"), live_day.isoformat())
    db.upsert_game(conn, _stuck("other_div", "a", "b"), stuck_day.isoformat(), division="d3")
    conn.commit()
    monkeypatch.setattr(config, "ENABLED_DIVISIONS", ["d1"])
    fetched = []

    def fake_scoreboard(date, path):
        fetched.append((date, path))
        return {"games": [_scoreboard_game("stuck", "duke", "unc")]}  # upstream: now final

    monkeypatch.setattr(ncaa_client, "get_scoreboard", fake_scoreboard)

    assert sync.catch_up_stuck_games() == 1

    # Only the stuck date, and only for an enabled division -- not the live
    # window (run_full_sync's job), nor dates past the lookback.
    assert fetched == [(stuck_day, config.DIVISIONS["d1"])]
    statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM games")}
    assert statuses == {"stuck": "final", "ancient": "pre", "recent": "pre", "other_div": "pre"}


def test_catch_up_leaves_a_date_alone_when_the_feed_comes_back_empty(conn, monkeypatch):
    db.upsert_game(conn, _stuck("stuck", "duke", "unc"), _days_ago(10).isoformat())
    conn.commit()
    monkeypatch.setattr(ncaa_client, "get_scoreboard", lambda date, path: {"games": []})

    assert sync.catch_up_stuck_games() == 0
    assert conn.execute("SELECT status FROM games WHERE id = 'stuck'").fetchone()["status"] == "pre"
