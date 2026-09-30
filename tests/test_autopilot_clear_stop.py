"""Cancel-then-sell (2026-08-15 spec, BLOCKER section).

A resting protective GTC stop commits the shares, so any additional SELL is rejected
`417 OAUTH_OPENAPI_ORDER_NOT_SUPPORT_REVERSE_OPTION` — logged live on 2026-08-13 and 2026-08-14.
Closing SELLs therefore clear the resting stop first, inside _try_place and only AFTER the gate
has allowed, so a failed clear leaves protection untouched rather than stripping it.
"""
from datetime import datetime

import pytest

from webull_api import exits, reconcile, trading
from webull_api.autopilot import run as autorun
from webull_api.autopilot.config import AutopilotConfig

# The verbatim combo envelope read from the real account on 2026-08-15.
RESTING = [{
    "client_order_id": "a0fe202fc60a4e49a43d50fabca47098",
    "combo_type": "NORMAL",
    "combo_order_id": "I8I4M2330LPFCCF4VC1B07IISB",
    "orders": [{"symbol": "AAPL", "side": "SELL", "status": "SUBMITTED",
                "order_type": "STOP_LOSS", "instrument_type": "EQUITY",
                "client_order_id": "a0fe202fc60a4e49a43d50fabca47098",
                "total_quantity": "1", "time_in_force": "GTC", "stop_price": "278.76"}],
}]


class _Resp:
    status_code = 200
    def __init__(self, body): self._b = body
    def json(self): return self._b


class _Ops:
    def __init__(self): self.calls = []
    def preview_order(self, account_id, orders, client_combo_order_id=None):
        self.calls.append(("preview", orders[0].get("side"), orders[0].get("symbol")))
        return _Resp({"preview": True})
    def place_order(self, account_id, orders, client_combo_order_id=None):
        self.calls.append(("place", orders[0].get("side"), orders[0].get("symbol")))
        return _Resp({"order_id": "OID"})


class _Client:
    def __init__(self): self.order_v2 = _Ops()


def _cfg(**kw):
    base = dict(enabled=True, max_notional=40.0, max_positions=1, max_orders_per_day=5,
                daily_loss_halt=40.0, max_positions_risk_off=0, kill_file="/nonexistent/KILL",
                windows="00:00-23:59", notify="")
    base.update(kw)
    return AutopilotConfig(**base)


# ---------------------------------------------------------------- pure: which legs are stops

def test_resting_stop_orders_finds_the_combo_nested_leg():
    rows = reconcile.resting_stop_orders(RESTING, "AAPL")
    assert len(rows) == 1
    assert rows[0]["client_order_id"] == "a0fe202fc60a4e49a43d50fabca47098"


def test_resting_stop_orders_ignores_non_stops_buys_and_other_symbols():
    payload = [
        {"symbol": "AAPL", "side": "BUY", "order_type": "STOP_LOSS", "client_order_id": "b"},
        {"symbol": "AAPL", "side": "SELL", "order_type": "LIMIT", "client_order_id": "c"},
        {"symbol": "MSFT", "side": "SELL", "order_type": "STOP_LOSS", "client_order_id": "d"},
    ]
    assert reconcile.resting_stop_orders(payload, "AAPL") == []


# ---------------------------------------------------------------- the clearer itself

def test_clear_returns_true_and_cancels_nothing_when_no_stop_rests(monkeypatch):
    cancels = []
    monkeypatch.setattr(autorun.trading, "get_open_orders", lambda a: [])
    monkeypatch.setattr(autorun.trading, "cancel", lambda a, cid: cancels.append(cid))
    ok, why = autorun._clear_resting_stop("ACC", "AAPL", day="2026-08-17")
    assert ok is True and cancels == []


def test_clear_cancels_then_verifies_against_the_broker(monkeypatch):
    reads, cancels = [], []

    def get_open(a):
        reads.append(a)
        return RESTING if len(reads) == 1 else []       # gone on the verification read

    monkeypatch.setattr(autorun.trading, "get_open_orders", get_open)
    monkeypatch.setattr(autorun.trading, "cancel", lambda a, cid: cancels.append(cid))
    ok, why = autorun._clear_resting_stop("ACC", "AAPL", day="2026-08-17")
    assert ok is True
    assert cancels == ["a0fe202fc60a4e49a43d50fabca47098"]
    assert len(reads) == 2, "the broker, not the cancel response, is the truth"


