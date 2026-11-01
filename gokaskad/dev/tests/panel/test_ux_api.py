"""3.3.3 UX routes of the panel (kaskad.panel.ux): the bot, the keys of the AI agent, a protocol's journal and restart."""

import calendar
import json
import time
from unittest import mock

from kaskad import paths
from kaskad.bot import onboard, telegram
from tests.panel.panelcase import PanelCase
from tests.test_333d_backend import TOKEN, FakeTg


class UxCase(PanelCase):
    def setUp(self):
        super().setUp()
        FakeTg.me, FakeTg.send, FakeTg.updates, FakeTg.made, FakeTg.sent = \
            {"username": "kaskad_test_bot"}, None, [], [], []
        self._ux = [mock.patch.object(telegram, "Api", FakeTg),
                    mock.patch("kaskad.gw.exits._script", lambda r: "/x/gokaskad"),
                    mock.patch.object(onboard, "_probe", self.probe)]
        for p in self._ux:
            p.start()
        self.probes = []
        onboard._reach.update(at=0.0, value=None, busy=False)
        self.w.runner.on(["/x/gokaskad", "--bot-start"], (0, "@K3:bot_started", ""))

    def probe(self, host, port, timeout):
        self.probes.append(host)
        raise OSError("blocked")

    def tearDown(self):
        for p in reversed(self._ux):
            p.stop()
        onboard._reach.update(at=0.0, value=None, busy=False)
        super().tearDown()

    def nothing_secret(self, *texts):
        for t in texts:
            self.assertNotIn(TOKEN, t if isinstance(t, str) else json.dumps(t, ensure_ascii=False))


class BotRoutesTest(UxCase):
    def test_status(self):
        doc = self.ok("GET", "/api/bot")
        for k in ("token", "chat", "chat_id", "username", "running", "last_error", "proxy", "reach", "need_exit", "ru"):
            self.assertIn(k, doc)
        self.assertEqual((doc["token"], doc["reach"], doc["need_exit"]), (False, False, True))
        self.err(401, "GET", "/api/bot", tunnel=False)

    def test_check(self):
        doc = self.err(400, "POST", "/api/bot/check", {"token": "nope"})
        self.assertEqual(doc["data"], {"reason": "bad_format"})
        self.assertEqual(self.ok("POST", "/api/bot/check", {"token": TOKEN})["username"], "kaskad_test_bot")
        FakeTg.me = telegram.ApiError("getMe", 401, "Unauthorized")
        self.assertEqual(self.err(400, "POST", "/api/bot/check", {"token": TOKEN})["data"]["reason"], "bad_token")
        FakeTg.me = telegram.NetworkError("getMe", "timed out")
        doc = self.err(502, "POST", "/api/bot/check", {"token": TOKEN})
        self.assertEqual(doc["data"], {"reason": "net"})
        FakeTg.me = telegram.ApiError("getMe", 500, "oops %s" % TOKEN)
        doc = self.err(502, "POST", "/api/bot/check", {"token": TOKEN})
        self.assertEqual(doc["data"], {"reason": "telegram"})
        self.nothing_secret(doc)
        self.err(403, "POST", "/api/bot/check", {"token": TOKEN}, csrf=False)
        self.assertFalse(self.w.runner.commands(["/x/gokaskad"]))          # check saves and starts nothing

    def test_test(self):
        self.assertEqual(self.err(400, "POST", "/api/bot/test", {"token": TOKEN, "chat_id": "x"})["data"],
                         {"reason": "bad_chat"})
        doc = self.ok("POST", "/api/bot/test", {"token": TOKEN, "chat_id": 555})
        self.assertEqual({k: doc[k] for k in ("username", "chat_id", "started", "sent", "sent_why")},
                         {"username": "kaskad_test_bot", "chat_id": "555", "started": True, "sent": True,
                          "sent_why": None})
        FakeTg.send = telegram.ApiError("sendMessage", 403, "Forbidden: bot can't initiate conversation with a user")
        doc = self.ok("POST", "/api/bot/test", {"chat_id": "555"})           # the saved token
        self.assertEqual((doc["sent"], doc["sent_why"]), (False, "nostart"))
        self.nothing_secret(doc, self.read("/var/log/kaskad.log"))
        self.assertIn("kaskad_test_bot", self.read("/var/log/kaskad.log"))
        st = self.ok("GET", "/api/bot")
        self.assertEqual((st["token"], st["chat"], st["chat_id"]), (True, True, "555"))
        self.nothing_secret(st)


