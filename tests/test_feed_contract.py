"""The feed document against kestrel's data contract, version 1 (kestrel src/kestrel/contract.py). This repo never
imports kestrel, so the contract is mirrored here by hand: each item's required fields, their kinds and the allowed
words. A field kestrel adds later is optional by its own rule (changes within a major version are additive)."""
import json
import math
import re
from datetime import date, datetime

import pytest

from feed_fixture import NOW, write_backtests, write_desk
from webull_web import feed_service as svc

NUM, INT, STR, DATE, STAMP, LIST = "num", "int", "str", "date", "stamp", "list"
# M1+M2: kestrel's own contract rejects the WHOLE document for a non-finite/too-large number or an out-of-range
# date; mirrored here so this hand-written contract catches it too, behind build()'s own last pass.
MAX_MAGNITUDE = 1e15
MIN_DATE, MAX_DATE = "1970-01-01", "2200-12-31"
# field -> kind or a tuple of allowed words. A trailing "?" marks a kestrel `X | None = None` field: may be absent
# or null. A trailing "!" marks a kestrel field that has a default but is NOT Optional (e.g. `detail: str = ""`):
# kestrel may see it absent (the default applies), but never accepts it present-and-null (W3).
SHAPES = {
    "sources": {"id": STR, "label": STR, "kind": STR, "last_success?": STAMP, "status!": ("ok", "stale", "error"),
                "detail!": STR},
    "books": {"id": STR, "name": STR, "money": ("real", "paper"), "strategy_id": STR, "account_id?": STR,
              "status": ("running", "paused", "parked"), "started": DATE, "value": NUM, "slots_total?": INT,
              "next_run?": STAMP},
    "book_history": {"id": STR, "points": LIST},
    "points": {"date": DATE, "value": NUM, "net_flow!": NUM},
    "positions": {"book_id": STR, "symbol": STR, "quantity": NUM, "entry_price": NUM, "last_price": NUM,
                  "stop_price?": NUM, "opened": DATE, "note!": STR, "stop_resting?": (True, False)},
    "trades": {"book_id": STR, "symbol": STR, "opened": DATE, "closed": DATE, "entry_price": NUM, "exit_price": NUM,
               "quantity": NUM, "pnl": NUM, "return_pct": NUM, "r_multiple?": NUM, "exit_reason!": STR},
    "trade_charts": {"book_id": STR, "symbol": STR, "opened": DATE, "bars": LIST, "indicator?": dict, "stop?": NUM},
    "bars": {"date": DATE, "open": NUM, "high": NUM, "low": NUM, "close": NUM},
    "indicator": {"label": STR, "values": LIST, "lines!": LIST},
    "lines": {"value": NUM, "label": STR},
    "strategies": {"id": STR, "name": STR, "summary!": STR, "steps!": LIST, "sizing!": STR, "expected?": dict,
                   "review_at_trades?": INT, "watch!": LIST},
    "steps": {"label": STR, "title": STR, "text": STR, "params!": LIST},
    "expected": {"win_rate": NUM, "avg_trade_pct": NUM, "avg_win_pct": NUM, "avg_loss_pct": NUM,
                 "trades_per_month": NUM, "sd_trade_pct": NUM, "distribution!": LIST, "source!": STR, "window!": STR,
                 "cagr_pct?": NUM, "max_drawdown_pct?": NUM},
    "runs": {"time": STAMP, "label": STR, "book_id?": STR, "status": ("done", "due", "late", "failed", "paused"),
             "detail!": STR},
    "alerts": {"level": ("serious", "warning", "note"), "title": STR, "detail!": STR, "link!": STR},
    # kestrel Backtest (kestrel-phase-5 plan Task 1): family/window/note have a "" default but are not Optional;
    # strategy_id/trades/avg_trade_pct/t_stat/calmar/max_drawdown_pct are `X | None = None`.
    "backtests": {"id": STR, "name": STR, "family!": STR, "window!": STR,
                  "verdict": ("pass", "fail", "refused", "pending"), "at": STAMP, "strategy_id?": STR,
                  "trades?": INT, "avg_trade_pct?": NUM, "t_stat?": NUM, "calmar?": NUM, "max_drawdown_pct?": NUM,
                  "note!": STR},
}
TOP = {"contract_version", "generated_at", "sources", "books", "book_history", "positions", "trades", "trade_charts",
       "strategies", "runs", "alerts", "backtests"}
