"""Pieces of the bot: /etc/kaskad/config (parsed, written like save_config_val), aliases, HTML escaping and
cutting, «Система» from a fake /proc, pagination, the handler tables against the keyboards, the menu styles,
``kaskadctl bot status|test|send-test``."""

import contextlib
import io
import json
import os
from unittest import mock

from kaskad import cli
from kaskad.bot import aliases, menus, registry, sysinfo
from kaskad.bot import config as bcfg
from kaskad.bot.lang import use_lang
from kaskad.bot.telegram import fit, h
from tests.bot.botcase import BotCase
from tests.bot.fake_tg import OWNER, TOKEN
from tests.helpers import RootCase


class ConfigTest(RootCase):
    def test_parse_like_bash(self):
        text = ('BOT_TOKEN="123:abc"\n# BOT_CHAT_ID="1"\nBOT_CHAT_ID=\'42\'\nMENU_STYLE=reply # comment\n'
                'BOT_PROXY="socks5h://127.0.0.1:10808"\nX="a\\"b\\\\c\\$d\\`e\\n"\nexport BOT_IMPL=python\n'
                'BOT_ADMINS="1, 2;x 3"\nBOT_TOKEN="123:def"\n')
        v = bcfg.parse(text)
        self.assertEqual(v["X"], 'a"b\\c$d`e\\n')
        c = bcfg.Config(v)
        self.assertEqual((c.token, c.chat_id, c.menu_style, c.proxy, c.impl), ("123:def", "42", "reply",
                                                                               "socks5h://127.0.0.1:10808", "python"))
        self.assertEqual(c.admins, ["1", "2", "3"])
        self.assertTrue(c.allowed(42) and c.allowed("2") and not c.allowed(5))
        self.assertFalse(bcfg.Config({"BOT_CHAT_ID": "abc"}).bound)

    def test_save_value_like_save_config_val(self):
        self.write("/etc/kaskad/config", 'BOT_TOKEN="t"\n  MENU_STYLE="inline"\nGITHUB_PAT=""\n')
        bcfg.save_value("MENU_STYLE", "reply")
        bcfg.save_value("X", 'a"b$c`d\\e')
        text = self.read("/etc/kaskad/config")
        self.assertEqual(text, 'BOT_TOKEN="t"\nGITHUB_PAT=""\nMENU_STYLE="reply"\nX="a\\"b\\$c\\`d\\\\e"\n')
        self.assertEqual(bcfg.load().values["X"], 'a"b$c`d\\e')
        self.assertEqual(os.stat(self.path("/etc/kaskad/config")).st_mode & 0o777, 0o600)


class AliasesTest(RootCase):
    def test_read_write(self):
        self.write("/etc/kaskad/aliases", "1.2.3.4=Старое\n1.2.3.4=второе\n5.6.7.8=a|b|c|d\nмусор\n")
        self.assertEqual(aliases.get("1.2.3.4")["name"], "Старое")             # first line wins (head -1)
        aliases.update("5.6.7.8", note="новая | заметка\\\x07")
        self.assertEqual(aliases.get("5.6.7.8"), {"name": "a", "note": "новая   заметка", "country": "c", "isp": "d"})
        aliases.update("1.2.3.4", name="Новое")
        text = self.read("/etc/kaskad/aliases")
        self.assertEqual(text.count("1.2.3.4="), 1)
        self.assertIn("мусор\n", text)
        with self.assertRaises(ValueError):
            aliases.update("../../etc/passwd", name="x")


class HtmlTest(RootCase):
    def test_escape_and_fit(self):
        self.assertEqual(h('<b>"x" & y</b>'), "&lt;b&gt;&quot;x&quot; &amp; y&lt;/b&gt;")
        self.assertEqual(h(None), "")
        long = "<b>head</b>\n" + "\n".join("<code>%d</code> строка &amp; ещё" % i for i in range(400))
        cut = fit("<pre>" + long)
        self.assertLessEqual(len(cut), 4096)
        self.assertTrue(cut.endswith("</pre>\n…"))
        self.assertEqual(fit("short"), "short")


