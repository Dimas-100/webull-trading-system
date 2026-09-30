from webull_api.swing.sizing import size_position


def test_stock_a_three_shares():  # entry 30, stop 28.50, BOOK 500 (plan worked example A)
    r = size_position(500, 30.0, 28.5)
    assert r["shares"] == 3 and r["ok"]
    assert abs(r["actual_risk_dollars"] - 4.5) < 1e-9
    assert abs(r["actual_risk_pct"] - 0.9) < 1e-6  # percent units


def test_stock_b_one_share():  # entry 80, stop 76 -> floor(5/4)=1 (example B)
    r = size_position(500, 80.0, 76.0)
    assert r["shares"] == 1 and r["ok"] and abs(r["actual_risk_pct"] - 0.8) < 1e-6


def test_stock_c_one_share_under_ceiling():  # entry 95, stop 86 -> 0 shares, 1 risks 1.8% (example C)
    r = size_position(500, 95.0, 86.0)
    assert r["shares"] == 1 and r["ok"] and abs(r["actual_risk_pct"] - 1.8) < 1e-6


def test_one_share_over_2pct_skips():  # entry 60, stop 47 -> 1 share risks $13 = 2.6% > 2%
    r = size_position(500, 60.0, 47.0)
    assert not r["ok"] and "2%" in r["reason"]


def test_cheap_tight_name_is_noise_skip():  # 1% wants 250sh but notional cap -> 40sh -> 0.16% noise
    r = size_position(500, 5.0, 4.98)
    assert not r["ok"] and "noise" in r["reason"].lower()


def test_invalid_stop_skips():
    assert not size_position(500, 30.0, 31.0)["ok"]
