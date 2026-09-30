"""_decisions_stage — queued decisions execute through injected placers; rows leave 'queued'
only when acted on; cooling/caps/triggers leave them queued. No network, no real audit dir."""
from datetime import datetime, timedelta

import pytest

from webull_api import decisions_exec
from webull_api.autopilot import run as ap_run
from webull_api.autopilot.config import AutopilotConfig
from webull_api.autopilot.state import DailyState

NOW = datetime(2026, 8, 7, 15, 46)


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(ap_run.audit, "log_decision", lambda *a, **k: None)
    monkeypatch.setattr(ap_run.audit, "notify", lambda *a, **k: True)
    # isolate the executor-owned state store (first_seen map) from the real autopilot dir
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "ap"))


def _seed(monkeypatch, tmp_path, rows):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    return [decisions_exec.append(r) for r in rows]


def _fbtc_sell(**kw):
    row = {"asset": "EQUITY", "symbol": "FBTC", "side": "SELL", "qty": "ALL",
           "order_type": "MARKET", "trigger": {"kind": "green_day"}, "expires": "2026-12-31"}
    row.update(kw)
    return row


def _f_call(**kw):
    row = {"asset": "OPTION", "symbol": "F", "side": "BUY",
           "trigger": {"kind": "immediate"}, "expires": "2026-12-31",
           "option": {"expiry": "2026-09-18", "strike": 14.0, "right": "C",
                      "quantity": "1", "limit_price": "0.50"}}
    row.update(kw)
    return row


def _run_stage(*, now=NOW, positions=None, cfg=None, tp=None, tpo=None):
    calls = {"eq": [], "opt": []}

    def default_tp(order, side, source, extra=None, extra_open=frozenset()):
        calls["eq"].append((order["symbol"], side, source, order))
        return True

    def default_tpo(combo, source, *, open_option_units):
        calls["opt"].append((source, open_option_units, combo))
        return True

    ap_run._decisions_stage(
        acct="A", cfg=cfg or AutopilotConfig(enabled=True, decisions_enabled=True),
        now=now, st=DailyState(day=now.date().isoformat()),
        positions=positions if positions is not None else [{"symbol": "FBTC", "quantity": "1.7"}],
        try_place=tp or default_tp, try_place_option=tpo or default_tpo,
        placed=[], skipped=[], errors=[], protect_actioned=set(), open_orders_known=True)
    return calls


def test_green_day_sell_places_and_marks(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_fbtc_sell()])
    monkeypatch.setattr(ap_run.market_data, "spot_price", lambda s: 56.50)
    monkeypatch.setattr(ap_run, "_daily_bars", lambda s: [{"date": "2026-08-06", "close": 56.13}])
    calls = _run_stage()
    assert [(c[0], c[1], c[2]) for c in calls["eq"]] == [("FBTC", "SELL", "decision:green_day")]
    order = calls["eq"][0][3]
    assert order["quantity"] == "1.7" and order["order_type"] == "MARKET"
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "placed"


def test_red_day_leaves_queued(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_fbtc_sell()])
    monkeypatch.setattr(ap_run.market_data, "spot_price", lambda s: 55.90)
    monkeypatch.setattr(ap_run, "_daily_bars", lambda s: [{"date": "2026-08-06", "close": 56.13}])
    calls = _run_stage()
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_placed_row_never_reexecutes(monkeypatch, tmp_path):
    stored = _seed(monkeypatch, tmp_path, [_fbtc_sell()])[0]
    decisions_exec.mark(stored["id"], "placed")
    calls = _run_stage()
    assert calls["eq"] == [] and calls["opt"] == []


