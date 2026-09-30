import pytest

from webull_api.strategy import rsi2


def test_rsi2_of_short_series_is_none():
    assert rsi2.rsi2_of([10.0, 11.0]) is None  # need > period(2) points


def test_rsi2_of_rising_series_is_high():
    # two consecutive gains -> avg_loss 0 -> RSI(2) == 100
    assert rsi2.rsi2_of([8.0, 9.0, 10.0, 11.0, 12.0]) == 100.0


def test_rsi2_of_falling_series_is_zero():
    assert rsi2.rsi2_of([12.0, 11.0, 10.0, 9.0, 8.0]) == 0.0


def test_exit_when_rsi_above_70():
    d = rsi2.decide(owned_lots=[{"symbol": "NVDA", "shares": 31}], cash=79000.0,
                    rsi_by_symbol={"NVDA": 93.75}, price_by_symbol={"NVDA": 204.12})
    sells = [x for x in d if x["action"] == "SELL"]
    assert len(sells) == 1 and sells[0]["symbol"] == "NVDA" and sells[0]["quantity"] == 31
    assert "exit" in sells[0]["reason"]


def test_no_exit_at_exactly_70():
    d = rsi2.decide(owned_lots=[{"symbol": "NVDA", "shares": 31}], cash=79000.0,
                    rsi_by_symbol={"NVDA": 70.0}, price_by_symbol={"NVDA": 204.12})
    assert not any(x["action"] == "SELL" for x in d)


def test_entry_when_rsi_below_10_sizes_whole_shares():
    d = rsi2.decide(owned_lots=[], cash=79000.0,
                    rsi_by_symbol={"AAPL": 8.0}, price_by_symbol={"AAPL": 200.0})
    buys = [x for x in d if x["action"] == "BUY"]
    assert len(buys) == 1 and buys[0]["symbol"] == "AAPL" and buys[0]["quantity"] == 30  # floor(6000/200)


def test_no_entry_at_exactly_10():
    d = rsi2.decide(owned_lots=[], cash=79000.0,
                    rsi_by_symbol={"AAPL": 10.0}, price_by_symbol={"AAPL": 200.0})
    assert not any(x["action"] == "BUY" for x in d)


def test_excluded_manual_hold_never_entered():
    # Explicit cfg: under the default config no excluded name is in the universe (pinned
    # below), so only a custom overlap exercises the exclusion branch non-vacuously.
    cfg = rsi2.Rsi2Config(universe=("AVUV", "AAPL"), excluded=("AVUV",))
    d = rsi2.decide(owned_lots=[], cash=79000.0,
                    rsi_by_symbol={"AVUV": 5.0, "AAPL": 6.0},
                    price_by_symbol={"AVUV": 120.0, "AAPL": 200.0}, cfg=cfg)
    assert not any(x["symbol"] == "AVUV" for x in d)
    assert any(x["action"] == "BUY" and x["symbol"] == "AAPL" for x in d)


def test_owned_symbol_not_reentered():
    d = rsi2.decide(owned_lots=[{"symbol": "AAPL", "shares": 30}], cash=79000.0,
                    rsi_by_symbol={"AAPL": 5.0}, price_by_symbol={"AAPL": 200.0})
    assert not any(x["action"] == "BUY" and x["symbol"] == "AAPL" for x in d)


def test_max_lots_caps_and_ranks_most_oversold_first():
    owned = [{"symbol": s, "shares": 1} for s in ["GOOG", "LLY", "AMZN", "MSFT", "VOO"]]  # 5 lots, 1 slot
    d = rsi2.decide(owned_lots=owned, cash=200000.0,
                    rsi_by_symbol={"AAPL": 5.0, "NVDA": 4.0},
                    price_by_symbol={"AAPL": 100.0, "NVDA": 100.0})
    buys = [x for x in d if x["action"] == "BUY"]
    assert len(buys) == 1 and buys[0]["symbol"] == "NVDA"  # rsi 4 < 5 -> most oversold wins the slot


def test_cash_floor_blocks_entry():
    d = rsi2.decide(owned_lots=[], cash=25000.0,  # 25000 - 6000 = 19000 < 20000 floor
                    rsi_by_symbol={"AAPL": 5.0}, price_by_symbol={"AAPL": 200.0})
    assert not any(x["action"] == "BUY" for x in d)


def test_zero_share_price_skipped():
    d = rsi2.decide(owned_lots=[], cash=79000.0,
                    rsi_by_symbol={"NVDA": 5.0}, price_by_symbol={"NVDA": 7000.0})  # floor(6000/7000)=0
    assert not any(x["action"] == "BUY" for x in d)


