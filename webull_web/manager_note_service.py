"""Nightly note — deterministic composition of what the REAL book did today and what is set up
for tomorrow, from data already on disk. NO LLM, NO tokens: the note is honest synthesis, and an
unreadable source says 'unavailable' rather than inventing a value. compose() is pure and
unit-tested; run() is its own scheduled step ("Webull Nightly Note", 18:25 ET — AFTER the 18:15
autopilot backstop, so the protective stops the evening run places are already in the note;
2026-09-29, it used to be the last step of the 17:30 suite and went out before them).

Two renderings come out of one gather (phone restructure, 2026-09-21):

- ``lines`` — one full sentence per source with honest per-source degradation. This is the
  record: stored append-only in data/activity/manager_notes.jsonl (the phone status-checkin
  reads its last line).
- ``text`` — the PHONE rendering. ntfy's phone apps render plain text only (no Markdown) and
  turn any body over 4,096 bytes into a file attachment, so the phone note is sectioned,
  ASCII-separated, attention-first and held under PHONE_BUDGET_BYTES. It NAMES the trades:
  TODAY (each real order the autopilot placed), TOMORROW (queued executable decisions),
  STOPS (every held real lot with its resting protective stop). Anything that needs the owner
  lands in the trailing ATTENTION section (a held lot with NO STOP always does), which also
  flips ``priority``/``tags`` to high/warning.

RSI2-only since 2026-09-29: the parked paper systems (paper books, options risk, scorecard,
proof bar, lab funnel, scanner, discipline, judgment) are no longer composed at all.
"""
from __future__ import annotations

import re

# Suite order; netliq is reported via the Books line, not the runner clause.
RUNNER_LABELS = [
    ("rsi2_real", "RSI2-real"),
    ("flows", "Flows"),
]
# SIMPLIFIED 2026-09-29 (owner: RSI2-only): the paper RSI2 runner is parked (runner_cli.PARKED_SUITE); a
# re-enable moves it back into RUNNER_LABELS (and watchdog_service.EXPECTED_KEYS) as one edit. The other old
# runners were archived at git tag archive/pre-rsi2-only-2026-09-29 (docs/ARCHIVE.md).
PARKED_RUNNER_LABELS = [
    ("rsi2", "RSI2"),
]

# ── phone rendering ─────────────────────────────────────────────────────────────────────────
# UTF-8 byte ceiling for the phone body: comfortably under ntfy's 4,096-byte attachment
# threshold AND short enough to read on a lock screen. Over it, sections are dropped in
# _PHONE_TRIM_ORDER before the hard cut in webull_api.push.fit ever has to act.
PHONE_BUDGET_BYTES = 2500
_PHONE_SECTIONS = ("TODAY", "TOMORROW", "STOPS", "MARKET", "SYSTEM")
_PHONE_TRIM_ORDER = ("SYSTEM", "MARKET")
_PHONE_MAX_ATTENTION = 8
_MAX_ORDER_LINES = 8
_PHONE_MAX_ORDERS_WHEN_TRIMMING = 3
# The full lines use typographic separators; the phone body is ASCII so every character is
# one byte and every font renders it. Applied once, at render time.
_ASCII = (("—", "-"), ("−", "-"), ("→", "->"), ("≤", "<="),
          ("≥", ">="), ("×", "x"), ("÷", "/"), ("…", "..."), (" · ", " | "),
          ("·", "|"))
_RSI_RE = re.compile(r"RSI\(2\)\s*([0-9]+(?:\.[0-9]+)?)")


