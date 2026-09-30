from webull_api import reconcile


def test_is_stop_protected_flattens_combo_envelope_and_camelcase():
    oo = [{"orders": [{"ticker": "GE", "side": "SELL", "orderType": "STOP_LOSS_LIMIT", "totalQuantity": "5"}]}]
    assert reconcile.is_stop_protected(oo, "ge", 5) is True


def test_is_stop_protected_ignores_buy_non_stop_and_other_symbol():
    oo = [
        {"symbol": "AMD", "side": "BUY", "order_type": "STOP_LOSS_LIMIT", "quantity": "5"},
        {"symbol": "AMD", "side": "SELL", "order_type": "LIMIT", "quantity": "5"},
        {"symbol": "GE", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "5"},
    ]
    assert reconcile.is_stop_protected(oo, "AMD", 5) is False


def test_is_stop_protected_handles_data_envelope_and_junk():
    assert reconcile.is_stop_protected(
        {"data": [{"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "5"}]}, "AMD", 5) is True
    assert reconcile.is_stop_protected(None, "AMD", 5) is False
    assert reconcile.is_stop_protected([None, 3, "x"], "AMD", 5) is False


def test_protective_order_shape_and_validates():
    o = reconcile.protective_order("AMD", 5, 9.50)
    assert o["side"] == "SELL"
    # market-on-trigger stop so a protective stop is guaranteed to fill on a gap-down
    assert o["order_type"] == "STOP_LOSS"
    assert o["time_in_force"] == "GTC"
    assert o["quantity"] == "5"
    assert o["stop_price"] == "9.5"
    # a bare STOP_LOSS carries NO limit price (market on trigger)
    assert "limit_price" not in o
    # validate_order (no last_price) must accept it
    from webull_api import safety
    safety.validate_order(o)


def test_protective_order_is_bare_stop_across_price_range():
    # market-on-trigger works at any price (no limit-rounding edge cases like a stop-limit had)
    from webull_api import safety
    for stop in (0.50, 1.00, 1.67, 9.50, 50.0):
        o = reconcile.protective_order("X", 1, stop)
        assert o["order_type"] == "STOP_LOSS" and "limit_price" not in o
        safety.validate_order(o)


def _pos(symbol, qty, cost, last=None, instrument_type="EQUITY"):
    return {"symbol": symbol, "quantity": qty, "cost_price": cost,
            "last_price": last, "instrument_type": instrument_type}


def test_scan_drafts_structural_stop_from_plan_when_present():
    rows = reconcile.scan_unprotected(
        "ACCT",
        plans={"AMD": {"symbol": "AMD", "structural_stop": 9.10}},
        get_positions=lambda a: [_pos("AMD", 5, 10.0, 10.5)],
        get_open_orders=lambda a: [],
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.stop_source == "structural"
    assert r.stop_price == 9.10
    assert r.protective["order_type"] == "STOP_LOSS" and r.protective["side"] == "SELL"


def test_scan_falls_back_to_pct_backstop_without_plan():
    rows = reconcile.scan_unprotected(
        "ACCT", plans={}, stop_loss_pct=8.0,
        get_positions=lambda a: [_pos("GE", 3, 20.0)],
        get_open_orders=lambda a: [],
    )
    assert rows[0].stop_source == "backstop"
    assert rows[0].stop_price == 18.40   # 20 * 0.92


def test_scan_skips_positions_that_already_have_a_resting_stop():
    rows = reconcile.scan_unprotected(
        "ACCT", plans={},
        get_positions=lambda a: [_pos("AMD", 5, 10.0)],
        get_open_orders=lambda a: [{"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS_LIMIT"}],
    )
    assert rows == []


def test_scan_skips_options_short_and_zero_qty():
    rows = reconcile.scan_unprotected(
        "ACCT", plans={},
        get_positions=lambda a: [
            _pos("OPT", 1, 5.0, instrument_type="OPTION"),
            _pos("SHRT", -4, 10.0),
            _pos("ZERO", 0, 10.0),
        ],
        get_open_orders=lambda a: [],
    )
    assert rows == []


def test_scan_no_cost_and_no_plan_is_needs_manual():
    rows = reconcile.scan_unprotected(
        "ACCT", plans={},
        get_positions=lambda a: [_pos("AMD", 5, None)],
        get_open_orders=lambda a: [],
    )
    assert rows[0].stop_source == "manual" and rows[0].protective is None


def test_scan_whole_share_suppressed_when_a_closing_sell_is_already_working():
    """The 17:45 exit cancels the stop and rests a MARKET SELL for next open; a retry pass in
    that window must not stack a fresh stop onto shares the sell has committed (417)."""
    rows = reconcile.scan_unprotected(
        "ACCT", plans={},
        get_positions=lambda a: [_pos("AAPL", 1, 303.0, 310.0)],
        get_open_orders=lambda a: [{"symbol": "AAPL", "side": "SELL", "order_type": "MARKET",
                                    "total_quantity": "1", "status": "SUBMITTED"}],
    )
    assert rows == []


def test_scan_still_drafts_when_the_working_sell_under_covers_the_position():
    rows = reconcile.scan_unprotected(
        "ACCT", plans={},
        get_positions=lambda a: [_pos("AMD", 5, 10.0)],
        get_open_orders=lambda a: [{"symbol": "AMD", "side": "SELL", "order_type": "MARKET",
                                    "total_quantity": "2"}],
    )
    assert len(rows) == 1 and rows[0].protective is not None


def test_scan_closing_sell_guard_ignores_buys_and_other_symbols():
    rows = reconcile.scan_unprotected(
        "ACCT", plans={},
        get_positions=lambda a: [_pos("AMD", 5, 10.0)],
        get_open_orders=lambda a: [
            {"symbol": "AMD", "side": "BUY", "order_type": "MARKET", "total_quantity": "5"},
            {"symbol": "MSFT", "side": "SELL", "order_type": "MARKET", "total_quantity": "5"},
        ],
    )
    assert len(rows) == 1


def test_scan_suppresses_on_unparseable_closing_sell_qty():
    # mirror is_stop_protected: presence fallback when coverage cannot be PROVEN short —
    # a duplicate stop draft (417 reject) is the noisier failure, not the safer one
    rows = reconcile.scan_unprotected(
        "ACCT", plans={},
        get_positions=lambda a: [_pos("AMD", 5, 10.0)],
        get_open_orders=lambda a: [{"symbol": "AMD", "side": "SELL", "order_type": "MARKET",
                                    "total_quantity": "junk"}],
    )
    assert rows == []


def test_scan_open_orders_read_failure_is_conservative_drafts_protection():
    def boom(a):
        raise RuntimeError("orders endpoint down")
    rows = reconcile.scan_unprotected(
        "ACCT", plans={"AMD": {"structural_stop": 9.0}},
        get_positions=lambda a: [_pos("AMD", 5, 10.0)],
        get_open_orders=boom,
    )
    # cannot prove protection -> draft anyway (a missing stop is the dangerous failure)
    assert len(rows) == 1 and rows[0].protective is not None


def test_scan_recovers_from_a_bad_plan_record_and_still_protects_others():
    """A plan stop that fails plan_stop_is_sane no longer strands the position on an error row
    (which left it UNPROTECTED); it falls back to the percentage backstop and says so loudly.
    Owner decision 2026-08-15, option (a): an unprotected position is the worse failure."""
    rows = reconcile.scan_unprotected(
        "ACCT",
        plans={"AMD": {"structural_stop": -5.0}},   # nonsense -> rejected, not obeyed
        get_positions=lambda a: [_pos("AMD", 5, 10.0), _pos("GE", 3, 20.0)],
        get_open_orders=lambda a: [],
    )
    by = {r.symbol: r for r in rows}
    assert by["AMD"].stop_source == "backstop:plan-rejected"
    assert by["AMD"].protective is not None, "a rejected plan must still leave the lot protected"
    assert by["AMD"].stop_price == 9.2 and by["AMD"].rejected_plan_stop == -5.0
    # the good position is still evaluated (not discarded by AMD's bad plan)
    assert by["GE"].stop_source == "backstop" and by["GE"].protective is not None


def test_stale_plan_stop_is_rejected_in_favour_of_the_backstop():
    """The live 2026-08-13 defect: a 2026-07-07 artifact plan (structural_stop 28.0, written when
    the entry band capped near $80) put a $28.00 stop under a $303 AAPL share — protection that
    triggers at -91%. The correct value is the -8% backstop, 278.76."""
    rows = reconcile.scan_unprotected(
        "ACCT",
        plans={"AAPL": {"structural_stop": 28.0, "entry": 30.0, "source": "autopilot"}},
        get_positions=lambda a: [_pos("AAPL", 1, 303.00)],
        get_open_orders=lambda a: [],
    )
    assert rows[0].stop_price == 278.76
    assert rows[0].stop_source == "backstop:plan-rejected"
    assert rows[0].rejected_plan_stop == 28.0


def test_a_sane_plan_stop_is_still_preferred_over_the_backstop():
    """The guard is an absurdity filter, not a policy change — a plausible structural stop wins."""
    rows = reconcile.scan_unprotected(
        "ACCT",
        plans={"AAPL": {"structural_stop": 285.0}},
        get_positions=lambda a: [_pos("AAPL", 1, 303.00)],
        get_open_orders=lambda a: [],
    )
    assert rows[0].stop_price == 285.0 and rows[0].stop_source == "structural"


def test_is_fractional_detects_partial_shares():
    assert reconcile.is_fractional(1.7) is True
    assert reconcile.is_fractional(0.5) is True
    assert reconcile.is_fractional(5) is False
    assert reconcile.is_fractional(5.0) is False


def test_has_working_sell_matches_any_sell_type_not_just_stops():
    # is_stop_protected only sees STOP types; a synthetic MARKET/LIMIT sell must still be seen.
    for otype in ("MARKET", "LIMIT", "STOP_LOSS"):
        oo = [{"symbol": "FBTC", "side": "SELL", "order_type": otype}]
        assert reconcile.has_working_sell(oo, "fbtc") is True


def test_has_working_sell_ignores_buys_and_other_symbols():
    oo = [
        {"symbol": "FBTC", "side": "BUY", "order_type": "MARKET"},
        {"symbol": "AMD", "side": "SELL", "order_type": "MARKET"},
    ]
    assert reconcile.has_working_sell(oo, "FBTC") is False
    assert reconcile.has_working_sell(None, "FBTC") is False


def test_synthetic_stop_order_fires_only_at_or_below_the_stop():
    from webull_api import safety
    # above the stop -> nothing to do
    assert reconcile.synthetic_stop_order("FBTC", 1.7, 52.0, 57.31) is None
    # at/below -> a full-size MARKET sell
    for last in (52.0, 51.04):
        o = reconcile.synthetic_stop_order("FBTC", 1.7, 52.0, last)
        assert o["side"] == "SELL"
        assert o["order_type"] == "MARKET"
        assert o["time_in_force"] == "DAY"
        assert o["support_trading_session"] == "CORE"
        assert o["quantity"] == "1.7"
        # a MARKET order must carry neither price
        assert "limit_price" not in o and "stop_price" not in o
        safety.validate_order(o)


def test_synthetic_stop_order_needs_both_prices():
    assert reconcile.synthetic_stop_order("FBTC", 1.7, None, 50.0) is None
    assert reconcile.synthetic_stop_order("FBTC", 1.7, 52.0, None) is None


def test_synthetic_decision_fails_closed_on_unknown_state():
    # Opposite of the resting-stop path: a synthetic sell EXECUTES, so unknown -> do nothing.
    o, why = reconcile.synthetic_decision([], False, "FBTC", 1.7, 52.0, 50.0)
    assert o is None and why == "fractional: open orders unreadable"

    oo = [{"symbol": "FBTC", "side": "SELL", "order_type": "MARKET"}]
    o, why = reconcile.synthetic_decision(oo, True, "FBTC", 1.7, 52.0, 50.0)
    assert o is None and why == "fractional: sell already working"

    o, why = reconcile.synthetic_decision([], True, "FBTC", 1.7, 52.0, None)
    assert o is None and why == "fractional: last price unknown"

    o, why = reconcile.synthetic_decision([], True, "FBTC", 1.7, None, 50.0)
    assert o is None and why == "fractional: no stop level"


def test_synthetic_decision_above_stop_is_monitored_not_placed():
    o, why = reconcile.synthetic_decision([], True, "FBTC", 1.7, 52.0, 57.31)
    assert o is None and why == "fractional: monitored, above stop"


def test_synthetic_decision_places_when_breached():
    o, why = reconcile.synthetic_decision([], True, "FBTC", 1.7, 52.0, 51.0)
    assert why is None
    assert o["order_type"] == "MARKET" and o["side"] == "SELL" and o["quantity"] == "1.7"


def test_scan_routes_fractional_to_a_synthetic_stop_not_a_resting_one():
    rows = reconcile.scan_unprotected(
        "ACC",
        get_positions=lambda a: [_pos("FBTC", 1.7, 55.48, 51.00)],
        get_open_orders=lambda a: [],
        stop_loss_pct=8.0,
    )
    (r,) = rows
    assert r.fractional is True
    # a fractional position can NEVER carry a resting stop
    assert r.protective is None
    # 55.48 * 0.92 = 51.04 backstop; last 51.00 is below it -> fire
    assert r.synthetic is not None
    assert r.synthetic["order_type"] == "MARKET"
    assert r.synthetic["quantity"] == "1.7"
    assert r.skip_reason is None


def test_scan_fractional_above_stop_is_monitored_only():
    rows = reconcile.scan_unprotected(
        "ACC",
        get_positions=lambda a: [_pos("FBTC", 1.7, 55.48, 57.31)],
        get_open_orders=lambda a: [],
    )
    (r,) = rows
    assert r.fractional is True
    assert r.synthetic is None and r.protective is None
    assert r.skip_reason == "fractional: monitored, above stop"


def test_scan_fractional_suppressed_when_a_sell_is_already_working():
    rows = reconcile.scan_unprotected(
        "ACC",
        get_positions=lambda a: [_pos("FBTC", 1.7, 55.48, 51.00)],
        get_open_orders=lambda a: [{"symbol": "FBTC", "side": "SELL", "order_type": "MARKET"}],
    )
    (r,) = rows
    assert r.synthetic is None
    assert r.skip_reason == "fractional: sell already working"


def test_scan_fractional_fails_closed_when_open_orders_unreadable():
    def boom(_a):
        raise RuntimeError("broker down")
    rows = reconcile.scan_unprotected(
        "ACC",
        get_positions=lambda a: [_pos("FBTC", 1.7, 55.48, 51.00)],
        get_open_orders=boom,
    )
    (r,) = rows
    assert r.synthetic is None
    assert r.skip_reason == "fractional: open orders unreadable"


def test_scan_fractional_fails_closed_on_unrecognised_open_orders_shapes():
    # Each of these is a non-raising 200 return with a body _rows() cannot recognise as
    # real data — a rate-limit envelope and the exact fallback dict trading._safe_json
    # returns when res.json() raises (e.g. a WAF/gateway HTML page). Both must fail closed
    # exactly like a raised exception, not silently degrade to "no open orders".
    for bad in (
        {"code": "RATE_LIMIT", "msg": "too many requests"},
        {"status_code": 200, "text": "<html>upstream error</html>"},
        None,
    ):
        rows = reconcile.scan_unprotected(
            "ACC",
            get_positions=lambda a: [_pos("FBTC", 1.7, 55.48, 51.00)],
            get_open_orders=lambda a, _bad=bad: _bad,
        )
        (r,) = rows
        assert r.synthetic is None, bad
        assert r.skip_reason == "fractional: open orders unreadable", bad


def test_scan_fractional_recognises_empty_list_as_known_no_open_orders():
    # A genuinely empty read ([]) legitimately means "no open orders" -> oo_ok=True,
    # so the synthetic sell IS built (distinct from the unrecognised shapes above).
    rows = reconcile.scan_unprotected(
        "ACC",
        get_positions=lambda a: [_pos("FBTC", 1.7, 55.48, 51.00)],
        get_open_orders=lambda a: [],
    )
    (r,) = rows
    assert r.synthetic is not None
    assert r.skip_reason is None


def test_scan_fractional_recognises_data_envelope_as_known_no_open_orders():
    # {"data": []} is the recognised dict envelope for a genuinely empty read -> oo_ok=True.
    rows = reconcile.scan_unprotected(
        "ACC",
        get_positions=lambda a: [_pos("FBTC", 1.7, 55.48, 51.00)],
        get_open_orders=lambda a: {"data": []},
    )
    (r,) = rows
    assert r.synthetic is not None
    assert r.skip_reason is None


def test_scan_whole_share_position_is_unchanged():
    rows = reconcile.scan_unprotected(
        "ACC",
        get_positions=lambda a: [_pos("AMD", 5, 10.0, 10.5)],
        get_open_orders=lambda a: [],
    )
    (r,) = rows
    assert r.fractional is False
    assert r.synthetic is None and r.skip_reason is None
    assert r.protective is not None and r.protective["order_type"] == "STOP_LOSS"


def test_stop_covered_qty_sums_matching_sell_stops():
    oo = [
        {"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "3"},
        {"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS_LIMIT", "quantity": "2"},
        {"symbol": "AMD", "side": "BUY", "order_type": "STOP_LOSS", "quantity": "9"},   # buy, ignored
        {"symbol": "GE", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "5"},   # other sym
        {"symbol": "AMD", "side": "SELL", "order_type": "LIMIT", "quantity": "9"},      # not a stop
    ]
    covered, all_parsed = reconcile.stop_covered_qty(oo, "amd")
    assert covered == 5.0
    assert all_parsed is True


def test_stop_covered_qty_flags_an_unparseable_leg():
    oo = [
        {"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "3"},
        {"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS", "quantity": None},
    ]
    covered, all_parsed = reconcile.stop_covered_qty(oo, "AMD")
    assert covered == 3.0          # the parseable leg still counts
    assert all_parsed is False     # but coverage is only a lower bound


def test_stop_covered_qty_reads_camelcase_and_alt_keys():
    oo = [{"orders": [{"ticker": "GE", "side": "SELL", "orderType": "STOP_LOSS", "totalQuantity": "4"}]}]
    covered, all_parsed = reconcile.stop_covered_qty(oo, "GE")
    assert covered == 4.0 and all_parsed is True


def test_is_stop_protected_full_and_over_coverage():
    oo = [{"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "5"}]
    assert reconcile.is_stop_protected(oo, "AMD", 5) is True     # exact
    assert reconcile.is_stop_protected(oo, "AMD", 4) is True     # over-covers


def test_is_stop_protected_under_coverage_is_not_protected():
    oo = [{"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "3"}]
    assert reconcile.is_stop_protected(oo, "AMD", 5) is False    # the masking fix


def test_is_stop_protected_partial_stops_sum_to_full():
    oo = [
        {"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "3"},
        {"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS_LIMIT", "quantity": "2"},
    ]
    assert reconcile.is_stop_protected(oo, "AMD", 5) is True


def test_is_stop_protected_no_stop_is_not_protected():
    oo = [{"symbol": "AMD", "side": "SELL", "order_type": "LIMIT", "quantity": "5"}]
    assert reconcile.is_stop_protected(oo, "AMD", 5) is False


def test_is_stop_protected_unparseable_falls_back_to_protected():
    # Can't PROVE under-coverage -> must not regress into drafting a duplicate stop.
    oo = [{"symbol": "AMD", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "x"}]
    assert reconcile.is_stop_protected(oo, "AMD", 5) is True


def test_is_stop_protected_fractional_exact_coverage():
    oo = [{"symbol": "FBTC", "side": "SELL", "order_type": "STOP_LOSS", "quantity": "1.7"}]
    assert reconcile.is_stop_protected(oo, "FBTC", 1.7) is True   # epsilon guards the float compare


def test_scan_drafts_when_resting_stop_under_covers_the_position():
    # 5 shares held, a resting stop for only 2 -> masked before, now drafts protection.
    rows = reconcile.scan_unprotected(
        "ACC",
        get_positions=lambda a: [_pos("AMD", 5, 10.0, 10.5)],
        get_open_orders=lambda a: [{"symbol": "AMD", "side": "SELL",
                                    "order_type": "STOP_LOSS", "quantity": "2"}],
    )
    (r,) = rows
    assert r.protective is not None and r.protective["order_type"] == "STOP_LOSS"
    assert r.protective["quantity"] == "5"   # full-size draft, per spec non-goal on partial sizing


def test_scan_skips_when_resting_stop_fully_covers_the_position():
    rows = reconcile.scan_unprotected(
        "ACC",
        get_positions=lambda a: [_pos("AMD", 5, 10.0, 10.5)],
        get_open_orders=lambda a: [{"symbol": "AMD", "side": "SELL",
                                    "order_type": "STOP_LOSS", "quantity": "5"}],
    )
    assert rows == []   # fully protected -> no row, parity with the old quantity-blind skip
