"""System helpers of the gateway (spec §3 «Общие правила установки пакетов», §7 ``gw/sys.py``).

    apt_install(runner, pkgs, mask=())   apt-get install under a temporary /usr/sbin/policy-rc.d that
                                         returns 101: distribution services do NOT start on install
                                         (strongSwan would take UDP 500/4500, dnsmasq port 53); then the
                                         packaged units of ``mask`` are ``disable --now`` + ``mask``. apt busy
                                         (fresh server: unattended-upgrades) -> waits up to 10 min; one install
                                         of Kaskad at a time; SIGHUP/SIGTERM never leave policy-rc.d behind
                                         (aptlock)
    write_unit(name, text)               /etc/systemd/system/<name> if changed -> bool (caller: daemon-reload)
    remove_unit(runner, name)            disable --now, delete, daemon-reload
    systemd(runner, verb, unit=None)     systemctl <verb> [unit]; KaskadError on failure
    enable(runner, unit, now=False)      systemctl enable --no-reload [--now] unit; daemon-reload only when it
                                         made a link (T-M6-qs330b №14: a bare enable reloads systemd every time)
    unit_active / unit_enabled
    ufw_active / ufw_allow / ufw_delete / ufw_rules   only when UFW is active; rules carry a comment
    modprobe(runner, mods)               -> modules that failed to load
    sysctl_set(runner, key, value)       sysctl -w + /etc/sysctl.d/90-kaskad-gw.conf
    networkd_keep_foreign()              systemd-networkd's drop-in 90-kaskad.conf: its restarts leave our
    networkd_drop()                      ip rules and routes alone (written / removed; never a restart)
    hold() / held                        a change of the gateway is finished, not cut (F2, finding 8):
                                         SIGHUP and SIGINT ignored (the commands it runs inherit that),
                                         SIGTERM only remembered; a closed terminal loses text, not the change

Every command goes through ``runner`` (argv lists). Nothing raises for "not installed" cases where the
spec says to skip (no ufw -> nothing to open).
"""

import contextlib
import functools
import json
import os
import re
import signal
import sys
import threading

from .. import aptlock, paths, util
from ..errors import KaskadError
from ..i18n import tr
from ..migrate import interrupt

POLICY_RC_D = aptlock.POLICY_RC_D          # aptlock.POLICY_MARK inside: the watchdog knows it is ours
SYSCTL_FILE = "/etc/sysctl.d/90-kaskad-gw.conf"
APT_TIMEOUT = aptlock.RUN_TIMEOUT          # longer than apt's own lock wait (DPkg::Lock::Timeout)
# systemd-networkd removes, on every restart (each upgrade of the systemd package — unattended-upgrades,
# four times in the first half hour of a fresh Ubuntu 26.04), every ip rule it did not create and the
# routes without a device: fwmark 256+N of the egress legs, their «unreachable» backstop, fwmark 0x200 of
# the gateway. With these two keys a restart keeps them and the node's own network stays as it was
# (stand, 2026-10-04). install.sh writes the SAME text (K3_NETWORKD_TEXT; a test compares them).
NETWORKD_DROPIN = (
    "# Kaskad: systemd-networkd не трогает чужие правила маршрутизации и маршруты (файл пишет gokaskad).\n"  # i18n: skip — a system file, install.sh writes the same text (K3_NETWORKD_TEXT)
    "# Иначе каждый его перезапуск (обновление пакета systemd) стирает ip rule Kaskad — fwmark 256+N\n"
    "# ног AmneziaWG «Прокси для API Telegram» и fwmark 0x200 шлюза — и выходы молча перестают работать.\n"
    "# Основную сеть сервера настройка не меняет. Удаляется вместе с Kaskad.\n"
    "[Network]\n"
    "ManageForeignRoutingPolicyRules=no\n"
    "ManageForeignRoutes=no\n")
UFW_COMMENT = "kaskad-gw"
_PKG = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,63}$")
_UNIT = re.compile(r"^[A-Za-z0-9@_.:-]{1,128}\.(service|timer|socket|path|target)$")
_SYSCTL = re.compile(r"^[a-z0-9_.-]{1,128}$")