def test_buy_cooling_blocks_fresh_directive(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_f_call()])
    calls = _run_stage(now=datetime.now())  # ts just stamped -> not cooled
    assert calls["opt"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_cooled_option_buy_builds_combo_and_places(monkeypatch, tmp_path):
    stored = _seed(monkeypatch, tmp_path, [_f_call()])[0]
    ap_run.state_mod.save_first_seen({ap_run._decision_key(stored): datetime.now().isoformat()})
    calls = _run_stage(now=datetime.now() + timedelta(minutes=125))
    assert len(calls["opt"]) == 1
    source, units, combo = calls["opt"][0]
    assert source == "decision:immediate" and units == 0
    leg = combo["orders"][0]
    assert leg["symbol"] == "F" and leg["option_type"] == "CALL" and leg["strike_price"] == "14"
    assert combo["option_strategy"] == "SINGLE" and combo["limit_price"] == "0.50"
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "placed"


def test_expired_row_marked_expired(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_f_call(expires="2026-08-01")])
    calls = _run_stage(now=datetime.now() + timedelta(minutes=125))
    assert calls["opt"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "expired"


def test_decisions_per_day_cap(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path,
          [_fbtc_sell(symbol="AAPL", qty="1", trigger={"kind": "immediate"}),
           _fbtc_sell(symbol="MSFT", qty="1", trigger={"kind": "immediate"})])
    cfg = AutopilotConfig(enabled=True, decisions_enabled=True, max_decisions_per_day=1)
    calls = _run_stage(cfg=cfg, positions=[{"symbol": "AAPL", "quantity": "1"},
                                           {"symbol": "MSFT", "quantity": "1"}])
    assert len(calls["eq"]) == 1
    rows, _ = decisions_exec.load()
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["placed", "queued"]


def test_disabled_executor_touches_nothing(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_fbtc_sell(trigger={"kind": "immediate"})])
    cfg = AutopilotConfig(enabled=True, decisions_enabled=False)
    calls = _run_stage(cfg=cfg)
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_qty_all_without_position_marks_failed(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_fbtc_sell(trigger={"kind": "immediate"})])
    calls = _run_stage(positions=[])
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "failed"


def test_backdated_ts_cannot_skip_cooling(monkeypatch, tmp_path):
    """Finding I2: a hand-written BUY row with an old ts gets first_seen stamped on first
    observation and still waits out the veto window."""
    import json
    p = tmp_path / "q.jsonl"
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(p))
    row = decisions_exec.append(_f_call())
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    lines[0]["ts"] = (datetime.now() - timedelta(days=2)).isoformat()  # backdated claim
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    calls = _run_stage(now=datetime.now())
    assert calls["opt"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued" and rows[0]["first_seen"]


def test_forged_first_seen_in_queue_file_is_ignored(monkeypatch, tmp_path):
    """Re-review residual on I2: a hand-written row carrying an aged first_seen must not skip
    the veto — the executor's own map is the only cooling basis."""
    import json
    p = tmp_path / "q.jsonl"
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(p))
    decisions_exec.append(_f_call())
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    lines[0]["first_seen"] = (datetime.now() - timedelta(days=2)).isoformat()  # forged
    lines[0]["ts"] = (datetime.now() - timedelta(days=2)).isoformat()
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    calls = _run_stage(now=datetime.now())
    assert calls["opt"] == []      # executor map had no entry -> stamped now -> not cooled
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_reused_id_cannot_inherit_an_aged_stamp(monkeypatch, tmp_path):
    """Re-review new-Important: the map key includes a content hash, so a hand-written row that
    reuses a stamped id but changes ANY writer field gets a fresh cooling clock."""
    import json
    p = tmp_path / "q.jsonl"
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(p))
    old = _seed(monkeypatch, tmp_path, [_f_call()])[0]
    aged = (datetime.now() - timedelta(days=3)).isoformat()
    ap_run.state_mod.save_first_seen({ap_run._decision_key(old): aged})
    # forge: same id, different contract (strike 20 instead of 14)
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    lines[0]["option"]["strike"] = 20.0
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    calls = _run_stage(now=datetime.now())
    assert calls["opt"] == []      # fresh key -> stamped now -> cooling blocks
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_duplicate_ids_rejected_at_load(monkeypatch, tmp_path):
    p = tmp_path / "q.jsonl"
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(p))
    stored = _seed(monkeypatch, tmp_path, [_fbtc_sell(trigger={"kind": "immediate"})])[0]
    dup = dict(stored)
    dup["symbol"] = "TSLA"
    dup["qty"] = "1"
    with open(p, "a", encoding="utf-8") as f:
        import json
        f.write(json.dumps(dup) + "\n")
    rows, errs = decisions_exec.load()
    assert len(rows) == 1 and rows[0]["symbol"] == "FBTC"
    assert any("duplicate id" in e for e in errs)


