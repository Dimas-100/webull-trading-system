"""NYSE full-day market holidays (ET dates). Moved here 2026-09-29 from webull_api/day_session/paper.py when
the day session was archived (tag archive/pre-rsi2-only-2026-09-29). Extend it each year."""
from __future__ import annotations

from datetime import date

NYSE_HOLIDAYS = frozenset({
    date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
})