def _short(res):
    lines = [ln.strip() for ln in (res.err or res.out or "").splitlines() if ln.strip()]
    return util.redact(" | ".join(lines[-3:]) if lines else tr("gw_sys.rc", rc=res.rc))


# ── packages ───────────────────────────────────────────────────────────────

def pkg_installed(runner, pkg):
    res = runner.run(["dpkg-query", "-W", "-f=${Status}", pkg])
    return res.ok and res.out.strip().endswith("install ok installed")


def _policy_on():
    """Create policy-rc.d (101) unless one exists (Docker images ship their own). True if ours."""
    p = paths.policy_rc_d()
    if os.path.lexists(p):
        return False
    util.atomic_write(p, POLICY_RC_D, 0o755)
    return True


def _policy_off(ours):
    if not ours:
        return
    p = paths.policy_rc_d()
    try:
        if aptlock.policy_is_ours(util.read_text(p)):
            os.unlink(p)
    except OSError:
        pass


def _wait(want):
    """Wait while the system holds apt (aptlock.wait — a line on stderr; KaskadError after 10 minutes naming
    the holder)."""
    try:
        aptlock.wait()
    except interrupt.Interrupted:
        raise
    except KaskadError as e:
        raise KaskadError(tr("gw_sys.apt.not_installed", pkgs=" ".join(want), why=e), data={"kind": "apt_busy"})


def apt_install(runner, pkgs, mask=(), timeout=APT_TIMEOUT):
    """Install the missing packages without starting their services; mask the given packaged units.
    Returns the packages that were installed. Raises KaskadError if apt-get fails or stays busy.

    One install of Kaskad at a time (aptlock.installing — the menu and the bot: the first would take
    policy-rc.d away in the middle of the second's apt). A busy apt is waited for BEFORE policy-rc.d appears
    (a SIGHUP there just stops). From policy-rc.d on, SIGHUP/SIGTERM (an SSH session drops) are only
    remembered: apt-get is let finish (no retry), policy-rc.d goes, then the install stops with
    ``interrupt.Interrupted``. What a SIGKILL or a power cut leaves, the minute watchdog removes
    (aptlock.policy_cleanup)."""
    for p in pkgs:
        if not _PKG.match(p):
            raise KaskadError(tr("gw_sys.bad.package", name=repr(p)))
    want = [p for p in pkgs if not pkg_installed(runner, p)]
    if want:
        argv = ["env", "DEBIAN_FRONTEND=noninteractive", "NEEDRESTART_SUSPEND=1", "apt-get", "install", "-y", "-q",
                "--no-install-recommends"] + aptlock.OPTS + want
        tolerate_output()                          # F2: a wait line to a closed terminal stops nothing
        hup_ignored = _ignored("SIGHUP")           # F2: the menu (install.sh) or hold() ignores the hangup
        with aptlock.installing(), interrupt.guard():
            if hup_ignored:
                _ignore("SIGHUP")                  # ... and so do apt-get and dpkg: an SSH drop never cuts them
            _wait(want)
            with interrupt.deferred():
                ours = _policy_on()
                try:
                    res = runner.run(argv, timeout=timeout)
                    if not res.ok and not interrupt.fired():
                        if aptlock.failure_text(res) is None:
                            # stale package lists (fresh VPS images): update once and retry
                            _wait(want)
                            runner.run(["env", "DEBIAN_FRONTEND=noninteractive", "NEEDRESTART_SUSPEND=1", "apt-get", "update", "-q"]
                                       + aptlock.OPTS, timeout=timeout)
                        _wait(want)                        # (after a lock failure: wait again, once)
                        if not interrupt.fired():
                            res = runner.run(argv, timeout=timeout)
                finally:
                    _policy_off(ours)
            if interrupt.fired():
                raise interrupt.Interrupted(tr("gw_sys.apt.interrupted", signal=interrupt.fired(),
                                               pkgs=" ".join(want)))
        if not res.ok:
            busy = aptlock.failure_text(res)
            if busy:
                raise KaskadError(tr("gw_sys.apt.not_installed", pkgs=" ".join(want), why=busy),
                                  data={"kind": "apt_busy"})
            raise KaskadError(tr("gw_sys.apt.failed", pkgs=" ".join(want), why=_short(res)), data={"kind": "packages"})
    for unit in mask:
        mask_unit(runner, unit)
    return want


