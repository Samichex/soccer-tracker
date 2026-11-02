import datetime as dt
import json
import math
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

from markupsafe import Markup

from . import config

_TEAM_STATES_PATH = config.BASE_DIR / "app" / "data" / "team_states.json"
_TEAM_CITIES_PATH = config.BASE_DIR / "app" / "data" / "team_cities.json"
_TEAM_COLLEGE_STATS_PATH = config.BASE_DIR / "app" / "data" / "team_college_stats.json"
_NON_D1_PATH = config.BASE_DIR / "app" / "data" / "non_d1.json"
_CONFERENCES_PATH = config.BASE_DIR / "app" / "data" / "conferences.json"

_cache: dict | None = None
_cities_cache: dict | None = None
_college_stats_cache: dict | None = None
_college_stat_bounds_cache: dict | None = None
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


def get_team_cities() -> dict:
    """seo -> city, from app/backfill_college_scorecard.py. Coverage is
    partial -- only schools that matched a College Scorecard record have an
    entry, unlike get_team_states (sourced from the NCAA directory, which
    every school appears in)."""
    global _cities_cache
    if _cities_cache is None:
        if _TEAM_CITIES_PATH.exists():
            _cities_cache = json.loads(_TEAM_CITIES_PATH.read_text())
        else:
            _cities_cache = {}
    return _cities_cache


def get_team_college_stats() -> dict:
    """seo -> {tuition_in_state, tuition_out_of_state, net_price, grad_rate,
    admission_rate, student_size}, from app/backfill_college_stats.py.
    Coverage is partial (only schools matched to a College Scorecard
    record), and any individual field can be missing if that school's
    record didn't have it -- same partial-coverage caveat as
    get_team_cities."""
    global _college_stats_cache
    if _college_stats_cache is None:
        if _TEAM_COLLEGE_STATS_PATH.exists():
            _college_stats_cache = json.loads(_TEAM_COLLEGE_STATS_PATH.read_text())
        else:
            _college_stats_cache = {}
    return _college_stats_cache


def get_college_stat_bounds() -> dict:
    """Slider max values for the Teams filters, derived from the actual
    College Scorecard data range and rounded up to a clean number so the
    top of each slider means "no filter" for every team that has the
    field. Percent fields (grad rate, admission rate) aren't included here
    since they're already bounded 0-1 by definition."""
    global _college_stat_bounds_cache
    if _college_stat_bounds_cache is None:
        stats = get_team_college_stats()

        def _max_rounded(field: str, round_to: int) -> int:
            values = [v[field] for v in stats.values() if v.get(field) is not None]
            if not values:
                return round_to
            return math.ceil(max(values) / round_to) * round_to

        _college_stat_bounds_cache = {
            "tuition_in_state_max": _max_rounded("tuition_in_state", 5000),
            "tuition_out_of_state_max": _max_rounded("tuition_out_of_state", 5000),
            "student_size_max": _max_rounded("student_size", 5000),
        }
    return _college_stat_bounds_cache


def format_money(value: float | int | None) -> str:
    """1000-separated whole-dollar display, e.g. 17736 -> '$17,736'."""
    if value is None:
        return ""
    return f"${value:,.0f}"


def format_count(value: float | int | None) -> str:
    """1000-separated whole-number display, e.g. 34177 -> '34,177'."""
    if value is None:
        return ""
    return f"{value:,.0f}"


def format_percent(value: float | None) -> str:
    """Fraction as a whole-percent display, e.g. 0.9678 -> '97%'."""
    if value is None:
        return ""
    return f"{value * 100:.0f}%"


def google_maps_url(name: str, city: str, state: str) -> str:
    """Google Maps search URL for a school by name + city/state -- good
    enough to land on campus without needing a precise street address,
    which nothing in this app's data sources provides."""
    query = f"{name}, {city}, {state}"
    return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"


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


def _non_d1_tag(seo: str | None, conference_seo: str | None) -> str | None:
    """The non_d1.json tag for a school (school-level entry wins over its
    conference's), or None if neither says it isn't D1. Shared by is_d1,
    get_team_division, and get_team_label so they agree on which source
    wins."""
    data = _get_non_d1()
    tag = data["schools"].get(seo) if seo else None
    if not tag:
        tag = get_conference_tag(conference_seo)
    return tag


