from webull_web import rsi2_store


class _Pos:
    def __init__(self, q):
        self.quantity = q


class _Acct:
    def __init__(self, positions):  # positions: {sym: qty}
        self.positions = {s: _Pos(q) for s, q in positions.items()}


def test_load_default_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path / "nope"))
    st = rsi2_store.load()
    assert st["owned_lots"] == [] and st["reconciliation_log"] == []


def test_reconcile_drops_vanished_lot(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path))
    st = {"owned_lots": [{"symbol": "VZ", "shares": 142}], "reconciliation_log": []}
    st, notes = rsi2_store.reconcile(st, _Acct({}), "2026-07-09")
    assert st["owned_lots"] == [] and len(notes) == 1 and "VZ" in notes[0]["action"]


def test_reconcile_keeps_matching_lot(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path))
    st = {"owned_lots": [{"symbol": "NVDA", "shares": 31}], "reconciliation_log": []}
    st, notes = rsi2_store.reconcile(st, _Acct({"NVDA": 31}), "2026-07-09")
    assert len(st["owned_lots"]) == 1 and notes == []


def test_reconcile_adjusts_quantity(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path))
    st = {"owned_lots": [{"symbol": "NVDA", "shares": 31}], "reconciliation_log": []}
    st, notes = rsi2_store.reconcile(st, _Acct({"NVDA": 20}), "2026-07-09")
    assert st["owned_lots"][0]["shares"] == 20 and len(notes) == 1


def test_reconcile_never_invents_from_manual_hold(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path))
    st = {"owned_lots": [], "reconciliation_log": []}
    st, notes = rsi2_store.reconcile(st, _Acct({"F": 100}), "2026-07-09")  # a manual hold in the account
    assert st["owned_lots"] == [] and notes == []


def test_record_entry_then_exit_roundtrips_through_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path))
    st = rsi2_store.load()
    rsi2_store.record_entry(st, symbol="AAPL", shares=30, entry_price=200.0,
                            entry_date="2026-07-09", paper_order_id="abc")
    rsi2_store.save(st, "2026-07-09T17:00:00-04:00")
    st2 = rsi2_store.load()
    assert st2["owned_lots"][0]["symbol"] == "AAPL" and st2["owned_lots"][0]["paper_order_id"] == "abc"
    assert st2["updated_at"] == "2026-07-09T17:00:00-04:00"
    rsi2_store.record_exit(st2, "AAPL")
    assert st2["owned_lots"] == []


def test_reconcile_ignores_extra_foreign_shares(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path))
    st = {"owned_lots": [{"symbol": "AAPL", "shares": 30}], "reconciliation_log": []}
    st, notes = rsi2_store.reconcile(st, _Acct({"AAPL": 130}), "2026-07-09")  # 100 foreign shares added
    assert st["owned_lots"][0]["shares"] == 30 and notes == []  # RSI2 still owns only its 30 (never adjusts UP)


def test_pending_roundtrip_and_adopt_filled_buy():
    from types import SimpleNamespace
    from webull_web import rsi2_store
    state = rsi2_store._default()
    rsi2_store.record_pending(state, paper_order_id="id1", symbol="AAPL", side="BUY",
                              placed_date="2026-07-27")
    order = SimpleNamespace(paper_order_id="id1", status="filled", quantity=10.0,
                            fill_price=204.5, fill_session="2026-07-28")
    state, notes = rsi2_store.adopt_settled(state, {"id1": order}, "2026-07-28")
    assert state["pending_orders"] == []
    assert len(state["owned_lots"]) == 1
    lot = state["owned_lots"][0]
    assert lot["symbol"] == "AAPL" and lot["entry_price"] == 204.5
    assert lot["entry_date"] == "2026-07-28" and lot["paper_order_id"] == "id1"
    assert len(notes) == 1


def test_adopt_filled_sell_removes_lot():
    from types import SimpleNamespace
    from webull_web import rsi2_store
    state = rsi2_store._default()
    rsi2_store.record_entry(state, symbol="AAPL", shares=10, entry_price=200.0,
                            entry_date="2026-07-20", paper_order_id="id0")
    rsi2_store.record_pending(state, paper_order_id="id2", symbol="AAPL", side="SELL",
                              placed_date="2026-07-27")
    order = SimpleNamespace(paper_order_id="id2", status="filled", quantity=10.0,
                            fill_price=210.0, fill_session="2026-07-28")
    state, _ = rsi2_store.adopt_settled(state, {"id2": order}, "2026-07-28")
    assert state["owned_lots"] == [] and state["pending_orders"] == []


def test_adopt_rejected_and_missing_drop_pending_no_lot():
    from types import SimpleNamespace
    from webull_web import rsi2_store
    state = rsi2_store._default()
    rsi2_store.record_pending(state, paper_order_id="idr", symbol="AAPL", side="BUY",
                              placed_date="2026-07-27")
    rsi2_store.record_pending(state, paper_order_id="idm", symbol="MSFT", side="BUY",
                              placed_date="2026-07-27")
    rejected = SimpleNamespace(paper_order_id="idr", status="rejected", quantity=10.0,
                               fill_price=None, fill_session=None)
    state, notes = rsi2_store.adopt_settled(state, {"idr": rejected}, "2026-07-28")
    assert state["owned_lots"] == []
    assert state["pending_orders"] == []          # missing id 'idm' dropped too (stale)
    assert len(notes) == 2


def test_adopt_still_pending_is_kept():
    from types import SimpleNamespace
    from webull_web import rsi2_store
    state = rsi2_store._default()
    rsi2_store.record_pending(state, paper_order_id="idp", symbol="AAPL", side="BUY",
                              placed_date="2026-07-27")
    order = SimpleNamespace(paper_order_id="idp", status="pending", quantity=10.0,
                            fill_price=None, fill_session=None)
    state, notes = rsi2_store.adopt_settled(state, {"idp": order}, "2026-07-28")
    assert len(state["pending_orders"]) == 1 and notes == []
