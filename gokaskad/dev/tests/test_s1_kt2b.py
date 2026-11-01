"""Находки стенда КТ2 (k3/stand/logs/T-KT2.md) по здоровью сервера и текстам: К2-3, К2-5, К2-6, К2-7 и придирка
сценария 5. Каждый тест краснеет на коде до правки (ca38c820)."""

import json
from unittest import mock

from kaskad import state
from kaskad.gw import collect, live
from kaskad.i18n import tr
from tests.test_gw_ops import CliGwCase


class NoPoolDirectTest(CliGwCase):
    """К2-5: при «при отказе — напрямую» и всех лежащих выходах «Что не в порядке» не пишет «заблокированы»."""

    def setUp(self):
        super().setUp()
        self._p = mock.patch.object(live, "_now", lambda: self.now)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def world(self, fail=None):
        from kaskad import compat
        from kaskad.gw import xray
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        if fail:
            did = state.load()["gateway"]["devices"][0]["id"]
            self.ok("gw", "device", "route", did, "pool", "--fail", fail)
        self.w.sysd.units[xray.UNIT]["active"] = True
        compat.ensure(self.w.runner)
        self.w.kill_leg(2)
        self.w.curl.observatory = {"gx-" + self.vid: False}
        for _ in range(4):
            collect.tick(self.w.runner)
            self.now += 10
        return json.loads(self.read("/run/kaskad/live.json"))

    def test_direct_says_direct(self):
        lv = self.world("direct")
        self.assertEqual(lv["no_exit"] and set(v["step"] for v in lv["no_exit"].values()), {"last_direct"})
        self.assertIn(tr("gw_live.no_pool_direct"), lv["gateway"]["problems"])
        self.assertNotIn(tr("gw_live.no_pool"), lv["gateway"]["problems"])
        self.assertIn("напрямую с адреса сервера", tr("gw_live.no_pool_direct"))

    def test_block_stays_blocked(self):
        lv = self.world("block")
        self.assertIn(tr("gw_live.no_pool"), lv["gateway"]["problems"])
        self.assertIn("заблокированы", tr("gw_live.no_pool"))

    def test_key_by_steps(self):
        d = lambda *steps: {"groups": {"main": {"steps": dict(("d%d" % i, s) for i, s in enumerate(steps)),
                                                "last_step": None}}}
        self.assertEqual(live.no_pool_key(d("last_direct", "last_direct")), "gw_live.no_pool_direct")
        self.assertEqual(live.no_pool_key(d("last_direct", "last_block")), "gw_live.no_pool_mixed")
        self.assertEqual(live.no_pool_key(d("last_block")), "gw_live.no_pool")
        self.assertEqual(live.no_pool_key(None), "gw_live.no_pool")
        self.assertEqual(live.no_pool_key({"groups": {"main": {"steps": {}, "last_step": "direct"}}}),
                         "gw_live.no_pool_direct")
        for lang in ("ru", "en"):
            for k in ("gw_live.no_pool_direct", "gw_live.no_pool_mixed"):
                self.assertNotEqual(tr(k, lang), k)


