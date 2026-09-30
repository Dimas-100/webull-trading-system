"""Composed tests: the REAL trading.place + REAL safety.should_submit run (only the SDK
client/env seam is patched, mirroring tests/test_gate_hardening.py). Assert the gate governs
whether place() is reached."""
from datetime import datetime

import pytest

from webull_api import trading
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


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))  # empty -> realized 0; seedable per test
    c = _Client()
    monkeypatch.setattr(trading, "_resolve", lambda client, env: (c, "prod"))
    # No positions, no open orders, no exits by default; one entry PASS.
    monkeypatch.setattr(autorun, "_default_account", lambda *_a, **_k: "ACC")
    monkeypatch.setattr(autorun.journal_ingest, "sync_real", lambda ids: (0, None))
    monkeypatch.setattr(autorun.portfolio, "get_positions", lambda acct: [])
    monkeypatch.setattr(autorun.trading, "get_open_orders", lambda acct: [])
    monkeypatch.setattr(autorun, "_spy_risk_off", lambda *_a, **_k: False)
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected", lambda *a, **k: [])
    monkeypatch.setattr(autorun.exits, "scan", lambda *a, **k: [])
    return c


def _entry_pass(symbol="AAPL", entry=30.0, stop=28.0, target=36.0, shares=1):
    class P:  # mimics swing planner PASS plan
        pass
    p = P()
    p.symbol, p.entry, p.stop, p.target, p.shares = symbol, entry, stop, target, shares
    p.rr, p.actual_risk_pct, p.exit_mode, p.verdict, p.reason = 2.0, 1.0, "trend", "PASS", ""
    class R:
        pass
    r = R(); r.symbol, r.plan, r.error = symbol, p, None
    return r


def test_disabled_places_nothing(wired, monkeypatch):
    monkeypatch.setattr(autorun.discovery, "discover", lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(enabled=False), now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []
    assert out["placed"] == []


def test_entry_within_caps_is_placed(wired, monkeypatch):
    monkeypatch.setattr(autorun.discovery, "discover", lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    placed = [(s, sym) for (k, s, sym) in wired.order_v2.calls if k == "place"]
    assert ("BUY", "AAPL") in placed
    assert any(p["symbol"] == "AAPL" for p in out["placed"])


def test_placement_pushes_a_phone_note_and_a_quiet_run_stays_silent(wired, monkeypatch):
    """2026-09-21: a run that placed (or errored) sends one short note to the owner's phone
    topic; a run that placed nothing sends nothing. The push happens AFTER placement and is
    best-effort -- it can never reach the gate."""
    pushes = []
    monkeypatch.setattr(autorun.push, "push_text",
                        lambda text, *, title, url, **kw: pushes.append((title, text, url, kw)) or True)
    monkeypatch.setattr(autorun.discovery, "discover", lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(push_url="https://ntfy.example/t"),
                      now=datetime(2026, 7, 7, 18, 0))
    assert [p["symbol"] for p in out["placed"]] == ["AAPL"]
    assert out["placed"][0]["qty"] == "1" and out["placed"][0]["price"] is not None
    assert len(pushes) == 1
    title, text, url, kw = pushes[0]
    assert title == "Autopilot 18:00 - 1 placed" and url == "https://ntfy.example/t"
    assert text.startswith("- BUY 1 AAPL @ ") and "(entry)" in text
    assert kw == {"priority": "default", "tags": "money_with_wings"}

    pushes.clear()
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])   # nothing to do
    out = autorun.run(book=400.0, cfg=_cfg(push_url="https://ntfy.example/t"),
                      now=datetime(2026, 7, 7, 18, 0))
    assert out["placed"] == [] and out["errors"] == [] and pushes == []

    # No topic configured -> no push, placement unaffected.
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass("MSFT")])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    assert [p["symbol"] for p in out["placed"]] == ["MSFT"] and pushes == []


