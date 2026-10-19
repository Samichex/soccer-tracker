"""Find player-name variants that are probably the same person.

The NCAA feed spells the same player differently across games (casing,
nicknames, a middle name drifting between first/last, typos, broken
encoding) and the app identifies a player by exact (team, first, last),
so each variant becomes its own player. This is a read-only detector: it
never writes to the DB. Run it with

    python -m app.name_dupes [--since YYYY-MM-DD] [--out report.csv]
    python -m app.name_dupes --write-aliases          # record the safe tier
    python -m app.name_dupes --import-decisions r.csv # record your review answers

Decisions live in app/data/player_aliases.json. Aliases are applied to new
rows at ingest and to stored rows at startup (normalize.py, db.py); pairs
marked "different" are never reported again.

Candidates are found by blocking on (team, season, jersey number) -- one
player wears one number per team per season, so only names sharing a block
are compared -- and then each pair is classified:

  safe    case / accent / punctuation / encoding / exact nickname. Safe to
          merge without a human look.
  review  typos, spelling variants, word order, a name part gained or lost,
          or the same surname under a different first name. Needs a human.

Two names that appear in the same team's box score for the same game are
never offered as a match: that is two people.

Jersey number 0 and blank are skipped as blocking keys -- upstream uses
them as a placeholder, so they group unrelated players.
"""

import argparse
import csv
import datetime as dt
import itertools
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

import jellyfish

from . import config, normalize

SAFE = "safe"
REVIEW = "review"

_SKIP_NUMBERS = {"", "0"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_PARTICLES = {"de", "da", "di", "del", "van", "von", "der", "den", "la", "le", "du", "dos", "das", "bin", "al", "el"}
_TRANSLIT = str.maketrans({
    "ß": "ss", "ø": "o", "æ": "ae", "œ": "oe", "đ": "d", "ł": "l", "ð": "d", "þ": "th", "ı": "i",
})
_GARBLE = "�"
_WILD = "?"

# formal name -> common short forms. Two first names match when equal, when
# one is a short form of the other, or when both are short forms of the same
# formal name (joe/joey). Christian and Christopher stay distinct on purpose.
_NICKNAMES = {
    "alexander": {"alex", "xander", "lex"},
    "alejandro": {"alex", "alejo"},
    "anthony": {"tony"},
    "andrew": {"andy", "drew"},
    "benjamin": {"ben", "benji", "benny"},
    "christopher": {"chris", "topher"},
    "christian": {"chris"},
    "daniel": {"dan", "danny"},
    "david": {"dave", "davey"},
    "edward": {"ed", "eddie", "ted", "ned"},
    "frederick": {"fred", "freddie"},
    "gabriel": {"gabe", "gabi"},
    "gregory": {"greg"},
    "jacob": {"jake"},
    "james": {"jim", "jimmy", "jamie"},
    "jonathan": {"jon", "jonny", "jonathon"},
    "joseph": {"joe", "joey"},
    "joshua": {"josh"},
    "matthew": {"matt", "matty"},
    "michael": {"mike", "mikey", "mick"},
    "nicholas": {"nick", "nico", "nicky", "nik"},
    "nicolas": {"nick", "nico", "nicky", "nik"},
    "nathaniel": {"nate", "nathan"},
    "patrick": {"pat", "paddy"},
    "peter": {"pete"},
    "philip": {"phil"},
    "phillip": {"phil"},
    "richard": {"rick", "rich", "ricky", "dick"},
    "robert": {"rob", "bob", "bobby", "robbie"},
    "samuel": {"sam", "sammy"},
    "stephen": {"steve"},
    "steven": {"steve"},
    "theodore": {"theo", "ted"},
    "thomas": {"tom", "tommy"},
    "timothy": {"tim", "timmy"},
    "william": {"will", "bill", "billy", "liam", "willie"},
    "zachary": {"zach", "zack", "zak", "zac"},
}


# Double-decoded UTF-8 leaves C1 control characters (U+0080-U+009F) or an
# "Ã"/"Â" lead character followed by a symbol -- and title-casing lowercases
# the lead to "ã". A legitimate "ã" (João, irmã) is never followed by a
# consonant or a symbol.
_MOJIBAKE = re.compile(r"[\x80-\x9f]|[\u00c3\u00c2\u00e3][\u00a0-\u00bf]|\u00e3[bcdfghjklmnpqrstvwxyz]")


def looks_garbled(text: str) -> bool:
    return _GARBLE in text or _MOJIBAKE.search(text) is not None


def _nick_equiv(a: str, b: str) -> bool:
    if a == b:
        return True
    if b in _NICKNAMES.get(a, ()) or a in _NICKNAMES.get(b, ()):
        return True
    return any(a in nicks and b in nicks for nicks in _NICKNAMES.values())


def name_tokens(first: str | None, last: str | None) -> list[str]:
    """Lowercase, accent-free word tokens for a full name. Hyphens split
    words, apostrophes/periods vanish, generational suffixes (Jr, III) drop.
    A U+FFFD from broken upstream encoding survives as a "?" wildcard."""
    s = f"{first or ''} {last or ''}".replace(_GARBLE, _WILD).lower().translate(_TRANSLIT)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"\([^)]*\)?", " ", s)  # "(Captain)" and similar annotations, even cut off as "(Cap"
    s = re.sub(r"['`.]", "", s)
    s = re.sub(r"[-_,/]", " ", s)
    s = re.sub(r"[^a-z? ]", "", s)
    return [t for t in s.split() if t not in _SUFFIXES]