def test_exit_before_entry_and_frees_cash():
    d = rsi2.decide(owned_lots=[{"symbol": "NVDA", "shares": 100}], cash=21000.0,
                    rsi_by_symbol={"NVDA": 95.0, "AAPL": 5.0},
                    price_by_symbol={"NVDA": 200.0, "AAPL": 100.0})
    actions = [(x["action"], x["symbol"]) for x in d]
    assert ("SELL", "NVDA") in actions and ("BUY", "AAPL") in actions  # exit frees 20000 -> entry fits
    assert actions.index(("SELL", "NVDA")) < actions.index(("BUY", "AAPL"))


def test_universe_is_the_deliberate_basket():
    # Junk dropped from the hand-ledger port must stay out (spec 2026-07-12), as must LLY
    # (2026-07-28 red-team drop: binary-gap criteria violation); the owner's manual holds
    # must never overlap the tradable universe.
    for junk in ("RIVN", "GRAB", "VZ", "MELI", "LLY"):
        assert junk not in rsi2.UNIVERSE
    assert not set(rsi2.UNIVERSE) & set(rsi2.EXCLUDED_MANUAL_HOLDS)
    assert {"JPM", "V", "COST", "IWM"} <= set(rsi2.UNIVERSE)  # the 2026-07-12 additions
    # The 2026-07-28 owner-signed widening (13 -> 20, each vetted via backtest_rsi2 --universe):
    assert {"AVGO", "CAT", "SMH", "META", "ABBV", "WMT", "DIA", "JNJ"} <= set(rsi2.UNIVERSE)
    # The 2026-08-07 owner-signed widening (20 -> 28), for THROUGHPUT not edge; each name cleared
    # >= +1.20%/trade AND was positive in both SPY regimes over 1,200 bars. Report:
    # docs/reviews/2026-08-07-rsi2-universe-expansion-vetting.md
    assert {"ANET", "KLAC", "LRCX", "GS", "ETN", "RTX", "GE", "AXP"} <= set(rsi2.UNIVERSE)
    # Rejected to avoid semis concentration on top of NVDA/AVGO/SMH (MU also risk-off negative):
    for semis in ("AMAT", "AMD", "MU"):
        assert semis not in rsi2.UNIVERSE
    assert len(rsi2.UNIVERSE) == 28
    assert len(set(rsi2.UNIVERSE)) == len(rsi2.UNIVERSE)  # no dupes
    assert "META" not in rsi2.EXCLUDED_MANUAL_HOLDS  # stale hold resolved with the widening


def test_real_config_overrides_only_sizing_fields():
    cfg = rsi2.real_config(dollars=300.0, max_lots=1)
    assert cfg.dollars_per_signal == 300.0 and cfg.max_lots == 1
    assert cfg.cash_floor == 0.0
    # entry/exit rules are shared with paper and must NOT drift
    assert cfg.entry_below == rsi2.DEFAULT_CONFIG.entry_below
    assert cfg.exit_above == rsi2.DEFAULT_CONFIG.exit_above
    assert cfg.universe == rsi2.DEFAULT_CONFIG.universe


def test_entries_pick_the_most_oversold_affordable_name():
    cfg = rsi2.real_config(dollars=400.0, max_lots=1)
    out = rsi2.decide_entries_cash(
        owned_lots=[], settled_cash=400.0,
        rsi_by_symbol={"AAPL": 8.0, "JNJ": 3.0, "MSFT": 9.0},
        price_by_symbol={"AAPL": 300.0, "JNJ": 150.0, "MSFT": 380.0}, cfg=cfg)
    assert [d["symbol"] for d in out] == ["JNJ"]          # lowest RSI(2) wins
    assert out[0]["quantity"] == 2.0                       # floor(400/150)
    assert out[0]["action"] == "BUY"


def test_entries_never_exceed_settled_cash():
    cfg = rsi2.real_config(dollars=1000.0, max_lots=1)
    out = rsi2.decide_entries_cash(
        owned_lots=[], settled_cash=310.0,
        rsi_by_symbol={"AAPL": 5.0}, price_by_symbol={"AAPL": 300.0}, cfg=cfg)
    assert out[0]["quantity"] == 1.0    # budget is min(dollars, settled_cash)


def test_no_entry_when_a_whole_share_is_unaffordable():
    cfg = rsi2.real_config(dollars=None, max_lots=1)
    out = rsi2.decide_entries_cash(
        owned_lots=[], settled_cash=100.0,
        rsi_by_symbol={"AAPL": 5.0}, price_by_symbol={"AAPL": 300.0}, cfg=cfg)
    assert out == []


def test_unaffordable_reports_the_skipped_signal():
    cfg = rsi2.real_config(dollars=None, max_lots=1)
    rows = rsi2.unaffordable(
        owned_lots=[], settled_cash=100.0,
        rsi_by_symbol={"AAPL": 5.0}, price_by_symbol={"AAPL": 300.0}, cfg=cfg)
    assert rows == [{"symbol": "AAPL", "price": 300.0, "rsi2": 5.0}]


