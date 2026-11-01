"""AmneziaWG inbounds (awg1/awg2/awg3): golden configs per line, the core, apply, issuance per OS."""

import io
import json
import os
import stat
import urllib.error
from unittest import mock

from kaskad import paths
from kaskad.gw.inbounds import awg as awg_mod
from kaskad.vendor import awgkeys
from tests.gw.tunnel_case import PARAMS, GwCase, device, gw_state, key, slurp

INBOUNDS = {
    "awg1": {"enabled": True, "port": 31871, "subnet": "10.77.2.0/24", "line": "1.5", "params": PARAMS["1.5"]},
    "awg2": {"enabled": True, "port": 41023, "subnet": "10.77.3.0/24", "line": "2.0", "params": PARAMS["2.0"]},
    "awg3": {"enabled": True, "port": 51911, "subnet": "10.77.4.0/24", "line": "3.1", "params": PARAMS["3.1"]},
}
LINES = {"awg1": "1.5", "awg2": "2.0", "awg3": "3.1"}


def devices():
    return [
        device("d-phone", "Телефон Мамы", "ios", ["awg1", "awg2", "awg3"],
               ips={"awg1": "10.77.2.10", "awg2": "10.77.3.10", "awg3": "10.77.4.10"}),
        device("d-keen", "Keenetic на даче", "keenetic", ["awg2"], kind="router", ips={"awg2": "10.77.3.11"}),
    ]


class AwgCase(GwCase):
    def setUp(self):
        super().setUp()
        self.st = gw_state(INBOUNDS, devices())
        self._roots = os.environ.get("KASKAD_AWG_ROOTS")
        os.environ["KASKAD_AWG_ROOTS"] = self.path("/srv/kaskad-pro")
        self._env = {k: os.environ.pop(k, None) for k in ("GH_TOKEN", "GITHUB_TOKEN", "GOINST_TOKEN_FILE")}
        os.environ["GOINST_TOKEN_FILE"] = self.path("/no-goinstaller-token")   # no GitHub key unless a test gives one

    def tearDown(self):
        if self._roots is None:
            os.environ.pop("KASKAD_AWG_ROOTS", None)
        else:
            os.environ["KASKAD_AWG_ROOTS"] = self._roots
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def github_key(self, value="ghp_TESTKEY"):
        os.makedirs(os.path.dirname(paths.config()), exist_ok=True)
        with open(paths.config(), "a", encoding="utf-8") as f:
            f.write('GITHUB_PAT="%s"\n' % value)

    def seed(self):
        for kind in INBOUNDS:
            self.sec["%s.server.private" % kind] = key("srv-" + kind)
            self.sec["dev.d-phone.%s.private" % kind] = key("dev1-" + kind)
            self.sec["dev.d-phone.%s.psk" % kind] = key("psk1")
        self.sec["dev.d-keen.awg2.private"] = key("dev2-awg2")
        self.sec["dev.d-keen.awg2.psk"] = key("psk2")

    def core(self, where, v3=True):
        """Fake core binaries: the 3.1 markers decide whether they count."""
        d = self.path(where)
        os.makedirs(d, exist_ok=True)
        for name, marker in (("amneziawg-go", b"disable_cookies"), ("awg", b"DisableCookies")):
            with open(os.path.join(d, name), "wb") as f:
                f.write(b"\x7fELF fake " + (marker if v3 else b"old core"))
            os.chmod(os.path.join(d, name), 0o755)
        return d

    def source(self, arch="amd64", v3=True):
        d = self.path("/srv/kaskad-pro/bin")
        os.makedirs(d, exist_ok=True)
        for name, marker in (("amneziawg-go", b"disable_cookies"), ("awg", b"DisableCookies")):
            with open(os.path.join(d, "%s-linux-%s" % (name, arch)), "wb") as f:
                f.write(b"\x7fELF repo " + (marker if v3 else b"old"))

    def dev(self, dev_id):
        return [d for d in self.st["gateway"]["devices"] if d["id"] == dev_id][0]