def _loose(text: str) -> str:
    """Collapse common spelling variance: oe/ae/ue -> o/a/u, doubled letters."""
    text = re.sub(r"([oau])e", r"\1", text)
    return re.sub(r"(.)\1", r"\1", text)


def _tok_match(a: str, b: str) -> bool:
    if _nick_equiv(a, b) or _loose(a) == _loose(b):
        return True
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 3 and long_.startswith(short):
        return True
    return len(short) >= 4 and jellyfish.jaro_winkler_similarity(a, b) >= 0.92


@dataclass
class Name:
    """One distinct raw spelling within one (team, season, number) block."""
    first: str
    last: str
    toks: list[str]
    key: str
    kf: str = ""  # folded first-name field alone
    kl: str = ""  # folded last-name field alone
    games: set = field(default_factory=set)
    first_date: str = ""
    last_date: str = ""

    @property
    def text(self) -> str:
        return " ".join(self.toks)


def _prefix_compat(x: str, y: str, minlen: int) -> bool:
    """Equal, or one is a cut-off/initial of the other (the feed truncates
    fields: Toby -> To, Acosta -> Acost)."""
    if not x or not y:
        return False
    short, long_ = sorted((x, y), key=len)
    return x == y or (len(short) >= minlen and long_.startswith(short))


def _garble_match(a: Name, b: Name) -> bool:
    """One spelling has a lost character (U+FFFD) and the other fills it in."""
    if (_WILD in a.key) == (_WILD in b.key):
        return False
    garbled, clean = (a, b) if _WILD in a.key else (b, a)
    pattern = re.escape(garbled.key).replace(re.escape(_WILD), ".{0,2}")
    return re.fullmatch(pattern, clean.key) is not None


def _subset_match(a: Name, b: Name) -> tuple[bool, bool]:
    """(is_match, used_nickname): the shorter name's parts all line up with
    distinct parts of the longer one (a dropped middle or second surname)."""
    small, large = sorted((a.toks, b.toks), key=len)
    if not small or len(small) == len(large):
        return False, False
    free = list(enumerate(large))
    matched_idx = []
    used_nick = False
    for tok in small:
        hit = next((p for p in free if _tok_match(tok, p[1])), None)
        if hit is None:
            return False, False
        free.remove(hit)
        matched_idx.append(hit[0])
        used_nick |= tok != hit[1] and _nick_equiv(tok, hit[1])
    if len(small) < 2 and matched_idx[0] == 0:
        return False, False
    return True, used_nick


