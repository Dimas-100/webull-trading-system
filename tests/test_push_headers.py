"""push_text's ntfy headers (Priority / Tags / Click) and the byte guard that keeps a body
under ntfy's 4,096-byte attachment threshold (over it the phone shows "message.txt")."""
from webull_web import push


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"ok"


def test_priority_tags_and_click_headers(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["priority"] = req.get_header("Priority")
        seen["tags"] = req.get_header("Tags")
        seen["click"] = req.get_header("Click")
        return _Resp()
    monkeypatch.setattr(push.urllib.request, "urlopen", fake_urlopen)
    assert push.push_text("b", title="t", url="https://ntfy.sh/topic",
                          priority="high", tags=["warning", "chart"], click="https://x") is True
    assert seen == {"priority": "high", "tags": "warning,chart", "click": "https://x"}
    seen.clear()
    assert push.push_text("b", title="t", url="https://ntfy.sh/topic") is True
    assert seen == {"priority": None, "tags": None, "click": None}   # nothing sent when unset


def test_fit_trims_on_a_line_boundary_under_the_byte_ceiling():
    body = "\n".join(f"line {i} " + "x" * 40 for i in range(200))       # ~9.6 KB
    out = push.fit(body)
    assert len(out.encode("utf-8")) <= push.MAX_BYTES
    assert out.endswith(push.TRIM_MARK)
    kept = out[: -len(push.TRIM_MARK)]
    assert kept.splitlines()[-1].startswith("line ")           # no half line
    assert push.fit("short") == "short"


def test_fit_counts_utf8_bytes_not_characters():
    body = "—" * 2000                                      # 2,000 chars = 6,000 bytes
    out = push.fit(body)
    assert len(out.encode("utf-8")) <= push.MAX_BYTES


def test_oversized_body_is_trimmed_before_the_post(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["data"] = req.data
        return _Resp()
    monkeypatch.setattr(push.urllib.request, "urlopen", fake_urlopen)
    assert push.push_text("y" * 5000, title="t", url="https://ntfy.sh/topic") is True
    assert len(seen["data"]) <= push.MAX_BYTES
    assert seen["data"].decode("utf-8").endswith(push.TRIM_MARK)


def test_web_module_is_a_pure_reexport_of_the_api_helper():
    from webull_api import push as api_push
    assert push.push_text is api_push.push_text and push.fit is api_push.fit
