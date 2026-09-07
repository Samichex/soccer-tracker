import datetime as dt

import requests

from . import config

_session = requests.Session()
_session.headers.update({"User-Agent": "soccer-tracker/0.1 (local personal use)"})


def get_scoreboard(date: dt.date) -> dict:
    url = f"{config.NCAA_API_BASE}/scoreboard/{config.SPORT_PATH}/{date:%Y/%m/%d}"
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_boxscore(game_id: str) -> dict:
    url = f"{config.NCAA_API_BASE}/game/{game_id}/boxscore"
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_rankings(poll: str = "") -> dict:
    path = f"{config.SPORT_PATH}/{poll}".rstrip("/")
    url = f"{config.NCAA_API_BASE}/rankings/{path}"
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()
