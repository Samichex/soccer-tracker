import datetime as dt

import requests

from . import config

_session = requests.Session()
_session.headers.update({"User-Agent": "soccer-tracker/0.1 (local personal use)"})


def get_scoreboard(date: dt.date, sport_path: str = config.DIVISIONS["d1"]) -> dict:
    url = f"{config.NCAA_API_BASE}/scoreboard/{sport_path}/{date:%Y/%m/%d}"
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_boxscore(game_id: str) -> dict:
    url = f"{config.NCAA_API_BASE}/game/{game_id}/boxscore"
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_rankings(sport_path: str = config.DIVISIONS["d1"], poll: str = "") -> dict:
    path = f"{sport_path}/{poll}".rstrip("/")
    url = f"{config.NCAA_API_BASE}/rankings/{path}"
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()
