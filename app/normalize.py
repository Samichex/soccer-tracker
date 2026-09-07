"""Canonicalization for player names/positions.

The NCAA boxscore feed is inconsistent across games: some return
ALL-CAPS names with abbreviated positions (BEDOLLA / F), others return
Title Case names with spelled-out positions (Bedolla / FORWARD). Left
alone, this splits a single player into multiple rows in any query that
groups by name. These helpers push everything to one canonical shape.
"""

_POSITION_MAP = {
    "": "",
    "D": "DEF",
    "DEF": "DEF",
    "DEFENDER": "DEF",
    "LEFTWINGBACK": "DEF",
    "RIGHTWINGBACK": "DEF",
    "M": "MID",
    "MID": "MID",
    "MIDFIELDER": "MID",
    "CENTERATTACKINGMIDFIELD": "MID",
    "CENTERDEFENSIVEMIDFIELD": "MID",
    "RIGHTMIDFIELD": "MID",
    "F": "ATK",
    "FWD": "ATK",
    "FORWARD": "ATK",
    "RIGHTFORWARD": "ATK",
    "RIGHTWINGER": "ATK",
    "STRIKER": "ATK",
    "GK": "GK",
    "GOALKEEPER": "GK",
}


def canonical_position(raw: str | None) -> str:
    """Map any observed position spelling to GK / DEF / MID / ATK."""
    if not raw:
        return ""
    key = raw.strip().upper()
    if key in _POSITION_MAP:
        return _POSITION_MAP[key]
    # fallback heuristic for any future/unseen spelling
    if "GK" in key or "KEEP" in key:
        return "GK"
    if "MID" in key:
        return "MID"
    if "DEF" in key or "BACK" in key:
        return "DEF"
    if "FOR" in key or "WING" in key or "STRIK" in key or "ATK" in key or "ATTACK" in key:
        return "ATK"
    return ""


def titlecase_name(raw: str) -> str:
    """Best-effort Title Case for a name with no properly-cased reference."""
    return raw.strip().title() if raw else raw


def canonical_name(conn, team_seo: str, first_name: str, last_name: str) -> tuple[str, str]:
    """Return the best-known casing for a player.

    If the raw name looks ALL-CAPS, prefer a properly-cased spelling
    already stored for the same player (team_seo + case-insensitive
    name match). Falls back to Title-casing when no reference exists.
    Names that aren't ALL-CAPS are trusted as-is.
    """
    if not first_name or not last_name or not last_name.isupper():
        return first_name, last_name

    existing = conn.execute(
        """
        SELECT first_name, last_name FROM player_stats
        WHERE team_seo = ? AND UPPER(first_name) = ? AND UPPER(last_name) = ?
        AND last_name != UPPER(last_name)
        LIMIT 1
        """,
        (team_seo, first_name.upper(), last_name.upper()),
    ).fetchone()
    if existing:
        return existing["first_name"], existing["last_name"]
    return titlecase_name(first_name), titlecase_name(last_name)


def normalize_rows(conn, team_seo: str, rows: list[dict]) -> None:
    """Normalize name casing + position on a batch of row dicts, in place."""
    for r in rows:
        r["first_name"], r["last_name"] = canonical_name(
            conn, team_seo, r["first_name"], r["last_name"]
        )
        r["position"] = canonical_position(r["position"])
