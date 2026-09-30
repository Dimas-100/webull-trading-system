import json
from webull_api import decisions_exec


def _eq_row(**kw):
    row = {"asset": "EQUITY", "symbol": "FBTC", "side": "SELL", "qty": "ALL",
           "order_type": "MARKET", "trigger": {"kind": "green_day"}, "expires": "2026-08-14"}
    row.update(kw)
    return row


def test_append_stamps_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    stored = decisions_exec.append(_eq_row())
    assert stored["id"] and stored["status"] == "queued" and stored["ts"]
    rows, errs = decisions_exec.load()
    assert errs == [] and rows[0]["symbol"] == "FBTC"


def test_load_failclosed_on_malformed(tmp_path, monkeypatch):
    p = tmp_path / "q.jsonl"
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(p))
    good = decisions_exec.append(_eq_row())
    p.write_text(p.read_text() + "not json\n" + json.dumps({"id": "x", "status": "queued"}) + "\n")
    rows, errs = decisions_exec.load()
    assert [r["id"] for r in rows] == [good["id"]] and len(errs) == 2


def test_mark_flips_only_target(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    a = decisions_exec.append(_eq_row())
    b = decisions_exec.append(_eq_row(symbol="AAPL", qty="1"))
    assert decisions_exec.mark(a["id"], "placed", "ok")
    rows, _ = decisions_exec.load()
    by = {r["id"]: r for r in rows}
    assert by[a["id"]]["status"] == "placed" and by[b["id"]]["status"] == "queued"
    assert decisions_exec.mark("nope", "failed") is False


def test_option_row_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    decisions_exec.append({"asset": "OPTION", "symbol": "F", "side": "BUY",
                           "trigger": {"kind": "immediate"}, "expires": "2026-08-07",
                           "option": {"expiry": "2026-09-18", "strike": 14.0, "right": "C",
                                      "quantity": "1", "limit_price": "0.50"}})
    rows, errs = decisions_exec.load()
    assert errs == [] and rows[0]["option"]["right"] == "C"


def test_invalid_asset_rejected_at_append(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    import pytest
    with pytest.raises(ValueError):
        decisions_exec.append(_eq_row(asset="CRYPTO"))


def test_lock_released_after_operations(tmp_path, monkeypatch):
    p = tmp_path / "q.jsonl"
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(p))
    a = decisions_exec.append(_eq_row())
    decisions_exec.mark(a["id"], "placed")
    assert not (tmp_path / "q.jsonl.lock").exists()


def test_nan_prices_rejected_at_append(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    import pytest
    with pytest.raises(ValueError):
        decisions_exec.append(_eq_row(qty="nan"))
    with pytest.raises(ValueError):
        decisions_exec.append({"asset": "OPTION", "symbol": "F", "side": "BUY",
                               "trigger": {"kind": "immediate"}, "expires": "2026-08-07",
                               "option": {"expiry": "2026-09-18", "strike": 14.0, "right": "C",
                                          "quantity": "1", "limit_price": "nan"}})


def test_append_strips_executor_owned_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    stored = decisions_exec.append(_eq_row(side="BUY", qty="1",
                                           first_seen="2020-01-01T00:00:00",
                                           acted_ts="2020-01-01T00:00:00", detail="forged"))
    assert "first_seen" not in stored and "acted_ts" not in stored and "detail" not in stored
    rows, _ = decisions_exec.load()
    assert "first_seen" not in rows[0]


def test_stamp_first_seen_sets_once(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    a = decisions_exec.append(_eq_row(side="BUY", qty="1"))
    assert decisions_exec.stamp_first_seen(a["id"], "2026-08-07T12:00:00") is True
    assert decisions_exec.stamp_first_seen(a["id"], "2026-08-07T13:00:00") is False  # already set
    assert decisions_exec.stamp_first_seen("nope", "2026-08-07T12:00:00") is False
    rows, _ = decisions_exec.load()
    assert rows[0]["first_seen"] == "2026-08-07T12:00:00"
    assert rows[0]["status"] == "queued"  # status untouched


def test_stale_lock_is_broken_not_deadlocked(tmp_path, monkeypatch):
    import os
    import time
    p = tmp_path / "q.jsonl"
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(p))
    lock = tmp_path / "q.jsonl.lock"
    lock.write_text("")
    old = time.time() - 120
    os.utime(lock, (old, old))
    stored = decisions_exec.append(_eq_row())   # must break the stale lock, not hang/raise
    assert stored["status"] == "queued" and not lock.exists()
