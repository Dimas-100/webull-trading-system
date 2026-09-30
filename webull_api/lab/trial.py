"""StrategyTrial — the isolated forward-paper Gate-B book. PURE: no I/O, no order placement.
Fills route only through the reused backtest engine + strategy.cost helpers. Trials are constrained
to 1D/1W so EOD catch-up is faithful (appending bars one-at-a-time over K days yields a byte-identical
bars list — hence a byte-identical derived book — to appending all K at once)."""
from __future__ import annotations

import datetime as dt

from webull_api.lab.gate_a import NotEnoughData, sum_curves, _leaf_periods
from webull_api.lab.regime import compute_regime
from webull_api.lab.schema import (LAB_BASKET, DEFAULT_GATE_A_CONFIG, DEFAULT_LAB_CONFIG,
                                   ForwardRun, ProvingState, TrialBook, WalkForwardReport)
from webull_api.strategy.backtest import _triggers, compute_metrics, first_tradable_index
from webull_api.strategy.cost import (CostModel, NO_COST, commission, entry_fill, exit_fill,
                                      liquidity_ok, stop_fill)
from webull_api.strategy.predicates import _combined_held
from webull_api.strategy.schema import EquityPoint, Strategy, Trade


# ── time helpers (shared with append_confirmed in Task 20) ─────────────────────────────
def _parse_time(t) -> dt.datetime:
    """Parse a bar time to a comparable datetime. Handles ISO strings and epoch sec/ms strings."""
    s = str(t)
    if s.isdigit():
        v = int(s)
        if v > 10_000_000_000:        # epoch ms
            v //= 1000
        return dt.datetime.utcfromtimestamp(v)
    return dt.datetime.fromisoformat(s)


def _bar_date(bar: dict) -> str:
    """The bar's calendar date (YYYY-MM-DD) for the forming-session check."""
    return _parse_time(bar["time"]).date().isoformat()


# ── warmup ────────────────────────────────────────────────────────────────────────────
def _leaf_period(leaf) -> int:
    """Max indicator period for a single leaf node. Delegates to gate_a._leaf_periods so the
    warmup span is computed by ONE shared implementation across Gate A and Gate B."""
    return max(_leaf_periods(leaf), default=0)


def _node_period(node) -> int:
    if node is None:
        return 0
    if node.type in ("all_of", "any_of"):
        return max((_leaf_period(c) for c in node.conditions), default=0)
    return _leaf_period(node)


def warmup_for(strategy: Strategy) -> int:
    """Max indicator period across entry/exit/filter leaves + GateAConfig.warmup_buffer."""
    return max(_node_period(strategy.entry), _node_period(strategy.exit),
               _node_period(strategy.filter)) + DEFAULT_GATE_A_CONFIG.warmup_buffer


def _drop_forming(bars: list[dict], inception_et_date: str) -> list[dict]:
    """Drop the last bar ONLY when it is genuinely the still-forming session (its date == today)."""
    if bars and _bar_date(bars[-1]) == inception_et_date:
        return bars[:-1]
    return bars


def _forward_regime_bucket(basket: list[str], bars_by_symbol: dict[str, list[dict]]) -> str | None:
    """The market regime bucket as-of the latest confirmed bar of the trial's REFERENCE symbol
    (the first basket entry — the market proxy, mirroring lab_service.regime_now). compute_regime's
    trailing SMA/vol windows make this the regime the forward window is currently experiencing.
    None when the reference symbol has no bars."""
    ref = basket[0] if basket else None
    bars = bars_by_symbol.get(ref) if ref else None
    if not bars:
        return None
    return compute_regime(bars).bucket()


def _merge_regime_bucket(state: ProvingState) -> None:
    """Compute the current forward regime bucket and merge it (deduped) into regime_buckets_seen.
    This is the ONLY runtime writer of regime_buckets_seen — the field that distinguishes
    provisional_proven from confirmed_proven (>= confirmed_regime_buckets distinct buckets)."""
    bucket = _forward_regime_bucket(state.basket, state.bars_by_symbol)
    if bucket is not None and bucket not in state.regime_buckets_seen:
        state.regime_buckets_seen.append(bucket)


