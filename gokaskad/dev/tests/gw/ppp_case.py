"""Common ground of the PPP and IPsec inbound tests (work package 6): the WP5 fake server extended with the
accel-ppp build (curl, tar, patch, cmake, make), accel-pppd/accel-cmd, swanctl, the L2TP guard script and
``ip xfrm``; the core stand-ins with the modules of WP6 (accel, ipsec, l2tp, pptp, sstp, ikev2).

Golden files live in tests/fixtures/gw/ppp/golden/ (production paths; ``KASKAD_GOLDEN_UPDATE=1`` rewrites).
The download is a small fake tarball: the tests set ``accel.SHA256``/``SIZE`` to its digest, as the code
compares with whatever the module says.
"""

import hashlib
import os

from kaskad import paths
from tests.gw.tunnel_case import HOST, GwCase, device, gw_state  # noqa: F401 — re-exported for the tests
from tests.gw.tunnel_fakes import BINARIES, Node
from tests.gwstub import PPP_ADAPTERS, Stubbed

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "gw", "ppp", "golden")
TARBALL = b"accel-ppp-1.14.0 fake tarball for the tests\n"
PPP_MODULES = ("ppp_generic", "ppp_mppe", "l2tp_ppp", "pptp", "gre", "ppp_async", "esp4", "xfrm_user", "tun")
INBOUNDS = {"l2tp": {"enabled": True, "ipsec": True, "plain": False, "subnet": "10.77.5.0/24"},
            "pptp": {"enabled": True, "subnet": "10.77.5.0/24"},
            "sstp": {"enabled": True, "port": 4433, "subnet": "10.77.5.0/24"},
            "ikev2": {"enabled": True, "subnet": "10.77.8.0/24", "cert": "self"}}

SESSIONS_HEAD = (" ifname    | username   |     ip     | type | state  | uptime-raw | rx-bytes-raw | tx-bytes-raw\r\n"
                 "-----------+------------+------------+------+--------+------------+--------------+-------------\r\n")


def session_row(ifname, login, ip, kind, up=120, rx=1000, tx=2000):
    return " %-9s | %-10s | %-10s | %-4s | active | %-10d | %-12d | %d\r\n" % (ifname, login, ip, kind, up, rx, tx)


def ike_sa(uid, conn, eap=None, vip=None, host="198.51.100.7", established=300, bytes_in=1500, bytes_out=2500):
    """One IKE_SA as ``swanctl --list-sas --pretty`` prints it."""
    lines = ["list-sa event {", "  %s {" % conn, "    uniqueid = %d" % uid, "    version = %d" % (2 if "ikev2" in conn else 1),
             "    state = ESTABLISHED", "    local-host = 203.0.113.10", "    remote-host = %s" % host,
             "    remote-id = 192.168.1.5"]
    if eap:
        lines.append("    remote-eap-id = %s" % eap)
    lines.append("    established = %d" % established)
    if vip:
        lines += ["    remote-vips = [", "      %s" % vip, "    ]"]
    lines += ["    child-sas {", "      %s-%d {" % (conn, uid + 1), "        name = %s" % conn,
              "        state = INSTALLED", "        bytes-in = %d" % bytes_in, "        bytes-out = %d" % bytes_out,
              "        local-ts = [", "          0.0.0.0/0", "        ]", "      }", "    }", "  }", "}"]
    return "\n".join(lines) + "\n"


