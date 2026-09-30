"""The wake-from-standby network gate (webull_api/net_gate.py): the scheduled entry points
(paper suite / lab cycle / autopilot shim) fire seconds after the PC wakes, before Wi-Fi/DNS
has reconnected (2026-08-26/27: every bar fetch died on `getaddrinfo failed` inside a ~10s
window). `wait_for_network` blocks until an outbound probe succeeds or a deadline passes —
returning seconds waited (0.0 = network already up) or None on timeout, so callers can log
the race without the gate printing anything itself. Probe/sleep/clock are injected here; no
test touches a real socket."""
from webull_api.net_gate import wait_for_network


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Sleep:
    """Recorded sleep that advances the fake clock."""

    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def __call__(self, sec):
        self.calls.append(sec)
        self.clock.t += sec


class _Probe:
    """Fails with OSError `failures` times, then succeeds."""

    def __init__(self, failures=0):
        self.failures = failures
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("getaddrinfo failed")


def test_network_already_up_returns_zero_and_never_sleeps():
    clock = _Clock()
    sleep = _Sleep(clock)
    probe = _Probe(failures=0)
    assert wait_for_network(probe=probe, sleep=sleep, clock=clock) == 0.0
    assert probe.calls == 1 and sleep.calls == []


def test_first_probe_success_is_zero_even_when_the_connect_itself_took_time():
    """The real TCP connect takes ~0.1-0.3s even on a healthy network; that must read as
    "no wait" (0.0), or the callers would log a wake-race line every normal evening."""
    clock = _Clock()

    def slow_ok_probe():
        clock.t += 0.2                     # connect duration, not waiting

    assert wait_for_network(probe=slow_ok_probe, sleep=_Sleep(clock), clock=clock) == 0.0


def test_retries_until_probe_succeeds_and_returns_seconds_waited():
    clock = _Clock()
    sleep = _Sleep(clock)
    probe = _Probe(failures=2)
    waited = wait_for_network(probe=probe, sleep=sleep, clock=clock, interval_sec=3.0)
    assert waited == 6.0                       # two 3s sleeps before the third probe succeeded
    assert probe.calls == 3 and sleep.calls == [3.0, 3.0]


def test_gives_up_with_none_after_the_deadline():
    clock = _Clock()
    sleep = _Sleep(clock)
    probe = _Probe(failures=10 ** 9)           # never succeeds
    waited = wait_for_network(probe=probe, sleep=sleep, clock=clock,
                              deadline_sec=10.0, interval_sec=3.0)
    assert waited is None
    # Probes at t=0,3,6,9 fail inside the deadline; the t=12 failure is past it and gives up.
    assert probe.calls == 5


def test_non_oserror_from_probe_propagates():
    """Only network failures (OSError) mean "not ready yet" — a broken probe must not be
    silently retried forever as if it were a down network."""
    def broken():
        raise ValueError("bad probe wiring")
    clock = _Clock()
    try:
        wait_for_network(probe=broken, sleep=_Sleep(clock), clock=clock)
    except ValueError:
        pass
    else:
        raise AssertionError("ValueError from the probe should propagate")
