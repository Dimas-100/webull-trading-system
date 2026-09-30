import json
from webull_web import north_star_service as svc


def _seed_paper(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DIR", str(tmp_path / "paper"))
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "default.json").write_text(json.dumps(
        {"realized_pnl": 498.70, "positions": [{"symbol": "F", "qty": 1.0}]}), encoding="utf-8")
    (tmp_path / "paper" / "options.json").write_text(json.dumps(
        {"realized_pnl": -250.0, "positions": []}), encoding="utf-8")


def test_build_shape_and_pillars(tmp_path, monkeypatch):
    _seed_paper(tmp_path, monkeypatch)
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))          # empty -> 0 decisions
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "ap"))       # empty -> never placed
    monkeypatch.setenv("WEBULL_AUTOPILOT_ENABLED", "true")
    # Pin the cap: once any earlier test import runs load_dotenv(), the owner's .env value
    # leaks into from_env() and this assertion would track .env instead of the code path.
    monkeypatch.setenv("WEBULL_AUTOPILOT_MAX_NOTIONAL", "40")
    out = svc.build(net_liq=15.68)
    assert set(out) == {"lab", "paper", "real"}
    assert round(out["paper"]["combined_realized_pnl"], 2) == 248.70
    assert out["real"]["ever_placed"] is False
    assert out["real"]["safety_flag"] is True          # enabled but not cleared
    assert out["real"]["caps"]["max_notional"] == 40.0


def test_build_funded_unknown_without_net_liq(tmp_path, monkeypatch):
    _seed_paper(tmp_path, monkeypatch)
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "ap"))
    out = svc.build(net_liq=None)
    funded = next(i for i in out["real"]["checklist"] if i["key"] == "funded")
    assert funded["state"] == "unknown"


def test_build_degrades_when_paper_store_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DIR", str(tmp_path / "empty"))              # no files -> load() is None
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "ap"))
    out = svc.build(net_liq=None)
    # missing paper account is a normal zero-state, not a crash
    assert out["paper"]["combined_realized_pnl"] == 0.0
    assert "real" in out


def test_build_degrades_when_real_reader_raises(tmp_path, monkeypatch):
    _seed_paper(tmp_path, monkeypatch)
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    def boom():
        raise RuntimeError("autopilot config blew up")
    monkeypatch.setattr("webull_api.autopilot.config.AutopilotConfig.from_env", staticmethod(boom))
    out = svc.build(net_liq=15.68)
    assert out["real"]["chip"] == "unknown"                       # real pillar degraded...
    assert round(out["paper"]["combined_realized_pnl"], 2) == 248.70  # ...others intact
    assert "lab" in out


def test_build_degrades_when_lab_reader_raises(tmp_path, monkeypatch):
    _seed_paper(tmp_path, monkeypatch)
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "ap"))
    def boom():
        raise RuntimeError("lab state unreadable")
    monkeypatch.setattr("webull_web.lab_service.status_view", boom)
    out = svc.build(net_liq=None)
    assert out["lab"]["chip"] == "unknown"                        # lab pillar degraded...
    assert "real" in out and "paper" in out                      # ...others intact


def test_in_flight_excludes_confirmed_proven(monkeypatch):
    fake = {"proven_shelf": {"total": 3, "records": []},
            "library": {"by_status": {"proving": 2, "provisional_proven": 1, "confirmed_proven": 3}},
            "cycles_run": 1, "last_cycle_date": "2026-07-08", "stale": False}
    monkeypatch.setattr("webull_web.lab_service.status_view", lambda: fake)
    monkeypatch.setenv("PAPER_DIR", "nope"); monkeypatch.setenv("JOURNAL_DIR", "nope2")
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", "nope3")
    out = svc.build(net_liq=None)
    assert out["lab"]["in_flight"] == 3   # proving 2 + provisional 1, NOT confirmed_proven
    assert out["lab"]["proven"] == 3


def _t(sym, entry_at, exit_at, pnl, return_pct, fill_id=None):
    from webull_api.journal.schema import ClosedTrade
    return ClosedTrade(symbol=sym, source="paper", quantity=1, entry_price=100.0,
                       exit_price=100.0 + pnl, entry_at_iso=entry_at, exit_at_iso=exit_at,
                       holding_days=0.0, pnl=pnl, return_pct=return_pct, win=pnl > 0,
                       entry_fill_id=fill_id)


