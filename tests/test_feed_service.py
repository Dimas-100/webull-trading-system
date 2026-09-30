"""The kestrel feed document (spec 2026-09-26 kestrel feed §2-§5, §7) over a small fictional desk."""
import json
import statistics
from datetime import date, timedelta

import pytest
from dotenv import load_dotenv

from feed_fixture import NOW, bars, fill, last_close, weekdays, write_backtests, write_desk
from webull_api.strategy import indicators
from webull_web import feed_service as svc


@pytest.fixture
def live_desk(tmp_path, monkeypatch):
    """The desk with the owner's real track states: only running books/strategies are emitted (2026-09-29)."""
    return write_desk(tmp_path, monkeypatch)


@pytest.fixture
def desk(live_desk, monkeypatch):
    """The desk with every book and strategy emitted, so the mechanics of the paused ones stay pinned."""
    monkeypatch.setattr(svc, "_emitted", lambda spec: True)
    monkeypatch.setattr(svc, "_backtest_emitted", lambda row, strategy_ids: True)
    monkeypatch.setattr(svc, "REVIEW_BACKTESTS", ())      # bench mechanics are pinned alone; reviews have own tests
    return live_desk


def _by(items, key="id"):
    return {i[key]: i for i in items}


def _trades(doc, book_id):
    return [t for t in doc["trades"] if t["book_id"] == book_id]


def _notes(doc):
    return [(a["title"], a["detail"]) for a in doc["alerts"] if a["level"] == "note"]


# ---------------------------------------------------------------- I3: settings load their own .env

def test_i3_slots_and_kill_file_are_read_from_env_even_when_nothing_else_loaded_dotenv(desk, tmp_path, monkeypatch):
    """I3: a cold process (kestrel polling before any broker route ran) must still see WEBULL_RSI2_REAL_MAX_LOTS and
    WEBULL_AUTOPILOT_KILL_FILE -- build() loads a (temp, here) .env itself via the `_load_env` seam, never the real
    one."""
    monkeypatch.delenv("WEBULL_RSI2_REAL_MAX_LOTS", raising=False)
    monkeypatch.delenv("WEBULL_AUTOPILOT_KILL_FILE", raising=False)
    kill = tmp_path / "phone-synced" / "KILL"
    kill.parent.mkdir(parents=True, exist_ok=True)
    kill.write_text("", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(f"WEBULL_RSI2_REAL_MAX_LOTS=5\nWEBULL_AUTOPILOT_KILL_FILE={kill}\n", encoding="utf-8")
    monkeypatch.setattr(svc, "_load_env", lambda: load_dotenv(env_file, override=False))
    doc = svc.build(NOW)
    assert _by(doc["books"])["rsi2-real"]["slots_total"] == 5
    serious = [a["title"] for a in doc["alerts"] if a["level"] == "serious"]
    assert "A kill file is present: phone-synced/KILL" in serious


# ---------------------------------------------------------------- the paper books

def test_paper_books_value_and_history(desk):
    doc = svc.build(NOW)
    books, history = _by(doc["books"]), _by(doc["book_history"])
    assert books["rsi2-paper"]["value"] == round(90000.0 + 50 * last_close("GOLF") + 10 * last_close("HTEL"), 2)
    assert history["rsi2-paper"]["points"] == [{"date": "2026-03-13", "value": 98500.0, "net_flow": 0.0},
                                               {"date": "2026-03-16", "value": 99000.0, "net_flow": 0.0}]


def test_paper_positions_are_their_ledgers_lots_marked_at_the_latest_close(desk):
    pos = {(p["book_id"], p["symbol"]): p for p in svc.build(NOW)["positions"] if p["book_id"].endswith("-paper")}
    assert set(pos) == {("rsi2-paper", "GOLF")}
    assert pos[("rsi2-paper", "GOLF")] == {"book_id": "rsi2-paper", "symbol": "GOLF", "quantity": 50.0,
                                          "entry_price": 80.0, "last_price": last_close("GOLF"), "stop_price": None,
                                          "opened": "2026-03-12", "note": "", "stop_resting": None}


def test_paper_books_status_and_slots(desk):
    books = _by(svc.build(NOW)["books"])
    paper = {k: (b["money"], b["strategy_id"], b["status"], b["slots_total"]) for k, b in books.items()
             if k.endswith("-paper")}
    # SIMPLIFIED 2026-09-29 (owner: RSI2-only): every paper book is parked; only the real RSI2 book runs
    assert paper == {"rsi2-paper": ("paper", "rsi2", "paused", 6)}
    assert all(b["account_id"] is None for b in books.values())


def test_bars_are_the_stores_own_rows_within_the_horizon(desk):
    from webull_api.tiingo import store
    csv_file = desk["data"] / "tiingo" / "ALFA.csv"
    lines = csv_file.read_text(encoding="utf-8").splitlines()
    lines.insert(30, "2026-02-12,40.0,41.0,39.0,nan,100000,40.0,41.0,39.0,nan,100000,0.0,1.0")   # an unusable close
    csv_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    want = [{k: r[k] for k in ("date", "open", "high", "low", "close")} for r in store.read("ALFA")
            if r["date"] >= "2026-02-01"]
    assert svc._read_bars("ALFA", "2026-02-01") == want and len(want) == len(weekdays()[21:])
    assert svc._read_bars("ECHO", "2026-02-01") == []
    far = NOW.replace(year=2028, month=6)                 # every bar is older than the horizon by then
    golf = next(p for p in svc.build(far)["positions"] if p["symbol"] == "GOLF")
    assert golf["last_price"] == 80.0 and golf["note"].startswith("no recent price")


def test_m4_a_stale_mark_says_so_but_a_fresh_one_stays_silent(desk):
    """M4: a position's last_price can come from a close up to BAR_HORIZON_DAYS old with no note at all; one more
    than 5 NYSE trading days before `now` must say so in `note` (kestrel's Position.note: a string, never null) --
    but that close is still the price used, not a fallback to entry."""
    days = _weekdays_between(date(2026, 2, 1), date(2026, 3, 2))     # last bar well over 5 sessions before NOW
    closes = _write_bars_csv(desk["data"] / "tiingo" / "STAL.csv", days, 20.0)
    state = json.loads((desk["activity"] / "rsi2_real_state.json").read_text(encoding="utf-8"))
    state["owned_lots"].append({"symbol": "STAL", "shares": 3.0, "entry_price": 20.0, "entry_date": "2026-02-15",
                                "decision_id": "d-2"})
    (desk["activity"] / "rsi2_real_state.json").write_text(json.dumps(state), encoding="utf-8")
    doc = svc.build(NOW)
    stal = next(p for p in doc["positions"] if p["symbol"] == "STAL")
    assert stal["last_price"] == closes[-1]
    assert stal["note"] == "last close 2026-03-02"
    dlta = next(p for p in doc["positions"] if p["symbol"] == "DLTA")
    assert dlta["note"] == ""                     # fresh: 2026-03-17 (the fixture's LAST_BAR) is inside the window


def test_a_half_written_paper_account_is_a_note_and_is_never_renamed(desk):
    """paper_store.load would quarantine a torn account by renaming it; the feed leaves the book out and touches
    nothing."""
    torn = desk["data"] / "paper" / "default.json"
    torn.write_text('{"cash": 900', encoding="utf-8")
    doc = svc.build(NOW)
    assert "rsi2-paper" not in _by(doc["books"]) and "rsi2-real" in _by(doc["books"])
    assert [t for t, _ in _notes(doc)] == ["Left out of the feed: the rsi2-paper book"]
    assert torn.read_text(encoding="utf-8") == '{"cash": 900'
    assert sorted(p.name for p in torn.parent.iterdir()) == ["default.json"]


def test_a_paper_position_the_ledger_does_not_know_counts_in_value_but_is_not_an_rsi2_position(desk):
    doc = svc.build(NOW)
    assert [p["symbol"] for p in doc["positions"] if p["book_id"] == "rsi2-paper"] == ["GOLF"]
    value = _by(doc["books"])["rsi2-paper"]["value"]
    assert value == round(90000.0 + 50 * last_close("GOLF") + 10 * last_close("HTEL"), 2)     # HTEL counts


def test_m10_rsi2_paper_trades_are_the_default_accounts_own_not_every_simulator_round_trip(desk):
    """M10: fills.jsonl's `paper` source mixes every simulator account, including the proven-strategy runner's own
    `proven` account (its fills carry a strategy_id) -- only the default account's fills with no strategy_id are
    rsi2-paper's trades."""
    fills = desk["data"] / "journal" / "fills.jsonl"
    extra = [{**fill("v1", "paper", "PRVN", "BUY", 1.0, 10.0, "2026-02-10T09:30:00-05:00"),
             "account_id": "proven-1", "strategy_id": "trial-9"},
            {**fill("v2", "paper", "PRVN", "SELL", 1.0, 11.0, "2026-02-11T09:30:00-05:00"),
             "account_id": "proven-1", "strategy_id": "trial-9"}]
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra),
                     encoding="utf-8")
    doc = svc.build(NOW)
    assert "PRVN" not in {t["symbol"] for t in _trades(doc, "rsi2-paper")}
    assert "FXTR" in {t["symbol"] for t in _trades(doc, "rsi2-paper")}          # the default account's own trade


