"""Rank history is per season: a snapshot's season is the year of its
observed_date, and no reader may blend one season's polls into another's."""

from app import db


def _poll(*seos, prev=None):
    return [
        {"school": s.title(), "seo": s, "rank": i, "prev_rank": prev, "points": None,
         "first_place_votes": None, "record": None}
        for i, s in enumerate(seos, start=1)
    ]


def _regional(*seos, region=1):
    return [
        {"region": region, "rank": i, "school": s.title(), "seo": s, "npi": None, "record": None}
        for i, s in enumerate(seos, start=1)
    ]


def test_ranking_history_and_full_history_are_scoped_to_one_season(conn):
    db.replace_rankings_for_date(conn, "2026-11-10", _poll("duke", "unc"))
    db.replace_rankings_for_date(conn, "2027-08-18", _poll("unc", "duke"))

    assert [r["observed_date"] for r in db.get_ranking_history(conn, "duke", "2027")] == ["2027-08-18"]
    assert len(db.get_ranking_history(conn, "duke")) == 2  # no season -> every season
    assert {r["observed_date"] for r in db.get_all_ranking_history(conn, season="2026")} == {"2026-11-10"}


def test_new_seasons_first_poll_doesnt_inherit_last_seasons_ranks(conn):
    db.replace_rankings_for_date(conn, "2026-11-10", _poll("duke", "unc", prev="1"))
    db.replace_rankings_for_date(conn, "2027-08-18", _poll("unc", "duke"))  # preseason: no PREVIOUS

    latest = {r["seo"]: r for r in db.get_latest_rankings(conn, season="2027")}
    assert latest["unc"]["rank"] == 1
    assert latest["unc"]["prev_rank"] is None  # not 2026's final rank of 2


def test_latest_rankings_is_empty_before_a_seasons_first_poll(conn):
    # August 2027 games exist, but no 2027 poll yet: no 2026 badges on them.
    db.replace_rankings_for_date(conn, "2026-11-10", _poll("duke"))
    assert db.get_latest_rankings(conn, season="2027") == []
    assert [r["seo"] for r in db.get_latest_rankings(conn, season="2026")] == ["duke"]


def test_regional_rankings_and_regions_are_scoped_to_one_season(conn):
    db.replace_regional_rankings_for_date(conn, "2026-11-03", _regional("tufts", region=1))
    db.replace_regional_rankings_for_date(conn, "2027-10-05", _regional("tufts", region=2))

    assert db.get_team_regions(conn, season="2026") == {"tufts": 1}
    assert db.get_team_regions(conn, season="2027") == {"tufts": 2}
    latest = db.get_latest_regional_rankings(conn, season="2027")
    assert [(r["seo"], r["region"], r["prev_rank"]) for r in latest] == [("tufts", 2, None)]
    history = db.get_all_regional_ranking_history(conn, region=1, season="2027")
    assert history == []
