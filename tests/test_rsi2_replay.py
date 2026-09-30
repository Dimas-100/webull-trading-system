"""Pure portfolio replay of the exact production RSI2 config — engine tests.

Synthetic bars drive the REAL rsi2.decide(): closes are shaped so RSI(2) crosses the
production bands exactly where the scenario needs. rsi2_of needs >= 3 closes to emit a
value; a monotonically falling tail pins RSI(2) to ~0 (entry band), a rising tail pins
it to ~100 (exit band)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api.strategy import rsi2  # noqa: E402
from webull_api.strategy.rsi2_replay import ReplayResult, replay  # noqa: E402

CFG = rsi2.Rsi2Config(universe=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG"),
                      excluded=(), dollars_per_signal=6000.0, max_lots=6,
                      cash_floor=20000.0)
SMALL = rsi2.Rsi2Config(universe=("AAA",), excluded=(), dollars_per_signal=6000.0,
                        max_lots=6, cash_floor=20000.0)


def _bars(prices, start_day=1):
    """[{date, open, close}] from a list of (open, close) tuples, dated 2026-01-<start_day>+i.
    Day numbers stay within one month for simplicity (tests use < 28 bars)."""
    return [{"date": f"2026-01-{start_day + i:02d}", "open": float(o), "close": float(c)}
            for i, (o, c) in enumerate(prices)]


def _flat(n, px=100.0, start_day=1):
    return _bars([(px, px)] * n, start_day)


def test_entry_fills_at_next_open_and_exit_realizes_pnl():
    # AAA: flat 7 bars then ONE down-close (RSI == 0.0 on day 8) -> BUY decided day 8, fills
    # day 9 open. A monotonic decline pins RSI(2) at exactly 0.0 on the FIRST down-tick after
    # a flat run regardless of how many more down-closes follow (Wilder's avg_gain has no
    # positive contribution left to decay from), so a single down-close is enough — verified:
    # rsi2_of([100]*7 + [94]) == 0.0. Then a gradual 3-day rise keeps RSI(2) <= 70 through day
    # 10 and crosses above 70 on day 11 (rsi2_of([...,94,95,97,101]) == 87.5) -> SELL decided
    # day 11, fills day 12 open. (An immediate large up-jump right after the dip crosses the
    # exit band a day earlier than intended — verified — hence the gradual rise.)
    rows = [(100, 100)] * 7 + [(95, 94)]                       # days 1-8: flat then the dip
    rows += [(95, 95), (96, 97), (99, 101), (104, 104)]        # days 9-12: gradual rise, exit fill
    acct_bars = {"AAA": _bars(rows)}
    res = replay(acct_bars, spy_bars=[], cfg=SMALL)
    assert isinstance(res, ReplayResult)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.symbol == "AAA"
    assert t.entry_date == "2026-01-09" and t.entry_price == 95.0      # next open after dip day
    assert t.exit_date == "2026-01-12" and t.exit_price == 104.0
    assert t.shares == 63.0                                            # floor(6000/94) on day-8 close
    assert abs(t.pnl - 63 * (104.0 - 95.0)) < 1e-9
    assert t.holding_days == 3                                         # trading days 9 -> 12
    assert res.open_positions == [] and res.dropped_buys == 0


def test_shares_sized_on_decision_close_not_fill_open():
    # decide() sizes floor(6000/close_on_decision_day); the fill price differs.
    # 7 flat bars then ONE crash close to 60 (RSI == 0.0 on day 8, same single-down-tick
    # mechanics as above) -> BUY decided day 8 sized off close=60; gap-up open 80 day 9 fills.
    rows = [(100, 100)] * 7 + [(95, 60)]                               # crash close 60 on day 8
    rows += [(80, 80)]                                                  # gap-up open 80 day 9
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL)
    assert res.open_positions and res.open_positions[0]["shares"] == 100.0  # floor(6000/60)
    assert res.open_positions[0]["entry_price"] == 80.0


def test_unaffordable_buy_at_open_is_dropped():
    # 7 flat bars then ONE down-close (RSI == 0.0 on day 8, same mechanics as the round-trip
    # test above) -> BUY decided day 8, sized floor(6000/94) = 63 shares.
    rows = [(100, 100)] * 7 + [(95, 94)]
    rows += [(10_000.0, 10_000.0)]                                     # absurd gap-up
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL,
                 starting_cash=26_000.0)                               # 63sh*10000 >> cash
    assert res.dropped_buys == 1
    assert res.trades == [] and res.open_positions == []


def test_cash_floor_blocks_entry():
    # cash 25_000, floor 20_000: a ~6k entry would breach -> decide() itself blocks it.
    rows = [(100, 100)] * 5 + [(99, 98), (97, 96), (95, 94)] + [(94, 94)]
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL, starting_cash=25_000.0)
    assert res.trades == [] and res.open_positions == [] and res.dropped_buys == 0


def test_slot_competition_most_oversold_first():
    # 7 symbols all dip together; max_lots=6 -> exactly 6 entries, and the LEAST oversold
    # (shallowest dip = highest RSI) is the one left out.
    # A pure monotonic decline pins RSI(2) at EXACTLY 0.0 regardless of dip depth (Wilder's
    # avg_gain has no positive contribution to decay from once every change is a loss), so a
    # plain flat-then-dip series can't rank symbols by depth -- verified: rsi2_of on a 5-flat +
    # N-down series is 0.0 for every step size. A tiny up-tick right before the dip gives
    # avg_gain a small nonzero seed, so the dip's depth then produces a genuinely different
    # RSI(2) per symbol: verified rsi2_of([100]*5 + [100.1, 100.1-step]) strictly decreases as
    # step grows (4.76 at step=1 down to 0.71 at step=7), all comfortably < entry_below=10.0.
    sym_bars = {}
    for i, sym in enumerate(CFG.universe):
        step = 1.0 + i                      # deeper dip per symbol index: GGG dips hardest
        dip_close = 100.1 - step
        rows = [(100, 100)] * 5 + [(100.1, 100.1), (100.1, dip_close), (dip_close, dip_close)]
        sym_bars[sym] = _bars(rows)
    res = replay(sym_bars, spy_bars=[], cfg=CFG, starting_cash=100_000.0)
    assert len(res.open_positions) == 6
    assert "AAA" not in {p["symbol"] for p in res.open_positions}      # shallowest dip skipped


def test_missing_bar_day_symbol_invisible_and_fill_waits():
    # AAA: 7 flat bars then ONE down-close (RSI == 0.0 on day 8, same single-down-tick
    # mechanics as the round-trip test above) -> BUY queued; AAA has NO day-9 bar; fill lands
    # at day-10 open.
    rows = [(100, 100)] * 7 + [(95, 94)]
    bars = _bars(rows)                                                  # days 1-8
    bars.append({"date": "2026-01-10", "open": 93.0, "close": 93.0})    # day 9 missing
    # BBB exists on day 9 so the calendar contains the day.
    bbb = _flat(9)
    res = replay({"AAA": bars, "BBB": bbb},
                 spy_bars=[], cfg=rsi2.Rsi2Config(universe=("AAA", "BBB"), excluded=()),
                 starting_cash=100_000.0)
    assert res.open_positions and res.open_positions[0]["entry_date"] == "2026-01-10"
    assert res.open_positions[0]["entry_price"] == 93.0


def test_in_flight_symbol_takes_no_second_decision():
    # AAA's BUY queued on day 8 waits (no day-9/10 bars for AAA) while AAA keeps signaling
    # via... it can't signal without bars; instead: BBB queues day 8, has no day-9 bar, and
    # ALSO dips on day 10 — the day-10 decide must not queue a second BBB BUY while the
    # first is still pending (production in-flight filter, commit ce4c526).
    bbb = _bars([(100, 100)] * 5 + [(99, 98), (97, 96), (95, 94)])      # dip days 6-8
    bbb.append({"date": "2026-01-10", "open": 93.0, "close": 92.0})     # still oversold day 10
    bbb.append({"date": "2026-01-11", "open": 92.0, "close": 92.0})
    aaa = _flat(11)
    res = replay({"AAA": aaa, "BBB": bbb},
                 spy_bars=[], cfg=rsi2.Rsi2Config(universe=("AAA", "BBB"), excluded=()),
                 starting_cash=100_000.0)
    bbb_lots = [p for p in res.open_positions if p["symbol"] == "BBB"]
    assert len(bbb_lots) == 1                                           # never doubled


def test_regime_tagging_risk_on_off_and_warmup():
    # SPY's LAST 5 bars carry the SAME dates as AAA's calendar so this exercises the exact-date
    # tag path (previously vacuous: SPY dates never overlapped the equity calendar, so every day
    # resolved via the earlier-date fallback). 200 flat warmup closes at 100 (SMA200 ~= 100 and
    # moves slowly) then closes far above (150 -> risk_on) for days 1-2 and far below
    # (50 -> risk_off) for days 3-5.
    spy = _spy_series()[:200]                       # 200 warmup bars on synthetic past dates
    for i, px in zip(range(1, 6), [150.0, 150.0, 50.0, 50.0, 50.0]):
        spy.append({"date": f"2026-01-{i:02d}", "open": px, "close": px})
    aaa = _flat(5)
    res = replay({"AAA": aaa}, spy_bars=spy, cfg=SMALL)
    regimes = [p["regime"] for p in res.equity_curve]
    assert regimes == ["risk_on", "risk_on", "risk_off", "risk_off", "risk_off"]


def _spy_series():
    """206 SPY bars: 200 warmup closes at 100, then 3 closes at 110 (above SMA200 ->
    risk_on), then 3 at 80 (below -> risk_off). Dates 2025-01-01..2025-07-25-ish are
    synthesized with a simple incrementing counter formatted as YYYY-MM-DD."""
    rows = []
    day = 0
    for px in [100.0] * 200 + [110.0] * 3 + [80.0] * 3:
        y, rem = divmod(day, 360)
        m, d = divmod(rem, 30)
        rows.append({"date": f"{2020 + y}-{m + 1:02d}-{d + 1:02d}", "open": px, "close": px})
        day += 1
    return rows


def test_trade_carries_entry_day_regime():
    # Build SPY so the AAA entry-decision day is risk_off; assert the trade's regime.
    # AAA scenario identical to the round-trip test; SPY = 200 warmup bars dated far in the
    # past + bars covering AAA's 2026-01 days with closes at 80 (< SMA200 which is ~100).
    rows = [(100, 100)] * 5 + [(99, 98), (97, 96), (95, 94)]
    rows += [(95, 97), (98, 100), (101, 103), (104, 104)]
    spy = _spy_series()
    for i in range(1, 13):
        spy.append({"date": f"2026-01-{i:02d}", "open": 80.0, "close": 80.0})
    res = replay({"AAA": _bars(rows)}, spy_bars=spy, cfg=SMALL)
    assert res.trades and res.trades[0].regime == "risk_off"


def test_empty_input_clean_result():
    res = replay({}, spy_bars=[], cfg=SMALL)
    assert res.trades == [] and res.equity_curve == [] and res.open_positions == []


def test_determinism():
    rows = [(100, 100)] * 5 + [(99, 98), (97, 96), (95, 94), (95, 97), (98, 100)]
    a = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL)
    b = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL)
    assert a == b


def test_stats_blocks_and_regime_buckets():
    rows = [(100, 100)] * 5 + [(99, 98), (97, 96), (95, 94)]
    rows += [(95, 97), (98, 100), (101, 103), (104, 104)]
    spy = _spy_series()
    for i in range(1, 13):
        spy.append({"date": f"2026-01-{i:02d}", "open": 80.0, "close": 80.0})
    res = replay({"AAA": _bars(rows)}, spy_bars=spy, cfg=SMALL)
    s = res.stats
    assert s["overall"]["trades"] == 1 and s["overall"]["win_rate"] == 1.0
    assert abs(s["overall"]["expectancy"] - res.trades[0].pnl) < 1e-9
    assert s["risk_off"]["trades"] == 1 and s["risk_on"]["trades"] == 0
    assert s["per_symbol"]["AAA"]["trades"] == 1
    assert s["span"]["start"] == "2026-01-01" and s["span"]["end"] == "2026-01-12"
    assert s["span"]["trading_days"] == 12
    assert s["spy_buy_hold_pct"] == 0.0            # SPY flat at 80 across the window


def test_warmup_trades_excluded_from_regime_buckets():
    rows = [(100, 100)] * 5 + [(99, 98), (97, 96), (95, 94)]
    rows += [(95, 97), (98, 100), (101, 103), (104, 104)]
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL)   # no SPY -> all warmup
    s = res.stats
    assert s["overall"]["trades"] == 1
    assert s["risk_on"]["trades"] == 0 and s["risk_off"]["trades"] == 0
    assert s["warmup"]["trades"] == 1            # the residual is surfaced, never silent
    assert s["spy_buy_hold_pct"] is None


def test_max_drawdown_negative_when_equity_dips():
    # Entry then a hard down-close before recovery -> equity dips below start.
    rows = [(100, 100)] * 5 + [(99, 98), (97, 96), (95, 94)]
    rows += [(94, 60), (60, 60)]                                  # post-entry crash
    res = replay({"AAA": _bars(rows)}, spy_bars=[], cfg=SMALL)
    assert res.stats["max_drawdown_pct"] < 0


def test_unfilled_decision_at_calendar_end_counts():
    # AAA: flat 7 bars then ONE down-close (RSI == 0.0 on day 8, same single-down-tick
    # mechanics as the round-trip test) -> BUY decided day 8, queued. AAA has NO bar after
    # day 8, ever. BBB is flat and extends the calendar to day 10 so the replay keeps running
    # past AAA's last bar, and the queued AAA decision never finds a bar to settle against.
    rows = [(100, 100)] * 7 + [(95, 94)]
    aaa = _bars(rows)                                                  # days 1-8, no more
    bbb = _flat(10)                                                    # extends calendar to day 10
    res = replay({"AAA": aaa, "BBB": bbb},
                 spy_bars=[], cfg=rsi2.Rsi2Config(universe=("AAA", "BBB"), excluded=()),
                 starting_cash=100_000.0)
    assert res.unfilled_decisions == 1
    assert res.trades == []
    assert all(p["symbol"] != "AAA" for p in res.open_positions)
    assert res.stats["unfilled_decisions"] == 1
