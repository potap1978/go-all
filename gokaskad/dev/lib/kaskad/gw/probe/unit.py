"""The units of the probes (docs/k3/SPEC-stage1-exits §5.7; stage 1, T6).

``kaskad-gw-probe.service`` (``Type=oneshot``, ``Nice=10``, ``IOSchedulingClass=idle``, ``MemoryMax=96M``,
``TimeoutStartSec=120``, no ``CPUQuota``) + ``kaskad-gw-probe.timer`` (``OnBootSec=2min``, ``OnUnitActiveSec=60``,
``RandomizedDelaySec=10``: without ``OnBootSec`` a timer with ``OnUnitActiveSec`` alone never fires after a reboot until
the service has run once, as ``units.py`` knows) and the template ``kaskad-gw-probe@.service`` — the manual speed test
of one exit (``systemctl start kaskad-gw-probe@<exit id>.service``; ``<exit id>:force`` — past the day's budget).

``ensure`` — from ``ops._apply`` (the gateway on); ``remove`` — ``gw disable`` and ``gw purge`` (all three away; the
removal of Kaskad goes through ``gw purge``, its fallback by files takes ``kaskad-gw-*.service`` and ``.timer``).

The units run the code of the payload by ``runpy`` as the ``kaskadctl`` wrapper does (``python3 -I``, the current lib
first on the path). СВЕСТИ С T10: when ``kaskadctl gw`` gets a command of the probes, ExecStart may call it instead.
"""

import re

from ... import paths
from ...errors import KaskadError
from .. import sys as gsys

SERVICE = "kaskad-gw-probe.service"
TIMER = "kaskad-gw-probe.timer"
TEMPLATE = "kaskad-gw-probe@.service"
UNITS = (TIMER, SERVICE, TEMPLATE)
PYTHON = "/usr/bin/python3"
_EID = re.compile(r"^e-[0-9a-f]{10}(-[0-9]{1,3})?$")


def _exec(args=""):
    code = ("import runpy, sys; sys.path.insert(0, '%s'); "
            "runpy.run_module('kaskad.gw.probe', run_name='__main__', alter_sys=True)" % paths.lib_current())
    return 'ExecStart=%s -I -c "%s"%s' % (PYTHON, code, (" " + args) if args else "")


def _service(desc, args=""):
    return "\n".join([
        "[Unit]",
        "Description=%s" % desc,                        # one text for every language (3.3.2 №3)
        "After=kaskad-gw-collect.service network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "Nice=10",
        "IOSchedulingClass=idle",
        "MemoryMax=96M",
        "TimeoutStartSec=120",
        _exec(args),
        "",
    ])


def service_text():
    return _service("Kaskad 3: probes of the exits (speed, services, country)")


def template_text():
    return _service("Kaskad 3: speed test of exit %i", "--exit %i")


def timer_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: probes of the exits every minute",   # one text for every language
        "",
        "[Timer]",
        "OnBootSec=2min",
        "OnUnitActiveSec=60",
        "RandomizedDelaySec=10",
        "Unit=%s" % SERVICE,
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


# (name, text, kind of unit_refresh) — the one line of unit_refresh.registry()
REFRESH = ((SERVICE, service_text, "oneshot"), (TEMPLATE, template_text, "oneshot"), (TIMER, timer_text, "timer"))


def ensure(runner):
    """The three units written (when their text changed) and the timer enabled and started. -> {"written": [...],
    "started": bool}. KaskadError of systemctl goes up (``_apply`` makes it a warning)."""
    written = [n for n, fn in ((SERVICE, service_text), (TEMPLATE, template_text)) if gsys.write_unit(n, fn())]
    r = gsys.ensure_unit(runner, TIMER, timer_text())
    if written and not r["written"]:
        gsys.systemd(runner, "daemon-reload")           # ensure_unit reloads only for the timer's own file or links
    if r["written"]:
        written.append(TIMER)
    return {"written": written, "started": r["started"]}


def remove(runner):
    """Stop and remove the timer, the service, the template and every running instance of it. Never fails.
    -> [names removed]."""
    runner.run(["systemctl", "stop", "kaskad-gw-probe@*.service"])
    gone = []
    for name in UNITS:
        try:
            if gsys.remove_unit(runner, name):
                gone.append(name)
        except (KaskadError, OSError):
            pass
    return gone


def manual(runner, eid, force=False):
    """Start the manual speed test of ``eid`` (no wait: the result comes to ``probe-results.json`` with
    ``manual: true``). KaskadError for an id that is not one."""
    if not _EID.match(str(eid)):
        raise KaskadError("bad exit id: %r" % (eid,))
    name = "kaskad-gw-probe@%s%s.service" % (eid, ":force" if force else "")
    return gsys.systemd(runner, "start", name, no_block=True)


def kick(runner):
    """A run now (the collector after a request of ``health``): the service is oneshot — a running one is not doubled."""
    return gsys.systemd(runner, "start", SERVICE, check=False, no_block=True)
