"""Sleeve guard on the REAL book (2026-08-15 spec).

A break-only exit signal must never sell a real position unless that position is positively
attributed as the autopilot's own trend/swing lot. Mean-reversion lots live below the SMA20 by
design, so a trend break is their setup, not their failure — selling on it churns the position
out of the trade it was opened for.

Fails safe toward HOLDING: unknown provenance, a missing plan, or a plan that fails the
sanity check all hold. The -8%/+20% backstops stay unconditional.
"""
from datetime import datetime

import pytest

from webull_api import exits, trading
from webull_api.autopilot import run as autorun
from webull_api.autopilot.config import AutopilotConfig


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


def _signal(symbol="AAPL", reasons=("break",), last=305.93, upl=0.9):
    return exits.ExitSignal(symbol=symbol, qty=1.0, last=last, unrealized_pct=upl,
                            reason=reasons[0], reasons=list(reasons),
                            detail=f"{symbol} test signal")


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
                                       "last_price": "305.93", "instrument_type": "EQUITY"}])
    monkeypatch.setattr(autorun.trading, "get_open_orders", lambda acct: [])
    monkeypatch.setattr(autorun, "_spy_risk_off", lambda *_a, **_k: False)
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected", lambda *a, **k: [])
    # No entries: isolate the EXITS stage.
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])
    return c


def _sells(client):
    return [sym for (k, side, sym) in client.order_v2.calls if k == "place" and side == "SELL"]


def _run(now=datetime(2026, 8, 17, 15, 45)):
    return autorun.run(book=614.0, cfg=_cfg(), now=now)


def test_break_only_does_not_sell_a_real_lot_with_no_plan(wired, monkeypatch):
    """The live 2026-08-14 case: 1 sh AAPL, RSI2 entry, no valid plan. Must HOLD."""
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda *a, **k: [exits.ExitRow("AAPL", 1.0, _signal())])
    monkeypatch.setattr(autorun.position_plans, "all_plans", lambda: {})
    out = _run()
    assert _sells(wired) == [], "break-only on an unattributed real lot must not sell"
    assert any(s.get("symbol") == "AAPL" and s.get("layer") == "sleeve"
               for s in out["skipped"]), "the HOLD must leave an audit row"


def test_break_only_sells_an_attributed_swing_lot(wired, monkeypatch):
    """The autopilot's OWN trend lot still exits on a break — the guard is not a blanket off."""
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda *a, **k: [exits.ExitRow("AAPL", 1.0, _signal())])
    monkeypatch.setattr(autorun.position_plans, "all_plans",
                        lambda: {"AAPL": {"symbol": "AAPL", "strategy": "swing",
                                          "structural_stop": 278.76, "entry": 303.0,
                                          "source": "autopilot"}})
    _run()
    assert _sells(wired) == ["AAPL"]


def test_break_only_holds_when_the_plan_fails_the_sanity_check(wired, monkeypatch):
    """A stale plan (stop 28 against a 303 cost) is not attribution — it is the bug. HOLD."""
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda *a, **k: [exits.ExitRow("AAPL", 1.0, _signal())])
    monkeypatch.setattr(autorun.position_plans, "all_plans",
                        lambda: {"AAPL": {"symbol": "AAPL", "strategy": "swing",
                                          "structural_stop": 28.0, "entry": 30.0,
                                          "source": "autopilot"}})
    out = _run()
    assert _sells(wired) == []
    assert any(s.get("layer") == "sleeve" for s in out["skipped"])


@pytest.mark.parametrize("reasons", [("stop", "break"), ("target",), ("stop",)])
def test_backstops_still_sell_unconditionally(wired, monkeypatch, reasons):
    """-8%/+20% are the safety net for EVERY sleeve and must never be gated by attribution."""
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda *a, **k: [exits.ExitRow("AAPL", 1.0, _signal(reasons=reasons))])
    monkeypatch.setattr(autorun.position_plans, "all_plans", lambda: {})
    _run()
    assert _sells(wired) == ["AAPL"], f"{reasons} must sell regardless of attribution"


