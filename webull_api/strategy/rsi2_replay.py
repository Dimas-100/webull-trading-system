"""Portfolio replay of the EXACT production RSI2 config. Pure: bars in, trades/stats out —
no I/O, no network, no wall-clock. Drives the real ``rsi2.decide`` + ``rsi2.rsi2_of`` over a
merged daily calendar with next-open fills (matching the shipped execution-fidelity
semantics), so it tests the production strategy, not a re-implementation. Spec:
docs/superpowers/specs/2026-07-27-rsi2-config-backtest-design.md.

Optional ``stop_rule`` (``StopRule``: backstop % / breakeven-after-% resting stop) layers a
protective stop ON TOP of that unchanged RSI(2) decision engine -- it never touches
``rsi2.decide``/``rsi2.rsi2_of``, it only intercepts an open lot's exit each session.
Absent/None, results are byte-identical to the pre-stop-rule engine."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import rsi2

_EPS = 1e-9
_RSI_WINDOW = 30  # trailing closes per day, matching rsi2_service._fresh_rsi count="30"


@dataclass(frozen=True)
class StopRule:
    """A resting protective stop layered on top of the RSI(2) exit, evaluated per open lot on
    every session AFTER its fill day (never the fill day itself), BEFORE that day's RSI exit
    logic is decided -- but an RSI exit already queued for that day's open (from a PRIOR day's
    decision) settles at the open first, so only a still-unqueued lot is exposed to the day's
    low. ``backstop_pct=8.0`` means a resting stop at ``entry * (1 - 0.08)`` from the fill day
    on. ``breakeven_after_pct=3.0`` means once a daily CLOSE >= ``entry * 1.03``, the stop
    level is raised (never lowered) to ``entry`` starting the NEXT session -- mirroring the
    owner moving the stop up by hand. A stop-level hit (session low <= stop_level) exits that
    same session at ``min(stop_level, open)`` (a gap below the stop fills at the open --
    conservative, never better than the stop)."""
    backstop_pct: float | None = None
    breakeven_after_pct: float | None = None


@dataclass
class ReplayTrade:
    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    shares: float
    pnl: float
    return_pct: float
    holding_days: int
    regime: str  # regime of the ENTRY-DECISION day: risk_on | risk_off | warmup
    exit_reason: str = "rsi"  # "rsi" | "stop" | "breakeven" -- additive; old consumers unaffected
    entry_rsi: float | None = None  # RSI(2) of the ENTRY-DECISION day (the session before the
    # fill) -- the value the live runner ranks candidates by, carried through so a caller can
    # re-apply that ranking downstream. Additive, default None; old consumers unaffected.


@dataclass
class ReplayResult:
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    open_positions: list = field(default_factory=list)
    dropped_buys: int = 0
    unfilled_decisions: int = 0
    stats: dict = field(default_factory=dict)


def _regime_by_date(spy_bars: list) -> dict:
    """date -> risk_on | risk_off | warmup from SPY closes-to-date vs SMA200. Reporting-only
    (production has no regime gate on this sleeve)."""
    out: dict = {}
    closes: list[float] = []
    for b in spy_bars:
        c = b.get("close")
        if c is None:
            continue
        closes.append(float(c))
        if len(closes) < 200:
            out[b["date"]] = "warmup"
        else:
            sma = sum(closes[-200:]) / 200.0
            out[b["date"]] = "risk_off" if closes[-1] < sma else "risk_on"
    return out


def _tag(regimes: dict, ordered_spy_dates: list, date: str) -> str:
    """The regime for `date`: exact SPY bar if present, else the most recent earlier SPY
    date's tag (calendar mismatch), else warmup."""
    if date in regimes:
        return regimes[date]
    prev = None
    for d in ordered_spy_dates:  # ordered; last one before `date` wins
        if d < date:
            prev = d
        else:
            break
    return regimes.get(prev, "warmup")


