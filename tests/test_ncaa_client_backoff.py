import datetime as dt

import pytest
import requests

from app import ncaa_client


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(f"{self.status_code} error")
            error.response = self
            raise error

    def json(self):
        return self._payload


class _FakeSession:
    """Returns queued responses/exceptions in order, one per .get() call."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def get(self, url, timeout=15):
        self.calls += 1
        result = self._results[self.calls - 1]
        if isinstance(result, Exception):
            raise result
        result.raise_for_status()
        return result


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(ncaa_client.time, "sleep", lambda seconds: None)


def test_retries_on_429_then_succeeds(monkeypatch):
    fake = _FakeSession([
        _FakeResponse(429),
        _FakeResponse(429),
        _FakeResponse(200, {"games": []}),
    ])
    monkeypatch.setattr(ncaa_client, "_session", fake)
    result = ncaa_client.get_scoreboard(dt.date(2026, 9, 1))
    assert result == {"games": []}
    assert fake.calls == 3


def test_404_raises_immediately_without_retry(monkeypatch):
    fake = _FakeSession([_FakeResponse(404)])
    monkeypatch.setattr(ncaa_client, "_session", fake)
    with pytest.raises(requests.exceptions.HTTPError):
        ncaa_client.get_boxscore("g1")
    assert fake.calls == 1


def test_persistent_429_raises_after_max_retries(monkeypatch):
    fake = _FakeSession([_FakeResponse(429)] * (ncaa_client._MAX_RETRIES + 1))
    monkeypatch.setattr(ncaa_client, "_session", fake)
    with pytest.raises(requests.exceptions.HTTPError):
        ncaa_client.get_rankings()
    assert fake.calls == ncaa_client._MAX_RETRIES + 1
