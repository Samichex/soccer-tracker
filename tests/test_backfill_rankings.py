from app.backfill_rankings import WEEKS


def test_every_week_has_exactly_25_unique_ranks():
    for week in WEEKS:
        ranks = [t[0] for t in week.teams]
        assert sorted(ranks) == list(range(1, 26)), (
            f"{week.label} ({week.date}) does not have ranks 1-25 exactly once: {ranks}"
        )


def test_every_week_has_25_unique_school_names():
    for week in WEEKS:
        schools = [t[1] for t in week.teams]
        assert len(schools) == len(set(schools)), (
            f"{week.label} ({week.date}) has a duplicate school name: {schools}"
        )


def test_week_dates_are_strictly_increasing():
    dates = [week.date for week in WEEKS]
    assert dates == sorted(dates), "WEEKS entries must stay in chronological order"
    assert len(dates) == len(set(dates)), "duplicate observed_date across WEEKS entries"