def replay(bars_by_symbol: dict, spy_bars: list, *, cfg=rsi2.DEFAULT_CONFIG,
           starting_cash: float = 100_000.0, stop_rule: StopRule | None = None) -> ReplayResult:
    by_sym_date: dict = {}
    for sym, rows in bars_by_symbol.items():
        by_sym_date[sym] = {r["date"]: r for r in rows
                            if r.get("date") and r.get("open") is not None
                            and r.get("close") is not None}
    calendar = sorted({d for m in by_sym_date.values() for d in m})
    regimes = _regime_by_date(spy_bars)
    spy_dates = sorted(regimes)

    cash = float(starting_cash)
    owned_lots: list[dict] = []       # {"symbol","shares","entry_price","entry_date","entry_idx","regime"}
    pending: list[dict] = []          # decide() decision + {"regime": str}
    last_close: dict = {}
    closes_hist: dict = {sym: [] for sym in by_sym_date}
    res = ReplayResult()

    for idx, day in enumerate(calendar):
        # 1) SETTLE yesterday's queued decisions at today's opens (first-bar-after semantics:
        #    a decision whose symbol has no bar today keeps waiting).
        still_pending: list[dict] = []
        for d in pending:
            bar = by_sym_date.get(d["symbol"], {}).get(day)
            if bar is None:
                still_pending.append(d)
                continue
            o = float(bar["open"])
            if d["action"] == "BUY":
                cost = d["quantity"] * o
                if cost > cash + _EPS:            # unaffordable at the actual open
                    res.dropped_buys += 1
                    continue
                cash -= cost
                lot = {"symbol": d["symbol"], "shares": d["quantity"],
                      "entry_price": o, "entry_date": day, "entry_idx": idx,
                      "regime": d["regime"], "entry_rsi": d.get("rsi2"),
                      "stop_level": None, "stop_reason": None}
                if stop_rule is not None and stop_rule.backstop_pct is not None:
                    lot["stop_level"] = o * (1 - stop_rule.backstop_pct / 100.0)
                    lot["stop_reason"] = "stop"
                owned_lots.append(lot)
            else:  # SELL — RSI2 holds at most one lot per symbol
                lot = next((l for l in owned_lots if l["symbol"] == d["symbol"]), None)
                if lot is None:
                    continue
                cash += lot["shares"] * o
                pnl = (o - lot["entry_price"]) * lot["shares"]
                ret = (o / lot["entry_price"] - 1) * 100 if lot["entry_price"] else 0.0
                res.trades.append(ReplayTrade(
                    symbol=lot["symbol"], entry_date=lot["entry_date"],
                    entry_price=lot["entry_price"], exit_date=day, exit_price=o,
                    shares=lot["shares"], pnl=pnl, return_pct=ret,
                    holding_days=idx - lot["entry_idx"], regime=lot["regime"],
                    entry_rsi=lot.get("entry_rsi")))
                owned_lots.remove(lot)
        pending = still_pending

        # 1b) STOP RULE: only lots that survived the settle above (an RSI exit already queued
        # for TODAY's open fills first, above -- the position is gone before the day's range is
        # even looked at) and only on a session AFTER the lot's own fill day (entry_idx < idx;
        # the fill day itself neither checks a stop nor arms breakeven).
        if stop_rule is not None:
            for lot in list(owned_lots):
                if lot["entry_idx"] >= idx:
                    continue
                bar = by_sym_date.get(lot["symbol"], {}).get(day)
                if bar is None:
                    continue
                stop_level = lot["stop_level"]
                low = bar.get("low")
                hit = (stop_level is not None and low is not None
                      and float(low) <= stop_level + _EPS)
                if hit:
                    o = float(bar["open"])
                    fill = min(stop_level, o)
                    reason = lot["stop_reason"] or "stop"
                    pnl = (fill - lot["entry_price"]) * lot["shares"]
                    ret = (fill / lot["entry_price"] - 1) * 100 if lot["entry_price"] else 0.0
                    res.trades.append(ReplayTrade(
                        symbol=lot["symbol"], entry_date=lot["entry_date"],
                        entry_price=lot["entry_price"], exit_date=day, exit_price=fill,
                        shares=lot["shares"], pnl=pnl, return_pct=ret,
                        holding_days=idx - lot["entry_idx"], regime=lot["regime"],
                        exit_reason=reason, entry_rsi=lot.get("entry_rsi")))
                    cash += lot["shares"] * fill
                    owned_lots.remove(lot)
                    continue
                # Not hit -- may ARM (raise, never lower) the stop to breakeven off TODAY's
                # close, effective from the NEXT session on (this day's own low was just
                # checked above against the PRE-arming level).
                if stop_rule.breakeven_after_pct is not None:
                    c = float(bar["close"])
                    threshold = lot["entry_price"] * (1 + stop_rule.breakeven_after_pct / 100.0)
                    if c >= threshold - _EPS:
                        new_level = lot["entry_price"]
                        if stop_level is None or new_level > stop_level:
                            lot["stop_level"] = new_level
                            lot["stop_reason"] = "breakeven"

        # 2) SIGNALS: trailing-30-bar RSI(2) per symbol with a bar TODAY (freshness gate).
        rsi_by_symbol: dict = {}
        price_by_symbol: dict = {}
        for sym, dates in by_sym_date.items():
            bar = dates.get(day)
            if bar is None:
                continue
            closes_hist[sym].append(float(bar["close"]))
            last_close[sym] = float(bar["close"])
            r = rsi2.rsi2_of(closes_hist[sym][-_RSI_WINDOW:])
            if r is not None:
                rsi_by_symbol[sym] = r
            price_by_symbol[sym] = float(bar["close"])

        # 3) DECIDE (production function, verbatim), then the production in-flight filter.
        day_regime = _tag(regimes, spy_dates, day)
        decisions = rsi2.decide(owned_lots=owned_lots, cash=cash,
                                rsi_by_symbol=rsi_by_symbol,
                                price_by_symbol=price_by_symbol, cfg=cfg)
        in_flight = {d["symbol"] for d in pending}
        for d in decisions:
            if d["symbol"] in in_flight:
                continue
            pending.append({**d, "regime": day_regime})

        # 4) MARK equity at closes (last known close for symbols invisible today).
        mkt = sum(l["shares"] * last_close.get(l["symbol"], l["entry_price"])
                  for l in owned_lots)
        res.equity_curve.append({"date": day, "equity": cash + mkt, "regime": day_regime})

    res.open_positions = [
        {"symbol": l["symbol"], "shares": l["shares"], "entry_price": l["entry_price"],
         "entry_date": l["entry_date"],
         "last_close": last_close.get(l["symbol"], l["entry_price"]),
         "unrealized_pnl": (last_close.get(l["symbol"], l["entry_price"])
                            - l["entry_price"]) * l["shares"]}
        for l in owned_lots]
    res.unfilled_decisions = len(pending)
    res.stats = _build_stats(res, spy_bars, calendar, starting_cash)
    return res


