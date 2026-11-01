"""Monitoring in the bot process: 3 failures in a row before an alert, «восстановлено» after, the cooldown, the
alert recorded only after Telegram took it, TCP for TCP forwards, monitors/*.conf parsed (never sourced) and
written in the 2.x format, the monitoring screens, the gateway alerts of live.json (once per ts)."""

import json
import unittest
import os
import re
import time

from kaskad import i18n, state
from kaskad.bot import data, monitors
from kaskad.gw import live
from tests.bot.botcase import BotCase
from tests.bot.fake_tg import OWNER
from tests.fakes_nft import F443, F51820, save_state

IP = "203.0.113.5"


def alerts(case):
    return [t for t in case.tg.texts(OWNER) if "ALERT" in t]


class MonitorCase(BotCase):
    def setUp(self):
        super(MonitorCase, self).setUp()
        save_state([F443, F51820])
        self.make_app(monitor=True)
        self.mon = self.app.monitor
        monitors.write(IP, 10, 100, 300)

    def tick(self, step=10):
        self.mon.tick(self.now)
        self.now += step

    def stored(self):
        p = self.path("/etc/kaskad/bot_state/monitor.json")
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8") as f:
            return json.load(f)


class TransitionsTest(MonitorCase):
    def test_three_failures_alert_then_cooldown_then_recovery(self):
        self.tick()
        self.tick()
        self.assertEqual(alerts(self), [])
        self.assertEqual(self.mon.state_of(IP), None)
        self.tick()
        self.assertEqual(len(alerts(self)), 1)
        text = alerts(self)[0]
        self.assertIn("⚠️ <b>ALERT</b>: %s\nPing: TIMEOUT (порог: 100ms)" % IP, text)
        self.assertIn("Не отвечает: 3 проверки подряд.", text)
        self.assertEqual(self.read("/etc/kaskad/monitors/.last_alert_%s" % IP).strip(), str(int(self.now - 10)))
        self.assertEqual(self.mon.state_of(IP), "down")
        for _ in range(28):                                              # 280 s: within the 300 s cooldown
            self.tick()
        self.assertEqual(len(alerts(self)), 1)
        self.tick()
        self.tick()
        self.assertEqual(len(alerts(self)), 2)                           # still down after the cooldown
        self.assertIn("Не отвечает: 33 проверки подряд.", alerts(self)[1])
        self.pings[IP] = "20.5"
        self.tick()
        self.assertEqual(self.tg.texts(OWNER)[-1], "✅ <b>%s</b>: восстановлено\nPing: 20.5ms [ICMP]" % IP)
        self.assertEqual(self.mon.state_of(IP), "up")
        self.tick()
        self.assertEqual(len(self.tg.texts(OWNER)), 3)                   # no more messages while it is fine
        log = self.read("/var/log/kaskad.log")
        self.assertIn("ALERT: %s ping=TIMEOUT threshold=100ms" % IP, log)
        self.assertIn("MONITOR OK: %s" % IP, log)

    def test_alert_recorded_only_after_a_successful_send(self):
        self.tg.fail("sendMessage", 500, "Internal Server Error")
        self.tick()
        self.tick()
        self.tick()
        self.assertEqual(alerts(self), [])
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/monitors/.last_alert_%s" % IP)))
        self.assertNotIn(IP, self.stored().get("alerted", {}))
        self.tick()                                                      # the next failure: sent this time
        self.assertEqual(len(alerts(self)), 1)
        self.assertTrue(os.path.exists(self.path("/etc/kaskad/monitors/.last_alert_%s" % IP)))
        self.assertIn(IP, self.stored()["alerted"])

    def test_recovery_message_survives_a_restart(self):
        for _ in range(3):
            self.tick()
        self.assertEqual(len(alerts(self)), 1)
        from kaskad.bot.monitor import Monitor
        self.app.monitor = self.mon = Monitor(self.app, sync=True)       # a new process
        self.assertEqual(self.mon.state_of(IP), "down")
        self.pings[IP] = "9"
        self.tick()
        self.assertIn("восстановлено", self.tg.texts(OWNER)[-1])

    def test_a_ping_above_the_threshold_is_a_failure(self):
        self.pings[IP] = "150"
        for _ in range(3):
            self.tick()
        self.assertIn("Ping: 150ms (порог: 100ms)\nВыше порога: 3 проверки подряд.", alerts(self)[0])

    def test_interval_is_honoured(self):
        for _ in range(4):                                               # T0, +3, +6, +9
            self.tick(step=3)
        self.assertEqual(len(self.runner.commands(["ping"])), 1)
        self.tick(step=3)                                                # +12: the next check
        self.assertEqual(len(self.runner.commands(["ping"])), 2)

    def test_tcp_forward_is_checked_by_connect_udp_only_by_icmp(self):
        self.tcp[(IP, 443)] = 30.0
        for _ in range(4):
            self.tick()
        self.assertEqual(alerts(self), [])
        self.assertEqual(self.mon.state_of(IP), "up")
        monitors.write("198.51.100.7", 10, 100, 300)                     # the udp forward
        self.tcp[("198.51.100.7", 51820)] = 5.0
        self.mon.forget()
        for _ in range(3):
            self.tick()
        self.assertEqual(len(alerts(self)), 1)
        self.assertIn("198.51.100.7", alerts(self)[0])

    def test_no_alerts_without_an_owner_chat(self):
        self.write_config(BOT_CHAT_ID="")
        self.app.refresh_config()
        for _ in range(4):
            self.tick()
        self.assertEqual(self.tg.sent("sendMessage"), [])


class ConfTest(MonitorCase):
    def test_2x_format_and_safe_parsing(self):
        self.assertEqual(self.read("/etc/kaskad/monitors/%s.conf" % IP),
                         'MON_IP="%s"\nMON_INTERVAL=10\nMON_THRESHOLD=100\nMON_COOLDOWN=300\n' % IP)
        self.write("/etc/kaskad/monitors/evil.conf", 'MON_IP="1.2.3.4$(touch /tmp/kaskad-pwned)"\nMON_INTERVAL=10\n'
                                                     'MON_THRESHOLD=100\n')
        self.write("/etc/kaskad/monitors/evil2.conf", 'MON_IP="1.2.3.5"\nMON_INTERVAL=10; rm -rf /\nMON_THRESHOLD=1\n')
        self.write("/etc/kaskad/monitors/old.conf", 'MON_IP="198.51.100.20"\nMON_INTERVAL=60\nMON_THRESHOLD=50\n')
        got = monitors.read_all()
        self.assertEqual([m["ip"] for m in got], ["198.51.100.20", IP])
        self.assertEqual(got[0]["cooldown"], 300)                        # 2.0 files had no cooldown
        self.assertFalse(os.path.exists("/tmp/kaskad-pwned"))

    def test_threshold_input_and_old_files(self):
        # 3.2.21 (T-N5 3.2.20, Н-2): новый порог — 1…10000 мс, как в меню 7; файл прежней версии с порогом
        # выше 10000 читается как был — монитор не пропадает
        self.assertEqual([monitors.parse_threshold(t) for t in ("150", " 1 ", "10000", "0150")], [150, 1, 10000, 150])
        # как меню и бот на bash (критик 3.2.21, М-5): пробелы внутри числа убираются, цифры — только ASCII
        self.assertEqual([monitors.parse_threshold(t) for t in ("1 50", "\u0661\u0665\u0660", "1\t0")], [150, None, 10])
        self.assertEqual([monitors.parse_threshold(t) for t in ("", "0", "10001", "y", "1.5", "-1", None)],
                         [None] * 7)
        self.write("/etc/kaskad/monitors/big.conf", 'MON_IP="198.51.100.30"\nMON_INTERVAL=60\nMON_THRESHOLD=20000\n')
        self.assertEqual(monitors.get("198.51.100.30")["threshold"], 20000)

    def test_screens(self):
        self.press("mm")
        self.assertEqual(self.buttons()[:3], [("➕ Добавить", "ma"), ("📋 Список", "ml"), ("➖ Удалить", "md")])
        self.press("ma")
        self.assertEqual(self.data_of("ma:"), ["ma:198.51.100.7", "ma:" + IP])
        self.press("ma:198.51.100.7")
        self.assertEqual(self.data_of("mi:"), ["mi:198.51.100.7:10", "mi:198.51.100.7:60", "mi:198.51.100.7:300"])
        self.press("mi:198.51.100.7:60")
        self.assertIn("Порог (мс)", self.last_text())
        for bad in ("abc", "y", "0", "10001", "65535", "-5"):
            self.say(bad)
            self.assertEqual(self.last_text(), "❌ Порог — число миллисекунд от 1 до 10000, например 150.")
        self.say("150")
        self.assertEqual(self.data_of("mc:")[0], "mc:198.51.100.7:60:150:10")
        self.press("mc:198.51.100.7:60:150:900")
        self.assertEqual(self.last_text(), "✅ <b>198.51.100.7</b>\n60с | 150мс | уведомл: 900с")
        self.assertEqual(monitors.get("198.51.100.7"),
                         {"ip": "198.51.100.7", "interval": 60, "threshold": 150, "cooldown": 900})
        self.press("ml")
        self.assertIn("<code>198.51.100.7</code>\n  60с | 150мс | 900с", self.last_text())
        self.press("md")
        self.assertEqual(self.data_of("md:"), ["md:198.51.100.7", "md:" + IP])
        self.press("md:198.51.100.7")
        self.assertEqual(self.last_text(), "✅ 198.51.100.7 удалён.")
        self.assertIsNone(monitors.get("198.51.100.7"))
        self.press("mc:1.2.3.4:x:1:1")
        self.assertEqual(self.last_text(), "❌ Ошибка данных.")


