"""Supported-tickers list: zip parse, US stock/ETF filter, window overlap incl. delisted names,
CSV round-trip under a tmp TIINGO_DIR. No network — a fake session returns zip bytes."""
import io
import json
import zipfile

from webull_api.tiingo import tickers

CSV = ("ticker,exchange,assetType,priceCurrency,startDate,endDate\n"
       "A,NYSE,Stock,USD,1999-11-18,2026-09-04\n"
       "OLD,NASDAQ,Stock,USD,2010-01-04,2023-03-01\n"
       "GONE,NYSE,Stock,USD,2005-01-03,2019-12-31\n"
       "FUT,NASDAQ,Stock,USD,2026-10-01,\n"
       "VTSAX,NMFQS,Mutual Fund,USD,2000-11-13,2026-09-04\n"
       "SPY,NYSE ARCA,ETF,USD,1993-01-29,2026-09-04\n"
       "TSCO,LSE,Stock,GBP,2001-01-01,2026-09-04\n"
       "NOSTART,NYSE,Stock,USD,,2026-09-04\n")


def _zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("supported_tickers.csv", text)
    return buf.getvalue()


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class _Session:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def get(self, url, *, timeout=None, **kw):
        self.calls.append(url)
        return self.resp


def test_parse_zip_reads_the_single_csv():
    rows = tickers.parse_zip(_zip(CSV))
    assert [r["ticker"] for r in rows][:3] == ["A", "OLD", "GONE"]
    assert rows[3]["endDate"] == ""          # blank stays blank, never None


def test_fetch_uses_the_zip_url_and_raises_on_non_200():
    s = _Session(_Resp(200, _zip(CSV)))
    assert len(tickers.fetch(s)) == 8 and s.calls == [tickers.ZIP_URL]
    import pytest
    with pytest.raises(RuntimeError):
        tickers.fetch(_Session(_Resp(503)))


def test_us_equities_filter_table():
    rows = tickers.us_equities(tickers.parse_zip(_zip(CSV)))
    assert [r["ticker"] for r in rows] == ["A", "OLD", "GONE", "FUT", "SPY"]   # fund, GBP, blank start dropped


def test_listed_between_keeps_delisted_inside_the_window_and_drops_outside():
    rows = tickers.us_equities(tickers.parse_zip(_zip(CSV)))
    got = tickers.listed_between(rows, "2021-09-01", "2026-09-08")
    assert got == ["A", "OLD", "SPY"]          # GONE ended 2019, FUT starts after the window


def test_write_read_round_trip_and_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    rows = tickers.us_equities(tickers.parse_zip(_zip(CSV)))
    p = tickers.write(rows)
    assert p == tmp_path / "_tickers.csv"
    assert [r["ticker"] for r in tickers.read()] == ["A", "OLD", "GONE", "FUT", "SPY"]
    meta = json.loads((tmp_path / "_tickers.json").read_text(encoding="utf-8"))
    assert meta["rows"] == 5 and meta["fetched_at"].endswith("Z")


def test_refresh_writes_the_filtered_list(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    out = tickers.refresh(_Session(_Resp(200, _zip(CSV))))
    assert len(out) == 5 and (tmp_path / "_tickers.csv").exists()