class DirectStepEventTest(CliGwCase):
    """К2-6: шаг «напрямую» сказан событием ``group_last_step`` и тогда, когда минуты назад сказан блок."""

    def setUp(self):
        super().setUp()
        self.L = live.Live()
        self.L._loaded = True
        self.mono = [100000.0]
        self.wall = [1790000000.0]
        for p in (mock.patch.object(live, "_mono", lambda: self.mono[0]),
                  mock.patch.object(live, "_now", lambda: self.wall[0])):
            p.start()
            self.addCleanup(p.stop)

    def at(self, minutes):
        self.mono[0] = 100000.0 + minutes * 60
        self.wall[0] = 1790000000.0 + minutes * 60
        return self.wall[0]

    def last(self, minutes, step):
        self.L.publish([{"kind": "group_last_step", "group": "main", "step": step, "devices": 5}], {}, {"main": "M"},
                       {"exits": {}}, self.at(minutes))

    def restored(self, minutes):
        self.L.publish([{"kind": "group_restored", "group": "main", "exit": "e-1"}], {"e-1": "X"}, {"main": "M"},
                       {"exits": {}}, self.at(minutes))

    def steps(self):
        return [(e["kind"], (e.get("args") or {}).get("step")) for e in self.L.events]

    def test_block_restored_then_direct_is_told(self):
        """Стенд КТ2: блок 06:14, «снова через…» 06:19, «напрямую» 06:20 — третье событие было задержано на 15 мин."""
        self.last(0, "block")
        self.restored(5)
        self.last(6, "direct")
        self.assertEqual(self.steps(), [("group_last_step", "block"), ("group_restored", None),
                                        ("group_last_step", "direct")])
        text = self.L.events[-1]["text"]
        self.assertIn("напрямую", text)

    def test_direct_repeat_is_still_held(self):
        self.last(0, "direct")
        self.restored(2)
        self.last(4, "direct")                                   # the same news within 15 min: the antispam holds it
        self.assertEqual(self.steps(), [("group_last_step", "direct"), ("group_restored", None)])
        self.last(6, "block")                                    # the block is other news
        self.assertEqual(self.steps()[-1], ("group_last_step", "block"))

    def end(self, minutes, step):
        self.L._window_end({}, {"exits": {}}, self.at(minutes), {"groups": {"main": {"last_step": step}}})

    def test_direct_and_block_in_one_window_end_on_the_last_step(self):
        """REV-s1-kt2-final З-2: 0 блок / 2 снова через / 4 напрямую / 6 снова через / 8 блок — конец окна говорит
        блок (лента не остаётся на «напрямую»), повторов нет."""
        self.last(0, "block")
        self.restored(2)
        self.last(4, "direct")
        self.restored(6)                                         # held: «снова через» told at 2
        self.last(8, "block")                                    # held: the block told at 0
        told = [("group_last_step", "block"), ("group_restored", None), ("group_last_step", "direct")]
        self.assertEqual(self.steps(), told)
        self.end(18, "block")                                    # the window (from 4) is not over yet
        self.assertEqual(self.steps(), told)
        self.end(20, "block")
        self.assertEqual(self.steps(), told + [("group_last_step", "block")])
        self.assertIn("main", json.dumps(self.L.events[-1].get("args") or {}))
        self.assertNotIn("напрямую", self.L.events[-1]["text"])
        self.end(40, "block")                                    # nothing held: no repeat
        self.end(60, "block")
        self.assertEqual(len(self.steps()), 4)

    def test_block_then_direct_in_one_window_ends_direct(self):
        """Зеркально: 0 напрямую / 2 снова через / 4 блок / 6 снова через / 8 напрямую — конец окна: «напрямую»."""
        self.last(0, "direct")
        self.restored(2)
        self.last(4, "block")
        self.restored(6)
        self.last(8, "direct")
        self.end(20, "direct")
        self.assertEqual(self.steps()[-1], ("group_last_step", "direct"))
        self.assertIn("напрямую", self.L.events[-1]["text"])
        self.assertEqual(len(self.steps()), 4)
        self.end(40, "direct")
        self.assertEqual(len(self.steps()), 4)

    def test_the_same_step_at_the_window_end_is_not_told_again(self):
        self.last(0, "direct")
        self.last(4, "direct")                                   # held
        self.end(20, "direct")                                   # the feed already says «напрямую»
        self.assertEqual(self.steps(), [("group_last_step", "direct")])
        self.last(21, "block")
        self.last(23, "block")                                   # held
        self.end(40, "block")
        self.assertEqual(self.steps(), [("group_last_step", "direct"), ("group_last_step", "block")])

    def test_said_with_its_step_survives_a_restart(self):
        self.last(0, "direct")
        self.L._save_feed()
        L2 = live.Live()
        L2._load_feed()
        self.assertEqual(L2.said[live.GROUP_KEY + "main"][1], live.DIRECT_STEP)

    def test_told_direct_survives_a_restart(self):
        self.last(0, "direct")
        self.L._save_feed()
        L2 = live.Live()
        L2._load_feed()
        self.assertIn((live.GROUP_KEY + "main", live.DIRECT_STEP), L2.told)

    def restart(self):
        """The collector restarted inside the window: held and the window are kept (/run), the held args are not."""
        self.L._save_feed()
        L2 = live.Live()
        L2._load_feed()
        L2._loaded = True
        self.assertIn(live.GROUP_KEY + "main", L2.held)
        self.assertEqual(L2.last_args, {})
        self.L = L2

    def test_the_window_end_after_a_restart_tells_the_devices_and_the_name(self):
        """REV-338 З-3: после перезапуска сборщика внутри окна строка конца окна берёт число устройств и имя группы из
        решения такта и текущих названий групп — не «0 устройств» и не чужое имя (порча M5: шаг всегда «блок»)."""
        self.last(0, "block")
        self.restored(2)
        self.last(4, "direct")
        self.restored(6)
        self.last(8, "block")                                    # held
        self.restart()
        dec = {"groups": {"main": {"last_step": "block", "steps": {
            "d-1": "last_block", "d-2": "last_block", "d-3": "last_direct", "d-4": "pinned"}}}}
        self.L._window_end({}, {"exits": {}}, self.at(20), dec, None, {"main": "Дом"})
        self.assertEqual(len(self.L.events), 1, "the end of the window told nothing")
        ev = self.L.events[-1]
        a = ev.get("args") or {}
        self.assertEqual((ev["kind"], a.get("step"), a.get("devices"), a.get("direct"), a.get("name")),
                         ("group_last_step", "block", 3, 1, "Дом"))
        self.assertIn("Дом", ev["text"])
        self.assertIn("3", ev["text"])
        self.assertNotIn("Основная", ev["text"])
        self.assertNotIn(" 0 ", ev["text"])

    def test_the_full_round_hands_the_group_names_to_the_window_end(self):
        """REV-338-2 З-2: the end of the window through the whole round of the collector (``Live.full``, live.py: the
        names of the groups from the decision's policy go to ``_window_end``) — without them the line said «main»."""
        self.last(0, "block")
        self.restored(2)
        self.last(4, "direct")
        self.restored(6)
        self.last(8, "block")                                    # held
        self.restart()
        dec = {"groups": {"main": {"last_step": "block", "steps": {"d-1": "last_block", "d-2": "last_block"}}},
               "policy": {"groups": [{"id": "main", "name": "Дом"}]}}
        self.L.full({}, None, {}, {"decision": dec, "events": []}, [], now=self.at(20))
        told = [e for e in self.L.events if e["kind"] == "group_last_step"]
        self.assertEqual(len(told), 1, "the end of the window told nothing")
        a = told[-1].get("args") or {}
        self.assertEqual((a.get("step"), a.get("devices"), a.get("name")), ("block", 2, "Дом"))
        self.assertIn("Дом", told[-1]["text"])
        self.assertNotIn("main", told[-1]["text"])

    def test_the_window_end_after_a_restart_tells_direct(self):
        self.last(0, "direct")
        self.restored(2)
        self.last(4, "block")
        self.restored(6)
        self.last(8, "direct")                                   # held
        self.restart()
        dec = {"groups": {"main": {"last_step": "direct", "steps": {"d-1": "last_direct", "d-2": "last_direct"}}}}
        self.L._window_end({}, {"exits": {}}, self.at(20), dec, None, {"main": "Дом"})
        self.assertEqual(len(self.L.events), 1, "the end of the window told nothing")
        ev = self.L.events[-1]
        a = ev.get("args") or {}
        self.assertEqual((ev["kind"], a.get("step"), a.get("devices"), a.get("name")),
                         ("group_last_step", "direct", 2, "Дом"))
        self.assertIn("напрямую", ev["text"])
        self.assertIn("Дом", ev["text"])
        n = len(self.L.events)
        self.L._window_end({}, {"exits": {}}, self.at(40), dec, None, {"main": "Дом"})
        self.assertEqual(len(self.L.events), n)                  # told: no repeat


