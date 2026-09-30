import pytest
from webull_api.journal.normalize import (
    fills_from_order_history, fills_from_paper_account, decisions_from_session, _mfe_mae,
)


def test_fills_from_order_history_filled_only_and_combo_flatten():
    raw = {"data": [
        {"orders": [
            {"client_order_id": "c1", "symbol": "AAPL", "side": "BUY", "status": "FILLED",
             "order_type": "LIMIT", "filled_quantity": "5", "filled_price": "100.5",
             "filled_time_at": "2026-06-15T14:00:00+00:00"},
        ]},
        {"client_order_id": "c2", "symbol": "MSFT", "side": "SELL", "status": "PENDING",
         "order_type": "MARKET", "filled_quantity": "0"},
    ]}
    fills = fills_from_order_history(raw, "ACC1", context_fn=None)
    assert len(fills) == 1
    f = fills[0]
    assert f.id == "c1" and f.source == "real" and f.account_id == "ACC1"
    assert f.symbol == "AAPL" and f.side == "BUY" and f.quantity == 5.0 and f.price == 100.5
    assert f.order_type == "LIMIT"


def test_fills_from_paper_account():
    acct = {
        "account_id": "default", "starting_cash": 1000.0, "cash": 900.0, "positions": {},
        "open_orders": [], "realized_pnl": 0.0, "created_at": "t", "updated_at": "t",
        "history": [
            {"paper_order_id": "p1", "symbol": "AAPL", "side": "BUY", "order_type": "MARKET",
             "quantity": 2.0, "limit_price": None, "time_in_force": "DAY", "status": "filled",
             "created_at": "t", "placed_et_date": "2026-06-15", "filled_at": "2026-06-15T14:00:00+00:00",
             "fill_price": 100.0},
            {"paper_order_id": "p2", "symbol": "AAPL", "side": "SELL", "order_type": "LIMIT",
             "quantity": 2.0, "limit_price": 110.0, "time_in_force": "GTC", "status": "cancelled",
             "created_at": "t", "placed_et_date": "2026-06-15", "filled_at": None, "fill_price": None},
        ],
    }
    fills = fills_from_paper_account(acct, context_fn=None)
    assert len(fills) == 1
    assert fills[0].id == "p1" and fills[0].source == "paper" and fills[0].price == 100.0


def test_decisions_from_session_only_finished_and_maps_outcome(monkeypatch):
    import webull_api.journal.normalize as norm
    from webull_api.strategy.schema import BacktestResult, EquityPoint, Trade

    session = {
        "id": "sess1",
        "strategy": {"name": "Demo", "symbol": "AAPL", "entry": {"type": "rsi", "period": 14,
                     "threshold": 30, "comparison": "below"}},
        "symbol": "AAPL",
        "window_bars": [
            {"time": "2026-06-01T00:00:00+00:00", "open": 10, "high": 11, "low": 9, "close": 10},
            {"time": "2026-06-02T00:00:00+00:00", "open": 10, "high": 12, "low": 10, "close": 12},
        ],
        "decisions": [{"bar_index": 0, "why": "RSI<30", "choice": "take"}],
        "status": "finished",
    }
    fake = BacktestResult(total_return_pct=0, buy_hold_return_pct=0, num_trades=1, win_rate=1.0,
                          avg_win_pct=0, avg_loss_pct=0, expectancy=0, max_drawdown_pct=0,
                          equity_curve=[EquityPoint(time="t", equity=0)],
                          trades=[Trade(entry_time="2026-06-02T00:00:00+00:00", entry_price=10.0,
                                        exit_time="x", exit_price=12.0, shares=1, pnl=2.0,
                                        return_pct=20.0, exit_reason="signal")])
    monkeypatch.setattr(norm, "run_backtest", lambda strat, bars: fake)

    decs = decisions_from_session(session)
    assert len(decs) == 1
    d = decs[0]
    assert d.id == "sess1:0" and d.choice == "take" and d.strategy_name == "Demo"
    assert d.signal_pnl == 2.0 and d.signal_win is True
    assert d.context is not None and d.context.trend in ("uptrend", "downtrend", "sideways")


def test_decisions_from_session_skips_in_progress():
    session = {"id": "s2", "strategy": {"name": "X", "symbol": "AAPL",
               "entry": {"type": "rsi", "period": 14, "threshold": 30, "comparison": "below"}},
               "symbol": "AAPL", "window_bars": [], "decisions": [], "status": "in_progress"}
    assert decisions_from_session(session) == []


def test_mfe_mae_pure_helper():
    """_mfe_mae computes signed % MFE/MAE over the inclusive hold window."""
    bars = [
        {"time": "t0", "open": 10, "high": 11, "low": 9,  "close": 10},
        {"time": "t1", "open": 10, "high": 15, "low": 8,  "close": 12},
        {"time": "t2", "open": 12, "high": 13, "low": 11, "close": 12},
    ]
    mfe, mae = _mfe_mae(bars, "t0", "t2", 10.0)
    assert mfe is not None and mae is not None
    # highest = 15 → mfe = (15-10)/10*100 = 50.0
    # lowest  = 8  → mae = (8-10)/10*100 = -20.0
    assert mfe == pytest.approx(50.0)
    assert mae == pytest.approx(-20.0)
    assert mfe >= 0
    assert mae <= 0


