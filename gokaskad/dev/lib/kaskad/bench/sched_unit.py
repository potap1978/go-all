"""The timer of the schedule of the checks (SPEC-node-score-schedule-share §2.4, §2.8 п. 3, NS-S4, NS-S6, NS-S7; B11).

    kaskad-bench-sched.timer        OnCalendar= per line of ``schedule.calendar_lines`` (the time − 15 min, the zone
                                    as a suffix), RandomizedDelaySec=30min, AccuracySec=1s, Persistent=false
    kaskad-bench-sched.service      Type=oneshot, TimeoutStartSec=90min, Nice=10:
                                    ``kaskadctl host bench --scheduled`` (``bench.sched_run``)
    kaskad-bench-retry-<id>.timer   the repeat after a sharp drop (§2.8): ``systemd-run --on-active=<s>``, transient

``apply(runner, st)`` — writes both units when their text changed and enables the timer; removes them when no entry is
on or the schedule is not armed (NS-S4). Callers: the save of the schedule (API, CLI), the change of ``KASKAD_TZ``
(``tz_cli``), the restore of a .kbk (``backup``), a mark of the first manual check / the consent (``marks.AFTER``), the
update of Kaskad (``unit_refresh.registry`` — the text of the timer from the state), and the runner after a firing when
its lines drifted (the one-shot spring lines run out, §2.4). A failing ``systemctl`` — the schedule stays saved, the
answer ``{"applied": false, "why"}``.

Every line of ``OnCalendar=`` is built of checked numbers and the zone name (NS-S7) and checked once more here by
``fullmatch`` of ``LINE`` before it reaches the file: a line that does not match is left out (and said in ``bad``),
the file never gets a newline or a «=» from anywhere. ``gw purge`` never touches these units (the check of the server
does not depend on the gateway); the removal of Kaskad takes ``kaskad-*.timer|service``.
"""

import re

from .. import paths, tz
from ..errors import KaskadError

SERVICE = "kaskad-bench-sched.service"
TIMER = "kaskad-bench-sched.timer"
UNITS = (TIMER, SERVICE)
PYTHON = "/usr/bin/python3"
RETRY_UNIT = "kaskad-bench-retry-%s"
_ID = re.compile(r"[0-9a-f]{10}")
_DAYS = r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(,(Mon|Tue|Wed|Thu|Fri|Sat|Sun)){0,6}"
_HMS = r"([01][0-9]|2[0-3]):[0-5][0-9]:00"
_ZONE = re.compile(tz.NAME_RE.pattern.lstrip("^").rstrip("$"))
LINE = re.compile(r"%s (\*-\*-\*|\*-\*-01\.\.07|\*-\*-01\.\.06|\*-\*~01) %s (?P<zone>[A-Za-z0-9_+/-]{1,64})"
                  % (_DAYS, _HMS))
ONE_SHOT = re.compile(r"20[0-9]{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01]) %s UTC" % _HMS)
RETRY_MIN_S, RETRY_MAX_S = 3600, 7200        # §2.8 п. 3: 60–120 min after the end of the check


def _exec(args):
    code = ("import runpy, sys; sys.path.insert(0, '%s'); "
            "runpy.run_module('kaskad', run_name='__main__', alter_sys=True)" % paths.lib_current())
    return 'ExecStart=%s -I -c "%s" %s' % (PYTHON, code, args)


def service_text():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: server check by the schedule",      # one text for every language
        "After=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "Nice=10",
        "TimeoutStartSec=90min",              # waiting for quiet 60 min + the test ≤ 10 min (§2.4)
        _exec("host bench --scheduled"),
        "",
    ])


def line_ok(line):
    """A value of ``OnCalendar=`` that may reach the file: our own shape by ``fullmatch``, the zone a valid name."""
    if not isinstance(line, str):
        return False
    if ONE_SHOT.fullmatch(line):
        return True
    m = LINE.fullmatch(line)
    if not m:
        return False
    z = m.group("zone")
    return z == "UTC" or bool(_ZONE.fullmatch(z) and tz.valid_name(z))


