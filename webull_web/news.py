"""Read-only company news via Finnhub (external; key from FINNHUB_API_KEY, server-side).

Webull has no news, so this is a separate provider integration living in the services layer
(webull_web), keeping webull_api Webull-pure.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from .fundamentals_data import finnhub_symbol

FINNHUB_URL = "https://finnhub.io/api/v1/company-news"
EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"


class NewsNotConfiguredError(RuntimeError):
    """FINNHUB_API_KEY is not set; news is unavailable."""


def get_company_news(symbol: str, days: int = 14) -> list[dict]:
    load_dotenv()
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        raise NewsNotConfiguredError("FINNHUB_API_KEY not set")
    to_d = datetime.now().date()
    from_d = to_d - timedelta(days=days)
    res = requests.get(
        FINNHUB_URL,
        params={"symbol": finnhub_symbol(symbol), "from": from_d.isoformat(),
                "to": to_d.isoformat(), "token": key},
        timeout=20,
    )
    if res.status_code != 200:
        raise RuntimeError(f"news error {res.status_code}")
    data = res.json()
    return data if isinstance(data, list) else []


def get_next_earnings_checked(symbol: str, ahead_days: int = 40) -> tuple[str | None, bool]:
    """(soonest upcoming earnings date, checked) via Finnhub. checked=False means the
    lookup did NOT happen (missing key / HTTP error / exception) — callers must not read
    (None, False) as "nothing scheduled". (None, True) IS verified-nothing-inside-window."""
    load_dotenv()
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        return None, False
    today = datetime.now().date()
    try:
        res = requests.get(
            EARNINGS_URL,
            params={"symbol": finnhub_symbol(symbol), "from": today.isoformat(),
                    "to": (today + timedelta(days=ahead_days)).isoformat(), "token": key},
            timeout=20,
        )
        if res.status_code != 200:
            return None, False
        cal = res.json().get("earningsCalendar", [])
        dates = sorted(c["date"] for c in cal if c.get("date"))
        return (dates[0] if dates else None), True
    except Exception:
        return None, False


def get_next_earnings_date(symbol: str, ahead_days: int = 40) -> str | None:
    """Compat wrapper: date-only view of get_next_earnings_checked. Best-effort None on
    any failure so the swing route degrades to 'earnings unverified'."""
    return get_next_earnings_checked(symbol, ahead_days)[0]


def get_past_earnings_dates(symbol: str, lookback_days: int = 540) -> list[dict]:
    """Past earnings as [{"date": ISO, "hour": "bmo"|"amc"|"dmh"|None}], most-recent first,
    via Finnhub, best-effort -> [] on error.

    Used to compare the options-implied earnings move against historical post-earnings
    moves. The hour matters: an amc (after-market-close) reporter's price reaction is the
    NEXT trading day, so historical_earnings_moves needs it to pick the right bar.
    """
    load_dotenv()
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        return []
    today = datetime.now().date()
    try:
        res = requests.get(
            EARNINGS_URL,
            params={"symbol": finnhub_symbol(symbol),
                    "from": (today - timedelta(days=lookback_days)).isoformat(),
                    "to": today.isoformat(), "token": key},
            timeout=20,
        )
        if res.status_code != 200:
            return []
        cal = res.json().get("earningsCalendar", [])
        iso = today.isoformat()
        by_date: dict[str, str | None] = {}
        for c in cal:
            d = c.get("date")
            if not d or d > iso:
                continue
            hour = (c.get("hour") or "").strip().lower() or None
            if d not in by_date or by_date[d] is None:  # dedup by date, prefer a known hour
                by_date[d] = hour
        return [{"date": d, "hour": by_date[d]} for d in sorted(by_date, reverse=True)]
    except Exception:
        return []
