"""Exit rules + a scan loop over open positions.

A pure rule layer (no I/O) plus an injectable scan() that fetches positions + daily bars and
returns ExitRows (signals first). Long equity only. Shared by the webull-trade `manage_exits`
tool and (later) the nightly scheduler. Mirrors webull_api/swing/screen.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from webull_api import market_data, portfolio
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.strategy.bars import to_ohlcv


@dataclass(frozen=True)
class ExitConfig:
    stop_loss_pct: float = 8.0
    take_profit_pct: float = 20.0


DEFAULT_EXIT_CONFIG = ExitConfig()


@dataclass
class ExitSignal:
    symbol: str
    qty: float
    last: float | None
    unrealized_pct: float | None
    reason: str            # primary: "stop" | "target" | "break"
    reasons: list[str]     # every condition that fired
    detail: str


@dataclass
class ExitRow:
    symbol: str
    qty: float | None = None
    signal: ExitSignal | None = None
    held: str | None = None
    error: str | None = None


# Flexible field-key aliases so a position dict from any source normalizes (camelCase or snake_case).
_SYMBOL_KEYS = ("symbol", "ticker")
_QTY_KEYS = ("quantity", "qty", "position", "shares")
_COST_KEYS = ("cost_price", "costPrice", "avgCost", "averageCost", "unitCost", "unit_cost")
_LAST_KEYS = ("last_price", "lastPrice")
_UPL_RATE_KEYS = ("unrealized_profit_loss_rate", "unrealizedProfitLossRate")


from webull_api._util import num as _num  # canonical defensive numeric extractor


def _str(row, keys):
    for k in keys:
        v = row.get(k)
        if v is not None and v != "":
            return str(v)
    return ""


def _is_option(row) -> bool:
    return str(row.get("instrument_type") or row.get("asset_type") or "").upper() == "OPTION"


def broke_below_sma(closes, period: int = 20, confirm_days: int = 3) -> bool:
    """True on the day a close crosses from >= its SMA(period) to below it, OR when the last
    `confirm_days` closes each sit below their SMA(period) — an established break. The state
    form self-heals crosses that landed on a day the caller didn't run (a cross fires only
    once, so a missed evening used to leave the position unmanaged forever)."""
    n = len(closes)
    if n < period + 1:
        return False
    sma_now = sum(closes[-period:]) / period
    sma_prev = sum(closes[-period - 1:-1]) / period
    if closes[-2] >= sma_prev and closes[-1] < sma_now:
        return True
    if n < period + confirm_days - 1:
        return False
    for k in range(1, confirm_days + 1):
        end = n - k + 1
        if closes[end - 1] >= sum(closes[end - period:end]) / period:
            return False
    return True


def evaluate(symbol, qty, avg_cost, last, upl_rate, closes,
             cfg: ExitConfig = DEFAULT_EXIT_CONFIG) -> ExitSignal | None:
    # round(...,6) is float-boundary insurance: without it IEEE-754 turns an exact -8%/+20%
    # into e.g. -7.9999999 and the threshold comparison flips. Do not "simplify" it away.
    if last is not None and avg_cost not in (None, 0):
        unrealized_pct = round((last / avg_cost - 1) * 100, 6)
    elif upl_rate is not None:
        unrealized_pct = round(upl_rate * 100, 6)
    else:
        unrealized_pct = None

    reasons: list[str] = []
    if unrealized_pct is not None and unrealized_pct <= -cfg.stop_loss_pct:
        reasons.append("stop")
    if unrealized_pct is not None and unrealized_pct >= cfg.take_profit_pct:
        reasons.append("target")
    if broke_below_sma(closes):
        reasons.append("break")
    if not reasons:
        return None

    pct = f"{unrealized_pct:+.1f}%" if unrealized_pct is not None else "n/a"
    labels = {
        "stop": f"down past your -{cfg.stop_loss_pct:g}% stop",
        "target": f"up past your +{cfg.take_profit_pct:g}% target",
        "break": "closed below its 20-day average (trend break)",
    }
    detail = f"{symbol} {', '.join(labels[r] for r in reasons)} (unrealized {pct})."
    return ExitSignal(symbol=symbol, qty=qty, last=last, unrealized_pct=unrealized_pct,
                      reason=reasons[0], reasons=reasons, detail=detail)


def _closes(get_bars, symbol):
    """Best-effort daily closes (oldest-first). Entitlement errors propagate; others -> []."""
    try:
        bars = to_ohlcv(get_bars(symbol, "D", count="60"))
        return [b["close"] for b in bars if b.get("close") is not None]
    except MarketDataNotEntitledError:
        raise
    except Exception:
        return []


def scan(account_id, *, cfg: ExitConfig = DEFAULT_EXIT_CONFIG,
         get_positions=portfolio.get_positions, get_bars=market_data.get_bars) -> list[ExitRow]:
    raw = get_positions(account_id)
    rows: list[ExitRow] = []
    for pos in (raw if isinstance(raw, list) else []):
        if not isinstance(pos, dict) or _is_option(pos):
            continue
        symbol = _str(pos, _SYMBOL_KEYS)
        qty = _num(pos, _QTY_KEYS)
        if not symbol or qty is None or qty <= 0:
            continue
        try:
            avg_cost = _num(pos, _COST_KEYS)
            last = _num(pos, _LAST_KEYS)
            upl_rate = _num(pos, _UPL_RATE_KEYS)
            closes = _closes(get_bars, symbol)
            sig = evaluate(symbol, qty, avg_cost, last, upl_rate, closes, cfg)
            rows.append(ExitRow(symbol=symbol, qty=qty, signal=sig,
                                held=None if sig else "no exit signal"))
        except MarketDataNotEntitledError:
            raise
        except Exception as e:
            rows.append(ExitRow(symbol=symbol, qty=qty, error=str(e)[:120]))
    rows.sort(key=lambda r: r.signal is None)
    return rows
