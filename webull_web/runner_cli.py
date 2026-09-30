"""Shared CLI wrapper for the Windows-scheduled paper-runner entry scripts. Each ``scripts/<x>.py``
is a thin shim that calls ``run_cli(<service module>, <LABEL>, <argparse description>)`` — this
centralizes the stdout hardening, argparse, repo-env load, and the result -> exit-code mapping the
four scripts duplicated byte-for-byte.

Import-safe with NO env: only stdlib is imported at module level; ``load_repo_env`` and the runner
service are imported lazily INSIDE run_cli (after argparse), preserving the scripts' original order
(env loaded, SDK logging hardened, THEN the webull_web service imported). Nothing here imports an
order-placement path (the flows step reads order history only — see its own guard test).
"""
from __future__ import annotations

import argparse
import importlib
import sys
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo


def _reconfigure_stdio() -> None:
    # The one-line summary contains an em-dash; redirected logs default to cp1252 on Windows, so a
    # successful run must not crash on print. Best-effort — some streams can't be reconfigured.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _exit_code(report: dict) -> int:
    """0 clean · 1 hard fail (data outage / all placements failed) · 2 ran with soft per-symbol errors."""
    if report.get("result") == "error":
        return 1
    return 0 if not report.get("errors") else 2


def run_cli(service_module: str, label: str, description: str, *,
            argv: list[str] | None = None,
            run_fn: Callable | None = None,
            env_fn: Callable | None = None) -> int:
    """Drive one scheduled paper runner and return its process exit code. `service_module` is the
    ``webull_web`` submodule name (its ``run(force=...)`` is called); `label` prefixes the
    SKIPPED/FAILED stderr lines; `description` is the argparse help text. `run_fn`/`env_fn` are
    injection seams for tests (default: the real service run + load_repo_env)."""
    _reconfigure_stdio()
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("command", nargs="?", default="run", choices=["run"])
    ap.add_argument("--force", action="store_true", help="ignore the same-day guard")
    args = ap.parse_args(argv)

    if env_fn is None:
        from webull_mcp.env import load_repo_env  # loads <repo>/.env, chdirs to repo, hardens SDK logging
        env_fn = load_repo_env
    env_fn()

    from webull_api.market_data import MarketDataNotEntitledError
    if run_fn is None:
        run_fn = importlib.import_module(f"webull_web.{service_module}").run
    try:
        report = run_fn(force=args.force)
    except MarketDataNotEntitledError as e:
        print(f"{label} SKIPPED: market data not entitled — {e}", file=sys.stderr)
        return 1
    except Exception as e:  # token/2FA lapse, network — clean one-liner, non-zero
        print(f"{label} FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(report.get("summary", ""))
    return _exit_code(report)


# The whole suite, in run order (equity entries+exits -> equity stops -> the shared-pool
# shadow accounting (reads both paper books, drives neither) -> option exits ->
# option entries -> Lab-proven strategies -> weekly strategy scorecard -> net-liq snapshot ->
# REAL-book RSI2 (queue-only, OFF by default) -> owner-flow detection (needs the snapshot's cash)
# -> scanner efficacy ledger -> entry judgment (shadow)); the note is its own later task (NOTE_STEP). Each service has its
# own lock + same-day guard + run-log.
#
# SIMPLIFIED 2026-09-29 (owner: one real strategy, RSI2 at S6 sizing). Only the real book's chain
# runs; the paper RSI2 step is parked in PARKED_SUITE (move the row back to re-enable it, and re-add its key
# to watchdog_service.EXPECTED_KEYS); the other old steps were archived (see below).
PAPER_SUITE = [
    ("Net-liq", "netliq_snapshot_service"),
    ("RSI2-real", "rsi2_real_service"),
    ("Flows", "flows_service"),
]

# The nightly note runs ALONE, after the 18:15 autopilot backstop (task "Webull Nightly Note", 18:25 ET,
# scripts/nightly_note.py -> run_suite(runners=NOTE_STEP)), so the stops the evening autopilot places are
# in the note. Same EOD-window guard + network gate as the suite. Moved out of PAPER_SUITE 2026-09-29.
NOTE_STEP = [
    ("Note", "manager_note_service"),
]

PARKED_SUITE = [
    ("RSI2", "rsi2_service"),   # the paper RSI2 book; its code stays because rsi2_real_service imports it
]
# ARCHIVED 2026-09-29 (RSI2-only): the paper-EOD, pool-shadow, options, proven, scorecard, scan and judgment
# steps were deleted from the tree — restore from git tag archive/pre-rsi2-only-2026-09-29 (docs/ARCHIVE.md).


_EOD_WINDOW_START_HOUR = 16  # ET; daily bars exist and the same-day guard stamp is legitimate


def run_suite(*, argv: list[str] | None = None, runners=PAPER_SUITE,
              env_fn: Callable | None = None, run_fns: dict | None = None,
              now_fn: Callable | None = None, gate_fn: Callable | None = None) -> int:
    """Run a suite (default the runners in PAPER_SUITE, in order; the nightly note passes NOTE_STEP) in one session, loading the repo env
    once. Each runner has its own lock + same-day guard + run-log, so this just SEQUENCES them; a
    runner that raises never stops the rest. Outside the EOD window (before 4 PM ET) the suite
    exits 0 WITHOUT invoking any runner — a Task Scheduler catch-up firing at boot pre-market must
    not stamp today's run-log and block the real evening run (--force overrides). Inside the
    window, the network gate runs FIRST: the scheduled task wakes the PC and this fires before
    Wi-Fi/DNS reconnects (2026-08-26/27: every bar fetch died on `getaddrinfo` inside a ~10s
    window), so wait for the API host to be reachable; on gate timeout the runners still run —
    a real outage must produce honest error rows + the note, never a silent skip. Exit code: 1 if
    any runner hard-errored (or raised), else 2 if any had soft per-symbol errors, else 0.
    run_fns is a {module: run} injection seam for tests; now_fn/gate_fn likewise for the ET
    clock and the network gate."""
    _reconfigure_stdio()
    ap = argparse.ArgumentParser(description="Run a Webull runner suite (default PAPER_SUITE; the nightly note is NOTE_STEP).")
    ap.add_argument("--force", action="store_true", help="ignore each runner's same-day guard")
    args = ap.parse_args(argv)
    now = (now_fn or (lambda: datetime.now(ZoneInfo("America/New_York"))))()
    if not args.force and now.hour < _EOD_WINDOW_START_HOUR:
        print(f"Suite: outside the EOD window ({now:%H:%M} ET, opens 16:00) — no-op, "
              "run-logs untouched. Use --force to run anyway.")
        return 0
    if gate_fn is None:
        from webull_api.net_gate import wait_for_network
        gate_fn = wait_for_network
    waited = gate_fn()
    if waited is None:
        print("Suite: network still unreachable after the gate deadline — running anyway "
              "(runners will record errors).")
    elif waited:
        print(f"Suite: network came up after {waited:.0f}s (wake-from-standby race).")
    if env_fn is None:
        from webull_mcp.env import load_repo_env
        env_fn = load_repo_env
    env_fn()
    from webull_api.market_data import MarketDataNotEntitledError
    worst = 0
    for label, mod in runners:
        try:
            run = (run_fns or {}).get(mod) or importlib.import_module(f"webull_web.{mod}").run
            report = run(force=args.force)
        except MarketDataNotEntitledError as e:
            print(f"[{label}] SKIPPED: market data not entitled — {e}", file=sys.stderr)
            worst = max(worst, 1)
            continue
        except Exception as e:  # a broken runner must not abort the whole suite
            print(f"[{label}] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            worst = max(worst, 1)
            continue
        print(f"[{label}] {report.get('summary', '')}")
        if report.get("result") == "error":
            worst = max(worst, 1)
        elif report.get("errors"):
            worst = max(worst, 2)
    return worst
