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


def test_team_schedule_results_and_record():
    unc_games = [g for g in GAMES if "unc" in (g["home_seo"], g["away_seo"])]
    rows, record = standings.build_team_schedule(unc_games, "unc", "acc")

    assert [r["result"] for r in rows] == ["l", "l", None]  # 1-2 at Duke, 1-3 vs SMU, unplayed
    assert (record["conf_l"], record["nc_l"], record["overall_l"]) == (1, 1, 2)
