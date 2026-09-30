"""Wake-from-standby network gate for the scheduled entry points (paper suite, lab cycle,
autopilot shim). Task Scheduler wakes the PC and the job fires within seconds — before
Wi-Fi/DNS has reconnected — so every fetch dies on `getaddrinfo failed` in a ~10s window
(2026-08-26/27 outages). This helper blocks until an outbound TCP connect to the API host
succeeds, or a deadline passes. Stdlib-only, no SDK/env imports; callers do their own
logging (the gate prints nothing). It never raises on a down network — a timeout returns
None and the caller proceeds, so a REAL outage still produces honest error rows, the
manager note, and watchdog visibility instead of a silent skip."""
from __future__ import annotations

import socket
import time
from typing import Callable

DEFAULT_HOST = "api.webull.com"


def _tcp_probe(host: str, port: int, timeout_sec: float) -> None:
    """One DNS-resolve + TCP-connect attempt (no HTTP request — costs no API quota)."""
    socket.create_connection((host, port), timeout=timeout_sec).close()


def wait_for_network(host: str = DEFAULT_HOST, port: int = 443, *,
                     deadline_sec: float = 120.0, interval_sec: float = 3.0,
                     probe_timeout_sec: float = 5.0,
                     probe: Callable[[], None] | None = None,
                     sleep: Callable[[float], None] = time.sleep,
                     clock: Callable[[], float] = time.monotonic) -> float | None:
    """Block until the network is reachable or `deadline_sec` has passed. Returns the seconds
    waited (0.0 = up on the first probe, the normal awake case — the connect's own duration
    never counts as waiting) or None on timeout. Only OSError from the probe counts as "not
    ready yet"; anything else is a broken probe and propagates. `probe`/`sleep`/`clock` are
    injection seams for tests."""
    if probe is None:
        probe = lambda: _tcp_probe(host, port, probe_timeout_sec)  # noqa: E731
    start = clock()
    first = True
    while True:
        try:
            probe()
            return 0.0 if first else clock() - start
        except OSError:
            pass
        first = False
        if clock() - start >= deadline_sec:
            return None
        sleep(interval_sec)
