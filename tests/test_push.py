"""push_text: plain-text ntfy POST with Title header; best-effort, never raises."""
import inspect

from webull_web import push


class _Resp:
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return b"ok"


def test_empty_url_returns_false_without_network(monkeypatch):
    calls = []
    monkeypatch.setattr(push.urllib.request, "urlopen", lambda *a, **k: calls.append(1))
    assert push.push_text("hi", title="t", url="") is False
    assert calls == []   # provably no network attempt


def test_posts_plain_text_with_title(monkeypatch):
    seen = {}
    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["data"] = req.data
        seen["title"] = req.get_header("Title")
        seen["timeout"] = timeout
        return _Resp()
    monkeypatch.setattr(push.urllib.request, "urlopen", fake_urlopen)
    ok = push.push_text("note body", title="Manager's note - 2026-07-10", url="https://ntfy.sh/topic")
    assert ok is True
    assert seen["url"] == "https://ntfy.sh/topic"
    assert seen["data"] == "note body".encode("utf-8")
    assert seen["title"] == "Manager's note - 2026-07-10"
    assert seen["timeout"] is not None


def test_network_failure_returns_false_never_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(push.urllib.request, "urlopen", boom)
    assert push.push_text("x", title="t", url="https://ntfy.sh/topic") is False


def test_title_with_non_latin1_chars_still_delivers():
    """U+2014 etc. must never kill delivery: header values are latin-1-encoded by
    http.client — push_text must sanitize, not die (regression: a swallowed
    UnicodeEncodeError made every real push fail while mocked tests passed)."""
    import http.server
    import threading

    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen["title"] = self.headers.get("Title")
            seen["body"] = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.end_headers()
        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        ok = push.push_text("body — with unicode", title="Manager's note — 2026-07-10",
                            url=f"http://127.0.0.1:{srv.server_port}/topic")
    finally:
        srv.shutdown()
    assert ok is True
    assert seen["title"] is not None            # delivered, title sanitized not dropped
    assert "2026-07-10" in seen["title"]
    assert "unicode" in seen["body"].decode("utf-8")  # body stays true UTF-8


def test_push_has_no_submit_path():
    src = inspect.getsource(push)
    for forbidden in ("trading.place", "import trading", "place_order", "confirm=True"):
        assert forbidden not in src
