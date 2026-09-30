"""Dead-man's watchdog for the evening paper suite — a SEPARATE scheduled task that
answers one question: did the suite report today? If it lives inside the suite it shares
the failure it exists to detect. Reads the run-log (the ground truth of what completed —
Task Scheduler results and heartbeat files were rejected in the spec); alerts via the
existing ntfy topic. This is the one runner that fails LOUD: an unreadable run-log is
itself the alert, never a quiet fallback. Read-only + push; imports no trading path.
Silence when healthy — the manager's note is the positive signal."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

_KEY = "watchdog"
# The suite fires 5:30 PM ET; by 18:30 the note must exist. The window also stops a Task
# Scheduler catch-up firing at morning boot from seeing an empty run-log and false-alarming.
WINDOW_START = (18, 30)


def _ping(url: str) -> bool:
    """GET the healthchecks dead-man URL. One attempt, never raises — the external layer
    answers "did the watchdog run tonight?", it must not break the run that answers it."""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=10):
            return True
    except Exception:
        return False

# The live suite runners' run-log keys + the 5:45 PM real-money autopilot's heartbeat
# (scripts/autopilot.py appends it; NOT a suite runner, hence no manager-note label).
# test_watchdog_service cross-checks this against manager_note_service.RUNNER_LABELS
# + {netliq, note, autopilot} so suite/watchdog drift fails a test.
# SIMPLIFIED 2026-09-29 (owner: RSI2-only): the parked suite steps (runner_cli.PARKED_SUITE),
# ibs_book_paper and session_grid_paper were removed with their tasks. Re-add a key only when its
# runner/task is re-enabled — while listed, every night reads "degraded".
EXPECTED_KEYS = ("netliq", "rsi2_real", "flows", "note", "autopilot")
# DELIBERATELY not in EXPECTED_KEYS: bench_feeder. It is R&D, not the suite — a missed night must
# never read "dead"/"degraded" — and it starts 17:35 with up to a 90-min budget, so on a full night
# its row can still be absent when this 19:00 run looks. It gets its own lane instead: `advisory`
# checks the PREVIOUS weekday's row (found 2026-09-18: three nights of the old 19:10 task woke the
# PC, froze in Modern Standby before the script could hold a power request, and were killed at the
# 2h limit when the owner came back — no digest, no run-log row, nobody told). A missing row pushes
# an ADVISORY and rides in the summary; the suite verdict and the exit code are untouched.
ADVISORY_KEYS: tuple[str, ...] = ()   # bench_feeder archived 2026-09-29 (tag archive/pre-rsi2-only-2026-09-29)


def lookback_target(now) -> str:
    """The most recent weekday strictly before `now` (ISO date). Pure."""
    from datetime import timedelta
    d = now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def lookback_covered(run_rows: list[dict], target: str) -> bool:
    """Pure. A target evening is covered when:
      (a) a same-day watchdog row exists that is NOT itself a back-alert (no `looked_back`)
          AND fired at or after 18:30 ET — a forced/boot-time 10 AM watchdog row does NOT
          count as evening coverage;
      (b) a target-day note row exists AND EITHER a target-day `autopilot` row also exists
          OR no `autopilot` row exists anywhere in run_rows (transition guard: history from
          before the autopilot heartbeat shipped must not false-alarm) — a note row ALONE is
          no longer sufficient once the heartbeat is in play, since "note written, PC died
          before the 5:45 autopilot" is exactly the gap this covers;
      (c) a prior look-back already alerted for it (any watchdog row with
          looked_back == target) — a back-alert row covers only the day named in its own
          `looked_back` field, so consecutive-day outages must each alert.
    """
    has_target_day_note = False
    has_target_day_autopilot = False
    has_any_autopilot = False
    for r in run_rows:
        key = str(r.get("key"))
        ts = str(r.get("ts", ""))
        ts_date = ts[:10]
        if key == "autopilot":
            has_any_autopilot = True
            if ts_date == target:
                has_target_day_autopilot = True
        if ts_date == target and key == "note":
            has_target_day_note = True
        # (a) same-day, non-back-alert watchdog row covers only from 18:30 ET onward
        if (ts_date == target and key == _KEY and not r.get("looked_back")
                and ts[11:16] >= "18:30"):
            return True
        # (c) any watchdog row whose looked_back names this target already alerted it
        if key == _KEY and str(r.get("looked_back", "")) == target:
            return True
    # (b) note-row coverage, gated by the autopilot heartbeat (or its absence, pre-rollout)
    return has_target_day_note and (has_target_day_autopilot or not has_any_autopilot)


def _autopilot_gap(run_rows: list[dict], target: str) -> bool:
    """Pure, message-selection only: True when the target day's note row exists but its
    autopilot row is specifically the missing piece (autopilot rows exist elsewhere, so the
    heartbeat is live — this is a real gap, not pre-rollout history). Distinguishes the
    "note written, PC died before 5:45" back-alert wording from the generic dark-evening one."""
    has_target_day_note = False
    has_target_day_autopilot = False
    has_any_autopilot = False
    for r in run_rows:
        key = str(r.get("key"))
        ts_date = str(r.get("ts", ""))[:10]
        if key == "autopilot":
            has_any_autopilot = True
            if ts_date == target:
                has_target_day_autopilot = True
        if ts_date == target and key == "note":
            has_target_day_note = True
    return has_target_day_note and has_any_autopilot and not has_target_day_autopilot


def advisory(run_rows: list[dict], target: str) -> list[str]:
    """Pure. The ADVISORY_KEYS with no run-log row dated `target` (the previous weekday, per
    `lookback_target`). Any row counts — ok, error, skipped (KILL file) — because each proves the
    task ran; only silence is the finding."""
    seen = {str(r.get("key")) for r in run_rows if str(r.get("ts", ""))[:10] == target}
    return [k for k in ADVISORY_KEYS if k not in seen]


def assess(run_rows: list[dict], today: str) -> dict:
    """Pure. States in severity order: dead (note missing) > autopilot-silent
    (only the real-money runner missing) > degraded (note present but another runner missing)
    > ok (all reported). A row with result 'error' still counts as reported — the watchdog
    detects ABSENCE of reporting; the note itself surfaces internal errors nightly."""
    seen = {str(r.get("key")) for r in run_rows if str(r.get("ts", ""))[:10] == today}
    missing = [k for k in EXPECTED_KEYS if k not in seen]
    if "note" not in seen:
        return {"state": "dead", "missing": missing,
                "detail": "no manager's-note run-log row today — "
                          "the evening suite never finished (or never started)"}
    if "autopilot" in missing:
        return {"state": "autopilot-silent", "missing": missing,
                "detail": "the 5:45 PM REAL-MONEY autopilot never reported today"
                          + ((" (also missing: "
                              + ", ".join(k for k in missing if k != "autopilot") + ")")
                             if len(missing) > 1 else "")}
    if missing:
        return {"state": "degraded", "missing": missing,
                "detail": "suite finished but these runners never reported: " + ", ".join(missing)}
    return {"state": "ok", "missing": [],
            "detail": f"all {len(EXPECTED_KEYS)} suite runners reported today"}


def _lookback(now, push_fn) -> int:
    """Boot-time catch-up path: instead of the old silent no-op, back-alert if the most
    recent weekday evening went dark UNOBSERVED (no suite report, no watchdog row, no prior
    back-alert). Covered -> quiet exit 0 with no row (no spam from routine boots)."""
    from webull_api import run_log

    from . import push as push_mod
    target = lookback_target(now)
    rows = None
    try:
        rows = run_log.load()
    except Exception as e:
        detail = (f"back-alert: run-log unreadable ({type(e).__name__}) — "
                  f"cannot prove the suite ran on {target}")
    if rows is not None:
        if lookback_covered(rows, target):
            print(f"Watchdog: look-back — {target} was covered; no-op.")
            return 0
        if _autopilot_gap(rows, target):
            detail = ("back-alert: the 5:45 PM real-money autopilot never reported on "
                      f"{target} (the suite's note exists) — PC likely off before 5:45")
        else:
            detail = (f"back-alert: the evening suite never reported on {target} and the "
                      "watchdog never ran — PC likely off")
    url = os.environ.get("WEBULL_MANAGER_NOTE_NTFY", "").strip()
    pushed: bool | str = "off"
    if url:
        pushed = (push_fn or push_mod.push_text)(
            detail, title="Suite watchdog - BACK-ALERT", url=url,
            priority="high", tags="rotating_light")
    summary = f"Watchdog: back-alert {target} · " + {
        True: "pushed", False: "push FAILED",
        "off": "ntfy unconfigured — alert undeliverable"}[pushed]
    try:
        run_log.append([{"key": _KEY, "ts": now.isoformat(), "result": "error",
                         "summary": summary, "looked_back": target, "placed": 0,
                         "errors": [detail]}])
    except Exception:
        _log.exception("watchdog: run-log append failed")
    print(summary)
    return 1


def run(force: bool = False, *, now_fn=None, push_fn=None, ping_fn=None) -> int:
    """0 = healthy or deliberate no-op; 1 = alert state (pushed if ntfy is configured)."""
    from webull_api import run_log

    from . import push as push_mod
    now = (now_fn or (lambda: datetime.now(ZoneInfo("America/New_York"))))()
    today = now.strftime("%Y-%m-%d")
    if not force:
        if now.weekday() >= 5 or (now.hour, now.minute) < WINDOW_START:
            # A boot-time catch-up firing (or a weekend catch-up of a missed Friday):
            # look BACK instead of no-opping, else a dark evening is never heard about.
            return _lookback(now, push_fn)
    adv_target = lookback_target(now)
    adv_missing: list[str] = []
    try:
        rows = run_log.load()
        verdict = assess(rows, today)
        adv_missing = advisory(rows, adv_target)
    except Exception as e:
        verdict = {"state": "dead", "missing": list(EXPECTED_KEYS),
                   "detail": f"run-log unreadable ({type(e).__name__}) — "
                             "cannot prove the suite ran"}
    ok = verdict["state"] == "ok"
    url = os.environ.get("WEBULL_MANAGER_NOTE_NTFY", "").strip()
    pushed: bool | str = "off"
    do_push = push_fn or push_mod.push_text
    if not ok and url:
        pushed = do_push(verdict["detail"] + (
            "\nmissing: " + ", ".join(verdict["missing"]) if verdict["missing"] else ""),
            title=f"Suite watchdog - {verdict['state'].upper().replace('-', ' ')}", url=url,
            priority="high", tags="rotating_light")
    elif ok and adv_missing and url:
        # One push per evening: a suite alert already names the night (the advisory rides in its
        # summary); only a healthy suite gets the separate, lower-key advisory.
        try:
            do_push(f"lab advisory: {', '.join(adv_missing)} never reported on {adv_target} - the "
                    "nightly develop batch did not run or was killed. Check "
                    f"data\\bench\\feeder\\{adv_target}.md and the 'Webull Bench Feeder' task's last result.",
                    title="Suite watchdog - ADVISORY", url=url,
                    priority="low", tags="information_source")
        except Exception:
            _log.exception("watchdog: advisory push failed")
    # External dead-man: an EVENING assessment (weekday, in-window — forced or scheduled)
    # is the only thing allowed to satisfy the ~20:00 ET healthchecks deadline. Any verdict
    # pings: the ping means "the watchdog ran", not "the suite is healthy" (ntfy above
    # carries health). Look-back / weekend / pre-window runs never reach this line unforced.
    ping_url = os.environ.get("WEBULL_WATCHDOG_HEALTHCHECK_URL", "").strip()
    ping_failed = False
    if ping_url and now.weekday() < 5 and (now.hour, now.minute) >= WINDOW_START:
        ping_failed = not (ping_fn or _ping)(ping_url)
    bits = [f"Watchdog: {verdict['state']}"]
    if verdict["missing"]:
        bits.append("missing " + ",".join(verdict["missing"]))
    if adv_missing:
        bits.append(f"advisory {','.join(adv_missing)} missing {adv_target}")
    if not ok:
        bits.append({True: "pushed", False: "push FAILED",
                     "off": "ntfy unconfigured — alert undeliverable"}[pushed])
    if ping_failed:
        bits.append("ping FAILED")
    summary = " · ".join(bits)
    try:
        run_log.append([{"key": _KEY, "ts": now.isoformat(), "result": "ok" if ok else "error",
                         "summary": summary, "placed": 0,
                         "errors": [] if ok else [verdict["detail"]]}])
    except Exception:
        _log.exception("watchdog: run-log append failed")
    print(summary)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    ap = argparse.ArgumentParser(
        description="Dead-man's watchdog: alert when the evening suite did not report.")
    ap.add_argument("--force", action="store_true", help="ignore the weekday/window guards")
    args = ap.parse_args(argv)
    from webull_mcp.env import load_repo_env  # loads <repo>/.env, hardens SDK logging
    load_repo_env()
    return run(force=args.force)
