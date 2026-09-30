"""The daily routine as data + a pure status function (spec 2026-09-07 program map §4).
No I/O: the caller (the kestrel feed, `feed_service`) hands in today's run-log rows and an
`exists(evidence, day)` callback; the program-map renderer prints ROUTINE as a table. Times are ET."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from webull_api.market_calendar import NYSE_HOLIDAYS

from .tracks import AUTOPILOT_MORNING, label_for

ET = ZoneInfo("America/New_York")
_EARLY_MIN = 5          # a run-log row may land a few minutes before its trigger (task scheduler jitter)
_SKIPPED = ("skipped", "no_op")


@dataclass(frozen=True)
class RoutineRow:
    time: str                 # "HH:MM" ET, or "" for an untimed row
    track: str                # a tracks key, or "all"
    label: str
    kind: str                 # "task" | "owner" | "external" | "market"
    days: str = "weekdays"    # "weekdays" | "friday" | "monthly" (last Friday of the month)
    evidence: str = ""        # run-log key · "file:<glob with {date}>" · "jsonl:<path>" · ""
    grace_min: int = 0
    paused: str = ""          # non-empty = the task is deliberately disabled; the reason (row reads "off")


ROUTINE: tuple[RoutineRow, ...] = (
    RoutineRow("09:30", "all", "Market open — resting real orders fill", "market"),
    RoutineRow(AUTOPILOT_MORNING, "swing_rsi2", "Autopilot morning trigger (queued exits)", "task", evidence="autopilot", grace_min=20),
    RoutineRow("15:45", "swing_rsi2", "Autopilot retry trigger", "task", evidence="autopilot", grace_min=20),
    RoutineRow("16:00", "all", "Market close", "market"),
    RoutineRow("17:30", "swing_rsi2", "Evening suite (net-liq → RSI2-real → flows)", "task", evidence="rsi2_real", grace_min=60),
    RoutineRow("17:45", "swing_rsi2", "Autopilot evening run (decisions + entry screen; 18:15 backstop)", "task", evidence="autopilot", grace_min=20),
    RoutineRow("18:25", "swing_rsi2", "Nightly note (names today's orders, tomorrow's queue, stops) → phone", "task", evidence="note", grace_min=30),
    RoutineRow("18:30", "swing_pullback", "Evening scan (Claude Desktop, optional) → drafts → trade-placer (codeword)", "owner", evidence="file:playbook/screens/{date}-eod-screen.md", grace_min=0),
    RoutineRow("18:35", "swing_rsi2", "Tiingo EOD backfill, free tier (today's EOD row lands after ~17:00; RSI2 research store)", "task",
               evidence="jsonl:data/tiingo/backfill_runs.jsonl", grace_min=25),
    RoutineRow("19:00", "all", "Suite watchdog", "task", evidence="watchdog", grace_min=30),
    RoutineRow("21:00", "all", "healthchecks.io deadline (external dead-man)", "external"),
    RoutineRow("", "swing_rsi2", "Friday: trade-review", "owner", days="friday"),
    RoutineRow("", "all", "Monthly: strategy red-team", "owner", days="monthly"),
)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def _last_friday(d: date) -> bool:
    return d.weekday() == 4 and (d + timedelta(days=7)).month != d.month


def rows_for(d: date, rows: tuple[RoutineRow, ...] = ROUTINE) -> list[RoutineRow]:
    out = []
    for r in rows:
        if r.days == "weekdays" or (r.days == "friday" and d.weekday() == 4) or (r.days == "monthly" and _last_friday(d)):
            out.append(r)
    return out


def _at(d: date, hhmm: str) -> datetime:
    hh, mm = hhmm.split(":")
    return datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=ET)


def _ts_et(row: dict) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(row.get("ts")))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    return ts.astimezone(ET)


def _by_clock(now: datetime, start: datetime, grace_min: int, after: str = "late") -> str:
    """next → due (within grace) → `after` ("late" for tasks, "past" for the owner's own rows)."""
    if now < start:
        return "next"
    if now <= start + timedelta(minutes=grace_min):
        return "due"
    return after


def _result_word(row: dict) -> str:
    res = str(row.get("result") or "")
    if res == "error":
        return "error"
    if res in _SKIPPED:
        return "skipped"
    return "done"


def _window_end(rows: list[RoutineRow], i: int, day: date) -> datetime:
    """A keyed task row owns run-log rows from (its time − 5 min) up to the NEXT timed row with
    the same key — so the morning autopilot run never marks the 17:45 row done."""
    me = rows[i]
    for later in rows[i + 1:]:
        if later.evidence == me.evidence and later.time:
            return _at(day, later.time) - timedelta(minutes=_EARLY_MIN)
    return _at(day, "23:59")


def status_for(rows, run_rows, now: datetime, exists) -> list[dict]:
    """Pure. `rows`: RoutineRow list for the day (rows_for). `run_rows`: run-log dicts (any day —
    only today's count). `now`: any aware datetime (converted to ET). `exists(evidence, day)`.
    `rows` must be in ROUTINE order (rows_for preserves it) — same-key windows are derived from that order."""
    now = now.astimezone(ET)
    day = now.date()
    rows = list(rows)
    trading = is_trading_day(day)
    todays = [(r, t) for r in run_rows if (t := _ts_et(r)) is not None and t.date() == day]
    out = []
    for i, r in enumerate(rows):
        status = "off"
        if trading and not r.paused:
            start = _at(day, r.time) if r.time else None
            if r.kind in ("market", "external"):
                status = "past" if start is not None and now >= start else "next"
            elif r.evidence and not r.evidence.startswith(("file:", "jsonl:")):
                if start is None:
                    matched = [(row, t) for row, t in todays if row.get("key") == r.evidence]
                else:
                    lo, hi = start - timedelta(minutes=_EARLY_MIN), _window_end(rows, i, day)
                    matched = [(row, t) for row, t in todays if row.get("key") == r.evidence and lo <= t < hi]
                if matched:
                    status = _result_word(max(matched, key=lambda p: p[1])[0])
                elif start is None:
                    status = "next"
                else:
                    status = _by_clock(now, start, r.grace_min)
            elif r.evidence:
                if exists(r.evidence, day):
                    status = "done"
                elif start is None:
                    status = "next"
                else:
                    status = _by_clock(now, start, r.grace_min, after="past" if r.kind == "owner" else "late")
            else:
                status = "yours" if r.kind == "owner" else "next"
        out.append({"time": r.time, "track": r.track, "track_label": label_for(r.track),
                    "label": r.label, "kind": r.kind, "status": status, "evidence": r.evidence})
    return out
