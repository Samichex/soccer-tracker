import pytest
from starlette.requests import Request

from app import db, main


def _game(id_, home="duke", away="unc"):
    return {
        "game": {
            "gameID": id_,
            "gameState": "final",
            "home": {"names": {"seo": home, "short": home.title()}, "score": "2", "conferences": []},
            "away": {"names": {"seo": away, "short": away.title()}, "score": "1", "conferences": []},
        }
    }


def _player(last_name, number, goals):
    return {
        "team_id": "1", "team_seo": "duke", "is_home": 1,
        "first_name": "Al", "last_name": last_name, "number": number,
        "position": "MID", "starter": 1, "minutes_played": "90",
        "goals": goals, "assists": "0", "shots": "0", "shots_on_goal": "0",
        "saves": "0", "yellow_cards": "1", "red_cards": "0", "fouls": "0",
        "green_cards": "0", "game_winning_goals": "0", "penalty_goals": "0",
        "participated": 1,
    }


@pytest.fixture
def seeded(conn):
    main._season_roster_cache.clear()
    db.upsert_game(conn, _game("g1"), "2026-09-01")
    db.replace_player_stats(conn, "g1", [_player("Zed", "9", "3"), _player("Abe", "4", "1")])
    db.set_last_synced(conn, "2026-09-01T00:00:00")
    conn.commit()
    yield conn
    main._season_roster_cache.clear()


def _request(query=""):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "server": ("test", 80),
        "path": "/players", "root_path": "", "query_string": query.encode(), "headers": [],
    })


def test_season_roster_is_reused_until_the_next_sync(seeded):
    first = main._season_roster(seeded, "d1", "2026")
    assert main._season_roster(seeded, "d1", "2026") is first
    assert {p["last_name"]: p["g_plus_a"] for p in first.rows} == {"Zed": 3, "Abe": 1}
    assert first.rows[0]["total_cards"] == 1

    db.replace_player_stats(seeded, "g1", [_player("Zed", "9", "5")])
    db.set_last_synced(seeded, "2026-09-01T00:30:00")
    seeded.commit()

    rebuilt = main._season_roster(seeded, "d1", "2026")
    assert rebuilt is not first
    assert [(p["last_name"], p["goals"]) for p in rebuilt.rows] == [("Zed", 5)]


def test_players_page_sorting_does_not_reorder_the_cached_rows(seeded):
    cached_order = [p["last_name"] for p in main._season_roster(seeded, "d1", "2026").rows]

    for sort in ("name", "g"):
        main.players_list(_request(), sort=sort, dir="asc", season="2026", division="d1")

    assert [p["last_name"] for p in main._season_roster(seeded, "d1", "2026").rows] == cached_order


def test_leaderboard_leaves_its_input_rows_untouched():
    rows = [{"goals": 1}, {"goals": 3}, {"goals": 2}]
    board = main._leaderboard(rows, "goals", n=2)
    assert [(r["goals"], r["rank"]) for r in board] == [(3, 1), (2, 2)]
    assert rows == [{"goals": 1}, {"goals": 3}, {"goals": 2}]
