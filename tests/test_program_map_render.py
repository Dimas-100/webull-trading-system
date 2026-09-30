"""docs/program-map.md's tables are rendered from the registry + routine and may not drift
(spec 2026-09-07 program map §3.2)."""
from webull_api.paths import REPO_ROOT
from webull_web import program_map

DOC = REPO_ROOT / "docs" / "program-map.md"


def test_render_fills_every_marked_region_and_is_idempotent():
    skeleton = "\n".join(f"<!-- map:{n}:start -->\nSTALE-TABLE-SENTINEL\n<!-- map:{n}:end -->" for n in program_map.SECTIONS)
    once = program_map.render(skeleton)
    assert "STALE-TABLE-SENTINEL" not in once
    assert "| Swing·RSI2 |" in once and "| 09:31 |" in once and "Swing·Pullback · real" in once
    assert program_map.render(once) == once


def test_render_fills_an_empty_skeleton_with_adjacent_markers():
    # the committed doc starts life with nothing between a region's start and end markers
    skeleton = "<!-- map:grid:start -->\n<!-- map:grid:end -->"
    out = program_map.render(skeleton)
    assert "| Swing·RSI2 |" in out
    assert out.startswith("<!-- map:grid:start -->\n|") and out.endswith("|\n<!-- map:grid:end -->")
    assert program_map.render(out) == out


def test_render_leaves_prose_alone_and_ignores_unknown_markers():
    text = "# Title\nprose stays\n<!-- map:nope:start -->\nkeep\n<!-- map:nope:end -->\n"
    assert program_map.render(text) == text


def test_committed_doc_is_current():
    text = DOC.read_text(encoding="utf-8")
    assert program_map.render(text) == text, "run: .venv/Scripts/python.exe scripts/render_program_map.py"


def test_pipes_in_values_are_escaped():
    from webull_web.tracks import Track
    t = Track(key="x", label="X·Y", money="paper", summary="a | b", book="b", book_source="s", plan_doc="p",
              universe="u", entry="e", exit="x", schedule="s", data=("d",), journal="j", kill_switch="k", review="r")
    assert "a \\| b" in program_map.grid_table((t,))
