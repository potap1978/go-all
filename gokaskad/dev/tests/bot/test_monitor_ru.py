"""T15 Q5 (SPEC-groups §10, SE §8.1–8.2): the pushes of the Russian groups.

* the words of a Russian exit and group — «Российский выход «Роутер» (группа «Дом») недоступен», ««Дом»: российские
  сайты переключились …», the last step «напрямую» with the banks, «заблокированы»/«закрыты», the country; RU and EN,
  no Cyrillic in EN, no raw key, no «{…}» left;
* ``node_ru_offline`` is not sent at once: the summary ``node_ru_online`` «Сервер не доставал до российских адресов с …
  до …» (its switch — ``node_ru_offline``, on by default); an outage still going on after 15 min — said ONCE while it
  lasts, never twice after a restart of the bot, never while the node itself is offline;
* the antispam of the group (the layer of events, ``group:<id>`` + kind): a repeat in 15 min is held, the other kind
  goes at once, the end of the window tells the state now — the same for a Russian group;
* the feed and the bot — one rule: the feed keeps ``node_ru_offline`` while the outage is open, the summary replaces
  it when it closes.
"""

import re

from kaskad.gw import live, model
from tests.bot.fake_tg import OWNER
from tests.bot.test_monitor import S1PushCase, ev, hhmm

CYR = re.compile(r"[А-Яа-яЁё]")
RU = dict(group="g-0a0b0c", group_name="Дом", role="ru")


def texts(e):
    return live.event_text(e, "ru"), live.event_text(e, "en")