def test_a_broken_book_is_a_note_and_the_rest_still_serves(desk, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setitem(svc._BUILDERS, "rsi2-paper", boom)
    doc = svc.build(NOW)
    assert ("Left out of the feed: the rsi2-paper book", "RuntimeError") in _notes(doc)
    assert "rsi2-paper" not in _by(doc["books"]) and "rsi2-real" in _by(doc["books"])


def test_a_note_alert_carries_no_path_only_the_exception_class_and_file_name(desk, monkeypatch):
    """W5: a raw exception message can hold an absolute path (or worse); the note names the file, never the path
    or any other message text."""
    def boom(*a, **k):
        raise PermissionError(13, "Permission denied", r"C:\Users\someone\secret\state.json")
    monkeypatch.setitem(svc._BUILDERS, "rsi2-paper", boom)
    doc = svc.build(NOW)
    detail = next(d for t, d in _notes(doc) if "rsi2-paper" in t)
    assert detail == "PermissionError reading state.json"
    assert "C:" not in detail and "secret" not in detail and "someone" not in detail


def test_a_note_alert_with_no_file_is_just_the_exception_class(desk, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("some message that must not appear")
    monkeypatch.setitem(svc._BUILDERS, "rsi2-paper", boom)
    doc = svc.build(NOW)
    detail = next(d for t, d in _notes(doc) if "rsi2-paper" in t)
    assert detail == "RuntimeError"


# ---------------------------------------------------------------- M1+M2: one bad value never costs the whole feed

def test_m1_a_nan_fill_price_drops_only_that_trade(desk):
    """M1: pairing's ClosedTrade fields flow through _from_closed/_trade unchecked; a NaN price must not 500 the
    whole response -- only the trade holding it is dropped, with a note."""
    fills = desk["data"] / "journal" / "fills.jsonl"
    extra = [fill("z1", "real", "ZANY", "BUY", 1.0, float("nan"), "2026-02-27T14:31:00.000Z"),
             fill("z2", "real", "ZANY", "SELL", 1.0, 56.0, "2026-03-02T14:31:00.000Z")]
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra),
                     encoding="utf-8")
    doc = svc.build(NOW)
    json.dumps(doc, allow_nan=False)             # must not raise
    assert "ZANY" not in {t["symbol"] for t in doc["trades"]}
    assert ("trades: 1 item dropped (a value kestrel can't show)", "") in _notes(doc)


def test_m2_a_lot_dated_1900_drops_only_that_position_and_the_book_survives(desk):
    """M2: kestrel rejects the WHOLE document for any date outside 1970-2200; the feed drops just the position that
    holds it, and the book's own `started` must not be dragged to 1900 by the very item that got dropped."""
    state = json.loads((desk["activity"] / "rsi2_state.json").read_text(encoding="utf-8"))
    state["owned_lots"].append({"symbol": "OLDX", "shares": 5.0, "entry_price": 10.0, "entry_date": "1900-01-02"})
    (desk["activity"] / "rsi2_state.json").write_text(json.dumps(state), encoding="utf-8")
    doc = svc.build(NOW)
    json.dumps(doc, allow_nan=False)
    assert "OLDX" not in {p["symbol"] for p in doc["positions"]}
    assert ("positions: 1 item dropped (a value kestrel can't show)", "") in _notes(doc)
    rsi2_paper = next(b for b in doc["books"] if b["id"] == "rsi2-paper")
    assert rsi2_paper["started"] == "2026-01-20"          # unchanged: the dropped lot's date never counted


# ---------------------------------------------------------------- the real books and every book's trades

def test_real_round_trips_are_attributed_by_the_one_rule(desk):
    doc = svc.build(NOW)
    rsi2_real = [(t["symbol"], t["opened"], t["closed"]) for t in _trades(doc, "rsi2-real")]
    assert rsi2_real == [("ALFA", "2026-02-02", "2026-02-05")]
    assert [t["symbol"] for t in _trades(doc, "day-orb-real")] == ["BRAV"]
    # a gate-denied BUY and a placed protective SELL on CHRL's entry day don't make it an RSI2 entry
    assert [t["symbol"] for t in _trades(doc, "pullback-real")] == ["CHRL"]
    placed = {("ALFA", "2026-02-02")}
    # W1: a placement on P matches a fill on P or any of the next 5 trading days (2026-02-02 .. 2026-02-09)
    assert svc.attribute("alfa", "2026-02-02", "2026-02-02", placed) == "rsi2-real"   # rule 1 comes first
    assert svc.attribute("ALFA", "2026-02-03", "2026-02-03", placed) == "rsi2-real"   # 1 trading day later, same day
    assert svc.attribute("ALFA", "2026-02-09", "2026-02-09", placed) == "rsi2-real"   # the window's last day
    assert svc.attribute("ALFA", "2026-02-10", "2026-02-10", placed) == "day-orb-real"  # one trading day too late
    assert svc.attribute("ALFA", "2026-02-10", "2026-02-13", placed) == "pullback-real"
    assert svc.attribute("ALFA", None, None, placed) == "pullback-real"
    assert doc["sources"][0]["detail"] == svc.ATTRIBUTION_RULE


def test_i1_an_entry_source_placement_never_makes_rsi2_real(desk):
    """I1: the autopilot's 17:45 swing-screen entries are logged `source: "entry"` (the pullback track, tracks.py:
    "the 17:45 autopilot runs the same screen behind its gate") -- only `decision:*`-sourced placements are RSI2's,
    so an `entry`-source placed BUY must not turn its round trip into rsi2-real."""
    fills = desk["data"] / "journal" / "fills.jsonl"
    extra = [
        fill("n1", "real", "ENTY", "BUY", 2.0, 55.0, "2026-02-27T14:31:00.000Z"),
        fill("n2", "real", "ENTY", "SELL", 2.0, 56.0, "2026-03-02T14:31:00.000Z"),
    ]
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra),
                     encoding="utf-8")
    log = desk["autopilot"] / "log" / "2026-02-27.jsonl"
    log.write_text(json.dumps({"source": "entry", "symbol": "ENTY", "side": "BUY", "allow": True, "layer": "ok",
                               "placed": True, "result": {"submitted": True}}) + "\n", encoding="utf-8")
    doc = svc.build(NOW)
    assert "ENTY" not in {t["symbol"] for t in _trades(doc, "rsi2-real")}
    assert "ENTY" in {t["symbol"] for t in _trades(doc, "pullback-real")}


