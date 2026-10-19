from app import name_dupes
from app.name_dupes import REVIEW, SAFE, detect, name_tokens


def rec(team, number, first, last, game, date="2026-09-01", season="2026"):
    return {"team": team, "season": season, "date": date, "game_id": game,
            "number": number, "first": first, "last": last}


def test_name_tokens_fold_case_accents_punctuation_and_suffixes():
    assert name_tokens("José", "O'Rourke Jr.") == ["jose", "orourke"]
    assert name_tokens("Mitchell", "Rogers-Shevchenko") == ["mitchell", "rogers", "shevchenko"]
    assert name_tokens("Maddox", "(Captain)") == ["maddox"]
    assert name_tokens("Maddox", "(Cap") == ["maddox"]


def test_name_tokens_keeps_lost_encoding_as_wildcard():
    assert name_tokens("H�kon", "Edstrom") == ["h?kon", "edstrom"]


def test_case_variants_are_safe_and_point_at_best_cased_spelling():
    # Civan De Jesus Ferreira (West Virginia): same player, only casing differs
    rows = detect([
        rec("wvu", "25", "Civan", "De Jesus Ferreira", "g1"),
        rec("wvu", "25", "Civan", "De Jesus Ferreira", "g2"),
        rec("wvu", "25", "Civan", "de Jesus Ferreira", "g3"),
    ])
    assert len(rows) == 1
    r = rows[0]
    assert r["tier"] == SAFE
    assert (r["from"].last, r["to"].last) == ("de Jesus Ferreira", "De Jesus Ferreira")


def test_casing_prefers_deliberate_inner_capitals_over_title_case():
    rows = detect([rec("t", "4", "Tate", "McDermott", "g1"), rec("t", "4", "Tate", "Mcdermott", "g2"),
                   rec("t", "4", "Tate", "Mcdermott", "g3")])
    assert rows[0]["to"].last == "McDermott"


def test_whole_name_in_first_field_is_safe():
    rows = detect([rec("adrian", "2", "RYAN KIENTZ", "", "g1"), rec("adrian", "2", "Ryan", "Kientz", "g2")])
    assert rows[0]["tier"] == SAFE and (rows[0]["to"].first, rows[0]["to"].last) == ("Ryan", "Kientz")


def test_exact_nickname_is_safe():
    rows = detect([rec("t", "9", "Gabe", "Smyth", "g1"), rec("t", "9", "Gabriel", "Smyth", "g2")])
    assert rows[0]["tier"] == SAFE and rows[0]["reason"] == "nickname"


def test_christian_and_christopher_are_not_nicknames_of_each_other():
    assert detect([rec("t", "9", "Christian", "Lee", "g1"), rec("t", "9", "Christopher", "Lee", "g2")]) == []


def test_lost_encoding_is_safe_when_exactly_one_clean_spelling_fits():
    rows = detect([rec("t", "7", "H�kon", "Edstrom", "g1"), rec("t", "7", "Hakon", "Edstrom", "g2")])
    assert rows[0]["tier"] == SAFE and rows[0]["reason"] == "encoding"
    assert rows[0]["to"].first == "Hakon"


def test_garbled_name_fitting_two_clean_spellings_goes_to_review():
    rows = detect([rec("t", "6", "B�la", "K", "g1"), rec("t", "6", "Bela", "K", "g2"),
                   rec("t", "6", "Bala", "K", "g3")])
    assert all(r["tier"] == REVIEW for r in rows if "encoding" in r["reason"])
    assert not [r for r in rows if r["tier"] == SAFE and r["reason"] == "encoding"]


def test_nickname_plus_dropped_middle_name_needs_review():
    # Nico Zeballos vs Nicholas Alvaro Zeballos (Davidson)
    rows = detect([rec("davidson", "27", "Nicholas", "Alvaro Zeballos", "g1", "2026-09-04"),
                   rec("davidson", "27", "Nico", "Zeballos", "g2", "2026-09-15")])
    assert len(rows) == 1 and rows[0]["tier"] == REVIEW
    assert rows[0]["reason"] == "name part added/dropped + nickname"


def test_typo_truncation_and_initial_go_to_review():
    typo = detect([rec("t", "3", "Johannes", "Kack", "g1"), rec("t", "3", "Johannes", "Kaeck", "g2")])
    trunc = detect([rec("t", "3", "Toby", "Prabhu", "g1"), rec("t", "3", "To", "Prabhu", "g2")])
    init = detect([rec("t", "3", "Keegan", "Hance", "g1"), rec("t", "3", "K", "Hance", "g2")])
    assert [r["tier"] for r in typo + trunc + init] == [REVIEW] * 3
    assert trunc[0]["reason"] == "truncated" and init[0]["reason"] == "first-name initial"


def test_same_surname_different_first_name_flagged_only_when_sequential():
    # Neil/John Meyers (Davidson): nothing alike but the number, and one takes over from the other
    seq = detect([rec("davidson", "13", "Neil", "Meyers", "g1", "2026-09-01"),
                  rec("davidson", "13", "John", "Meyers", "g2", "2026-09-10")])
    assert len(seq) == 1 and seq[0]["tier"] == REVIEW and seq[0]["score"] < 0.5
    overlap = detect([rec("davidson", "13", "Neil", "Meyers", "g1", "2026-09-01"),
                      rec("davidson", "13", "John", "Meyers", "g2", "2026-09-10"),
                      rec("davidson", "13", "Neil", "Meyers", "g3", "2026-09-20")])
    assert overlap == []


def test_names_in_the_same_game_are_never_matched():
    rows = detect([rec("t", "8", "Johannes", "Kack", "g1"), rec("t", "8", "Johannes", "Kaeck", "g1")])
    assert rows == []


def test_placeholder_and_blank_numbers_are_not_blocking_keys():
    rows = detect([rec("t", "0", "Johannes", "Kack", "g1"), rec("t", "0", "Johannes", "Kaeck", "g2"),
                   rec("t", "", "Gabe", "Smyth", "g3"), rec("t", "", "Gabriel", "Smyth", "g4")])
    assert rows == []


def test_different_teams_and_seasons_are_not_compared():
    rows = detect([rec("a", "5", "Gabe", "Smyth", "g1"), rec("b", "5", "Gabriel", "Smyth", "g2"),
                   rec("a", "6", "Gabe", "Smyth", "g3", season="2025"),
                   rec("a", "6", "Gabriel", "Smyth", "g4", season="2026")])
    assert rows == []


def test_since_limits_to_recently_seen_names():
    recs = [rec("t", "9", "Gabe", "Smyth", "g1", "2025-09-01", "2025"),
            rec("t", "9", "Gabriel", "Smyth", "g2", "2025-09-08", "2025")]
    assert len(detect(recs)) == 1
    assert detect(recs, since="2026-01-01") == []


def test_safe_cluster_chains_to_one_canonical_spelling():
    rows = detect([rec("t", "9", "Gabe", "Smyth", "g1"), rec("t", "9", "GABE", "SMYTH", "g2"),
                   rec("t", "9", "Gabriel", "Smyth", "g3"), rec("t", "9", "Gabriel", "Smyth", "g4"),
                   rec("t", "9", "Gabriel", "Smyth", "g5")])
    assert {(r["from"].first, r["from"].last) for r in rows} == {("Gabe", "Smyth"), ("GABE", "SMYTH")}
    assert {(r["to"].first, r["to"].last) for r in rows} == {("Gabriel", "Smyth")}
