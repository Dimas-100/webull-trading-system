"""The Lab's bar source and lookback come from the environment (spec 2026-09-07 tiingo depth §3.4):
default = Webull + the gate's own lookback; tiingo = the offline adapter; the lookback only goes up;
GateAConfig defaults are never mutated."""
from types import SimpleNamespace

from webull_api.lab.schema import DEFAULT_GATE_A_CONFIG, StrategyRecord, WalkForwardReport
from webull_api.strategy.schema import Strategy
from webull_web import lab_service as svc
from webull_web import lab_store


def test_default_source_is_webull_and_default_lookback(monkeypatch):
    monkeypatch.delenv("WEBULL_LAB_BAR_SOURCE", raising=False)
    monkeypatch.delenv("WEBULL_LAB_LOOKBACK_BARS", raising=False)
    assert svc._bar_source() == "webull"
    assert svc._lookback_bars() == DEFAULT_GATE_A_CONFIG.min_lookback_bars
    cfg = svc.gate_a_config()
    assert cfg == DEFAULT_GATE_A_CONFIG


def test_tiingo_source_uses_the_adapter(monkeypatch):
    monkeypatch.setenv("WEBULL_LAB_BAR_SOURCE", "tiingo")
    called = {}

    def fake_factory(read=None):
        def get_bars(symbol, timespan="D", count="1200", **k):
            called.update({"symbol": symbol, "timespan": timespan, "count": count})
            return [{"time": "2026-09-04", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        return get_bars
    monkeypatch.setattr(svc.tiingo_bars, "make_get_bars", fake_factory)
    monkeypatch.setattr(svc.market_data, "get_bars", lambda *a, **k: (_ for _ in ()).throw(AssertionError("webull used")))
    out = svc._get_bars("SPY", "1D", 5000)
    assert called == {"symbol": "SPY", "timespan": "D", "count": "5000"}
    assert out[0]["time"] == "2026-09-04"


def test_unknown_source_falls_back_to_webull(monkeypatch):
    monkeypatch.setenv("WEBULL_LAB_BAR_SOURCE", "yahoo")
    assert svc._bar_source() == "webull"


def test_lookback_env_only_raises_depth_and_never_touches_the_defaults(monkeypatch):
    monkeypatch.setenv("WEBULL_LAB_LOOKBACK_BARS", "5000")
    cfg = svc.gate_a_config()
    assert cfg.min_lookback_bars == 5000
    assert DEFAULT_GATE_A_CONFIG.min_lookback_bars == 750               # the constant is untouched
    assert cfg.dsr_min == DEFAULT_GATE_A_CONFIG.dsr_min                  # every other field carried over
    monkeypatch.setenv("WEBULL_LAB_LOOKBACK_BARS", "100")
    assert svc._lookback_bars() == DEFAULT_GATE_A_CONFIG.min_lookback_bars
    monkeypatch.setenv("WEBULL_LAB_LOOKBACK_BARS", "lots")
    assert svc._lookback_bars() == DEFAULT_GATE_A_CONFIG.min_lookback_bars


def test_regime_now_honors_tiingo_bar_source(monkeypatch):
    """C2: regime_now()'s DEFAULT get_bars must route through _get_bars (webull vs tiingo per
    WEBULL_LAB_BAR_SOURCE), not stay hardwired to market_data.get_bars. An explicitly passed
    get_bars (the other test above) keeps the old direct-call behavior."""
    import datetime as dt

    monkeypatch.setenv("WEBULL_LAB_BAR_SOURCE", "tiingo")
    monkeypatch.setattr(svc.market_data, "get_bars",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("webull touched")))

    def fake_factory(read=None):
        def get_bars(symbol, timespan="D", count="1200", **k):
            d = dt.date(2025, 1, 1)
            out = []
            price = 100.0
            for _ in range(300):
                price += 0.05
                out.append({"time": d.isoformat(), "open": price, "high": price + 1,
                            "low": price - 1, "close": price, "volume": 1})
                d += dt.timedelta(days=1)
            return out
        return get_bars
    monkeypatch.setattr(svc.tiingo_bars, "make_get_bars", fake_factory)
    tag = svc.regime_now()
    assert tag.trend in ("up", "down", "side") and tag.vol in ("low", "high")


def test_status_reports_source_and_lookback(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    monkeypatch.setenv("WEBULL_LAB_BAR_SOURCE", "tiingo")
    monkeypatch.setenv("WEBULL_LAB_LOOKBACK_BARS", "5000")
    st = svc.lab_status()
    assert st["bar_source"] == "tiingo" and st["lookback_bars"] == 5000


def _strat(name="s1", symbol="AAPL"):
    return Strategy(name=name, symbol=symbol,
                    entry={"type": "sma_cross", "fast": 20, "slow": 50, "direction": "above"})


def _ready_record(rec_id="r1"):
    """A StrategyRecord that passes submit()'s Gate-A re-check."""
    return StrategyRecord(id=rec_id, strategy=_strat(name=rec_id), fingerprint=f"fp-{rec_id}",
                          canon_bucket="cb", archetype="trend_follow", cohort="trend_follow:up/low",
                          created_at_iso="2026-06-29T00:00:00Z", as_of="2026-06-29",
                          status="proposed", gate_a=WalkForwardReport(passed=True))


def test_every_lab_fetch_honors_the_lookback_env(monkeypatch, tmp_path):
    """A same-day re-trigger (advance_all) and the interactive paths (propose/submit/regime_now)
    must judge at the same depth as the fresh cycle (Fix round 1, Finding 1).

    Adapted from the reviewer's sketch: `regime_now()`'s own default `get_bars=market_data.get_bars`
    is bound to the real function object at MODULE IMPORT time (a Python default argument is
    evaluated once, not re-looked-up per call), so neither patching `svc._get_bars` (as the sketch
    did) nor patching `svc.market_data.get_bars` after the fact can intercept it — the first cannot
    observe regime_now's count at all, and the second still dispatches to the original bound
    function, which was proven live during this fix (it reached the real Webull SDK and got a
    417 for `count=5000` > the API's 1200 cap). The sketch's two try/except blocks around calls
    that either always raise or always hit the live network both swallow their exception before
    `seen` is ever populated, so the sketch's assertion would fail (`seen` empty) regardless of
    whether the fix is correct. This version instead calls `regime_now(get_bars=<fake>)` directly
    — the exact code path (`count=str(_lookback_bars())`) executes identically whether `get_bars`
    arrives via the default or the explicit keyword, so this still proves the depth computation
    itself honors the env, without fighting the early-bound default. advance_all/propose are
    checked by patching `orch.advance` / `orch.ingest_proposals`, capturing the config lab_service
    computed and passed down; submit() is checked via `_get_bars`, whose call site there takes an
    explicit `count=`."""
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    monkeypatch.setenv("WEBULL_LAB_LOOKBACK_BARS", "5000")

    # regime_now(): the count it asks for must be the raised depth regardless of which get_bars runs.
    seen_regime = []

    def fake_get_bars(symbol, timespan, count="1200", **k):
        seen_regime.append(int(count))
        return [{"time": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

    svc.regime_now(get_bars=fake_get_bars)
    assert seen_regime == [5000], seen_regime

    # advance_all(): the gate_a it builds and hands to orch.advance must carry the raised depth.
    captured_gate_a = {}

    def fake_advance(*a, **k):
        captured_gate_a["gate_a"] = k["gate_a"]
        return SimpleNamespace(promoted=[], killed=[])

    monkeypatch.setattr(svc.orch, "advance", fake_advance)
    svc.advance_all()
    assert captured_gate_a["gate_a"].min_lookback_bars == 5000

    # propose(): the gate_cfg it builds and hands to orch.ingest_proposals must too.
    captured_gate_cfg = {}

    def fake_ingest_proposals(*a, **k):
        captured_gate_cfg["gate_cfg"] = k["gate_cfg"]
        return SimpleNamespace(m_after=0, gate_a_failed=[], model_dump=lambda: {})

    monkeypatch.setattr(svc.orch, "ingest_proposals", fake_ingest_proposals)
    svc.propose([], notes="")
    assert captured_gate_cfg["gate_cfg"].min_lookback_bars == 5000

    # submit(): the per-basket-symbol fetch must request the raised depth explicitly.
    seen_counts = []
    monkeypatch.setattr(svc, "_get_bars",
                        lambda symbol, timeframe="1D", count=750: seen_counts.append(int(count)) or [])
    monkeypatch.setattr(svc.trial_engine, "open_trial", lambda *a, **k: object())
    monkeypatch.setattr(lab_store, "save_trial", lambda state: None)
    lab_store.save_library([_ready_record("r1")])
    out = svc.submit(["r1"])
    assert out["submitted"] == ["r1"]
    assert seen_counts and all(c == 5000 for c in seen_counts), seen_counts
