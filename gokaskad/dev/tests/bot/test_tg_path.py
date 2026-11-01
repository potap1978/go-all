"""SPEC-tg-proxy Р3, Р4 (3.3.7.11): the daemon goes straight while the proxy of its group does not carry and comes back
to it; «Как бот ходит в Telegram» — one answer (bot/proxy.path) for the panel (GET /api/bot), the CLI (``bot proxy``,
``bot status --json``) and MCP: group / fallback / direct (chosen, no keys, no pool) / none / off, RU and EN, without
the token, the proxy's address or the keys."""

import contextlib
import io
import json
import os
from unittest import mock

from kaskad import cli, state
from kaskad.bot import app as bapp
from kaskad.bot import config as bcfg
from kaskad.bot import proxy as bproxy
from kaskad.gw import policy
from kaskad.i18n import use_lang
from tests.bot.botcase import BotCase
from tests.bot.fake_socks import FakeSocks
from tests.bot.fake_tg import TOKEN
from tests.fakes_gw import write_pool
from tests.gw.test_tg_proxy import LINES, st_with
from tests.netports import free_port


class FallbackTest(BotCase):
    def proxied_app(self, reply=1, base=None):
        self.px = FakeSocks(reply=reply).start()                 # reply 1: «general failure» — the proxy carries nothing
        self.addCleanup(self.px.stop)
        self.write_config(BOT_PROXY=self.px.url)
        kw = {"api_base": base} if base else {}
        app = self.make_app(**kw)
        self.assertEqual((app.via, bool(app.api.proxy)), ("proxy", True))
        return app

    def fails(self, app, times, step=40.0):
        for _ in range(times):
            self.assertIsNone(app.poll_once())
            self.now += step

    def daemon(self):
        return json.loads(self.read("/run/kaskad/bot.json"))

    def test_straight_while_the_proxy_does_not_carry_then_back(self):
        app = self.proxied_app()
        self.fails(app, 3)                                            # 3 failures, 80 s: not yet
        self.assertEqual(app.via, "proxy")
        self.fails(app, 1)                                            # the 4th at 120 s: getMe straight answers
        self.assertEqual(app.via, "fallback")
        self.assertIsNone(app.api.proxy)
        self.assertEqual(self.daemon()["via"], "fallback")
        self.assertIn("Bot: The Telegram API proxy does not answer", self.read("/var/log/kaskad.log"))
        self.assertEqual(app.poll_once(), 0)                         # polls straight
        self.now += 100
        self.assertFalse(app._maybe_back())                          # the proxy again not before 5 min
        self.now += 250
        self.assertFalse(app._maybe_back())                          # tried: still dead — stays straight
        self.assertEqual(app.via, "fallback")
        self.px.reply = 0                                             # the exits of the group answer again
        self.now += 301
        self.assertTrue(app._maybe_back())
        self.assertEqual((app.via, bool(app.api.proxy)), ("proxy", True))
        self.assertEqual(app.poll_once(), 0)
        self.assertEqual(self.daemon()["via"], "proxy")
        self.assertIn("Bot: The Telegram API proxy answers again", self.read("/var/log/kaskad.log"))
        self.assertNotIn(TOKEN, self.read("/run/kaskad/bot.json"))
        self.assertNotIn(TOKEN, self.read("/var/log/kaskad.log"))

    def test_no_switch_when_straight_fails_too(self):
        dead = "http://127.0.0.1:%d" % free_port()
        app = self.proxied_app(base=dead)
        self.fails(app, 4)
        self.assertEqual(app.via, "proxy")                            # straight did not answer: the proxy stays
        tried = app._direct_at
        self.assertIsNotNone(tried)
        self.fails(app, 2)
        self.assertEqual(app._direct_at, tried)                       # straight again only after FALLBACK_RETRY

    def test_chosen_direct_never_falls_back(self):
        app = self.make_app(api_base="http://127.0.0.1:%d" % free_port())
        self.assertEqual(app.via, "direct")
        self.fails(app, 5)
        self.assertEqual((app.via, app._direct_at), ("direct", None))

    def test_a_new_config_starts_over(self):
        app = self.proxied_app()
        self.fails(app, 4)
        self.assertEqual(app.via, "fallback")
        self.write_config(BOT_PROXY="")                                # «Напрямую» chosen: install.sh empties it
        bcfg_sig = app.cfg.signature
        os.utime(self.path("/etc/kaskad/config"), (self.now + 50, self.now + 50))
        app.refresh_config()
        self.assertNotEqual(app.cfg.signature, bcfg_sig)
        self.assertEqual((app.via, app.fallback_since), ("direct", None))


