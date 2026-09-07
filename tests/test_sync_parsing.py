import pytest

from app import sync
from app import db as db_module


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
    assert sync._safe_int(value) == expected
    assert db_module._safe_int(value) == expected


def test_parse_rankings_maps_expected_fields():
    data = {
        "data": [
            {
                "RANK": "1",
                "SCHOOL": " Duke ",
                "PREV": "2",
                "TOTAL POINTS": "100",
                "1ST VOTES": "5",
                "W-L-T": "10-1-0",
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


def test_parse_rankings_skips_rows_with_unparseable_rank():
    data = {"data": [{"RANK": "NR", "SCHOOL": "Some School"}, {"RANK": "3", "SCHOOL": "Duke"}]}
    rows = sync._parse_rankings(data)
    assert len(rows) == 1
    assert rows[0]["school"] == "Duke"
