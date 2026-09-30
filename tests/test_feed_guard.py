"""The kestrel feed never reaches the order path, the network or a subprocess (spec 2026-09-26 kestrel feed §6).

An AST walk follows every repo module feed_service and its exporter import, transitively, and checks what each of
their import statements resolves to (test_bench_guard's resolution rules, extended across the whole graph); a fresh
interpreter then imports feed_service for real and inspects sys.modules; a source guard keeps it from writing.
(The GET /api/feed router was archived with the web app 2026-09-28; kestrel runs feed_export itself.)"""
import ast
import inspect
import subprocess
import sys

from webull_api.paths import REPO_ROOT
from webull_web import feed_export, feed_service

ROOTS = ("webull_web.feed_service", "webull_web.feed_export")
FORBIDDEN = ("webull_api.trading", "webull_api.client", "webull_api.portfolio", "webull_api.market_data",
             "webull_api.safety", "webull_api.autopilot.run", "subprocess", "requests", "urllib", "socket",
             # W4: the SDK itself, more of the order/sandbox/day-trade surface, and the wider HTTP-client family
             "webull", "webull_api.config", "http.client", "httpx", "urllib3", "aiohttp",
             # 2026-09-28 stop-resting design: the stop LEVEL lives in the order-path planner; the feed may only
             # read the audit log for whether a protective stop is known, never the 8% rule that prices one.
             "webull_api.reconcile")
FORBIDDEN_AT_IMPORT = ("webull_api.trading", "webull_api.client", "webull_api.config", "webull_api.portfolio",
                       "webull_api.market_data", "webull_api.safety", "webull_api.autopilot.run", "webull",
                       "requests", "httpx", "urllib3", "webull_api.reconcile")
WRITES = (".write_text(", ".write_bytes(", "atomic_write", "open(", ".unlink(", ".mkdir(", ".rename(", "os.replace",
          "shutil", ".append_", "save(", ".touch(")


def _file_of(module: str):
    """The repo file for a dotted module name (a package is its __init__.py), or None outside the repo."""
    base = REPO_ROOT.joinpath(*module.split("."))
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    return None


def _package_of(module: str, path) -> str:
    return module if path.name == "__init__.py" else module.rpartition(".")[0]


def _imports(module: str):
    """Every module an import statement in `module` resolves to: `from a import b` yields `a` and, when `a.b` is a
    module, `a.b` too; a relative import is resolved against the module's package."""
    path = _file_of(module)
    package = _package_of(module, path)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                bits = package.rsplit(".", node.level - 1)
                base = bits[0] if len(bits) >= node.level else ""
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            yield target
            for alias in node.names:
                yield f"{target}.{alias.name}"


def _graph() -> dict[str, set[str]]:
    """{repo module reached from ROOTS: the names its import statements resolve to}, parent packages included."""
    seen: dict[str, set[str]] = {}
    todo = list(ROOTS)
    while todo:
        module = todo.pop()
        if module in seen or _file_of(module) is None:
            continue
        seen[module] = set(_imports(module))
        parents = [module.rsplit(".", n)[0] for n in range(1, module.count(".") + 1)]
        todo += [m for m in seen[module] | set(parents) if _file_of(m) is not None]
    return seen


def _hits(name: str, bad: str) -> bool:
    return name == bad or name.startswith(bad + ".")


def test_nothing_the_feed_reaches_imports_the_order_path_the_network_or_a_subprocess():
    graph = _graph()
    assert "webull_web.tracks" in graph and "webull_api.journal.pairing" in graph      # the walk really walks
    for module, targets in graph.items():
        for bad in FORBIDDEN:
            assert not _hits(module, bad), f"the feed reaches {module}"
            hit = sorted(t for t in targets if _hits(t, bad))
            assert not hit, f"{module} imports {hit} (forbidden: {bad})"


def _calls_dynamic_import(module: str) -> bool:
    """True if `module`'s source calls `importlib.import_module(...)` or the `__import__(...)` builtin -- either
    could reach a forbidden module without leaving a trace in the static import graph (W4)."""
    path = _file_of(module)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in ("__import__", "import_module"):
            return True
        if isinstance(f, ast.Attribute) and f.attr == "import_module":
            return True
    return False


def test_nothing_the_feed_reaches_calls_a_dynamic_import():
    graph = _graph()
    for module in graph:
        assert not _calls_dynamic_import(module), f"{module} calls importlib.import_module or __import__"


def test_an_injected_forbidden_import_two_hops_away_is_caught_by_graph(tmp_path, monkeypatch):
    """M6 self-check (the guard's own pattern, e.g. test_lab_safety.py's AST self-checks): the old version of this
    test re-parsed a modified copy with its OWN mini AST walk, never exercising `_file_of`/`_imports`/`_graph`
    themselves -- not relative-import resolution, not `from a import b` resolving to `a.b`, not the transitive
    walk. This one points the guard's own resolver at a temp package tree where the forbidden import sits two hops
    away (pkg.a --[relative import]--> pkg.b --[`from a import b` form]--> webull_api.trading) and asserts `_graph()`
    itself reports it, rather than trusting that the real resolver never regresses silently."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("from . import b\n", encoding="utf-8")             # relative import: pkg.a -> pkg.b
    (pkg / "b.py").write_text("from webull_api import trading\n", encoding="utf-8")   # "from a import b" form
    this_module = sys.modules[__name__]
    monkeypatch.setattr(this_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(this_module, "ROOTS", ("pkg.a",))
    graph = _graph()
    assert "pkg.b" in graph, "the relative import pkg.a -> pkg.b was not resolved by the real walker"
    assert any(_hits(t, "webull_api.trading") for t in graph["pkg.b"]), \
        "the forbidden import two hops away was not detected by _graph() itself"


def test_importing_the_feed_loads_no_client_order_or_network_module():
    proc = subprocess.run(
        [sys.executable, "-c", "import sys, webull_web.feed_service; print('\\n'.join(sys.modules))"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    loaded = set(proc.stdout.split())
    for bad in FORBIDDEN_AT_IMPORT:
        assert not any(_hits(m, bad) for m in loaded), f"importing feed_service loaded {bad}"


def test_the_feed_writes_nothing():
    """M7: the write-free source scan covers feed_service. feed_export is scanned as well: its only
    "write" is `sys.stdout.buffer.write(...)`, a STREAM write, which none of the WRITES patterns below match --
    they name a real FILE write specifically (`.write_text(`/`.write_bytes(`, not the bare `.write(` a stream
    also uses), on purpose, so the stdout write keeps passing while a future FILE write added to the exporter
    still trips this test."""
    for module in (feed_service, feed_export):
        src = inspect.getsource(module)
        for word in WRITES:
            assert word not in src, f"{module.__name__} must not contain {word!r}"
