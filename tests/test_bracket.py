import pytest
from starlette.requests import Request

from app import db, main, ncaa_client, reference_data, sync


def _team(seo, name, *, top, seed=None, score=0, winner=False):
    return {
        "isTop": top, "seoname": seo, "nameShort": name,
        "seed": seed, "score": score, "isWinner": winner,
    }


def _slot(contest_id, bracket_id, teams=(), **overrides):
    game = {
        "contestId": contest_id, "bracketId": bracket_id, "enabled": bool(teams),
        "teams": list(teams), "startDate": "11/23/2025", "startTimeEpoch": 1763922000,
        "statusCodeDisplay": "final", "finalMessage": "FINAL",
    }
    game.update(overrides)
    return game


def _feed(*games):
    return {"championships": [{"games": list(games)}]}


def test_parse_bracket_skips_unpublished_skeleton_and_unused_slots():
    skeleton = _feed(_slot(1, 101, statusCodeDisplay="pre", finalMessage=""), _slot(2, 201))
    assert sync._parse_bracket(skeleton) == []
    assert sync._parse_bracket({"championships": []}) == []


def test_parse_bracket_reads_teams_seed_score_and_round():
    feed = _feed(_slot(
        6500814, 201,
        [_team("hofstra", "Hofstra", top=False, score=3, winner=True),
         _team("vermont", "Vermont", top=True, seed=1, score=2)],
    ))
    [row] = sync._parse_bracket(feed)
    assert (row["contest_id"], row["bracket_id"], row["round"]) == ("6500814", 201, 2)
    assert (row["start_date"], row["status"], row["final_message"]) == ("2025-11-23", "final", "FINAL")
    assert (row["top_seo"], row["top_seed"], row["top_score"], row["top_winner"]) == ("vermont", 1, 2, 0)
    assert (row["bottom_seo"], row["bottom_seed"], row["bottom_score"], row["bottom_winner"]) == ("hofstra", None, 3, 1)


def test_parse_bracket_keeps_a_slot_still_waiting_on_one_team():
    feed = _feed(_slot(7, 301, [_team("duke", "Duke", top=True, seed=3)], statusCodeDisplay="pre", finalMessage=""))
    [row] = sync._parse_bracket(feed)
    assert (row["top_name"], row["bottom_name"], row["bottom_seo"]) == ("Duke", None, None)


def test_parse_bracket_drops_a_slot_with_an_unrecognized_bracket_id(caplog):
    feed = _feed(_slot(7, 901, [_team("duke", "Duke", top=True)]), _slot(8, "", [_team("unc", "UNC", top=True)]))
    assert sync._parse_bracket(feed) == []
    assert "unrecognized id/slot" in caplog.text


def _stored(conn, division="d1", season="2025"):
    return [dict(r) for r in db.get_bracket_games(conn, division, season)]


def test_sync_bracket_stores_and_replaces_wholesale(conn, monkeypatch):
    feeds = iter([
        _feed(_slot(1, 201, [_team("a", "A", top=True), _team("b", "B", top=False)], statusCodeDisplay="pre")),
        _feed(_slot(1, 201, [_team("a", "A", top=True, score=1, winner=True), _team("b", "B", top=False)])),
    ])
    monkeypatch.setattr(ncaa_client, "get_bracket", lambda season, sport_path: next(feeds))

    assert sync.sync_bracket(conn, "d1", 2025) == 1
    assert _stored(conn)[0]["status"] == "pre"
    assert sync.sync_bracket(conn, "d1", 2025) == 1
    [row] = _stored(conn)
    assert (row["status"], row["top_winner"]) == ("final", 1)


def test_sync_bracket_keeps_a_stored_bracket_when_the_feed_comes_back_empty(conn, monkeypatch):
    good = _feed(_slot(1, 201, [_team("a", "A", top=True), _team("b", "B", top=False)]))
    feeds = iter([good, _feed(_slot(1, 201))])
    monkeypatch.setattr(ncaa_client, "get_bracket", lambda season, sport_path: next(feeds))

    sync.sync_bracket(conn, "d1", 2025)
    assert sync.sync_bracket(conn, "d1", 2025) == 0
    assert len(_stored(conn)) == 1


