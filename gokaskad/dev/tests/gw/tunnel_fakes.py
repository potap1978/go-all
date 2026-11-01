"""A fake server for the tunnel inbound tests, wired into one FakeRunner by ``Node``:

    systemctl          units with enabled/active/masked; package units (openvpn.service, ocserv.service)
                       exist without files; start of a masked unit fails like systemd does
    dpkg-query/apt-get installed packages; apt-get records whether /usr/sbin/policy-rc.d answered 101
    modprobe/modinfo, uname -m, ufw (status, allow, delete)
    wg / awg           syncconf/setconf (the config text is recorded), show <if> latest-handshakes|transfer,
                       --version of the AmneziaWG core
    openssl            genpkey / req / x509 -req write files where -out says (certificates are the real
                       ones of tests/fixtures/gw/tunnel), passwd -6 -salt S -stdin
    openvpn --genkey secret <file>, occtl (show users JSON, disconnect user)

Every command the code runs must be one of these: anything else lands in ``runner.unmatched``.
"""

import hashlib
import os

from kaskad import paths
from kaskad.sysrun import FakeRunner

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw", "tunnel")
FAKE_KEY = "-----BEGIN PRIVATE KEY-----\nTESTKEY%d\n-----END PRIVATE KEY-----\n"
STATIC_KEY = ("#\n# 2048 bit OpenVPN static key\n#\n-----BEGIN OpenVPN Static key V1-----\n"
              + "00112233445566778899aabbccddeeff\n" * 16 + "-----END OpenVPN Static key V1-----\n")
BINARIES = ("ip", "wg", "openvpn", "ocserv", "occtl", "openssl")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def _arg(argv, flag):
    return argv[argv.index(flag) + 1]


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class FakeSystemd(object):
    def __init__(self, distro=()):
        self.units = {}
        self.distro = set(distro)
        self.calls = []
        self.reloads = 0
        self.fail = {}

    def exists(self, name):
        return name in self.distro or os.path.exists(os.path.join(paths.systemd_dir(), name))

    def u(self, name):
        return self.units.setdefault(name, {"enabled": False, "active": False, "masked": False})

    def __call__(self, argv, input):
        args = list(argv[1:])
        self.calls.append(args)
        verb = args[0] if args else ""
        names = [a for a in args[1:] if not a.startswith("-")]
        now = "--now" in args
        if verb in self.fail:
            return 1, "", self.fail[verb]
        if verb == "daemon-reload":
            self.reloads += 1
            return 0, "", ""
        if verb == "is-active":
            return (0, "active\n", "") if self.u(names[0])["active"] else (3, "inactive\n", "")
        if verb == "is-enabled":
            n = names[0]
            if not self.exists(n):
                return 1, "", "Failed to get unit file state for %s: No such file or directory\n" % n
            u = self.u(n)
            if u["masked"]:
                return 1, "masked\n", ""
            return (0, "enabled\n", "") if u["enabled"] else (1, "disabled\n", "")
        if verb in ("mask", "unmask"):
            for n in names:
                self.u(n)["masked"] = verb == "mask"
            return 0, "", ""
        for n in names:
            if not self.exists(n):
                return 5, "", "Failed to %s %s: Unit %s not found.\n" % (verb, n, n)
        for n in names:
            u = self.u(n)
            if verb in ("enable", "start", "restart") and u["masked"]:
                return 1, "", "Failed to %s %s: Unit %s is masked.\n" % (verb, n, n)
            if verb in ("enable", "disable"):
                u["enabled"] = verb == "enable"
                if now:
                    u["active"] = verb == "enable"
            elif verb in ("start", "restart"):
                u["active"] = True
            elif verb == "stop":
                u["active"] = False
            elif verb == "reload":
                if not u["active"]:
                    return 1, "", "Job for %s failed: unit not active\n" % n
            else:
                return 1, "", "Unknown command verb %s.\n" % verb
        return 0, "", ""

    def verbs(self, unit):
        """Calls that touched ``unit``, as 'verb[ --now]' strings."""
        out = []
        for c in self.calls:
            if unit in c[1:]:
                out.append(" ".join([c[0]] + [a for a in c[1:] if a.startswith("-")]))
        return out


