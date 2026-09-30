from webull_api.journal.analytics import (
    stat_block, breakdown_by, discipline_stats, build_summary, is_coordination_artifact,
)
from webull_api.journal.schema import ClosedTrade, Fill, PracticeDecision


def _trade(symbol, pnl, source="paper", setup="unknown",
           entry_at="2026-06-01T00:00:00+00:00", exit_at="2026-06-02T00:00:00+00:00",
           return_pct=None):
    win = pnl > 0
    return ClosedTrade(symbol=symbol, source=source, quantity=1, entry_price=100.0,
                       exit_price=100.0 + pnl, entry_at_iso=entry_at,
                       exit_at_iso=exit_at, holding_days=1.0, pnl=pnl,
                       return_pct=return_pct if return_pct is not None else pnl,
                       win=win, entry_context=None, setup=setup)


def test_stat_block_basic():
    s = stat_block([_trade("A", 100.0), _trade("A", -50.0), _trade("A", 30.0)])
    assert s.trades == 3 and s.wins == 2
    assert round(s.win_rate, 4) == round(2 / 3, 4)
    assert s.total_pnl == 80.0
    assert s.avg_win == 65.0 and s.avg_loss == -50.0
    assert round(s.expectancy, 4) == round(65.0 * (2 / 3) + (-50.0) * (1 / 3), 4)


def test_stat_block_empty_is_zeros():
    s = stat_block([])
    assert s.trades == 0 and s.win_rate == 0.0 and s.expectancy == 0.0


def test_stat_block_expectancy_pct_is_mean_return_pct():
    s = stat_block([_trade("A", 100.0, return_pct=2.0), _trade("A", -50.0, return_pct=-1.0),
                    _trade("A", 30.0, return_pct=0.5)])
    assert round(s.expectancy_pct, 6) == round((2.0 - 1.0 + 0.5) / 3, 6)
    assert stat_block([]).expectancy_pct == 0.0


def test_artifact_paper_subhour_round_trip():
    t = _trade("GOOG", 0.0, entry_at="2026-07-23T17:30:02-04:00",
               exit_at="2026-07-23T17:30:18-04:00")
    assert is_coordination_artifact(t) is True


def test_artifact_not_for_multi_day_paper_trade():
    t = _trade("VOO", -5.0, entry_at="2026-07-20T17:30:00-04:00",
               exit_at="2026-07-23T17:30:00-04:00")
    assert is_coordination_artifact(t) is False


def test_artifact_never_for_real_trades():
    t = _trade("AAPL", 0.0, source="real", entry_at="2026-07-23T10:00:02-04:00",
               exit_at="2026-07-23T10:00:18-04:00")
    assert is_coordination_artifact(t) is False


def test_artifact_unparseable_timestamps_are_kept_as_decisions():
    t = _trade("GOOG", 0.0, entry_at="not-a-time", exit_at="also-not")
    assert is_coordination_artifact(t) is False


def test_artifact_paper_option_subhour_counts():
    t = _trade("GOOG", -453.0, source="paper_option",
               entry_at="2026-07-23T17:30:26-04:00", exit_at="2026-07-23T17:30:28-04:00")
    assert is_coordination_artifact(t) is True


def test_breakdown_by_symbol_sorted_by_pnl():
    trades = [_trade("A", 100.0), _trade("B", -20.0), _trade("B", -10.0), _trade("C", 50.0)]
    bd = breakdown_by(trades, lambda t: t.symbol)
    assert [b.key for b in bd] == ["A", "C", "B"]  # 100, 50, -30
    assert bd[2].stats.total_pnl == -30.0


def _dec(choice, pnl, win, did="s1", i=0):
    return PracticeDecision(id=f"{did}:{i}", session_id=did, strategy_name="x", symbol="AAPL",
                            decided_at_iso="t", choice=choice, signal_why="why",
                            entry_price=10.0, signal_pnl=pnl, signal_win=win, context=None)


def test_discipline_stats():
    decs = [_dec("take", 50.0, True, i=0), _dec("take", -20.0, False, i=1),
            _dec("skip", 80.0, True, i=2), _dec("skip", -5.0, False, i=3)]
    d = discipline_stats(decs)
    assert d.signals == 4 and d.taken == 2 and d.skipped == 2
    assert d.take_rate == 0.5
    assert d.pnl_captured == 30.0           # 50 + (-20)
    assert d.pnl_forgone == 75.0            # 80 + (-5)
    assert d.skipped_winners == 1           # the skipped 80
    assert d.taken_losers == 1              # the taken -20


def test_build_summary_end_to_end():
    fills = [
        Fill(id="b", source="paper", account_id="A", symbol="AAPL", side="BUY", quantity=1,
             price=100.0, filled_at_iso="2026-06-01T00:00:00+00:00", order_type="MARKET"),
        Fill(id="s", source="paper", account_id="A", symbol="AAPL", side="SELL", quantity=1,
             price=110.0, filled_at_iso="2026-06-02T00:00:00+00:00", order_type="MARKET"),
    ]
    decs = [_dec("take", 50.0, True)]
    summary = build_summary(fills, decs, generated_at_iso="2026-06-16T00:00:00+00:00",
                            last_sync={"at": "now"})
    assert summary.overall.trades == 1 and summary.overall.total_pnl == 10.0
    assert len(summary.closed_trades) == 1
    assert summary.discipline.taken == 1
    assert summary.last_sync == {"at": "now"}
    assert [b.key for b in summary.by_source] == ["paper"]


def _round_trip(symbol, pnl, i):
    return [
        Fill(id=f"b{i}", source="paper", account_id="A", symbol=symbol, side="BUY", quantity=1,
             price=100.0, filled_at_iso="2026-06-01T00:00:00+00:00", order_type="MARKET"),
        Fill(id=f"s{i}", source="paper", account_id="A", symbol=symbol, side="SELL", quantity=1,
             price=100.0 + pnl, filled_at_iso="2026-06-02T00:00:00+00:00", order_type="MARKET"),
    ]


def test_build_summary_best_worst_over_full_breakdown():
    """10 symbols, 8 winners + 2 losers: by_symbol truncates to the top-8 (winners only), but
    best/worst are computed over the FULL breakdown — the true worst survives truncation."""
    fills = []
    for i in range(8):
        fills += _round_trip(f"W{i}", 80.0 - i, i)          # +80 … +73
    fills += _round_trip("LOSER1", -10.0, 90)
    fills += _round_trip("LOSER2", -40.0, 91)               # the true worst
    summary = build_summary(fills, [], top_symbols=8)
    assert len(summary.by_symbol) == 8
    assert all(b.stats.total_pnl > 0 for b in summary.by_symbol)   # losers truncated away
    assert summary.best_symbol is not None and summary.best_symbol.key == "W0"
    assert summary.worst_symbol is not None and summary.worst_symbol.key == "LOSER2"
    assert summary.worst_symbol.stats.total_pnl == -40.0


def test_build_summary_single_symbol_has_no_worst():
    summary = build_summary(_round_trip("AAPL", 10.0, 0), [])
    assert summary.best_symbol is not None and summary.best_symbol.key == "AAPL"
    assert summary.worst_symbol is None


def test_build_summary_empty_has_no_best_worst():
    summary = build_summary([], [])
    assert summary.best_symbol is None and summary.worst_symbol is None
