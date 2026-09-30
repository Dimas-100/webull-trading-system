"""exec_ledger — append-only decision-mark store (execution-quality slice)."""
import json

from webull_api import exec_ledger


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBULL_EXEC_DIR", str(tmp_path))


def test_append_dedups_by_id_and_load_roundtrips(tmp_path, monkeypatch):
    _use_tmp(monkeypatch, tmp_path)
    a = {"id": "A", "symbol": "AAPL"}
    b = {"id": "B", "symbol": "MSFT"}
    assert exec_ledger.append([a, b]) == 2
    assert exec_ledger.append([a]) == 0          # already on disk
    assert [r["id"] for r in exec_ledger.load()] == ["A", "B"]


def test_load_skips_corrupt_lines(tmp_path, monkeypatch):
    _use_tmp(monkeypatch, tmp_path)
    exec_ledger.append([{"id": "A"}])
    f = tmp_path / "decisions.jsonl"
    f.write_text(f.read_text(encoding="utf-8") + "{not json\n" + json.dumps({"id": "B"}) + "\n",
                 encoding="utf-8")
    assert [r["id"] for r in exec_ledger.load()] == ["A", "B"]


def test_record_submit_builds_record_with_mark(tmp_path, monkeypatch):
    _use_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(exec_ledger, "_mark", lambda symbol: 100.5)
    order = {"client_order_id": "OID1", "symbol": "aapl", "side": "BUY",
             "order_type": "LIMIT", "quantity": "2", "limit_price": "99.5"}
    assert exec_ledger.record_submit("ACC", order, env="prod") == 1
    rec = exec_ledger.load()[0]
    assert rec["id"] == "OID1" and rec["symbol"] == "AAPL" and rec["mark"] == 100.5
    assert rec["limit_price"] == "99.5" and rec["stop_price"] is None and rec["env"] == "prod"
    assert rec["side"] == "BUY" and rec["order_type"] == "LIMIT" and rec["ts"]


def test_record_submit_survives_mark_failure_and_skips_missing_id(tmp_path, monkeypatch):
    _use_tmp(monkeypatch, tmp_path)

    def boom(symbol):
        raise RuntimeError("no market data")

    monkeypatch.setattr("webull_api.market_data.spot_price", boom)
    order = {"client_order_id": "OID2", "symbol": "AAPL", "side": "BUY",
             "order_type": "MARKET", "quantity": "1"}
    assert exec_ledger.record_submit("ACC", order, env="prod") == 1
    assert exec_ledger.load()[0]["mark"] is None
    assert exec_ledger.record_submit("ACC", {"symbol": "AAPL"}, env="prod") == 0


def test_mark_is_bounded_when_market_data_hangs(monkeypatch):
    import time

    def hang(symbol):
        time.sleep(10)
        return 1.0

    monkeypatch.setattr("webull_api.market_data.spot_price", hang)
    monkeypatch.setattr(exec_ledger, "_MARK_TIMEOUT_S", 0.2)
    t0 = time.monotonic()
    assert exec_ledger._mark("AAPL") is None
    assert time.monotonic() - t0 < 2.0