class _Phone:
    """Collector for the phone rendering: header lines, fixed sections, ATTENTION."""

    def __init__(self) -> None:
        self.head: list[str] = []
        self.sections: dict[str, list[str]] = {k: [] for k in _PHONE_SECTIONS}
        self.attention: list[str] = []
        self.unavailable: list[str] = []

    def add(self, section: str, line: str) -> None:
        self.sections[section].append(line)

    def warn(self, line: str) -> None:
        self.attention.append(line)

    def unavail(self, source: str) -> None:
        self.unavailable.append(source)

    def render(self, *, budget: int = PHONE_BUDGET_BYTES) -> str:
        from webull_api import push
        sections = {k: list(v) for k, v in self.sections.items()}
        attention = list(self.attention)
        if len(attention) > _PHONE_MAX_ATTENTION:
            attention = attention[:_PHONE_MAX_ATTENTION] + [
                f"+{len(self.attention) - _PHONE_MAX_ATTENTION} more (see data/activity/manager_notes.jsonl)"]
        dropped: list[str] = []
        all_orders = list(self.sections["TODAY"])
        sections["TODAY"] = _cap_orders(all_orders, _MAX_ORDER_LINES)
        orders_trimmed = False
        while True:
            text = _ascii(_assemble(self.head, sections, attention, dropped))
            if len(text.encode("utf-8")) <= budget:
                return text
            if not orders_trimmed and len(all_orders) > _PHONE_MAX_ORDERS_WHEN_TRIMMING:
                sections["TODAY"] = _cap_orders(all_orders, _PHONE_MAX_ORDERS_WHEN_TRIMMING)
                orders_trimmed = True
                continue
            for name in _PHONE_TRIM_ORDER:
                if sections[name]:
                    sections[name] = []
                    dropped.append(name)
                    break
            else:
                return push.fit(text, max_bytes=budget)   # nothing left to drop: hard cut


def _cap_orders(orders: list[str], k: int) -> list[str]:
    """At most k order lines, the rest folded into one honest count."""
    if len(orders) <= k:
        return list(orders)
    return orders[:k] + [f"...+{len(orders) - k} more (see autopilot/log)"]


def _assemble(head: list[str], sections: dict[str, list[str]], attention: list[str],
              dropped: list[str]) -> str:
    blocks: list[str] = ["\n".join(head)] if head else []
    for name in _PHONE_SECTIONS:
        if sections.get(name):
            blocks.append("\n".join([name, *sections[name]]))
    if attention:
        blocks.append("\n".join(["ATTENTION", *(f"- {a}" for a in attention)]))
    if dropped:
        blocks.append(f"[trimmed: {', '.join(n.lower() for n in dropped)} - full note in "
                      "data/activity/manager_notes.jsonl]")
    return "\n\n".join(blocks)


def _ascii(text: str) -> str:
    for src, dst in _ASCII:
        text = text.replace(src, dst)
    return text


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _signed(v: float) -> str:
    """ASCII signed dollars for the phone: +$11.55 / -$3.20."""
    return f"{'+' if v >= 0 else '-'}{_money(abs(v))}"


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def _qty(v) -> str:
    """18.0 -> "18", "2" -> "2", 2.5 -> "2.5", "ALL" as-is."""
    f = _num(v)
    if f is None:
        return str(v)
    return str(int(f)) if f == int(f) else f"{f:g}"


def _price(v) -> str:
    f = _num(v)
    return f"{f:.2f}" if f is not None else str(v)


def _after_colon(row: dict) -> str:
    """A run-log row's summary with its 'Label:' prefix removed."""
    return str(row.get("summary", "")).split(":", 1)[-1].strip() or str(row.get("result", "?"))


# ── the real book's orders ───────────────────────────────────────────────────────────────────

def _match_decision(entry: dict, decisions: list[dict], today: str, used: set) -> dict | None:
    """The executable decision a placed audit row acted on: by decision_id when the row carries
    one, else the first unused same-symbol/same-side row marked placed today."""
    did = entry.get("decision_id")
    if did:
        return next((d for d in decisions if d.get("id") == did), None)
    for d in decisions:
        if (d.get("id") not in used and d.get("symbol") == entry.get("symbol")
                and d.get("side") == entry.get("side") and d.get("status") == "placed"
                and str(d.get("acted_ts", ""))[:10] == today):
            return d
    return None