class GatewayEventsTest(MonitorCase):
    def live(self, events):
        self.write("/run/kaskad/live.json", json.dumps({"v": 1, "ts": self.now, "events": events}))

    def test_history_is_not_news_and_each_ts_is_sent_once(self):
        os.unlink(self.path("/etc/kaskad/monitors/%s.conf" % IP))
        self.mon.forget()
        self.tick()                                                      # no live.json yet
        self.live([{"ts": self.now - 100, "kind": "exit_dead", "text": "выход «Вена» не работает"}])
        self.tick()                                                      # the first look: history
        self.assertEqual(self.tg.sent("sendMessage"), [])
        t = self.now
        self.live([{"ts": t - 110, "kind": "exit_dead", "text": "выход «Вена» не работает"},
                   {"ts": t, "kind": "exit_alive", "text": "выход «Вена» снова работает"},
                   {"ts": t, "kind": "inbound_down", "text": "вход WireGuard <не работает>"}])
        self.tick()
        self.assertEqual(self.tg.texts(OWNER), ["🛡 <b>Шлюз</b>\n🟢 выход «Вена» снова работает\n"
                                                "🔴 вход WireGuard &lt;не работает&gt;"])
        self.tick()
        self.tick()
        self.assertEqual(len(self.tg.sent("sendMessage")), 1)
        self.live([{"ts": t, "kind": "exit_alive", "text": "x"}, {"ts": t + 20, "kind": "gw_part_restored"}])
        self.tg.fail("sendMessage", 502, "Bad Gateway")
        self.tick()                                                      # not sent: tried again next time
        self.tick()
        self.assertEqual(self.tg.texts(OWNER)[-1], "🛡 <b>Шлюз</b>\n🛠 часть шлюза в таблице nftables восстановлена")
        self.assertEqual(self.stored()["gw_ts"], t + 20)

    def test_old_events_are_skipped(self):
        self.live([])
        self.tick()
        self.live([{"ts": self.now - 7200, "kind": "exit_dead", "text": "давно"}])
        self.tick()
        self.assertEqual([t for t in self.tg.texts(OWNER) if "Шлюз" in t], [])


# ── этап 1, T9: пуши новых событий (SPEC-stage1-exits.md §3.6, §8, §11.1 «Бот») ──────────────────────────────────
# Бот отправляет готовые события live.json (их публикует слой событий T4 — антиспам там же); здесь — фильтр по
# gateway.notify.push, тексты новых ключей (gw/live.event_text), иконки и сводка node_online.

CYR = re.compile(r"[А-Яа-яЁё]")


def ev(kind, ts, key=None, **args):
    return {"id": int(ts * 1000), "ts": ts, "kind": kind, "text": "", "key": key or "gw_live.event." + kind,
            "args": args}


def hhmm(ts):
    """Время сервера: пояс Kaskad по умолчанию — Europe/Moscow (UTC+3, SE §3.7)."""
    t = time.gmtime(ts + 3 * 3600)
    return "%02d:%02d" % (t.tm_hour, t.tm_min)


class S1PushCase(MonitorCase):
    def setUp(self):
        super(S1PushCase, self).setUp()
        os.unlink(self.path("/etc/kaskad/monitors/%s.conf" % IP))
        self.mon.forget()
        self.live([])
        self.tick()                                                      # первый взгляд: история

    def live(self, events, node_offline=None):
        doc = {"v": 1, "ts": self.now, "events": events, "i18n": {"lang": "ru", "problems": [], "why": {}}}
        if node_offline is not None:                                     # live.policy (SE §7.9, пишет T4)
            doc["policy"] = {"node_offline": node_offline}
        self.write("/run/kaskad/live.json", json.dumps(doc))

    def switches(self, **push):
        st = state.load()
        st.setdefault("gateway", {})["notify"] = {"push": push, "flap_summary": True}
        state.save(st)

    def gw_texts(self):
        return [t for t in self.tg.texts(OWNER) if "Шлюз" in t]


class S1PushSwitchTest(S1PushCase):
    def test_switch_silences_the_bot_only_the_feed_keeps_the_event(self):
        # Н3: переключатель вида гасит только отправку в бот; лента панели — те же события
        self.switches(exit_dead=False, exit_degraded=False)
        t = self.now
        evs = [ev("exit_dead", t, name="Вена"), ev("exit_alive", t, name="Рига"),
               ev("exit_degraded", t + 1, name="Вена", rule="r-1", metric="rtt", value=400, threshold=300)]
        self.live(evs)
        self.tick()
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n🟢 Выход «Рига» снова работает"])
        self.assertEqual(self.stored()["gw_ts"], t + 1)                  # заглушённое не ждёт: бот его прошёл
        feed = data.live_doc()["events"]                                 # то, что читают панель и SSE
        self.assertEqual([e["kind"] for e in feed], ["exit_dead", "exit_alive", "exit_degraded"])
        self.assertEqual(feed[0]["text"], "Выход «Вена» не работает")
        doc, _age = live.read()
        self.assertEqual(len(doc["events"]), 3)
        self.switches(exit_dead=True, exit_degraded=True)                # включили снова — старое не досылается
        self.tick()
        self.assertEqual(len(self.gw_texts()), 1)

    def test_defaults_without_switches_and_kinds_without_one(self):
        # нет gateway.notify (узел до этапа 1) — всё как в 3.3.5; speedtest_budget по умолчанию выкл.;
        # xray_rejected — только лента; входы и gw_part_restored переключателя не имеют — всегда
        t = self.now
        self.live([ev("exit_dead", t, name="Вена"), ev("speedtest_budget", t, name="Вена", period="day"),
                   ev("xray_rejected", t, error="bad"), ev("inbound_down", t, key="gw_live.inbound_down", title="WireGuard"),
                   ev("exit_recovered", t, name="Рига")])
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n🔴 Выход «Вена» не работает\n🔴 Протокол WireGuard не работает\n"
                                           "🟢 Выход «Рига» снова в работе"])
        self.switches(speedtest_budget=True, exit_dead="нет")            # не bool — умолчание вида
        self.live([ev("speedtest_budget", t + 5, name="Вена", period="month"), ev("exit_dead", t + 5, name="Рига")])
        self.tick()
        self.assertEqual(self.gw_texts()[-1], "🛡 <b>Шлюз</b>\n⏱ Выход «Вена»: лимит тестов скорости на этот месяц "
                                              "исчерпан\n🔴 Выход «Рига» не работает")

    def test_a_switch_that_is_not_bool_is_the_default_not_its_truth(self):
        # З1: «не bool → умолчание вида», а не bool(v): "да" у выключенного по умолчанию и 0 у включённого
        self.switches(speedtest_budget="да", exit_dead=0)
        t = self.now
        self.live([ev("speedtest_budget", t, name="Вена", period="day"), ev("exit_dead", t, name="Рига")])
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n🔴 Выход «Рига» не работает"])

    def test_rule_push_is_not_degraded(self):
        # exit_rule_push — свой текст и свой переключатель, не «ухудшен»
        self.switches(exit_degraded=False)
        t = self.now
        args = dict(name="Вена", rule="r-rtt", rule_name="Высокий отклик", metric="rtt", value=350, threshold=300)
        self.live([ev("exit_degraded", t, **args), ev("exit_rule_push", t, **args)])
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n🔔 Выход «Вена»: сработало условие «Высокий отклик» — "
                                           "отклик 350 мс (порог 300 мс); выход в работе"])
        self.assertNotEqual(live.event_text(ev("exit_degraded", t, **args), "en"),
                            live.event_text(ev("exit_rule_push", t, **args), "en"))


