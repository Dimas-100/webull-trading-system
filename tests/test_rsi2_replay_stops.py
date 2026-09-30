"""StopRule (backstop % + breakeven-after-% resting stop) — engine tests.

Hand-derived expectations, verified against the RSI(2) mechanics already pinned in
test_rsi2_replay.py: a flat run of >=3 closes followed by ONE down-close pins RSI(2) at
exactly 0.0 (Wilder's avg_gain has nothing positive left to decay from), so
`[(100,100,100,100)]*7 + [(95,95,94,94)]` (o,h,l,c) always queues a BUY on day 8 that
fills day 9's open (verified in test_rsi2_replay.py). Every scenario below ends its bar
series exactly on the day under test so no later decision can settle into a confounding
extra trade -- a queued decision with no more bars just sits in `unfilled_decisions`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api.strategy import rsi2  # noqa: E402
from webull_api.strategy.rsi2_replay import ReplayResult, StopRule, replay  # noqa: E402

SMALL = rsi2.Rsi2Config(universe=("AAA",), excluded=(), dollars_per_signal=6000.0,
                        max_lots=6, cash_floor=20000.0)


def _bars(rows, start_day=1):
    """[{date, open, high, low, close}] from (open, high, low, close) tuples."""
    return [{"date": f"2026-01-{start_day + i:02d}", "open": float(o), "high": float(h),
             "low": float(l), "close": float(c)} for i, (o, h, l, c) in enumerate(rows)]


def _bars_oc(rows, start_day=1):
    """[{date, open, close}] from (open, close) tuples -- no high/low, like the legacy suite."""
    return [{"date": f"2026-01-{start_day + i:02d}", "open": float(o), "close": float(c)}
            for i, (o, c) in enumerate(rows)]


def test_stop_rule_none_identical_to_omitted_kwarg():
    # Round-trip scenario from test_rsi2_replay.py (entry day 9, RSI exit fills day 12).
    rows = [(100, 100)] * 7 + [(95, 94)]
    rows += [(95, 95), (96, 97), (99, 101), (104, 104)]
    bars = {"AAA": _bars_oc(rows)}
    a = replay(bars, spy_bars=[], cfg=SMALL)
    b = replay(bars, spy_bars=[], cfg=SMALL, stop_rule=None)
    assert a.trades == b.trades
    assert a.stats == b.stats


def test_backstop_8pct_hit_on_a_normal_low():
    # Entry: 7 flat @100, day 8 down-close (o95/h95/l94/c94) -> RSI(2)==0.0 -> BUY queued.
    # Day 9 (fill day, entry_idx): open=100.00 exactly -> entry_price=100.00.
    # stop_level = 100 * (1 - 0.08) = 92.00.
    # Day 10 (first day AFTER the fill day): open=93, low=91.50 <= 92.00 -> stop hit;
    # fill = min(stop_level=92.00, open=93.00) = 92.00.
    rows = [(100, 100, 100, 100)] * 7 + [(95, 95, 94, 94)]
    rows += [(100, 100.5, 99.5, 100.0)]                      # day 9: fill day, entry=100.00
    rows += [(93, 94, 91.50, 92.0)]                          # day 10: stop hit
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL,
                 stop_rule=StopRule(backstop_pct=8.0))
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.entry_price == 100.0
    assert t.exit_price == 92.0
    assert t.exit_reason == "stop"
    assert t.exit_date == "2026-01-10"
    assert res.open_positions == []


def test_backstop_8pct_hit_on_a_gap_below_the_stop():
    # Same entry as above; day 10 instead GAPS below the stop: open=90.00, low=89 <= 92.00 ->
    # fill = min(92.00, 90.00) = 90.00 (the gap open, conservative -- never better than the stop).
    rows = [(100, 100, 100, 100)] * 7 + [(95, 95, 94, 94)]
    rows += [(100, 100.5, 99.5, 100.0)]                      # day 9: fill day, entry=100.00
    rows += [(90.0, 90.5, 89.0, 89.5)]                       # day 10: gap-down stop hit
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL,
                 stop_rule=StopRule(backstop_pct=8.0))
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_price == 90.0
    assert t.exit_reason == "stop"


def test_breakeven_after_3pct_arms_next_session_and_exits_at_entry():
    # Same day-8/day-9 entry mechanics, but day 9 (the fill day) closes at 40.0 -- a further
    # crash on the fill day itself. This is NOT needed for the entry (which is already
    # decided, off day 8's close, before day 9 opens) -- it is needed to keep RSI(2) below the
    # production exit_above=70 band once price rockets back to the breakeven trigger one day
    # later, because RSI(2)'s 2-period Wilder smoothing is otherwise so reactive that even a
    # +3% recovery right after the entry dip crosses 70 on its own (verified: from a
    # [100]*7+[94] history, a same/next-day close of 103.5 alone already reads ~76-86). A
    # deeper preceding loss keeps the post-jump RSI(2) under 70 so the breakeven mechanism --
    # not a coincidental RSI(2) exit -- is what is under test here.
    # Verified via rsi2_of on this exact history: day 9 (close 40.0) == 0.0; day 10
    # (close 103.5) == 69.02; day 11 is never computed (the lot is gone before decide() runs).
    # Day 10 (first day after the fill): close=103.5 >= 100*(1.03)=103.00 -> arms the stop to
    # entry (100.00) from the NEXT session on. This day's own low (90.0, deliberately far below
    # any level) must NOT be checked against the not-yet-armed (None) level -- and RSI(2)=69.02
    # stays under the exit band, so no RSI SELL is queued either.
    # Day 11: stop_level is now 100.00 (armed since day 10's close); low=99.8 <= 100.00 -> hit;
    # open=100.5 -> fill = min(100.00, 100.5) = 100.00; reason "breakeven" (the raised level).
    rows = [(100, 100, 100, 100)] * 7 + [(95, 95, 94, 94)]
    rows += [(100, 100.5, 39.5, 40.0)]                        # day 9: fill day, entry=100.00
    rows += [(100.2, 104, 90.0, 103.5)]                       # day 10: arms from day 11
    rows += [(100.5, 101, 99.8, 99.9)]                        # day 11: breakeven stop hit
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL,
                 stop_rule=StopRule(breakeven_after_pct=3.0))
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.entry_price == 100.0
    assert t.exit_price == 100.0
    assert t.exit_reason == "breakeven"
    assert t.exit_date == "2026-01-11"
    assert abs(t.pnl) < 1e-9                                  # exits flat at entry


def test_breakeven_disabled_and_no_backstop_never_exits_on_the_same_series():
    # Identical bars to the breakeven-arms test above, but StopRule() (both fields None) ->
    # no stop logic engages at all; day 11's low=99.8 is never checked against anything.
    # The lot stays open through the end of the data (it "runs to the RSI exit" in practice --
    # RSI(2) is verified to stay under 70 through day 10 in this series, so there is nothing
    # further to settle within the window tested here).
    rows = [(100, 100, 100, 100)] * 7 + [(95, 95, 94, 94)]
    rows += [(100, 100.5, 39.5, 40.0)]
    rows += [(100.2, 104, 90.0, 103.5)]
    rows += [(100.5, 101, 99.8, 99.9)]
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL, stop_rule=StopRule())
    assert res.trades == []
    assert len(res.open_positions) == 1
    assert res.open_positions[0]["symbol"] == "AAA"


def test_queued_rsi_exit_fills_at_open_before_that_days_low_can_trip_the_stop():
    # Entry day 9 (open=95.00, from the verified round-trip series), RSI(2) crosses >70 on
    # day 11 (close 101 after the 95/97 climb -- same mechanics as
    # test_entry_fills_at_next_open_and_exit_realizes_pnl) -> SELL queued, fills day 12 open.
    # stop_level = 95 * (1 - 0.08) = 87.4. Day 12's low is set to 50 -- WAY below the stop --
    # to prove the already-queued RSI exit settles at the open FIRST (removing the lot) before
    # the stop-check ever sees that day's range. If the ordering were wrong, this trade would
    # show exit_reason "stop" at price 87.4 instead of "rsi" at 104.
    rows = [(100, 100, 100, 100)] * 7 + [(95, 95, 94, 94)]
    rows += [(95, 96, 94, 95)]                                # day 9: fill, entry=95.00
    rows += [(96, 97, 95, 97)]                                # day 10
    rows += [(99, 101.5, 98, 101)]                             # day 11: RSI(2) crosses >70, SELL queued
    rows += [(104, 105, 50, 104)]                              # day 12: RSI SELL fills @ open=104;
                                                                # low=50 must NOT trigger a stop
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL,
                 stop_rule=StopRule(backstop_pct=8.0))
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "rsi"
    assert t.exit_price == 104.0
    assert t.exit_date == "2026-01-12"
    assert abs(t.pnl - 63 * (104.0 - 95.0)) < 1e-9            # 63 = floor(6000 / 94), day-8 close


def test_arming_ignores_the_fill_days_own_close():
    # Entry dip is DEEPER here (close 40.0 on day 8, instead of 94.0) purely to keep RSI(2)
    # under the exit_above=70 band once the fill day itself jumps to the breakeven trigger (a
    # same-day dip-to-jump is the most RSI(2)-reactive shape there is -- verified: from
    # [100]*7+[94.0], a same-day close of 103.5 already reads RSI(2)=76.0; from
    # [100]*7+[40.0] it reads 67.91). A single monotonic decline pins RSI(2) at exactly 0.0
    # regardless of dip depth (already relied on elsewhere in this suite), so the entry
    # mechanics (BUY decided day 8, RSI(2)==0.0) are unaffected by using a deeper dip.
    # Fill day (day 9) closes at 103.5 -- ABOVE the 3% breakeven threshold (103.00), with
    # RSI(2)=67.91 (verified), so no RSI SELL is queued from this day either. If the fill day
    # itself counted toward arming, the stop would already be raised to 100.00 by day 10, and
    # day 10's low (99.8) would trip a breakeven exit. It must not: arming only counts closes
    # strictly AFTER the fill day. Day 10's own close (101.0, RSI(2)=64.47, verified) is also
    # below the 103.00 threshold, so it doesn't newly arm the stop either -- the lot must still
    # be open at the end of the data.
    rows = [(100, 100, 100, 100)] * 7 + [(45, 45, 40, 40)]
    rows += [(100, 104, 99.5, 103.5)]                          # day 9: fill day, close=103.5 (must not arm)
    rows += [(100.2, 101.5, 99.8, 101.0)]                      # day 10: would trip a wrongly-armed stop
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL,
                 stop_rule=StopRule(breakeven_after_pct=3.0))
    assert res.trades == []
    assert len(res.open_positions) == 1
    assert res.open_positions[0]["symbol"] == "AAA"


def test_exit_reasons_tallied_in_overall_stats():
    rows = [(100, 100, 100, 100)] * 7 + [(95, 95, 94, 94)]
    rows += [(100, 100.5, 99.5, 100.0)]
    rows += [(93, 94, 91.50, 92.0)]
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL,
                 stop_rule=StopRule(backstop_pct=8.0))
    assert res.stats["overall"]["exit_reasons"] == {"stop": 1}


def test_default_replay_trade_exit_reason_is_rsi():
    rows = [(100, 100)] * 7 + [(95, 94)]
    rows += [(95, 95), (96, 97), (99, 101), (104, 104)]
    res = replay({"AAA": _bars_oc(rows)}, spy_bars=[], cfg=SMALL)
    assert len(res.trades) == 1 and res.trades[0].exit_reason == "rsi"
    assert res.stats["overall"]["exit_reasons"] == {"rsi": 1}
