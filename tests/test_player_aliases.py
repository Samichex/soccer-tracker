import json

import pytest

from app import db, normalize


def add_player(conn, game_id, first, last, number="9", team="t", **extra):
    conn.execute(
        "INSERT INTO player_stats (game_id, team_id, team_seo, number, first_name, last_name, goals) "
        "VALUES (?,?,?,?,?,?,?)",
        (game_id, "1", team, number, first, last, extra.get("goals", "0")),
    )


def names(conn):
    return sorted((r["game_id"], r["first_name"], r["last_name"]) for r in conn.execute("SELECT * FROM player_stats"))


def alias(team, f1, l1, f2, l2):
    return {"team": team, "from": [f1, l1], "to": [f2, l2]}


# --- ingest: normalize_rows --------------------------------------------------

@pytest.fixture
def aliases(monkeypatch):
    table = {("t", "Gabe", "Smyth"): ("Gabriel", "Smyth"), ("t", "Alexander", "Teixeira"): ("Alex", "Teixeira")}
    monkeypatch.setattr(normalize, "get_player_aliases", lambda: table)
    return table


def test_normalize_rows_applies_alias_for_that_team_only(conn, aliases):
    rows = [{"first_name": "Gabe", "last_name": "Smyth", "position": "F"}]
    normalize.normalize_rows(conn, "t", rows)
    assert (rows[0]["first_name"], rows[0]["last_name"]) == ("Gabriel", "Smyth")

    other = [{"first_name": "Gabe", "last_name": "Smyth", "position": "F"}]
    normalize.normalize_rows(conn, "other-team", other)
    assert (other[0]["first_name"], other[0]["last_name"]) == ("Gabe", "Smyth")


def test_normalize_rows_aliases_the_title_cased_form_of_an_all_caps_name(conn, aliases):
    rows = [{"first_name": "ALEXANDER", "last_name": "TEIXEIRA", "position": "D"}]
    normalize.normalize_rows(conn, "t", rows)
    assert (rows[0]["first_name"], rows[0]["last_name"]) == ("Alex", "Teixeira")


def test_missing_aliases_file_means_no_aliases(tmp_path):
    assert normalize.load_aliases_file(tmp_path / "nope.json") == {"aliases": [], "not_same": []}


def test_shipped_aliases_file_is_well_formed():
    data = normalize.load_aliases_file()
    assert set(data) == {"aliases", "not_same"}
    sources = set()
    for a in data["aliases"]:
        src, dst = (a["team"], *a["from"]), (a["team"], *a["to"])
        assert src != dst
        assert all(isinstance(x, str) for x in (*a["from"], *a["to"]))
        sources.add(src)
    # no chains: applying aliases once must reach a final spelling
    assert not [a for a in data["aliases"] if (a["team"], *a["to"]) in sources]
    assert len(sources) == len(data["aliases"])  # one target per spelling


# --- stored rows: db.apply_player_aliases ------------------------------------

def test_apply_rewrites_stored_names_only_for_the_aliased_team(conn):
    add_player(conn, "g1", "Gabe", "Smyth")
    add_player(conn, "g2", "Gabe", "Smyth", team="other")
    add_player(conn, "g3", "Gabriel", "Smyth")
    result = db.apply_player_aliases(conn, [alias("t", "Gabe", "Smyth", "Gabriel", "Smyth")])
    assert result == {"updated": 1, "skipped": 0}
    assert names(conn) == [("g1", "Gabriel", "Smyth"), ("g2", "Gabe", "Smyth"), ("g3", "Gabriel", "Smyth")]


def test_apply_leaves_stats_untouched(conn):
    add_player(conn, "g1", "Gabe", "Smyth", goals="3")
    db.apply_player_aliases(conn, [alias("t", "Gabe", "Smyth", "Gabriel", "Smyth")])
    assert conn.execute("SELECT goals FROM player_stats").fetchone()["goals"] == "3"


def test_apply_runs_once_per_change_to_the_alias_list(conn):
    aliases = [alias("t", "Gabe", "Smyth", "Gabriel", "Smyth")]
    add_player(conn, "g1", "Gabe", "Smyth")
    assert db.apply_player_aliases(conn, aliases) is not None
    add_player(conn, "g2", "Gabe", "Smyth")  # e.g. a row written by something that bypassed ingest
    assert db.apply_player_aliases(conn, aliases) is None
    assert ("g2", "Gabe", "Smyth") in names(conn)
    assert db.apply_player_aliases(conn, aliases, force=True)["updated"] == 1
    assert ("g2", "Gabriel", "Smyth") in names(conn)


def test_apply_picks_up_newly_added_aliases(conn):
    add_player(conn, "g1", "Gabe", "Smyth")
    add_player(conn, "g2", "Matt", "Lee")
    first = [alias("t", "Gabe", "Smyth", "Gabriel", "Smyth")]
    db.apply_player_aliases(conn, first)
    db.apply_player_aliases(conn, [*first, alias("t", "Matt", "Lee", "Matthew", "Lee")])
    assert names(conn) == [("g1", "Gabriel", "Smyth"), ("g2", "Matthew", "Lee")]


def test_apply_skips_rows_that_would_collide_on_the_primary_key(conn):
    # both spellings already present in one game under one number
    add_player(conn, "g1", "Gabe", "Smyth")
    add_player(conn, "g1", "Gabriel", "Smyth")
    add_player(conn, "g2", "Gabe", "Smyth")
    result = db.apply_player_aliases(conn, [alias("t", "Gabe", "Smyth", "Gabriel", "Smyth")])
    assert result == {"updated": 1, "skipped": 1}
    assert names(conn) == [("g1", "Gabe", "Smyth"), ("g1", "Gabriel", "Smyth"), ("g2", "Gabriel", "Smyth")]


def test_apply_failure_rolls_back_everything_and_does_not_raise(conn):
    add_player(conn, "g1", "Gabe", "Smyth")
    marker = "SELECT value FROM sync_meta WHERE key = 'player_aliases_applied'"
    before = conn.execute(marker).fetchone()
    bad = [alias("t", "Gabe", "Smyth", "Gabriel", "Smyth"), {"team": "t", "from": ["x"]}]  # malformed entry
    assert db.apply_player_aliases(conn, bad) is None
    assert names(conn) == [("g1", "Gabe", "Smyth")]
    assert tuple(conn.execute(marker).fetchone()) == tuple(before)  # not marked as applied


def test_init_db_applies_the_shipped_aliases(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    shipped = normalize.load_aliases_file()["aliases"]
    assert shipped, "expected generated aliases to be committed"
    a = shipped[0]
    db.init_db()
    with db.get_conn() as c:
        add_player(c, "g1", *a["from"], team=a["team"])
    db.init_db()  # the next startup: alias list unchanged, so it must NOT rewrite (applied once per change)
    with db.get_conn() as c:
        assert c.execute("SELECT first_name, last_name FROM player_stats").fetchone()[:] == tuple(a["from"])
        db.apply_player_aliases(c, force=True)
        assert c.execute("SELECT first_name, last_name FROM player_stats").fetchone()[:] == tuple(a["to"])