def timer_text(lines):
    """The text of the timer for ``lines`` (only those ``line_ok``); None — no line: no timer."""
    lines = [l for l in lines if line_ok(l)]
    if not lines:
        return None
    return "\n".join(["[Unit]", "Description=Kaskad 3: server checks by the schedule", "", "[Timer]"]
                     + ["OnCalendar=%s" % l for l in lines]
                     + ["RandomizedDelaySec=30min", "AccuracySec=1s", "Persistent=false", "Unit=%s" % SERVICE, "",
                        "[Install]", "WantedBy=timers.target", ""])


def _zone():
    """The zone for the lines: the name checked by ``fullmatch`` (``tz.NAME_RE`` lets «…\\n» pass by «$») and loaded;
    otherwise the fallback (UTC+03:00, ``ok`` False — the lines in UTC, ``warn: tz_fallback``)."""
    n = tz.name()
    if isinstance(n, str) and _ZONE.fullmatch(n) and tz.valid_name(n):
        z = tz.load(n)
        if z.ok:
            return z, None
    return tz.Zone.fixed("UTC", tz.FALLBACK_OFFSET), "tz_fallback"


def wanted(st, now=None, here=None):
    """-> (text of the timer | None, warn | None, lines): what the timer must be for ``st`` now. None — no timer
    (nothing on, or not armed: NS-S4)."""
    import time as _time
    from ..gw import model
    from . import machine, schedule
    m = model.bench(st, here=machine.mid() if here is None else here)
    zone, warn = _zone()
    if not m.get("armed"):
        return None, warn, []
    lines = schedule.calendar_lines(m["schedule"], zone, int(_time.time() if now is None else now))
    return timer_text(lines), warn, lines


def current_timer():
    """The text of the timer as written on the disk (None — no file)."""
    from .. import util
    from ..gw import sys as gsys
    return util.read_text(gsys.unit_path(TIMER))


def apply(runner, st, now=None, here=None, inside=False):
    """See the head. -> {"applied": bool, "armed": bool (a timer is wanted), "why"?, "warn"?}. Never raises.
    Not armed on this machine — the repeats still waiting are stopped too (``stop_retries``, SEC С-1). ``inside`` —
    the call of the service of the timer itself (``sched_run._redraw``): only the timer is removed, never a stop of
    its own unit (SEC П-1); the file of the service goes with the next ``apply`` from outside."""
    from ..gw import model
    from ..gw import sys as gsys
    from . import machine
    try:
        text, warn, _lines = wanted(st, now, here)
        armed = bool(model.bench(st, here=machine.mid() if here is None else here).get("armed"))
    except Exception as e:                           # noqa: BLE001 — a broken state never stops the save of it
        return {"applied": False, "armed": False, "why": type(e).__name__}
    out = {"applied": True, "armed": text is not None}
    if warn:
        out["warn"] = warn
    try:
        if not armed:
            stop_retries(runner)
        if text is None:
            for name in ((TIMER,) if inside else UNITS):
                gsys.remove_unit(runner, name)
            return out
        written = gsys.write_unit(SERVICE, service_text())
        r = gsys.ensure_unit(runner, TIMER, text)
        if written and not r["written"]:
            gsys.systemd(runner, "daemon-reload")
    except (KaskadError, OSError) as e:
        out.update(applied=False, why=_short(e))
    return out


def _short(e):
    s = str(e).splitlines()[0] if str(e) else type(e).__name__
    return s[:120]


def refresh_text():
    """The text of the timer for ``unit_refresh`` (the update of Kaskad): from the saved state; None — no timer is
    wanted (the update removes the file, REV З-4). A state that cannot be read — the text on the disk (nothing
    changes: an update never disarms a working schedule by a read error)."""
    from .. import state
    try:
        return wanted(state.load())[0]
    except Exception:                                # noqa: BLE001
        return current_timer()