class SysinfoTest(RootCase):
    def proc(self, stat, utime):
        self.write("/proc/stat", stat)
        self.write("/proc/123/stat", "123 (python3 (x)) S 1 1 1 0 -1 4194560 0 0 0 0 %d 0 0 0 20 0 1 0 100 1000 2560 "
                                     "184467 0 0 0\n" % utime)

    def test_real_sample_and_top(self):
        self.proc("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 1 1 1\n", 10)
        self.write("/proc/loadavg", "0.10 0.20 0.30 1/100 123\n")
        self.write("/proc/uptime", "93780.5 1000.0\n")
        self.write("/proc/meminfo", "MemTotal:        2048000 kB\nMemFree:          100000 kB\n"
                                    "MemAvailable:    1024000 kB\nSwapTotal:             0 kB\nSwapFree: 0 kB\n")

        def sleep(s):
            self.proc("cpu  150 0 150 900 0 0 0 0 0 0\n", 35)          # +25 jiffies of pid 123 in the sample

        class V(object):
            f_blocks, f_frsize, f_bavail, f_bfree = 1000, 4096, 600, 700

        s = sysinfo.stats(sleep=sleep, statvfs=lambda p: V(), clk_tck=100, page_size=4096)
        self.assertAlmostEqual(s["cpu"], 50.0)
        self.assertEqual(s["top"][0][2:], (123, "python3 (x)"))
        self.assertAlmostEqual(s["top"][0][0], 50.0, places=0)
        text = sysinfo.render(s)
        self.assertIn("<b>Uptime:</b> 1 д 2 ч 3 мин", text)
        # REV-ns-a6 П-3: числа экрана — по языку («50,0 %» / «50.0%»), как в каталоге
        self.assertIn("ядер | 50,0%", text)
        self.assertIn("<b>Load:</b> 0,10 0,20 0,30", text)
        self.assertIn("<b>RAM:</b> 1000/2000MB (50,0%)", text)
        self.assertIn("<b>Swap:</b> нет", text)
        self.assertIn("<b>Disk /:</b> 1,2 МБ / 3,9 МБ (33%)", text)
        self.assertIn("<b>Топ CPU</b> (за 0,5 с):", text)
        self.assertIn("<pre>PID     CPU%  MEM%  CMD\n123", text)
        with use_lang("en"):
            text = sysinfo.render(s)
        self.assertIn("cores | 50.0%", text)
        self.assertIn("<b>Load:</b> 0.10 0.20 0.30", text)
        self.assertIn("<b>RAM:</b> 1000/2000MB (50.0%)", text)
        self.assertIn("<b>Disk /:</b> 1.2 MB / 3.9 MB (33%)", text)
        self.assertIn("(over 0.5 s):", text)
        self.assertNotIn("%%", text)

    def test_without_proc(self):
        text = sysinfo.render(sysinfo.stats(sleep=lambda s: None, statvfs=lambda p: (_ for _ in ()).throw(OSError())))
        self.assertIn("<b>CPU:</b>", text)
        self.assertIn("| —", text)