def _order_terms(d: dict) -> str:
    ot = str(d.get("order_type", "")).upper()
    if ot == "LIMIT" and d.get("limit_price") is not None:
        return f" lmt {_price(d['limit_price'])}"
    if ot == "MARKET":
        return " mkt"
    if d.get("stop_price") is not None:
        return f" stop {_price(d['stop_price'])}"
    return ""


def _today_orders(log: list[dict], decisions: list[dict], stops: dict | None,
                  today: str) -> tuple[list[str], list[str]]:
    """(order lines, failure lines) for every real order the autopilot placed today."""
    out: list[str] = []
    failed: list[str] = []
    used: set = set()
    for e in log:
        if not isinstance(e, dict):
            continue
        sym, side = e.get("symbol", "?"), e.get("side", "?")
        src = str(e.get("source", ""))
        if e.get("error"):
            failed.append(f"autopilot order FAILED: {side} {sym} ({src}) - {str(e['error'])[:100]}")
            continue
        if e.get("placed") is not True:
            continue
        if src == "protect":
            stop = (stops or {}).get(sym)
            out.append(f"{side} {sym}" + (f" stop {stop}" if stop else "") + " (stop placed)")
            continue
        d = _match_decision(e, decisions, today, used)
        if d is not None:
            used.add(d.get("id"))
        kind = "entry" if side == "BUY" else "exit"
        trig = (d or {}).get("trigger", {}) or {}
        if kind == "exit" and trig.get("kind") and trig.get("kind") != "immediate":
            kind = f"exit {trig['kind']}"
        elif src.startswith("exit:"):
            kind = f"exit {src.split(':', 1)[1]}"
        qty = f" {_qty(d['qty'])}" if d and d.get("qty") is not None else ""
        terms = _order_terms(d) if d else ""
        hhmm = str((d or {}).get("acted_ts", ""))[11:16]
        when = f", {hhmm}" if hhmm else ""
        out.append(f"{side}{qty} {sym}{terms} ({kind}{when})")
    return out, failed


def _latest_rsi(log: list[dict], decision_id: str) -> str | None:
    """RSI(2) as the autopilot's last trigger check for this row read it (no new broker call)."""
    for e in reversed(log or []):
        if isinstance(e, dict) and e.get("decision_id") == decision_id:
            m = _RSI_RE.search(str(e.get("reason", "")))
            if m:
                return m.group(1)
    return None


def _tomorrow(decisions: list[dict], log: list[dict] | None, today: str) -> list[str]:
    out: list[str] = []
    for d in decisions:
        if d.get("status") != "queued":
            continue
        exp = str(d.get("expires", ""))
        if exp and exp < today:
            continue
        sym, side = d.get("symbol", "?"), d.get("side", "?")
        trig = d.get("trigger") or {}
        kind = trig.get("kind", "?")
        qty = _qty(d.get("qty", "?"))
        if kind == "immediate":
            out.insert(sum(1 for o in out if " at open" in o),
                       f"{side} {sym} {qty}{_order_terms(d)} at open"
                       + (f", expires {exp}" if exp else ""))
        elif kind == "rsi2_above":
            rsi = _latest_rsi(log or [], str(d.get("id")))
            thr = trig.get("threshold")
            out.append(f"{side} {qty} {sym} if RSI(2) > {_qty(thr)}"
                       + (f" (now {rsi})" if rsi else "") + " - exit armed")
        else:
            out.append(f"{side} {qty} {sym} ({kind})")
    return out