class RuWordsTest(S1PushCase):
    def check_en(self, e):
        ru, en = texts(e)
        self.assertFalse(CYR.search(en.replace("Роутер", "").replace("Дом", "").replace("Дача", "")), en)
        for t in (ru, en):
            self.assertNotIn("{", t)
            self.assertNotIn("gw_live.", t)
        return ru, en

    def test_the_words(self):
        t = self.now
        cases = [
            (ev("exit_dead", t, name="Роутер", **RU), "Внутренний выход «Роутер» (группа «Дом») недоступен"),
            (ev("exit_dead", t, key="gw_live.event.exit_dead_n", name="Роутер", n=3, **RU),
             "Внутренний выход «Роутер» (группа «Дом») недоступен (обрывов за 15 мин: 3)"),
            (ev("exit_removed", t, name="Роутер", why="dead", **RU), "Внутренний выход «Роутер» (группа «Дом») недоступен"),
            (ev("exit_alive", t, name="Роутер", **RU), "Внутренний выход «Роутер» (группа «Дом») снова работает"),
            (ev("exit_failover", t, from_tier="Роутер", to_tier="Дача", moved=3, **RU),
             "«Дом»: российские сайты переключились с «Роутер» на «Дача» — переехали 3 устройства"),
            (ev("exit_failover", t, to_tier="Роутер", moved=3, back=True, **RU),
             "«Дом»: российские сайты снова идут через «Роутер» (3 устройства)"),
            (ev("exit_country_changed", t, name="Роутер", old="RU", new="FI", **RU),
             "Внутренний выход «Роутер» (группа «Дом»): сменилась страна — было RU, стало FI; трафик идёт как прежде"),
            (ev("group_last_step", t, name="Дом", step="direct", devices=4, **RU),
             "«Дом»: все выходы недоступны — российские сайты у 4 устройств открываются напрямую с адреса сервера; "
             "банки могут попросить подтвердить вход"),
            (ev("group_last_step", t, name="Дом", step="block", devices=1, **RU),
             "«Дом»: все выходы недоступны — российские сайты закрыты у 1 устройства"),
            (ev("group_restored", t, name="Дом", exit="e-1", exit_name="Роутер", after_s=420, **RU),
             "«Дом»: снова через «Роутер» (через 7 мин)"),
            (ev("node_ru_offline", t, since=t),
             "Сервер не достаёт до российских адресов с %s; внутренние выходы не переключаются, пока связь не "
             "вернётся" % hhmm(t)),
            (ev("node_ru_online", t, since=t - 420, until=t, minutes=7),
             "Сервер не доставал до российских адресов с %s до %s (7 мин)" % (hhmm(t - 420), hhmm(t))),
        ]
        for e, want in cases:
            with self.subTest(kind=e["kind"]):
                ru, _en = self.check_en(e)
                self.assertEqual(ru, want)

    def test_the_numbers(self):
        """n = 1, 2, 5, 21 in both languages (the owner's rule of the texts)."""
        t = self.now
        want = {1: ("переехало 1 устройство", "1 device moved", "у 1 устройства", "for 1 device"),
                2: ("переехали 2 устройства", "2 devices moved", "у 2 устройств", "for 2 devices"),
                5: ("переехали 5 устройств", "5 devices moved", "у 5 устройств", "for 5 devices"),
                21: ("переехало 21 устройство", "21 devices moved", "у 21 устройства", "for 21 devices")}
        for n, (ru_m, en_m, ru_d, en_d) in sorted(want.items()):
            with self.subTest(n=n):
                ru, en = self.check_en(ev("exit_failover", t, from_tier="Роутер", to_tier="Дача", moved=n, **RU))
                self.assertTrue(ru.endswith(ru_m), ru)
                self.assertTrue(en.endswith(en_m), en)
                ru, en = self.check_en(ev("group_last_step", t, name="Дом", step="direct", devices=n, **RU))
                self.assertIn(ru_d, ru)
                self.assertIn(en_d, en)

    def test_foreign_groups_keep_their_words(self):
        """The words of a foreign group did not change (3.3.5 and T9)."""
        t = self.now
        ab = dict(group="g-0d0e0f", group_name="Стриминг", role="abroad")
        self.assertEqual(texts(ev("exit_dead", t, name="Вена", **ab))[0], "«Стриминг»: выход «Вена» не работает")
        self.assertEqual(texts(ev("exit_failover", t, from_tier="A", to_tier="B", moved=2, **ab))[0],
                         "«Стриминг»: переход с уровня «A» на «B»: переехали 2 устройства")
        self.assertEqual(texts(ev("group_last_step", t, name="Стриминг", step="block", devices=2, **ab))[0],
                         "«Стриминг»: все выходы недоступны — нет выхода в интернет у 2 устройств")
        self.assertEqual(texts(ev("exit_dead", t, name="Вена"))[0], "Выход «Вена» не работает")

    def test_examples_and_no_jargon(self):
        """No «.ru» domain in the examples of the new words, no internal words (ru_offline, gr-, kawg, tier)."""
        import json
        import os
        root = os.path.join(os.path.dirname(live.__file__), "..", "i18n", "catalog")
        cat = {}
        for lang in ("ru", "en"):
            with open(os.path.join(root, lang, "gw_live.json"), encoding="utf-8") as f:
                cat[lang] = json.load(f)
        keys = ["gw_live.event.exit_dead_ru", "gw_live.event.exit_alive_ru", "gw_live.event.exit_failover_ru",
                "gw_live.event.exit_failover_ru_back", "gw_live.event.exit_country_changed_ru",
                "gw_live.event.group_last_step_ru_direct", "gw_live.event.node_ru_offline",
                "gw_live.event.node_ru_online", "gw_live.event.node_ru_online_restart"]
        for k in keys:
            for lang in ("ru", "en"):
                raw = cat[lang][k]
                for bad in (".ru", "ru_offline", "gr-", "kawg", "tier", "probe", "проб"):
                    self.assertNotIn(bad, raw, (k, lang))