def test_w1_attribution_matches_a_placements_later_fill_within_the_trading_day_window(desk):
    """W1: a resting/GTC autopilot BUY placement can fill a later day; still rsi2-real within 5 trading days of the
    placement, not beyond."""
    fills = desk["data"] / "journal" / "fills.jsonl"
    extra = [
        fill("w1", "real", "MIKE", "BUY", 1.0, 70.0, "2026-02-03T14:31:00.000Z"),   # 1 trading day after the placement
        fill("w2", "real", "MIKE", "SELL", 1.0, 71.0, "2026-02-06T14:31:00.000Z"),
        fill("w3", "real", "NOVA", "BUY", 1.0, 80.0, "2026-02-11T14:31:00.000Z"),   # 7 trading days after -> not rsi2
        fill("w4", "real", "NOVA", "SELL", 1.0, 79.0, "2026-02-13T14:31:00.000Z"),
    ]
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra),
                     encoding="utf-8")
    log = desk["autopilot"] / "log" / "2026-02-02.jsonl"
    log.write_text(log.read_text(encoding="utf-8") +
                   json.dumps({"source": "decision:immediate", "symbol": "MIKE", "side": "BUY", "allow": True,
                              "layer": "ok", "placed": True, "result": {"submitted": True}}) + "\n" +
                   json.dumps({"source": "decision:immediate", "symbol": "NOVA", "side": "BUY", "allow": True,
                              "layer": "ok", "placed": True, "result": {"submitted": True}}) + "\n",
                   encoding="utf-8")
    doc = svc.build(NOW)
    rsi2_real = {(t["symbol"], t["opened"], t["closed"]) for t in _trades(doc, "rsi2-real")}
    assert ("MIKE", "2026-02-03", "2026-02-06") in rsi2_real
    assert "NOVA" not in {t["symbol"] for t in _trades(doc, "rsi2-real")}
    assert "NOVA" in {t["symbol"] for t in _trades(doc, "pullback-real")}


def test_w1_the_earliest_unmatched_entry_claims_the_placement(desk):
    """W1 ambiguity ruling: if a placement's window could match several entries of the same symbol, only the
    earliest unmatched one is claimed; the rest fall through to the ordinary rule."""
    fills = desk["data"] / "journal" / "fills.jsonl"
    extra = [
        fill("e1", "real", "OTIS", "BUY", 1.0, 40.0, "2026-02-03T14:31:00.000Z"),   # earliest -> claims the placement
        fill("e2", "real", "OTIS", "SELL", 1.0, 41.0, "2026-02-04T14:31:00.000Z"),
        fill("e3", "real", "OTIS", "BUY", 1.0, 42.0, "2026-02-05T14:31:00.000Z"),   # later -> not claimed
        fill("e4", "real", "OTIS", "SELL", 1.0, 43.0, "2026-02-06T14:31:00.000Z"),
    ]
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra),
                     encoding="utf-8")
    log = desk["autopilot"] / "log" / "2026-02-02.jsonl"
    log.write_text(log.read_text(encoding="utf-8") +
                   json.dumps({"source": "decision:immediate", "symbol": "OTIS", "side": "BUY", "allow": True,
                              "layer": "ok", "placed": True, "result": {"submitted": True}}) + "\n",
                   encoding="utf-8")
    doc = svc.build(NOW)
    by_open = {t["opened"]: t["book_id"] for t in doc["trades"] if t["symbol"] == "OTIS"}
    assert by_open["2026-02-03"] == "rsi2-real"
    assert by_open["2026-02-05"] == "pullback-real"


def test_i2_a_placed_buy_closed_by_two_sells_gives_both_slices_the_same_book(desk):
    """I2: FIFO pairing yields two ClosedTrades sharing one entry fill (a partially filled exit, or a whole-share
    stop plus a protect:synthetic sell of the fractional remainder) -- both must get the SAME label, not just the
    first one to claim the placement."""
    fills = desk["data"] / "journal" / "fills.jsonl"
    extra = [
        fill("q1", "real", "QUIN", "BUY", 3.0, 40.0, "2026-02-20T14:31:00.000Z"),
        fill("q2", "real", "QUIN", "SELL", 1.0, 41.0, "2026-02-23T14:31:00.000Z"),
        fill("q3", "real", "QUIN", "SELL", 2.0, 42.0, "2026-02-24T14:31:00.000Z"),
    ]
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra),
                     encoding="utf-8")
    log = desk["autopilot"] / "log" / "2026-02-20.jsonl"
    log.write_text(json.dumps({"source": "decision:immediate", "symbol": "QUIN", "side": "BUY", "allow": True,
                               "layer": "ok", "placed": True, "result": {"submitted": True}}) + "\n",
                   encoding="utf-8")
    doc = svc.build(NOW)
    quin = [(t["book_id"], t["quantity"]) for t in doc["trades"] if t["symbol"] == "QUIN"]
    assert sorted(quin) == [("rsi2-real", 1.0), ("rsi2-real", 2.0)]


def test_i2_an_open_remainder_of_a_placed_buy_claims_it_and_stays_out_of_pullback_real(desk):
    """I2: an unsold real BUY that a placement matches is rsi2-real's, even before the RSI2 real ledger has
    adopted it into owned_lots -- it must not leak into pullback-real's positions (the ambiguity ruling)."""
    fills = desk["data"] / "journal" / "fills.jsonl"
    fills.write_text(fills.read_text(encoding="utf-8") +
                     json.dumps(fill("i1", "real", "IVEY", "BUY", 4.0, 70.0, "2026-03-05T14:31:00.000Z")) + "\n",
                     encoding="utf-8")
    log = desk["autopilot"] / "log" / "2026-03-05.jsonl"
    log.write_text(json.dumps({"source": "decision:immediate", "symbol": "IVEY", "side": "BUY", "allow": True,
                               "layer": "ok", "placed": True, "result": {"submitted": True}}) + "\n",
                   encoding="utf-8")
    doc = svc.build(NOW)
    assert "IVEY" not in {p["symbol"] for p in doc["positions"]}


def test_trade_fields_come_from_their_sources(desk):
    doc = svc.build(NOW)
    assert _trades(doc, "rsi2-real") == [{"book_id": "rsi2-real", "symbol": "ALFA", "opened": "2026-02-02",
                                          "closed": "2026-02-05", "entry_price": 40.0, "exit_price": 42.0,
                                          "quantity": 3.0, "pnl": 6.0, "return_pct": 5.0, "r_multiple": None,
                                          "exit_reason": ""}]
    assert _trades(doc, "rsi2-paper") == [{"book_id": "rsi2-paper", "symbol": "FXTR", "opened": "2026-01-20",
                                           "closed": "2026-01-23", "entry_price": 60.0, "exit_price": 63.0,
                                           "quantity": 100.0, "pnl": 300.0, "return_pct": 5.0, "r_multiple": None,
                                           "exit_reason": ""}]


def test_real_books_value_their_open_lots_at_the_latest_close_and_carry_no_history(desk):
    doc = svc.build(NOW)
    books = _by(doc["books"])
    assert books["rsi2-real"]["value"] == round(2.0 * last_close("DLTA"), 2)
    assert books["pullback-real"]["value"] == 60.0               # ECHO 5 x 12.00: no price file, marked at entry
    assert books["day-orb-real"]["value"] == 0.0
    assert {s["id"] for s in doc["book_history"]} == {"rsi2-paper"}
    real = {(p["book_id"], p["symbol"]): p for p in doc["positions"] if p["book_id"].endswith("-real")}
    assert set(real) == {("rsi2-real", "DLTA"), ("pullback-real", "ECHO")}     # DLTA is the ledger's, not pullback's
    assert real[("rsi2-real", "DLTA")] == {"book_id": "rsi2-real", "symbol": "DLTA", "quantity": 2.0,
                                          "entry_price": 50.0, "last_price": last_close("DLTA"), "stop_price": None,
                                          "opened": "2026-03-10", "note": "", "stop_resting": None}


def test_real_books_status_and_slots_and_every_books_start(desk):
    books = _by(svc.build(NOW)["books"])
    real = {k: (b["money"], b["strategy_id"], b["status"], b["slots_total"]) for k, b in books.items()
            if k.endswith("-real")}
    assert real == {"rsi2-real": ("real", "rsi2", "running", 5), "pullback-real": ("real", "pullback", "running", None),
                    "day-orb-real": ("real", "day-orb", "paused", None)}
    assert list(books) == ["rsi2-real", "pullback-real", "day-orb-real", "rsi2-paper"]
    assert {k: b["started"] for k, b in books.items()} == {
        "rsi2-real": "2026-02-02", "pullback-real": "2026-02-12", "day-orb-real": "2026-02-10",
        "rsi2-paper": "2026-01-20"}