class RenderTest(AwgCase):
    def test_golden_server_conf_per_line(self):
        self.seed()
        for kind in ("awg1", "awg2", "awg3"):
            with self.subTest(kind=kind):
                a = self.adapter(kind)
                self.assertEqual(a.prepare(self.st, self.sec), [])
                files = self.files(a.render(self.st, self.sec))
                conf = files["/etc/kaskad/gw/%s/kgw-%s.conf" % (kind, kind)][0]
                self.golden("%s-server.conf" % kind, conf)
                self.assertNotIn("\nAddress", conf)
                self.assertNotIn("\nMTU", conf)
                # §6.1: the server MTU fits the budget 1400 (S4 = 117 of 2.0: 1400 − 80 − 117 -> 1200); clients keep 1280
                self.assertEqual(files["/etc/kaskad/gw/%s/env" % kind][0].splitlines()[1:],
                                 ["ADDR=10.77.%d.1/24" % {"awg1": 2, "awg2": 3, "awg3": 4}[kind],
                                  "MTU=%d" % {"awg1": 1280, "awg2": 1200, "awg3": 1280}[kind]])
                params = awgkeys.params_from_conf(conf)
                self.assertEqual(awgkeys.detect_line(params), LINES[kind])

    def test_golden_unit(self):
        self.core("/etc/kaskad/egress/awg")
        a = self.adapter("awg2")
        self.golden("kaskad-gw-awg2.service", a.unit_texts(self.runner)["kaskad-gw-awg2.service"])

    def test_params_are_generated_per_line_and_kept(self):
        st = gw_state({"awg1": {"enabled": True}, "awg2": {"enabled": True}, "awg3": {"enabled": True}}, [])
        for kind in ("awg1", "awg2", "awg3"):
            a = self.adapter(kind)
            done = a.prepare(st, self.sec)
            cfg = st["gateway"]["inbounds"][kind]
            self.assertEqual(cfg["line"], LINES[kind])
            self.assertEqual(awgkeys.validate_params(cfg["line"], cfg["params"]), [])
            self.assertEqual(awgkeys.detect_line(cfg["params"]), LINES[kind])
            self.assertTrue(30000 <= cfg["port"] <= 60999)
            self.assertIn("параметры линии %s" % LINES[kind], done)
            before = dict(cfg["params"])
            self.assertEqual(a.prepare(st, self.sec), [])
            self.assertEqual(cfg["params"], before)

    def test_awg1_line_10_and_switching_lines(self):
        st = gw_state({"awg1": {"enabled": True, "line": "1.0"}}, [])
        a = self.adapter("awg1")
        a.prepare(st, self.sec)
        params = st["gateway"]["inbounds"]["awg1"]["params"]
        self.assertEqual(params["I1"], "")
        self.assertEqual(awgkeys.detect_line(params), "1.0")
        st["gateway"]["inbounds"]["awg1"]["params"] = dict(PARAMS["1.5"])          # 1.5 parameters, line 1.0
        self.assertEqual(a.prepare(st, self.sec), ["параметры приведены к линии 1.0"])
        kept = st["gateway"]["inbounds"]["awg1"]["params"]
        self.assertEqual((kept["I1"], kept["H1"]), ("", PARAMS["1.5"]["H1"]))       # obfuscation kept, I1 gone
        st["gateway"]["inbounds"]["awg1"]["line"] = "2.0"
        with self.assertRaises(self.gw.base.InboundError) as cm:
            a.prepare(st, self.sec)
        self.assertIn("1.5, 1.0", str(cm.exception))


