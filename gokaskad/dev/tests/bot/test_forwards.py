"""Forwards in the bot on the fake node (engine nft): the AWG / VLESS / MTProto / Custom dialogs with the server
check (GeoIP, ICMP, TCP, nothing answers), names written only after the forward exists, conflicts and the
legacy engine refused, the list with pagination, delete by key with confirmation, «Сбросить всё», HTML
escaping of names, UFW of 2.x forwards."""

import os

from kaskad import state
from kaskad.bot import aliases, data, monitors
from tests.bot.botcase import BotCase
from tests.bot.fake_tg import OWNER
from tests.fakes_nft import DEFAULT_BINARIES, F443, F51820, SS_SSHD, World, save_state

DIALOG = "/etc/kaskad/bot_state/%d.json" % OWNER


class FwdCase(BotCase):
    BINARIES = DEFAULT_BINARIES
    SS = ""

    def make_runner(self):
        self.w = World(binaries=self.BINARIES, ss=self.SS)
        self.w.runner.on(["ping"], self.ping_handler)
        return self.w.runner

    def fwd(self, proto, port):
        return state.find_forward(state.load(), proto, port)

    def run_dialog(self, start, *answers):
        if isinstance(start, (list, tuple)):
            for p in start:
                self.press(p)
        else:
            self.press(start)
        for a in answers:
            self.say(a)


