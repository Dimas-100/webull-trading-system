"""`python -m webull_web.feed_export` -- the kestrel feed printed to stdout, no server needed (plan
2026-09-27-kestrel-feed-export.md Task 1). Read-only like feed_service; this is the command source kestrel's
profile names."""
import json
import subprocess
import sys

from webull_api.paths import REPO_ROOT
from webull_web import feed_export, feed_service


def test_stdout_is_the_documents_json(capsys, monkeypatch):
    monkeypatch.setattr(feed_service, "build", lambda: {"contract_version": "1", "books": []})
    rc = feed_export.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert json.loads(out.out) == {"contract_version": "1", "books": []}
    assert out.err == ""


def test_a_raising_build_exits_1_with_nothing_on_stdout_and_one_stderr_line_without_a_path(capsys, monkeypatch):
    def boom():
        raise PermissionError(13, "Permission denied", r"C:\Users\someone\secret\state.json")
    monkeypatch.setattr(feed_service, "build", boom)
    rc = feed_export.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err == "feed export failed: PermissionError reading state.json\n"
    assert "C:" not in out.err and "secret" not in out.err and "someone" not in out.err


def test_a_nan_in_the_document_exits_1_not_invalid_json(capsys, monkeypatch):
    monkeypatch.setattr(feed_service, "build", lambda: {"contract_version": "1", "value": float("nan")})
    rc = feed_export.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.startswith("feed export failed: ValueError")


def test_a_real_subprocess_run_exits_0_with_a_valid_contract_v1_document():
    """No data/ folder exists in this worktree (it's gitignored) -- the exporter still succeeds: every block that
    reads a missing local file becomes a note alert and an empty block (feed_service's own guarantee), never a
    crash. This proves the command really runs end to end against the repo's own (absent) files; it does not
    assert specific book ids, which need a populated data/ folder."""
    proc = subprocess.run([sys.executable, "-m", "webull_web.feed_export"], cwd=REPO_ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["contract_version"] == "1"


def test_the_full_desk_document_prints_as_strict_json(tmp_path, monkeypatch, capsys):
    """Ported 2026-09-28 from the archived GET /api/feed route test (its only end-to-end check of a POPULATED desk):
    the real document over the fictional desk serialises as strict JSON with every RUNNING book (2026-09-29) and the trade charts."""
    from feed_fixture import write_desk
    write_desk(tmp_path, monkeypatch)
    rc = feed_export.main([])
    out = capsys.readouterr()
    assert rc == 0, out.err
    doc = json.loads(out.out)
    assert doc["contract_version"] == "1" and [b["id"] for b in doc["books"]] == ["rsi2-real", "pullback-real"] and doc["trade_charts"]
