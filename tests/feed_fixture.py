"""A small, FICTIONAL desk for the kestrel feed tests: invented symbols, prices and dates, laid out the way the
desk's runners write their files, under one temp folder. Never a copy of real rows."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from webull_api.strategy import indicators

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 3, 18, 12, 0, tzinfo=ET)          # a Wednesday, midday ET
FIRST_BAR, LAST_BAR = date(2026, 1, 2), date(2026, 3, 17)
PRICED = {"ALFA": 40.0, "BRAV": 20.0, "CHRL": 30.0, "DLTA": 50.0, "FXTR": 60.0, "GOLF": 80.0, "HTEL": 25.0,
          "ETFA": 100.0, "ETFB": 50.0, "GRID": 20.0, "KILO": 15.0}      # ECHO has no price file on purpose
STORE_VARS = ("JOURNAL_DIR", "ACTIVITY_DIR", "PAPER_DIR", "TIINGO_DIR", "RSI2_STATE_DIR", "RSI2_REAL_STATE_DIR",
              "BENCH_DIR", "WEBULL_AUTOPILOT_KILL_FILE", "WEBULL_RSI2_REAL_MAX_LOTS")


def weekdays(start: date = FIRST_BAR, end: date = LAST_BAR) -> list[str]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def bars(symbol: str) -> list[dict]:
    """A deterministic zig-zag around the symbol's base price, one bar per weekday."""
    base, k = PRICED[symbol], sum(map(ord, symbol))
    out = []
    for i, d in enumerate(weekdays()):
        close = round(base + ((i * 7 + k) % 11) - 5, 2)
        out.append({"date": d, "open": round(close - 0.5, 2), "high": round(close + 1.0, 2),
                    "low": round(close - 1.5, 2), "close": close})
    return out


def last_close(symbol: str) -> float:
    return bars(symbol)[-1]["close"]


def rsi2(symbol: str) -> list[float | None]:
    return indicators.rsi([b["close"] for b in bars(symbol)], 2)


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def fill(fid, source, symbol, side, qty, price, at):
    return {"id": fid, "source": source, "account_id": "ACCT-1" if source == "real" else "paper-1", "symbol": symbol,
            "side": side, "quantity": qty, "price": price, "filled_at_iso": at, "order_type": "LIMIT"}


REAL_FILLS = [
    fill("r1", "real", "ALFA", "BUY", 3.0, 40.0, "2026-02-02T14:31:05.000Z"),     # autopilot-placed -> rsi2-real
    fill("r2", "real", "ALFA", "SELL", 3.0, 42.0, "2026-02-05T14:31:07.000Z"),
    fill("r3", "real", "BRAV", "BUY", 10.0, 20.0, "2026-02-10T15:00:00.000Z"),    # same day -> day-orb-real
    fill("r4", "real", "BRAV", "SELL", 10.0, 19.5, "2026-02-10T15:40:00.000Z"),
    fill("r5", "real", "CHRL", "BUY", 4.0, 30.0, "2026-02-12T15:00:00.000Z"),     # neither -> pullback-real
    fill("r6", "real", "CHRL", "SELL", 4.0, 33.0, "2026-02-20T15:00:00.000Z"),
    fill("r7", "real", "DLTA", "BUY", 2.0, 50.0, "2026-03-10T13:31:00.000Z"),     # open, the RSI2 ledger's lot
    fill("r8", "real", "ECHO", "BUY", 5.0, 12.0, "2026-03-11T15:00:00.000Z"),     # open, discretionary, no prices
]
PAPER_FILLS = [
    fill("p1", "paper", "FXTR", "BUY", 100.0, 60.0, "2026-01-20T09:30:02.000000-05:00"),
    fill("p2", "paper", "FXTR", "SELL", 100.0, 63.0, "2026-01-23T09:30:03.000000-05:00"),
]
RUNS = [
    {"key": "note", "ts": "2026-03-17T17:31:10.000000-04:00", "result": "error", "summary": "suite step failed"},
    {"key": "bench_feeder", "ts": "2026-03-17T17:36:00.000000-04:00", "result": "ok", "summary": "batch done"},
    {"key": "bench_paper", "ts": "2026-03-17T18:41:00.000000-04:00", "result": "ok", "summary": "replayed"},
    {"key": "watchdog", "ts": "2026-03-17T19:00:30.000000-04:00", "result": "ok", "summary": "all fresh"},
    {"key": "autopilot", "ts": "2026-03-18T09:31:40.000000-04:00", "result": "ok", "summary": "exits placed"},
]
RSI2_BACKTEST = {
    "trades": [{"symbol": "ALFA", "entry_date": "2020-01-02", "exit_date": "2020-01-07", "return_pct": 2.0},
               {"symbol": "BRAV", "entry_date": "2020-01-15", "exit_date": "2020-01-20", "return_pct": -1.0},
               {"symbol": "CHRL", "entry_date": "2020-02-03", "exit_date": "2020-02-06", "return_pct": 3.0},
               {"symbol": "DLTA", "entry_date": "2020-02-20", "exit_date": "2020-03-02", "return_pct": -12.5}],
    "equity_curve": [{"date": "2020-01-02", "equity": 100.0}, {"date": "2020-02-03", "equity": 110.0},
                     {"date": "2020-03-02", "equity": 99.0}],
}
ORB_BACKTEST = {"cells": {"base": {"per_signal": {"all": {"n": 10, "expectancy_pct": -0.1}}}}}


