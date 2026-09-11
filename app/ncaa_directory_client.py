"""HTTP client for NCAA's membership directory (web3.ncaa.org) -- a
different host and data shape from the ncaa-api scoreboard/boxscore/
rankings feed in ncaa_client.py, so it gets its own small client."""

import requests

_session = requests.Session()
_session.headers.update({"User-Agent": "soccer-tracker/0.1 (local personal use)"})

_MEMBER_LIST_URL = "https://web3.ncaa.org/directory/api/directory/memberList"
_ORG_DETAIL_URL = "https://web3.ncaa.org/directory/orgDetail"


def get_member_list(sport_code: str = "MSO", division: str = "I", type_: int = 12) -> list[dict]:
    """All member schools for the given sport/division in one response,
    e.g. 213 rows for D1 men's soccer. Each row includes orgId,
    nameOfficial, conferenceName, webSiteUrl, athleticWebUrl,
    memberOrgAddress.state, and privateFlag ("Y"/"N")."""
    resp = _session.get(
        _MEMBER_LIST_URL,
        params={"type": type_, "division": division, "sportCode": sport_code},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_org_detail_html(org_id: int) -> str:
    """Server-rendered HTML for one school's directory page, whose
    "Sponsored Sports" table lists each sport's head coach. Not JSON --
    see app/backfill_coaches.py for the scrape."""
    resp = _session.get(_ORG_DETAIL_URL, params={"id": org_id}, timeout=15)
    resp.raise_for_status()
    return resp.text