def open_trial(strategy: Strategy, confirmed_bars_by_symbol: dict[str, list[dict]], *,
               trial_id: str, now_iso: str, inception_et_date: str,
               gate_a: WalkForwardReport, basket: tuple[str, ...] | list[str] = LAB_BASKET,
               starting_equity: float = 10000.0, m: int = 0) -> ProvingState:
    """Open a forward-paper trial: drop any forming bar per symbol; forward_start = len(confirmed)
    (warmup feeds indicators only — the forward window starts empty and accrues via advance);
    require len >= warmup_for(strategy). status='proving'. Seeds the initial forward regime bucket.
    Freezes the lab-global multiple-testing count `m` as m_at_open so this trial's FORWARD DSR is
    judged against a FIXED penalty — a granted (*_proven) trial cannot demote to 'proving' just
    because OTHER candidates later grew the global M (DSR is monotone-decreasing in M); see
    verdict.evaluate_verdict."""
    w = warmup_for(strategy)
    bars_by_symbol: dict[str, list[dict]] = {}
    forward_start_by_symbol: dict[str, int] = {}
    for sym in basket:
        bars = _drop_forming(list(confirmed_bars_by_symbol.get(sym, [])), inception_et_date)
        if len(bars) < w:
            raise NotEnoughData(f"{sym}: {len(bars)} confirmed bars < warmup {w}")
        bars_by_symbol[sym] = bars
        forward_start_by_symbol[sym] = len(bars)
    seen: list[str] = []
    b0 = _forward_regime_bucket(list(basket), bars_by_symbol)
    if b0 is not None:
        seen.append(b0)
    return ProvingState(
        trial_id=trial_id, strategy=strategy, basket=list(basket),
        bars_by_symbol=bars_by_symbol, forward_start_by_symbol=forward_start_by_symbol,
        inception_et_date=inception_et_date, started_at_iso=now_iso,
        gate_a=gate_a, status="proving", bars_observed=0,
        regime_buckets_seen=seen, m_at_open=m)