class CoreTest(AwgCase):
    def test_reuses_the_egress_core(self):
        d = self.core("/etc/kaskad/egress/awg")
        self.source()
        a = self.adapter("awg3")
        a.install(self.runner)
        unit = slurp(os.path.join(paths.systemd_dir(), "kaskad-gw-awg3.service"))
        self.assertIn("ExecStart=%s/amneziawg-go -f kgw-awg3" % d, unit)
        self.assertIn("ExecStartPost=%s/awg setconf kgw-awg3" % d, unit)
        self.assertFalse(os.path.exists(os.path.join(paths.bin_dir(), "awg")))   # nothing copied
        self.assertEqual(self.node.modprobed, ["tun"])
        self.assertEqual(self.node.apt, [])

    def test_copies_bin_when_there_is_no_core(self):
        self.core("/etc/kaskad/egress/awg", v3=False)                            # 2.12 core: not 3.1
        self.source()
        a = self.adapter("awg2")
        a.install(self.runner)
        for name in ("amneziawg-go", "awg"):
            p = os.path.join(paths.bin_dir(), name)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o755)
            self.assertIn(b"repo", slurp(p, binary=True))
        unit = slurp(os.path.join(paths.systemd_dir(), "kaskad-gw-awg2.service"))
        self.assertIn("ExecStart=%s/amneziawg-go -f kgw-awg2" % paths.bin_dir(), unit)
        self.assertEqual([f for f in os.listdir(paths.bin_dir()) if f.startswith(".")], [])

    def test_arm64_takes_its_own_binaries(self):
        self.node.arch = "aarch64"
        self.source("amd64")
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.adapter("awg2").install(self.runner)
        self.assertIn("bin/amneziawg-go-linux-arm64", str(cm.exception))
        self.source("arm64")
        self.adapter("awg2").install(self.runner)
        self.assertTrue(os.path.exists(os.path.join(paths.bin_dir(), "awg")))

    def test_refusals(self):
        err = self.gw.base.InboundError
        self.node.arch = "riscv64"
        with self.assertRaises(err) as cm:
            self.adapter("awg2").install(self.runner)
        self.assertIn("amd64 или arm64", str(cm.exception))
        self.assertFalse(self.adapter("awg2").requirements(self.runner)["supported"])
        self.node.arch = "x86_64"
        self.source()
        self.node.core_runs = False                                               # foreign architecture
        with self.assertRaises(err) as cm:
            self.adapter("awg2").install(self.runner)
        self.assertIn("не запускается", str(cm.exception))

    def test_requirements(self):
        req = self.adapter("awg3").requirements(self.runner)
        self.assertFalse(req["supported"])
        self.assertIn("нет ядра AmneziaWG", req["why"])
        self.assertIn("ключа GitHub", req["why"])
        self.assertTrue(req["needs_core"])                   # the menu downloads the core and plans again
        self.assertTrue(req["needs_key"])                    # ...and cannot: the menu says so before «Установить»
        self.github_key()                                    # with the key: installable, the core comes at install
        req = self.adapter("awg3").requirements(self.runner)
        self.assertTrue(req["supported"])
        self.assertTrue(req["needs_core"])
        self.assertFalse(req["needs_key"])
        self.assertIn("скачается", req["warning"])
        os.unlink(paths.config())
        self.source()
        req = self.adapter("awg3").requirements(self.runner)
        self.assertTrue(req["supported"])
        self.assertFalse(req["needs_core"])
        self.assertEqual((req["packages"], req["modules"]), ([], ["tun"]))
        self.assertEqual(req["ports"][0]["proto"], "udp")