def test_an_rsi2_lot_is_never_break_sold_even_with_a_swing_plan(wired, monkeypatch):
    """Positive attribution outranks a plan: if the real RSI2 ledger owns the lot, a stray
    swing plan for that symbol must not make it break-sellable."""
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda *a, **k: [exits.ExitRow("AAPL", 1.0, _signal())])
    monkeypatch.setattr(autorun.position_plans, "all_plans",
                        lambda: {"AAPL": {"symbol": "AAPL", "strategy": "swing",
                                          "structural_stop": 278.76, "source": "autopilot"}})
    monkeypatch.setattr(autorun, "_rsi2_real_symbols", lambda: {"AAPL"})
    out = _run()
    assert _sells(wired) == []
    assert any(s.get("layer") == "sleeve" for s in out["skipped"])


def test_rsi2_ledger_unreadable_does_not_break_the_guard(wired, monkeypatch):
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda *a, **k: [exits.ExitRow("AAPL", 1.0, _signal())])
    monkeypatch.setattr(autorun.position_plans, "all_plans", lambda: {})
    def boom():
        raise OSError("ledger gone")
    monkeypatch.setattr(autorun, "_rsi2_real_symbols", boom)
    out = _run()
    assert _sells(wired) == []          # still holds — fail closed


# ---- ledger staleness (I5): a disarmed sleeve must stop vetoing forever ------------------

_SWING_PLAN = {"AAPL": {"symbol": "AAPL", "strategy": "swing", "structural_stop": 278.76,
                        "entry": 303.0, "source": "autopilot"}}


def _write_ledger(tmp_path, monkeypatch, *, updated_at):
    """A REAL ledger file through the store's own env-isolated path — the wiring under test is
    _rsi2_real_symbols reading it, so nothing here may monkeypatch that function."""
    monkeypatch.setenv("RSI2_REAL_STATE_DIR", str(tmp_path / "rsi2real"))
    from webull_web import rsi2_real_store
    s = rsi2_real_store.record_entry(rsi2_real_store.load(), symbol="AAPL", shares=1,
                                     entry_price=303.0, entry_date="2026-08-17", decision_id="d")
    rsi2_real_store.save(s, updated_at)


def test_a_fresh_rsi2_ledger_vetoes_the_break_exit(wired, monkeypatch, tmp_path):
    from datetime import datetime as _dt
    _write_ledger(tmp_path, monkeypatch, updated_at=_dt.now().isoformat())
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda *a, **k: [exits.ExitRow("AAPL", 1.0, _signal())])
    monkeypatch.setattr(autorun.position_plans, "all_plans", lambda: dict(_SWING_PLAN))
    out = _run()
    assert _sells(wired) == [], "an armed RSI2 lot is a mean-reversion lot — a break is its setup"
    assert any(s.get("layer") == "sleeve" for s in out["skipped"])


def test_a_stale_rsi2_ledger_stops_vetoing(wired, monkeypatch, tmp_path):
    """The ledger only reconciles while the real runner is ENABLED. Disarmed with a recorded lot,
    a frozen file would veto every future swing exit on that symbol forever."""
    from datetime import date as _date, timedelta as _td
    _write_ledger(tmp_path, monkeypatch,
                  updated_at=f"{(_date.today() - _td(days=30)).isoformat()}T17:30:00")
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda *a, **k: [exits.ExitRow("AAPL", 1.0, _signal())])
    monkeypatch.setattr(autorun.position_plans, "all_plans", lambda: dict(_SWING_PLAN))
    _run()
    assert _sells(wired) == ["AAPL"]


def test_an_unstamped_ledger_still_vetoes(wired, monkeypatch, tmp_path):
    """Unparseable/missing updated_at with owned lots -> NOT stale. A live armed ledger always
    stamps one, so the ambiguous case fails toward HOLD."""
    _write_ledger(tmp_path, monkeypatch, updated_at=None)
    monkeypatch.setattr(autorun.exits, "scan",
                        lambda *a, **k: [exits.ExitRow("AAPL", 1.0, _signal())])
    monkeypatch.setattr(autorun.position_plans, "all_plans", lambda: dict(_SWING_PLAN))
    assert _sells(wired) == []