def mask_unit(runner, unit):
    """``disable --now`` + ``mask`` a packaged unit we do not use (only if it exists)."""
    if not _UNIT.match(unit):
        raise KaskadError(tr("gw_sys.bad.unit", name=repr(unit)))
    st = runner.run(["systemctl", "is-enabled", unit]).out.strip()
    if st in ("", "masked") and not unit_active(runner, unit):
        return False
    runner.run(["systemctl", "disable", "--now", unit])
    runner.run(["systemctl", "mask", unit])
    return True


# ── systemd ────────────────────────────────────────────────────────────────

def systemd(runner, verb, unit=None, check=True, no_block=False):
    """``systemctl <verb> [--no-block] [unit]``; ``no_block`` only queues the job (never waits on ordering)."""
    argv = ["systemctl", verb] + (["--no-block"] if no_block else []) + ([unit] if unit else [])
    if unit and not _UNIT.match(unit):
        raise KaskadError(tr("gw_sys.bad.unit", name=repr(unit)))
    res = runner.run(argv)
    if check and not res.ok:
        raise KaskadError("systemctl %s%s: %s" % (verb, (" " + unit) if unit else "", _short(res)))
    return res


def unit_active(runner, unit):
    return runner.run(["systemctl", "is-active", unit]).out.strip() == "active"


STOPPED_STATES = ("inactive", "failed")


def unit_stopped(runner, unit):
    """The unit is KNOWN to be stopped: ``systemctl show -p ActiveState --value`` answered ``inactive`` or ``failed``
    (REV-s1-b6-sec Н-1). Anything else — ``activating``, ``deactivating``, ``reloading``, an empty answer of an
    unreachable bus, a timeout — is NOT «stopped»: the caller that closes something must not say it closed."""
    res = runner.run(["systemctl", "show", "-p", "ActiveState", "--value", unit])
    return res.ok and (res.out or "").strip() in STOPPED_STATES


def unit_enabled(runner, unit):
    res = runner.run(["systemctl", "is-enabled", unit])
    return res.ok and res.out.strip() not in ("", "disabled", "masked")


def unit_path(name):
    return os.path.join(paths.systemd_dir(), name)


def write_unit(name, text):
    """Write the unit file if its text changed. Returns True if written (then run daemon-reload)."""
    if not _UNIT.match(name):
        raise KaskadError(tr("gw_sys.bad.unit", name=repr(name)))
    p = unit_path(name)
    if util.read_text(p) == text:
        return False
    try:
        util.atomic_write(p, text, 0o644)
    except OSError as e:
        raise KaskadError(tr("gw_sys.write_failed", path=p, why=e))
    return True


def links_changed(res):
    """Did ``systemctl enable|disable`` create or remove a link (its «Created symlink …» / «Removed …»)?"""
    text = (res.err or "") + "\n" + (res.out or "")
    return "Created symlink" in text or re.search(r"(?m)^Removed ", text) is not None


def enable(runner, unit, now=False, check=True, reload=True):
    """``systemctl enable --no-reload [--now] unit`` — then ``daemon-reload`` only when the enable made a link
    (``reload``; False — the caller reloads anyway). A plain ``systemctl enable`` reloads the whole manager every
    time, even when the unit is enabled already: every ``gw apply`` (five units) and every leg left «Reloading…» in
    the journal — 562 times in 1.5 h (T-M6-qs330b №14). Units are read on demand: a unit never loaded starts without
    a reload; a CHANGED unit file is reloaded by its writer (``write_unit`` -> True). -> (Result, links changed)."""
    if not _UNIT.match(unit):
        raise KaskadError(tr("gw_sys.bad.unit", name=repr(unit)))
    res = runner.run(["systemctl", "enable", "--no-reload"] + (["--now"] if now else []) + [unit])
    if check and not res.ok:
        raise KaskadError("systemctl enable %s: %s" % (unit, _short(res)))
    changed = res.ok and links_changed(res)
    if changed and reload:
        runner.run(["systemctl", "daemon-reload"])
    return res, changed