class S1MetricFallbackTest(unittest.TestCase):
    def test_an_unknown_metric_is_said_in_words_without_its_code(self):
        # REV-s1-t9-2 п. 6: запасной текст условия — понятный человеку, без внутреннего кода метрики
        args = dict(name="Вена", rule="r-x", rule_name="Своё", metric="zz_new_metric", value=2.5, threshold=2)
        ru = live.event_text(ev("exit_rule_push", 1000, **args), "ru")
        en = live.event_text(ev("exit_rule_push", 1000, **args), "en")
        self.assertIn("замер 2,5 вышел за порог 2", ru)
        self.assertIn("a reading of 2.5 crossed the limit of 2", en)
        for text in (ru, en):
            self.assertNotIn("zz_new_metric", text)
            self.assertNotIn("условие:", text)


class S1OutageTest(S1PushCase):
    def test_node_online_is_one_summary_instead_of_n_exit_dead(self):
        t, before = self.now, self.stored()["gw_ts"]
        down = [ev("node_offline", t, since=t), ev("exit_dead", t, name="Вена"),
                ev("exit_dead", t + 10, name="Рига"), ev("exit_dead", t + 20, name="Токио"),
                ev("exit_alive", t + 30, name="Вена")]
        self.live(down)
        self.tick()
        self.tick()
        self.assertEqual(self.gw_texts(), [])                            # сервер без сети: бот ждёт
        self.assertEqual(self.stored()["gw_ts"], before)                 # и не проходит событий сбоя
        back = down + [ev("node_online", t + 60, since=t, until=t + 60, minutes=1),
                       ev("exit_alive", t + 60, name="Рига")]
        self.live(back)
        self.tg.fail("sendMessage", 502, "Bad Gateway")
        self.tick()                                                      # не ушло — сводка не теряется
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n📶 Сервер был без связи с %s до %s (1 мин)\n"
                                           "За это время: выход не работает — 3, выход снова работает — 1\n"
                                           "🟢 Выход «Рига» снова работает" % (hhmm(t), hhmm(t + 60))])
        self.assertEqual(self.stored()["gw_ts"], t + 60)
        self.tick()
        self.assertEqual(len(self.gw_texts()), 1)

    def test_a_blip_of_one_round_is_no_summary(self):
        # REV-3379 П-3: one failed round of the collector (10 s) — «📶 … (0 мин)» is not sent; what it held goes as usual
        t = self.now
        self.live([ev("node_offline", t, since=t), ev("exit_dead", t + 5, name="Вена"),
                   ev("node_online", t + 10, since=t, until=t + 10, minutes=0)], node_offline=False)
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n🔴 Выход «Вена» не работает"])
        self.assertEqual(self.stored()["gw_ts"], t + 10)
        t2 = t + 600                                               # a blip alone — silence, the feed keeps it
        self.live([ev("node_offline", t2, since=t2), ev("node_online", t2 + 50, since=t2, until=t2 + 50, minutes=0)],
                  node_offline=False)
        self.now = t2 + 50
        self.tick()
        self.assertEqual(len(self.gw_texts()), 1)
        self.assertEqual(self.stored()["gw_ts"], t2 + 50)
        evs = [{"kind": "node_offline", "id": 1, "ts": t2}, {"kind": "node_online", "id": 2, "ts": t2 + 50}]
        self.assertEqual([e["kind"] for e in live.feed_events(evs)], ["node_online"])

    def test_outage_switch_off_and_silenced_kinds_not_counted(self):
        self.switches(node_offline=False, exit_alive=False)
        t = self.now
        self.live([ev("node_offline", t, since=t), ev("exit_dead", t + 10, name="Вена"),
                   ev("exit_alive", t + 20, name="Вена"), ev("node_online", t + 30, since=t, until=t + 30)])
        self.tick()
        self.assertEqual(self.gw_texts(), [])                            # сводки нет, N exit_dead — тоже нет
        self.assertEqual(self.stored()["gw_ts"], t + 30)

    def test_a_lost_node_online_does_not_silence_the_bot_for_good(self):
        from kaskad.bot import monitor
        t = self.now
        self.live([ev("node_offline", t, since=t), ev("exit_dead", t + 10, name="Вена")])
        self.tick()
        self.assertEqual(self.gw_texts(), [])
        self.now = t + monitor.NODE_HOLD + 60
        self.tick()
        # С1: бот не знает, что связь вернулась, — прошедшее время, не «Сервер без связи с …»
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n📴 Сервер был без связи с %s; сообщения о возврате связи "
                                           "не было\nЗа это время: выход не работает — 1" % hhmm(t)])
        self.live([ev("node_offline", t, since=t), ev("exit_dead", t + 10, name="Вена"),
                   ev("exit_alive", self.now, name="Вена")])
        self.tick()
        self.assertEqual(self.gw_texts()[-1], "🛡 <b>Шлюз</b>\n🟢 Выход «Вена» снова работает")


    def test_a_long_real_outage_is_one_message(self):
        # С1: сети нет 2 ч, отправка падает; сеть вернулась, бот тикнул раньше, чем сборщик записал node_online.
        # Узел сам говорит «без сети» (live.policy.node_offline) — страховка 1 ч не отпускается: одно сообщение
        t = self.now
        down = [ev("node_offline", t, since=t), ev("inbound_down", t + 30, key="gw_live.inbound_down", title="WireGuard")]
        self.live(down, node_offline=True)
        self.tg.fail("sendMessage", 502, "Bad Gateway")
        self.now = t + 7200
        self.tick()                                                      # сети нет
        self.tg.failures[:] = []                                         # сеть вернулась
        self.tick()                                                      # node_online сборщик ещё не записал
        self.assertEqual(self.gw_texts(), [])
        back = self.now
        self.live(down + [ev("node_online", back, since=t, until=back, minutes=120)], node_offline=False)
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n📶 Сервер был без связи с %s до %s (2 ч)\n"
                                           "За это время: протокол не работает — 1" % (hhmm(t), hhmm(back))])
        self.tick()
        self.assertEqual(len(self.gw_texts()), 1)

    def test_offline_for_hours_then_no_node_online_is_one_message(self):
        # REV-s1-t9-2 З-1: policy.node_offline 3 ч, потом false без node_online (событие потеряно) — одна страховка
        # в прошедшем времени через час, не отброшенная как старая (GW_MAX_AGE), и больше ничего
        from kaskad.bot import monitor
        t = self.now
        down = [ev("node_offline", t, since=t), ev("exit_dead", t + 10, name="Вена")]
        self.live(down, node_offline=True)
        self.tick()
        self.now = t + 3 * 3600
        self.tick()
        self.assertEqual(self.gw_texts(), [])
        self.live(down, node_offline=False)
        self.now += 60
        self.tick()
        self.assertEqual(self.gw_texts(), [])                            # час от последнего «без сети»
        self.now = t + 3 * 3600 + monitor.NODE_HOLD + 60
        self.tick()
        self.tick()
        self.assertEqual(len(self.gw_texts()), 1)
        self.assertIn("Сервер был без связи с %s" % hhmm(t), self.gw_texts()[0])
        self.assertIn("За это время: выход не работает — 1", self.gw_texts()[0])
        self.assertEqual(self.stored()["gw_ts"], t + 10)

    def test_policy_cleared_before_node_online_is_one_message(self):
        # REV-s1-t9-2 п. 1: сборщик снял policy.node_offline раньше, чем записал node_online, — страховка не уходит
        # вперёд сводки (час — от последнего «без сети», а не от node_offline)
        t = self.now
        down = [ev("node_offline", t, since=t), ev("exit_dead", t + 10, name="Вена")]
        self.live(down, node_offline=True)
        self.tick()
        self.now = t + 7200
        self.tick()
        self.live(down, node_offline=False)
        self.now += 5
        self.tick()
        self.assertEqual(self.gw_texts(), [])
        back = self.now + 30
        self.live(down + [ev("node_online", back, since=t, until=back, minutes=120)], node_offline=False)
        self.now = back
        self.tick()
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n📶 Сервер был без связи с %s до %s (2 ч)\n"
                                           "За это время: выход не работает — 1" % (hhmm(t), hhmm(back))])

    def test_the_hold_is_said_again_when_its_send_failed(self):
        # З1: страховка не проходит события, если отправка не удалась; следующая попытка — то же сообщение
        from kaskad.bot import monitor
        t = self.now
        self.live([ev("node_offline", t, since=t), ev("exit_dead", t + 10, name="Вена")])
        self.tick()
        self.now = t + monitor.NODE_HOLD + 60
        self.tg.fail("sendMessage", 502, "Bad Gateway")
        self.tick()
        self.assertEqual(self.stored()["gw_ts"] < t, True)
        self.tick()
        self.assertEqual(len(self.gw_texts()), 1)
        self.assertIn("За это время: выход не работает — 1", self.gw_texts()[0])
        self.assertEqual(self.stored()["gw_ts"], t + 10)

    def test_an_old_hold_is_not_sent_after_the_bot_was_down(self):
        # С1, GW_MAX_AGE: бот лежал 3 ч, в ленте node_offline 3 ч назад без node_online — сводку трёхчасовой
        # давности не шлём (как события старше часа в 3.3.5), события проходим
        t = self.now
        self.live([ev("node_offline", t - 10800, since=t - 10800), ev("exit_dead", t - 10790, name="Вена")])
        self.tick()
        self.assertEqual(self.gw_texts(), [])
        self.assertEqual(self.stored()["gw_ts"], t - 10790)
        self.live([ev("node_offline", t - 10800, since=t - 10800), ev("exit_dead", t - 10790, name="Вена"),
                   ev("exit_alive", self.now, name="Вена")])
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n🟢 Выход «Вена» снова работает"])

    def test_node_online_first_in_its_round(self):
        # З2: событие того же такта, что и node_online, — новость после пропажи, не «за это время»,
        # даже если в ленте оно стоит раньше
        t = self.now
        self.live([ev("node_offline", t, since=t), ev("exit_dead", t + 10, name="Вена"),
                   ev("exit_alive", t + 60, name="Рига"), ev("node_online", t + 60, since=t, until=t + 60, minutes=1)])
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n📶 Сервер был без связи с %s до %s (1 мин)\n"
                                           "За это время: выход не работает — 1\n🟢 Выход «Рига» снова работает"
                                           % (hhmm(t), hhmm(t + 60))])

    def test_double_node_offline_since_the_first(self):
        # З2: перезапуск сборщика во время пропажи — второй node_offline; «с …» — от первого
        t = self.now
        self.live([ev("node_offline", t, since=t), ev("exit_dead", t + 10, name="Вена"),
                   ev("node_offline", t + 600, since=t + 600),
                   ev("node_online", t + 1200, since=t + 600, until=t + 1200, minutes=10)])
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n📶 Сервер был без связи с %s до %s (20 мин)\n"
                                           "За это время: выход не работает — 1" % (hhmm(t), hhmm(t + 1200))])