def _f_vertical(**kw):
    row = {"asset": "OPTION", "symbol": "F", "side": "BUY",
           "trigger": {"kind": "immediate"}, "expires": "2026-12-31",
           "option": {"expiry": "2026-09-04", "strike": 14.0, "short_strike": 15.0,
                      "right": "C", "quantity": "1", "limit_price": "0.33"}}
    row.update(kw)
    return row


def _cooled_now(stored):
    ap_run.state_mod.save_first_seen(
        {ap_run._decision_key(stored): (datetime.now() - timedelta(minutes=200)).isoformat()})


def test_vertical_builds_a_debit_combo_pinned_to_the_queued_net(monkeypatch, tmp_path):
    stored = _seed(monkeypatch, tmp_path, [_f_vertical()])[0]
    rows, _ = decisions_exec.load()
    _cooled_now(rows[0])
    monkeypatch.setattr(ap_run, "_option_marks", lambda occs: {
        occs[0]: {"bid": 0.45, "ask": 0.49, "mid": 0.47},
        occs[1]: {"bid": 0.14, "ask": 0.15, "mid": 0.145}})
    calls = _run_stage(now=datetime.now())
    assert len(calls["opt"]) == 1
    combo = calls["opt"][0][2]
    assert combo["option_strategy"] == "VERTICAL" and combo["side"] == "BUY"
    assert float(combo["limit_price"]) == 0.33          # net pinned to the queued ceiling
    legs = combo["orders"]
    assert [l["side"] for l in legs] == ["BUY", "SELL"]
    assert [l["strike_price"] for l in legs] == ["14", "15"]
    assert legs[0]["init_exp_date"] == legs[1]["init_exp_date"] == "2026-09-04"


def test_vertical_leg_limits_never_overpay_the_ceiling():
    """Sweep: the constructed net must equal the queued ceiling or fall below it — never above
    (a sub-penny request floors to the tick), and must never collapse to a $0.00 'debit' spread,
    which would slip under every cap while being unfillable."""
    from webull_api import safety
    worst_overpay = 0.0
    for anchor in [x / 100 for x in range(5, 400)]:
        for net in [x / 1000 for x in range(5, 400, 7)]:
            got = ap_run._vertical_leg_limits({"mid": anchor}, net)
            if got is None:
                continue
            lo, sh = got
            combo = safety.build_option_combo(strategy="VERTICAL", legs=[
                safety.build_option_leg(symbol="F260904C00014000", side="BUY", quantity="1",
                                        limit_price=lo),
                safety.build_option_leg(symbol="F260904C00015000", side="SELL", quantity="1",
                                        limit_price=sh)])
            actual = float(combo["limit_price"])
            assert actual > 0, (anchor, net, combo["limit_price"])
            assert combo["side"] == "BUY"
            worst_overpay = max(worst_overpay, actual - net)
    assert worst_overpay <= 1e-9, worst_overpay


def test_vertical_leg_limits_fail_closed_on_bad_input():
    for bad in ({}, None, {"mid": None, "ask": None}, {"mid": 0}, {"mid": -1},
                {"mid": float("nan")}, {"mid": float("inf")}):
        assert ap_run._vertical_leg_limits(bad, 0.33) is None, bad
    assert ap_run._vertical_leg_limits({"mid": 0.30}, 0.33) is None   # net >= anchor
    assert ap_run._vertical_leg_limits({"mid": 1.00}, 0.005) is None  # sub-penny net
    assert ap_run._vertical_leg_limits({"mid": None, "ask": 0.49}, 0.33) == ("0.49", "0.16")


