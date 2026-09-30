import math
from datetime import date, timedelta
from statistics import mean

from webull_api.swing.planner import plan_swing


def _bar(o, h, l, c, v):
    return {"time": "d", "open": round(o, 2), "high": round(h, 2), "low": round(l, 2),
            "close": round(c, 2), "volume": v}


def clean_pullback_bars():
    """Gentle, noisy uptrend (RSI sits mid-range), then a modest pullback that dips RSI toward
    the 35-45 band, then a close-above-prior-high trigger on rising volume."""
    bars = []
    c = 35.0
    for i in range(200):
        cl = 35.0 + 0.05 * i + 1.4 * math.sin(i / 2.0)  # uptrend + strong oscillation (RSI ~mid-range)
        o = c
        bars.append(_bar(o, max(o, cl) + 0.5, min(o, cl) - 0.5, cl, 2_000_000))
        c = cl
    for d in (-0.55, -0.45, -0.4):  # modest 3-bar pullback (RSI dips toward 35-45, not a crash)
        cl = c + d
        o = c
        bars.append(_bar(o, max(o, cl) + 0.3, min(o, cl) - 0.4, cl, 1_700_000))
        c = cl
    ph = bars[-1]["high"]  # trigger: closes above prior high, volume > 20d avg
    bars.append(_bar(c, ph + 0.3, c - 0.15, ph + 0.2, 2_600_000))
    return bars


def downtrend_bars():
    bars = []
    c = 60.0
    for i in range(230):
        cl = 60.0 - 20.0 * (i / 229)  # below the 200 SMA
        o = c
        bars.append(_bar(o, max(o, cl) + 0.5, min(o, cl) - 0.5, cl, 2_000_000))
        c = cl
    return bars


def _clean_overrides():
    bars = clean_pullback_bars()
    support = min(b["low"] for b in bars[-10:])  # user marks support at the pullback low
    target = bars[-1]["high"] + 5.0              # a resistance well above (>=2R)
    return bars, support, target


def test_downtrend_skips_on_trend_gate():
    plan = plan_swing("X", daily=downtrend_bars(), book=500,
                      confirm_not_leveraged=True, confirm_not_binary=True)
    assert plan.verdict == "SKIP"
    assert "Gate 1" in plan.reason or "trend" in plan.reason.lower()


def test_manual_confirm_required():
    bars, support, target = _clean_overrides()
    plan = plan_swing("X", daily=bars, book=500, support=support, target=target)  # confirms default False
    assert plan.verdict == "SKIP" and "confirm" in plan.reason.lower()


def test_clean_pullback_passes():
    bars, support, target = _clean_overrides()
    plan = plan_swing("X", daily=bars, book=500, support=support, target=target,
                      confirm_not_leveraged=True, confirm_not_binary=True)
    assert plan.verdict == "PASS", f"reason={plan.reason}; gates={[(g.n, g.ok, g.detail) for g in plan.gates]}"
    assert plan.entry and plan.stop and plan.target and plan.shares >= 1 and plan.rr >= 2.0
    assert plan.exit_mode in ("A", "B")


def test_insufficient_history_skips():
    short = clean_pullback_bars()[-50:]
    plan = plan_swing("X", daily=short, book=500, confirm_not_leveraged=True, confirm_not_binary=True)
    assert plan.verdict == "SKIP" and "history" in plan.reason.lower()


def test_failed_volume_gate_blocks_pass():
    """Regression: Gate 4 (volume confirmation) must gate the PASS verdict — a weak-volume
    trigger (800k vs ~1.9M avg) was previously computed but omitted from all_ok."""
    bars, support, target = _clean_overrides()
    bars[-1]["volume"] = 800_000  # kill only the trigger volume; every other gate still passes
    plan = plan_swing("X", daily=bars, book=500, support=support, target=target,
                      confirm_not_leveraged=True, confirm_not_binary=True)
    assert plan.verdict == "SKIP"
    assert "Gate 4" in plan.reason and "Volume confirmation" in plan.reason
    assert next(g for g in plan.gates if g.n == 4).ok is False


