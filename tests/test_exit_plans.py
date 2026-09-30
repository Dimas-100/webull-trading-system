# tests/test_exit_plans.py
"""Exit-plan resolution per book: rsi2 rule > proven rule > backstop; broker-down degrades."""
import inspect

from webull_web import exit_plans_service as svc


def test_backstop_defaults_present_even_with_empty_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    monkeypatch.setattr(svc, "resting_stops", lambda: {})
    plans = svc.build()
    assert "stop −8%" in plans["paper_equity"]["*"] and "+20%" in plans["paper_equity"]["*"]
    assert "trend-break" in plans["paper_equity"]["*"]
    assert "+50%" in plans["paper_options"]["*"] and "7 DTE" in plans["paper_options"]["*"]
    assert "backstop" in plans["real"]["*"]


def test_rsi2_owned_lot_gets_the_rsi2_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    from webull_web import rsi2_store
    state = rsi2_store.load()
    rsi2_store.record_entry(state, symbol="NVDA", shares=3, entry_price=200.0,
                            entry_date="2026-07-08", paper_order_id="x")
    rsi2_store.save(state, "2026-07-08T21:00:00")
    monkeypatch.setattr(svc, "resting_stops", lambda: {})
    plans = svc.build()
    assert "RSI(2) ≥ 70" in plans["paper_equity"]["NVDA"]
    assert "day" in plans["paper_equity"]["NVDA"]  # holding-day counter present


def test_resting_stop_renders_and_broker_down_degrades(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    monkeypatch.setattr(svc, "resting_stops", lambda: {"FBTC": "51.04"})
    plans = svc.build()
    assert plans["real"]["FBTC"] == "stop $51.04 resting (GTC)"
    assert plans["real_stops"] == {"FBTC": 51.04}        # structured twin of the sentence (cockpit math)

    def boom():
        raise RuntimeError("broker down")
    monkeypatch.setattr(svc, "resting_stops", boom)
    plans = svc.build()
    assert "FBTC" not in plans["real"] and "backstop" in plans["real"]["*"]  # never invented
    assert plans["real_stops"] == {}


def test_real_stops_skips_an_unparseable_price_but_keeps_the_sentence(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    monkeypatch.setattr(svc, "resting_stops", lambda: {"FBTC": "51.04", "XYZ": "n/a"})
    plans = svc.build()
    assert plans["real_stops"] == {"FBTC": 51.04}
    assert plans["real"]["XYZ"] == "stop $n/a resting (GTC)"


def test_resting_stops_rides_out_broker_throttling(monkeypatch):
    # The note runs at the tail of the suite where the API quota is often spent; the open-orders
    # read must retry through a 429 instead of leaving Protection blind every night.
    from webull_api import portfolio, trading
    calls = []

    def throttled_orders(aid):
        calls.append(aid)
        if len(calls) < 3:
            raise Exception("HTTP Status: 429, Code: TOO_MANY_REQUESTS, Msg: Too many requests")
        return [{"symbol": "FBTC", "side": "SELL", "order_type": "STOP", "stop_price": "51.04"}]

    monkeypatch.setattr(portfolio, "list_accounts", lambda: [{"account_id": "A1"}])
    monkeypatch.setattr(trading, "get_open_orders", throttled_orders)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert svc.resting_stops() == {"FBTC": "51.04"}


def test_order_rows_unwraps_the_combo_envelope():
    """Webull returns open orders as a LIST of combo wrappers with the real order nested under
    `orders`. Returning that list unchanged handed _stops_from_rows the wrappers — which carry no
    side/order_type — so resting_stops() returned {} for every account and the nightly note said
    'no resting stops' no matter what rested. Verbatim shape from the real account 2026-08-15."""
    payload = [{
        "client_order_id": "a0fe202fc60a4e49a43d50fabca47098",
        "combo_type": "NORMAL",
        "combo_order_id": "I8I4M2330LPFCCF4VC1B07IISB",
        "orders": [{"symbol": "AAPL", "side": "SELL", "status": "SUBMITTED",
                    "order_type": "STOP_LOSS", "instrument_type": "EQUITY",
                    "total_quantity": "1", "filled_quantity": "0",
                    "time_in_force": "GTC", "stop_price": "28.00"}],
    }]
    assert svc._stops_from_rows(svc._order_rows(payload)) == {"AAPL": "28.00"}


def test_order_rows_still_handles_the_flat_and_dict_shapes():
    flat = [{"symbol": "FBTC", "side": "SELL", "order_type": "STOP", "stop_price": "51.04"}]
    assert svc._order_rows(flat) == flat
    assert svc._order_rows({"data": flat}) == flat
    assert svc._order_rows({"orders": flat}) == flat
    assert svc._order_rows(None) == []


def test_stop_row_parser_tolerates_broker_shapes():
    rows = [
        {"symbol": "FBTC", "side": "SELL", "order_type": "STOP_LOSS", "stop_price": "51.04"},
        {"ticker": {"symbol": "AAPL"}, "side": "BUY", "order_type": "STOP_LOSS", "stop_price": "1"},   # BUY -> not protective
        {"symbol": "MSFT", "side": "SELL", "order_type": "LIMIT", "limit_price": "500"},              # not a stop
        {"symbol": "VZ", "side": "SELL", "order_type": "STOP_LOSS_LIMIT", "stopPrice": "40.10"},      # alt key
        "garbage",
    ]
    assert svc._stops_from_rows(rows) == {"FBTC": "51.04", "VZ": "40.10"}


def test_proven_owned_symbol_gets_the_strategy_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path))  # hermetic: a live MSFT rsi2 lot must not overwrite the proven line
    from webull_web import lab_service, proven_store
    proven_store.save({"MSFT": "trial-42"})
    fake_records = [{
        "gate_b": {"trial_id": "trial-42"},
        "strategy": {"name": "rsi2-variant", "stop_loss_pct": 6.0,
                     "take_profit_pct": 15.0, "exit": {"kind": "indicator"}},
    }]
    monkeypatch.setattr(lab_service, "proven_view", lambda: fake_records)
    monkeypatch.setattr(svc, "resting_stops", lambda: {})
    plans = svc.build()
    assert plans["paper_equity"]["MSFT"] == "proven[rsi2-variant]: stop −6% · target +15% · exit signal"


def test_exit_plans_surface_is_read_only():
    src = inspect.getsource(svc)
    for forbidden in ("trading.place", "place_order", "confirm=True",
                      ".save_account", "rsi2_store.save", "proven_store.save"):
        assert forbidden not in src, f"{svc.__name__} must not contain {forbidden!r}"
