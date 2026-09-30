"""The systems still load: a static guard over every scheduled / launcher / connector entry point.

Spec docs/superpowers/specs/2026-09-28-systems-only-design.md §6. Nothing here is imported or run. Each entry
point's in-repo import closure is walked with `ast` -- imports inside functions included, plus the paper
suite's by-name imports (`runner_cli.PAPER_SUITE`, `run_cli("<service>")`) -- and every target must resolve to
a repo file that defines the imported name. A module the systems still need that gets deleted or moved fails
here, not at 09:25 on a weekday. Repo imports are checked even inside `try/except`: a caught ImportError is
exactly how a runner loses a line of its report without anyone noticing.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# What the Windows scheduled tasks run directly (the .bat/.sh launchers are parsed below, so their targets
# can't drift from this list).
TASK_SCRIPTS = ["autopilot", "prune_logs", "suite_watchdog", "run_paper_suite", "nightly_note"]
# The launchers the scheduled tasks call by absolute path. Deleting one would otherwise pass: its targets just
# drop out of the parsed launcher set below.
TASK_LAUNCHERS = ["run-paper-suite.bat", "run-nightly-note.bat", "run-tiingo-backfill.bat"]
# The MCP servers the systems keep (Claude Desktop / Claude Code run `python -m <pkg>`).
KEPT_MCP = ["webull_trade_mcp", "webull_mcp"]
_SKIP_DIRS = {".venv", ".venv-snaptrade", "node_modules", "__pycache__", ".git", ".claude", "data", "logs"}
_OPTIONAL_HANDLERS = {"ImportError", "ModuleNotFoundError"}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _repo_packages(root: Path) -> set[str]:
    return {p.name for p in root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}


def _module_file(root: Path, name: str) -> Path | None:
    base = root.joinpath(*name.split("."))
    for cand in (base.with_suffix(".py"), base / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _toplevel_names(tree: ast.Module) -> tuple[set[str], bool]:
    """Names a module defines at import time, and whether it can define more dynamically."""
    names: set[str] = set()
    dynamic = False

    def visit(stmts):
        nonlocal dynamic
        for node in stmts:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                dynamic |= node.name == "__getattr__"
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            names.add(n.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    if a.name == "*":
                        dynamic = True
                    else:
                        names.add(a.asname or a.name.split(".")[0])
            elif isinstance(node, ast.If):
                visit(node.body)
                visit(node.orelse)
            elif isinstance(node, ast.Try):
                visit(node.body)
                for h in node.handlers:
                    visit(h.body)
                visit(node.orelse)
                visit(node.finalbody)
            elif isinstance(node, (ast.With, ast.For)):
                visit(node.body)

    visit(tree.body)
    return names, dynamic


def _catches_import_error(handlers) -> bool:
    for h in handlers:
        types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        for t in types:
            name = t.id if isinstance(t, ast.Name) else getattr(t, "attr", None)
            if name in _OPTIONAL_HANDLERS:
                return True
    return False


def _imports(tree: ast.Module, modname: str, is_pkg: bool):
    """Yield (absolute module, imported names or None, inside an ImportError-guarded try) per import."""
    optional: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and _catches_import_error(node.handlers):
            for stmt in node.body:
                optional.update(id(n) for n in ast.walk(stmt))
    pkg = modname if is_pkg else modname.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, None, id(node) in optional
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = pkg.split(".") if pkg else []
                parts = parts[:len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            yield base, [a.name for a in node.names], id(node) in optional


def _by_name_services(modname: str, tree: ast.Module) -> list[str]:
    """webull_web service modules named in strings: the paper suite list and `run_cli("<service>", ...)`."""
    out: list[str] = []
    for node in ast.walk(tree):
        if (modname == "webull_web.runner_cli" and isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id in ("PAPER_SUITE", "NOTE_STEP") for t in node.targets)):
            out += [elt.elts[1].value for elt in node.value.elts]
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run_cli" and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            out.append(node.args[0].value)
    return [f"webull_web.{name}" for name in out]


def _third_party_ok(top: str) -> bool:
    return top in sys.stdlib_module_names or importlib.util.find_spec(top) is not None


def check_closure(root: Path, entries: list[str]) -> list[str]:
    """Every problem found walking the in-repo import closure of `entries` (empty = all resolve)."""
    pkgs = _repo_packages(root)
    errors: list[str] = []
    seen: set[str] = set()
    queue = list(entries)
    while queue:
        mod = queue.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = _module_file(root, mod)
        if path is None:
            errors.append(f"{mod}: module file not found")
            continue
        parts = mod.split(".")
        queue += [".".join(parts[:i]) for i in range(1, len(parts))]  # importing a.b.c runs a and a.b first
        tree = _parse(path)
        for svc in _by_name_services(mod, tree):
            svc_path = _module_file(root, svc)
            if svc_path is None or "run" not in _toplevel_names(_parse(svc_path))[0]:
                errors.append(f"{mod}: names service {svc!r}, which has no module with a run()")
            else:
                queue.append(svc)
        for base, names, optional in _imports(tree, mod, path.name == "__init__.py"):
            top = base.split(".")[0]
            if mod.startswith("scripts.") and top not in pkgs and _module_file(root, f"scripts.{base}"):
                base, top = f"scripts.{base}", "scripts"  # a script run directly imports its siblings bare
            if top not in pkgs:
                if top and not optional and not _third_party_ok(top):
                    errors.append(f"{mod}: third-party package {top!r} is not installed")
                continue
            if names is None:
                if _module_file(root, base) is None:
                    errors.append(f"{mod}: imports missing module {base}")
                else:
                    queue.append(base)
                continue
            base_path = _module_file(root, base)
            if base_path is None:
                errors.append(f"{mod}: imports from missing module {base}")
                continue
            queue.append(base)
            defined, dynamic = _toplevel_names(_parse(base_path))
            for name in names:
                if name == "*":
                    continue
                if _module_file(root, f"{base}.{name}") is not None:
                    queue.append(f"{base}.{name}")
                elif name not in defined and not dynamic:
                    errors.append(f"{mod}: imports {name!r} from {base}, which does not define it")
    return sorted(set(errors))


def _launcher_targets(root: Path) -> set[str]:
    mods: set[str] = set()
    for f in [*root.glob("*.bat"), *root.glob("*.sh")]:
        text = f.read_text(encoding="utf-8", errors="replace")
        mods.update(f"scripts.{m}" for m in re.findall(r"scripts[\\/](\w+)\.py", text))
        for m in re.findall(r"-m\s+([A-Za-z_][\w.]*)", text):
            is_pkg = (root.joinpath(*m.split(".")) / "__init__.py").is_file()
            mods.add(f"{m}.__main__" if is_pkg else m)
    return mods


def system_entry_points(root: Path = ROOT) -> list[str]:
    entries = {f"scripts.{s}" for s in TASK_SCRIPTS}
    entries |= _launcher_targets(root)
    entries |= {f"scripts.{p.stem}" for p in (root / "scripts").glob("*.py") if p.stem != "__init__"}
    entries.add("webull_web.feed_export")
    for pkg in KEPT_MCP:
        entries |= {f"{pkg}.{m}" for m in ("__main__", "server") if _module_file(root, f"{pkg}.{m}")}
    return sorted(entries)


# Imports that must not exist anywhere in the repo any more (spec 2026-09-28 systems-only §6).
FORBIDDEN_IMPORTS = ("fastapi", "uvicorn", "httpx", "anthropic", "webull_web.app", "webull_web.routers",
                     "webull_web.copilot", "webull_analysis_mcp", "snaptrade_mcp", "snaptrade_api")
_MODULE_STRING = re.compile(
    r"(?<![\w./\\-])((?:webull_web|webull_api|scripts|snaptrade_api|[a-z_]+_mcp)(?:\.[A-Za-z_]\w*)+)")
# Packages deleted whole (2026-09-28 archive): any string naming one, anywhere in its dotted path, is a
# dangling by-name reference no matter whether a same-named directory happens to exist (it never does once
# this task lands) -- checked before the generic `pkgs` membership test below.
RETIRED_PACKAGES = ("webull_analysis_mcp", "snaptrade_mcp", "snaptrade_api")


def _all_repo_modules(root: Path):
    """(module name, path, is_package) for every .py in the repo outside the skipped dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".venv")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = Path(dirpath) / fn
            parts = list(path.relative_to(root).with_suffix("").parts)
            is_pkg = parts[-1] == "__init__"
            yield ".".join(parts[:-1] if is_pkg else parts), path, is_pkg