def test_protective_stop_placed_before_entry(wired, monkeypatch):
    from webull_api.reconcile import Unprotected
    prot = autorun.reconcile.protective_order("MSFT", 1, 20.0)
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected",
                        lambda *a, **k: [Unprotected("MSFT", 1, 25.0, 24.0, 20.0, "structural", prot)])
    monkeypatch.setattr(autorun.discovery, "discover", lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    places = [(s, sym) for (k, s, sym) in wired.order_v2.calls if k == "place"]
    assert places.index(("SELL", "MSFT")) < places.index(("BUY", "AAPL"))


def test_over_cap_entry_skipped(wired, monkeypatch):
    monkeypatch.setattr(autorun.discovery, "discover", lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass(entry=100.0)])
    out = autorun.run(book=400.0, cfg=_cfg(max_notional=40), now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []
    assert any(s.get("layer") == "cap" for s in out["skipped"])


def test_outside_window_places_nothing(wired, monkeypatch):
    monkeypatch.setattr(autorun.discovery, "discover", lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(windows="09:00-10:00"), now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []


def test_daily_loss_halt_latches_and_blocks_entry(wired, monkeypatch):
    # A held position deep underwater trips the daily-loss halt: the new entry is skipped at the
    # "halt" layer and the latch is persisted for the day. max_positions is raised so the entry is
    # NOT blocked earlier at the position-cap layer — we want it to reach the halt check.
    monkeypatch.setattr(autorun.portfolio, "get_positions",
                        lambda acct: [{"symbol": "XYZ", "quantity": "1",
                                       "cost_price": "100", "last_price": "50"}])
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(max_positions=5, daily_loss_halt=40),
                      now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []
    assert any(s.get("layer") == "halt" for s in out["skipped"])
    from webull_api.autopilot import state as _st
    assert _st.load_state("2026-07-07").halt_tripped is True


def test_in_run_position_cap_blocks_second_entry(wired, monkeypatch):
    # Two PASSes, max_positions=1: only the FIRST places; the second is blocked at "positions"
    # because the first entry counts within the same run (C1 regression).
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": ["AAPL", "MSFT"], "scanned": 2, "in_band": 2})())
    monkeypatch.setattr(autorun.swing_screen, "screen",
                        lambda syms, book: [_entry_pass("AAPL"), _entry_pass("MSFT")])
    out = autorun.run(book=400.0, cfg=_cfg(max_positions=1), now=datetime(2026, 7, 7, 18, 0))
    buys = [sym for (k, s, sym) in wired.order_v2.calls if k == "place" and s == "BUY"]
    assert buys == ["AAPL"]
    assert any(s.get("layer") == "positions" for s in out["skipped"])


def test_broker_read_error_fails_closed_for_entries(wired, monkeypatch):
    # get_positions raising must NOT be treated as a flat $0 book: BUYs deny at the halt layer (C2).
    def boom(acct):
        raise RuntimeError("broker down")
    monkeypatch.setattr(autorun.portfolio, "get_positions", boom)
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []
    assert any(s.get("layer") == "halt" for s in out["skipped"])


# ── security-review hardening (2026-07-09): GateState-construction fail-closed fixes ──

def test_normalize_rows_known_vs_unknown():
    from webull_api.autopilot.run import _normalize_rows
    assert _normalize_rows([{"symbol": "X"}]) == ([{"symbol": "X"}], True)
    assert _normalize_rows([]) == ([], True)                       # genuinely flat = known
    assert _normalize_rows({"data": [{"s": 1}]}) == ([{"s": 1}], True)
    assert _normalize_rows({"positions": [{"s": 1}]}) == ([{"s": 1}], True)
    assert _normalize_rows({"items": [{"s": 1}]}) == ([{"s": 1}], True)
    assert _normalize_rows({"code": "1", "msg": "err"}) == ([], False)   # unrecognized dict = unknown
    assert _normalize_rows(None) == ([], False)
    assert _normalize_rows("oops") == ([], False)


def test_recognized_positions_envelope_is_seen_by_halt(wired, monkeypatch):
    # {"positions": [...]} envelope (HTTP 200) must be PARSED, not read as flat: the underwater
    # position trips the loss halt. Before the fix, the dict -> [] -> $0 P/L -> BUY would place.
    monkeypatch.setattr(autorun.portfolio, "get_positions",
                        lambda acct: {"positions": [{"symbol": "XYZ", "quantity": "1",
                                                     "cost_price": "100", "last_price": "50"}]})
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(max_positions=5, daily_loss_halt=40),
                      now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []
    assert any(s.get("layer") == "halt" for s in out["skipped"])


def test_unrecognized_positions_shape_fails_closed(wired, monkeypatch):
    # An unparseable 200 body (a dict with no positions list) must be UNKNOWN -> BUYs deny at halt.
    monkeypatch.setattr(autorun.portfolio, "get_positions", lambda acct: {"code": 400, "msg": "bad"})
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []
    assert any(s.get("layer") == "halt" for s in out["skipped"])


def test_open_orders_unreadable_fails_closed_for_entries(wired, monkeypatch):
    # get_positions OK (flat) but get_open_orders raises -> a resting entry could be invisible ->
    # entries must fail closed (no BUY placed).
    def boom(acct):
        raise RuntimeError("orders down")
    monkeypatch.setattr(autorun.trading, "get_open_orders", boom)
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, s, sym) in wired.order_v2.calls if k == "place" and s == "BUY"] == []
    assert any(s.get("layer") == "open_orders" for s in out["skipped"])


