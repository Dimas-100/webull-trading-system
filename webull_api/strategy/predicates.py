"""Per-bar STATE predicate layer for the DSL. `_held3` answers 'is this condition currently true at
bar i' as a THREE-VALUED state — True / False / None (None = the indicator is undefined at bar i:
warmup or a data gap) — for ANY leaf type, legacy leaves included, so composites and the regime
filter can mix legacy + new leaves. `_combined_held3` AND/OR-s a composite with Kleene logic
(all-True for None); `_held`/`_combined_held` are the two-valued projections (True only when
defined-and-True — what the regime filter consumes). `_edge` turns a state list into a rising-edge
EVENT list and requires the prior state to be DEFINED-and-False: an undefined prior bar never counts
as False, so the first defined bar of an indicator can't fire a phantom entry (matching the legacy
single-leaf crossing semantics, which require BOTH bars' indicator values). The legacy single-leaf
`_triggers` path in backtest is untouched — this layer only powers the composite/filter/new-leaf paths."""
from __future__ import annotations

from .indicators import atr, ema, ibs, macd as macd_lines, rolling_high, rolling_low, rsi, sma, zscore


def _held3(cond, closes, highs, lows):
    """Three-valued per-bar state: True / False / None (undefined — warmup or a data gap)."""
    n = len(closes)
    out = [None] * n
    if cond is None:
        return out
    t = cond.type
    if t in ("sma_cross", "ema_cross"):
        fn = sma if t == "sma_cross" else ema
        fast, slow = fn(closes, cond.fast), fn(closes, cond.slow)
        for i in range(n):
            if fast[i] is None or slow[i] is None:
                continue
            out[i] = fast[i] > slow[i] if cond.direction == "above" else fast[i] < slow[i]
    elif t == "rsi":
        r = rsi(closes, cond.period)
        for i in range(n):
            if r[i] is None:
                continue
            out[i] = r[i] < cond.threshold if cond.comparison == "below" else r[i] > cond.threshold
    elif t == "breakout":
        hi, lo = rolling_high(highs, cond.lookback), rolling_low(lows, cond.lookback)
        # STATE = close vs the PRIOR bar's rolling extreme (window ending at i-1), mirroring the
        # legacy event semantics in backtest._triggers. A window including bar i would compare
        # close[i] to high[i] (>= close[i] almost always) — the state would ~never be true and a
        # breakout filter would silently block everything.
        for i in range(1, n):
            if closes[i] is None:
                continue
            if cond.direction == "high" and hi[i - 1] is not None:
                out[i] = closes[i] > hi[i - 1]
            elif cond.direction == "low" and lo[i - 1] is not None:
                out[i] = closes[i] < lo[i - 1]
    elif t == "price_vs_sma":
        s = sma(closes, cond.period)
        for i in range(n):
            if closes[i] is None or s[i] is None:
                continue
            out[i] = closes[i] > s[i] if cond.side == "above" else closes[i] < s[i]
    elif t == "macd":
        lines = macd_lines(closes, cond.fast, cond.slow, cond.signal)
        line, sig = lines["macd"], lines["signal"]
        for i in range(n):
            if line[i] is None:
                continue
            if cond.ref == "signal":
                if sig[i] is None:
                    continue
                ref = sig[i]
            else:
                ref = 0.0
            out[i] = line[i] > ref if cond.direction == "above" else line[i] < ref
    elif t == "atr_pct":
        a = atr(highs, lows, closes, cond.period)
        for i in range(n):
            if a[i] is None or closes[i] in (None, 0):
                continue
            pct = a[i] / closes[i] * 100.0
            out[i] = pct > cond.level if cond.side == "above" else pct < cond.level
    elif t == "ibs":
        vals = ibs(highs, lows, closes)
        for i in range(n):
            if vals[i] is None:
                continue
            out[i] = vals[i] < cond.level if cond.side == "below" else vals[i] > cond.level
    elif t == "zscore":
        z = zscore(closes, cond.period)
        for i in range(n):
            if z[i] is None:
                continue
            out[i] = (z[i] < cond.threshold if cond.comparison == "below"
                      else z[i] > cond.threshold)
    elif t == "drop_from_high":
        # Prior-bar window (hi[i-1]), mirroring breakout: no lookahead, and the drop is measured
        # against the high BEFORE today's bar.
        hi = rolling_high(highs, cond.lookback)
        for i in range(1, n):
            if closes[i] is None or hi[i - 1] in (None, 0):
                continue
            out[i] = closes[i] <= hi[i - 1] * (1.0 - cond.pct / 100.0)
    elif t == "consec_down":
        # A missing close leaves the state undefined (None) and restarts the run — a gap can
        # neither extend nor fire a down-streak.
        run = 0
        for i in range(1, n):
            if closes[i] is None or closes[i - 1] is None:
                run = 0
                continue
            run = run + 1 if closes[i] < closes[i - 1] else 0
            out[i] = run >= cond.count
    return out


def _held(cond, closes, highs, lows):
    """Two-valued projection of `_held3`: True only when defined-and-True (undefined -> False)."""
    return [v is True for v in _held3(cond, closes, highs, lows)]


def _combined_held3(node, closes, highs, lows):
    """Kleene AND/OR over child states: all_of is False when ANY child is defined-False, True only
    when ALL are defined-True, else None (undefined); any_of is True when ANY child is defined-True,
    False only when ALL are defined-False, else None. None node -> all defined-True (no condition).
    Guarantees all_of([leaf]) ≡ leaf, definedness included — the composite-vs-bare parity anchor."""
    n = len(closes)
    if node is None:
        return [True] * n
    if getattr(node, "type", None) not in ("all_of", "any_of"):
        return _held3(node, closes, highs, lows)
    held_lists = [_held3(c, closes, highs, lows) for c in node.conditions]
    out = [None] * n
    for i in range(n):
        vals = [hl[i] for hl in held_lists]
        if node.type == "all_of":
            if any(v is False for v in vals):
                out[i] = False
            elif all(v is True for v in vals):
                out[i] = True
        else:
            if any(v is True for v in vals):
                out[i] = True
            elif all(v is False for v in vals):
                out[i] = False
    return out


def _combined_held(node, closes, highs, lows):
    """Two-valued projection of `_combined_held3` (undefined -> False). The regime-filter consumer:
    an entry stays blocked while the filter is undefined, exactly as before."""
    return [v is True for v in _combined_held3(node, closes, highs, lows)]


def _edge(held):
    """STATE -> rising-edge EVENT. An edge requires the PRIOR state to be DEFINED-and-False:
    None (undefined) never counts as False, so an indicator's first defined bar — or the first
    defined bar after a data-gap reset — cannot fire a phantom entry. On plain bool lists this is
    byte-identical to the old `held[i] and not held[i-1]`."""
    out = [False] * len(held)
    for i in range(1, len(held)):
        out[i] = held[i] is True and held[i - 1] is False
    return out
