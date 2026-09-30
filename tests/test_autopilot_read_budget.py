"""Broker-read budget of one autopilot run (the exits-stage 429, diagnosed 2026-09-17).

One run used to read GET /openapi/assets/positions THREE times in ~1.2 s — gate state,
PROTECT (reconcile.scan_unprotected) and EXITS (exits.scan) — plus open orders twice. A live
read-only probe showed the positions endpoint allows ~2 reads per 2–3 s, so the third read
drew `HTTP Status: 429, Code: TOO_MANY_REQUESTS` on 21 runs since 2026-07-29 and, being
unguarded, aborted the EXITS stage each time (a missed exit costs a day). These tests pin
the budget: positions and open orders are read ONCE at the top of the run and that snapshot
is handed to PROTECT and EXITS; the reads that remain ride the bounded throttle retry.

Composed like tests/test_autopilot_run.py: the REAL portfolio.get_positions,
trading.get_open_orders, reconcile.scan_unprotected, exits.scan, trading.place and
safety.should_submit all run — only the SDK client seam is a fake broker that counts every
call per endpoint. Bars are the one injected stub (exits.scan's default get_bars would reach
the network).
"""
from datetime import datetime

import pytest

from webull_api import exits, trading
from webull_api.autopilot import run as autorun
from webull_api.autopilot.config import AutopilotConfig

THROTTLED = "HTTP Status: 429, Code: TOO_MANY_REQUESTS, Msg: Too many requests"

# One whole share, bought at 25, marking 22 (-12%): past the -8% backstop, no resting stop.
BREACHED = [{"symbol": "MSFT", "instrument_type": "EQUITY", "quantity": "1",
             "cost_price": "25.00", "last_price": "22.00"}]


class _Resp:
    status_code = 200
    def __init__(self, body): self._b = body
    def json(self): return self._b


class _Broker:
    """Fake SDK client at the seam both reads share: counts calls per endpoint and raises the
    broker's real 429 on the positions reads whose 1-based index is in `throttle_positions_at`
    (and likewise for open orders). Every read records how many orders had been placed at
    that moment, so a test can bound the reads that happened BEFORE any placement."""

    def __init__(self, positions, open_orders=(), *, throttle_positions_at=(),
                 throttle_open_orders_at=()):
        self.positions, self.open_orders = positions, list(open_orders)
        self.throttle_positions_at = set(throttle_positions_at)
        self.throttle_open_orders_at = set(throttle_open_orders_at)
        self.reads = []        # (endpoint, placements_so_far)
        self.calls = []        # ("preview"|"place"|"cancel", side, symbol)
        self.account_v2 = self
        self.order_v2 = self

    def _placed(self):
        return sum(1 for c in self.calls if c[0] == "place")

    def count(self, endpoint, *, before_placement=False):
        return sum(1 for (e, placed) in self.reads
                   if e == endpoint and (placed == 0 or not before_placement))

    def get_account_position(self, account_id):
        self.reads.append(("positions", self._placed()))
        if self.count("positions") in self.throttle_positions_at:
            raise Exception(THROTTLED)
        return _Resp(self.positions)

    def get_order_open(self, account_id):
        self.reads.append(("open_orders", self._placed()))
        if self.count("open_orders") in self.throttle_open_orders_at:
            raise Exception(THROTTLED)
        return _Resp(list(self.open_orders))

    def preview_order(self, account_id, orders, client_combo_order_id=None):
        self.calls.append(("preview", orders[0].get("side"), orders[0].get("symbol")))
        return _Resp({"preview": True})

    def place_order(self, account_id, orders, client_combo_order_id=None):
        self.calls.append(("place", orders[0].get("side"), orders[0].get("symbol")))
        return _Resp({"order_id": "OID"})

    def cancel_order(self, account_id, client_order_id):
        self.calls.append(("cancel", None, client_order_id))
        return _Resp({"client_order_id": client_order_id})


def _cfg(**kw):
    base = dict(enabled=True, max_notional=40.0, max_positions=1, max_orders_per_day=5,
                daily_loss_halt=40.0, max_positions_risk_off=0, kill_file="/nonexistent/KILL",
                windows="00:00-23:59", notify="")
    base.update(kw)
    return AutopilotConfig(**base)


def _wire(broker, tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("POSITION_PLANS_DIR", str(tmp_path / "plans"))
    monkeypatch.setattr(autorun.portfolio, "trade_client", lambda: broker)
    monkeypatch.setattr(trading, "_resolve", lambda client, env: (broker, "prod"))
    monkeypatch.setattr(autorun, "_default_account", lambda *_a, **_k: "ACC")
    monkeypatch.setattr(autorun.journal_ingest, "sync_real", lambda ids: (0, None))
    monkeypatch.setattr(autorun, "_spy_risk_off", lambda *_a, **_k: False)
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])
    # The real exits.scan, minus its network-bound bar fetch (no closes -> no "break" signal).
    real_scan = exits.scan
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda acct, **k: real_scan(acct, get_bars=lambda *a, **kw: [], **k))
    sleeps = []
    monkeypatch.setattr(autorun, "_sleep", lambda s: sleeps.append(s), raising=False)
    return sleeps