# ── realized loss in the daily-loss halt (I1): TODAY's realized losses tighten the halt ──

def _seed_close(symbol, buy_px, sell_px, sell_day="2026-07-07"):
    from webull_api.journal.schema import Fill
    from webull_web import journal_store
    journal_store.append_fills([
        Fill(id=f"{symbol}-b", source="real", account_id="ACC", symbol=symbol, side="BUY",
             quantity=1, price=buy_px, filled_at_iso="2026-07-01T10:00:00-04:00", order_type="MARKET"),
        Fill(id=f"{symbol}-s", source="real", account_id="ACC", symbol=symbol, side="SELL",
             quantity=1, price=sell_px, filled_at_iso=f"{sell_day}T15:00:00-04:00", order_type="MARKET"),
    ])


def test_realized_loss_today_trips_halt_even_when_flat(wired, monkeypatch):
    # Flat book (unrealized $0) but a real position was closed TODAY at -$55. The unrealized-only
    # halt saw $0 and allowed the BUY; folding the day's realized loss in trips the halt.
    _seed_close("LOSSSYM", buy_px=100.0, sell_px=45.0)  # realized -$55 on 2026-07-07
    monkeypatch.setattr(autorun.discovery, "discover", lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(daily_loss_halt=40), now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []
    assert any(s.get("layer") == "halt" for s in out["skipped"])


def test_realized_gain_does_not_loosen_the_halt(wired, monkeypatch):
    # A held position is -$45 unrealized (below the $40 halt) AND +$30 was realized today. The clamp
    # (min(0, realized)) ignores the gain, so day P/L stays -$45 and the halt DENIES. Signed total
    # would be -$15 and ALLOW — the invariant forbids that loosening.
    monkeypatch.setattr(autorun.portfolio, "get_positions",
                        lambda acct: [{"symbol": "HELD", "quantity": "1",
                                       "cost_price": "100", "last_price": "55"}])  # unrealized -45
    _seed_close("GAINSYM", buy_px=100.0, sell_px=130.0)  # realized +$30 today
    monkeypatch.setattr(autorun.discovery, "discover", lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(max_positions=5, daily_loss_halt=40),
                      now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, s, sym) in wired.order_v2.calls if k == "place" and s == "BUY"] == []
    assert any(s.get("layer") == "halt" for s in out["skipped"])


def test_unreadable_journal_fails_closed_for_entries(wired, monkeypatch):
    # If today's realized P/L can't be determined (journal read raises), day P/L is UNKNOWN and the
    # entry must fail closed at the halt layer — like an unreadable positions read.
    def boom():
        raise RuntimeError("journal down")
    monkeypatch.setattr(autorun.journal_store, "load_fills", boom)
    monkeypatch.setattr(autorun.discovery, "discover", lambda **k: type("D", (), {"symbols": ["AAPL"], "scanned": 1, "in_band": 1})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [_entry_pass()])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []
    assert any(s.get("layer") == "halt" for s in out["skipped"])


def test_fractional_synthetic_stop_is_placed_as_a_market_sell(wired, monkeypatch):
    from webull_api.reconcile import Unprotected, synthetic_stop_order
    synth = synthetic_stop_order("FBTC", 1.7, 52.0, 51.0)
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected",
                        lambda *a, **k: [Unprotected("FBTC", 1.7, 55.48, 51.0, 52.0, "backstop",
                                                     None, fractional=True, synthetic=synth)])
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    assert ("SELL", "FBTC") in [(s, sym) for (k, s, sym) in wired.order_v2.calls if k == "place"]
    assert any(p["symbol"] == "FBTC" and p["source"] == "protect:synthetic" for p in out["placed"])


