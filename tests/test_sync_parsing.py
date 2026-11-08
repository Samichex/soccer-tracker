import pytest

from app import sync, validate


def _player(**overrides):
    p = {
        "firstName": "John",
        "lastName": "Smith",
        "number": "10",
        "position": "GK",
        "starter": True,
        "minutesPlayed": "90",
        "goals": "0",
        "assists": "0",
        "shots": "0",
        "shotsOnGoal": "0",
        "saves": "0",
        "penalties": {"yellowCards": "0", "redCards": "0"},
        "participated": True,
    }
    p.update(overrides)
    return p


def test_rows_from_boxscore_maps_basic_fields():
    box = {
        "teams": [{"teamId": 1, "seoname": "duke", "isHome": True}],
        "teamBoxscore": [
            {
                "teamId": 1,
                "teamStats": {"goalie": {"saves": None}},
                "playerStats": [_player(position="F", goals="2")],
            }
        ],
    }
    rows = sync._rows_from_boxscore(box)
    assert len(rows) == 1
    row = rows[0]
    assert row["team_id"] == 1
    assert row["team_seo"] == "duke"
    assert row["is_home"] == 1
    assert row["first_name"] == "John"
    assert row["last_name"] == "Smith"
    assert row["goals"] == "2"
    assert row["yellow_cards"] == "0"


def test_rows_from_boxscore_substitutes_team_saves_for_single_goalkeeper():
    # The feed always reports 0 in a keeper's own "saves" field; the real
    # total only exists team-wide and should be attributed to a lone keeper.
    box = {
        "teams": [{"teamId": 1, "seoname": "duke", "isHome": True}],
        "teamBoxscore": [
            {
                "teamId": 1,
                "teamStats": {"goalie": {"saves": 7}},
                "playerStats": [
                    _player(position="GK", saves="0"),
                    _player(lastName="Doe", position="F", saves="0"),
                ],
            }
        ],
    }
    rows = sync._rows_from_boxscore(box)
    gk = next(r for r in rows if r["position"] == "GK")
    field_player = next(r for r in rows if r["position"] == "F")
    assert gk["saves"] == 7
    assert field_player["saves"] == "0"


def test_rows_from_boxscore_does_not_substitute_saves_when_two_keepers_split_time():
    # Team-wide saves can't be attributed to a specific keeper when minutes
    # were split between two -- must be left alone rather than guessed.
    box = {
        "teams": [{"teamId": 1, "seoname": "duke", "isHome": True}],
        "teamBoxscore": [
            {
                "teamId": 1,
                "teamStats": {"goalie": {"saves": 7}},
                "playerStats": [
                    _player(lastName="A", position="GK", saves="0"),
                    _player(lastName="B", position="GK", saves="0"),
                ],
            }
        ],
    }
    rows = sync._rows_from_boxscore(box)
    assert all(r["saves"] == "0" for r in rows)


def test_rows_from_boxscore_unparticipated_goalkeeper_excluded_from_saves_substitution():
    box = {
        "teams": [{"teamId": 1, "seoname": "duke", "isHome": True}],
        "teamBoxscore": [
            {
                "teamId": 1,
                "teamStats": {"goalie": {"saves": 7}},
                "playerStats": [
                    _player(position="GK", saves="0", participated=False),
                ],
            }
        ],
    }
    rows = sync._rows_from_boxscore(box)
    assert rows[0]["saves"] == "0"
    assert rows[0]["participated"] == 0


def test_rows_from_boxscore_missing_team_metadata_yields_no_seo():
    # If a team is in teamBoxscore but absent from the top-level "teams"
    # list, team_seo comes back None -- these rows have nothing to join
    # against teams/games and are effectively orphaned from birth.
    box = {
        "teams": [],
        "teamBoxscore": [
            {"teamId": 99, "teamStats": {}, "playerStats": [_player()]}
        ],
    }
    rows = sync._rows_from_boxscore(box)
    assert rows[0]["team_seo"] is None


@pytest.mark.parametrize("value,expected", [
    ("5", 5),
    (5, 5),
    ("", None),
    (None, None),
    ("NR", None),
    ("abc", None),
])
def test_safe_int(value, expected):
    assert validate.safe_int(value) == expected


def test_parse_rankings_maps_expected_fields():
    # Field names as the live rankings/soccer-men/d1 feed sends them
    # (checked 2026-10-09).
    data = {
        "data": [
            {
                "RANK": "1",
                "SCHOOL": " Duke ",
                "PREVIOUS": "2",
                "POINTS": "100",
                "FIRST-PLACE VOTES": "5",
                "RECORD": "10-1-0",
            }
        ]
    }
    rows = sync._parse_rankings(data)
    assert rows == [
        {
            "school": "Duke",
            "rank": 1,
            "prev_rank": "2",
            "points": "100",
            "first_place_votes": "5",
            "record": "10-1-0",
        }
    ]


@pytest.mark.parametrize("rank_key", ["REGIONAL RANK", "RANK"])
def test_parse_regional_rankings_accepts_either_rank_column(rank_key):
    # The live rankings/soccer-men/d3 feed sends "REGIONAL RANK" (checked
    # 2026-11-02); it used to send "RANK".
    data = {
        "data": [
            {rank_key: "Region I", "SCHOOL": "", "IN-DIVISION RECORD": "", "NPI": ""},
            {rank_key: "1", "SCHOOL": "Williams", "IN-DIVISION RECORD": "11-2-2", "NPI": "61.593"},
            {rank_key: "Region II", "SCHOOL": "", "IN-DIVISION RECORD": "", "NPI": ""},
            {rank_key: "1", "SCHOOL": "Messiah", "IN-DIVISION RECORD": "12-1-1", "NPI": "60.100"},
        ]
    }
    rows = sync._parse_regional_rankings(data)
    assert rows == [
        {"region": 1, "school": "Williams", "rank": 1, "npi": "61.593", "record": "11-2-2"},
        {"region": 2, "school": "Messiah", "rank": 1, "npi": "60.100", "record": "12-1-1"},
    ]


def test_parse_rankings_skips_rows_with_unparseable_rank():
    data = {"data": [{"RANK": "NR", "SCHOOL": "Some School"}, {"RANK": "3", "SCHOOL": "Duke"}]}
    rows = sync._parse_rankings(data)
    assert len(rows) == 1
    assert rows[0]["school"] == "Duke"
