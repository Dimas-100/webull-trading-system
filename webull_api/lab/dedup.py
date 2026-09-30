"""Structural + behavioral duplicate detection for lab strategies.

fingerprint/canon_bucket canonicalize the RULE only (entry/exit/filter + stops/sizing) — name,
symbol, and timeframe are not identity. Commutative composite leaves (all_of/any_of) are sorted
before hashing so leaf order cannot split a fingerprint. canon_bucket coarsens integer indicator
periods (29/30/31 -> one bucket) to collapse param-noise variants. Pure, no I/O.
"""
from __future__ import annotations

import hashlib
import json

from webull_api.strategy.schema import EquityPoint, Strategy
from webull_api.lab.schema import _INT_FIELDS as _PERIOD_FIELDS


def _canon_node(node) -> dict | None:
    if node is None:
        return None
    d = node.model_dump(mode="json", exclude_none=True)
    conds = d.get("conditions")
    if conds is not None:
        d["conditions"] = sorted(conds, key=lambda c: json.dumps(c, sort_keys=True))
    return d


def _hash(canon: dict) -> str:
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def fingerprint(strategy: Strategy) -> str:
    return _hash({
        "entry": _canon_node(strategy.entry),
        "exit": _canon_node(strategy.exit),
        "filter": _canon_node(strategy.filter),
        "stop_loss_pct": strategy.stop_loss_pct,
        "take_profit_pct": strategy.take_profit_pct,
        "sizing": strategy.sizing.model_dump(mode="json"),
    })


def _bucket_int(v: int, period_bucket: int) -> int:
    return int(round(v / period_bucket) * period_bucket)


def _bucket_leaf(leaf: dict, period_bucket: int) -> dict:
    out = dict(leaf)
    for f in _PERIOD_FIELDS:
        if isinstance(out.get(f), int):
            out[f] = _bucket_int(out[f], period_bucket)
    return out


def _bucket_node(node, period_bucket: int) -> dict | None:
    if node is None:
        return None
    d = node.model_dump(mode="json", exclude_none=True)
    conds = d.get("conditions")
    if conds is not None:
        bucketed = [_bucket_leaf(c, period_bucket) for c in conds]
        d["conditions"] = sorted(bucketed, key=lambda c: json.dumps(c, sort_keys=True))
        return d
    return _bucket_leaf(d, period_bucket)


def canon_bucket(strategy: Strategy, *, period_bucket: int = 5) -> str:
    return _hash({
        "entry": _bucket_node(strategy.entry, period_bucket),
        "exit": _bucket_node(strategy.exit, period_bucket),
        "filter": _bucket_node(strategy.filter, period_bucket),
        "stop_loss_pct": strategy.stop_loss_pct,
        "take_profit_pct": strategy.take_profit_pct,
    })


def _returns(eq: list[float]) -> list[float]:
    out = []
    for i in range(1, len(eq)):
        prev = eq[i - 1]
        out.append((eq[i] / prev - 1) if prev else 0.0)
    return out


def equity_correlation(a: list[EquityPoint], b: list[EquityPoint]) -> float:
    ra = _returns([p.equity for p in a])
    rb = _returns([p.equity for p in b])
    n = min(len(ra), len(rb))
    if n < 2:
        return 0.0
    ra, rb = ra[:n], rb[:n]
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / (va ** 0.5 * vb ** 0.5)


def behavioral_cohort(equity_curve: list[EquityPoint], *, bins: int = 8) -> str:
    eq = [p.equity for p in equity_curve]
    if len(eq) < 2 or not eq[0]:
        return "flat"
    rets = _returns(eq)
    total = eq[-1] / eq[0] - 1
    up_frac = sum(1 for r in rets if r > 0) / len(rets)
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        if peak:
            mdd = min(mdd, v / peak - 1)
    return f"r{int(round(total * bins))}:u{int(round(up_frac * bins))}:d{int(round(mdd * bins))}"


def is_duplicate(strategy, library_fingerprints, *, canon_buckets=None,
                 equity_curve=None, library_curves=None,
                 corr_threshold: float = 0.90) -> tuple[bool, str | None]:
    fp = fingerprint(strategy)
    if fp in library_fingerprints:
        return True, fp
    if canon_buckets is not None and canon_bucket(strategy) in canon_buckets:
        return True, fp
    if equity_curve is not None and library_curves:
        for other_fp, curve in library_curves:
            if equity_correlation(equity_curve, curve) > corr_threshold:
                return True, other_fp
    return False, None