def compose(*, today: str, run_rows: list[dict], netliq_rows: list[dict],
            autopilot: dict | None, real_stops: dict[str, str] | None,
            autopilot_log: list[dict] | None = None,
            exec_decisions: list[dict] | None = None,
            held_lots: list[dict] | None = None,
            events: dict | None = None, exec_quality: dict | None = None,
            regime: dict | None = None, decisions: list[dict] | None = None,
            flows_today: float | None = None) -> dict:
    """Pure. Returns {date, lines, text, attention, priority, tags}: ``lines`` is the full
    record, ``text`` the phone body (see the module docstring), ``priority``/``tags`` the ntfy
    headers for that push ("high"/"warning" whenever ATTENTION is non-empty).
    ``autopilot_log`` = today's autopilot audit rows; ``exec_decisions`` = executable_decisions
    rows; ``held_lots`` = the RSI2-real ledger's owned lots. None = that source unreadable.
    ``flows_today`` (owner deposits/withdrawals booked today) lets the real-book delta be
    reported ex-flows; None = flows unreadable, delta shown raw."""
    lines: list[str] = []
    ph = _Phone()

    # 1) What ran (last entry per key today, suite order).
    todays: dict[str, dict] = {}
    for r in run_rows:
        if str(r.get("ts", ""))[:10] == today and r.get("key"):
            todays[str(r["key"])] = r
    clauses = []
    missing = []
    for key, label in RUNNER_LABELS:
        row = todays.get(key)
        if row:
            summary = _after_colon(row)
            clauses.append(f"{label}: {summary}")
            if str(row.get("result", "")) == "error" or row.get("errors"):
                first = (row.get("errors") or [summary])[0]
                ph.warn(f"{label}: {str(first)[:140]}")
        else:
            missing.append(label)
    if clauses:
        lines.append("Evening run — " + " · ".join(clauses) + ".")
        sys_line = f"suite {len(clauses)}/{len(RUNNER_LABELS)} reported"
        if missing:
            sys_line += " | missing: " + ", ".join(missing)
        ph.add("SYSTEM", sys_line)
        if "rsi2_real" in todays:
            ph.add("SYSTEM", "RSI2-real: " + _after_colon(todays["rsi2_real"]))
    else:
        lines.append("No runners have run yet today.")
        ph.warn("no runners have reported today")

    # 2) Real book value + delta vs the previous snapshot (ex-flows on the phone).
    row = next((r for r in reversed(netliq_rows) if str(r.get("date", ""))[:10] == today), None)
    real = _num(row.get("real")) if row else None
    if row and real is not None:
        prev = next((r for r in reversed(netliq_rows) if str(r.get("date", ""))[:10] < today), None)
        prev_date = str(prev.get("date", ""))[:10] if prev else ""
        pv_real = _num(prev.get("real")) if prev else None
        rec = f"Books: real {_money(real)}"
        head = f"REAL {_money(real)}"
        if pv_real is not None:
            d = real - pv_real
            rec += f" ({'+' if d >= 0 else '−'}{_money(abs(d))} vs {prev_date})"
            flow = flows_today if flows_today is not None else 0.0
            head += f"  {_signed(real - pv_real - flow)} vs {prev_date[5:] or '?'}"
            if flows_today:
                head += f" | flow {_signed(flows_today)}"
        lines.append(rec + ".")
        ph.head.append(head)
    else:
        lines.append("Book values unavailable today (no net-liq snapshot).")
        ph.head.append("REAL unavailable (no net-liq snapshot)")
        ph.warn("book values unavailable (no net-liq snapshot)")

    # 3) Autopilot posture (the head line); the orders themselves are named in TODAY.
    if autopilot and not autopilot.get("unavailable"):
        t = autopilot.get("today") or {}
        posture = str(autopilot.get("posture", "?")).upper()
        lines.append(f"Autopilot {posture}: "
                     f"{t.get('placed', '?')} placed · {t.get('skipped', '?')} skipped today.")
        ph.head.append(f"Autopilot {posture}: {t.get('placed', '?')} placed, "
                       f"{t.get('skipped', '?')} skipped")
    else:
        lines.append("Autopilot status unavailable.")
        ph.head.append("Autopilot: status unavailable")
        ph.warn("autopilot status unavailable")

    # 4) TODAY — every real order the autopilot placed, named.
    if autopilot_log is not None:
        orders, failed = _today_orders(autopilot_log, exec_decisions or [], real_stops, today)
        lines.append("Today: " + (" · ".join(orders) if orders else "no real orders placed") + ".")
        for o in orders:
            ph.add("TODAY", f"- {o}")
        if not orders:
            ph.add("TODAY", "- no real orders placed")
        for f in failed:
            ph.warn(f)
    else:
        lines.append("Today: autopilot log unavailable.")
        ph.unavail("autopilot log")

    # 5) TOMORROW — queued executable decisions.
    if exec_decisions is not None:
        tmr = _tomorrow(exec_decisions, autopilot_log, today)
        lines.append("Tomorrow: " + (" · ".join(tmr) if tmr else "nothing queued") + ".")
        for t_line in tmr:
            ph.add("TOMORROW", f"- {t_line}")
        if not tmr:
            ph.add("TOMORROW", "- nothing queued")
    else:
        lines.append("Tomorrow: executable decisions unavailable.")
        ph.unavail("queued decisions")

    # 6) STOPS — every held real lot with its resting protective stop. real_stops {} =
    # checked-and-none; None = unreadable.
    if real_stops is None:
        lines.append("Protection: broker open orders unavailable.")
        ph.add("STOPS", "- unavailable")
        ph.warn("broker open orders unavailable (protection unverified)")
    else:
        prot: list[str] = []
        lot_syms: set = set()
        for lot in held_lots or []:
            sym = str(lot.get("symbol", "?"))
            lot_syms.add(sym)
            desc = f"{sym} {_qty(lot.get('shares', '?'))}"
            if lot.get("entry_price") is not None:
                desc += f" @ {_price(lot['entry_price'])}"
            stop = real_stops.get(sym)
            if stop:
                prot.append(f"{sym} stop ${stop} resting (GTC)")
                ph.add("STOPS", f"- {desc} stop {stop}")
            else:
                prot.append(f"{sym} NO STOP")
                ph.add("STOPS", f"- {desc} NO STOP")
                ph.warn(f"NO STOP on real lot {sym} ({desc})")
        for sym, stop in sorted(real_stops.items()):
            if sym not in lot_syms:
                prot.append(f"{sym} stop ${stop} resting (GTC)")
                ph.add("STOPS", f"- {sym} stop {stop}")
        if held_lots is None:
            ph.unavail("held lots")
        if prot:
            lines.append("Protection: " + " · ".join(prot) + ".")
        else:
            lines.append("Protection: no held lots and no resting stops on the real book.")
            ph.add("STOPS", "- no held lots, no resting stops")

    # 7) Market regime — SPY vs its 200-day SMA (drives the autopilot's position caps).
    if regime is not None:
        ro = regime.get("spy_risk_off")
        price, sma = _num(regime.get("spy_price")), _num(regime.get("spy_sma200"))
        ctx = f" ({_money(price)} vs 200SMA {_money(sma)})" if price is not None and sma is not None else ""
        ctx_ph = f" {price:,.2f} vs 200d {sma:,.2f}" if price is not None and sma is not None else ""
        if ro is True:
            lines.append(f"Regime: SPY risk-off{ctx} — position caps tighten.")
            ph.add("MARKET", f"SPY RISK-OFF{ctx_ph} - caps tighten")
        elif ro is False:
            lines.append(f"Regime: SPY risk-on{ctx}.")
            ph.add("MARKET", f"SPY risk-on{ctx_ph}")
        else:
            lines.append("Regime: SPY trend unknown (insufficient data).")
            ph.add("MARKET", "SPY trend unknown (insufficient data)")
    else:
        lines.append("Regime unavailable.")
        ph.unavail("regime")

    # 8) Earnings on held names. None = source unreadable.
    if events is not None:
        bits = []
        ph_bits = []
        for h in events.get("hits") or []:
            is_real = "real" in (h.get("books") or [])
            realtag = ", real" if is_real else ""
            bits.append(f"{h['symbol']} earnings {h['date']} (in {h['days']}d{realtag})")
            ph_bits.append(f"{h['symbol']} {str(h['date'])[5:]} ({h['days']}d{realtag})")
            if is_real:
                ph.warn(f"earnings on real lot {h['symbol']} in {h['days']}d ({h['date']})")
        unv = len(events.get("unverified") or [])
        checked = events.get("checked", 0)
        all_unverified = not bits and unv > 0 and unv == checked
        if bits:
            line = "Events: " + " · ".join(bits)
            ph_line = "earnings: " + " | ".join(ph_bits)
        elif all_unverified:
            line = f"Events: {unv} held name(s) unverified (earnings lookup failed)"
            ph_line = ""
            ph.warn(f"earnings lookup failed ({unv} held name(s) unverified)")
        else:
            line = (f"Events: none within {events.get('window_days', 14)} days "
                    f"({checked} names checked)")
            ph_line = f"earnings: none within {events.get('window_days', 14)}d ({checked} held)"
        if unv and not all_unverified:
            line += f" · {unv} unverified"
            ph_line += f" | {unv} unverified"
        for b in events.get("books_unavailable") or []:
            line += f" · {b} book unreadable"
            if b == "real":
                ph.warn("real book unreadable (earnings check)")
        lines.append(line + ".")
        if ph_line:
            ph.add("MARKET", ph_line)
    else:
        lines.append("Events unavailable.")
        ph.unavail("events")

    # 9) Standing decisions — a fired trigger is an ATTENTION item (the deliberate nightly
    # nag until resolved); the waiting ones stay in the record only.
    if decisions:
        parts = [f"{d.get('title', d.get('id', '?'))} — "
                 f"{'TRIGGERED today' if d.get('fired') else 'waiting'} ({d.get('detail', '?')})"
                 for d in decisions]
        lines.append("Decisions: " + " · ".join(parts) + ".")
        for d in decisions:
            if d.get("fired"):
                item = f"TRIGGERED: {d.get('title', d.get('id', '?'))} - {d.get('detail', '?')}"
                if d.get("decision"):
                    item += f" -> {d['decision']}"
                ph.warn(item)
    elif decisions is not None:
        lines.append("Decisions: none open.")
    else:
        lines.append("Decisions unavailable.")
        ph.warn("decisions check unavailable")

    # 10) Execution quality — real fills vs their decision-time reference.
    if exec_quality is not None:
        n = exec_quality.get("fills_matched", 0)
        if n:
            lines.append(f"Exec: {n} real fill(s) matched · avg slippage "
                         f"{exec_quality.get('avg_bp', 0)}bp (model assumes "
                         f"{exec_quality.get('modeled_bp', 0)}bp).")
            ph.add("SYSTEM", f"exec {n} fills, slip {exec_quality.get('avg_bp', 0)}bp "
                             f"(model {exec_quality.get('modeled_bp', 0)}bp)")
        else:
            lines.append("Exec: no matched real fills yet.")
    else:
        lines.append("Exec quality unavailable.")
        ph.unavail("exec quality")

    if ph.unavailable:
        ph.warn("unavailable: " + ", ".join(ph.unavailable))
    attention = list(ph.attention)
    priority, tags = ("high", "warning") if attention else ("default", "white_check_mark")
    return {"date": today, "lines": lines, "text": ph.render(), "attention": attention,
            "priority": priority, "tags": tags}


