from webull_api.journal.schema import (
    MarketContext, Fill, PracticeDecision, ClosedTrade, OpenPosition,
    StatBlock, Breakdown, DisciplineStats, JournalSummary, ThesisRecord, setup_label,
)


def test_fill_round_trips():
    f = Fill(id="o1", source="real", account_id="A", symbol="AAPL", side="BUY",
             quantity=10, price=100.0, filled_at_iso="2026-06-15T14:00:00+00:00",
             order_type="LIMIT", context=None)
    assert Fill.model_validate(f.model_dump()) == f


def test_setup_label_buckets():
    up_oversold = MarketContext(as_of_iso=None, trend="uptrend", rsi14=25.0, sma20=None,
                                sma50=None, pct_change_5=None, support=None, resistance=None)
    assert setup_label(up_oversold) == "uptrend · RSI<30"
    down_overbought = MarketContext(as_of_iso=None, trend="downtrend", rsi14=80.0, sma20=None,
                                    sma50=None, pct_change_5=None, support=None, resistance=None)
    assert setup_label(down_overbought) == "downtrend · RSI>70"
    mid = MarketContext(as_of_iso=None, trend="sideways", rsi14=50.0, sma20=None, sma50=None,
                        pct_change_5=None, support=None, resistance=None)
    assert setup_label(mid) == "sideways · RSI mid"


def test_setup_label_unknown_when_no_context():
    assert setup_label(None) == "unknown"
    no_trend = MarketContext(as_of_iso=None, trend=None, rsi14=None, sma20=None, sma50=None,
                             pct_change_5=None, support=None, resistance=None)
    assert setup_label(no_trend) == "unknown"


def test_summary_constructs_empty():
    s = JournalSummary(
        generated_at_iso="2026-06-16T00:00:00+00:00",
        overall=StatBlock(trades=0, wins=0, win_rate=0.0, total_pnl=0.0, avg_win=0.0,
                          avg_loss=0.0, expectancy=0.0),
        by_source=[], by_symbol=[], by_setup=[], closed_trades=[], open_positions=[],
        discipline=DisciplineStats(signals=0, taken=0, skipped=0, take_rate=0.0,
                                   pnl_captured=0.0, pnl_forgone=0.0, skipped_winners=0,
                                   taken_losers=0),
        practice_decisions=[], last_sync={})
    assert s.overall.trades == 0


# ---------------------------------------------------------------------------
# ThesisRecord tests
# ---------------------------------------------------------------------------

def test_thesis_record_minimal_round_trips():
    """Only confidence is required; all optional fields default to None."""
    t = ThesisRecord(confidence=3)
    assert t.direction == "LONG"
    assert t.expected_move_pct is None
    assert t.horizon is None
    assert t.invalidation is None
    assert t.setup is None
    assert t.note is None
    assert ThesisRecord.model_validate(t.model_dump()) == t


def test_thesis_record_full_round_trips():
    """All fields populated; round-trip through model_dump/model_validate is lossless."""
    t = ThesisRecord(
        confidence=5,
        direction="LONG",
        expected_move_pct=3.5,
        horizon="days",
        invalidation="close below 200-day SMA",
        setup="breakout",
        note="strong volume on breakout bar",
    )
    assert ThesisRecord.model_validate(t.model_dump()) == t


def test_fill_with_thesis_round_trips():
    """A Fill with a thesis nested inside round-trips correctly."""
    thesis = ThesisRecord(confidence=4, setup="pullback", expected_move_pct=2.0)
    f = Fill(id="o1", source="paper", account_id="A", symbol="AAPL", side="BUY",
             quantity=5, price=150.0, filled_at_iso="2026-06-16T14:00:00+00:00",
             order_type="LIMIT", thesis=thesis)
    assert f.thesis == thesis
    assert Fill.model_validate(f.model_dump()) == f


def test_fill_without_thesis_still_validates():
    """Existing fills with no thesis field are unaffected (defaults to None)."""
    f = Fill(id="o2", source="real", account_id="B", symbol="MSFT", side="SELL",
             quantity=10, price=200.0, filled_at_iso="2026-06-16T15:00:00+00:00",
             order_type="MARKET")
    assert f.thesis is None
    assert Fill.model_validate(f.model_dump()) == f


def test_closed_trade_with_thesis_mfe_mae():
    """ClosedTrade accepts thesis, mfe, mae and round-trips correctly."""
    thesis = ThesisRecord(confidence=2, horizon="intraday", invalidation="break of LOD")
    ct = ClosedTrade(
        symbol="AAPL", source="paper", quantity=10, entry_price=100.0, exit_price=110.0,
        entry_at_iso="2026-06-01T00:00:00+00:00", exit_at_iso="2026-06-02T00:00:00+00:00",
        holding_days=1.0, pnl=100.0, return_pct=10.0, win=True,
        thesis=thesis, mfe=5.5, mae=-1.2,
    )
    assert ct.thesis == thesis
    assert ct.mfe == 5.5
    assert ct.mae == -1.2
    assert ClosedTrade.model_validate(ct.model_dump()) == ct


def test_closed_trade_mfe_mae_default_none():
    """ClosedTrade without mfe/mae/thesis: all three default to None."""
    ct = ClosedTrade(
        symbol="AAPL", source="paper", quantity=1, entry_price=100.0, exit_price=110.0,
        entry_at_iso="2026-06-01T00:00:00+00:00", exit_at_iso="2026-06-02T00:00:00+00:00",
        holding_days=1.0, pnl=10.0, return_pct=10.0, win=True,
    )
    assert ct.thesis is None
    assert ct.mfe is None
    assert ct.mae is None


def test_practice_decision_with_thesis_mfe_mae():
    """PracticeDecision accepts thesis, mfe, mae and round-trips correctly."""
    thesis = ThesisRecord(confidence=1, note="speculative")
    pd = PracticeDecision(
        id="d1", session_id="s1", strategy_name="MA cross", symbol="TSLA",
        decided_at_iso="2026-06-16T10:00:00+00:00", choice="take",
        signal_why="SMA20 crossed SMA50", entry_price=200.0,
        signal_pnl=15.0, signal_win=True,
        thesis=thesis, mfe=8.0, mae=-2.5,
    )
    assert pd.thesis == thesis
    assert pd.mfe == 8.0
    assert pd.mae == -2.5
    assert PracticeDecision.model_validate(pd.model_dump()) == pd


def test_practice_decision_without_thesis_still_validates():
    """Existing PracticeDecision fixtures without new fields are unaffected."""
    pd = PracticeDecision(
        id="d2", session_id="s2", strategy_name=None, symbol="AAPL",
        decided_at_iso="t", choice="skip", signal_why="no signal",
        signal_pnl=None, signal_win=None,
    )
    assert pd.thesis is None
    assert pd.mfe is None
    assert pd.mae is None
