"""Pure forward-return scoring: SPY dates are the trading calendar; entry = next session open."""
from webull_api import scanner_efficacy as se


def _bars(dates, price=100.0, step=1.0):
    """Chronological daily bars; open = close of prior step so returns are deterministic."""
    out = []
    p = price
    for d in dates:
        out.append({"time": d, "open": p, "high": p + 1, "low": p - 1, "close": p + step,
                    "volume": 1000})
        p = p + step
    return out


# 8 sessions spanning a weekend gap (Fri 07-17 -> Mon 07-20).
DATES = ["2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17",
         "2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23"]


def _snap(date="2026-07-15", symbol="AAPL", tags=("dip_buy",)):
    return {"id": f"{date}:{symbol}", "date": date, "symbol": symbol, "tags": list(tags)}


def test_entry_is_first_session_strictly_after_snapshot_date():
    spy = _bars(DATES, price=100.0, step=0.0)          # flat SPY
    sym = _bars(DATES, price=50.0, step=0.5)           # rises 0.5/day
    scores = se.score_snapshot(_snap(), sym, spy, windows=(5,), scored_at="X")
    assert len(scores) == 1
    s = scores[0]
    assert s["entry_date"] == "2026-07-16"             # next session after 07-15
    assert s["id"] == "2026-07-15:AAPL:5" and s["scored_at"] == "X"
    # entry open 51.0 (2 steps in), exit close = close of the 5th session from entry
    assert s["entry_price"] == 51.0
    assert s["window"] == 5


def test_window_not_matured_returns_nothing():
    spy = _bars(DATES[:4], step=0.0)                   # only 2 sessions after snapshot
    sym = _bars(DATES[:4], price=50.0, step=0.5)
    assert se.score_snapshot(_snap(), sym, spy, windows=(5,)) == []
    assert se.score_snapshot(_snap(date="2026-07-23"), sym, spy, windows=(5,)) == []


def test_excess_bp_is_symbol_minus_spy():
    spy = _bars(DATES, price=100.0, step=1.0)          # SPY rises 1%/~day of its price
    sym = _bars(DATES, price=100.0, step=2.0)          # symbol rises twice as fast
    s = se.score_snapshot(_snap(), sym, spy, windows=(5,))[0]
    assert s["excess_bp"] > 0
    assert round(s["sym_ret_pct"] - s["spy_ret_pct"], 1) == round(s["excess_bp"] / 100.0, 1)


def test_symbol_missing_a_session_skips_that_window():
    spy = _bars(DATES, step=0.0)
    sym_dates = [d for d in DATES if d != "2026-07-16"]   # symbol halted on entry day
    sym = _bars(sym_dates, price=50.0, step=0.5)
    assert se.score_snapshot(_snap(), sym, spy, windows=(5,)) == []


def test_efficacy_summary_windows_tags_pending():
    snapshots = [_snap(), _snap(symbol="MSFT", tags=("dip_buy", "momentum_leader"))]
    scores = [
        {"id": "2026-07-15:AAPL:5", "snapshot_id": "2026-07-15:AAPL", "window": 5, "excess_bp": 50.0},
        {"id": "2026-07-15:MSFT:5", "snapshot_id": "2026-07-15:MSFT", "window": 5, "excess_bp": -20.0},
    ]
    s = se.efficacy_summary(snapshots, scores, today="2026-07-15")
    w5 = next(w for w in s["windows"] if w["window"] == 5)
    assert w5["n"] == 2 and w5["hit_rate"] == 0.5 and w5["avg_excess_bp"] == 15.0
    dip = next(t for t in s["by_tag"] if t["tag"] == "dip_buy")
    assert dip["n"] == 2                               # both picks carry dip_buy
    mom = next(t for t in s["by_tag"] if t["tag"] == "momentum_leader")
    assert mom["n"] == 1 and mom["hit_rate"] == 0.0
    assert s["pending"] == 4                           # 2 snapshots × windows (5,10,20) − 2 scored
    assert s["snapped_today"] == 2
    assert se.efficacy_summary([], [], today="")["windows"] == []