def _seed_rsi2_actions(tmp_path, monkeypatch, refs):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path / "activity"))
    (tmp_path / "activity").mkdir()
    rows = [json.dumps({"id": f"act-{r}", "ref": r, "source": "runner:rsi2", "kind": "trade"})
            for r in refs]
    (tmp_path / "activity" / "actions.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_paper_pillar_excludes_coordination_artifacts(tmp_path, monkeypatch):
    # Both rows are bar-eligible (post-fix, rsi2-attributed); the artifact still drops out.
    _seed_paper(tmp_path, monkeypatch)
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "ap"))
    _seed_rsi2_actions(tmp_path, monkeypatch, ["f1", "f2"])
    real_decision = _t("VOO", "2026-07-25T17:30:00-04:00", "2026-07-28T17:30:18-04:00",
                       -5.0, -0.5, fill_id="f1")
    artifact = _t("GOOG", "2026-07-28T17:30:02-04:00", "2026-07-28T17:30:18-04:00",
                  0.0, 0.0, fill_id="f2")
    monkeypatch.setattr("webull_api.journal.pairing.pair_fills",
                        lambda fills: ([real_decision, artifact], []))
    out = svc.build(net_liq=None)
    p = out["paper"]
    assert p["decisions"] == 1                       # the artifact is not a decision
    assert p["excluded_artifacts"] == 1              # ...and the exclusion is visible
    assert round(p["expectancy_pct"], 4) == -0.5     # size-independent expectancy threads through


def test_paper_pillar_enforces_proof_bar_read_scope(tmp_path, monkeypatch):
    # proof-bar-read-scope (ledger 2026-07-28), code-enforced: the bar counts ONLY post-fix
    # rsi2-equity rows; legacy equity, pre-fix rsi2, and the ENTIRE options sleeve are context.
    from webull_api.journal.schema import OptionTrade

    _seed_paper(tmp_path, monkeypatch)
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "ap"))
    _seed_rsi2_actions(tmp_path, monkeypatch, ["f1", "f2"])
    bar_row = _t("AAPL", "2026-08-01T17:30:00-04:00", "2026-08-05T17:30:00-04:00",
                 8.0, 2.0, fill_id="f1")
    pre_fix = _t("MSFT", "2026-07-20T17:30:00-04:00", "2026-07-23T17:30:00-04:00",
                 4.0, 1.0, fill_id="f2")
    legacy = _t("VOO", "2026-08-01T17:30:00-04:00", "2026-08-05T17:30:00-04:00", -3.0, -1.0)
    monkeypatch.setattr("webull_api.journal.pairing.pair_fills",
                        lambda fills: ([bar_row, pre_fix, legacy], []))
    opt = OptionTrade(id="o1", underlying="AAPL", strategy="SINGLE",
                      legs_desc="AAPL +300C 2026-08-21", quantity=1,
                      open_net_price=1.0, close_value=1.5,
                      opened_at_iso="2026-08-01T17:30:00-04:00",
                      closed_at_iso="2026-08-05T17:30:00-04:00",
                      pnl=50.0, win=True, return_pct=50.0, reason="closed")
    monkeypatch.setattr("webull_web.journal_store.load_option_trades", lambda: [opt])
    out = svc.build(net_liq=None)
    p = out["paper"]
    assert p["decisions"] == 1                       # ONLY the post-fix rsi2 row is the bar
    assert round(p["expectancy_pct"], 4) == 2.0      # options +50% no longer pollutes the read
    assert p["context"]["options"] == 1
    assert p["context"]["legacy_equity"] == 1
    assert p["context"]["pre_fix_rsi2"] == 1


def test_build_handles_dict_shaped_equity_positions(tmp_path, monkeypatch):
    # The real equity paper store keeps positions as a {symbol: {...}} DICT (not a list) with a
    # `quantity` key — regression for the paper pillar degrading to "unknown" on real data.
    monkeypatch.setenv("PAPER_DIR", str(tmp_path / "paper"))
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "default.json").write_text(json.dumps(
        {"realized_pnl": 498.70, "positions": {
            "F": {"symbol": "F", "quantity": 1.0, "avg_cost": 14.77},
            "AVUV": {"symbol": "AVUV", "quantity": 40.0, "avg_cost": 122.77}}}), encoding="utf-8")
    (tmp_path / "paper" / "options.json").write_text(json.dumps(
        {"realized_pnl": -250.0, "positions": []}), encoding="utf-8")
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "ap"))
    out = svc.build(net_liq=None)
    assert out["paper"]["chip"] == "active"                 # NOT "unknown"
    assert out["paper"]["open_positions"] == 2              # both dict-valued positions counted
    assert round(out["paper"]["combined_realized_pnl"], 2) == 248.70
