from webull_api import momentum as m


def _bars(times, closes):
    return [{"time": t, "open": c, "high": c, "low": c, "close": c, "volume": 1} for t, c in zip(times, closes)]


def test_returns_over_basic():
    closes = [100.0] * 300
    closes[-1] = 110.0
    closes[-22] = 100.0  # 21 bars back
    out = m.returns_over(closes)
    assert round(out["1M"], 4) == 10.0
    assert out["12M"] is not None


def test_returns_over_insufficient():
    assert m.returns_over([100.0, 101.0])["12M"] is None


def test_align_by_date_intersects():
    s = _bars(["d1", "d2", "d3"], [10, 11, 12])
    b = _bars(["d2", "d3", "d4"], [20, 22, 24])
    sc, bc = m.align_by_date(s, b)
    assert sc == [11, 12]
    assert bc == [20, 22]


def test_beta_of_2x_returns_is_2():
    # bench daily returns r; stock daily returns 2r -> beta 2
    bench = [100.0]
    stock = [100.0]
    rs = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01]
    for r in rs:
        bench.append(bench[-1] * (1 + r))
        stock.append(stock[-1] * (1 + 2 * r))
    assert abs(m.beta(stock, bench) - 2.0) < 1e-6


def test_beta_none_on_zero_variance():
    assert m.beta([1, 2, 3], [5, 5, 5]) is None


def test_range_position():
    assert m.range_position(50, 100, 0) == 50.0
    assert m.range_position(50, 50, 50) is None
    assert m.range_position(None, 1, 0) is None


def test_relative_strength_leader():
    # stock +20% across windows, bench flat -> leader
    closes_s = [100.0] * 300
    closes_s[-1] = 120.0
    closes_b = [100.0] * 300
    rs = m.relative_strength(closes_s, closes_b)
    assert rs["label"] == "leader"
    assert rs["excess"]["12M"] > 0


def test_relative_strength_unknown_when_short():
    rs = m.relative_strength([100, 101], [100, 100])
    assert rs["label"] == "unknown"


def test_momentum_score_bands():
    assert m.momentum_score({"1M": 5.0, "3M": 5.0})["label"] == "strong"
    assert m.momentum_score({"1M": -5.0, "3M": -5.0})["label"] == "weak"
    assert m.momentum_score({"1M": 0.0, "3M": 1.0})["label"] == "neutral"
    assert m.momentum_score({"1M": None})["label"] == "unknown"


def test_momentum_for_uses_bars_and_benchmark(monkeypatch):
    # stock rises 20%, SPY flat, sector falls -> stock is a leader vs both
    def fake_bars(symbol, *a, **k):
        n = 300
        if symbol == "SPY":
            closes = [100.0 + (i % 5) * 0.1 for i in range(n)]  # tiny wiggle (~flat, var>0)
        elif symbol == "XLK":
            closes = [120.0 - 20.0 * i / (n - 1) for i in range(n)]  # declining
        else:
            closes = [100.0 + 20.0 * i / (n - 1) for i in range(n)]  # rising
        # newest-first raw rows (to_ohlcv reverses); shared 'time' so align matches
        return [{"t": f"d{n - 1 - i}", "o": c, "h": c, "l": c, "c": c, "v": 1}
                for i, c in enumerate(reversed(closes))]

    monkeypatch.setattr(m, "get_bars", fake_bars, raising=False)
    out = m.momentum_for("TEST", {"SPY": "SPY", "Sector": "XLK"})
    assert out["symbol"] == "TEST"
    assert out["returns"]["12M"] is not None
    assert out["relative_strength"]["SPY"]["label"] == "leader"
    assert out["relative_strength"]["Sector"]["label"] == "leader"
    assert out["beta"] is not None
    assert out["range_position"] is not None


def test_momentum_for_benchmark_failure_degrades(monkeypatch):
    def fake_bars(symbol, *a, **k):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return [{"t": f"d{i}", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1} for i in range(300)]

    monkeypatch.setattr(m, "get_bars", fake_bars, raising=False)
    out = m.momentum_for("TEST", {"SPY": "SPY", "Sector": "BAD"})
    assert out["relative_strength"]["Sector"]["error"] == "unavailable"
    assert "SPY" in out["relative_strength"]
