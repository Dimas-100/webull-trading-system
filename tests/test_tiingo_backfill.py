# tests/test_tiingo_backfill.py
"""Backfill: default set, incremental update from last date + 1, full refetch on split/dividend,
404 skipped and listed, manifest written, --dry-run fetches nothing. Fake client, tmp TIINGO_DIR."""
import json

from webull_api.tiingo import store
from webull_api.tiingo.client import TiingoNotFound
from scripts import tiingo_backfill as bf


class _Client:
    def __init__(self, data, missing=()):
        self.data, self.missing, self.calls = data, set(missing), []

    def daily_prices(self, symbol, *, start, end=None, resample="daily"):
        self.calls.append((symbol, start))
        if symbol in self.missing:
            raise TiingoNotFound(symbol)
        return [r for r in self.data.get(symbol, []) if r["date"][:10] >= start]


def _api(d, split=1, div=0, adj_close=1.5):
    return {"date": f"{d}T00:00:00.000Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1,
            "adjOpen": 1, "adjHigh": 2, "adjLow": 0.5, "adjClose": adj_close, "adjVolume": 1,
            "divCash": div, "splitFactor": split}


def test_default_symbols_dedupe_and_include_the_candidates():
    syms = bf.default_symbols(all_curated=False)
    assert syms[0] == "AAPL" and "SPY" in syms and "AMAT" in syms and len(syms) == len(set(syms))
    assert "BRK B" in syms                                  # toolkit spelling kept
    assert len(bf.default_symbols(all_curated=True)) > len(syms)


def test_plan_starts_at_last_date_and_refresh_refetches_all():
    # I4: start is the stored last date ITSELF (inclusive), not the day after — the caller
    # re-fetches that overlap row to detect a retroactive re-adjustment.
    p = dict(bf.plan(["A", "B"], last_date=lambda s: "2026-09-03" if s == "A" else None, refresh=False, since="1996-01-01"))
    assert p == {"A": "2026-09-03", "B": "1996-01-01"}
    assert dict(bf.plan(["A"], last_date=lambda s: "2026-09-04", refresh=True, since="1996-01-01")) == {"A": "1996-01-01"}


