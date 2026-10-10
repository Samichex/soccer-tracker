import datetime as dt

import pytest

from app import db
from app.backfill import most_recent_season, season_window
from app.backfill_rankings import WEEKS_BY_SEASON, backfill_rankings

ALL_WEEKS = [(season, week) for season, weeks in WEEKS_BY_SEASON.items() for week in weeks]


@pytest.mark.parametrize("season,week", ALL_WEEKS, ids=[w.date for _, w in ALL_WEEKS])
def test_every_week_has_exactly_25_unique_ranks(season, week):
    ranks = [t[0] for t in week.teams]
    assert sorted(ranks) == list(range(1, 26)), (
        f"{week.label} ({week.date}) does not have ranks 1-25 exactly once: {ranks}"
    )


@pytest.mark.parametrize("season,week", ALL_WEEKS, ids=[w.date for _, w in ALL_WEEKS])
def test_every_week_has_25_unique_school_names(season, week):
    schools = [t[1] for t in week.teams]
    assert len(schools) == len(set(schools)), (
        f"{week.label} ({week.date}) has a duplicate school name: {schools}"
    )


@pytest.mark.parametrize("season", WEEKS_BY_SEASON)
def test_each_seasons_weeks_are_in_order_and_inside_that_season(season):
    dates = [week.date for week in WEEKS_BY_SEASON[season]]
    assert dates == sorted(dates), "weeks must stay in chronological order"
    assert len(dates) == len(set(dates)), "duplicate date within a season"
    assert all(d.startswith(f"{season}-") for d in dates), f"a {season} week is dated outside {season}"


def test_most_recent_season_rolls_over_in_late_july():
    assert most_recent_season(dt.date(2027, 3, 1)) == 2026  # off-season: last fall's season
    assert most_recent_season(dt.date(2027, 7, 30)) == 2027
    assert most_recent_season(dt.date(2026, 10, 9)) == 2026


def test_season_window_covers_the_season_but_never_the_future():
    assert season_window(2025, today=dt.date(2026, 10, 9)) == (dt.date(2025, 7, 30), dt.date(2025, 12, 31))
    assert season_window(2026, today=dt.date(2026, 10, 9)) == (dt.date(2026, 7, 30), dt.date(2026, 10, 9))


def test_backfill_rankings_writes_one_season_with_prev_ranks(conn):
    assert backfill_rankings(conn, "2026") == len(WEEKS_BY_SEASON["2026"])

    preseason, week1 = WEEKS_BY_SEASON["2026"][0], WEEKS_BY_SEASON["2026"][1]
    rows = {r["school"]: r for r in conn.execute(
        "SELECT * FROM team_rankings WHERE observed_date = ?", (week1.date,)
    )}
    assert rows["Stanford"]["prev_rank"] == "9"  # 9th in the preseason poll
    assert rows["Clemson"]["prev_rank"] == "NR"  # unranked in the preseason poll
    first = conn.execute(
        "SELECT prev_rank FROM team_rankings WHERE observed_date = ? LIMIT 1", (preseason.date,)
    ).fetchone()
    assert first["prev_rank"] is None  # a season's first poll has no previous one


def test_backfill_rankings_skips_existing_d1_dates_but_not_other_divisions(conn):
    preseason = WEEKS_BY_SEASON["2026"][0].date
    conn.execute(
        "INSERT INTO team_rankings (observed_date, division, school, rank) VALUES (?, 'd3', 'X', 1)",
        (preseason,),
    )
    assert backfill_rankings(conn, "2026") == len(WEEKS_BY_SEASON["2026"])  # a d3 row doesn't block d1
    assert backfill_rankings(conn, "2026") == 0  # second run: every d1 date already present
    assert backfill_rankings(conn, "2026", force=True) == len(WEEKS_BY_SEASON["2026"])


def test_backfill_rankings_for_a_season_without_stored_weeks_is_a_no_op(conn):
    assert backfill_rankings(conn, "2031") == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM team_rankings").fetchone()["n"] == 0