def after_mark(st):
    """``marks.AFTER``: a mark of the first manual check or of the consent changed — arm (or disarm) the timer by
    the state just saved (SEC П-5: outside the lock of state.json). Never raises (a mark never stops a check)."""
    from .. import util
    runner = system_runner()
    if runner is None:
        return
    try:
        r = apply(runner, st)
        if not r.get("applied"):
            util.log_tr("bench.log.sched_not_applied", why=r.get("why") or "-")
    except Exception:                                # noqa: BLE001
        pass


RUNNER = [None]                                      # tests put a FakeRunner here


def system_runner():
    """The runner of the callers that have none (a mark, the zone of the panel): ``RUNNER[0]`` of a test, else the
    system one — for root only (a process without the right to write units — an offline test — calls nothing)."""
    import os
    from .. import sysrun
    if RUNNER[0] is not None:
        return RUNNER[0]
    return sysrun.default_runner() if hasattr(os, "geteuid") and os.geteuid() == 0 else None


# ── the repeat after a sharp drop (§2.8 п. 3) ─────────────────────────────

def retry_unit(rid):
    """The name of the transient timer of the repeat of check ``rid`` — only from an id by ``fullmatch``."""
    if not (isinstance(rid, str) and _ID.fullmatch(rid)):
        raise KaskadError("bad check id")
    return RETRY_UNIT % rid


def retry_delay(rng=None):
    """The seconds of the delay of a repeat: 60–120 min (``SystemRandom``; tests give their own ``rng``)."""
    import random
    return int((rng or random.SystemRandom()).randint(RETRY_MIN_S, RETRY_MAX_S))


def stop_retries(runner):
    """Stop the transient repeats still waiting (``kaskad-bench-retry-*``: the timer and its service — the wait for
    quiet) and forget them in sched-state.json. Never raises."""
    from . import sched_run
    try:
        runner.run(["systemctl", "stop", RETRY_UNIT % "*.timer", RETRY_UNIT % "*.service"], timeout=30)
    except Exception:                                # noqa: BLE001 — at the firing the repeat checks the switches again
        pass
    try:
        sched_run.clear_pending()
    except OSError:
        pass


def schedule_retry(runner, rid, rng=None, s=None, links=()):
    """``systemd-run --on-active=<60–120 min> --unit=kaskad-bench-retry-<id>`` → ``kaskadctl host bench --retry <id>
    --links <the links that fell>``. The seconds — a number of ``random`` (or ``s`` of ``retry_delay``), the id — by
    ``fullmatch``, the links — words of ``bench.suite.LINKS`` only: nothing of a request in the command (NS-S7). ``Type=oneshot``: ``TimeoutStartSec`` bounds the whole run (SEC П-3; a simple service would
    count as started at once). -> the seconds. KaskadError — a bad id or systemd-run failed."""
    from . import suite
    unit = retry_unit(rid)
    if not links or isinstance(links, str) or any(x not in suite.LINKS for x in links):
        raise KaskadError("bad links")            # the repeat refuses a timer without its links (REV-ns-join З-1)
    s = retry_delay(rng) if s is None else int(s)
    if not RETRY_MIN_S <= s <= RETRY_MAX_S:
        raise KaskadError("bad delay")
    code = ("import runpy, sys; sys.path.insert(0, '%s'); "
            "runpy.run_module('kaskad', run_name='__main__', alter_sys=True)" % paths.lib_current())
    argv = ["systemd-run", "--on-active=%d" % s, "--unit=%s" % unit, "--collect", "--quiet",
            "--service-type=oneshot", "-p", "Nice=10", "-p", "TimeoutStartSec=90min",
            PYTHON, "-I", "-c", code, "host", "bench", "--retry", rid, "--links", ",".join(sorted(set(links)))]
    res = runner.run(argv, timeout=30)
    if not res.ok:
        raise KaskadError("systemd-run: %s" % ((res.err or "").strip().splitlines() or ["?"])[0][:120])
    return s
