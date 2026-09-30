"""The kestrel feed (spec 2026-09-26 kestrel feed): one document in kestrel's data contract (version "1", plain
dicts -- this repo never imports kestrel) built only from files the desk already writes: the journal's fills, the
paper account and both RSI2 ledgers, the net-liq history, the run-log, the
autopilot's audit log and daily state, the Tiingo store and the committed backtest reviews.

Read-only by construction: no broker client, no order module, no subprocess, no network -- tests/test_feed_guard.py
walks every repo module this one reaches. Nothing here writes, not even the quarantine rename paper_store.load does
on a torn file, so the paper account and the RSI2 ledgers are read directly. Every file is read at most once per
request (_Files). Each block -- one per book, then charts, strategies, runs and alerts -- is built in its own try: a
failure is a `note` alert and an empty block, never a 500. No accounts or holdings: the real account's money reaches
kestrel from financial-data-collector, deposit-aware, and serving it here too would count it twice."""
from __future__ import annotations

import bisect
import csv
import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from webull_api import run_log
from webull_api.autopilot import paths as autopilot_paths
from webull_api.journal import pairing
from webull_api.journal.schema import Fill
from webull_api.paths import REPO_ROOT, data_dir
from webull_api.strategy import indicators
from webull_api.strategy.rsi2 import DEFAULT_CONFIG as RSI2_CONFIG
from webull_api.tiingo import store as tiingo_store

from . import (journal_store, netliq_store, paper_store, plan_stats, routine, rsi2_real_store, rsi2_store,
               visibility)
from .rsi2_real_attribution import _et
from .tracks import TRACKS

_log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
CONTRACT_VERSION = "1"
MAX_TRADES = 500             # per book, the most recent (spec §6 bounded work)
CHARTS_PER_BOOK = 8          # closed-trade charts per book: its most recent trades (spec §5, §6)
BARS_BEFORE = 20             # sessions of daily bars before the open ...
BARS_AFTER = 10              # ... and after the close (or up to the latest bar)
RSI_WARMUP = 100             # closes before the chart window that seed RSI(2)'s smoothing
BAR_HORIZON_DAYS = 730       # freshness only (W2): a position's last-close MARK is stale beyond this many days
                             # before `now`; a 30-year file is ~7,500 rows and marking needs only the latest close
CHART_SINCE_PAD_DAYS = 200   # W2: a chart's bars are read from (its symbol's earliest charted open - this many
                             # calendar days) -- not a fixed, `now`-relative horizon, so a chart near the old
                             # horizon boundary always gets its full RSI_WARMUP sessions when the file has them
BUCKETS = 20                 # kestrel Expected.distribution: 1-point buckets from -10% to +10%, the ends folded in
_EPS = 1e-9
MAX_MAGNITUDE = 1e15         # M1+M2: kestrel's own contract rejects the WHOLE document for a number past this
MIN_DATE, MAX_DATE = "1970-01-01", "2200-12-31"   # ... or an ISO date outside this range
STALE_SESSIONS = 5           # M4: a mark from a close this many (or more) NYSE trading days before `now` says so
MAX_BACKTESTS = 600          # Task 2 (2026-09-27 kestrel feed export plan): cap the backtests block, newest first

# The committed backtest files each plan cites, and their labels: these were the cockpit's RSI2_BACKTEST,
# ORB_BACKTEST and EXPECTED_LABELS values (plan_service, archived 2026-09-28; the IBS entry went with the IBS book,
# archived 2026-09-29). tests/test_feed_service.py's test_the_backtest_files_are_committed pins that each path
# still exists.
RSI2_BACKTEST = REPO_ROOT / "docs" / "reviews" / "2026-09-07-rsi2-backtest-tiingo.json"
ORB_BACKTEST = REPO_ROOT / "docs" / "reviews" / "2026-09-08-orb-backtest.json"
EXPECTED_LABELS = {"rsi2": "20-year backtest (Tiingo, 2006–2026, 28 names)",
                   "day-orb": "5-year ORB backtest (Tiingo IEX 1-min, plan v1.0)"}

# How many closed trades until each plan's next review (kestrel Strategy.review_at_trades), with its source line.
REVIEW_AT_TRADES: dict[str, int | None] = {
    "rsi2": 30,            # docs/proof-phase-charter.md §6: "Sample: 30+ completed decisions (real + paper combined)."
    "pullback": 20,        # playbook/swing-trading-plan.md: "Next scheduled rule review: after 20 closed trades, or
                           # 1 month, whichever comes first."
    "day-orb": None,       # paused 2026-09-08; the feed spec gives it none
}

ATTRIBUTION_RULE = ("real round trips: entry on a day the autopilot placed an RSI2 decision BUY of that symbol "
                    "(source starting \"decision:\" -- not its swing-screen entries), or within the next 5 "
                    "trading days (the earliest unmatched entry claims the placement, decided once per entry "
                    "fill) → rsi2-real; opened and closed the same day → day-orb-real; otherwise → pullback-real")

# I3: a seam so build() can populate os.environ from .env itself -- a cold process (kestrel polling before any
# broker route has run) otherwise never sees WEBULL_RSI2_REAL_MAX_LOTS/WEBULL_AUTOPILOT_KILL_FILE (this was the
# same pattern plan_rsi2.py's default_readers() used before that module was archived 2026-09-28).
# tests/feed_fixture.write_desk stubs this to a no-op so no test ever loads the real .env; never writes .env,
# never logs its values.
_load_env = lambda: load_dotenv(override=False)


@dataclass(frozen=True)
class BookSpec:
    id: str
    name: str
    money: str                      # "real" | "paper"
    strategy: str
    cell: tuple[str, str] | None    # the tracks.py cell its status comes from (key, money)
    run_key: str | None             # the routine's run-log key for its next run


BOOKS: tuple[BookSpec, ...] = (
    BookSpec("rsi2-real", "RSI2 real", "real", "rsi2", ("swing_rsi2", "real"), "autopilot"),
    BookSpec("pullback-real", "Pullback (discretionary)", "real", "pullback", ("swing_pullback", "real"), None),
    BookSpec("day-orb-real", "Day trade trial", "real", "day-orb", ("day_orb", "real"), None),
    BookSpec("rsi2-paper", "RSI2 paper", "paper", "rsi2", ("swing_rsi2", "paper"), "note"),
)

# (id, name, the tracks.py cell its steps, summary and sizing come from)
STRATEGIES: tuple[tuple[str, str, tuple[str, str] | None], ...] = (
    ("rsi2", "Mean reversion (RSI2)", ("swing_rsi2", "paper")),
    ("pullback", "Pullback (discretionary)", ("swing_pullback", "real")),
    ("day-orb", "Day trade (ORB)", ("day_orb", "real")),
)
# The one-line summary kestrel shows on each strategy card, in plain words for someone who doesn't know the
# runners: the track cells' own summaries are operator notes (task names, codewords, gates) and stay in tracks.py.
CARD_SUMMARIES = {
    "rsi2": "Buys a stock after a sharp short-term drop (two-day RSI below 10) and sells into the rebound.",
    "pullback": "Buys a strong stock on a pullback, chosen by hand from an evening scan.",
    "day-orb": "Day trades breakouts from the first minutes' range, placed by hand.",
}
# The cell whose summary states the resting stop -- the "Protect" step (spec §4: "+ the stop rule when the track
# states it"); only the real RSI2 sleeve rests one (8% GTC).
PROTECT_CELLS = {"rsi2": ("swing_rsi2", "real")}
STEP_TITLES = {"Universe": "What it trades", "Entry": "When it buys", "Protect": "How it is protected",
               "Exit": "When it sells", "Session": "How a session runs"}


# ---------------------------------------------------------------- small helpers

