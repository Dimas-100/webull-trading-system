"""Sizing and stacking study (research only) -- spec
docs/superpowers/specs/2026-09-10-sizing-stacking-study-design.md.

At what fraction of equity per lot, stacked or not, do the two proven trade lists (the RSI2 swing book
and the IBS ETF book) compound best for a given marked drawdown? Pure simulation over fixed trade
lists: it changes no signal, touches no live book, and places nothing.
"""
from webull_api.sizing_study import book, cells, loaders, metrics, study  # noqa: F401

__all__ = ["book", "cells", "loaders", "metrics", "study"]
