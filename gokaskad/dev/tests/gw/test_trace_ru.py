"""Q7 (SPEC-stage1-exit-groups §9, §13.1 «трассировка»): «Куда пойдёт трафик?» для российского адреса у человека с
российской группой. Шаги ``exit``, ``last_direct``, ``last_block`` и ``ru_offline``; поле ``ru`` ответа ({group, name,
step, exit, tier, since, offline, chain}); RU и EN. Группа «Дом»: уровень 1 — «Роутер» (AmneziaWG), уровень 2 —
«Дача» (VLESS)."""

import json
import os
import time
from unittest import mock

from kaskad import i18n, paths, state
from kaskad.gw import exits, live, trace
from kaskad.gw import policy as P
from tests.fakes_gw import awg_line, install_parser, write_ru_pool
from tests.gw.test_trace import D1, DNS, TraceCase, fake_dns

HOME = "g-0a0b0c"
DACHA = ("vless://aaaaaaaa-1111-2222-3333-444444444444@203.0.113.32:443?type=tcp&security=reality"
         "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#Dacha")
RU_SITE = "mail.yandex.ru"


class RuTraceCase(TraceCase):
    def ru_world(self, fail="direct", alive=(True, True), ru_via=HOME):
        st = self.world()
        install_parser()
        write_ru_pool([awg_line(31, name="Роутер", endpoint="203.0.113.31:51820", address="10.8.31.2/32"), DACHA])
        self.rids = [e["id"] for e in exits.pool(None, roles=("ru",))]
        doc = st["gateway"].get("exit_policy") or P.default_doc("standard")
        home = P.default_group(HOME, "ru", "ru_home", name="Дом")
        home["fail"] = fail
        home["tiers"] = [{"id": "t1", "name": "", "exits": [self.rids[0]]}, {"id": "t2", "name": "", "exits": [self.rids[1]]}]
        doc["groups"] = [g for g in doc["groups"] if g.get("id") != HOME] + [home]
        st["gateway"]["exit_policy"] = doc
        st["gateway"]["users"][0]["ru_via"] = ru_via
        snap = exits.load_snapshot()
        for rid, ok in zip(self.rids, alive):
            snap.setdefault("exits", {})[rid] = {"alive": ok, "state": "ok" if ok else "dead", "reasons": []}
        exits.save_snapshot(snap)
        state.save(st)
        return state.load()

    def live(self, no_exit=None, no_exit_ru=None, ru_since=None, age=5):
        """live.json as the collector writes it: ``no_exit`` / ``no_exit_ru`` of the tick, an open
        ``node_ru_offline``."""
        now = time.time()
        events = []
        if ru_since:
            events.append({"kind": "node_ru_offline", "ts": ru_since, "args": {"since": ru_since}})
        doc = {"v": live.V, "ts": now - age, "seq": 1, "no_exit": no_exit or {}, "no_exit_ru": no_exit_ru or {}, "events": events, "exits": {},
               "devices": {}, "policy": {"node_offline": False}}
        os.makedirs(os.path.dirname(paths.live_json()), exist_ok=True)
        with open(paths.live_json(), "w", encoding="utf-8") as f:
            json.dump(doc, f)


