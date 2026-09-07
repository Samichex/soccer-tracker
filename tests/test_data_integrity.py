"""Regression checks against the real, currently-synced data/soccer.db.

These exist because the schema (app/db.py) enforces almost nothing -- no
FOREIGN KEY or CHECK constraints, no UNIQUE beyond primary keys -- so the
DB is only as clean as the upstream NCAA feed and this app's own ingestion
code happen to keep it. Each check here converts a currently-true fact
about the live data into an assertion, so future upstream schema drift or
an ingestion bug shows up as a test failure instead of only being caught
by manual inspection.

A couple of checks carry an explicit, commented allowlist for anomalies
already confirmed as one-off upstream data quirks (not this app's bug) --
that keeps the suite green today while still catching any *new* instance.
"""

NUMERIC_STAT_COLUMNS = [
    "minutes_played", "goals", "assists", "shots", "shots_on_goal",
    "saves", "yellow_cards", "red_cards",
]

# Longest real college match: 90 regulation + two 10-minute overtime
# periods = 110. Padded for stoppage time; a value above this is almost
# certainly a unit error upstream (e.g. seconds instead of minutes).
MAX_PLAUSIBLE_MINUTES = 130

# Confirmed 2026-09-06: the same physical player recorded twice under two
# different jersey numbers within one game. The player_stats PK includes
# `number`, so this isn't rejected at write time (see
# tests/test_db_schema.py::test_player_stats_pk_does_not_catch_same_player_under_two_jersey_numbers).
# Likely an in-game number correction faithfully replicated from the feed.
KNOWN_DUPLICATE_PLAYER_GAMES = {"6616816", "6617211"}

# Confirmed 2026-09-06: game 6617023 (Mount St. Mary's vs. Bard) has no
# `teams` row for the away side ("bard"). Bard isn't an NCAA D1 program,
# so it likely never gets a row from either upstream feed. Documents the
# exact risk flagged in the data-quality review: nothing enforces this
# reference (no FOREIGN KEY on games.away_seo), it only happens to hold
# elsewhere by construction of the sync code.
KNOWN_ORPHAN_TEAM_GAMES = {"6617023"}


def test_no_negative_numeric_stats(live_conn):
    for col in NUMERIC_STAT_COLUMNS:
        row = live_conn.execute(
            f"SELECT COUNT(*) AS n FROM player_stats "
            f"WHERE {col} != '' AND CAST({col} AS INTEGER) < 0"
        ).fetchone()
        assert row["n"] == 0, f"found negative {col} values"


def test_no_implausible_minutes_played(live_conn):
    row = live_conn.execute(
        "SELECT COUNT(*) AS n FROM player_stats "
        "WHERE minutes_played != '' AND CAST(minutes_played AS INTEGER) > ?",
        (MAX_PLAUSIBLE_MINUTES,),
    ).fetchone()
    assert row["n"] == 0


def test_no_unexpected_duplicate_players_within_a_game(live_conn):
    rows = live_conn.execute(
        """
        SELECT game_id, team_seo, first_name, last_name, COUNT(DISTINCT number) AS n
        FROM player_stats
        GROUP BY game_id, team_seo, first_name, last_name
        HAVING n > 1
        """
    ).fetchall()
    unexpected = {r["game_id"] for r in rows} - KNOWN_DUPLICATE_PLAYER_GAMES
    assert not unexpected, f"new duplicate-player-in-game instances: {unexpected}"


def test_no_unexpected_orphaned_team_references(live_conn):
    rows = live_conn.execute(
        """
        SELECT id FROM games g
        WHERE (g.home_seo IS NOT NULL AND g.home_seo != ''
               AND NOT EXISTS (SELECT 1 FROM teams t WHERE t.seo = g.home_seo))
           OR (g.away_seo IS NOT NULL AND g.away_seo != ''
               AND NOT EXISTS (SELECT 1 FROM teams t WHERE t.seo = g.away_seo))
        """
    ).fetchall()
    unexpected = {r["id"] for r in rows} - KNOWN_ORPHAN_TEAM_GAMES
    assert not unexpected, f"new games referencing a missing team: {unexpected}"


