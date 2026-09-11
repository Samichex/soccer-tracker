from app import normalize


def test_canonical_position_known_abbreviations():
    assert normalize.canonical_position("F") == "ATK"
    assert normalize.canonical_position("D") == "DEF"
    assert normalize.canonical_position("M") == "MID"
    assert normalize.canonical_position("GK") == "GK"


def test_canonical_position_spelled_out():
    assert normalize.canonical_position("FORWARD") == "ATK"
    assert normalize.canonical_position("DEFENDER") == "DEF"
    assert normalize.canonical_position("MIDFIELDER") == "MID"
    assert normalize.canonical_position("GOALKEEPER") == "GK"


def test_canonical_position_case_and_whitespace_insensitive():
    assert normalize.canonical_position(" forward ") == "ATK"


def test_canonical_position_unrecognized_spelling_uses_heuristic():
    assert normalize.canonical_position("RIGHT BACK") == "DEF"
    assert normalize.canonical_position("SWEEPER KEEPER") == "GK"


def test_canonical_position_empty_or_none():
    assert normalize.canonical_position(None) == ""
    assert normalize.canonical_position("") == ""


def test_canonical_position_truly_unrecognized_becomes_blank():
    # canonical_position never raises -- an upstream spelling the heuristic
    # can't match becomes "" rather than surfacing as an error. This test
    # documents that gap: if it starts failing, either the heuristic grew
    # broader (fine) or something now raises (check callers can handle it).
    assert normalize.canonical_position("LIBERO") == ""


class _FakeConn:
    def __init__(self, existing=None):
        self._existing = existing

    def execute(self, query, params):
        existing = self._existing

        class _Result:
            def fetchone(self_):
                return existing

        return _Result()


def test_canonical_name_non_uppercase_last_name_trusted_as_is():
    first, last = normalize.canonical_name(_FakeConn(), "duke", "john", "Smith")
    assert (first, last) == ("john", "Smith")


def test_canonical_name_all_caps_without_reference_titlecases():
    first, last = normalize.canonical_name(_FakeConn(existing=None), "duke", "JOHN", "SMITH")
    assert (first, last) == ("John", "Smith")


def test_canonical_name_all_caps_prefers_existing_properly_cased_spelling():
    existing = {"first_name": "Callum", "last_name": "Lugton"}
    first, last = normalize.canonical_name(
        _FakeConn(existing=existing), "washington", "CALLUM", "LUGTON"
    )
    assert (first, last) == ("Callum", "Lugton")


def test_fix_mojibake_repairs_double_decoded_utf8():
    assert normalize.fix_mojibake("PeÃ±a") == "Peña"
    assert normalize.fix_mojibake("MuÃ±oz") == "Muñoz"


def test_fix_mojibake_leaves_correct_text_alone():
    assert normalize.fix_mojibake("João") == "João"
    assert normalize.fix_mojibake("Pérez") == "Pérez"
    assert normalize.fix_mojibake("Smith") == "Smith"


def test_fix_mojibake_handles_none_and_empty():
    assert normalize.fix_mojibake(None) is None
    assert normalize.fix_mojibake("") == ""


def test_canonical_name_repairs_mojibake_in_mixed_case_name():
    first, last = normalize.canonical_name(_FakeConn(), "north-carolina-st", "AdriÃ ", "Erik")
    assert first == "Adrià"


def test_normalize_rows_applies_both_helpers_in_place():
    rows = [
        {"first_name": "JOHN", "last_name": "SMITH", "position": "FORWARD"},
    ]
    normalize.normalize_rows(_FakeConn(existing=None), "duke", rows)
    assert rows[0]["first_name"] == "John"
    assert rows[0]["last_name"] == "Smith"
    assert rows[0]["position"] == "ATK"
