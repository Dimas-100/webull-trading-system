"""Trigger-watch evaluators + the nightly evening check. Unknown data NEVER fires a trigger."""
import json

from webull_web import decisions_service as svc
from webull_web import decisions_store

TODAY = "2026-07-24"


def _bars(closes_by_date):
    return [{"time": d, "open": c, "high": c, "low": c, "close": c}
            for d, c in closes_by_date]


# ---- green_day ----

def test_green_day_fires_on_fresh_green_bar():
    bars = _bars([("2026-07-23", 100.0), (TODAY, 101.5)])
    fired, detail = svc.evaluate({"type": "green_day", "symbol": "FBTC"}, today=TODAY,
                                 get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is True
    assert "FBTC" in detail and "green" in detail


def test_green_day_not_fired_on_red_bar():
    bars = _bars([("2026-07-23", 100.0), (TODAY, 99.0)])
    fired, detail = svc.evaluate({"type": "green_day", "symbol": "FBTC"}, today=TODAY,
                                 get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "red" in detail


def test_green_day_stale_bar_is_honest_not_fired():
    bars = _bars([("2026-07-22", 100.0), ("2026-07-23", 101.0)])  # no bar for today
    fired, detail = svc.evaluate({"type": "green_day", "symbol": "FBTC"}, today=TODAY,
                                 get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "no fresh daily bar" in detail


# ---- lab_m_reached ----

def test_lab_m_reached_fires_at_threshold():
    fired, detail = svc.evaluate({"type": "lab_m_reached", "m": 2500}, today=TODAY,
                                 read_meta=lambda: {"M": 2500})
    assert fired is True and "2500" in detail


def test_lab_m_not_reached_reports_progress():
    fired, detail = svc.evaluate({"type": "lab_m_reached", "m": 2500}, today=TODAY,
                                 read_meta=lambda: {"M": 1028})
    assert fired is False and "1028" in detail and "2500" in detail


# ---- lab_regime_flip ----

def _cycle(seq, trend, vol):
    return {"cycle_seq": seq, "regime": {"trend": trend, "vol": vol}}


def test_regime_flip_waiting_while_in_baseline():
    cycles = [_cycle(i, "down", "low") for i in range(1, 16)]
    fired, detail = svc.evaluate({"type": "lab_regime_flip", "baseline": "down/low",
                                  "min_cycles": 5}, today=TODAY, read_cycles=lambda **k: cycles)
    assert fired is False and "down/low" in detail


def test_regime_flip_fires_after_min_cycles_in_new_regime():
    cycles = ([_cycle(i, "down", "low") for i in range(1, 11)]
              + [_cycle(i, "up", "low") for i in range(11, 16)])  # trailing 5 up/low
    fired, detail = svc.evaluate({"type": "lab_regime_flip", "baseline": "down/low",
                                  "min_cycles": 5}, today=TODAY, read_cycles=lambda **k: cycles)
    assert fired is True and "up/low" in detail


def test_regime_flip_short_streak_does_not_fire():
    cycles = ([_cycle(i, "down", "low") for i in range(1, 13)]
              + [_cycle(i, "up", "low") for i in range(13, 16)])  # trailing 3 only
    fired, detail = svc.evaluate({"type": "lab_regime_flip", "baseline": "down/low",
                                  "min_cycles": 5}, today=TODAY, read_cycles=lambda **k: cycles)
    assert fired is False


# ---- autopilot_placed ----

def test_autopilot_placed_fires_from_yesterdays_log():
    # The nightly check (~5:31 PM) runs BEFORE the 5:45 PM autopilot, so a placement is seen
    # by the NEXT evening's check via the lookback window.
    by_day = {"2026-07-24": {"placed": 0}, "2026-07-23": {"placed": 1}}
    fired, detail = svc.evaluate({"type": "autopilot_placed", "lookback_days": 2}, today=TODAY,
                                 read_autopilot_day=lambda d: by_day.get(d, {"placed": 0}))
    assert fired is True and "2026-07-23" in detail


def test_autopilot_placed_not_fired_when_nothing_placed():
    fired, detail = svc.evaluate({"type": "autopilot_placed", "lookback_days": 2}, today=TODAY,
                                 read_autopilot_day=lambda d: {"placed": 0})
    assert fired is False and "no real orders" in detail


def test_autopilot_placed_outside_lookback_does_not_fire():
    by_day = {"2026-07-20": {"placed": 3}}  # older than the 2-day window
    fired, _ = svc.evaluate({"type": "autopilot_placed", "lookback_days": 2}, today=TODAY,
                            read_autopilot_day=lambda d: by_day.get(d, {"placed": 0}))
    assert fired is False


def test_autopilot_placed_min_respected():
    by_day = {"2026-07-24": {"placed": 1}}
    fired, _ = svc.evaluate({"type": "autopilot_placed", "lookback_days": 1, "min": 2},
                            today=TODAY, read_autopilot_day=lambda d: by_day.get(d, {"placed": 0}))
    assert fired is False


# ---- proof_bar_met ----

def _ns(decisions, expectancy, expectancy_pct, chip="active"):
    return {"paper": {"chip": chip, "decisions": decisions, "expectancy": expectancy,
                      "expectancy_pct": expectancy_pct, "win_rate": 0.55}}


def test_proof_bar_met_fires_when_machine_met():
    fired, detail = svc.evaluate({"type": "proof_bar_met"}, today=TODAY,
                                 read_north_star=lambda: _ns(30, 1.25, 2.1))
    assert fired is True
    assert "30/30" in detail and "edge met" in detail


def test_proof_bar_short_sample_reports_progress():
    fired, detail = svc.evaluate({"type": "proof_bar_met"}, today=TODAY,
                                 read_north_star=lambda: _ns(12, 0.85, 1.2))
    assert fired is False and "12/30" in detail


def test_proof_bar_negative_pct_expectancy_never_fires():
    # $ edge positive but % negative — the mixed-lot red flag must not fire the scale-up.
    fired, detail = svc.evaluate({"type": "proof_bar_met"}, today=TODAY,
                                 read_north_star=lambda: _ns(35, 0.40, -0.3))
    assert fired is False and "edge not met" in detail


def test_proof_bar_unavailable_stats_never_fire():
    fired, detail = svc.evaluate({"type": "proof_bar_met"}, today=TODAY,
                                 read_north_star=lambda: _ns(0, 0.0, 0.0, chip="unknown"))
    assert fired is False and "unavailable" in detail


# ---- composite / manual ----

def test_any_composite_fires_if_one_child_fires():
    watch = {"type": "any", "of": [{"type": "lab_m_reached", "m": 2500},
                                   {"type": "lab_m_reached", "m": 1000}]}
    fired, detail = svc.evaluate(watch, today=TODAY, read_meta=lambda: {"M": 1028})
    assert fired is True


def test_unknown_type_is_manual_never_fires():
    fired, detail = svc.evaluate({"type": "wibble"}, today=TODAY)
    assert fired is False and "manual" in detail


# ---- evening_check (impure shell) ----

def _seed(tmp_path, monkeypatch, decisions):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    with (tmp_path / "decisions.jsonl").open("w", encoding="utf-8") as fh:
        for d in decisions:
            fh.write(json.dumps(d) + "\n")


def _dec(id, watch=None, status="open"):
    return {"kind": "decision", "id": id, "ts": "2026-07-17T18:00:00", "title": id.upper(),
            "decision": "d", "why": "w", "trigger_text": "t", "watch": watch, "status": status}


def test_evening_check_appends_check_rows_and_hands_fired_to_the_note(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _dec("fbtc-exit", watch={"type": "green_day", "symbol": "FBTC"}),
        _dec("done-one", watch={"type": "green_day", "symbol": "AAPL"}, status="done"),
    ])
    bars = _bars([("2026-07-23", 100.0), (TODAY, 101.0)])
    monkeypatch.setattr("webull_api.market_data.get_bars",
                        lambda s, *a, **k: list(reversed(bars)))
    pushes = []
    monkeypatch.setattr("webull_web.push.push_text",
                        lambda text, *, title, url, **kw: pushes.append(title) or True)
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/topic")

    view = svc.evening_check(TODAY, f"{TODAY}T17:31:00-04:00")

    assert [v["id"] for v in view] == ["fbtc-exit"]  # done decisions are not in the note view
    assert view[0]["fired"] is True
    # The note view carries what the phone needs (the manager's note renders TRIGGERED rows
    # in its ATTENTION section); the check itself pushes nothing (folded 2026-09-21).
    assert view[0]["decision"] == "d" and view[0]["manual"] is False
    assert pushes == []
    folded = decisions_store.fold(decisions_store.load())
    fbtc = next(d for d in folded if d["id"] == "fbtc-exit")
    assert fbtc["last_check"]["fired"] is True and fbtc["last_check"]["date"] == TODAY
    done = next(d for d in folded if d["id"] == "done-one")
    assert done["last_check"] is None  # resolved decisions are not checked


def test_evening_check_treats_a_free_text_watch_as_manual(tmp_path, monkeypatch):
    """Hand-written rows carry a prose `watch` ("check RSI(2)>70 in the evening note...").
    Regression 2026-09-21: `(watch or {}).get(...)` raised AttributeError on the str and took
    every decision's nightly check down with it ("Decisions unavailable" on the note)."""
    _seed(tmp_path, monkeypatch, [
        _dec("prose-watch", watch="Real AMZN lot has no automated exit; check RSI(2)>70 nightly"),
        _dec("fbtc-exit", watch={"type": "green_day", "symbol": "FBTC"}),
    ])
    bars = _bars([("2026-07-23", 100.0), (TODAY, 101.0)])
    monkeypatch.setattr("webull_api.market_data.get_bars",
                        lambda s, *a, **k: list(reversed(bars)))
    view = {v["id"]: v for v in svc.evening_check(TODAY, f"{TODAY}T17:31:00-04:00")}
    assert view["prose-watch"]["manual"] is True and view["prose-watch"]["fired"] is False
    assert view["prose-watch"]["detail"] == "manual — no machine check"
    assert view["fbtc-exit"]["fired"] is True          # the dict-watch row still gets checked
    folded = {d["id"]: d for d in decisions_store.fold(decisions_store.load())}
    assert folded["prose-watch"]["last_check"] is None  # no check row for a manual note


def test_evening_check_no_push_when_not_fired_or_env_unset(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [_dec("fbtc-exit", watch={"type": "green_day", "symbol": "FBTC"})])
    bars = _bars([("2026-07-23", 100.0), (TODAY, 99.0)])  # red day
    monkeypatch.setattr("webull_api.market_data.get_bars",
                        lambda s, *a, **k: list(reversed(bars)))
    pushes = []
    monkeypatch.setattr("webull_web.push.push_text",
                        lambda text, *, title, url: pushes.append(title) or True)
    monkeypatch.delenv("WEBULL_MANAGER_NOTE_NTFY", raising=False)
    view = svc.evening_check(TODAY, f"{TODAY}T17:31:00-04:00")
    assert view[0]["fired"] is False and pushes == []


def test_evening_check_degrades_per_decision_on_raising_source(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _dec("fbtc-exit", watch={"type": "green_day", "symbol": "FBTC"}),
        _dec("manual-one"),  # no watch
    ])
    def boom(*a, **k):
        raise RuntimeError("outage")
    monkeypatch.setattr("webull_api.market_data.get_bars", boom)
    monkeypatch.delenv("WEBULL_MANAGER_NOTE_NTFY", raising=False)
    view = svc.evening_check(TODAY, f"{TODAY}T17:31:00-04:00")
    by_id = {v["id"]: v for v in view}
    assert by_id["fbtc-exit"]["fired"] is False
    assert "unavailable" in by_id["fbtc-exit"]["detail"]
    assert "manual" in by_id["manual-one"]["detail"]  # manual decisions still listed, never fired


# ---- dip_reversal ----

def _bars_hl(rows):  # (date, close, low)
    return [{"time": d, "open": c, "high": c, "low": lo, "close": c} for d, c, lo in rows]


def test_dip_reversal_fires_on_green_bar_after_dip():
    bars = _bars_hl([("2026-07-21", 13.80, 13.70), ("2026-07-22", 13.50, 13.35),
                     ("2026-07-23", 13.45, 13.40), (TODAY, 13.75, 13.50)])
    fired, detail = svc.evaluate({"type": "dip_reversal", "symbol": "F", "level": 13.40},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is True
    assert "F" in detail and "green" in detail


def test_dip_reversal_green_without_dip_does_not_fire():
    bars = _bars_hl([("2026-07-22", 13.80, 13.75), ("2026-07-23", 13.70, 13.60),
                     (TODAY, 13.90, 13.65)])
    fired, detail = svc.evaluate({"type": "dip_reversal", "symbol": "F", "level": 13.40},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "no dip" in detail


def test_dip_reversal_dip_but_red_day_does_not_fire():
    bars = _bars_hl([("2026-07-22", 13.60, 13.50), ("2026-07-23", 13.45, 13.30),
                     (TODAY, 13.35, 13.25)])
    fired, detail = svc.evaluate({"type": "dip_reversal", "symbol": "F", "level": 13.40},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "red" in detail


def test_dip_reversal_stale_bar_is_honest_not_fired():
    bars = _bars_hl([("2026-07-22", 13.50, 13.35), ("2026-07-23", 13.60, 13.40)])
    fired, detail = svc.evaluate({"type": "dip_reversal", "symbol": "F", "level": 13.40},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "no fresh daily bar" in detail


# ---- close_above_sma ----

def test_close_above_sma_fires_when_reclaimed():
    bars = _bars([("2026-07-21", 14.00), ("2026-07-22", 13.00), ("2026-07-23", 12.00),
                  (TODAY, 13.50)])
    fired, detail = svc.evaluate({"type": "close_above_sma", "symbol": "F", "window": 3},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is True and "SMA3" in detail


def test_close_above_sma_below_does_not_fire():
    bars = _bars([("2026-07-21", 14.00), ("2026-07-22", 13.00), ("2026-07-23", 12.00),
                  (TODAY, 12.00)])
    fired, detail = svc.evaluate({"type": "close_above_sma", "symbol": "F", "window": 3},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False


def test_close_above_sma_short_history_is_honest_not_fired():
    bars = _bars([("2026-07-23", 13.00), (TODAY, 13.50)])
    fired, detail = svc.evaluate({"type": "close_above_sma", "symbol": "F", "window": 3},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "short" in detail


def test_close_above_sma_stale_bar_is_honest_not_fired():
    bars = _bars([("2026-07-21", 14.00), ("2026-07-22", 13.00), ("2026-07-23", 13.60)])
    fired, detail = svc.evaluate({"type": "close_above_sma", "symbol": "F", "window": 3},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "no fresh daily bar" in detail


# ---- rsi2_above ----

def test_rsi2_above_fires_on_fresh_hot_close():
    # Straight-up closes => RSI(2) = 100 > 70: the mean-reversion exit band is tagged.
    bars = _bars([("2026-07-18", 100.0), ("2026-07-21", 101.0), ("2026-07-22", 102.0),
                  ("2026-07-23", 103.0), (TODAY, 104.0)])
    fired, detail = svc.evaluate({"type": "rsi2_above", "symbol": "AAPL", "threshold": 70},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is True and "RSI(2)" in detail and "AAPL" in detail


def test_rsi2_above_cold_close_does_not_fire():
    # Straight-down closes => RSI(2) = 0.
    bars = _bars([("2026-07-18", 104.0), ("2026-07-21", 103.0), ("2026-07-22", 102.0),
                  ("2026-07-23", 101.0), (TODAY, 100.0)])
    fired, detail = svc.evaluate({"type": "rsi2_above", "symbol": "AAPL", "threshold": 70},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "RSI(2)" in detail


def test_rsi2_above_short_history_is_honest_not_fired():
    bars = _bars([("2026-07-23", 100.0), (TODAY, 104.0)])
    fired, detail = svc.evaluate({"type": "rsi2_above", "symbol": "AAPL", "threshold": 70},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "short" in detail


def test_rsi2_above_stale_bar_is_honest_not_fired():
    bars = _bars([("2026-07-22", 100.0), ("2026-07-23", 104.0)])  # no bar for today
    fired, detail = svc.evaluate({"type": "rsi2_above", "symbol": "AAPL", "threshold": 70},
                                 today=TODAY, get_bars=lambda s, *a, **k: list(reversed(bars)))
    assert fired is False and "no fresh daily bar" in detail


# ---- flat-subject auto-resolve (2026-09-04: the NVDA earnings rule nagged for 9 nights after the lot closed) ----

class _F:
    def __init__(self, **kw):
        self._d = kw
    def model_dump(self):
        return dict(self._d)


def _fill(symbol, side, qty, ts, source="real"):
    return _F(source=source, symbol=symbol, side=side, quantity=qty, filled_at_iso=ts, price=1.0)


def test_evening_check_auto_resolves_a_decision_whose_subject_is_flat(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _dec("nvda-rule", watch={"type": "rsi2_above", "symbol": "NVDA", "threshold": 70}),
        _dec("klac-rule", watch={"type": "green_day", "symbol": "KLAC"}),
    ])
    monkeypatch.setattr("webull_web.journal_store.load_fills", lambda: [
        _fill("NVDA", "BUY", 2, "2026-07-18T13:35:00Z"), _fill("NVDA", "SELL", 2, "2026-07-20T13:35:00Z"),
        _fill("KLAC", "BUY", 2, "2026-07-19T13:49:00Z"),
        _fill("NVDA", "BUY", 5, "2026-07-21T13:35:00Z", source="paper"),   # paper fills never count
    ])
    bars = _bars([("2026-07-23", 100.0), (TODAY, 101.0)])
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: list(reversed(bars)))
    pushes = []
    monkeypatch.setattr("webull_web.push.push_text", lambda text, *, title, url: pushes.append(title) or True)
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/topic")

    view = svc.evening_check(TODAY, f"{TODAY}T17:31:00-04:00")

    folded = {d["id"]: d for d in decisions_store.fold(decisions_store.load())}
    nvda = folded["nvda-rule"]
    assert nvda["status"] == "resolved" and nvda["resolution"] == "Subject flat"
    assert "NVDA" in (nvda.get("note") or "") and nvda["last_check"] is None   # no check row, no nag
    assert [v["id"] for v in view] == ["klac-rule"]                             # held subject still checked
    assert folded["klac-rule"]["status"] == "open" and folded["klac-rule"]["last_check"]["date"] == TODAY
    assert all("NVDA" not in p for p in pushes)
