"""The PHONE rendering of the nightly note (compose()["text"]): sectioned plain text naming the
real book's trades (TODAY / TOMORROW / STOPS), attention-first, ASCII, held under
PHONE_BUDGET_BYTES, with the ntfy priority/tags that follow ATTENTION. The full record
(["lines"]) is covered by test_manager_note_composer."""
from webull_api import push
from webull_web import manager_note_service as svc
from webull_web.manager_note_service import compose

DAY = "2026-09-29"


def _run(key, summary, result="ok", errors=None):
    return {"key": key, "ts": f"{DAY}T17:30:00", "result": result, "summary": summary,
            "placed": 0, "errors": errors or []}


def _base(**over):
    base = dict(today=DAY, run_rows=[], netliq_rows=[], autopilot=None, real_stops=None)
    base.update(over)
    return base


# Tonight's real inputs (2026-09-29): AMZN entry filled 15:45 and was protected at 17:45; IWM queued.
AP_LOG = [
    {"symbol": "RTX", "side": "SELL", "source": "exit:break", "allow": False, "placed": False},
    {"symbol": "AMZN", "side": "BUY", "source": "decision:immediate", "allow": True, "placed": True},
    {"symbol": "AMZN", "side": "SELL", "source": "protect", "allow": True, "placed": True},
    {"decision_id": "rtx-exit", "symbol": "RTX", "source": "decision:rsi2_above", "allow": False,
     "reason": "rsi2_above: RSI(2) 11.6 <= 70", "layer": "trigger", "placed": False},
    {"decision_id": "iwm-entry", "symbol": "IWM", "source": "decision:immediate", "allow": False,
     "reason": "cooling: risk-adding row younger than 120m (veto window)", "placed": False},
    {"decision_id": "amzn-exit", "symbol": "AMZN", "source": "decision:rsi2_above", "allow": False,
     "reason": "rsi2_above: RSI(2) 24.4 <= 70", "layer": "trigger", "placed": False},
]
EXEC = [
    {"id": "jpm-exit", "symbol": "JPM", "side": "SELL", "qty": "ALL", "order_type": "MARKET",
     "trigger": {"kind": "rsi2_above", "threshold": 70.0}, "expires": "2026-12-31", "status": "cancelled"},
    {"id": "amzn-entry", "symbol": "AMZN", "side": "BUY", "qty": "2", "order_type": "LIMIT",
     "limit_price": "248.62", "trigger": {"kind": "immediate"}, "expires": DAY, "status": "placed",
     "acted_ts": f"{DAY}T15:45:05.637540"},
    {"id": "rtx-exit", "symbol": "RTX", "side": "SELL", "qty": "ALL", "order_type": "MARKET",
     "trigger": {"kind": "rsi2_above", "threshold": 70.0}, "expires": "2026-12-31", "status": "queued"},
    {"id": "iwm-entry", "symbol": "IWM", "side": "BUY", "qty": "1", "order_type": "LIMIT",
     "limit_price": "281.81", "trigger": {"kind": "immediate"}, "expires": "2026-09-30", "status": "queued"},
    {"id": "amzn-exit", "symbol": "AMZN", "side": "SELL", "qty": "ALL", "order_type": "MARKET",
     "trigger": {"kind": "rsi2_above", "threshold": 70.0}, "expires": "2026-12-31", "status": "queued"},
]
LOTS = [{"symbol": "RTX", "shares": 2.0, "entry_price": 191.45},
        {"symbol": "AMZN", "shares": 2.0, "entry_price": 247.03}]


def _full_night(**over):
    kw = dict(
        today=DAY,
        run_rows=[_run("rsi2_real", "RSI2-real: 1 entr(y/ies) queued · 1 exit row(s) armed · reconciled 1"),
                  _run("flows", "Flows: recorded +0.01 owner flow")],
        netliq_rows=[{"date": "2026-09-28", "real": 3040.95, "paper_equity": 106257.93, "paper_options": 98350.0},
                     {"date": DAY, "real": 3036.78, "paper_equity": 106249.55, "paper_options": 98350.0}],
        autopilot={"posture": "armed", "today": {"placed": 2, "skipped": 18}},
        real_stops={"RTX": "176.13", "AMZN": "227.27"},
        autopilot_log=AP_LOG, exec_decisions=EXEC, held_lots=LOTS,
        events={"window_days": 14, "checked": 2, "unverified": [], "books_unavailable": [], "hits": []},
        exec_quality={"fills_matched": 28, "avg_bp": -21.44, "modeled_bp": 10.0},
        regime={"spy_risk_off": False, "spy_price": 764.2, "spy_sma200": 715.71},
        decisions=[{"id": "a", "title": "Red team", "fired": False,
                    "detail": "manual — no machine check", "manual": True}],
        flows_today=0.0,
    )
    kw.update(over)
    return kw


