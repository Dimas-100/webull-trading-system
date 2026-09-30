"""Repo-anchored paths for the autopilot store + kill-file.

So the daily state, the audit log, and — most importantly — the kill-file resolve to the SAME
location whether the connector launches from the repo (Desktop chdirs there), a script, or the
Windows scheduler (different CWD). Mirrors how ``webull_api.client`` anchors SDK logs to
``<repo>/logs/``. A RELATIVE ``WEBULL_AUTOPILOT_*`` value is resolved under the repo root; an
ABSOLUTE one is honored as-is (e.g. a phone-synced OneDrive kill-file). The store root
(``WEBULL_AUTOPILOT_DIR``) holds the state, the audit log, and the DEFAULT kill-file;
``WEBULL_AUTOPILOT_KILL_FILE`` moves only the kill-file (e.g. to a synced folder). Pure, no I/O.
"""
from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]  # webull_api/autopilot/paths.py -> repo root


def _anchor(value: str | None, default_rel: str) -> Path:
    raw = Path(value) if value else Path(default_rel)
    return raw if raw.is_absolute() else (_REPO_ROOT / raw)


def autopilot_dir() -> Path:
    """The autopilot store root (daily state + audit logs). Override with WEBULL_AUTOPILOT_DIR;
    a relative override is anchored under the repo, an absolute one is used verbatim."""
    return _anchor(os.environ.get("WEBULL_AUTOPILOT_DIR"), "autopilot")


def resolve_kill_file(value: str | None) -> str:
    """Resolve the kill-file path. An explicit value: relative -> anchored under the repo, absolute
    (e.g. a OneDrive-synced path) -> used verbatim. UNSET -> ``<store dir>/KILL`` so the kill-file
    follows WEBULL_AUTOPILOT_DIR (state, audit, and the default kill-file share one store root)."""
    if value:
        return str(_anchor(value, "autopilot/KILL"))
    return str(autopilot_dir() / "KILL")


def default_kill_file() -> str:
    """The default kill-file path: ``<store dir>/KILL`` (i.e. ``<repo>/autopilot/KILL`` unless
    WEBULL_AUTOPILOT_DIR overrides the store root)."""
    return resolve_kill_file(None)