class ApplyTest(AwgCase):
    def setUp(self):
        super().setUp()
        self.seed()
        self.core("/etc/kaskad/egress/awg")
        for kind in INBOUNDS:
            self.adapter(kind).install(self.runner)

    def test_peers_sync_params_restart(self):
        a = self.adapter("awg2")
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "started")
        self.st["gateway"]["devices"].append(device("d-tab", "Планшет", "android", ["awg2"], kind="tablet"))
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "synced")
        tool, verb, iface, text = self.node.syncs[-1]
        self.assertEqual((tool, verb, iface), ("awg", "syncconf", "kgw-awg2"))
        self.assertIn("AllowedIPs = 10.77.3.12/32", text)
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-awg2.service"))
        self.st["gateway"]["inbounds"]["awg2"]["params"]["Jc"] = "7"             # obfuscation changed
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.st["gateway"]["inbounds"]["awg2"]["port"] = 41024                   # port changed
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "unchanged")

    def test_three_lines_are_independent(self):
        for kind in INBOUNDS:
            self.assertEqual(self.adapter(kind).apply(self.st, self.sec, self.runner)["action"], "started")
        self.st["gateway"]["inbounds"]["awg1"]["enabled"] = False
        self.assertEqual(self.adapter("awg1").apply(self.st, self.sec, self.runner)["action"], "stopped")
        units = self.node.sysd.units
        self.assertEqual([units["kaskad-gw-%s.service" % k]["active"] for k in ("awg1", "awg2", "awg3")],
                         [False, True, True])
        self.assertEqual(self.adapter("awg3").ifaces(), ["kgw-awg3"])

    def test_status_uses_the_core_tool(self):
        a = self.adapter("awg3")
        a.apply(self.st, self.sec, self.runner)
        self.gw.tunnel.now = lambda: 1759579200
        pub = self.gw.wg.pubkey(key("dev1-awg3"))
        self.node.handshakes["kgw-awg3"] = "%s\t1759579190\n" % pub
        self.node.transfer["kgw-awg3"] = "%s\t10\t20\n" % pub
        st = a.status(self.runner)
        self.assertEqual(st["port"], 51911)
        self.assertEqual(st["sessions"], [{"device": "d-phone", "ip": "10.77.4.10", "since": 1759579190, "rx": 10, "tx": 20}])
        egress_awg = os.path.join(self.path("/etc/kaskad/egress/awg"), "awg")
        self.assertIn([egress_awg, "show", "kgw-awg3", "latest-handshakes"], self.node.commands(egress_awg))
        self.assert_no_secrets(json.dumps(st))