def test_run_fetches_writes_and_lists_404s(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    c = _Client({"A": [_api("2026-09-03"), _api("2026-09-04")]}, missing={"GONE"})
    rc = bf.run(["--symbols", "A,GONE", "--since", "2020-01-01"], client=c, sleep=lambda s: None, today="2026-09-04")
    assert rc == 0
    assert [r["date"] for r in store.read("A")] == ["2026-09-03", "2026-09-04"]
    assert store.read_manifest()["A"]["rows"] == 2
    out = capsys.readouterr().out
    assert "GONE" in out and "not found" in out.lower()
    runs = [json.loads(l) for l in (tmp_path / bf.RUN_LOGS["lab"]).read_text(encoding="utf-8").splitlines()]
    assert runs == [{"date": "2026-09-04", "ts": runs[0]["ts"], "kind": "lab", "planned": 2, "fetched": 1, "skipped": 1, "failed": 0}]


def test_update_appends_unless_a_split_or_dividend_forces_a_full_refetch(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    store.write("A", store.from_api([_api("2026-09-01"), _api("2026-09-02")]))
    c = _Client({"A": [_api("2026-09-01"), _api("2026-09-02"), _api("2026-09-03")]})
    bf.run(["--symbols", "A", "--since", "2026-01-01"], client=c, sleep=lambda s: None, today="2026-09-03")
    # I4: fetched INCLUSIVE of the stored last date (2026-09-02), not the day after.
    assert c.calls == [("A", "2026-09-02")] and len(store.read("A")) == 3
    c2 = _Client({"A": [_api("2026-09-01"), _api("2026-09-02"), _api("2026-09-03"), _api("2026-09-04", div=0.5)]})
    bf.run(["--symbols", "A", "--since", "2026-01-01"], client=c2, sleep=lambda s: None, today="2026-09-04")
    assert c2.calls == [("A", "2026-09-03"), ("A", "2026-01-01")]        # increment, then full refetch
    assert len(store.read("A")) == 4


def test_refresh_replaces_the_store_even_when_the_fetch_window_is_narrower(tmp_path, monkeypatch):
    # I3: --refresh REPLACES the store with what's fetched; it must never merge in old rows that
    # fall outside the (narrower) --since window of the refresh run.
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    store.write("A", store.from_api([_api("2026-09-01"), _api("2026-09-02")]))
    c = _Client({"A": [_api("2026-09-02"), _api("2026-09-03")]})
    bf.run(["--symbols", "A", "--refresh", "--since", "2026-09-02"], client=c, sleep=lambda s: None, today="2026-09-03")
    assert [r["date"] for r in store.read("A")] == ["2026-09-02", "2026-09-03"]     # 09-01 is gone


def test_retroactive_readjustment_on_the_overlap_row_forces_a_full_refetch(tmp_path, monkeypatch):
    # I4: the overlap row (the stored last date, refetched inclusively) comes back with a
    # different adj_close -> Tiingo re-adjusted history retroactively (a late-discovered split/div
    # or a data correction) -> full refetch from --since, replacing the store outright.
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    store.write("A", store.from_api([_api("2026-09-01"), _api("2026-09-02", adj_close=4.75)]))
    c = _Client({"A": [_api("2026-09-02", adj_close=4.00), _api("2026-09-03")]})
    bf.run(["--symbols", "A", "--since", "2026-01-01"], client=c, sleep=lambda s: None, today="2026-09-03")
    assert c.calls == [("A", "2026-09-02"), ("A", "2026-01-01")]
    assert [r["date"] for r in store.read("A")] == ["2026-09-02", "2026-09-03"]     # the client's full series


def test_dry_run_fetches_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    c = _Client({"A": [_api("2026-09-04")]})
    assert bf.run(["--symbols", "A", "--dry-run"], client=c, sleep=lambda s: None) == 0
    assert c.calls == [] and "A" in capsys.readouterr().out


def test_exit_code_and_summary_distinguish_skipped_from_failed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    all_missing = _Client({}, missing={"GONE", "ALSO"})
    assert bf.run(["--symbols", "GONE,ALSO"], client=all_missing, sleep=lambda s: None, today="2026-09-04") == 1
    out = capsys.readouterr().out
    assert "0 fetched" in out and "2 skipped" in out

    class Boom(_Client):
        def daily_prices(self, symbol, **k):
            if symbol == "BAD":
                from webull_api.tiingo.client import TiingoError
                raise TiingoError("500 boom")
            return super().daily_prices(symbol, **k)
    mixed = Boom({"A": [_api("2026-09-04")]}, missing={"GONE"})
    assert bf.run(["--symbols", "A,GONE,BAD"], client=mixed, sleep=lambda s: None, today="2026-09-04") == 0
    out = capsys.readouterr().out
    assert "1 fetched" in out and "1 skipped" in out and "1 failed" in out


def test_a_single_network_error_is_counted_failed_and_the_run_continues(tmp_path, monkeypatch, capsys):
    import requests

    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))

    class Flaky(_Client):
        def daily_prices(self, symbol, **k):
            if symbol == "NET":
                raise requests.ConnectionError("dns drop")
            return super().daily_prices(symbol, **k)
    c = Flaky({"A": [_api("2026-09-04")]})
    rc = bf.run(["--symbols", "A,NET"], client=c, sleep=lambda s: None, today="2026-09-04")
    assert rc == 0
    out = capsys.readouterr().out
    assert "NET: network error - dns drop" in out
    assert "1 fetched" in out and "1 failed" in out


def test_five_consecutive_network_errors_abort_the_run(tmp_path, monkeypatch, capsys):
    import requests

    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))

    class AllFlaky(_Client):
        def daily_prices(self, symbol, *, start, end=None, resample="daily"):
            self.calls.append((symbol, start))
            raise requests.ConnectionError("dns drop")
    c = AllFlaky({})
    rc = bf.run(["--symbols", "S1,S2,S3,S4,S5,S6"], client=c, sleep=lambda s: None, today="2026-09-04")
    assert rc == 1
    out = capsys.readouterr().out
    assert "aborting: 5 consecutive network errors" in out
    assert [sym for sym, _start in c.calls] == ["S1", "S2", "S3", "S4", "S5"]   # stopped after the 5th
    # the abort path WRITES the manifest -- assert the file, not read_manifest()'s {} fallback,
    # which is what an unwritten manifest returns too (the old assertion could not fail).
    written = tmp_path / "_manifest.json"
    assert written.exists() and json.loads(written.read_text(encoding="utf-8")) == {}


def _tickers_csv(tmp_path):
    p = tmp_path / "_tickers.csv"
    p.write_text("ticker,exchange,assetType,priceCurrency,startDate,endDate\n"
                 "A,NYSE,Stock,USD,2000-01-03,2026-09-04\n"
                 "B,NASDAQ,Stock,USD,2000-01-03,2020-01-01\n"
                 "C,NYSE,ETF,USD,2022-05-02,2026-09-04\n", encoding="utf-8")
    return p