class SysinfoHealthTest(RootCase):
    """SPEC-node-score §3.9 п. 4 (A6): первой строкой — светофор и ≤ 3 находок по hostprobe.read_health; без истории —
    пометка; steal — своя строка, не часть «занятого» процессора; прежние строки целы."""

    def stats(self, health):
        self.write("/proc/stat", "cpu  100 0 100 790 0 0 0 10 0 0\n")
        self.write("/proc/meminfo", "MemTotal: 2048000 kB\nMemAvailable: 1024000 kB\n")

        def sleep(s):
            self.write("/proc/stat", "cpu  140 0 140 900 0 0 0 20 0 0\n")     # +200: busy 80, steal 10 (5 %)
        return sysinfo.stats(sleep=sleep, statvfs=lambda p: (_ for _ in ()).throw(OSError()), clk_tck=100,
                             page_size=4096, health=health)

    def test_light_findings_and_steal(self):
        f = [{"level": "crit", "text": "Диск / почти заполнен: осталось 1 ГБ (99% занято)"},
             {"level": "warn", "text": "Память <на исходе>"}, {"level": "warn", "text": "третья"},
             {"level": "warn", "text": "четвёртая"}]
        s = self.stats(lambda: ({"level": "crit", "top": f, "instant": False}, "collector"))
        self.assertAlmostEqual(s["cpu"], 40.0)                           # steal не «занят»
        self.assertAlmostEqual(s["steal"], 5.0)
        lines = sysinfo.render(s).split("\n")
        self.assertEqual(lines[2:7], ["🔴 <b>Требует внимания</b>",
                                      "• Диск / почти заполнен: осталось 1 ГБ (99% занято)",
                                      "• Память &lt;на исходе&gt;", "• третья", ""])
        self.assertNotIn("четвёртая", "\n".join(lines))
        self.assertNotIn("без истории", "\n".join(lines).lower())
        self.assertIn("<b>Соседи (steal):</b> 5,0%", lines)
        self.assertIn("ядер | 40,0%", "\n".join(lines))                 # прежние строки целы, числа — как у steal
        self.assertIn("<b>RAM:</b> 1000/2000MB (50,0%)", lines)
        with use_lang("en"):
            text = sysinfo.render(s)
        self.assertIn("🔴 <b>Needs attention</b>", text)
        self.assertIn("<b>Other VMs (steal):</b> 5.0%", text)
        self.assertIn("cores | 40.0%", text)

    def test_the_words_of_the_light_are_the_panels(self):
        # REV-ns-a6 П-2: слова светофора и пометки «без истории» — одни с панелью (gw_host.light.*), своих нет
        from kaskad import i18n
        for lang in ("ru", "en"):
            cat = i18n.catalog(lang)
            self.assertEqual([k for k in cat if k.startswith("bot.sys.health") or k == "bot.sys.no_history"], [])
            with use_lang(lang):
                for lvl in sysinfo.HEALTH_LEVELS:
                    got = sysinfo.health_lines(({"level": lvl, "top": [], "instant": True}, "bot"))
                    self.assertEqual(got, ["%s <b>%s</b>" % (sysinfo.HEALTH_ICONS[lvl], cat["gw_host.light." + lvl]),
                                           "<i>%s</i>" % cat["gw_host.light.instant"], ""], (lang, lvl))

    def test_without_live_a_mark_and_no_health_no_line(self):
        s = self.stats(sysinfo.health)                                   # нет live.json и файла бота: свой снимок
        self.assertIsNotNone(s["health"])
        self.assertEqual(s["health"][1], "panel")
        text = sysinfo.render(s)
        self.assertRegex(text.split("\n")[2], r"^(🟢|🟡|🔴|⚪) <b>[^<]+</b>$")
        self.assertIn("<i>Без истории: историю и пробы сети собирает шлюз — сейчас его данных нет</i>", text)
        # REV-ns-a7 П-3: нейтрально — без «при включённом шлюзе», когда шлюз включён, а сборщик молчит
        from kaskad import i18n
        for lang in ("ru", "en"):
            words = i18n.catalog(lang)["gw_host.light.instant"]
            self.assertNotIn("включ", words.lower())
            self.assertNotIn(" on", words)
        ok = sysinfo.render(self.stats(lambda: ({"level": "ok", "top": [], "instant": True}, "bot")))
        self.assertIn("🟢 <b>Всё в норме</b>\n<i>Без истории", ok)
        none = sysinfo.render(self.stats(lambda: None)).split("\n")
        self.assertTrue(none[2].startswith("<b>Uptime:</b>"))
        self.assertEqual(sysinfo.cpu_counters("cpu  1 2 3 4 5 6 7 8\n"), (1 + 2 + 3 + 6 + 7, 36, 8))

    def test_a_broken_health_never_breaks_the_screen(self):
        from unittest import mock
        from kaskad.gw import hostprobe
        with mock.patch.object(hostprobe, "read_health", mock.Mock(side_effect=RuntimeError("boom"))):
            self.assertIsNone(sysinfo.health())
        self.assertEqual(sysinfo.health_lines(({"level": "?", "top": "x"}, "bot")),
                         ["⚪ <b>Сервер ещё набирает данные</b>", ""])


class PaginationTest(RootCase):
    def test_pages_by_rows_and_chars(self):
        pg = menus.paginate(range(65), 0)
        self.assertEqual((list(pg.items)[-1], pg.next, pg.prev, pg.number, pg.total), (29, 30, None, 1, 3))
        pg = menus.paginate(range(65), 61)
        self.assertEqual((list(pg.items), pg.prev, pg.next), ([60, 61, 62, 63, 64], 30, None))
        big = ["x" * 1000] * 10
        pg = menus.paginate(big, 0, size=len)
        self.assertEqual(len(pg.items), 3)
        self.assertEqual(menus.split_page("lr~30", "lr"), 30)
        self.assertEqual(menus.split_page("lr", "lr"), 0)
        self.assertEqual(menus.split_page("lr~x", "lr"), 0)