class IssueTest(AwgCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_ios_awg3_vpn_first_and_round_trip(self):
        a = self.adapter("awg3")
        res = a.issue(self.st, self.sec, self.dev("d-phone"), "ios")
        self.assertEqual([(i["format"], i["filename"]) for i in res["items"]],
                         [("vpn", None), ("qr", None), ("qr", None), ("conf", "goKaskadPro-telefon-mamy-awg31.conf")])   # owner №31
        self.assertTrue(res["body"].startswith("vpn://"))
        back = awgkeys.vpn_decode(res["body"])
        self.assertEqual(back["line"], "3.1")
        self.assertEqual(back["client"]["privkey"], key("dev1-awg3"))
        self.assertEqual(back["client"]["ip"], "10.77.4.10")
        self.assertEqual(back["server"]["pubkey"], self.gw.wg.pubkey(key("srv-awg3")))
        self.assertEqual((back["hostName"], back["port"], back["dns"]), ("203.0.113.10", 51911, ["10.77.0.1"]))
        self.assertEqual(back["params"]["HeaderProtectionKey"], PARAMS["3.1"]["HeaderProtectionKey"])
        self.assertEqual(back["description"], "goKaskad PRO · AmneziaWG 3.1 · Телефон Мамы")   # + device (T-M6-332c №9)
        self.assertEqual(awgkeys.qr_chunks_join(res["items"][1]["body"]), res["body"])
        conf = res["items"][3]["body"]
        self.assertEqual(res["items"][2]["body"], [conf])
        self.golden("awg3-client-phone.conf", conf)
        self.assertIn("AmneziaVPN", res["notes"][0])
        self.assertTrue(any("теряет поля 3.x" in n for n in res["items"][3]["notes"]))

    def test_every_line_decodes_to_its_own_line(self):
        for kind, line in sorted(LINES.items()):
            res = self.adapter(kind).issue(self.st, self.sec, self.dev("d-phone"), "android")
            back = awgkeys.vpn_decode(res["body"])
            self.assertEqual((back["line"], back["port"]), (line, INBOUNDS[kind]["port"]))
            self.assertEqual(back["client"]["privkey"], key("dev1-" + kind))
            self.assertEqual(back["container"], "amnezia-awg2")             # the only name the app knows
            self.assertEqual(back["description"], "goKaskad PRO · AmneziaWG %s · Телефон Мамы" % line)
            conf = [i for i in res["items"] if i["format"] == "conf"][0]["filename"]
            # Android ≤ 15, three .conf protocols; the cut name ends with the id's tail (FIX-332g №16)
            self.assertEqual(conf, "gKP-te-ne-awg%s.conf" % line.replace(".", ""))
            self.assertLessEqual(len(conf) - len(".conf"), 15)

    def test_keenetic_gets_only_the_file(self):
        res = self.adapter("awg2").issue(self.st, self.sec, self.dev("d-keen"), None)
        self.assertEqual([i["format"] for i in res["items"]], ["conf"])
        self.assertTrue(any("импорт из файла" in n for n in res["notes"]))
        self.assertTrue(any("Keenetic" in n for n in res["notes"]))         # the catalog line
        res = self.adapter("awg3").issue(self.st, self.sec, self.dev("d-phone"), "keenetic")
        self.assertTrue(any("5.2 Alpha 11" in n for n in res["notes"]))

    def test_linux_conf_first(self):
        res = self.adapter("awg2").issue(self.st, self.sec, self.dev("d-phone"), "linux")
        self.assertEqual([i["format"] for i in res["items"]], ["conf", "vpn"])
        self.assertTrue(any("awg-quick up ./gKP-te-ne-awg20.conf" in n for n in res["notes"]))


class CoreDownloadTest(AwgCase):
    """Nothing on disk: install() downloads bin/ of the repository with the GitHub key (panel, bot, CLI, menu)."""

    def fake_github(self, answers):
        seen = []

        def urlopen(req, timeout=None):                     # ghkey.open_github(req, timeout)
            seen.append((req.full_url, req.get_header("Authorization")))
            ans = answers.get(req.full_url.rsplit("/", 3)[-3] + "/" + req.full_url.rsplit("/", 1)[-1])
            if isinstance(ans, int):
                raise urllib.error.HTTPError(req.full_url, ans, "error", {}, io.BytesIO(b""))
            return io.BytesIO(ans)
        return seen, mock.patch.object(awg_mod.ghkey, "open_github", urlopen)

    def test_downloads_from_test_when_main_has_none(self):
        self.github_key("ghp_SECRET1")
        good = {"amneziawg-go": b"\x7fELF dl disable_cookies", "awg": b"\x7fELF dl DisableCookies"}
        seen, patch = self.fake_github({"main/amneziawg-go-linux-amd64": 404,
                                        "test/amneziawg-go-linux-amd64": good["amneziawg-go"],
                                        "test/awg-linux-amd64": good["awg"]})
        with patch:
            self.adapter("awg2").install(self.runner)
        d = os.path.join(paths.egress_dir(), "awg")
        for name in ("amneziawg-go", "awg"):
            self.assertEqual(slurp(os.path.join(d, name), binary=True), good[name])
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, name)).st_mode), 0o755)
        self.assertEqual([f for f in os.listdir(d) if f.startswith(".")], [])
        self.assertTrue(all(auth == "token ghp_SECRET1" for _, auth in seen))
        self.assertEqual(seen[0][0], "https://raw.githubusercontent.com/anten-ka/kaskad-pro/main/bin/amneziawg-go-linux-amd64")
        unit = slurp(os.path.join(paths.systemd_dir(), "kaskad-gw-awg2.service"))
        self.assertIn("ExecStart=%s/amneziawg-go -f kgw-awg2" % d, unit)
        self.assertNotIn("ghp_SECRET1", slurp(paths.log()) if os.path.exists(paths.log()) else "")

    def test_refuses_what_is_not_a_31_core(self):
        self.github_key()
        old = {"main/amneziawg-go-linux-amd64": b"\x7fELF old core", "test/amneziawg-go-linux-amd64": 404}
        seen, patch = self.fake_github(old)
        with patch, self.assertRaises(self.gw.base.InboundError) as cm:
            self.adapter("awg2").install(self.runner)
        self.assertIn("ключ GitHub", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(paths.egress_dir(), "awg", "awg")))

    def test_no_key_says_how_to_get_one(self):
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.adapter("awg2").install(self.runner)
        self.assertIn("ключа GitHub", str(cm.exception))
        self.assertIn("bin/amneziawg-go-linux-amd64", str(cm.exception))
