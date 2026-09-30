"""Depth lowers the deflated-Sharpe hurdle (min_lookback_bars raised well above the 750-bar
default); it must not let that extra depth let pure noise through Gate A. Seeded random-walk daily
bars, three archetypal strategies (trend-follow cross, mean-reversion RSI(2), breakout), m=1 ->
every strategy must FAIL Gate A. Deterministic (seeded RNG) and hermetic (no I/O, no network).

Spec called for 5,000 bars / 10 symbols; that combination ran in ~150s (over the ~60s budget), so
this was reduced to 3,000 bars / 6 symbols -- see the runtime note in the task report. A second
test below probes the breakout archetype alone at 5,000 bars / 6 symbols (~38s) to keep the
DSR-alone-is-a-weak-filter finding under test without paying for all three strategies at depth."""
from __future__ import annotations

import dataclasses
import datetime as dt
import random

from webull_api.lab import gate_a
from webull_api.lab.schema import DEFAULT_GATE_A_CONFIG
from webull_api.strategy.cost import NO_COST
from webull_api.strategy.schema import Breakout, RsiCond, SmaCross, Strategy

# NOTE: spec called for 5,000 bars / 10 symbols; that combination took ~150s (over the ~60s
# budget), so this was reduced to 3,000 bars / 6 symbols per the task's runtime rule.
N_BARS = 3000
N_SYMBOLS = 6


def _walk(seed: int, n: int = N_BARS, start: str = "2006-01-02") -> list[dict]:
    """A plain geometric random walk: small daily Gaussian noise on the close, an independent small
    intraday range around the open/close for high/low, weekdays only, strictly positive prices, and
    valid OHLC ordering (low <= min(open, close) <= max(open, close) <= high)."""
    rng = random.Random(seed)
    px = 100.0
    d = dt.date.fromisoformat(start)
    out: list[dict] = []
    while len(out) < n:
        d += dt.timedelta(days=1)
        if d.weekday() >= 5:      # Sat/Sun
            continue
        o = px
        c = max(1.0, o * (1 + rng.gauss(0, 0.012)))
        hi = max(o, c) * (1 + abs(rng.gauss(0, 0.004)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.004)))
        lo = max(0.01, min(lo, min(o, c)))     # guarantee low <= min(open, close)
        hi = max(hi, max(o, c))                # guarantee high >= max(open, close)
        out.append({"time": d.isoformat(), "open": o, "high": hi, "low": lo, "close": c,
                    "volume": 1_000_000.0})
        px = c
    return out


def _strategies() -> list[Strategy]:
    """Three different archetypes, each a single valid entry leaf per webull_api/strategy/schema.py.
    None of them specify an `exit` node — per webull_api/strategy/replay.py::step_to_decision, a
    position with no `exit` node never fires a signal-exit and rides to end_of_data (excluded from
    pooled trade stats), degenerating into a single buy-and-hold. stop_loss_pct/take_profit_pct are
    evaluated independent of `exit` (checked every bar regardless), so each strategy is given both
    to force real, repeated closed trades -- an honest test of the archetype, not a buy-and-hold."""
    return [
        Strategy(name="trend_sma_cross", symbol="SPY",
                 entry=SmaCross(type="sma_cross", fast=10, slow=50, direction="above"),
                 stop_loss_pct=5.0, take_profit_pct=10.0),
        Strategy(name="mean_rev_rsi2", symbol="SPY",
                 entry=RsiCond(type="rsi", period=2, threshold=10, comparison="below"),
                 stop_loss_pct=3.0, take_profit_pct=3.0),
        Strategy(name="breakout_20d", symbol="SPY",
                 entry=Breakout(type="breakout", lookback=20, direction="high"),
                 stop_loss_pct=4.0, take_profit_pct=8.0),
    ]


def test_random_walks_never_pass_gate_a_at_depth():
    bars_by_symbol = {f"N{i}": _walk(1000 + i) for i in range(N_SYMBOLS)}
    cfg = dataclasses.replace(DEFAULT_GATE_A_CONFIG, min_lookback_bars=N_BARS)

    for strat in _strategies():
        rep = gate_a.screen_one(strat, bars_by_symbol, cfg=cfg, cost=NO_COST, m=1,
                                require_cost_robust=False, lockbox_bars_by_symbol=None)
        # `passed is False` is the load-bearing assertion — Gate A's AND of every condition
        # (DSR, CI-LB, breadth, drawdown) is what must reject noise, not any one component.
        assert rep.passed is False, (strat.name, rep.fail_codes)
        # At 3,000 bars the breakout archetype's DSR is ~0.896 against the 0.95 floor; at 5,000
        # bars it reached 0.966 on pure noise while `ci_lb_negative` still failed it — depth makes
        # DSR alone a weak noise filter, the AND with CI-LB/breadth/drawdown is what holds. Do NOT
        # raise N_BARS expecting this dsr assertion to survive (see the 5,000-bar case below,
        # which asserts only `passed is False` for exactly this reason).
        assert rep.dsr < cfg.dsr_min, (strat.name, rep.dsr)


def test_breakout_still_fails_gate_a_at_5000_bars():
    # A deeper probe than the loop above, on the archetype the 08-31/09-07 memos flagged as the
    # weak link for DSR-alone filtering: `passed is False` still holds at depth even though DSR
    # itself is no longer a reliable noise filter there (see the comment above). 6 symbols keeps
    # this under the ~90s combined runtime budget alongside the loop's ~40s (verified: ~38s here).
    n_bars = 5000
    bars_by_symbol = {f"N{i}": _walk(1000 + i, n=n_bars) for i in range(N_SYMBOLS)}
    cfg = dataclasses.replace(DEFAULT_GATE_A_CONFIG, min_lookback_bars=n_bars)
    strat = next(s for s in _strategies() if s.name == "breakout_20d")
    rep = gate_a.screen_one(strat, bars_by_symbol, cfg=cfg, cost=NO_COST, m=1,
                            require_cost_robust=False, lockbox_bars_by_symbol=None)
    assert rep.passed is False, (strat.name, rep.fail_codes)
