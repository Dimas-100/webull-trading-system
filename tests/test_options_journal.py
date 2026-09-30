"""Options trades in the unified Journal: engine P&L stamping -> normalizer -> store -> sync ->
analytics. Mirrors the equity auto-journal feature (2026-06-25)."""
import pytest

from webull_api.journal import analytics, normalize
from webull_api.journal.schema import OptionTrade
from webull_api.paper import options_engine as eng
from webull_web import journal_ingest, journal_store

C300 = "AAPL260717C00300000"
C310 = "AAPL260717C00310000"
MARKS = {
    C300: {"bid": 5.0, "ask": 5.4, "last": 5.2, "mid": 5.2},
    C310: {"bid": 2.0, "ask": 2.4, "last": 2.2, "mid": 2.2},
}


def _open_fill_single(cash=10000.0):
    acct = eng.open_account(cash, "t0")
    acct, _ = eng.place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}], quantity=1,
                        intent="OPEN", order_type="MARKET", time_in_force="DAY",
                        now_iso="t0", today_et="2026-07-01", marks=MARKS)
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={}, now_iso="t1", today_et="2026-07-01")  # fill @ ask 5.4
    return acct


def _closed_single_account():
    """Long single bought @ 5.4, sold @ bid 7.0 -> +160 realized."""
    acct = _open_fill_single()
    unit = acct.positions[0]
    rich = {**MARKS, C300: {"bid": 7.0, "ask": 7.4, "last": 7.2, "mid": 7.2}}
    acct, _ = eng.place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "SELL"}], quantity=1,
                        intent="CLOSE", close_unit_id=unit.unit_id, order_type="MARKET",
                        time_in_force="DAY", now_iso="t2", today_et="2026-07-01", marks=rich)
    acct, _ = eng.evaluate(acct, marks=rich, spots={}, now_iso="t3", today_et="2026-07-01")
    return acct


# ── engine: realized-P&L stamp on the close / expiration record ────────────────

def test_close_stamps_realized_pnl_on_record():
    acct = _closed_single_account()
    rec = acct.history[-1]
    assert rec.status == "filled" and rec.intent == "CLOSE"
    assert rec.realized_pnl == pytest.approx((7.0 - 5.4) * 100)   # +160
    assert rec.open_net_price == pytest.approx(5.4)
    assert rec.close_value == pytest.approx(7.0)
    assert rec.closed_qty == 1
    assert rec.unit_opened_at == "t1"                              # the open's fill time
    assert rec.risk_basis == pytest.approx(5.4 * 100)             # debit premium


def test_expiration_stamps_realized_pnl_on_record():
    acct = _open_fill_single()
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={"AAPL": 320.0}, now_iso="t2", today_et="2026-07-18")
    rec = acct.history[-1]
    assert rec.status == "expired"
    assert rec.realized_pnl == pytest.approx((20.0 - 5.4) * 100)  # intrinsic 20 − debit 5.4
    assert rec.open_net_price == pytest.approx(5.4) and rec.close_value == pytest.approx(20.0)
    assert rec.closed_qty == 1 and rec.unit_opened_at == "t1"


def test_partial_close_stamps_closed_qty_and_credit_basis():
    acct = eng.open_account(20000.0, "t0")
    # credit vertical qty 2: BUY C310 @2.4, SELL C300 @5.0 -> net −2.6 (credit); collateral (10−2.6)*100*2=1480
    acct, _ = eng.place(acct, strategy="VERTICAL", quantity=2, intent="OPEN", order_type="MARKET",
                        time_in_force="DAY", now_iso="t0", today_et="2026-07-01", marks=MARKS,
                        legs=[{"occ": C310, "side": "BUY"}, {"occ": C300, "side": "SELL"}])
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={}, now_iso="t1", today_et="2026-07-01")
    unit = acct.positions[0]
    # close 1 of 2: BUY C300 @5.4, SELL C310 @2.0 -> net +3.4 -> value −3.4 -> pnl (−3.4−(−2.6))*100 = −80
    acct, _ = eng.place(acct, strategy="VERTICAL", quantity=1, intent="CLOSE", close_unit_id=unit.unit_id,
                        order_type="MARKET", time_in_force="DAY", now_iso="t2", today_et="2026-07-01",
                        marks=MARKS, legs=[{"occ": C300, "side": "BUY"}, {"occ": C310, "side": "SELL"}])
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={}, now_iso="t3", today_et="2026-07-01")
    rec = acct.history[-1]
    assert rec.closed_qty == 1
    assert rec.open_net_price == pytest.approx(-2.6)
    assert rec.realized_pnl == pytest.approx(-80.0)
    assert rec.risk_basis == pytest.approx(1480.0 * 0.5)          # collateral for the closed half


# ── normalizer ─────────────────────────────────────────────────────────────────