def _block(trades: list) -> dict:
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    reasons: dict = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    return {"trades": n,
            "win_rate": len(wins) / n if n else 0.0,
            "expectancy": sum(t.pnl for t in trades) / n if n else 0.0,
            "expectancy_pct": sum(t.return_pct for t in trades) / n if n else 0.0,
            "avg_hold_days": sum(t.holding_days for t in trades) / n if n else 0.0,
            "exit_reasons": reasons}


def _build_stats(res: ReplayResult, spy_bars: list, calendar: list,
                 starting_cash: float) -> dict:
    per_symbol: dict = {}
    for t in res.trades:
        per_symbol.setdefault(t.symbol, []).append(t)
    final = res.equity_curve[-1]["equity"] if res.equity_curve else starting_cash
    peak, mdd = float("-inf"), 0.0
    for p in res.equity_curve:
        peak = max(peak, p["equity"])
        if peak > 0:
            mdd = min(mdd, (p["equity"] - peak) / peak * 100)
    days = set(calendar)
    spy_in = [float(b["close"]) for b in spy_bars
              if b.get("date") in days and b.get("close") is not None]
    return {
        "overall": _block(res.trades),
        "risk_on": _block([t for t in res.trades if t.regime == "risk_on"]),
        "risk_off": _block([t for t in res.trades if t.regime == "risk_off"]),
        # The warmup residual is NOT noise: with a span starting at the data cap it can hold a
        # whole market phase (e.g. the H1-2022 bear on a 2021-10 start) — surface it so the
        # regime split can't over-present (final review 2026-07-27).
        "warmup": _block([t for t in res.trades if t.regime == "warmup"]),
        "per_symbol": {s: _block(ts) for s, ts in sorted(per_symbol.items())},
        "total_return_pct": (final / starting_cash - 1) * 100 if starting_cash else 0.0,
        "max_drawdown_pct": mdd,
        "spy_buy_hold_pct": ((spy_in[-1] / spy_in[0] - 1) * 100) if len(spy_in) >= 2 else
                            (0.0 if len(spy_in) == 1 else None),
        "span": {"start": calendar[0] if calendar else "",
                 "end": calendar[-1] if calendar else "",
                 "trading_days": len(calendar)},
        "unfilled_decisions": res.unfilled_decisions,
    }