KESTREL_LINK = re.compile(r"^(/([^/\\\s].*)?)?$")           # kestrel Alert.link: a page inside kestrel, or empty


def _kind_ok(value, kind) -> bool:
    if isinstance(kind, tuple):
        return value in kind
    if kind is dict:
        return isinstance(value, dict)
    if kind == NUM:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) and abs(value) <= MAX_MAGNITUDE)
    if kind == INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == STR:
        return isinstance(value, str)
    if kind == LIST:
        return isinstance(value, list)
    if kind == DATE:
        return (isinstance(value, str) and len(value) == 10 and bool(date.fromisoformat(value))
               and MIN_DATE <= value <= MAX_DATE)
    if kind == STAMP:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    raise AssertionError(kind)


def _check(item: dict, shape: str, where: str) -> None:
    for field, kind in SHAPES[shape].items():
        name = field.rstrip("?!")
        may_be_absent = field.endswith("?") or field.endswith("!")
        may_be_null = field.endswith("?")            # "!" fields may be absent, but never null when present (W3)
        if name not in item:
            assert may_be_absent, f"{where}: required field {name!r} missing"
            continue
        if item[name] is None:
            assert may_be_null, f"{where}.{name}: null not allowed (kestrel default, not Optional)"
            continue
        assert _kind_ok(item[name], kind), f"{where}.{name}: {item[name]!r} is not {kind}"


def _check_document(doc: dict) -> None:
    assert set(doc) == TOP
    assert doc["contract_version"].split(".")[0] == "1" and _kind_ok(doc["generated_at"], STAMP)
    for block in TOP - {"contract_version", "generated_at"}:
        for i, item in enumerate(doc[block]):
            _check(item, block, f"{block}[{i}]")
    for s in doc["book_history"]:
        for i, p in enumerate(s["points"]):
            _check(p, "points", f"book_history[{s['id']}].points[{i}]")
    for c in doc["trade_charts"]:
        for i, b in enumerate(c["bars"]):
            _check(b, "bars", f"chart {c['symbol']} bar {i}")
        if c.get("indicator"):
            _check(c["indicator"], "indicator", f"chart {c['symbol']} indicator")
            assert len(c["indicator"]["values"]) == len(c["bars"])
            assert all(v is None or _kind_ok(v, NUM) for v in c["indicator"]["values"])
            for line in c["indicator"].get("lines") or []:
                _check(line, "lines", f"chart {c['symbol']} line")
    for s in doc["strategies"]:
        for step in s.get("steps") or []:
            _check(step, "steps", f"strategy {s['id']} step")
        if s.get("expected"):
            _check(s["expected"], "expected", f"strategy {s['id']} expected")
    for a in doc["alerts"]:
        assert KESTREL_LINK.match(a.get("link") or ""), a


def _emit_all(monkeypatch):
    """Emit paused books/strategies and bench backtests too, so every block's shape is exercised (2026-09-29:
    the live feed emits only running tracks)."""
    monkeypatch.setattr(svc, "_emitted", lambda spec: True)
    monkeypatch.setattr(svc, "_backtest_emitted", lambda row, strategy_ids: True)


@pytest.fixture
def doc(tmp_path, monkeypatch):
    _emit_all(monkeypatch)
    write_desk(tmp_path, monkeypatch)
    write_backtests(tmp_path, monkeypatch)
    return svc.build(NOW)


def test_the_live_rsi2_only_document_keeps_the_contract(tmp_path, monkeypatch):
    write_desk(tmp_path, monkeypatch)
    write_backtests(tmp_path, monkeypatch)
    live = svc.build(NOW)
    _check_document(live)
    books = {b["id"] for b in live["books"]}
    assert {r["book_id"] for r in live["runs"] if r.get("book_id")} <= books


def test_the_document_has_the_contracts_blocks_and_no_account_money(doc):
    _check_document(doc)
    assert not {"accounts", "holdings", "account_history", "benchmark"} & set(doc)