def forbidden_imports(root: Path) -> list[str]:
    hits = []
    for mod, path, is_pkg in _all_repo_modules(root):
        for base, names, _optional in _imports(_parse(path), mod, is_pkg):
            for full in [base] + [f"{base}.{n}" for n in names or []]:
                if any(full == bad or full.startswith(bad + ".") for bad in FORBIDDEN_IMPORTS):
                    hits.append(f"{mod}: imports {full}")
    return sorted(set(hits))


def _is_module_attribute(root: Path, name: str) -> bool:
    """`a.b.attr` naming something module `a.b` has: any attribute of a plain module, but under a PACKAGE only
    what its `__init__` defines -- anything else there is a submodule, and it is gone."""
    parent, _, attr = name.rpartition(".")
    path = _module_file(root, parent)
    if path is None:
        return False
    if path.name != "__init__.py":
        return True
    defined, dynamic = _toplevel_names(_parse(path))
    return dynamic or attr in defined


def dangling_module_strings(root: Path) -> list[str]:
    """String literals in non-test code naming a repo module (a `-m` target, an importlib name, a docstring
    pointer) that resolves to nothing -- a by-name link the import closure cannot follow."""
    pkgs = _repo_packages(root)
    hits = []
    for mod, path, _is_pkg in _all_repo_modules(root):
        if mod.split(".")[0] == "tests":
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for m in _MODULE_STRING.finditer(node.value):
                    name = m.group(1)
                    top = name.split(".")[0]
                    if top in RETIRED_PACKAGES:
                        hits.append(f"{mod}: a string names {name}, a retired package")
                        continue
                    if top not in pkgs:
                        continue
                    if _module_file(root, name) or _is_module_attribute(root, name):
                        continue
                    hits.append(f"{mod}: a string names {name}, which resolves to nothing")
    return sorted(set(hits))