class FakeOpenssl(object):
    """``req -x509`` of a CA with nameConstraints writes the real constrained fixture of that scope
    (ikev2-ca-ip.crt: 203.0.113.10, ikev2-ca-dns.crt: vpn.example.org); ``ca -gencrl`` writes a fake CRL that
    lists the serials of index.txt (``crls`` keeps them per call)."""

    SCOPES = {"permitted;IP:203.0.113.10/255.255.255.255": "ikev2-ca-ip.crt",
              "permitted;DNS:vpn.example.org": "ikev2-ca-dns.crt"}

    def __init__(self):
        self.calls = []
        self.configs = []
        self.keys = 0
        self.crls = []
        self.fail = set()               # subcommands that fail
        self.client_certs = []          # client certificates handed out in turn (default: client.crt always)

    def _ca_fixture(self, cnf):
        if "nameConstraints" not in cnf:
            return fixture("ca.crt")
        for scope, name in self.SCOPES.items():
            if scope in cnf:
                return fixture(name)
        raise AssertionError("fake openssl: no constrained CA fixture for %r" % cnf)

    def __call__(self, argv, input):
        sub = argv[1]
        self.calls.append(sub)
        if sub in self.fail:
            return 1, "", "fake openssl: %s failed\n" % sub
        if sub == "genpkey":
            self.keys += 1
            _write(_arg(argv, "-out"), FAKE_KEY % self.keys)
            return 0, "", ""
        if sub == "ca" and "-gencrl" in argv:
            for flag in ("-config", "-keyfile", "-cert"):
                if not os.path.exists(_arg(argv, flag)):
                    return 1, "", "missing %s" % flag
            cnf = _read(_arg(argv, "-config"))
            db = [ln.split(" = ", 1)[1] for ln in cnf.splitlines() if ln.startswith("database = ")][0]
            serials = [ln.split("\t")[3] for ln in _read(db).splitlines() if ln.startswith("R\t")]
            self.crls.append(serials)
            _write(_arg(argv, "-out"), "-----BEGIN X509 CRL-----\nTESTCRL %d %s\n-----END X509 CRL-----\n"
                   % (len(self.crls), " ".join(serials)))
            return 0, "", "Using configuration from %s\n" % _arg(argv, "-config")
        if sub == "req":
            if not os.path.exists(_arg(argv, "-key")):
                return 1, "", "Could not open file or uri for loading private key"
            cnf = _read(_arg(argv, "-config"))
            self.configs.append(cnf)
            if "-x509" in argv:
                _write(_arg(argv, "-out"), self._ca_fixture(cnf) if "CA:TRUE" in cnf else fixture("ocserv.crt"))
            else:
                _write(_arg(argv, "-out"), "-----BEGIN CERTIFICATE REQUEST-----\nTESTCSR\n-----END CERTIFICATE REQUEST-----\n")
            return 0, "", ""
        if sub == "x509" and "-req" in argv:
            for flag in ("-in", "-CA", "-CAkey", "-extfile"):
                if not os.path.exists(_arg(argv, flag)):
                    return 1, "", "missing %s" % flag
            ext = _read(_arg(argv, "-extfile"))
            if "clientAuth" in ext and self.client_certs:
                name = self.client_certs.pop(0)
                self.client_certs.append(name)
                _write(_arg(argv, "-out"), fixture(name))
            else:
                _write(_arg(argv, "-out"), fixture("client.crt") if "clientAuth" in ext else fixture("server.crt"))
            return 0, "", ""
        if sub == "passwd" and "-stdin" in argv:
            salt = _arg(argv, "-salt")
            pw = (input or "").rstrip("\n")
            return 0, "$6$%s$%s\n" % (salt, hashlib.sha256((salt + pw).encode()).hexdigest()[:43]), ""
        return 1, "", "fake openssl: unsupported %s" % sub