class TablesTest(RootCase):
    def test_every_button_of_the_menus_has_a_handler(self):
        registry.load()
        for row in menus.main_inline():
            for _, data in row:
                self.assertIsNotNone(registry.find_callback(data)[0], data)
        texts = [t for row in menus.reply_rows() for t in row if t != "/inline"]
        self.assertEqual(sorted(set(texts) - set(registry.REPLIES)), [])
        self.assertEqual(len(texts), 14)        # the 11 of 2.x + «🛡 Шлюз», «🌐 Домен» and «🌍 Язык / Language» (3.3.2)
        for cmd in ("/start", "/menu", "/id", "/help", "/inline", "/lang"):
            self.assertIn(cmd, registry.COMMANDS)


class MenuStyleTest(BotCase):
    def test_reply_keyboard_and_back(self):
        self.make_app()
        self.press("sw_reply")
        self.assertIn('MENU_STYLE="reply"', self.read("/etc/kaskad/config"))
        menu = self.last()
        self.assertEqual(menu.method, "sendMessage")
        self.assertEqual(menu.markup()["keyboard"][0], [{"text": "🔀 AWG/WG"}, {"text": "🔀 VLESS"}])
        self.assertEqual(menu.markup()["keyboard"][-1], [{"text": "/inline"}])
        self.say("💻 Система")
        self.assertIn("Системная информация", self.last_text())
        self.say("🏢 Хостинг")
        self.assertEqual(self.last_text(), menus.promo())
        self.say("/inline")
        calls = self.tg.sent("sendMessage")
        self.assertEqual(calls[-2].text, "Переключено на inline-кнопки.")
        self.assertEqual(calls[-2].markup(), {"remove_keyboard": True})
        self.assertIn("inline_keyboard", calls[-1].markup())
        self.assertIn('MENU_STYLE="inline"', self.read("/etc/kaskad/config"))
        self.say("/help")
        self.assertIn("/inline — inline-кнопки", self.last_text())
        self.say("/start@kaskad_test_bot")
        self.assertIn("Выберите действие", self.last_text())
        self.say("/nonsense")
        self.assertEqual(self.last_text(), "/start или /menu")

    def test_promo_text_is_the_bash_one(self):
        self.assertIn("👉 https://vk.cc/ct29NQ", menus.promo())
        self.assertIn("<code>antenka12</code> — +5% (12мес)", menus.promo())
        self.assertIn("<b>🇧🇾 Беларусь</b>\n👉 https://vk.cc/cUxAhj\n<code>OFF60</code> — 60% скидка", menus.promo())


class CliTest(BotCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(os.environ, {"KASKAD_TG_API": self.tg.base}):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_status_never_prints_the_token(self):
        self.write("/run/kaskad/bot.json", json.dumps({"username": "kaskad_test_bot", "last_poll": self.now,
                                                       "last_error": "x"}))
        rc, out, err = self.run_cli("bot", "status", "--json")
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual((doc["token"], doc["bound"], doc["chat_id"]), (True, True, str(OWNER)))
        rc, out2, err = self.run_cli("bot", "status")
        for text in (out, out2):
            self.assertNotIn(TOKEN.split(":")[1], text)
        self.assertIn("Имя бота: @kaskad_test_bot", out2)

    def test_test_and_send_test(self):
        rc, out, err = self.run_cli("bot", "test")
        self.assertEqual((rc, out.strip()), (0, "Telegram доступен: бот @kaskad_test_bot (id 123456789)."))
        rc, out, err = self.run_cli("bot", "send-test", "--json")
        self.assertEqual((rc, json.loads(out)["chat_id"]), (0, str(OWNER)))
        self.assertIn("тестовое сообщение", self.tg.texts(OWNER)[-1])
        self.tg.fail("getMe", 401, "Unauthorized")
        rc, out, err = self.run_cli("bot", "test")
        self.assertEqual(rc, 1)
        self.assertIn("не принял токен", err)
        self.write_config(BOT_PROXY="socks4://127.0.0.1:1")
        rc, out, err = self.run_cli("bot", "test")
        self.assertEqual(rc, 1)
        self.assertIn("не поддерживается", err)
        self.write_config(BOT_TOKEN="")
        rc, out, err = self.run_cli("bot", "send-test")
        self.assertEqual(rc, 1)
        self.assertIn("нет BOT_TOKEN", err)
