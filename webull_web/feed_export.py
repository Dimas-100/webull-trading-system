"""python -m webull_web.feed_export -- print the kestrel feed (one JSON document) to standard output.
kestrel runs this as a command source (its profile names it), so the web server need not be running.
Read-only like feed_service: it builds the document and writes it to stdout, nothing else."""
from __future__ import annotations

import json
import sys

from . import feed_service


def main(argv: list[str] | None = None) -> int:
    try:
        text = json.dumps(feed_service.build(), allow_nan=False)
    except Exception as e:
        print(f"feed export failed: {feed_service._err(e)}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