def test_a_bench_ledger_run_row_maps_to_a_contract_shaped_backtest(tmp_path, monkeypatch):
    """The contract test's own fixture (`doc`) has no bench ledger rows, so `backtests` is empty there and the
    shared shape loop over it is a no-op -- this test gives it one real row so the shape is actually exercised
    (kestrel-phase-5 plan 2026-09-27 Task 1's Backtest model)."""
    _emit_all(monkeypatch)
    write_desk(tmp_path, monkeypatch)
    write_backtests(tmp_path, monkeypatch)
    row = {"type": "run", "run_id": "20260301-abcd1234", "at": "2026-03-01T09:00:00-05:00", "name": "rsi2_ext.a",
          "family": "rsi2_ext", "hash": "habcd1234", "window": "develop", "data_class": "daily",
          "instrument_class": "equity", "verdict": "pass", "failed": [], "n": 42, "expectancy_pct": 1.25,
          "t_stat": 2.1, "base_calmar": 0.9, "stacked_calmar": 1.4, "git_sha": "abc1234",
          "confirm_end": "2026-06-30", "note": ""}
    path = tmp_path / "data" / "bench" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    document = svc.build(NOW)
    _check_document(document)
    ids = [b["id"] for b in document["backtests"]]
    assert "habcd1234:develop" in ids
    assert {"review:rsi2-20yr", "review:s6-sizing-confirm"} <= set(ids)   # the committed reviews are shape-checked too


def test_every_id_a_block_names_exists_and_every_chart_shows_its_open(doc):
    books = {b["id"] for b in doc["books"]}
    strategies = {s["id"] for s in doc["strategies"]}
    assert {b["strategy_id"] for b in doc["books"]} <= strategies
    for block in ("positions", "trades", "trade_charts"):
        assert {i["book_id"] for i in doc[block]} <= books
    assert {s["id"] for s in doc["book_history"]} <= books
    assert {r["book_id"] for r in doc["runs"] if r.get("book_id")} <= books
    held = {(i["book_id"], i["symbol"], i["opened"]) for i in doc["trades"] + doc["positions"]}
    for c in doc["trade_charts"]:
        assert (c["book_id"], c["symbol"], c["opened"]) in held
        assert c["opened"] in [b["date"] for b in c["bars"]]
    assert len(books) == len(doc["books"])


def test_the_document_is_strict_json(doc):
    json.dumps(doc, allow_nan=False)          # the response itself would fail on a NaN or an infinity


def test_w3_a_null_on_a_defaulted_non_optional_field_fails_the_strict_check(doc):
    """kestrel's Trade.exit_reason has a default ("") but is not Optional -- a null there is a validation error in
    kestrel, not a tolerated absence. The hand-mirrored contract here must reject it too."""
    assert doc["trades"], "fixture must have at least one trade to mutate"
    bad = {**doc, "trades": [{**doc["trades"][0], "exit_reason": None}]}
    with pytest.raises(AssertionError):
        _check_document(bad)


def test_m1_m2_the_mirrored_contract_rejects_a_non_finite_or_out_of_range_value(doc):
    """M1+M2: kestrel's own contract rejects the WHOLE document for a NaN/too-large number or a date outside
    1970-2200; the hand-mirrored contract here must reject it too -- a second line of defense behind build()'s own
    last pass, which is what actually keeps such a value out of a real response."""
    assert doc["trades"] and doc["positions"], "fixture must have a trade and a position to mutate"
    bad_num = {**doc, "trades": [{**doc["trades"][0], "pnl": float("nan")}]}
    with pytest.raises(AssertionError):
        _check_document(bad_num)
    bad_magnitude = {**doc, "trades": [{**doc["trades"][0], "pnl": 1e16}]}
    with pytest.raises(AssertionError):
        _check_document(bad_magnitude)
    bad_date = {**doc, "positions": [{**doc["positions"][0], "opened": "1900-01-02"}]}
    with pytest.raises(AssertionError):
        _check_document(bad_date)


def test_a_document_with_failed_blocks_still_keeps_the_contract(tmp_path, monkeypatch):
    _emit_all(monkeypatch)
    desk = write_desk(tmp_path, monkeypatch)
    (desk["data"] / "paper" / "default.json").write_text("{", encoding="utf-8")
    (desk["data"] / "journal" / "fills.jsonl").write_text("not json\n", encoding="utf-8")
    doc = svc.build(NOW)
    _check_document(doc)
    assert [a["level"] for a in doc["alerts"] if a["title"].startswith("Left out")] == ["note"]