def test_vertical_short_leg_must_have_a_live_market(monkeypatch, tmp_path):
    """A typo'd short strike must not reach the broker: without a quote we can't know the
    contract exists, and a broker rejection would still burn a lifetime option unit."""
    _seed(monkeypatch, tmp_path, [_f_vertical(option={
        "expiry": "2026-09-04", "strike": 14.0, "short_strike": 99.0, "right": "C",
        "quantity": "1", "limit_price": "0.33"})])
    rows, _ = decisions_exec.load()
    _cooled_now(rows[0])
    monkeypatch.setattr(ap_run, "_option_marks",
                        lambda occs: {occs[0]: {"bid": 0.45, "ask": 0.49, "mid": 0.47}})
    calls = _run_stage(now=datetime.now())
    assert calls["opt"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_put_vertical_builds_with_the_short_below(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_f_vertical(option={
        "expiry": "2026-09-04", "strike": 14.0, "short_strike": 13.0, "right": "P",
        "quantity": "1", "limit_price": "0.25"})])
    rows, _ = decisions_exec.load()
    _cooled_now(rows[0])
    monkeypatch.setattr(ap_run, "_option_marks", lambda occs: {
        occs[0]: {"bid": 0.40, "ask": 0.44, "mid": 0.42},
        occs[1]: {"bid": 0.15, "ask": 0.18, "mid": 0.165}})
    calls = _run_stage(now=datetime.now())
    combo = calls["opt"][0][2]
    assert [l["option_type"] for l in combo["orders"]] == ["PUT", "PUT"]
    assert [l["strike_price"] for l in combo["orders"]] == ["14", "13"]
    assert combo["side"] == "BUY" and float(combo["limit_price"]) == 0.25


def test_cooling_key_covers_the_short_strike(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_f_vertical()])
    rows, _ = decisions_exec.load()
    r = rows[0]
    swapped = dict(r, option=dict(r["option"], short_strike=16.0))
    assert ap_run._decision_key(r) != ap_run._decision_key(swapped)


def test_vertical_without_marks_stays_queued(monkeypatch, tmp_path):
    stored = _seed(monkeypatch, tmp_path, [_f_vertical()])[0]
    rows, _ = decisions_exec.load()
    _cooled_now(rows[0])
    monkeypatch.setattr(ap_run, "_option_marks", lambda occs: {})
    calls = _run_stage(now=datetime.now())
    assert calls["opt"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"       # retries next run, never prices off nothing


def test_vertical_schema_rejects_credit_orientation(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    with pytest.raises(ValueError, match="ABOVE"):      # call spread with the short BELOW
        decisions_exec.append(_f_vertical(option={"expiry": "2026-09-04", "strike": 14.0,
                                                  "short_strike": 13.0, "right": "C",
                                                  "quantity": "1", "limit_price": "0.33"}))
    with pytest.raises(ValueError, match="BELOW"):      # put spread with the short ABOVE
        decisions_exec.append(_f_vertical(option={"expiry": "2026-09-04", "strike": 14.0,
                                                  "short_strike": 15.0, "right": "P",
                                                  "quantity": "1", "limit_price": "0.33"}))


def test_vertical_schema_rejects_debit_at_or_above_width(monkeypatch, tmp_path):
    import pytest
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    with pytest.raises(ValueError, match="less than"):
        decisions_exec.append(_f_vertical(option={"expiry": "2026-09-04", "strike": 14.0,
                                                  "short_strike": 15.0, "right": "C",
                                                  "quantity": "1", "limit_price": "1.00"}))


def test_option_sell_row_rejected_at_the_queue_boundary(monkeypatch, tmp_path):
    """Security review S1: a SELL-labeled OPTION row would have skipped the BUY-only cooling
    veto and open-orders guard while still building a risk-adding debit BUY. v1 is long-only."""
    import pytest
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    with pytest.raises(ValueError):
        decisions_exec.append(_f_call(side="SELL"))


def test_hand_written_option_sell_never_places(monkeypatch, tmp_path):
    """Defense in depth for S1: a row that bypasses append() (hand-written line) must still not
    place — load() rejects it, and were it to pass, the side flows to the gate's structure wall."""
    import json
    p = tmp_path / "q.jsonl"
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(p))
    row = dict(_f_call(), id="f" * 32, ts=(datetime.now() - timedelta(days=1)).isoformat(),
               status="queued", side="SELL")
    p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    rows, errs = decisions_exec.load()
    assert rows == [] and any("long-only" in e for e in errs)
    calls = _run_stage()
    assert calls["opt"] == [] and calls["eq"] == []


def test_equity_sell_qty_bounded_by_the_holding(monkeypatch, tmp_path):
    """Security review S2: gate.authorize waves SELLs past every cap as 'risk-reducing', which
    holds only when the quantity comes from a real position."""
    _seed(monkeypatch, tmp_path, [_fbtc_sell(symbol="AAPL", qty="100000",
                                             trigger={"kind": "immediate"})])
    calls = _run_stage(positions=[{"symbol": "AAPL", "quantity": "19"}])
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "failed"


def test_equity_sell_without_position_marked_failed(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_fbtc_sell(symbol="TSLA", qty="5",
                                             trigger={"kind": "immediate"})])
    calls = _run_stage(positions=[{"symbol": "AAPL", "quantity": "19"}])
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "failed"


