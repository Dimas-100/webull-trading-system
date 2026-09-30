"""Tests the guidance script's PURE formatting on stubbed price dicts only -- never the Tiingo
store, never netliq_store, never the network (spec 2026-09-11 pool-shadow design §3)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import sizing_guidance as G  # noqa: E402


SWING = {"AAPL": 234.07, "COST": 902.0, "CHEAP": 20.0}
ETF = {"SPY": 507.0, "XLU": 42.62}


def test_format_affordable_line_lists_symbols_and_shares():
    line = G.format_affordable_line(SWING, 1_600.0, 6, "swing")
    assert "slot $266.67" in line
    assert "CHEAP 13sh" in line       # floor(266.67/20)=13
    assert "AAPL 1sh" in line         # floor(266.67/234.07)=1
    assert "COST" not in line         # 902 > 266.67, unaffordable -> excluded
    assert "3/3" not in line          # only 2 of 3 afford a share
    assert "2/3" in line


def test_format_affordable_line_none_when_nothing_affords():
    line = G.format_affordable_line({"EXPENSIVE": 100_000.0}, 100.0, 6, "swing")
    assert "0/1" in line
    assert line.rstrip().endswith("none")


def test_format_grid_shape_one_header_plus_one_row_per_equity():
    lines = G.format_grid(SWING, "swing", equities=(1_600.0, 10_000.0), divisors=(6, 4))
    assert lines[0].startswith("  swing (of 3):")
    assert len(lines) == 1 + 2                       # header + 2 equity rows
    assert "/6" in lines[1] and "/4" in lines[1]
    assert "$1,600.00" in lines[1] and "$10,000.00" in lines[2]


def test_format_report_is_pure_and_deterministic_on_stubbed_prices():
    out = G.format_report(SWING, ETF, 1_600.0, "given via --equity")
    assert "equity $1,600.00 (given via --equity)" in out
    assert "Affordable today at equity/6 and equity/4" in out
    assert "Affordability grid" in out
    assert "playbook/sizing-rule.md" in out
    assert "WEBULL_AUTOPILOT_MAX_NOTIONAL" in out
    # deterministic: same inputs, same output, no I/O performed
    assert out == G.format_report(SWING, ETF, 1_600.0, "given via --equity")


def test_format_report_reflects_the_resolved_equity_source_label():
    out = G.format_report(SWING, ETF, 3_150.0, "latest real net liq (2026-09-10)")
    assert "equity $3,150.00 (latest real net liq (2026-09-10))" in out


def test_grid_default_equities_are_the_five_named_levels():
    assert G.GRID_EQUITIES == (1_600.0, 3_200.0, 5_000.0, 10_000.0, 25_000.0)
    assert G.DIVISORS == (6, 4)