# ── forward engine ────────────────────────────────────────────────────────────────────
def forward_run(strategy: Strategy, bars: list[dict], start_index: int, *,
                cost: CostModel, starting_equity: float = 10000.0) -> ForwardRun:
    """run_backtest's loop EXACTLY over bars[start_index:] (indicators over the FULL bars), MINUS the
    terminal force-close (an open position at the last confirmed bar stays open, marked to that close),
    WITH the cost model threaded in. NO_COST + start_index=0 reduces byte-for-byte to run_backtest's
    loop sans force-close (the M3 anchor)."""
    opens = [b["open"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    times = [b["time"] for b in bars]
    vols = [b.get("volume") for b in bars]
    n = len(bars)
    entry_trig = _triggers(strategy.entry, closes, highs, lows)
    exit_trig = _triggers(strategy.exit, closes, highs, lows)
    filt = _combined_held(strategy.filter, closes, highs, lows)   # all-True when filter is None

    cash = starting_equity
    shares = 0.0
    entry_price = 0.0
    entry_time = ""
    pending_entry = pending_exit = False
    trades: list[Trade] = []
    curve: list[EquityPoint] = []
    gap_slip = 0.0
    illiquid = 0

    def close_position(exit_price, exit_time, reason):
        nonlocal cash, shares, pending_exit
        # round-trip commission (2x, exactly like backtest.close_position) NETS into the reported
        # trade pnl; the CASH flow stays split (1x charged at entry above, 1x here) so the equity
        # curve is unchanged — only the per-trade pnl was gross-of-commission before.
        fee = 2 * commission(shares, cost)
        pnl = (exit_price - entry_price) * shares - fee
        ret = (exit_price / entry_price - 1) * 100 if entry_price else 0.0
        trades.append(Trade(entry_time=entry_time, entry_price=entry_price, exit_time=exit_time,
                            exit_price=exit_price, shares=shares, pnl=pnl, return_pct=ret,
                            exit_reason=reason))
        cash += shares * exit_price - commission(shares, cost)
        shares = 0.0
        # ANY close consumes/voids a queued exit — a pending_exit that survived a missing open must
        # never fire on a LATER position (mirrors backtest.close_position).
        pending_exit = False

    for i in range(start_index, n):
        o = opens[i]
        # 1) execute pending orders at this bar's open
        if pending_entry and shares == 0 and o:
            fill = entry_fill(o, cost)
            alloc = (cash * strategy.sizing.value / 100.0 if strategy.sizing.type == "pct_equity"
                     else min(strategy.sizing.value, cash))
            qty = int(alloc // fill) if fill > 0 else 0
            if qty > 0 and liquidity_ok(qty * fill, vols[i], fill, cost):
                shares, entry_price, entry_time = float(qty), fill, times[i]
                cash -= qty * fill + commission(float(qty), cost)
            elif qty > 0:
                illiquid += 1
            pending_entry = False
        if pending_exit and shares > 0 and o:
            close_position(exit_fill(o, cost), times[i], "signal")
            pending_exit = False

        # 2) intrabar stop (gap-through, cost-aware) then target (at the trigger price)
        if shares > 0:
            stop = entry_price * (1 - strategy.stop_loss_pct / 100.0) if strategy.stop_loss_pct else None
            target = entry_price * (1 + strategy.take_profit_pct / 100.0) if strategy.take_profit_pct else None
            if stop is not None and lows[i] is not None and lows[i] <= stop:
                fill = stop_fill(stop, o, cost)
                gap_slip += max(stop - fill, 0.0) * shares
                close_position(fill, times[i], "stop")
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

    # NB: NO terminal force-close — an open position is intentionally left open (Gate B forward book).
    # buy-&-hold spans the tradable window only (a no-op for real forward windows, whose
    # start_index >> warmup; keeps the start_index=0 anchor byte-identical to run_backtest).
    bh_start = max(start_index, first_tradable_index(strategy))
    result = compute_metrics(trades, curve, closes[bh_start:], starting_equity)
    return ForwardRun(result=result, forward_trades=len(trades), forward_bars=n - start_index,
                      gap_stop_slippage=gap_slip, illiquid_skips=illiquid)


def derive_book(strategy: Strategy, bars: list[dict], forward_start_index: int, *,
                cost: CostModel, starting_equity: float = 10000.0) -> TrialBook:
    """forward_run(...) reshaped into a single-symbol sub-book + compute_metrics. Never hand-patched."""
    fr = forward_run(strategy, bars, forward_start_index, cost=cost, starting_equity=starting_equity)
    return TrialBook(
        scope="single", starting_equity=starting_equity, equity_curve=fr.result.equity_curve,
        metrics=fr.result, trades_by_symbol={strategy.symbol: list(fr.result.trades)},
        per_symbol_metrics={strategy.symbol: fr.result},
        forward_trades=fr.forward_trades, forward_bars=fr.forward_bars,
        gap_stop_slippage=fr.gap_stop_slippage, illiquid_skips=fr.illiquid_skips)


def _retarget(strategy: Strategy, symbol: str) -> Strategy:
    """A copy of the frozen rule pointed at another basket symbol (so each sub-book is tagged)."""
    return strategy.model_copy(update={"symbol": symbol})


def derive_portfolio(strategy: Strategy, bars_by_symbol: dict[str, list[dict]],
                     starts_by_symbol: dict[str, int], *, cost: CostModel,
                     starting_equity: float = 10000.0) -> TrialBook:
    """Run derive_book per basket symbol on starting_equity/N; SUM the sub-book equity curves into
    the portfolio curve; POOL per-symbol trades (symbol-tagged) into the trial ledger. THE sole
    producer of the trial's reported book/curve."""
    syms = list(bars_by_symbol.keys())
    n = max(len(syms), 1)
    per_equity = starting_equity / n
    subs: dict[str, TrialBook] = {}
    for sym in syms:
        subs[sym] = derive_book(_retarget(strategy, sym), bars_by_symbol[sym],
                                starts_by_symbol.get(sym, 0), cost=cost, starting_equity=per_equity)
    curve = sum_curves([subs[s].equity_curve for s in syms], per_equity)   # the shared gate-A/B helper
    pooled = [t for s in syms for t in subs[s].metrics.trades]
    base = compute_metrics(pooled, curve, [], starting_equity)          # buy_hold overridden below
    bh = sum(subs[s].metrics.buy_hold_return_pct for s in syms) / n if syms else 0.0
    metrics = base.model_copy(update={"buy_hold_return_pct": bh})
    return TrialBook(
        scope="portfolio", starting_equity=starting_equity, equity_curve=curve, metrics=metrics,
        trades_by_symbol={s: list(subs[s].metrics.trades) for s in syms},
        per_symbol_metrics={s: subs[s].metrics for s in syms},
        forward_trades=len(pooled),
        forward_bars=max((subs[s].forward_bars for s in syms), default=0),
        gap_stop_slippage=sum(subs[s].gap_stop_slippage for s in syms),
        illiquid_skips=sum(subs[s].illiquid_skips for s in syms))


# ── append_confirmed (Task 20) ─────────────────────────────────────────────────────────
_SEAM_TOL = 0.005   # >0.5% drift on an overlap close signals a split/dividend re-statement (RT-14)


class CorporateActionSeam(Exception):
    """A re-fetched overlap bar's price no longer matches the stored bar — the symbol's history was
    restated (split/dividend). The trial must repair before appending, never silently splice."""


def append_confirmed(bars: list[dict], fresh: list[dict], *, today_et: str) -> list[dict]:
    """Return the genuinely-new confirmed bars to append. Drops the forming bar ONLY when it is
    today's still-open session (RT-15); asserts strict-monotonic parsed times and raises loudly on a
    non-monotonic batch (RT-16); re-checks the overlap region and raises CorporateActionSeam on a
    restated price (RT-14). Idempotent: returns [] when nothing has closed past what we hold."""
    if not fresh:
        return []
    confirmed = list(fresh)
    if _bar_date(confirmed[-1]) == today_et:        # genuine forming session -> drop it
        confirmed = confirmed[:-1]
    if not confirmed:
        return []

    parsed = [_parse_time(b["time"]) for b in confirmed]
    for a, b in zip(parsed, parsed[1:]):
        if not (b > a):
            raise ValueError(f"non-monotonic bar times: {a.isoformat()} !< {b.isoformat()}")

    stored = {_parse_time(b["time"]): b for b in bars}
    for b, pt in zip(confirmed, parsed):
        prev = stored.get(pt)
        if prev is not None:
            denom = max(abs(prev["close"]), 1e-9)
            if abs(prev["close"] - b["close"]) / denom > _SEAM_TOL:
                raise CorporateActionSeam(
                    f"overlap close mismatch at {pt.isoformat()}: stored {prev['close']} vs fresh {b['close']}")

    last_t = _parse_time(bars[-1]["time"]) if bars else None
    return [b for b, pt in zip(confirmed, parsed) if last_t is None or pt > last_t]


# ── advance (Task 21) ─────────────────────────────────────────────────────────────────
def advance(state: ProvingState, fresh_bars_by_symbol: dict[str, list[dict]], *,
            now_iso: str, today_et: str, cost: CostModel) -> tuple[ProvingState, int]:
    """Per basket symbol: append_confirmed; a corp-action seam records a coverage_gap and skips that
    symbol's append (needs repair). If NO symbol advanced -> idempotent no-op (state unchanged, 0).
    Else re-derive the PORTFOLIO book + update bars_observed/last_advance_iso. Verdict evaluation is
    the orchestrator's job (it calls evaluate_verdict after)."""
    n_appended = 0
    for sym in state.basket:
        fresh = fresh_bars_by_symbol.get(sym)
        if not fresh:
            continue
        existing = state.bars_by_symbol.get(sym, [])
        try:
            new = append_confirmed(existing, fresh, today_et=today_et)
        except CorporateActionSeam as e:
            state.coverage_gaps.append({"symbol": sym, "type": "corp_action_seam",
                                        "detail": str(e)[:160], "at_iso": now_iso})
            continue
        if new:
            state.bars_by_symbol[sym] = existing + new
            n_appended += len(new)

    if n_appended == 0:
        return state, 0

    state.book = derive_portfolio(state.strategy, state.bars_by_symbol, state.forward_start_by_symbol,
                                  cost=cost, starting_equity=DEFAULT_LAB_CONFIG.starting_equity)
    state.bars_observed = state.book.forward_bars
    _merge_regime_bucket(state)            # accrue the forward regime bucket (confirmed_proven driver)
    state.last_advance_iso = now_iso
    return state, n_appended
