"""Pure, resumable replay stepper for the practice drill. Same engine semantics as run_backtest
(next-open fills, intrabar stop/target stop-before-target, mark-to-market, no look-ahead) — but an
entry pauses for a Take/Skip decision instead of auto-executing. State is fully serializable so a
session persists and resumes deterministically."""
from __future__ import annotations

import random
from typing import Literal

from pydantic import BaseModel, Field

from .backtest import compute_metrics, first_tradable_index, run_backtest, _triggers
from .cost import commission, entry_fill, exit_fill, liquidity_ok, stop_fill
from .predicates import _combined_held
from .schema import BacktestResult, EquityPoint, Strategy, Trade
from webull_api.journal.schema import ThesisRecord


class Decision(BaseModel):
    bar_index: int
    why: str
    choice: Literal["take", "skip"]
    thesis: ThesisRecord | None = None


class DecisionPoint(BaseModel):
    bar_index: int
    why: str
    price: float | None


class ReplayState(BaseModel):
    strategy: Strategy
    symbol: str
    window_bars: list[dict]
    cursor: int = 0
    cash: float = 0.0
    shares: float = 0.0
    entry_price: float = 0.0
    entry_time: str = ""
    pending_entry: bool = False
    pending_exit: bool = False
    decisions: list[Decision] = Field(default_factory=list)
    user_trades: list[Trade] = Field(default_factory=list)
    user_equity: list[EquityPoint] = Field(default_factory=list)
    status: Literal["in_progress", "finished"] = "in_progress"
    mode: Literal["replay", "live"] = "replay"
    live_start_index: int = 0


class ReplayResult(BaseModel):
    user: BacktestResult
    benchmark: BacktestResult
    taken: int
    skipped: int
    adherence_pct: float
    decisions: list[Decision]
    revealed_window: list[dict]


def explain_signal(cond) -> str:
    t = cond.type
    if t == "sma_cross":
        return f"The {cond.fast}-period SMA crossed {cond.direction} the {cond.slow}-period SMA — your entry rule."
    if t == "ema_cross":
        return f"The {cond.fast}-period EMA crossed {cond.direction} the {cond.slow}-period EMA — your entry rule."
    if t == "rsi":
        return f"RSI({cond.period}) crossed {cond.comparison} {cond.threshold} — your entry rule."
    if t == "breakout":
        return f"Price broke the {cond.lookback}-bar {cond.direction} — your entry rule."
    return "Your entry signal fired."


def pick_window(all_bars, length, warmup, seed=None):
    need = length + warmup
    if len(all_bars) < need:
        raise ValueError(f"need {need} bars, have {len(all_bars)}")
    rng = random.Random(seed)
    start = rng.randint(0, len(all_bars) - need)
    return all_bars[start:start + need]


def start_replay(strategy: Strategy, window_bars: list[dict]) -> ReplayState:
    return ReplayState(strategy=strategy, symbol=strategy.symbol, window_bars=window_bars,
                       cash=strategy.starting_equity)


def _last_known_close(bars: list[dict]):
    """The most recent non-None close in the window (None only if every close is missing)."""
    return next((b["close"] for b in reversed(bars) if b["close"] is not None), None)


def _close(state: ReplayState, exit_price, exit_time, reason, cost=None):
    fee = 2 * commission(state.shares, cost) if cost is not None else 0.0
    pnl = (exit_price - state.entry_price) * state.shares - fee
    ret = (exit_price / state.entry_price - 1) * 100 if state.entry_price else 0.0
    state.user_trades.append(Trade(entry_time=state.entry_time, entry_price=state.entry_price,
                                   exit_time=exit_time, exit_price=exit_price, shares=state.shares,
                                   pnl=pnl, return_pct=ret, exit_reason=reason))
    state.cash += state.shares * exit_price - fee
    state.shares = 0.0
    # ANY close consumes/voids a queued exit — a pending_exit that survived a missing open must
    # never fire on a LATER position (mirrors backtest.close_position).
    state.pending_exit = False