# Lines of a unit whose change needs no restart of the service: daemon-reload is enough (ExecReload= is taken at the
# next reload, Description= is only a caption — 3.3.2 №3: the Russian captions became English, an update must not
# restart xray, dnsmasq, the panel… for that). The same rule as gw/inbounds/tunnel.unit_needs_restart (its own copy:
# the tests of the inbounds stub this module).
_SOFT_UNIT = re.compile(r"^(ExecReload|Description)=")


def unit_needs_restart(old, new):
    """A rewritten unit needs a restart unless only soft lines (ExecReload=, Description=) changed. No old text
    (unit just installed) -> True."""
    if old is None or new is None:
        return True

    def hard(text):
        return [ln.rstrip() for ln in text.splitlines() if ln.strip() and not _SOFT_UNIT.match(ln.strip())]
    return hard(old) != hard(new)


def ensure_unit(runner, name, text, restart=False):
    """write_unit + enable (no reload of its own) + daemon-reload if the file or its links changed — one reload at
    most, none when nothing changed; start if not running, restart if ``restart`` or the unit changed in more than
    its soft lines (``unit_needs_restart``: a new Description= alone — daemon-reload, no restart). Not active and
    not KNOWN stopped (``activating``) with ``restart`` — restart, not start (a start under way read the old files).
    Returns {"written", "started", "restarted"}."""
    old = util.read_text(unit_path(name)) if _UNIT.match(name) else None
    written = write_unit(name, text)
    _, linked = enable(runner, name, reload=False)
    if written or linked:
        systemd(runner, "daemon-reload")
    out = {"written": written, "started": False, "restarted": False}
    if not unit_active(runner, name):
        if restart and not unit_stopped(runner, name):
            # REV-s1-b6-sec П-1 (B7 круг 2): «activating» — a start under way (the watchdog, Restart=always) may have
            # read the old file already (LoadCredential); a ``start`` would only join it: restart, the file read anew
            systemd(runner, "restart", name)
            out["restarted"] = True
        else:
            systemd(runner, "start", name)
            out["started"] = True
    elif restart or (written and unit_needs_restart(old, text)):
        systemd(runner, "restart", name)
        out["restarted"] = True
    return out


def remove_unit(runner, name):
    """disable --now, delete the file, daemon-reload. Never fails if absent. True if a file was removed."""
    runner.run(["systemctl", "disable", "--now", name])
    p = unit_path(name)
    if os.path.lexists(p):
        try:
            os.unlink(p)
        except OSError:
            return False
        runner.run(["systemctl", "daemon-reload"])
        return True
    return False


# ── ufw ────────────────────────────────────────────────────────────────────

def ufw_active(runner):
    if not runner.which("ufw"):
        return False
    res = runner.run(["ufw", "status"])
    return res.ok and res.out.strip().splitlines()[:1] == ["Status: active"]


def _port_spec(port, proto):
    """«51820/udp»; a range «20000:20099» (one rule for Hysteria2 port hopping) — «20000:20099/udp»."""
    if isinstance(port, str) and ":" in port:
        lo, hi = (int(x) for x in port.split(":", 1))
        return "%d:%d/%s" % (lo, hi, proto)
    return "%d/%s" % (int(port), proto)


def _rule_spec(port, proto):
    if port is None:
        return ["proto", proto, "from", "any", "to", "any"]
    return [_port_spec(port, proto)]


def ufw_rules(runner):
    """Our rules in ``ufw status``: [(spec, comment)] where spec is "51820/udp" or "esp" (v6 twins skipped)."""
    res = runner.run(["ufw", "status"])
    out = []
    for line in (res.out if res.ok else "").splitlines():
        if "# " + UFW_COMMENT not in line or "(v6)" in line:
            continue
        head, _, comment = line.partition("# ")
        tok = head.split()
        if not tok:
            continue
        out.append((tok[0], comment.strip()))       # "51820/udp"; proto-only rules print "Anywhere/esp"
    return out