def test_no_orphaned_player_stats_team_seo(live_conn):
    row = live_conn.execute(
        """
        SELECT COUNT(*) AS n FROM player_stats ps
        WHERE ps.team_seo IS NOT NULL AND ps.team_seo != ''
          AND NOT EXISTS (SELECT 1 FROM teams t WHERE t.seo = ps.team_seo)
        """
    ).fetchone()
    assert row["n"] == 0


def test_no_orphaned_player_stats_game_id(live_conn):
    row = live_conn.execute(
        """
        SELECT COUNT(*) AS n FROM player_stats ps
        WHERE NOT EXISTS (SELECT 1 FROM games g WHERE g.id = ps.game_id)
        """
    ).fetchone()
    assert row["n"] == 0


def test_games_status_is_known_enum(live_conn):
    rows = live_conn.execute("SELECT DISTINCT status FROM games").fetchall()
    values = {r["status"] for r in rows}
    assert values <= {"pre", "live", "final", None}


def test_player_position_is_known_enum(live_conn):
    rows = live_conn.execute("SELECT DISTINCT position FROM player_stats").fetchall()
    values = {r["position"] for r in rows}
    assert values <= {"GK", "DEF", "MID", "ATK", "", None}


def test_no_team_playing_itself(live_conn):
    row = live_conn.execute(
        """
        SELECT COUNT(*) AS n FROM games
        WHERE home_seo IS NOT NULL AND home_seo != '' AND home_seo = away_seo
        """
    ).fetchone()
    assert row["n"] == 0


def test_no_duplicate_game_for_same_matchup_and_date(live_conn):
    rows = live_conn.execute(
        """
        SELECT date, home_seo, away_seo, COUNT(*) AS n FROM games
        WHERE home_seo IS NOT NULL AND home_seo != ''
        GROUP BY date, home_seo, away_seo
        HAVING n > 1
        """
    ).fetchall()
    assert not rows, f"duplicate game rows for same matchup/date: {[dict(r) for r in rows]}"


def test_each_ranking_snapshot_has_exactly_25_teams_ranked_1_to_25(live_conn):
    rows = live_conn.execute(
        "SELECT observed_date, COUNT(*) AS n, MIN(rank) AS mn, MAX(rank) AS mx "
        "FROM team_rankings GROUP BY observed_date"
    ).fetchall()
    assert rows, "expected at least one rankings snapshot"
    for r in rows:
        assert r["n"] == 25, f"{r['observed_date']} has {r['n']} ranked teams, expected 25"
        assert r["mn"] == 1 and r["mx"] == 25, f"{r['observed_date']} rank range is {r['mn']}-{r['mx']}"


def test_goals_sum_matches_recorded_score_within_own_goal_tolerance(live_conn):
    # The boxscore feed has no "own goal" event, so a team's recorded
    # score can legitimately exceed the sum of its players' credited
    # goals. Currently ~3.6% of final games (17/475) have some mismatch --
    # this asserts that stays a small minority rather than becoming the
    # norm, which would instead indicate a real ingestion bug.
    rows = live_conn.execute(
        """
        SELECT g.id, g.home_score, g.away_score,
            (SELECT COALESCE(SUM(CAST(goals AS INTEGER)), 0) FROM player_stats ps
             WHERE ps.game_id = g.id AND ps.is_home = 1) AS home_goals_sum,
            (SELECT COALESCE(SUM(CAST(goals AS INTEGER)), 0) FROM player_stats ps
             WHERE ps.game_id = g.id AND ps.is_home = 0) AS away_goals_sum
        FROM games g
        WHERE g.status = 'final'
          AND EXISTS (SELECT 1 FROM game_boxscore_raw b WHERE b.game_id = g.id)
        """
    ).fetchall()
    assert rows, "expected at least one final game with a stored boxscore"

    mismatched = 0
    for r in rows:
        for score, goals_sum in ((r["home_score"], r["home_goals_sum"]), (r["away_score"], r["away_goals_sum"])):
            try:
                score_i = int(score)
            except (TypeError, ValueError):
                continue
            if score_i != goals_sum:
                mismatched += 1
                break

    mismatch_rate = mismatched / len(rows)
    assert mismatch_rate <= 0.10, (
        f"{mismatched}/{len(rows)} final games ({mismatch_rate:.1%}) have a "
        "goals-sum/score mismatch -- well above the ~3.6% own-goal baseline, "
        "likely indicates an ingestion bug rather than own goals"
    )
