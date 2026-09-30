"""The Swing Planner: run a candidate through the swing-trading-plan gates + disqualifiers
+ stop/sizing rules and return a PASS plan or a SKIP with the exact failing reason.

Pure and deterministic - all I/O (bars, earnings, SPY) is fetched by the web layer and passed in.
Encodes playbook/swing-trading-plan.md v1.0.
"""
from __future__ import annotations

from statistics import mean

from ..analysis import support_resistance
from ..strategy.indicators import atr, macd, rsi, sma
from .detect import pullback_swing_low, reversal_trigger
from .schema import CheckResult, GateResult, SwingPlan
from .sizing import size_position

PORTFOLIO_NOTE = ("single-trade check only - also verify your portfolio caps "
                  "(max 3 open positions / 4% total open risk / circuit breakers)")
PRICE_FLOOR = 10.0
PRICE_CEIL_PCT = 0.20  # ceiling = PRICE_CEIL_PCT * BOOK
MIN_AVG_VOL = 1_000_000
MIN_AVG_DOLLAR_VOL = 20_000_000
MAX_ATR_PCT = 7.0
RSI_DIP_LO, RSI_DIP_HI = 35.0, 45.0
PULLBACK_WINDOW = 10


def _last(xs):
    return xs[-1] if xs else None