def is_d1(seo: str | None, conference_seo: str | None = None) -> bool:
    """False if the school itself or its conference is a known non-D1
    (D2/D3/NAIA/NCCAA) program; True otherwise (D1 is the default for any
    school/conference not listed in non_d1.json)."""
    return _non_d1_tag(seo, conference_seo) is None


def get_team_division(
    seo: str | None, conference_seo: str | None = None, stored_division: str | None = None
) -> str:
    """Effective division key ("d1"/"d2"/"d3"/"naia"/"nccaa") for a school,
    for callers that need to filter/group by division rather than just
    render a display suffix (see get_team_label for that).

    Preference order:
    1. `stored_division` -- the authoritative `teams.division` column,
       when the caller has it (set only by the NCAA directory backfill,
       see app/backfill_ncaa_directory.py).
    2. The hand-maintained non_d1.json tag (school, then conference).
    3. "d1" -- the same default every other division check in this module
       uses for anything not proven otherwise.
    """
    if stored_division:
        return stored_division
    tag = _non_d1_tag(seo, conference_seo)
    return tag.lower() if tag else "d1"


def get_team_label(
    name: str | None,
    seo: str | None,
    conference_seo: str | None = None,
    *,
    hideable_suffix: bool = False,
    viewing_division: str | None = None,
    city: str | None = None,
) -> str:
    """Team display name, with a '(CA)'/'(CA, D2)'/'(D3)'/'(NAIA)'/'(NCCAA)' suffix
    combining the school's state and, when the school itself or its conference
    isn't NCAA D1, its division tag.

    `name` can be missing when a team has no resolvable SEO slug (some small
    non-D1 opponents show up in the scoreboard feed without one), so nothing
    ever gets written for it into the teams table; fall back to the seo or a
    placeholder rather than erroring.

    `hideable_suffix` wraps the suffix in a `.team-state` span so a page can
    hide it via CSS (e.g. on narrow screens); leave off when the result is
    used somewhere HTML markup can't go, like an `<option>` label.

    `viewing_division` suppresses the division tag when it matches the
    division of the page being rendered -- e.g. no point labeling every team
    "(D3)" while already on the D3-scoped view of a page; the tag still
    earns its keep flagging a team from a *different* division showing up
    there (a cross-division non-conference opponent).

    `city`, when given (team page header only -- get_team_cities coverage is
    partial, so every other call site leaves this unset), replaces the plain
    state text with a "City, ST" link to a Google Maps search for the school.
    """
    if not name:
        name = seo or "Unknown Team"
    tag = _non_d1_tag(seo, conference_seo)
    if tag and viewing_division and tag.lower() == viewing_division:
        tag = None
    state = get_team_states().get(seo) if seo else None
    # Some source names already disambiguate with a trailing "(ST)", e.g.
    # "St. Thomas (MN)" for the Minnesota school vs. "St. Thomas (FL)".
    # Strip it so we don't double up when we add our own state suffix.
    existing = re.match(r"^(.*) \(([A-Z]{2})\)$", name)
    if existing and existing.group(2) == state:
        name = existing.group(1)
    parts = [p for p in (state, tag) if p]
    if not parts:
        return name
    if city and state:
        location = Markup('<a class="team-city-link" href="{}" target="_blank" rel="noopener noreferrer">{}, {}</a>').format(
            google_maps_url(name, city, state), city, state
        )
        suffix = Markup("(") + Markup(", ").join([location, *parts[1:]]) + Markup(")")
    else:
        suffix = f"({', '.join(parts)})"
    if hideable_suffix:
        return Markup('{} <span class="team-state">{}</span>').format(name, suffix)
    if isinstance(suffix, Markup):
        return Markup("{} {}").format(name, suffix)
    return f"{name} {suffix}"


def get_team_label_responsive(
    short_name: str | None,
    full_name: str | None,
    seo: str | None,
    conference_seo: str | None = None,
    viewing_division: str | None = None,
    city: str | None = None,
) -> Markup:
    """Team display name that shows the full name on wide screens and the
    short scoreboard name (e.g. "NC State" instead of "North Carolina State
    University") on narrow ones, via CSS toggling `.name-full`/`.name-short`.
    The state/division suffix is wrapped in `.team-state` so a page can hide
    it separately (e.g. narrow-screen tables tight on space).

    See get_team_label for `viewing_division` and `city`.
    """
    full_label = get_team_label(
        full_name or short_name, seo, conference_seo, hideable_suffix=True, viewing_division=viewing_division, city=city
    )
    short_label = get_team_label(
        short_name or full_name, seo, conference_seo, hideable_suffix=True, viewing_division=viewing_division, city=city
    )
    return Markup('<span class="name-full">{}</span><span class="name-short">{}</span>').format(
        full_label, short_label
    )


