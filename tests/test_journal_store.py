from webull_api.journal.schema import Fill, PracticeDecision
from webull_web import journal_store


def _fill(fid):
    return Fill(id=fid, source="paper", account_id="A", symbol="AAPL", side="BUY", quantity=1,
                price=10.0, filled_at_iso="2026-06-01T00:00:00+00:00", order_type="MARKET")


def test_append_fills_dedups_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    assert journal_store.append_fills([_fill("a"), _fill("b")]) == 2
    assert journal_store.append_fills([_fill("b"), _fill("c")]) == 1  # only "c" is new
    loaded = journal_store.load_fills()
    assert {f.id for f in loaded} == {"a", "b", "c"}
    assert all(isinstance(f, Fill) for f in loaded)


def test_append_decisions_dedups(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    d = PracticeDecision(id="s:0", session_id="s", strategy_name="x", symbol="AAPL",
                         decided_at_iso="t", choice="take", signal_why="w")
    assert journal_store.append_decisions([d]) == 1
    assert journal_store.append_decisions([d]) == 0
    assert journal_store.load_decisions()[0].id == "s:0"


def test_meta_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    assert journal_store.read_meta() == {}
    journal_store.write_meta({"at": "now", "appended": {"real": 1}})
    assert journal_store.read_meta() == {"at": "now", "appended": {"real": 1}}


def test_load_empty_when_no_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "nope"))
    assert journal_store.load_fills() == []
    assert journal_store.load_decisions() == []