def _run():
    return autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 9, 17, 9, 31))


def _audit_reasons(tmp_path):
    import json
    log = tmp_path / "log" / "2026-09-17.jsonl"
    if not log.exists():
        return []
    return [json.loads(l).get("reason") for l in log.read_text().splitlines() if l.strip()]


# ------------------------------------------------------ the budget: one run, one positions read

def test_run_completes_every_stage_within_the_read_budget(tmp_path, monkeypatch):
    """The live failure: the THIRD positions read of the run is throttled. The run must still
    finish PROTECT, EXITS, DECISIONS and ENTRIES — which it can only do by not making it."""
    broker = _Broker(BREACHED, throttle_positions_at={3})
    _wire(broker, tmp_path, monkeypatch)

    out = _run()

    assert out["errors"] == [], f"a stage aborted: {out['errors']}"
    assert broker.count("positions") <= 2, f"positions read {broker.count('positions')}x"
    assert broker.count("open_orders", before_placement=True) <= 2, \
        f"open orders read {broker.count('open_orders', before_placement=True)}x before placement"
    # PROTECT ran on the snapshot (the breached share got its backstop stop) ...
    assert ("place", "SELL", "MSFT") in broker.calls
    # ... and EXITS ran on the same snapshot, reaching its dedup against PROTECT.
    assert any(s["reason"] == "already actioned in PROTECT this run" for s in out["skipped"]), \
        f"EXITS never evaluated the position: {out['skipped']}"


# ------------------------------------------------- the remaining reads ride the bounded retry

def test_top_of_run_positions_read_survives_one_throttle(tmp_path, monkeypatch):
    """A 429 on the very first read must not fail the whole run closed: retry (bounded, 4 s),
    then share the successful read. Read #3 is throttled too — a run that still burst-read
    per stage would hit it."""
    broker = _Broker(BREACHED, throttle_positions_at={1, 3})
    sleeps = _wire(broker, tmp_path, monkeypatch)

    out = _run()

    assert out["errors"] == []
    assert broker.count("positions") == 2
    assert sleeps == [autorun._CLEAR_READ_WAIT_S]
    assert ("place", "SELL", "MSFT") in broker.calls
    assert "positions unreadable — decisions fail closed" not in _audit_reasons(tmp_path)


def test_top_of_run_open_orders_read_survives_one_throttle(tmp_path, monkeypatch):
    broker = _Broker([], throttle_open_orders_at={1})
    sleeps = _wire(broker, tmp_path, monkeypatch)

    out = _run()

    assert out["errors"] == []
    assert broker.count("open_orders") == 2
    assert sleeps == [autorun._CLEAR_READ_WAIT_S]
    assert not any("open orders unreadable" in str(s.get("reason")) for s in out["skipped"]), \
        "one throttled read must not fail entries closed for the run"


def test_top_of_run_retry_is_bounded_and_then_fails_closed(tmp_path, monkeypatch):
    """Throttled on every attempt: three tries, two waits, then the run treats positions as
    UNKNOWN — decisions fail closed, and PROTECT/EXITS record the failure as their own stage
    error rather than silently reading the book as flat. Nothing is placed."""
    broker = _Broker(BREACHED, throttle_positions_at={1, 2, 3, 4, 5, 6})
    sleeps = _wire(broker, tmp_path, monkeypatch)

    out = _run()

    assert broker.count("positions") == 3, "bounded — never an unbounded hammer on a throttled API"
    assert sleeps == [autorun._CLEAR_READ_WAIT_S] * 2
    assert {e.get("stage") for e in out["errors"]} == {"protect", "exits"}
    assert all("TOO_MANY_REQUESTS" in e["error"] for e in out["errors"])
    assert "positions unreadable — decisions fail closed" in _audit_reasons(tmp_path)
    assert [c for c in broker.calls if c[0] == "place"] == []


def test_non_throttle_read_error_is_not_retried(monkeypatch):
    """Only TOO_MANY_REQUESTS earns a retry; anything else propagates at once (no wait)."""
    reads, sleeps = [], []

    def boom(acct):
        reads.append(acct)
        raise RuntimeError("portfolio error 500: gateway")

    monkeypatch.setattr(autorun, "_sleep", lambda s: sleeps.append(s), raising=False)
    with pytest.raises(RuntimeError, match="500"):
        autorun._read_with_retry(boom, "ACC")
    assert reads == ["ACC"] and sleeps == []


def test_read_with_retry_returns_the_first_good_read(monkeypatch):
    reads, sleeps = [], []

    def flaky(acct):
        reads.append(acct)
        if len(reads) < 2:
            raise Exception(THROTTLED)
        return {"data": []}

    monkeypatch.setattr(autorun, "_sleep", lambda s: sleeps.append(s), raising=False)
    assert autorun._read_with_retry(flaky, "ACC") == {"data": []}
    assert len(reads) == 2 and sleeps == [autorun._CLEAR_READ_WAIT_S]