class McpRoutesTest(UxCase):
    def setUp(self):
        super().setUp()
        from kaskad.mcp import seen
        from tests.test_333d_backend import FakeSshd
        seen.forget()
        self.addCleanup(seen.forget)
        self.w.runner.binaries.add("journalctl")
        self.sshd = FakeSshd(self.w.runner)

    def test_add_list_seen_delete(self):
        from tests.test_333d_backend import audit_line, ed25519
        k1, fp1 = ed25519(1)
        k2, fp2 = ed25519(2)
        doc = self.ok("GET", "/api/mcp")
        self.assertEqual(doc["keys"], [])
        self.assertIn("claude_code", doc["connect"])
        self.assertEqual(doc["host"], doc["connect"]["host"])
        doc = self.ok("POST", "/api/mcp/keys", {"pubkey": k1, "label": "Ноутбук Вити"})
        self.assertEqual((doc["fingerprint"], doc["type"], doc["label"]), (fp1, "ssh-ed25519", "Noutbuk-Viti"))
        self.assertIn("claude_code", doc["connect"])
        self.assertEqual(self.err(409, "POST", "/api/mcp/keys", {"pubkey": k1})["data"]["reason"], "exists")
        self.assertEqual(self.err(400, "POST", "/api/mcp/keys",
                                  {"pubkey": "-----BEGIN OPENSSH PRIVATE KEY-----\nb3Bl\n"})["data"]["reason"],
                         "private_key")
        self.assertEqual(self.err(400, "POST", "/api/mcp/keys", {"pubkey": "ssh-ed25519 !!!"})["data"]["reason"],
                         "bad_key")
        self.assertEqual(self.err(400, "POST", "/api/mcp/keys", {"pubkey": " "})["data"]["reason"], "empty")
        # a root key of the owner (not ours) — refused as root_key
        with open(paths.authorized_keys(), "a", encoding="utf-8") as f:
            f.write(k2 + "\n")
        self.assertEqual(self.err(409, "POST", "/api/mcp/keys", {"pubkey": k2})["data"]["reason"], "root_key")
        # the agent connected: the audit line + sshd's Accepted
        p = paths.P("/var/log/kaskad-mcp.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(audit_line("203.0.113.5", "51000", "2033-05-18T03:00:00Z"))
        self.sshd.accept("203.0.113.5", "51000", fp1)
        from kaskad.mcp import seen
        seen.forget()                                               # the answer of the first GET: 5 s
        doc = self.ok("GET", "/api/mcp")
        self.assertEqual(doc["keys"], [{"fingerprint": fp1, "type": "ssh-ed25519", "label": "Noutbuk-Viti",
                                        "seen": {"at": calendar.timegm((2033, 5, 18, 3, 0, 0, 0, 0, 0)),
                                                 "client": "claude-code/1.2"}}])
        self.assertNotIn(k1.split()[1], json.dumps(doc))
        self.assertEqual(self.err(400, "DELETE", "/api/mcp/keys/SHA256%3Anope")["data"]["reason"], "bad_fp")
        enc = fp1.replace(":", "%3A").replace("/", "%2F").replace("+", "%2B")
        gone = self.ok("DELETE", "/api/mcp/keys/" + enc)
        self.assertEqual((gone["removed"], gone["ended"], gone["unsure"]), (1, 0, False))   # no open session
        self.assertEqual(self.err(404, "DELETE", "/api/mcp/keys/" + enc)["data"]["reason"], "gone")
        self.assertEqual(self.ok("GET", "/api/mcp")["keys"], [])
        self.err(403, "DELETE", "/api/mcp/keys/" + enc, csrf=False)


class ExitPreviewModuleBuildTest(UxCase):
    def test_preview_says_the_module_will_be_built(self):
        from kaskad.gw import ops
        from tests.fakes_gw import VLESS_A
        self.gateway()
        with mock.patch.object(ops, "module_build", lambda items: True):
            self.assertIs(self.ok("POST", "/api/gw/exits/preview", {"key": VLESS_A})["module_build"], True)
        with mock.patch.object(ops, "module_build", lambda items: False):
            self.assertIs(self.ok("POST", "/api/gw/exits/preview", {"key": VLESS_A})["module_build"], False)
        self.assertIs(self.ok("POST", "/api/gw/exits/preview", {"key": "https://example.org/sub/x\n"})["module_build"],
                      False)                                         # a subscription: nothing counted


class InboundLogRestartTest(UxCase):
    UNIT = "kaskad-gw-wg.service"

    def setUp(self):
        super().setUp()
        self.gateway(inbounds=("wg",))
        self.w.runner.binaries.add("journalctl")
        self.w.journal[self.UNIT] = ("2026-10-12T14:00:00+0300 node wireguard[7]: Starting\n"
                                     "2026-10-12T14:00:01+0300 node wireguard[7]: bind: Address already in use\n")

    def unit_file(self):
        p = paths.P("/etc/systemd/system/" + self.UNIT)
        with open(p, "w", encoding="utf-8") as f:
            f.write("[Service]\nExecStart=/bin/true\n")

    def test_log_and_why(self):
        doc = self.ok("GET", "/api/gw/inbounds/wg/log")
        self.assertEqual((doc["kind"], doc["units"], doc["cmd"]), ("wg", [self.UNIT], "journalctl -u %s -n 30" % self.UNIT))
        self.assertEqual([l["err"] for l in doc["lines"]], [False, True])
        f = doc["fail"]
        self.assertEqual((f["why"], f["proto"], f["line"]), ("busy", "udp", "bind: Address already in use"))
        self.assertIsInstance(f["port"], int)
        self.assertEqual(self.err(404, "GET", "/api/gw/inbounds/zz/log")["data"], {"reason": "no_kind"})
        self.err(401, "GET", "/api/gw/inbounds/wg/log", tunnel=False)

    def test_restart(self):
        self.assertEqual(self.err(409, "POST", "/api/gw/inbounds/ocserv/restart", {})["data"], {"reason": "not_on"})
        self.err(403, "POST", "/api/gw/inbounds/wg/restart", {}, csrf=False)
        with mock.patch("kaskad.gw.inlog._sleep", lambda s: None):
            doc = self.ok("POST", "/api/gw/inbounds/wg/restart", {})        # no unit file: it does not start
            self.assertEqual((doc["kind"], doc["running"], doc["fail"]["why"]), ("wg", False, "busy"))
            self.assertIsInstance(doc["at"], int)
            self.unit_file()
            doc = self.ok("POST", "/api/gw/inbounds/wg/restart", {})
        self.assertEqual((doc["running"], doc["fail"]), (True, None))
        self.assertEqual(self.w.sysd.restarts.get(self.UNIT), 1)
        self.assertIn("wg", self.read("/var/log/kaskad.log").lower())

    def test_inbounds_rows_carry_fail(self):
        from kaskad.gw import inlog
        rows = dict((r["kind"], r) for r in self.ok("GET", "/api/gw/inbounds")["inbounds"])
        self.assertIn("fail", rows["wg"])
        self.assertIsNone(rows["wg"]["fail"])
        self.assertIsNone(rows["ocserv"]["fail"])                            # not installed
        self.assertEqual(rows["wg"]["shared"], {"gateway": False, "with": []})   # its own unit: no question
        self.assertIsNone(rows["ocserv"]["shared"])
        self.ads["wg"].running = False
        self.collect()
        rows = dict((r["kind"], r) for r in self.ok("GET", "/api/gw/inbounds")["inbounds"])
        self.assertEqual((rows["wg"]["running"], rows["wg"]["fail"]["why"]), (False, "busy"))
        # working, the settings given out 11 minutes ago, nobody came: «closed» (the cache sees the file)
        self.ads["wg"].running = True
        self.collect()
        inlog.mark_issued("wg", now=time.time() - 660)
        rows = dict((r["kind"], r) for r in self.ok("GET", "/api/gw/inbounds")["inbounds"])
        self.assertEqual((rows["wg"]["running"], rows["wg"]["fail"]["why"]), (True, "closed"))
        self.assertEqual(self.ok("GET", "/api/gw")["inbounds"][0]["fail"]["why"], "closed")
        # the collector saw a session of it: a handshake from before the settings were given out proves nothing
        # (REV-3315-a1 З-1, reissue.fresh), one after them — the port is open
        did = self.ok("POST", "/api/gw/devices", {"user": self.uid, "name": "Телефон", "kind": "phone",
                                                  "platform": "android", "access": ["wg"]})["device"]["id"]
        self.ads["wg"].sessions = [{"device": did, "ip": "10.77.1.10", "since": 1790000000, "rx": 10, "tx": 20}]
        self.collect()
        rows = dict((r["kind"], r) for r in self.ok("GET", "/api/gw/inbounds")["inbounds"])
        self.assertEqual(rows["wg"]["fail"]["why"], "closed")
        self.ads["wg"].sessions = [{"device": did, "ip": "10.77.1.10", "since": int(time.time()), "rx": 10, "tx": 20}]
        self.collect()
        rows = dict((r["kind"], r) for r in self.ok("GET", "/api/gw/inbounds")["inbounds"])
        self.assertIsNone(rows["wg"]["fail"])