_REGION_NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def region_roman(region: int | None) -> str | None:
    """1 -> "I", 10 -> "X", etc. None for anything outside the ten NCAA D3
    regions (see config.REGIONAL_RANKINGS_DIVISIONS)."""
    if not region or not (1 <= region <= len(_REGION_NUMERALS)):
        return None
    return _REGION_NUMERALS[region - 1]


def rank_prefix(rank: int | None, prev_rank: str | None = None, region: int | None = None) -> Markup:
    """Rank badge prefix, e.g. '#3 ' for rank=3, '' if unranked. When
    `prev_rank` is given, leads with a colored move indicator: an arrow for
    a rank gained/lost, or a dot the first week a team is ranked.

    `region` swaps the '#3' national-poll style for 'I-3' -- NCAA D3's
    regional NPI rankings have no single national rank, only a rank within
    one of ten regions (see config.REGIONAL_RANKINGS_DIVISIONS)."""
    if not rank:
        return Markup("")
    arrow = rank_arrow(rank, prev_rank)
    label = f"{region_roman(region)}-{rank}" if region else f"#{rank}"
    return (arrow + Markup(" ") if arrow else Markup("")) + Markup(f"{label} ")


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


def build_rank_history(rows) -> dict:
    """Pivot flat team_rankings rows (db.get_all_ranking_history output,
    ordered by seo then observed_date) into a per-team weekly series for
    every team ever ranked:

    {"weeks": [week_start, ...] (sorted, every week any team was ranked),
     "teams": [{"seo", "name", "conference", "current_rank",
                "series": [{"week_start", "rank", "prev_rank"}, ...],
                "ranks_by_week": {week_start: {"rank", "prev_rank"}}}, ...]}

    `series`/`ranks_by_week` simply omit weeks a team wasn't ranked --
    no interpolation. `teams` is sorted by current (most recent week's)
    rank ascending, with teams unranked in the most recent week pushed
    below the ranked ones, alphabetically by name.
    """
    by_seo: dict[str, list] = {}
    order: list[str] = []
    for row in rows:
        seo = row["seo"]
        if seo not in by_seo:
            order.append(seo)
            by_seo[seo] = []
        by_seo[seo].append(row)

    all_weeks: set[str] = set()
    teams = []
    for seo in order:
        team_rows = by_seo[seo]
        weekly = group_rankings_by_week(team_rows)
        last = team_rows[-1]
        name = last["team_name_full"] or last["team_name"] or last["school"]
        if not any(w["rank"] for w in weekly):
            continue
        all_weeks.update(w["week_start"] for w in weekly)
        teams.append({
            "seo": seo,
            "name": name,
            "conference": last["team_conference"],
            "series": [
                {"week_start": w["week_start"], "rank": w["rank"], "prev_rank": w["prev_rank"]}
                for w in weekly
            ],
            "ranks_by_week": {
                w["week_start"]: {"rank": w["rank"], "prev_rank": w["prev_rank"]} for w in weekly
            },
        })

    weeks = sorted(all_weeks)
    latest_week = weeks[-1] if weeks else None
    for t in teams:
        current = t["ranks_by_week"].get(latest_week) if latest_week else None
        t["current_rank"] = current["rank"] if current else None

    teams.sort(key=lambda t: (0, t["current_rank"]) if t["current_rank"] is not None else (1, t["name"]))
    return {"weeks": weeks, "teams": teams}


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
    e.g. "O'BRIEN" -> "O'Brien". Does not special-case Mc/Mac prefixes.

    Matches runs of Unicode letters (not just A-Za-z) so accented names like
    "PEÑA" title-case to "Peña" instead of splitting at the Ñ and capitalizing
    the letter after it ("PeñA")."""
    if not value:
        return value or ""
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), value)


def site_domain(url: str | None) -> str:
    """Display form of a stored website URL, stripping the scheme and any
    trailing slash, e.g. 'https://gocrimson.com/' -> 'gocrimson.com'."""
    if not url:
        return ""
    return re.sub(r"^https?://", "", url).rstrip("/")


def site_href(url: str | None) -> str:
    """Absolute href for a stored website URL that may or may not already
    include a scheme, e.g. 'gozips.com' -> 'https://gozips.com'."""
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    return f"https://{url}"


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


_EASTERN = ZoneInfo("America/New_York")

# Round tabs on a phone: six of them share one row, so these stay tiny.
_TOURNAMENT_ROUNDS_SHORT = {1: "Rd 1", 2: "Rd 2", 3: "Rd 3", 4: "QF", 5: "SF", 6: "Final"}

_TOURNAMENT_ROUNDS = {
    1: "First Round",
    2: "Second Round",
    3: "Third Round",
    4: "Quarterfinals",
    5: "Semifinals",
    6: "Championship",
}


def tournament_round_label(bracket_round: int | None) -> str:
    """"NCAA Tournament · Quarterfinals" for a game the feed marked as part
    of the NCAA bracket (round names match the bracket endpoint's, same for
    D1 and D3), or "" for any other game."""
    name = _TOURNAMENT_ROUNDS.get(bracket_round)
    return f"NCAA Tournament · {name}" if name else ""


# Pixel geometry of the bracket page (static/style.css reads these through
# CSS variables set in bracket.html). A game card is two team rows plus a
# footer; PITCH is the vertical distance between consecutive first-round
# cards, and GUTTER the width of the gap between rounds that the connector
# lines cross.
BRACKET_LAYOUT = {"row_h": 26, "foot_h": 18, "pitch": 82, "gutter": 26}


def _place_bracket_games(games: list[dict]) -> float:
    """Give every game a vertical position `y` (in card slots, 0 = top) so
    each game sits halfway between the games that feed it, and return how
    many slots tall the bracket is. Games nothing feeds into (first-round
    games, or a seed's bye) take the next free slot top to bottom; the walk
    starts from the final and visits a game's top feeder before its bottom."""
    by_id = {g["bracket_id"]: g for g in games}
    feeders: dict[int, list[dict]] = {}
    for g in games:
        if g["victor_bracket_id"] in by_id:
            feeders.setdefault(g["victor_bracket_id"], []).append(g)
    for group in feeders.values():
        group.sort(key=lambda f: (f["victor_slot"] != "top", f["bracket_id"]))

    next_leaf = 0
    visited: set[int] = set()  # also stops a malformed feed's loop from recursing forever

    def place(g):
        nonlocal next_leaf
        if g["bracket_id"] in visited:
            return
        visited.add(g["bracket_id"])
        kids = feeders.get(g["bracket_id"], [])
        for k in kids:
            place(k)
        kids = [k for k in kids if "y" in k]
        if kids:
            g["y"] = sum(k["y"] for k in kids) / len(kids)
        else:
            g["y"] = float(next_leaf)
            next_leaf += 1

    # The final(s) first, then anything the feed left disconnected.
    roots = [g for g in games if g["victor_bracket_id"] not in by_id]
    for g in sorted(roots, key=lambda g: (-g["round"], g["bracket_id"])) + games:
        place(g)
    for g in games:
        g["feeders"] = feeders.get(g["bracket_id"], [])
    return max(next_leaf, 1)


def _bracket_links(g: dict) -> dict | None:
    """The connector lines drawn in the gutter left of game `g`: one elbow per
    game that feeds it, from the team that won that game (or the middle of the
    card while it's undecided) to the row of `g` the winner moves into.
    Returns an <svg> box (`top` relative to g's card) and its paths, or None
    for a first-round game."""
    lay = BRACKET_LAYOUT
    row_h, gutter, mid = lay["row_h"], lay["gutter"], lay["gutter"] / 2
    elbows = []
    for f in g["feeders"]:
        dy = (f["y"] - g["y"]) * lay["pitch"]
        winner_side = next((i for i, s in enumerate(f["sides"]) if s["winner"]), None)
        start = dy + (row_h * (winner_side + 0.5) if winner_side is not None else row_h)
        end = row_h * (1.5 if f["victor_slot"] == "bottom" else 0.5)
        elbows.append((start, end, winner_side is not None))
    if not elbows:
        return None
    top = min(min(s, e) for s, e, _ in elbows) - 2
    bottom = max(max(s, e) for s, e, _ in elbows) + 2
    paths = [
        {"d": f"M0 {s - top:.1f} H{mid:g} V{e - top:.1f} H{gutter}", "decided": decided}
        for s, e, decided in sorted(elbows, key=lambda x: x[2])  # decided last, so drawn on top
    ]
    return {"top": round(top, 1), "height": round(bottom - top, 1), "paths": paths}


def build_bracket_rounds(games) -> list[dict]:
    """Group db.get_bracket_games rows into [{"number", "name", "games",
    "height"}], one per round that has games, in round order, ready to draw
    as a tree: each game carries `y` (see _place_bracket_games), `sides` (top,
    then bottom), `links` (_bracket_links), a short `date_label`, `kickoff`
    ("6:00 PM ET" -- the viewer's own timezone replaces it client-side, see
    static/localize-time.js) and a `note` ("FINAL (2OT)", "LIVE"). A team
    still to be decided has an empty name; a score is hidden until the game
    starts."""
    rows = []
    for row in games:
        g = dict(row)
        started = g["status"] in ("live", "final")
        g["sides"] = [
            {
                "seo": g[f"{side}_seo"],
                "name": g[f"{side}_name"],
                "seed": g[f"{side}_seed"],
                "score": g[f"{side}_score"] if started and g[f"{side}_name"] else None,
                "winner": bool(g[f"{side}_winner"]),
                "known": bool(g[f"{side}_known"]),
            }
            for side in ("top", "bottom")
        ]
        epoch = g["start_epoch"]
        if epoch:
            local = dt.datetime.fromtimestamp(epoch, tz=_EASTERN)
            g["kickoff"] = f"{local.hour % 12 or 12}:{local.minute:02d} {local.strftime('%p')} ET"
        else:
            g["kickoff"] = ""
        try:
            day = dt.date.fromisoformat(g["start_date"])
            g["date_label"] = f"{day:%b} {day.day}"
        except (TypeError, ValueError):
            g["date_label"] = ""
        if g["status"] == "live":
            g["note"] = "LIVE"
        elif g["status"] == "final":
            # A plain "FINAL" is implied by the score; keep only what adds
            # something, e.g. "FINAL (2OT)" or "FINAL/PK".
            g["note"] = "" if (g["final_message"] or "FINAL") == "FINAL" else g["final_message"]
        else:
            g["note"] = ""
        rows.append(g)

    slots = _place_bracket_games(rows)
    by_id = {g["bracket_id"]: g for g in rows}
    for g in rows:
        g["links"] = _bracket_links(g)
        g["advances"] = _bracket_advances(g, by_id.get(g["victor_bracket_id"]))
    lay = BRACKET_LAYOUT
    height = (slots - 1) * lay["pitch"] + 2 * lay["row_h"] + lay["foot_h"]

    rounds: dict[int, list[dict]] = {}
    for g in rows:
        g["top_px"] = round(g["y"] * lay["pitch"], 1)
        rounds.setdefault(g["round"], []).append(g)
    return [
        {
            "number": n,
            "name": _TOURNAMENT_ROUNDS.get(n, f"Round {n}"),
            "short": _TOURNAMENT_ROUNDS_SHORT.get(n, f"R{n}"),
            # Top to bottom as drawn in the tree, which is also the order the
            # phone's one-round-at-a-time list reads in.
            "games": sorted(rounds[n], key=lambda g: g["y"]),
            "height": height,
        }
        for n in sorted(rounds)
    ]


def _bracket_advances(g: dict, nxt: dict | None) -> str:
    """The phone list's stand-in for the tree's connector line: where game
    `g`'s winner goes next and who they meet there, e.g. "Winner plays No. 1
    Vermont in the Second Round". "" for the final."""
    if nxt is None:
        return ""
    round_name = _TOURNAMENT_ROUNDS.get(nxt["round"], f"Round {nxt['round']}")
    opponent = nxt["sides"][0 if g["victor_slot"] == "bottom" else 1]
    if g["victor_slot"] not in ("top", "bottom") or not opponent["name"]:
        meets = ""
    else:
        seed = f"No. {opponent['seed']} " if opponent["seed"] else ""
        meets = f"{seed}{opponent['name']}"
    if g["status"] == "final":
        winner = next((s["name"] for s in g["sides"] if s["winner"]), None)
        if winner:
            return f"{winner} advanced to the {round_name}" + (f" vs {meets}" if meets else "")
    return f"Winner plays {meets} in the {round_name}" if meets else f"Winner advances to the {round_name}"


def current_bracket_round(rounds: list[dict]) -> int | None:
    """The round the bracket page opens on: the earliest one that still has a
    game to play, else (the tournament is over) the last round."""
    for r in rounds:
        if any(g["status"] != "final" for g in r["games"]):
            return r["number"]
    return rounds[-1]["number"] if rounds else None


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