def test_a_half_written_real_ledger_is_a_note_not_a_flat_book(desk):
    """rsi2_real_store.save writes with a plain write_text, so a read can land mid-write. Both real books that read
    the ledger are left out (never shown as flat); the others still serve."""
    torn = desk["activity"] / "rsi2_real_state.json"
    torn.write_text('{"schema_version": 1, "owned_lots": [{"symbol": "DLT', encoding="utf-8")
    doc = svc.build(NOW)
    books = _by(doc["books"])
    assert "rsi2-real" not in books and "pullback-real" not in books
    assert "day-orb-real" in books and "rsi2-paper" in books
    assert {t for t, _ in _notes(doc)} == {"Left out of the feed: the rsi2-real book",
                                           "Left out of the feed: the pullback-real book"}


def test_a_fills_line_that_is_not_json_or_not_a_fill_is_skipped_and_counted(desk):
    fills = desk["data"] / "journal" / "fills.jsonl"
    fills.write_text(fills.read_text(encoding="utf-8") + '{"id": "half", "source": "re\n' + '{"id": "r9"}\n',
                     encoding="utf-8")
    doc = svc.build(NOW)
    assert len(doc["trades"]) == 4
    assert ("fills.jsonl: lines skipped", "2 lines not JSON or not a fill") in _notes(doc)


def test_m9_a_trade_with_an_unparseable_timestamp_is_counted_not_silently_dropped(desk):
    """M9: pairing accepts epoch-millisecond stamps (`_parse_dt`'s fallback) but `_day`/`_et` don't -- such a trade
    must be counted in a note, not vanish with no trace, as a skipped fill already is."""
    fills = desk["data"] / "journal" / "fills.jsonl"
    extra = [fill("e1", "real", "EPOX", "BUY", 1.0, 20.0, "1740000000000"),
             fill("e2", "real", "EPOX", "SELL", 1.0, 21.0, "2026-02-15T14:31:00.000Z")]
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra),
                     encoding="utf-8")
    doc = svc.build(NOW)
    assert "EPOX" not in {t["symbol"] for t in doc["trades"]}
    assert any(title.startswith("trades:") and "unparseable timestamp" in detail for title, detail in _notes(doc))


def test_a_symbol_with_no_price_file_is_marked_at_the_entry_price(desk):
    echo = next(p for p in svc.build(NOW)["positions"] if p["symbol"] == "ECHO")
    assert echo["last_price"] == echo["entry_price"] == 12.0
    assert echo["note"] == "no recent price in the Tiingo store: marked at the entry price"


def test_w6_lots_keeps_a_lot_whose_price_or_shares_is_exactly_zero(desk):
    state = {"schema_version": 1, "owned_lots": [
        {"symbol": "ZERO", "shares": 5.0, "entry_price": 0.0, "entry_date": "2026-03-01"},
        {"symbol": "NILQ", "shares": 0.0, "entry_price": 10.0, "entry_date": "2026-03-01"}],
        "pending_orders": [], "reconciliation_log": [], "updated_at": None}
    (desk["activity"] / "rsi2_state.json").write_text(json.dumps(state), encoding="utf-8")
    positions = {p["symbol"] for p in svc.build(NOW)["positions"] if p["book_id"] == "rsi2-paper"}
    assert {"ZERO", "NILQ"} <= positions


# ------------------------------------------ stop_resting (a protective stop known from the autopilot audit log)
# DLTA is the fixture's own open rsi2-real lot (2 shares, entry_date "2026-03-10"); ECHO is pullback-real's open
# remainder (entry "2026-03-11"). Neither has a qualifying log row in the base fixture, so both stay `None` there
# (pinned by the updated exact-dict assertions above). The stop LEVEL is never reported -- only whether one rests.

def _write_log(desk, day, rows):
    path = desk["autopilot"] / "log" / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _protect_row(symbol, *, source="protect", placed=True, submitted=True):
    return {"source": source, "symbol": symbol, "side": "SELL", "allow": True, "layer": "risk-reducing",
            "placed": placed, "result": {"submitted": submitted}}


def _clearing_row(symbol, *, source="exit:green_day"):
    return {"source": source, "symbol": symbol, "side": "SELL", "allow": True, "layer": "ok", "placed": True,
            "result": {"submitted": True}}


def test_stop_resting_true_when_a_protect_sell_lands_on_or_after_the_lots_entry(desk):
    _write_log(desk, "2026-03-11", [_protect_row("DLTA")])          # entry is 2026-03-10
    dlta = next(p for p in svc.build(NOW)["positions"] if p["symbol"] == "DLTA")
    assert dlta["stop_resting"] is True
    assert dlta["note"] == "protective stop placed 2026-03-11 (level not reported)"


def test_stop_resting_none_when_the_protect_sell_is_before_the_lots_entry(desk):
    _write_log(desk, "2026-03-05", [_protect_row("DLTA")])          # before entry 2026-03-10
    dlta = next(p for p in svc.build(NOW)["positions"] if p["symbol"] == "DLTA")
    assert dlta["stop_resting"] is None
    assert dlta["note"] == ""


def test_stop_resting_none_when_a_later_closing_sell_supersedes_the_protect(desk):
    _write_log(desk, "2026-03-11", [_protect_row("DLTA")])
    _write_log(desk, "2026-03-13", [_clearing_row("DLTA")])         # a later exit: SELL clears the resting stop
    dlta = next(p for p in svc.build(NOW)["positions"] if p["symbol"] == "DLTA")
    assert dlta["stop_resting"] is None
    assert dlta["note"] == ""


def test_stop_resting_none_when_only_protect_synthetic_rows_exist(desk):
    """protect:synthetic sells a fractional remainder -- it is NOT a resting order, and must not be confused with
    an exact "protect" source."""
    _write_log(desk, "2026-03-11", [_protect_row("DLTA", source="protect:synthetic")])
    dlta = next(p for p in svc.build(NOW)["positions"] if p["symbol"] == "DLTA")
    assert dlta["stop_resting"] is None


def test_stop_resting_none_when_the_protect_row_was_not_submitted(desk):
    _write_log(desk, "2026-03-11", [_protect_row("DLTA", submitted=False)])
    dlta = next(p for p in svc.build(NOW)["positions"] if p["symbol"] == "DLTA")
    assert dlta["stop_resting"] is None


def test_stop_resting_appends_to_an_existing_note_rather_than_replacing_it(desk):
    """A stale-mark note (M4) and a resting-stop note both apply to the same lot -- the stop note is appended, the
    stale note is not lost."""
    days = _weekdays_between(date(2026, 2, 1), date(2026, 3, 2))
    _write_bars_csv(desk["data"] / "tiingo" / "STAL.csv", days, 20.0)
    state = json.loads((desk["activity"] / "rsi2_real_state.json").read_text(encoding="utf-8"))
    state["owned_lots"].append({"symbol": "STAL", "shares": 3.0, "entry_price": 20.0, "entry_date": "2026-02-15",
                                "decision_id": "d-2"})
    (desk["activity"] / "rsi2_real_state.json").write_text(json.dumps(state), encoding="utf-8")
    _write_log(desk, "2026-02-16", [_protect_row("STAL")])
    stal = next(p for p in svc.build(NOW)["positions"] if p["symbol"] == "STAL")
    assert stal["stop_resting"] is True
    assert stal["note"] == "last close 2026-03-02; protective stop placed 2026-02-16 (level not reported)"


def test_stop_resting_paper_positions_never_get_it_even_with_a_matching_symbol_and_source(desk):
    _write_log(desk, "2026-03-13", [_protect_row("GOLF")])          # GOLF is rsi2-paper's own open lot
    golf = next(p for p in svc.build(NOW)["positions"] if p["symbol"] == "GOLF")
    assert golf["stop_resting"] is None
    assert golf["note"] == ""