def _err(e: BaseException) -> str:
    """The exception class, plus the file's name when one is known -- never the message (W5: a raw message can
    hold an absolute path, or worse). "PermissionError reading state.json", or just "PermissionError" when no
    file is known (e.g. a plain RuntimeError)."""
    name = getattr(e, "filename", None)
    cls = type(e).__name__
    return f"{cls} reading {Path(name).name}" if name else cls


def _num(v) -> float | None:
    """A finite float, or None: a NaN or an infinity would make the JSON response itself fail."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _unusable(value) -> bool:
    """True for a value kestrel's own contract rejects the WHOLE document over: a non-finite number, a number whose
    magnitude exceeds MAX_MAGNITUDE, or a bare ISO date ("YYYY-MM-DD") outside MIN_DATE..MAX_DATE (M1+M2). The feed
    drops just the item that holds it instead (mirrored in tests/test_feed_contract.py's `_check_document`)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not math.isfinite(value) or abs(value) > MAX_MAGNITUDE
    if isinstance(value, str) and len(value) == 10 and value[4:5] == "-" and value[7:8] == "-":
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return not (MIN_DATE <= value <= MAX_DATE)
    return False


def _has_unusable(node) -> bool:
    """Walk a dict/list for any _unusable leaf value (M1+M2's last pass)."""
    if isinstance(node, dict):
        return any(_has_unusable(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_unusable(v) for v in node)
    return _unusable(node)


def _day(stamp) -> str | None:
    """A timestamp's ET date (naive stamps are ET, the repo convention), or a bare YYYY-MM-DD as given."""
    s = str(stamp or "")
    if len(s) == 10:
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            return None
    at = _et(s)
    return at.date().isoformat() if at is not None else None


def _cell(key: str, money: str):
    return next((t for t in TRACKS if t.key == key and t.money == money), None)


def _read_json(path: Path, missing=None):
    """Strict: a torn file raises (the caller's block becomes a note), a missing one is `missing`."""
    if not path.exists():
        if missing is None:
            err = FileNotFoundError(f"no file at {path.name}")
            err.filename = str(path)
            raise err
        return missing
    return json.loads(path.read_text(encoding="utf-8"))


class _Files:
    """Every file the document reads, read at most once per request. A read that fails raises for every caller, so
    the blocks that need that file fail together and the rest still build."""

    def __init__(self, now: datetime):
        self.since = (now.date() - timedelta(days=BAR_HORIZON_DAYS)).isoformat()
        self._memo: dict[str, tuple[bool, object]] = {}

    def _once(self, key: str, fn):
        if key not in self._memo:
            try:
                self._memo[key] = (True, fn())
            except Exception as e:
                self._memo[key] = (False, e)
        ok, value = self._memo[key]
        if not ok:
            raise value
        return value

    def fills(self) -> tuple[list[Fill], int]:
        """(fills, lines skipped). A line that isn't JSON (a crash mid-append) or isn't a fill is skipped and
        counted, never fatal; duplicates of an id keep the first, as journal_store does."""
        def read():
            path = journal_store._dir() / journal_store._FILLS
            if not path.exists():
                return [], 0
            out: list[Fill] = []
            seen: set = set()
            skipped = 0
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    fill = Fill.model_validate(json.loads(line))
                except ValueError:
                    skipped += 1
                    continue
                if fill.id in seen:
                    continue
                seen.add(fill.id)
                out.append(fill)
            return out, skipped
        return self._once("fills", read)

    def placements(self) -> set[tuple[str, str]]:
        """(SYMBOL, day) for every RSI2 decision BUY the autopilot placed: autopilot/log/<day>.jsonl rows with
        placed: true and a source starting "decision:" (I1) -- NOT every placed BUY. The autopilot's 17:45 run also
        places swing-screen (pullback) entries logged `source: "entry"`; those must not count here, or a pullback
        round trip would be double-counted as rsi2-real. rsi2_real_attribution.py's own rule can't be reused: it
        needs the action log (state this feed doesn't read) to attribute an ADOPTED lot, where this only needs to
        know which fills the autopilot itself placed as an RSI2 decision."""
        def read():
            out: set[tuple[str, str]] = set()
            folder = autopilot_paths.autopilot_dir() / "log"
            for p in sorted(folder.glob("????-??-??.jsonl")) if folder.exists() else []:
                for line in p.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if (isinstance(row, dict) and row.get("placed") is True and row.get("side") == "BUY"
                            and str(row.get("source") or "").startswith("decision:")):
                        out.add((str(row.get("symbol") or "").strip().upper(), p.stem))
            return out
        return self._once("placements", read)

    def protect_events(self) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """({SYMBOL: sorted PROTECT-SELL days}, {SYMBOL: sorted clearing-SELL days}) from the autopilot audit log
        (2026-09-28 stop-resting design): the feed reports only WHETHER a resting protective stop is known from the
        log, never its level (that lives in `webull_api/reconcile.py`'s order-path planner, which this module must
        never import). A PROTECT day qualifies when a row has `source == "protect"` EXACTLY (never
        `"protect:synthetic"`, which sells a fractional remainder, not a resting order), `side == "SELL"`,
        `placed is True` and `result.submitted is True`. A clearing day qualifies when a row's `source` starts
        `"exit:"` or `"decision:"`, `side == "SELL"` and `placed is True` -- either can supersede/cancel a resting
        stop. Bounded to the day files on/after the earliest OPEN real lot's entry day (the RSI2-real ledger's own
        lots plus the real pairing's open remainders, the two `_Files` already knows) -- nothing before any real
        lot could ever qualify for it, so there is no reason to read it. A line that isn't JSON (a crash
        mid-append) is skipped, same as every other jsonl reader here."""
        def read():
            try:
                since_days = [lot["entry_date"] for lot in self.rsi2_real_lots()]
                _, remainders = self.real()
                since_days += [d for d in (_day(o.opened_at_iso) for o in remainders) if d]
            except Exception:
                # A torn rsi2-real ledger or fills.jsonl is already reported by the book builder's own guard (it
                # calls the same sources directly); raising the SAME failure again here would just add a second,
                # misleadingly-worded note about "the autopilot audit log" for what is really a lots/pairing
                # problem. Reading nothing is safe: with no known lot, nothing could qualify anyway.
                since_days = []
            if not since_days:
                return {}, {}
            since = min(since_days)
            protect: dict[str, list[str]] = {}
            clearing: dict[str, list[str]] = {}
            folder = autopilot_paths.autopilot_dir() / "log"
            for p in sorted(folder.glob("????-??-??.jsonl")) if folder.exists() else []:
                if p.stem < since:
                    continue
                for line in p.read_text(encoding="utf-8").splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict) or row.get("side") != "SELL" or row.get("placed") is not True:
                        continue
                    symbol = str(row.get("symbol") or "").strip().upper()
                    if not symbol:
                        continue
                    source = str(row.get("source") or "")
                    if source == "protect":
                        result = row.get("result")
                        if isinstance(result, dict) and result.get("submitted") is True:
                            protect.setdefault(symbol, []).append(p.stem)
                    elif source.startswith("exit:") or source.startswith("decision:"):
                        clearing.setdefault(symbol, []).append(p.stem)
            return protect, clearing
        return self._once("protect_events", read)

    def real(self) -> tuple[dict[str, list], list]:
        """The real fills paired and attributed: ({book id: [ClosedTrade]}, open remainders). Equity round trips are
        attributed in ascending entry-day order (ties by entry timestamp) so that when a placement's window (W1)
        could match several entries, the earliest unmatched one claims it -- each placement is then removed from
        the pool so a later entry in the same window falls through to the ordinary rule.

        I2: a label is decided ONCE per entry fill, not per ClosedTrade -- FIFO can slice one BUY into several
        ClosedTrades (a partially filled exit, or a whole-share stop plus a protect:synthetic sell of the
        fractional remainder) that all share the same `entry_fill_id`/`entry_at_iso`, and can leave the tail of
        that same fill as the pairing's open remainder. Every slice of a fill, and its open remainder if any, are
        keyed by (symbol, entry timestamp) -- pairing.OpenPosition carries no fill id of its own, but does carry
        the same unmutated timestamp (`lot["time"]`) as every ClosedTrade sliced from that lot -- so the first
        slice seen decides the label and every later one just reuses it, and an open remainder that matches a
        placement claims it too, so a later same-symbol round trip inside the same window can't also take it. An
        open remainder decided rsi2-real is filtered out of what `_pullback_real` sees (the ambiguity ruling):
        rsi2-real's positions come only from its ledger's owned lots, so such a remainder shows nowhere until the
        ledger adopts it, rather than leaking into the discretionary book."""
        def read():
            fills, _ = self.fills()
            closed, opens = pairing.pair_fills([f for f in fills if f.source == "real"])
            placed = set(self.placements())
            by_book: dict[str, list] = {"rsi2-real": [], "pullback-real": [], "day-orb-real": []}
            equity = [c for c in closed if c.instrument == "equity"]
            label_of: dict[tuple[str, str], str] = {}

            def decide(symbol: str, ts: str, entry_day: str | None, exit_day: str | None) -> str:
                key = (symbol, ts)
                if key in label_of:
                    return label_of[key]
                label = attribute(symbol, entry_day, exit_day, placed)
                if label == "rsi2-real":
                    used = _matching_placement(symbol, entry_day, placed)
                    if used is not None:
                        placed.discard(used)
                label_of[key] = label
                return label

            entries = [(_day(c.entry_at_iso) or "", c.entry_at_iso, "closed", c) for c in equity]
            entries += [(_day(o.opened_at_iso) or "", o.opened_at_iso, "open", o) for o in opens]
            entries.sort(key=lambda e: (e[0], e[1]))
            for entry_day, ts, kind, item in entries:
                exit_day = _day(item.exit_at_iso) if kind == "closed" else None
                label = decide(item.symbol, ts, entry_day or None, exit_day)
                if kind == "closed":
                    by_book[label].append(item)
            opens = [o for o in opens if label_of.get((o.symbol, o.opened_at_iso)) != "rsi2-real"]
            return by_book, opens
        return self._once("real", read)

    def paper_closed(self) -> list:
        """M10: fills.jsonl's `paper` source mixes every simulator account, including the proven-strategy runner's
        own `proven` account (its fills carry a `strategy_id`) -- only the default paper account's fills with no
        `strategy_id` are rsi2-paper's."""
        def read():
            fills, _ = self.fills()
            acct_id = str((self.paper_account() or {}).get("account_id") or "")
            want = [f for f in fills if f.source == "paper" and f.strategy_id is None
                   and (not acct_id or f.account_id == acct_id)]
            closed, _opens = pairing.pair_fills(want)
            return [c for c in closed if c.instrument == "equity"]
        return self._once("paper_closed", read)

    def rsi2_real_lots(self) -> list[dict]:
        return self._once("rsi2_real", lambda: _lots(_read_json(rsi2_real_store._dir() / rsi2_real_store._FILE, {})))

    def rsi2_paper_lots(self) -> list[dict]:
        return self._once("rsi2_paper", lambda: _lots(_read_json(rsi2_store._dir() / rsi2_store._FILE, {})))

    def paper_account(self) -> dict:
        return self._once("paper", lambda: _read_json(paper_store._path()))

    def runs(self) -> list[dict]:
        return self._once("runs", run_log.load)

    def netliq(self) -> list[dict]:
        return self._once("netliq", netliq_store.load)

    def bench_runs(self) -> list[dict]:
        """Every readable row of the bench ledger (`data/bench/runs.jsonl`), read directly -- never through
        the (archived 2026-09-29) bench Ledger, whose `append` wrote. A line that isn't JSON (a crash mid-append) is
        skipped, exactly as Ledger.rows() skips one, and never fatal to the rest of the feed."""
        def read():
            path = data_dir("bench", "BENCH_DIR") / "runs.jsonl"
            if not path.exists():
                return []
            out = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
            return out
        return self._once("bench_runs", read)

    def _all_bars(self, symbol: str) -> list[dict]:
        """Every usable bar row in the symbol's Tiingo CSV, oldest first -- the file is read and parsed at most
        ONCE per request (M3), regardless of how many of `bars()`/`chart_bars()` a symbol needs; both filter this
        by their own `since` in memory instead of each doing their own file read."""
        return self._once(f"rows:{symbol.strip().upper()}", lambda: _read_bars(symbol, ""))

    def bars(self, symbol: str) -> list[dict]:
        """The symbol's daily bars within the freshness horizon, oldest first (for MARKING a position's last
        price); [] when the store has no file for it, or its latest bar is older than BAR_HORIZON_DAYS."""
        return [r for r in self._all_bars(symbol) if r["date"] >= self.since]

    def chart_bars(self, symbol: str, since: str) -> list[dict]:
        """The symbol's daily bars for CHART building (W2), from `since` -- the symbol's earliest charted open
        minus CHART_SINCE_PAD_DAYS, not the request's `now`-relative freshness horizon."""
        return [r for r in self._all_bars(symbol) if r["date"] >= since]

    def backtest(self, path: Path) -> dict:
        return self._once(f"backtest:{path}", lambda: _read_json(path))


def _read_bars(symbol: str, since: str) -> list[dict]:
    """{date, open, high, low, close} rows on or after `since` from the symbol's Tiingo CSV (tiingo_store.path), raw
    prices -- the ones the fills were made at. Older lines are dropped by their date before any number is parsed; a
    row with an unusable price is skipped, as tiingo_store.read skips it (its own _f parses each price)."""
    path = tiingo_store.path(symbol)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return []
    rows = csv.reader([lines[0]] + [line for line in lines[1:] if line[:10] >= since])
    at = {name: i for i, name in enumerate(next(rows))}
    out = []
    for row in rows:
        try:
            day = row[at["date"]]
            prices = {c: tiingo_store._f(row[at[c]], c) for c in ("open", "high", "low", "close")}
        except (IndexError, KeyError):
            continue
        if len(day) == 10 and None not in prices.values():
            out.append({"date": day, **prices})
    out.sort(key=lambda r: r["date"])
    return out


def _lots(state) -> list[dict]:
    """A ledger's owned lots as {symbol, shares, entry_price, entry_date}; a lot missing any of them is skipped."""
    out = []
    for lot in (state.get("owned_lots") or []) if isinstance(state, dict) else []:
        sym, opened = str(lot.get("symbol") or "").strip().upper(), _day(lot.get("entry_date"))
        shares, entry = _num(lot.get("shares")), _num(lot.get("entry_price"))
        if sym and shares is not None and entry is not None and opened:
            out.append({"symbol": sym, "shares": shares, "entry_price": entry, "entry_date": opened})
    return out


def _placement_window(day: str) -> list[str]:
    """[day] plus its next 5 NYSE trading days, as ISO strings -- the fill days a placement on `day` can still
    match (W1): a resting/GTC autopilot BUY can fill later than the day it was placed."""
    d = date.fromisoformat(day)
    out = [d]
    while len(out) <= 5:
        d += timedelta(days=1)
        if routine.is_trading_day(d):
            out.append(d)
    return [x.isoformat() for x in out]


def _matching_placement(symbol: str, entry_day: str | None, placements: set[tuple[str, str]]) -> tuple[str, str] | None:
    """The oldest placement of `symbol` whose window (W1) contains `entry_day`, or None."""
    if entry_day is None:
        return None
    sym = str(symbol).strip().upper()
    candidates = sorted((s, d) for s, d in placements if s == sym and entry_day in _placement_window(d))
    return candidates[0] if candidates else None


def attribute(symbol: str, entry_day: str | None, exit_day: str | None, placements: set[tuple[str, str]]) -> str:
    """The real book a round trip belongs to -- fills carry no strategy tag (spec §3). Written once; every real book
    reads it and the source row names it (ATTRIBUTION_RULE):
      1. rsi2-real      its entry day is on, or within 5 trading days after, a day the autopilot placed a BUY of
                        that symbol (W1: a resting/GTC placement can fill a later day);
      2. day-orb-real   it opened and closed the same day;
      3. pullback-real  everything else.
    `real()` calls this per entry in ascending entry-day order and removes a placement once an entry claims it, so
    when a placement's window could match several entries, the earliest unmatched one takes it (W1 ambiguity
    ruling) -- this function alone just answers whether `placements` (whatever is left unclaimed) matches."""
    if _matching_placement(symbol, entry_day, placements) is not None:
        return "rsi2-real"
    if entry_day is not None and entry_day == exit_day:
        return "day-orb-real"
    return "pullback-real"


def _stale_cutoff(today: date) -> date:
    """The date STALE_SESSIONS NYSE trading days before `today` (M4 ambiguity ruling: the same trading-day helper
    W1 used, `routine.is_trading_day`) -- a mark from a close strictly before this date is stale."""
    d, n = today, 0
    while n < STALE_SESSIONS:
        d -= timedelta(days=1)
        if routine.is_trading_day(d):
            n += 1
    return d


def _mark(files: _Files, symbol: str, entry: float, today: date) -> tuple[float, str]:
    """(last price, note): the latest close in the Tiingo store, else the entry price, said so. That close is still
    used even when it's stale (M4): a close more than STALE_SESSIONS NYSE trading days before `today` gets a note
    naming its date instead of silently standing in as if it were current."""
    try:
        rows = files.bars(symbol)
    except Exception:
        rows = []
    if not rows:
        return entry, "no recent price in the Tiingo store: marked at the entry price"
    last = _num(rows[-1]["close"])
    if last is None:
        return entry, "no recent price in the Tiingo store: marked at the entry price"
    if rows[-1]["date"] < _stale_cutoff(today).isoformat():
        return last, f"last close {rows[-1]['date']}"
    return last, ""


def _protect_events(files: _Files, notes: list) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """`files.protect_events()`, guarded on its own -- a failure reading the autopilot audit log must not take a
    real book's positions down with it (unlike the book builder's own outer guard, which would drop the whole
    book): it's noted like every other block, and every position simply reports no known stop (never a fabricated
    False). Called once per real book, but `files.protect_events()` itself is read-once and memoized (`_Files`),
    so a failure is noted once, not once per book that calls this."""
    title = "Left out of the feed: the autopilot audit log (protective stops)"
    try:
        return files.protect_events()
    except Exception as e:
        if not any(t == title for t, _ in notes):
            notes.append((title, _err(e)))
        return {}, {}


def _stop_resting(events: tuple[dict[str, list[str]], dict[str, list[str]]], symbol: str,
                  entry_day: str) -> tuple[bool | None, str]:
    """(stop_resting, a note to append) for one REAL lot: True, with "protective stop placed <date> (level not
    reported)", when the latest qualifying PROTECT day on/after `entry_day` has no qualifying clearing day after
    it; otherwise (None, "") -- the log's silence never proves there is no stop, so this NEVER returns False."""
    protect, clearing = events
    sym = symbol.strip().upper()
    days = sorted(d for d in protect.get(sym, []) if d >= entry_day)
    if not days:
        return None, ""
    latest = days[-1]
    if any(d > latest for d in clearing.get(sym, [])):
        return None, ""
    return True, f"protective stop placed {latest} (level not reported)"


def _position(files: _Files, book_id: str, symbol: str, qty: float, entry: float, opened: str, today: date,
              *, stop_events: tuple[dict, dict] | None = None) -> dict:
    last, note = _mark(files, symbol, entry, today)
    stop_resting = None
    if stop_events is not None:
        stop_resting, stop_note = _stop_resting(stop_events, symbol, opened)
        if stop_note:
            note = f"{note}; {stop_note}" if note else stop_note
    return {"book_id": book_id, "symbol": symbol, "quantity": round(qty, 6), "entry_price": round(entry, 4),
            "last_price": round(last, 4), "stop_price": None, "opened": opened, "note": note,
            "stop_resting": stop_resting}


def _trade(book_id, symbol, opened, closed, entry, exit_, qty, pnl, return_pct, r_multiple=None, reason="") -> dict:
    return {"book_id": book_id, "symbol": symbol, "opened": opened, "closed": closed, "entry_price": round(entry, 4),
            "exit_price": round(exit_, 4), "quantity": round(qty, 6), "pnl": round(pnl, 2),
            "return_pct": round(return_pct, 4), "r_multiple": r_multiple, "exit_reason": reason or ""}


def _from_closed(book_id: str, c) -> dict | None:
    opened, closed = _day(c.entry_at_iso), _day(c.exit_at_iso)
    if not opened or not closed:
        return None
    return _trade(book_id, c.symbol, opened, closed, c.entry_price, c.exit_price, c.quantity, c.pnl, c.return_pct)


def _closed_trades(book_id: str, closed: list) -> tuple[list[dict], int]:
    """(trades, skipped): `_from_closed` over pairing's ClosedTrades, counting how many are dropped for an
    unparseable timestamp (M9) -- pairing accepts epoch-millisecond stamps but `_day`/`_et` don't, so such a trade
    would otherwise vanish with no trace, the way a skipped fills.jsonl line already is counted."""
    trades, skipped = [], 0
    for c in closed:
        t = _from_closed(book_id, c)
        if t is None:
            skipped += 1
        else:
            trades.append(t)
    return trades, skipped


def _recent(trades: list[dict]) -> list[dict]:
    return sorted(trades, key=lambda t: (t["closed"], t["opened"], t["symbol"]))[-MAX_TRADES:]


def _drop_bad(items: list[dict], block: str, notes: list) -> list[dict]:
    """M1+M2: drop the smallest unit (a book, position, trade or chart) that holds a value kestrel's contract
    would reject the whole document over, with one note per block naming how many were dropped."""
    keep = [item for item in items if not _has_unusable(item)]
    dropped = len(items) - len(keep)
    if dropped:
        notes.append((f"{block}: {dropped} item{'s' if dropped > 1 else ''} dropped (a value kestrel can't show)",
                     ""))
    return keep


# ---------------------------------------------------------------- books

def _part(spec: BookSpec, *, value: float, positions: list[dict], trades: list[dict], history: list[dict] | None,
          today: date, stops: dict | None = None, skipped: int = 0) -> dict:
    """M2: a date build()'s last pass will drop (out of kestrel's 1970-2200 range) must not first drag this book's
    OWN `started` row down to it -- excluded from the aggregate here, not just from the list build() prunes."""
    dates = ([t["opened"] for t in trades if not _unusable(t["opened"])] +
            [p["opened"] for p in positions if not _unusable(p["opened"])] +
            [h["date"] for h in (history or []) if not _unusable(h["date"])])
    started = min(dates) if dates else today.isoformat()
    return {"spec": spec, "value": round(value, 2), "started": started, "positions": positions,
            "trades": _recent(trades), "history": history, "stops": stops or {}, "skipped": skipped}


def _rsi2_real(files: _Files, spec: BookSpec, today: date, notes: list) -> dict:
    by_book, _ = files.real()
    stop_events = _protect_events(files, notes)
    positions = [_position(files, spec.id, lot["symbol"], lot["shares"], lot["entry_price"], lot["entry_date"], today,
                           stop_events=stop_events)
                 for lot in files.rsi2_real_lots()]
    trades, skipped = _closed_trades(spec.id, by_book[spec.id])
    return _part(spec, value=sum(p["quantity"] * p["last_price"] for p in positions), positions=positions,
                 trades=trades, history=None, today=today, skipped=skipped)


def _pullback_real(files: _Files, spec: BookSpec, today: date, notes: list) -> dict:
    """The open remainder of the real pairing that the RSI2 ledger doesn't own is the discretionary book's."""
    by_book, opens = files.real()
    stop_events = _protect_events(files, notes)
    owned: dict[str, float] = {}
    for lot in files.rsi2_real_lots():
        owned[lot["symbol"]] = owned.get(lot["symbol"], 0.0) + lot["shares"]
    positions = []
    for o in opens:
        rest = float(o.quantity) - owned.get(o.symbol.strip().upper(), 0.0)
        opened = _day(o.opened_at_iso)
        if rest > _EPS and opened and _num(o.avg_entry_price):
            positions.append(_position(files, spec.id, o.symbol, rest, float(o.avg_entry_price), opened, today,
                                       stop_events=stop_events))
    trades, skipped = _closed_trades(spec.id, by_book[spec.id])
    return _part(spec, value=sum(p["quantity"] * p["last_price"] for p in positions), positions=positions,
                 trades=trades, history=None, today=today, skipped=skipped)


def _day_orb_real(files: _Files, spec: BookSpec, today: date, notes: list) -> dict:
    by_book, _ = files.real()
    trades, skipped = _closed_trades(spec.id, by_book[spec.id])
    return _part(spec, value=0.0, positions=[], trades=trades, history=None, today=today, skipped=skipped)


def _rsi2_paper(files: _Files, spec: BookSpec, today: date, notes: list) -> dict:
    """Value = the paper account's cash + every position at the latest close (the account's money, whoever holds
    it); positions = the RSI2 ledger's own lots."""
    acct = files.paper_account()
    value = _num(acct.get("cash")) or 0.0
    for sym, pos in (acct.get("positions") or {}).items():
        qty, cost = _num((pos or {}).get("quantity")), _num((pos or {}).get("avg_cost"))
        if qty:
            value += qty * _mark(files, str(sym), cost or 0.0, today)[0]
    positions = [_position(files, spec.id, lot["symbol"], lot["shares"], lot["entry_price"], lot["entry_date"], today)
                 for lot in files.rsi2_paper_lots()]
    trades, skipped = _closed_trades(spec.id, files.paper_closed())
    history: dict[str, float] = {}
    for row in files.netliq():
        d, v = _day(row.get("date")), _num(row.get("paper_equity"))
        if d and v is not None:
            history[d] = v
    return _part(spec, value=value, positions=positions, trades=trades, today=today, skipped=skipped,
                 history=[{"date": d, "value": round(v, 2), "net_flow": 0.0} for d, v in sorted(history.items())])


_BUILDERS = {"rsi2-real": _rsi2_real, "pullback-real": _pullback_real, "day-orb-real": _day_orb_real,
             "rsi2-paper": _rsi2_paper}


def _status(spec: BookSpec) -> str:
    cell = _cell(*spec.cell) if spec.cell else None
    return "running" if cell is not None and cell.state == "running" else "paused"


def _emitted(spec: BookSpec) -> bool:
    """2026-09-29 (owner: one real strategy): a book reaches kestrel only while its track runs -- derived from
    tracks.py/routine state, so re-enabling a track brings its book (and strategy) back."""
    return _status(spec) == "running"


def _backtest_emitted(row: dict, strategy_ids: set[str]) -> bool:
    """A backtest reaches kestrel only when it belongs to an emitted strategy; bench/lab rows carry none."""
    return row.get("strategy_id") in strategy_ids


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    try:
        v = float(raw) if raw else None
    except ValueError:
        return None
    return v if v is not None and math.isfinite(v) else None


def _rsi2_sizing() -> str:
    """The real RSI2 sleeve's sizing basis from the live env, the same variables rsi2_real_service sizes by
    (rsi2.real_budget: min(net liq / divisor, autopilot cap, DOLLARS)). Text only -- no broker call."""
    divisor = _env_float("WEBULL_RSI2_REAL_SLOT_DIVISOR")
    dollars = _env_float("WEBULL_RSI2_REAL_DOLLARS")
    if divisor is None or divisor <= 0:
        if dollars is None:
            return "Sizing: fixed — settled cash per lot (no per-lot dollars set)"
        return f"Sizing: fixed ${dollars:,.0f} per lot"
    from webull_api.autopilot.config import AutopilotConfig
    caps = [float(AutopilotConfig.from_env().max_notional)] + ([dollars] if dollars is not None else [])
    d = f"{divisor:g}"
    return f"Sizing: net liq ÷ {d} (S{d}), capped at ${min(caps):,.0f}/lot"


def _slots(spec: BookSpec) -> int | None:
    if spec.id == "rsi2-real":
        # the same variable and default rsi2_real_service sizes the sleeve by (the owner's .env sets 5)
        try:
            return int(os.environ.get("WEBULL_RSI2_REAL_MAX_LOTS", "1"))
        except ValueError:
            return None
    return {"rsi2-paper": RSI2_CONFIG.max_lots}.get(spec.id)


def _book(part: dict, next_run: str | None) -> dict:
    spec = part["spec"]
    return {"id": spec.id, "name": spec.name, "money": spec.money, "strategy_id": spec.strategy, "account_id": None,
            "status": _status(spec), "started": part["started"], "value": part["value"], "slots_total": _slots(spec),
            "next_run": next_run}


# ---------------------------------------------------------------- charts

def _indicator(kind: str | None, rows: list[dict], lo: int, hi: int) -> dict | None:
    if kind == "rsi2":
        start = lo - RSI_WARMUP
        if start < 0:
            return None       # W2: fewer than RSI_WARMUP sessions precede the window -- no indicator, not a guess
        values = indicators.rsi([r["close"] for r in rows[start:hi]], 2)[lo - start:]
        return {"label": "RSI(2)", "values": [round(v, 2) if v is not None else None for v in values],
                "lines": [{"value": RSI2_CONFIG.entry_below, "label": f"buy under {RSI2_CONFIG.entry_below:g}"},
                          {"value": RSI2_CONFIG.exit_above, "label": f"sell over {RSI2_CONFIG.exit_above:g}"}]}
    return None


def _chart(files: _Files, book_id: str, symbol: str, opened: str, closed: str | None, kind: str | None,
           stop: float | None, since: str) -> dict | None:
    """Daily bars from BARS_BEFORE sessions before the open to BARS_AFTER after the close (an open position: to
    the latest bar). None when the store has no bar on the open date -- kestrel then says there is no chart."""
    rows = files.chart_bars(symbol, since)
    dates = [r["date"] for r in rows]
    i = bisect.bisect_left(dates, opened)
    if i >= len(dates) or dates[i] != opened:
        return None
    lo = max(0, i - BARS_BEFORE)
    hi = len(rows) if closed is None else min(len(rows), bisect.bisect_right(dates, closed) + BARS_AFTER)
    bars = [{"date": r["date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]}
            for r in rows[lo:hi]]
    return {"book_id": book_id, "symbol": symbol, "opened": opened, "bars": bars,
            "indicator": _indicator(kind, rows, lo, hi), "stop": stop}


_INDICATOR = {"rsi2": "rsi2"}


def _wanted(part: dict) -> list[tuple[str, str, str | None]]:
    """(symbol, opened, closed) for the book's last CHARTS_PER_BOOK closed trades, then each open position -- the
    charts `_charts()` builds, and (W2) the source `build()` scans to find each symbol's earliest charted open."""
    wanted = [(t["symbol"], t["opened"], t["closed"]) for t in part["trades"][-CHARTS_PER_BOOK:]]
    wanted += [(p["symbol"], p["opened"], None) for p in part["positions"]]
    return wanted


def _chart_since(earliest_opened: str) -> str:
    """W2: a symbol's chart bars start CHART_SINCE_PAD_DAYS calendar days before its earliest charted open -- not a
    fixed, `now`-relative horizon -- so a warm-up-starved RSI(2) shows None instead of a wrong value."""
    return (date.fromisoformat(earliest_opened) - timedelta(days=CHART_SINCE_PAD_DAYS)).isoformat()


def _charts(files: _Files, part: dict, earliest: dict[str, str]) -> list[dict]:
    """The book's last CHARTS_PER_BOOK closed trades, then each open position; one chart per (symbol, opened)."""
    spec = part["spec"]
    kind = _INDICATOR.get(spec.strategy)
    out, seen = [], set()
    for symbol, opened, closed in _wanted(part):
        if (symbol, opened) in seen:
            continue
        seen.add((symbol, opened))
        since = _chart_since(earliest[symbol])
        chart = _chart(files, spec.id, symbol, opened, closed, kind, part["stops"].get((symbol, opened)), since)
        if chart is not None:
            out.append(chart)
    return out


# ---------------------------------------------------------------- strategies

def _distribution(returns: list[float]) -> list[float]:
    """Share of trades (%) per 1-point bucket, -10% .. +10%, the tails folded into the end buckets: plan_stats'
    0.5-point histogram, two bins to a bucket."""
    bins = plan_stats.histogram(returns)            # [low tail, 40 x 0.5%, high tail]
    counts = [0] * BUCKETS
    for b, row in enumerate(bins):
        k = 0 if b == 0 else BUCKETS - 1 if b == len(bins) - 1 else (b - 1) // 2
        counts[k] += row["count"]
    n = len(returns)
    return [round(c / n * 100.0, 2) for c in counts] if n else []


def _expected(files: _Files, sid: str) -> dict | None:
    """The plan's backtest (spec §4), from plan_stats -- the helpers the plan pages use on the same files."""
    path = {"rsi2": RSI2_BACKTEST, "day-orb": ORB_BACKTEST}.get(sid)
    if path is None:
        return None
    doc = files.backtest(path)
    trades = [t for t in doc.get("trades") or [] if _num(t.get("return_pct")) is not None]
    if not trades:
        return None                  # the ORB review holds per-cell summaries, no trades: no expectation
    returns = [float(t["return_pct"]) for t in trades]
    stats = plan_stats.describe(returns)
    days = sorted(d for t in trades for d in (_day(t.get("entry_date")), _day(t.get("exit_date"))) if d)
    first, last = (days[0], days[-1]) if days else (None, None)
    months = max((date.fromisoformat(last) - date.fromisoformat(first)).days / 30.4375, 1.0) if days else None
    curve = [{"date": e.get("date"), "value": e.get("equity")} for e in doc.get("equity_curve") or []]
    over = plan_stats.time_stats(curve) if curve else None
    return {"win_rate": round(stats["win_rate"] * 100.0, 2), "avg_trade_pct": round(stats["mean_pct"], 4),
            "avg_win_pct": round(stats["avg_win_pct"] or 0.0, 4),
            "avg_loss_pct": round(-(stats["avg_loss_pct"] or 0.0), 4),
            "trades_per_month": round(len(trades) / months, 2) if months else 0.0,
            "sd_trade_pct": round(stats["sd_pct"] or 0.0, 4), "distribution": _distribution(returns),
            "source": EXPECTED_LABELS[sid], "window": f"{first[:4]}–{last[:4]}" if days else "",
            "cagr_pct": round(over["cagr_pct"], 2) if over and over["cagr_pct"] is not None else None,
            "max_drawdown_pct": round(-over["max_dd_pct"], 2) if over and over["max_dd_pct"] is not None else None}


def _steps(sid: str, cell) -> list[dict]:
    if cell is None:
        return []
    pairs = [("Universe", cell.universe), ("Entry", cell.entry)]
    protect = _cell(*PROTECT_CELLS[sid]) if sid in PROTECT_CELLS else None
    if protect is not None:
        pairs.append(("Protect", protect.summary))
    pairs.append(("Exit", cell.exit))
    return [{"label": label, "title": STEP_TITLES[label], "text": text, "params": []} for label, text in pairs if text]


def _strategy(files: _Files, sid: str, name: str, cell_ref) -> dict:
    cell = _cell(*cell_ref) if cell_ref else None
    summary, sizing = (cell.summary, cell.book) if cell is not None else ("", "")
    if sid == "rsi2":
        sizing = _rsi2_sizing()          # the REAL sleeve's basis; the cell's text is the parked paper book's
    return {"id": sid, "name": name, "summary": CARD_SUMMARIES.get(sid, summary), "steps": _steps(sid, cell), "sizing": sizing,
            "expected": _expected(files, sid), "review_at_trades": REVIEW_AT_TRADES.get(sid), "watch": []}


# ---------------------------------------------------------------- backtests (the bench ledger -> kestrel Backtest)

_BACKTEST_VERDICTS = ("pass", "fail", "refused")


def _backtest_verdict(v) -> str:
    """kestrel's Backtest.verdict is pass/fail/refused/pending; the bench ledger's own outcome can also be "error"
    (a candidate that raised) or absent -- anything that isn't one of the three real verdicts reads as pending
    (2026-09-27 kestrel feed export plan Task 2)."""
    return v if v in _BACKTEST_VERDICTS else "pending"


def _backtest_at(row: dict) -> datetime | None:
    """The row's `at` as an aware datetime, or None when it's missing or not a parseable ISO-with-offset stamp --
    such a row is skipped entirely, the same as a torn ledger line."""
    raw = row.get("at")
    if not isinstance(raw, str):
        return None
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


def _int(v) -> int | None:
    """A plain int within kestrel's MAX_MAGNITUDE, or None -- kestrel's Backtest.trades is `int | None`; the
    ledger's own `n` should already be an int, but a whole-number float is coerced rather than assumed. An
    out-of-range value goes to None (2026-09-27 follow-up: this OPTIONAL field must blank itself rather than take
    the whole backtest row down in the later M1+M2 pass)."""
    if isinstance(v, bool):
        n = None
    elif isinstance(v, int):
        n = v
    elif isinstance(v, float) and math.isfinite(v):
        n = int(v)
    else:
        n = None
    return n if n is not None and abs(n) <= MAX_MAGNITUDE else None


def _backtest_num(v) -> float | None:
    """A finite float within kestrel's MAX_MAGNITUDE, or None -- an OPTIONAL Backtest number (avg_trade_pct,
    t_stat, calmar, max_drawdown_pct) that fails either check blanks just this field (2026-09-27 follow-up: a real
    ledger row's out-of-range calmar/t_stat was otherwise dropping the whole backtest via the later M1+M2 pass,
    which only knows how to drop a whole item, and was showing kestrel a permanent "1 item dropped" note)."""
    n = _num(v)
    return n if n is not None and abs(n) <= MAX_MAGNITUDE else None


def _backtest(row: dict, at: datetime) -> dict:
    calmar = row.get("stacked_calmar")
    if calmar is None:
        calmar = row.get("base_calmar")
    failed = row.get("failed")
    return {"id": f"{row.get('hash')}:{row.get('window')}", "name": row.get("name") or row.get("hash") or "",
            "family": row.get("family") or "", "window": row.get("window") or "",
            "verdict": _backtest_verdict(row.get("verdict")), "at": at.isoformat(), "strategy_id": None,
            "trades": _int(row.get("n")), "avg_trade_pct": _backtest_num(row.get("expectancy_pct")),
            "t_stat": _backtest_num(row.get("t_stat")), "calmar": _backtest_num(calmar), "max_drawdown_pct": None,
            "note": ", ".join(failed) if isinstance(failed, list) else ""}


def _backtests(files: _Files) -> list[dict]:
    """`type == "run"` rows of the bench ledger mapped to kestrel Backtests: the newest row per (hash, window),
    then every `pass` plus each (family, window)'s newest, capped at the newest MAX_BACKTESTS overall."""
    dated: list[tuple[dict, datetime]] = []
    for row in files.bench_runs():
        if not isinstance(row, dict) or row.get("type") != "run":
            continue
        at = _backtest_at(row)
        if at is not None:
            dated.append((row, at))

    newest: dict[tuple, tuple[dict, datetime]] = {}
    for row, at in dated:
        key = (row.get("hash"), row.get("window"))
        if key not in newest or at > newest[key][1]:
            newest[key] = (row, at)
    candidates = list(newest.values())

    selected: dict[tuple, tuple[dict, datetime]] = {}
    for row, at in candidates:
        if _backtest_verdict(row.get("verdict")) == "pass":
            selected[(row.get("hash"), row.get("window"))] = (row, at)
    family_newest: dict[tuple, tuple[dict, datetime]] = {}
    for row, at in candidates:
        key = (row.get("family"), row.get("window"))
        if key not in family_newest or at > family_newest[key][1]:
            family_newest[key] = (row, at)
    for row, at in family_newest.values():
        selected.setdefault((row.get("hash"), row.get("window")), (row, at))

    ordered = sorted(selected.values(), key=lambda pair: pair[1], reverse=True)[:MAX_BACKTESTS]
    return [_backtest(row, at) for row, at in ordered]


# ---------------------------------------------------------------- backtests (committed reviews of the live system)

SIZING_CONFIRM = REPO_ROOT / "docs" / "reviews" / "2026-09-10-sizing-study-confirm.json"


def _review_at(generated) -> datetime:
    """A review's `generated` (a date or an ISO stamp) as an aware datetime; a bare date reads as ET midnight."""
    at = datetime.fromisoformat(str(generated))
    return at if at.tzinfo is not None else at.replace(tzinfo=ET)


def _rsi2_20yr(doc: dict) -> dict:
    stats = doc["stats"]
    overall, span = stats["overall"], stats.get("span") or {}
    total, spy = _num(stats.get("total_return_pct")), _num(stats.get("spy_buy_hold_pct"))
    note = "The production RSI2 rules on 28 names, $100k fixed-lot book"
    if total is not None and spy is not None:
        note += f"; total {total:+.1f}% vs SPY buy-and-hold {spy:+.1f}%"
    return {"id": "review:rsi2-20yr", "name": "RSI2 20-year backtest (production config)", "family": "rsi2",
            "window": f"{str(span.get('start', ''))[:4]}–{str(span.get('end', ''))[:4]}", "verdict": "pass",
            "at": _review_at(doc["generated"]).isoformat(), "strategy_id": None,
            "trades": _int(overall.get("trades")), "avg_trade_pct": _backtest_num(overall.get("expectancy_pct")),
            "t_stat": None, "calmar": None, "max_drawdown_pct": _backtest_num(stats.get("max_drawdown_pct")),
            "note": note}


def _s6_confirm(doc: dict) -> dict:
    cell = doc["cells"]["S6"]
    verdict = str((doc.get("verdict") or {}).get("verdict", "")).lower()
    dd = _backtest_num(cell.get("max_drawdown_pct"))
    return {"id": "review:s6-sizing-confirm", "name": "S6 sizing study (equity ÷ 6, six shared slots)",
            "family": "sizing", "window": f"confirm {str(doc.get('start', ''))[:4]}–{str(doc.get('end', ''))[:4]}",
            "verdict": _backtest_verdict(verdict), "at": _review_at(doc["generated"]).isoformat(), "strategy_id": None,
            "trades": _int(cell.get("trades_taken")), "avg_trade_pct": None, "t_stat": None,
            "calmar": _backtest_num(cell.get("calmar")), "max_drawdown_pct": -abs(dd) if dd is not None else None,
            "note": f"CAGR {cell.get('cagr_pct', 0):.2f}% on the RSI2 + IBS pool; the live RSI2 book's sizing rule"}


# Explicit source file -> strategy_id (2026-09-29): only these committed reviews reach kestrel's Backtests page;
# IBS/ORB/intraday reviews and the bench ledger stay untagged (hidden while their tracks aren't running).
REVIEW_BACKTESTS = ((RSI2_BACKTEST, "rsi2", _rsi2_20yr), (SIZING_CONFIRM, "rsi2", _s6_confirm))


def _review_backtests(files: _Files) -> list[dict]:
    rows = []
    for path, sid, build in REVIEW_BACKTESTS:
        rows.append({**build(files.backtest(path)), "strategy_id": sid})
    return sorted(rows, key=lambda r: r["at"], reverse=True)


# ---------------------------------------------------------------- runs

def _next_firing(rows, now_et: datetime):
    """(time, row) of the next timed firing of any of `rows` after now, trading days only; None if untimed."""
    for ahead in range(0, 15):
        d = now_et.date() + timedelta(days=ahead)
        if not routine.is_trading_day(d):
            continue
        times = [(routine._at(d, r.time), r) for r in routine.rows_for(d, tuple(rows)) if r.time and not r.paused]
        future = [(t, r) for t, r in times if t > now_et]
        if future:
            return min(future, key=lambda p: p[0])
    return None


def _label_at(rows, at: datetime) -> str:
    """The label of the key's row whose clock is the latest at or before `at` (ET); the first row otherwise."""
    hhmm = at.astimezone(ET).strftime("%H:%M")
    timed = [r for r in rows if r.time and r.time <= hhmm]
    return (max(timed, key=lambda r: r.time) if timed else rows[0]).label


def _runs(files: _Files, now: datetime) -> tuple[list[dict], dict[str, str], list[dict]]:
    """(runs, next run per run-log key, the jobs that need the owner). One block per run-log key of the routine:
    its last run (done / failed from its result), a late firing today (status_for's clock), and the next firing."""
    now_et = now.astimezone(ET)
    today = now_et.date()
    rows = [r for r in routine.ROUTINE if r.kind == "task" and r.evidence and ":" not in r.evidence]
    todays = routine.rows_for(today, tuple(rows))
    status = routine.status_for(todays, files.runs(), now_et, lambda evidence, day: False)
    late = {(s["evidence"], s["time"]) for s in status if s["status"] == "late"}
    book_of = {spec.run_key: spec.id for spec in BOOKS if spec.run_key}
    runs, nexts, problems = [], {}, []
    for key in dict.fromkeys(r.evidence for r in rows):
        krows = [r for r in rows if r.evidence == key]
        last_row = next((row for row in reversed(files.runs())
                         if row.get("key") == key and routine._ts_et(row) is not None), None)
        last_at = routine._ts_et(last_row) if last_row else None
        base = {"book_id": book_of.get(key)}
        if all(r.paused for r in krows):
            runs.append({**base, "time": (last_at or now_et).isoformat(), "label": krows[0].label,
                         "status": "paused", "detail": krows[0].paused[:200]})
            continue
        if last_row is not None:
            runs.append({**base, "time": last_at.isoformat(), "label": _label_at(krows, last_at),
                         "status": "failed" if last_row.get("result") == "error" else "done",
                         "detail": str(last_row.get("summary") or "")[:200]})
        key_late = [r for r in krows if (key, r.time) in late]
        for r in key_late:
            runs.append({**base, "time": routine._at(today, r.time).isoformat(), "label": r.label, "status": "late",
                         "detail": f"no run since {r.time} ET (grace {r.grace_min} min)"})
        nxt = _next_firing(krows, now_et)
        if nxt is not None:
            nexts[key] = nxt[0].isoformat()
            runs.append({**base, "time": nexts[key], "label": nxt[1].label, "status": "due", "detail": ""})
        job = {"key": key, "status": "stale" if key_late else "ok",
               "last_result": (last_row or {}).get("result"), "last_summary": (last_row or {}).get("summary")}
        problem = visibility.problem_of(job) if visibility.is_live_job(job) else None
        if problem:
            word = {"stale": "late", "error": "failed"}.get(problem, problem)
            detail = (f"no run since {key_late[0].time} ET (grace {key_late[0].grace_min} min)" if key_late
                      else str((last_row or {}).get("summary") or "")[:200])
            problems.append({"key": key, "label": krows[0].label, "word": word, "detail": detail,
                             "book_id": book_of.get(key)})
    runs.sort(key=lambda r: datetime.fromisoformat(r["time"]))
    return runs, nexts, problems


# ---------------------------------------------------------------- alerts

def _page(book_id: str | None) -> str:
    spec = next((s for s in BOOKS if s.id == book_id), None)
    return f"/strategies/{spec.strategy}" if spec else ""


def _halt_alerts(today: date) -> list[dict]:
    """Serious: the autopilot's daily halt latched today (autopilot/state/<today>.json)."""
    state = _read_json(autopilot_paths.autopilot_dir() / "state" / f"{today.isoformat()}.json", {})
    if isinstance(state, dict) and state.get("halt_tripped"):
        return [{"level": "serious", "title": "The autopilot's daily halt is latched",
                 "detail": f"autopilot/state/{today.isoformat()}.json: no new real entries today",
                 "link": "/strategies/rsi2"}]
    return []


def _kill_alerts() -> list[dict]:
    """Serious: a kill file one of the books' runners honors is present."""
    out = []
    kills = [(Path(autopilot_paths.resolve_kill_file(os.environ.get("WEBULL_AUTOPILOT_KILL_FILE"))), "rsi2",
              "the autopilot places nothing, real money included")]
    for path, sid, why in kills:
        if path.exists():
            out.append({"level": "serious", "title": f"A kill file is present: {path.parent.name}/{path.name}",
                        "detail": why, "link": f"/strategies/{sid}"})
    return out


# ---------------------------------------------------------------- the document

def build(now: datetime | None = None) -> dict:
    """The feed document. Never raises: a block that fails is left empty and named in a `note` alert."""
    _load_env()
    now = (now or datetime.now(ET)).astimezone(ET)
    today = now.date()
    files = _Files(now)
    notes: list[tuple[str, str]] = []            # (title, detail) of each `note` alert

    def guarded(what: str, fn, default):
        try:
            return fn()
        except Exception as e:
            _log.exception("feed: %s unavailable", what)
            notes.append((f"Left out of the feed: {what}", _err(e)))
            return default

    runs, nexts, problems = guarded("the runs", lambda: _runs(files, now), ([], {}, []))
    parts, charts, strategies = [], [], []
    for spec in [s for s in BOOKS if _emitted(s)]:
        part = guarded(f"the {spec.id} book", lambda: _BUILDERS[spec.id](files, spec, today, notes), None)
        if part is not None:
            parts.append(part)
    earliest: dict[str, str] = {}                 # W2: each symbol's earliest charted open, across every book
    for part in parts:
        for symbol, opened, _closed in _wanted(part):
            if symbol not in earliest or opened < earliest[symbol]:
                earliest[symbol] = opened
    for part in parts:
        charts += guarded(f"the {part['spec'].id} charts", lambda: _charts(files, part, earliest), [])
    live = {s.strategy for s in BOOKS if _emitted(s)}     # the strategies of the running books (2026-09-29)
    for sid, name, cell in STRATEGIES:
        if sid not in live:
            continue
        strategy = guarded(f"the {sid} strategy", lambda: _strategy(files, sid, name, cell), None)
        if strategy is not None:
            strategies.append(strategy)
    backtests = guarded("the review backtests",
                        lambda: [b for b in _review_backtests(files) if _backtest_emitted(b, live)], [])
    backtests += guarded("the backtests", lambda: [b for b in _backtests(files) if _backtest_emitted(b, live)], [])
    alerts = guarded("the autopilot's halt", lambda: _halt_alerts(today), [])
    alerts += guarded("the kill files", _kill_alerts, [])
    alerts += [{"level": "warning", "title": f"{p['label']}: {p['word']}", "detail": p["detail"],
                "link": _page(p["book_id"])} for p in problems]
    try:
        skipped = files.fills()[1]
    except Exception:
        skipped = 0                              # an unreadable journal is already a note on each real book
    if skipped:
        plural = "s" if skipped > 1 else ""
        notes.append(("fills.jsonl: lines skipped", f"{skipped} line{plural} not JSON or not a fill"))
    trade_skips = sum(p.get("skipped", 0) for p in parts)
    if trade_skips:                              # M9: pairing accepts epoch-ms stamps that _day/_et can't parse
        plural = "s" if trade_skips > 1 else ""
        notes.append(("trades: entries skipped", f"{trade_skips} trade{plural} with an unparseable timestamp"))

    # M1+M2: a last pass drops just the item holding a non-finite number, an over-large magnitude, or an
    # out-of-range date -- kestrel's own contract would otherwise reject the WHOLE document for one bad value. A
    # book is dropped only when the bad value is on the book row itself; its positions/trades/history/charts are
    # then dropped too (silently -- the book's own note already says so), so no block names a book that isn't there.
    books = _drop_bad([_book(p, nexts.get(p["spec"].run_key)) for p in parts], "books", notes)
    kept = {b["id"] for b in books}
    # a routine row of a book that isn't emitted (paused track) stays -- it reads "paused" -- but names no book
    runs = [{**r, "book_id": None} if r.get("book_id") and r["book_id"] not in kept else r for r in runs]
    history, hist_dropped = [], 0
    for p in parts:
        if p["history"] is None or p["spec"].id not in kept:
            continue
        good = [pt for pt in p["history"] if not _has_unusable(pt)]
        hist_dropped += len(p["history"]) - len(good)
        history.append({"id": p["spec"].id, "points": good})
    if hist_dropped:
        notes.append((f"book_history: {hist_dropped} item{'s' if hist_dropped > 1 else ''} dropped "
                      f"(a value kestrel can't show)", ""))
    positions = _drop_bad([pos for p in parts if p["spec"].id in kept for pos in p["positions"]], "positions", notes)
    trades = _drop_bad([t for p in parts if p["spec"].id in kept for t in p["trades"]], "trades", notes)
    charts = _drop_bad([c for c in charts if c["book_id"] in kept], "trade_charts", notes)
    backtests = _drop_bad(backtests, "backtests", notes)

    alerts += [{"level": "note", "title": title, "detail": detail, "link": ""} for title, detail in notes]
    stamp = now.isoformat(timespec="seconds")
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": stamp,
        "sources": [{"id": "webull-desk", "label": "Webull desk", "kind": "feed", "last_success": stamp,
                     "status": "ok", "detail": ATTRIBUTION_RULE}],
        "books": books,
        "book_history": history,
        "positions": positions,
        "trades": trades,
        "trade_charts": charts,
        "strategies": strategies,
        "backtests": backtests,
        "runs": runs,
        "alerts": alerts,
    }
