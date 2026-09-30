"""Swing screen over a universe: fetch bars + run plan_swing per symbol, return ranked rows.

The single source of truth shared by the trade MCP's screen_and_draft and the autopilot ENTRIES
stage. Each ScreenRow carries the full SwingPlan (or a per-symbol error). SPY is fetched once.
Earnings are verified for PASSes only (0-3 Finnhub calls per screen, not 40): a PASS is
re-planned with the fetched next-earnings date, so the planner's 14-day disqualifier finally
runs on automated screens — a PASS can only stay PASS or become SKIP, and a fetch failure keeps
the earnings-unverified plan (never blocks the screen). MarketDataNotEntitledError propagates so
callers can map it (MCP -> error payload)."""
from __future__ import annotations

from dataclasses import dataclass

from webull_api import market_data
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.strategy.bars import to_ohlcv
from webull_api.swing import planner
from webull_api.swing.schema import SwingPlan


@dataclass
class ScreenRow:
    symbol: str
    plan: SwingPlan | None = None
    error: str | None = None


def _best_effort(fn):
    """None on any error EXCEPT an entitlement error, which must propagate."""
    try:
        return fn()
    except MarketDataNotEntitledError:
        raise
    except Exception:
        return None


def _gates_passed(plan: SwingPlan | None) -> int:
    return sum(1 for g in plan.gates if g.ok) if plan else 0


def _next_earnings_default(symbol):
    # The Finnhub helper lives in the services layer (webull_web); resolving it lazily at call
    # time keeps this module import-light and the fetcher injectable.
    from webull_web import news
    return news.get_next_earnings_date(symbol)


def screen(symbols, book: float = 500.0, *, get_bars=market_data.get_bars,
           get_next_earnings=None) -> list[ScreenRow]:
    fetch_earnings = get_next_earnings if get_next_earnings is not None else _next_earnings_default
    spy = _best_effort(lambda: to_ohlcv(get_bars("SPY", "D", count="250")))
    rows: list[ScreenRow] = []
    for sym in symbols:
        try:
            daily = to_ohlcv(get_bars(sym, "D", count="250"))
            weekly = _best_effort(lambda: to_ohlcv(get_bars(sym, "W", count="60")))
            p = planner.plan_swing(sym, daily, weekly=weekly, spy=spy, earnings_date=None,
                                   book=book, confirm_not_leveraged=True, confirm_not_binary=True)
            if p.verdict == "PASS":
                earn = _best_effort(lambda: fetch_earnings(sym))
                if earn:
                    p = planner.plan_swing(sym, daily, weekly=weekly, spy=spy, earnings_date=earn,
                                           book=book, confirm_not_leveraged=True,
                                           confirm_not_binary=True)
            rows.append(ScreenRow(symbol=sym, plan=p))
        except MarketDataNotEntitledError:
            raise
        except Exception as e:
            rows.append(ScreenRow(symbol=sym, error=str(e)[:120]))
    rows.sort(key=lambda r: (r.plan is None or r.plan.verdict != "PASS", -_gates_passed(r.plan)))
    return rows