def test_brackets_are_kept_per_division_and_season(conn):
    row = {"contest_id": "1", "bracket_id": 201, "round": 2}
    db.replace_bracket(conn, "d1", "2025", [row])
    db.replace_bracket(conn, "d3", "2025", [{**row, "contest_id": "2"}])
    db.replace_bracket(conn, "d1", "2025", [])  # re-sync of D1 must not touch D3
    assert _stored(conn, "d1", "2025") == []
    assert len(_stored(conn, "d3", "2025")) == 1
    assert _stored(conn, "d3", "2026") == []


def test_get_bracket_games_flags_games_and_teams_that_have_pages(conn):
    db.replace_bracket(conn, "d1", "2025", [
        {"contest_id": "g1", "bracket_id": 201, "round": 2, "top_seo": "duke", "bottom_seo": "ghost"},
        {"contest_id": "g2", "bracket_id": 101, "round": 1},
    ])
    db.upsert_team_basic(conn, "duke", "Duke", "acc")
    conn.execute("INSERT INTO games (id, date) VALUES ('g1', '2025-11-23')")

    rows = {r["contest_id"]: r for r in db.get_bracket_games(conn, "d1", "2025")}
    assert [r["contest_id"] for r in db.get_bracket_games(conn, "d1", "2025")] == ["g2", "g1"]  # by round
    assert (rows["g1"]["has_game"], rows["g1"]["top_known"], rows["g1"]["bottom_known"]) == (1, 1, 0)
    assert rows["g2"]["has_game"] == 0


def test_build_bracket_rounds_names_rounds_and_hides_scores_before_kickoff(conn):
    db.replace_bracket(conn, "d1", "2025", [
        {"contest_id": "a", "bracket_id": 401, "round": 4, "status": "pre", "start_epoch": 1763920800,
         "top_name": "Duke", "top_score": 0, "bottom_name": None, "bottom_score": 0},
        {"contest_id": "b", "bracket_id": 101, "round": 1, "status": "final",
         "top_name": "A", "top_score": 2, "top_winner": 1, "bottom_name": "B", "bottom_score": 1},
    ])
    rounds = reference_data.build_bracket_rounds(db.get_bracket_games(conn, "d1", "2025"))

    assert [(r["number"], r["name"]) for r in rounds] == [(1, "First Round"), (4, "Quarterfinals")]
    final = rounds[0]["games"][0]
    assert [s["score"] for s in final["sides"]] == [2, 1]
    assert final["sides"][0]["winner"] is True
    upcoming = rounds[1]["games"][0]
    assert [s["score"] for s in upcoming["sides"]] == [None, None]
    assert upcoming["sides"][1]["name"] is None
    assert upcoming["kickoff"] == "1:00 PM ET"  # 1763920800 = 2025-11-23 18:00 UTC


def _request(path="/bracket"):
    return Request({"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": []})


def test_bracket_page_renders_published_bracket_and_empty_state(conn):
    db.upsert_game(conn, {"game": {
        "gameID": "g1", "startTime": "1:00 PM ET", "startTimeEpoch": "1763922000", "gameState": "final",
        "home": {"names": {"seo": "a", "short": "A"}, "score": "1", "conferences": []},
        "away": {"names": {"seo": "b", "short": "B"}, "score": "0", "conferences": []},
    }}, "2025-11-23")
    conn.commit()

    empty = main.bracket_page(_request(), season="2025").body.decode()
    assert "bracket hasn't been announced yet" in empty

    db.replace_bracket(conn, "d1", "2025", [{
        "contest_id": "g1", "bracket_id": 201, "round": 2, "status": "final", "final_message": "FINAL",
        "start_date": "2025-11-23", "top_seo": "a", "top_name": "A", "top_seed": 4, "top_score": 1, "top_winner": 1,
        "bottom_seo": "b", "bottom_name": "B", "bottom_score": 0,
    }])
    conn.commit()
    page = main.bracket_page(_request(), season="2025").body.decode()
    assert "Second Round" in page and 'href="/game/g1"' in page and 'href="/team/a"' in page


