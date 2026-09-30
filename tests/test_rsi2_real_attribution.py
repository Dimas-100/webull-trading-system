"""Attribution of REAL RSI2 fills: every lot the real runner adopts gets one action row whose
`ref` is the journal fill id, so the Overview shows it as a system entry rather than "manual
fill", the Activity feed / evening note carry the why, and the scorecard has a bucket of its
own (`rsi2-real`, never the paper `rsi2-equity` the proof bar reads). Pure matching, fail-safe —
never a wrong attribution; idempotent by the lot's decision id."""
from webull_api.journal.schema import Fill
from webull_web import rsi2_real_attribution as attr
from webull_web import rsi2_real_store


def _fill(fid="f1", symbol="GE", side="BUY", source="real", at="2026-09-16T13:31:05.000Z",
          qty=1.0, price=310.13, account="EQ1"):
    return Fill(id=fid, source=source, account_id=account, symbol=symbol, side=side,
                quantity=qty, price=price, filled_at_iso=at, order_type="LIMIT")


def _lot(symbol="GE", entry_date="2026-09-16", shares=1.0, decision_id="d1"):
    return {"symbol": symbol, "shares": shares, "entry_price": 310.13,
            "entry_date": entry_date, "decision_id": decision_id}


def _row(fill_id="f1", decision_id="d1"):
    """An attribution row already on disk, as action_log.load() returns it."""
    return {"id": attr.row_id(fill_id), "ref": fill_id, "source": attr.SOURCE,
            "decision_id": decision_id, "symbol": "GE", "side": "BUY", "kind": "trade"}


def test_an_owned_lot_gets_one_row_referencing_its_journal_fill():
    rows, notes = attr.rows_for([_lot()], [_fill()], existing_rows=[])
    assert len(rows) == 1
    r = rows[0]
    assert r["ref"] == "f1" and r["id"] == "rsi2-real:f1"
    assert r["source"] == "runner:rsi2_real" and r["sleeve"] == "real"
    assert r["kind"] == "trade" and r["side"] == "BUY"
    assert r["symbol"] == "GE" and r["qty"] == 1.0
    assert r["decision_id"] == "d1" and r["account_id"] == "EQ1"
    assert "d1" in r["why"]
    assert notes == ["attributed GE -> fill f1"]


def test_the_row_is_stamped_at_the_fill_time_in_eastern_time():
    rows, _ = attr.rows_for([_lot()], [_fill(at="2026-09-16T13:31:05.000Z")], existing_rows=[])
    assert rows[0]["ts"] == "2026-09-16T09:31:05.000000-04:00"


def test_a_naive_fill_stamp_is_read_as_eastern_time():
    rows, _ = attr.rows_for([_lot()], [_fill(at="2026-09-16T09:31:05")], existing_rows=[])
    assert rows[0]["ts"] == "2026-09-16T09:31:05.000000-04:00"


def test_paper_fills_and_sells_are_never_matched():
    fills = [_fill(fid="p1", source="paper"), _fill(fid="s1", side="SELL")]
    rows, notes = attr.rows_for([_lot()], fills, existing_rows=[])
    assert rows == [] and notes == ["unattributed GE: no matching real BUY fill"]


def test_an_already_attributed_lot_is_skipped_silently():
    rows, notes = attr.rows_for([_lot()], [_fill()], existing_rows=[_row("f1", "d1")])
    assert rows == [] and notes == []


def test_a_lot_without_a_decision_id_falls_back_to_the_fill_row_for_idempotence():
    lot = _lot(decision_id=None)
    rows, notes = attr.rows_for([lot], [_fill()], existing_rows=[_row("f1", None)])
    assert rows == [] and notes == []


def test_two_candidate_fills_is_ambiguous_and_attributes_nothing():
    fills = [_fill(fid="f1"), _fill(fid="f2", at="2026-09-15T13:31:05.000Z")]
    rows, notes = attr.rows_for([_lot()], fills, existing_rows=[])
    assert rows == [] and notes == ["unattributed GE: 2 candidate fills (ambiguous)"]


def test_a_re_entry_in_the_same_name_matches_only_the_unclaimed_fill():
    """Reviewer finding 2026-09-18: GE fills 09-16 (lot d1, attributed), exits, re-signals and
    fills 09-19 (lot d2). Both fills sit inside lot d2's window; the one an earlier lot already
    claimed must not count, or the second lot stays 'ambiguous' forever."""
    fills = [_fill(fid="f1", at="2026-09-16T13:31:05.000Z"),
             _fill(fid="f2", at="2026-09-19T13:31:05.000Z")]
    lots = [_lot(entry_date="2026-09-16", decision_id="d1"),
            _lot(entry_date="2026-09-19", decision_id="d2")]
    rows, notes = attr.rows_for(lots, fills, existing_rows=[_row("f1", "d1")])
    assert [(r["ref"], r["decision_id"]) for r in rows] == [("f2", "d2")]
    assert notes == ["attributed GE -> fill f2"]


def test_fills_of_another_account_are_ignored_when_the_account_is_known():
    """The journal holds every synced account; a same-day BUY in another account must not make
    the sleeve's lot ambiguous, and must never be the ref."""
    fills = [_fill(fid="f1", account="EQ1"), _fill(fid="f9", account="OTHER")]
    rows, _ = attr.rows_for([_lot()], fills, existing_rows=[], account_id="EQ1")
    assert [r["ref"] for r in rows] == ["f1"]
    rows, notes = attr.rows_for([_lot()], fills, existing_rows=[])      # account unknown
    assert rows == [] and notes[0].endswith("(ambiguous)")


def test_a_fill_adopted_a_day_late_still_matches():
    """The 17:30 adoption run can miss a night; the lot's entry_date is then the day after
    the fill. Anything inside the pending window counts."""
    rows, _ = attr.rows_for([_lot(entry_date="2026-09-17")], [_fill()], existing_rows=[])
    assert [r["ref"] for r in rows] == ["f1"]


def test_the_window_is_the_ledgers_pending_ttl():
    assert attr.WINDOW_DAYS == rsi2_real_store._PENDING_TTL_DAYS


def test_a_fill_older_than_the_pending_window_is_ignored():
    rows, notes = attr.rows_for([_lot(entry_date="2026-09-17")],
                                [_fill(at="2026-09-01T13:31:05.000Z")], existing_rows=[])
    assert rows == [] and notes[0].startswith("unattributed GE")


def test_a_fill_after_the_entry_date_is_not_the_lots_entry():
    rows, _ = attr.rows_for([_lot(entry_date="2026-09-16")],
                            [_fill(at="2026-09-17T13:31:05.000Z")], existing_rows=[])
    assert rows == []


def test_an_unparseable_entry_date_attributes_nothing():
    rows, notes = attr.rows_for([_lot(entry_date="soon")], [_fill()], existing_rows=[])
    assert rows == [] and notes[0].startswith("unattributed GE: unparseable entry_date")
