"""PPP inbounds on one accel-ppp: device changes need no reload, settings reload, modules and ports restart,
sessions that lost their right are cut one by one, the IPsec-only guard follows the plain switch, UFW per
inbound, status from accel-cmd."""

import json
import os

from kaskad import paths

from tests.gw.ppp_case import PppCase, device, gw_state, session_row

UNIT = "kaskad-gw-accel.service"


def devices():
    return [device("d-win", "Ноутбук", "windows", ["pptp", "sstp"], login="noutbuk-1a2b", kind="laptop"),
            device("d-tp", "TP-Link", "tplink", ["pptp"], login="tplink-5e6f", kind="router")]


class ApplyCase(PppCase):
    def setUp(self):
        super().setUp()
        self.acc = self.gw.accel
        self.st = gw_state({"pptp": {"enabled": True}, "sstp": {"enabled": True, "port": 4433}}, devices())
        self.pptp, self.sstp = self.adapter("pptp"), self.adapter("sstp")

    def ready(self):
        self.installed("pptp", "sstp")
        self.pptp.apply(self.st, self.sec, self.runner)
        self.sstp.apply(self.st, self.sec, self.runner)
        self.runner.calls[:] = []
        self.node.sysd.calls[:] = []

    def verbs(self):
        return self.node.sysd.verbs(UNIT)

    def ip(self, dev_id):
        return self.dev(self.st, dev_id)["ips"]["ppp"]

    def accel_cmds(self):
        """Commands given to accel-cmd (after -H/-p and the -P password)."""
        out = []
        for a, _ in self.runner.calls:
            if os.path.basename(a[0]) == "accel-cmd":
                args = a[5:]
                out.append(args[2:] if args[:1] == ["-P"] else args)
        return out


class FirstApplyTest(ApplyCase):
    def test_first_apply_starts_once(self):
        self.node.ufw_active = True
        self.installed("pptp", "sstp")
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "started")
        self.assertIn("enable --no-reload --now", self.verbs())
        self.assertEqual(sorted(res["prepared"])[:2], ["адрес d-tp", "адрес d-win"])
        self.assertIn("сертификат TLS для 203.0.113.10", res["prepared"])
        res = self.sstp.apply(self.st, self.sec, self.runner)                           # the same engine
        self.assertEqual((res["action"], res["changed"], res["prepared"]), ("unchanged", [], []))
        self.assertEqual(self.node.ufw, ["allow 1723/tcp comment kaskad-gw pptp",
                                         "allow proto gre from any to any comment kaskad-gw pptp",
                                         "allow 4433/tcp comment kaskad-gw sstp"])
        self.assertEqual(self.node.guard_runs, [])                                      # the unit runs it at start


class DeviceChangesTest(ApplyCase):
    def test_new_device_needs_no_reload(self):
        self.ready()
        self.st["gateway"]["devices"].append(device("d-new", "Новый", "windows", ["sstp"], login="novyy-7a8b"))
        res = self.sstp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "unchanged")
        self.assertEqual(sorted(res["changed"]), ["allow/sstp", "chap-secrets", "users.json"])
        self.assertEqual(self.verbs(), ["is-active", "is-enabled"])                     # no reload, no restart
        self.assertEqual(self.accel_cmds(), [])                                         # nobody lost anything
        chap = self.read("/etc/kaskad/gw/accel/chap-secrets")
        self.assertIn("novyy-7a8b\t*\t\"%s\"\t10.77.5.12" % self.sec["dev.d-new.password"], chap)

    def test_lost_inbound_cuts_only_that_session(self):
        self.ready()
        self.assertEqual((self.ip("d-tp"), self.ip("d-win")), ("10.77.5.10", "10.77.5.11"))
        self.node.sessions = (session_row("kgw-pptp0", "noutbuk-1a2b-pptp", "10.77.5.11", "pptp") +
                              session_row("kgw-sstp0", "noutbuk-1a2b", "10.77.5.11", "sstp") +
                              session_row("kgw-pptp1", "tplink-5e6f-pptp", "10.77.5.10", "pptp"))
        self.dev(self.st, "d-win")["access"] = ["sstp"]
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "unchanged")
        self.assertEqual(res["cut"], ["pptp noutbuk-1a2b-pptp"])
        self.assertEqual(self.node.terminated, ["kgw-pptp0"])                            # its SSTP session stays
        self.assertEqual(self.accel_cmds()[-1], ["terminate", "if", "kgw-pptp0", "soft"])
        self.assertEqual(self.read("/etc/kaskad/gw/accel/allow/pptp"), "tplink-5e6f-pptp\n")

    def test_deleted_or_disabled_device_is_cut_everywhere(self):
        self.ready()
        self.node.sessions = (session_row("kgw-pptp0", "noutbuk-1a2b-pptp", "10.77.5.11", "pptp") +
                              session_row("kgw-sstp0", "noutbuk-1a2b", "10.77.5.11", "sstp") +
                              session_row("kgw-pptp1", "tplink-5e6f-pptp", "10.77.5.10", "pptp"))
        self.dev(self.st, "d-win")["enabled"] = False
        res = self.sstp.apply(self.st, self.sec, self.runner)
        self.assertEqual(sorted(self.node.terminated), ["kgw-pptp0", "kgw-sstp0"])
        self.assertEqual(sorted(res["cut"]), ["pptp noutbuk-1a2b-pptp", "sstp noutbuk-1a2b"])
        self.assertNotIn("noutbuk", self.read("/etc/kaskad/gw/accel/chap-secrets"))

    def test_changed_address_is_cut(self):
        self.ready()
        self.node.sessions = (session_row("kgw-pptp1", "tplink-5e6f-pptp", "10.77.5.10", "pptp") +
                              session_row("kgw-sstp0", "noutbuk-1a2b", "10.77.5.11", "sstp"))
        self.dev(self.st, "d-tp")["ips"]["ppp"] = "10.77.5.20"
        self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual(self.node.terminated, ["kgw-pptp1"])
        self.assertIn("tplink-5e6f-pptp\t*\t\"%s\"\t10.77.5.20" % self.sec["dev.d-tp.pptp.password"],
                      self.read("/etc/kaskad/gw/accel/chap-secrets"))


