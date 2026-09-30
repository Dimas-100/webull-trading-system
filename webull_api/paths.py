"""Repo-anchored resolution for the gitignored runtime data stores (``data/<store>``).

One resolver for every store (spec 2026-07-11-canonical-data-layout-design.md), replacing the
per-module CWD-relative defaults that forced MCPs to chdir to the repo. Precedence:
per-store env var > ``WEBULL_DATA_DIR``/<store> > ``<repo>/data/<store>``. A RELATIVE env
value is anchored under the repo root (same rule as ``autopilot/paths.py``); an ABSOLUTE one
is honored verbatim. Pure, no I/O — callers mkdir where they write. The autopilot store is
deliberately NOT served by this module (its kill-file resolution is safety-critical and has
its own ``webull_api/autopilot/paths.py``).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]  # webull_api/paths.py -> repo root


def _anchor(raw: Path) -> Path:
    return raw if raw.is_absolute() else (REPO_ROOT / raw)


def data_dir(store: str, env_var: str | None = None) -> Path:
    """Resolve the directory for one named store under the canonical data/ layout."""
    if env_var:
        override = os.environ.get(env_var)
        if override:
            return _anchor(Path(override))
    root = os.environ.get("WEBULL_DATA_DIR")
    if root:
        return _anchor(Path(root)) / store
    return REPO_ROOT / "data" / store