def ufw_allow(runner, port, proto, comment):
    """``ufw allow <port>/<proto> comment '<comment>'`` (or ``proto esp`` without port) — only if UFW is
    active. Returns True if a rule was added."""
    if not ufw_active(runner):
        return False
    c = "%s %s" % (UFW_COMMENT, comment) if not comment.startswith(UFW_COMMENT) else comment
    res = runner.run(["ufw", "allow"] + _rule_spec(port, proto) + ["comment", c])
    if not res.ok:
        raise KaskadError(tr("gw_sys.ufw_failed", rule=" ".join(_rule_spec(port, proto)), why=_short(res)))
    return True


def ufw_delete(runner, port, proto, comment=None):
    """``ufw delete allow <port>/<proto>`` — only if UFW is active and the rule is ours (comment
    kaskad-gw...). Returns True if deleted."""
    if not ufw_active(runner):
        return False
    spec = _port_spec(port, proto) if port is not None else proto
    ours = [c for s, c in ufw_rules(runner) if s == spec or s.endswith("/" + spec)]
    if comment is not None:
        want = "%s %s" % (UFW_COMMENT, comment) if not comment.startswith(UFW_COMMENT) else comment
        ours = [c for c in ours if c == want]
    if not ours:
        return False
    return runner.run(["ufw", "delete", "allow"] + _rule_spec(port, proto)).ok


# ── kernel ─────────────────────────────────────────────────────────────────

def modprobe(runner, mods):
    """Load kernel modules; returns the ones that failed."""
    failed = []
    for m in mods:
        if not re.match(r"^[a-z0-9_-]{1,64}$", m):
            raise KaskadError(tr("gw_sys.bad.module", name=repr(m)))
        if not runner.run(["modprobe", m]).ok:
            failed.append(m)
    return failed


def sysctl_get(runner, key):
    res = runner.run(["sysctl", "-n", key])
    return res.out.strip() if res.ok else None


def sysctl_set(runner, key, value, persist=True):
    """``sysctl -w key=value`` (+ the line in /etc/sysctl.d/90-kaskad-gw.conf). True if the live value changed."""
    if not _SYSCTL.match(key) or not re.match(r"^[0-9A-Za-z_ .-]{1,64}$", str(value)):
        raise KaskadError(tr("gw_sys.bad.sysctl", name=repr(key)))
    value = str(value)
    changed = False
    if sysctl_get(runner, key) != value:
        res = runner.run(["sysctl", "-w", "%s=%s" % (key, value)])
        if not res.ok:
            raise KaskadError("sysctl %s=%s: %s" % (key, value, _short(res)))
        changed = True
    if persist:
        p = paths.P(SYSCTL_FILE)
        lines = [ln for ln in (util.read_text(p) or "").splitlines()
                 if ln.strip() and not ln.startswith("#") and ln.split("=", 1)[0].strip() != key]
        head = "# Kaskad 3: шлюз\n"                 # i18n: skip — the header of a system file, not an interface
        text = head + "\n".join(sorted(lines + ["%s=%s" % (key, value)])) + "\n"
        if util.read_text(p) != text:
            util.atomic_write(p, text, 0o644)
    return changed


# ── systemd-networkd ───────────────────────────────────────────────────────

def networkd_installed():
    return any(os.path.isfile(p) for p in paths.networkd_bins())


def networkd_keep_foreign():
    """Write /etc/systemd/networkd.conf.d/90-kaskad.conf (NETWORKD_DROPIN, 0644; a new directory 0755 —
    networkd reads it as user systemd-network) when systemd-networkd is installed and the file is missing or
    different. networkd is NEVER restarted or reloaded here — a restart is exactly what removes the rules;
    its next start reads the file. Returns True if written; never raises (a read-only /etc must not stop
    the gateway)."""
    if not networkd_installed():
        return False
    p = paths.networkd_dropin()
    if util.read_text(p) == NETWORKD_DROPIN:
        return False
    d = os.path.dirname(p)
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
            os.chmod(d, 0o755)
        util.atomic_write(p, NETWORKD_DROPIN, 0o644)
    except OSError:
        return False
    util.log_tr("gw_sys.log.networkd_written", path=p)
    return True