class RuLevelWordsTest(S1PushCase):
    def test_the_push_names_the_key_not_the_level_id(self):
        """``ru_home``: each key its own level — the real chain assign → the layer of events → the text names the keys
        («Роутер» → «Дача»), never the ids «t1»/«t2»; the owner's name of a level wins; back — the key again."""
        from kaskad.gw import assign as A
        from tests.gw.test_assign import E1, E2, devs, doc
        d = doc([E1], [E2], role="ru")
        gid = d["groups"][0]["id"]
        ds = devs(3)
        d1 = A.assign({gid: ds}, {E1: "ok", E2: "ok"}, d, None, {}, {}, 0)
        d2 = A.assign({gid: ds}, {E1: "off", E2: "ok"}, d, d1, {}, {}, 10)
        d3 = A.assign({gid: ds}, {E1: "ok", E2: "ok"}, d, d2, {}, {}, 20)
        d3 = A.assign({gid: ds}, {E1: "ok", E2: "ok"}, d, d3, {}, {}, 20 + 3600)   # the primary good for hold_min
        names, gl = {E1: "Роутер", E2: "Дача"}, {gid: "Дом"}
        lv = live.Live()
        for dec, t in ((d2, 1000), (d3, 2000)):
            lv.publish([e for e in dec["events"] if e["kind"] == "exit_failover"], names, gl, {}, t,
                       notify={"push": {"coalesce": False}})
        said = [live.event_text(e, "ru") for e in lv.events if e["kind"] == "exit_failover"]
        self.assertEqual(said, ["«Дом»: российские сайты переключились с «Роутер» на «Дача» — переехали 3 устройства",
                                "«Дом»: российские сайты снова идут через «Роутер» (3 устройства)"])
        for e in lv.events:
            for lang in ("ru", "en"):
                self.assertNotRegex(live.event_text(e, lang), r"«t\d»|“t\d”")
        d["groups"][0]["tiers"][1]["name"] = "Дача у мамы"
        d4 = A.assign({gid: ds}, {E1: "off", E2: "ok"}, d, d3, {}, {}, 20 + 3610)
        lv.publish([e for e in d4["events"] if e["kind"] == "exit_failover"], names, gl, {}, 3000,
                   notify={"push": {"coalesce": False}})
        self.assertIn("на «Дача у мамы»", live.event_text(lv.events[-1], "ru"))


