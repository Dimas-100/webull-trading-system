"""Round-3 holding-period candidates on daily bars (spec docs/superpowers/specs/2026-09-09-hold-round-design.md).

Pure research package: no I/O, no network, no order path. The daily store is read only by
scripts/backtest_hold_candidates.py, which hands plain row dicts in here."""