def networkd_drop():
    """Remove our drop-in (the caller made sure no policy routing of Kaskad is left). True if removed."""
    p = paths.networkd_dropin()
    if not os.path.lexists(p):
        return False
    try:
        os.unlink(p)
    except OSError:
        return False
    util.log_tr("gw_sys.log.networkd_removed", path=p)
    return True


# ── changes are finished, not cut (F2, finding 8) ──────────────────────────
#
# kaskadctl runs from the menu in the SSH session: the connection drops (SIGHUP to the whole process group — the
# menu, kaskadctl and every command it runs), Ctrl+C (SIGINT, the same group), ``kill`` (SIGTERM). Cut between
# two steps of ``gw enable``, the gateway part was in the table and in kaskad.nft, the ip rules and the inbounds
# were up, and state.json still said «off» — the watchdog left it alone and the next ``fwd add`` rewrote
# kaskad.nft without it. Under ``hold()`` (the state lock taken, a change of the gateway running) SIGHUP and
# SIGINT are ignored — SIG_IGN, so the commands it starts (systemctl, nft, ip ...) inherit it and are not killed
# either — and SIGTERM is only remembered; the change runs to its end and state.json is saved, then the old
# handlers come back (a SIGTERM is written to the log). Writes to stdout/stderr that fail (EIO of a terminal that
# is gone, EPIPE of a reader that is gone) are dropped. Only the main thread installs handlers (the panel and the
# bot run changes in their worker threads: nothing changes there). Nested holds are one hold.

_HOLD = {"depth": 0, "saved": {}, "term": None}
_HOLD_SIGNALS = ("SIGHUP", "SIGINT", "SIGTERM")


class _Tolerant(object):
    """sys.stdout / sys.stderr that never raise on a dead terminal or pipe: the first failing write marks the stream
    broken, later text is dropped. Everything else goes to the wrapped stream."""

    def __init__(self, stream):
        self._stream = stream
        self.broken = False

    def write(self, text):
        if not self.broken:
            try:
                return self._stream.write(text)
            except (OSError, ValueError):          # EIO, EPIPE; ValueError: the stream was closed
                self.broken = True
        return len(text)

    def flush(self):
        if not self.broken:
            try:
                self._stream.flush()
            except (OSError, ValueError):
                self.broken = True

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _main_thread():
    return threading.current_thread() is threading.main_thread()


def tolerate_output():
    """stdout and stderr of this process wrapped in ``_Tolerant`` (once; main thread only). They stay wrapped: the
    result of a finished change is printed after the change, maybe to a terminal that is already gone."""
    if not _main_thread():
        return
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s is not None and not isinstance(s, _Tolerant):
            setattr(sys, name, _Tolerant(s))


def _ignored(name):
    sig = getattr(signal, name, None)
    try:
        return sig is not None and signal.getsignal(sig) == signal.SIG_IGN
    except (ValueError, OSError):
        return False


def _ignore(name):
    sig = getattr(signal, name, None)
    if sig is not None and _main_thread():
        try:
            signal.signal(sig, signal.SIG_IGN)
        except (ValueError, OSError):
            pass


def _on_term(signum, frame):
    _HOLD["term"] = _HOLD["term"] or "SIGTERM"


@contextlib.contextmanager
def hold():
    """The block (a change of the gateway) runs to its end whatever SIGHUP, SIGINT or SIGTERM come (module notes
    above). Re-entrant; outside the main thread it changes nothing."""
    if not _main_thread():
        yield
        return
    outer = _HOLD["depth"] == 0
    _HOLD["depth"] += 1
    if outer:
        _HOLD["term"] = None
        _HOLD["saved"] = {}
        for name in _HOLD_SIGNALS:
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                _HOLD["saved"][sig] = signal.signal(sig, _on_term if name == "SIGTERM" else signal.SIG_IGN)
            except (ValueError, OSError):
                pass
        tolerate_output()
    try:
        yield
    finally:
        _HOLD["depth"] -= 1
        if outer:
            for sig, h in _HOLD["saved"].items():
                try:
                    signal.signal(sig, h if h is not None else signal.SIG_DFL)
                except (ValueError, OSError, TypeError):
                    pass
            _HOLD["saved"] = {}
            if _HOLD["term"]:
                util.log_tr("gw_sys.log.hold_signal", signal=_HOLD["term"])


