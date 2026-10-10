"""Backfill for United Soccer Coaches (D1) poll weeks that predate this
app's own daily snapshots (see sync.sync_rankings: the ncaa-api rankings
endpoint only ever exposes the *current* poll, so historical weeks have to
come from somewhere else). Also the way to restore those weeks if the
production disk is ever recreated (see README).

Source: each season's "United Soccer Coaches" table on Wikipedia's
"<year> NCAA Division I men's soccer rankings" page (itself sourced from
unitedsoccercoaches.org). Points ("TOTAL POINTS") aren't published there,
so `points` is left NULL for every backfilled row -- everything else lines
up with the columns sync.sync_rankings already parses from the live API.

Usage: `python -m app.backfill_rankings [--season YYYY] [--force]`
(default: the most recent season, see backfill.most_recent_season).

To add weeks for a season: add (or extend) its entry in WEEKS_BY_SEASON
with `_Week(date, label, source_url, teams)` entries in chronological
order, in the same (rank, school, record, first_place_votes) shape as the
existing ones. Only weeks the app didn't snapshot itself are needed -- in
2026 that was everything before the live rankings sync began on 2026-09-04.
"""

import argparse
import logging
from dataclasses import dataclass

from . import db
from .backfill import most_recent_season

log = logging.getLogger("soccer-tracker.backfill_rankings")

# Read directly off the rendered page on 2026-09-06.
_WIKIPEDIA_2026 = "https://en.wikipedia.org/wiki/2026_NCAA_Division_I_men%27s_soccer_rankings"


@dataclass
class _Week:
    date: str  # YYYY-MM-DD, the poll's release date
    label: str
    source_url: str
    # (rank, school, record "W-L-T" or None, first_place_votes or None)
    teams: list[tuple[int, str, str | None, int | None]]


WEEKS_BY_SEASON: dict[str, list[_Week]] = {
    "2026": [
        _Week(
            date="2026-08-04",
            label="Preseason",
            source_url=_WIKIPEDIA_2026,
            teams=[
                (1, "Washington", None, 6),
                (2, "NC State", None, None),
                (3, "Furman", None, 1),
                (4, "Saint Louis", None, 1),
                (5, "Georgetown", None, None),
                (6, "Portland", None, None),
                (7, "Maryland", None, None),
                (8, "Princeton", None, None),
                (9, "Stanford", None, None),
                (10, "Bryant", None, None),
                (11, "Virginia", None, None),
                (12, "Akron", None, None),
                (13, "Vermont", None, None),
                (14, "UNC Greensboro", None, None),
                (15, "SMU", None, None),
                (16, "High Point", None, None),
                (17, "Marshall", None, None),
                (18, "San Diego", None, None),
                (19, "Oregon State", None, None),
                (20, "Hofstra", None, None),
                (21, "Grand Canyon", None, None),
                (22, "Indiana", None, None),
                (23, "West Virginia", None, None),
                (24, "UConn", None, None),
                (25, "Kansas City", None, None),
            ],
        ),
        _Week(
            date="2026-08-25",
            label="Week 1",
            source_url=_WIKIPEDIA_2026,
            teams=[
                (1, "Stanford", "2-0-0", 8),
                (2, "San Diego", "2-0-0", None),
                (3, "Portland", "0-0-1", None),
                (4, "Maryland", "1-0-0", None),
                (5, "SMU", "1-0-0", None),
                (6, "Georgetown", "1-0-1", None),
                (7, "Vermont", "2-0-0", None),
                (8, "Saint Louis", "1-0-0", None),
                (9, "High Point", "1-0-0", None),
                (10, "Akron", "1-0-0", None),
                (11, "Washington", "1-1-0", None),
                (12, "Marshall", "1-0-0", None),
                (13, "Hofstra", "2-0-0", None),
                (14, "Indiana", "2-0-0", None),
                (15, "Kansas City", "1-0-0", None),
                (16, "Clemson", "2-0-0", None),
                (17, "UCLA", "1-0-0", None),
                (18, "Charlotte", "1-0-0", None),
                (19, "Duke", "2-0-0", None),
                (20, "NC State", "1-0-1", None),
                (21, "UNC Greensboro", "1-0-1", None),
                (22, "Ohio State", "1-0-0", None),
                (23, "Wisconsin", "2-0-0", None),
                (24, "Michigan State", "2-0-0", None),
                (25, "Oregon State", "1-0-1", None),
            ],
        ),
        _Week(
            date="2026-09-01",
            label="Week 2",
            source_url=_WIKIPEDIA_2026,
            teams=[
                (1, "Stanford", "4-0-0", 6),
                (2, "Maryland", "3-0-0", 1),
                (3, "SMU", "4-0-0", None),
                (4, "Ohio State", "3-0-0", None),
                (5, "Georgetown", "3-0-1", 1),
                (6, "Indiana", "3-0-1", None),
                (7, "San Diego", "2-1-0", None),
                (8, "Michigan State", "4-0-0", None),
                (9, "Akron", "2-0-1", None),
                (10, "Oregon State", "2-0-1", None),
                (11, "Vermont", "2-0-1", None),
                (12, "UCLA", "2-0-1", None),
                (13, "South Carolina", "3-0-0", None),
                (14, "High Point", "2-1-0", None),
                (15, "Marshall", "1-0-1", None),
                (16, "Hofstra", "2-0-1", None),
                (17, "Wisconsin", "3-0-0", None),
                (18, "Clemson", "2-1-0", None),
                (19, "Florida Atlantic", "2-0-2", None),
                (20, "Cornell", "1-0-0", None),
                (21, "UC Santa Barbara", "1-0-1", None),
                (22, "Louisville", "3-0-0", None),
                (23, "Creighton", "2-0-1", None),
                (24, "Princeton", "1-0-1", None),
                (25, "Charlotte", "1-0-2", None),
            ],
        ),
    ],
}


