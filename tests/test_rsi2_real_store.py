"""The REAL RSI2 ledger. Same discipline as the paper ledger: broker truth wins, a logged
intent is never a lot. Kept in its own file so real and paper lots can never co-mingle."""
import pytest

from webull_web import rsi2_real_store as store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.delenv("RSI2_REAL_STATE_DIR", raising=False)


def _pos(sym, qty, cost="100.00"):
    return {"symbol": sym, "quantity": str(qty), "cost_price": cost}


def test_load_defaults_when_absent():
    s = store.load()
    assert s["owned_lots"] == [] and s["pending_orders"] == []


def test_round_trip():
    s = store.load()
    s = store.record_entry(s, symbol="AAPL", shares=1, entry_price=303.0,
                           entry_date="2026-08-17", decision_id="d1")
    store.save(s, "2026-08-17T17:30:00")
    again = store.load()
    assert again["owned_lots"][0]["symbol"] == "AAPL"
    assert again["owned_lots"][0]["decision_id"] == "d1"
    assert again["updated_at"] == "2026-08-17T17:30:00"


def test_reconcile_drops_a_lot_the_broker_does_not_hold():
    s = store.load()
    s = store.record_entry(s, symbol="GOOG", shares=5, entry_price=340.0,
                           entry_date="2026-08-17", decision_id="d2")
    s, notes = store.reconcile(s, [_pos("AAPL", 1)], "2026-08-18")
    assert s["owned_lots"] == []
    assert any("GOOG" in n for n in notes)
    assert s["reconciliation_log"][-1]["date"] == "2026-08-18"


def test_reconcile_adjusts_a_changed_quantity():
    s = store.load()
    s = store.record_entry(s, symbol="AAPL", shares=5, entry_price=303.0,
                           entry_date="2026-08-17", decision_id="d3")
    s, notes = store.reconcile(s, [_pos("AAPL", 2)], "2026-08-18")
    assert s["owned_lots"][0]["shares"] == 2.0
    assert any("AAPL" in n for n in notes)


def test_reconcile_never_invents_a_lot():
    """A broker position the ledger doesn't know about is NOT adopted — attribution must be
    earned by this runner, or the sleeve guard would treat a hand-placed lot as RSI2-owned."""
    s, notes = store.reconcile(store.load(), [_pos("MSFT", 3)], "2026-08-18")
    assert s["owned_lots"] == []


def test_reconcile_leaves_a_matching_lot_untouched():
    s = store.load()
    s = store.record_entry(s, symbol="AAPL", shares=1, entry_price=303.0,
                           entry_date="2026-08-17", decision_id="d4")
    s, notes = store.reconcile(s, [_pos("AAPL", 1)], "2026-08-18")
    assert s["owned_lots"][0]["shares"] == 1 and notes == []


def test_corrupt_file_degrades_to_defaults(tmp_path):
    (tmp_path / "rsi2_real_state.json").write_text("{not json", encoding="utf-8")
    assert store.load()["owned_lots"] == []


def test_adopt_promotes_a_pending_row_the_broker_now_holds():
    s = store.record_pending(store.load(), decision_id="d9", symbol="AAPL", shares=1,
                             queued_date="2026-08-17")
    s, notes = store.adopt_filled(s, [_pos("AAPL", 1, cost="303.00")], "2026-08-18")
    assert s["owned_lots"][0]["symbol"] == "AAPL"
    assert s["owned_lots"][0]["shares"] == 1.0
    assert s["owned_lots"][0]["entry_price"] == 303.0     # cost basis from the broker
    assert s["owned_lots"][0]["decision_id"] == "d9"
    assert s["pending_orders"] == []
    assert any("AAPL" in n for n in notes)


