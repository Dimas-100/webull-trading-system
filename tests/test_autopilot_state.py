# tests/test_autopilot_state.py
from webull_api.autopilot import state as st


def test_kill_switch_absent_is_false(tmp_path):
    assert st.kill_switch_active(str(tmp_path / "KILL")) is False


def test_kill_switch_present_is_true(tmp_path):
    f = tmp_path / "KILL"
    f.write_text("halt")
    assert st.kill_switch_active(str(f)) is True


def test_load_missing_state_returns_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    s = st.load_state("2026-07-07")
    assert s.day == "2026-07-07"
    assert s.orders_today == 0
    assert s.halt_tripped is False
    assert s.placed_symbols == []


def test_save_then_load_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    s = st.DailyState(day="2026-07-07", orders_today=2, realized_loss=-13.5,
                      halt_tripped=True, placed_symbols=["AAPL"])
    st.save_state(s)
    back = st.load_state("2026-07-07")
    assert back.orders_today == 2
    assert back.realized_loss == -13.5
    assert back.halt_tripped is True
    assert back.placed_symbols == ["AAPL"]