class OwnCredentialsTest(ApplyCase):
    """PPTP has a login and a password of its own (<login>-pptp): MS-CHAPv2 without TLS is breakable, the
    shared password of L2TP/SSTP/IKEv2/OpenConnect never goes through it. One address for both accounts."""

    def test_two_accounts_one_address(self):
        self.ready()
        chap = self.read("/etc/kaskad/gw/accel/chap-secrets")
        win_pw, win_pptp = self.sec["dev.d-win.password"], self.sec["dev.d-win.pptp.password"]
        self.assertNotEqual(win_pw, win_pptp)
        self.assertIn("noutbuk-1a2b\t*\t\"%s\"\t10.77.5.11" % win_pw, chap)
        self.assertIn("noutbuk-1a2b-pptp\t*\t\"%s\"\t10.77.5.11" % win_pptp, chap)
        self.assertNotIn("dev.d-tp.password", self.sec)                                  # PPTP only: no shared one
        self.assertEqual(self.read("/etc/kaskad/gw/accel/allow/pptp"), "noutbuk-1a2b-pptp\ntplink-5e6f-pptp\n")
        self.assertEqual(self.read("/etc/kaskad/gw/accel/allow/sstp"), "noutbuk-1a2b\n")
        owners = json.loads(self.read("/etc/kaskad/gw/accel/users.json"))
        self.assertEqual(owners, {"noutbuk-1a2b": "d-win", "noutbuk-1a2b-pptp": "d-win", "tplink-5e6f-pptp": "d-tp"})
        self.assertEqual(self.gw.accel.client(self.st, self.sec, self.dev(self.st, "d-win"), "pptp"),
                         ("noutbuk-1a2b-pptp", win_pptp))
        self.assertEqual(self.gw.accel.client(self.st, self.sec, self.dev(self.st, "d-win"), "sstp"),
                         ("noutbuk-1a2b", win_pw))

    def test_new_password_cuts_only_that_account(self):
        self.ready()
        self.node.sessions = (session_row("kgw-pptp0", "noutbuk-1a2b-pptp", "10.77.5.11", "pptp") +
                              session_row("kgw-pptp1", "tplink-5e6f-pptp", "10.77.5.10", "pptp"))
        del self.sec["dev.d-tp.pptp.password"]                                           # gw device reset
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "unchanged")                                     # chap-secrets: no reload
        self.assertEqual(res["prepared"], ["пароль PPTP d-tp"])
        self.assertEqual(res["cut"], ["pptp tplink-5e6f-pptp"])
        self.assertEqual(self.node.terminated, ["kgw-pptp1"])                            # d-win stays
        self.node.terminated[:] = []
        self.node.sessions = (session_row("kgw-pptp0", "noutbuk-1a2b-pptp", "10.77.5.11", "pptp") +
                              session_row("kgw-sstp0", "noutbuk-1a2b", "10.77.5.11", "sstp"))
        del self.sec["dev.d-win.password"]                                               # the shared one: SSTP
        res = self.sstp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["cut"], ["sstp noutbuk-1a2b"])
        self.assertEqual(self.node.terminated, ["kgw-sstp0"])                            # its PPTP account stays