class DialogTest(FwdCase):
    def setUp(self):
        super(DialogTest, self).setUp()
        save_state()
        self.make_app()

    def test_awg_dialog_with_a_server_check(self):
        self.pings["198.51.100.7"] = "12.3"
        self.tg.geo["198.51.100.7"] = {"country": "Netherlands", "city": "Amsterdam", "isp": "Example BV",
                                       "org": "Example"}
        self.press("a_u")
        self.assertEqual(self.last_text(), "🔀 <b>AmneziaWG (UDP)</b>\n\nВведите IP:")
        self.say("198.51.100.7")
        self.assertEqual(self.last_text(), "IP: <code>198.51.100.7</code> ✅\n\nВведите порт:")
        self.say("51820")
        probe = self.last()
        self.assertEqual(probe.method, "editMessageText")              # the «Проверяю…» message, edited
        self.assertIn("🔍 Проверяю <code>198.51.100.7:51820</code>", self.tg.texts()[-2])
        self.assertIn("🌍 <b>GeoIP:</b> Netherlands, Amsterdam | Example BV", probe.text)
        self.assertIn("#1: 12.3ms [ICMP]", probe.text)
        self.assertIn("<b>Среднее: 12.3ms</b> | Потеряно: 0/3", probe.text)
        self.assertIn("Введите имя", probe.text)
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/aliases")))     # nothing before the forward
        self.say("Амстердам <b>&")
        self.assertIn("Примечание", self.last_text())
        self.say("основной | резерв")
        f = self.fwd("udp", 51820)
        self.assertEqual((f["to"], f["to_port"], f["name"]), ("198.51.100.7", 51820, "AmneziaWG"))
        self.assertEqual(self.w.nft.forwards()[("udp", 51820)], ("198.51.100.7", 51820))
        self.assertEqual(self.read("/etc/kaskad/aliases"),
                         "198.51.100.7=Амстердам <b>&|основной   резерв|Netherlands|Example BV\n")
        text = self.last_text()
        self.assertIn("✅ <b>AmneziaWG</b>", text)
        self.assertIn("<code>udp 203.0.113.10:51820 → 198.51.100.7:51820</code>", text)
        self.assertIn("<b>Амстердам &lt;b&gt;&amp;</b> <code>198.51.100.7</code> Netherlands | Example BV", text)
        self.assertIn("<i>основной   резерв</i>", text)
        self.assertFalse(os.path.exists(self.path(DIALOG)))
        self.assertIn("ADD rule: udp :51820 -> 198.51.100.7:51820 (AmneziaWG)", self.read("/var/log/kaskad.log"))

    def test_vless_when_nothing_answers(self):
        self.run_dialog("a_t", "198.51.100.8", "443")
        text = self.last_text()
        self.assertNotIn("GeoIP", text)
        self.assertEqual(text.count("timeout"), 3)
        self.assertIn("<b>Сервер не ответил</b>", text)
        self.assertIn("icmp_echo_ignore_all=0", text)
        self.say("-")
        self.say("-")
        self.assertEqual(self.fwd("tcp", 443)["name"], "VLESS")
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/aliases")))     # no name, no geo: nothing to keep
        self.assertIn("✅ <b>VLESS</b>", self.last_text())

    def test_mtproto_answers_over_tcp(self):
        self.tcp[("198.51.100.9", 8443)] = 45.0
        self.run_dialog("a_mt", "198.51.100.9", "8443")
        self.assertIn("#1: 45.0ms [TCP:8443]", self.last_text())
        self.say("Рига")
        self.say("-")
        self.assertEqual(self.fwd("tcp", 8443)["name"], "MTProto")
        self.assertEqual(aliases.get("198.51.100.9")["name"], "Рига")

    def test_udp_is_never_probed_over_tcp(self):
        self.tcp[("198.51.100.7", 51820)] = 5.0
        self.run_dialog("a_u", "198.51.100.7", "51820")
        self.assertEqual(self.last_text().count("timeout"), 3)

    def test_custom(self):
        self.press("a_c")
        self.assertEqual(self.buttons()[:2], [("TCP", "a_cp_tcp"), ("UDP", "a_cp_udp")])
        self.press("a_cp_tcp")
        self.assertEqual(self.last_text(), "🛠 <b>Custom (tcp)</b>\n\nВведите IP:")
        self.say("198.51.100.10")
        self.assertIn("<b>ВХОДЯЩИЙ</b> порт", self.last_text())
        self.say("2222")
        self.assertIn("<b>ИСХОДЯЩИЙ</b> порт", self.last_text())
        self.say("22")
        self.assertIn("<code>198.51.100.10</code> (вход:2222 → выход:22)", self.last_text())
        self.say("Офис")
        self.say("-")
        f = self.fwd("tcp", 2222)
        self.assertEqual((f["to"], f["to_port"], f["name"]), ("198.51.100.10", 22, "Custom"))
        self.assertIn("✅ <b>Custom</b>\n<code>tcp 203.0.113.10:2222 → 198.51.100.10:22</code>", self.last_text())

    def test_bad_input_asks_again(self):
        self.press("a_t")
        self.say("999.1.1.1")
        self.assertEqual(self.last_text(), "❌ Некорректный IP. Ещё раз:")
        self.say("01.2.3.4")
        self.assertEqual(self.last_text(), "❌ Некорректный IP. Ещё раз:")
        self.say("127.0.0.1")
        self.assertIn("проброс не сделать", self.last_text())
        self.say("198.51.100.7")
        self.say("70000")
        self.assertEqual(self.last_text(), "❌ Порт (1-65535).")
        self.say("0")
        self.assertEqual(self.last_text(), "❌ Порт (1-65535).")

    def test_reply_keyboard_texts_start_the_same_dialogs(self):
        self.say("🔀 AWG/WG")
        self.assertEqual(self.last().method, "sendMessage")
        self.assertEqual(self.last_text(), "🔀 <b>AmneziaWG (UDP)</b>\n\nВведите IP:")
        self.say("🛠 Custom")
        self.assertIn("Выберите протокол", self.last_text())