def test_nothing_imports_the_retired_stack():
    assert forbidden_imports(ROOT) == []


def test_no_string_names_a_missing_module():
    assert dangling_module_strings(ROOT) == []


def test_forbidden_imports_sees_from_imports(tmp_path):
    root = _tree(tmp_path, {"webull_web/__init__.py": "", "webull_web/x.py": "from webull_web import app\n"})
    assert forbidden_imports(root) == ["webull_web.x: imports webull_web.app"]


def test_dangling_strings_flag_a_deleted_module(tmp_path):
    root = _tree(tmp_path, {"webull_web/__init__.py": "",
                            "webull_web/x.py": 'CMD = ["python", "-m", "webull_web.gone"]\nOK = "webull_web.x"\n'})
    assert dangling_module_strings(root) == ["webull_web.x: a string names webull_web.gone, which resolves to nothing"]


def test_dangling_strings_flag_a_retired_package(tmp_path):
    root = _tree(tmp_path, {"webull_web/__init__.py": "",
                            "webull_web/x.py": 'A = "snaptrade_mcp.server"\nB = "snaptrade_api.env"\n'})
    assert dangling_module_strings(root) == [
        "webull_web.x: a string names snaptrade_api.env, a retired package",
        "webull_web.x: a string names snaptrade_mcp.server, a retired package",
    ]


def test_dangling_strings_accept_module_attributes(tmp_path):
    root = _tree(tmp_path, {"webull_web/__init__.py": "FOO = 1\n",
                            "webull_web/x.py": "def CMD(): pass\n",
                            "webull_web/y.py": 'A = "webull_web.FOO"\nB = "webull_web.x.CMD"\n'})
    assert dangling_module_strings(root) == []


# ---------------------------------------------------------------------------------------------- the guard

def test_the_task_scripts_and_kept_connectors_exist():
    for s in TASK_SCRIPTS:
        assert (ROOT / "scripts" / f"{s}.py").is_file(), s
    for launcher in TASK_LAUNCHERS:
        assert (ROOT / launcher).is_file(), launcher
    for pkg in KEPT_MCP:
        assert _module_file(ROOT, f"{pkg}.__main__") or _module_file(ROOT, f"{pkg}.server"), pkg


def test_every_system_entry_point_resolves():
    assert check_closure(ROOT, system_entry_points()) == []


# ------------------------------------------------------------------------------- the guard's own checks

def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_guard_reports_a_missing_module(tmp_path):
    root = _tree(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": "from pkg.gone import x\n"})
    assert check_closure(root, ["pkg.a"]) == ["pkg.a: imports from missing module pkg.gone"]


def test_guard_reports_a_name_the_module_no_longer_defines(tmp_path):
    root = _tree(tmp_path, {"pkg/__init__.py": "", "pkg/b.py": "def kept(): pass\n",
                            "pkg/a.py": "from pkg.b import moved\n"})
    assert check_closure(root, ["pkg.a"]) == ["pkg.a: imports 'moved' from pkg.b, which does not define it"]


def test_guard_checks_repo_imports_inside_functions_and_try_blocks(tmp_path):
    root = _tree(tmp_path, {"pkg/__init__.py": "",
                            "pkg/a.py": ("def f():\n    try:\n        from pkg.gone import g\n"
                                         "    except ImportError:\n        return None\n")})
    assert check_closure(root, ["pkg.a"]) == ["pkg.a: imports from missing module pkg.gone"]


def test_guard_allows_an_optional_third_party_import(tmp_path):
    root = _tree(tmp_path, {"pkg/__init__.py": "",
                            "pkg/a.py": "try:\n    import not_a_real_pkg_xyz\nexcept ImportError:\n    pass\n"})
    assert check_closure(root, ["pkg.a"]) == []


def test_guard_reports_a_missing_third_party_package(tmp_path):
    root = _tree(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": "import not_a_real_pkg_xyz\n"})
    assert check_closure(root, ["pkg.a"]) == ["pkg.a: third-party package 'not_a_real_pkg_xyz' is not installed"]


def test_guard_follows_by_name_services(tmp_path):
    root = _tree(tmp_path, {"webull_web/__init__.py": "",
                            "webull_web/runner_cli.py": 'PAPER_SUITE = [("A", "svc_a"), ("B", "svc_gone")]\n',
                            "webull_web/svc_a.py": "def run(force=False): pass\n"})
    assert check_closure(root, ["webull_web.runner_cli"]) == [
        "webull_web.runner_cli: names service 'webull_web.svc_gone', which has no module with a run()"]
