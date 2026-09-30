"""webull_web.regime -- the SPY-vs-200-day regime the nightly manager's note and the entry judgment read
(moved out of the retired web app's swing router, spec 2026-09-28 systems-only §4)."""
from webull_api import market_data
from webull_web import manager_note_service, regime
from tests.test_swing_planner import clean_pullback_bars


def _raw_newest_first(bars):
    return list(reversed([dict(b) for b in bars]))


def test_uptrend_is_risk_on(monkeypatch):
    raw = _raw_newest_first(clean_pullback_bars())  # a rising series: last close above the 200-day SMA
    monkeypatch.setattr(market_data, "get_bars", lambda s, ts, count="250": raw)
    out = regime.swing_regime()
    assert out["spy_risk_off"] is False
    assert out["spy_price"] is not None and out["spy_sma200"] is not None


def test_a_data_error_degrades_to_nulls(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(market_data, "get_bars", boom)
    assert regime.swing_regime() == {"spy_risk_off": None, "spy_price": None, "spy_sma200": None}


def test_manager_note_reads_the_regime_module(monkeypatch):
    want = {"spy_risk_off": True, "spy_price": 1.0, "spy_sma200": 2.0}
    monkeypatch.setattr(regime, "swing_regime", lambda: want)
    assert manager_note_service._regime_now() == want
