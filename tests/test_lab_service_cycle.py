"""Impure one-cycle wrapper: lock, same-day guard, cold-start refusal, persist order, shelf
promotion. The pure cycle.run_cycle is mocked; this exercises only the I/O composition."""
import datetime as dt

import pytest

from webull_web import lab_service, lab_store
from webull_api.lab.schema import (
    AdvanceResult, BatchDecision, CycleReport, PaperProvenRecord, ProvingState, RegimeTag,
    StagnationState, StrategyRecord, WalkForwardReport,
)
from webull_api.strategy.schema import Strategy
from webull_api import market_data


def test_memo_get_bars_records_degraded_symbol_and_propagates(monkeypatch):
    # #3 observability: a symbol whose fetch raises is RECORDED in `failed` (so run_cycle can note it
    # on report.errors) while the error still propagates for the pure engine to tolerate; an
    # entitlement error propagates but is NOT recorded as a mere drop.
    def raw(sym, tf, n):
        if sym == "BAD":
            raise RuntimeError("transient")
        if sym == "GONE":
            raise market_data.MarketDataNotEntitledError("no entitlement")
        return [{"time": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    monkeypatch.setattr(lab_service, "_get_bars", raw)
    failed: set = set()
    getter = lab_service._memo_get_bars(failed)
    assert getter("OK", "1D", 5)                        # a good symbol works + caches
    with pytest.raises(RuntimeError):
        getter("BAD", "1D", 5)
    assert failed == {"BAD"}
    with pytest.raises(market_data.MarketDataNotEntitledError):
        getter("GONE", "1D", 5)
    assert failed == {"BAD"}                            # entitlement is NOT a mere drop


def _long_bars(n: int, *, end: str | None = None) -> list[dict]:
    """n ascending weekday-dated bars ending at `end` (default: today ET) — long enough (n >= the
    gate's default 750-bar lookback) to clear the C1 short-history shell check, so tests that
    aren't exercising that check don't trip it just because their fixture bars are short."""
    d = dt.date.fromisoformat(end) if end else dt.date.fromisoformat(lab_service._today_et())
    out: list[dict] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append({"time": d.isoformat(), "open": 10, "high": 11, "low": 9,
                       "close": 10.5, "volume": 1})
        d -= dt.timedelta(days=1)
    out.reverse()
    return out


@pytest.fixture(autouse=True)
def _hermetic(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    # _best_effort_regime() calls regime_now() which would hit the live API — stub it.
    monkeypatch.setattr(lab_service, "regime_now", lambda *a, **k: RegimeTag(trend="up", vol="low"))
    # The bar preflight (2026-08-16) fetches the basket before every cycle — stub it with clean
    # recent bars so run_cycle tests stay network-free (individual tests override to test refusal).
    clean = {"SPY": _long_bars(800)}
    monkeypatch.setattr(lab_service.orch, "fetch_basket_bars", lambda *a, **k: (clean, []))


def _strat(name="s1", symbol="AAPL"):
    return Strategy(name=name, symbol=symbol,
                    entry={"type": "sma_cross", "fast": 20, "slow": 50, "direction": "above"})


def _rec(rec_id="r1", status="proving", **kw):
    base = dict(id=rec_id, strategy=_strat(name=rec_id), fingerprint=f"fp-{rec_id}", canon_bucket="cb",
                archetype="trend_follow", cohort="trend_follow:up/low",
                created_at_iso="2026-06-29T00:00:00Z", as_of="2026-06-29", status=status)
    base.update(kw)
    return StrategyRecord(**base)


def _state(trial_id="g1"):
    return ProvingState(trial_id=trial_id, strategy=_strat(name=trial_id), basket=["AAPL"],
                        inception_et_date="2026-06-01", started_at_iso="2026-06-01T00:00:00Z")


def _proven():
    return PaperProvenRecord(graduated_at="2026-06-29T00:00:00Z", strategy=_strat(),
                             gate_a=WalkForwardReport(), gate_b={}, assumptions={}, objective_met={})


def _report(m_before=3, m_after=5, errors=None):
    return CycleReport(
        cycle_seq=0, cycle_date="2026-06-30", ran_at_iso="2026-06-30T22:00:00Z",
        regime=RegimeTag(trend="up", vol="low"),
        decision=BatchDecision(active_trials=0, admitted_waiting=0, open_slots=5, pass_rate_est=0.25,
                               mutation_n=4, explore_n=2, max_admit=5),
        m_before=m_before, m_after=m_after, stagnation=StagnationState(),
        accepted=["fp1", "fp2"], new_proving=["fp1"], errors=errors or [])


def test_run_cycle_refuses_when_lock_present(monkeypatch):
    lab_store.write_meta({**lab_store.read_meta(), "M": 0})
    lab_store.save_library([_rec("a")])
    lock = lab_store.lab_dir() / ".cycle.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("{}", encoding="utf-8")
    called = {"n": 0}
    monkeypatch.setattr(lab_service.cycle, "run_cycle",
                        lambda *a, **k: (called.__setitem__("n", 1), ([], None))[-1])
    rep = lab_service.run_cycle()
    assert rep.no_op and "cycle already running" in rep.errors[0]
    assert called["n"] == 0


def _mock_report(m_before=68, m_after=132):
    return CycleReport(cycle_seq=0, cycle_date=lab_service._today_et(),
                       ran_at_iso="2026-07-01T00:00:00Z", regime=RegimeTag(trend="up", vol="low"),
                       decision=BatchDecision(active_trials=0, admitted_waiting=0, open_slots=25,
                                              pass_rate_est=0.25, mutation_n=0, explore_n=0, max_admit=0),
                       m_before=m_before, m_after=m_after, stagnation=StagnationState())


def test_run_cycle_refuses_cold_start_when_trials_orphaned(monkeypatch):
    # genuine lost data: a trial file exists but the library is empty (its record was lost) -> refuse
    # so the open trial is not orphaned.
    lab_store.write_meta({**lab_store.read_meta(), "M": 9})
    lab_store.save_library([])
    lab_store.save_trial(_state("orphan"))              # trials/ non-empty while library is empty
    called = {"n": 0}
    monkeypatch.setattr(lab_service.cycle, "run_cycle",
                        lambda *a, **k: (called.__setitem__("n", 1), ([], None))[-1])
    rep = lab_service.run_cycle()
    assert rep.no_op and "refusing to cold-start" in rep.errors[0]
    assert called["n"] == 0 and rep.m_after == 9


def test_run_cycle_proceeds_when_library_empty_and_no_trials(monkeypatch):
    # the real onboarding state: interactive proposing bumped M (nothing has passed Gate-A yet) with
    # NO trials -> the autopilot must PROCEED and keep searching, not refuse (M is preserved).
    lab_store.write_meta({**lab_store.read_meta(), "M": 68})
    lab_store.save_library([])                          # empty library, no trials/
    called = {"n": 0}

    def fake_cycle(*a, **k):
        called["n"] = 1
        return ([], _mock_report())
    monkeypatch.setattr(lab_service.cycle, "run_cycle", fake_cycle)
    rep = lab_service.run_cycle()
    assert called["n"] == 1                             # the cycle ran — it was NOT refused
    assert not any("refusing" in e for e in rep.errors)
    assert rep.m_after == 132


def test_status_view_composes_snapshot():
    today = lab_service._today_et()
    lab_store.write_meta({**lab_store.read_meta(), "M": 132, "cycle_seq": 1, "last_cycle_date": today})
    lab_store.append_cycle(_mock_report(m_before=68, m_after=132))
    lab_store.save_library([_rec("r1", status="proving")])
    v = lab_service.status_view(num_cycles=5)
    assert v["M"] == 132 and v["cycles_run"] == 1 and v["ran_today"] is True
    assert v["stale"] is False and v["days_since_last_cycle"] == 0
    assert len(v["recent_cycles"]) == 1 and v["recent_cycles"][0]["m_after"] == 132
    assert v["library"]["total"] == 1 and v["library"]["by_status"] == {"proving": 1}
    assert v["library"]["active"][0]["name"] == "r1"
    assert v["proven_shelf"]["total"] == 0


def test_run_cycle_same_day_advance_only(monkeypatch):
    today = lab_service._today_et()
    lab_store.write_meta({**lab_store.read_meta(), "M": 4, "last_cycle_date": today, "cycle_seq": 2})
    lab_store.save_library([_rec("a")])
    called = {"n": 0}
    monkeypatch.setattr(lab_service.cycle, "run_cycle",
                        lambda *a, **k: (called.__setitem__("n", 1), ([], None))[-1])
    monkeypatch.setattr(lab_service, "advance_all",
                        lambda symbols="", max_trials=0: AdvanceResult(advanced=["a"], no_op=False))
    rep = lab_service.run_cycle()
    assert rep.no_op and rep.advanced == ["a"] and rep.m_before == 4 and rep.m_after == 4
    assert called["n"] == 0


def test_run_cycle_persists_meta_before_library_and_bumps_M(monkeypatch):
    lab_store.write_meta({**lab_store.read_meta(), "M": 3, "cycle_seq": 7})
    lab_store.save_library([_rec("a")])
    report = _report(m_before=3, m_after=5)
    monkeypatch.setattr(lab_service.cycle, "run_cycle", lambda *a, **k: ([], report))
    order = []
    real_meta, real_lib = lab_store.write_meta, lab_store.save_library
    monkeypatch.setattr(lab_store, "write_meta", lambda m: (order.append(("meta", m.get("M"))), real_meta(m))[-1])
    monkeypatch.setattr(lab_store, "save_library", lambda l: (order.append("library"), real_lib(l))[-1])
    out = lab_service.run_cycle()
    assert out.m_after == 5 and out.cycle_seq == 8
    assert order == [("meta", 5), "library"]                       # meta(M↑) FIRST, then library
    saved = lab_store.read_meta()
    assert saved["M"] == 5 and saved["last_cycle_date"] == lab_service._today_et()
    assert saved["cycle_seq"] == 8 and saved["last_cycle"]["m_after"] == 5


def test_cycles_view_returns_last_and_history(monkeypatch):
    lab_store.write_meta({**lab_store.read_meta(), "M": 3, "cycle_seq": 0})
    lab_store.save_library([_rec("a")])
    monkeypatch.setattr(lab_service.cycle, "run_cycle", lambda *a, **k: ([], _report(3, 5)))
    lab_service.run_cycle()
    view = lab_service.cycles_view()
    assert view["last"]["m_after"] == 5
    assert len(view["history"]) == 1 and view["history"][0]["m_after"] == 5


def test_run_cycle_promotes_confirmed_to_shelf_as_autopilot(monkeypatch):
    lab_store.write_meta({**lab_store.read_meta(), "M": 3})
    lab_store.save_library([_rec("a")])
    rec = _rec("g1", status="confirmed_proven", trial_id="g1")
    lab_store.save_trial(_state("g1"))
    monkeypatch.setattr(lab_service.cycle, "run_cycle", lambda *a, **k: ([rec], _report(3, 4)))
    captured = {}
    monkeypatch.setattr(lab_service.verdict_engine, "build_proven_record",
                        lambda r, s, **k: (captured.update(k), _proven())[-1])
    lab_service.run_cycle()
    assert captured.get("promoted_by") == "autopilot"
    assert len(lab_store.load_proven()) == 1


def test_lab_status_folds_last_cycle_and_stagnation(monkeypatch):
    lab_store.write_meta({**lab_store.read_meta(), "M": 6, "cycle_seq": 9,
                          "last_cycle": {"cycle_seq": 9}, "stagnation": {"overall_dry_streak": 2}})
    lab_store.save_library([_rec("a", "proving")])
    monkeypatch.setattr(lab_service, "_get_bars", lambda *a, **k: [])
    out = lab_service.lab_status()
    assert out["cycle_seq"] == 9 and out["last_cycle"] == {"cycle_seq": 9}
    assert out["stagnation"] == {"overall_dry_streak": 2}
    # ported 2026-09-28 from the archived GET /api/lab/status route test: the counts and M
    assert out["counts"] == {"proving": 1} and out["M"] == 6 and "summary" in out


def test_run_cycle_stamps_elapsed_ms(monkeypatch):
    # Telemetry (2026-08-15): elapsed_ms was hardcoded 0 (the pure core is time-free by design),
    # so the shell times the pure cycle and stamps the report before persisting it.
    lab_store.write_meta({**lab_store.read_meta(), "M": 3})
    monkeypatch.setattr(lab_service.cycle, "run_cycle", lambda *a, **k: ([], _mock_report()))
    ticks = iter([100.0, 100.25])
    monkeypatch.setattr(lab_service, "_monotonic", lambda: next(ticks))
    rep = lab_service.run_cycle()
    assert rep.elapsed_ms == 250
    assert lab_store.read_cycles(1)[0]["elapsed_ms"] == 250


def test_run_cycle_refuses_on_bar_preflight_failure(monkeypatch):
    # Suspect bars (the reversed-bars class of bug) must REFUSE the cycle: no screening,
    # no M consumed — a loud error beats 31 cycles of silently corrupted verdicts.
    lab_store.write_meta({**lab_store.read_meta(), "M": 7})
    called = {"n": 0}

    def fake_cycle(*a, **k):
        called["n"] = 1
        return ([], _mock_report())
    monkeypatch.setattr(lab_service.cycle, "run_cycle", fake_cycle)
    reversed_bars = {"SPY": [
        {"time": "2026-08-14", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1},
        {"time": "2026-08-13", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1}]}
    monkeypatch.setattr(lab_service.orch, "fetch_basket_bars",
                        lambda *a, **k: (reversed_bars, []))
    rep = lab_service.run_cycle()
    assert called["n"] == 0                                   # the cycle never ran
    assert any("preflight" in e for e in rep.errors)
    assert lab_store.read_meta()["M"] == 7                    # no M consumed


def test_run_cycle_refuses_when_lookback_exceeds_a_symbols_history(monkeypatch):
    # C1 (2026-09-07 tiingo depth fix wave): a raised WEBULL_LAB_LOOKBACK_BARS that exceeds a
    # basket symbol's actual history must refuse the cycle — Gate A would otherwise fail the
    # WHOLE candidate on that one short symbol for every strategy, silently.
    lab_store.write_meta({**lab_store.read_meta(), "M": 12})
    monkeypatch.setenv("WEBULL_LAB_LOOKBACK_BARS", "5000")
    monkeypatch.setattr(lab_service.cycle, "run_cycle",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cycle ran despite short history")))
    today = lab_service._today_et()
    short_bars = {"LIN": [{"time": d, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1}
                          for d in ("2026-08-12", "2026-08-13", today)]}
    monkeypatch.setattr(lab_service.orch, "fetch_basket_bars", lambda *a, **k: (short_bars, []))
    rep = lab_service.run_cycle()
    assert any("LIN" in e and "lookback" in e for e in rep.errors)
    assert rep.m_before == 12 and rep.m_after == 12
    assert lab_store.read_meta()["M"] == 12


def test_run_cycle_generates_nothing_on_a_no_bar_day(monkeypatch):
    # A weekend / NYSE-holiday basket is recent enough for check_bars but carries no bar dated
    # today: the cycle must NOT generate candidates or move M (Labor Day 2026-09-07 cost 64 trials).
    lab_store.write_meta({**lab_store.read_meta(), "M": 960})
    lab_store.save_library([])
    # `end` is derived from today (the most recent PRIOR weekday, always within the preflight's
    # 5-calendar-day staleness limit) so the basket stays "recent but not today" forever. A pinned
    # date silently expires: "2026-09-04" was exactly 5 days stale on 2026-09-09 and tripped
    # check_bars' refusal on 2026-09-10, turning the quiet-skip assertion below into a failure.
    prior = dt.date.fromisoformat(lab_service._today_et()) - dt.timedelta(days=1)
    while prior.weekday() >= 5:
        prior -= dt.timedelta(days=1)
    holiday = {"SPY": _long_bars(800, end=prior.isoformat())}   # today is later, no bar for it
    monkeypatch.setattr(lab_service.orch, "fetch_basket_bars", lambda *a, **k: (holiday, []))
    monkeypatch.setattr(lab_service.cycle, "run_cycle",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("generated on a no-bar day")))
    rep = lab_service.run_cycle()
    assert rep.no_op is True and rep.no_bar is True
    assert rep.generated == 0 and rep.m_before == 960 and rep.m_after == 960
    assert rep.errors == []                                # a quiet skip, not a refusal
    assert lab_store.read_meta()["M"] == 960
