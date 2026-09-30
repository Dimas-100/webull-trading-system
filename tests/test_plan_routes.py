"""The plan code that outlived the web app never imports an order path. (The /api/plan and /api/overview routes, and
the plan_service / cockpit_strip modules they served, were archived with the web app 2026-09-28.)"""
import re
from pathlib import Path

from webull_api.paths import REPO_ROOT


def test_plan_code_never_imports_an_order_path():
    forbidden = re.compile(r"webull_api\.trading|import trading|webull_api\.safety|import safety|webull_api\.autopilot|\.place\(|\.cancel\(")
    for rel in ("webull_web/plan_stats.py", "webull_web/visibility.py"):
        text = Path(REPO_ROOT / rel).read_text(encoding="utf-8")
        assert not forbidden.search(text), rel
