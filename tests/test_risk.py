import math
import statistics

from webull_api import risk


def test_annualized_volatility_matches_stdev():
    closes = [100.0]
    rets = [0.01, -0.02, 0.015, -0.005, 0.02]
    for r in rets:
        closes.append(closes[-1] * (1 + r))
    daily = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    expected = statistics.stdev(daily) * math.sqrt(252) * 100
    assert abs(risk.annualized_volatility(closes) - expected) < 1e-6


def test_volatility_none_when_short():
    assert risk.annualized_volatility([100.0]) is None


def test_max_drawdown():
    # rise to 120 then fall to 90 -> trough vs peak 120 = -25%
    closes = [100, 110, 120, 105, 90, 100]
    assert risk.max_drawdown(closes) == -25.0
    assert risk.max_drawdown([100, 101, 102]) == 0.0  # monotonic up
    assert risk.max_drawdown([]) is None


def test_sharpe_positive_for_uptrend():
    closes = [100.0 * (1.001 ** i) for i in range(60)]  # steady up, low vol
    s = risk.sharpe(closes, rf=0.0)
    assert s is not None and s > 0


def test_sharpe_none_on_zero_vol():
    assert risk.sharpe([100.0, 100.0, 100.0]) is None


def test_sortino_none_without_downside():
    closes = [100.0, 101.0, 102.0, 103.0]  # no negative returns
    assert risk.sortino(closes) is None


def test_risk_label_bands():
    assert risk.risk_label(10) == "low"
    assert risk.risk_label(30) == "moderate"
    assert risk.risk_label(50) == "high"
    assert risk.risk_label(None) == "unknown"


def test_atr_pct():
    highs = [10, 11, 12, 13, 14]
    lows = [9, 10, 11, 12, 13]
    closes = [9.5, 10.5, 11.5, 12.5, 13.5]
    # period 2 -> a real number / last close
    v = risk.atr_pct(highs, lows, closes, period=2)
    assert v is not None and v > 0


def test_risk_for_uses_bars(monkeypatch):
    def fake_bars(symbol, *a, **k):
        n = 300
        closes = [100.0 + 10.0 * math.sin(i / 5) for i in range(n)]
        return [{"t": f"d{n - 1 - i}", "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 1}
                for i, c in enumerate(reversed(closes))]

    monkeypatch.setattr(risk, "get_bars", fake_bars, raising=False)
    out = risk.risk_for("TEST")
    assert out["symbol"] == "TEST"
    assert out["volatility"] is not None
    assert out["risk_label"] in ("low", "moderate", "high")
    assert out["max_drawdown"] <= 0