from tests.gw.test_collect_s1 import S1World  # noqa: E402  (a base class without tests of its own)


class DisabledExitTest(S1World):
    """К2-7: выключенный для шлюза выход xray убирает из своих проверок — его последняя проба не «тишина xray»: ни
    ``exit_dead`` с пушем, ни «xray давно не проверял выход»; CLI и бот говорят «выключен для шлюза»."""

    def kinds_of(self, eid):
        return [e["kind"] for e in self.events() if e.get("exit") == eid]

    def test_switched_off_is_not_judged(self):
        from kaskad.gw import exits, model, ops
        from kaskad.gw import cli as gcli
        for _ in range(6):
            self.round()
        snap = exits.load_snapshot()
        self.assertTrue(snap["exits"][self.vid]["alive"])
        with state.transaction() as st:
            model.set_exit(st, self.vid, enabled=False)
        self.w.curl.try_at = int(self.now)                      # xray probes it no more: the same probe read again
        for _ in range(15):                                      # 150 s — past UNKNOWN_MAX (60 s)
            self.round()
        self.assertNotIn("exit_dead", self.kinds_of(self.vid))
        rec = exits.load_snapshot()["exits"][self.vid]
        self.assertNotEqual(rec.get("detail_code"), "detail.xray_stale")
        self.assertNotIn("dead", rec.get("reasons") or [])
        row = next(r for r in ops.exits_view(self.w.runner) if r["id"] == self.vid)
        self.assertEqual(row["text"], tr("gw_exits.state.off_for_gw"))
        self.assertTrue(gcli.exit_line(row).endswith("— выключен для шлюза"), gcli.exit_line(row))
        self.assertNotIn("работает", gcli.exit_line(row))
        # switched on again: xray probes it anew — alive, no «упал» on the way
        with state.transaction() as st:
            model.set_exit(st, self.vid, enabled=True)
        self.w.curl.try_at = None
        for _ in range(6):
            self.round()
        self.assertNotIn("exit_dead", self.kinds_of(self.vid))
        self.assertTrue(exits.load_snapshot()["exits"][self.vid]["alive"])


