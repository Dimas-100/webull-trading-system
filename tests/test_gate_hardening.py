"""Hardened guards for the project's #1 invariant: no real order submits without confirm=True,
and the read-only surfaces never reach a submit.

These ADD coverage around the gate; they do not modify safety.py / trading.py. What stays here:
  (3b) a namespace check on the read-only MCP server (no submit symbol importable from it);
  the autopilot AST guards: run.py's one trading.place / place_option call lives inside its
  gated helper, behind an early-returning `if not decision.allow`.
The web order-route composition tests (Finding 22) and the Copilot tools AST guard (3a) were
archived with the web app 2026-09-28; trading.place's confirm gate stays pinned directly in
tests/test_trading.py (test_dry_run_previews_but_never_places, test_prod_without_confirm_does_not_place,
test_confirm_true_places_on_test).
"""
from __future__ import annotations


# ── Finding 3b: read-only MCP server exposes no submit symbol in its namespace ──
def test_webull_mcp_server_namespace_has_no_submit_symbols():
    import webull_mcp.server as s
    for attr in ("trading", "place", "place_option", "place_order", "cancel", "modify"):
        assert not hasattr(s, attr), f"webull_mcp.server unexpectedly exposes {attr!r}"


# ── Autopilot: run.py's sole trading.place is structurally bound inside the gate guard ──
def test_autopilot_run_places_only_inside_a_gate_guard():
    """Safety invariant guarding the sacrosanct submit gate: run.py's ONE trading.place call must
    live inside the _try_place helper, which calls authorize() and early-returns (no placement)
    when the Decision is not allowed. Structural AST checks (not substring) so a relocated/ungated
    place call can't pass behind a vestigial _try_place."""
    import ast
    import inspect
    from webull_api.autopilot import run as autorun

    tree = ast.parse(inspect.getsource(autorun))

    # (a) exactly one `*.place(...)` call in the whole module
    place_calls = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "place"]
    assert len(place_calls) == 1, f"expected exactly one .place() call in run.py, got {len(place_calls)}"

    # (b) the _try_place helper exists and the place() call is a descendant of it
    guard_fn = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "_try_place"), None)
    assert guard_fn is not None, "_try_place helper missing"
    assert place_calls[0] in set(ast.walk(guard_fn)), "the .place() call must live inside _try_place"

    # (c) _try_place calls authorize()
    calls = {c.func.id for c in ast.walk(guard_fn)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "authorize" in calls, "_try_place must call authorize()"

    # (d) _try_place has an `if not <...>.allow: ... return` guard (structural, early-return)
    def _is_not_allow_guard(node):
        if not isinstance(node, ast.If):
            return False
        t = node.test
        if not (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)):
            return False
        return any(isinstance(a, ast.Attribute) and a.attr == "allow" for a in ast.walk(t))

    guards = [n for n in ast.walk(guard_fn) if _is_not_allow_guard(n)]
    assert guards, "_try_place must have an `if not <decision>.allow:` guard"
    assert any(isinstance(b, ast.Return) for g in guards for b in g.body), \
        "the `if not ...allow` guard must early-return (no placement below it)"

    # (e) the guard's early return must come BEFORE the place() call in source order, so a future
    # refactor that hoists trading.place above the guard (or drops the guard) fails this test.
    guard_return_lines = [b.lineno for g in guards for b in g.body if isinstance(b, ast.Return)]
    assert guard_return_lines and min(guard_return_lines) < place_calls[0].lineno, \
        "the deny early-return must come before the place() call"


# ── Decision executor: run.py's sole place_option is structurally bound inside its gate guard ──
def test_autopilot_run_places_options_only_inside_the_option_gate_guard():
    """Mirror of the equity guard test for the 2026-08-07 decision executor: run.py's ONE
    trading.place_option call must live inside _try_place_option, which calls authorize_option()
    and early-returns on a denied Decision BEFORE the placement line."""
    import ast
    import inspect
    from webull_api.autopilot import run as autorun

    tree = ast.parse(inspect.getsource(autorun))

    place_calls = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "place_option"]
    assert len(place_calls) == 1, \
        f"expected exactly one .place_option() call in run.py, got {len(place_calls)}"

    guard_fn = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == "_try_place_option"), None)
    assert guard_fn is not None, "_try_place_option helper missing"
    assert place_calls[0] in set(ast.walk(guard_fn)), \
        "the .place_option() call must live inside _try_place_option"

    calls = {c.func.id for c in ast.walk(guard_fn)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "authorize_option" in calls, "_try_place_option must call authorize_option()"

    def _is_not_allow_guard(node):
        if not isinstance(node, ast.If):
            return False
        t = node.test
        if not (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)):
            return False
        return any(isinstance(a, ast.Attribute) and a.attr == "allow" for a in ast.walk(t))

    guards = [n for n in ast.walk(guard_fn) if _is_not_allow_guard(n)]
    assert guards, "_try_place_option must have an `if not <decision>.allow:` guard"
    guard_return_lines = [b.lineno for g in guards for b in g.body if isinstance(b, ast.Return)]
    assert guard_return_lines and min(guard_return_lines) < place_calls[0].lineno, \
        "the deny early-return must come before the place_option() call"

    # confirm=True must be explicit and literal on the placement call (no variable indirection)
    kw = {k.arg: k.value for k in place_calls[0].keywords}
    assert "confirm" in kw and isinstance(kw["confirm"], ast.Constant) and kw["confirm"].value is True, \
        "place_option must pass a literal confirm=True inside the guarded helper"
