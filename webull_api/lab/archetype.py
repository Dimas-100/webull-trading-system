"""Structural archetype classification of a Strategy (no I/O).

A present regime FILTER makes the rule a 'filtered_trigger' (the user's pullback-in-regime
archetype). Otherwise the entry's most trigger-like leaf decides, by a deterministic precedence.
"""
from __future__ import annotations

from webull_api.strategy.schema import Strategy

# Most trigger-like first; the entry's archetype is the first present in this order.
_PRECEDENCE = ("breakout", "mean_revert", "momentum", "trend_follow")


def _leaves(node) -> list:
    if node is None:
        return []
    if getattr(node, "conditions", None) is not None:
        return list(node.conditions)
    return [node]


def _leaf_archetype(leaf) -> str:
    t = leaf.type
    if t == "breakout":
        return "breakout"
    if t == "rsi":
        return "mean_revert" if leaf.comparison == "below" else "momentum"
    if t == "macd":
        return "momentum"
    if t == "ibs":
        return "mean_revert" if leaf.side == "below" else "momentum"
    if t == "zscore":
        return "mean_revert" if leaf.comparison == "below" else "momentum"
    if t in ("drop_from_high", "consec_down"):
        return "mean_revert"
    # sma_cross / ema_cross / price_vs_sma / atr_pct -> a trend/regime read
    return "trend_follow"


def classify(strategy: Strategy) -> str:
    if strategy.filter is not None:
        return "filtered_trigger"
    tags = [_leaf_archetype(leaf) for leaf in _leaves(strategy.entry)]
    return next((p for p in _PRECEDENCE if p in tags), "trend_follow")
