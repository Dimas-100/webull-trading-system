"""The note composer's full record (["lines"]): deterministic, honest per-source degradation."""
from webull_web.manager_note_service import compose

DAY = "2026-07-10"


def _run(key, summary, ts=f"{DAY}T21:31:00"):
    return {"key": key, "ts": ts, "result": "ok", "summary": summary, "placed": 0, "errors": []}


def _base_kwargs(**over):
    base = dict(today=DAY, run_rows=[], netliq_rows=[], autopilot=None, real_stops=None)
    base.update(over)
    return base


def test_full_day_note():
    note = compose(
        today=DAY,
        run_rows=[_run("rsi2_real", "RSI2-real: 1 entr(y/ies) queued"),
                  _run("netliq", "Net-liq: real $110.51")],
        netliq_rows=[{"date": "2026-07-09", "real": 109.12}, {"date": DAY, "real": 110.51}],
        autopilot={"posture": "armed", "today": {"placed": 1, "skipped": 0}},
        real_stops={"FBTC": "51.04"},
        autopilot_log=[{"symbol": "FBTC", "side": "SELL", "source": "protect", "placed": True}],
        exec_decisions=[], held_lots=[{"symbol": "FBTC", "shares": 3, "entry_price": 55.0}],
    )
    assert note["date"] == DAY
    text = "\n".join(note["lines"])
    assert "RSI2-real: 1 entr(y/ies) queued" in text
    assert "Books: real $110.51 (+$1.39 vs 2026-07-09)." in note["lines"]
    assert "FBTC stop $51.04 resting" in text
    assert "Today: SELL FBTC stop 51.04 (stop placed)." in note["lines"]
    assert "Tomorrow: nothing queued." in note["lines"]
    assert "ARMED" in text and "1 placed" in text


def test_empty_day_is_still_a_valid_note():
    note = compose(**_base_kwargs())
    text = "\n".join(note["lines"])
    assert "No runners have run yet today" in text
    assert "no net-liq snapshot" in text
    assert "broker open orders unavailable" in text
    assert "Autopilot status unavailable" in text
    assert "Today: autopilot log unavailable." in note["lines"]
    assert "Tomorrow: executable decisions unavailable." in note["lines"]


def test_no_stops_checked_vs_unavailable_are_distinct():
    checked = compose(**_base_kwargs(real_stops={}, held_lots=[]))
    assert "broker open orders unavailable" not in "\n".join(checked["lines"])
    assert "Protection: no held lots and no resting stops on the real book." in checked["lines"]


def test_held_lot_without_stop_is_named_in_the_record():
    note = compose(**_base_kwargs(real_stops={}, held_lots=[{"symbol": "AMZN", "shares": 2}]))
    assert "Protection: AMZN NO STOP." in note["lines"]


def test_decisions_line_renders_triggered_and_waiting():
    note = compose(**_base_kwargs(decisions=[
        {"id": "fbtc-exit", "title": "FBTC exit", "fired": True, "detail": "FBTC +1.2% — green day"},
        {"id": "lab", "title": "Lab revisit", "fired": False, "detail": "regime down/low ×15"}]))
    text = "\n".join(note["lines"])
    assert "Decisions: FBTC exit — TRIGGERED today (FBTC +1.2% — green day)" in text
    assert "Lab revisit — waiting (regime down/low ×15)" in text


def test_decisions_none_open_vs_unavailable_are_distinct():
    assert "Decisions: none open." in compose(**_base_kwargs(decisions=[]))["lines"]
    assert "Decisions unavailable." in compose(**_base_kwargs(decisions=None))["lines"]


def test_yesterdays_runs_do_not_leak_into_today():
    note = compose(**_base_kwargs(run_rows=[_run("rsi2_real", "RSI2-real: 3 exit(s)", ts="2026-07-09T21:31:00")]))
    assert "3 exit(s)" not in "\n".join(note["lines"])


def test_note_exec_line_with_data_and_fallbacks():
    eq = {"fills_matched": 3, "avg_bp": 7.5, "modeled_bp": 10.0}
    assert ("Exec: 3 real fill(s) matched · avg slippage 7.5bp (model assumes 10.0bp)."
            in compose(**_base_kwargs(exec_quality=eq))["lines"])
    assert "Exec: no matched real fills yet." in compose(**_base_kwargs(exec_quality={"fills_matched": 0}))["lines"]
    assert "Exec quality unavailable." in compose(**_base_kwargs())["lines"]


def test_note_regime_line_with_data_and_fallbacks():
    note = compose(**_base_kwargs(regime={"spy_risk_off": False, "spy_price": 542.19, "spy_sma200": 521.4}))
    assert "Regime: SPY risk-on ($542.19 vs 200SMA $521.40)." in note["lines"]
    note = compose(**_base_kwargs(regime={"spy_risk_off": True, "spy_price": 500.0, "spy_sma200": 521.4}))
    assert "Regime: SPY risk-off ($500.00 vs 200SMA $521.40) — position caps tighten." in note["lines"]
    note = compose(**_base_kwargs(regime={"spy_risk_off": None, "spy_price": None, "spy_sma200": None}))
    assert "Regime: SPY trend unknown (insufficient data)." in note["lines"]
    assert "Regime unavailable." in compose(**_base_kwargs())["lines"]


def test_events_line_sorted_hits_real_marker():
    ev = {"window_days": 14, "checked": 4, "unverified": [], "books_unavailable": [],
          "hits": [{"symbol": "AAPL", "books": ["paper-eq"], "date": "2026-07-31", "days": 7},
                   {"symbol": "PLTR", "books": ["real", "rsi2"], "date": "2026-08-01", "days": 8}]}
    note = compose(**_base_kwargs(events=ev))
    assert ("Events: AAPL earnings 2026-07-31 (in 7d) · "
            "PLTR earnings 2026-08-01 (in 8d, real).") in note["lines"]
    assert "earnings on real lot PLTR in 8d (2026-08-01)" in note["attention"]


def test_events_line_none_with_suffixes():
    ev = {"window_days": 14, "checked": 9, "unverified": ["X", "Y"],
          "books_unavailable": ["real"], "hits": []}
    note = compose(**_base_kwargs(events=ev))
    assert ("Events: none within 14 days (9 names checked) · 2 unverified · "
            "real book unreadable.") in note["lines"]


def test_events_all_unverified_leads_with_the_failure():
    ev = {"window_days": 14, "checked": 3, "unverified": ["A", "B", "C"],
          "books_unavailable": [], "hits": []}
    line = "Events: 3 held name(s) unverified (earnings lookup failed)."
    assert line in compose(**_base_kwargs(events=ev))["lines"]


def test_events_unavailable_line_when_source_none():
    assert "Events unavailable." in compose(**_base_kwargs(events=None))["lines"]