import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import unittest  # noqa: E402

from tests import jsparts  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def _static(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(shutil.which("node"), "node is needed")
class DisabledExitCardTest(unittest.TestCase):
    """К2-7: плитка выключенного выхода — «выключен для шлюза», без «не работает», «снят» и красной плашки."""

    def card(self, row):
        app = _static("app.js")
        parts = [jsparts.const(app, n) for n in ("EXIT_CHIP", "exitOwn", "exitGwOff", "exitChip", "exitOff",
                                                  "PST_KEY")]
        parts.append(jsparts.function(app, "exitItem"))
        stubs = """
const t = (k) => k; const known = (x) => x !== null && x !== undefined; const DASH = "—";
const fmtTs = (x) => String(x); const fmtDur = (x) => String(x); const txt = (x) => x; const el = () => ({});
const U = { chip: (text, tone) => ({ chip: text, tone }), btn: () => ({}) };
const gwEnabled = () => true; const exitKindText = () => "VLESS"; const isXrayExit = () => true;
const groupName = (g) => g; const exitToggleAct = () => ({}); const exitDelAct = () => ({});
const openExitDetail = () => {}; const renameExit = () => {}; const exitDeadText = () => "DEAD";
const exitSilentText = () => "SILENT"; const EXIT_TWICE = {};
"""
        script = stubs + "\n".join(parts) + "\nconsole.log(JSON.stringify(exitItem(%s, false)));" % json.dumps(row)
        res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_off_card(self):
        row = {"id": "e-0123456789", "name": "claude-kt2-1", "kind": "xray", "enabled": False, "state": "dead",
               "detail": "сервер выхода отвечает", "since": 1, "hasPolicy": True, "group": "main", "pstate": "off"}
        c = self.card(row)
        self.assertEqual(c["chipsR"], [{"chip": "ex.offForGw", "tone": "off"}])
        self.assertNotIn("ex.pst.off", json.dumps(c["chips"]))
        self.assertIsNone(c["alert"])
        on = self.card(dict(row, enabled=True))                  # switched on and dead: as before
        self.assertEqual(on["chipsR"], [{"chip": "ex.dead", "tone": "danger"}])
        self.assertEqual(on["alert"], {"k": "danger", "text": "DEAD"})


class NoNullChildTest(unittest.TestCase):
    """К2-2: «Маршрут по умолчанию» показывал буквальное «null» — ``replaceChildren(…, x ? y : null)`` пишет null
    текстом. Класс ошибки: ни один вызов replaceChildren/append скриптов панели не отдаёт верхним аргументом
    «… : null» (пустое — фильтром ``.filter(Boolean)`` или без аргумента)."""

    def args(self, src, i):
        """Top-level arguments of the call whose «(» is at ``i``."""
        end = jsparts.close_of(src, i)
        cuts = [i]
        jsparts._scan(src, i, lambda c, depth, at: (cuts.append(at) if c == "," and depth == 1 and at < end else None)
                      or at >= end)
        cuts.append(end)
        return [src[a + 1:b].strip() for a, b in zip(cuts, cuts[1:])]

    def test_no_literal_null(self):
        bad = []
        for name in sorted(os.listdir(STATIC)):
            if not name.endswith(".js") or name.startswith("dev-mock"):
                continue
            src = _static(name)
            for m in __import__("re").finditer(r"\.(?:replaceChildren|append)\(", src):
                for a in self.args(src, m.end() - 1):
                    if __import__("re").search(r"\?[\s\S]*:\s*null$", a) or a == "null":
                        bad.append("%s:%d" % (name, src.count("\n", 0, m.start()) + 1))
        self.assertEqual(bad, [])


from kaskad.gw import hostmon as H, hostprobe  # noqa: E402
from tests.gw.test_hostmon import Node, ticks  # noqa: E402  (helpers, no tests)


class RetransQuietNodeTest(unittest.TestCase):
    """К2-3: «Повторы TCP» не срабатывают от шума сканеров на тихом узле, но ловят потери на канале под нагрузкой."""

    def level(self, retrans, out_segs):
        node = Node()
        node.run(ticks("retrans"), retrans=retrans, out_segs=out_segs)          # per round of 10 s
        return node.level("retrans")

    def test_stand_kt2_quiet_node_with_scanners_is_ok(self):
        # стенд КТ2: ≈ 2 400 сегментов за 5 мин, повторов ≈ 8 % — 2–3 соединения сканеров в FIN-WAIT
        self.assertEqual(self.level(7, 80), "ok")
        # чуть больше объёма, повторов меньше порога в штуках: 12 000 сегментов, 3 % = 360 повторов
        self.assertEqual(self.level(12, 400), "ok")

    def test_loss_under_load_is_caught(self):
        self.assertEqual(self.level(50, 2000), "warn")                            # 60 000 сегментов, 2,5 %
        self.assertEqual(self.level(30, 600), "warn")                             # 18 000, 5 % = 900 повторов

    def test_a_warn_goes_only_past_the_off_threshold(self):
        """REV-s1-kt2-final П-1: в полосе 25 000–33 333 сегментов за 5 мин warn, вошедший на 2,5 %, держится при 1,9 %,
        хотя повторов стало меньше 500, и снимается ниже 1,5 % (порог «выкл»); подняться под min_bad warn не может — и при
        доле выше порога «вкл» (REV-338 П-1)."""
        node = Node()
        node.run(ticks("retrans"), retrans=25, out_segs=1000)                     # 30 000 сегментов, 2,5 %
        self.assertEqual(node.level("retrans"), "warn")
        node.run(ticks("retrans"), retrans=16, out_segs=850)                      # 25 500, 1,88 % = 480 < 500
        self.assertEqual(node.level("retrans"), "warn")
        node.run(ticks("retrans"), retrans=10, out_segs=850)                      # 1,18 %: ниже «выкл»
        self.assertEqual(node.level("retrans"), "ok")
        node.run(ticks("retrans"), retrans=16, out_segs=700)                      # 21 000, 2,29 % — выше «вкл» 2 %, но
        self.assertEqual(node.level("retrans"), "ok")                             # 480 < 500 (min_bad): не поднимает
        node.run(ticks("retrans"), retrans=1, out_segs=10)                        # трафика нет: не судим — ok
        self.assertEqual(node.level("retrans"), "ok")

    def test_the_rule_stays_a_draft(self):
        r = H.RULES["retrans"]
        self.assertTrue(r["draft"])
        self.assertIsNone(r["crit"])
        self.assertIsNone(r["alarm"])                                             # no problem, no push
        self.assertEqual((r["min_n"], r["min_bad"]), (H.RETRANS_MIN_OUT, H.RETRANS_MIN_BAD))

    def test_syn_retrans_is_not_loss(self):
        a = {"mono": 10.0, "tcp": {"retrans": 100, "out_segs": 1000, "syn_retrans": 40}}
        b = {"mono": 20.0, "tcp": {"retrans": 160, "out_segs": 2000, "syn_retrans": 90}}
        s = H.derive(a, b)
        self.assertEqual((s["retrans"], s["out_segs"]), (10, 1000))
        old = H.derive({"mono": 10.0, "tcp": {"retrans": 100, "out_segs": 1000}},
                       {"mono": 20.0, "tcp": {"retrans": 160, "out_segs": 2000}})
        self.assertEqual(old["retrans"], 60)                                      # an older probe: as before

    def test_probe_reads_syn_retrans(self):
        snmp = ("Tcp: RtoAlgorithm OutSegs RetransSegs\nTcp: 1 2000 30\n")
        net = "TcpExt: SyncookiesSent ListenOverflows ListenDrops TCPSynRetrans\nTcpExt: 0 7 9 12\n"
        self.assertEqual(hostprobe._snmp(snmp, net), {"retrans": 30, "out_segs": 2000, "syn_retrans": 12})
        self.assertEqual(hostprobe._snmp(snmp), {"retrans": 30, "out_segs": 2000})


class HeaderHostTest(CliGwCase):
    """К2-3: шапка SSH-меню знает свет здоровья сервера (пункт 22) — ``summary --json`` отдаёт ``host.level`` из
    файлов сборщика или бота, своей пробы не делает; одно слово уровня warn в CLI и панели."""

    def test_summary_gives_the_light(self):
        from kaskad.gw import hostprobe
        self.enabled_world()
        with mock.patch.object(hostprobe, "read_health", lambda now=None, st=None, mono=None, lang=None,
                               instant=None: ({"level": "warn"}, "collector")):
            rc, doc, err = self.run_json("summary")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["host"], {"level": "warn"})
        rc, doc, err = self.run_json("summary")                  # nobody judged it: no light, never an own reading
        self.assertEqual(rc, 0, err)
        self.assertIsNone(doc["host"])

    def test_one_word_for_warn(self):
        # 3.3.7.12 (владелец 28.10 «статусы одними словами везде»): слова — панели; полное равенство — test_state_words
        self.assertEqual(tr("gw_host.light.warn", "ru"), "Есть предупреждения")
        self.assertEqual(tr("gw_host.light.warn", "en"), "There are warnings")
        # панель: светофор старого «Обзора» (ov.healthWarn «есть замечания») снят как недостижимый (REV-it1 №4); живой
        # экран «Здоровье сервера» говорит словами владельца 28.10 («замечания не пишем») — без числа
        self.assertIn('"ovb.hh.warn": "Есть предупреждения"', _static("pages/ovb-i18n-ru.js"))
        self.assertIn('"ovb.hh.warn": "There are warnings"', _static("pages/ovb-i18n-en.js"))
        for lang in ("ru", "en"):
            with open(os.path.join(os.path.dirname(STATIC), "..", "i18n", "sh", "menu.%s.sh" % lang),
                      encoding="utf-8") as f:
                self.assertIn("MM_H_HOST_WARN=", f.read())


