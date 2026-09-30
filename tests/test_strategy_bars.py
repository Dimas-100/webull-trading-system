from webull_api.strategy import bars
from webull_api.strategy.bars import to_ohlcv

# Webull bars arrive newest-first, values as strings.
RAW = [
    {"time": "t2", "open": "11", "high": "12", "low": "10", "close": "11.5", "volume": "100"},
    {"time": "t1", "open": "10", "high": "10.5", "low": "9.5", "close": "10", "volume": "50"},
]


def test_reverses_to_chronological_and_coerces_strings():
    bars = to_ohlcv(RAW)
    assert [b["time"] for b in bars] == ["t1", "t2"]
    assert bars[0]["open"] == 10.0 and bars[1]["close"] == 11.5
    assert isinstance(bars[0]["open"], float)


def test_accepts_data_wrapper_and_drops_bad_rows():
    assert to_ohlcv({"data": RAW})[0]["time"] == "t1"
    assert to_ohlcv([{"time": "x", "open": None, "high": "1", "low": "1", "close": "1"}]) == []
    assert to_ohlcv(None) == []


def test_timeframe_to_timespan_map():
    assert bars.TIMEFRAME_TO_TIMESPAN["1D"] == "D"
    assert bars.TIMEFRAME_TO_TIMESPAN["1H"] == "M60"
    assert bars.TIMEFRAME_TO_TIMESPAN["1m"] == "M1"
    assert bars.TIMEFRAME_TO_TIMESPAN["1W"] == "W"


def test_to_ohlcv_is_idempotent():
    """LIVE BUG 2026-08-15: the lab's cycle injected an already-normalized get_bars, and
    orchestrator.fetch_basket_bars / first_available_bars re-applied to_ohlcv. The old blind
    bars.reverse() then flipped oldest-first back to newest-first — every Gate-A screen and
    every cycle regime for 31 cycles ran on TIME-REVERSED bars (a 3-year uptrend classified
    'down/low' 31/31). to_ohlcv must therefore detect order instead of assuming newest-first."""
    raw_newest_first = [
        {"time": "2026-08-14", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 1},
        {"time": "2026-08-13", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1},
        {"time": "2026-08-12", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ]
    once = to_ohlcv(raw_newest_first)
    assert [b["time"] for b in once] == ["2026-08-12", "2026-08-13", "2026-08-14"]
    twice = to_ohlcv(once)
    assert twice == once, "double application must be a no-op, not a time reversal"


def test_to_ohlcv_preserves_an_already_oldest_first_payload():
    raw_oldest_first = [
        {"time": "2026-08-12", "close": 1, "open": 1, "high": 1, "low": 1, "volume": 1},
        {"time": "2026-08-14", "close": 3, "open": 3, "high": 3, "low": 3, "volume": 1},
    ]
    out = to_ohlcv(raw_oldest_first)
    assert [b["time"] for b in out] == ["2026-08-12", "2026-08-14"]


def test_to_ohlcv_single_bar_and_empty_still_work():
    assert to_ohlcv([]) == []
    one = to_ohlcv([{"time": "2026-08-14", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
    assert len(one) == 1
