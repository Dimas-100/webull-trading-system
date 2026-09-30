"""autopilot.phone.compose: the after-run phone note -- silent unless a real order was placed
or a stage errored; short, plain text; priority/tags follow the error state."""
from datetime import datetime

from webull_api.autopilot import phone

NOW = datetime(2026, 9, 22, 9, 33)


def test_quiet_run_composes_nothing():
    assert phone.compose(placed=[], skipped=[{"symbol": "X"}] * 6, errors=[], now=NOW,
                         enabled=True, kill_active=False) is None


def test_placement_note_names_each_order_and_stays_default_priority():
    placed = [{"symbol": "AXP", "side": "BUY", "source": "entry", "qty": "1", "price": "286.25",
               "entry": 286.25, "stop": 263.35, "shares": 1},
              {"symbol": "GE", "side": "SELL", "source": "exit:rsi2_above", "qty": "2.0",
               "price": None}]
    note = phone.compose(placed=placed, skipped=[{}] * 8, errors=[], now=NOW,
                         enabled=True, kill_active=False)
    assert note["title"] == "Autopilot 09:33 - 2 placed"
    assert note["text"].splitlines() == [
        "- BUY 1 AXP @ 286.25 (entry)",
        "- SELL 2 GE (exit:rsi2_above)",
        "skipped 8 | enabled=True kill=False",
    ]
    assert note["priority"] == "default" and note["tags"] == "money_with_wings"
    assert len(note["text"].encode("utf-8")) < 500


def test_error_note_lists_stages_and_goes_high_priority():
    note = phone.compose(placed=[], skipped=[], errors=[{"stage": "exits", "error": "429 x" * 60}],
                         now=NOW, enabled=True, kill_active=True)
    assert note["title"] == "Autopilot 09:33 - 0 placed, 1 error(s)"
    lines = note["text"].splitlines()
    assert lines[0] == "ERRORS" and lines[1].startswith("- exits: 429 x")
    assert len(lines[1]) <= 170                                   # error text is clipped
    assert lines[-1] == "skipped 0 | enabled=True kill=True"
    assert note["priority"] == "high" and note["tags"] == "warning"


def test_falls_back_to_the_entry_plan_fields_when_qty_price_are_absent():
    note = phone.compose(placed=[{"symbol": "SOFI", "side": "BUY", "source": "entry",
                                  "entry": 16.2, "stop": 15.1, "shares": 3}],
                         skipped=[], errors=[], now=NOW, enabled=True, kill_active=False)
    assert note["text"].splitlines()[0] == "- BUY 3 SOFI @ 16.20 (entry)"
