import datetime as dt
import logging
import random
import time

import requests

from . import config

log = logging.getLogger("soccer-tracker.ncaa_client")

_session = requests.Session()
_session.headers.update({"User-Agent": "soccer-tracker/0.1 (local personal use)"})

_MAX_RETRIES = 3
_BASE_DELAY_SECONDS = 1.0
_MAX_DELAY_SECONDS = 20.0


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status == 429 or (status is not None and 500 <= status < 600)
    # Connection errors / timeouts: upstream is unreachable or slow -- also
    # worth a bounded retry, not a hard failure on the first blip.
    return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))


def _get_with_backoff(url: str, timeout: int = 15) -> dict:
    attempt = 0
    while True:
        try:
            resp = _session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except (
            requests.exceptions.HTTPError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            attempt += 1
            if attempt > _MAX_RETRIES or not _is_retryable(exc):
                raise
            delay = min(_BASE_DELAY_SECONDS * (2 ** (attempt - 1)), _MAX_DELAY_SECONDS)
            delay += random.uniform(0, delay * 0.1)
            log.warning("retrying %s after %.1fs (attempt %d)", url, delay, attempt)
            time.sleep(delay)


def get_scoreboard(date: dt.date, sport_path: str = config.DIVISIONS["d1"]) -> dict:
    url = f"{config.NCAA_API_BASE}/scoreboard/{sport_path}/{date:%Y/%m/%d}"
    return _get_with_backoff(url)


def get_boxscore(game_id: str) -> dict:
    url = f"{config.NCAA_API_BASE}/game/{game_id}/boxscore"
    return _get_with_backoff(url)


def get_rankings(sport_path: str = config.DIVISIONS["d1"], poll: str = "") -> dict:
    path = f"{sport_path}/{poll}".rstrip("/")
    url = f"{config.NCAA_API_BASE}/rankings/{path}"
    return _get_with_backoff(url)
