import datetime as dt
import json
import re

from markupsafe import Markup

from . import config

_TEAM_STATES_PATH = config.BASE_DIR / "app" / "data" / "team_states.json"
_NON_D1_PATH = config.BASE_DIR / "app" / "data" / "non_d1.json"
_CONFERENCES_PATH = config.BASE_DIR / "app" / "data" / "conferences.json"

_cache: dict | None = None
_non_d1_cache: dict | None = None
_conferences_cache: dict | None = None


def get_team_states() -> dict:
    global _cache
    if _cache is None:
        if _TEAM_STATES_PATH.exists():
            _cache = json.loads(_TEAM_STATES_PATH.read_text())
        else:
            _cache = {}
    return _cache


def _get_non_d1() -> dict:
    global _non_d1_cache
    if _non_d1_cache is None:
        if _NON_D1_PATH.exists():
            _non_d1_cache = json.loads(_NON_D1_PATH.read_text())
        else:
            _non_d1_cache = {"conferences": {}, "schools": {}}
    return _non_d1_cache


def get_conference_tag(conference_seo: str | None) -> str | None:
    """'D2'/'D3' if `conference_seo` is a known non-D1 conference, else None."""
    if not conference_seo:
        return None
    return _get_non_d1()["conferences"].get(conference_seo)


def is_d1(seo: str | None, conference_seo: str | None = None) -> bool:
    """False if the school itself or its conference is a known non-D1
    (D2/D3/NAIA/NCCAA) program; True otherwise (D1 is the default for any
    school/conference not listed in non_d1.json)."""
    data = _get_non_d1()
    tag = data["schools"].get(seo) if seo else None
    if not tag:
        tag = get_conference_tag(conference_seo)
    return tag is None


def get_team_label(name: str | None, seo: str | None, conference_seo: str | None = None) -> str:
    """Team display name, with a '(CA)'/'(CA, D2)'/'(D3)'/'(NAIA)'/'(NCCAA)' suffix
    combining the school's state and, when the school itself or its conference
    isn't NCAA D1, its division tag.

    `name` can be missing when a team has no resolvable SEO slug (some small
    non-D1 opponents show up in the scoreboard feed without one), so nothing
    ever gets written for it into the teams table; fall back to the seo or a
    placeholder rather than erroring.
    """
    if not name:
        name = seo or "Unknown Team"
    data = _get_non_d1()
    tag = data["schools"].get(seo) if seo else None
    if not tag:
        tag = get_conference_tag(conference_seo)
    state = get_team_states().get(seo) if seo else None
    # Some source names already disambiguate with a trailing "(ST)", e.g.
    # "St. Thomas (MN)" for the Minnesota school vs. "St. Thomas (FL)".
    # Strip it so we don't double up when we add our own state suffix.
    existing = re.match(r"^(.*) \(([A-Z]{2})\)$", name)
    if existing and existing.group(2) == state:
        name = existing.group(1)
    parts = [p for p in (state, tag) if p]
    return f"{name} ({', '.join(parts)})" if parts else name


def get_team_label_responsive(
    short_name: str | None, full_name: str | None, seo: str | None, conference_seo: str | None = None
) -> Markup:
    """Team display name that shows the full name on wide screens and the
    short scoreboard name (e.g. "NC State" instead of "North Carolina State
    University") on narrow ones, via CSS toggling `.name-full`/`.name-short`.
    """
    full_label = get_team_label(full_name or short_name, seo, conference_seo)
    short_label = get_team_label(short_name or full_name, seo, conference_seo)
    return Markup('<span class="name-full">{}</span><span class="name-short">{}</span>').format(
        full_label, short_label
    )


def rank_prefix(rank: int | None, prev_rank: str | None = None) -> Markup:
    """Rank badge prefix, e.g. '#3 ' for rank=3, '' if unranked. When
    `prev_rank` is given, leads with a colored move indicator: an arrow for
    a rank gained/lost, or a dot the first week a team is ranked."""
    if not rank:
        return Markup("")
    arrow = rank_arrow(rank, prev_rank)
    return (arrow + Markup(" ") if arrow else Markup("")) + Markup(f"#{rank} ")


def rank_arrow(rank: int | None, prev_rank: str | None) -> Markup:
    """Standalone move indicator (no rank number), for use next to a rank
    that's already rendered elsewhere, e.g. a standings table's Rk column."""
    if not rank or prev_rank in (None, ""):
        return Markup("")
    if prev_rank == "NR":
        return Markup('<span class="rank-move rank-new" title="First ranked this week">●</span>')
    try:
        delta = int(prev_rank) - rank
    except ValueError:
        return Markup("")
    if delta > 0:
        return Markup(f'<span class="rank-move rank-up" title="Up {delta} this week">▲</span>')
    if delta < 0:
        return Markup(f'<span class="rank-move rank-down" title="Down {abs(delta)} this week">▼</span>')
    return Markup("")


def _poll_week_start(observed_date: str) -> str:
    """Tuesday on/before `observed_date` -- United Soccer Coaches publishes
    weekly on Tuesdays, so this is the release date of whichever poll was
    current as of that snapshot."""
    d = dt.date.fromisoformat(observed_date)
    days_since_tuesday = (d.weekday() - 1) % 7  # Mon=0 ... Tue=1 ... Sun=6
    return (d - dt.timedelta(days=days_since_tuesday)).isoformat()


