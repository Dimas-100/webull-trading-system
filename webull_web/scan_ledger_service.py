"""Scanner efficacy ledger runner — the paper-suite step directly before Note (spec 2026-07-21).
Pass 1 snapshots today's ranked discovery picks into data/scanner/snapshots.jsonl; pass 2 scores
every matured, unscored snapshot window (+5/+10/+20 trading days vs SPY, next-session-open
entry). Read-only market data + JSONL appends; NEVER imports trading. Per-symbol errors are
soft (reported, retried next run); MarketDataNotEntitledError propagates (run_cli -> SKIPPED)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from webull_api import market_data, run_log, scanner_efficacy
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.strategy.bars import to_ohlcv

from . import paper_service, runner_util, scanner_store, setups_service

_log = logging.getLogger(__name__)
_KEY = "scan_ledger"
_BAR_COUNT = "120"      # covers a multi-week scoring backlog
_WORKERS = 8


def _daily_bars(symbol: str) -> list[dict]:
    """OHLCV daily bars, oldest first (test seam)."""
    return to_ohlcv(market_data.get_bars(symbol, "D", count=_BAR_COUNT))


def _snapshot_pass(today: str) -> tuple[int, list[str]]:
    scan = setups_service.scan_setups()
    recs = []
    for row in scan.get("setups") or []:
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue
        recs.append({"id": f"{today}:{sym}", "date": today, "symbol": sym,
                     "score": row.get("score"),
                     "tags": [t.get("kind") for t in row.get("tags") or []],
                     "last": row.get("last"), "change_pct": row.get("change_pct"),
                     "source_kind": (scan.get("source") or {}).get("kind")})
    n = scanner_store.append_snapshots(recs)
    errs = [f"scan:{e.get('symbol')}:{e.get('error')}" for e in scan.get("errors") or []]
    return n, errs


def _score_pass(iso: str) -> tuple[int, list[str]]:
    snapshots = scanner_store.load_snapshots()
    score_ids = {str(s.get("id")) for s in scanner_store.load_scores()}
    todo = [s for s in snapshots
            if any(f"{s.get('date')}:{s.get('symbol')}:{w}" not in score_ids
                   for w in scanner_efficacy.WINDOWS)]
    if not todo:
        return 0, []
    spy = _daily_bars("SPY")
    errors: list[str] = []

    def bars_for(sym: str):
        try:
            return sym, _daily_bars(sym)
        except MarketDataNotEntitledError:
            raise
        except Exception as e:
            errors.append(f"bars:{sym}:{type(e).__name__}")
            return sym, None

    syms = sorted({str(s.get("symbol")) for s in todo})
    with ThreadPoolExecutor(max_workers=min(_WORKERS, len(syms))) as ex:
        bars = dict(ex.map(bars_for, syms))
    new: list[dict] = []
    for snap in todo:
        sb = bars.get(str(snap.get("symbol")))
        if not sb:
            continue
        new.extend(r for r in scanner_efficacy.score_snapshot(snap, sb, spy, scored_at=iso)
                   if r["id"] not in score_ids)
    return scanner_store.append_scores(new), errors


def run(force: bool = False) -> dict:
    iso, today = paper_service.now_et()
    with runner_util.filelock(_KEY, iso) as got:
        if not got:
            return {"result": "no_op", "errors": [],
                    "summary": "Scan: another run in progress", "ran_at": iso}
        if runner_util.already_ran_today(_KEY, today, force):
            return {"result": "no_op", "errors": [],
                    "summary": "Scan: already ran today", "ran_at": iso}
        errors: list[str] = []
        snapped = scored = 0
        try:
            snapped, errs = _snapshot_pass(today)
            errors.extend(errs)
        except MarketDataNotEntitledError:
            raise
        except Exception as e:
            _log.exception("scan-ledger: snapshot pass failed")
            errors.append(f"snapshot:{type(e).__name__}")
        try:
            scored, errs = _score_pass(iso)
            errors.extend(errs)
        except MarketDataNotEntitledError:
            raise
        except Exception as e:
            _log.exception("scan-ledger: score pass failed")
            errors.append(f"score:{type(e).__name__}")
        summary = f"Scan: {snapped} pick(s) snapped · {scored} score(s) added"
        result = "ok" if not errors else "partial"
        run_log.append([{"key": _KEY, "ts": iso, "result": result, "summary": summary,
                         "placed": 0, "errors": errors}])
        return {"result": result, "errors": errors, "summary": summary, "ran_at": iso}


def efficacy_view() -> dict:
    """Read-only efficacy payload for the route + the manager's note."""
    _iso, today = paper_service.now_et()
    snapshots = scanner_store.load_snapshots()
    scores = scanner_store.load_scores()
    return {"summary": scanner_efficacy.efficacy_summary(snapshots, scores, today=today),
            "recent": scores[-50:]}