def test_tickers_file_and_window_select_symbols_and_broad_since(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    p = _tickers_csv(tmp_path)
    rc = bf.run(["--tickers-file", str(p), "--listed-between", "2021-09-01", "2026-09-08", "--kind", "broad", "--dry-run"],
                client=None, sleep=lambda s: None, today="2026-09-08")
    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["A -> 2021-06-01", "C -> 2021-06-01"]        # B delisted 2020; broad since = 2021-06-01


def _band_api(d, close, volume):
    return {"date": f"{d}T00:00:00.000Z", "open": close, "high": close, "low": close, "close": close,
            "volume": volume, "adjOpen": close, "adjHigh": close, "adjLow": close, "adjClose": close,
            "adjVolume": volume, "divCash": 0, "splitFactor": 1}


def test_in_band_flag_appends_symbols_already_in_the_store(tmp_path, monkeypatch, capsys):
    # spec 2026-09-08 auto universe §2: --in-band ALSO includes every symbol in the store whose
    # latest row passes the wider paper-400 band ($5-$100, ADV>=5M), deduped with the base set.
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    dates = [f"2026-08-{i + 1:02d}" for i in range(21)]
    store.write("INBAND1", store.from_api([_band_api(d, 20.0, 6_000_000) for d in dates]))
    store.write("INBAND2", store.from_api([_band_api(d, 50.0, 6_000_000) for d in dates]))
    store.write("OUTBAND", store.from_api([_band_api(d, 500.0, 6_000_000) for d in dates]))       # price out of band
    store.write("SHORTHIST", store.from_api([_band_api(d, 20.0, 6_000_000) for d in dates[:5]]))  # < 20 rows

    rc = bf.run(["--symbols", "A", "--in-band", "--dry-run"], client=None, sleep=lambda s: None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "in-band pool: 2 symbols" in out
    assert "INBAND1 ->" in out and "INBAND2 ->" in out
    assert "OUTBAND ->" not in out and "SHORTHIST ->" not in out
    assert "A ->" in out                       # the explicit --symbols entry is still present


def test_in_band_flag_dedupes_against_the_base_symbol_set(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    dates = [f"2026-08-{i + 1:02d}" for i in range(21)]
    store.write("A", store.from_api([_band_api(d, 20.0, 6_000_000) for d in dates]))   # in band AND in --symbols
    rc = bf.run(["--symbols", "A", "--in-band", "--dry-run"], client=None, sleep=lambda s: None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "in-band pool: 1 symbols" in out
    assert out.count("A ->") == 1               # not duplicated in the plan


def test_broad_run_logs_to_its_own_file_and_batches_the_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    writes = []
    real = store.write_manifest
    monkeypatch.setattr(store, "write_manifest", lambda m: (writes.append(len(m)), real(m)))
    c = _Client({s: [_api("2026-09-03"), _api("2026-09-04")] for s in "ABCDE"})
    rc = bf.run(["--symbols", "A,B,C,D,E", "--since", "2020-01-01", "--kind", "broad", "--manifest-every", "2", "--spacing", "0"],
                client=c, sleep=lambda s: None, today="2026-09-04")
    assert rc == 0
    assert writes == [2, 4, 5]                                   # every 2 symbols + once at the end
    runs = [json.loads(l) for l in (tmp_path / bf.RUN_LOGS["broad"]).read_text(encoding="utf-8").splitlines()]
    assert runs[0]["kind"] == "broad" and runs[0]["fetched"] == 5
    assert not (tmp_path / bf.RUN_LOGS["lab"]).exists()          # the Lab's 16:45 evidence stays untouched


def test_spacing_is_passed_to_sleep(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    slept = []
    c = _Client({"A": [_api("2026-09-04")]})
    bf.run(["--symbols", "A", "--since", "2020-01-01", "--spacing", "0.4"], client=c, sleep=slept.append, today="2026-09-04")
    assert slept == [0.4]


def test_every_symbol_is_spaced_including_404s_and_errors(tmp_path, monkeypatch):
    # C1: the 404 / error / network-error paths used to `continue` past the sleep, so a run over
    # names Tiingo does not have fired requests back to back straight into the hourly cap.
    import requests

    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))

    class Mixed(_Client):
        def daily_prices(self, symbol, **k):
            from webull_api.tiingo.client import TiingoError
            if symbol == "BAD":
                raise TiingoError("500 boom")
            if symbol == "NET":
                raise requests.ConnectionError("dns drop")
            return super().daily_prices(symbol, **k)
    slept = []
    c = Mixed({"A": [_api("2026-09-04")]}, missing={"GONE"})
    bf.run(["--symbols", "A,GONE,BAD,NET", "--since", "2020-01-01", "--spacing", "0.4"],
           client=c, sleep=slept.append, today="2026-09-04")
    assert slept == [0.4, 0.4, 0.4, 0.4]                     # one per planned symbol, not just the fetch