def test_volume_gate_average_excludes_trigger_bar():
    """Regression: Gate 4 averages the 20 bars BEFORE the trigger — the trigger bar's own
    volume must not dilute (or inflate) the average it is compared against."""
    bars, support, target = _clean_overrides()
    prior_avg = mean(b["volume"] for b in bars[-21:-1])
    details = []
    for trig_vol in (2_600_000, 26_000_000):
        b = [dict(x) for x in bars]
        b[-1]["volume"] = trig_vol
        plan = plan_swing("X", daily=b, book=500, support=support, target=target,
                          confirm_not_leveraged=True, confirm_not_binary=True)
        details.append(next(g for g in plan.gates if g.n == 4).detail)
    assert all(f"{prior_avg:,.0f}" in d for d in details)     # reported avg = pre-trigger bars only
    assert details[0].split(" vs ")[1] == details[1].split(" vs ")[1]  # unmoved by the trigger's own volume


def test_rr_gate_compares_unrounded():
    """Regression: a true R:R of 1.996 displays as 2.0 but must NOT pass the >= 2.0 gate."""
    bars, support, target = _clean_overrides()
    good = plan_swing("X", daily=bars, book=500, support=support, target=target,
                      confirm_not_leveraged=True, confirm_not_binary=True)
    assert good.verdict == "PASS"
    boundary_target = good.entry + 1.996 * (good.entry - good.stop)
    plan = plan_swing("X", daily=bars, book=500, support=support, target=boundary_target,
                      confirm_not_leveraged=True, confirm_not_binary=True)
    assert plan.rr == 2.0                                      # display rounds to 2dp...
    assert plan.verdict == "SKIP" and "Gate 6" in plan.reason  # ...but the gate compares unrounded


def test_earnings_disqualifier_label_matches_14_day_window():
    """Regression: the label must say what the code does — it blocks 0-14 calendar days
    (~10 trading days), not "<=10 days"."""
    bars, support, target = _clean_overrides()
    soon = (date.today() + timedelta(days=12)).isoformat()
    plan = plan_swing("X", daily=bars, book=500, support=support, target=target,
                      earnings_date=soon, confirm_not_leveraged=True, confirm_not_binary=True)
    d = next(d for d in plan.disqualifiers if "earnings" in d.name.lower())
    assert d.ok is False and plan.verdict == "SKIP"
    assert "14 days" in d.name and "10 trading days" in d.name
    far = (date.today() + timedelta(days=15)).isoformat()      # window math itself unchanged
    plan2 = plan_swing("X", daily=bars, book=500, support=support, target=target,
                       earnings_date=far, confirm_not_leveraged=True, confirm_not_binary=True)
    assert next(d for d in plan2.disqualifiers if "earnings" in d.name.lower()).ok is True
    assert plan2.verdict == "PASS"


def test_plan_has_full_checklist():
    bars, support, target = _clean_overrides()
    plan = plan_swing("X", daily=bars, book=500, support=support, target=target,
                      confirm_not_leveraged=True, confirm_not_binary=True)
    assert len(plan.gates) >= 5 and len(plan.disqualifiers) >= 4


if __name__ == "__main__":
    import json
    bars, support, target = _clean_overrides()
    p = plan_swing("X", daily=bars, book=500, support=support, target=target,
                   confirm_not_leveraged=True, confirm_not_binary=True)
    print("VERDICT:", p.verdict, "| reason:", p.reason)
    print("indicators:", json.dumps(p.indicators))
    for d in p.disqualifiers:
        print(f"  DQ {'OK' if d.ok else 'XX'} {d.name}: {d.detail}")
    for g in p.gates:
        print(f"  G{g.n} {'OK' if g.ok else 'XX'} {g.name}: {g.detail}")
    print("plan:", p.entry, p.stop, p.target, p.shares, p.rr, p.exit_mode)
