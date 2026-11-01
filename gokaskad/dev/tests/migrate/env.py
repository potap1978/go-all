"""Test environment for the migration: FakeRunner + FakeIptables + fakes of the WP1 contracts
(nft / compat / units, patched with unittest.mock) + system commands (systemctl, ss, ip, ufw,
netfilter-persistent, apt-get). Every fake appends to one ``events`` list so tests can check the
order of steps.
"""

import copy
import os
from unittest import mock

from kaskad import nft, sysrun, units
from kaskad.errors import KaskadError
from kaskad.sysrun import FakeRunner
from tests.fakes_ipt import FakeIptables

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "iptables")

FAKE_TOKEN = "1234567890:AAHfakeFAKEfakeFAKEfakeFAKEfake12345"
FAKE_PAT = "ghp_FAKEfakeFAKEfakeFAKEfakeFAKEfake1234"
FAKE_VLESS_ID = "11111111-2222-3333-4444-555555555555"
FAKE_AWG = "eyJmYWtlIjoiRkFLRS1BV0ctS0VZLVNFQ1JFVCJ9"
SECRETS = (FAKE_TOKEN, FAKE_PAT, FAKE_VLESS_ID, FAKE_AWG, "AAHfakeFAKE")

ROUTE = "8.8.8.8 via 203.0.113.1 dev eth0 src 203.0.113.10 uid 0 \n    cache \n"