def test_clear_fails_closed_when_the_stop_survives_the_cancel(monkeypatch):
    """A cancel that 'succeeded' but left the order resting must NOT let a SELL through."""
    monkeypatch.setattr(autorun.trading, "get_open_orders", lambda a: RESTING)
    monkeypatch.setattr(autorun.trading, "cancel", lambda a, cid: {"ok": True})
    ok, why = autorun._clear_resting_stop("ACC", "AAPL", day="2026-08-17")
    assert ok is False and "still resting" in why


def test_clear_fails_closed_when_cancel_raises(monkeypatch):
    def boom(a, cid):
        raise Exception("HTTP Status: 500")
    monkeypatch.setattr(autorun.trading, "get_open_orders", lambda a: RESTING)
    monkeypatch.setattr(autorun.trading, "cancel", boom)
    ok, why = autorun._clear_resting_stop("ACC", "AAPL", day="2026-08-17")
    assert ok is False and "cancel failed" in why


def test_clear_fails_closed_when_open_orders_are_unreadable(monkeypatch):
    def boom(a):
        raise Exception("429 TOO_MANY_REQUESTS")
    monkeypatch.setattr(autorun.trading, "get_open_orders", boom)
    monkeypatch.setattr(autorun, "_sleep", lambda s: None, raising=False)
    ok, why = autorun._clear_resting_stop("ACC", "AAPL", day="2026-08-17")
    assert ok is False and "unreadable" in why


# ------------------------------------------- transient-read retries (2026-08-18 429 miss)

def test_clear_read_retries_a_transient_429_then_succeeds(monkeypatch):
    """One throttled read must not cost the exit a full day (live 2026-08-18 17:45)."""
    reads, sleeps = [], []

    def get_open(a):
        reads.append(a)
        if len(reads) < 3:
            raise Exception("HTTP Status: 429, Code: TOO_MANY_REQUESTS, Msg: Too many req")
        return []

    monkeypatch.setattr(autorun.trading, "get_open_orders", get_open)
    monkeypatch.setattr(autorun, "_sleep", lambda s: sleeps.append(s), raising=False)
    ok, why = autorun._clear_resting_stop("ACC", "AAPL", day="2026-08-18")
    assert ok is True
    assert len(reads) == 3
    assert len(sleeps) == 2, "waits between attempts, none after the last"


def test_clear_read_fails_closed_after_exhausting_retries(monkeypatch):
    reads = []

    def boom(a):
        reads.append(a)
        raise Exception("HTTP Status: 429, Code: TOO_MANY_REQUESTS")

    monkeypatch.setattr(autorun.trading, "get_open_orders", boom)
    monkeypatch.setattr(autorun, "_sleep", lambda s: None, raising=False)
    ok, why = autorun._clear_resting_stop("ACC", "AAPL", day="2026-08-18")
    assert ok is False and "unreadable" in why
    assert len(reads) == 3, "bounded retries — never an unbounded hammer on a throttled API"


def test_clear_verification_read_also_retries(monkeypatch):
    """The post-cancel proof read is as exit-critical as the first read."""
    reads, cancels, sleeps = [], [], []

    def get_open(a):
        reads.append(a)
        if len(reads) == 1:
            return RESTING
        if len(reads) < 4:
            raise Exception("HTTP Status: 429, Code: TOO_MANY_REQUESTS")
        return []

    monkeypatch.setattr(autorun.trading, "get_open_orders", get_open)
    monkeypatch.setattr(autorun.trading, "cancel", lambda a, cid: cancels.append(cid))
    monkeypatch.setattr(autorun, "_sleep", lambda s: sleeps.append(s), raising=False)
    ok, why = autorun._clear_resting_stop("ACC", "AAPL", day="2026-08-18")
    assert ok is True
    assert cancels == ["a0fe202fc60a4e49a43d50fabca47098"]
    assert len(reads) == 4


