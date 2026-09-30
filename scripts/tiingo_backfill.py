"""Tiingo daily-history backfill CLI (spec 2026-09-07 tiingo depth §3.5). Offline-only: reads/writes
the Tiingo store, never touches the trading path.

  python scripts/tiingo_backfill.py                              # default 61-name set, incremental
  python scripts/tiingo_backfill.py --symbols AAPL,MSFT --refresh # force a full refetch for just these
  python scripts/tiingo_backfill.py --all-curated                # default set + the full curated pool
  python scripts/tiingo_backfill.py --dry-run                     # print the plan, fetch nothing

Prints ASCII only (Windows console safe): "SYM rows first->last", "SYM: not found (skipped)".
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_api.discovery import CURATED_UNIVERSE  # noqa: E402
from webull_api.lab.schema import LAB_BASKET  # noqa: E402
from webull_api.paths import REPO_ROOT  # noqa: E402
from webull_api.strategy import rsi2  # noqa: E402
from webull_api.tiingo import store, tickers, universe  # noqa: E402
from webull_api.tiingo.client import TiingoClient, TiingoError, TiingoNotFound  # noqa: E402

DEFAULT_SINCE = "1996-01-01"
BROAD_SINCE = "2021-06-01"      # 3 months before the ORB window so the first day's 20-day ADV exists
RUN_LOGS = {"lab": "backfill_runs.jsonl", "broad": "broad_backfill_runs.jsonl"}
CONSECUTIVE_NETWORK_ERROR_LIMIT = 5   # a dropped connection/DNS blip mid-run must not kill hours of progress,
                                       # but a truly dead network must not spin through the whole symbol list

# spec 2026-09-08 auto universe §2: the WIDER paper-400 band, so a --in-band backfill keeps both
# day profiles (plan-v1 $5-$30 and paper-400 $5-$100) fresh from one scan of the store.
IN_BAND_BAND = universe.Band("pool", 5.0, 100.0, 5_000_000)

# spec §3.5: names outside UNIVERSE/LAB_BASKET worth having depth on for future universe expansion.
EXPANSION_CANDIDATES = ("AMAT", "AMD", "MU", "MRK", "AMGN", "ISRG", "TJX", "KO", "PG", "XOM", "CVX",
                        "HD", "NFLX", "MA", "RSP", "ACN", "CRM", "DE", "DHR", "DIS", "MCD", "NKE",
                        "NOW", "UNH", "XLK", "XLP", "BKNG", "GOOGL", "NEE", "LIN")


def default_symbols(all_curated: bool) -> list[str]:
    """Universe -> lab basket -> SPY -> expansion candidates [-> curated pool], upper-cased,
    deduped preserving first occurrence, toolkit spelling kept ("BRK B" stays as-is)."""
    ordered = list(rsi2.UNIVERSE) + list(LAB_BASKET) + ["SPY"] + list(EXPANSION_CANDIDATES)
    if all_curated:
        ordered += list(CURATED_UNIVERSE)
    seen: set[str] = set()
    out: list[str] = []
    for sym in ordered:
        up = sym.strip().upper()
        if up not in seen:
            seen.add(up)
            out.append(up)
    return out


def plan(symbols, *, last_date, refresh: bool, since: str = DEFAULT_SINCE):
    """[(symbol, start)] — start is `since` when nothing is stored (or --refresh forces it), else
    the stored last date ITSELF (inclusive, not the day after): re-fetching that overlap row lets
    `run` detect a retroactive re-adjustment (I4) by comparing it against what's on disk. A symbol
    already current is still planned from its last date; the client simply returns that one row
    again, unchanged, and the merge dedupes it."""
    out = []
    for sym in symbols:
        last = None if refresh else last_date(sym)
        start = since if last is None else last
        out.append((sym, start))
    return out


def _fetched_at(today: str | None) -> str:
    if today:
        return today
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_run(today: str | None, kind: str, **counts: int) -> None:
    """Append one row per completed run to <store dir>/{backfill_runs.jsonl|broad_backfill_runs.jsonl}.
    The routine's 16:45 "Tiingo backfill" row reads it as `jsonl:` evidence (the `date` key, ET calendar day),
    so the Today card can tell a run that happened from one that did not. Never raises."""
    day = today[:10] if today else datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    row = {"date": day, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, **counts}
    try:
        p = store.dir_path() / RUN_LOGS[kind]
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:  # pragma: no cover - a failed log line must not fail the backfill
        print(f"run log not written: {exc}")


def run(argv=None, *, client=None, sleep=time.sleep, today: str | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill/update Tiingo daily-history CSVs.")
    ap.add_argument("--symbols", help="Comma-separated symbol list (default: the built-in set)")
    ap.add_argument("--since", default=None, help=f"Earliest date to fetch (default {DEFAULT_SINCE} for lab, {BROAD_SINCE} for broad)")
    ap.add_argument("--all-curated", action="store_true", help="Also include the full curated universe")
    ap.add_argument("--in-band", action="store_true",
                    help="Also include every symbol already in the store whose latest row is in "
                         "the $5-$100 / 20-day ADV>=5M band (feeds the auto day-trade universe)")
    ap.add_argument("--refresh", action="store_true", help="Force a full refetch from --since for every symbol")
    ap.add_argument("--dry-run", action="store_true", help="Print the plan; fetch nothing")
    ap.add_argument("--tickers-file", help="CSV from scripts/tiingo_tickers.py; selects every ticker in it")
    ap.add_argument("--listed-between", nargs=2, metavar=("START", "END"),
                    help="With --tickers-file: only tickers listed at some point inside [START, END]")
    ap.add_argument("--spacing", type=float, default=0.25, help="Seconds between requests (broad runs: 0.4)")
    ap.add_argument("--kind", choices=("lab", "broad"), default="lab",
                    help="Run log file: lab -> backfill_runs.jsonl (routine evidence), broad -> broad_backfill_runs.jsonl")
    ap.add_argument("--manifest-every", type=int, default=200, help="Write the manifest every N fetched symbols")
    args = ap.parse_args(argv)
    since = args.since or (BROAD_SINCE if args.kind == "broad" else DEFAULT_SINCE)

    if args.tickers_file:
        rows = tickers.read(Path(args.tickers_file))
        if args.listed_between:
            symbols = tickers.listed_between(rows, args.listed_between[0], args.listed_between[1])
        else:
            symbols = sorted({r["ticker"].upper() for r in rows})
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = default_symbols(args.all_curated)

    if args.in_band:
        # Scan the ~18k-CSV store once (~1-2 min) for names already in the wider paper-400 band --
        # kept deliberately simple (no cache): spec 2026-09-08 auto universe §2.
        pool_syms = universe.pool(store.symbols(), IN_BAND_BAND)
        print(f"in-band pool: {len(pool_syms)} symbols")
        seen = set(symbols)
        for sym in pool_syms:
            if sym not in seen:
                seen.add(sym)
                symbols.append(sym)

    planned = plan(symbols, last_date=store.last_date, refresh=args.refresh, since=since)

    if args.dry_run:
        for sym, start in planned:
            print(f"{sym} -> {start}")
        return 0

    if client is None:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
        client = TiingoClient()

    manifest = store.read_manifest()
    fetched = 0
    skipped = 0   # 404 TiingoNotFound — symbol doesn't exist at Tiingo
    failed = 0    # any other TiingoError or network error
    consecutive_network_errors = 0
    for sym, start in planned:
        try:
            # spacing is paid on EVERY iteration, including the 404 / error / network-error
            # paths: those used to `continue` straight past it and hammer the hourly cap
            # exactly when the API was least happy (broad runs plan ~18,000 symbols).
            try:
                api_rows = client.daily_prices(sym, start=start)
            except TiingoNotFound:
                print(f"{sym}: not found (skipped)")
                skipped += 1
                consecutive_network_errors = 0
                continue
            except TiingoError as exc:
                print(f"{sym}: error - {exc}")
                failed += 1
                consecutive_network_errors = 0
                continue
            except requests.RequestException as exc:
                print(f"{sym}: network error - {exc}")
                failed += 1
                consecutive_network_errors += 1
                if consecutive_network_errors >= CONSECUTIVE_NETWORK_ERROR_LIMIT:
                    store.write_manifest(manifest)
                    print(f"aborting: {CONSECUTIVE_NETWORK_ERROR_LIMIT} consecutive network errors")
                    _log_run(today, args.kind, planned=len(planned), fetched=fetched, skipped=skipped, failed=failed)
                    return 1
                continue
            consecutive_network_errors = 0

            rows = store.from_api(api_rows)
            incremental = start != since
            needs_replace = False
            existing_by_date: dict = {}
            if incremental:
                # `start` is the stored last date (inclusive, per plan()) — the fetched row at `start`
                # overlaps the stored one; compare them to catch a retroactive re-adjustment (I4).
                existing_by_date = {r["date"]: r for r in store.read(sym)}
                stored_overlap = existing_by_date.get(start)
                fetched_overlap = next((r for r in rows if r["date"] == start), None)
                if stored_overlap is not None and fetched_overlap is not None:
                    old_adj, new_adj = stored_overlap.get("adj_close"), fetched_overlap.get("adj_close")
                    if old_adj is not None and new_adj is not None:
                        denom = abs(old_adj) if old_adj else 1e-9
                        if abs(new_adj - old_adj) / denom > 1e-6:
                            needs_replace = True     # the overlap row itself was re-adjusted
                if not needs_replace and store.needs_full_refetch(rows):
                    needs_replace = True             # a split/dividend appeared in the new rows

            if incremental and needs_replace:
                try:
                    api_rows = client.daily_prices(sym, start=since)
                except TiingoNotFound:
                    print(f"{sym}: not found (skipped)")
                    skipped += 1
                    continue
                except TiingoError as exc:
                    print(f"{sym}: error - {exc}")
                    failed += 1
                    continue
                except requests.RequestException as exc:
                    print(f"{sym}: network error - {exc}")
                    failed += 1
                    consecutive_network_errors += 1
                    if consecutive_network_errors >= CONSECUTIVE_NETWORK_ERROR_LIMIT:
                        store.write_manifest(manifest)
                        print(f"aborting: {CONSECUTIVE_NETWORK_ERROR_LIMIT} consecutive network errors")
                        _log_run(today, args.kind, planned=len(planned), fetched=fetched, skipped=skipped, failed=failed)
                        return 1
                    continue
                final_rows = store.from_api(api_rows)
            elif incremental:
                by_date = dict(existing_by_date)
                for r in rows:
                    by_date[r["date"]] = r
                final_rows = list(by_date.values())
            else:
                # --refresh, or a first-time fetch (nothing stored yet): REPLACE the store outright
                # with what's fetched. Merging with whatever used to be on disk here would let stale
                # rows survive a --refresh whose fetch window is narrower than the old data (I3).
                final_rows = rows

            n = store.write(sym, final_rows)
            written = store.read(sym)
            first = written[0]["date"] if written else ""
            last = written[-1]["date"] if written else ""
            print(f"{sym} {n} {first}->{last}")

            manifest[sym] = {"first": first, "last": last, "rows": n, "fetched_at": _fetched_at(today)}

            fetched += 1
            if args.manifest_every and fetched % args.manifest_every == 0:
                store.write_manifest(manifest)
        finally:
            sleep(args.spacing)

    store.write_manifest(manifest)
    print(f"done: {fetched} fetched, {skipped} skipped (not found), {failed} failed")
    _log_run(today, args.kind, planned=len(planned), fetched=fetched, skipped=skipped, failed=failed)

    if planned and fetched == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
