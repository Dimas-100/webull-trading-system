"""One CSV per symbol under TIINGO_DIR; adjusted and raw columns kept; deduped ascending; the
update policy refetches in full when a split or dividend lands in the new rows."""
import json

from webull_api.tiingo import store

API = [{"date": "2026-09-04T00:00:00.000Z", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100,
        "adjOpen": 5, "adjHigh": 5.5, "adjLow": 4.5, "adjClose": 5.25, "adjVolume": 200, "divCash": 0, "splitFactor": 1},
       {"date": "2026-09-03T00:00:00.000Z", "open": 9, "high": 10, "low": 8, "close": 9.5, "volume": 90,
        "adjOpen": 4.5, "adjHigh": 5, "adjLow": 4, "adjClose": 4.75, "adjVolume": 180, "divCash": 0, "splitFactor": 1}]


def test_round_trip_sorted_deduped_and_typed(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    rows = store.from_api(API)
    assert [r["date"] for r in rows] == ["2026-09-04", "2026-09-03"]      # from_api keeps API order
    n = store.write("BRK B", rows + [rows[0]])                            # duplicate 09-04 collapses
    assert n == 2
    assert store.path("BRK B").name == "BRK_B.csv" and store.path("BRK B").exists()
    back = store.read("BRK B")
    assert [r["date"] for r in back] == ["2026-09-03", "2026-09-04"]      # ascending on disk
    assert back[1]["adj_close"] == 5.25 and back[1]["close"] == 10.5 and back[1]["adj_volume"] == 200.0
    assert isinstance(back[0]["split_factor"], float)
    assert store.last_date("BRK B") == "2026-09-04"
    assert store.read("NOPE") == [] and store.last_date("NOPE") is None
    header = store.path("BRK B").read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(store.COLUMNS)


def test_write_is_atomic_and_replaces(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    store.write("SPY", store.from_api(API))
    store.write("SPY", store.from_api(API[:1]))
    assert len(store.read("SPY")) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_needs_full_refetch_on_split_or_dividend():
    clean = store.from_api(API)
    assert store.needs_full_refetch(clean) is False
    div = store.from_api([{**API[0], "divCash": 0.42}])
    split = store.from_api([{**API[0], "splitFactor": 4}])
    assert store.needs_full_refetch(div) is True and store.needs_full_refetch(split) is True
    assert store.needs_full_refetch([]) is False


def test_symbols_lists_csvs_sorted_and_skips_underscore_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    store.write("ZZZ", store.from_api(API))
    store.write("AAA", store.from_api(API))
    store.write("BRK B", store.from_api(API))
    store.write_manifest({"AAA": {}})                    # writes _manifest.json alongside
    (tmp_path / "_tickers.csv").write_text("ticker\n", encoding="utf-8")   # leading-underscore CSV
    assert store.symbols() == ["AAA", "BRK_B", "ZZZ"]


def test_symbols_empty_when_the_store_dir_does_not_exist_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path / "does-not-exist"))
    assert store.symbols() == []


def test_manifest_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    assert store.read_manifest() == {}
    store.write_manifest({"SPY": {"first": "1996-01-02", "last": "2026-09-04", "rows": 7700, "fetched_at": "x"}})
    assert store.read_manifest()["SPY"]["rows"] == 7700
    assert json.loads((tmp_path / "_manifest.json").read_text(encoding="utf-8"))["SPY"]["last"] == "2026-09-04"


def test_malformed_price_rows_are_dropped_not_zero_filled(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    bad = {**API[0], "date": "2026-09-05T00:00:00.000Z", "open": "N/A"}
    rows = store.from_api(API + [bad])
    assert [r["date"] for r in rows] == ["2026-09-04", "2026-09-03"]      # the N/A row is gone, not 0.0
    missing_vol = {**API[0], "date": "2026-09-06T00:00:00.000Z"}
    missing_vol.pop("volume"); missing_vol.pop("splitFactor")
    ok = store.from_api([missing_vol])
    assert ok[0]["volume"] == 0.0 and ok[0]["split_factor"] == 1.0           # non-price gaps default
    store.write("SPY", store.from_api(API))
    p = store.path("SPY")
    p.write_text(p.read_text(encoding="utf-8") + "2026-09-07,N/A,1,1,1,1,1,1,1,1,1,0,1\n", encoding="utf-8")
    assert [r["date"] for r in store.read("SPY")] == ["2026-09-03", "2026-09-04"]


def test_non_finite_prices_are_dropped_and_zero_volume_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    inf_row = {**API[0], "date": "2026-09-08T00:00:00.000Z", "close": "inf"}
    nan_row = {**API[0], "date": "2026-09-09T00:00:00.000Z", "adjLow": "nan"}
    zero_vol = {**API[0], "date": "2026-09-10T00:00:00.000Z", "volume": 0, "adjVolume": 0}
    rows = store.from_api([inf_row, nan_row, zero_vol])
    assert [r["date"] for r in rows] == ["2026-09-10"]
    assert rows[0]["volume"] == 0.0 and rows[0]["adj_volume"] == 0.0
    store.write("SPY", store.from_api(API))
    p = store.path("SPY")
    p.write_text(p.read_text(encoding="utf-8") + "2026-09-11,1,1,1,inf,1,1,1,1,1,1,0,1\n", encoding="utf-8")
    assert [r["date"] for r in store.read("SPY")] == ["2026-09-03", "2026-09-04"]
