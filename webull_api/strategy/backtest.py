"""Deterministic, long-only backtest. Signals evaluate at a bar's close and fill at the NEXT
bar's open (no look-ahead); stop/target fill intrabar at the trigger price (stop before target)."""
from __future__ import annotations

from .cost import CostModel, commission, entry_fill, exit_fill, liquidity_ok, stop_fill
from .indicators import ema, rolling_high, rolling_low, rsi, sma
from .predicates import _combined_held, _combined_held3, _edge, _held3
from .schema import BacktestResult, EquityPoint, Strategy, Trade


def _max_leaf_period(node) -> int:
    """Largest indicator period referenced by a leaf/composite (0 for None)."""
    if node is None:
        return 0
    t = getattr(node, "type", None)
    if t in ("all_of", "any_of"):
        return max((_max_leaf_period(c) for c in node.conditions), default=0)
    if t in ("sma_cross", "ema_cross"):
        return node.slow
    if t == "rsi":
        return node.period
    if t == "breakout":
        return node.lookback
    if t == "price_vs_sma":
        return node.period
    if t == "macd":
        return max(node.slow, node.signal)
    if t == "atr_pct":
        return node.period
    if t == "zscore":
        return node.period
    if t == "drop_from_high":
        return node.lookback
    if t == "consec_down":
        return node.count
    if t == "ibs":
        return 1
    return 0


def first_tradable_index(strategy) -> int:
    """First bar index at which the ENTRY could possibly fire (max indicator period across the
    entry + the entry-gating filter; the exit doesn't gate the FIRST trade). Buy-&-hold is measured
    from here so the benchmark spans only the window the strategy could actually trade — a
    full-window B&H silently included the indicator-warmup dead zone. (The remaining one-bar
    close-vs-next-open convention difference is a deliberate simplification: B&H enters at the close
    of the first signal-evaluable bar.)"""
    return max(_max_leaf_period(strategy.entry), _max_leaf_period(strategy.filter))


def _triggers(cond, closes, highs, lows):
    """Bool list: did `cond` fire (a fresh rising edge) at bar i, evaluated at the close.
    Legacy single-leaf path is BYTE-IDENTICAL; new leaves + composites route through predicates."""
    n = len(closes)
    trig = [False] * n
    if cond is None:
        return trig
    t = cond.type
    if t in ("sma_cross", "ema_cross"):
        fn = sma if t == "sma_cross" else ema
        fast, slow = fn(closes, cond.fast), fn(closes, cond.slow)
        for i in range(1, n):
            if None in (fast[i], slow[i], fast[i - 1], slow[i - 1]):
                continue
            if cond.direction == "above":
                trig[i] = fast[i] > slow[i] and fast[i - 1] <= slow[i - 1]
            else:
                trig[i] = fast[i] < slow[i] and fast[i - 1] >= slow[i - 1]
    elif t == "rsi":
        r = rsi(closes, cond.period)
        for i in range(1, n):
            if r[i] is None or r[i - 1] is None:
                continue
            if cond.comparison == "below":
                trig[i] = r[i] < cond.threshold and r[i - 1] >= cond.threshold
            else:
                trig[i] = r[i] > cond.threshold and r[i - 1] <= cond.threshold
    elif t == "breakout":
        hi, lo = rolling_high(highs, cond.lookback), rolling_low(lows, cond.lookback)
        for i in range(1, n):
            if closes[i] is None or closes[i - 1] is None:
                continue
            if cond.direction == "high" and hi[i - 1] is not None:
                trig[i] = closes[i] > hi[i - 1] and closes[i - 1] <= hi[i - 1]
            elif cond.direction == "low" and lo[i - 1] is not None:
                trig[i] = closes[i] < lo[i - 1] and closes[i - 1] >= lo[i - 1]
    elif t in ("all_of", "any_of"):
        # three-valued states: an edge needs a DEFINED-and-False prior bar, so a composite can't
        # fire a phantom entry at the warmup boundary (parity with the legacy single-leaf paths,
        # which all require BOTH bars' indicator values).
        return _edge(_combined_held3(cond, closes, highs, lows))
    else:  # state leaves (price_vs_sma / macd / atr_pct / ibs / zscore / drop_from_high / consec_down)
        return _edge(_held3(cond, closes, highs, lows))
    return trig