def test_stop_resting_a_torn_log_line_is_skipped_not_fatal(desk):
    path = desk["autopilot"] / "log" / "2026-03-11.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_protect_row("DLTA")) + "\n" + '{"source": "protect", "symbol": "DLT\n',
                    encoding="utf-8")
    doc = svc.build(NOW)
    dlta = next(p for p in doc["positions"] if p["symbol"] == "DLTA")
    assert dlta["stop_resting"] is True


def test_stop_resting_pullback_real_gets_it_too(desk):
    _write_log(desk, "2026-03-12", [_protect_row("ECHO")])          # ECHO is pullback-real's open remainder
    echo = next(p for p in svc.build(NOW)["positions"] if p["symbol"] == "ECHO")
    assert echo["stop_resting"] is True
    # ECHO has no Tiingo price file on purpose (fixture comment) -- _mark's own note is still there, appended to
    assert echo["note"] == ("no recent price in the Tiingo store: marked at the entry price; "
                            "protective stop placed 2026-03-12 (level not reported)")


def test_a_broken_protect_log_is_a_note_and_positions_still_serve_without_stop_resting(desk, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(svc._Files, "protect_events", boom)
    doc = svc.build(NOW)
    assert ("Left out of the feed: the autopilot audit log (protective stops)", "RuntimeError") in _notes(doc)
    dlta = next(p for p in doc["positions"] if p["symbol"] == "DLTA")
    assert dlta["stop_resting"] is None
    assert "rsi2-real" in _by(doc["books"])            # the book itself is NOT dropped by this failure


# ---------------------------------------------------------------- strategies

def test_strategies_steps_and_the_backtest_expectation(desk, tmp_path, monkeypatch):
    write_backtests(tmp_path, monkeypatch)
    strategies = _by(svc.build(NOW)["strategies"])
    assert list(strategies) == ["rsi2", "pullback", "day-orb"]
    assert [s["label"] for s in strategies["rsi2"]["steps"]] == ["Universe", "Entry", "Protect", "Exit"]
    assert "8% GTC stops" in strategies["rsi2"]["steps"][2]["text"]
    assert {k: s["review_at_trades"] for k, s in strategies.items()} == {
        "rsi2": 30, "pullback": 20, "day-orb": None}
    returns = [2.0, -1.0, 3.0, -12.5]
    e = strategies["rsi2"]["expected"]
    assert (e["win_rate"], e["avg_trade_pct"], e["avg_win_pct"], e["avg_loss_pct"]) == (50.0, -2.125, 2.5, -6.75)
    assert e["sd_trade_pct"] == round(statistics.stdev(returns), 4)
    assert e["trades_per_month"] == round(4 / (60 / 30.4375), 2)          # 2020-01-02 .. 2020-03-02
    assert len(e["distribution"]) == 20 and sum(e["distribution"]) == 100.0
    assert [i for i, share in enumerate(e["distribution"]) if share] == [0, 9, 12, 13]   # -12.5 folds into the end
    assert (e["window"], e["source"]) == ("2020–2020", svc.EXPECTED_LABELS["rsi2"])
    assert e["cagr_pct"] == round(((99.0 / 100.0) ** (365 / 60) - 1) * 100, 2) and e["max_drawdown_pct"] == -10.0
    assert strategies["day-orb"]["expected"] is None         # the ORB review holds cell summaries, no trades
    assert strategies["pullback"]["expected"] is None
    assert all(s["watch"] == [] for s in strategies.values())


def test_the_backtest_files_are_committed():
    # The plan pages (plan_service, archived 2026-09-28) were the other reader these were pinned equal to; the feed
    # is the only reader now, so what stays pinned is that the committed files exist.
    for path in (svc.RSI2_BACKTEST, svc.ORB_BACKTEST, svc.SIZING_CONFIRM):
        assert path.is_file(), path
    assert set(svc.EXPECTED_LABELS) == {"rsi2", "day-orb"}


# ---------------------------------------------------------------- charts

def test_charts_show_the_bars_around_each_trade_with_the_strategys_indicator(desk):
    charts = {(c["book_id"], c["symbol"]): c for c in svc.build(NOW)["trade_charts"]}
    days = weekdays()
    alfa = charts[("rsi2-real", "ALFA")]
    lo, hi = days.index("2026-02-02") - 20, days.index("2026-02-05") + 1 + 10
    assert [b["date"] for b in alfa["bars"]] == days[lo:hi]
    assert alfa["bars"][0] == bars("ALFA")[lo]
    # W2: this fixture's whole history is short (~55 sessions total) -- fewer than RSI_WARMUP precede ALFA's open,
    # so its chart has no indicator (the bars still show); the dedicated W2 tests cover a real, non-None RSI(2).
    assert alfa["indicator"] is None
    assert alfa["stop"] is None
    dlta = charts[("rsi2-real", "DLTA")]                 # an open position: to the latest bar
    assert dlta["opened"] == "2026-03-10" and dlta["bars"][-1]["date"] == days[-1]
    assert charts[("day-orb-real", "BRAV")]["indicator"] is None


def test_a_book_charts_its_last_eight_closed_trades_and_each_open_position(desk):
    fills = desk["data"] / "journal" / "fills.jsonl"
    extra = []
    for i, day in enumerate(weekdays()[25:35]):          # ten more paper round trips, one a day
        extra.append({"id": f"x{i}b", "source": "paper", "account_id": "paper-1", "symbol": "FXTR", "side": "BUY",
                      "quantity": 1.0, "price": 60.0, "filled_at_iso": f"{day}T09:30:00-05:00", "order_type": "LIMIT"})
        extra.append({"id": f"x{i}s", "source": "paper", "account_id": "paper-1", "symbol": "FXTR", "side": "SELL",
                      "quantity": 1.0, "price": 61.0, "filled_at_iso": f"{day}T15:30:00-05:00", "order_type": "LIMIT"})
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra), encoding="utf-8")
    doc = svc.build(NOW)
    paper = [c for c in doc["trade_charts"] if c["book_id"] == "rsi2-paper"]
    assert len(_trades(doc, "rsi2-paper")) == 11
    assert [c["opened"] for c in paper if c["symbol"] == "FXTR"] == weekdays()[27:35]
    assert [c["symbol"] for c in paper if c["symbol"] != "FXTR"] == ["GOLF"]


def test_a_trade_or_position_without_bars_gets_no_chart(desk):
    doc = svc.build(NOW)
    assert "ECHO" not in {c["symbol"] for c in doc["trade_charts"]}     # no price file at all
    (desk["data"] / "tiingo" / "ALFA.csv").write_text("date,open,high,low,close\n", encoding="utf-8")
    assert "ALFA" not in {c["symbol"] for c in svc.build(NOW)["trade_charts"]}     # a file with no bars


def test_m3_a_symbol_both_marked_and_charted_is_read_once_per_request(desk, monkeypatch):
    """M3: bars() (marking a position) and chart_bars() (building its chart) used separate memo keys, so a symbol
    needing both re-read and re-parsed its whole Tiingo CSV twice, against spec §6 ("files read once per request")
    and _Files' own docstring."""
    calls: list[str] = []
    orig = svc._read_bars

    def spy(symbol, since):
        calls.append(symbol.strip().upper())
        return orig(symbol, since)
    monkeypatch.setattr(svc, "_read_bars", spy)
    doc = svc.build(NOW)
    assert "DLTA" in {p["symbol"] for p in doc["positions"]}            # marked: an open rsi2-real lot
    assert "DLTA" in {c["symbol"] for c in doc["trade_charts"]}         # and charted: an open position gets one
    assert calls.count("DLTA") == 1


def _weekdays_between(start, end):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _write_bars_csv(path, days, base):
    header = "date,open,high,low,close,volume,adj_open,adj_high,adj_low,adj_close,adj_volume,div_cash,split_factor\n"
    closes, lines = [], []
    for i, d in enumerate(days):
        c = round(base + ((i * 7 + 3) % 11) - 5, 2)
        closes.append(c)
        lines.append(f"{d.isoformat()},{c - 0.5},{c + 1.0},{c - 1.5},{c},100000,{c - 0.5},{c + 1.0},{c - 1.5},{c},"
                     f"100000,0.0,1.0\n")
    path.write_text(header + "".join(lines), encoding="utf-8")
    return closes


