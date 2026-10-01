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
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("remove-orphaned-duplicate-games")


_STATUS_RANK = {"final": 2, "live": 1, "pre": 0}


def _keeper_sort_key(row):
    # Status comes first: a played-and-recorded result always outranks a
    # schedule placeholder, no matter which row the feed happened to touch
    # more recently -- a far-out 'pre' row can get re-confirmed by the
    # 24-hour sync_far_schedule pass well after its matchup already played
    # out and finaled under a different id nearer to kickoff, which made a
    # plain "latest updated_at wins" comparison pick the placeholder over
    # the real result in several cases (e.g. East Mennonite @ Salisbury on
    # 2026-09-04: a 'pre' row touched at the same moment as a final 3-0 row
    # was kept over it purely because its id sorted higher as a string).
    return (_STATUS_RANK.get(row["status"], -1), row["updated_at"] or "", row["id"])


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


def _apply_with_retry(to_delete, attempts=6, base_delay=10):
    # The production app's background sync loop holds a single long write
    # transaction open across a whole run_full_sync pass (several dates x
    # network calls, boxscores, rankings), which can outlast even a bumped
    # busy_timeout -- especially right now, with a live game shortening the
    # sync cadence. Retry instead of giving up on the first collision; each
    # attempt reopens its own connection since the failed one never got to
    # commit (nothing was written -- see db.get_conn, which only commits
    # after the whole `with` body succeeds).
    placeholders = ",".join("?" for _ in to_delete)
    for attempt in range(1, attempts + 1):
        try:
            with db.get_conn() as conn:
                conn.execute("PRAGMA busy_timeout = 30000")
                # A final duplicate can have its own boxscore synced under
                # the old id -- drop those rows too, or they're left
                # orphaned pointing at a game id that no longer exists.
                conn.execute(
                    f"DELETE FROM player_stats WHERE game_id IN ({placeholders})", to_delete
                )
                conn.execute(
                    f"DELETE FROM game_boxscore_raw WHERE game_id IN ({placeholders})", to_delete
                )
                conn.execute(f"DELETE FROM games WHERE id IN ({placeholders})", to_delete)
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == attempts:
                raise
            delay = base_delay * attempt
            log.warning(
                "database locked (attempt %d/%d), retrying in %ds", attempt, attempts, delay
            )
            time.sleep(delay)


def main():
    apply = "--apply" in sys.argv
    with db.get_conn() as conn:
        groups = find_duplicate_groups(conn)
        if not groups:
            log.info("no duplicate games found")
            return

        to_delete = []
        for (date, division, pair), rows in groups.items():
            keeper = max(rows, key=_keeper_sort_key)
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
        _apply_with_retry(to_delete)
        log.info("deleted %d row(s)", len(to_delete))


if __name__ == "__main__":
    main()
