"""The track registry is the one place the tracks are named (spec 2026-09-07 program map §2/§3.1).
Every cell carries the nine map fields; every plan doc it points at exists in the repo."""
from dataclasses import fields

from webull_api.paths import REPO_ROOT
from webull_web import tracks

MAP_FIELDS = ("plan_doc", "book", "universe", "entry", "exit", "schedule", "data", "journal", "kill_switch", "review")


def test_every_cell_is_fully_described():
    assert len(tracks.TRACKS) == 3        # RSI2 paper + real, pullback real (RSI2-only 2026-09-29)
    for t in tracks.TRACKS:
        for name in MAP_FIELDS:
            v = getattr(t, name)
            assert v, f"{t.key}/{t.money}.{name} is empty"
        assert t.summary and t.book_source
        assert t.money in ("paper", "real", "none")


def test_keys_are_unique_per_money_state_and_labels_use_the_middle_dot():
    seen = {(t.key, t.money) for t in tracks.TRACKS}
    assert len(seen) == len(tracks.TRACKS)
    for t in tracks.TRACKS:
        if t.key not in ("lab",):
            assert "·" in t.label, t.label
    assert tracks.label_for("swing_rsi2") == "Swing·RSI2"
    assert tracks.label_for("all") == "All"
    assert tracks.label_for("nope") == "nope"


def test_plan_docs_exist_and_data_paths_are_repo_relative():
    for t in tracks.TRACKS:
        assert (REPO_ROOT / t.plan_doc).exists(), t.plan_doc
        for p in t.data:
            assert not p.startswith(("/", "\\")) and ":" not in p, p


def test_the_retired_carve_out():
    assert tracks.DAY_CARVE_OUT == 0.0


def test_every_cell_has_a_dated_state_and_the_tabs_derive_from_it():
    from dataclasses import replace
    for t in tracks.TRACKS:
        assert t.state in ("running", "paused") and t.state_note, f"{t.key}/{t.money}"
    tabs = tracks.tab_states()
    assert set(tabs) == set(tracks.PLAN_TABS)
    # SIMPLIFIED 2026-09-29: the real RSI2 book keeps the swing tab running
    assert tabs["swing_rsi2"]["state"] == "running"
    # a tab runs if ANY of its books runs; all paused -> paused
    all_paused = tuple(replace(t, state="paused") for t in tracks.TRACKS)
    assert {v["state"] for v in tracks.tab_states(all_paused).values()} == {"paused"}
    one_real = tuple(replace(t, state="running" if (t.key, t.money) == ("swing_rsi2", "real") else "paused")
                     for t in tracks.TRACKS)
    assert tracks.tab_states(one_real)["swing_rsi2"]["state"] == "running"


def test_track_is_frozen():
    t = tracks.TRACKS[0]
    assert {f.name for f in fields(t)} >= set(MAP_FIELDS) | {"key", "label", "money", "summary", "book_source"}