class PathTest(BotCase):
    def setUp(self):
        super().setUp()
        write_pool(LINES)
        from kaskad.gw import exits
        ids = self.ids = [eid for eid, _ in exits.line_ids()]
        self.groups = [policy.default_group("main", exits=ids[:2]),
                       policy.default_group("g-0000a1", name="Дом", exits=ids[2:])]

    def st(self, tg="main"):
        return st_with(self.groups, tg)

    def cfg(self, proxy="socks5h://127.0.0.1:10808", token=TOKEN):
        return bcfg.Config({"BOT_TOKEN": token, "BOT_PROXY": proxy})

    def test_states_and_words(self):
        now = 1000.0
        live = {"running": True, "written": now - 10, "last_poll": now - 5, "last_error": "", "via": "proxy",
                "proxy": True}
        p = bproxy.path(self.cfg(), live, now, self.st("g-0000a1"))
        self.assertEqual((p["state"], p["group"], p["label"], p["choice"]), ("group", "g-0000a1", "Дом", "g-0000a1"))
        self.assertEqual(p["text"], "через выходы группы «Дом»")
        p = bproxy.path(self.cfg(), dict(live, via="fallback"), now, self.st())
        self.assertEqual((p["state"], p["via"]), ("fallback", "direct"))
        self.assertIn("выходы группы «Основная» не отвечают", p["text"])
        # REV-3310 П2: обход напрямую — только у живого и свежего демона; упавший в обходе бот не показывается «в обходе»
        for stale in (dict(live, via="fallback", written=now - 10 * bproxy.DAEMON_STALE), dict(live, via="fallback", running=False)):
            self.assertNotEqual(bproxy.path(self.cfg(), stale, now, self.st())["state"], "fallback", stale)
        p = bproxy.path(self.cfg(proxy=""), live, now, self.st("direct"))
        self.assertEqual((p["state"], p["why"], p["choice"]), ("direct", "chosen", "direct"))
        self.assertEqual(p["text"], "напрямую — так выбрано в панели")
        p = bproxy.path(self.cfg(proxy=""), live, now, self.st())       # keys in the group, no proxy built
        self.assertEqual((p["state"], p["why"]), ("direct", "not_built"))
        self.assertIn("не собран", p["text"])
        off = dict((eid, {"enabled": False}) for eid in self.ids)
        p = bproxy.path(self.cfg(proxy=""), live, now, st_with(self.groups, "main", off))
        self.assertEqual((p["state"], p["why"]), ("direct", "no_keys"))
        self.assertEqual(p["text"], "напрямую: в группе «Основная» нет включённых ключей")
        p = bproxy.path(self.cfg(), live, now, self.st("direct"))     # chosen, install.sh has not rebuilt yet
        self.assertEqual((p["state"], p["why"], p["via"]), ("direct", "pending", "group"))
        self.assertEqual(p["text"], "выбрано «Напрямую» — бот переключится в течение минуты")
        os.unlink(self.path("/etc/kaskad/egress/upstream.txt"))
        self.assertEqual(bproxy.path(self.cfg(proxy=""), live, now, self.st())["why"], "no_pool")
        write_pool(LINES)
        dead = dict(live, last_poll=now - 300, last_error="getUpdates: timed out")
        p = bproxy.path(self.cfg(), dead, now, self.st())
        self.assertEqual(p["state"], "none")
        self.assertEqual(p["text"], "нет связи с Telegram (через выходы группы «Основная» и напрямую)")
        self.assertEqual(bproxy.path(self.cfg(proxy=""), dead, now, self.st("direct"))["text"],
                         "нет связи с Telegram напрямую")
        stale = dict(dead, written=now - 1000)                        # an old file of a stopped daemon says nothing
        self.assertEqual(bproxy.path(self.cfg(), stale, now, self.st())["state"], "group")
        self.assertEqual(bproxy.path(self.cfg(token=""), dead, now, self.st())["state"], "off")
        with use_lang("en"):
            self.assertEqual(bproxy.path(self.cfg(), live, now, self.st("g-0000a1"))["text"],
                             "through the exits of the group «Дом»")
            self.assertEqual(bproxy.path(self.cfg(proxy=""), live, now, self.st("direct"))["text"],
                             "directly — chosen in the panel")
        for v in (bproxy.path(self.cfg(), dead, now, self.st()), ):
            blob = json.dumps(v, ensure_ascii=False)
            self.assertNotIn("10808", blob)
            self.assertNotIn(TOKEN.split(":")[1], blob)
            self.assertNotIn("example.com", blob)                     # no addresses of exits

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, {"KASKAD_TG_API": self.tg.base}):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_cli(self):
        st = state.load()
        st.setdefault("gateway", {}).update(self.st("direct")["gateway"])
        state.save(st)
        self.write_config(BOT_PROXY="")
        rc, out, err = self.run_cli("bot", "proxy")
        self.assertEqual((rc, out.strip()), (0, "Бот ходит в Telegram: напрямую — так выбрано в панели"), err)
        rc, out, err = self.run_cli("bot", "proxy", "--json")
        doc = json.loads(out)
        self.assertEqual((doc["ok"], doc["state"], doc["why"]), (True, "direct", "chosen"))
        rc, out, err = self.run_cli("bot", "status", "--json")
        self.assertEqual(json.loads(out)["path"]["state"], "direct")
        rc, out, err = self.run_cli("bot", "status")
        self.assertIn("Бот ходит в Telegram: напрямую — так выбрано в панели", out)


class AppStatusTest(BotCase):
    def test_status_file_has_via_and_written(self):
        app = self.make_app()
        app.write_status(force=True)
        doc = json.loads(self.read("/run/kaskad/bot.json"))
        self.assertEqual((doc["via"], doc["written"], doc["fallback_since"]), ("direct", self.now, None))
        self.assertEqual((bapp.FALLBACK_FAILS, bapp.FALLBACK_AFTER, bapp.FALLBACK_RETRY), (3, 90.0, 300.0))
