"""Stand-in of kaskad/gw/sys.py (WP4): packages without autostart, unit files, modules, UFW.

apt_install follows SPEC §3: a temporary /usr/sbin/policy-rc.d answering 101 while apt-get runs, so
the package services do not start; it is removed afterwards (unless it was there before).
As in the real core: modprobe returns the modules that failed (never raises); a UFW rule with port
None is a rule for a whole protocol (``ufw allow proto esp from any to any``).
"""

import os

from kaskad import paths, util
from kaskad.gw.inbounds.base import InboundError

POLICY_RC = "/usr/sbin/policy-rc.d"
POLICY_TEXT = "#!/bin/sh\n# Kaskad: package services do not start while packages are installed\nexit 101\n"


def apt_install(runner, pkgs, mask=()):
    p = paths.P(POLICY_RC)
    created = not os.path.exists(p)
    if created:
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            os.makedirs(d, 0o755)
        util.atomic_write(p, POLICY_TEXT, 0o755)
    try:
        res = runner.run(["apt-get", "install", "-y", "-q", "--no-install-recommends"] + list(pkgs), timeout=1800)
        if not res.ok:
            raise InboundError("apt-get install %s: код %d" % (" ".join(pkgs), res.rc))
    finally:
        if created and os.path.exists(p):
            os.unlink(p)


def write_unit(name, text):
    p = os.path.join(paths.systemd_dir(), name)
    if util.read_text(p) == text:
        return False
    util.atomic_write(p, text, 0o644)
    return True


def modprobe(runner, mods):
    failed = []
    for m in mods:
        if not runner.run(["modprobe", m]).ok:
            failed.append(m)
    return failed


def _ufw_active(runner):
    res = runner.run(["ufw", "status"])
    return res.ok and "Status: active" in res.out


def _spec(port, proto):
    if port is None:
        return ["proto", proto, "from", "any", "to", "any"]
    return ["%d/%s" % (port, proto)]


def ufw_allow(runner, port, proto, comment):
    if not _ufw_active(runner):
        return False
    runner.run(["ufw", "allow"] + _spec(port, proto) + ["comment", comment])
    return True


def ufw_delete(runner, port, proto, comment=None):
    if not _ufw_active(runner):
        return False
    runner.run(["ufw", "delete", "allow"] + _spec(port, proto))
    return True
