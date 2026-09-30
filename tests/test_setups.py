from datetime import date

from webull_api import setups


def _patch_analysis(monkeypatch, *, price=100.0, rsi=50.0, high20=120.0, trend="uptrend", support=None):
    monkeypatch.setattr(setups.analysis, "technicals",
                        lambda bars: {"price": price, "rsi14": rsi, "high20": high20, "trend": trend})
    monkeypatch.setattr(setups.analysis, "support_resistance",
                        lambda bars: {"price": price, "support": support or [], "resistance": []})


def test_near_support_tag(monkeypatch):
    _patch_analysis(monkeypatch, price=100.0, support=[98.5])  # 1.5% above support
    r = setups.scan_symbol("X", [{}], today=date(2026, 6, 19))
    assert any(t["kind"] == "near_support" for t in r["tags"])


def test_near_support_too_far_no_tag(monkeypatch):
    _patch_analysis(monkeypatch, price=100.0, support=[95.0])  # 5% above support
    r = setups.scan_symbol("X", [{}], today=date(2026, 6, 19))
    assert not any(t["kind"] == "near_support" for t in r["tags"])


def test_oversold_tag(monkeypatch):
    _patch_analysis(monkeypatch, rsi=28.0)
    r = setups.scan_symbol("X", [{}], today=date(2026, 6, 19))
    assert any(t["kind"] == "oversold" for t in r["tags"])


def test_breakout_tag(monkeypatch):
    _patch_analysis(monkeypatch, price=121.0, high20=120.0)
    r = setups.scan_symbol("X", [{}], today=date(2026, 6, 19))
    assert any(t["kind"] == "breakout" for t in r["tags"])


def test_momentum_leader_tag(monkeypatch):
    _patch_analysis(monkeypatch, trend="uptrend")
    monkeypatch.setattr(setups.momentum, "align_by_date", lambda a, b: ([1, 2], [1, 1]))
    monkeypatch.setattr(setups.momentum, "relative_strength", lambda s, b: {"label": "leader", "excess": {"3M": 12.0}})
    r = setups.scan_symbol("X", [{}], spy_bars=[{}], today=date(2026, 6, 19))
    t = next(t for t in r["tags"] if t["kind"] == "momentum_leader")
    assert "(+12% 3M)" in t["detail"]


def test_momentum_leader_negative_excess_signed(monkeypatch):
    # Regression: a negative 3M excess must render "(-3% 3M)", not the old "(+-3% 3M)".
    _patch_analysis(monkeypatch, trend="uptrend")
    monkeypatch.setattr(setups.momentum, "align_by_date", lambda a, b: ([1, 2], [1, 1]))
    monkeypatch.setattr(setups.momentum, "relative_strength", lambda s, b: {"label": "leader", "excess": {"3M": -3.0}})
    r = setups.scan_symbol("X", [{}], spy_bars=[{}], today=date(2026, 6, 19))
    t = next(t for t in r["tags"] if t["kind"] == "momentum_leader")
    assert "(-3% 3M)" in t["detail"]
    assert "+-" not in t["detail"]


def test_swing_pass_tag(monkeypatch):
    _patch_analysis(monkeypatch)
    r = setups.scan_symbol("X", [{}], swing_plan={"verdict": "PASS", "entry": 100, "stop": 95, "target": 115},
                           today=date(2026, 6, 19))
    assert any(t["kind"] == "swing_pass" for t in r["tags"])


def test_earnings_soon_tag(monkeypatch):
    _patch_analysis(monkeypatch)
    soon = setups.scan_symbol("X", [{}], earnings_date="2026-06-22", today=date(2026, 6, 19))
    far = setups.scan_symbol("X", [{}], earnings_date="2026-08-01", today=date(2026, 6, 19))
    assert any(t["kind"] == "earnings_soon" for t in soon["tags"])
    assert not any(t["kind"] == "earnings_soon" for t in far["tags"])


def test_score_and_last_default(monkeypatch):
    _patch_analysis(monkeypatch, price=100.0, rsi=28.0, support=[99.0])  # oversold + near_support
    r = setups.scan_symbol("X", [{}], today=date(2026, 6, 19))
    assert r["last"] == 100.0
    assert r["score"] == setups.SETUP_WEIGHTS["oversold"] + setups.SETUP_WEIGHTS["near_support"]


def test_rank_drops_empty_and_sorts(monkeypatch):
    rows = [
        {"symbol": "A", "score": 2, "tags": [{}]},
        {"symbol": "B", "score": 0, "tags": []},
        {"symbol": "C", "score": 6, "tags": [{}]},
    ]
    out = setups.rank(rows)
    assert [r["symbol"] for r in out] == ["C", "A"]


def test_no_crash_on_empty(monkeypatch):
    monkeypatch.setattr(setups.analysis, "technicals", lambda bars: {})
    monkeypatch.setattr(setups.analysis, "support_resistance", lambda bars: {"support": []})
    r = setups.scan_symbol("X", [], today=date(2026, 6, 19))
    assert r["tags"] == [] and r["score"] == 0
