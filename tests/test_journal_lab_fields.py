import pytest
from pydantic import ValidationError
from webull_api.journal.schema import ClosedTrade


def _base(**kw):
    base = dict(symbol="AAPL", source="paper", quantity=1.0, entry_price=10.0, exit_price=12.0,
                entry_at_iso="2026-01-01T00:00:00", exit_at_iso="2026-01-02T00:00:00",
                holding_days=1.0, pnl=2.0, return_pct=20.0, win=True)
    base.update(kw)
    return base


def test_legacy_closed_trade_without_lab_fields_still_validates():
    t = ClosedTrade(**_base())
    assert t.strategy_id is None and t.generation is None and t.cohort is None
    assert t.behavioral_cohort is None and t.trial_id is None
    assert t.instrument == "equity"   # existing default preserved


def test_paper_trial_source_is_accepted():
    t = ClosedTrade(**_base(source="paper_trial"))
    assert t.source == "paper_trial"


def test_lab_fields_round_trip():
    t = ClosedTrade(**_base(source="paper_trial", strategy_id="rec1", generation=3,
                            cohort="trend_follow:up/low", behavioral_cohort="b7", trial_id="t9"))
    again = ClosedTrade.model_validate(t.model_dump())
    assert again.strategy_id == "rec1" and again.generation == 3
    assert again.cohort == "trend_follow:up/low" and again.behavioral_cohort == "b7"
    assert again.trial_id == "t9"


def test_unknown_source_still_rejected():
    with pytest.raises(ValidationError):
        ClosedTrade(**_base(source="nonsense"))
