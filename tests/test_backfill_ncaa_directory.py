from app.backfill_ncaa_directory import build_crosswalk, normalize_name
from app.backfill_coaches import extract_head_coach


def test_normalize_name_strips_generic_words():
    assert normalize_name("University of Akron") == "akron"
    assert normalize_name("Boston College") == "boston"
    assert normalize_name("Boston University") == "boston"


def _team(seo, name_full):
    return {"seo": seo, "name_full": name_full}


def _entry(org_id, name_official):
    return {"orgId": org_id, "nameOfficial": name_official}


def test_build_crosswalk_exact_match():
    local_teams = [_team("akron", "University of Akron")]
    directory_entries = [_entry(5, "University of Akron")]

    result = build_crosswalk(local_teams, directory_entries)

    assert result["matched"] == {"akron": 5}
    assert result["ambiguous"] == []
    assert result["unmatched_local"] == []
    assert result["unmatched_directory"] == []


def test_build_crosswalk_normalized_match_when_unambiguous():
    # Trailing whitespace defeats the exact-match pass, forcing the
    # normalized fallback to kick in.
    local_teams = [_team("akron", "University of Akron ")]
    directory_entries = [_entry(5, "University of Akron")]

    result = build_crosswalk(local_teams, directory_entries)

    assert result["matched"] == {"akron": 5}
    assert result["ambiguous"] == []


def test_build_crosswalk_flags_collision_as_ambiguous_not_guessed():
    local_teams = [
        _team("boston-college", "Boston College "),
        _team("boston-u", "Boston University "),
    ]
    directory_entries = [
        _entry(67, "Boston College"),
        _entry(68, "Boston University"),
    ]

    result = build_crosswalk(local_teams, directory_entries)

    assert result["matched"] == {}
    ambiguous_seos = {seo for seo, _ in result["ambiguous"]}
    assert ambiguous_seos == {"boston-college", "boston-u"}


def test_build_crosswalk_reports_unmatched_on_both_sides():
    local_teams = [_team("adrian", "Adrian College")]
    directory_entries = [_entry(5, "University of Akron")]

    result = build_crosswalk(local_teams, directory_entries)

    assert result["matched"] == {}
    assert result["unmatched_local"] == ["adrian"]
    assert result["unmatched_directory"] == [5]


def test_extract_head_coach_handles_apostrophe():
    page_html = """
        <tr>
            <td>Men&#39;s Soccer</td>
            <td>
                <span>Francesco  D&#39;Agostino</span>

                <span></span>

            </td>
            <td>I</td>
        </tr>
    """
    assert extract_head_coach(page_html) == "Francesco D'Agostino"


def test_extract_head_coach_returns_none_when_row_missing():
    assert extract_head_coach("<table></table>") is None