def test_equity_sell_within_holding_places(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_fbtc_sell(symbol="AAPL", qty="5",
                                             trigger={"kind": "immediate"})])
    calls = _run_stage(positions=[{"symbol": "AAPL", "quantity": "19"}])
    assert len(calls["eq"]) == 1 and calls["eq"][0][3]["quantity"] == "5"


def test_acted_save_failure_vetoes_the_submit(monkeypatch, tmp_path):
    """Round-4 I1: if the consumed-id token cannot be persisted, the submit must NOT happen."""
    _seed(monkeypatch, tmp_path, [_fbtc_sell(trigger={"kind": "immediate"})])

    def boom(_m):
        raise OSError("state dir read-only")

    monkeypatch.setattr(ap_run.state_mod, "save_acted", boom)
    calls = _run_stage()
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"    # untouched, retries when state is writable


def test_option_units_counted_from_acted_tokens(monkeypatch, tmp_path):
    """Round-4 I2: the open-units cap reads executor-owned tokens, not the editable queue."""
    stored = _seed(monkeypatch, tmp_path, [_f_call()])[0]
    ap_run.state_mod.save_first_seen({ap_run._decision_key(stored): datetime.now().isoformat()})
    ap_run.state_mod.save_acted({"aaa": "2026-08-01T10:00:00|OPTION",
                                 "bbb": "2026-08-02T10:00:00|OPTION",
                                 "ccc": "deny:2026-08-03T10:00:00",
                                 "ddd": "failed:2026-08-04T10:00:00"})
    calls = _run_stage(now=datetime.now() + timedelta(minutes=125))
    assert len(calls["opt"]) == 1
    _source, units, _combo = calls["opt"][0]
    assert units == 2   # two attempted OPTION tokens count; deny/failed do not


def test_replayed_placed_row_is_refused(monkeypatch, tmp_path):
    """Round-3 replay finding: flipping a placed row's status back to 'queued' (identical
    content, so same cooling key) must NOT re-place — the executor-owned acted set refuses
    the consumed id."""
    import json
    p = tmp_path / "q.jsonl"
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(p))
    _seed(monkeypatch, tmp_path, [_fbtc_sell(trigger={"kind": "immediate"})])
    calls = _run_stage()
    assert len(calls["eq"]) == 1     # placed once
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    lines[0]["status"] = "queued"    # forged flip-back
    lines[0].pop("acted_ts", None)
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    calls2 = _run_stage()
    assert calls2["eq"] == []        # replay refused


def test_gate_deny_then_allow_retries_cleanly(monkeypatch, tmp_path):
    """A clean gate deny must NOT consume the id — the same decision retries next run."""
    _seed(monkeypatch, tmp_path, [_fbtc_sell(trigger={"kind": "immediate"})])

    def deny_tp(order, side, source, extra=None, extra_open=frozenset()):
        return False

    _run_stage(tp=deny_tp)
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"
    calls = _run_stage()             # next run, gate allows
    assert len(calls["eq"]) == 1
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "placed"


def test_placer_raise_parks_row_in_placing(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_fbtc_sell(trigger={"kind": "immediate"})])

    def raising_tp(order, side, source, extra=None, extra_open=frozenset()):
        raise RuntimeError("audit disk full")

    _run_stage(tp=raising_tp)
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "placing"