def _section(text: str, name: str) -> str:
    """The body of one section block (between its header and the next blank line)."""
    block = text.split(f"\n\n{name}\n", 1)[1] if f"\n\n{name}\n" in text else ""
    return block.split("\n\n", 1)[0]


def test_clean_night_shape_header_and_section_order():
    note = compose(**_full_night())
    text = note["text"]
    assert text.startswith("REAL $3,036.78  -$4.17 vs 09-28\nAutopilot ARMED: 2 placed, 18 skipped\n")
    assert text.index("\nTODAY\n") < text.index("\nTOMORROW\n") < text.index("\nSTOPS\n") \
        < text.index("\nMARKET\n") < text.index("\nSYSTEM\n")
    assert "ATTENTION" not in text and note["attention"] == []
    assert note["priority"] == "default" and note["tags"] == "white_check_mark"
    assert text.isascii()
    assert len(text.encode("utf-8")) <= svc.PHONE_BUDGET_BYTES


def test_today_names_each_real_order_with_qty_limit_kind_and_time():
    text = compose(**_full_night())["text"]
    assert _section(text, "TODAY").splitlines() == [
        "- BUY 2 AMZN lmt 248.62 (entry, 15:45)",
        "- SELL AMZN stop 227.27 (stop placed)"]


def test_today_joins_by_decision_id_and_names_exits():
    log = [{"decision_id": "v-exit", "symbol": "V", "side": "SELL", "source": "decision:rsi2_above",
            "allow": True, "placed": True}]
    ex = [{"id": "v-exit", "symbol": "V", "side": "SELL", "qty": "ALL", "order_type": "MARKET",
           "trigger": {"kind": "rsi2_above", "threshold": 70.0}, "status": "placed",
           "acted_ts": f"{DAY}T09:31:02"}]
    text = compose(**_full_night(autopilot_log=log, exec_decisions=ex))["text"]
    assert _section(text, "TODAY").splitlines() == ["- SELL ALL V mkt (exit rsi2_above, 09:31)"]


def test_quiet_day_says_so_and_failed_place_is_attention():
    text = compose(**_full_night(autopilot_log=[]))["text"]
    assert _section(text, "TODAY") == "- no real orders placed"
    note = compose(**_full_night(autopilot_log=[
        {"symbol": "IWM", "side": "BUY", "source": "decision:immediate", "error": "HTTP 500"}]))
    assert any(a.startswith("autopilot order FAILED: BUY IWM") for a in note["attention"])


def test_tomorrow_lists_queued_entries_first_then_armed_exits_with_rsi():
    text = compose(**_full_night())["text"]
    assert _section(text, "TOMORROW").splitlines() == [
        "- BUY IWM 1 lmt 281.81 at open, expires 2026-09-30",
        "- SELL ALL RTX if RSI(2) > 70 (now 11.6) - exit armed",
        "- SELL ALL AMZN if RSI(2) > 70 (now 24.4) - exit armed"]


def test_tomorrow_omits_unknown_rsi_and_expired_rows():
    ex = [dict(EXEC[2]), {**EXEC[3], "expires": "2026-09-28"}]
    text = compose(**_full_night(exec_decisions=ex, autopilot_log=[]))["text"]
    assert _section(text, "TOMORROW").splitlines() == ["- SELL ALL RTX if RSI(2) > 70 - exit armed"]
    assert _section(compose(**_full_night(exec_decisions=[]))["text"], "TOMORROW") == "- nothing queued"


def test_stops_list_every_held_lot_and_no_stop_is_attention():
    text = compose(**_full_night())["text"]
    assert _section(text, "STOPS").splitlines() == ["- RTX 2 @ 191.45 stop 176.13",
                                                    "- AMZN 2 @ 247.03 stop 227.27"]
    note = compose(**_full_night(real_stops={"RTX": "176.13"}))
    assert "- AMZN 2 @ 247.03 NO STOP" in _section(note["text"], "STOPS")
    assert "NO STOP on real lot AMZN (AMZN 2 @ 247.03)" in note["attention"]
    assert note["priority"] == "high"
    assert "- NO STOP on real lot AMZN" in note["text"].split("ATTENTION\n")[1]


