"""One-time backfill: canonicalize existing player_stats name casing + positions.

Run once against the existing DB to fix historical rows. Ongoing syncs are
already normalized going forward via app.sync (see app/normalize.py).
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, normalize

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("normalize-backfill")


def main():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT rowid, game_id, team_id, team_seo, first_name, last_name, number, position FROM player_stats"
        ).fetchall()
        log.info("scanning %s player_stats rows", len(rows))

        name_changes = 0
        position_changes = 0
        for r in rows:
            new_first, new_last = normalize.canonical_name(
                conn, r["team_seo"], r["first_name"], r["last_name"]
            )
            new_position = normalize.canonical_position(r["position"])

            if (new_first, new_last) == (r["first_name"], r["last_name"]) and new_position == r["position"]:
                continue

            if (new_first, new_last) != (r["first_name"], r["last_name"]):
                name_changes += 1
            if new_position != r["position"]:
                position_changes += 1

            conn.execute(
                """
                UPDATE player_stats
                SET first_name = ?, last_name = ?, position = ?
                WHERE rowid = ?
                """,
                (new_first, new_last, new_position, r["rowid"]),
            )

        log.info("normalized %s rows for name casing, %s rows for position", name_changes, position_changes)


if __name__ == "__main__":
    main()