class S1TextsTest(S1PushCase):
    def test_review_nits(self):
        # придирки REV-s1-t9: 🟠 только при числе flaps; «(12 мин был снят)»; ожидаемая страна; без кода метрики;
        # без «(0 устройств)»
        from kaskad.bot import monitor
        t = self.now
        self.assertEqual(monitor.gw_icon(ev("exit_alive", t, name="Вена", flaps=None)), "🟢")
        self.assertEqual(monitor.gw_icon(ev("exit_failover", t, flaps=3, to_tier="L1")), "🔀")
        self.assertEqual(monitor.gw_icon(ev("exit_alive", t, name="Вена", flaps=3)), "🟠")
        rec = ev("exit_recovered", t, name="Вена", **{"from": "removed", "after_s": 720})
        self.assertEqual(live.event_text(rec, "ru"), "Выход «Вена» снова в работе (12 мин был снят)")
        c = ev("exit_degraded", t, name="Вена", rule="r-1", metric="country", value="NL", threshold="DE")
        self.assertEqual(live.event_text(c, "ru"), "Выход «Вена» ухудшен: страна NL, ожидалась DE")
        self.assertIn("country NL, DE expected", live.event_text(c, "en"))
        o = ev("exit_degraded", t, name="Вена", rule="r-1", metric="cpu_temp", value=2.5, threshold=2)
        for lang in ("ru", "en"):
            self.assertNotIn("cpu_temp", live.event_text(o, lang))
        b = ev("exit_failover", t, from_tier="L2", to_tier="L1", moved=0, back=True)
        self.assertEqual(live.event_text(b, "ru"), "Снова основной уровень «L1»")
        self.assertEqual(live.event_text(b, "en"), "Back on the primary level “L1”")
        self.assertNotIn("(", live.event_text(b, "ru"))

    def test_flaps_summary_and_compat_words(self):
        # flap_summary: «нестабилен» по flaps, вид — текущее состояние; compat — текст 3.3.5 со счётчиком
        t = self.now
        self.live([ev("exit_dead", t, name="Вена", flaps=3),
                   ev("exit_dead", t, key="gw_live.event.exit_dead_n", name="Рига", n=3)])
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n🟠 Выход «Вена» нестабилен: 3 падения за 15 мин, сейчас — "
                                           "не работает\n🔴 Выход «Рига» не работает (обрывов за 15 мин: 3)"])
        e = ev("exit_alive", t, name="Vienna", flaps=5)
        self.assertEqual(live.event_text(e, "en"), "Exit “Vienna” is unstable: 5 drops in 15 min, now working")
        self.assertEqual(live.event_text(e, "ru"), "Выход «Vienna» нестабилен: 5 падений за 15 мин, сейчас — работает")

    def test_standby_unstable_and_sub_confirm(self):
        t = self.now
        u = ev("exit_standby", t, name="Vienna", why="unstable", flaps=3, until=t + 900)
        self.assertEqual(live.event_text(u, "ru"), "Выход «Vienna» в запасе: нестабилен (3 падения за 15 мин), "
                                                   "вернётся в %s" % hhmm(t + 900))
        self.assertEqual(live.event_text(u, "en"), "Exit “Vienna” on standby: unstable (3 drops in 15 min), "
                                                   "back at %s" % hhmm(t + 900))
        s = ev("exit_standby", t, name="k1", why="sub_confirm", sub="s-1", sub_name="Provider", group="g-1a2b3c",
               group_name="Home", role="ru")
        self.assertEqual(live.event_text(s, "ru"), "Подписка «Provider»: новый ключ «k1» поставлен в запас в группе "
                                                   "«Home» — подтвердите в карточке выхода")
        self.assertEqual(live.event_text(s, "en"), "Subscription “Provider”: new key “k1” was put on standby in group "
                                                   "“Home” — confirm it in Exits")
        self.live([u])
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n⏸ " + live.event_text(u, "ru")])

    def test_group_last_step_and_restored(self):
        t = self.now
        main = {"key": "gw_live.group.main", "args": {}}                 # имя по умолчанию — переводится при показе
        block = ev("group_last_step", t, group="main", name=main, role="abroad", step="block", devices=5, direct=2,
                   fallen=["Вена", "Рига"])
        self.assertEqual(live.event_text(block, "ru"),
                         "«Основная»: все выходы недоступны — нет выхода в интернет у 5 устройств; 2 устройства идут "
                         "напрямую с адреса сервера; упали «Вена» и «Рига»")
        self.assertEqual(live.event_text(block, "en"),
                         "“Main”: all exits are down — 5 devices cannot reach the internet; 2 devices go out directly from "
                         "the server’s address; “Вена” and “Рига” went down")
        direct = ev("group_last_step", t, group="g-1a2b3c", name="Стриминг", step="direct", devices=1)
        self.assertEqual(live.event_text(direct, "ru"), "«Стриминг»: все выходы недоступны — 1 устройство идёт "
                                                        "напрямую с адреса сервера")
        back = ev("group_restored", t, group="main", name="", exit="Вена", after_s=420)
        self.assertEqual(live.event_text(back, "ru"), "«Основная»: снова через «Вена» (через 7 мин)")
        self.assertEqual(live.event_text(back, "en"), "“Main”: going through “Вена” again (after 7 min)")
        self.live([block, back])
        self.tick()
        self.assertEqual(self.gw_texts()[-1].split("\n")[1:], ["🚫 " + live.event_text(block, "ru"),
                                                               "🟢 " + live.event_text(back, "ru")])

    def test_group_of_the_exit_in_front_except_main(self):
        t = self.now
        self.assertEqual(live.event_text(ev("exit_dead", t, name="Вена", group="main", group_name="", role="abroad"),
                                         "ru"), "Выход «Вена» не работает")                 # «Как раньше»: 3.3.5
        g = ev("exit_dead", t, key="gw_live.event.exit_dead_n", name="Вена", n=2, group="g-1a2b3c",
               group_name="Стриминг", role="abroad")
        self.assertEqual(live.event_text(g, "ru"), "«Стриминг»: выход «Вена» не работает (обрывов за 15 мин: 2)")
        self.assertEqual(live.event_text(g, "en"), "“Стриминг”: exit “Вена” is down (drops in 15 min: 2)")

    def test_every_new_key_in_both_languages(self):
        # live.event_text для новых ключей: свой текст (не ключ и не пустой), RU и EN, без пустых {…}; иконка вида
        from kaskad.bot import monitor
        t = self.now
        samples = [
            ev("exit_degraded", t, name="A", rule="r-1", metric="rtt", unit="x_norm", value=2.5, threshold=2),
            ev("exit_degraded", t, name="A", rule="r-1", metric="service", service="youtube", value=False),
            ev("exit_rule_push", t, name="A", rule="r-1", metric="traffic", unit="gb_month", value=120, threshold=100),
            ev("exit_recovered", t, name="A", **{"from": "standby", "after_s": 3900}),
            ev("exit_recovered", t, name="A", **{"from": "off"}),
            ev("exit_standby", t, name="A", why="rule:r-1"), ev("exit_standby", t, name="A", why="manual"),
            ev("exit_removed", t, name="A", why="quota"), ev("exit_removed", t, name="A", why="country"),
            ev("exit_removed", t, name="A", why="maintenance"), ev("exit_removed", t, name="A", why="disabled"),
            ev("exit_removed", t, name="A", why="rule:r-2", rule_name="Loss"),
            ev("exit_removed", t, name="A", why="dead"),
            ev("exit_failover", t, from_tier="L1", to_tier="L2", moved=3, back=False),
            ev("exit_failover", t, from_tier="L2", to_tier="L1", moved=3, back=True),
            ev("exit_quota", t, name="A", used_gb=81.5, limit_gb=100, pct=81, reset="2026-11-01"),
            ev("exit_quota", t, name="A", used_gb=100, limit_gb=100, pct=100, reset="2026-11-01"),
            ev("exit_country_changed", t, name="A", old="DE", new="NL", all_exits=False),
            ev("exit_country_changed", t, name="A", old="DE", new="NL", all_exits=True),
            ev("group_last_step", t, group="g-1", name="G", step="block", devices=3),
            ev("group_restored", t, group="g-1", name="G", after_s=30),
            ev("xray_rejected", t, error="outbound x: bad"),
            ev("node_offline", t, since=t), ev("node_online", t, since=t, until=t + 7200, minutes=120),
            ev("speedtest_budget", t, name="A", period="day")]
        seen = set()
        for e in samples:
            ru, en = live.event_text(e, "ru"), live.event_text(e, "en")
            for text in (ru, en):
                self.assertTrue(text and "gw_live." not in text and "{" not in text, (e, text))
            self.assertTrue(CYR.search(ru), (e, ru))
            self.assertIsNone(CYR.search(en), (e, en))
            self.assertNotEqual(ru, en)
            seen.add((ru, en))
            self.assertNotEqual(monitor.gw_icon(e), "ℹ️", e["kind"])
        self.assertEqual(len(seen), len(samples))                        # каждый вариант — свои слова
        for kind in monitor.GW_KINDS:                                    # короткие слова вида — в обоих каталогах
            for lang in ("ru", "en"):
                self.assertTrue(i18n.has("bot.mon.gw_" + kind, lang), (kind, lang))
                self.assertIn("bot.mon.gw_" + kind, i18n.catalog(lang))
        self.assertIn("2 ч", live.event_text(samples[23], "ru"))
        self.assertIn("1 h 5 min", live.event_text(samples[3], "en"))
        self.assertIn("отклик ×2,5 от обычного (порог ×2)", live.event_text(samples[0], "ru"))
        self.assertIn("81% квоты — 81,5 из 100 ГБ, сброс 01.11", live.event_text(samples[15], "ru"))
        self.assertIn("resets Nov 1", live.event_text(samples[15], "en"))