def test_gate_deny_returns_row_to_queued(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, [_fbtc_sell(trigger={"kind": "immediate"})])

    def deny_tp(order, side, source, extra=None, extra_open=frozenset()):
        return False  # gate deny: no exception, no errors growth

    _run_stage(tp=deny_tp)
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_submit_error_leaves_placing_never_retried(monkeypatch, tmp_path):
    """Finding I1: an errored submit MAY be live at the broker — at-most-once means the row
    parks in 'placing' and a later run alerts instead of re-placing."""
    _seed(monkeypatch, tmp_path, [_fbtc_sell(trigger={"kind": "immediate"})])
    errors = []

    def err_tp(order, side, source, extra=None, extra_open=frozenset()):
        errors.append({"symbol": order["symbol"], "error": "timeout"})
        return False

    ap_run._decisions_stage(
        acct="A", cfg=AutopilotConfig(enabled=True, decisions_enabled=True),
        now=NOW, st=DailyState(day=NOW.date().isoformat()),
        positions=[{"symbol": "FBTC", "quantity": "1.7"}],
        try_place=err_tp, try_place_option=lambda *a, **k: True,
        placed=[], skipped=[], errors=errors, protect_actioned=set(), open_orders_known=True)
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "placing"

    # a subsequent run must alert, not re-place
    calls = _run_stage()
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "placing"


