from webull_web import intent_store

FUTURE = "2099-01-01T00:00:00+00:00"
PAST = "2000-01-01T00:00:00+00:00"
NOW = "2026-06-18T00:00:00+00:00"


def test_save_assigns_id_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path))
    saved = intent_store.save_intent({"symbol": "NVDA", "status": "pending", "expires_at": FUTURE})
    assert saved["id"]
    assert intent_store.get_intent(saved["id"])["symbol"] == "NVDA"


def test_list_filters_pending_and_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path))
    intent_store.save_intent({"id": "a", "status": "pending", "expires_at": FUTURE})
    intent_store.save_intent({"id": "b", "status": "pending", "expires_at": PAST})       # expired
    intent_store.save_intent({"id": "c", "status": "dismissed", "expires_at": FUTURE})   # not pending
    assert {i["id"] for i in intent_store.list_intents(NOW)} == {"a"}


def test_set_status(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path))
    intent_store.save_intent({"id": "x", "status": "pending", "expires_at": FUTURE})
    intent_store.set_status("x", "consumed")
    assert intent_store.get_intent("x")["status"] == "consumed"


def test_get_missing_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path))
    import pytest
    with pytest.raises(FileNotFoundError):
        intent_store.get_intent("nope")


import importlib


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "intents"))
    from webull_web import intent_store
    return importlib.reload(intent_store)


def _mk(store, symbol, side, created_at, iid, expires="2999-01-01T00:00:00+00:00"):
    return store.save_intent({"id": iid, "symbol": symbol, "side": side, "status": "pending",
                              "created_at": created_at, "expires_at": expires})


def test_annotate_merges_onto_matching_pending_intent(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    _mk(s, "AMD", "BUY", "2026-07-07T10:00:00+00:00", "i1")
    out = s.annotate("amd", "BUY", "2026-07-07T12:00:00+00:00",
                     bear_case="earnings in 4d", red_team_verdict="caution")
    assert out is not None
    assert out["bear_case"] == "earnings in 4d" and out["red_team_verdict"] == "caution"
    assert s.get_intent("i1")["bear_case"] == "earnings in 4d"   # persisted, same id


def test_annotate_picks_most_recent_when_multiple(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    _mk(s, "GE", "BUY", "2026-07-07T09:00:00+00:00", "old")
    _mk(s, "GE", "BUY", "2026-07-07T11:00:00+00:00", "new")
    out = s.annotate("GE", "BUY", "2026-07-07T12:00:00+00:00", red_team_verdict="kill")
    assert out["id"] == "new"
    assert s.get_intent("old").get("red_team_verdict") is None


def test_annotate_returns_none_on_no_match(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    _mk(s, "AMD", "BUY", "2026-07-07T10:00:00+00:00", "i1")
    assert s.annotate("AMD", "SELL", "2026-07-07T12:00:00+00:00", x=1) is None   # wrong side
    assert s.annotate("TSLA", "BUY", "2026-07-07T12:00:00+00:00", x=1) is None   # wrong symbol


def test_annotate_ignores_non_pending_and_expired(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    _mk(s, "AMD", "BUY", "2026-07-07T10:00:00+00:00", "consumed")
    s.set_status("consumed", "consumed")
    _mk(s, "AMD", "BUY", "2026-07-07T09:00:00+00:00", "expired", expires="2000-01-01T00:00:00+00:00")
    assert s.annotate("AMD", "BUY", "2026-07-07T12:00:00+00:00", x=1) is None


def test_annotate_side_is_case_insensitive(tmp_path, monkeypatch):
    s = _store(tmp_path, monkeypatch)
    _mk(s, "AMD", "BUY", "2026-07-07T10:00:00+00:00", "i1")
    out = s.annotate("AMD", "buy", "2026-07-07T12:00:00+00:00", red_team_verdict="proceed")
    assert out is not None and out["id"] == "i1"
