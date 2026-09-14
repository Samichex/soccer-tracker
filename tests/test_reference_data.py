import datetime as dt

from app import reference_data


_KICKOFF = dt.datetime(2026, 9, 1, 19, 0, 0, tzinfo=dt.timezone.utc)
_START_EPOCH = int(_KICKOFF.timestamp())


def _minutes_after_kickoff(minutes: float) -> dt.datetime:
    return _KICKOFF + dt.timedelta(minutes=minutes)


def test_live_match_clock_none_period():
    assert reference_data.live_match_clock(None, _START_EPOCH) == "LIVE"


def test_live_match_clock_halftime_break_shows_ht():
    assert reference_data.live_match_clock("HALF", _START_EPOCH, now=_minutes_after_kickoff(46)) == "HT"


def test_live_match_clock_first_half_shows_elapsed_minute():
    now = _minutes_after_kickoff(34)
    assert reference_data.live_match_clock("1ST HALF", _START_EPOCH, now=now) == "34'"


def test_live_match_clock_first_half_never_shows_zero():
    now = _minutes_after_kickoff(0)
    assert reference_data.live_match_clock("1ST HALF", _START_EPOCH, now=now) == "1'"


def test_live_match_clock_first_half_caps_at_45_during_stoppage():
    now = _minutes_after_kickoff(50)
    assert reference_data.live_match_clock("1ST HALF", _START_EPOCH, now=now) == "45'"


def test_live_match_clock_second_half_shows_continuous_match_minute():
    # 45 min first half + 15 min break + 10 min into the second half.
    now = _minutes_after_kickoff(45 + 15 + 10)
    assert reference_data.live_match_clock("2ND HALF", _START_EPOCH, now=now) == "55'"


def test_live_match_clock_second_half_floors_at_46_if_break_ran_long():
    # A halftime break longer than the assumed 15 min shouldn't show a
    # second-half minute lower than 46.
    now = _minutes_after_kickoff(45 + 10)
    assert reference_data.live_match_clock("2ND HALF", _START_EPOCH, now=now) == "46'"


def test_live_match_clock_second_half_caps_at_90_during_stoppage():
    now = _minutes_after_kickoff(45 + 15 + 50)
    assert reference_data.live_match_clock("2ND HALF", _START_EPOCH, now=now) == "90'"


def test_live_match_clock_falls_back_without_start_epoch():
    assert reference_data.live_match_clock("1ST HALF", None) == "1ST"


def test_live_match_clock_falls_back_for_unrecognized_period():
    # e.g. an overtime period -- not "1st half"/"2nd half", so the elapsed-
    # time heuristic doesn't apply; use the existing abbreviation instead.
    now = _minutes_after_kickoff(100)
    assert reference_data.live_match_clock("1ST OT", _START_EPOCH, now=now) == "1ST"


def test_title_case_name_all_caps():
    assert reference_data.title_case_name("SMITH") == "Smith"


def test_title_case_name_preserves_apostrophe_break():
    assert reference_data.title_case_name("O'BRIEN") == "O'Brien"


def test_title_case_name_preserves_hyphen_break():
    assert reference_data.title_case_name("SMITH-JONES") == "Smith-Jones"


def test_title_case_name_does_not_break_on_accented_letters():
    assert reference_data.title_case_name("PEÑA") == "Peña"
    assert reference_data.title_case_name("MUÑOZ") == "Muñoz"
    assert reference_data.title_case_name("JOÃO") == "João"


def test_title_case_name_empty_or_none():
    assert reference_data.title_case_name(None) == ""
    assert reference_data.title_case_name("") == ""