def test_buy_fails_closed_when_open_orders_unreadable(monkeypatch, tmp_path):
    stored = _seed(monkeypatch, tmp_path, [_f_call()])[0]
    ap_run.state_mod.save_first_seen({ap_run._decision_key(stored): datetime.now().isoformat()})
    calls = {"opt": []}

    def tpo(combo, source, *, open_option_units):
        calls["opt"].append(source)
        return True

    ap_run._decisions_stage(
        acct="A", cfg=AutopilotConfig(enabled=True, decisions_enabled=True),
        now=datetime.now() + timedelta(minutes=125), st=DailyState(day="2026-08-07"),
        positions=[], try_place=lambda *a, **k: True, try_place_option=tpo,
        placed=[], skipped=[], errors=[], protect_actioned=set(), open_orders_known=False)
    assert calls["opt"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def _aapl_exit(**kw):
    """The real standing decision: exit the RSI2 lot when RSI(2) crosses the 70 band."""
    row = {"asset": "EQUITY", "symbol": "AAPL", "side": "SELL", "qty": "ALL",
           "order_type": "MARKET", "trigger": {"kind": "rsi2_above", "threshold": 70},
           "expires": "2026-12-31"}
    row.update(kw)
    return row


def test_rsi2_fresh_pass_confirms_without_placing(monkeypatch, tmp_path):
    """Post-close pass = CONFIRM only (2026-08-18: evening MARKET is 417-rejected; the stop
    must keep resting overnight). Nothing places; the confirmation lands in executor state."""
    from webull_api.autopilot import state as state_mod
    stored = _seed(monkeypatch, tmp_path, [_aapl_exit()])[0]
    monkeypatch.setattr(ap_run, "_rsi2_now", lambda sym, today: 90.5)
    calls = _run_stage(now=datetime(2026, 8, 18, 17, 45),
                       positions=[{"symbol": "AAPL", "quantity": "1"}])
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"
    assert state_mod.load_confirmed() == {ap_run._decision_key(stored): "2026-08-18"}


def test_rsi2_confirmed_yesterday_executes_at_the_open(monkeypatch, tmp_path):
    from webull_api.autopilot import state as state_mod
    stored = _seed(monkeypatch, tmp_path, [_aapl_exit()])[0]
    state_mod.save_confirmed({ap_run._decision_key(stored): "2026-08-06"})
    monkeypatch.setattr(ap_run, "_rsi2_now", lambda sym, today: None)   # intraday: stale bar
    calls = _run_stage(now=datetime(2026, 8, 7, 9, 35),
                       positions=[{"symbol": "AAPL", "quantity": "1"}])
    assert [(c[0], c[1], c[2]) for c in calls["eq"]] == [("AAPL", "SELL", "decision:rsi2_above")]
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "placed"
    assert state_mod.load_confirmed() == {}, "confirmation is single-use — consumed on execution"


def test_rsi2_confirmation_never_executes_the_same_evening(monkeypatch, tmp_path):
    """The 18:15 retry run re-confirms at most; it must never fire MARKET into extended hours."""
    from webull_api.autopilot import state as state_mod
    stored = _seed(monkeypatch, tmp_path, [_aapl_exit()])[0]
    state_mod.save_confirmed({ap_run._decision_key(stored): "2026-08-18"})
    monkeypatch.setattr(ap_run, "_rsi2_now", lambda sym, today: None)
    calls = _run_stage(now=datetime(2026, 8, 18, 18, 15),
                       positions=[{"symbol": "AAPL", "quantity": "1"}])
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"
    assert state_mod.load_confirmed() != {}, "unconsumed — tomorrow's open still executes it"


def test_rsi2_rewritten_row_cannot_inherit_a_confirmation(monkeypatch, tmp_path):
    """Confirmation is keyed on the writer-fields hash, like cooling — edit the row, lose it."""
    from webull_api.autopilot import state as state_mod
    stored = _seed(monkeypatch, tmp_path, [_aapl_exit()])[0]
    tampered = dict(stored, qty="999")
    state_mod.save_confirmed({ap_run._decision_key(tampered): "2026-08-06"})
    monkeypatch.setattr(ap_run, "_rsi2_now", lambda sym, today: None)
    calls = _run_stage(now=datetime(2026, 8, 7, 9, 35),
                       positions=[{"symbol": "AAPL", "quantity": "1"}])
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_rsi2_below_the_band_leaves_the_row_queued(monkeypatch, tmp_path):
    """Friday's live value: RSI(2) 61.4 -> hold, and the decision stays queued for next run."""
    _seed(monkeypatch, tmp_path, [_aapl_exit()])
    monkeypatch.setattr(ap_run, "_rsi2_now", lambda sym, today: 61.4)
    calls = _run_stage(positions=[{"symbol": "AAPL", "quantity": "1"}])
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_rsi2_unavailable_fails_closed(monkeypatch, tmp_path):
    """A stale/missing bar must not place a real SELL — and must not consume the decision."""
    _seed(monkeypatch, tmp_path, [_aapl_exit()])
    monkeypatch.setattr(ap_run, "_rsi2_now", lambda sym, today: None)
    calls = _run_stage(positions=[{"symbol": "AAPL", "quantity": "1"}])
    assert calls["eq"] == []
    rows, _ = decisions_exec.load()
    assert rows[0]["status"] == "queued"


def test_rsi2_now_refuses_a_stale_daily_bar(monkeypatch):
    """The freshness guard: yesterday's bar must yield None, never a tradable RSI."""
    stale = [{"time": "2026-08-06", "close": 300.0 + i} for i in range(30)]
    monkeypatch.setattr(ap_run.market_data, "get_bars", lambda *a, **k: stale)
    monkeypatch.setattr(ap_run, "to_ohlcv", lambda raw: raw)
    assert ap_run._rsi2_now("AAPL", "2026-08-07") is None


def test_rsi2_now_computes_from_a_fresh_bar(monkeypatch):
    closes = [300.0] * 27 + [310.0, 305.0, 312.0]
    bars = [{"time": f"2026-07-{(i % 28) + 1:02d}", "close": c} for i, c in enumerate(closes[:-1])]
    bars.append({"time": "2026-08-07", "close": closes[-1]})
    monkeypatch.setattr(ap_run.market_data, "get_bars", lambda *a, **k: bars)
    monkeypatch.setattr(ap_run, "to_ohlcv", lambda raw: raw)
    val = ap_run._rsi2_now("AAPL", "2026-08-07")
    assert val is not None and 0.0 <= val <= 100.0


def test_rsi2_now_swallows_broker_errors(monkeypatch):
    def boom(*a, **k):
        raise Exception("HTTP Status: 429, Code: TOO_MANY_REQUESTS")
    monkeypatch.setattr(ap_run.market_data, "get_bars", boom)
    assert ap_run._rsi2_now("AAPL", "2026-08-07") is None
