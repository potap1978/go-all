"""3.3.3 UX backend: kaskad.bot.onboard (one logic for the wizard and the panel), kaskad.mcp.seen (the agent's key
is used), kaskad.gw.inlog (a protocol's journal, why it does not work, restart)."""

import json
import os
import time
from unittest import mock

from kaskad import i18n, paths
from kaskad.bot import onboard, telegram
from kaskad.errors import KaskadError
from kaskad.sysrun import FakeRunner
from tests.helpers import RootCase

TOKEN = "123456789:" + "A" * 35


class FakeTg(object):
    """telegram.Api of the tests: ``me`` — getMe's answer or an exception; ``send`` — None or an exception."""
    me = {"username": "kaskad_test_bot"}
    send = None
    updates = []
    made = []
    sent = []

    def __init__(self, token, proxy=None, base=None, **kw):
        self.token, self.proxy = token, proxy
        FakeTg.made.append(self)

    def get_me(self):
        if isinstance(FakeTg.me, Exception):
            raise FakeTg.me
        return FakeTg.me

    def get_updates(self, offset, timeout=30, limit=100):
        return FakeTg.updates

    def send_message(self, chat, text):
        if FakeTg.send is not None:
            raise FakeTg.send
        FakeTg.sent.append((chat, text))
        return True

    def clean(self, e):
        return str(e).replace(self.token, "<token>")


class BotCase(RootCase):
    def setUp(self):
        super().setUp()
        FakeTg.me, FakeTg.send, FakeTg.updates, FakeTg.made, FakeTg.sent = \
            {"username": "kaskad_test_bot"}, None, [], [], []
        self._p = [mock.patch.object(telegram, "Api", FakeTg),
                   mock.patch("kaskad.gw.exits._script", lambda r: "/x/gokaskad")]
        for p in self._p:
            p.start()
        onboard._reach.update(at=0.0, value=None, busy=False)
        self.runner = FakeRunner(binaries=["systemctl"])
        self.out = ["@K3:bot_started"]
        self.runner.on(["/x/gokaskad", "--bot-start"], lambda argv, inp: (0, self.out[0], ""))
        self.active = ["inactive"]
        self.runner.on(["systemctl", "is-active"], lambda argv, inp: (0, self.active[0] + "\n", ""))

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()
        onboard._reach.update(at=0.0, value=None, busy=False)
        super().tearDown()

    def config(self, text):
        self.write("/etc/kaskad/config", text)

    def reason(self, fn, *a, **kw):
        with self.assertRaises(KaskadError) as cm:
            fn(*a, **kw)
        return cm.exception.data["reason"], str(cm.exception)


class OnboardTest(BotCase):
    def test_check_token_formats(self):
        for bad in ("", "nope", "123:abc", "12345:" + "A" * 35, "123456789:" + "A" * 10, TOKEN + " x", TOKEN + "$"):
            self.assertEqual(self.reason(onboard.check_token, bad)[0], "bad_format", bad)
        self.assertEqual(FakeTg.made, [])                     # nothing asked of Telegram
        self.assertEqual(onboard.check_token(" %s " % TOKEN), {"ok": True, "username": "kaskad_test_bot"})
        self.assertFalse(os.path.exists(paths.config()))      # nothing saved

    def test_check_token_refused_and_other(self):
        for code in (401, 404):
            FakeTg.me = telegram.ApiError("getMe", code, "Unauthorized")
            self.assertEqual(self.reason(onboard.check_token, TOKEN)[0], "bad_token")
        FakeTg.me = telegram.ApiError("getMe", 500, "Internal %s" % TOKEN)
        reason, text = self.reason(onboard.check_token, TOKEN)
        self.assertEqual(reason, "telegram")
        self.assertNotIn(TOKEN, text)

    def test_net_without_and_with_proxy(self):
        FakeTg.me = telegram.NetworkError("getMe", "timed out")
        with i18n.use_lang("ru"):
            reason, text = self.reason(onboard.check_token, TOKEN)
        self.assertEqual((reason, text), ("net", "Не достучались до Telegram — сначала добавьте выход."))
        self.assertIsNone(FakeTg.made[-1].proxy)
        self.config('BOT_PROXY="socks5://127.0.0.1:10808"\n')
        with i18n.use_lang("en"):
            reason, text = self.reason(onboard.check_token, TOKEN)
        self.assertEqual(reason, "net_proxy")                # an exit is set but does not carry — not «add an exit»
        self.assertIn("timed out", text)
        self.assertIsNotNone(FakeTg.made[-1].proxy)          # getMe went through BOT_PROXY

    def test_apply_saves_starts_and_sends(self):
        res = onboard.apply(self.runner, TOKEN, "555")
        self.assertEqual(res, {"ok": True, "username": "kaskad_test_bot", "chat_id": "555", "started": True,
                               "sent": True, "sent_why": None})
        conf = self.read("/etc/kaskad/config")
        self.assertIn('BOT_TOKEN="%s"' % TOKEN, conf)
        self.assertIn('BOT_CHAT_ID="555"', conf)
        self.assertEqual(FakeTg.sent[0][0], "555")
        self.assertIn((["/x/gokaskad", "--bot-start"], {"K3_MARKS": "1"}), self.runner.envs)
        # the saved token when none is given; a bot that did not start
        self.out[0] = "@K3:bot_failed:not_active"
        res = onboard.apply(self.runner, "", "-100123")
        self.assertEqual((res["started"], res["chat_id"]), (False, "-100123"))
        self.assertEqual(FakeTg.made[-1].token, TOKEN)

    def test_apply_nostart_and_other_send_errors(self):
        for err, why in ((telegram.ApiError("sendMessage", 403, "Forbidden: bot can't initiate conversation"),
                          "nostart"),
                         (telegram.ApiError("sendMessage", 400, "Bad Request: chat not found"), "nostart"),
                         (telegram.ApiError("sendMessage", 400, "Bad Request: message is too long"), "telegram"),
                         (telegram.ApiError("sendMessage", 429, "Too Many Requests: retry after 5"), "busy"),
                         (telegram.ApiError("sendMessage", 502, "Bad Gateway"), "busy"),
                         (telegram.NetworkError("sendMessage", "reset"), "net")):
            FakeTg.send = err
            res = onboard.apply(self.runner, TOKEN, "555")
            self.assertEqual((res["sent"], res["sent_why"]), (False, why), err)
        res = onboard.apply(self.runner, TOKEN, "555", test=False)
        self.assertEqual((res["sent"], res["sent_why"]), (False, None))

    def test_apply_refuses_bad_chat_and_no_token(self):
        for chat in ("", "abc", "12 3", "1" * 21):
            self.assertEqual(self.reason(onboard.apply, self.runner, TOKEN, chat)[0], "bad_chat")
        self.assertEqual(self.reason(onboard.apply, self.runner, "", "555")[0], "no_token")
        FakeTg.me = telegram.ApiError("getMe", 401, "Unauthorized")
        self.assertEqual(self.reason(onboard.apply, self.runner, TOKEN, "555")[0], "bad_token")
        self.assertFalse(os.path.exists(paths.config()))      # a refused token is not saved

    def test_status_reach_cache_and_need_exit(self):
        probes = []

        def probe(host, port, timeout):
            probes.append((host, port, timeout))
            raise OSError("timed out")
        now = [1000.0]
        spawned = []
        with mock.patch.object(onboard, "_probe", probe), mock.patch.object(onboard, "_now", lambda: now[0]), \
                mock.patch.object(onboard, "_spawn", spawned.append):
            st = onboard.status(self.runner)
            self.assertEqual((st["token"], st["chat"], st["proxy"], st["reach"], st["need_exit"], st["running"]),
                             (False, False, False, False, True, False))
            self.assertEqual(probes, [("api.telegram.org", 443, 4.0)])
            now[0] += 599
            onboard.status(self.runner)
            self.assertEqual(len(probes), 1)                  # remembered 10 minutes
            now[0] += 2
            self.assertIs(onboard.status(self.runner)["need_exit"], True)   # the old answer at once (R-333 П3)
            self.assertEqual((len(probes), len(spawned)), (1, 1))            # the probe — in the background
            onboard.status(self.runner)
            self.assertEqual(len(spawned), 1)                                 # one probe at a time
            spawned[0]()
            self.assertEqual(len(probes), 2)
            # BOT_PROXY: the direct reach is not needed
            self.config('BOT_TOKEN="%s"\nBOT_CHAT_ID="555"\nBOT_PROXY="socks5://127.0.0.1:10808"\n' % TOKEN)
            self.active[0] = "active"
            self.write("/run/kaskad/bot.json", json.dumps({"username": "kaskad_test_bot",
                                                           "last_error": "getUpdates: bot%s/x 502" % TOKEN}))
            st = onboard.status(self.runner)
            self.assertEqual(len(probes), 2)
        self.assertEqual((st["token"], st["chat"], st["chat_id"], st["proxy"], st["reach"], st["need_exit"],
                          st["running"], st["username"]),
                         (True, True, "555", True, None, False, True, "kaskad_test_bot"))
        self.assertNotIn(TOKEN, json.dumps(st))

    def test_in_russia_only_when_known(self):
        from kaskad import state as kstate
        from kaskad.gw.inbounds import vless_lists
        self.assertIsNone(onboard.in_ru())                                       # nothing known: neutral words
        for code, want in (("RU", True), ("DE", False)):
            st = kstate.empty()
            st["gateway"] = {"inbounds": {"vless": {"country": {"code": code, "source": "ipinfo"}}}}
            kstate.save(st)
            self.assertIs(onboard.in_ru(), want, code)
        st = kstate.empty()
        st["gateway"] = {"host": "95.163.0.1"}
        kstate.save(st)
        with mock.patch.object(vless_lists, "_local", lambda ip: "RU" if ip == "95.163.0.1" else ""):
            self.assertIs(onboard.in_ru(), True)                                 # the Russian ranges of the rules
        with mock.patch.object(onboard, "_probe", lambda h, p, t: (_ for _ in ()).throw(OSError("x"))):
            self.assertIsNone(onboard.status(self.runner)["ru"])                 # no rules here: not known
        with mock.patch.object(onboard, "_probe", lambda h, p, t: None):
            onboard._reach.update(at=0.0, value=None, busy=False)
            self.assertIsNone(onboard.status(self.runner)["ru"])                 # Telegram reachable: not asked

    def test_status_reach_ok(self):
        with mock.patch.object(onboard, "_probe", lambda h, p, t: None):
            st = onboard.status(self.runner)
        self.assertEqual((st["reach"], st["need_exit"]), (True, False))

    def test_i18n_ru_en(self):
        for key in ("bad_format", "bad_token", "net_no_exit", "bad_chat", "test_message"):
            ru, en = i18n.tr("bot_onboard." + key, "ru"), i18n.tr("bot_onboard." + key, "en")
            self.assertNotEqual(ru, "bot_onboard." + key)
            self.assertNotEqual(ru, en)
            self.assertFalse(any("а" <= ch.lower() <= "я" for ch in en), en)


