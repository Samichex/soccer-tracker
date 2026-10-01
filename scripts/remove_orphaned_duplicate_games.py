"""One-time cleanup for games left behind by an NCAA gameID reissue.

See app.db.delete_superseded_games: when the feed reissues a brand-new
gameID for a matchup close to kickoff, the old id's row can already be
status='final' (score and home/away reversed, since the venue flipped too)
by the time the new id shows up, so the regular sync-time cleanup -- which
only ever touches non-final rows -- never removes it. Once that game's
date ages out of the sync window (DAYS_BACK), nothing revisits it again.

This script finds any date/division where two rows describe the same pair
of teams and removes all but the most recently synced one. Run it once
against production after deploying the delete_superseded_games fix (e.g.
via the Render shell: `python scripts/remove_orphaned_duplicate_games.py`
for a dry run, then add `--apply` to actually delete).
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("remove-orphaned-duplicate-games")


def find_duplicate_groups(conn):
    rows = conn.execute(
        """
        SELECT id, date, division, home_seo, away_seo, home_score, away_score,
               status, updated_at
        FROM games
        ORDER BY date
        """
    ).fetchall()

    groups: dict[tuple, list] = {}
    for r in rows:
        pair = tuple(sorted((r["home_seo"], r["away_seo"])))
        key = (r["date"], r["division"], pair)
        groups.setdefault(key, []).append(r)

    return {key: grp for key, grp in groups.items() if len(grp) > 1}


def main():
    apply = "--apply" in sys.argv
    with db.get_conn() as conn:
        groups = find_duplicate_groups(conn)
        if not groups:
            log.info("no duplicate games found")
            return

        to_delete = []
        for (date, division, pair), rows in groups.items():
            keeper = max(rows, key=lambda r: (r["updated_at"] or "", r["id"]))
            losers = [r for r in rows if r["id"] != keeper["id"]]
            log.info(
                "%s %s %s vs %s: keeping id=%s (status=%s, score=%s-%s, updated=%s)",
                date, division, *pair, keeper["id"], keeper["status"],
                keeper["home_score"], keeper["away_score"], keeper["updated_at"],
            )
            for r in losers:
                log.info(
                    "  -> removing id=%s (status=%s, score=%s-%s, updated=%s)",
                    r["id"], r["status"], r["home_score"], r["away_score"], r["updated_at"],
                )
                to_delete.append(r["id"])

        log.info("%d duplicate row(s) to remove (dry-run=%s)", len(to_delete), not apply)
        if apply and to_delete:
            placeholders = ",".join("?" for _ in to_delete)
            # A final duplicate can have its own boxscore synced under the
            # old id -- drop those rows too, or they're left orphaned
            # pointing at a game id that no longer exists.
            conn.execute(f"DELETE FROM player_stats WHERE game_id IN ({placeholders})", to_delete)
            conn.execute(f"DELETE FROM game_boxscore_raw WHERE game_id IN ({placeholders})", to_delete)
            conn.execute(f"DELETE FROM games WHERE id IN ({placeholders})", to_delete)
            conn.commit()
            log.info("deleted %d row(s)", len(to_delete))


if __name__ == "__main__":
    main()