def _rows_for_week(week: _Week, prev_ranks: dict[str, int]) -> list[dict]:
    rows = []
    for rank, school, record, first_place_votes in week.teams:
        prev_rank = str(prev_ranks[school]) if school in prev_ranks else (
            "NR" if prev_ranks else None
        )
        rows.append(
            {
                "school": school,
                "rank": rank,
                "prev_rank": prev_rank,
                "points": None,
                "first_place_votes": first_place_votes,
                "record": record,
            }
        )
    return rows


def backfill_rankings(conn, season: str, force: bool = False) -> int:
    """Write `season`'s hardcoded poll weeks (WEEKS_BY_SEASON) into
    team_rankings, skipping any date that already has D1 rows unless
    `force`. Returns how many weeks were written."""
    weeks = WEEKS_BY_SEASON.get(season)
    if not weeks:
        log.info(
            "no hardcoded poll weeks for %s -- that season's history is only what the "
            "live sync snapshotted (add weeks to WEEKS_BY_SEASON to backfill it)",
            season,
        )
        return 0

    written = 0
    prev_ranks: dict[str, int] = {}  # starts empty each season: preseason has no previous poll
    for week in weeks:
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM team_rankings WHERE observed_date = ? AND division = 'd1'",
            (week.date,),
        ).fetchone()["n"]
        if existing and not force:
            log.info("skipping %s (%s) -- already has %s rows, pass --force to overwrite",
                     week.date, week.label, existing)
            prev_ranks = {school: rank for rank, school, *_ in week.teams}
            continue

        rows = _rows_for_week(week, prev_ranks)
        for r in rows:
            r["seo"] = db.resolve_seo_by_name(conn, r["school"])
        with conn:
            db.replace_rankings_for_date(conn, week.date, rows)
        written += 1

        unresolved = [r["school"] for r in rows if not r["seo"]]
        if unresolved:
            log.warning("could not resolve seo for ranked schools in %s: %s", week.label, unresolved)
        log.info("backfilled %s (%s teams) from %s", week.date, len(rows), week.label)

        prev_ranks = {school: rank for rank, school, *_ in week.teams}
    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--season", default=None,
        help="season (year) whose poll weeks to backfill (default: the most recent season)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite observed_date rows that already exist (e.g. from a live sync)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    with db.get_conn() as conn:
        backfill_rankings(conn, args.season or str(most_recent_season()), force=args.force)