class RuTraceTest(RuTraceCase):
    def test_exit_now_its_tier_and_what_comes_next(self):
        st = self.ru_world()
        res = self.run_trace(st, D1, RU_SITE)
        self.assertEqual((res["verdict"], res["reason"]), ("exit", "ru_domain"))
        ru = res["ru"]
        self.assertEqual((ru["group"], ru["name"], ru["step"], ru["exit"], ru["tier"]),
                         (HOME, "Дом", "exit", self.rids[0], "Уровень 1"))
        self.assertEqual([c.get("name") or c.get("step") for c in ru["chain"]], ["Роутер", "Dacha", "direct"])
        self.assertEqual([c["now"] for c in ru["chain"]], [True, False, False])
        text = self.texts(res)
        self.assertIn("Российские сайты — через группу «Дом»: выход «Роутер»", text)
        self.assertIn("Уровень 1. Если недоступен: «Dacha» → с сервера напрямую.", text)
        self.assertNotIn("ждём связи", text)

    def test_the_second_tier_and_a_block_at_the_end(self):
        st = self.ru_world(fail="block", alive=(False, True))
        res = self.run_trace(st, D1, RU_SITE)
        self.assertEqual((res["ru"]["step"], res["ru"]["exit"], res["ru"]["tier"]), ("exit", self.rids[1], "Уровень 2"))
        self.assertIn("Уровень 2. Если недоступен: блок.", self.texts(res))

    def test_last_direct_says_since_when(self):
        st = self.ru_world(fail="direct", alive=(False, False))
        since = int(time.time()) - 600
        self.live(no_exit_ru={D1: {"step": "last_direct", "group": HOME, "since": since}})
        res = self.run_trace(st, D1, RU_SITE)
        self.assertEqual(res["verdict"], "direct")
        self.assertEqual((res["ru"]["step"], res["ru"]["since"]), ("last_direct", since))
        self.assertEqual(res["ru"]["chain"][-1], {"kind": "last", "step": "direct", "now": True})
        text = self.texts(res)
        self.assertIn("последний шаг — с сервера напрямую", text)
        self.assertIn("Выходы группы «Дом» недоступны с %s." % live._clock(since), text)

    def test_the_foreign_last_step_is_not_the_russian_one(self):
        st = self.ru_world(fail="direct", alive=(False, False))
        self.live(no_exit={D1: {"step": "last_block", "group": "main", "since": int(time.time()) - 600}})
        res = self.run_trace(st, D1, RU_SITE)
        self.assertIsNone(res["ru"]["since"])

    def test_last_block(self):
        st = self.ru_world(fail="block", alive=(False, False))
        res = self.run_trace(st, D1, RU_SITE)
        self.assertEqual((res["verdict"], res["reason"], res["ru"]["step"]), ("block", "ru_last_block", "last_block"))
        self.assertIn("Российские сайты закрыты", res["summary"])
        self.assertIsNone(res["ru"]["since"])                       # no fresh live.json — no time, no line

    def test_a_stale_live_json_gives_no_time(self):
        st = self.ru_world(fail="direct", alive=(False, False))
        self.live(no_exit_ru={D1: {"step": "last_direct", "group": HOME, "since": int(time.time()) - 600}}, age=3600)
        res = self.run_trace(st, D1, RU_SITE)
        self.assertIsNone(res["ru"]["since"])
        self.assertNotIn("недоступны с", self.texts(res))

    def test_ru_offline_the_node_waits(self):
        st = self.ru_world()
        since = int(time.time()) - 300
        self.live(ru_since=since)
        res = self.run_trace(st, D1, RU_SITE)
        self.assertTrue(res["ru"]["offline"])
        self.assertEqual(res["ru"]["step"], "exit")                 # the group is frozen: the exit as it was
        text = self.texts(res)
        self.assertIn("Сервер не достаёт до российских адресов с %s — ждём связи" % live._clock(since), text)
        self.assertIn("группа не переключает выходы", text)

    def test_without_a_russian_group_as_before(self):
        st = self.ru_world(ru_via="direct")
        res = self.run_trace(st, D1, RU_SITE)
        self.assertEqual((res["verdict"], res["reason"]), ("direct", "ru_domain"))
        self.assertEqual((res["ru"]["group"], res["ru"]["step"], res["ru"]["chain"]), (None, "direct", []))
        self.assertNotIn("«Дом»", self.texts(res))
        res = self.run_trace(st, D1, "8.8.8.8")                       # not a Russian address: no ``ru``
        self.assertIsNone(res["ru"])

    def test_russia_direct_not_acting_no_ru(self):
        st = self.ru_world()
        st["gateway"]["users"][0]["rules"] = "all_exit"
        state.save(st)
        res = self.run_trace(state.load(), D1, RU_SITE)
        self.assertIsNone(res["ru"])

    def test_english(self):
        st = self.ru_world(fail="direct", alive=(False, False))
        since = int(time.time()) - 600
        self.live(no_exit_ru={D1: {"step": "last_direct", "group": HOME, "since": since}}, ru_since=since)
        with i18n.use_lang("en"):
            res = self.run_trace(st, D1, RU_SITE)
            text = self.texts(res)
        self.assertIn("The exits of the group “Дом” have been down since", text)
        self.assertIn("has not reached Russian addresses since", text)
        for name in ("Дом", "Роутер", "Телефон", "Мама", "Тестовый вход"):          # names of the fixture
            text = text.replace(name, "")
        self.assertNotRegex(text, "[А-Яа-яЁё]")

    def test_the_bot_text_carries_the_lines(self):
        st = self.ru_world()
        res = self.run_trace(st, D1, RU_SITE)
        html = trace.text_html(res, lambda s: s.replace("<", "&lt;"))
        self.assertIn("Если недоступен: «Dacha» → с сервера напрямую.", html)

    def test_a_broken_live_json_changes_nothing(self):
        st = self.ru_world()
        os.makedirs(os.path.dirname(paths.live_json()), exist_ok=True)
        with open(paths.live_json(), "w", encoding="utf-8") as f:
            f.write("{broken")
        res = self.run_trace(st, D1, RU_SITE)
        self.assertEqual((res["ru"]["step"], res["ru"]["offline"]), ("exit", False))


class LiveRuTest(RuTraceCase):
    def test_reads_only_files(self):
        with mock.patch("kaskad.sysrun.default_runner", side_effect=AssertionError("no command")):
            self.assertEqual(trace._live_ru(), {"no_exit": {}, "ru_offline": None})


class PanelTraceRuTest(RuTraceCase):
    def test_the_panel_answers_as_the_cli(self):
        """Стенд it1 Н-3: «Куда пойдёт трафик?» панели (POST /api/gw/trace → ``scheme.trace_doc``) брал пул только из
        upstream.txt — российских выходов в нём нет, ``exits.decision`` не видел живого выхода «Дома» и отвечал «все
        выходы недоступны — с сервера напрямую» (``last_direct``), а CLI (``exits.current``: оба файла) — «через
        российский выход». Пользователь российской группы, российский сайт, живой выход «Дома» — один ответ."""
        from kaskad.panel import scheme
        st = self.ru_world()
        for c in (scheme._pool_cache, scheme._ru_pool_cache):
            c.clear()
        with mock.patch.object(trace, "resolve", fake_dns(DNS)):
            panel = scheme.trace_doc({"who": D1, "target": RU_SITE})
            trace._dns_cache.clear()
            cli = trace.trace(state.load(), D1, RU_SITE)
        self.assertEqual((panel["ru"]["step"], panel["ru"]["exit"]), ("exit", self.rids[0]))   # было last_direct, None
        self.assertEqual(panel["ru"], cli["ru"])
        self.assertEqual(trace.lines(panel), trace.lines(cli))
        self.assertTrue(all(e.get("role") == "ru" for e in scheme.ru_pool_exits()))
        self.assertTrue(all("line" not in e and "outbound" not in e for e in scheme.ru_pool_exits()))   # без секретов
