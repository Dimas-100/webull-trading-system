"""Read-only Finnhub fundamentals fetch + sector-ETF resolution (external; key from
FINNHUB_API_KEY). Lives in the web layer like news.py, keeping webull_api Webull-pure.
All calls are best-effort: missing key / non-200 / network error -> None, never raises."""
from __future__ import annotations

import requests

from . import finnhub

METRIC_URL = "https://finnhub.io/api/v1/stock/metric"
PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2"

# (etf, keywords) — first keyword substring match (case-insensitive) wins.
SECTOR_ETF = [
    ("XLK", ["technology", "semiconductor", "software", "hardware", "electronic", "it services", "computer"]),
    ("XLF", ["bank", "insurance", "financial", "capital markets", "asset management", "credit"]),
    ("XLV", ["pharma", "biotech", "health", "medical", "life sciences", "drug"]),
    ("XLE", ["oil", "gas", "energy", "coal", "petroleum"]),
    ("XLI", ["aerospace", "defense", "machinery", "industrial", "transportation", "airline", "construction", "logistics"]),
    ("XLY", ["retail", "auto", "apparel", "hotel", "restaurant", "leisure", "e-commerce", "consumer discretionary"]),
    ("XLP", ["food", "beverage", "tobacco", "household", "personal products", "grocery", "consumer staples"]),
    ("XLU", ["utility", "utilities", "electric", "water", "power"]),
    ("XLRE", ["real estate", "reit"]),
    ("XLB", ["chemical", "metal", "mining", "materials", "paper", "steel"]),
    ("XLC", ["telecom", "communication", "entertainment", "interactive media", "social", "media"]),
]


# SnapTrade/Webull return share-class tickers without Finnhub's dot (e.g. BRKB, or the space form
# "BRK B"); Finnhub expects "BRK.B". Normalize before any Finnhub request so these resolve instead of
# 404ing to None (which otherwise mislabels the stock as a fund via the no-industry heuristic).
_FINNHUB_ALIASES = {
    "BRKB": "BRK.B", "BRKA": "BRK.A", "BFB": "BF.B", "BFA": "BF.A",
    "LENB": "LEN.B", "HEIA": "HEI.A", "LGFA": "LGF.A", "LGFB": "LGF.B",
    "GEFB": "GEF.B", "MOGA": "MOG.A", "CWENA": "CWEN.A", "PBRA": "PBR.A",
}


def finnhub_symbol(symbol: str) -> str:
    """Map a SnapTrade/Webull ticker to the symbol form Finnhub expects (share-class dot)."""
    s = (symbol or "").upper().strip()
    if " " in s:                       # "BRK B" -> "BRK.B"
        s = s.replace(" ", ".")
    return _FINNHUB_ALIASES.get(s, s)


def _key() -> str:
    return finnhub.key()


def has_key() -> bool:
    """True when a Finnhub key is configured (so callers can distinguish 'no data' from 'no key')."""
    return bool(_key())


def _get_json(url: str, **params):
    """Best-effort Finnhub GET (token auto-added) -> parsed JSON, or None on missing key/non-200/error.
    The shared request body for this module's three fetches; requests stays in-module (test seam)."""
    key = _key()
    if not key:
        return None
    try:
        res = requests.get(url, params={**params, "token": key}, timeout=20)
        if res.status_code != 200:
            return None
        return res.json()
    except Exception:
        return None


def company_industry(symbol: str) -> str | None:
    """Finnhub `finnhubIndustry` (sector-ish) for a symbol; None on missing key / error / blank."""
    j = _get_json(PROFILE_URL, symbol=finnhub_symbol(symbol))
    return ((j or {}).get("finnhubIndustry") or "").strip() or None


def finnhub_metrics(symbol: str) -> dict | None:
    j = _get_json(METRIC_URL, symbol=finnhub_symbol(symbol), metric="all")
    return (j or {}).get("metric") or None


def map_industry_to_etf(industry: str | None) -> str | None:
    if not industry:
        return None
    s = industry.lower()
    for etf, kws in SECTOR_ETF:
        if any(k in s for k in kws):
            return etf
    return None


def sector_etf(symbol: str) -> str | None:
    j = _get_json(PROFILE_URL, symbol=finnhub_symbol(symbol))
    return map_industry_to_etf((j or {}).get("finnhubIndustry"))