def compute_metrics(trades, equity_curve, closes, starting_equity):
    """Build a BacktestResult from realized trades + a mark-to-market equity curve.
    Shared by run_backtest and the replay finalizer so both report identically. `closes` should be
    the TRADABLE window's closes (callers slice off the indicator warmup / pre-forward lead-in) —
    it feeds only the buy-&-hold benchmark, which must span the same window the strategy could trade."""
    start = starting_equity
    final = equity_curve[-1].equity if equity_curve else start
    first_close = next((x for x in closes if x is not None), None)
    last_close = next((x for x in reversed(closes) if x is not None), None)
    bh = (last_close / first_close - 1) * 100 if first_close else 0.0
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    peak = float("-inf")
    mdd = 0.0
    for p in equity_curve:
        peak = max(peak, p.equity)
        if peak > 0:
            mdd = min(mdd, (p.equity - peak) / peak * 100)
    return BacktestResult(
        total_return_pct=(final / start - 1) * 100 if start else 0.0,
        buy_hold_return_pct=bh,
        num_trades=len(trades),
        win_rate=len(wins) / len(trades) * 100 if trades else 0.0,
        avg_win_pct=sum(t.return_pct for t in wins) / len(wins) if wins else 0.0,
        avg_loss_pct=sum(t.return_pct for t in losses) / len(losses) if losses else 0.0,
        expectancy=sum(t.pnl for t in trades) / len(trades) if trades else 0.0,
        max_drawdown_pct=mdd,
        equity_curve=equity_curve,
        trades=trades,
    )


def run_backtest(strategy: Strategy, bars: list[dict], slippage_pct: float = 0.0, *,
                 cost: CostModel | None = None) -> BacktestResult:
    times = [b["time"] for b in bars]
    opens = [b["open"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    vols = [b.get("volume") for b in bars]
    n = len(bars)
    entry_trig = _triggers(strategy.entry, closes, highs, lows)
    exit_trig = _triggers(strategy.exit, closes, highs, lows)
    filt = _combined_held(strategy.filter, closes, highs, lows)   # all-True when filter is None
    slip = slippage_pct / 100.0

    cash = strategy.starting_equity
    shares = 0.0
    entry_price = 0.0
    entry_time = ""
    pending_entry = pending_exit = False
    trades: list[Trade] = []
    curve: list[EquityPoint] = []

    def close_position(exit_price, exit_time, reason):
        nonlocal cash, shares, pending_exit
        # round-trip commission = 2 * commission(shares): entry+exit share counts are equal on a
        # full close, so charging both here (net into pnl + cash) keeps the equity curve honest.
        fee = 2 * commission(shares, cost) if cost is not None else 0.0
        pnl = (exit_price - entry_price) * shares - fee
        ret = (exit_price / entry_price - 1) * 100 if entry_price else 0.0
        trades.append(Trade(entry_time=entry_time, entry_price=entry_price, exit_time=exit_time,
                            exit_price=exit_price, shares=shares, pnl=pnl, return_pct=ret,
                            exit_reason=reason))
        cash += shares * exit_price - fee
        shares = 0.0
        # ANY close consumes/voids a queued exit — a pending_exit that survived a missing open must
        # never fire on a LATER position (e.g. a fresh entry after a stop-out).
        pending_exit = False

    for i in range(n):
        o = opens[i]
        # 1) execute pending orders at this bar's open
        if pending_entry and shares == 0 and o:
            fill = entry_fill(o, cost) if cost is not None else o * (1 + slip)
            alloc = cash * strategy.sizing.value / 100.0 if strategy.sizing.type == "pct_equity" else min(strategy.sizing.value, cash)
            qty = int(alloc // fill) if fill > 0 else 0
            if qty > 0 and (cost is None or liquidity_ok(qty * fill, vols[i], fill, cost)):
                shares, entry_price, entry_time = float(qty), fill, times[i]
                cash -= qty * fill
            pending_entry = False
        if pending_exit and shares > 0 and o:
            ep = exit_fill(o, cost) if cost is not None else o * (1 - slip)
            close_position(ep, times[i], "signal")
            pending_exit = False

        # 2) intrabar stop/target (stop checked first — conservative)
        if shares > 0:
            stop = entry_price * (1 - strategy.stop_loss_pct / 100.0) if strategy.stop_loss_pct else None
            target = entry_price * (1 + strategy.take_profit_pct / 100.0) if strategy.take_profit_pct else None
            if stop is not None and lows[i] is not None and lows[i] <= stop:
                fill_px = stop_fill(stop, o, cost) if cost is not None else stop
                close_position(fill_px, times[i], "stop")
            elif target is not None and highs[i] is not None and highs[i] >= target:
                close_position(target, times[i], "target")

        # 3) mark-to-market at close
        c = closes[i] if closes[i] is not None else entry_price
        curve.append(EquityPoint(time=times[i], equity=cash + shares * c))

        # 4) evaluate signals at close for NEXT-bar execution (entry gated by the regime filter)
        if shares == 0 and entry_trig[i] and filt[i]:
            pending_entry = True
        elif shares > 0 and strategy.exit is not None and exit_trig[i]:
            pending_exit = True

    if shares > 0:
        # a None final close (degraded bar) must never raise — fall back to the last known close
        last_close = next((x for x in reversed(closes) if x is not None), entry_price)
        close_position(last_close, times[-1], "end_of_data")
        if curve:
            curve[-1] = EquityPoint(time=times[-1], equity=cash)

    # buy-&-hold is measured from the first bar the strategy could actually trade (end of the
    # indicator warmup), not the full window — see first_tradable_index.
    return compute_metrics(trades, curve, closes[first_tradable_index(strategy):],
                           strategy.starting_equity)
