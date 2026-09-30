"""Earnings watch on held names: pure windowing/dedup in assess; held_earnings names an
unreadable book instead of silently dropping it."""
from webull_web import events_service as svc

TODAY = "2026-07-24"


def _fetch(table):
    """table: sym -> (date|None, checked)."""
    return lambda sym: table.get(sym, (None, True))


def test_hit_inside_window_with_days_count():
    out = svc.assess({"real": ["PLTR"]}, _fetch({"PLTR": ("2026-08-01", True)}), TODAY)
    assert out["hits"] == [{"symbol": "PLTR", "books": ["real"], "date": "2026-08-01", "days": 8}]


def test_window_boundaries_day0_and_day14_in_day15_out():
    table = {"A": ("2026-07-24", True), "B": ("2026-08-07", True), "C": ("2026-08-08", True)}
    out = svc.assess({"real": ["A", "B", "C"]}, _fetch(table), TODAY)
    assert [h["symbol"] for h in out["hits"]] == ["A", "B"]
    assert out["hits"][0]["days"] == 0 and out["hits"][1]["days"] == 14


def test_dedup_across_books_lists_all_books_once():
    out = svc.assess({"real": ["PLTR"], "rsi2": ["pltr"], "proven": ["PLTR"]},
                     _fetch({"PLTR": ("2026-07-30", True)}), TODAY)
    assert len(out["hits"]) == 1
    assert out["hits"][0]["books"] == ["real", "rsi2", "proven"]
    assert out["checked"] == 1


def test_unverified_counted_never_claimed_clear():
    out = svc.assess({"real": ["X", "Y"]}, _fetch({"X": (None, False), "Y": (None, True)}), TODAY)
    assert out["unverified"] == ["X"] and out["hits"] == []


def test_hits_sorted_by_days_then_symbol():
    table = {"ZZ": ("2026-07-26", True), "AA": ("2026-07-28", True), "BB": ("2026-07-26", True)}
    out = svc.assess({"real": ["ZZ", "AA", "BB"]}, _fetch(table), TODAY)
    assert [h["symbol"] for h in out["hits"]] == ["BB", "ZZ", "AA"]


def test_unparseable_date_lands_in_unverified():
    out = svc.assess({"real": ["X"]}, _fetch({"X": ("not-a-date", True)}), TODAY)
    assert out["unverified"] == ["X"] and out["hits"] == []


def test_held_earnings_names_a_broken_book(monkeypatch):
    def boom():
        raise RuntimeError("broker down")
    monkeypatch.setattr(svc, "_BOOK_SOURCES",
                        [("real", boom), ("rsi2", lambda: ["FBTC"])])
    monkeypatch.setattr("webull_web.news.get_next_earnings_checked",
                        lambda sym, ahead_days=40: (None, True))
    out = svc.held_earnings(TODAY)
    assert out["books_unavailable"] == ["real"]
    assert out["checked"] == 1 and out["hits"] == []
