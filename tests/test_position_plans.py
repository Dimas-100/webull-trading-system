import importlib


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("POSITION_PLANS_DIR", str(tmp_path / "pp"))
    from webull_web import position_plans
    return importlib.reload(position_plans)


def test_save_get_roundtrip(tmp_path, monkeypatch):
    pp = _store(tmp_path, monkeypatch)
    saved = pp.save_plan({"symbol": "amd", "structural_stop": 9.5, "target": 12.0,
                          "entry": 10.2, "book": 400, "source": "swing_screen"})
    assert saved["symbol"] == "AMD"
    got = pp.get_plan("AMD")
    assert got["structural_stop"] == 9.5 and got["target"] == 12.0


def test_get_missing_returns_none(tmp_path, monkeypatch):
    pp = _store(tmp_path, monkeypatch)
    assert pp.get_plan("NOPE") is None


def test_all_plans_keyed_by_symbol_and_latest_write_wins(tmp_path, monkeypatch):
    pp = _store(tmp_path, monkeypatch)
    pp.save_plan({"symbol": "AMD", "structural_stop": 9.5})
    pp.save_plan({"symbol": "GE", "structural_stop": 20.0})
    pp.save_plan({"symbol": "AMD", "structural_stop": 9.9})  # overwrite
    plans = pp.all_plans()
    assert set(plans) == {"AMD", "GE"}
    assert plans["AMD"]["structural_stop"] == 9.9


def test_corrupt_file_is_skipped(tmp_path, monkeypatch):
    pp = _store(tmp_path, monkeypatch)
    pp.save_plan({"symbol": "AMD", "structural_stop": 9.5})
    from pathlib import Path
    (Path(tmp_path) / "pp" / "GE.json").write_text("{not json", encoding="utf-8")
    plans = pp.all_plans()
    assert "AMD" in plans and "GE" not in plans