def test_adopt_leaves_a_pending_row_the_broker_does_not_hold():
    """A queued decision is not a fill. It may place tomorrow — keep waiting, don't invent."""
    s = store.record_pending(store.load(), decision_id="d10", symbol="AAPL", shares=1,
                             queued_date="2026-08-17")
    s, notes = store.adopt_filled(s, [], "2026-08-18")
    assert s["owned_lots"] == [] and len(s["pending_orders"]) == 1


def test_adopt_never_double_counts_an_already_owned_symbol():
    s = store.record_entry(store.load(), symbol="AAPL", shares=1, entry_price=303.0,
                           entry_date="2026-08-17", decision_id="d11")
    s = store.record_pending(s, decision_id="d12", symbol="AAPL", shares=1,
                             queued_date="2026-08-18")
    s, _ = store.adopt_filled(s, [_pos("AAPL", 1)], "2026-08-19")
    assert len(s["owned_lots"]) == 1
    assert s["pending_orders"] == []      # the stale pending is cleared, not adopted


def test_adopt_expires_a_pending_row_older_than_the_retention_window():
    s = store.record_pending(store.load(), decision_id="d13", symbol="AAPL", shares=1,
                             queued_date="2026-08-01")
    s, notes = store.adopt_filled(s, [], "2026-08-18")
    assert s["pending_orders"] == []
    assert any("expired" in n for n in notes)


# ---- exit-row map (C3): the ONLY ids this runner may ever cancel ------------------------

def test_exit_row_map_defaults_to_empty_and_round_trips():
    assert store.load()["exit_rows"] == {}
    s = store.record_exit_row(store.load(), symbol="aapl", decision_id="x1")
    store.save(s, "2026-08-17T17:30:00")
    assert store.load()["exit_rows"] == {"AAPL": "x1"}


def test_orphan_exit_rows_lists_only_symbols_no_longer_owned():
    s = store.record_entry(store.load(), symbol="AAPL", shares=1, entry_price=303.0,
                           entry_date="2026-08-17", decision_id="d")
    s = store.record_exit_row(s, symbol="AAPL", decision_id="x1")
    s = store.record_exit_row(s, symbol="GOOG", decision_id="x2")
    assert store.orphan_exit_rows(s) == [("GOOG", "x2")]


def test_drop_exit_row_removes_only_that_symbol():
    s = store.record_exit_row(store.load(), symbol="AAPL", decision_id="x1")
    s = store.record_exit_row(s, symbol="GOOG", decision_id="x2")
    s = store.drop_exit_row(s, symbol="GOOG")
    assert s["exit_rows"] == {"AAPL": "x1"}


# ---- pending pruning on a terminal decision status (I1) --------------------------------

def test_prune_pendings_drops_a_pending_whose_decision_died():
    s = store.record_pending(store.load(), decision_id="d20", symbol="AAPL", shares=1,
                             queued_date="2026-08-17")
    s, notes = store.prune_pendings(s, {"d20": "expired"}, "2026-08-18")
    assert s["pending_orders"] == []
    assert any("AAPL" in n for n in notes)
    assert s["reconciliation_log"][-1]["date"] == "2026-08-18"


@pytest.mark.parametrize("status", ["failed", "cancelled", "EXPIRED"])
def test_prune_pendings_treats_every_terminal_status_the_same(status):
    s = store.record_pending(store.load(), decision_id="d21", symbol="AAPL", shares=1,
                             queued_date="2026-08-17")
    s, _ = store.prune_pendings(s, {"d21": status}, "2026-08-18")
    assert s["pending_orders"] == []


@pytest.mark.parametrize("status", ["queued", "placed", "placing", None])
def test_prune_pendings_keeps_a_live_or_unknown_decision(status):
    """A row still in flight keeps its slot — only adopt_filled's TTL may retire it."""
    s = store.record_pending(store.load(), decision_id="d22", symbol="AAPL", shares=1,
                             queued_date="2026-08-17")
    s, notes = store.prune_pendings(s, {"d22": status} if status else {}, "2026-08-18")
    assert len(s["pending_orders"]) == 1 and notes == []
