"""scripts/sizing_study.py: the reports it writes and -- the load-bearing one -- the confirm guard.

The confirm window is the study's single holdout opening (§6), so the script must refuse to touch it
unless a tagged develop report already names a selected cell, and must then run that cell only.
"""
from __future__ import annotations

import json

from scripts import sizing_study as ss
from tests.sizing_study.conftest import sig
from webull_api.sizing_study import study
from webull_api.sizing_study.loaders import IBS, RSI2

DEV = ("2020-01-01", "2020-01-31")
CON = ("2020-02-01", "2020-02-29")
DATES = ([f"2020-01-{d:02d}" for d in range(2, 32)] + [f"2020-02-{d:02d}" for d in range(3, 29)])


def _rows(symbol):
    step = 1.0 if symbol == "SPY" else 0.5
    return [{"date": d, "adj_close": 100.0 + i * step} for i, d in enumerate(DATES)]


def _signals():
    out = []
    for i, d in enumerate(DATES[:-3]):
        out.append(sig(RSI2 if i % 2 == 0 else IBS, "AAA" if i % 2 == 0 else "BBB",
                       d, DATES[i + 2], 1.0 if i % 3 else -0.5))
    return out


def _patch(monkeypatch):
    monkeypatch.setattr(study, "WINDOWS", {"develop": DEV, "confirm": CON})
    monkeypatch.setattr(study, "load_signals", lambda *a, **k: _signals())
    monkeypatch.setattr(ss.store, "read", _rows)


def _develop(tmp_path, monkeypatch, tag="size1"):
    _patch(monkeypatch)
    assert ss.run(["--window", "develop", "--tag", tag, "--out-dir", str(tmp_path),
                   "--today", "2026-09-10"]) == 0
    return json.loads((tmp_path / "2026-09-10-sizing-study-develop.json").read_text(encoding="utf-8"))


def test_develop_report_runs_every_cell_and_names_a_selection(tmp_path, monkeypatch):
    payload = _develop(tmp_path, monkeypatch)
    assert payload["window"] == "develop" and payload["tag"] == "size1"
    assert set(payload["cells"]) == {"R8", "R6", "R4", "S8", "S6", "S4", "I4", "B0", "SPY"}
    sel = payload["selection"]
    assert sel["selected"] in (None, "R8", "R6", "R4", "S8", "S6", "S4", "I4")
    assert len(sel["ranking"]) == 7 and sel["dd_budget_pct"] == 30.0
    for cell in payload["cells"].values():
        assert "cagr_pct" in cell and "calmar" in cell and "monthly_equity" in cell
        assert len(cell["monthly_equity"]) <= cell["sessions"]        # monthly points, not the daily series
    md = (tmp_path / "2026-09-10-sizing-study-develop.md").read_text(encoding="utf-8")
    assert "| R8 |" in md and "Selection" in md and "Per year" in md


def test_confirm_refuses_with_no_develop_report_on_record(tmp_path, monkeypatch, capsys):
    _patch(monkeypatch)
    assert ss.run(["--window", "confirm", "--tag", "size1", "--out-dir", str(tmp_path)]) == 2
    assert "no develop report on record" in capsys.readouterr().out
    assert not list(tmp_path.glob("*confirm*"))


def test_confirm_refuses_when_the_develop_report_names_no_selected_cell(tmp_path, monkeypatch, capsys):
    payload = _develop(tmp_path, monkeypatch)
    payload["selection"]["selected"] = None
    (tmp_path / "2026-09-10-sizing-study-develop.json").write_text(json.dumps(payload), encoding="utf-8")
    assert ss.run(["--window", "confirm", "--tag", "size1", "--out-dir", str(tmp_path)]) == 2
    assert "names no selected cell" in capsys.readouterr().out
    assert not list(tmp_path.glob("*confirm*"))


def test_confirm_refuses_on_a_tag_that_does_not_match_the_develop_report(tmp_path, monkeypatch, capsys):
    _develop(tmp_path, monkeypatch, tag="size1")
    assert ss.run(["--window", "confirm", "--tag", "other", "--out-dir", str(tmp_path)]) == 2
    assert "not 'other'" in capsys.readouterr().out


def test_confirm_opens_for_the_selected_cell_only_plus_the_references(tmp_path, monkeypatch):
    payload = _develop(tmp_path, monkeypatch)
    payload["selection"]["selected"] = "R6"
    (tmp_path / "2026-09-10-sizing-study-develop.json").write_text(json.dumps(payload), encoding="utf-8")
    assert ss.run(["--window", "confirm", "--tag", "size1", "--out-dir", str(tmp_path),
                   "--today", "2026-09-20"]) == 0
    out = json.loads((tmp_path / "2026-09-20-sizing-study-confirm.json").read_text(encoding="utf-8"))
    assert set(out["cells"]) == {"R6", "B0", "SPY"} and out["window"] == "confirm"
    assert out["verdict"]["verdict"] in ("PASS", "FAIL")
    assert "selection" not in out                       # no re-selection on the confirm window
