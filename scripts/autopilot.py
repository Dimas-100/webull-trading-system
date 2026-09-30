"""Autopilot operator console - check, run, halt, or resume the autonomous trader.

  python scripts/autopilot.py status    # read-only: config + kill-switch state + today's log (DEFAULT)
  python scripts/autopilot.py run        # run ONE cycle (places only if ENABLED and the gate allows)
  python scripts/autopilot.py halt       # create the kill-file -> autopilot places NOTHING until resumed
  python scripts/autopilot.py resume     # remove the kill-file

The kill-file, daily state, and audit log all live under <repo>/autopilot/ (or WEBULL_AUTOPILOT_DIR).
`run` is non-interactive by design (a scheduler can call it) and is a NO-OP unless
WEBULL_AUTOPILOT_ENABLED is set. Nothing here weakens the submit gate - `run` goes through the same
gate.authorize -> trading.place(confirm=True) path, so it only places what the gate allows.

Output is intentionally ASCII-only (Windows consoles default to cp1252 and choke on box-drawing).
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_mcp.env import load_repo_env  # loads <repo>/.env, chdirs to repo, hardens SDK logging


def _cfg():
    from webull_api.autopilot.config import AutopilotConfig
    return AutopilotConfig.from_env()


def _log_summary() -> str:
    from webull_api.autopilot import paths
    f = paths.autopilot_dir() / "log" / f"{datetime.now().date().isoformat()}.jsonl"
    if not f.exists():
        return "today: no audit log yet"
    placed = skipped = errors = 0
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("placed") is True:
            placed += 1
        elif e.get("error"):
            errors += 1
        else:
            skipped += 1
    return f"today: {placed} placed / {skipped} skipped / {errors} error(s)  ({f})"


def _heartbeat(report: dict) -> None:
    """Append the run-log heartbeat row the suite watchdog's autopilot coverage detects
    (spec 2026-07-28-watchdog-extension). Observability only, best-effort: a run-log
    failure must never break the operator output. A run that CRASHES before this line
    writes nothing — that absence is exactly what the watchdog alerts on."""
    try:
        from webull_api import run_log
        errors = report.get("errors") or []
        run_log.append([{"key": "autopilot",
                         "ts": datetime.now(ZoneInfo("America/New_York")).isoformat(),
                         "result": "error" if errors else "ok",
                         "summary": report.get("message", ""),
                         "placed": len(report.get("placed") or []), "errors": errors}])
    except Exception:
        pass


def cmd_status(cfg) -> int:
    from webull_api.autopilot import state as state_mod
    kill = state_mod.kill_switch_active(cfg.kill_file)
    print("=== Autopilot status ===")
    print(f"  ENABLED:      {cfg.enabled}   {'(ARMED)' if cfg.enabled else '(off - nothing will place)'}")
    print(f"  caps:         ${cfg.max_notional:.0f}/order | max {cfg.max_positions} pos | "
          f"{cfg.max_orders_per_day}/day | daily-loss halt ${cfg.daily_loss_halt:.0f}")
    print(f"  risk-off cap: {cfg.max_positions_risk_off} positions when SPY < 200SMA")
    print(f"  window:       {cfg.windows} (local time)")
    print(f"  kill-file:    {cfg.kill_file}")
    print(f"  KILL:         {'ACTIVE - HALTED' if kill else 'clear'}")
    print(f"  notify:       {'(configured)' if cfg.notify else '(stdout only)'}")
    print(f"  {_log_summary()}")
    print("=" * 24)
    if cfg.enabled and not kill:
        print("  ARMED + not halted: a `run` inside the window will place REAL orders within the caps.")
    return 0


def cmd_run(cfg) -> int:
    from webull_api.autopilot import run as run_mod
    # The task's wake timer can be what wakes the PC, firing this before Wi-Fi/DNS has
    # reconnected (2026-08-26/27 getaddrinfo outages). Wait for the API host; on timeout run
    # anyway - autopilot fails closed without data, and skipping would also drop the
    # heartbeat row the watchdog counts on.
    from webull_api.net_gate import wait_for_network
    waited = wait_for_network()
    if waited is None:
        print("network still unreachable after the gate deadline - running anyway (fails closed)")
    elif waited:
        print(f"network came up after {waited:.0f}s (wake-from-standby race)")
    if not cfg.enabled:
        print("Autopilot is DISABLED - this run is a no-op (nothing places). "
              "Set WEBULL_AUTOPILOT_ENABLED=true to arm.")
    report = run_mod.run(cfg=cfg)  # same cfg the status check used, not a fresh re-read
    _heartbeat(report)
    print(report.get("message", ""))
    for p in report.get("placed", []):
        print(f"  PLACED  {p.get('side')} {p.get('symbol')}  ({p.get('source')})")
    for s in report.get("skipped", []):
        print(f"  skipped {s.get('side')} {s.get('symbol')}  [{s.get('layer')}] {s.get('reason')}")
    for e in report.get("errors", []):
        print(f"  ERROR   {e}")
    return 0


def cmd_halt(cfg) -> int:
    p = Path(cfg.kill_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("halt\n", encoding="utf-8")
    print(f"HALTED - created {p}\n"
          "Autopilot will place NOTHING until you run:  python scripts/autopilot.py resume")
    return 0


def cmd_resume(cfg) -> int:
    p = Path(cfg.kill_file)
    existed = p.exists()
    if existed:
        p.unlink()
    print(f"RESUMED - {('removed ' + str(p)) if existed else 'no kill-file was present'}.\n"
          "Autopilot may place again within its caps/window (only when ENABLED and a run is invoked).")
    return 0


_COMMANDS = {"status": cmd_status, "run": cmd_run, "halt": cmd_halt, "resume": cmd_resume}


def main() -> int:
    ap = argparse.ArgumentParser(description="Autopilot operator console.")
    ap.add_argument("command", nargs="?", default="status", choices=list(_COMMANDS),
                    help="status (default) | run | halt | resume")
    args = ap.parse_args()
    load_repo_env()  # <repo>/.env -> os.environ (WEBULL_AUTOPILOT_* + creds); chdir repo; SDK logging safe
    return _COMMANDS[args.command](_cfg())


if __name__ == "__main__":
    raise SystemExit(main())