def test_unaffordable_is_empty_when_no_slot_is_open():
    """I2: a slot-full skip is NOT a funding skip. Reporting it as 'unaffordable' poisons the
    exact proof-bar distinction this function exists to protect."""
    cfg = rsi2.real_config(dollars=None, max_lots=1)
    rows = rsi2.unaffordable(
        owned_lots=[{"symbol": "AAPL", "shares": 1}], settled_cash=10.0,
        rsi_by_symbol={"JNJ": 3.0, "MSFT": 2.0},
        price_by_symbol={"JNJ": 150.0, "MSFT": 380.0}, cfg=cfg)
    assert rows == []


def test_max_lots_respected_and_owned_never_rebought():
    cfg = rsi2.real_config(dollars=None, max_lots=1)
    out = rsi2.decide_entries_cash(
        owned_lots=[{"symbol": "AAPL", "shares": 1}], settled_cash=5000.0,
        rsi_by_symbol={"AAPL": 4.0, "JNJ": 3.0},
        price_by_symbol={"AAPL": 300.0, "JNJ": 150.0}, cfg=cfg)
    assert out == []                                       # slot already used


def test_exit_proceeds_are_never_credited_as_buying_power():
    """The reason this exists instead of reusing decide(): an owned lot above the exit band
    must NOT free cash or a slot, because its exit is a standing row that may not have fired."""
    cfg = rsi2.real_config(dollars=None, max_lots=1)
    out = rsi2.decide_entries_cash(
        owned_lots=[{"symbol": "AAPL", "shares": 1}], settled_cash=0.0,
        rsi_by_symbol={"AAPL": 95.0, "JNJ": 3.0},
        price_by_symbol={"AAPL": 300.0, "JNJ": 150.0}, cfg=cfg)
    assert out == []


def test_excluded_and_unpriced_names_are_skipped():
    cfg = rsi2.real_config(dollars=None, max_lots=2)
    out = rsi2.decide_entries_cash(
        owned_lots=[], settled_cash=1000.0,
        rsi_by_symbol={"AVUV": 2.0, "JNJ": 3.0}, price_by_symbol={"AVUV": 12.0}, cfg=cfg)
    assert out == []    # AVUV is an excluded manual hold; JNJ has no price


# ---- real_budget (spec 2026-09-23 §2): net liq / divisor, clipped under the cap; fail closed ----

def test_real_budget_unset_divisor_passes_dollars_through():
    """Unset divisor == today's path, byte for byte: dollars unchanged, None still means infinity."""
    assert rsi2.real_budget(net_liq=3037.39, divisor=None, dollars=None, cap=525.0) == (None, "unset")
    assert rsi2.real_budget(net_liq=None, divisor=None, dollars=420.0, cap=None) == (420.0, "unset")


def test_real_budget_divisor_sizes_a_sixth_of_net_liq():
    val, basis = rsi2.real_budget(net_liq=3037.39, divisor=6, dollars=None, cap=525.0)
    assert basis == "divisor" and val == pytest.approx(3037.39 / 6)


def test_real_budget_cap_clips_the_slot():
    assert rsi2.real_budget(net_liq=3600.0, divisor=6, dollars=None, cap=525.0) == (525.0, "cap")


def test_real_budget_dollars_left_set_still_clips():
    assert rsi2.real_budget(net_liq=3037.39, divisor=6, dollars=500.0, cap=525.0) == (500.0, "dollars")


def test_real_budget_ties_name_the_owner_action_term():
    """cap == slot -> 'cap' (raise it); dollars == slot -> 'dollars' (delete it); cap == dollars -> 'cap'."""
    assert rsi2.real_budget(net_liq=3150.0, divisor=6, dollars=None, cap=525.0) == (525.0, "cap")
    assert rsi2.real_budget(net_liq=3000.0, divisor=6, dollars=500.0, cap=525.0) == (500.0, "dollars")
    assert rsi2.real_budget(net_liq=3000.0, divisor=6, dollars=500.0, cap=500.0) == (500.0, "cap")


def test_real_budget_without_a_cap_or_dollars_is_the_slot_itself():
    assert rsi2.real_budget(net_liq=6000.0, divisor=6, dollars=None, cap=None) == (1000.0, "divisor")


@pytest.mark.parametrize("nl", [None, 0.0, -1.0, float("nan"), float("inf"), "abc"])
def test_real_budget_fails_closed_on_a_bad_net_liq(nl):
    """A zero / missing / garbled net liq must never widen to settled cash (M7 precedent)."""
    assert rsi2.real_budget(net_liq=nl, divisor=6, dollars=420.0, cap=525.0) == (None, "net_liq")


@pytest.mark.parametrize("d", [0, -6, float("inf"), float("nan"), "six"])
def test_real_budget_fails_closed_on_a_bad_divisor(d):
    assert rsi2.real_budget(net_liq=3037.39, divisor=d, dollars=420.0, cap=525.0) == (None, "divisor")
