"""Tiingo client: token from env, retries on 429/5xx with the injected sleep, 404 -> TiingoNotFound,
symbols with a space sent in Tiingo's dashed form. No network — a fake session records the calls."""
import pytest

from webull_api.tiingo import client as mod


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return self.responses.pop(0)


ROWS = [{"date": "2026-09-03T00:00:00.000Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10,
         "adjOpen": 1, "adjHigh": 2, "adjLow": 0.5, "adjClose": 1.5, "adjVolume": 10, "divCash": 0, "splitFactor": 1},
        {"date": "2026-09-04T00:00:00.000Z", "open": 1.5, "high": 2, "low": 1, "close": 1.8, "volume": 12,
         "adjOpen": 1.5, "adjHigh": 2, "adjLow": 1, "adjClose": 1.8, "adjVolume": 12, "divCash": 0, "splitFactor": 1}]


def test_daily_prices_sends_token_header_range_and_resample(monkeypatch):
    monkeypatch.setenv("TIINGO_API_TOKEN", "tok123")
    s = _Session([_Resp(200, ROWS)])
    c = mod.TiingoClient(session=s, sleep=lambda x: None)
    out = c.daily_prices("SPY", start="1996-01-01", end="2026-09-04")
    assert out == ROWS
    call = s.calls[0]
    assert call["url"] == "https://api.tiingo.com/tiingo/daily/SPY/prices"
    assert call["params"] == {"startDate": "1996-01-01", "endDate": "2026-09-04", "resampleFreq": "daily", "format": "json"}
    assert call["headers"]["Authorization"] == "Token tok123"
    assert call["timeout"] == 30


def test_symbol_with_a_space_is_sent_dashed():
    assert mod.api_symbol("BRK B") == "BRK-B"
    assert mod.api_symbol("aapl") == "AAPL"
    s = _Session([_Resp(200, ROWS)])
    mod.TiingoClient(token="t", session=s, sleep=lambda x: None).daily_prices("BRK B", start="2020-01-01")
    assert s.calls[0]["url"].endswith("/daily/BRK-B/prices")
    assert "endDate" not in s.calls[0]["params"]


def test_missing_token_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("TIINGO_API_TOKEN", raising=False)
    with pytest.raises(mod.TiingoError, match="TIINGO_API_TOKEN"):
        mod.TiingoClient(session=_Session([]))


def test_404_is_not_found_and_other_errors_carry_status():
    c = mod.TiingoClient(token="t", session=_Session([_Resp(404, text="nope")]), sleep=lambda x: None)
    with pytest.raises(mod.TiingoNotFound):
        c.meta("ZZZZ")
    c2 = mod.TiingoClient(token="t", session=_Session([_Resp(401, text="bad token")]), sleep=lambda x: None)
    with pytest.raises(mod.TiingoError, match="401"):
        c2.meta("SPY")


def test_retries_on_429_and_5xx_with_backoff_then_succeeds():
    slept = []
    s = _Session([_Resp(429, text="slow down"), _Resp(503, text="down"), _Resp(200, {"ticker": "SPY"})])
    c = mod.TiingoClient(token="t", session=s, sleep=slept.append)
    assert c.meta("SPY") == {"ticker": "SPY"}
    assert slept == [2, 4]
    assert len(s.calls) == 3


def test_gives_up_after_three_retries():
    s = _Session([_Resp(500, text="x")] * 4)
    c = mod.TiingoClient(token="t", session=s, sleep=lambda x: None)
    with pytest.raises(mod.TiingoError, match="500"):
        c.meta("SPY")
    assert len(s.calls) == 4


IEX_CSV = ("date,open,high,low,close,volume\n"
           "2026-09-04T13:30:00.000Z,15.18,15.2,15.1,15.15,1200\n"
           "2026-09-04T13:31:00.000Z,15.15,15.22,15.14,15.2,800\n")


class _TextResp(_Resp):
    def __init__(self, status, text):
        super().__init__(status, None, text)


def test_intraday_prices_hits_iex_with_csv_and_parses_rows():
    s = _Session([_TextResp(200, IEX_CSV)])
    c = mod.TiingoClient("tok", session=s, sleep=lambda x: None)
    rows = c.intraday_prices("vale", start="2026-09-04", end="2026-09-04")
    assert s.calls[0]["url"] == f"{mod.IEX_BASE}/VALE/prices"
    assert s.calls[0]["params"] == {"startDate": "2026-09-04", "endDate": "2026-09-04", "resampleFreq": "1min",
                                    "columns": "open,high,low,close,volume", "format": "csv"}
    assert rows[1] == {"date": "2026-09-04T13:31:00.000Z", "open": "15.15", "high": "15.22", "low": "15.14", "close": "15.2", "volume": "800"}


def test_intraday_prices_retries_429_then_404_is_not_found():
    slept = []
    s = _Session([_TextResp(429, "slow"), _TextResp(200, IEX_CSV)])
    c = mod.TiingoClient("tok", session=s, sleep=slept.append)
    assert len(c.intraday_prices("A", start="2026-09-04", end="2026-09-04")) == 2 and slept == [2]
    with pytest.raises(mod.TiingoNotFound):
        mod.TiingoClient("tok", session=_Session([_TextResp(404, "")]), sleep=lambda x: None).intraday_prices("ZZ", start="2026-09-04", end="2026-09-04")