class SettingsTest(ApplyCase):
    def test_settings_reload_modules_and_ports_restart(self):
        self.node.ufw_active = True
        self.ready()
        self.node.ufw[:] = []
        self.st["gateway"]["net"]["dns"] = "10.77.0.2"
        self.assertEqual(self.pptp.apply(self.st, self.sec, self.runner)["action"], "reloaded")
        self.assertIn("reload", self.verbs())
        self.st["gateway"]["inbounds"]["sstp"]["port"] = 8443
        self.assertEqual(self.sstp.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertEqual(self.node.ufw, ["delete allow 4433/tcp", "allow 8443/tcp comment kaskad-gw sstp"])
        self.st["gateway"]["inbounds"]["pptp"]["enabled"] = False
        self.assertEqual(self.pptp.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertNotIn("\npptp\n", self.read("/etc/kaskad/gw/accel/accel-ppp.conf"))
        self.assertEqual(self.node.ufw[-2:], ["delete allow 1723/tcp", "delete allow proto gre from any to any"])
        self.st["gateway"]["enabled"] = False
        self.assertEqual(self.sstp.apply(self.st, self.sec, self.runner)["action"], "stopped")
        self.assertEqual(self.node.ufw[-1], "delete allow 8443/tcp")
        self.assertIn("disable --now", self.verbs())

    def test_new_address_of_the_node_new_certificate_reload(self):
        self.ready()
        old = self.sec["tls.server.crt"]
        self.st["gateway"]["host"] = "vpn.example.org"
        res = self.sstp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "reloaded")                                     # SSTP re-reads it on reload
        self.assertEqual(res["prepared"], ["сертификат TLS для vpn.example.org"])
        self.assertEqual(self.st["gateway"]["inbounds"]["sstp"]["cert_host"], "vpn.example.org")
        self.assertIn("CN = vpn.example.org", self.node.ssl.configs[-1])
        self.assertIn("DNS.1 = vpn.example.org", self.node.ssl.configs[-1])
        self.assertIn("rsa_keygen_bits:2048", " ".join(c for c, _ in [(" ".join(a), i) for a, i in self.runner.calls]))
        self.assertEqual(old, self.sec["tls.server.crt"])                               # the fake hands out one cert

    def test_plain_switch_moves_the_guard_without_restart(self):
        self.st["gateway"]["inbounds"]["l2tp"] = {"enabled": True, "ipsec": True, "plain": False}
        self.dev(self.st, "d-tp")["access"].append("l2tp")
        self.installed("pptp", "sstp")
        self.acc.install(self.runner, "l2tp")
        self.acc.apply(self.st, self.sec, self.runner)
        self.assertIn("GUARD=1", self.read("/etc/kaskad/gw/accel/l2tp-guard.sh"))
        self.node.sysd.calls[:] = []
        self.st["gateway"]["inbounds"]["l2tp"]["plain"] = True
        res = self.acc.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "unchanged")
        self.assertIn("GUARD=0", self.read("/etc/kaskad/gw/accel/l2tp-guard.sh"))
        self.assertEqual(self.node.guard_runs, [[]])                                    # applied at once
        self.assertNotIn("restart", self.verbs())
        self.st["gateway"]["inbounds"]["l2tp"].update({"plain": False, "ipsec": False})  # nothing accepted
        res = self.acc.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "restarted")                                    # the l2tp module goes
        self.assertNotIn("\nl2tp\n", self.read("/etc/kaskad/gw/accel/accel-ppp.conf"))


