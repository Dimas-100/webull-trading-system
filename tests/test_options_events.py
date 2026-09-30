import datetime

import pytest

from webull_api import options_events as ev


def test_historical_earnings_moves():
    bars = [{"date": "2026-01-01", "close": 100}, {"date": "2026-01-02", "close": 100},
            {"date": "2026-01-03", "close": 110},   # earnings 01-03 -> +10% vs prior 100
            {"date": "2026-04-01", "close": 120}, {"date": "2026-04-02", "close": 108}]  # 04-02 -> -10%
    moves = ev.historical_earnings_moves(bars, ["2026-01-03", "2026-04-02"])
    assert moves == pytest.approx([10.0, 10.0], abs=1e-6)


def test_historical_earnings_moves_amc_uses_next_bar():
    # An amc reporter announces AFTER the close of the earnings date: the on-date bar is
    # pre-announcement (would read 0% here), so the reaction is the NEXT trading bar.
    bars = [{"date": "2026-01-02", "close": 100}, {"date": "2026-01-05", "close": 100},
            {"date": "2026-01-06", "close": 110}]
    amc = ev.historical_earnings_moves(bars, [{"date": "2026-01-05", "hour": "amc"}])
    assert amc == pytest.approx([10.0], abs=1e-6)
    # bmo announces before that day's open: the on-date bar IS the reaction (prior behavior)
    bars2 = [{"date": "2026-01-02", "close": 100}, {"date": "2026-01-05", "close": 108}]
    bmo = ev.historical_earnings_moves(bars2, [{"date": "2026-01-05", "hour": "bmo"}])
    assert bmo == pytest.approx([8.0], abs=1e-6)
    # unknown hour keeps the on/after behavior, matching plain string dates
    unk = ev.historical_earnings_moves(bars2, [{"date": "2026-01-05", "hour": None}])
    assert unk == pytest.approx([8.0], abs=1e-6)
    assert ev.historical_earnings_moves(bars2, [{"hour": "amc"}]) == []   # dateless entry skipped


def test_earnings_move_verdict():
    cheap = ev.earnings_move(100, 4.0, [8.0, 9.0, 10.0])
    assert cheap["implied_pct"] == pytest.approx(4.0) and cheap["verdict"] == "cheap"
    exp = ev.earnings_move(100, 12.0, [8.0, 7.0, 6.0])
    assert exp["verdict"] == "expensive"
    fair = ev.earnings_move(100, 7.5, [7.0, 7.5, 8.0])
    assert fair["verdict"] == "fair"
    assert ev.earnings_move(100, None, [])["implied_pct"] is None


def test_earnings_move_for_degrades_without_earnings():
    r = ev.earnings_move_for("AAPL", today=datetime.date(2026, 4, 18),
                             next_earn_fn=lambda s: None, spot_fn=lambda s: 100.0,
                             exp_fn=lambda *a, **k: [], snapshot_fn=lambda c: [],
                             bars_fn=lambda s, **k: [], past_earn_fn=lambda s: [])
    assert r["next_earnings"] is None and r["implied_pct"] is None


def test_earnings_move_for_with_data():
    from webull_api.options_chain import build_occ
    exp = "2026-05-15"
    call = build_occ("AAPL", datetime.date.fromisoformat(exp), "C", 100)
    put = build_occ("AAPL", datetime.date.fromisoformat(exp), "P", 100)
    rows = [{"symbol": call, "bid": "4", "ask": "4.2"}, {"symbol": put, "bid": "3.8", "ask": "4.0"}]

    def snap(csv):
        table = {r["symbol"]: r for r in rows}
        return [table[s] for s in csv.split(",") if s in table]

    bars = [{"date": "2026-01-02", "close": 100}, {"date": "2026-01-05", "close": 108}]
    r = ev.earnings_move_for("AAPL", today=datetime.date(2026, 4, 18),
                             next_earn_fn=lambda s: "2026-05-10", spot_fn=lambda s: 100.0,
                             exp_fn=lambda *a, **k: [exp], snapshot_fn=snap,
                             bars_fn=lambda s, **k: bars, past_earn_fn=lambda s: ["2026-01-05"])
    assert r["expiration"] == exp
    assert r["implied_pct"] == pytest.approx(8.0, abs=1e-6)   # straddle (4.1+3.9) / spot 100
    assert r["hist_avg_pct"] == pytest.approx(8.0, abs=1e-6)