def group_rankings_by_week(rows) -> list[dict]:
    """Collapse daily `team_rankings` snapshot rows into one row per poll week.

    `rows` must be ascending by observed_date (as returned by
    db.get_ranking_history). The rankings feed only ever exposes the
    *current* poll (see sync.sync_rankings), so a plain daily snapshot just
    repeats the same values every day until United Soccer Coaches publishes
    the next one, on Tuesdays. Bucket rows by the Tuesday they fall under
    (Monday still belongs to the *previous* Tuesday's poll, since the new
    one hasn't landed yet) and merge same-week snapshots, preferring the
    latest non-null value for each field -- the feed can take a day or two
    to backfill details like points/record for a just-published poll, and a
    later sync shouldn't be treated as a distinct entry just because it
    fills in a gap the first capture missed.

    Finally, if a week's `prev_rank` never got backfilled by the feed, fall
    back to the previous week's rank -- that's what "previous rank" means
    by construction, and is a safe substitute for an upstream gap.
    """
    weeks: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        key = _poll_week_start(row["observed_date"])
        if key not in weeks:
            order.append(key)
            weeks[key] = dict(row)
        else:
            merged = weeks[key]
            for field in row.keys():
                if row[field] is not None:
                    merged[field] = row[field]
        weeks[key]["week_start"] = key

    result = [weeks[key] for key in order]
    for i, entry in enumerate(result):
        if entry["prev_rank"] in (None, "") and i > 0:
            entry["prev_rank"] = str(result[i - 1]["rank"])
    return result


def conference_display_name(conference_seo: str | None) -> str:
    """Readable conference name from its seo slug, e.g. 'great-midwest' -> 'Great Midwest'."""
    if not conference_seo:
        return ""
    return conference_seo.replace("-", " ").title()


def _get_conference_names() -> dict:
    """Hand-maintained seo -> {"short", "full"} overrides for conferences whose
    slug doesn't title-case into a sensible name (e.g. "caa" -> "Caa")."""
    global _conferences_cache
    if _conferences_cache is None:
        if _CONFERENCES_PATH.exists():
            _conferences_cache = json.loads(_CONFERENCES_PATH.read_text())
        else:
            _conferences_cache = {}
    return _conferences_cache


def conference_short_name(conference_seo: str | None) -> str:
    """Short/acronym form for tables and filters, e.g. 'caa' -> 'CAA'."""
    if not conference_seo:
        return ""
    entry = _get_conference_names().get(conference_seo)
    if entry and entry.get("short"):
        return entry["short"]
    return conference_display_name(conference_seo)


def conference_full_name(conference_seo: str | None) -> str:
    """Official full name for the conference detail page, e.g. 'caa' -> 'Coastal Athletic Association'."""
    if not conference_seo:
        return ""
    entry = _get_conference_names().get(conference_seo)
    if entry and entry.get("full"):
        return entry["full"]
    return conference_display_name(conference_seo)


def title_case_name(value: str | None) -> str:
    """Title-case an ALL-CAPS source name while preserving breaks like apostrophes,
    e.g. "O'BRIEN" -> "O'Brien". Does not special-case Mc/Mac prefixes."""
    if not value:
        return value or ""
    return re.sub(r"[A-Za-z]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), value)


_POSITION_SHORT = {"GK": "G", "DEF": "D", "MID": "M", "ATK": "F"}


def position_short(value: str | None) -> str:
    """Single-letter position abbreviation, e.g. "DEF" -> "D"."""
    if not value:
        return value or ""
    return _POSITION_SHORT.get(value, value[:1])


_TIME_ZONE_ABBREVIATIONS = {"ET", "CT", "MT", "PT", "AT"}


def split_time_tz(value: str | None) -> tuple[str, str]:
    """Split a kickoff time like "7:00 PM ET" into ("7:00 PM", "ET") so the
    timezone can be hidden separately on narrow screens. Returns the value
    unchanged with an empty tz when there's no recognized suffix."""
    if not value:
        return value or "", ""
    parts = value.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].upper() in _TIME_ZONE_ABBREVIATIONS:
        return parts[0], parts[1].upper()
    return value, ""


def period_short(value: str | None) -> str:
    """Compact badge label for a live game's current_period, e.g.
    "1st Half" -> "1ST", "2nd Half" -> "2ND", "Half" -> "HT". current_period
    is passed through verbatim from NCAA's feed, so anything unrecognized
    (an overtime period, etc.) falls back to an uppercased 3-letter
    abbreviation rather than guessing at a format we haven't seen."""
    if not value:
        return "LIVE"
    v = value.strip().lower()
    if v.startswith("1st"):
        return "1ST"
    if v.startswith("2nd"):
        return "2ND"
    if "half" in v:
        return "HT"
    return value.strip()[:3].upper()


def _format_local_time(local: dt.datetime, with_year: bool) -> str:
    # Avoid %-d/%-I (no-leading-zero) strftime codes: they're a Unix/glibc
    # extension and raise on Windows.
    hour_12 = local.hour % 12 or 12
    date_part = local.strftime(f"%b %d, {local.year}" if with_year else "%b %d")
    return f"{date_part}, {hour_12}:{local.minute:02d} {local.strftime('%p')}"


def format_last_synced(value: str | None) -> dict:
    """Relative label + absolute tooltip for the stored UTC ISO `last_synced`
    timestamp, e.g. {"label": "12m ago", "title": "Sep 5, 2026 2:34 PM"}."""
    if not value:
        return {"label": "not yet synced", "title": ""}
    synced_at = dt.datetime.fromisoformat(value).replace(tzinfo=dt.timezone.utc)
    local = synced_at.astimezone()
    title = _format_local_time(local, with_year=True)
    seconds = (dt.datetime.now(dt.timezone.utc) - synced_at).total_seconds()
    if seconds < 60:
        label = "just now"
    elif seconds < 3600:
        label = f"{int(seconds // 60)}m ago"
    elif seconds < 86400:
        label = f"{int(seconds // 3600)}h ago"
    else:
        label = _format_local_time(local, with_year=False)
    return {"label": label, "title": title}