def classify(a: Name, b: Name, co_occur: bool) -> tuple[str, str, float] | None:
    """(tier, reason, score) if a and b look like the same person, else None."""
    if a.key and a.key == b.key:
        if (a.kf, a.kl) == (b.kf, b.kl):
            if len(a.toks) != len(b.toks):
                return REVIEW, "spacing", 0.9
            return SAFE, "case/accent/punctuation", 1.0
        if not a.kl or not b.kl:  # whole name stuffed into first_name, last_name blank
            return SAFE, "full name in first-name field", 1.0
        return REVIEW, "name split between first/last differently", 0.95
    if _garble_match(a, b):
        return SAFE, "encoding", 0.98
    if co_occur or not a.toks or not b.toks:
        return None

    if len(a.toks) == len(b.toks) and a.toks[1:] == b.toks[1:] and _nick_equiv(a.toks[0], b.toks[0]):
        return SAFE, "nickname", 0.95
    if sorted(a.toks) == sorted(b.toks):
        return REVIEW, "word order", 0.9
    if _loose(a.key) == _loose(b.key):
        return REVIEW, "spelling variant", 0.9
    is_subset, used_nick = _subset_match(a, b)
    if is_subset:
        return REVIEW, "name part added/dropped" + (" + nickname" if used_nick else ""), 0.9 if used_nick else 0.85
    if _prefix_compat(a.kf, b.kf, 1) and _prefix_compat(a.kl, b.kl, 3):
        if 1 in (len(a.kf), len(b.kf)) and a.kf != b.kf:
            return REVIEW, "first-name initial", 0.9
        return REVIEW, "truncated", 0.95
    jw = jellyfish.jaro_winkler_similarity(a.text, b.text)
    if jw >= 0.93 or (len(a.key) >= 8 and jellyfish.damerau_levenshtein_distance(a.key, b.key) <= 2):
        return REVIEW, "typo", round(jw, 2)
    # Same surname, unrelated first name (Neil vs John Meyers): no string
    # evidence at all, only that one name takes over the number as the other
    # stops appearing. A human has to decide.
    if a.toks[-1] == b.toks[-1] and (a.last_date < b.first_date or b.last_date < a.first_date):
        return REVIEW, "same surname, different first name (sequential)", 0.3
    return None