class StatusTest(ApplyCase):
    def test_sessions_per_inbound(self):
        self.ready()
        self.node.sessions = (session_row("kgw-pptp1", "tplink-5e6f-pptp", "10.77.5.10", "pptp", up=60, rx=5, tx=7) +
                              session_row("kgw-sstp0", "noutbuk-1a2b", "10.77.5.11", "sstp", up=0))
        s = self.pptp.status(self.runner)
        self.assertEqual((s["installed"], s["running"], s["port"]), (True, True, 1723))
        self.assertEqual(len(s["sessions"]), 1)
        one = s["sessions"][0]
        self.assertEqual((one["device"], one["login"], one["ip"], one["rx"], one["tx"], one["ifname"]),
                         ("d-tp", "tplink-5e6f-pptp", "10.77.5.10", 5, 7, "kgw-pptp1"))
        self.assertIsInstance(one["since"], int)
        self.assertIn("слабое шифрование", s["warnings"][0])
        s = self.sstp.status(self.runner)
        self.assertEqual((s["port"], [x["device"] for x in s["sessions"]]), (4433, ["d-win"]))
        self.st["gateway"]["inbounds"]["sstp"]["enabled"] = False
        self.sstp.apply(self.st, self.sec, self.runner)
        self.assertFalse(self.sstp.status(self.runner)["running"])                      # pptp still runs the unit
        self.assertTrue(self.pptp.status(self.runner)["running"])

    def test_parse_sessions_table(self):
        text = ("  ifname  | username |     ip     | type | state  | uptime-raw | rx-bytes-raw | tx-bytes-raw\r\n"
                "----------+----------+------------+------+--------+------------+--------------+-------------\r\n")
        self.assertEqual(self.acc.parse_sessions(text), [])
        rows = self.acc.parse_sessions(text + session_row("kgw-l2tp3", "a-b", "10.77.5.13", "l2tp"))
        self.assertEqual(rows[0]["ifname"], "kgw-l2tp3")
        self.assertEqual(rows[0]["uptime-raw"], "120")

    def test_uninstalled_kind_is_not_running(self):
        self.ready()
        self.pptp.uninstall(self.runner)
        s = self.pptp.status(self.runner)
        self.assertEqual((s["installed"], s["running"]), (False, False))
        self.assertTrue(self.sstp.status(self.runner)["running"])
        self.assertEqual(json.loads(self.read("/etc/kaskad/gw/accel/kinds.json")), ["sstp"])


class UnitRefreshTest(ApplyCase):
    def test_an_older_unit_is_rewritten_without_cutting_sessions(self):
        self.ready()
        unit = paths.P("/etc/systemd/system/" + UNIT)
        fresh = self.read_abs(unit)
        with open(unit, "w", encoding="utf-8") as f:
            f.write(fresh.replace("Description=", "Description=old "))
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual(self.read_abs(unit), fresh)
        self.assertIn(["daemon-reload"], self.node.sysd.calls)
        self.assertNotIn("restart", self.verbs())
        self.assertEqual(res["action"], "unchanged")
        self.assertEqual(self.accel_cmds(), [])


class UpgradeTest(ApplyCase):
    """accel-ppp of an earlier build (PPP-1: before the patch of L2TP reconnects) is built again by apply — from the
    kept tarball with the build tools the install left, no apt, no download — and the unit restarted once. When it
    cannot be built the old build keeps working, the log and the result say why, and it is not tried again for a
    day."""

    def earlier_build(self):
        """The marker of the install and build.json of the stage as a build of 3.2.12 wrote them (no patches)."""
        for p in (os.path.join(self.acc.install_dir(), self.acc.MARKER),
                  os.path.join(self.acc.build_dir(), "build.json")):
            doc = json.loads(self.read_abs(p))
            del doc["patches"]
            with open(p, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        self.assertFalse(self.acc.install_ok() or self.acc.stage_ok())

    def test_earlier_build_is_built_again_and_restarted_once(self):
        self.ready()
        self.earlier_build()
        self.node.apt[:] = []
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "restarted")
        self.assertEqual(self.node.builds, ["tar", "patch", "cmake", "make", "make install"])
        self.assertEqual((self.node.apt, res["warnings"]), ([], []))
        self.assertEqual(self.verbs().count("restart"), 1)
        self.assertTrue(self.acc.install_ok())
        self.assertIn("accel-ppp 1.14.0 пересобран (патчи: accel-ppp-l2tp-peer-restart.patch)", self.log_text())
        self.node.builds[:] = []
        self.node.sysd.calls[:] = []
        res = self.sstp.apply(self.st, self.sec, self.runner)                       # the same engine: done already
        self.assertEqual((res["action"], self.node.builds), ("unchanged", []))
        self.assertNotIn("restart", self.verbs())

    def test_without_build_tools_the_old_build_stays_and_waits_a_day(self):
        self.ready()
        self.earlier_build()
        self.node.apt[:] = []
        self.node.pkgs.discard("cmake")                                             # removed by the owner
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual((res["action"], self.node.builds, self.node.apt), ("unchanged", [], []))
        self.assertNotIn("restart", self.verbs())
        self.assertIn("нет средств сборки (cmake); собрать сейчас (с пакетами и загрузкой): kaskadctl gw inbound "
                      "install pptp", res["warnings"][0])                          # an inbound this node has
        self.assertIn("accel-ppp 1.14.0 не пересобран, работает прежняя сборка", self.log_text())
        mark = os.path.join(self.acc.pkg_dir(), self.acc.REBUILD_FAILED)
        failed = json.loads(self.read_abs(mark))
        self.assertEqual(failed["build"], self.acc.build_info())
        self.node.pkgs.add("cmake")
        res = self.pptp.apply(self.st, self.sec, self.runner)                       # not again within a day
        self.assertEqual((res["action"], res["warnings"], self.node.builds), ("unchanged", [], []))
        failed["at"] -= self.acc.REBUILD_RETRY
        with open(mark, "w", encoding="utf-8") as f:
            json.dump(failed, f)
        res = self.pptp.apply(self.st, self.sec, self.runner)                       # a day later
        self.assertEqual(res["action"], "restarted")
        self.assertTrue(self.acc.install_ok())
        self.assertFalse(os.path.exists(mark))

    def test_a_build_that_fails_keeps_the_old_one(self):
        self.ready()
        self.earlier_build()
        self.node.patch_rc = 1
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "unchanged")
        self.assertIn("сборка не прошла (патч accel-ppp-l2tp-peer-restart.patch)", res["warnings"][0])
        self.assertNotIn("restart", self.verbs())
        self.assertTrue(os.path.isfile(self.acc.accel_pppd()))                      # the install is untouched
        self.assertFalse(self.acc.install_ok())

    def test_no_rebuild_while_no_ppp_inbound_runs(self):
        self.ready()
        self.earlier_build()
        for k in ("pptp", "sstp"):
            self.st["gateway"]["inbounds"][k]["enabled"] = False
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual((res["action"], self.node.builds), ("stopped", []))