def test_parse_bracket_keeps_where_the_winner_advances():
    feed = _feed(
        _slot(1, 102, [_team("a", "A", top=True), _team("b", "B", top=False)],
              victorBracketPositionId=201, victorGamePosition="Bottom"),
        _slot(2, 601, [_team("c", "C", top=True), _team("d", "D", top=False)]),
    )
    first, final = sync._parse_bracket(feed)
    assert (first["victor_bracket_id"], first["victor_slot"]) == (201, "bottom")
    assert (final["victor_bracket_id"], final["victor_slot"]) == (None, None)


def _tree(conn, *games):
    db.replace_bracket(conn, "d1", "2025", [
        {"contest_id": str(g[0]), "bracket_id": g[0], "round": g[0] // 100,
         "victor_bracket_id": g[1], "victor_slot": g[2], "status": "final",
         "top_name": "T", "bottom_name": "B", "top_winner": g[3], "bottom_winner": 1 - g[3]}
        for g in games
    ])
    rounds = reference_data.build_bracket_rounds(db.get_bracket_games(conn, "d1", "2025"))
    return {g["bracket_id"]: g for r in rounds for g in r["games"]}, rounds


def test_bracket_layout_centers_a_game_between_the_two_that_feed_it(conn):
    # (bracket_id, victor_bracket_id, victor_slot, top_won)
    games, rounds = _tree(conn, (101, 201, "top", 1), (102, 201, "bottom", 0), (201, None, None, 1))

    assert (games[101]["y"], games[102]["y"], games[201]["y"]) == (0, 1, 0.5)
    assert games[101]["links"] is None
    assert len(games[201]["links"]["paths"]) == 2
    assert all(r["height"] == rounds[0]["height"] for r in rounds)  # columns share one height


def test_bracket_layout_lines_up_a_game_with_its_only_feeder(conn):
    # D1's first round: one earlier game per second-round game, no bye pairing.
    games, _ = _tree(conn, (102, 201, "bottom", 1), (103, 202, "top", 1), (201, None, None, 1), (202, None, None, 1))
    assert games[201]["y"] == games[102]["y"] and games[202]["y"] == games[103]["y"]


def test_bracket_links_run_from_the_winning_row_to_the_row_it_advances_into(conn):
    games, _ = _tree(conn, (101, 201, "bottom", 0), (201, None, None, 1))  # bottom team won game 101
    lay = reference_data.BRACKET_LAYOUT
    [path] = games[201]["links"]["paths"]
    top = games[201]["links"]["top"]
    start_y = lay["row_h"] * 1.5 - top  # the bottom row of the feeder, which sits level with game 201
    end_y = lay["row_h"] * 1.5 - top    # and it enters the bottom row of 201
    assert path["decided"] is True
    assert path["d"].startswith(f"M0 {start_y:.1f} H{lay['gutter'] / 2:g}")
    assert path["d"].endswith(f"V{end_y:.1f} H{lay['gutter']}")


def test_bracket_layout_survives_a_malformed_loop_in_the_feed(conn):
    games, _ = _tree(conn, (101, 201, "top", 1), (201, 101, "top", 1))
    assert set(games) == {101, 201} and all("y" in g for g in games.values())


def _rounds_with(conn, *games):
    """games: (bracket_id, victor_bracket_id, victor_slot, status, top_name, bottom_name, top_seed)"""
    db.replace_bracket(conn, "d1", "2025", [
        {"contest_id": str(g[0]), "bracket_id": g[0], "round": g[0] // 100, "victor_bracket_id": g[1],
         "victor_slot": g[2], "status": g[3], "top_name": g[4], "bottom_name": g[5], "top_seed": g[6],
         "top_winner": 1 if g[3] == "final" else 0, "bottom_winner": 0}
        for g in games
    ])
    rounds = reference_data.build_bracket_rounds(db.get_bracket_games(conn, "d1", "2025"))
    return {g["bracket_id"]: g for r in rounds for g in r["games"]}, rounds


