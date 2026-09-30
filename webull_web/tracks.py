"""The track registry — the ONE place the money tracks are named and described
(spec docs/superpowers/specs/2026-09-07-program-map-and-today-design.md §2, §3.1).
Pure data: no I/O, no imports beyond dataclasses. Consumers: the program-map renderer
(webull_web/program_map.py) and the kestrel feed (labels, TRACKS — webull_web/feed_service.py).
Nothing else is renamed — run-log keys, task names, env vars, data folders keep their existing
names."""
from __future__ import annotations

from dataclasses import dataclass

# The real-account carve-out for a hand day trade. The $400 hand book was retired 2026-09-21
# ("let's just get rid of the 400 book"); nothing is carved out of the Individual Cash account.
DAY_CARVE_OUT = 0.0

# The Webull Autopilot morning trigger (ET, weekdays) — the run that executes confirmed RSI2 exits
# at the open. Owner 2026-09-13: 09:35 → 09:31 (one minute of margin after the open; the gate
# window in .env starts here too). Mirror any change in scripts/set-autopilot-morning-trigger.ps1.
AUTOPILOT_MORNING = "09:31"

MONEY_WORD = {"paper": "paper", "real": "real", "none": "R&D"}

_LABELS = {
    "swing_rsi2": "Swing·RSI2",
    "swing_pullback": "Swing·Pullback",
    "all": "All",
}


def label_for(key: str) -> str:
    return _LABELS.get(key, key)


# The plans (kestrel shows them via the feed). RSI2-only since 2026-09-29: the IBS ETF, day ORB, Lab and
# options tracks were archived at git tag archive/pre-rsi2-only-2026-09-29 (docs/ARCHIVE.md).
PLAN_TABS = ("swing_rsi2", "swing_pullback")


def tab_states(tracks=None) -> dict[str, dict]:
    """Per plan tab: RUNNING if ANY of its books runs, else PAUSED, plus the dated notes. Pure —
    the tab badge, the program map grid and the Today card all read the same registry."""
    tracks = TRACKS if tracks is None else tracks
    out: dict[str, dict] = {}
    for key in PLAN_TABS:
        cells = [t for t in tracks if t.key == key]
        running = any(t.state == "running" for t in cells)
        note = " · ".join(f"{MONEY_WORD[t.money]}: {t.state_note}" for t in cells if t.state_note)
        out[key] = {"state": "running" if running else "paused", "note": note}
    return out


@dataclass(frozen=True)
class Track:
    key: str
    label: str
    money: str          # "paper" | "real" | "none"
    summary: str        # one line for the grid cell
    book: str           # human figure
    book_source: str    # which file/env wins for that figure
    plan_doc: str       # repo path (must exist)
    universe: str
    entry: str
    exit: str
    schedule: str
    data: tuple[str, ...]
    journal: str
    kill_switch: str
    review: str
    state: str = "running"    # "running" | "paused" — what the program map and the kestrel feed read (owner 2026-09-14: "I don't want to get confused about what plans are running")
    state_note: str = ""      # why, dated — carried by tab_states() and visibility.paused_plans()