def load_records(conn: sqlite3.Connection) -> list[dict]:
    """Every (game, team, number, name) appearance, with the game's season."""
    rows = conn.execute(
        """
        SELECT p.team_seo AS team, g.season AS season, g.date AS date, p.game_id AS game_id,
               p.number AS number, p.first_name AS first, p.last_name AS last
        FROM player_stats p JOIN games g ON g.id = p.game_id
        WHERE p.team_seo IS NOT NULL AND g.season IS NOT NULL
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _canonical_sort_key(n: Name, total_games: int):
    """Best spelling sorts first: intact encoding, split into first+last,
    sane casing, accents kept, then whichever is used most."""
    full = f"{n.first} {n.last}"
    # words split on hyphens/apostrophes too, so "Perez-gasiba" and "O'rourke"
    # read as badly cased; generational suffixes are exempt (II, Jr, ...)
    words = [w for w in re.split(r"[\s\-']+", full) if w and w.lower().strip(".,") not in _SUFFIXES]
    case_bad = any(
        (w.isupper() and len(w) > 2) or (w[0].islower() and w.lower() not in _PARTICLES)
        for w in words if w[0].isalpha()
    )
    # Plain Title Case is what ingest produces from an ALL-CAPS name, so a
    # variant with deliberate inner capitals (McDermott) is better attested.
    plain_title = all(w == w.title() or w.lower() in _PARTICLES for w in words)
    last_words = n.last.lower().replace(".", "").split()
    bad_split = not last_words or last_words[-1] in _SUFFIXES  # last name blank, or just "Jr"
    accents = sum(1 for ch in full if ord(ch) > 127 and ch != _GARBLE)
    return (
        looks_garbled(full), bad_split, case_bad, plain_title, len(n.first.split()),
        -accents, -total_games, full,
    )


def detect(records: list[dict], since: str | None = None) -> list[dict]:
    """Candidate rows, each meaning "`a` is probably `b`".

    SAFE rows are alias suggestions (a -> the cluster's best spelling);
    REVIEW rows are pairs, `b` being the better-attested spelling. With
    `since`, only pairs where a spelling was seen on/after that date."""
    blocks: dict[tuple, dict[tuple, Name]] = defaultdict(dict)
    team_games: dict[tuple, set] = defaultdict(set)  # (team, first, last) -> every game
    for r in records:
        first, last = r["first"] or "", r["last"] or ""
        team_games[(r["team"], first, last)].add(r["game_id"])
        number = (r["number"] or "").strip()
        if number in _SKIP_NUMBERS:
            continue
        toks = name_tokens(first, last)
        if not toks:
            continue
        n = blocks[(r["team"], r["season"], number)].setdefault(
            (first, last),
            Name(first, last, toks, "".join(toks), "".join(name_tokens(first, "")), "".join(name_tokens("", last)),
                 set(), r["date"], r["date"]),
        )
        n.games.add(r["game_id"])
        n.first_date = min(n.first_date, r["date"])
        n.last_date = max(n.last_date, r["date"])

    parent: dict[tuple, tuple] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    review: list[dict] = []
    safe_meta: dict[tuple, dict] = {}
    for (team, season, number), names in blocks.items():
        if len(names) < 2:
            continue
        # a garbled spelling that fits several clean ones can't be trusted to
        # pick one, and would otherwise chain them into a single cluster
        ambiguous = {
            id(n) for n in names.values()
            if _WILD in n.key and sum(_garble_match(n, m) for m in names.values() if m is not n) > 1
        }
        for a, b in itertools.combinations(names.values(), 2):
            co = bool(team_games[(team, a.first, a.last)] & team_games[(team, b.first, b.last)])
            verdict = classify(a, b, co)
            if verdict is None:
                continue
            if since and max(a.last_date, b.last_date) < since:
                continue
            tier, reason, score = verdict
            if reason == "encoding" and (id(a) in ambiguous or id(b) in ambiguous):
                tier, reason, score = REVIEW, "encoding (ambiguous)", 0.5
            if tier == SAFE:
                ka, kb = (team, a.first, a.last), (team, b.first, b.last)
                parent[find(ka)] = find(kb)
                for k in (ka, kb):
                    safe_meta.setdefault(k, {"reasons": set(), "seasons": set(), "numbers": set(), "name": names[(k[1], k[2])]})
                    safe_meta[k]["reasons"].add(reason)
                    safe_meta[k]["seasons"].add(season)
                    safe_meta[k]["numbers"].add(number)
            else:
                lo, hi = sorted(
                    (a, b), key=lambda n: _canonical_sort_key(n, len(team_games[(team, n.first, n.last)])), reverse=True
                )
                if reason in ("truncated", "first-name initial"):
                    # The feed cuts names off, never pads them: the longer spelling is the intact one,
                    # however few games it appears in.
                    lo, hi = sorted((a, b), key=lambda n: len(n.kf) + len(n.kl))
                review.append({
                    "tier": REVIEW, "reason": reason, "score": score, "team": team, "season": season,
                    "number": number, "from": lo, "to": hi,
                })

    clusters: dict[tuple, list[tuple]] = defaultdict(list)
    for k in list(parent):
        clusters[find(k)].append(k)
    out = []
    for members in clusters.values():
        def total(k):
            return len(team_games[k])
        best = min(members, key=lambda k: _canonical_sort_key(safe_meta[k]["name"], total(k)))
        for k in members:
            if k == best:
                continue
            m = safe_meta[k]
            out.append({
                "tier": SAFE, "reason": "; ".join(sorted(m["reasons"])), "score": 1.0, "team": k[0],
                "season": ",".join(sorted(m["seasons"])), "number": ",".join(sorted(m["numbers"])),
                "from": m["name"], "to": safe_meta[best]["name"], "from_games": total(k), "to_games": total(best),
            })
    for r in review:
        r["from_games"] = len(team_games[(r["team"], r["from"].first, r["from"].last)])
        r["to_games"] = len(team_games[(r["team"], r["to"].first, r["to"].last)])
        out.append(r)

    out.sort(key=lambda r: (r["tier"] != SAFE, -r["score"], r["team"], r["number"]))
    return out


def filter_decided(rows: list[dict], file_data: dict) -> list[dict]:
    """Drop candidates already settled: either spelling is an alias source
    (a DB copy that predates the cleanup), or the pair was marked "different"."""
    sources = {(a["team"], *a["from"]) for a in file_data["aliases"]}
    different = {
        frozenset((d["team"], n[0], n[1]) for n in d["names"])
        for d in file_data["not_same"]
    }
    out = []
    for r in rows:
        a = (r["team"], r["from"].first, r["from"].last)
        b = (r["team"], r["to"].first, r["to"].last)
        if a in sources or b in sources or frozenset((a, b)) in different:
            continue
        out.append(r)
    return out


def merge_aliases(existing: list[dict], new: list[dict]) -> list[dict]:
    """Union of alias entries. An existing entry for a spelling is never
    overridden; chains (a -> b, b -> c) are flattened to a -> c; entries that
    would map a name to itself or loop are dropped."""
    mapping: dict[tuple, tuple] = {}
    for entry in [*existing, *new]:
        mapping.setdefault((entry["team"], *entry["from"]), (entry["team"], *entry["to"]))
    out = []
    for src, dst in mapping.items():
        seen = {src}
        while dst in mapping and dst not in seen:
            seen.add(dst)
            dst = mapping[dst]
        if dst == src or dst in seen:
            continue
        out.append({"team": src[0], "from": [src[1], src[2]], "to": [dst[1], dst[2]]})
    return sorted(out, key=lambda e: (e["team"], e["from"]))


def find_conflicts(aliases: list[dict]) -> list[str]:
    """Names that the given answers send to more than one final spelling,
    e.g. "K Hance" -> Kagan and also -> Keegan. Following each chain of
    merges to its end must give a single spelling."""
    targets: dict[tuple, set] = defaultdict(set)
    for a in aliases:
        targets[(a["team"], *a["from"])].add((a["team"], *a["to"]))

    def ends(name, seen=()):
        found = set()
        for dst in targets.get(name, ()):
            found |= ends(dst, (*seen, name)) if dst in targets and dst not in seen else {dst}
        return found

    problems = []
    for src in targets:
        final = ends(src)
        if len(final) > 1:
            options = " or ".join(f"{f} {l}" for _, f, l in sorted(final))
            problems.append(f"{src[0]}: {src[1]} {src[2]} -> {options}")
    return sorted(problems)


def to_alias_entries(rows: list[dict]) -> list[dict]:
    return [
        {"team": r["team"], "from": [r["from"].first, r["from"].last], "to": [r["to"].first, r["to"].last]}
        for r in rows
    ]


def write_aliases_file(data: dict, path=None) -> None:
    """One entry per line, so a PR diff shows exactly which aliases changed."""
    path = path or normalize.ALIASES_PATH

    def block(entries):
        if not entries:
            return "[]"
        return "[\n" + ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in entries) + "\n]"

    text = '{\n "aliases": ' + block(data["aliases"]) + ',\n "not_same": ' + block(data["not_same"]) + "\n}\n"
    path.write_text(text, encoding="utf-8")


def read_decisions(csv_path) -> tuple[list[dict], list[dict]]:
    """(aliases, not_same) from a report CSV whose `decision` column was
    filled in: "merge" (from -> to), "swap" (to -> from) or "different"."""
    aliases, not_same = [], []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            decision = (r.get("decision") or "").strip().lower()
            a = [r["from_first"], r["from_last"]]
            b = [r["to_first"], r["to_last"]]
            if decision == "merge":
                aliases.append({"team": r["team"], "from": a, "to": b})
            elif decision == "swap":
                aliases.append({"team": r["team"], "from": b, "to": a})
            elif decision == "different":
                not_same.append({"team": r["team"], "names": [a, b]})
            elif decision:
                raise ValueError(f"unknown decision {decision!r} for {r['team']} {a} / {b} (use merge, swap or different)")
    return aliases, not_same


_CSV_COLUMNS = [
    "tier", "reason", "score", "team", "season", "number",
    "from_first", "from_last", "from_games", "to_first", "to_last", "to_games", "decision",
]


def write_csv(rows: list[dict], path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(_CSV_COLUMNS)
        for r in rows:
            w.writerow([
                r["tier"], r["reason"], r["score"], r["team"], r["season"], r["number"],
                r["from"].first, r["from"].last, r["from_games"],
                r["to"].first, r["to"].last, r["to_games"], "",
            ])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(config.DB_PATH), help="SQLite file (opened read-only)")
    ap.add_argument("--since", help="only report names seen on/after this date (YYYY-MM-DD), for the weekly check")
    ap.add_argument("--out", help="CSV path (default: data/name_dupes-<today>.csv)")
    ap.add_argument("--write-aliases", action="store_true", help="add the safe tier to player_aliases.json")
    ap.add_argument("--import-decisions", metavar="CSV", help="add the merge/swap/different answers from a reviewed report")
    args = ap.parse_args(argv)

    file_data = normalize.load_aliases_file()
    if args.import_decisions:
        aliases, not_same = read_decisions(args.import_decisions)
        conflicts = find_conflicts(aliases)
        if conflicts:
            raise SystemExit(
                "Nothing recorded. These names are sent to more than one spelling; "
                "blank or fix their decision cells:\n  " + "\n  ".join(conflicts)
            )
        file_data["aliases"] = merge_aliases(file_data["aliases"], aliases)
        file_data["not_same"] = [*file_data["not_same"], *(d for d in not_same if d not in file_data["not_same"])]
        write_aliases_file(file_data)
        print(f"{len(aliases)} merges and {len(not_same)} 'different' answers recorded in {normalize.ALIASES_PATH}")
        return

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = filter_decided(detect(load_records(conn), since=args.since), file_data)
    conn.close()

    if args.write_aliases:
        before = len(file_data["aliases"])
        file_data["aliases"] = merge_aliases(file_data["aliases"], to_alias_entries([r for r in rows if r["tier"] == SAFE]))
        write_aliases_file(file_data)
        print(f"aliases: {before} -> {len(file_data['aliases'])} in {normalize.ALIASES_PATH}")
        return

    out = args.out or str(config.BASE_DIR / "data" / f"name_dupes-{dt.date.today().isoformat()}.csv")
    write_csv(rows, out)

    counts: dict[tuple, int] = defaultdict(int)
    for r in rows:
        counts[(r["tier"], r["reason"])] += 1
    for (tier, reason), n in sorted(counts.items(), key=lambda kv: (kv[0][0] != SAFE, -kv[1])):
        print(f"{tier:7} {n:5}  {reason}")
    print(f"\n{len(rows)} candidates written to {out}")


if __name__ == "__main__":
    main()
