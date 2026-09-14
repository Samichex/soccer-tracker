"""One-off backfill of cost/graduation/admissions stats (from the U.S.
Department of Education's College Scorecard *API*, not the bulk CSV) for
every team already matched to a school in
app/backfill_college_scorecard.py's crosswalk.

Reuses that module's bulk-CSV download purely to resolve each local team to
a College Scorecard UNITID (the crosswalk logic is already tested there --
no reason to re-run name matching against the API too, which would also
burn a request per team instead of one batched request per 100 teams). The
live API is then queried for the specific stats fields below, batched by id,
using config.COLLEGE_SCORECARD_API_KEY.

Output: app/data/team_college_stats.json, {seo: {tuition_in_state,
tuition_out_of_state, net_price, grad_rate, admission_rate, student_size}}.
Fields absent from a school's record (e.g. grad_rate for a school too new to
have a 150%-time cohort yet) are omitted rather than written as null.
"""

import argparse
import json
import logging

import requests

from . import backfill_college_scorecard as bcs
from . import config, db

log = logging.getLogger("soccer-tracker.backfill_college_stats")

_TEAM_STATS_PATH = config.BASE_DIR / "app" / "data" / "team_college_stats.json"
_API_URL = "https://api.data.gov/ed/collegescorecard/v1/schools.json"
# api.data.gov's gateway 403s requests carrying requests' default
# "python-requests/x.y" User-Agent as a bot-filtering heuristic.
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_BATCH_SIZE = 100  # API's max per_page

_API_FIELDS = {
    "tuition_in_state": "latest.cost.tuition.in_state",
    "tuition_out_of_state": "latest.cost.tuition.out_of_state",
    "net_price": "latest.cost.avg_net_price.overall",
    "grad_rate": "latest.completion.completion_rate_4yr_150nt",
    "admission_rate": "latest.admissions.admission_rate.overall",
    "student_size": "latest.student.size",
}


def fetch_stats_by_unitid(unitids: list[int]) -> dict[int, dict]:
    """{unitid: {local_field_name: value}} for every id in `unitids`,
    batched _BATCH_SIZE at a time."""
    if not config.COLLEGE_SCORECARD_API_KEY:
        raise SystemExit("COLLEGE_SCORECARD_API_KEY is not set")

    stats: dict[int, dict] = {}
    for i in range(0, len(unitids), _BATCH_SIZE):
        batch = unitids[i:i + _BATCH_SIZE]
        resp = requests.get(
            _API_URL,
            params={
                "api_key": config.COLLEGE_SCORECARD_API_KEY,
                "id": ",".join(str(u) for u in batch),
                "fields": "id," + ",".join(_API_FIELDS.values()),
                "per_page": _BATCH_SIZE,
            },
            headers=_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        for row in resp.json()["results"]:
            stats[row["id"]] = {
                local_name: row[api_name]
                for local_name, api_name in _API_FIELDS.items()
                if row.get(api_name) is not None
            }
    return stats


def backfill_college_stats(conn, force: bool = False) -> None:
    existing: dict[str, dict] = {}
    if _TEAM_STATS_PATH.exists():
        existing = json.loads(_TEAM_STATS_PATH.read_text())

    local_teams = conn.execute(
        "SELECT seo, name_full FROM teams WHERE name_full IS NOT NULL"
    ).fetchall()
    schools = bcs.fetch_all_schools()
    crosswalk = bcs.build_city_crosswalk(local_teams, schools)

    targets = {
        seo: int(schools[idx]["unitid"])
        for seo, idx in crosswalk["matched"].items()
        if (force or seo not in existing) and schools[idx].get("unitid")
    }
    if not targets:
        log.info("nothing to do -- all matched teams already have stats")
        return

    stats_by_unitid = fetch_stats_by_unitid(sorted(set(targets.values())))

    written = 0
    for seo, unitid in targets.items():
        row = stats_by_unitid.get(unitid)
        if not row:
            continue
        existing[seo] = row
        written += 1

    _TEAM_STATS_PATH.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
    log.info(
        "matched %s/%s local teams against %s schools (%s newly written)",
        len(crosswalk["matched"]), len(local_teams), len(schools), written,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite teams that already have stats set",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    db.init_db()
    with db.get_conn() as conn:
        backfill_college_stats(conn, force=args.force)
