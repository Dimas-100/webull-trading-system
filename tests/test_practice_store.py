from webull_web import practice_store as store


def test_save_list_get_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("PRACTICE_DIR", str(tmp_path))
    store.save_session("abc123", {"status": "in_progress", "symbol": "AAPL"})
    assert store.get_session("abc123")["symbol"] == "AAPL"
    assert [s["id"] for s in store.list_sessions()] == ["abc123"]
    store.delete_session("abc123")
    assert store.list_sessions() == []


def test_missing_raises_and_garbage_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("PRACTICE_DIR", str(tmp_path))
    (tmp_path / "broken.json").write_text("{nope", encoding="utf-8")
    import pytest
    with pytest.raises(FileNotFoundError):
        store.get_session("missing")
    assert store.list_sessions() == []
