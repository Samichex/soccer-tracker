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


# --- aliases file: merge / filter / decisions -------------------------------

import json  # noqa: E402

from app.name_dupes import filter_decided, merge_aliases, read_decisions, write_aliases_file  # noqa: E402


def alias(team, f1, l1, f2, l2):
    return {"team": team, "from": [f1, l1], "to": [f2, l2]}


def test_merge_aliases_keeps_existing_flattens_chains_and_drops_loops():
    existing = [alias("t", "A", "x", "B", "x")]
    new = [alias("t", "A", "x", "Z", "x"),   # conflicts with existing: existing wins
           alias("t", "B", "x", "C", "x"),   # chain A -> B -> C
           alias("t", "P", "x", "Q", "x"), alias("t", "Q", "x", "P", "x"),  # loop
           alias("t", "S", "x", "S", "x")]  # self-map
    merged = merge_aliases(existing, new)
    assert merged == [alias("t", "A", "x", "C", "x"), alias("t", "B", "x", "C", "x")]


def test_aliases_file_round_trips_and_is_one_entry_per_line(tmp_path):
    path = tmp_path / "aliases.json"
    data = {"aliases": [alias("t", "Gabe", "Smyth", "Gabriel", "Smyth"), alias("u", "José", "X", "Jose", "X")],
            "not_same": [{"team": "t", "names": [["A", "B"], ["C", "D"]]}]}
    write_aliases_file(data, path)
    assert json.loads(path.read_text(encoding="utf-8")) == data
    assert path.read_text(encoding="utf-8").count("\n") == 9  # 2 aliases + 1 not_same, one per line
    write_aliases_file({"aliases": [], "not_same": []}, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"aliases": [], "not_same": []}


def test_read_decisions_merge_swap_different_and_unknown(tmp_path):
    header = "tier,reason,score,team,season,number,from_first,from_last,from_games,to_first,to_last,to_games,decision\n"
    ok = tmp_path / "ok.csv"
    ok.write_text(header
                  + "review,typo,0.9,t,2026,9,Kack,K,1,Kaeck,K,5,merge\n"
                  + "review,typo,0.9,t,2026,9,Aa,K,1,Bb,K,5,swap\n"
                  + "review,typo,0.9,t,2026,9,Cc,K,1,Dd,K,5,different\n"
                  + "review,typo,0.9,t,2026,9,Ee,K,1,Ff,K,5,\n", encoding="utf-8")
    aliases, not_same = read_decisions(ok)
    assert aliases == [alias("t", "Kack", "K", "Kaeck", "K"), alias("t", "Bb", "K", "Aa", "K")]
    assert not_same == [{"team": "t", "names": [["Cc", "K"], ["Dd", "K"]]}]
    bad = tmp_path / "bad.csv"
    bad.write_text(header + "review,typo,0.9,t,2026,9,A,K,1,B,K,5,yes\n", encoding="utf-8")
    try:
        read_decisions(bad)
    except ValueError as e:
        assert "yes" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_filter_decided_skips_alias_sources_and_not_same_pairs():
    recs = [rec("t", "9", "Johannes", "Kack", "g1"), rec("t", "9", "Johannes", "Kaeck", "g2"),
            rec("t", "3", "Toby", "Prabhu", "g3"), rec("t", "3", "To", "Prabhu", "g4")]
    rows = detect(recs)
    assert len(rows) == 2
    none = {"aliases": [], "not_same": []}
    assert len(filter_decided(rows, none)) == 2
    by_alias = {"aliases": [alias("t", "Johannes", "Kack", "Johannes", "Kaeck")], "not_same": []}
    assert [r["team"] + r["number"] for r in filter_decided(rows, by_alias)] == ["t3"]
    by_not_same = {"aliases": [], "not_same": [{"team": "t", "names": [["To", "Prabhu"], ["Toby", "Prabhu"]]}]}
    assert [r["team"] + r["number"] for r in filter_decided(rows, by_not_same)] == ["t9"]


def test_looks_garbled():
    from app.name_dupes import looks_garbled
    assert not any(looks_garbled(t) for t in ["João", "Müller", "Peña", "irmã", "Ramström"])
    assert all(looks_garbled(t) for t in ["Joã\u0083o", "PeÃ±a", "H�kon", "Ramstrãm"])


def test_mojibake_spelling_never_wins_as_canonical():
    rows = detect([rec("t", "4", "Joao", "T", "g1"), rec("t", "4", "Joã\u0083o", "T", "g2"),
                   rec("t", "4", "Joã\u0083o", "T", "g3"), rec("t", "4", "João", "T", "g4")])
    assert {r["to"].first for r in rows} == {"João"}


def test_truncated_and_initial_point_at_the_longer_spelling_even_with_fewer_games():
    trunc = detect([rec("t", "3", "Henry", "Ibarguen-londono", "g1"), rec("t", "3", "Henry", "Ibarguen-londono", "g2"),
                    rec("t", "3", "Henry", "Ibarguen-Londo", "g3")])
    assert (trunc[0]["from"].last, trunc[0]["to"].last) == ("Ibarguen-Londo", "Ibarguen-londono")
    init = detect([rec("t", "4", "K", "Hance", "g1"), rec("t", "4", "K", "Hance", "g2"), rec("t", "4", "Keegan", "Hance", "g3")])
    assert (init[0]["from"].first, init[0]["to"].first) == ("K", "Keegan")


def test_find_conflicts_flags_names_sent_to_two_final_spellings_but_not_converging_chains():
    from app.name_dupes import find_conflicts
    converge = [alias("t", "R", "O", "Rain", "O"), alias("t", "R", "O", "Rainer", "O"), alias("t", "Rain", "O", "Rainer", "O")]
    assert find_conflicts(converge) == []
    diverge = [alias("t", "K", "Hance", "Kagan", "Hance"), alias("t", "K", "Hance", "Keegan", "Hance")]
    assert find_conflicts(diverge) == ["t: K Hance -> Kagan Hance or Keegan Hance"]
