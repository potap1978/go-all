"""SIGHUP / SIGTERM during a migration or a return to iptables.

kaskadctl runs them from the menu: the SSH session drops (SIGHUP), systemd stops something,
someone types ``kill``. Python's default action ends the process between two steps — engine nft
written but the services missing, or the old rules gone and the nft table deleted — and nothing
puts it right. Under ``guard()`` the first such signal raises ``Interrupted`` inside the step that
is running, so it fails like any other error and the automatic rollback runs; further signals are
ignored while it does. Inside ``deferred()`` (the rollback itself, the last bookkeeping after a
switch that has already been verified) a signal is only remembered: finishing is safer than
stopping half-way.

The previous handlers are put back when ``guard()`` ends. Outside the main thread signal handlers
cannot be installed — then nothing changes (Python's defaults stay).
"""

import contextlib
import signal

from ..errors import KaskadError
from ..i18n import tr

SIGNALS = ("SIGHUP", "SIGTERM")


class Interrupted(KaskadError):
    """The process got SIGHUP or SIGTERM in the middle of a change."""


class _Guard(object):
    def __init__(self):
        self.fired = None          # name of the first signal
        self.defer = 0             # > 0: inside deferred()


_g = _Guard()


def _handler(signum, frame):
    name = _name(signum)
    if _g.fired is not None or _g.defer:
        _g.fired = _g.fired or name      # remembered; the rollback (or the last step) goes on
        return
    _g.fired = name
    raise Interrupted(tr("migrate.interrupted", signal=name))


def _name(signum):
    for n in SIGNALS:
        if getattr(signal, n, None) == signum:
            return n
    return str(signum)


def fired():
    """Name of the signal received under the current guard, or None."""
    return _g.fired


@contextlib.contextmanager
def guard():
    """Turn the first SIGHUP/SIGTERM into ``Interrupted`` for the duration of the block."""
    old = {}
    for n in SIGNALS:
        sig = getattr(signal, n, None)
        if sig is None:
            continue
        try:
            old[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):            # not the main thread: keep the defaults
            pass
    saved = (_g.fired, _g.defer)
    _g.fired, _g.defer = None, 0
    try:
        yield
    finally:
        for sig, h in old.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError, TypeError):
                pass
        _g.fired, _g.defer = saved


@contextlib.contextmanager
def deferred():
    """Inside the block a SIGHUP/SIGTERM is only remembered (see the module docstring)."""
    _g.defer += 1
    try:
        yield
    finally:
        _g.defer -= 1
