from app import standings


def _g(home, away, hs, as_, hc="acc", ac="acc", status="final"):
    return {
        "home_seo": home, "away_seo": away,
        "home_name": home.title(), "away_name": away.title(),
        "home_name_short": home.title(), "away_name_short": away.title(),
        "home_score": hs, "away_score": as_,
        "home_conference": hc, "away_conference": ac, "status": status,
    }


GAMES = [
    _g("duke", "unc", "2", "1"),                    # conference game
    _g("duke", "smu", "0", "0", ac="american"),     # non-conference draw
    _g("unc", "smu", "1", "3", ac="american"),      # non-conference loss
    _g("unc", "duke", "", "", status="pre"),        # unplayed -- ignored
]


def test_conference_table_buckets_and_ranks_only_that_conference():
    table = standings.build_conference_table(GAMES, "acc")

    assert [t["seo"] for t in table] == ["duke", "unc"]  # by conference points
    duke, unc = table
    assert (duke["conf_w"], duke["nc_d"], duke["conf_pts"]) == (1, 1, 3)
    assert (unc["conf_l"], unc["nc_l"], unc["gf"], unc["ga"], unc["gd"]) == (1, 1, 2, 5, -3)


def test_all_teams_table_uses_each_teams_own_conference():
    table = {t["seo"]: t for t in standings.build_all_teams_table(GAMES)}

    assert list(table) == ["duke", "smu", "unc"]  # by name
    assert table["smu"]["conference"] == "american"
    # Both of SMU's games were against ACC teams, so neither is a conference game.
    assert (table["smu"]["nc_w"], table["smu"]["nc_d"], table["smu"]["conf_w"]) == (1, 1, 0)
    assert (table["duke"]["overall_w"], table["duke"]["overall_d"]) == (1, 1)


def test_conference_gd_counts_only_conference_games():
    table = {t["seo"]: t for t in standings.build_all_teams_table(GAMES)}

    # Duke: beat UNC 2-1 (conference), drew SMU 0-0 (non-conference).
    assert (table["duke"]["conf_gf"], table["duke"]["conf_ga"], table["duke"]["conf_gd"]) == (2, 1, 1)
    assert table["duke"]["gd"] == 1
    # UNC: lost 1-2 to Duke (conference), lost 1-3 to SMU (non-conference).
    assert table["unc"]["conf_gd"] == -1
    assert table["unc"]["gd"] == -3
    # SMU played no conference games.
    assert table["smu"]["conf_gd"] == 0


def test_form_is_last_five_results_oldest_first():
    # Duke: W D L W W D W (seven games, mixed conference and non-conference).
    scores = [("1", "0"), ("2", "2"), ("0", "1"), ("3", "0"), ("1", "0"), ("1", "1"), ("2", "1")]
    games = [
        _g("duke", f"opp{i}", hs, as_, ac="acc" if i % 2 else "american")
        for i, (hs, as_) in enumerate(scores)
    ]
    duke = {t["seo"]: t for t in standings.build_all_teams_table(games)}["duke"]

    assert duke["form"] == ["l", "w", "w", "d", "w"]  # games 3-7, most recent last


def test_form_is_short_when_a_team_has_played_fewer_than_five():
    table = {t["seo"]: t for t in standings.build_all_teams_table(GAMES)}

    assert table["duke"]["form"] == ["w", "d"]
    assert table["unc"]["form"] == ["l", "l"]  # unplayed game is not in form


def test_team_schedule_results_and_record():
    unc_games = [g for g in GAMES if "unc" in (g["home_seo"], g["away_seo"])]
    rows, record = standings.build_team_schedule(unc_games, "unc", "acc")

    assert [r["result"] for r in rows] == ["l", "l", None]  # 1-2 at Duke, 1-3 vs SMU, unplayed
    assert (record["conf_l"], record["nc_l"], record["overall_l"]) == (1, 1, 2)
