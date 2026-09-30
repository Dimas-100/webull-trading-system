from webull_web import journal_ingest, journal_store


def test_sync_all_ingests_each_source_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))

    real_raw = {"data": [{"client_order_id": "c1", "symbol": "AAPL", "side": "BUY",
                          "status": "FILLED", "order_type": "MARKET", "filled_quantity": "1",
                          "filled_price": "100", "filled_time_at": "2026-06-15T14:00:00+00:00"}]}
    paper_acct = {"account_id": "default", "starting_cash": 1000.0, "cash": 900.0, "positions": {},
                  "open_orders": [], "realized_pnl": 0.0, "created_at": "t", "updated_at": "t",
                  "history": [{"paper_order_id": "p1", "symbol": "AAPL", "side": "BUY",
                               "order_type": "MARKET", "quantity": 1.0, "limit_price": None,
                               "time_in_force": "DAY", "status": "filled", "created_at": "t",
                               "placed_et_date": "2026-06-15", "filled_at": "2026-06-15T14:00:00+00:00",
                               "fill_price": 100.0}]}

    appended, errors = journal_ingest.sync_all(
        ["ACC1"],
        history_fn=lambda aid: real_raw,
        paper_load=lambda: paper_acct,
        options_load=lambda: None,
        practice_list=lambda: [],
        practice_get=lambda sid: {},
        bars_fn=lambda *a, **k: [],  # context degrades to None
    )
    assert errors == {}
    assert appended == {"real": 1, "paper": 1, "options": 0, "practice": 0}

    again, _ = journal_ingest.sync_all(
        ["ACC1"], history_fn=lambda aid: real_raw, paper_load=lambda: paper_acct,
        options_load=lambda: None, practice_list=lambda: [], practice_get=lambda sid: {},
        bars_fn=lambda *a, **k: [])
    assert again == {"real": 0, "paper": 0, "options": 0, "practice": 0}  # idempotent
    assert len(journal_store.load_fills()) == 2


def test_sync_all_isolates_a_failing_source(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))

    def boom(aid):
        raise RuntimeError("webull down")

    paper_acct = {"account_id": "default", "starting_cash": 1000.0, "cash": 1000.0, "positions": {},
                  "open_orders": [], "realized_pnl": 0.0, "created_at": "t", "updated_at": "t",
                  "history": []}
    appended, errors = journal_ingest.sync_all(
        ["ACC1"], history_fn=boom, paper_load=lambda: paper_acct, options_load=lambda: None,
        practice_list=lambda: [], practice_get=lambda sid: {}, bars_fn=lambda *a, **k: [])
    assert "real" in errors and "webull down" in errors["real"]
    assert appended["paper"] == 0 and appended["practice"] == 0  # others still ran


def test_context_at_returns_none_on_empty_bars(monkeypatch):
    ctx = journal_ingest._context_at("AAPL", "2026-06-15T14:00:00+00:00", {},
                                     bars_fn=lambda *a, **k: [])
    assert ctx is None


# --- per-source syncs (used by the MCP auto-journal hooks) ---

_PAPER_ACCT = {"account_id": "default", "starting_cash": 1000.0, "cash": 900.0, "positions": {},
               "open_orders": [], "realized_pnl": 0.0, "created_at": "t", "updated_at": "t",
               "history": [{"paper_order_id": "p1", "symbol": "AAPL", "side": "BUY",
                            "order_type": "MARKET", "quantity": 1.0, "limit_price": None,
                            "time_in_force": "DAY", "status": "filled", "created_at": "t",
                            "placed_et_date": "2026-06-15", "filled_at": "2026-06-15T14:00:00+00:00",
                            "fill_price": 100.0}]}

_REAL_RAW = {"data": [{"client_order_id": "c1", "symbol": "AAPL", "side": "BUY", "status": "FILLED",
                       "order_type": "MARKET", "filled_quantity": "1", "filled_price": "100",
                       "filled_time_at": "2026-06-15T14:00:00+00:00"}]}


def test_sync_paper_appends_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    n, err = journal_ingest.sync_paper(paper_load=lambda: _PAPER_ACCT, bars_fn=lambda *a, **k: [])
    assert err is None and n == 1
    n2, _ = journal_ingest.sync_paper(paper_load=lambda: _PAPER_ACCT, bars_fn=lambda *a, **k: [])
    assert n2 == 0  # idempotent — already journaled
    assert len(journal_store.load_fills()) == 1


def test_sync_paper_no_account_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    n, err = journal_ingest.sync_paper(paper_load=lambda: None, bars_fn=lambda *a, **k: [])
    assert n == 0 and err is None


def test_sync_real_appends_and_keeps_partial_count_on_error(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))

    def hist(aid):
        if aid == "BAD":
            raise RuntimeError("webull down")
        return _REAL_RAW

    n, err = journal_ingest.sync_real(["ACC1", "BAD"], history_fn=hist, bars_fn=lambda *a, **k: [])
    assert n == 1 and err is not None and "webull down" in err  # ACC1's fill kept despite BAD raising


def test_skip_ids_avoids_context_fetch_for_journaled_fills(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    calls = []

    def bars(*a, **k):
        calls.append(a)
        return [{"time": "2026-06-15", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}]

    journal_ingest.sync_paper(paper_load=lambda: _PAPER_ACCT, bars_fn=bars)
    first = len(calls)
    assert first >= 1  # context fetched for the new fill
    journal_ingest.sync_paper(paper_load=lambda: _PAPER_ACCT, bars_fn=bars)
    assert len(calls) == first  # p1 already journaled -> skipped before any bars fetch