class Node(object):
    """FakeRunner + every fake above. ``node.runner`` goes into the code under test."""

    def __init__(self, arch="x86_64", ufw=False, pkgs=(), modules=("wireguard", "tun"),
                 distro=("openvpn.service", "ocserv.service")):
        self.runner = FakeRunner(binaries=BINARIES)
        self.sysd = FakeSystemd(distro)
        self.ssl = FakeOpenssl()
        self.arch = arch
        self.pkgs = set(pkgs)
        self.modules = set(modules)
        self.ufw_active = ufw
        self.ufw = []
        self.apt = []
        self.modprobed = []
        self.syncs = []
        self.handshakes = {}
        self.transfer = {}
        self.core_runs = True
        self.occtl_users = "[]"
        self.disconnects = []
        r = self.runner
        r.on(["systemctl"], self.sysd)
        r.on(["dpkg-query"], self._dpkg)
        r.on(["apt-get"], self._apt)
        r.on(["modprobe"], self._modprobe)
        r.on(["modinfo"], lambda a, i: (0, "/lib/modules/x/%s.ko\n" % a[-1], "") if a[-1] in self.modules
             else (1, "", "modinfo: ERROR: Module %s not found.\n" % a[-1]))
        r.on(["uname", "-m"], lambda a, i: (0, self.arch + "\n", ""))
        r.on(["ufw"], self._ufw)
        r.on(["/usr/sbin/wg"], self._wg)
        r.on(["openssl"], self.ssl)
        r.on(["/usr/sbin/openvpn", "--genkey", "secret"], self._ovpn_genkey)
        r.on(["/usr/sbin/occtl"], self._occtl)
        r.on([], self._other)

    # ── handlers ──
    def _dpkg(self, argv, input):
        pkg = argv[-1]
        if pkg in self.pkgs:
            return 0, "install ok installed", ""
        return 1, "", "dpkg-query: no packages found matching %s\n" % pkg

    def _apt(self, argv, input):
        p = paths.P("/usr/sbin/policy-rc.d")
        policy = os.path.exists(p) and "exit 101" in _read(p)
        pkgs = [a for a in argv[2:] if not a.startswith("-")]
        self.apt.append((pkgs, policy))
        self.pkgs.update(pkgs)
        return 0, "", ""

    def _modprobe(self, argv, input):
        self.modprobed.append(argv[-1])
        return (0, "", "") if argv[-1] in self.modules else (1, "", "modprobe: FATAL: Module %s not found\n" % argv[-1])

    def _ufw(self, argv, input):
        if argv[1:] == ["status"]:
            return 0, "Status: active\n" if self.ufw_active else "Status: inactive\n", ""
        self.ufw.append(" ".join(argv[1:]))
        return 0, "Rule added\n", ""

    def _wg(self, argv, input):
        tool = os.path.basename(argv[0])
        args = argv[1:]
        if args[:1] == ["--version"]:
            return (0, "%s v3.1\n" % tool, "") if self.core_runs else (126, "", "cannot execute binary file")
        if args[:1] in (["syncconf"], ["setconf"]):
            self.syncs.append((tool, args[0], args[1], _read(args[2])))
            return 0, "", ""
        if args[:1] == ["show"] and len(args) == 3:
            table = self.handshakes if args[2] == "latest-handshakes" else self.transfer
            return 0, table.get(args[1], ""), ""
        return 1, "", "fake %s: unsupported %s" % (tool, " ".join(args))

    def _ovpn_genkey(self, argv, input):
        _write(argv[3], STATIC_KEY)
        return 0, "", ""

    def _occtl(self, argv, input):
        args = argv[1:]
        if "show" in args and "users" in args:
            return 0, self.occtl_users, ""
        if "disconnect" in args:
            self.disconnects.append(args[-1])
            return 0, "", ""
        return 1, "", "fake occtl: unsupported"

    def _other(self, argv, input):
        """The AmneziaWG core lives under KASKAD_ROOT: dispatch it by name, anything else is unmatched."""
        if os.path.basename(argv[0]) in ("awg", "amneziawg-go"):
            return self._wg(argv, input)
        if os.path.basename(argv[0]) == "ip" and argv[1:3] == ["link", "set"] and "mtu" in argv:
            return 0, "", ""                      # the server MTU onto a live interface (WgFamily.set_mtu)
        self.runner.unmatched.append(argv)
        return 127, "", "not mocked: %s" % " ".join(argv)

    # ── helpers for assertions ──
    def commands(self, first=None):
        return [a for a, _ in self.runner.calls if first is None or a[0] == first]
