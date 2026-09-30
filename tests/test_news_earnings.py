"""get_next_earnings_checked must distinguish 'verified: none scheduled' from 'could not
look' — (None, False) must never be read as no-earnings."""
import requests

from webull_web import news


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_missing_key_is_unchecked(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr(news, "load_dotenv", lambda: None)
    assert news.get_next_earnings_checked("AAPL") == (None, False)


def test_http_error_is_unchecked(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(news, "load_dotenv", lambda: None)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(500))
    assert news.get_next_earnings_checked("AAPL") == (None, False)


def test_exception_is_unchecked(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(news, "load_dotenv", lambda: None)

    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(requests, "get", boom)
    assert news.get_next_earnings_checked("AAPL") == (None, False)


def test_empty_calendar_is_verified_none(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(news, "load_dotenv", lambda: None)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(200, {"earningsCalendar": []}))
    assert news.get_next_earnings_checked("AAPL") == (None, True)


def test_soonest_date_wins(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(news, "load_dotenv", lambda: None)
    cal = {"earningsCalendar": [{"date": "2026-08-06"}, {"date": "2026-07-31"}]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(200, cal))
    assert news.get_next_earnings_checked("AAPL") == ("2026-07-31", True)


def test_compat_wrapper_returns_date_only(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(news, "load_dotenv", lambda: None)
    cal = {"earningsCalendar": [{"date": "2026-07-31"}]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(200, cal))
    assert news.get_next_earnings_date("AAPL") == "2026-07-31"
