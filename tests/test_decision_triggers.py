# tests/test_decision_triggers.py
from datetime import date, datetime, timedelta
from webull_api import decision_triggers as dt


def test_green_day():
    assert dt.evaluate({"kind": "green_day"}, last=56.5, prev_close=56.13)[0] is True
    assert dt.evaluate({"kind": "green_day"}, last=56.0, prev_close=56.13)[0] is False
    assert dt.evaluate({"kind": "green_day"}, last=None, prev_close=56.13)[0] is False


def test_immediate_and_levels():
    assert dt.evaluate({"kind": "immediate"}, last=None, prev_close=None)[0] is True
    assert dt.evaluate({"kind": "price_above", "level": 14.0}, last=14.2, prev_close=None)[0] is True
    assert dt.evaluate({"kind": "price_below", "level": 13.4}, last=13.5, prev_close=None)[0] is False
    assert dt.evaluate({"kind": "moon_phase"}, last=1, prev_close=1)[0] is False


def test_cooling_sell_exempt_buy_gated():
    now = datetime(2026, 8, 7, 13, 0)
    fresh = {"side": "BUY", "ts": (now - timedelta(minutes=30)).isoformat()}
    aged = {"side": "BUY", "ts": (now - timedelta(minutes=121)).isoformat()}
    sell = {"side": "SELL", "ts": now.isoformat()}
    assert dt.cooled(fresh, now, 120) is False
    assert dt.cooled(aged, now, 120) is True
    assert dt.cooled(sell, now, 120) is True
    assert dt.cooled({"side": "BUY", "ts": "junk"}, now, 120) is False


def test_cooling_first_seen_beats_backdated_ts():
    now = datetime(2026, 8, 7, 13, 0)
    backdated = {"side": "BUY", "ts": (now - timedelta(days=3)).isoformat(),
                 "first_seen": (now - timedelta(minutes=30)).isoformat()}
    assert dt.cooled(backdated, now, 120) is False
    aged = {"side": "BUY", "ts": (now - timedelta(days=3)).isoformat(),
            "first_seen": (now - timedelta(minutes=121)).isoformat()}
    assert dt.cooled(aged, now, 120) is True


def test_expired():
    assert dt.expired({"expires": "2026-08-06"}, date(2026, 8, 7)) is True
    assert dt.expired({"expires": "2026-08-07"}, date(2026, 8, 7)) is False
    assert dt.expired({"expires": "junk"}, date(2026, 8, 7)) is True


def test_rsi2_above_fires_only_past_the_threshold():
    """The mean-reversion exit band. Default 70, matching decisions_service._check_rsi2_above."""
    assert dt.evaluate({"kind": "rsi2_above"}, last=None, prev_close=None, rsi=71.2)[0] is True
    assert dt.evaluate({"kind": "rsi2_above"}, last=None, prev_close=None, rsi=70.0)[0] is False
    assert dt.evaluate({"kind": "rsi2_above"}, last=None, prev_close=None, rsi=61.4)[0] is False


def test_rsi2_above_honours_a_custom_threshold():
    t = {"kind": "rsi2_above", "threshold": 60}
    assert dt.evaluate(t, last=None, prev_close=None, rsi=61.4)[0] is True
    assert dt.evaluate(t, last=None, prev_close=None, rsi=59.9)[0] is False


def test_rsi2_above_fails_closed_without_an_rsi():
    """No RSI (stale bar, short history, broker error) must never fire a real SELL."""
    ok, why = dt.evaluate({"kind": "rsi2_above"}, last=None, prev_close=None, rsi=None)
    assert ok is False and "rsi" in why.lower()
    # the kwarg is optional, so pre-existing callers keep working and still fail closed
    assert dt.evaluate({"kind": "rsi2_above"}, last=305.0, prev_close=304.0)[0] is False


def test_rsi2_above_rejects_an_unparseable_threshold():
    t = {"kind": "rsi2_above", "threshold": "seventy"}
    assert dt.evaluate(t, last=None, prev_close=None, rsi=99.0)[0] is False


# ---------------------------------------------------------------- rsi2 two-phase (2026-08-18)
# Post-close is the only time fresh RSI(2) exists, and the only time MARKET is refused (417,
# live 2026-08-18). So a fresh-RSI pass CONFIRMS; a later core-hours run EXECUTES on it.

TRIG = {"kind": "rsi2_above", "threshold": 70}
T_0935 = datetime(2026, 8, 19, 9, 35).time()
T_1815 = datetime(2026, 8, 18, 18, 15).time()


def test_phase_confirms_on_fresh_rsi_above_threshold():
    action, why = dt.rsi2_phase(TRIG, rsi=90.5, confirmed_on=None,
                                today=date(2026, 8, 18), now_time=T_1815)
    assert action == "confirm" and "90.5" in why


def test_phase_denies_on_fresh_rsi_below_threshold():
    action, why = dt.rsi2_phase(TRIG, rsi=51.8, confirmed_on="2026-08-17",
                                today=date(2026, 8, 18), now_time=T_1815)
    assert action == "deny"


def test_phase_executes_next_day_core_hours_on_confirmation():
    action, why = dt.rsi2_phase(TRIG, rsi=None, confirmed_on="2026-08-18",
                                today=date(2026, 8, 19), now_time=T_0935)
    assert action == "execute"


def test_phase_never_executes_same_day_as_confirmation():
    # the 18:15 retry run must re-confirm, not fire a MARKET into extended hours
    action, why = dt.rsi2_phase(TRIG, rsi=None, confirmed_on="2026-08-18",
                                today=date(2026, 8, 18), now_time=T_1815)
    assert action == "deny"


def test_phase_never_executes_outside_core_hours():
    action, why = dt.rsi2_phase(TRIG, rsi=None, confirmed_on="2026-08-18",
                                today=date(2026, 8, 19), now_time=T_1815)
    assert action == "deny"


def test_phase_confirmation_spans_a_weekend_but_goes_stale_after_3_days():
    fri_to_mon = dt.rsi2_phase(TRIG, rsi=None, confirmed_on="2026-08-14",
                               today=date(2026, 8, 17), now_time=T_0935)
    assert fri_to_mon[0] == "execute"
    stale = dt.rsi2_phase(TRIG, rsi=None, confirmed_on="2026-08-14",
                          today=date(2026, 8, 18), now_time=T_0935)
    assert stale[0] == "deny"


def test_phase_fails_closed_on_missing_or_junk_inputs():
    assert dt.rsi2_phase(TRIG, rsi=None, confirmed_on=None,
                         today=date(2026, 8, 19), now_time=T_0935)[0] == "deny"
    assert dt.rsi2_phase(TRIG, rsi=None, confirmed_on="junk",
                         today=date(2026, 8, 19), now_time=T_0935)[0] == "deny"
    assert dt.rsi2_phase({"kind": "rsi2_above", "threshold": "junk"}, rsi=90.0,
                         confirmed_on=None, today=date(2026, 8, 19), now_time=T_0935)[0] == "deny"