class CritWordTest(CliGwCase):
    """SPEC-todo «После выпуска 3.3.7»: уровень crit назван одними словами в CLI и боте (gw_host.light.crit) и шапке
    SSH-меню (MM_H_HOST_CRIT) — RU и EN; прежних «Серверу плохо» / «in trouble» нет. Панель: светофор старого «Обзора»
    (ov.healthCrit) снят как недостижимый (REV-it1 №4); живой экран «Здоровье сервера» — «Требует внимания» (слова
    вердикта Сводки, круг 1 Т1), тоже без «плохо». 3.3.7.12 (владелец 28.10 «статусы одними словами везде»): CLI, бот и
    меню — слова панели «Требует внимания» (было «Есть серьёзные неполадки»; все уровни — tests/test_state_words.py)."""

    WORDS = {"ru": "требует внимания", "en": "needs attention"}

    def test_one_word_on_every_surface(self):
        import os
        import re
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad")
        for lang, word in self.WORDS.items():
            cli = tr("gw_host.light.crit", lang)
            with open(os.path.join(root, "panel", "static", "pages", "ovb-i18n-%s.js" % lang), encoding="utf-8") as f:
                panel = re.search(r'"ovb\.hh\.crit":\s*"([^"]*)"', f.read()).group(1)
            with open(os.path.join(root, "i18n", "sh", "menu.%s.sh" % lang), encoding="utf-8") as f:
                menu = re.search(r"M_%s_MM_H_HOST_CRIT='([^']*)'" % lang, f.read()).group(1)
            for where, text in (("cli/bot", cli), ("menu", menu)):
                self.assertIn(word, text.lower(), (lang, where, text))
            for where, text in (("cli/bot", cli), ("panel", panel), ("menu", menu)):
                self.assertNotRegex(text.lower(), "плохо|trouble", (lang, where, text))
            self.assertEqual(panel, {"ru": "Требует внимания", "en": "Needs attention"}[lang])