class RefusedTest(FwdCase):
    SS = SS_SSHD

    def test_conflict_refuses_and_writes_no_name(self):
        save_state()
        self.make_app()
        self.tg.geo["198.51.100.7"] = {"country": "Netherlands", "city": "", "isp": "X", "org": ""}
        self.run_dialog("a_t", "198.51.100.7", "22", "Сервер", "-")
        text = self.last_text()
        self.assertIn("⛔ <b>Проброс не добавлен</b>\n<code>tcp :22 → 198.51.100.7:22</code>", text)
        self.assertIn("Порт занят: здесь слушает SSH-сервер", text)
        self.assertIn("добавьте его в меню на сервере", text)
        self.assertIsNone(self.fwd("tcp", 22))
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/aliases")))
        self.assertEqual(self.w.nft_writes(), [])

    def test_legacy_engine_is_refused_politely(self):
        save_state(engine="legacy")
        self.make_app()
        for d in ("a_u", "a_t", "a_mt", "a_c", "a_cp_udp", "dr", "fa_y"):
            self.press(d)
            self.assertEqual(self.last_text(), "⛔ " + data.legacy_text(), d)
        self.assertIn("16 → 1", data.legacy_text())
        self.assertFalse(os.path.exists(self.path(DIALOG)))
        self.assertEqual(self.w.ipt.calls[-1:], [["iptables", "-t", "nat", "-S", "PREROUTING"]])   # only reads

    def test_engine_switched_back_in_the_middle(self):
        save_state()
        self.make_app()
        self.run_dialog("a_u", "198.51.100.7", "51820", "Имя")
        save_state(engine="legacy")
        self.say("-")
        self.assertEqual(self.last_text(), "⛔ " + data.legacy_text())
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/aliases")))


class ListDeleteTest(FwdCase):
    def setUp(self):
        super(ListDeleteTest, self).setUp()
        save_state([F443, F51820])
        aliases.update("203.0.113.5", name="Вена <script>", note="заметка & ещё", country="Austria", isp="A1")
        self.make_app()

    def test_list(self):
        self.write("/run/kaskad/live.json", '{"v": 1, "ts": 1, "forwards": {"tcp:443": {"packets": 9, "bytes": 2048}}}')
        self.press("lr")
        text = self.last_text()
        self.assertIn("📋 <b>Правила</b>\nСервер: <code>203.0.113.10</code>", text)
        self.assertIn("<code>203.0.113.10:443 (tcp) → 203.0.113.5:443</code> · 2,0 КБ", text)
        self.assertIn("<b>Вена &lt;script&gt;</b> <code>203.0.113.5</code> Austria | A1\n  <i>заметка &amp; ещё</i>", text)
        self.assertIn("<code>203.0.113.10:51820 (udp) → 198.51.100.7:51820</code>\n  <code>198.51.100.7</code>", text)

    def test_delete_by_key_with_confirmation(self):
        monitors.write("203.0.113.5", 60, 100, 300)
        monitors.write("198.51.100.7", 60, 100, 300)
        self.press("dr")
        self.assertIn(("❌ :443 → Вена <script>", "drk:tcp:443:203.0.113.5:443"), self.buttons())
        self.assertIn(("❌ :51820 (udp) → 198.51.100.7:51820", "drk:udp:51820:198.51.100.7:51820"), self.buttons())
        self.press("drk:tcp:443:203.0.113.5:443")
        self.assertIn("⚠️ <b>Удалить правило?</b>\n<code>tcp :443 → 203.0.113.5:443</code>", self.last_text())
        self.assertIsNotNone(self.fwd("tcp", 443))                      # only asked
        self.assertEqual(self.buttons()[0], ("✅ Да, удалить", "dry:tcp:443:203.0.113.5:443"))
        self.press("dry:tcp:443:203.0.113.5:443")
        self.assertEqual(self.last_text(), "✅ <code>tcp :443 → 203.0.113.5:443</code> удалено.")
        self.assertIsNone(self.fwd("tcp", 443))
        self.assertIsNotNone(self.fwd("udp", 51820))
        self.assertIsNone(monitors.get("203.0.113.5"))                  # nothing goes there any more
        self.assertIsNotNone(monitors.get("198.51.100.7"))
        log = self.read("/var/log/kaskad.log")
        self.assertIn("BOT DELETE: tcp :443 -> 203.0.113.5:443", log)
        self.assertIn("MONITOR REMOVE: 203.0.113.5", log)
        self.press("dry:tcp:443:203.0.113.5:443")                        # pressed again: already gone
        self.assertEqual(self.last_text(), "Не найдено.")

    def test_a_changed_rule_is_not_deleted(self):
        self.press("drk:tcp:443:203.0.113.5:443")
        st = state.load()
        state.upsert_forward(st, dict(F443, to="198.51.100.99"))
        state.save(st)
        self.press("dry:tcp:443:203.0.113.5:443")
        self.assertIn("Правило изменилось", self.last_text())
        self.assertEqual(self.fwd("tcp", 443)["to"], "198.51.100.99")

    def test_flush(self):
        monitors.write("203.0.113.5", 60, 100, 300)
        self.press("fa")
        self.assertEqual(self.buttons(), [("✅ Да", "fa_y"), ("❌ Нет", "m")])
        self.assertEqual(len(state.load()["forwards"]), 2)
        self.press("fa_y")
        self.assertEqual(self.last_text(), "✅ Очищено.")
        self.assertEqual(state.load()["forwards"], [])
        self.assertEqual(monitors.read_all(), [])
        self.assertIn("BOT FLUSH", self.read("/var/log/kaskad.log"))


