from webull_api import action_log


def test_load_empty_when_no_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path / "nope"))
    assert action_log.load() == []


def test_append_then_load_dedups_by_id(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    n = action_log.append([{"id": "a", "why": "one"}, {"id": "b", "why": "two"}])
    assert n == 2
    n2 = action_log.append([{"id": "a", "why": "dup"}, {"id": "c", "why": "three"}])
    assert n2 == 1  # only c is new
    rows = action_log.load()
    assert [r["id"] for r in rows] == ["a", "b", "c"]
    assert next(r for r in rows if r["id"] == "a")["why"] == "one"  # first wins
