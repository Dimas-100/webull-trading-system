"""Autopilot audit log (append-only JSONL) + a best-effort notify() hook."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from .paths import autopilot_dir


def _dir() -> Path:
    return autopilot_dir()


def log_decision(entry: dict, *, day: str) -> None:
    d = _dir() / "log"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{day}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def notify(summary: str, target: str) -> bool:
    """Best-effort. Always prints; POSTs {"text": summary} when target is an http(s) URL.
    Never raises — a notification failure must not affect placement (which already happened)."""
    print(f"[autopilot] {summary}")
    if not target:
        return True
    try:
        if target.startswith("http://") or target.startswith("https://"):
            req = urllib.request.Request(
                target, data=json.dumps({"text": summary}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:
        return False


def placed_count() -> int:
    """Count audit entries across all daily logs where a real order was actually placed
    (``placed is True``). Store absent -> 0. Tolerates blank / corrupt lines. Read-only."""
    d = _dir() / "log"
    if not d.exists():
        return 0
    n = 0
    for f in sorted(d.glob("*.jsonl")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("placed") is True:
                n += 1
    return n