def plan_swing(symbol, daily, weekly=None, spy=None, earnings_date=None, book=500.0,
               support=None, target=None, confirm_not_leveraged=False, confirm_not_binary=False) -> SwingPlan:
    closes = [b["close"] for b in daily]
    highs = [b["high"] for b in daily]
    lows = [b["low"] for b in daily]
    vols = [b.get("volume") for b in daily]

    price = _last(closes)
    sma50 = _last(sma(closes, 50))
    sma200 = _last(sma(closes, 200))
    rsi_list = rsi(closes, 14)
    rsi14 = _last(rsi_list)
    atr_list = atr(highs, lows, closes, 14)
    atr14 = _last(atr_list)
    hist = macd(closes)["hist"]
    macd_hist = _last(hist)
    rv = [v for v in vols[-20:] if v is not None]
    avg_vol20 = mean(rv) if rv else None
    rdv = [closes[i] * vols[i] for i in range(max(0, len(daily) - 20), len(daily))
           if vols[i] is not None and closes[i] is not None]
    avg_dollar_vol20 = mean(rdv) if rdv else None

    indicators = {
        "price": round(price, 2) if price is not None else None,
        "sma50": round(sma50, 2) if sma50 is not None else None,
        "sma200": round(sma200, 2) if sma200 is not None else None,
        "rsi14": round(rsi14, 1) if rsi14 is not None else None,
        "atr14": round(atr14, 2) if atr14 is not None else None,
        "avg_vol20": round(avg_vol20) if avg_vol20 is not None else None,
        "avg_dollar_vol20": round(avg_dollar_vol20) if avg_dollar_vol20 is not None else None,
        "macd_hist": round(macd_hist, 3) if macd_hist is not None else None,
    }

    spy_risk_off = None
    if spy:
        spy_closes = [b["close"] for b in spy]
        spy_sma200 = _last(sma(spy_closes, 200))
        spc = _last(spy_closes)
        if spy_sma200 is not None and spc is not None:
            spy_risk_off = spc < spy_sma200

    sr = support_resistance(daily)
    disq: list[CheckResult] = []
    gates: list[GateResult] = []
    notes: list[str] = [PORTFOLIO_NOTE]
    if spy_risk_off:
        notes.insert(0, "SPY is below its 200-day SMA (risk-off) - the plan caps you at 1 open position.")
    first_fail = {"reason": None}

    def record(reason):
        if first_fail["reason"] is None:
            first_fail["reason"] = reason

    def dq(name, ok, detail):
        disq.append(CheckResult(name=name, ok=ok, detail=detail))
        if not ok:
            record(detail)

    def gate(n, name, ok, detail):
        gates.append(GateResult(n=n, name=name, ok=ok, detail=detail))
        if not ok:
            record(f"Gate {n} ({name}): {detail}")

    # Guard: need enough history for the 200 SMA.
    if sma200 is None or price is None or atr14 is None:
        return SwingPlan(symbol=symbol, book=book, indicators=indicators, disqualifiers=disq, gates=gates,
                         spy_risk_off=spy_risk_off, verdict="SKIP",
                         reason="insufficient history (need ~200+ daily bars)", notes=notes)

    # -- Section 3 disqualifiers -----------------------------------------------
    ceil = PRICE_CEIL_PCT * book
    dq("Price band", PRICE_FLOOR <= price <= ceil, f"${price:.2f} (need ${PRICE_FLOOR:.0f}-${ceil:.0f})")
    atr_pct = atr14 / price * 100
    dq("ATR <= 7% of price", atr_pct <= MAX_ATR_PCT, f"ATR {atr_pct:.1f}% of price")
    vol_ok = avg_vol20 is not None and avg_dollar_vol20 is not None and avg_vol20 >= MIN_AVG_VOL and avg_dollar_vol20 >= MIN_AVG_DOLLAR_VOL
    dq("Liquidity", vol_ok, f"20d avg vol {avg_vol20 or 0:,.0f} sh, ${(avg_dollar_vol20 or 0):,.0f}/day")
    # gap shock: any |pct move| > 10% in the last 5 sessions
    gap = max((abs(closes[i] / closes[i - 1] - 1) * 100) for i in range(max(1, len(closes) - 5), len(closes))) if len(closes) > 1 else 0
    dq("No >10% gap (5 sessions)", gap <= 10, f"largest move {gap:.1f}%")
    if weekly:
        wcl = [b["close"] for b in weekly]
        w10 = sma(wcl, 10)
        wdown = len([x for x in w10 if x is not None]) >= 2 and w10[-1] is not None and w10[-2] is not None and w10[-1] < w10[-2]
        dq("Weekly not down", not wdown, "weekly 10-SMA falling" if wdown else "weekly ok")
    else:
        notes.append("weekly trend unverified (no weekly bars)")
    if earnings_date:
        from datetime import date
        try:
            y, m, d = (int(x) for x in earnings_date[:10].split("-"))
            days = (date(y, m, d) - date.today()).days
            dq("No earnings within 14 days (~10 trading days)", not (0 <= days <= 14), f"next earnings in {days}d")
        except Exception:
            notes.append("earnings date unparseable")
    else:
        notes.append("earnings not verified (no calendar data)")
    dq("Confirmed not leveraged/inverse ETF", confirm_not_leveraged, "you must confirm" if not confirm_not_leveraged else "confirmed")
    dq("Confirmed not a binary-event name", confirm_not_binary, "you must confirm" if not confirm_not_binary else "confirmed")

    # -- Section 4 gates -------------------------------------------------------
    # Gate 1 - trend up
    g1 = sma50 is not None and sma50 > sma200 and price > sma200
    gate(1, "Trend up", g1, f"50SMA {sma50:.2f} {'>' if sma50 and sma50 > sma200 else '<='} 200SMA {sma200:.2f}, price {price:.2f}")

    # Gate 2 - valid pullback to support
    swing_low = pullback_swing_low(daily, window=PULLBACK_WINDOW)
    levels = [sma50]
    if support is not None:
        levels.append(support)
    else:
        levels += sr["support"]
    levels = [lv for lv in levels if lv is not None]
    near = swing_low is not None and levels and min(abs(swing_low - lv) for lv in levels) <= atr14
    recent_rsi = [x for x in rsi_list[-PULLBACK_WINDOW:] if x is not None]
    rsi_low = min(recent_rsi) if recent_rsi else None
    crash = rsi_low is not None and rsi_low < 30 and price < (sma50 or price)
    healthy = rsi_low is not None and RSI_DIP_LO <= rsi_low <= RSI_DIP_HI
    g2 = bool(near and healthy and not crash)
    g2_detail = f"swing low {swing_low:.2f} {'near' if near else 'NOT near'} support, RSI dip {rsi_low:.0f}" if (swing_low is not None and rsi_low is not None) else "no pullback data"
    gate(2, "Valid pullback", g2, g2_detail + (" (crash, RSI<30 below 50SMA)" if crash else ""))

    # Gate 3 - reversal trigger
    trig = reversal_trigger(daily)
    entry = round(trig["trigger_high"] + 0.05, 2) if trig else None
    gate(3, "Reversal trigger", trig is not None, f"{trig['kind']}, entry buy-stop ${entry:.2f}" if trig else "no bullish reversal candle")

    # Stop (Section 5a) + sanity cap
    stop = round(swing_low - 0.25 * atr14, 2) if swing_low is not None else None
    stop_ok = entry is not None and stop is not None and (entry - stop) <= 2 * atr14 and entry > stop
    if entry is not None and stop is not None:
        gate(5, "Stop within 2xATR", stop_ok, f"stop ${stop:.2f}, risk/share ${entry - stop:.2f} (<=2xATR ${2 * atr14:.2f})")
    else:
        gate(5, "Stop within 2xATR", False, "needs entry + swing low")

    # Gate 4 - volume confirmation (vs the 20 bars BEFORE the trigger bar, so the trigger's
    # own volume can't dilute the average it is compared against)
    trig_vol = vols[-1]
    prior_vols = [v for v in vols[-21:-1] if v is not None]
    prior_avg_vol20 = mean(prior_vols) if prior_vols else None
    hist_rising = len([x for x in hist if x is not None]) >= 2 and hist[-1] is not None and hist[-2] is not None and hist[-1] > hist[-2]
    if trig_vol is not None and prior_avg_vol20:
        vol_conf = trig_vol >= prior_avg_vol20 or (trig_vol >= 0.9 * prior_avg_vol20 and hist_rising)
        gate(4, "Volume confirmation", vol_conf, f"trigger vol {trig_vol:,.0f} vs prior 20d avg {prior_avg_vol20:,.0f}" + (" (+MACD rising)" if vol_conf and trig_vol < prior_avg_vol20 else ""))
    else:
        vol_conf = False
        gate(4, "Volume confirmation", False, "no volume data")

    # Gate 6 - reward:risk (numbered 5 in the plan; listed last here since it needs entry+stop+target)
    tgt = target
    if tgt is None:
        above = [r for r in sr["resistance"] if entry is not None and r > entry]
        tgt = above[0] if above else None
    rr_raw = None
    if entry is not None and stop is not None and tgt is not None and entry > stop:
        rr_raw = (tgt - entry) / (entry - stop)
    rr = round(rr_raw, 2) if rr_raw is not None else None  # display only
    rr_ok = rr_raw is not None and rr_raw >= 2.0  # gate on the UNROUNDED ratio (1.995 must not pass as 2.0)
    gate(6, "Reward:risk >= 2.0", rr_ok, f"target ${tgt:.2f}, R:R {rr}" if rr is not None else "no resistance above entry for a target")

    # -- Sizing (Section 6) ----------------------------------------------------
    sized = {"ok": False, "reason": "not sized"}
    if entry is not None and stop is not None and entry > stop:
        sized = size_position(book, entry, stop)
        if not sized["ok"]:
            record(f"Sizing: {sized['reason']}")

    all_ok = (g1 and g2 and trig is not None and vol_conf and stop_ok and rr_ok
              and all(c.ok for c in disq) and sized["ok"])

    if all_ok:
        return SwingPlan(
            symbol=symbol, book=book, indicators=indicators, disqualifiers=disq, gates=gates,
            entry=entry, stop=stop, target=round(tgt, 2), shares=sized["shares"],
            actual_risk_dollars=sized["actual_risk_dollars"], actual_risk_pct=sized["actual_risk_pct"],
            notional=sized["notional"], rr=rr, exit_mode=("A" if sized["shares"] <= 3 else "B"),
            spy_risk_off=spy_risk_off, verdict="PASS", reason="", notes=notes,
        )
    return SwingPlan(
        symbol=symbol, book=book, indicators=indicators, disqualifiers=disq, gates=gates,
        entry=entry, stop=stop, target=round(tgt, 2) if tgt is not None else None, rr=rr,
        spy_risk_off=spy_risk_off, verdict="SKIP", reason=first_fail["reason"] or "did not qualify", notes=notes,
    )