import json
import logging
import os

from webull_api import run_log

from . import manager_notes_store, netliq_store, paper_service, push, runner_util

_log = logging.getLogger(__name__)
_KEY = "note"


def _safe(fn, fallback):
    try:
        return fn()
    except Exception:
        _log.exception("manager-note: source unavailable")
        return fallback


def _regime_now() -> dict:
    """SPY vs its 200-day SMA (webull_web.regime)."""
    from . import regime
    return regime.swing_regime()


def _flows_today(today: str) -> float:
    """Owner deposits/withdrawals booked today (the flows runner's contributions rows), so the
    real-book delta on the phone is P/L, not a deposit dressed as a gain (2026-09-21: +$699.96)."""
    from . import contributions_store
    return round(sum(float(r.get("amount") or 0.0) for r in contributions_store.load()
                     if str(r.get("date", ""))[:10] == today), 2)


def _autopilot_log(today: str) -> list[dict]:
    """Today's autopilot audit rows (read-only; a missing file = no run yet = [])."""
    from webull_api.autopilot.paths import autopilot_dir
    p = autopilot_dir() / "log" / f"{today}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _exec_decisions() -> list[dict]:
    from webull_api import decisions_exec
    return decisions_exec.load()[0]


def _held_lots() -> list[dict]:
    from . import rsi2_real_store
    return list(rsi2_real_store.load().get("owned_lots") or [])


