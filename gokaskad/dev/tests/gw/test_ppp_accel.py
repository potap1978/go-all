"""accel-ppp engine: the build from the verified tarball (sha mismatch refused, tarball and stage reused), the
vendor patch of L2TP reconnects (PPP-1) and the rebuild of an earlier build, the kernel and foreign-daemon
checks, the unit, golden configs (accel-ppp.conf, chap-secrets, allow lists, scripts)."""

import hashlib
import json
import os
import re

from kaskad import paths
from tests.gw.ppp_case import INBOUNDS, PppCase, device, gw_state


def devices():
    return [
        device("d-router", "Роутер на даче", "keenetic", ["l2tp"], login="dacha-router", kind="router"),
        device("d-win", "Ноутбук", "windows", ["sstp", "ikev2", "pptp"], login="noutbuk-1a2b", kind="laptop"),
        device("d-mac", "Макбук", "macos", ["ikev2"], login="makbuk-3c4d", kind="laptop"),
        device("d-off", "Старый телефон", "android", ["l2tp"], login="old-phone", enabled=False),
        device("d-bad", "Плохой логин", "tplink", ["pptp"], login="../etc", kind="router"),
    ]


class AccelCase(PppCase):
    def setUp(self):
        super().setUp()
        self.acc = self.gw.accel
        self.st = gw_state(INBOUNDS, devices())
        self.sec.update({"dev.d-router.password": "RouterPass2345", "dev.d-win.password": "WinPass6789xyz",
                         "dev.d-mac.password": "MacPass2345abc"})


