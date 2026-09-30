"""Pure watchlist-setup scanner: classify one symbol's pre-fetched bars into actionable tags.
No network — analysis/momentum/swing inputs are passed in or read via the (pure) analysis engines."""
from __future__ import annotations

from datetime import date

from . import analysis, momentum

SETUP_WEIGHTS = {
    "swing_pass": 4,
    "near_support": 2,
    "oversold": 2,
    "breakout": 2,
    "momentum_leader": 2,
    "earnings_soon": 1,
}

TAG_LABELS = {
    "swing_pass": "Swing PASS",
    "near_support": "Near support",
    "oversold": "Oversold",
    "breakout": "Breakout",
    "momentum_leader": "Momentum leader",
    "earnings_soon": "Earnings soon",
}


def _tag(kind, detail=""):
    return {"kind": kind, "label": TAG_LABELS[kind], "detail": detail}


def scan_symbol(symbol, bars, *, spy_bars=None, swing_plan=None, earnings_date=None, today, last=None) -> dict:
    tech = analysis.technicals(bars) if bars else {}
    lvls = analysis.support_resistance(bars) if bars else {"support": []}
    price = last if last is not None else tech.get("price")
    rsi = tech.get("rsi14")
    high20 = tech.get("high20")
    trend = tech.get("trend")
    tags = []

    supports = [s for s in (lvls.get("support") or []) if price and s <= price]
    if price and supports:
        s = supports[0]  # highest support at/below price
        gap = (price - s) / price * 100
        if 0 <= gap <= 2.0:
            tags.append(_tag("near_support", f"support {s} (-{gap:.1f}%)"))

    if rsi is not None and rsi <= 30:
        tags.append(_tag("oversold", f"RSI {rsi:.0f}"))

    if price and high20 and price >= high20 * 0.99:
        tags.append(_tag("breakout", f"{(price / high20 - 1) * 100:+.1f}% vs 20d high"))

    if spy_bars and bars and trend == "uptrend":
        s_al, b_al = momentum.align_by_date(bars, spy_bars)
        rs = momentum.relative_strength(s_al, b_al)
        if rs.get("label") == "leader":
            ex = (rs.get("excess") or {}).get("3M")
            detail = "RS leader vs SPY" + (f" ({ex:+.0f}% 3M)" if ex is not None else "")
            tags.append(_tag("momentum_leader", detail))

    if isinstance(swing_plan, dict) and swing_plan.get("verdict") == "PASS":
        bits = [f"{k} {swing_plan[k]}" for k in ("entry", "stop", "target") if swing_plan.get(k) is not None]
        tags.append(_tag("swing_pass", ", ".join(bits)))

    if earnings_date:
        try:
            d = (date.fromisoformat(earnings_date) - today).days
            if 0 <= d <= 7:
                tags.append(_tag("earnings_soon", f"in {d}d"))
        except (TypeError, ValueError):
            pass

    score = sum(SETUP_WEIGHTS.get(t["kind"], 0) for t in tags)
    return {"symbol": symbol.upper(), "last": price, "tags": tags, "score": score}


def rank(rows: list[dict]) -> list[dict]:
    return sorted([r for r in rows if r.get("score", 0) > 0], key=lambda r: (-r["score"], r["symbol"]))