def test_w2_a_chart_reads_bars_from_its_own_earliest_open_not_a_fixed_horizon(desk):
    """W2: BAR_HORIZON_DAYS anchored to `now` used to starve a chart's RSI(2) warm-up when the trade was old enough
    that the horizon cut off history the file actually has. Reading from (the symbol's earliest charted open - 200
    calendar days) instead gives the same values as a genesis computation over the symbol's whole file."""
    days = _weekdays_between(date(2022, 1, 3), date(2024, 6, 1))
    closes = _write_bars_csv(desk["data"] / "tiingo" / "OLDA.csv", days, 100.0)
    opened, closed = "2024-04-25", "2024-04-30"
    # a paper fill (not real): the "rsi2-paper" book charts every paper round trip with the rsi2 indicator,
    # with no dependence on the W1 attribution rule this test isn't about
    extra = [fill("o1", "paper", "OLDA", "BUY", 1.0, 90.0, f"{opened}T14:31:00.000Z"),
             fill("o2", "paper", "OLDA", "SELL", 1.0, 91.0, f"{closed}T14:31:00.000Z")]
    fills = desk["data"] / "journal" / "fills.jsonl"
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra),
                     encoding="utf-8")
    doc = svc.build(NOW)
    chart = next(c for c in doc["trade_charts"] if c["symbol"] == "OLDA")
    lo = days.index(date(2024, 4, 25)) - 20
    hi = days.index(date(2024, 4, 30)) + 1 + 10
    genesis = indicators.rsi(closes, 2)
    want = [round(v, 2) if v is not None else None for v in genesis[lo:hi]]
    assert chart["indicator"]["values"] == want


def test_w2_a_chart_gets_no_indicator_when_its_symbols_file_starts_inside_the_warm_up(desk):
    """W2 ambiguity ruling: fewer than RSI_WARMUP sessions precede a chart's first bar in the rows read -> that
    chart's indicator is None, but the chart itself (its bars) stays."""
    days = _weekdays_between(date(2026, 2, 16), date(2026, 3, 10))    # 17 trading days: far short of RSI_WARMUP
    _write_bars_csv(desk["data"] / "tiingo" / "YOUNG.csv", days, 50.0)
    opened, closed = "2026-03-02", "2026-03-05"
    extra = [fill("y1", "paper", "YOUNG", "BUY", 1.0, 50.0, f"{opened}T14:31:00.000Z"),
             fill("y2", "paper", "YOUNG", "SELL", 1.0, 51.0, f"{closed}T14:31:00.000Z")]
    fills = desk["data"] / "journal" / "fills.jsonl"
    fills.write_text(fills.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in extra),
                     encoding="utf-8")
    doc = svc.build(NOW)
    chart = next(c for c in doc["trade_charts"] if c["symbol"] == "YOUNG")
    assert chart["indicator"] is None
    assert len(chart["bars"]) > 0


# ---------------------------------------------------------------- runs and alerts

def _late_row(monkeypatch, key="morning_test"):
    """Add a live 09:25 task row with a 120-min grace to a copy of the routine. The RSI2-only routine (2026-09-29)
    has no morning task that is late at NOW; these tests pin the late / due / done MECHANISM, which needs one."""
    from webull_web import routine
    monkeypatch.setattr(routine, "ROUTINE", (routine.RoutineRow("09:25", "swing_rsi2", "Morning test task", "task",
                                                                evidence=key, grace_min=120),) + routine.ROUTINE)


def test_the_live_routine_has_nothing_paused_or_late(desk):
    runs = svc.build(NOW)["runs"]
    assert not [r for r in runs if r["status"] in ("paused", "late")]


def test_runs_are_the_last_run_a_late_firing_and_the_next_firing_per_routine_key(desk, monkeypatch):
    _late_row(monkeypatch)
    doc = svc.build(NOW)
    runs = [(r["time"], r["status"], r["book_id"]) for r in doc["runs"]]
    assert ("2026-03-18T09:31:40-04:00", "done", "rsi2-real") in runs
    assert ("2026-03-18T15:45:00-04:00", "due", "rsi2-real") in runs
    assert ("2026-03-17T17:31:10-04:00", "failed", "rsi2-paper") in runs
    assert ("2026-03-18T18:25:00-04:00", "due", "rsi2-paper") in runs   # the note (its run_key) moved to 18:25
    assert ("2026-03-18T09:25:00-04:00", "late", None) in runs
    assert ("2026-03-19T09:25:00-04:00", "due", None) in runs
    assert not [r for r in doc["runs"] if r["status"] == "paused"]
    assert [r["time"] for r in doc["runs"]] == sorted(r["time"] for r in doc["runs"])
    books = _by(doc["books"])
    assert books["rsi2-real"]["next_run"] == "2026-03-18T15:45:00-04:00"
    assert books["rsi2-paper"]["next_run"] == "2026-03-18T18:25:00-04:00"
    late = next(r for r in doc["runs"] if r["status"] == "late")
    assert late["detail"] == "no run since 09:25 ET (grace 120 min)"


def test_alerts_late_and_failed_runs_are_warnings(desk, monkeypatch):
    _late_row(monkeypatch)
    alerts = svc.build(NOW)["alerts"]
    warnings = [(a["title"].split(":")[-1].strip(), a["link"]) for a in alerts if a["level"] == "warning"]
    assert sorted(warnings) == [("failed", "/strategies/rsi2"), ("late", "")]
    assert not [a for a in alerts if a["level"] != "warning"]


def test_w6_the_late_run_alerts_detail_carries_the_same_grace_wording_as_the_run_entry(desk, monkeypatch):
    _late_row(monkeypatch)
    doc = svc.build(NOW)
    run_detail = next(r["detail"] for r in doc["runs"] if r["status"] == "late")
    alert_detail = next(a["detail"] for a in doc["alerts"] if a["level"] == "warning" and "late" in a["title"])
    assert run_detail == "no run since 09:25 ET (grace 120 min)"
    assert alert_detail == run_detail


def test_alerts_a_latched_halt_and_a_kill_file_are_serious(desk):
    state = desk["autopilot"] / "state" / "2026-03-18.json"
    state.write_text(json.dumps({"day": "2026-03-18", "halt_tripped": True}), encoding="utf-8")
    (desk["autopilot"] / "KILL").write_text("", encoding="utf-8")
    serious = [a for a in svc.build(NOW)["alerts"] if a["level"] == "serious"]
    assert [(a["title"], a["link"]) for a in serious] == [
        ("The autopilot's daily halt is latched", "/strategies/rsi2"),
        ("A kill file is present: autopilot/KILL", "/strategies/rsi2")]