def _gather(today: str) -> dict:
    """Each source independently degradable — compose() renders the honest fallback."""
    from webull_api.autopilot import status as autopilot_status

    from . import events_service, exec_quality_service, exit_plans_service
    return dict(
        run_rows=_safe(run_log.load, []),
        netliq_rows=_safe(netliq_store.load, []),
        autopilot=_safe(lambda: autopilot_status.snapshot(today), None),
        real_stops=_safe(exit_plans_service.resting_stops, None),
        autopilot_log=_safe(lambda: _autopilot_log(today), None),
        exec_decisions=_safe(_exec_decisions, None),
        held_lots=_safe(_held_lots, None),
        events=_safe(lambda: events_service.held_earnings(today), None),
        exec_quality=_safe(lambda: exec_quality_service.view()["summary"], None),
        regime=_safe(_regime_now, None),
        flows_today=_safe(lambda: _flows_today(today), None),
    )


def run(force: bool = False) -> dict:
    iso, today = paper_service.now_et()
    with runner_util.filelock(_KEY, iso) as got:
        if not got:
            return {"result": "no_op", "errors": [], "pushed": "off",
                    "summary": "Note: another run in progress", "ran_at": iso}
        if runner_util.already_ran_today(_KEY, today, force):
            return {"result": "no_op", "errors": [], "pushed": "off",
                    "summary": "Note: already ran today", "ran_at": iso}

        from . import decisions_service
        # The trigger watch appends its own check rows; a fired trigger reaches the phone through
        # this note's ATTENTION section. A broken decisions source degrades to the "Decisions
        # unavailable." line (+ an ATTENTION item), never fails the note.
        decisions = _safe(lambda: decisions_service.evening_check(today, iso), None)
        note = compose(today=today, decisions=decisions, **_gather(today))
        manager_notes_store.append({**note, "ts": iso})

        url = os.environ.get("WEBULL_MANAGER_NOTE_NTFY", "").strip()
        pushed: bool | str = "off"
        if url:
            pushed = push.push_text(note["text"], title=f"Nightly note - {today}", url=url,
                                    priority=note["priority"], tags=note["tags"])

        push_txt = {"off": "push off", True: "pushed", False: "push FAILED (note stored)"}[pushed]
        summary = (f"Note: {len(note['lines'])} line(s) · {len(note['attention'])} attention · "
                   f"{push_txt}")
        errors = [] if pushed is not False else ["ntfy push failed"]
        run_log.append([{"key": _KEY, "ts": iso, "result": "ok", "summary": summary,
                         "placed": 0, "errors": errors}])
        return {"result": "ok", "errors": errors, "pushed": pushed,
                "summary": summary, "ran_at": iso}