def test_mfe_mae_guard_conditions():
    """_mfe_mae returns (None, None) for edge cases."""
    bars = [{"time": "t0", "open": 10, "high": 11, "low": 9, "close": 10}]
    # P <= 0
    assert _mfe_mae(bars, "t0", "t0", 0.0) == (None, None)
    # entry_time not found
    assert _mfe_mae(bars, "MISSING", "t0", 10.0) == (None, None)
    # exit_time not found
    assert _mfe_mae(bars, "t0", "MISSING", 10.0) == (None, None)
    # None high/low
    null_bars = [{"time": "t0", "open": 10, "high": None, "low": 9, "close": 10}]
    assert _mfe_mae(null_bars, "t0", "t0", 10.0) == (None, None)


def test_decisions_from_session_mfe_mae_and_thesis(monkeypatch):
    """A take decision mapped to a benchmark trade → non-None MFE/MAE with sane signs;
    thesis round-trips; a decision with no matching benchmark → both None."""
    import webull_api.journal.normalize as norm
    from webull_api.strategy.schema import BacktestResult, EquityPoint, Trade
    from webull_api.journal.schema import ThesisRecord

    # Bars: signal fires at bar 0 (index 0); fill at bar 1 (index 1, entry_time="t1");
    # exit at bar 2 (index 2, exit_time="t2").
    bars = [
        {"time": "t0", "open": 10.0, "high": 11.0, "low":  9.0, "close": 10.0},
        {"time": "t1", "open": 10.0, "high": 14.0, "low":  9.0, "close": 12.0},
        {"time": "t2", "open": 12.0, "high": 13.0, "low": 11.0, "close": 12.0},
    ]

    # decision 0: take at signal bar 0 → entry_time = bars[1]["time"] = "t1"
    # decision 1: skip at signal bar 2 → entry_time = bars[3]["time"] = None (out of range)
    session = {
        "id": "sess_mfe",
        "strategy": {"name": "Demo", "symbol": "AAPL", "entry": {"type": "rsi", "period": 14,
                     "threshold": 30, "comparison": "below"}},
        "symbol": "AAPL",
        "window_bars": bars,
        "decisions": [
            {"bar_index": 0, "why": "RSI<30", "choice": "take",
             "thesis": {"confidence": 4, "setup": "breakout"}},
            {"bar_index": 2, "why": "RSI<30", "choice": "skip", "thesis": None},
        ],
        "status": "finished",
    }

    # Fake backtest: one trade from t1 (entry) to t2 (exit) at entry_price=10.0
    fake = BacktestResult(
        total_return_pct=20.0, buy_hold_return_pct=20.0, num_trades=1, win_rate=1.0,
        avg_win_pct=20.0, avg_loss_pct=0.0, expectancy=20.0, max_drawdown_pct=0.0,
        equity_curve=[EquityPoint(time="t1", equity=10000.0)],
        trades=[Trade(entry_time="t1", entry_price=10.0, exit_time="t2",
                      exit_price=12.0, shares=1000.0, pnl=2000.0,
                      return_pct=20.0, exit_reason="signal")],
    )
    monkeypatch.setattr(norm, "run_backtest", lambda strat, bars: fake)

    decs = decisions_from_session(session)
    assert len(decs) == 2

    take_dec = decs[0]
    skip_dec = decs[1]

    # take decision: has a matching benchmark trade → MFE/MAE non-None with sane signs
    # window [t1, t2]: highest=max(14,13)=14, lowest=min(9,11)=9, P=10
    # mfe = (14-10)/10*100 = 40.0, mae = (9-10)/10*100 = -10.0
    assert take_dec.mfe is not None and take_dec.mae is not None
    assert take_dec.mfe == pytest.approx(40.0)
    assert take_dec.mae == pytest.approx(-10.0)
    assert take_dec.mfe >= 0
    assert take_dec.mae <= 0

    # thesis round-trips
    assert take_dec.thesis is not None
    assert take_dec.thesis.confidence == 4
    assert take_dec.thesis.setup == "breakout"

    # skip decision: no matching benchmark trade (bar_index=2 → entry_time=bars[3] OOB)
    assert skip_dec.mfe is None and skip_dec.mae is None
    assert skip_dec.thesis is None


def test_fills_from_paper_account_carries_thesis():
    """A filled BUY order with a thesis → Fill.thesis matches; a filled order without → None."""
    from webull_api.journal.schema import ThesisRecord
    acct = {
        "account_id": "default", "starting_cash": 1000.0, "cash": 800.0, "positions": {},
        "open_orders": [], "realized_pnl": 0.0, "created_at": "t", "updated_at": "t",
        "history": [
            # filled BUY with thesis
            {"paper_order_id": "p1", "symbol": "AAPL", "side": "BUY", "order_type": "MARKET",
             "quantity": 2.0, "limit_price": None, "time_in_force": "DAY", "status": "filled",
             "created_at": "t", "placed_et_date": "2026-06-16",
             "filled_at": "2026-06-16T14:00:00+00:00", "fill_price": 100.0,
             "thesis": {"confidence": 4, "setup": "breakout"}},
            # filled SELL without thesis
            {"paper_order_id": "p2", "symbol": "AAPL", "side": "SELL", "order_type": "MARKET",
             "quantity": 2.0, "limit_price": None, "time_in_force": "DAY", "status": "filled",
             "created_at": "t", "placed_et_date": "2026-06-16",
             "filled_at": "2026-06-16T15:00:00+00:00", "fill_price": 110.0,
             "thesis": None},
        ],
    }
    fills = fills_from_paper_account(acct, context_fn=None)
    assert len(fills) == 2
    buy_fill = next(f for f in fills if f.side == "BUY")
    sell_fill = next(f for f in fills if f.side == "SELL")
    assert buy_fill.thesis is not None
    assert buy_fill.thesis.confidence == 4
    assert buy_fill.thesis.setup == "breakout"
    assert sell_fill.thesis is None