# ── здоровье узла (SPEC-node-score §3.5, §2.1, §3.9; A6): пуш ровно по args.push, бот без шлюза ─────────────────────

def hev(kind, ts, key="gw_host.event.raise", **args):
    """Событие узла, как его публикует сборщик (A5): hostmon решил ``push``; ``what``/``find`` — msgs gw_host."""
    a = {"check": "disk", "level": "crit", "from": "ok", "what": {"key": "gw_host.check.disk", "args": {}},
         "find": {"key": "gw_host.find.disk.crit", "args": {"mount": "/", "gb": 1.2, "pct": 99}}, "why": None}
    a.update(args)
    return {"id": int(ts * 1000), "ts": ts, "kind": kind, "text": "", "key": key, "args": a}


class HostPushTest(S1PushCase):
    def test_exactly_by_args_push(self):
        # REV-ns-a5 С-1: push = false не шлётся (предупреждение при host_level = crit), true — шлётся
        t = self.now
        self.live([hev("host_disk", t, level="warn", push=False,
                       find={"key": "gw_host.find.disk.warn", "args": {"mount": "/", "gb": 8, "pct": 92}}),
                   hev("host_mem", t, push=False, check="mem")])
        self.tick()
        self.assertEqual(self.tg.texts(OWNER), [])
        self.live([hev("host_disk", t + 1, push=True)])
        self.tick()
        self.assertEqual(self.tg.texts(OWNER), ["🔴 Сервер: Диск / почти заполнен: осталось 1,2 ГБ (99% занято)"])

    def test_node_lines_go_without_the_gateway_title(self):
        # REV-ns-a6 П-1: тревога узла со шлюзом — как без него (bot/hostwatch.py): без «🛡 Шлюз»; в одном
        # сообщении со строками шлюза — после них, через пустую строку
        t = self.now
        self.live([hev("host_disk", t, push=True), dict(ev("exit_dead", t, name="Exit-1"), id=int(t * 1000) + 1)])
        self.tick()
        self.assertEqual(self.tg.texts(OWNER), ["🛡 <b>Шлюз</b>\n🔴 Выход «Exit-1» не работает\n\n"
                                                "🔴 Сервер: Диск / почти заполнен: осталось 1,2 ГБ (99% занято)"])

    def test_probes_hint_is_in_the_feed_and_not_pushed(self):
        # подсказка о пробах (A5 п. 10): одно событие host_loss без пуша — в ленте есть, в Telegram нет
        t = self.now
        hint = {"id": int(t * 1000), "ts": t, "kind": "host_loss", "text": "", "key": "gw_host.event.probes_hint",
                "args": {"check": "loss", "level": "na", "push": False, "addrs": ["192.0.2.1", "198.51.100.1"]}}
        self.live([hint])
        self.tick()
        self.tick()
        self.assertEqual(self.tg.texts(OWNER), [])
        feed = data.live_doc()["events"]
        self.assertEqual([(e["kind"], e["key"]) for e in feed], [("host_loss", "gw_host.event.probes_hint")])
        self.assertIn("Можно включить лёгкие пробы сети", live.event_text(feed[0], "ru"))
        self.assertEqual(self.stored()["gw_ts"], t)                      # бот его прошёл: позже не придёт

    def test_crit_warn_ok_three_messages_and_icons(self):
        t = self.now
        warn = {"key": "gw_host.find.disk.warn", "args": {"mount": "/", "gb": 8, "pct": 92}}
        self.live([hev("host_disk", t, push=True)])
        self.tick()
        self.live([hev("host_disk", t, push=True),
                   hev("host_disk", t + 1, key="gw_host.event.lower", level="warn", push=True, find=warn,
                       **{"from": "crit"})])
        self.tick()
        self.live([hev("host_disk", t + 2, key="gw_host.event.clear", level="ok", push=True, find=None,
                       **{"from": "warn"})])
        self.tick()
        self.assertEqual(self.tg.texts(OWNER),
                         ["🔴 Сервер: Диск / почти заполнен: осталось 1,2 ГБ (99% занято)",
                          "🟡 Сервер: стало легче — На диске / осталось 8 ГБ (92% занято)",
                          "🟢 Сервер: снова в норме — Место на диске"])
        from kaskad.bot import monitor
        na = hev("host_disk", t, key="gw_host.event.na", level="na", why={"key": "gw_host.why.no_gateway",
                                                                          "args": {}})
        self.assertEqual(monitor.gw_icon(na), "⚪")
        self.assertEqual(monitor.gw_icon(hev("host_cert", t, level="bad")), "⚪")      # чужой уровень — не цвет

    def test_without_push_by_the_switch(self):
        # старый писатель без args.push — по переключателю вида, как прочие виды
        t = self.now
        old = hev("host_disk", t)
        old["args"].pop("push", None)
        self.switches(host_disk=False)
        self.live([old])
        self.tick()
        self.assertEqual(self.tg.texts(OWNER), [])
        self.switches()
        old2 = dict(old, ts=t + 1, id=old["id"] + 1)
        self.live([old, old2])
        self.tick()
        self.assertEqual(len(self.tg.texts(OWNER)), 1)
        self.switches(host_disk=False)                                   # args.push решает и против переключателя:
        self.live([hev("host_disk", t + 2, push=True)])                  # hostmon его уже учёл (§2.1)
        self.tick()
        self.assertEqual(len(self.tg.texts(OWNER)), 2)

    def test_held_while_the_node_is_offline(self):
        # удержание при node_offline — общее: событие узла с push = true ждёт и считается в «За это время»,
        # push = false — не считается
        t = self.now
        self.live([ev("node_offline", t, since=t), hev("host_disk", t + 5, push=True),
                   hev("host_mem", t + 6, push=False, check="mem")], node_offline=True)
        self.tick()
        self.assertEqual(self.gw_texts(), [])
        self.live([ev("node_offline", t, since=t), hev("host_disk", t + 5, push=True),
                   hev("host_mem", t + 6, push=False, check="mem"),
                   ev("node_online", t + 600, since=t, until=t + 600, minutes=10)], node_offline=False)
        self.tick()
        self.assertEqual(len(self.gw_texts()), 1)
        self.assertIn("За это время: сервер: место на диске — 1", self.gw_texts()[0])
        self.assertNotIn("память", self.gw_texts()[0])

    def test_node_lines_carried_over_an_outage_keep_their_place(self):
        # REV-ns-a56-2 A6 П-2: строка узла из такта, где сервер вернулся и снова пропал, ждёт конца новой пропажи и
        # приходит в том же сообщении — после строк шлюза, без «🛡 Шлюз» над собой
        t = self.now
        self.live([ev("node_online", t, since=t - 600, until=t, minutes=10), hev("host_disk", t, push=True),
                   ev("node_offline", t, since=t),
                   ev("node_online", t + 600, since=t, until=t + 600, minutes=10)], node_offline=False)
        self.tick()
        texts = self.tg.texts(OWNER)
        self.assertEqual(len(texts), 1, texts)
        self.assertTrue(texts[0].startswith("🛡 <b>Шлюз</b>\n📶 "), texts[0])
        self.assertTrue(texts[0].endswith("\n\n🔴 Сервер: Диск / почти заполнен: осталось 1,2 ГБ (99% занято)"),
                        texts[0])

    def test_kinds_switches_and_texts(self):
        from kaskad.bot import monitor
        from kaskad.gw import hostmon, model
        self.assertEqual(monitor.HOST_KINDS, hostmon.HOST_KINDS)
        self.assertIs(monitor._push_default(), model.NOTIFY_PUSH)    # one source of the defaults (T7)
        for kind in hostmon.HOST_KINDS:
            self.assertIs(model.NOTIFY_PUSH[kind], True, kind)
            self.assertIs(monitor._push_default()[kind], True, kind)
            self.assertIn(kind, monitor.GW_ICONS)
            for lang in ("ru", "en"):
                self.assertIn("bot.mon.gw_" + kind, i18n.catalog(lang))
        for lang in ("ru", "en"):
            self.assertEqual(sorted(k for k in i18n.catalog(lang) if k.startswith("bot.mon.gw_host_")),
                             sorted("bot.mon.gw_" + k for k in hostmon.HOST_KINDS))

    def test_every_kind_of_notify_push_has_a_label_in_the_panel(self):
        # §10.1 п. 7 (REV З-8): подписи видов узла — в A6, вместе с видами в NOTIFY_PUSH. Карточка «Уведомления»
        # (T8) берёт подпись вида по ключу nt.k.<вид>; виды выходов подписывает T8 — здесь виды узла
        from kaskad.gw import hostmon, model
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for lang in ("ru", "en"):
            with open(os.path.join(root, "lib", "kaskad", "panel", "static", "i18n-%s.js" % lang),
                      encoding="utf-8") as f:
                js = f.read()
            have = dict(re.findall(r'^  "(nt\.k\.[a-z_]+)": "([^"]+)"', js, flags=re.M))
            kinds = [k for k in model.NOTIFY_PUSH if k.startswith("host_")]
            self.assertEqual(sorted(kinds), sorted(hostmon.HOST_KINDS))
            for kind in kinds:
                self.assertTrue(have.get("nt.k." + kind), (lang, kind))
                self.assertEqual(bool(CYR.search(have["nt.k." + kind])), lang == "ru", (lang, kind))
            self.assertEqual(len(set(have["nt.k." + k] for k in kinds)), len(kinds))