def test_normalizer_builds_option_trade_from_close():
    trades = normalize.option_trades_from_options_account(_closed_single_account())
    assert len(trades) == 1
    t = trades[0]
    assert t.underlying == "AAPL" and t.strategy == "SINGLE" and t.reason == "closed"
    assert t.quantity == 1 and t.pnl == pytest.approx(160.0) and t.win is True
    assert t.return_pct == pytest.approx(160.0 / (5.4 * 100) * 100)
    assert "AAPL" in t.legs_desc and "300C" in t.legs_desc


def test_normalizer_skips_open_and_unstamped():
    # An opened-but-not-closed account: history has only the filled OPEN (no P&L stamp).
    assert normalize.option_trades_from_options_account(_open_fill_single()) == []


def test_normalizer_honors_skip_ids():
    acct = _closed_single_account()
    rid = acct.history[-1].paper_order_id
    assert normalize.option_trades_from_options_account(acct, skip_ids={rid}) == []


def test_normalizer_marks_expired_reason():
    acct = _open_fill_single()
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={"AAPL": 320.0}, now_iso="t2", today_et="2026-07-18")
    trades = normalize.option_trades_from_options_account(acct)
    assert len(trades) == 1 and trades[0].reason == "expired"


# ── store ──────────────────────────────────────────────────────────────────────

def _ot(id="o1", pnl=160.0, win=True, strategy="SINGLE", underlying="AAPL"):
    return OptionTrade(id=id, underlying=underlying, strategy=strategy,
                       legs_desc=f"{underlying} +300C 2026-07-17", quantity=1, open_net_price=5.4,
                       close_value=7.0, opened_at_iso="2026-07-01T00:00:00+00:00",
                       closed_at_iso="2026-07-03T00:00:00+00:00", pnl=pnl, win=win,
                       return_pct=29.6, reason="closed")


def test_store_append_load_dedup(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    assert journal_store.append_option_trades([_ot("a"), _ot("b")]) == 2
    assert journal_store.append_option_trades([_ot("a")]) == 0          # dedup by id
    assert {t.id for t in journal_store.load_option_trades()} == {"a", "b"}
    assert journal_store.existing_option_trade_ids() == {"a", "b"}


# ── sync ───────────────────────────────────────────────────────────────────────

def test_sync_options_paper_appends_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    acct = _closed_single_account()
    n, err = journal_ingest.sync_options_paper(options_load=lambda: acct)
    assert err is None and n == 1
    n2, _ = journal_ingest.sync_options_paper(options_load=lambda: acct)
    assert n2 == 0                                                       # idempotent
    assert len(journal_store.load_option_trades()) == 1


def test_sync_options_paper_no_account_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    n, err = journal_ingest.sync_options_paper(options_load=lambda: None)
    assert n == 0 and err is None


def test_sync_options_paper_end_to_end_via_real_store(tmp_path, monkeypatch):
    """Full chain through the REAL options-paper store + default load: persist a closed account,
    then sync -> an OptionTrade lands in the journal the web app reads."""
    monkeypatch.setenv("PAPER_DIR", str(tmp_path / "paper"))
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    from webull_web import paper_options_service
    paper_options_service.save_account(_closed_single_account())
    n, err = journal_ingest.sync_options_paper()                        # real default load_account
    assert err is None and n == 1
    trades = journal_store.load_option_trades()
    assert len(trades) == 1 and trades[0].underlying == "AAPL" and trades[0].pnl > 0


# ── analytics: unified ledger ──────────────────────────────────────────────────

def test_build_summary_merges_option_trades():
    summary = analytics.build_summary([], [], option_trades=[_ot("a", pnl=160.0, win=True),
                                                             _ot("b", pnl=-50.0, win=False)])
    assert summary.overall.trades == 2 and summary.overall.total_pnl == pytest.approx(110.0)
    assert "paper_option" in {b.key for b in summary.by_source}
    assert "option single" in {b.key for b in summary.by_setup}
    ct = next(t for t in summary.closed_trades if t.source == "paper_option")
    assert ct.instrument == "option" and ct.option_strategy == "SINGLE" and ct.legs_desc


def test_build_summary_without_options_is_unchanged():
    summary = analytics.build_summary([], [])
    assert summary.overall.trades == 0 and summary.closed_trades == []


def test_normalizer_carries_settle_basis_to_journal():
    """The expiration settlement stamps which price basis it used; the Journal record carries it
    (honest about exp-day close vs the refresh-time spot approximation)."""
    acct = _open_fill_single()
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={},
                           settle_spots={("AAPL", "2026-07-17"): 320.0},
                           now_iso="t2", today_et="2026-07-18")
    trades = normalize.option_trades_from_options_account(acct)
    assert len(trades) == 1 and trades[0].reason == "expired"
    assert trades[0].settle_basis == "exp_close"
    # and it flows into the unified ClosedTrade ledger
    summary = analytics.build_summary([], [], option_trades=trades)
    assert summary.closed_trades[0].settle_basis == "exp_close"


def test_normalizer_settle_basis_none_on_plain_close():
    trades = normalize.option_trades_from_options_account(_closed_single_account())
    assert trades[0].reason == "closed" and trades[0].settle_basis is None