def write_desk(root, monkeypatch) -> dict:
    """Lay the fictional desk out under `root` and point every store at it. Returns the folders."""
    from webull_web import feed_service

    data, autopilot = root / "data", root / "autopilot"
    # I3: never let a test's build() load the real .env -- tests that care about the seam replace this stub.
    monkeypatch.setattr(feed_service, "_load_env", lambda: None)
    for var in STORE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WEBULL_DATA_DIR", str(data))
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(autopilot))
    monkeypatch.setenv("WEBULL_RSI2_REAL_MAX_LOTS", "5")

    header = "date,open,high,low,close,volume,adj_open,adj_high,adj_low,adj_close,adj_volume,div_cash,split_factor\n"
    for sym in PRICED:
        lines = [f"{b['date']},{b['open']},{b['high']},{b['low']},{b['close']},100000,{b['open']},{b['high']},"
                 f"{b['low']},{b['close']},100000,0.0,1.0\n" for b in bars(sym)]
        (data / "tiingo").mkdir(parents=True, exist_ok=True)
        (data / "tiingo" / f"{sym}.csv").write_text(header + "".join(lines), encoding="utf-8")

    _jsonl(data / "journal" / "fills.jsonl", REAL_FILLS + PAPER_FILLS)
    _jsonl(autopilot / "log" / "2026-02-02.jsonl", [
        {"source": "decision:immediate", "symbol": "ALFA", "side": "BUY", "allow": True, "layer": "ok", "placed": True,
         "result": {"submitted": True}}])
    _jsonl(autopilot / "log" / "2026-02-12.jsonl", [                # neither makes CHRL an RSI2 entry
        {"source": "decision:immediate", "symbol": "CHRL", "side": "BUY", "allow": False, "layer": "caps",
         "placed": False},
        {"source": "protect", "symbol": "CHRL", "side": "SELL", "allow": True, "layer": "risk-reducing",
         "placed": True, "result": {"submitted": True}}])
    _jsonl(autopilot / "log" / "2026-03-10.jsonl", [
        {"source": "decision:immediate", "symbol": "DLTA", "side": "BUY", "allow": True, "layer": "ok", "placed": True,
         "result": {"submitted": True}}])
    _json(autopilot / "state" / "2026-03-18.json", {"day": "2026-03-18", "orders_today": 1, "realized_loss": 0.0,
                                                     "halt_tripped": False, "placed_symbols": []})

    activity = data / "activity"
    _json(activity / "rsi2_real_state.json", {"schema_version": 1, "owned_lots": [
        {"symbol": "DLTA", "shares": 2.0, "entry_price": 50.0, "entry_date": "2026-03-10", "decision_id": "d-1"}],
        "pending_orders": [], "exit_rows": {}, "reconciliation_log": [], "updated_at": None})
    _json(activity / "rsi2_state.json", {"schema_version": 1, "owned_lots": [
        {"symbol": "GOLF", "shares": 50.0, "entry_price": 80.0, "entry_date": "2026-03-12", "paper_order_id": "po-1"}],
        "pending_orders": [], "reconciliation_log": [], "updated_at": None})
    _jsonl(activity / "netliq_history.jsonl", [
        {"date": "2026-03-13", "paper_equity": 98500.0, "paper_options": 1000.0, "real": None},
        {"date": "2026-03-16", "paper_equity": 99000.0, "paper_options": 1000.0, "real": None},
        {"date": "2026-03-17", "paper_equity": None, "paper_options": 1000.0, "real": None}])
    _jsonl(activity / "runs.jsonl", RUNS)
    _json(data / "paper" / "default.json", {"account_id": "paper-1", "starting_cash": 100000.0, "cash": 90000.0,
                                            "positions": {"GOLF": {"symbol": "GOLF", "quantity": 50.0, "avg_cost": 80.0},
                                                          "HTEL": {"symbol": "HTEL", "quantity": 10.0, "avg_cost": 25.0}},
                                            "open_orders": [], "history": [], "realized_pnl": 0.0})

    return {"data": data, "autopilot": autopilot, "activity": activity}


def write_backtests(root, monkeypatch) -> None:
    """The backtest reviews, tiny and fictional, in place of the committed ones."""
    from webull_web import feed_service

    for name, doc in (("RSI2_BACKTEST", RSI2_BACKTEST), ("ORB_BACKTEST", ORB_BACKTEST)):
        _json(root / "reviews" / f"{name.lower()}.json", doc)
        monkeypatch.setattr(feed_service, name, root / "reviews" / f"{name.lower()}.json")