class HostTickTest(MonitorCase):
    """Узел без шлюза (§3.9): host_tick раз в минуту — мгновенные правила, пуши, hostmon.json (owner: bot),
    /run/kaskad/host-health.json; при работающем сборщике бот ничего не читает и не пишет."""

    def setUp(self):
        super(HostTickTest, self).setUp()
        from unittest import mock
        from kaskad.gw import hostprobe
        from tests.gw import test_hostprobe as HP
        from tests.gw.test_collect_host import Vfs
        os.unlink(self.path("/etc/kaskad/monitors/%s.conf" % IP))
        self.mon.forget()
        hostprobe.reset()
        self.addCleanup(hostprobe.reset)
        self.disk = 50.0
        w = self.write
        w("/proc/stat", HP.STAT)
        w("/proc/loadavg", "0.10 0.20 0.30 1/100 1234\n")
        w("/proc/meminfo", HP.MEMINFO)
        w("/proc/vmstat", "nr_free_pages 1\noom_kill 0\n")
        w("/proc/net/dev", HP.NETDEV)
        w("/proc/sys/net/netfilter/nf_conntrack_count", "100\n")
        w("/proc/sys/net/netfilter/nf_conntrack_max", "1000\n")
        w("/proc/sys/fs/file-nr", "2048\t0\t9223372036854775807\n")
        w("/proc/uptime", "100000.50 7000.00\n")
        for p in (mock.patch.object(hostprobe, "_statvfs", lambda path: Vfs(self.disk)),
                  mock.patch.object(hostprobe, "clock", lambda: {"synced": True, "offset_s": 0.001}),
                  mock.patch.object(hostprobe, "_certs", lambda cfg: [])):
            p.start()
            self.addCleanup(p.stop)
        self.mono = 5000.0

    def minute(self, n=1, mon=None):
        mon = mon or self.mon
        out = None
        for _ in range(n):
            out = mon.host_tick(self.now, self.mono)
            self.now += 60
            self.mono += 60
        return out

    def gateway(self, on, engine="nft"):
        st = state.load()
        st["engine"] = engine
        st.setdefault("gateway", {})["enabled"] = on
        state.save(st)

    def health_file(self):
        return json.loads(self.read("/run/kaskad/host-health.json"))

    def hostmon_file(self):
        return json.loads(self.read("/var/lib/kaskad/hostmon.json"))

    def test_collector_works_nothing_read_or_written(self):
        from unittest import mock
        from kaskad.gw import hostprobe
        self.gateway(True)
        with mock.patch.object(hostprobe, "read", mock.Mock(side_effect=AssertionError("read"))):
            self.assertIs(self.minute(3), False)
        self.assertFalse(os.path.exists(self.path("/run/kaskad/host-health.json")))
        self.assertFalse(os.path.exists(self.path("/var/lib/kaskad/hostmon.json")))
        self.assertEqual(self.tg.texts(OWNER), [])

    def test_disk_crit_once_restart_quiet_clear_green(self):
        self.gateway(False)
        self.assertIs(self.minute(), True)
        self.assertEqual(self.tg.texts(OWNER), [])
        hf = self.health_file()
        self.assertEqual((hf["health"]["by"], hf["health"]["instant"], hf["health"]["level"]), ("bot", True, "ok"))
        self.disk = 99.0
        self.minute()
        self.assertEqual(self.tg.texts(OWNER), ["🔴 Сервер: Диск / почти заполнен: осталось 1 ГБ (99% занято)"])
        self.assertEqual(self.health_file()["health"]["level"], "crit")
        doc = self.hostmon_file()
        self.assertEqual((doc["owner"], doc["rules"]["disk"]["said"], doc["rules"]["disk"]["pushed"]),
                         ("bot", "crit", "crit"))
        self.assertEqual(os.stat(self.path("/var/lib/kaskad/hostmon.json")).st_mode & 0o777, 0o600)
        self.minute(3)                                                   # повтор через минуту — без пуша
        self.assertEqual(len(self.tg.texts(OWNER)), 1)
        from kaskad.bot.monitor import Monitor                           # перезапуск бота: файл, а не новый пуш
        mon2 = Monitor(self.app, sync=True)
        self.minute(2, mon2)
        self.assertEqual(len(self.tg.texts(OWNER)), 1)
        self.disk = 50.0
        self.mono += 16 * 60                                             # окно антиспама прошло
        self.minute(1, mon2)
        self.assertEqual(self.tg.texts(OWNER)[-1], "🟢 Сервер: снова в норме — Место на диске")
        self.assertEqual(self.hostmon_file()["rules"]["disk"]["pushed"], None)

    def test_warn_without_alarm_is_not_pushed(self):
        # REV-ns-a5 С-1: у диска тревога — crit; warn при host_level = crit не шлётся, при warn — шлётся
        self.gateway(False)
        self.disk = 96.0                                                 # 4 ГБ из 100: warn (> 90 % и < 5 ГБ)
        self.minute(2)
        self.assertEqual(self.tg.texts(OWNER), [])
        self.assertEqual(self.health_file()["health"]["level"], "warn")
        st = state.load()
        st.setdefault("gateway", {})["notify"] = {"host_level": "warn"}
        state.save(st)
        self.disk = 99.0
        self.minute()
        self.disk = 50.0
        self.mono += 16 * 60
        self.minute()
        self.assertEqual(len(self.tg.texts(OWNER)), 2)                   # crit и снятие

    def test_gateway_off_on_off_reads_the_collectors_file(self):
        # REV-2 З-6: второй период бот берёт said/pushed из файла сборщика — снятие, которое прислал сборщик,
        # не повторяется, старая тревога бота не возвращается
        self.gateway(False)
        self.disk = 99.0
        self.minute()
        self.assertEqual(len(self.tg.texts(OWNER)), 1)
        self.gateway(True)
        self.assertIs(self.minute(), False)
        self.assertIsNone(self.mon.host.state)                           # между периодами — ничего в памяти
        doc = self.hostmon_file()                                        # сборщик снял тревогу и прислал 🟢
        doc["rules"]["disk"].update(said="ok", pushed=None, pending=None, last_ev="clear", quiet_until_wall=None)
        doc["owner"] = "collector"
        self.write("/var/lib/kaskad/hostmon.json", json.dumps(doc))
        self.disk = 50.0
        self.gateway(False)
        self.minute(3)
        self.assertEqual(len(self.tg.texts(OWNER)), 1)
        self.assertEqual(self.hostmon_file()["owner"], "bot")
        self.disk = 99.0                                                 # новая тревога — снова сразу
        self.minute()
        self.assertEqual(len(self.tg.texts(OWNER)), 2)

    def test_engine_not_nft_with_the_gateway_on(self):
        self.gateway(True, engine="legacy")                              # сборщика нет — хозяин бот
        self.disk = 99.0
        self.assertIs(self.minute(), True)
        self.assertEqual(len(self.tg.texts(OWNER)), 1)

    def test_switch_off_and_hostmon_off_and_no_chat(self):
        self.gateway(False)
        st = state.load()
        st.setdefault("gateway", {})["notify"] = {"push": {"host_disk": False}}
        state.save(st)
        self.disk = 99.0
        self.minute()
        self.assertEqual(self.tg.texts(OWNER), [])                       # переключатель вида
        self.assertEqual(self.hostmon_file()["rules"]["disk"]["said"], "crit")
        st = state.load()
        st["gateway"]["hostmon"] = {"enabled": False}
        state.save(st)
        os.unlink(self.path("/run/kaskad/host-health.json"))
        from unittest import mock
        from kaskad.gw import hostprobe
        with mock.patch.object(hostprobe, "read", mock.Mock(side_effect=AssertionError("read"))):
            self.assertIs(self.minute(), True)                           # выключено: хозяин тот же, ничего не читает
        self.assertEqual(self.tg.texts(OWNER), [])                       # переключатель вида — и для «na off»
        self.assertEqual(self.health_file()["health"]["level"], "off")
        self.assertIsNone(self.hostmon_file()["rules"]["disk"]["said"])
        os.unlink(self.path("/run/kaskad/host-health.json"))
        st["gateway"]["hostmon"] = {}
        state.save(st)
        self.write_config(BOT_CHAT_ID="")                                # бот не настроен — слать некуда
        self.app.cfg.chat_id = None
        self.assertIs(self.minute(), False)
        self.assertFalse(os.path.exists(self.path("/run/kaskad/host-health.json")))

    def test_hostmon_off_with_an_open_alarm_as_the_collector(self):
        # REV-ns-a6 З-2: выключили проверку при открытой тревоге — одно «не проверяется» и снятие (said = null), как
        # у сборщика; включили снова при той же беде — новая тревога, а не тишина и не запоздалое 🟢
        from unittest import mock
        from kaskad.gw import hostprobe
        self.gateway(False)
        self.disk = 99.0
        self.minute()
        self.assertEqual(len(self.tg.texts(OWNER)), 1)
        st = state.load()
        st["gateway"]["hostmon"] = {"enabled": False}
        state.save(st)
        with mock.patch.object(hostprobe, "read", mock.Mock(side_effect=AssertionError("read"))):
            self.assertIs(self.minute(3), True)
        texts = self.tg.texts(OWNER)
        self.assertEqual(len(texts), 2, texts)
        self.assertTrue(texts[1].startswith("⚪ Сервер: Место на диске — не проверяется"), texts[1])
        doc = self.hostmon_file()
        self.assertEqual((doc["owner"], doc["rules"]["disk"]["said"], doc["rules"]["disk"]["pushed"]),
                         ("bot", None, None))
        self.assertEqual(self.health_file()["health"]["level"], "off")
        st["gateway"]["hostmon"] = {}
        state.save(st)
        self.mono += 16 * 60
        real, every = hostprobe.read, []
        with mock.patch.object(hostprobe, "read", lambda ctx, mono, ev: every.append(list(ev)) or real(ctx, mono, ev)):
            self.minute()
        self.assertEqual(every, [["minute", "hour"]])                    # REV-ns-a56-2 A6 П-2: часовой набор сразу
        self.assertEqual(len(self.tg.texts(OWNER)), 3)
        self.assertTrue(self.tg.texts(OWNER)[2].startswith("🔴 Сервер: Диск /"), self.tg.texts(OWNER)[2])

    def test_a_file_not_written_never_loses_the_alert(self):
        # REV-ns-a6 С-1: диск и иноды заполнены — hostmon.json и host-health.json не пишутся, а 🔴 всё равно приходит
        # (затем 🟢); в журнале — одна строка на смену ошибки, не трассировка каждую минуту
        import errno
        from unittest import mock
        from kaskad import util
        self.gateway(False)
        self.minute()
        real = util.atomic_write

        def full(path, data, mode=0o600):
            if path.endswith(("hostmon.json", "host-health.json")):
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(path, data, mode)
        errs, notes = [], []
        self.app._worker_error = errs.append
        self.disk = 99.0
        from kaskad.bot import hostwatch
        last = self.app.last_error
        with mock.patch.object(util, "atomic_write", full), \
                mock.patch.object(hostwatch.log, "warning", notes.append):
            self.minute(3)
        self.assertEqual(self.tg.texts(OWNER), ["🔴 Сервер: Диск / почти заполнен: осталось 1 ГБ (99% занято)"])
        self.assertEqual(errs, [])
        self.assertEqual(len(notes), 2, notes)                           # по строке на файл, не на минуту
        self.assertTrue(all("ENOSPC" not in n and "OSError" in n for n in notes), notes)
        # REV-ns-a56-2 A6 П-1: только журнал, текст из каталога; не «последняя ошибка» связи с Telegram
        self.assertEqual(sorted(n.split(":")[0] for n in notes),
                         ["host-health.json не записан", "hostmon.json не записан"])
        self.assertEqual(self.app.last_error, last)
        self.assertEqual(self.hostmon_file()["rules"]["disk"]["said"], "ok")  # решение не записалось
        self.minute()                                                    # запись снова работает
        self.assertEqual(self.hostmon_file()["rules"]["disk"]["said"], "crit")
        self.assertEqual(len(self.tg.texts(OWNER)), 1)
        self.disk = 50.0
        self.mono += 16 * 60
        self.minute()
        self.assertEqual(self.tg.texts(OWNER)[-1], "🟢 Сервер: снова в норме — Место на диске")
        with mock.patch.object(util, "atomic_write", full), \
                mock.patch.object(hostwatch.log, "warning", notes.append):
            self.minute()
        self.assertEqual(len(notes), 3, notes)                           # новая беда после успеха — снова в журнале
        self.assertIn("host-health.json", notes[2])                      # (hostmon.json не менялся — не писался)

    def test_the_collectors_mark_of_the_probes_hint_is_kept(self):
        # A5 п. 10: подсказка о пробах — одна; бот пишет файл с отметкой сборщика как она была
        self.gateway(False)
        self.minute()
        doc = self.hostmon_file()
        doc.update(owner="collector", probes_hint=1700000000)
        self.gateway(True)
        self.minute()
        self.write("/var/lib/kaskad/hostmon.json", json.dumps(doc))
        self.gateway(False)
        self.disk = 99.0                                                 # смена уровня — файл пишется сразу
        self.minute()
        doc = self.hostmon_file()
        self.assertEqual((doc["owner"], doc["probes_hint"], doc["rules"]["disk"]["said"]), ("bot", 1700000000, "crit"))

    def test_a_broken_file_goes_aside(self):
        from unittest import mock
        from kaskad import util
        self.gateway(False)
        self.write("/var/lib/kaskad/hostmon.json", "{not json")
        with mock.patch.object(util, "log_tr") as log:
            self.assertIs(self.minute(), True)
        self.assertEqual(self.read("/var/lib/kaskad/hostmon.json.bad"), "{not json")
        self.assertEqual([c.args[0] for c in log.call_args_list], ["gw_host.log.state_bad"])
        self.disk = 99.0
        self.minute()
        self.assertEqual(self.hostmon_file()["owner"], "bot")
        self.assertEqual(len(self.tg.texts(OWNER)), 1)

    def test_an_alert_older_than_an_hour_is_not_sent(self):
        # повтор неотправленного — не дольше часа (SEND_MAX_AGE = monitor.GW_MAX_AGE)
        from kaskad.bot import hostwatch
        self.gateway(False)
        self.disk = 99.0
        self.tg.fail("sendMessage", 502, "Bad Gateway")
        self.minute()
        self.assertEqual(self.tg.texts(OWNER), [])
        self.now += hostwatch.SEND_MAX_AGE
        self.mono += hostwatch.SEND_MAX_AGE
        self.minute()
        self.assertEqual(self.tg.texts(OWNER), [])
        self.assertEqual(self.mon.host.unsent, [])

    def test_the_health_file_is_0600(self):
        self.gateway(False)
        self.minute()
        self.assertEqual(os.stat(self.path("/run/kaskad/host-health.json")).st_mode & 0o777, 0o600)

    def test_an_error_is_the_threads_and_the_loop_lives(self):
        from unittest import mock
        from kaskad.gw import hostprobe
        self.gateway(False)
        errs = []
        self.app._worker_error = errs.append
        with mock.patch.object(hostprobe, "read", mock.Mock(side_effect=RuntimeError("boom"))):
            self.assertIsNone(self.minute())
        self.assertEqual([str(e) for e in errs], ["boom"])
        self.disk = 99.0
        self.assertIs(self.minute(), True)
        self.assertEqual(len(self.tg.texts(OWNER)), 1)

    def test_unsent_is_tried_again(self):
        self.gateway(False)
        self.disk = 99.0
        self.tg.fail("sendMessage", 502, "Bad Gateway")
        self.minute()
        self.assertEqual(self.tg.texts(OWNER), [])
        self.minute()
        self.assertEqual(self.tg.texts(OWNER), ["🔴 Сервер: Диск / почти заполнен: осталось 1 ГБ (99% занято)"])

    def test_tick_calls_it_once_a_minute(self):
        from unittest import mock
        from kaskad.bot import monitor
        self.gateway(False)
        calls = []
        with mock.patch.object(self.mon, "host_tick", lambda now, mono=None: calls.append(now)):
            self.tick()
            self.assertEqual(calls, [])                                  # первая минута после старта
            with mock.patch.object(monitor, "HOST_EVERY", 0.0):
                self.tick()
            self.assertEqual(len(calls), 1)