class CliPasswordTest(ApplyCase):
    """accel-ppp's CLI on 127.0.0.1:2001 has a password (secret accel.cli.password, made once): every
    accel-cmd call gives it, a new one is a reload (sessions stay), it reaches no log."""

    def test_every_call_gives_the_password(self):
        self.ready()
        pw = self.sec["accel.cli.password"]
        self.assertEqual(len(pw), 24)
        conf = self.read("/etc/kaskad/gw/accel/accel-ppp.conf")
        self.assertIn("[cli]\nverbose=0\ntcp=127.0.0.1:2001\npassword=%s\n" % pw, conf)
        self.node.cli_password = pw                                 # the daemon read it
        self.node.sessions = (session_row("kgw-pptp0", "noutbuk-1a2b-pptp", "10.77.5.11", "pptp") +
                              session_row("kgw-pptp1", "tplink-5e6f-pptp", "10.77.5.10", "pptp"))
        self.dev(self.st, "d-tp")["enabled"] = False
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["cut"], ["pptp tplink-5e6f-pptp"])
        self.assertEqual(self.node.terminated, ["kgw-pptp1"])
        self.assertTrue(self.node.cli_seen and set(self.node.cli_seen) == {pw})
        self.assertEqual([x["login"] for x in self.acc.sessions(self.runner)],
                         ["noutbuk-1a2b-pptp", "tplink-5e6f-pptp"])
        self.node.cli_password = "something-else"                  # a wrong password: the daemon hangs up
        self.assertEqual(self.acc.sessions(self.runner), [])
        log = self.read("/var/log/kaskad.log") if os.path.exists(self.path("/var/log/kaskad.log")) else ""
        self.assertNotIn(pw, log)
        self.assertEqual(self.sec["accel.cli.password"], pw)       # made once, kept

    def test_a_new_password_is_a_reload_not_a_restart(self):
        self.ready()
        self.sec["accel.cli.password"] = "AnotherPassword2345678xy"
        res = self.pptp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "reloaded")
        self.assertNotIn("restart", self.verbs())
        self.assertIn("password=AnotherPassword2345678xy", self.read("/etc/kaskad/gw/accel/accel-ppp.conf"))

    def test_a_daemon_without_the_password_yet(self):
        # right after the update: the conf has the password, the daemon has not re-read it — it takes the
        # password line for a command; the table that follows is still read
        text = ("invalid command\r\n"
                "  ifname  | username |     ip     | type | state  | uptime-raw | rx-bytes-raw | tx-bytes-raw\r\n"
                "----------+----------+------------+------+--------+------------+--------------+-------------\r\n"
                + session_row("kgw-l2tp3", "a-b", "10.77.5.13", "l2tp"))
        self.assertEqual([r["ifname"] for r in self.acc.parse_sessions(text)], ["kgw-l2tp3"])
        from kaskad import util
        self.assertEqual(util.redact("/opt/kaskad/accel-ppp/bin/accel-cmd -H 127.0.0.1 -p 2001 -P Secret123 show"),
                         "/opt/kaskad/accel-ppp/bin/accel-cmd -H 127.0.0.1 -p 2001 -P <скрыт> show")
