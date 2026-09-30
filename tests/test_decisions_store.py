"""Standing-decisions store: append-only event-sourced JSONL — latest row per id wins."""
import json

from webull_web import decisions_store


def _seed(tmp_path, monkeypatch, lines):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    with (tmp_path / "decisions.jsonl").open("w", encoding="utf-8") as fh:
        for l in lines:
            fh.write((l if isinstance(l, str) else json.dumps(l)) + "\n")


def _dec(id, ts, status="open", **kw):
    return {"kind": "decision", "id": id, "ts": ts, "title": kw.pop("title", id),
            "decision": "do the thing", "why": "because", "trigger_text": "when X",
            "watch": kw.pop("watch", None), "status": status, **kw}


def test_fold_latest_decision_row_wins(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _dec("a", "2026-07-17T18:00:00", status="open"),
        _dec("b", "2026-07-21T18:00:00", status="open"),
        _dec("a", "2026-07-24T18:00:00", status="done", note="sold"),
    ])
    folded = decisions_store.fold(decisions_store.load())
    assert [d["id"] for d in folded] == ["a", "b"]  # first-seen order preserved
    a = folded[0]
    assert a["status"] == "done" and a["note"] == "sold"


def test_fold_attaches_latest_check(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _dec("a", "2026-07-17T18:00:00"),
        {"kind": "check", "id": "a", "ts": "2026-07-22T17:31:00", "date": "2026-07-22",
         "fired": False, "detail": "red day"},
        {"kind": "check", "id": "a", "ts": "2026-07-23T17:31:00", "date": "2026-07-23",
         "fired": True, "detail": "green day"},
    ])
    folded = decisions_store.fold(decisions_store.load())
    assert folded[0]["last_check"]["fired"] is True
    assert folded[0]["last_check"]["date"] == "2026-07-23"


def test_fold_no_check_is_none(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [_dec("a", "2026-07-17T18:00:00")])
    assert decisions_store.fold(decisions_store.load())[0]["last_check"] is None


def test_load_skips_corrupt_lines_and_orphan_checks_are_ignored_by_fold(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        "not json {",
        _dec("a", "2026-07-17T18:00:00"),
        {"kind": "check", "id": "ghost", "ts": "2026-07-23T17:31:00", "date": "2026-07-23",
         "fired": True, "detail": "orphan"},
    ])
    folded = decisions_store.fold(decisions_store.load())
    assert [d["id"] for d in folded] == ["a"]  # no ghost decision invented


def test_append_then_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    decisions_store.append([_dec("a", "2026-07-17T18:00:00")])
    decisions_store.append([{"kind": "check", "id": "a", "ts": "2026-07-24T17:31:00",
                             "date": "2026-07-24", "fired": False, "detail": "waiting"}])
    folded = decisions_store.fold(decisions_store.load())
    assert folded[0]["id"] == "a" and folded[0]["last_check"]["detail"] == "waiting"


def test_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    assert decisions_store.load() == []
    assert decisions_store.fold([]) == []