TRACKS: tuple[Track, ...] = (
    Track(
        key="swing_rsi2", label=label_for("swing_rsi2"), money="paper",
        state="paused", state_note="SIMPLIFIED 2026-09-29 (owner: RSI2-only): the paper RSI2 step is parked (runner_cli.PARKED_SUITE)",
        summary="RSI2 runner in the 17:30 suite; $100k simulator, $6k lots, 6 slots; the proof bar",
        book="$100k simulator · $6k per lot · 6 slots · $20k cash floor",
        book_source="webull_api/strategy/rsi2.py Rsi2Config (dollars_per_signal, max_lots, cash_floor); data/paper/default.json starting_cash",
        plan_doc="docs/rsi2-paper-runner.md",
        universe="28 names — webull_api/strategy/rsi2.py UNIVERSE (widened 20→28 on 2026-08-07)",
        entry="RSI(2) < 10 at the confirmed close → paper BUY queued for the next open by rsi2_service (17:30 suite)",
        exit="RSI(2) > 70 → paper SELL queued for the next open; paper_eod holds RSI2 lots on trend-break",
        schedule="17:30 — Webull Paper Suite (run-log key rsi2)",
        data=("data/paper/default.json", "data/activity/rsi2_state.json", "data/activity/runs.jsonl"),
        journal="data/journal/fills.jsonl (paper-equity rows); scorecard bucket rsi2-equity",
        kill_switch="disable the 'Webull Paper Suite' task — no real money, no kill file",
        review="Friday scorecard inside the suite; monthly red-team; proof bar 30/30 = Gate E (docs/proof-phase-charter.md §6)",
    ),
    Track(
        key="swing_rsi2", label=label_for("swing_rsi2"), money="real",
        state="running", state_note="system entries since Gate E armed 2026-09-14; the autopilot places, protects and exits (09:31 / 09:46 / 15:45 / 17:45 / 18:15)",
        summary=f"rsi2_real queues RSI(2) < 10 entries in the 17:30 suite (ARMED 2026-09-14); the {AUTOPILOT_MORNING} autopilot places them at the open, rests 8% GTC stops and runs the exits",
        book="the Individual Cash account (the $400 day carve-out was retired 2026-09-21)",
        book_source="broker balance (rsi2_real sizes from settled cash: a lot is net liq ÷ WEBULL_RSI2_REAL_SLOT_DIVISOR — 6 = the S6 rule — clipped under the autopilot cap, spec 2026-09-23; WEBULL_RSI2_REAL_DOLLARS is the older fixed figure and, if left set, one more ceiling; 5 lots via WEBULL_RSI2_REAL_MAX_LOTS); caps WEBULL_AUTOPILOT_MAX_NOTIONAL $525 + WEBULL_AUTOPILOT_MAX_POSITIONS 5 (.env, owner 2026-09-17) and codeword WEBULL_TRADE_MAX_NOTIONAL $500 — see the books-and-caps table",
        plan_doc="docs/superpowers/specs/2026-08-15-real-book-rsi2-path-design.md",
        universe="the same 28 names as the paper book (shared by design)",
        entry="rsi2_real_service (17:30 suite; WEBULL_RSI2_REAL_ENABLED=true since 2026-09-14) queues one executable BUY per RSI(2) < 10 signal — QUEUE-ONLY, it never places; the 09:31 autopilot run turns the row into a next-open order behind gate.authorize (first system fills 2026-09-16). No hand entries on this sleeve",
        exit=f"two-phase: an rsi2_above decision row → the {AUTOPILOT_MORNING} autopilot run cancels the resting stop and sells at the open (cancel-then-sell)",
        schedule=f"{AUTOPILOT_MORNING} / 09:46 / 15:45 / 17:45 / 18:15 — Webull Autopilot (run-log key autopilot); 17:30 suite (rsi2_real row)",
        data=("autopilot/state", "autopilot/log", "data/activity/executable_decisions.jsonl", "data/exec/decisions.jsonl"),
        journal="data/journal/fills.jsonl (real-equity rows); thesis rows in data/activity/decisions.jsonl",
        kill_switch="autopilot halt (kill file, phone-allowed) · WEBULL_AUTOPILOT_ENABLED",
        review="proof bar + the Gate E arming checklist (docs/superpowers/specs/2026-08-07-arming-checklist.md); monthly red-team",
    ),
    Track(
        key="swing_pullback", label=label_for("swing_pullback"), money="real",
        state="running", state_note="owner-driven entries; the autopilot entry screen runs nightly",
        summary="evening-scan drafts → trade-placer (codeword); trade-planner; the autopilot entry screen",
        book="$400 charter book for sizing the screen",
        book_source="docs/proof-phase-charter.md §2 ($400); playbook/swing-trading-plan.md's worked BOOK = $500 is an example, not the live figure",
        plan_doc="playbook/swing-trading-plan.md",
        universe="webull_api/discovery.py CURATED_UNIVERSE (~180 names) plus injected movers; $10–$80 band (20% of book)",
        entry="evening-scan skill → discover_and_draft → GTC buy-stop-limit drafts (data/intents/) → trade-placer with the codeword; the 17:45 autopilot runs the same screen behind its gate",
        exit="manage_exits / exit-placer (stop −8%, target +20%, trend-break); resting protective stops via protect_positions",
        schedule="18:30 — evening scan (owner, Claude Desktop); 17:45 — autopilot entry screen",
        data=("data/intents", "data/plans", "playbook/screens"),
        journal="playbook/screens/<date>-eod-screen.md; fills in data/journal/fills.jsonl",
        kill_switch="the same autopilot kill file; drafts expire (30 min, 4 h for the routine)",
        review="trade-review skill; plan review after 20 closed trades or 1 month (plan header)",
    ),
)