def test_clear_refuses_a_stop_with_no_cancellable_id(monkeypatch):
    anon = [{"symbol": "AAPL", "side": "SELL", "order_type": "STOP_LOSS", "total_quantity": "1"}]
    monkeypatch.setattr(autorun.trading, "get_open_orders", lambda a: anon)
    ok, why = autorun._clear_resting_stop("ACC", "AAPL", day="2026-08-17")
    assert ok is False and "client_order_id" in why


# ---------------------------------------------------------------- wired through the stages

@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    c = _Client()
    monkeypatch.setattr(trading, "_resolve", lambda client, env: (c, "prod"))
    monkeypatch.setattr(autorun, "_default_account", lambda *_a, **_k: "ACC")
    monkeypatch.setattr(autorun.journal_ingest, "sync_real", lambda ids: (0, None))
    monkeypatch.setattr(autorun.portfolio, "get_positions",
                        lambda acct: [{"symbol": "AAPL", "quantity": "1", "cost_price": "303.00",
                                       "last_price": "250.00", "instrument_type": "EQUITY"}])
    monkeypatch.setattr(autorun, "_spy_risk_off", lambda *_a, **_k: False)
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected", lambda *a, **k: [])
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])
    # A -8% stop signal: unconditional, so it reaches _try_place regardless of the sleeve guard.
    sig = exits.ExitSignal(symbol="AAPL", qty=1.0, last=250.0, unrealized_pct=-17.5,
                           reason="stop", reasons=["stop"], detail="AAPL past stop")
    monkeypatch.setattr(autorun.exits, "scan", lambda *a, **k: [exits.ExitRow("AAPL", 1.0, sig)])
    return c


def test_exit_clears_the_resting_stop_before_selling(wired, monkeypatch):
    reads, cancels = [], []

    def get_open(a):
        reads.append(a)
        return RESTING if len(reads) <= 2 else []
    monkeypatch.setattr(autorun.trading, "get_open_orders", get_open)
    monkeypatch.setattr(autorun.trading, "cancel", lambda a, cid: cancels.append(cid))
    autorun.run(book=614.0, cfg=_cfg(), now=datetime(2026, 8, 17, 15, 45))
    assert cancels == ["a0fe202fc60a4e49a43d50fabca47098"]
    assert ("place", "SELL", "AAPL") in wired.order_v2.calls


def test_exit_does_not_sell_when_the_stop_cannot_be_cleared(wired, monkeypatch):
    """Fail closed: the position keeps its stop and stays open rather than half-cleared."""
    monkeypatch.setattr(autorun.trading, "get_open_orders", lambda a: RESTING)
    monkeypatch.setattr(autorun.trading, "cancel", lambda a, cid: {"ok": True})
    out = autorun.run(book=614.0, cfg=_cfg(), now=datetime(2026, 8, 17, 15, 45))
    assert ("place", "SELL", "AAPL") not in wired.order_v2.calls
    assert any(s.get("layer") == "clear_stop" for s in out["skipped"])


def test_protect_placement_never_triggers_a_clear(wired, monkeypatch):
    """PROTECT only fires when nothing rests; it must never cancel a stop on its way in."""
    from webull_api.reconcile import Unprotected
    prot = autorun.reconcile.protective_order("AAPL", 1, 278.76)
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected",
                        lambda *a, **k: [Unprotected("AAPL", 1, 303.0, 250.0, 278.76,
                                                     "backstop", prot)])
    monkeypatch.setattr(autorun.exits, "scan", lambda *a, **k: [])
    cancels = []
    monkeypatch.setattr(autorun.trading, "get_open_orders", lambda a: [])
    monkeypatch.setattr(autorun.trading, "cancel", lambda a, cid: cancels.append(cid))
    autorun.run(book=614.0, cfg=_cfg(), now=datetime(2026, 8, 17, 15, 45))
    assert cancels == []
    assert ("place", "SELL", "AAPL") in wired.order_v2.calls
