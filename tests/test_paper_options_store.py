from webull_web import paper_options_store as store


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    assert store.load() is None
    store.save({"account_id": "options", "cash": 5000.0})
    assert store.load()["cash"] == 5000.0


def test_load_quarantines_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    (tmp_path / "options.json").write_text("{not json", encoding="utf-8")
    assert store.load() is None
    assert (tmp_path / "options.json.corrupt").exists()
