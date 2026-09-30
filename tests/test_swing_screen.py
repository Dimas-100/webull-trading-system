import pytest

from webull_api.swing import screen as sc
from webull_api.swing.schema import SwingPlan
from webull_api.market_data import MarketDataNotEntitledError


def _plan(symbol, verdict):
    return SwingPlan(symbol=symbol, book=500, indicators={}, verdict=verdict)


def test_screen_ranks_pass_first_and_carries_plan(monkeypatch):
    plans = {"AAA": _plan("AAA", "SKIP"), "BBB": _plan("BBB", "PASS")}
    monkeypatch.setattr(sc.planner, "plan_swing", lambda sym, daily, **k: plans[sym])
    monkeypatch.setattr(sc, "to_ohlcv", lambda raw: [{"close": 1}])
    rows = sc.screen(["AAA", "BBB"], book=500, get_bars=lambda *a, **k: [])
    assert [r.symbol for r in rows] == ["BBB", "AAA"]      # PASS first
    assert rows[0].plan.verdict == "PASS" and rows[0].error is None


def test_screen_per_symbol_error_becomes_a_row(monkeypatch):
    def boom(sym, daily, **k):
        raise RuntimeError("bad bars")
    monkeypatch.setattr(sc.planner, "plan_swing", boom)
    monkeypatch.setattr(sc, "to_ohlcv", lambda raw: [{"close": 1}])
    rows = sc.screen(["X"], get_bars=lambda *a, **k: [])
    assert rows[0].error == "bad bars" and rows[0].plan is None


def test_screen_entitlement_propagates():
    def boom(*a, **k):
        raise MarketDataNotEntitledError("no")
    with pytest.raises(MarketDataNotEntitledError):
        sc.screen(["X"], get_bars=boom)


# ---- earnings gate: PASS-only fetch + re-plan ------------------------------------------------

def _recording_planner(plans_by_earnings):
    """Fake plan_swing keyed on the earnings_date kwarg; records every call's earnings_date."""
    calls = []

    def fake(sym, daily, **k):
        calls.append(k.get("earnings_date"))
        return plans_by_earnings[k.get("earnings_date")]
    return fake, calls


def test_pass_with_near_earnings_is_replanned_to_skip(monkeypatch):
    fake, calls = _recording_planner({None: _plan("AAA", "PASS"), "2026-07-30": _plan("AAA", "SKIP")})
    monkeypatch.setattr(sc.planner, "plan_swing", fake)
    monkeypatch.setattr(sc, "to_ohlcv", lambda raw: [{"close": 1}])
    fetched = []
    rows = sc.screen(["AAA"], get_bars=lambda *a, **k: [],
                     get_next_earnings=lambda s: fetched.append(s) or "2026-07-30")
    assert fetched == ["AAA"]
    assert calls == [None, "2026-07-30"]          # planned, then re-planned with the date
    assert rows[0].plan.verdict == "SKIP"


def test_pass_with_far_earnings_stays_pass(monkeypatch):
    fake, _ = _recording_planner({None: _plan("AAA", "PASS"), "2026-12-01": _plan("AAA", "PASS")})
    monkeypatch.setattr(sc.planner, "plan_swing", fake)
    monkeypatch.setattr(sc, "to_ohlcv", lambda raw: [{"close": 1}])
    rows = sc.screen(["AAA"], get_bars=lambda *a, **k: [],
                     get_next_earnings=lambda s: "2026-12-01")
    assert rows[0].plan.verdict == "PASS"


def test_earnings_fetch_failure_keeps_the_original_pass(monkeypatch):
    fake, calls = _recording_planner({None: _plan("AAA", "PASS")})
    monkeypatch.setattr(sc.planner, "plan_swing", fake)
    monkeypatch.setattr(sc, "to_ohlcv", lambda raw: [{"close": 1}])

    def boom(s):
        raise RuntimeError("finnhub down")
    rows = sc.screen(["AAA"], get_bars=lambda *a, **k: [], get_next_earnings=boom)
    assert calls == [None]                        # no re-plan attempted
    assert rows[0].plan.verdict == "PASS"         # outage restores today's behavior


def test_earnings_none_result_keeps_the_original_pass(monkeypatch):
    fake, calls = _recording_planner({None: _plan("AAA", "PASS")})
    monkeypatch.setattr(sc.planner, "plan_swing", fake)
    monkeypatch.setattr(sc, "to_ohlcv", lambda raw: [{"close": 1}])
    rows = sc.screen(["AAA"], get_bars=lambda *a, **k: [], get_next_earnings=lambda s: None)
    assert calls == [None] and rows[0].plan.verdict == "PASS"


def test_skip_rows_never_fetch_earnings(monkeypatch):
    fake, _ = _recording_planner({None: _plan("AAA", "SKIP")})
    monkeypatch.setattr(sc.planner, "plan_swing", fake)
    monkeypatch.setattr(sc, "to_ohlcv", lambda raw: [{"close": 1}])
    fetched = []
    sc.screen(["AAA"], get_bars=lambda *a, **k: [],
              get_next_earnings=lambda s: fetched.append(s) or None)
    assert fetched == []                          # 0-3 Finnhub calls per screen, not 40