class RuOutagePushTest(S1PushCase):
    def test_a_short_outage_is_one_summary(self):
        t = self.now
        self.assertTrue(model.NOTIFY_PUSH["node_ru_offline"])
        down = [ev("node_ru_offline", t, since=t), ev("exit_dead", t + 5, name="Вена")]
        self.live(down)
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n🔴 Выход «Вена» не работает"])   # the rest goes as usual
        self.live(down + [ev("node_ru_online", t + 300, since=t, until=t + 300, minutes=5)])
        self.tick()
        self.assertEqual(self.gw_texts()[-1], "🛡 <b>Шлюз</b>\n📶 Сервер не доставал до российских адресов с %s до %s "
                                              "(5 мин)" % (hhmm(t), hhmm(t + 300)))
        self.now += 3600
        self.tick()
        self.assertEqual(len(self.gw_texts()), 2)                         # no hold of a closed outage

    def test_a_blip_of_one_round_is_silence(self):
        # REV-3379 П-3: one failed round (10 s) gave «📶 … (0 мин)»; under a minute — no summary, the feed keeps it
        t = self.now
        self.live([ev("node_ru_offline", t, since=t), ev("node_ru_online", t + 10, since=t, until=t + 10, minutes=0)])
        self.tick()
        self.assertEqual(self.gw_texts(), [])
        self.now = t + 3600
        self.tick()
        self.assertEqual(self.gw_texts(), [])                             # nor a hold later
        self.live([ev("node_ru_offline", t, since=t), ev("node_ru_online", t + 10, since=t, until=t + 10, minutes=0),
                   ev("node_ru_online", t + 4000, minutes=0)])            # an older writer: minutes only
        self.now = t + 4000
        self.tick()
        self.assertEqual(self.gw_texts(), [])

    def test_a_long_outage_is_said_once_while_it_lasts(self):
        t = self.now
        self.live([ev("node_ru_offline", t, since=t)])
        self.tick()
        self.assertEqual(self.gw_texts(), [])
        self.now = t + 15 * 60 - 20
        self.tick()
        self.assertEqual(self.gw_texts(), [])                             # under 15 min: nothing yet
        self.now = t + 15 * 60 + 5
        self.tick()
        self.assertEqual(self.gw_texts(), ["🛡 <b>Шлюз</b>\n🚧 Сервер не достаёт до российских адресов с %s; внутренние "
                                           "выходы не переключаются, пока связь не вернётся" % hhmm(t)])
        self.tick()
        self.make_app(monitor=True)                                       # a restart of the bot: said already
        self.mon = self.app.monitor
        self.mon.tick(self.now + 60)
        self.assertEqual(len(self.gw_texts()), 1)
        self.live([ev("node_ru_offline", t, since=t), ev("node_ru_online", t + 1800, since=t, until=t + 1800, minutes=30)])
        self.now = t + 1800
        self.tick()
        self.assertEqual(self.gw_texts()[-1].split("\n")[-1],
                         "📶 Сервер не доставал до российских адресов с %s до %s (30 мин)" % (hhmm(t), hhmm(t + 1800)))

    def test_the_switch_and_the_whole_outage(self):
        t = self.now
        self.switches(node_ru_offline=False)
        self.live([ev("node_ru_offline", t, since=t)])
        self.now = t + 1000
        self.tick()
        self.live([ev("node_ru_offline", t, since=t), ev("node_ru_online", t + 1100, since=t, until=t + 1100)])
        self.now = t + 1100
        self.tick()
        self.assertEqual(self.gw_texts(), [])                             # off: no hold, no summary
        self.switches(node_ru_offline=True)
        t2 = self.now + 10
        self.live([ev("node_ru_offline", t2, since=t2)], node_offline=True)
        self.now = t2 + 1000
        self.tick()
        self.assertEqual(self.gw_texts(), [])                             # the node offline: the bot holds all

    def test_the_feed_keeps_the_open_start_and_shows_the_summary(self):
        t = self.now
        evs = [{"kind": "node_ru_offline", "id": 1, "ts": t}]
        self.assertEqual([e["kind"] for e in live.feed_events(evs)], ["node_ru_offline"])
        evs.append({"kind": "node_ru_online", "id": 2, "ts": t + 60})
        self.assertEqual([e["kind"] for e in live.feed_events(evs)], ["node_ru_online"])


class RuGroupAntispamTest(S1PushCase):
    def test_group_and_kind(self):
        """SE §8.2 for a Russian group: a repeat of ``group_last_step`` in 15 min is held, ``group_restored`` after
        it goes at once, the end of the window tells the state now (the step again) — by ``group:<id>``."""
        lv = live.Live()
        dec = {"groups": {"g-0a0b0c": {"last_step": "direct", "steps": {"d-1": "last_direct"}}},
               "policy": {"groups": [{"id": "g-0a0b0c", "role": "ru"}]}}
        last = dict(kind="group_last_step", group="g-0a0b0c", role="ru", step="direct", devices=1)
        back = dict(kind="group_restored", group="g-0a0b0c", role="ru", exit="e-1")
        names, gl = {"e-1": "Роутер"}, {"g-0a0b0c": "Дом"}
        lv.publish([last], names, gl, {}, 1000)
        lv.publish([back], names, gl, {}, 1060)
        lv.publish([last], names, gl, {}, 1120)                           # a repeat of the kind: held
        kinds = [e["kind"] for e in lv.events]
        self.assertEqual(kinds, ["group_last_step", "group_restored"])
        lv._window_end(names, {}, 1120 + live.EVENT_QUIET + 1, dec, glabels=gl)
        self.assertEqual([e["kind"] for e in lv.events][-1], "group_last_step")
        said = live.event_text(lv.events[-1], "ru")
        self.assertIn("российские сайты", said)
        self.assertIn("«Дом»", said)
