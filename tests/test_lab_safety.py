"""Source-level safety guards for the Strategy Learning Machine (M6).

Mirrors tests/test_gate_hardening.py / test_paper_safety.py but applied to EVERY new lab module
(webull_api/lab/*.py) (the lab MCP connector was removed 2026-09-29). Layers:
  (1) call-shaped substring denylist (cheap),
  (2) AST walk catching aliased/indirect mutating-order calls (Task 38),
  (3) namespace hasattr absence + MCP containment (Task 39).
Plus denylist self-check meta-tests (a typo'd denylist can't silently pass), the PRECISE allowance
for the PURE paper.engine.place_order (NOT the real submit surface), the PaperProvenRecord handoff
shape, and the should_submit byte-identity (Task 40). These ADD coverage; they modify no source.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import textwrap
import types

import webull_api.lab
import webull_web.lab_service        # proven-store path (read-only composition layer)
import webull_web.lab_store          # proven-store path (file I/O)
from webull_api import safety

# Call-shaped (trailing "(") so a docstring/comment MENTION of the gate is not a false positive.
_FORBIDDEN_SUBSTRINGS = (
    "trading.place(",
    "trading.place_option(",
    "place_option(",
    "order_v2",
    "order_v2.place_order(",
    "should_submit(",
    "import trading",
)


def _lab_source_files() -> list[pathlib.Path]:
    """Every .py under the lab engine package AND the lab MCP connector."""
    files: list[pathlib.Path] = []
    for pkg in (webull_api.lab,):
        files += sorted(pathlib.Path(pkg.__file__).resolve().parent.rglob("*.py"))
    assert files, "no lab source files discovered — wrong package path?"
    return files


def scan_substrings(src: str) -> list[str]:
    """Return the forbidden substrings present in `src` (empty == clean)."""
    return [bad for bad in _FORBIDDEN_SUBSTRINGS if bad in src]


def test_substring_denylist_self_check():
    planted = "x = 1\ntrading.place(acct, order)\nfrom webull_api import trading\n"
    hits = scan_substrings(planted)
    assert "trading.place(" in hits, "denylist must trip on a planted forbidden call"
    assert "import trading" in hits, "denylist must trip on a planted trading import"
    assert scan_substrings("y = backtest.run_backtest(s, bars)\n") == [], "clean code must not trip"


def test_market_data_dependency_stays_order_free():
    # The lab now imports webull_api.market_data (read-only) for MarketDataNotEntitledError. Pin that
    # this newly-depended module carries no order/submit surface, so a future edit can't silently turn
    # the lab's data dependency into a path to a real order.
    import webull_api.market_data
    src = pathlib.Path(webull_api.market_data.__file__).read_text(encoding="utf-8")
    assert scan_substrings(src) == [], "market_data (a lab dependency) must stay order-free"


# ── (1) Substring layer over every real lab/MCP module ──
def test_lab_modules_have_no_forbidden_substrings():
    for f in _lab_source_files():
        hits = scan_substrings(f.read_text(encoding="utf-8"))
        assert hits == [], f"{f.name} contains forbidden token(s): {hits}"


def test_lab_modules_do_not_import_the_trading_module():
    """The aliased forms (`import webull_api.trading as t`) are caught by the AST guard in Task 38;
    here the common explicit import spellings the substring layer can read directly."""
    for f in _lab_source_files():
        src = f.read_text(encoding="utf-8")
        for bad in ("from webull_api.trading", "from webull_api import trading",
                    "import webull_api.trading"):
            assert bad not in src, f"{f.name} imports the real trading module ({bad!r})"


# ── (2) AST layer: catch aliased/indirect mutating-order calls; ALLOW pure paper.engine.place_order ──
_FORBIDDEN_ATTRS = {"place", "place_option", "cancel", "modify", "should_submit"}
_TRADING_RECEIVERS = {"trading", "order_v2"}  # receivers that make `.place_order` forbidden


def find_ast_violations(src: str) -> list[str]:
    """Walk the AST. Flag any Call whose resolved target is a forbidden mutating-order fn — even
    via import-as alias or a simple `name = module.attr` assignment. `.place_order` is flagged ONLY
    when its receiver resolves to trading/order_v2; paper.engine.place_order is permitted."""
    tree = ast.parse(src)
    trading_names: set[str] = set()      # local names bound to the trading module / order_v2
    aliased_forbidden: set[str] = set()  # local names bound to a forbidden callable

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] == "trading":          # import webull_api.trading [as t]
                    trading_names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "trading":                          # from webull_api import trading [as t]
                    trading_names.add(a.asname or a.name)
                if a.name in _FORBIDDEN_ATTRS:                    # from ...trading import place as p
                    aliased_forbidden.add(a.asname or a.name)
                # bare `from ...trading import place_order` — the receiver is implicit so the
                # attribute-access guard below can't catch it; flag it here (defense-in-depth).
                if a.name == "place_order" and node.module and "trading" in node.module:
                    aliased_forbidden.add(a.asname or a.name)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Attribute) \
                and node.value.attr in _FORBIDDEN_ATTRS:         # f = safety.should_submit
            aliased_forbidden.add(node.targets[0].id)

    def _recv(attr_node: ast.Attribute) -> str:
        r = attr_node.value
        if isinstance(r, ast.Name):
            return r.id
        if isinstance(r, ast.Attribute):
            return r.attr
        return ""

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            if f.id in _FORBIDDEN_ATTRS or f.id in aliased_forbidden:
                violations.append(f.id)
        elif isinstance(f, ast.Attribute):
            recv = _recv(f)
            if f.attr in _FORBIDDEN_ATTRS:
                violations.append(f"{recv}.{f.attr}")
            elif f.attr == "place_order" and (recv in _TRADING_RECEIVERS or recv in trading_names):
                violations.append(f"{recv}.{f.attr}")
    return violations


def test_ast_guard_self_check_catches_aliases():
    # import-as alias of the trading module
    assert "t.place" in find_ast_violations("import webull_api.trading as t\nt.place(a, o)\n")
    # from-import alias of a forbidden callable
    assert "p" in find_ast_violations("from webull_api.trading import place as p\np(1)\n")
    # assignment alias of should_submit
    assert "f" in find_ast_violations(
        "from webull_api import safety\nf = safety.should_submit\nf(True)\n")
    # trading-namespaced .place_order IS forbidden
    assert find_ast_violations("from webull_api import trading\ntrading.place_order(a, o)\n")
    # bare `from webull_api.trading import place_order` — defense-in-depth for the ImportFrom path
    assert find_ast_violations("from webull_api.trading import place_order\nplace_order(a, o)\n")


def test_ast_guard_allows_pure_paper_engine_place_order():
    """The pure paper.engine.place_order is NOT the real submit surface — receiver resolves to
    `engine`, so it must NOT be flagged (RT-17)."""
    assert find_ast_violations("from webull_api.paper import engine\nengine.place_order(acct, symbol='X')\n") == []
    assert find_ast_violations("from webull_api import paper\npaper.engine.place_order(acct)\n") == []


def test_lab_modules_ast_have_no_mutating_order_call():
    for f in _lab_source_files():
        v = find_ast_violations(f.read_text(encoding="utf-8"))
        assert v == [], f"{f.name} has an AST-resolved mutating-order call: {v}"


def test_trial_fills_route_only_through_paper_engine():
    """Positive: across the lab, the ONLY order-placement surface (if any `.place_order`) resolves
    to the pure paper.engine — never trading/order_v2. The real trading module is never imported."""
    combined = "".join(f.read_text(encoding="utf-8") for f in _lab_source_files())
    # No real-trading import in any form (substring forms; aliased forms covered by the AST guard).
    for bad in ("from webull_api.trading", "from webull_api import trading",
                "import webull_api.trading", "order_v2"):
        assert bad not in combined, f"a lab module references the real order surface ({bad!r})"
    # The pure paper engine is the only legitimate order surface the lab may import.
    assert find_ast_violations(combined) == []


# ── (3) namespace layer: no lab module / lab MCP server may BIND a submit/order symbol ──
_NO_SUBMIT_ATTRS = ("trading", "place", "place_order", "place_option", "cancel", "modify",
                    "should_submit")


def _import_lab_namespaces() -> list[tuple[str, object]]:
    """(module_name, module) for every webull_api/lab/*.py module (reusing the Task-37 file
    discovery) plus webull_web.lab_service — the namespaces a `from x import
    place_order`-style leak would taint."""
    lab_root = pathlib.Path(webull_api.lab.__file__).resolve().parent
    pairs: list[tuple[str, object]] = []
    for f in _lab_source_files():
        if f.parent != lab_root:
            continue
        name = "webull_api.lab" if f.stem == "__init__" else f"webull_api.lab.{f.stem}"
        pairs.append((name, importlib.import_module(name)))
    pairs.append(("webull_web.lab_service", importlib.import_module("webull_web.lab_service")))
    # self-check: the two M4 additions must be present or the namespace sweep is incomplete
    assert any(n == "webull_web.lab_service" for n, _ in pairs), \
        "self-check: webull_web.lab_service not loaded — import failed silently"
    return pairs


def _namespace_leaks(mod) -> list[str]:
    """Submit/order names actually bound in a module's namespace (empty == clean). The pure paper
    engine is reached as `engine.place_order` (module-qualified) — `place_order` is never bound, so
    a correctly-built lab module passes; a bare `from paper.engine import place_order` would trip."""
    return [a for a in _NO_SUBMIT_ATTRS if hasattr(mod, a)]


def test_namespace_leak_self_check():
    leaky = types.SimpleNamespace(place_order=lambda *a, **k: None, helper=1)
    assert _namespace_leaks(leaky) == ["place_order"], "must flag a bound place_order symbol"
    clean = types.SimpleNamespace(helper=1, build_proposal_brief=lambda: None, engine=object())
    assert _namespace_leaks(clean) == [], "module-qualified engine.place_order must NOT trip"


def test_lab_module_namespaces_expose_no_submit_symbols():
    """Stronger than the per-file substring/AST scans: walks the LIVE namespace of every lab module
    and the lab MCP server, so a `from webull_api.trading import place_order` leak is caught even
    though it adds no `trading.`/`.place_order(` token to the file's own source."""
    for name, mod in _import_lab_namespaces():
        leaked = _namespace_leaks(mod)
        assert leaked == [], f"{name} binds submit/order symbol(s) in its namespace: {leaked}"


# ── (4) the #1 invariant locked at the source level: should_submit body byte-identity ──
def _normalize_body(fn) -> str:
    """A function's executable body as normalized source — docstring stripped, whitespace-robust
    (ast.unparse canonicalizes), making this a true source/string-equality check."""
    node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    stmts = [s for s in node.body
             if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    return "; ".join(ast.unparse(s) for s in stmts)


def test_normalize_body_extractor_self_check():
    def _sample(env, confirm):
        """ignored docstring"""
        return bool(confirm)
    assert _normalize_body(_sample) == "return bool(confirm)"


def test_should_submit_body_is_exactly_return_bool_confirm():
    """Mirrors tests/test_gate_hardening.py at the SOURCE level: prove THIS branch never weakened the
    submit gate — safety.should_submit's entire executable body is `return bool(confirm)` and nothing
    else (the env arg is read by no statement). Any added factor/branch fails this immediately."""
    assert _normalize_body(safety.should_submit) == "return bool(confirm)"


# ── (5) the proven-store path can never authorize a real order, by construction ──
def _proven_store_files() -> list[pathlib.Path]:
    """webull_web.lab_store + webull_web.lab_service (read/write the $LAB_DIR/proven shelf) plus
    every lab source file that HANDLES a PaperProvenRecord — the whole paper_proven-record path.
    Reuses the Task-37 _lab_source_files discovery."""
    files = [pathlib.Path(webull_web.lab_store.__file__).resolve(),
             pathlib.Path(webull_web.lab_service.__file__).resolve()]
    for f in _lab_source_files():
        if "PaperProvenRecord" in f.read_text(encoding="utf-8"):
            files.append(f)
    return files


def test_proven_store_path_never_reaches_the_submit_surface():
    """A `paper_proven` record is data only (its human-promotion gate is pre-installed OFF — see
    PaperProvenRecord / build_proven_record). Guarantee it cannot be wired to a real order: no module
    that reads/writes the $LAB_DIR/proven store (lab_store, lab_service) nor any lab module handling a
    PaperProvenRecord may import webull_api.trading or resolve a mutating-order call."""
    files = _proven_store_files()
    assert any(f.name == "lab_store.py" for f in files), "lab_store must be on the proven path"
    assert any(f.name == "lab_service.py" for f in files), "lab_service must be on the proven path"
    for f in files:
        src = f.read_text(encoding="utf-8")
        assert scan_substrings(src) == [], f"{f.name} contains a forbidden submit token"
        assert find_ast_violations(src) == [], f"{f.name} has an AST-resolved mutating-order call"
        for bad in ("from webull_api.trading", "from webull_api import trading",
                    "import webull_api.trading"):
            assert bad not in src, f"{f.name} imports the real trading module ({bad!r})"


# ── (M5) Source census: every M2–M4 module must remain rglob-discoverable ────────────────────────

def _web_lab_surface_files() -> list[pathlib.Path]:
    """The M4 autopilot surface file outside webull_api/lab -- the impure run_cycle
    wrapper (webull_web/lab_service.py) -- and therefore NOT swept by the original _lab_source_files()
    discovery. It is an I/O path that must not touch the submit surface. (The /api/lab/* routes that
    also lived here were archived with the web app, 2026-09-28.)"""
    files = [
        pathlib.Path(webull_web.lab_service.__file__).resolve(),
    ]
    for f in files:
        assert f.exists(), f"web lab surface file not found: {f}"
    return files


def test_lab_source_census():
    """Regression guard: every new M2/M3 pure-engine module AND the M4 CLI module must appear
    in the rglob-discovered set so a dropped or renamed file fails this test immediately rather
    than silently falling out of the substring/AST/namespace sweeps below."""
    files = _lab_source_files()
    names = {f.name for f in files}
    # M2 proposer + M3 stagnation/cycle conductor (under webull_api/lab/)
    for expected in ("propose.py", "stagnation.py", "cycle.py"):
        assert expected in names, (
            f"{expected} not found in _lab_source_files() — M2/M3 source module is "
            "missing or was renamed; add it back to webull_api/lab/ to restore the guard"
        )


def test_lab_namespace_extended_covers_cli_and_service():
    """The _import_lab_namespaces() extension (impl step) must expose the M4 CLI and the impure
    service wrapper to the live-namespace sweep, so a `from webull_api.trading import place_order`
    leak in either module is caught by the existing test_lab_module_namespaces_expose_no_submit_symbols.
    RED: fails until _import_lab_namespaces() is extended with the two new pairs."""
    names = {name for name, _ in _import_lab_namespaces()}
    assert "webull_web.lab_service" in names, (
        "_import_lab_namespaces() must include webull_web.lab_service (M4 service) — "
        "extend the function with the two new pairs in the impl step"
    )


def test_web_lab_surface_never_reaches_submit():
    """Runs all three guard layers (substring denylist, AST walk, explicit trading-import check)
    over the M4 autopilot surface file that _lab_source_files() does not discover (it lives in
    webull_web/, not webull_api/lab/): the run_cycle impure wrapper, which must
    stay read-only relative to the real order submit surface."""
    for f in _web_lab_surface_files():
        src = f.read_text(encoding="utf-8")
        hits = scan_substrings(src)
        assert hits == [], f"{f.name} contains forbidden submit token(s): {hits}"
        v = find_ast_violations(src)
        assert v == [], f"{f.name} has an AST-resolved mutating-order call: {v}"
        for bad in ("from webull_api.trading", "from webull_api import trading",
                    "import webull_api.trading"):
            assert bad not in src, (
                f"{f.name} imports the real trading module ({bad!r}) — "
                "this violates the hard read-only invariant for the autopilot surface"
            )