def test_fractional_above_stop_places_nothing(wired, monkeypatch):
    from webull_api.reconcile import Unprotected
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected",
                        lambda *a, **k: [Unprotected("FBTC", 1.7, 55.48, 57.31, 52.0, "backstop",
                                                     None, fractional=True, synthetic=None,
                                                     skip_reason="fractional: monitored, above stop")])
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    assert [k for (k, *_r) in wired.order_v2.calls if k == "place"] == []
    assert out["placed"] == []
    assert out["errors"] == []


def test_fractional_skip_reason_is_audited(wired, monkeypatch, tmp_path):
    import json
    from webull_api.reconcile import Unprotected
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected",
                        lambda *a, **k: [Unprotected("FBTC", 1.7, 55.48, 51.0, 52.0, "backstop",
                                                     None, fractional=True, synthetic=None,
                                                     skip_reason="fractional: sell already working")])
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])
    autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    log = tmp_path / "log" / "2026-07-07.jsonl"
    recs = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert any(r["source"] == "protect:synthetic" and r["placed"] is False
               and r["reason"] == "fractional: sell already working" for r in recs)


def test_protect_sale_suppresses_the_same_symbols_exit_in_one_run(wired, monkeypatch, tmp_path):
    """PROTECT and EXITS fire on the same condition when the stop is the cost-basis
    backstop. Without dedup this run places two full-size SELLs for one position."""
    import json
    from webull_api.reconcile import Unprotected, synthetic_stop_order
    synth = synthetic_stop_order("FBTC", 1.7, 52.0, 51.0)
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected",
                        lambda *a, **k: [Unprotected("FBTC", 1.7, 55.48, 51.0, 52.0, "backstop",
                                                     None, fractional=True, synthetic=synth)])

    class _Sig:
        symbol, qty, last, reason = "FBTC", 1.7, 51.0, "stop"

    class _Row:
        signal, error = _Sig(), None

    monkeypatch.setattr(autorun.exits, "scan", lambda *a, **k: [_Row()])
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    sells = [(s, sym) for (k, s, sym) in wired.order_v2.calls if k == "place"]
    assert sells == [("SELL", "FBTC")], f"expected ONE sell, got {sells}"
    assert any(s["reason"] == "already actioned in PROTECT this run" for s in out["skipped"])
    assert out["errors"] == []

    log = tmp_path / "log" / "2026-07-07.jsonl"
    recs = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert any(r["reason"] == "already actioned in PROTECT this run" and r["layer"] == "dedup"
               for r in recs), f"expected an audited dedup-layer record, got {recs}"


def test_whole_share_protective_stop_also_suppresses_the_same_run_exit(wired, monkeypatch):
    """A resting GTC stop does not sell now, but if EXITS sells the position the stop
    outlives the position and can later trigger into a short."""
    from webull_api.reconcile import Unprotected
    prot = autorun.reconcile.protective_order("MSFT", 5, 20.0)
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected",
                        lambda *a, **k: [Unprotected("MSFT", 5, 25.0, 19.0, 20.0, "backstop", prot)])

    class _Sig:
        symbol, qty, last, reason = "MSFT", 5, 19.0, "stop"

    class _Row:
        signal, error = _Sig(), None

    monkeypatch.setattr(autorun.exits, "scan", lambda *a, **k: [_Row()])
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    sells = [(s, sym) for (k, s, sym) in wired.order_v2.calls if k == "place"]
    assert sells == [("SELL", "MSFT")], f"expected ONE sell, got {sells}"
    assert out["errors"] == []