class FailChangedOnTheLastStepTest(DirectStepEventTest):
    """KT3-3 (стенд 3.3.7.1; К2-6 З-2): группа уже на последнем шаге, владелец меняет «при отказе» блок ↔ напрямую —
    строка в ленте сразу (действие владельца, не повтор), и «напрямую» не остаётся «нет выхода в интернет»."""

    def decide(self, fail, prev, minutes):
        from kaskad.gw import assign as A
        from tests.gw.test_assign import E1, devs, doc
        dec = A.assign({"main": devs(5)}, {E1: "off"}, doc([E1], fail=fail), prev, {}, {}, minutes * 60)
        self.L.publish([dict(e) for e in dec["events"]], {E1: "X"}, {"main": "M"}, {"exits": {}}, self.at(minutes))
        return dec

    def test_both_ways_tell_the_step_now(self):
        dec = self.decide("direct", None, 0)
        self.assertEqual(self.steps(), [("group_last_step", "direct")])
        dec = self.decide("direct", dec, 1)                       # the same step: nothing new
        self.assertEqual(len(self.steps()), 1)
        dec = self.decide("block", dec, 6)                        # direct -> block: at once
        dec = self.decide("direct", dec, 8)                       # block -> direct within the window: at once too
        self.assertEqual(self.steps(), [("group_last_step", "direct"), ("group_last_step", "block"),
                                        ("group_last_step", "direct")])
        self.assertIn("напрямую", self.L.events[-1]["text"])
        self.assertNotIn("нет выхода", self.L.events[-1]["text"])
        self.assertIn("нет выхода", self.L.events[-2]["text"])
        self.assertEqual(dec["groups"]["main"]["last_since"], 0)          # still the same outage
        dec = self.decide("direct", dec, 9)
        self.assertEqual(len(self.steps()), 3)                           # no repeat while nothing changes
        self.L._window_end({}, {"exits": {}}, self.at(30), dec)
        self.assertEqual(len(self.steps()), 3)                           # the window end has nothing to add
