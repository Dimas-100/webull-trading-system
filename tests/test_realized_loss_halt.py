"""Realized loss folded into the autopilot daily-loss halt (I1). The pure journal helper +
the run.py gate integration. Add-only: the halt can only trip MORE, never less."""
import pytest

from webull_api.journal import pairing
from webull_api.journal.schema import Fill


def _fill(symbol, side, qty, price, at, *, source="real", account="ACC", fid=None):
    return Fill(id=fid or f"{symbol}-{side}-{at}", source=source, account_id=account,
                symbol=symbol, side=side, quantity=qty, price=price,
                filled_at_iso=at, order_type="MARKET")


# ── pure helper: pairing.realized_pnl_on_day ──────────────────────────────────

def test_realized_pnl_on_day_sums_todays_closes():
    fills = [
        _fill("AAA", "BUY", 100, 10.0, "2026-07-01T10:00:00-04:00"),
        _fill("AAA", "SELL", 100, 8.0, "2026-07-09T15:00:00-04:00"),  # -$200 closed 07-09
    ]
    assert pairing.realized_pnl_on_day(fills, "2026-07-09") == pytest.approx(-200.0)


def test_realized_pnl_on_day_excludes_other_days():
    fills = [
        _fill("AAA", "BUY", 100, 10.0, "2026-07-01T10:00:00-04:00"),
        _fill("AAA", "SELL", 100, 8.0, "2026-07-08T15:00:00-04:00"),  # closed 07-08, not 07-09
    ]
    assert pairing.realized_pnl_on_day(fills, "2026-07-09") == 0.0


def test_realized_pnl_on_day_filters_source_and_account():
    fills = [
        _fill("AAA", "BUY", 100, 10.0, "2026-07-01T10:00:00-04:00", source="paper"),
        _fill("AAA", "SELL", 100, 8.0, "2026-07-09T15:00:00-04:00", source="paper"),
        _fill("BBB", "BUY", 10, 10.0, "2026-07-01T10:00:00-04:00", account="OTHER"),
        _fill("BBB", "SELL", 10, 5.0, "2026-07-09T15:00:00-04:00", account="OTHER"),
    ]
    assert pairing.realized_pnl_on_day(fills, "2026-07-09", source="real", account_id="ACC") == 0.0


def test_realized_pnl_on_day_returns_gains_unclamped():
    # The pure helper returns the SIGNED realized P&L; clamping gains to zero is the caller's job.
    fills = [
        _fill("AAA", "BUY", 10, 10.0, "2026-07-01T10:00:00-04:00"),
        _fill("AAA", "SELL", 10, 13.0, "2026-07-09T15:00:00-04:00"),  # +$30
    ]
    assert pairing.realized_pnl_on_day(fills, "2026-07-09") == pytest.approx(30.0)


def test_realized_pnl_on_day_no_closes_is_zero():
    fills = [_fill("AAA", "BUY", 10, 10.0, "2026-07-09T10:00:00-04:00")]  # open only
    assert pairing.realized_pnl_on_day(fills, "2026-07-09") == 0.0
