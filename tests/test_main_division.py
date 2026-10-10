import pytest
from starlette.requests import Request

from app import config, main


def _request(query: str = "", cookie: str | None = None) -> Request:
    headers = [(b"cookie", cookie.encode())] if cookie else []
    return Request({"type": "http", "query_string": query.encode(), "headers": headers})


@pytest.fixture(autouse=True)
def _both_divisions(monkeypatch):
    monkeypatch.setattr(config, "ENABLED_DIVISIONS", ["d1", "d3"])


def test_resolve_division_honors_query_param_over_cookie():
    # The nav and team labels call this without an explicit division; they
    # must still agree with a ?division=d3 link's data (e.g. the D3 Rank
    # History links), not fall back to the visitor's D1 cookie.
    assert main._resolve_division(_request("division=d3&region=2", "division=d1")) == "d3"


def test_resolve_division_falls_back_to_cookie_then_default():
    assert main._resolve_division(_request(cookie="division=d3")) == "d3"
    assert main._resolve_division(_request("division=bogus")) == "d1"


def test_set_division_drops_division_param_from_next_so_the_switch_sticks():
    response = main.set_division("d1", next="/rank-history?division=d3&region=2")
    assert response.headers["location"] == "/rank-history?region=2"
    assert "division=d1" in response.headers["set-cookie"]


def test_set_division_still_refuses_off_site_next():
    assert main.set_division("d1", next="//evil.com").headers["location"] == "/"