class PaginationTest(FwdCase):
    def test_35_forwards(self):
        st = state.load()
        st["engine"] = "nft"
        for i in range(35):
            state.upsert_forward(st, {"proto": "tcp", "port": 10001 + i, "to": "198.51.100.%d" % (i + 1),
                                      "to_port": 443, "name": ""})
        state.save(st)
        self.make_app()
        self.press("lr")
        self.assertEqual(self.last_text().count("(tcp) →"), 30)
        self.assertIn(("1 из 2", "noop"), self.buttons())
        self.assertIn(("дальше ➡️", "lr~30"), self.buttons())
        self.press("lr~30")
        self.assertEqual(self.last_text().count("(tcp) →"), 5)
        self.assertIn(("⬅️ назад", "lr~0"), self.buttons())
        self.press("dr")
        self.assertEqual(len(self.data_of("drk:")), 30)
        self.press("dr~30")
        self.assertEqual(len(self.data_of("drk:")), 5)
        self.press("pm")
        self.assertEqual(len(self.data_of("ps:")), 30)
        self.press("noop")                                               # the page number: nothing happens


class LegacyListTest(FwdCase):
    def test_list_and_ping_targets_on_the_old_engine(self):
        save_state(engine="legacy")
        self.w.ipt.add("nat", "PREROUTING", "-p tcp -m tcp --dport 443 -m comment --comment kaskad:443:tcp "
                                            "-j DNAT --to-destination 203.0.113.5:443")
        self.w.ipt.add("nat", "PREROUTING", "-p tcp -m tcp --dport 80 -j DNAT --to-destination 10.0.0.2:80")
        self.make_app()
        self.press("lr")
        self.assertIn("· движок iptables", self.last_text())
        self.assertIn("<code>203.0.113.10:443 (tcp) → 203.0.113.5:443</code>", self.last_text())
        self.assertNotIn("10.0.0.2", self.last_text())                  # not ours
        self.press("pm")
        self.assertEqual(self.data_of("ps:"), ["ps:203.0.113.5"])


class UfwTest(FwdCase):
    BINARIES = DEFAULT_BINARIES + ("ufw",)

    def make_runner(self):
        r = super(UfwTest, self).make_runner()
        self.ufw_calls = []

        def ufw(argv, inp):
            self.ufw_calls.append(argv)
            return 0, "Status: active\n" if argv[1:] == ["status"] else "Rule deleted\n", ""

        r.on(["ufw"], ufw)
        return r

    def test_ufw_allow_of_a_2x_forward_goes_with_it(self):
        save_state([F443])
        self.make_app()
        self.press("dry:tcp:443:203.0.113.5:443")
        self.assertEqual(self.ufw_calls, [["ufw", "status"], ["ufw", "delete", "allow", "443/tcp"]])
