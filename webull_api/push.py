"""Best-effort phone push (ntfy-style): POST a plain-text body to a topic URL with ntfy's
Title / Priority / Tags headers. NEVER raises -- a push failure must not fail the runner that
composed the message. Lives in webull_api so the autopilot can push without importing webull_web;
webull_web.push re-exports it for the webull_web services and the scripts.

Two facts about ntfy that shape this helper (docs.ntfy.sh/publish, verified 2026-09-21):
- the phone apps render PLAIN TEXT only (Markdown renders only in ntfy's own web app), so callers
  send text;
- a body above 4,096 bytes is converted into a text-file ATTACHMENT -- the phone then shows
  "message.txt" instead of the note. `fit()` trims below that with a visible marker so the
  server never makes that call for us (3 of 5 evening notes crossed the line in mid-Sept 2026).
"""
from __future__ import annotations

import logging
import urllib.request

_log = logging.getLogger(__name__)
_TIMEOUT_SEC = 10

# Hard ceiling for one push body, in UTF-8 bytes: safely under ntfy's 4,096-byte attachment
# threshold with room for the trim marker.
MAX_BYTES = 3800
TRIM_MARK = "\n... [trimmed - full note in data/activity/manager_notes.jsonl]"


def fit(text: str, *, max_bytes: int = MAX_BYTES) -> str:
    """`text` unchanged when it fits; otherwise cut on a line boundary and end with TRIM_MARK.
    Always returns a valid UTF-8 string of at most `max_bytes` bytes."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    mark = TRIM_MARK.encode("utf-8")
    budget = max(0, max_bytes - len(mark))
    cut = raw[:budget].decode("utf-8", "ignore")
    nl = cut.rfind("\n")
    if nl > budget // 2:          # keep whole lines unless that would throw away most of the room
        cut = cut[:nl]
    return cut.rstrip() + TRIM_MARK


def push_text(text: str, *, title: str, url: str, priority: str | None = None,
              tags: str | list[str] | tuple[str, ...] | None = None,
              click: str | None = None) -> bool:
    """True only when the POST completed. Empty url -> False with no network call.

    priority: ntfy level ("min" | "low" | "default" | "high" | "urgent"); None sends none.
    tags: ntfy tag names (emoji shortcodes render in front of the title), str or sequence.
    """
    if not url:
        return False
    try:
        # http.client.putheader() encodes header VALUES as latin-1; a title containing e.g.
        # an em-dash (U+2014) is outside latin-1 and would raise UnicodeEncodeError before any
        # network I/O. A lossy replace beats a dead push.
        safe_title = title.encode("latin-1", "replace").decode("latin-1")
        headers = {"Title": safe_title, "Content-Type": "text/plain; charset=utf-8"}
        if priority:
            headers["Priority"] = str(priority)
        if tags:
            headers["Tags"] = tags if isinstance(tags, str) else ",".join(tags)
        if click:
            headers["Click"] = click
        req = urllib.request.Request(
            url, data=fit(text).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC):
            return True
    except Exception:
        _log.exception("push: delivery failed")
        return False