def fixture(scenario, name):
    p = os.path.join(FIX, scenario, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def fixture_bytes(scenario, name):
    with open(os.path.join(FIX, scenario, name), "rb") as f:
        return f.read()


class FakeNft(object):
    def __init__(self, events, supported=True, reason=""):
        self.events = events
        self.supported = supported
        self.reason = reason
        self.table = None
        self.fail_apply = None          # exception instance -> raised by apply
        self.fail_delete = None
        self.corrupt = None             # fn(table_copy) applied to read_live results
        self.after_apply = None         # fn() called after a successful apply
        self.applied = []

    def probe_support(self, runner):
        return {"supported": self.supported, "reason": self.reason, "nft": "1.0.6",
                "kernel": "6.1.0-26-amd64", "backend": "nf_tables", "firewalld": False}

    def apply(self, st, runner, write_file=True):
        keys = tuple("%s:%d" % (f["proto"], f["port"]) for f in st["forwards"])
        self.events.append(("nft.apply", keys))
        if self.fail_apply is not None:
            raise self.fail_apply
        fw = dict(((f["proto"], int(f["port"])), (f["to"], int(f["to_port"]))) for f in st["forwards"])
        old = (self.table or {}).get("counters", {})
        counters = dict((nft.counter_name(p, n), old.get(nft.counter_name(p, n), {"packets": 0, "bytes": 0}))
                        for (p, n) in fw)
        self.table = {"forwards": fw, "counters": counters, "has_jump": True, "has_masquerade": True,
                      "chains": ["fwd_pre", "fwd_dnat", "fwd_count", "fwd_post"]}
        self.applied.append(copy.deepcopy(fw))
        if self.after_apply:
            self.after_apply()
        return {"ok": True, "added": list(keys), "removed": [], "changed": []}

    def read_live(self, runner):
        if self.table is None:
            return None
        t = copy.deepcopy(self.table)
        if self.corrupt:
            self.corrupt(t)
        return t

    def delete_table(self, runner):
        self.events.append(("nft.delete_table",))
        if self.fail_delete is not None:
            raise self.fail_delete
        self.table = None

    def patches(self):
        return [mock.patch("kaskad.nft.%s" % n, getattr(self, n))
                for n in ("probe_support", "apply", "read_live", "delete_table")]


class FakeCompat(object):
    ARGS = ["FORWARD", "-m", "connmark", "--mark", "0x4b000000/0xff000000",
            "-m", "comment", "--comment", "kaskad:compat", "-j", "ACCEPT"]

    def __init__(self, emu, events):
        self.emu = emu
        self.events = events
        self.fail_ensure = None

    def present(self, runner):
        try:
            self.emu.cmd("-C", *self.ARGS)
            return True
        except AssertionError:
            return False

    def ensure(self, runner):
        self.events.append(("compat.ensure",))
        if self.fail_ensure is not None:
            raise self.fail_ensure
        if self.present(runner):
            return False
        self.emu.cmd("-I", "FORWARD", "1", *self.ARGS[1:])
        return True

    def remove(self, runner):
        self.events.append(("compat.remove",))
        n = 0
        while self.present(runner):
            self.emu.cmd("-D", *self.ARGS)
            n += 1
        return n

    def patches(self):
        return [mock.patch("kaskad.compat.%s" % n, getattr(self, n)) for n in ("present", "ensure", "remove")]


class FakeUnits(object):
    def __init__(self, events):
        self.events = events
        self.installed = False
        self.fail_install = None
        self.broken_status = False

    def install(self, runner):
        self.events.append(("units.install",))
        if self.fail_install is not None:
            raise self.fail_install
        self.installed = True

    def remove(self, runner):
        self.events.append(("units.remove",))
        self.installed = False

    def activate(self, runner):
        # must run after the state lock is released (ExecStartPost of kaskad-net takes it)
        import fcntl, os
        from kaskad import paths
        fd = os.open(paths.lock(), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)      # raises if the caller still holds the lock
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        self.events.append(("units.activate",))
        return True

    def status(self, runner):
        on = self.installed and not self.broken_status
        return {units.NET_UNIT: {"enabled": on, "active": on},
                units.WATCH_SERVICE: {"enabled": on, "active": False},
                units.WATCH_TIMER: {"enabled": on, "active": on}}

    def patches(self):
        return [mock.patch("kaskad.units.%s" % n, getattr(self, n)) for n in ("install", "remove", "status", "activate")]


class Env(object):
    """One fake server. ``Env(case, "pro_213").start()`` inside a RootCase test."""

    def __init__(self, case, scenario=None, backend="nf_tables", nft_installed=True, supported=True,
                 ufw_active=False, services=None, netfilter_persistent=True, apt=True, files=True,
                 iptables=True):
        self.case = case
        self.scenario = scenario
        self.events = []
        bins = ["systemctl", "ss", "ip", "ip6tables-save"]
        if nft_installed:
            bins.append("nft")
        if netfilter_persistent:
            bins.append("netfilter-persistent")
        if apt:
            bins.append("apt-get")
        if ufw_active is not None and ufw_active is not False:
            bins.append("ufw")
        self.runner = FakeRunner(binaries=bins)
        self.ipt = FakeIptables(backend=backend, events=self.events)
        if iptables:
            self.ipt.install(self.runner)
            if scenario:
                self.ipt.load(fixture(scenario, "save.txt"))
                del self.events[:]                     # loading the fixture is not part of the test
        self.nft = FakeNft(self.events, supported=supported)
        self.compat = FakeCompat(self.ipt, self.events)
        self.units = FakeUnits(self.events)
        self.services = dict(services or {})
        self.ufw_active = ufw_active
        self.apt_fails = False
        self.apt_calls = []
        self.apt_env = []               # environment and stdin of every apt-get run through ``env``
        self.persist_calls = 0
        self.sleeps = []                # pauses the migration asked for (never really slept)
        self.on_sleep = None            # fn() run instead of the pause (a service finishing its restart)
        r = self.runner
        r.on(["systemctl", "is-enabled"], self._systemctl)
        r.on(["systemctl", "is-active"], self._systemctl)
        r.on(["ss", "-Hltnup"], fixture(scenario, "ss.txt") if scenario and fixture(scenario, "ss.txt") else
             'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=527,fd=3))\n')
        r.on(["ip", "route", "get", "8.8.8.8"], ROUTE)
        r.on(["ip", "rule", "show"], "0:\tfrom all lookup local\n32766:\tfrom all lookup main\n")
        r.on(["ip", "route", "show", "table", "all"], "default via 203.0.113.1 dev eth0\n")
        r.on(["ip6tables-save"], "# ip6tables\n*filter\n:INPUT ACCEPT [0:0]\nCOMMIT\n")
        r.on(["nft", "list", "ruleset"], self._nft_ruleset)
        r.on(["ufw", "status"], self._ufw)
        r.on(["netfilter-persistent", "save"], self._persist)
        r.on(["apt-get"], self._apt)
        r.on(["env"], self._env)
        if scenario and files:
            self.install_files()

    # ── fake commands ─────────────────────────────────────────────────────

    def _systemctl(self, argv, inp):
        unit = argv[2]
        en, ac = self.services.get(unit, (None, None))
        if unit in units.UNITS and unit not in self.services and self.units.installed:
            # the engine's own units, as FakeUnits installed them (the watch service is a static oneshot)
            en = "static" if unit == units.WATCH_SERVICE else "enabled"
            ac = "inactive" if unit == units.WATCH_SERVICE else "active"
        if argv[1] == "is-enabled":
            if en is None:
                return 1, "", "Failed to get unit file state for %s: No such file or directory\n" % unit
            return (0 if en == "enabled" else 1), en + "\n", ""
        ac = ac or "inactive"
        return (0 if ac == "active" else 3), ac + "\n", ""

    def _nft_ruleset(self, argv, inp):
        if self.nft.table is None:
            return 0, "", ""
        return 0, "table ip kaskad {\n}\n", ""

    def _ufw(self, argv, inp):
        if self.ufw_active:
            text = fixture(self.scenario, "ufw-status.txt") if self.scenario else None
            return 0, text or "Status: active\n", ""
        return 0, "Status: inactive\n", ""

    def _persist(self, argv, inp):
        self.persist_calls += 1
        p = self.case.path("/etc/iptables/rules.v4")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(self.ipt.save_text())
        return 0, "", ""

    def _env(self, argv, inp):
        """``env VAR=value ... apt-get ...``: only apt-get is expected behind env."""
        i, envs = 1, {}
        while i < len(argv) and "=" in argv[i] and not argv[i].startswith("-"):
            k, v = argv[i].split("=", 1)
            envs[k] = v
            i += 1
        if argv[i:i + 1] != ["apt-get"]:
            self.runner.unmatched.append(argv)
            return 127, "", "not mocked: %s" % " ".join(argv)
        self.apt_env.append((envs, inp))
        return self._apt(argv[i:], inp)

    def _apt(self, argv, inp):
        self.apt_calls.append(list(argv))
        if "install" in argv and argv[-1] == "nftables":
            if self.apt_fails:
                return 100, "", "E: Unable to locate package nftables\n"
            self.runner.binaries.add("nft")
            self.nft.supported = True
        return 0, "", ""

    def snapshot_state(self):
        """Everything a migration may touch, for 'nothing lost' comparisons."""
        def raw(p):
            try:
                with open(self.case.path(p), "rb") as f:
                    return f.read()
            except (OSError, IOError):
                return None
        return {"iptables": self.ipt.state(), "rules.v4": raw("/etc/iptables/rules.v4"),
                "nft": copy.deepcopy(self.nft.table), "compat": self.compat.present(None),
                "units": self.units.installed}

    # ── files of the scenario ─────────────────────────────────────────────

    def install_files(self):
        sc = self.scenario
        c = self.case
        for name, dest, mode in (("rules.v4", "/etc/iptables/rules.v4", 0o640),
                                 ("gokaskad", "/usr/local/bin/gokaskad", 0o755),
                                 ("aliases", "/etc/kaskad/aliases", 0o600),
                                 ("config", "/etc/kaskad/config", 0o600),
                                 ("upstream.txt", "/etc/kaskad/egress/upstream.txt", 0o600),
                                 ("default-ufw", "/etc/default/ufw", 0o644)):
            text = fixture(sc, name)
            if text is not None:
                c.write(dest, text, mode)
        for n in sorted(os.listdir(os.path.join(FIX, sc))):
            if n.startswith("monitor_") and n.endswith(".conf"):
                c.write("/etc/kaskad/monitors/%s" % n[len("monitor_"):], fixture(sc, n))
        if fixture(sc, "upstream.txt") is not None:
            c.write("/etc/kaskad/egress/xray", "ELF-binary-placeholder", 0o755)
            c.write("/etc/kaskad/egress/awg/amneziawg-go", "ELF-binary-placeholder", 0o755)

    def start(self):
        # every command must be mocked: checked after the test body for every environment
        self.case.addCleanup(lambda: self.case.assertEqual(self.runner.unmatched, [], "unmocked commands"))
        patches = self.nft.patches() + self.compat.patches() + self.units.patches()
        patches.append(mock.patch("kaskad.migrate.apply._sleep", self._sleep))
        for p in patches:
            p.start()
            self.case.addCleanup(p.stop)
        old = sysrun._default
        sysrun._default = self.runner
        self.case.addCleanup(setattr, sysrun, "_default", old)
        return self

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        if self.on_sleep:
            self.on_sleep()

    # ── helpers for assertions ────────────────────────────────────────────

    def names(self):
        return [e[0] for e in self.events]

    def index(self, name, start=0):
        for i in range(start, len(self.events)):
            if self.events[i][0] == name:
                return i
        return -1

    def restores(self, mode=None):
        return [e for e in self.events if e[0] == "iptables-restore" and (mode is None or e[2] == mode)]


def kaskad_error(msg="boom"):
    return KaskadError(msg)