def test_advances_line_names_the_opponent_and_round_before_and_after_the_game(conn):
    games, _ = _rounds_with(
        conn,
        (102, 201, "bottom", "pre", "Syracuse", "Hofstra", None),
        (103, 202, "top", "final", "Duke", "UNC", None),
        (201, 301, "top", "pre", "Vermont", None, 1),
        (202, 301, "bottom", "pre", None, None, None),
        (301, None, None, "pre", None, None, None),
    )
    # Winner of 102 takes the bottom row of 201; the top row (seed 1 Vermont) is the opponent.
    assert games[102]["advances"] == "Winner plays No. 1 Vermont in the Second Round"
    # Opponent not decided yet.
    assert games[103]["advances"] == "Duke advanced to the Second Round"
    assert games[201]["advances"] == "Winner advances to the Third Round"
    assert games[301]["advances"] == ""  # the final goes nowhere


def test_current_bracket_round_is_the_earliest_with_a_game_left_else_the_last(conn):
    _, rounds = _rounds_with(
        conn,
        (101, 201, "top", "final", "A", "B", None),
        (102, 201, "bottom", "final", "C", "D", None),
        (201, 301, "top", "live", "A", "C", None),
        (301, None, None, "pre", None, None, None),
    )
    assert reference_data.current_bracket_round(rounds) == 2

    _, finished = _rounds_with(conn, (101, 201, "top", "final", "A", "B", None), (201, None, None, "final", "A", "C", None))
    assert reference_data.current_bracket_round(finished) == 2
    assert reference_data.current_bracket_round([]) is None


def test_games_in_a_round_are_listed_in_tree_order(conn):
    _, rounds = _rounds_with(
        conn,
        (101, 201, "top", "final", "A", "B", None),
        (102, 202, "top", "final", "C", "D", None),
        (201, 301, "bottom", "pre", "A", "E", None),  # feeds the bottom of 301 ...
        (202, 301, "top", "pre", "C", "F", None),     # ... so 202 sits above 201 in the tree
        (301, None, None, "pre", None, None, None),
    )
    assert [g["bracket_id"] for g in rounds[1]["games"]] == [202, 201]


def _seed_two_rounds(conn):
    db.replace_bracket(conn, "d1", "2025", [
        {"contest_id": "a", "bracket_id": 101, "round": 1, "status": "final", "top_name": "A", "bottom_name": "B",
         "top_winner": 1, "victor_bracket_id": 201, "victor_slot": "top"},
        {"contest_id": "b", "bracket_id": 201, "round": 2, "status": "pre", "top_name": "A", "bottom_name": "C"},
    ])
    conn.execute("INSERT INTO games (id, date, season, division) VALUES ('a', '2025-11-20', '2025', 'd1')")
    conn.commit()


def test_bracket_page_opens_on_the_current_round_unless_one_is_asked_for(conn):
    _seed_two_rounds(conn)

    def selected(**kw):
        html = main.bracket_page(_request(), season="2025", **kw).body.decode()
        return [m for m in ("1", "2") if f'bracket-round is-selected" data-round="{m}"' in html]

    assert selected() == ["2"]            # round 1 is all final; round 2 still has a game to play
    assert selected(round=1) == ["1"]
    assert selected(round=9) == ["2"]     # not a round of this bracket -> falls back to the current one


def test_bracket_page_offers_a_whole_card_link_only_for_games_that_have_a_page(conn):
    _seed_two_rounds(conn)
    html = main.bracket_page(_request(), season="2025").body.decode()
    assert html.count('class="bracket-card-link"') == 1 and 'href="/game/a"' in html
    assert "Winner plays C in the Second Round" not in html  # 'b' is round 2; A advanced into it
    assert "A advanced to the Second Round vs C" in html