class PppNode(Node):
    """Node + the build tools of accel-ppp, accel-pppd/accel-cmd, swanctl, /bin/sh (the guard), ip xfrm."""

    def __init__(self, **kw):
        kw.setdefault("modules", PPP_MODULES)
        kw.setdefault("distro", ("openvpn.service", "ocserv.service", "strongswan.service"))
        super().__init__(**kw)
        self.runner.binaries = set(BINARIES) | {"swanctl", "charon-systemd", "modprobe"}
        self.download = TARBALL
        self.curl_rc = 0
        self.builds = []                 # cmake/make/tar steps in order
        self.cmake_args = None
        self.sessions = ""               # accel-cmd show sessions (rows only)
        self.cli_password = None         # the daemon's [cli] password (None: none, every call is let in)
        self.cli_seen = []               # the -P values accel-cmd got
        self.terminated = []
        self.sas = ""                    # swanctl --list-sas --pretty
        self.loads = 0
        self.load_rc = 0
        self.ike_terminated = []
        self.guard_runs = []
        self.xfrm = []
        self.pppd_runs = True
        self.patch_rc = 0                # patch -p1 of the vendor patches: 1 = does not apply
        self.patched = []                # (sources, patch file) in order

    # ── dispatch of the commands Node does not know ──
    def _other(self, argv, input):
        name = os.path.basename(argv[0])
        handler = {"curl": self._curl, "tar": self._tar, "patch": self._patch, "cmake": self._cmake,
                   "make": self._make, "accel-pppd": self._pppd, "accel-cmd": self._accel_cmd,
                   "swanctl": self._swanctl}.get(name)
        if argv[0] == "/bin/sh":
            handler = self._sh
        elif name == "ip" and argv[1:2] == ["xfrm"]:
            handler = self._xfrm
        if handler is None:
            return super()._other(argv, input)
        return handler(argv, input)

    def _curl(self, argv, input):
        self.builds.append("curl")
        if self.curl_rc:
            return self.curl_rc, "", "curl: (6) Could not resolve host: github.com\n"
        with open(argv[argv.index("-o") + 1], "wb") as f:
            f.write(self.download)
        return 0, "", ""

    def _tar(self, argv, input):
        self.builds.append("tar")
        src = os.path.join(argv[argv.index("-C") + 1], "accel-ppp-1.14.0")
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "CMakeLists.txt"), "w") as f:
            f.write("project(accel-ppp C)\n")
        return 0, "", ""

    def _patch(self, argv, input):
        """patch -d <sources> -p1 -N -s -i <file>: the file of the PPP-1 patch gets its mark (conf_peer_restart)."""
        self.builds.append("patch")
        src, pfile = argv[argv.index("-d") + 1], argv[argv.index("-i") + 1]
        self.patched.append((src, pfile))
        if self.patch_rc or not os.path.isfile(pfile) or argv[3:7] != ["-p1", "-N", "-s", "-i"]:
            return 1, "", "1 out of 4 hunks FAILED -- saving rejects to file accel-pppd/ctrl/l2tp/l2tp.c.rej\n"
        target = os.path.join(src, "accel-pppd", "ctrl", "l2tp", "l2tp.c")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a") as f:
            f.write("static int conf_peer_restart = 0;\n")
        return 0, "", ""

    def _cmake(self, argv, input):
        self.builds.append("cmake")
        self.cmake_args = list(argv[1:])
        os.makedirs(argv[argv.index("-B") + 1], exist_ok=True)
        return 0, "", ""

    def _make(self, argv, input):
        if "install" not in argv:
            self.builds.append("make")
            return 0, "", ""
        self.builds.append("make install")
        stage = [a for a in argv if a.startswith("DESTDIR=")][0].split("=", 1)[1]
        root = os.path.join(stage, "opt", "kaskad", "accel-ppp")
        for rel in ("sbin/accel-pppd", "bin/accel-cmd", "lib/accel-ppp/libtriton.so", "lib/accel-ppp/libl2tp.so",
                    "share/accel-ppp/l2tp/dictionary"):
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("fake %s\n" % rel)
        return 0, "", ""

    def _pppd(self, argv, input):
        if argv[1:] == ["-V"]:
            return (0, "accel-ppp version 1.14.0\n", "") if self.pppd_runs else (127, "", "error while loading "
                                                                                 "shared libraries: libtriton.so\n")
        return 1, "", "fake accel-pppd: %r" % argv[1:]

    def _accel_cmd(self, argv, input):
        """accel-cmd -H 127.0.0.1 -p 2001 [-P PASSWORD] <command>: with ``cli_password`` set (the daemon's
        [cli] password) a missing or wrong password closes the connection, as accel-pppd does."""
        args = argv[5:] if argv[1:5] == ["-H", "127.0.0.1", "-p", "2001"] else None
        given = None
        if args and args[:1] == ["-P"]:
            given, args = args[1], args[2:]
        self.cli_seen.append(given)
        if self.cli_password is not None and given != self.cli_password:
            return 1, "", "accel-cmd: connection closed\n"
        if args and args[:2] == ["show", "sessions"]:
            return 0, SESSIONS_HEAD + self.sessions, ""
        if args and args[:2] == ["terminate", "if"]:
            self.terminated.append(args[2])
            return 0, "", ""
        return 1, "", "fake accel-cmd: %r" % argv[1:]

    def _swanctl(self, argv, input):
        args = argv[1:]
        if args == ["--load-all", "--noprompt"]:
            self.loads += 1
            if self.load_rc:
                return self.load_rc, "", "loading connection 'kaskad-ikev2' failed: invalid value for: proposals\n"
            return 0, "loaded connection 'kaskad-ikev2'\nsuccessfully loaded 2 connections, 0 unloaded\n", ""
        if args == ["--list-sas", "--pretty"]:
            return 0, self.sas + "list-sas reply {\n}\n", ""
        if args[:2] == ["--terminate", "--ike-id"]:
            self.ike_terminated.append(int(args[2]))
            return 0, "terminate completed successfully\n", ""
        return 1, "", "fake swanctl: %r" % args

    def _sh(self, argv, input):
        self.guard_runs.append(argv[2:])
        return 0, "", ""

    def _xfrm(self, argv, input):
        self.xfrm.append(" ".join(argv[1:]))
        return 0, "", ""


class PppCase(GwCase):
    """GwCase with the stand-ins of WP6 (self.gw.accel, .ipsec, .l2tp, ...) and a PppNode."""

    def setUp(self):
        super(GwCase, self).setUp()                  # RootCase only: Stubbed and Node of our own
        self.gw = Stubbed(PPP_ADAPTERS).__enter__()
        self.addCleanup(self.gw.__exit__, None, None, None)
        self.node = PppNode()
        self.runner = self.node.runner
        self.sec = self.gw.secrets.DATA
        self.gw.accel.SHA256 = hashlib.sha256(TARBALL).hexdigest()
        self.gw.accel.SIZE = len(TARBALL)

    def golden(self, name, text):
        p = os.path.join(GOLDEN, name)
        text = self.prod(text)
        if os.environ.get("KASKAD_GOLDEN_UPDATE"):
            os.makedirs(GOLDEN, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return
        with open(p, encoding="utf-8") as f:
            self.assertEqual(text, f.read(), "golden %s differs (KASKAD_GOLDEN_UPDATE=1 rewrites it)" % name)

    def dev(self, st, dev_id):
        return [d for d in st["gateway"]["devices"] if d["id"] == dev_id][0]

    def installed(self, *kinds):
        """Install the inbounds (the build included) and forget the calls made so far."""
        for k in kinds:
            self.adapter(k).install(self.runner)
        self.runner.calls[:] = []
        self.node.sysd.calls[:] = []
        self.node.builds[:] = []

    def accel_file(self, name):
        return os.path.join(self.gw.accel.conf_dir(), name)

    def read_abs(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def prod_path(self, path):
        return path[len(paths.root()):] if paths.root() and path.startswith(paths.root()) else path