class BuildTest(AccelCase):
    def test_build_from_scratch(self):
        how = self.acc.install(self.runner, "pptp")
        self.assertEqual(how, "собран")
        self.assertEqual(self.node.apt, [(self.acc.BUILD_DEPS, True)])                 # under policy-rc.d = 101
        self.assertFalse(os.path.exists(paths.P("/usr/sbin/policy-rc.d")))
        self.assertEqual(self.node.builds, ["curl", "tar", "patch", "cmake", "make", "make install"])
        src = os.path.join(self.acc.build_dir(), "src", "accel-ppp-1.14.0")
        self.assertEqual(self.node.commands("patch"), [["patch", "-d", src, "-p1", "-N", "-s", "-i",
                                                         self.acc.patch_path("accel-ppp-l2tp-peer-restart.patch")]])
        a = self.node.cmake_args
        self.assertEqual(a[:4], ["-S", os.path.join(self.acc.build_dir(), "src", "accel-ppp-1.14.0"), "-B",
                                 os.path.join(self.acc.build_dir(), "build")])
        self.assertEqual(a[4:], ["-DBUILD_IPOE_DRIVER=FALSE", "-DBUILD_VLAN_MON_DRIVER=FALSE",
                                 "-DCMAKE_INSTALL_PREFIX=/opt/kaskad/accel-ppp", "-DLIB_SUFFIX=",
                                 "-DCMAKE_BUILD_TYPE=Release", "-DLUA=TRUE", "-DSHAPER=TRUE", "-DRADIUS=TRUE"])
        curl = self.node.commands("curl")[0]
        self.assertEqual(curl[-1], "https://github.com/accel-ppp/accel-ppp/archive/refs/tags/1.14.0.tar.gz")
        self.assertTrue(os.path.isfile(self.acc.accel_pppd()))
        self.assertTrue(os.path.isfile(self.acc.tarball_path()))                         # kept for a rebuild
        self.assertTrue(self.acc.stage_ok())                                             # kept for a reinstall
        self.assertFalse(os.path.exists(os.path.join(self.acc.build_dir(), "src")))      # intermediate trees gone
        self.assertFalse(os.path.exists(os.path.join(self.acc.build_dir(), "build")))
        marker = json.loads(self.read("/opt/kaskad/accel-ppp/.kaskad-build.json"))
        self.assertEqual((marker["version"], marker["sha256"]), ("1.14.0", self.acc.SHA256))
        with open(self.acc.patch_path("accel-ppp-l2tp-peer-restart.patch"), "rb") as f:
            self.assertEqual(marker["patches"], [["accel-ppp-l2tp-peer-restart.patch",
                                                  hashlib.sha256(f.read()).hexdigest()]])
        self.assertIn([self.acc.accel_pppd(), "-V"], self.node.commands())
        self.assertEqual(self.node.modprobed, ["ppp_generic", "pptp", "gre", "ppp_mppe"])
        self.assertTrue(os.path.exists(os.path.join(paths.systemd_dir(), "kaskad-gw-accel.service")))
        self.assertEqual(self.acc.installed_kinds(), ["pptp"])
        self.assertIn("gw pptp: установлен: accel-ppp 1.14.0 (собран)", self.log_text())

    def test_sha_mismatch_refuses_and_deletes(self):
        self.node.download = b"something else from the network"
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.acc.install(self.runner, "pptp")
        self.assertIn("не совпал с проверенным", str(cm.exception))
        self.assertIn("архив удалён", str(cm.exception))
        self.assertEqual(self.node.builds, ["curl"])                                     # nothing unpacked
        self.assertEqual(os.listdir(self.acc.pkg_dir()), [])
        self.assertFalse(os.path.exists(self.acc.install_dir()))
        self.assertEqual(self.acc.installed_kinds(), [])

    def test_kept_tarball_is_reused_and_checked(self):
        os.makedirs(self.acc.pkg_dir())
        with open(self.acc.tarball_path(), "wb") as f:
            f.write(self.node.download)
        self.acc.install(self.runner, "sstp")
        self.assertEqual(self.node.builds, ["tar", "patch", "cmake", "make", "make install"])     # no download
        self.acc.uninstall(self.runner, "sstp")
        os.unlink(self.acc.tarball_path())
        with open(self.acc.tarball_path(), "wb") as f:
            f.write(b"damaged")                                                          # a damaged copy
        import shutil
        shutil.rmtree(self.acc.install_dir())
        shutil.rmtree(self.acc.build_dir())
        self.node.builds[:] = []
        self.acc.install(self.runner, "sstp")
        self.assertEqual(self.node.builds[0], "curl")                                    # downloaded again

    def test_patch_that_does_not_apply_refuses(self):
        self.node.patch_rc = 1
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.acc.install(self.runner, "l2tp")
        self.assertIn("сборка не прошла (патч accel-ppp-l2tp-peer-restart.patch)", str(cm.exception))
        self.assertEqual(self.node.builds, ["curl", "tar", "patch"])                     # no cmake on raw sources
        self.assertFalse(os.path.exists(self.acc.install_dir()))
        self.assertFalse(self.acc.stage_ok())
        self.assertEqual(self.acc.installed_kinds(), [])

    def test_stage_is_copied_again_without_building(self):
        self.acc.install(self.runner, "pptp")
        import shutil
        shutil.rmtree(self.acc.install_dir())                                            # e.g. /opt cleaned
        self.node.builds[:] = []
        self.node.apt[:] = []
        self.assertEqual(self.acc.install(self.runner, "pptp"), "из готовой сборки")
        self.assertEqual((self.node.builds, self.node.apt), ([], []))
        self.assertTrue(os.path.isfile(self.acc.accel_pppd()))

    def test_second_inbound_reuses_the_install(self):
        self.acc.install(self.runner, "pptp")
        self.node.builds[:] = []
        self.assertEqual(self.acc.install(self.runner, "sstp"), "уже стоит")
        self.assertEqual(self.node.builds, [])
        self.assertEqual(self.acc.installed_kinds(), ["pptp", "sstp"])

    def test_kernel_without_ppp_is_refused(self):
        self.node.modules.discard("ppp_generic")
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.acc.install(self.runner, "l2tp")
        msg = str(cm.exception)
        self.assertIn("без PPP (нет модулей: ppp_generic)", msg)
        self.assertIn("ядро сам не меняет", msg)
        self.assertEqual((self.node.apt, self.node.builds), ([], []))
        req = self.adapter("pptp").requirements(self.runner)
        self.assertFalse(req["supported"])
        self.assertIn("ppp_generic", req["why"])

    def test_build_tools_are_needed_only_before_the_build(self):
        self.assertEqual(self.adapter("pptp").requirements(self.runner)["packages"], self.acc.BUILD_DEPS)
        self.assertEqual(self.adapter("l2tp").requirements(self.runner)["packages"],
                         self.acc.BUILD_DEPS + self.gw.ipsec.PACKAGES)
        self.assertIn("из исходников", self.adapter("sstp").requirements(self.runner)["build"])
        self.acc.install(self.runner, "pptp")
        self.assertEqual(self.adapter("sstp").requirements(self.runner)["packages"], [])     # built: no apt for it
        self.assertEqual(self.adapter("l2tp").requirements(self.runner)["packages"], self.gw.ipsec.PACKAGES)
        # stand T-G2b, N-6: the menu promises «собирается — до двух минут» only when the install really builds
        for kind in ("l2tp", "sstp", "pptp"):
            self.assertEqual(self.adapter(kind).requirements(self.runner)["build"], "", kind)

    def test_missing_inbound_module_and_foreign_daemon(self):
        self.node.modules.discard("l2tp_ppp")
        self.assertIn("l2tp_ppp", self.adapter("l2tp").requirements(self.runner)["why"])
        self.assertTrue(self.adapter("pptp").requirements(self.runner)["supported"])     # PPTP does not need it
        self.node.modules.add("l2tp_ppp")
        self.node.sysd.u("xl2tpd.service")["active"] = True
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.acc.install(self.runner, "l2tp")
        self.assertIn("xl2tpd.service", str(cm.exception))
        self.assertEqual(self.node.builds, [])

    def test_binary_that_does_not_run_and_failed_download(self):
        self.node.pppd_runs = False
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.acc.install(self.runner, "pptp")
        self.assertIn("не запускается", str(cm.exception))
        self.node.pppd_runs = True
        self.node.curl_rc = 6
        import shutil
        shutil.rmtree(self.acc.pkg_dir())
        shutil.rmtree(self.acc.install_dir())
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.acc.install(self.runner, "pptp")
        self.assertIn("не скачался архив", str(cm.exception))

    def test_uninstall_keeps_the_engine_until_the_last_inbound(self):
        self.acc.install(self.runner, "pptp")
        self.acc.install(self.runner, "sstp")
        self.assertFalse(self.acc.uninstall(self.runner, "pptp"))
        self.assertTrue(os.path.exists(os.path.join(paths.systemd_dir(), "kaskad-gw-accel.service")))
        self.assertTrue(self.acc.uninstall(self.runner, "sstp"))
        self.assertFalse(os.path.exists(os.path.join(paths.systemd_dir(), "kaskad-gw-accel.service")))
        self.assertFalse(os.path.exists(self.acc.conf_dir()))
        self.assertTrue(os.path.isfile(self.acc.accel_pppd()))                           # the build stays
        self.assertEqual(self.node.xfrm, ["xfrm policy delete dir in src 0.0.0.0/0 dst 0.0.0.0/0 proto udp dport 1701"])