# ── the keys of the AI agent: «подключено» ─────────────────────────────────

def ed25519(n):
    """A public key line of the agent (any 32 bytes make a key keys.py accepts) and its fingerprint."""
    import base64
    import hashlib
    import struct
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([n]) * 32
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return "ssh-ed25519 %s agent%d" % (base64.b64encode(blob).decode(), n), fp


def audit_line(ip, port, ts, kind="initialize", client="claude-code/1.2", iface="mcp"):
    return json.dumps({"ts": ts, "iface": iface, "kind": kind, "tool": "", "from": ip, "port": port,
                       "client": client, "status": "ok"}) + "\n"


class FakeSshd(object):
    """journalctl -t sshd -g "Accepted publickey" --since @T: the Accepted lines of ``self.lines``; ``rc`` — its exit
    code (124: a timeout); ``since`` — the --since of each read."""

    def __init__(self, runner):
        self.lines, self.calls, self.since, self.rc = [], 0, [], 0
        runner._handlers.insert(0, (["journalctl", "-q", "--no-pager", "-o", "cat", "-t", "sshd"], self))

    def accept(self, ip, port, fp):
        self.lines.append("Accepted publickey for root from %s port %s ssh2: ED25519 %s" % (ip, port, fp))

    def __call__(self, argv, inp):
        self.calls += 1
        self.since.append(argv[argv.index("--since") + 1])
        if self.rc not in (0, 1):
            return self.rc, "", "timeout"
        pat = argv[argv.index("-g") + 1]
        return self.rc, "".join(l + "\n" for l in self.lines if pat in l), ""


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class SeenTest(RootCase):
    NOW = 2000000000.0                     # 2033-05-18T03:33:20Z

    def setUp(self):
        super().setUp()
        from kaskad.mcp import seen
        self.seen = seen
        seen.forget()
        self.now = [self.NOW]
        self._p = mock.patch.object(seen, "_now", lambda: self.now[0])
        self._p.start()
        self.runner = FakeRunner(binaries=["journalctl"])
        self.sshd = FakeSshd(self.runner)
        self.k1, self.fp1 = ed25519(1)
        self.k2, self.fp2 = ed25519(2)

    def tearDown(self):
        self._p.stop()
        self.seen.forget()
        super().tearDown()

    def audit(self, *lines):
        p = self.path("/var/log/kaskad-mcp.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("".join(lines))

    def tick(self, s):
        self.now[0] += s
        self.seen.forget()                                  # past the 5 s answer

    def keys(self, *fps):
        return self.seen.for_keys(list(fps), self.runner)

    def state(self):
        with open(self.seen.state_path(), encoding="utf-8") as f:
            return json.load(f)

    def test_pairs_to_fingerprints_one_read_and_unknown(self):
        self.audit(audit_line("203.0.113.5", "51000", "2033-05-18T03:00:00Z"),
                   audit_line("203.0.113.5", "51000", "2033-05-18T03:00:05Z", kind="call", client="-"),
                   audit_line("198.51.100.7", "40000", "2033-05-18T02:00:00Z", client="cursor/0.1"),
                   audit_line("local", "", "2033-05-18T04:00:00Z"),
                   audit_line("192.0.2.1", "1", "2033-05-18T04:00:00Z", iface="cli"),
                   audit_line("192.0.2.9", "2222", "2033-05-18T01:00:00Z"),
                   "not json\n")
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        self.sshd.accept("198.51.100.7", "40000", self.fp2)
        got = self.seen.seen(self.runner)
        import calendar
        at = calendar.timegm((2033, 5, 18, 3, 0, 5, 0, 0, 0))
        self.assertEqual(got[self.fp1], {"at": at, "client": "claude-code/1.2"})        # the newest call, its client
        self.assertEqual(got[self.fp2]["client"], "cursor/0.1")
        self.assertEqual(set(got), {self.fp1, self.fp2, "?"})                            # 192.0.2.9: no Accepted
        self.assertEqual(self.sshd.calls, 1)                                              # one read for all pairs
        # since the oldest initialize, a little before it
        self.assertEqual(self.sshd.since, ["@%d" % (calendar.timegm((2033, 5, 18, 1, 0, 0, 0, 0, 0)) - 600)])
        for _ in range(20):                                                               # 10 min: no read again
            self.tick(30)
            self.seen.seen(self.runner)
        self.assertEqual(self.sshd.calls, 1)
        self.audit(audit_line("203.0.113.5", "51001", iso(self.now[0])))                 # a new connection: one read
        self.sshd.accept("203.0.113.5", "51001", self.fp2)
        self.tick(30)
        self.assertEqual(self.seen.seen(self.runner)[self.fp2]["at"], int(self.now[0] - 30))
        self.assertEqual(self.sshd.calls, 2)

    def test_auth_log_when_journal_is_empty(self):
        self.audit(audit_line("203.0.113.5", "51000", "2033-05-18T03:00:00Z"))
        self.write("/var/log/auth.log", "May 18 03:00:00 h sshd[1]: Accepted publickey for root from 203.0.113.5 "
                                        "port 51000 ssh2: ED25519 %s\n" % self.fp1)
        self.assertIn(self.fp1, self.seen.seen(self.runner))

    def test_an_old_call_never_makes_a_new_key_connected(self):
        # R-333 С1: a call 5 days old of a deleted key, nothing in the logs; the new key is the only one
        self.audit(audit_line("203.0.113.5", "50123", iso(self.NOW - 5 * 86400), client="claude-code/2.0"))
        self.assertEqual(self.keys(self.fp1), {self.fp1: None})
        self.assertEqual(self.sshd.calls, 1)
        self.audit(audit_line("203.0.113.7", "50125", iso(self.NOW - 20 * 86400)))
        self.tick(60)
        self.assertEqual(self.keys(self.fp1), {self.fp1: None})
        self.assertEqual(self.sshd.calls, 1)                       # older than the logs we read: not looked for
        # two keys, an unknown call: whose it is is not known
        self.keys(self.fp1, self.fp2)
        self.tick(60)
        self.audit(audit_line("203.0.113.6", "50124", iso(self.now[0] - 10)))
        self.assertEqual(self.keys(self.fp1, self.fp2), {self.fp1: None, self.fp2: None})     # not looked up yet
        self.tick(FRESH_PLUS)
        self.assertEqual(self.keys(self.fp1, self.fp2), {self.fp1: None, self.fp2: None})     # not in the logs

    def test_unknown_goes_to_a_key_only_by_elimination(self):
        self.seen.note_added(self.fp1)                             # kaskad.mcp.keys.add
        self.tick(100)
        self.audit(audit_line("203.0.113.5", "50000", iso(self.now[0] - 50)))          # only fp1 was there
        self.seen.note_added(self.fp2)
        self.tick(100)
        self.audit(audit_line("203.0.113.5", "50001", iso(self.now[0] - 50), client="cursor/0.1"))   # both were
        self.tick(FRESH_PLUS)
        got = self.keys(self.fp1, self.fp2)
        self.assertEqual(got[self.fp1]["at"], int(self.NOW + 50))
        self.assertIsNone(got[self.fp2])
        self.seen.note_gone(self.fp1)                              # kaskad.mcp.keys.delete
        self.tick(100)
        self.audit(audit_line("203.0.113.5", "50002", iso(self.now[0] - 50), client="cursor/0.1"))   # only fp2
        self.tick(FRESH_PLUS)
        got = self.keys(self.fp2)
        self.assertEqual(got[self.fp2]["client"], "cursor/0.1")
        self.assertEqual(got[self.fp2]["at"], int(self.now[0] - FRESH_PLUS - 50))
        # a key found in the file without a note (added by hand, before the update): from the moment it is seen
        self.audit(audit_line("198.51.100.1", "40000", iso(self.now[0] - 1)))
        k3, fp3 = ed25519(3)
        self.tick(5)
        self.assertIsNone(self.keys(fp3)[fp3])

    def test_connected_survives_a_restart_and_the_rotation(self):
        self.seen.note_added(self.fp1)
        self.audit(audit_line("203.0.113.5", "51000", iso(self.NOW + 10)))
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        self.tick(20)
        self.assertEqual(self.keys(self.fp1)[self.fp1]["at"], int(self.NOW + 10))
        with open(self.path("/var/log/kaskad-mcp.log"), "w"):     # logrotate copytruncate; the panel restarted
            pass
        self.tick(7 * 86400)
        self.assertEqual(self.keys(self.fp1)[self.fp1]["at"], int(self.NOW + 10))
        st = self.state()
        self.assertEqual(st["pairs"], {})                          # pairs that left the audit go
        self.assertEqual(oct(os.stat(self.seen.state_path()).st_mode & 0o777), "0o600")
        self.tick(5)
        self.assertEqual(self.keys(), {})                          # the key deleted: its record goes
        self.assertEqual(self.state()["last"], {})
        self.tick(31 * 86400)
        self.keys()
        st = self.state()
        self.assertEqual(st["keys"], {})                           # old intervals go, the horizon moves
        self.assertEqual(st["horizon"], int(self.NOW + 20 + 7 * 86400 + 5))

    def test_a_broken_state_file_is_not_an_error(self):
        os.makedirs(os.path.dirname(self.seen.state_path()), exist_ok=True)
        for text in ("not json", '{"v": 1, "keys": {"x": [1, [2], ["a", null]]}, "pairs": {"a b": 5}, "last": {"x": 1}}',
                     '{"v": 1, "keys": [], "scan": null}'):
            with open(self.seen.state_path(), "w") as f:
                f.write(text)
            self.tick(5)
            self.assertEqual(self.keys(self.fp1), {self.fp1: None}, text)

    def test_spoiled_nested_fields_are_repaired(self):
        # R-333 З1′: a nested field of a wrong type gave TypeError -> GET /api/mcp 500 for ever
        os.makedirs(os.path.dirname(self.seen.state_path()), exist_ok=True)
        self.audit(audit_line("203.0.113.5", "51000", iso(self.NOW - 60)))
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        spoiled = [
            '{"v":1,"scan":{"next":"x","fail":0}}',
            '{"v":1,"scan":{"next":0,"fail":"x"}}',
            '{"v":1,"scan":{"next":Infinity,"fail":0}}',
            '{"v":1,"scan":{"next":NaN,"fail":-3}}',
            '{"v":1,"scan":{"next":true,"fail":[1]}}',
            '{"v":1,"pairs":{"203.0.113.5 51000":{"fp":null,"init":"x"}}}',
            '{"v":1,"pairs":{"203.0.113.5 51000":{"fp":5,"init":1}}}',
            '{"v":1,"pairs":{"203.0.113.5 51000":{"fp":null,"init":NaN}}}',
            '{"v":1,"horizon":"x","last":{"%s":{"at":"x"}}}' % self.fp1,
            '{"v":1,"horizon":Infinity,"last":{"%s":{"at":1,"client":{"a":1}}}}' % self.fp1,
            '{"v":1,"keys":{"%s":[[1,"x"],[null,null],"z"]}}' % self.fp1,
            '{"v":1,"keys":{"%s":{"a":1}},"pairs":[],"last":null,"scan":"x"}' % self.fp1,
            '[1,2]', '{"v":2}', '{"v":1', '',
        ]
        for text in spoiled:
            with open(self.seen.state_path(), "w") as f:
                f.write(text)
            self.tick(5)
            got = self.keys(self.fp1)                                       # no exception
            self.assertEqual(got[self.fp1]["at"], int(self.NOW - 60), text)  # and the logs are read again
            st = self.state()                                               # the file is repaired by the write
            self.assertIsInstance(st["scan"]["next"], (int, float), text)
            self.assertIsInstance(st["scan"]["fail"], int, text)
            for p in st["pairs"].values():
                self.assertTrue(p["init"] is None or isinstance(p["init"], (int, float)), text)
                self.assertTrue(p["fp"] is None or isinstance(p["fp"], str), text)
            self.seen.seen(self.runner)                                     # the other entry too
        # R-333 П2″: bytes that are not UTF-8 and a nesting past the parser's recursion limit are repaired too
        for raw in (b'{"v":1,"pairs":{}}\xff\xfe', b'\xff' * 16,
                    b'{"v":1,"pairs":' + b"[" * 200000 + b"]" * 200000 + b"}", b"\0" * 64):
            with open(self.seen.state_path(), "wb") as f:
                f.write(raw)
            self.tick(5)
            got = self.keys(self.fp1)
            self.assertEqual(got[self.fp1]["at"], int(self.NOW - 60), raw[:20])
            with open(self.seen.state_path(), "rb") as f:
                self.assertEqual(json.loads(f.read().decode("utf-8"))["v"], 1, raw[:20])
            self.seen.note_gone(self.fp2)                                   # the key's add/delete path too
            self.seen.note_added(self.fp2)
        # the audit tail: a line nested past the limit is skipped, not an error
        self.audit("[" * 200000 + "]" * 200000 + "\n")
        self.tick(5)
        self.assertEqual(self.keys(self.fp1)[self.fp1]["at"], int(self.NOW - 60))
        from kaskad.panel import ux
        with mock.patch.object(self.seen, "for_keys", side_effect=RecursionError("x")), \
                mock.patch("kaskad.mcp.keys.listing", return_value={"keys": [{"fingerprint": self.fp1, "type": "t"}],
                                                                   "connect": {"host": "h"}}):
            self.assertEqual(json.loads(ux.mcp_get(None).body)["keys"][0]["seen"], None)
        # the panel: a broken state never gives 500
        from kaskad.panel import ux
        with mock.patch.object(self.seen, "for_keys", side_effect=TypeError("x")), \
                mock.patch("kaskad.mcp.keys.listing", return_value={"keys": [{"fingerprint": self.fp1, "type": "t"}],
                                                                   "connect": {"host": "h"}}):
            body = json.loads(ux.mcp_get(None).body)
        self.assertEqual(body["keys"][0]["seen"], None)

    def test_a_long_session_of_a_deleted_key_never_lights_the_new_one(self):
        # R-333 З3′: OLD holds its connection for days, its initialize left the audit tail, the journal does not
        # name it; OLD is deleted, NEW added — a call after that is not NEW's by elimination
        self.seen.note_added(self.fp1)
        self.tick(10 * 86400)
        for i in range(3):
            self.audit(audit_line("203.0.113.5", "51000", iso(self.now[0] - 3600 + i), kind="call", client="claude-desktop/1.0"))
        self.seen.note_gone(self.fp1)
        self.seen.note_added(self.fp2)
        self.tick(60)
        self.audit(audit_line("203.0.113.5", "51000", iso(self.now[0] - 1), kind="call", client="claude-desktop/1.0"))
        self.tick(FRESH_PLUS)
        self.assertEqual(self.keys(self.fp2), {self.fp2: None})
        # a connection opened (initialize in the tail) when NEW alone was there is NEW's — even as its calls go on
        self.audit(audit_line("198.51.100.9", "40000", iso(self.now[0] - 5), client="claude-code/2.0"))
        for _ in range(30):                                                # P2′: ten minutes of calls, not «waiting»
            self.tick(20)
            self.audit(audit_line("198.51.100.9", "40000", iso(self.now[0] - 1), kind="call", client="-"))
            got = self.keys(self.fp2)
        self.assertEqual(got[self.fp2]["client"], "claude-code/2.0")
        self.assertGreaterEqual(got[self.fp2]["at"], int(self.now[0] - 25))

    def test_a_failed_journal_with_auth_log_is_not_final(self):
        # R-333 П1′: journalctl timed out, auth.log read: a pair it does not name is looked up again later
        self.audit(audit_line("203.0.113.5", "51000", iso(self.NOW - 3600)),
                   audit_line("203.0.113.6", "52000", iso(self.NOW - 3600)))
        self.write("/var/log/auth.log", "May 18 03:00:00 h sshd[1]: Accepted publickey for root from 203.0.113.6 "
                                        "port 52000 ssh2: ED25519 %s\n" % self.fp2)
        self.sshd.rc = 124
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        got = self.seen.seen(self.runner)
        self.assertIn(self.fp2, got)                                        # what auth.log named is taken
        self.assertNotIn("203.0.113.5 51000", self.state()["pairs"])        # the rest is not final
        self.sshd.rc = 0
        for _ in range(20):
            self.tick(30)
            got = self.seen.seen(self.runner)
        self.assertIn(self.fp1, got)
        self.assertNotIn("?", got)

    def test_the_load_of_the_polling(self):
        # R-333 З1: 300 old connections, two keys, the card polls every 5 s for 10 minutes
        for i in range(300):
            self.audit(audit_line("203.0.113.5", str(40000 + i), iso(self.NOW - 3 * 86400 - i)))
        for _ in range(120):
            self.keys(self.fp1, self.fp2)
            self.tick(5)
        self.assertEqual(self.sshd.calls, 1)
        self.assertEqual(self.sshd.since, ["@%d" % (self.NOW - 3 * 86400 - 299 - 600)])
        # a fresh connection not in the journal yet: read again every 30 s while it is fresh, then never
        self.audit(audit_line("203.0.113.5", "50000", iso(self.now[0])))
        for _ in range(120):
            self.keys(self.fp1, self.fp2)
            self.tick(5)
        self.assertLessEqual(self.sshd.calls, 1 + 5)

    def test_a_failed_read_backs_off(self):
        self.sshd.rc = 124
        self.audit(audit_line("203.0.113.5", "50000", iso(self.NOW - 600)))
        for _ in range(720):                                       # an hour of polling
            self.keys(self.fp1)
            self.tick(5)
        self.assertLessEqual(self.sshd.calls, 7)                   # 60, 120, 240 … s
        self.sshd.rc = 0
        self.sshd.accept("203.0.113.5", "50000", self.fp1)
        for _ in range(720):
            self.keys(self.fp1)
            self.tick(5)
        self.assertIsNotNone(self.keys(self.fp1)[self.fp1])

    def test_a_new_initialize_on_the_same_pair_is_looked_up_again(self):
        self.audit(audit_line("203.0.113.5", "51000", iso(self.NOW - 3600)))
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        self.assertIn(self.fp1, self.seen.seen(self.runner))
        self.tick(60)
        self.audit(audit_line("203.0.113.5", "51000", iso(self.now[0] - 1)))           # NAT gave the port again
        self.sshd.accept("203.0.113.5", "51000", self.fp2)
        got = self.seen.seen(self.runner)
        self.assertEqual(got[self.fp2]["at"], int(self.now[0] - 1))
        self.assertEqual(self.sshd.calls, 2)


FRESH_PLUS = 130


MCP_PY = ["python3", "-I", "-c", 'import runpy, sys; sys.path.insert(0, "/opt/kaskad/lib/current"); '
           'runpy.run_module("kaskad.mcp", run_name="__main__", alter_sys=True)']


class SessionsTest(RootCase):
    """kaskad.mcp.sessions: deleting a key ends the kaskad-mcp sessions it opened (sshd does not drop them)."""
    NOW = 2000000000.0

    def setUp(self):
        super().setUp()
        from kaskad.mcp import keys, seen, sessions
        self.keys, self.seen, self.sessions = keys, seen, sessions
        seen.forget()
        self.now = [self.NOW]
        self.killed, self.stubborn = [], set()
        self._p = [mock.patch.object(sessions, "_now", lambda: self.now[0]),
                   mock.patch.object(seen, "_now", lambda: self.now[0]),
                   mock.patch.object(sessions, "_sleep", self._sleep),
                   mock.patch.object(sessions, "_kill", self._kill)]
        for p in self._p:
            p.start()
        self.runner = FakeRunner(binaries=["journalctl"])
        self.sshd = FakeSshd(self.runner)
        self.k1, self.fp1 = ed25519(1)
        self.k2, self.fp2 = ed25519(2)
        self.write("/proc/stat", "cpu 1 2 3\nbtime %d\n" % (self.NOW - 86400))

    def tearDown(self):
        for p in self._p:
            p.stop()
        self.seen.forget()
        super().tearDown()

    def _sleep(self, s):
        self.now[0] += s

    def _kill(self, pid, sig):
        import shutil
        import signal
        if not os.path.isdir(self.path("/proc/%d" % pid)):
            raise ProcessLookupError(pid)
        self.killed.append((pid, sig))
        if sig == signal.SIGKILL or pid not in self.stubborn:
            shutil.rmtree(self.path("/proc/%d" % pid))                  # the process exits

    def proc(self, pid, argv, conn=None, age=3600):
        self.write("/proc/%d/cmdline" % pid, "\0".join(argv) + "\0")
        env = ["PATH=/usr/bin", "USER=root"] + (["SSH_CONNECTION=%s %s 192.0.2.10 22" % conn] if conn else [])
        self.write("/proc/%d/environ" % pid, "\0".join(env) + "\0")
        ticks = int((86400 - age) * os.sysconf("SC_CLK_TCK"))
        self.write("/proc/%d/stat" % pid, "%d (python3) S " % pid + " ".join(["0"] * 18 + [str(ticks), "0"]))

    def scene(self):
        self.proc(101, MCP_PY, ("203.0.113.5", "51000"))                     # fp1's agent
        self.proc(102, MCP_PY, ("203.0.113.6", "52000"))                     # fp2's agent
        self.proc(103, ["/bin/sh", "/usr/local/bin/kaskad-mcp"], ("198.51.100.1", "40000"))
        self.proc(104, ["-bash"], ("192.0.2.50", "60000"))                   # the owner's shell
        self.proc(105, ["grep", "kaskad-mcp", "/var/log/syslog"], ("192.0.2.50", "60000"))
        self.proc(106, MCP_PY)                                               # kaskad-mcp without SSH
        self.proc(107, ["bash", "-c", "/usr/local/bin/kaskad-mcp"], ("203.0.113.5", "51001"))
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        self.sshd.accept("203.0.113.6", "52000", self.fp2)
        self.sshd.accept("192.0.2.50", "60000", self.fp2)
        self.sshd.accept("203.0.113.5", "51001", self.fp1)

    def add(self, k):
        self.keys.add(k)

    def test_the_sessions_of_the_key_end(self):
        import signal
        self.scene()
        self.sshd.accept("198.51.100.1", "40000", self.fp2)                  # every session's key is known
        self.add(self.k1)
        self.add(self.k2)
        self.stubborn.add(101)
        res = self.keys.delete(self.fp1, self.runner)
        self.assertEqual(res["ended"], 2)
        self.assertFalse(res["unsure"])
        self.assertEqual(sorted(set(p for p, _ in self.killed)), [101, 107])
        self.assertIn((101, signal.SIGKILL), self.killed)                    # did not go on SIGTERM
        self.assertEqual(res["text"], i18n.tr("mcp.text.removed_cut"))
        for pid in (102, 103, 104, 105, 106):
            self.assertTrue(os.path.isdir(self.path("/proc/%d" % pid)), pid)
        # the logs read from a little before the oldest session started
        self.assertEqual(self.sshd.since, ["@%d" % (self.NOW - 3600 - 600)])

    def test_unknown_sessions_end_too_and_the_answer_says_so(self):
        self.scene()                                                         # 103: the logs do not name it
        self.add(self.k1)
        self.add(self.k2)
        res = self.keys.delete(self.fp1, self.runner)
        self.assertEqual(sorted(p for p, _ in self.killed), [101, 103, 107])
        self.assertTrue(res["unsure"])
        self.assertEqual(res["text"], i18n.tr("mcp.text.removed_all"))
        self.assertTrue(os.path.isdir(self.path("/proc/102")))              # proven another key's: stays

    def test_no_logs_ends_every_agent_session_only(self):
        self.scene()
        self.sshd.rc = 124                                                   # journalctl failed, no auth.log
        self.add(self.k1)
        res = self.keys.delete(self.fp1, self.runner)
        self.assertEqual(sorted(p for p, _ in self.killed), [101, 102, 103, 107])
        self.assertTrue(res["unsure"])
        for pid in (104, 105, 106):                                          # not an agent's ssh session
            self.assertTrue(os.path.isdir(self.path("/proc/%d" % pid)), pid)

    def test_nothing_open(self):
        self.add(self.k1)
        res = self.keys.delete(self.fp1, self.runner)
        self.assertEqual((res["ended"], res["unsure"], self.sshd.calls), (0, False, 0))
        self.assertEqual(res["text"], i18n.tr("mcp.text.removed"))

    # R-333 П1″: between the listing and a signal the logs are read (up to 15 s) and GRACE passes — a pid that went to
    # another process meanwhile gets no signal
    def _replace(self, pid, argv, conn=None, age=3600):
        import shutil
        shutil.rmtree(self.path("/proc/%d" % pid), ignore_errors=True)
        self.proc(pid, argv, conn, age=age)

    def test_a_pid_reused_while_the_logs_are_read_gets_no_signal(self):
        self.proc(101, MCP_PY, ("203.0.113.5", "51000"))
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        self.add(self.k1)
        real = self.seen._scan

        def scan(runner, since):
            self._replace(101, ["/usr/sbin/sshd", "-D"])                 # the session exited, sshd got its pid
            return real(runner, since)
        with mock.patch.object(self.seen, "_scan", scan):
            res = self.keys.delete(self.fp1, self.runner)
        self.assertEqual(self.killed, [])
        self.assertEqual(res["ended"], 0)
        self.assertTrue(os.path.isdir(self.path("/proc/101")))

    def test_a_pid_reused_by_a_new_session_during_grace_gets_no_sigkill(self):
        import signal
        self.proc(101, MCP_PY, ("203.0.113.5", "51000"))
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        self.add(self.k1)
        for conn, age in ((("203.0.113.9", "53000"), 3600),            # another session's SSH_CONNECTION
                          (("203.0.113.5", "51000"), 5)):               # the same address, started later
            self.killed[:] = []
            self._replace(101, MCP_PY, ("203.0.113.5", "51000"))
            real = self._kill

            def kill(pid, sig, conn=conn, age=age, real=real):
                real(pid, sig)
                if sig == signal.SIGTERM and pid == 101:
                    self._replace(101, MCP_PY, conn, age=age)          # the pid went to a new kaskad-mcp
            with mock.patch.object(self.sessions, "_kill", kill):
                res = self.sessions.end_for(self.fp1, self.runner)
            self.assertEqual(self.killed, [(101, signal.SIGTERM)], conn)
            self.assertEqual(res["ended"], 1)
            self.assertTrue(os.path.isdir(self.path("/proc/101")))

    def test_the_same_session_still_gets_sigkill(self):
        import signal
        self.proc(101, MCP_PY, ("203.0.113.5", "51000"))
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        self.stubborn.add(101)
        res = self.sessions.end_for(self.fp1, self.runner)
        self.assertEqual(self.killed, [(101, signal.SIGTERM), (101, signal.SIGKILL)])
        self.assertEqual(res["ended"], 1)

    def test_a_clock_step_does_not_hide_the_session(self):
        """R-333 П2‴: btime in /proc/stat moves when the clock is stepped; the session is told by its raw start ticks."""
        import signal
        self.proc(101, MCP_PY, ("203.0.113.5", "51000"))
        self.sshd.accept("203.0.113.5", "51000", self.fp1)
        self.stubborn.add(101)
        real = self.seen._scan

        def scan(runner, since):
            self.write("/proc/stat", "cpu 1 2 3\nbtime %d\n" % (self.NOW - 86400 + 1))   # the clock stepped by 1 s
            return real(runner, since)
        with mock.patch.object(self.seen, "_scan", scan):
            res = self.sessions.end_for(self.fp1, self.runner)
        self.assertEqual(self.killed, [(101, signal.SIGTERM), (101, signal.SIGKILL)])
        self.assertEqual(res, {"ended": 1, "unsure": False})

    def test_the_journal_line_agrees_with_the_number_of_keys(self):
        """R-333 П4‴: «ключ … удалён» for one key, «ключи … удалены» / «keys … removed» for several."""
        from kaskad import util
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        lines = []
        with mock.patch.object(self.sessions, "end_for", lambda fps, runner: {"ended": 2, "unsure": False}), \
                mock.patch.object(util, "log_action", lines.append):
            self.keys.end_sessions([self.fp1], self.runner)
            self.keys.end_sessions([self.fp2, self.fp1], self.runner)
        with util.journal_lang():
            one = i18n.tr("journal.mcp.sessions_ended", fp=self.fp1, n=2)
            many = i18n.tr("journal.mcp.sessions_ended_many", fp=", ".join(sorted([self.fp1, self.fp2])), n=2)
        self.assertEqual(lines, [one, many])
        cat = {}
        for lang in ("ru", "en"):
            with open(os.path.join(root, "lib", "kaskad", "i18n", "catalog", lang, "journal.json"), encoding="utf-8") as f:
                cat[lang] = json.load(f)
        self.assertIn("(ключ {fp} удалён)", cat["ru"]["journal.mcp.sessions_ended"])
        self.assertIn("(ключи {fp} удалены)", cat["ru"]["journal.mcp.sessions_ended_many"])
        self.assertIn("(key {fp} removed)", cat["en"]["journal.mcp.sessions_ended"])
        self.assertIn("(keys {fp} removed)", cat["en"]["journal.mcp.sessions_ended_many"])

    def test_several_keys_at_once(self):
        """The rollback of an import (kaskad.backup) ends the sessions of all the keys it removed with one look."""
        self.scene()
        self.sshd.accept("198.51.100.1", "40000", self.fp2)
        res = self.sessions.end_for([self.fp1, self.fp2], self.runner)
        self.assertEqual(sorted(set(p for p, _ in self.killed)), [101, 102, 103, 107])
        self.assertEqual((res["ended"], res["unsure"], self.sshd.calls), (4, False, 1))

    def test_is_mcp(self):
        m = self.sessions.is_mcp
        self.assertTrue(m(MCP_PY))
        self.assertTrue(m(["/usr/local/bin/kaskad-mcp"]))
        self.assertTrue(m(["bash", "-c", "/usr/local/bin/kaskad-mcp"]))
        self.assertFalse(m(["bash", "-c", "grep kaskad-mcp x"]))
        self.assertFalse(m(["grep", "kaskad-mcp"]))
        self.assertFalse(m(["python3", "-c", "print('kaskad.mcp')"]))
        self.assertFalse(m([]))


class SeenLabelTest(RootCase):
    def test_label_is_brought_to_the_form_not_refused(self):
        from kaskad.mcp import keys
        from kaskad.panel import ux
        for given, want in (("laptop", "laptop"), ("Ноутбук Вити", "Noutbuk-Viti"), ("мой  ПК!!", "moy-PK"),
                            ("ab cd/ef", "ab-cd-ef"), ("x" * 40, "x" * 32), ("!!!", None), ("", None), (None, None),
                            (5, None), ("Щука" * 10, ("Schuka" * 6)[:32])):
            got = ux._label(given)
            self.assertEqual(got, want, given)
            self.assertTrue(got is None or keys.LABEL_RE.match(got), got)


# ── a protocol: journal, why, restart ──────────────────────────────────────

ST = {"gateway": {"enabled": True, "inbounds": {"wg": {"port": 51820}}}}


class InlogTest(RootCase):
    def setUp(self):
        super().setUp()
        from kaskad.gw import inlog, registry
        registry.reset()
        self.inlog = inlog
        self.runner = FakeRunner(binaries=["journalctl", "systemctl"])
        self.out = [""]
        self.runner.on(["journalctl"], lambda argv, inp: (0, self.out[0], ""))

    def say(self, *lines):
        self.out[0] = "".join("2026-10-12T14:00:%02d+0300 node kaskad-gw-wg.service[77]: %s\n" % (i, l)
                              for i, l in enumerate(lines))

    def test_units_and_the_command(self):
        self.assertEqual(self.inlog.units("vless"), ["kaskad-gw-xray.service"])
        self.assertEqual(self.inlog.units("wg"), ["kaskad-gw-wg.service"])
        from kaskad.gw import registry
        with mock.patch.object(registry.get("awg2"), "unit_core", lambda: "kernel"):
            self.assertEqual(self.inlog.units("awg2"), ["kaskad-gw-awg2.service", "kaskad-gw-awgkmod.service"])
        self.say("started")
        doc = self.inlog.journal(self.runner, "l2tp")
        self.assertEqual(doc["cmd"], "journalctl -u kaskad-gw-accel.service -u kaskad-gw-ipsec.service -n 30")
        argv = self.runner.commands(["journalctl"])[-1]
        self.assertEqual(argv[:8], ["journalctl", "-q", "--no-pager", "-o", "short-iso", "-n", "30", "-u"])

    def test_what_else_a_restart_breaks(self):
        on = ["wg", "awg2", "l2tp", "pptp", "sstp", "ikev2", "vless", "socks5", "openvpn"]
        self.assertEqual(self.inlog.shared("vless", on), {"gateway": True, "with": ["socks5"]})
        self.assertEqual(self.inlog.shared("socks5", ["socks5"]), {"gateway": True, "with": []})
        self.assertEqual(self.inlog.shared("l2tp", on), {"gateway": False, "with": ["pptp", "sstp", "ikev2"]})
        self.assertEqual(self.inlog.shared("pptp", on), {"gateway": False, "with": ["l2tp", "sstp"]})
        self.assertEqual(self.inlog.shared("ikev2", on), {"gateway": False, "with": ["l2tp"]})
        for kind in ("wg", "awg2", "openvpn"):
            self.assertEqual(self.inlog.shared(kind, on), {"gateway": False, "with": []}, kind)

    def test_journal_redacts_cuts_and_marks_errors(self):
        self.say("Started WireGuard", "bad key vpn://AAAABBBBCCCC and %s" % TOKEN, "x" * 900,
                 "Error: Address already in use", "permission DENIED")
        doc = self.inlog.journal(self.runner, "wg")
        ts = [l["t"] for l in doc["lines"]]
        self.assertEqual([l["err"] for l in doc["lines"]], [False, False, False, True, True])
        self.assertNotIn("AAAABBBB", ts[1])
        self.assertNotIn(TOKEN, ts[1])
        self.assertEqual(len(ts[2]), 400)
        self.runner.binaries.discard("journalctl")
        self.assertEqual(self.inlog.journal(self.runner, "wg")["lines"], [])

    def test_old_busy_of_an_earlier_start_is_not_the_reason(self):
        # review №5: «Address already in use» of a start before the last one says nothing about the failure now
        row = {"installed": True, "enabled": True, "running": False}
        self.say("RTNETLINK answers: Address already in use", "systemd[1]: Starting WireGuard via wg-quick",
                 "Line unrecognized: `Jc=4'")
        f = self.inlog.fail_info(self.runner, "wg", row, ST)
        self.assertEqual(f["why"], "other")
        self.say("systemd[1]: Starting WireGuard via wg-quick", "bind 0.0.0.0:51820: Address already in use")
        self.assertEqual(self.inlog.fail_info(self.runner, "wg", row, ST)["why"], "busy")

    def test_fail_busy_other_and_words(self):
        row = {"installed": True, "enabled": True, "running": False}
        self.say("Starting", "bind: Address already in use")
        f = self.inlog.fail_info(self.runner, "wg", row, ST)
        self.assertEqual((f["why"], f["port"], f["proto"], f["line"]), ("busy", 51820, "udp",
                                                                        "bind: Address already in use"))
        self.assertIn("51820/udp", f["human"])
        for line, key in (("Error: Unknown device type", "kmod"), ("Line unrecognized: `Jc=x'", "config"),
                          ("open /etc/x.pem: permission denied", "denied"),
                          ("Cannot find device \"kgw-wg\"", "device"),
                          ("failed: x509: certificate has expired", "cert")):
            self.say("Starting", line, "Failed with result 'exit-code'.")
            f = self.inlog.fail_info(self.runner, "wg", row, ST)
            self.assertEqual((f["why"], f["line"], f["human"]), ("other", line, i18n.tr("gw_inlog.human." + key)), line)
        self.say("Error: Unknown device type")
        self.assertEqual(self.inlog.fail_info(self.runner, "awg2", row, ST)["human"],
                         i18n.tr("gw_inlog.human.kmod_awg"))
        self.say("Starting", "panic: something odd")
        f = self.inlog.fail_info(self.runner, "wg", row, ST)
        self.assertEqual((f["why"], f["line"], f["human"]), ("other", "panic: something odd", ""))
        self.say("Starting")
        self.assertEqual(self.inlog.fail_info(self.runner, "wg", row, ST)["line"], "")
        for r in ({"installed": False, "running": False}, {"installed": True, "enabled": False, "running": False},
                  {"installed": True, "enabled": True, "running": None}):
            self.assertIsNone(self.inlog.fail_info(self.runner, "wg", r, ST), r)

    def test_closed_by_time(self):
        row = {"installed": True, "enabled": True, "running": True}
        self.assertIsNone(self.inlog.fail_info(self.runner, "wg", row, ST))
        self.inlog.mark_issued("wg", now=1000)
        self.assertEqual(oct(os.stat(self.inlog.seen_path()).st_mode & 0o777), "0o644")
        with mock.patch.object(self.inlog, "_now", lambda: 1000 + 600):
            self.assertIsNone(self.inlog.fail_info(self.runner, "wg", row, ST))
        with mock.patch.object(self.inlog, "_now", lambda: 1000 + 601):
            f = self.inlog.fail_info(self.runner, "wg", row, ST)
            self.assertEqual((f["why"], f["port"], f["proto"], f["line"]), ("closed", 51820, "udp", ""))
            self.assertTrue(f["human"])
            # a session after the issue: written once, the port is open
            self.assertFalse(self.inlog.note_sessions(["awg2"], now=1500))
            mtime = os.stat(self.inlog.seen_path()).st_mtime_ns
            self.assertFalse(self.inlog.note_sessions(["wg"], now=1500))            # a bare kind proves nothing (З-1)
            self.assertTrue(self.inlog.note_sessions([{"kind": "wg", "device": "d-1", "since": 1400}], now=1500))
            self.assertIsNone(self.inlog.fail_info(self.runner, "wg", row, ST))
            self.assertNotEqual(os.stat(self.inlog.seen_path()).st_mtime_ns, 0 * mtime)
            self.assertFalse(self.inlog.note_sessions([{"kind": "wg", "device": "d-1", "since": 1590}], now=1600))
        self.inlog.mark_issued("wg", now=2000)                                       # a new issue: watch again
        with mock.patch.object(self.inlog, "_now", lambda: 2700):
            self.assertEqual(self.inlog.fail_info(self.runner, "wg", row, ST)["why"], "closed")
        self.assertFalse(self.inlog.note_sessions([]))
        # SOCKS5 (xray) never reports sessions: «nobody connected» would be a false alarm
        self.inlog.mark_issued("socks5", now=1000)
        with mock.patch.object(self.inlog, "_now", lambda: 5000):
            self.assertFalse(self.inlog.closed("socks5"))


class XrayOwnLineKT3Test(InlogTest):
    """КТ3-10 (снимок владельца 20.10): карточка Hysteria2 «не работает» с причиной — строкой сканера RDP, постучавшегося
    в другой вход общего xray. Причина входа на общем xray — только его строка (тег или порт в «listen», уровень Error)."""
    HY = {"gateway": {"enabled": True, "inbounds": {"hy2": {"enabled": True, "port": 443}}}}
    SCAN = ('[Warning] [3317] app/proxyman/inbound: connection ends > proxy/http: failed to read http request > '
            'malformed HTTP request "\\x03\\x00\\x00/*\\xe0\\x00\\x00\\x00\\x00\\x00Cookie: mstshash=Administr"')

    def xray(self, *lines):
        self.out[0] = "".join("2026-10-20T09:00:%02d+0300 node xray[95256]: %s\n" % (i, l) for i, l in enumerate(lines))

    def test_a_foreign_line_is_never_the_reason(self):
        row = {"installed": True, "enabled": True, "running": False}
        self.xray(self.SCAN, "[Error] app/proxyman/inbound: gw-socks5 > failed to handle connection: something odd",
                  "[Warning] gw-hy2 > something minor")
        f = self.inlog.fail_info(self.runner, "hy2", row, self.HY)
        self.assertEqual((f["why"], f["line"]), ("other", ""))            # no own Error line: no line, words only
        self.assertNotIn("mstshash", repr(f))

    def test_its_own_error_is_the_reason(self):
        row = {"installed": True, "enabled": True, "running": False}
        own = "[Error] app/proxyman/inbound: gw-hy2 > failed to load certificate: open x.pem: permission denied"
        self.xray(self.SCAN, own)
        f = self.inlog.fail_info(self.runner, "hy2", row, self.HY)
        self.assertEqual((f["why"], f["line"]), ("cert", own))           # REV-3373 З-3: the card offers «Применить»
        self.assertEqual(f["human"], i18n.tr("gw_inlog.human.cert_self"))
        self.xray(self.SCAN, "Failed to start: main: failed to create server > failed to listen UDP on 0.0.0.0:443 > "
                             "address already in use")
        f = self.inlog.fail_info(self.runner, "hy2", row, self.HY)
        self.assertEqual((f["why"], f["port"]), ("busy", 443))            # its port in a «listen» line
        self.xray("Failed to start: failed to listen TCP on 0.0.0.0:8443 > address already in use")
        self.assertEqual(self.inlog.fail_info(self.runner, "hy2", row, self.HY)["why"], "other")   # another port

    def test_the_same_port_of_another_protocol_is_not_its_line(self):
        """REV-3371-3 З-2: VLESS 443/tcp и Hysteria2 443/udp — обычная пара; «listen TCP on 443» — не строка Hysteria2."""
        row = {"installed": True, "enabled": True, "running": False}
        tcp = ("Failed to start: main: failed to create server > app/proxyman/inbound: failed to listen TCP on 443 > "
               "transport/internet: failed to listen on address: 0.0.0.0:443 > listen tcp 0.0.0.0:443: bind: "
               "address already in use")
        self.xray(tcp)
        f = self.inlog.fail_info(self.runner, "hy2", row, self.HY)
        self.assertEqual((f["why"], f["line"]), ("other", ""))
        self.assertFalse(self.inlog._own_xray("hy2", self.HY, tcp))
        self.assertFalse(self.inlog._own_xray("hy2", self.HY, "Failed to start: listen on 443 > address in use"))
        udp = "Failed to start: listen udp 0.0.0.0:443: bind: address already in use"
        self.assertTrue(self.inlog._own_xray("hy2", self.HY, udp))          # Go words, its protocol: its line
        self.xray(udp)
        self.assertEqual(self.inlog.fail_info(self.runner, "hy2", row, self.HY)["why"], "busy")


FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fix(*parts):
    with open(os.path.join(FIX, *parts), encoding="utf-8") as f:
        return f.read()


class Stand3372CardsTest(InlogTest):
    """Стенд 3.3.7.2, разд. 8: Н-8.1 (занятость — по протоколу входа), Н-8.3 (сертификат раньше «config»)."""
    xray = XrayOwnLineKT3Test.xray
    ALL = {"gateway": {"enabled": True, "inbounds": {"vless": {"enabled": True, "port": 443},
                                                     "socks5": {"enabled": True, "port": 1080},
                                                     "hy2": {"enabled": True, "port": 443}}}}
    # T-3372.md, разд. 8, П-2 — дословно
    BUSY_UDP = ("Failed to start: app/proxyman/inbound: failed to listen TCP on 443 > transport/internet: failed to "
                "listen on address: 0.0.0.0:443 > listen udp 0.0.0.0:443: bind: address already in use")
    # T-3372.md, разд. 8, З-3 «Файла нет» — дословно
    NO_CERT = ("Failed to start: main: failed to load config files: [/run/credentials/kaskad-gw-xray.service/xray.json] > "
               "infra/conf: failed to build inbound config with tag gw-hy2 > infra/conf: Failed to build TLS config. > "
               "infra/conf: failed to parse certificate > open /opt/kaskad/gw/tls-hy2/domain/fullchain.pem: no such file "
               "or directory")
    DOWN = {"installed": True, "enabled": True, "running": False}

    def test_busy_udp_443_is_hysteria2_only(self):
        self.xray(self.BUSY_UDP)
        hy = self.inlog.fail_info(self.runner, "hy2", self.DOWN, self.ALL)
        self.assertEqual((hy["why"], hy["port"], hy["proto"]), ("busy", 443, "udp"))
        self.assertIn("443/udp", hy["human"])
        for kind in ("vless", "socks5"):                    # the shared xray is down, but not by THEIR port
            f = self.inlog.fail_info(self.runner, kind, self.DOWN, self.ALL)
            self.assertNotEqual(f["why"], "busy", kind)
            self.assertNotIn("tcp", f["human"], kind)
            self.assertNotIn("443", f["human"], kind)
            self.assertFalse(self.inlog._own_xray(kind, self.ALL, self.BUSY_UDP), kind)

    def test_busy_tcp_443_is_vless_only(self):
        tcp = self.BUSY_UDP.replace("listen udp", "listen tcp")
        self.xray(tcp)
        v = self.inlog.fail_info(self.runner, "vless", self.DOWN, self.ALL)
        self.assertEqual((v["why"], v["port"], v["proto"]), ("busy", 443, "tcp"))
        self.assertNotEqual(self.inlog.fail_info(self.runner, "hy2", self.DOWN, self.ALL)["why"], "busy")

    def test_a_missing_certificate_says_apply_settings(self):
        """REV-3373 З-3: не «выпустите заново» (``gw hy2 rekey`` меняет pin) — «Применить настройки», по режиму."""
        self.xray(self.NO_CERT)
        f = self.inlog.fail_info(self.runner, "hy2", self.DOWN, self.ALL)
        self.assertEqual((f["why"], f["line"]), ("cert", self.NO_CERT))
        self.assertEqual(f["human"], i18n.tr("gw_inlog.human.cert_self"))
        dom = json.loads(json.dumps(self.ALL))
        dom["gateway"]["inbounds"]["hy2"]["cert_mode"] = "domain"
        f = self.inlog.fail_info(self.runner, "hy2", self.DOWN, dom)
        self.assertEqual((f["why"], f["human"]), ("cert", i18n.tr("gw_inlog.human.cert_domain")))
        for lang in ("ru", "en"):
            for key in ("cert_self", "cert_domain"):
                words = i18n.tr("gw_inlog.human." + key, lang)
                self.assertNotRegex(words, r"(?i)выпуст|issue|rekey")
        self.assertEqual(self.inlog.human("hy2", "Failed to start: main: failed to load config files > x: unknown option"),
                         i18n.tr("gw_inlog.human.config"))              # a config error is still one


class Rev3373BusyPortTest(InlogTest):
    """REV-3373 З-1: Н-8.1 искал номер только в звене «listen»; у служб не на xray (L2TP/IPsec) его нет — номер снова
    ищется по всей строке. Строка xray со стенда — по-прежнему по последнему звену «listen»."""
    DOWN = {"installed": True, "enabled": True, "running": False}

    def test_l2tp_takes_the_port_from_the_line(self):
        st = {}
        self.assertEqual(self.inlog._busy_port("l2tp", st, "charon: unable to bind socket: Address already in use "
                                                           "(port 4500)"), (4500, "udp"))
        self.assertEqual(self.inlog._busy_port("l2tp", st, "xl2tpd: bind: Address already in use 0.0.0.0:500"),
                         (500, "udp"))
        self.assertEqual(self.inlog._busy_port("l2tp", st, "Address already in use"), (1701, "udp"))   # no number
        self.say("charon: unable to bind socket: Address already in use (port 4500)")
        f = self.inlog.fail_info(self.runner, "l2tp", self.DOWN, st)
        self.assertEqual((f["why"], f["port"], f["proto"]), ("busy", 4500, "udp"))
        self.assertIn("4500/udp", f["human"])

    def test_the_stand_xray_line_keeps_its_last_listen_link(self):
        st = Stand3372CardsTest.ALL
        line = Stand3372CardsTest.BUSY_UDP
        self.assertEqual(self.inlog._busy_port("hy2", st, line), (443, "udp"))
        self.assertEqual(self.inlog._busy_port("vless", st, line.replace("listen udp", "listen tcp")), (443, "tcp"))


class Hy2CertCardTest(InlogTest):
    """Стенд 3.3.7.2 Н-8.2: xray идёт с негодной парой Hysteria2 (только предупреждение «ignoring invalid X509 key
    pair») — карточка не «работает»: файл есть, читается, ключ подходит, срок не вышел."""
    HY = {"gateway": {"enabled": True, "inbounds": {"hy2": {"enabled": True, "port": 443}}}}
    RUN = {"installed": True, "enabled": True, "running": True}

    def setUp(self):
        super().setUp()
        from kaskad.gw.inbounds import hy2
        self.crt, self.key = hy2.self_files()               # cert_mode «self» by default
        os.makedirs(os.path.dirname(self.crt), exist_ok=True)
        self.put(self.crt, _fix("panel", "node.crt"))
        self.put(self.key, _fix("panel", "node.key"))

    def put(self, path, text):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def fail(self):
        return self.inlog.fail_info(self.runner, "hy2", self.RUN, self.HY)

    def test_a_good_pair_works(self):
        self.assertEqual(self.inlog.cert_bad("hy2", self.HY), "")
        self.assertIsNone(self.fail())

    def bad(self, why):
        f = self.fail()
        self.assertEqual((f["why"], f["cert"], f["port"], f["proto"]), ("cert", why, 443, "udp"))
        self.assertEqual(f["human"], i18n.tr("gw_inlog.human.cert_self"))       # REV-3373 З-3: by the mode
        self.assertEqual(f["line"], "")

    def test_a_broken_pem(self):
        self.put(self.crt, "not a certificate\n")            # the stand's test: xray starts, only warns
        self.bad("unreadable")

    def test_no_file(self):
        os.unlink(self.crt)
        self.bad("missing")
        self.put(self.crt, _fix("panel", "node.crt"))
        os.unlink(self.key)
        self.bad("missing")

    def test_a_key_of_another_certificate(self):
        self.put(self.key, _fix("site", "other.key"))
        self.bad("key")

    def test_expired(self):
        from kaskad.site import certinfo
        with mock.patch.object(certinfo, "_now", lambda: 7258118400.0):     # 2200-01-01
            self.bad("expired")

    def test_the_domain_pair_when_the_inbound_takes_it(self):
        from kaskad.gw.inbounds import hy2
        st = {"gateway": {"enabled": True, "inbounds": {"hy2": {"enabled": True, "port": 443, "cert_mode": "domain"}}}}
        self.assertEqual(self.inlog.cert_bad("hy2", st), "missing")         # no copy of the site's pair: no good
        crt, key = hy2.domain_files()
        os.makedirs(os.path.dirname(crt), exist_ok=True)
        self.put(crt, _fix("panel", "node.crt"))
        self.put(key, _fix("panel", "node.key"))
        self.assertEqual(self.inlog.cert_bad("hy2", st), "")

    def test_other_kinds_are_not_looked_at(self):
        self.assertEqual(self.inlog.cert_bad("vless", self.HY), "")
        self.assertEqual(self.inlog.cert_bad("ocserv", self.HY), "")