def test_a_broken_runs_block_is_a_note_and_the_books_still_serve(desk, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(svc.routine, "status_for", boom)
    doc = svc.build(NOW)
    assert ("Left out of the feed: the runs", "RuntimeError") in _notes(doc)
    assert doc["runs"] == [] and len(doc["books"]) == 4
    assert all(b["next_run"] is None for b in doc["books"]) and doc["strategies"] and doc["trades"]


# ---------------------------------------------------------------- backtests (the bench ledger -> kestrel Backtest)

def _run_row(*, hash_, window, at, verdict="pass", name=None, family="rsi2_ext", n=30, expectancy_pct=1.1,
             t_stat=2.0, base_calmar=0.8, stacked_calmar=1.2, failed=None, type_="run"):
    return {"type": type_, "run_id": f"{at[:19].replace('-', '').replace(':', '')}-{hash_[:8]}", "at": at,
            "name": name if name is not None else f"{family}.{hash_[:4]}", "family": family, "hash": hash_,
            "window": window, "data_class": "daily", "instrument_class": "equity", "verdict": verdict,
            "failed": failed if failed is not None else [], "n": n, "expectancy_pct": expectancy_pct,
            "t_stat": t_stat, "base_calmar": base_calmar, "stacked_calmar": stacked_calmar, "calmar_rel": None,
            "delta_mdd_pts": None, "corr": None, "deployed_calmar": None, "deployed_rel": None, "overlap": None,
            "git_sha": "abc1234", "confirm_end": "2026-06-30", "note": ""}


def _write_runs(desk, rows):
    path = desk["data"] / "bench" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_backtests_mapping(desk):
    row = _run_row(hash_="h1abcd12", window="develop", at="2026-03-01T09:00:00-05:00", verdict="pass",
                   family="rsi2_ext", n=42, expectancy_pct=1.25, t_stat=2.1, base_calmar=0.9, stacked_calmar=1.4)
    _write_runs(desk, [row])
    doc = svc.build(NOW)
    assert doc["backtests"] == [{"id": "h1abcd12:develop", "name": row["name"], "family": "rsi2_ext",
                                "window": "develop", "verdict": "pass", "at": "2026-03-01T09:00:00-05:00",
                                "strategy_id": None, "trades": 42, "avg_trade_pct": 1.25, "t_stat": 2.1,
                                "calmar": 1.4, "max_drawdown_pct": None, "note": ""}]


def test_backtests_calmar_falls_back_to_base_when_no_stacked_calmar(desk):
    row = _run_row(hash_="h2abcd12", window="develop", at="2026-03-01T09:00:00-05:00", base_calmar=0.75,
                   stacked_calmar=None)
    _write_runs(desk, [row])
    doc = svc.build(NOW)
    assert doc["backtests"][0]["calmar"] == 0.75


def test_backtests_name_falls_back_to_hash_when_blank(desk):
    row = _run_row(hash_="h3abcd12", window="develop", at="2026-03-01T09:00:00-05:00", name="")
    _write_runs(desk, [row])
    doc = svc.build(NOW)
    assert doc["backtests"][0]["name"] == "h3abcd12"


def test_backtests_verdict_missing_or_unknown_reads_as_pending(desk):
    rows = [_run_row(hash_="hpend0001", window="develop", at="2026-03-01T09:00:00-05:00", verdict="error",
                     family="fam_pend_a"),
           _run_row(hash_="hpend0002", window="develop", at="2026-03-02T09:00:00-05:00", verdict=None,
                    family="fam_pend_b")]
    _write_runs(desk, rows)
    doc = svc.build(NOW)
    assert {b["id"]: b["verdict"] for b in doc["backtests"]} == {"hpend0001:develop": "pending",
                                                                 "hpend0002:develop": "pending"}


def test_backtests_note_joins_the_failed_reasons(desk):
    row = _run_row(hash_="hfail0001", window="confirm", at="2026-03-01T09:00:00-05:00", verdict="fail",
                   failed=["overlap", "n_min"])
    _write_runs(desk, [row])
    doc = svc.build(NOW)
    assert doc["backtests"][0]["note"] == "overlap, n_min"


def test_backtests_keeps_the_newest_row_per_hash_and_window(desk):
    older = _run_row(hash_="hnew00001", window="develop", at="2026-03-01T09:00:00-05:00", verdict="fail")
    newer = _run_row(hash_="hnew00001", window="develop", at="2026-03-05T09:00:00-05:00", verdict="pass")
    _write_runs(desk, [older, newer])
    doc = svc.build(NOW)
    assert [b["verdict"] for b in doc["backtests"] if b["id"] == "hnew00001:develop"] == ["pass"]


def test_backtests_selection_keeps_every_pass_and_each_family_windows_newest(desk):
    rows = [
        _run_row(hash_="hpass0001", window="develop", at="2026-01-01T09:00:00-05:00", verdict="pass", family="fam_a"),
        _run_row(hash_="hfail0001", window="develop", at="2026-01-05T09:00:00-05:00", verdict="fail", family="fam_a"),
        _run_row(hash_="hfail0002", window="develop", at="2026-01-10T09:00:00-05:00", verdict="fail", family="fam_a"),
        _run_row(hash_="hother001", window="develop", at="2026-01-02T09:00:00-05:00", verdict="fail", family="fam_b"),
    ]
    _write_runs(desk, rows)
    doc = svc.build(NOW)
    # the pass (hpass0001), fam_a's newest fail (hfail0002, not the older hfail0001), and fam_b's only row
    assert {b["id"] for b in doc["backtests"]} == {"hpass0001:develop", "hfail0002:develop", "hother001:develop"}


def test_backtests_cap_at_the_newest_600_overall(desk):
    rows = [_run_row(hash_=f"hcap{i:04d}", window="develop",
                     at=f"{(date(2020, 1, 1) + timedelta(days=i)).isoformat()}T09:00:00-05:00",
                     verdict="fail", family=f"fam{i}")
           for i in range(650)]
    _write_runs(desk, rows)
    doc = svc.build(NOW)
    assert len(doc["backtests"]) == 600
    kept_days = sorted(b["at"][:10] for b in doc["backtests"])
    want_days = sorted((date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(50, 650))
    assert kept_days == want_days


def test_backtests_a_torn_line_is_skipped(desk):
    good = _run_row(hash_="hgood0001", window="develop", at="2026-03-01T09:00:00-05:00", verdict="pass")
    path = desk["data"] / "bench" / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(good) + "\n" + '{"type": "run", "hash": "torn\n', encoding="utf-8")
    doc = svc.build(NOW)
    assert [b["id"] for b in doc["backtests"]] == ["hgood0001:develop"]


def test_backtests_only_type_run_rows_count(desk):
    rows = [_run_row(hash_="hstage001", window="develop", at="2026-03-01T09:00:00-05:00", type_="stage"),
           _run_row(hash_="hpaperrun", window="develop", at="2026-03-01T09:00:00-05:00", type_="paper_run")]
    _write_runs(desk, rows)
    doc = svc.build(NOW)
    assert doc["backtests"] == []


def test_backtests_a_row_without_a_parseable_at_is_skipped(desk):
    missing_at = _run_row(hash_="hbadat001", window="develop", at="2026-03-01T09:00:00-05:00", verdict="pass")
    del missing_at["at"]
    unparseable = _run_row(hash_="hbadat002", window="develop", at="not-a-date", verdict="pass")
    good = _run_row(hash_="hbadat003", window="develop", at="2026-03-01T09:00:00-05:00", verdict="pass")
    _write_runs(desk, [missing_at, unparseable, good])
    doc = svc.build(NOW)
    assert [b["id"] for b in doc["backtests"]] == ["hbadat003:develop"]


def test_backtests_a_naive_at_with_no_offset_is_skipped(desk):
    """kestrel's Backtest.at is an AwareDatetime; an `at` that PARSES fine (datetime.fromisoformat accepts a naive
    stamp) but carries no UTC offset is still not usable -- it must be skipped like an unparseable one, never kept
    with a fabricated offset."""
    naive = _run_row(hash_="hnaive001", window="develop", at="2026-03-01T09:00:00", verdict="pass")
    aware = _run_row(hash_="hnaive002", window="develop", at="2026-03-01T09:00:00-05:00", verdict="pass")
    _write_runs(desk, [naive, aware])
    doc = svc.build(NOW)
    assert [b["id"] for b in doc["backtests"]] == ["hnaive002:develop"]


def test_m1_an_over_large_avg_trade_pct_blanks_only_that_field(desk):
    """An out-of-range OPTIONAL number blanks just that field in `_backtest` -- the row still shows (kestrel's
    avg_trade_pct/t_stat/calmar/max_drawdown_pct/trades are all `X | None`), rather than the whole backtest being
    dropped by the later M1+M2 pass (2026-09-27 kestrel feed export follow-up: a real ledger row with one
    out-of-range optional number was producing a permanent "backtests: 1 item dropped" note)."""
    row = _run_row(hash_="hbig000001", window="develop", at="2026-03-01T09:00:00-05:00", expectancy_pct=1e16)
    _write_runs(desk, [row])
    doc = svc.build(NOW)
    json.dumps(doc, allow_nan=False)
    assert [b["avg_trade_pct"] for b in doc["backtests"] if b["id"] == "hbig000001:develop"] == [None]
    assert not [a for a in doc["alerts"] if a["title"].startswith("backtests:")]


def test_backtests_a_non_finite_or_out_of_range_optional_number_blanks_only_that_field(desk):
    """TDD for the same follow-up, at the unit level the review called out by name: t_stat = inf and
    calmar = 1e300 (over MAX_MAGNITUDE) each survive with just that field None, on two separate rows -- neither
    row is dropped and no note alert appears."""
    inf_row = _run_row(hash_="hinf00001", window="develop", at="2026-03-01T09:00:00-05:00", family="fam_inf",
                       t_stat=float("inf"))
    big_row = _run_row(hash_="hbig00002", window="develop", at="2026-03-01T09:00:00-05:00", family="fam_big",
                       base_calmar=1e300, stacked_calmar=None)
    _write_runs(desk, [inf_row, big_row])
    doc = svc.build(NOW)
    json.dumps(doc, allow_nan=False)
    by_id = {b["id"]: b for b in doc["backtests"]}
    assert set(by_id) == {"hinf00001:develop", "hbig00002:develop"}
    assert by_id["hinf00001:develop"]["t_stat"] is None
    assert by_id["hbig00002:develop"]["calmar"] is None
    assert not [a for a in doc["alerts"] if a["title"].startswith("backtests:")]


def test_a_broken_backtests_block_is_a_note_and_the_rest_still_serves(desk, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(svc, "_backtests", boom)
    doc = svc.build(NOW)
    assert ("Left out of the feed: the backtests", "RuntimeError") in _notes(doc)
    assert doc["backtests"] == [] and len(doc["books"]) == 4


# ---------------------------------------------------------------- runs and alerts

def test_a_runs_row_with_an_unknown_key_no_timestamp_or_an_odd_result_is_harmless(desk, monkeypatch):
    _late_row(monkeypatch, "bench_feeder")   # a live keyed row so its "dry" row reads done
    runs = desk["activity"] / "runs.jsonl"
    runs.write_text(runs.read_text(encoding="utf-8") + "".join(json.dumps(r) + "\n" for r in [
        {"key": "bench_feeder", "ts": "2026-03-18T08:00:00-04:00", "result": "dry", "summary": "rehearsal",
         "mystery": {"nested": [1, 2]}},
        {"key": "autopilot", "result": "error", "summary": "no stamp"},
        {"key": "not_a_routine_key", "ts": "2026-03-18T09:00:00-04:00", "result": "error"}]), encoding="utf-8")
    doc = svc.build(NOW)
    last = {r["book_id"]: r for r in doc["runs"] if r["status"] in ("done", "failed") and r["book_id"]}
    assert last["rsi2-real"]["time"] == "2026-03-18T09:31:40-04:00" and last["rsi2-real"]["status"] == "done"
    feeder = [r for r in doc["runs"] if r["time"] == "2026-03-18T08:00:00-04:00"]
    assert [r["status"] for r in feeder] == ["done"]
    assert not [a for a in doc["alerts"] if "not_a_routine_key" in a["title"] + a["detail"]]


# ---------------------------------------------------------------- 2026-09-29: RSI2-only (emit running tracks only)

def test_only_running_books_and_their_strategies_are_emitted(live_desk):
    doc = svc.build(NOW)
    books = [b["id"] for b in doc["books"]]
    assert books == [s.id for s in svc.BOOKS if svc._status(s) == "running"]
    assert "rsi2-real" in books and not any(b.endswith("-paper") or b == "day-orb-real" for b in books)
    assert all(b["status"] == "running" for b in doc["books"])
    assert [s["id"] for s in doc["strategies"]] == [sid for sid, _, _ in svc.STRATEGIES
                                                    if sid in {b["strategy_id"] for b in doc["books"]}]
    kept = set(books)
    for block in ("positions", "trades", "trade_charts"):
        assert {i["book_id"] for i in doc[block]} <= kept
    assert {h["id"] for h in doc["book_history"]} <= kept


def test_re_enabling_a_track_brings_its_book_back(live_desk, monkeypatch):
    real = svc._status
    monkeypatch.setattr(svc, "_status", lambda spec: "running" if spec.id == "day-orb-real" else real(spec))
    doc = svc.build(NOW)
    assert "day-orb-real" in [b["id"] for b in doc["books"]] and "day-orb" in [s["id"] for s in doc["strategies"]]


def test_bench_backtests_without_an_emitted_strategy_are_dropped(live_desk):
    _write_runs(live_desk, [_run_row(hash_="h1abcd12", window="develop", at="2026-03-01T09:00:00-05:00",
                                     verdict="pass")])
    assert not [b for b in svc.build(NOW)["backtests"] if not b["id"].startswith("review:")]
    assert svc._backtest_emitted({"strategy_id": "rsi2"}, {"rsi2"})
    assert not svc._backtest_emitted({"strategy_id": None}, {"rsi2"})


@pytest.mark.parametrize("env,want", [
    ({"WEBULL_RSI2_REAL_SLOT_DIVISOR": "6", "WEBULL_RSI2_REAL_DOLLARS": "500", "WEBULL_AUTOPILOT_MAX_NOTIONAL": "525"},
     "Sizing: net liq ÷ 6 (S6), capped at $500/lot"),
    ({"WEBULL_RSI2_REAL_SLOT_DIVISOR": "6", "WEBULL_AUTOPILOT_MAX_NOTIONAL": "525"},
     "Sizing: net liq ÷ 6 (S6), capped at $525/lot"),
    ({"WEBULL_RSI2_REAL_DOLLARS": "400"}, "Sizing: fixed $400 per lot"),
    ({}, "Sizing: fixed — settled cash per lot (no per-lot dollars set)"),
])
def test_rsi2_sizing_basis_comes_from_the_env(live_desk, monkeypatch, env, want):
    for k in ("WEBULL_RSI2_REAL_SLOT_DIVISOR", "WEBULL_RSI2_REAL_DOLLARS", "WEBULL_AUTOPILOT_MAX_NOTIONAL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert svc._rsi2_sizing() == want
    rsi2 = _by(svc.build(NOW)["strategies"])["rsi2"]
    assert rsi2["sizing"] == want


# ---------------------------------------------------------------- 2026-09-29: the live RSI2 system's own backtests

def test_review_backtests_are_an_explicit_file_to_strategy_mapping():
    assert {p.name: sid for p, sid, _ in svc.REVIEW_BACKTESTS} == {
        "2026-09-07-rsi2-backtest-tiingo.json": "rsi2", "2026-09-10-sizing-study-confirm.json": "rsi2"}
    for path, _, _ in svc.REVIEW_BACKTESTS:
        assert path.is_file(), path


def test_review_backtests_from_the_committed_reviews():
    rows = {b["id"]: b for b in svc._review_backtests(svc._Files(NOW))}
    bt = rows["review:rsi2-20yr"]
    assert (bt["strategy_id"], bt["verdict"], bt["trades"], bt["window"]) == ("rsi2", "pass", 3221, "2006–2026")
    assert round(bt["avg_trade_pct"], 2) == 0.84 and round(bt["max_drawdown_pct"], 2) == -9.54
    s6 = rows["review:s6-sizing-confirm"]
    assert (s6["strategy_id"], s6["verdict"], s6["trades"], s6["window"]) == ("rsi2", "pass", 1021, "confirm 2021–2024")
    assert s6["calmar"] == 2.4201 and s6["max_drawdown_pct"] == -11.667 and "28.23" in s6["note"]


def test_live_feed_emits_the_rsi2_review_backtests_and_no_bench_rows(live_desk):
    _write_runs(live_desk, [_run_row(hash_="h1abcd12", window="develop", at="2026-03-01T09:00:00-05:00",
                                     verdict="pass")])
    got = svc.build(NOW)["backtests"]
    assert [b["id"] for b in got] == ["review:s6-sizing-confirm", "review:rsi2-20yr"]   # newest first
    assert all(b["strategy_id"] == "rsi2" for b in got)