class PatchTest(AccelCase):
    """vendor/accel-ppp-l2tp-peer-restart.patch (PPP-1): an SCCRQ from the address and port of an existing L2TP
    tunnel replaces that tunnel. Its provenance, the one file it changes, the option it adds is the one
    accel-ppp.conf sets in [l2tp]."""

    def patch_text(self):
        with open(self.acc.patch_path(self.acc.PATCHES[0]), encoding="utf-8") as f:
            return f.read()

    def test_provenance_and_the_file_it_changes(self):
        text = self.patch_text()
        with open(self.acc.__file__, encoding="utf-8") as f:
            sha = re.search(r'^SHA256 = "([0-9a-f]{64})"$', f.read(), re.M).group(1)    # setUp swaps the module's
        self.assertIn("sha256 %s" % sha, text)
        self.assertIn(self.acc.URL, text)
        self.assertEqual(re.findall(r"^(?:---|\+\+\+) (\S+)", text, re.M),
                         ["a/" + self.acc.PATCHED[0], "b/" + self.acc.PATCHED[0]])

    def test_the_option_it_adds_is_set_for_l2tp_only(self):
        added = "\n".join(ln[1:] for ln in self.patch_text().splitlines()
                          if ln.startswith("+") and not ln.startswith("+++"))
        self.assertIn("static int %s = 0;" % self.acc.PATCHED[1], added)                 # the mark build() checks
        self.assertIn('conf_get_opt("l2tp", "peer-restart")', added)
        self.assertIn("l2tp_recv_SCCRQ(&udp_serv, pack, &pkt_info);", added)
        l2tp = self.acc.conf_text(self.st, ["l2tp"]).split("[l2tp]\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertIn("peer-restart=1", l2tp)
        self.assertNotIn("peer-restart", self.acc.conf_text(self.st, ["pptp", "sstp"]))
        self.assertEqual(self.acc.restart_key(self.acc.conf_text(self.st, ["l2tp"])),
                         self.acc.restart_key(self.acc.conf_text(self.st, ["l2tp"]).replace("peer-restart=1\n", "")))


class RenderTest(AccelCase):
    def rendered(self):
        self.acc.prepare(self.st, self.sec, self.runner)
        return self.files(self.acc.render(self.st, self.sec))

    def test_golden_files(self):
        files = self.rendered()
        d = "/etc/kaskad/gw/accel/"
        self.assertEqual(sorted(files), sorted([d + n for n in (
            "accel-ppp.conf", "chap-secrets", "ip-pre-up", "ip-up", "ip-down", "l2tp-guard.sh", "env", "accel.json",
            "users.json", "allow/l2tp", "allow/pptp", "allow/sstp")] + ["/etc/kaskad/gw/tls/server.crt",
                                                                       "/etc/kaskad/gw/tls/server.key"]))
        self.golden("accel-ppp.conf", files[d + "accel-ppp.conf"][0])
        self.golden("chap-secrets", files[d + "chap-secrets"][0])
        self.golden("ip-pre-up", files[d + "ip-pre-up"][0])
        self.golden("ip-up", files[d + "ip-up"][0])
        self.golden("l2tp-guard.sh", files[d + "l2tp-guard.sh"][0])
        self.assertEqual(files[d + "allow/l2tp"][0], "dacha-router\n")                   # d-off is disabled
        self.assertEqual(files[d + "allow/pptp"][0], "noutbuk-1a2b-pptp\n")              # d-bad has no usable login
        self.assertEqual(files[d + "allow/sstp"][0], "noutbuk-1a2b\n")
        self.assertEqual(json.loads(files[d + "users.json"][0]), {"dacha-router": "d-router", "noutbuk-1a2b": "d-win",
                                                                  "noutbuk-1a2b-pptp": "d-win"})
        self.assertEqual(files[d + "env"][0].splitlines()[-1],
                         "MODULES=ppp_generic l2tp_ppp pptp gre ppp_mppe ppp_async")
        for n in ("ip-pre-up", "ip-up", "ip-down", "l2tp-guard.sh"):
            self.assertEqual(files[d + n][1], 0o755)
        self.assertEqual(files[d + "chap-secrets"][1], 0o600)
        self.assertEqual(files["/etc/kaskad/gw/tls/server.key"][1], 0o600)

    def test_one_address_per_device_for_every_ppp_inbound(self):
        self.rendered()
        self.assertEqual(self.dev(self.st, "d-win")["ips"], {"ppp": "10.77.5.11"})
        self.assertEqual(self.dev(self.st, "d-router")["ips"], {"ppp": "10.77.5.10"})
        self.assertNotIn("ppp", self.dev(self.st, "d-mac")["ips"])                        # IKEv2 only
        self.assertIn("пропущены устройства с неподходящим логином: d-bad", self.log_text())

    def test_golden_unit(self):
        self.golden("kaskad-gw-accel.service", self.acc.unit_texts(self.runner)["kaskad-gw-accel.service"])

    def test_only_enabled_inbounds_and_their_sections(self):
        self.st["gateway"]["inbounds"]["pptp"]["enabled"] = False
        self.st["gateway"]["inbounds"]["l2tp"]["plain"] = True
        files = self.rendered()
        conf = files["/etc/kaskad/gw/accel/accel-ppp.conf"][0]
        self.assertNotIn("\npptp\n", conf)
        self.assertNotIn("[pptp]", conf)
        self.assertEqual(files["/etc/kaskad/gw/accel/allow/pptp"][0], "")
        self.assertIn("GUARD=0", files["/etc/kaskad/gw/accel/l2tp-guard.sh"][0])         # plain L2TP is on
        self.assertEqual(json.loads(files["/etc/kaskad/gw/accel/accel.json"][0]),
                         {"guard": False, "kinds": ["l2tp", "sstp"], "l2tp_ipsec": True, "l2tp_plain": True,
                          "sstp_port": 4433})

    def test_client_ip_range_is_everything_but_the_gateway(self):
        ranges = self.acc.client_ranges(self.st)
        self.assertEqual(len(ranges), 16)
        self.assertNotIn("10.77.0.0/16", ranges)
        import ipaddress
        nets = [ipaddress.ip_network(r) for r in ranges]
        self.assertFalse(any(ipaddress.ip_address("10.77.5.10") in n for n in nets))
        self.assertTrue(any(ipaddress.ip_address("198.51.100.7") in n for n in nets))
        self.assertEqual(sum(n.num_addresses for n in nets), 2 ** 32 - 2 ** 16)

    def test_password_quoting(self):
        self.sec["dev.d-router.password"] = 'pa ss"w\\rd'
        self.acc.prepare(self.st, self.sec, self.runner)
        chap = self.acc.render(self.st, self.sec)[self.acc.chap_path()][0]
        self.assertIn('dacha-router\t*\t"pa ss\\"w\\\\rd"\t10.77.5.10', chap)
        self.sec["dev.d-router.password"] = "пароль"                                     # not ASCII: left out
        chap = self.acc.render(self.st, self.sec)[self.acc.chap_path()][0]
        self.assertNotIn("dacha-router", chap)