def step_to_decision(state: ReplayState, mark_finished_on_end: bool = True, *,
                     slippage_pct: float = 0.0, cost=None):
    """Advance until the next entry-while-flat signal (pause, return its DecisionPoint) or the end
    of the window (status -> finished, return None). Entry is gated by the regime filter; the exit
    is never filter-gated. Defaults => byte-identical to the legacy stepper."""
    bars = state.window_bars
    n = len(bars)
    opens = [b["open"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    times = [b["time"] for b in bars]
    strat = state.strategy
    entry_trig = _triggers(strat.entry, closes, highs, lows)
    exit_trig = _triggers(strat.exit, closes, highs, lows)
    filt = _combined_held(strat.filter, closes, highs, lows)

    while state.cursor < n:
        i = state.cursor
        o = opens[i]
        # 1) pending fills at this bar's open
        if state.pending_entry and state.shares == 0 and o:
            fill = entry_fill(o, cost) if cost is not None else o * (1 + slippage_pct / 100.0)
            alloc = state.cash * strat.sizing.value / 100.0 if strat.sizing.type == "pct_equity" else min(strat.sizing.value, state.cash)
            qty = int(alloc // fill) if fill > 0 else 0
            if qty > 0 and (cost is None or liquidity_ok(qty * fill, bars[i].get("volume"), fill, cost)):
                state.shares, state.entry_price, state.entry_time = float(qty), fill, times[i]
                state.cash -= qty * fill
            state.pending_entry = False
        if state.pending_exit and state.shares > 0 and o:
            ep = exit_fill(o, cost) if cost is not None else o * (1 - slippage_pct / 100.0)
            _close(state, ep, times[i], "signal", cost)
            state.pending_exit = False
        # 2) intrabar stop/target (stop first)
        if state.shares > 0:
            stop = state.entry_price * (1 - strat.stop_loss_pct / 100.0) if strat.stop_loss_pct else None
            target = state.entry_price * (1 + strat.take_profit_pct / 100.0) if strat.take_profit_pct else None
            if stop is not None and lows[i] is not None and lows[i] <= stop:
                fill_px = stop_fill(stop, o, cost) if cost is not None else stop
                _close(state, fill_px, times[i], "stop", cost)
            elif target is not None and highs[i] is not None and highs[i] >= target:
                _close(state, target, times[i], "target", cost)
        # 3) mark-to-market at close
        c = closes[i] if closes[i] is not None else state.entry_price
        state.user_equity.append(EquityPoint(time=times[i], equity=state.cash + state.shares * c))
        # 4) signal eval (entry gated by the regime filter; exit never gated)
        if state.shares == 0 and entry_trig[i] and filt[i]:
            state.cursor = i + 1
            return state, DecisionPoint(bar_index=i, why=explain_signal(strat.entry), price=closes[i])
        if state.shares > 0 and strat.exit is not None and exit_trig[i]:
            state.pending_exit = True
        state.cursor = i + 1

    if mark_finished_on_end:
        state.status = "finished"
    return state, None


def apply_decision(state: ReplayState, take: bool, thesis: ThesisRecord | None = None) -> ReplayState:
    """Record the decision for the bar we just paused on; if taken, arm the next-open entry fill."""
    bar = state.cursor - 1
    state.decisions.append(Decision(bar_index=bar, why=explain_signal(state.strategy.entry),
                                    choice="take" if take else "skip", thesis=thesis))
    if take:
        state.pending_entry = True
    return state


def finalize(state: ReplayState) -> ReplayResult:
    # Drain ALL remaining bars before computing metrics (a loop, not one step: an early finalize
    # with several pending signals must reach the end, treating undecided signals as skips).
    while state.status != "finished":
        state, _ = step_to_decision(state)
    # A pending entry that reached end-of-data in a live session never got a next bar to fill at, so
    # settle it at the last bar's CLOSE (the only knowable post-signal price; avoids a look-ahead
    # inversion vs the bar's open, and the immediate end_of_data close below makes it a clean wash).
    if state.pending_entry and state.mode == "live" and state.window_bars:
        last = state.window_bars[-1]
        # a None final close (degraded bar) must never raise — settle at the last known close
        o = last["close"] if last["close"] is not None else _last_known_close(state.window_bars)
        alloc = state.cash * state.strategy.sizing.value / 100.0 if state.strategy.sizing.type == "pct_equity" else min(state.strategy.sizing.value, state.cash)
        qty = int(alloc // o) if o and o > 0 else 0
        if qty > 0:
            state.shares, state.entry_price, state.entry_time = float(qty), o, last["time"]
            state.cash -= qty * o
        state.pending_entry = False
    if state.shares > 0:
        last = state.window_bars[-1]
        px = last["close"] if last["close"] is not None else _last_known_close(state.window_bars)
        _close(state, px if px is not None else state.entry_price, last["time"], "end_of_data")
        if state.user_equity:
            state.user_equity[-1] = EquityPoint(time=last["time"], equity=state.cash)
    state.status = "finished"
    closes = [b["close"] for b in state.window_bars]
    # like the backtest: the buy-&-hold window starts where the strategy could actually trade
    bh_start = max(state.live_start_index if state.mode == "live" else 0,
                   first_tradable_index(state.strategy))
    metric_closes = closes[bh_start:]
    user = compute_metrics(state.user_trades, state.user_equity, metric_closes, state.strategy.starting_equity)
    benchmark = (strict_run(state.strategy, state.window_bars, state.live_start_index)
                 if state.mode == "live" else run_backtest(state.strategy, state.window_bars))
    taken = sum(1 for d in state.decisions if d.choice == "take")
    total = len(state.decisions)
    return ReplayResult(
        user=user, benchmark=benchmark, taken=taken, skipped=total - taken,
        adherence_pct=(taken / total * 100) if total else 0.0,
        decisions=state.decisions, revealed_window=state.window_bars,
    )


def masked_chart(state: ReplayState):
    """Bars processed so far, with calendar dates hidden (Day 1..N) for the blind drill."""
    return [{"t": f"Day {i + 1}", "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]}
            for i, b in enumerate(state.window_bars[:state.cursor])]


def start_live(strategy: Strategy, seed_bars: list[dict]) -> ReplayState:
    """A live drill: seed bars give valid indicators, but the drill starts at 'now' (cursor past the
    seed), so the seed's past signals are never surfaced."""
    s = start_replay(strategy, seed_bars)
    s.mode = "live"
    s.cursor = len(seed_bars)
    s.live_start_index = len(seed_bars)
    return s


def append_live_bars(state: ReplayState, fresh_bars: list[dict]) -> int:
    """Append newly-CONFIRMED bars: drop the still-forming last bar, keep only bars newer than the
    last one we hold. Returns the count appended; idempotent when nothing new has closed."""
    confirmed = fresh_bars[:-1] if fresh_bars else []
    last_t = state.window_bars[-1]["time"] if state.window_bars else ""
    new = [b for b in confirmed if b["time"] > last_t]
    state.window_bars.extend(new)
    return len(new)


def strict_run(strategy: Strategy, bars: list[dict], start_index: int = 0, *,
               slippage_pct: float = 0.0, cost=None) -> BacktestResult:
    """The rules-perfect run over bars[start_index:] (indicators use the full lead-in). For
    start_index=0 over a full window this equals run_backtest (the equivalence anchor)."""
    s = start_replay(strategy, bars)
    s.cursor = start_index
    while True:
        s, dp = step_to_decision(s, slippage_pct=slippage_pct, cost=cost)
        if dp is None:
            break
        s = apply_decision(s, True)
    if s.shares > 0:
        last = bars[-1]
        # a None final close (degraded bar) must never raise — fall back to the last known close
        px = last["close"] if last["close"] is not None else _last_known_close(bars)
        _close(s, px if px is not None else s.entry_price, last["time"], "end_of_data", cost)
        if s.user_equity:
            s.user_equity[-1] = EquityPoint(time=last["time"], equity=s.cash)
    # buy-&-hold spans the tradable window only (mirrors run_backtest; a no-op for the Gate-A/B
    # callers, whose start_index already covers the indicator warmup).
    bh_start = max(start_index, first_tradable_index(strategy))
    closes = [b["close"] for b in bars][bh_start:]
    return compute_metrics(s.user_trades, s.user_equity, closes, strategy.starting_equity)


def live_chart(state: ReplayState):
    """Bars seen so far with REAL dates (a live drill is not blind)."""
    return [{"t": b["time"], "open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"]}
            for b in state.window_bars[:state.cursor]]