def test_stops_unavailable_is_attention():
    note = compose(**_full_night(real_stops=None))
    assert _section(note["text"], "STOPS") == "- unavailable"
    assert "broker open orders unavailable (protection unverified)" in note["attention"]


def test_parked_system_lines_are_gone():
    note = compose(**_full_night())
    both = note["text"] + "\n" + "\n".join(note["lines"])
    for gone in ("paper-eq", "paper-opt", "proof ", "Proof bar", "scan ", "Scanner", "Lab funnel",
                 "Options risk", "Scorecard", "iscipline", "Judgment", "decisions: ", "BOOKS"):
        assert gone not in both, gone


def test_triggered_decision_is_an_attention_item_with_its_decision_text():
    note = compose(**_base(decisions=[
        {"id": "x", "title": "FBTC exit", "fired": True, "detail": "FBTC +1.2% (green day)",
         "decision": "sell in full at the open", "manual": False},
        {"id": "y", "title": "Red team", "fired": False, "detail": "manual — no machine check"}]))
    assert "- TRIGGERED: FBTC exit - FBTC +1.2% (green day) -> sell in full at the open" in note["text"]
    assert note["priority"] == "high"


def test_real_delta_is_reported_ex_flows_and_raw_when_flows_unreadable():
    rows = [{"date": "2026-09-18", "real": 1833.22}, {"date": DAY, "real": 2544.73}]
    with_flow = compose(**_base(netliq_rows=rows, flows_today=699.96))["text"]
    assert with_flow.startswith("REAL $2,544.73  +$11.55 vs 09-18 | flow +$699.96")
    withdrawal = compose(**_base(netliq_rows=rows, flows_today=-100.0))["text"]
    assert withdrawal.startswith("REAL $2,544.73  +$811.51 vs 09-18 | flow -$100.00")
    unreadable = compose(**_base(netliq_rows=rows, flows_today=None))["text"]
    assert unreadable.startswith("REAL $2,544.73  +$711.51 vs 09-18\n")


def test_empty_night_still_renders_a_header_and_attention():
    note = compose(**_base())
    text = note["text"]
    assert text.startswith("REAL unavailable (no net-liq snapshot)\nAutopilot: status unavailable\n")
    assert "- no runners have reported today" in text
    assert "- book values unavailable (no net-liq snapshot)" in text
    assert "unavailable: autopilot log, queued decisions, regime, events, exec quality" in note["attention"]
    assert note["priority"] == "high"


def _many_orders(n=10):
    log = [{"symbol": f"S{i}", "side": "BUY", "source": "decision:immediate", "placed": True}
           for i in range(n)]
    return log


def test_over_budget_trims_orders_first_then_sections_and_keeps_attention():
    rows = _full_night()["run_rows"] + [_run("rsi2_real", "RSI2-real: " + "z" * 2300)]
    note = compose(**_full_night(autopilot_log=_many_orders(), decisions=None, run_rows=rows))
    text = note["text"]
    assert len(text.encode("utf-8")) <= svc.PHONE_BUDGET_BYTES
    assert text.startswith("REAL $3,036.78")
    today = _section(text, "TODAY").splitlines()
    assert len(today) == svc._PHONE_MAX_ORDERS_WHEN_TRIMMING + 1
    assert today[-1] == "...+7 more (see autopilot/log)"
    assert "\nSYSTEM\n" not in text and "\nSTOPS\n" in text
    assert "- decisions check unavailable" in text
    assert "[trimmed: system" in text
    assert any("z" * 100 in ln for ln in note["lines"])


def test_attention_overflow_is_capped_and_a_hopeless_body_is_hard_cut(monkeypatch):
    monkeypatch.setattr(svc, "RUNNER_LABELS", svc.RUNNER_LABELS + [(f"extra{i}", f"Extra {i}") for i in range(9)])
    rows = [_run(k, f"{lbl}: failed", result="error", errors=["y" * 130])
            for k, lbl in svc.RUNNER_LABELS]
    note = compose(**_base(run_rows=rows))
    text = note["text"]
    assert len(text.encode("utf-8")) <= svc.PHONE_BUDGET_BYTES
    assert (f"+{len(note['attention']) - svc._PHONE_MAX_ATTENTION} more "
            "(see data/activity/manager_notes.jsonl)") in text \
        or text.endswith(push.TRIM_MARK)
    # every runner error kept, plus the bare night's items: book values, broker orders,
    # autopilot, decisions, the "unavailable: ..." roll-up
    assert len(note["attention"]) == len(svc.RUNNER_LABELS) + 5
