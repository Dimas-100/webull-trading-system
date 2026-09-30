"""Phone push -- re-exported from webull_api.push (the helper moved down a layer 2026-09-21 so
the autopilot can push without importing the web layer). Every web-side caller keeps importing
`from . import push` / `from webull_web.push import push_text`; the contract is unchanged:
plain text, best-effort, never raises."""
from __future__ import annotations

import urllib.request  # noqa: F401  -- tests patch push.urllib.request.urlopen (one shared module)

from webull_api.push import MAX_BYTES, TRIM_MARK, fit, push_text  # noqa: F401

__all__ = ["MAX_BYTES", "TRIM_MARK", "fit", "push_text"]
