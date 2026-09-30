"""Tiingo daily-history client for the OFFLINE path (spec 2026-09-07 tiingo depth §3.1).
Token from TIINGO_API_TOKEN only; never logged. No trading import, ever."""
from __future__ import annotations

import csv
import io
import os
import time

import requests

BASE = "https://api.tiingo.com/tiingo/daily"
IEX_BASE = "https://api.tiingo.com/iex"
_TIMEOUT = 30
_BACKOFF = (2, 4, 8)          # seconds before retry 1, 2, 3 on 429 / 5xx


class TiingoError(RuntimeError):
    pass


class TiingoNotFound(TiingoError):
    pass


def api_symbol(symbol: str) -> str:
    """The toolkit spells class shares with a space ("BRK B"); Tiingo wants a dash."""
    return symbol.strip().upper().replace(" ", "-")


class TiingoClient:
    def __init__(self, token: str | None = None, *, session=None, sleep=time.sleep):
        tok = token or os.environ.get("TIINGO_API_TOKEN", "")
        if not tok:
            raise TiingoError("TIINGO_API_TOKEN not set — put it in .env (never in git)")
        self._headers = {"Authorization": f"Token {tok}", "Content-Type": "application/json"}
        self._session = session or requests.Session()
        self._sleep = sleep

    def _request(self, url: str, params: dict | None = None):
        last = None
        for attempt in range(len(_BACKOFF) + 1):
            resp = self._session.get(url, params=params, headers=self._headers, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 404:
                raise TiingoNotFound(f"404 {url}")
            last = resp
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < len(_BACKOFF):
                    self._sleep(_BACKOFF[attempt])
                    continue
            break
        raise TiingoError(f"{last.status_code} {url}: {(last.text or '')[:200]}")

    def _get(self, url: str, params: dict | None = None):
        return self._request(url, params).json()

    def _get_text(self, url: str, params: dict | None = None) -> str:
        return self._request(url, params).text or ""

    def meta(self, symbol: str) -> dict:
        return dict(self._get(f"{BASE}/{api_symbol(symbol)}") or {})

    def daily_prices(self, symbol: str, *, start: str, end: str | None = None,
                     resample: str = "daily") -> list[dict]:
        ordered = {"startDate": start}
        if end:
            ordered["endDate"] = end
        ordered.update({"resampleFreq": resample, "format": "json"})
        rows = self._get(f"{BASE}/{api_symbol(symbol)}/prices", ordered)
        return list(rows or [])

    def intraday_prices(self, symbol: str, *, start: str, end: str, freq: str = "1min") -> list[dict]:
        """Historical IEX bars (IEX prints only) as dicts of strings; regular hours only by default."""
        params = {"startDate": start, "endDate": end, "resampleFreq": freq,
                  "columns": "open,high,low,close,volume", "format": "csv"}
        text = self._get_text(f"{IEX_BASE}/{api_symbol(symbol)}/prices", params)
        return [dict(r) for r in csv.DictReader(io.StringIO(text))]