def test_protect_sale_does_not_suppress_a_different_symbols_exit(wired, monkeypatch):
    """The guard must suppress ONLY the symbol PROTECT actually sold — a same-run PROTECT
    sale of FBTC must not suppress an unrelated EXIT for AMD. Widening the guard from
    `sym in protect_actioned` to `if protect_actioned:` (suppress every exit once PROTECT sold
    ANYTHING) would wrongly drop the AMD sell; this test requires BOTH sells to place."""
    from webull_api.reconcile import Unprotected, synthetic_stop_order
    synth = synthetic_stop_order("FBTC", 1.7, 52.0, 51.0)
    monkeypatch.setattr(autorun.reconcile, "scan_unprotected",
                        lambda *a, **k: [Unprotected("FBTC", 1.7, 55.48, 51.0, 52.0, "backstop",
                                                     None, fractional=True, synthetic=synth)])

    class _Sig:
        symbol, qty, last, reason = "AMD", 3, 10.0, "stop"

    class _Row:
        signal, error = _Sig(), None

    monkeypatch.setattr(autorun.exits, "scan", lambda *a, **k: [_Row()])
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])
    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    sells = [(s, sym) for (k, s, sym) in wired.order_v2.calls if k == "place"]
    assert ("SELL", "FBTC") in sells, f"expected PROTECT's FBTC sell, got {sells}"
    assert ("SELL", "AMD") in sells, f"expected AMD's exit unaffected by FBTC's dedup, got {sells}"
    assert out["errors"] == []


@pytest.mark.parametrize("branch", ["whole_share", "fractional"])
def test_failed_protect_placement_still_lets_exit_place(wired, monkeypatch, branch):
    """`protect_actioned.add(sym)` sits INSIDE `if _try_place(...)`: a PROTECT order that reaches
    the broker but whose broker call raises must NOT be treated as sold, so the EXIT for that
    same symbol still places. Hoisting `.add()` outside that guard would leave a breached
    position with NO sell at all — worse than the original double-sell bug this dedup fixes.

    Covers BOTH branches that add to protect_actioned: the whole-share `r.protective` (a GTC
    STOP_LOSS) and the fractional `r.synthetic` (a MARKET sell). The EXIT order is always
    the only LIMIT order in either scenario, so the fake broker discriminates on that
    instead of on `order_type == "MARKET"` — that check alone would never see a failure in
    the whole-share (STOP_LOSS) branch."""
    from webull_api.reconcile import Unprotected, synthetic_stop_order

    if branch == "whole_share":
        symbol, qty = "MSFT", 5
        prot = autorun.reconcile.protective_order(symbol, qty, 20.0)
        row = Unprotected(symbol, qty, 25.0, 19.0, 20.0, "backstop", prot)
    else:
        symbol, qty = "FBTC", 1.7
        synth = synthetic_stop_order(symbol, qty, 52.0, 51.0)
        row = Unprotected(symbol, qty, 55.48, 51.0, 52.0, "backstop", None,
                          fractional=True, synthetic=synth)

    monkeypatch.setattr(autorun.reconcile, "scan_unprotected", lambda *a, **k: [row])

    sig = type("Sig", (), {"symbol": symbol, "qty": qty, "last": row.last, "reason": "stop"})()

    class _Row:
        signal, error = sig, None

    monkeypatch.setattr(autorun.exits, "scan", lambda *a, **k: [_Row()])
    monkeypatch.setattr(autorun.discovery, "discover",
                        lambda **k: type("D", (), {"symbols": [], "scanned": 0, "in_band": 0})())
    monkeypatch.setattr(autorun.swing_screen, "screen", lambda syms, book: [])

    orig_place = wired.order_v2.place_order

    def flaky_place(account_id, orders, client_combo_order_id=None):
        # The EXIT sell is always the only LIMIT order; the PROTECT order is STOP_LOSS
        # (whole-share) or MARKET (fractional). Fail only the PROTECT placement so the
        # EXIT still reaches the (real) fake broker.
        if orders[0].get("order_type") != "LIMIT":
            raise RuntimeError("broker rejected the protect order")
        return orig_place(account_id, orders, client_combo_order_id=client_combo_order_id)

    monkeypatch.setattr(wired.order_v2, "place_order", flaky_place)

    out = autorun.run(book=400.0, cfg=_cfg(), now=datetime(2026, 7, 7, 18, 0))
    sells = [(s, sym) for (k, s, sym) in wired.order_v2.calls if k == "place"]
    assert ("SELL", symbol) in sells, f"exit SELL did not reach the broker after PROTECT failed: {sells}"
    assert any(e.get("symbol") == symbol for e in out["errors"]), \
        f"expected the failed PROTECT placement to surface as an error, got {out['errors']}"