def held(fn):
    """``hold()`` around a whole function (a change with steps after the state lock: inbound uninstall, purge)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with hold():
            return fn(*args, **kwargs)
    return wrapper


# ── our daemons on a binary apt replaced (F2, finding 9) ───────────────────
#
# Debian has no needrestart: apt upgrades strongSwan, OpenVPN, ocserv or dnsmasq, their postinst restarts the
# packaged unit (masked, not ours) and kaskad-gw-* / kaskad-dns keep running the deleted binary until a reboot.
# The watchdog (``gw check --repair``) finds our units whose /proc/<MainPID>/exe reads «<path> (deleted)» and
# try-restarts each once per update — remembered as (path, mtime of the new file) in /run/kaskad/gw.

STALE_STAMP = "exe-restarts.json"
_DELETED = " (deleted)"


def our_units():
    """kaskad-dns.service and every kaskad-gw-*.service on disk."""
    try:
        names = sorted(os.listdir(paths.systemd_dir()))
    except OSError:
        return []
    return [n for n in names if n == "kaskad-dns.service" or (n.startswith("kaskad-gw-") and n.endswith(".service"))]


def main_pids(runner, units):
    """{unit: MainPID} — one ``systemctl show -p MainPID --value`` for all of them (0: not running)."""
    if not units:
        return {}
    res = runner.run(["systemctl", "show", "-p", "MainPID", "--value"] + list(units))
    vals = [ln.strip() for ln in (res.out or "").splitlines() if ln.strip()] if res.ok else []
    if len(vals) != len(units):
        return {}
    return dict((u, int(v) if v.isdigit() else 0) for u, v in zip(units, vals))


def deleted_exe(pid):
    """The path of the binary process ``pid`` runs when that file was deleted (replaced), else None."""
    try:
        target = os.readlink(paths.P("/proc/%d/exe" % int(pid)))
    except (OSError, ValueError):
        return None
    return target[:-len(_DELETED)] if target.endswith(_DELETED) else None


def stale_daemons(runner):
    """[(unit, pid, path)] of our running units whose binary is gone from disk."""
    units = our_units()
    pids = main_pids(runner, units)
    out = []
    for u in units:
        pid = pids.get(u) or 0
        path = deleted_exe(pid) if pid > 0 else None
        if path:
            out.append((u, pid, path))
    return out


def _mtime(path):
    try:
        return int(os.stat(paths.P(path)).st_mtime_ns)
    except OSError:
        return None


def stale_check(runner, repair=False, log=None):
    """``gw check``: our daemons on a deleted binary — a problem ``gw_binary`` each; with ``repair`` a
    ``systemctl try-restart --no-block`` and a log line, once per unit per update (a binary that stays deleted after
    that is left alone until the next update: a restart loop would only cut the sessions again and again)."""
    found = stale_daemons(runner)
    if not found:
        return []
    log = log or (lambda kind, text: util.log_action(text))
    stamp_path = os.path.join(paths.gw_run_dir(), STALE_STAMP)
    stamp = _read_dict(stamp_path)
    probs, dirty = [], False
    for unit, pid, path in found:
        detail = tr("gw_sys.stale.detail", unit=unit, path=path)
        key = [path, _mtime(path)]
        if repair and stamp.get(unit) == key:
            continue                                   # restarted after this update already
        p = {"kind": "gw_binary", "detail": detail, "repaired": False}
        probs.append(p)
        if not repair:
            continue
        stamp[unit] = key
        dirty = True
        res = runner.run(["systemctl", "try-restart", "--no-block", unit])
        if res.ok:
            p["repaired"] = True
            with util.journal_lang():                    # the journal speaks the language of the server
                log("gw_binary", tr("gw_sys.log.restarted", detail=tr("gw_sys.stale.detail", unit=unit, path=path)))
        else:
            p["error"] = _short(res)
    if dirty:
        try:
            util.atomic_write(stamp_path, util.json_text(stamp), 0o600)
        except OSError:
            pass
    return probs


def _read_dict(path):
    """A small JSON dict of ours (/run), {} when missing or broken."""
    raw = util.read_text(path)
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    return doc if isinstance(doc, dict) else {}
