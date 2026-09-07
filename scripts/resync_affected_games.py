"""One-time re-sync of games whose box score was captured with stray
0-minute bench entries (NCAA feed trims these once finalized). See
app.sync.resync_boxscores.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, sync

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("resync-affected-games")


def main():
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT game_id FROM player_stats WHERE CAST(minutes_played AS REAL) = 0"
        ).fetchall()
        game_ids = [r["game_id"] for r in rows]
        log.info("resyncing %s affected games: %s", len(game_ids), game_ids)
        sync.resync_boxscores(conn, game_ids)


if __name__ == "__main__":
    main()
