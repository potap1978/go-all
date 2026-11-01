"""gw/awgkmod.py — the AmneziaWG kernel module through DKMS (WP-K): host checks, the install flow with fakes
(apt, the pinned archive and its sha256, dkms, patch #218 only for the new signature), the self-test and its
failure, the boot check and its fallback, removal, the kernel hook (run for real with fake dkms/patch), the CLI."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
from unittest import mock

from kaskad import cli, paths, sysrun, util
from kaskad.errors import ConflictError, KaskadError, UnsupportedError
from kaskad.gw import awgkmod, registry
from kaskad.gw import sys as gsys
from kaskad.sysrun import FakeRunner
from kaskad.vendor import awgkeys
from tests.helpers import RootCase

K = "6.8.0-50-generic"
OLD_HDR = ("void setup_udp_tunnel_sock(struct net *net, struct socket *sock,\n"
           "\t\t\t   struct udp_tunnel_sock_cfg *sock_cfg);\nvoid udp_tunnel_sock_release(struct socket *sock);\n")
NEW_HDR = ("void setup_udp_tunnel_sock(struct net *net, struct sock *sk,\n"
           "\t\t\t   struct udp_tunnel_sock_cfg *sock_cfg);\nvoid udp_tunnel_sock_release(struct socket *sock);\n")
VERSION_H = b'#ifndef WIREGUARD_VERSION\n#define WIREGUARD_VERSION "3.1.20260812"\n#endif\n'
COMPAT = b"#if LINUX_VERSION_CODE < KERNEL_VERSION(7, 1, 5)\n#define setup_udp_tunnel_sock(net, sk, c) x\n#endif\n"
DKMS_CONF = (b'PACKAGE_NAME="amneziawg"\nPACKAGE_VERSION="1.0.0"\nAUTOINSTALL=yes\nREMAKE_INITRD=yes\n\n'
             b'BUILT_MODULE_NAME="amneziawg"\nDEST_MODULE_LOCATION="/kernel/net"')
IMAGES = ("linux-image-6.8.0-50-generic\tinstall ok installed\tlinux-modules-6.8.0-50-generic\n"
          "linux-image-generic\tinstall ok installed\tlinux-image-6.8.0-50-generic, linux-firmware\n"
          "linux-image-virtual\tdeinstall ok config-files\tlinux-image-6.8.0-31-generic\n")


def tarball(extra=None):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tf:
        files = {"README.md": b"readme", "debian/control": b"x", "src/dkms.conf": DKMS_CONF,
                 "src/Makefile": b"all:\n", "src/compat/compat.h": COMPAT, "src/main.c": b"int main;\n",
                 "src/version.h": VERSION_H}
        files.update(extra or {})
        for name, data in sorted(files.items()):
            ti = tarfile.TarInfo(awgkmod.TOP + "/" + name)
            ti.size = len(data)
            ti.mode = 0o664
            tf.addfile(ti, io.BytesIO(data))
    return raw.getvalue()


class Kmod(object):
    """A server for the module: uname, systemd-detect-virt, mokutil, dpkg-query, curl, dkms, patch, modprobe,
    ip (netns of the self-test), ping, systemctl, kill, env (the legs' heal). gsys.apt_install is replaced by
    ``apt`` (it «installs» dkms and the headers)."""

    def __init__(self, case, kernel=K, arch="x86_64", virt="none", sb="SecureBoot disabled\n", header=OLD_HDR):
        self.case = case
        self.kernel, self.arch, self.virt, self.sb, self.header = kernel, arch, virt, sb, header
        self.tar = tarball()
        self.apt_calls = []
        self.dkms = {"added": False, "installed": []}
        self.ko_set = {}                        # kernel -> the .ko DKMS installed carries the set of 3.1
        self.build_fail = None
        self.set_build_fail = None              # the build fails only with the set of 3.1 in the sources
        self.set_apply_fail = False             # the set does not apply (patch --dry-run fails)
        self.ping_fail_set = set()              # ping fails in these cases only while the module carries the set
        self.module_ok = True
        self.ping_fail = set()
        self.calls = []
        self.r = FakeRunner(binaries=("ip", "systemd-detect-virt", "mokutil", "ping"))
        r = self.r
        r.on(["uname", "-m"], lambda a, i: (0, self.arch + "\n", ""))
        r.on(["uname", "-r"], lambda a, i: (0, self.kernel + "\n", ""))
        r.on(["systemd-detect-virt", "-c"], lambda a, i: (1, "none\n", "") if self.virt == "none" else (0, self.virt + "\n", ""))
        r.on(["mokutil", "--sb-state"], lambda a, i: (0, self.sb, ""))
        r.on(["dpkg-query"], lambda a, i: (0, IMAGES, ""))
        r.on(["curl"], self._curl)
        r.on(["dkms"], self._dkms)
        r.on(["patch"], self._patch)
        r.on(["modprobe"], self._modprobe)
        r.on(["rmmod"], self._rmmod)
        r.on(["modinfo"], self._modinfo)
        r.on(["ip"], self._ip)
        r.on(["systemctl"], lambda a, i: (3, "inactive\n", "") if a[1] == "is-active" else (0, "", ""))
        r.on(["kill"], (0, "", ""))
        r.on(["env"], (0, "", ""))

    def apt(self, runner, pkgs, mask=(), timeout=None):
        self.apt_calls.append(list(pkgs))
        if "dkms" in pkgs:
            self.r.binaries.add("dkms")
        if "linux-headers-%s" % self.kernel in pkgs:
            for sub in ("build", "source"):
                d = awgkmod.kernel_tree(self.kernel, sub)
                os.makedirs(os.path.join(d, "include", "net"), exist_ok=True)
            with open(os.path.join(awgkmod.kernel_tree(self.kernel, "source"), "include", "net", "udp_tunnel.h"), "w") as f:
                f.write(self.header)                    # Debian: the headers are in -common (source/)
        return list(pkgs)

    def _curl(self, argv, input):
        self.calls.append(argv)
        with open(argv[argv.index("-o") + 1], "wb") as f:
            f.write(self.tar)
        return 0, "", ""

    def _dkms(self, argv, input):
        self.calls.append(argv)
        verb = argv[1]
        if verb == "status":
            if not self.dkms["added"]:
                return 0, "", ""
            lines = ["amneziawg/%s, %s, x86_64: installed" % (awgkmod.VERSION, k) for k in self.dkms["installed"]]
            return 0, "\n".join(lines or ["amneziawg/%s: added" % awgkmod.VERSION]) + "\n", ""
        if verb == "add":
            self.dkms["added"] = True
            return 0, "Creating symlink\n", ""
        if verb == "build":
            fail = self.build_fail or (self.set_build_fail if awgkmod.set_in_src() else None)
            if fail:
                log = self.case.path("/var/lib/dkms/amneziawg/%s/build/make.log" % awgkmod.VERSION)
                os.makedirs(os.path.dirname(log), exist_ok=True)
                with open(log, "w") as f:
                    f.write("CC socket.o\nsocket.c:434:9: error: %s\n" % fail)
                return 10, "Error! Bad return status for module build on kernel: %s (x86_64)\nConsult %s for more "\
                           "information.\n" % (self.kernel, log), ""
            return 0, "", ""
        if verb == "install":
            k = argv[argv.index("-k") + 1]
            if k not in self.dkms["installed"]:
                self.dkms["installed"].append(k)
            self.ko_set[k] = awgkmod.set_in_src()
            return 0, "", ""
        if verb == "remove":
            if "-k" in argv:
                k = argv[argv.index("-k") + 1]
                self.dkms["installed"] = [x for x in self.dkms["installed"] if x != k]
                self.ko_set.pop(k, None)
            else:
                self.dkms = {"added": False, "installed": []}
                self.ko_set = {}
            return 0, "", ""
        return 1, "", "fake dkms: %s" % verb

    def _patch(self, argv, input):
        self.calls.append(argv)
        src = argv[argv.index("-d") + 1]
        if os.path.basename(argv[argv.index("-i") + 1]) == awgkmod.SET_NAME:
            if "--dry-run" in argv:
                return (1, "", "1 out of 3 hunks FAILED\n") if self.set_apply_fail and "-R" not in argv else (0, "", "")
            p = os.path.join(src, "version.h")
            with open(p) as f:
                text = f.read()
            # the hunk of src/version.h exactly as the vendored set has it (its «-» and «+» lines)
            with open(argv[argv.index("-i") + 1], encoding="utf-8") as f:
                hunk = f.read().split("diff --git a/src/version.h", 1)[1]
            was = [ln[1:] for ln in hunk.splitlines() if ln.startswith("-#define")][0]
            now = [ln[1:] for ln in hunk.splitlines() if ln.startswith("+#define")][0]
            text = text.replace(now, was) if "-R" in argv else text.replace(was, now)
            with open(p, "w") as f:
                f.write(text)
            return 0, "", ""
        p = os.path.join(src, "compat", "compat.h")
        with open(p, "a") as f:
            f.write("\n/* %s */\n" % awgkmod.PATCHED_MARK)
        return 0, "", ""

    def _modinfo(self, argv, input):
        self.calls.append(argv)
        k = argv[argv.index("-k") + 1]
        if k not in self.dkms["installed"]:
            return 1, "", "modinfo: ERROR: Module amneziawg not found.\n"
        return 0, "3.1.20260812%s\n" % (("+" + awgkmod.SET_ID) if self.ko_set.get(k) else ""), ""

    def load(self, with_set):
        """The module in memory (as modprobe leaves it), with or without the set."""
        os.makedirs(awgkmod.module_dir(), exist_ok=True)
        with open(os.path.join(awgkmod.module_dir(), "version"), "w") as f:
            f.write("3.1.20260812%s\n" % (("+" + awgkmod.SET_ID) if with_set else ""))

    def _rmmod(self, argv, input):
        self.calls.append(argv)
        if os.path.isdir(awgkmod.module_dir()):
            shutil.rmtree(awgkmod.module_dir())
            return 0, "", ""
        return 1, "", "rmmod: ERROR: Module amneziawg is not currently loaded\n"

    def _modprobe(self, argv, input):
        self.calls.append(argv)
        if argv[1:] == ["-r", awgkmod.NAME]:
            shutil.rmtree(awgkmod.module_dir(), ignore_errors=True)
            return 0, "", ""
        if self.module_ok:
            self.load(self.ko_set.get(self.kernel, False))
            return 0, "", ""
        return 1, "", "modprobe: FATAL: Module amneziawg not found in directory /lib/modules/%s\n" % self.kernel

    def _ip(self, argv, input):
        self.calls.append(argv)
        a = argv[1:]
        if a[:2] == ["netns", "exec"]:
            cmd = a[3:]
            if cmd[:1] == ["ping"]:
                n = int(cmd[-1].split(".")[2])
                bad = n in self.ping_fail or (n in self.ping_fail_set and awgkmod.set_loaded())
                return (1, "", "100% packet loss\n") if bad else (0, "3 received\n", "")
            if os.path.basename(cmd[0]) == "amneziawg-go":
                sock = awgkmod.uapi_sock(cmd[1])
                os.makedirs(os.path.dirname(sock), exist_ok=True)
                open(sock, "w").close()
                return 0, "", ""
            if cmd[1:2] == ["show"]:
                return 0, "PUBKEY=\t1759579200\n", ""
            return 0, "", ""                            # awg setconf
        if a[:2] == ["netns", "pids"]:
            return 0, "", ""
        return 0, "", ""

    def commands(self, first):
        return [c for c in self.calls if c[0] == first]


class KmodCase(RootCase):
    def setUp(self):
        super().setUp()
        registry.reset()
        self.w = self.world()
        self.core_d = self.path("/opt/kaskad/bin")
        os.makedirs(self.core_d, exist_ok=True)
        tar_sha = hashlib.sha256(self.w.tar).hexdigest()
        self._patches = [mock.patch.object(awgkmod, "SHA256", tar_sha),
                         mock.patch.object(awgkmod, "_core", lambda runner: self.core_d),
                         mock.patch.object(awgkmod, "_sleep", lambda s: None),
                         mock.patch.object(gsys, "apt_install", self.w.apt)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        registry.reset()
        self.assertEqual(self.w.r.unmatched, [])
        super().tearDown()

    def world(self, **kw):
        return Kmod(self, **kw)

    def state(self):
        return json.loads(self.read("/etc/kaskad/gw/awg-kmod.json"))

    def log(self):
        return util.read_text(paths.log()) or ""


class HostTest(KmodCase):
    def test_a_vm_with_secure_boot_off(self):
        self.assertEqual(awgkmod.supported_host(self.w.r)["ok"], True)

    def test_refusals(self):
        for attr, value, word in (("arch", "riscv64", "x86_64 и aarch64"), ("virt", "lxc", "контейнер lxc"),
                                  ("virt", "openvz", "контейнер openvz"),
                                  ("sb", "SecureBoot enabled\n", "Secure Boot")):
            with self.subTest(attr=attr, value=value):
                w = Kmod(self)
                setattr(w, attr, value)
                res = awgkmod.supported_host(w.r)
                self.assertFalse(res["ok"])
                self.assertIn(word, res["why"])

    def test_secure_boot_without_mokutil_reads_efivars(self):
        self.w.r.binaries.discard("mokutil")
        self.assertTrue(awgkmod.supported_host(self.w.r)["ok"])
        self.write("/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-e8c13a1d0e4c", "")
        with open(self.path("/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-e8c13a1d0e4c"), "wb") as f:
            f.write(b"\x06\x00\x00\x00\x01")
        self.assertIn("Secure Boot", awgkmod.supported_host(self.w.r)["why"])

    def test_install_refuses_an_unsupported_host_before_apt(self):
        self.w.virt = "lxc"
        with self.assertRaises(UnsupportedError):
            awgkmod.install(self.w.r)
        self.assertEqual(self.w.apt_calls, [])

    def test_flavours_and_headers_metapackages(self):
        self.assertEqual([awgkmod.flavor(k) for k in ("6.8.0-50-generic", "6.1.0-26-cloud-amd64", "6.12.48+deb13-amd64",
                                                      "7.0.0-38-generic", "6.8.0-1015-kvm", "6.12.12+bpo-amd64")],
                         ["generic", "cloud-amd64", "amd64", "generic", "kvm", "amd64"])
        self.assertEqual(awgkmod.headers_meta(self.w.r, K), "linux-headers-generic")
        # an upgrade not booted yet: the metapackage pulls -51, the running kernel is -49 of the same flavour
        self.assertEqual(awgkmod.headers_meta(self.w.r, "6.8.0-49-generic"), "linux-headers-generic")
        r = FakeRunner()
        r.on(["dpkg-query"], (0, "linux-image-cloud-amd64\tinstall ok installed\tlinux-image-6.1.0-27-cloud-amd64 "
                                 "(= 6.1.115-1)\nlinux-image-amd64\tinstall ok installed\tlinux-image-6.1.0-27-amd64\n", ""))
        self.assertEqual(awgkmod.headers_meta(r, "6.1.0-26-cloud-amd64"), "linux-headers-cloud-amd64")
        self.assertEqual(awgkmod.headers_meta(r, "6.1.0-26-amd64"), "linux-headers-amd64")
        self.assertIsNone(awgkmod.headers_meta(r, "6.8.0-50-generic"))

    def test_the_compiler_of_the_kernel(self):
        self.assertIsNone(awgkmod.kernel_gcc())
        self.write("/proc/version", "Linux version 6.8.0-50-generic (buildd@lcy02) (x86_64-linux-gnu-gcc-13 (Ubuntu "
                                    "13.2.0-23ubuntu4) 13.2.0, GNU ld 2.42) #50-Ubuntu SMP\n")
        self.assertEqual(awgkmod.kernel_gcc(), "gcc-13")


class ParamsTest(KmodCase):
    CONF20 = ("[Interface]\nPrivateKey = x\nJc = 4\nJmin = 10\nJmax = 50\nS1 = 106\nS2 = 123\nS3 = 18\nS4 = 117\n"
              "H1 = 1827480060-1827483990\nH2 = 447982126-447984206\nH3 = 717615779-717619267\n"
              "H4 = 398175212-398180108\nI1 = <r 2><b 0x8580>\n\n[Peer]\nPublicKey = y\n")

    def test_what_may_go_into_the_module(self):
        self.assertEqual(awgkmod.conf_problems(self.CONF20), [])
        self.assertIn("Jmin must be < Jmax", awgkmod.conf_problems(self.CONF20.replace("Jmin = 10", "Jmin = 60")))
        self.assertTrue(awgkmod.conf_problems(self.CONF20.replace("I1 = <r 2><b 0x8580>", "I1 = <b 0x8>")))
        conf31 = self.CONF20.replace("S4 = 117\n", "S4 = 117\nRandomTrailers = on\n")
        self.assertEqual(awgkmod.conf_problems(conf31), [])                    # an inbound 3.1: the set will carry it
        self.assertEqual(awgkmod.conf_problems(conf31, leg=True), [])          # a leg of 3.1: with the set too
        self.assertEqual(awgkmod.conf_problems(conf31, leg=True, k31=False), [awgkmod.LEG31_WHY])  # without: #222
        self.assertEqual(awgkmod.conf_problems(conf31, k31=False),
                         ["линия 3.1 остаётся на amneziawg-go: в модуле ядра нет набора исправлений Kaskad (%s)"
                          % awgkmod.SET_ID])
        p = self.write("/tmp/leg.conf", self.CONF20)
        self.assertEqual(awgkmod.check_file(p), [])
        leg31 = self.write("/tmp/leg31.conf", conf31)
        self.w.load(with_set=True)                                  # the module in memory decides
        self.assertEqual(awgkmod.check_file(leg31), [])
        self.w.load(with_set=False)
        self.assertEqual(awgkmod.check_file(leg31), [awgkmod.LEG31_WHY])
        self.assertIn("нет набора исправлений", awgkmod.LEG31_WHY)
        leg15 = self.write("/tmp/leg15.conf", self.CONF20.replace("S3 = 18\nS4 = 117\n", "")
                           .replace("-1827483990", "").replace("-447984206", "")
                           .replace("-717619267", "").replace("-398180108", ""))
        leg10 = self.write("/tmp/leg10.conf", util.read_text(leg15).replace("I1 = <r 2><b 0x8580>\n", ""))
        for p, line in ((leg15, "1.5"), (leg10, "1.0")):
            self.w.load(with_set=False)                             # without the set: on the program, words why
            self.assertEqual(len(awgkmod.check_file(p)), 1)
            self.assertIn("нога %s — на программе: в модуле ядра нет набора" % line, awgkmod.check_file(p)[0])
            self.w.load(with_set=True)                              # with it: the module (1.5 sends I1 itself)
            self.assertEqual(awgkmod.check_file(p), [], line)
        self.assertEqual(awgkmod.check_file(self.write("/tmp/leg15d.conf", util.read_text(leg15).replace(
            "I1 = <r 2><b 0x8580>", "I1 = <r 2><dz 4>"))), ["I1: тег <dz> модуль ядра не знает (знает <b>, <c>, <t>, "
                                                             "<r>, <rc>, <rd>)"])   # a tag of amneziawg-go only
        self.assertEqual(awgkmod.leg_lines(True), ("2.0", "3.1", "1.5", "1.0"))
        self.assertEqual(awgkmod.leg_lines(False), ("2.0",))
        self.assertEqual(awgkmod.check_file(p + ".none"), ["нет файла %s" % (p + ".none")])

    def test_cli_check(self):
        bad = self.write("/tmp/bad.conf", self.CONF20.replace("Jmin = 10", "Jmin = 60"))
        good = self.write("/tmp/good.conf", self.CONF20)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["gw", "accel", "check", bad]), 1)
            self.assertEqual(cli.main(["gw", "accel", "check", good]), 0)
        self.assertIn("Jmin must be < Jmax", err.getvalue())


class ModuleRulesTest(KmodCase):
    CONF31 = ("[Interface]\nPrivateKey = x\nListenPort = 51911\nJc = 4\nJmin = 10\nJmax = 50\nS1 = 12\nS2 = 12\n"
              "S3 = 12\nS4 = 12\nH1 = 1\nH2 = 2\nH3 = 3\nH4 = 4\nHeaderProtectionKey = %s\nRandomTrailers = on\n"
              "DisableCookies = on\n# I1 = <r 2><b 0x8580>\n\n# device d-1\n[Peer]\nPublicKey = y\nPresharedKey = z\n"
              "AllowedIPs = 10.77.4.10/32\n" % awgkeys.genkey())

    def test_tags_of_i_the_module_does_not_know_and_negative_sizes(self):
        base = ParamsTest.CONF20
        for i1, word in (("<d>", "тег <d>"), ("<r 2><dz 4>", "тег <dz>"), ("<rc -5>", "<rc -5>"), ("<rd 0>", "<rd 0>"),
                         ("<r>", "")):
            probs = awgkmod.conf_problems(base.replace("I1 = <r 2><b 0x8580>", "I1 = " + i1))
            self.assertTrue(probs, i1)
            self.assertIn(word, " ".join(probs), i1)
        for i1 in ("<b 0x8580><c><t><r 4><rc 3><rd 2>",):
            self.assertEqual(awgkmod.conf_problems(base.replace("I1 = <r 2><b 0x8580>", "I1 = " + i1)), [])

    def test_s4_of_31(self):
        self.assertEqual(awgkmod.conf_problems(self.CONF31), [])
        probs = awgkmod.conf_problems(self.CONF31.replace("HeaderProtectionKey", "#").replace("S4 = 12", "S4 = 8")
                                      .replace("RandomTrailers = on", "RekeyAfterTime = 100-120"))
        self.assertTrue(any("S4 = 8" in p and "не меньше 12" in p for p in probs), probs)

    def test_the_rule_against_226_is_a_warning(self):
        self.assertEqual(awgkmod.conf_warnings(self.CONF31), [])
        wide = (self.CONF31.replace("S1 = 12", "S1 = 47").replace("S2 = 12", "S2 = 77").replace("S3 = 12", "S3 = 29")
                .replace("S4 = 12", "S4 = 18").replace("H1 = 1\n", "H1 = 100000000-199999999\n")
                .replace("H2 = 2\n", "H2 = 200000000-299999999\n").replace("H3 = 3\n", "H3 = 300000000-399999999\n")
                .replace("H4 = 4\n", "H4 = 400000000-499999999\n"))
        self.assertIn("#226", awgkmod.conf_warnings(wide)[0])
        self.assertEqual(awgkmod.conf_problems(wide), [])                         # the key is taken
        self.assertEqual(awgkmod.conf_warnings(wide.replace("RandomTrailers = on", "RandomTrailers = off")), [])
        self.assertEqual(awgkmod.conf_warnings(wide.replace("S1 = 47", "S1 = 12").replace("S2 = 77", "S2 = 12")
                                               .replace("S3 = 29", "S3 = 12").replace("S4 = 18", "S4 = 12")), [])
        single = wide
        for i in range(1, 5):
            single = single.replace("H%d = %d00000000-%d99999999" % (i, i, i), "H%d = %d00000000" % (i, i))
        self.assertEqual(awgkmod.conf_warnings(single), [])
        self.assertTrue(awgkmod.trailers_loss_risk({"RandomTrailers": "on", "S1": "1", "S2": "2", "S3": "3", "S4": "4",
                                                    "H1": "5-9", "H2": "10", "H3": "11", "H4": "12"}))

    def test_the_self_test_of_31_carries_its_fields(self):
        text = awgkmod._test_conf("PRIV", 51000, awgkeys.gen_params("3.1"), "PUB", "PSK", "10.199.6.2/32",
                                  "127.0.0.1:51001")
        for k in ("HeaderProtectionKey = ", "RandomTrailers = on", "DisableCookies = on", "S4 = 12", "I1 = "):
            self.assertIn(k, text)
        self.assertEqual(awgkmod.conf_problems(text), [])
        self.assertNotIn("HeaderProtectionKey", awgkmod._test_conf("PRIV", 51000, awgkeys.gen_params("2.0"), "PUB",
                                                                     "PSK", "10.199.2.2/32", "127.0.0.1:51001"))

    def test_sync_conf_has_no_masking(self):
        text = awgkmod.sync_conf_text(self.CONF31)
        self.assertTrue(text.startswith("[Interface]\nPrivateKey = x\nListenPort = 51911\n"))
        for k in ("HeaderProtectionKey", "Jc", "S1", "S4", "H1", "RandomTrailers", "DisableCookies"):
            self.assertNotIn("\n%s = " % k, text)
        self.assertIn("[Peer]\nPublicKey = y\nPresharedKey = z\nAllowedIPs = 10.77.4.10/32\n", text)
        self.assertEqual(awgkmod.sync_conf_text(text), text)                      # idempotent


class InstallTest(KmodCase):
    def test_flow_on_a_kernel_with_the_old_signature(self):
        self.write("/proc/version", "Linux version %s (gcc-13 (Ubuntu 13.2.0) 13.2.0)\n" % K)
        said = []
        res = awgkmod.install(self.w.r, progress=said.append)
        self.assertTrue(res["ok"])
        self.assertEqual(self.w.apt_calls, [["dkms", "gcc", "make", "patch", "gcc-13"],
                                            ["linux-headers-%s" % K, "linux-headers-generic"]])
        # T-N5 3.2.19, №5: menu 15 names what the module brought (packages.json, by_kaskad)
        pk = json.loads(self.read("/etc/kaskad/gw/packages.json"))
        self.assertEqual(sorted(k for k, v in pk.items() if v.get("by_kaskad") and v.get("for") == "awg-kmod"),
                         sorted(["dkms", "gcc", "make", "patch", "gcc-13", "linux-headers-%s" % K, "linux-headers-generic"]))
        self.assertEqual(self.w.commands("curl")[0][-1], awgkmod.URL)
        src = awgkmod.src_dir()
        conf = self.read(src[len(self.root):] + "/dkms.conf")
        self.assertIn('PACKAGE_VERSION="%s"' % awgkmod.VERSION, conf)
        self.assertIn('AUTOINSTALL="no"', conf)
        self.assertNotIn("REMAKE_INITRD", conf)
        self.assertIn('BUILT_MODULE_NAME="amneziawg"', conf)
        self.assertFalse(os.path.exists(os.path.join(src, "README.md")))           # only src/ of the archive
        self.assertTrue(os.path.isfile(os.path.join(src, "compat", "compat.h")))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(src, "main.c")).st_mode), 0o644)
        with open(os.path.join(src, awgkmod.PATCH_NAME), encoding="utf-8") as f:
            self.assertIn("pull/218", f.read())                                       # for the kernel hook
        # old signature: no #218; the set of 3.1 — after a dry run
        setp = os.path.join(src, awgkmod.SET_NAME)
        self.assertEqual(self.w.commands("patch"), [["patch", "-d", src, "-p2", "-N", "-s", "--dry-run", "-i", setp],
                                                    ["patch", "-d", src, "-p2", "-N", "-s", "-i", setp]])
        # T-N5 3.2.19, №9: the mark carries the version of the tag (as DKMS), not the stale 3.1.20260812 of upstream
        self.assertIn('"%s+%s"' % (awgkmod.VERSION, awgkmod.SET_ID), util.read_text(os.path.join(src, "version.h")))
        self.assertTrue(awgkmod.set_loaded())
        self.assertEqual(res["set"], True)
        dk = [c[1:] for c in self.w.commands("dkms") if c[1] != "status"]
        self.assertEqual(dk, [["add", "-m", "amneziawg", "-v", awgkmod.VERSION],
                              ["build", "-m", "amneziawg", "-v", awgkmod.VERSION, "-k", K],
                              ["install", "-m", "amneziawg", "-v", awgkmod.VERSION, "-k", K]])
        for p in awgkmod.hook_paths():
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o755)
            self.assertIn('dkms build -m amneziawg -v %s -k "$ver"' % awgkmod.VERSION, util.read_text(p))
        unit = util.read_text(awgkmod.boot_unit_path())
        self.assertIn("Before=kaskad-gw-awg1.service kaskad-gw-awg2.service kaskad-gw-awg3.service kaskad-egress.service",
                      unit)
        self.assertIn("ExecStart=-%s gw accel boot --quiet" % paths.kaskadctl(), unit)
        st = self.state()
        self.assertEqual((st["enabled"], st["kernels"], st["selftest"]["ok"], st["patched"], st["tag"]),
                         (True, [K], True, False, awgkmod.TAG))
        self.assertIn("ускорение включено", self.log())
        self.assertTrue(any("около минуты" in t for t in said))
        # the self-test: module in kt0 moved to kt1 (its socket stays in kt0), a module or amneziawg-go peer
        ip = [c[1:] for c in self.w.commands("ip")]
        self.assertIn(["-n", "kaskad-kt0", "link", "add", "dev", "kkt1a", "type", "amneziawg"], ip)
        self.assertIn(["-n", "kaskad-kt0", "link", "set", "dev", "kkt1a", "netns", "kaskad-kt1"], ip)
        self.assertIn(["-n", "kaskad-kt0", "link", "add", "dev", "kkt2b", "type", "amneziawg"], ip)
        self.assertIn(["netns", "exec", "kaskad-kt0", os.path.join(self.core_d, "amneziawg-go"), "kkt3b"], ip)
        self.assertEqual(len([c for c in ip if "ping" in c]), 8)         # 4 + 3.1 in its three roles + the leg of 1.0
        # 3.1: the amneziawg-go client pings the module server from kt0 (case 7), the module client from kt1 (8)
        self.assertIn(["netns", "exec", "kaskad-kt0", "ping", "-c", "3", "-i", "0.2", "-W", "2", "10.199.7.1"], ip)
        self.assertIn(["netns", "exec", "kaskad-kt1", "ping", "-c", "3", "-i", "0.2", "-W", "2", "10.199.8.2"], ip)
        self.assertIn("3.1 и нога 1.0 с набором %s" % awgkmod.SET_ID, st["selftest"]["detail"])
        # the leg of 1.0: the module client from kt1 (case 9); the leg of 1.5 with I1 is the base case 3
        self.assertIn(["netns", "exec", "kaskad-kt1", "ping", "-c", "3", "-i", "0.2", "-W", "2", "10.199.9.2"], ip)
        self.assertIn(["netns", "exec", "kaskad-kt1", "ping", "-c", "3", "-i", "0.2", "-W", "2", "10.199.3.2"], ip)
        self.assertEqual(ip[-1], ["netns", "del", "kaskad-kt0"])                     # cleaned up

    def test_a_newer_installed_kernel_is_built_too(self):
        newer, older = "6.8.0-51-generic", "6.8.0-45-generic"
        for k in (newer, older):
            os.makedirs(os.path.join(awgkmod.kernel_tree(k), "include", "net"), exist_ok=True)
        with open(os.path.join(awgkmod.kernel_tree(newer), "include", "net", "udp_tunnel.h"), "w") as f:
            f.write(NEW_HDR)                             # the upgrade backported the new signature
        res = awgkmod.install(self.w.r)
        builds = [c[-1] for c in self.w.commands("dkms") if c[1] == "build"]
        self.assertEqual(builds, [K, newer])            # the older kernel is left alone
        self.assertEqual(self.state()["kernels"], [K, newer])
        self.assertEqual(len([c for c in self.w.commands("patch") if c[-1].endswith(awgkmod.PATCH_NAME)]), 1)
        self.assertEqual(res["warnings"], [])
        self.assertIn("ядро %s: новая сигнатура" % newer, self.log())

    def test_the_new_signature_gets_patch_218(self):
        self.w.header = NEW_HDR
        awgkmod.install(self.w.r)
        src = awgkmod.src_dir()
        self.assertEqual([c for c in self.w.commands("patch") if c[-1].endswith(awgkmod.PATCH_NAME)],
                         [["patch", "-d", src, "-p2", "-N", "-s", "-i", os.path.join(src, awgkmod.PATCH_NAME)]])
        self.assertTrue(awgkmod.set_in_src())                                         # #218 and the set together
        self.assertTrue(self.state()["patched"])
        self.assertIn("патч #218 наложен", self.log())

    def test_a_sha256_mismatch_refuses(self):
        self.w.tar = tarball({"src/evil.c": b"evil"})
        with self.assertRaises(KaskadError) as cm:
            awgkmod.install(self.w.r)
        self.assertIn("не тот", str(cm.exception))
        self.assertFalse(os.path.exists(awgkmod.src_dir()))
        self.assertFalse([c for c in self.w.commands("dkms") if c[1] != "status"])
        st = self.state()
        self.assertEqual(st["enabled"], False)
        self.assertIn("не тот", st["error"])

    def test_no_sha256_in_the_code_refuses_before_the_download(self):
        with mock.patch.object(awgkmod, "SHA256", ""):
            with self.assertRaises(KaskadError) as cm:
                awgkmod.install(self.w.r)
        self.assertIn("не задан sha256", str(cm.exception))
        self.assertEqual(self.w.commands("curl"), [])

    def test_a_failed_build_names_the_compiler_error(self):
        self.w.build_fail = "passing argument 2 of 'setup_udp_tunnel_sock' from incompatible pointer type"
        with self.assertRaises(KaskadError) as cm:
            awgkmod.install(self.w.r)
        self.assertIn("не собрался под ядро %s" % K, str(cm.exception))
        self.assertIn("incompatible pointer type", str(cm.exception))
        self.assertFalse(self.state()["enabled"])

    def test_a_failed_self_test_keeps_the_program(self):
        self.w.ping_fail = {4}                                     # kernel <-> amneziawg-go, line 2.0
        with mock.patch.object(awgkmod, "switch") as switch:
            with self.assertRaises(KaskadError) as cm:
                awgkmod.install(self.w.r)
        switch.assert_not_called()
        self.assertIn("Самопроверка модуля не прошла", str(cm.exception))
        self.assertIn("ядро↔amneziawg-go 2.0: ping через туннель не прошёл", str(cm.exception))
        st = self.state()
        self.assertEqual((st["enabled"], st["selftest"]["ok"]), (False, False))
        self.assertIn(["modprobe", "-r", "amneziawg"], self.w.calls)
        self.assertFalse(os.path.isdir(awgkmod.module_dir()))
        self.assertEqual(awgkmod.status(self.w.r)["enabled"], False)

    def test_a_foreign_module_is_left_alone(self):
        self.w.r.binaries.add("dkms")
        self.w.r._handlers.insert(0, (["dkms", "status"], (0, "amneziawg/1.0.20251004, %s, x86_64: installed\n" % K, "")))
        with self.assertRaises(KaskadError) as cm:
            awgkmod.install(self.w.r)
        self.assertIn("не от Kaskad", str(cm.exception))
        self.assertEqual(self.w.apt_calls, [])

    def test_on_again_does_not_rebuild(self):
        awgkmod.install(self.w.r)
        n = len(self.w.commands("curl"))
        awgkmod.install(self.w.r)
        self.assertEqual(len(self.w.commands("curl")), n)
        self.assertEqual(len([c for c in self.w.commands("dkms") if c[1] == "build"]), 1)


class SetTest(KmodCase):
    """The set of 3.1 (vendor/awg-kmod-31.patch): applied after a dry run, taken off again when it does not apply,
    build or pass its self-test — the module without it, 3.1 on amneziawg-go; the update of a node whose module lacks
    it (upgrade): rebuild, reload with the lines on the program for a moment, self-test, the lines back."""

    def no_mix(self):
        from kaskad.gw import awgengine
        return mock.patch.object(awgengine, "mix", lambda runner=None, st=None, pool=None, k31=None: [])

    def test_the_vendored_set_is_there_with_its_mark(self):
        text = util.read_text(awgkmod.set_source())
        self.assertIn("#253", text)
        self.assertIn('+#define WIREGUARD_VERSION "%s+%s"' % (awgkmod.VERSION, awgkmod.SET_ID), text)
        for f in ("device.c", "header_protection.c", "header_protection.h", "junk.c", "netlink.c", "receive.c",
                  "send.c", "timers.c", "version.h"):
            self.assertIn("+++ b/src/%s" % f, text)
        self.assertIn("read_lock_bh(&p->lock)", text)

    def test_a_set_that_does_not_apply(self):
        self.w.set_apply_fail = True
        with self.no_mix():
            res = awgkmod.install(self.w.r)
        self.assertEqual(res["set"], False)
        self.assertFalse(awgkmod.set_in_src())
        self.assertFalse(awgkmod.set_loaded())
        self.assertNotIn(["patch", "-d", awgkmod.src_dir(), "-p2", "-N", "-s", "-i",
                          os.path.join(awgkmod.src_dir(), awgkmod.SET_NAME)], self.w.calls)   # only the dry run
        st = self.state()
        self.assertEqual((st["enabled"], st["set_failed"]["kernel"]), (True, K))
        self.assertTrue(any("набор исправлений 3.1 не встал" in w for w in res["warnings"]))
        self.assertEqual(len([c for c in self.w.commands("ip") if "ping" in c]), 4)          # no 3.1 cases
        self.assertEqual(awgkmod.kernel_lines(), ("1.0", "1.5", "2.0"))
        self.assertEqual(awgkmod.kernel_kinds(), ("awg1", "awg2"))

    def test_a_set_that_does_not_build_comes_off(self):
        self.w.set_build_fail = "implicit declaration of function 'read_lock_bh'"
        with self.no_mix():
            res = awgkmod.install(self.w.r)
        self.assertEqual(res["set"], False)
        src = awgkmod.src_dir()
        self.assertIn(["patch", "-d", src, "-p2", "-R", "-s", "-i", os.path.join(src, awgkmod.SET_NAME)], self.w.calls)
        self.assertFalse(awgkmod.set_in_src())
        self.assertEqual(self.w.dkms["installed"], [K])
        self.assertIn("read_lock_bh", self.state()["set_failed"]["reason"])
        self.assertTrue(self.state()["enabled"])
        self.assertIn("набор исправлений 3.1", self.log())

    def test_a_set_that_fails_its_self_test_comes_off_and_the_module_is_reloaded(self):
        self.w.ping_fail_set = {7}                       # amneziawg-go client -> the module server of 3.1
        with self.no_mix():
            res = awgkmod.install(self.w.r)
        self.assertEqual(res["set"], False)
        self.assertFalse(awgkmod.set_loaded())
        self.assertTrue(awgkmod.module_loaded())
        st = self.state()
        self.assertTrue(st["enabled"])
        self.assertTrue(st["selftest"]["ok"])
        self.assertIn("самопроверка: amneziawg-go-клиент → ядро 3.1", st["set_failed"]["reason"])
        self.assertIn(["rmmod", "amneziawg"], self.w.calls)                         # the module with the set left
        self.assertEqual([c[1] for c in self.w.commands("dkms") if c[1] in ("build", "remove")],
                         ["build", "remove", "build"])

    def test_the_role_of_a_leg_fails_the_set(self):
        self.w.ping_fail_set = {8}                       # the module client -> an amneziawg-go server of 3.1
        with self.no_mix():
            res = awgkmod.install(self.w.r)
        self.assertEqual(res["set"], False)              # legs of 3.1 rely on it: the module without the set
        self.assertTrue(self.state()["selftest"]["ok"])
        self.assertIn("ядро-клиент → amneziawg-go 3.1", self.state()["set_failed"]["reason"])
        self.assertEqual(awgkmod.leg_lines(), ("2.0",))

    def test_the_leg_of_10_fails_the_set_too(self):
        self.w.ping_fail_set = {9}                       # the module client -> an amneziawg-go server of 1.0
        with self.no_mix():
            res = awgkmod.install(self.w.r)
        self.assertEqual(res["set"], False)
        self.assertIn("ядро-клиент → amneziawg-go 1.0", self.state()["set_failed"]["reason"])
        self.assertEqual(awgkmod.leg_lines(), ("2.0",))

    def test_a_mix_after_the_set_failed_is_refused(self):
        from kaskad.gw import awgengine
        self.w.set_apply_fail = True
        mix = lambda runner=None, st=None, pool=None, k31=None: [] if k31 else ["Вход AmneziaWG 3.1: …"]
        with mock.patch.object(awgengine, "mix", mix):
            with self.assertRaises(ConflictError) as cm:
                awgkmod.install(self.w.r)
        self.assertIn("Набор исправлений 3.1 здесь не встал", str(cm.exception))
        self.assertIn("--force", str(cm.exception))
        self.assertFalse(self.state()["enabled"])
        with mock.patch.object(awgengine, "mix", mix):
            res = awgkmod.install(self.w.r, force=True)
        self.assertTrue(self.state()["enabled"])
        self.assertTrue(any("смешанный режим" in w for w in res["warnings"]))

    def old_node(self):
        """A node of the previous Kaskad: the module built and loaded without the set, acceleration on."""
        with mock.patch.object(awgkmod, "apply_set", lambda runner, src: False):
            awgkmod.install(self.w.r)
        self.assertFalse(awgkmod.set_loaded())
        self.assertTrue(self.state()["enabled"])
        self.w.calls.clear()

    def test_upgrade_of_a_node_without_the_set(self):
        self.old_node()
        doc = self.state()
        doc.pop("set_failed", None)
        util.atomic_write(awgkmod.state_path(), json.dumps(doc), 0o600)
        said = []
        with mock.patch.object(awgkmod, "switch", wraps=awgkmod.switch) as sw:
            res = awgkmod.upgrade(self.w.r, progress=said.append)
        self.assertTrue(res["ran"])
        self.assertTrue(res["set"])
        self.assertTrue(awgkmod.set_loaded())
        self.assertTrue(self.state()["enabled"])
        self.assertEqual(sw.call_count, 2)                     # the lines to the program for the reload, then back
        calls = self.w.calls
        rm = calls.index(["dkms", "remove", "-m", "amneziawg", "-v", awgkmod.VERSION, "-k", K])
        self.assertLess(rm, calls.index(["dkms", "build", "-m", "amneziawg", "-v", awgkmod.VERSION, "-k", K]))
        self.assertLess(calls.index(["rmmod", "amneziawg"]), calls.index(["modprobe", "amneziawg"]))
        self.assertTrue(any("Перезагружаю модуль" in t for t in said))
        self.assertEqual(self.w.commands("curl"), [])                                 # the sources were there
        self.assertEqual(awgkmod.upgrade(self.w.r)["ran"], False)                    # once

    def test_upgrade_does_nothing_where_it_should_not(self):
        self.assertEqual(awgkmod.upgrade(self.w.r)["why"], "ускорение выключено")
        self.old_node()                                        # here apply_set was skipped: set_failed is not set
        doc = self.state()
        doc["set_failed"] = {"kernel": K, "reason": "error: x"}
        util.atomic_write(awgkmod.state_path(), json.dumps(doc), 0o600)
        res = awgkmod.upgrade(self.w.r)
        self.assertEqual(res["ran"], False)
        self.assertIn("error: x", res["why"])
        self.assertEqual([c for c in self.w.calls if c[0] in ("dkms", "patch") and c[1] != "status"], [])
        doc["set_failed"]["kernel"] = "6.8.0-49-generic"                 # another kernel: try again
        util.atomic_write(awgkmod.state_path(), json.dumps(doc), 0o600)
        self.assertTrue(awgkmod.upgrade(self.w.r)["ran"])
        shutil.rmtree(awgkmod.module_dir())
        self.assertEqual(awgkmod.upgrade(self.w.r)["why"], "модуль не загружен")

    def test_an_upgrade_that_cannot_unload_keeps_the_old_module(self):
        self.old_node()
        doc = self.state()
        doc.pop("set_failed", None)
        util.atomic_write(awgkmod.state_path(), json.dumps(doc), 0o600)
        self.w.r._handlers.insert(0, (["ip", "-o", "link", "show", "type", "amneziawg"],
                                      (0, "7: wg-foreign: <POINTOPOINT,NOARP,UP> mtu 1420\n", "")))
        res = awgkmod.upgrade(self.w.r)
        self.assertTrue(res["ran"])
        self.assertFalse(awgkmod.set_loaded())
        self.assertTrue(self.state()["enabled"])
        self.assertIn("загрузится после перезагрузки", res["warnings"][0])
        self.assertNotIn(["rmmod", "amneziawg"], self.w.calls)

    def test_cli_upgrade(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(sysrun, "_default", self.w.r):
            self.assertEqual(cli.main(["gw", "accel", "upgrade", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["ran"], False)


class BootRemoveTest(KmodCase):
    def awg2_kernel_unit(self):
        ad = registry.get("awg2")
        text = ad.unit_texts(self.w.r, core_dir=self.core_d, mode="kernel")[ad.unit]
        self.write("/etc/systemd/system/%s" % ad.unit, text, 0o644)
        return ad

    def test_boot_without_the_module_puts_the_lines_on_the_program(self):
        awgkmod.install(self.w.r)
        ad = self.awg2_kernel_unit()
        self.assertEqual(ad.unit_core(), "kernel")
        shutil.rmtree(awgkmod.module_dir())                     # reboot into a kernel the module was not built for
        self.w.kernel = "7.0.0-38-generic"
        self.w.module_ok = False
        res = awgkmod.boot(self.w.r)
        self.assertEqual((res["loaded"], res["fallback"]["reason"]), (False, "модуль не собрался под ядро 7.0.0-38-generic"))
        self.assertEqual(ad.unit_core(), "userspace")
        self.assertNotIn(["systemctl", "restart", ad.unit], self.w.r.commands(["systemctl"]))   # boot: no restart
        self.assertEqual(self.state()["fallback"]["kernel"], "7.0.0-38-generic")
        self.assertIn("ядро 7.0.0-38-generic: модуль не собрался", self.log())
        st = awgkmod.status(self.w.r)
        self.assertTrue(any("модуль ядра не загружен для ядра 7.0.0-38-generic" in w for w in st["warnings"]))
        self.assertIn("откат на amneziawg-go", "\n".join(awgkmod.lines(st)))
        self.w.module_ok = True                                 # the hook built it, next boot
        res = awgkmod.boot(self.w.r)
        self.assertEqual((res["loaded"], res["fallback"]), (True, None))
        self.assertEqual(ad.unit_core(), "kernel")
        self.assertIsNone(self.state()["fallback"])

    def test_boot_with_acceleration_off_does_nothing(self):
        self.assertEqual(awgkmod.boot(self.w.r), {"ok": True, "enabled": False, "units": []})
        self.assertEqual(self.w.calls, [])

    def test_remove(self):
        awgkmod.install(self.w.r)
        self.w.r._handlers.insert(0, (["ip", "-o", "link", "show", "type", "amneziawg"], (0, "", "")))
        res = awgkmod.remove(self.w.r)
        self.assertEqual(res["warnings"], [])
        self.assertIn(["dkms", "remove", "-m", "amneziawg", "-v", awgkmod.VERSION, "--all"], self.w.calls)
        # выгрузка — до dkms remove и через rmmod (после remove modprobe -r не находит модуль)
        self.assertLess(self.w.calls.index(["rmmod", "amneziawg"]),
                        self.w.calls.index(["dkms", "remove", "-m", "amneziawg", "-v", awgkmod.VERSION, "--all"]))
        for p in awgkmod.hook_paths() + [awgkmod.boot_unit_path(), awgkmod.state_path(), awgkmod.src_dir()]:
            self.assertFalse(os.path.exists(p), p)
        self.assertIn(["systemctl", "disable", "--now", awgkmod.BOOT_UNIT], self.w.r.commands(["systemctl"]))

    def test_disable_keeps_the_module_built_and_on_comes_back_without_a_build(self):
        awgkmod.install(self.w.r)
        self.w.r._handlers.insert(0, (["ip", "-o", "link", "show", "type", "amneziawg"], (0, "", "")))
        res = awgkmod.disable(self.w.r)
        self.assertEqual(res["warnings"], [])
        self.assertFalse(awgkmod.enabled())
        self.assertFalse(awgkmod.module_loaded())
        self.assertNotIn(["dkms", "remove", "-m", "amneziawg", "-v", awgkmod.VERSION, "--all"], self.w.calls)
        for p in awgkmod.hook_paths() + [awgkmod.boot_unit_path(), awgkmod.src_dir()]:
            self.assertTrue(os.path.exists(p), p)                   # «программа»: the way back is a minute
        self.w.calls.clear()
        awgkmod.install(self.w.r)
        self.assertTrue(awgkmod.enabled())
        self.assertFalse([c for c in self.w.calls if c[:2] == ["dkms", "build"] or c[0] == "curl"])

    def test_status_and_cli(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(sysrun, "_default", self.w.r):
            self.assertEqual(cli.main(["gw", "accel", "status", "--json"]), 0)
        doc = json.loads(out.getvalue())
        self.assertEqual((doc["enabled"], doc["tag"], doc["kernel_lines"], doc["leg_lines"]),
                         (False, awgkmod.TAG, ["1.0", "1.5", "2.0", "3.1"], ["2.0", "3.1", "1.5", "1.0"]))
        self.assertEqual(doc["set"]["id"], awgkmod.SET_ID)
        self.assertTrue(doc["host"]["ok"])
        self.assertIn("выключено", awgkmod.lines(doc)[0])


class HookTest(KmodCase):
    """The kernel hook is sh run by run-parts during a kernel upgrade: run it for real with fake dkms and patch."""

    def run_hook(self, header, dkms_rc=0):
        src = awgkmod.src_dir()
        os.makedirs(os.path.join(src, "compat"), exist_ok=True)
        with open(os.path.join(src, "compat", "compat.h"), "wb") as f:
            f.write(COMPAT)
        open(os.path.join(src, awgkmod.PATCH_NAME), "w").close()
        kv = "7.0.0-38-generic"
        h = os.path.join(awgkmod.kernel_tree(kv), "include", "net")
        os.makedirs(h, exist_ok=True)
        with open(os.path.join(h, "udp_tunnel.h"), "w") as f:
            f.write(header)
        bin_d = self.path("/fakebin")
        os.makedirs(bin_d, exist_ok=True)
        log = self.path("/calls.log")
        for name, body in (("dkms", 'echo "dkms $*" >> "%s"\n[ "$1" = status ] && [ %d = 0 ] && echo "amneziawg/x, k, '
                                    'x86_64: installed"\nexit %d\n' % (log, dkms_rc, dkms_rc)),
                           ("patch", 'echo "patch $*" >> "%s"\nexit 0\n' % log)):
            with open(os.path.join(bin_d, name), "w") as f:
                f.write("#!/bin/sh\n" + body)
            os.chmod(os.path.join(bin_d, name), 0o755)
        os.makedirs(os.path.dirname(paths.log()), exist_ok=True)
        awgkmod.write_hooks()
        hook = awgkmod.hook_paths()[0]
        rc = subprocess.call(["sh", hook, kv], env={"PATH": bin_d + ":/usr/bin:/bin"})
        return rc, (util.read_text(log) or "")

    def test_new_signature_patches_then_builds(self):
        rc, calls = self.run_hook(NEW_HDR)
        src = awgkmod.src_dir()
        self.assertEqual(rc, 0)
        self.assertIn("patch -d %s -p2 -N -s -i %s/%s" % (src, src, awgkmod.PATCH_NAME), calls)
        self.assertIn("dkms build -m amneziawg -v %s -k 7.0.0-38-generic" % awgkmod.VERSION, calls)
        self.assertIn("dkms install -m amneziawg -v %s -k 7.0.0-38-generic" % awgkmod.VERSION, calls)
        self.assertIn("патч #218 наложен", self.log())
        self.assertIn("модуль собран под ядро 7.0.0-38-generic", self.log())

    def test_old_signature_no_patch_and_a_failure_never_fails_the_kernel(self):
        rc, calls = self.run_hook(OLD_HDR, dkms_rc=10)
        self.assertEqual(rc, 0)
        self.assertNotIn("patch", calls)
        self.assertIn("модуль НЕ собрался под ядро 7.0.0-38-generic", self.log())

    def test_a_kernel_where_the_set_does_not_build_gets_the_module_without_it(self):
        src = awgkmod.src_dir()
        os.makedirs(src, exist_ok=True)
        with open(os.path.join(src, "version.h"), "w") as f:
            f.write('#define WIREGUARD_VERSION "3.1.20260812+%s"\n' % awgkmod.SET_ID)
        open(os.path.join(src, awgkmod.SET_NAME), "w").close()
        rc, calls = self.run_hook(OLD_HDR, dkms_rc=10)
        self.assertEqual(rc, 0)
        self.assertIn("patch -d %s -p2 -R -s --dry-run -i %s/%s" % (src, src, awgkmod.SET_NAME), calls)
        self.assertIn("patch -d %s -p2 -R -s -i %s/%s" % (src, src, awgkmod.SET_NAME), calls)
        self.assertIn("dkms remove -m amneziawg -v %s -k 7.0.0-38-generic" % awgkmod.VERSION, calls)
        self.assertEqual(calls.count("dkms build"), 2)
        self.assertIn("с набором исправлений 3.1 модуль не собрался", self.log())

    def test_nothing_without_the_sources(self):
        awgkmod.write_hooks()
        rc = subprocess.call(["sh", awgkmod.hook_paths()[1], "7.0.0-38-generic"], env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(rc, 0)


class VendorSetOnTheTagTest(RootCase):
    """The set of 3.1 and patch #218 apply to the pinned tag (the real archive, checked against SHA256): ``patch
    --dry-run`` on src/ of the tag, on the tag + #218 and #218 on the tag + the set. Needs github.com: without it (or
    with KASKAD_OFFLINE=1, or without patch) — skipped with the reason. KASKAD_KMOD_ARCHIVE — a local copy."""

    def archive(self):
        import unittest
        import urllib.request
        if not shutil.which("patch"):
            raise unittest.SkipTest("нет программы patch")
        local = os.environ.get("KASKAD_KMOD_ARCHIVE")
        if local:
            with open(local, "rb") as f:
                data = f.read()
        else:
            if os.environ.get("KASKAD_OFFLINE") == "1":
                raise unittest.SkipTest("KASKAD_OFFLINE=1: архив модуля с github.com не качаю")
            try:
                with urllib.request.urlopen(awgkmod.URL, timeout=20) as r:
                    data = r.read(8 << 20)
            except Exception as e:                          # noqa: BLE001 — no network is a skip, not a failure
                raise unittest.SkipTest("github.com недоступен (%s): наложение набора на тег не проверено" % e)
        self.assertEqual(hashlib.sha256(data).hexdigest(), awgkmod.SHA256)
        p = os.path.join(self.root, "tag.tar.gz")
        with open(p, "wb") as f:
            f.write(data)
        return p

    def src(self, archive, name):
        d = os.path.join(self.root, name)
        os.makedirs(d)
        awgkmod.unpack(archive, d)
        return d

    def patch(self, src, path, dry=True):
        argv = ["patch", "-d", src, "-p2", "-N", "-s"] + (["--dry-run"] if dry else []) + ["-i", path]
        res = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(res.returncode, 0, res.stdout)

    def test_the_set_and_218_apply_to_the_tag(self):
        archive = self.archive()
        a = self.src(archive, "a")
        self.patch(a, awgkmod.set_source())                                       # the tag + the set
        self.patch(a, awgkmod.set_source(), dry=False)
        self.assertTrue(awgkmod.set_in_src(a))
        self.patch(a, awgkmod.patch_source())                                     # + #218 on top
        res = subprocess.run(["patch", "-d", a, "-p2", "-R", "-s", "--dry-run", "-i", awgkmod.set_source()],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(res.returncode, 0)                                       # and it comes off again
        b = self.src(archive, "b")
        self.patch(b, awgkmod.patch_source(), dry=False)                          # the tag + #218 (Ubuntu 26.04)
        self.patch(b, awgkmod.set_source())                                       # + the set