def test_earnings_move_for_no_bracketing_expiration_reports_unavailable():
    # Every listed expiration is BEFORE earnings: a pre-event straddle would exclude the
    # move entirely, so implied must be unavailable (None + reason), never the old
    # exps[-1] fallback mislabeled as the earnings-implied move.
    from webull_api.options_chain import build_occ
    exp = "2026-05-01"   # before earnings 2026-05-10
    call = build_occ("AAPL", datetime.date.fromisoformat(exp), "C", 100)
    put = build_occ("AAPL", datetime.date.fromisoformat(exp), "P", 100)
    rows = [{"symbol": call, "bid": "4", "ask": "4.2"}, {"symbol": put, "bid": "3.8", "ask": "4.0"}]

    def snap(csv):
        table = {r["symbol"]: r for r in rows}
        return [table[s] for s in csv.split(",") if s in table]

    r = ev.earnings_move_for("AAPL", today=datetime.date(2026, 4, 18),
                             next_earn_fn=lambda s: "2026-05-10", spot_fn=lambda s: 100.0,
                             exp_fn=lambda *a, **k: [exp], snapshot_fn=snap,
                             bars_fn=lambda s, **k: [], past_earn_fn=lambda s: [])
    assert r["expiration"] is None and r["implied_pct"] is None
    assert r["implied_reason"] == "no expiration on/after earnings"


def test_earnings_move_for_amc_hist_move_uses_next_bar():
    from webull_api.options_chain import build_occ
    exp = "2026-05-15"
    call = build_occ("AAPL", datetime.date.fromisoformat(exp), "C", 100)
    put = build_occ("AAPL", datetime.date.fromisoformat(exp), "P", 100)
    rows = [{"symbol": call, "bid": "4", "ask": "4.2"}, {"symbol": put, "bid": "3.8", "ask": "4.0"}]

    def snap(csv):
        table = {r["symbol"]: r for r in rows}
        return [table[s] for s in csv.split(",") if s in table]

    bars = [{"date": "2026-01-02", "close": 100}, {"date": "2026-01-05", "close": 100},
            {"date": "2026-01-06", "close": 110}]
    r = ev.earnings_move_for("AAPL", today=datetime.date(2026, 4, 18),
                             next_earn_fn=lambda s: "2026-05-10", spot_fn=lambda s: 100.0,
                             exp_fn=lambda *a, **k: [exp], snapshot_fn=snap,
                             bars_fn=lambda s, **k: bars,
                             past_earn_fn=lambda s: [{"date": "2026-01-05", "hour": "amc"}])
    # next-bar move (10%), not the 0% pre-announcement on-date bar
    assert r["hist_avg_pct"] == pytest.approx(10.0, abs=1e-6)


def test_past_earnings_dates_no_key(monkeypatch):
    from webull_web import news
    # Neutralize load_dotenv like the sibling test below: get_past_earnings_dates calls it
    # internally, so on a machine whose .env holds a real FINNHUB_API_KEY the delenv alone
    # is undone at call time and the test hits the live API.
    monkeypatch.setattr(news, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert news.get_past_earnings_dates("AAPL") == []


def test_past_earnings_dates_returns_date_and_hour(monkeypatch):
    from webull_web import news

    class _Res:
        status_code = 200

        def json(self):
            return {"earningsCalendar": [
                {"date": "2026-01-28", "hour": "amc"},
                {"date": "2025-10-30", "hour": "bmo"},
                {"date": "2025-10-30", "hour": ""},        # dup date, blank hour -> deduped
                {"date": "2099-01-01", "hour": "amc"},     # future -> dropped
            ]}

    monkeypatch.setattr(news, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(news.requests, "get", lambda *a, **k: _Res())
    out = news.get_past_earnings_dates("AAPL")
    assert out == [{"date": "2026-01-28", "hour": "amc"},
                   {"date": "2025-10-30", "hour": "bmo"}]
