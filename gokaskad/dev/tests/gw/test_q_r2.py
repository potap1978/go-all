"""П2, круг 2 по стенду T-q (k3/stand/logs/T-q.md). Н-ru3 (observatory по «gr-») отменён в круге 3 — российский
VLESS-выход проверяет своя проба через probe-<id> (tests/gw/test_q_r3.py); Н-lazy — новая российская группа проверяется «Часто», «Дом» с «Лениво» прежних групп остаётся «Дом»,
а простаивающий «Лениво» AmneziaWG-выход всё равно проверяется раз в LAZY_WAKE_S (и перепроверяется после отказа)."""

import time
from unittest import mock

from kaskad import secrets
from kaskad.gw import collect, exits, xray
from kaskad.gw import policy as P
from tests.fakes_gw import awg_line, write_ru_pool
from tests.gw.test_ru_target import HOME, RuWorld


# Н-ru3 (observatory по «gr-») отменено в круге 3 (З-2): российский xray-выход — своя проба, tests/gw/test_q_r3.py


class LazyDefaultTest(RuWorld):
    """Н-lazy: умолчание новой российской группы — «Часто» (как «Стандарт»); прежний «Дом» с «Лениво» — «Дом»."""

    def test_a_new_russian_group_checks_often(self):
        g = P.default_group("g-0a0b0d", "ru", name="Дача")
        self.assertEqual(g["checks"]["freq"]["mode"], "often")
        self.assertEqual(P.preset_of(g), "ru_home")
        self.assertFalse(P.is_lazy(g))
        self.assertEqual(P.default_checks("ru")["freq"]["mode"], "often")

    def test_a_stored_lazy_home_stays_home_and_lazy(self):
        g = P.default_group("g-0a0b0d", "ru", name="Дача")
        g["checks"]["freq"] = {"mode": "lazy", "every_s": None}
        self.assertEqual(P.preset_of(g), "ru_home")
        st = self.world()
        doc = P.stored(st) or P.default_doc("standard")
        doc["groups"] = [x for x in doc["groups"] if x.get("id") != g["id"]] + [g]
        new, _ = P.save(st, doc, self.pool())
        got = [x for x in new["groups"] if x["id"] == g["id"]][0]
        self.assertEqual((got["preset"], got["checks"]["freq"]["mode"]), ("ru_home", "lazy"))
        g["checks"]["freq"] = {"mode": "rare", "every_s": None}     # any other change — «Свой», as before
        self.assertEqual(P.preset_of(g), P.CUSTOM)


class LazyWakeTest(RuWorld):
    """Н-lazy: простаивающий «Лениво» AmneziaWG-выход — раз в LAZY_WAKE_S и перепроверка отказа."""

    def setUp(self):
        super().setUp()
        self._mem = collect.MEM
        collect.MEM = collect.Mem()

    def tearDown(self):
        collect.MEM = self._mem
        super().tearDown()

    def plan(self, st, mono):
        pool = self.pool()
        pol = P.load(st, pool)
        P.group(pol, HOME)["checks"]["freq"] = {"mode": "lazy", "every_s": None}
        return collect.probe_plan(st, pol, pool, mono)

    def test_wake(self):
        st = self.world()
        awg = [r["id"] for r in self.ru_rows if r.get("kind") == "awg"][0]
        collect.MEM.health = dict((i, {"state": "ok"}) for i in self.ru_ids)
        collect.MEM.awg_at[awg] = 1000.0
        p = self.plan(st, 1000.0 + collect.LAZY_WAKE_S - 10)
        self.assertIn(awg, p["idle"])
        self.assertNotIn(awg, p["only"])                              # idle, checked lately: not yet
        p = self.plan(st, 1000.0 + collect.LAZY_WAKE_S)
        self.assertIn(awg, p["only"])                                 # once in LAZY_WAKE_S
        collect.MEM.awg_failed.add(awg)
        p = self.plan(st, 1000.0 + collect.RECHECK)
        self.assertIn(awg, p["only"])                                 # a failed one is rechecked: «упал» is reached


# ── Н-5x: соединения xray через прежний российский роутер рвутся без перезапуска xray ─────────────────────────────

from kaskad import util                                                  # noqa: E402
from kaskad.sysrun import FakeRunner                                     # noqa: E402

AWG_OB = {"tag": "gr-e-aaaaaaaaaa", "protocol": "freedom", "streamSettings": {"sockopt": {"mark": 457}}}
VLESS_OB = {"tag": "gr-e-bbbbbbbbbb", "protocol": "vless",
            "settings": {"vnext": [{"address": "203.0.113.32", "port": 443, "users": [{"id": "x"}]}]}}
DIRECT_OB = {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIPv4"}}


def cfg_to(tag, user="papa-phone-1"):
    return {"outbounds": [AWG_OB, VLESS_OB, DIRECT_OB],
            "routing": {"rules": [{"type": "field", "inboundTag": ["probe-e-bbbbbbbbbb"], "outboundTag": VLESS_OB["tag"]},
                                  {"type": "field", "user": [user], "domain": ["ext:roscomvpn-ru.dat:ru"],
                                   "outboundTag": tag}]}}


class RuLostTest(RuWorld):
    def test_lost_tags(self):
        self.assertEqual(xray.ru_lost(cfg_to(AWG_OB["tag"]), cfg_to(VLESS_OB["tag"])), {AWG_OB["tag"]})
        self.assertEqual(xray.ru_lost(cfg_to(VLESS_OB["tag"]), cfg_to(AWG_OB["tag"])), {VLESS_OB["tag"]})
        self.assertEqual(xray.ru_lost(cfg_to(AWG_OB["tag"]), cfg_to(AWG_OB["tag"])), set())
        self.assertEqual(xray.ru_lost(cfg_to(AWG_OB["tag"]), cfg_to("direct")), {AWG_OB["tag"]})   # consent off
        self.assertEqual(xray.ru_lost(cfg_to("direct"), cfg_to(AWG_OB["tag"])), set())
        gone = cfg_to(AWG_OB["tag"])
        gone["routing"]["rules"] = gone["routing"]["rules"][1:]           # the VLESS exit deleted: its probe- rule gone
        self.assertEqual(xray.ru_lost(cfg_to(AWG_OB["tag"]), gone), {VLESS_OB["tag"]})

    # the cut itself (by the fact, the sockets of xray only) — tests/gw/test_q_r3.py CutFactTest (круг 3, З-1, П-1)


class HotApplyCutTest(RuWorld):
    """``_apply_locked``: the hot path of a change of ``gw/ops`` (``apply_now`` → cut_ru) closes the old router's
    connections; the collector's own apply does not; xray is never restarted for it."""

    def run_hot(self, cut_ru):
        old, new = cfg_to(AWG_OB["tag"]), cfg_to(VLESS_OB["tag"])
        path = xray.config_path()
        util.atomic_write(path, util.json_text(old), 0o600)
        cut, out = [], {}
        front = mock.Mock()
        front.after_xray.return_value = None
        with mock.patch.multiple(xray, hot_tags=mock.DEFAULT, add_modes=mock.DEFAULT, _hy2_seen=mock.DEFAULT,
                                 hot_plan=mock.Mock(return_value={"routing": [1]}), hot_apply=mock.Mock(return_value=True),
                                 hot_adapters=mock.DEFAULT, _hy2_note=mock.DEFAULT, _test=mock.DEFAULT,
                                 _front=mock.Mock(return_value=front), guarded_ports=mock.Mock(return_value=[]),
                                 _note_pending=mock.Mock(return_value=True), pending=mock.Mock(return_value=None),
                                 cut_ru_conns=mock.Mock(side_effect=lambda r, o, t: cut.append(set(t)) or 1)), \
                mock.patch.object(xray.gsys, "unit_active", return_value=True), \
                mock.patch.object(xray.gsys, "ensure_unit", return_value={"started": False, "restarted": False}) as eu:
            xray._apply_locked({}, {}, self.w.runner, new, util.json_text(new), "/usr/local/bin/xray", False, None,
                               False, False, out, cut_ru=cut_ru)
        self.assertFalse(eu.call_args.kwargs.get("restart"))                # through the API, no restart
        return cut, out

    def test_ops_change_cuts(self):
        cut, out = self.run_hot(True)
        self.assertEqual(cut, [{AWG_OB["tag"]}])
        self.assertEqual(out.get("ru_cut"), 1)

    def test_collector_apply_does_not(self):
        cut, out = self.run_hot(False)
        self.assertEqual(cut, [])

    def test_apply_now_asks_for_the_cut(self):
        with mock.patch.object(xray, "apply", return_value={}) as ap, \
                mock.patch.object(xray.exits, "decision", return_value={}), \
                mock.patch("kaskad.gw.collect.fingerprint", return_value="x"):
            xray.apply_now({}, {}, self.w.runner, [])
        self.assertIs(ap.call_args.kwargs.get("cut_ru"), True)


# ── Н-trace: «Куда пойдёт трафик?» знает российскую группу устройства ────────────────────────────────────────────

from kaskad import state                                                  # noqa: E402
from tests.fakes_gw import install_parser                                 # noqa: E402
from tests.gw.test_trace import D1, TraceCase                             # noqa: E402


class TraceRuViaTest(TraceCase):
    def ru_world(self, fail="block", alive=True):
        st = self.world()
        install_parser()
        write_ru_pool([awg_line(31, name="Роутер", endpoint="203.0.113.31:51820", address="10.8.31.2/32")])
        rid = exits.pool(None, roles=("ru",))[0]["id"]
        doc = st["gateway"].get("exit_policy") or P.default_doc("standard")
        home = P.default_group(HOME, "ru", "ru_home", name="Дом")
        home["fail"] = fail
        home["tiers"][0]["exits"] = [rid]
        doc["groups"] = [g for g in doc["groups"] if g.get("id") != HOME] + [home]
        st["gateway"]["exit_policy"] = doc
        st["gateway"]["users"][0]["ru_via"] = HOME
        snap = exits.load_snapshot()
        snap.setdefault("exits", {})[rid] = {"alive": alive, "state": "ok" if alive else "dead", "reasons": []}
        exits.save_snapshot(snap)
        state.save(st)
        return state.load(), rid

    def test_through_the_russian_exit(self):
        st, rid = self.ru_world()
        res = self.run_trace(st, D1, "mail.yandex.ru")
        self.assertEqual((res["verdict"], res["reason"]), ("exit", "ru_domain"))
        self.assertIn("внутренний выход", res["summary"])
        self.assertNotIn("напрямую", res["summary"].lower())
        self.assertIn("exit:" + rid, res["highlight"]["nodes"])
        self.assertNotIn("direct", res["highlight"]["nodes"])
        self.assertIn("Дом", self.texts(res))
        res = self.run_trace(st, D1, "8.8.8.8")                          # the foreign sites: the route, as before
        self.assertEqual((res["verdict"], res["reason"]), ("exit", "route"))

    def test_last_step_block(self):
        st, _ = self.ru_world(fail="block", alive=False)
        res = self.run_trace(st, D1, "mail.yandex.ru")
        self.assertEqual((res["verdict"], res["reason"]), ("block", "ru_last_block"))
        self.assertIn("Российские сайты закрыты", res["summary"])

    def test_last_step_direct_and_ru_via_direct(self):
        st, _ = self.ru_world(fail="direct", alive=False)
        res = self.run_trace(st, D1, "mail.yandex.ru")
        self.assertEqual(res["verdict"], "direct")
        self.assertIn("последний шаг", self.texts(res))
        st["gateway"]["users"][0]["ru_via"] = "direct"
        state.save(st)
        res = self.run_trace(state.load(), D1, "mail.yandex.ru")
        self.assertEqual((res["verdict"], res["reason"]), ("direct", "ru_domain"))
        self.assertNotIn("последний шаг", self.texts(res))


# ── мелочи: Н-move, Н-text, Н-state ─────────────────────────────────────────────────────────────────────────────

from kaskad.gw import cli_policy, live, model, policy_ops               # noqa: E402
from kaskad.i18n import tr                                               # noqa: E402


class SmallTest(RuWorld):
    def test_move_preview_shows_the_russian_path(self):
        st = self.world()
        place = {"group": "main", "exit": "e-1111111111", "tier": "t1", "step": "exit"}
        moves = {"total": 1, "by_group": {}, "devices": [
            {"device": "d-1", "name": "gost-phone", "from": place, "to": dict(place), "ru": True,
             "ru_from": {"group": HOME, "exit": self.ru_ids[0], "tier": "t1", "step": "exit"},
             "ru_to": {"group": HOME, "exit": None, "tier": None, "step": "last_direct"}}]}
        text = "\n".join(cli_policy.moves_lines(moves, st))
        self.assertIn("российские сайты", text)
        self.assertNotIn("gost-phone: ", text)                        # no «Основная · X → Основная · X»
        self.assertIn("Дом", text)

    def test_ru_block_text_is_about_russian_sites(self):
        a = {"group": HOME, "name": "Дом", "role": "ru", "step": "block", "devices": 4, "direct": 0}
        text = live._said_s1("group_last_step", a, "ru")
        self.assertIn("российские сайты закрыты у 4 устройств", text)
        self.assertNotIn("интернет", text)
        text = live._said_s1("group_last_step", dict(a, step="direct"), "ru")
        self.assertIn("российские сайты у 4 устройств открываются напрямую", text)
        text = live._said_s1("group_last_step", dict(a, role="abroad"), "ru")
        self.assertIn("нет выхода в интернет", text)

    def test_stale_group_hints_go(self):
        st = self.world()
        model.gw(st)["exits"].update({"e-aaaaaaaaaa": {"group": "g-dead00"},
                                      "e-bbbbbbbbbb": {"group": HOME, "name": "Роутер"},
                                      "e-cccccccccc": {"group": "g-dead00", "name": "Дача"},
                                      "e-dddddddddd": {"group": HOME}})
        self.assertTrue(model.prune_exit_groups(st, ["main", HOME], drop=["e-dddddddddd"]))
        self.assertEqual(model.gw(st)["exits"].get("e-aaaaaaaaaa"), None)
        self.assertEqual(model.gw(st)["exits"]["e-bbbbbbbbbb"], {"group": HOME, "name": "Роутер"})
        self.assertEqual(model.gw(st)["exits"]["e-cccccccccc"], {"name": "Дача"})
        self.assertNotIn("e-dddddddddd", model.gw(st)["exits"])
        self.assertFalse(model.prune_exit_groups(st, ["main", HOME]))

    def test_group_delete_prunes(self):
        st = self.world()
        doc = st["gateway"]["exit_policy"]
        doc["groups"].append(P.default_group("g-0d0d0d", "ru", name="Дача-2"))
        model.gw(st)["exits"]["e-eeeeeeeeee"] = {"group": "g-0d0d0d"}
        state.save(st)
        with mock.patch.object(policy_ops, "_apply_change", create=True):
            try:
                policy_ops.group_delete(self.w.runner, "g-0d0d0d")
            except Exception as e:                                   # noqa: BLE001 — the apply of the test world
                self.assertNotIn("g-0d0d0d", [g["id"] for g in state.load()["gateway"]["exit_policy"]["groups"]], e)
        self.assertNotIn("e-eeeeeeeeee", model.gw(state.load())["exits"])


# ── долг REV-q-int-3 №1: таблица до волны 3 (без gw_rd) ──────────────────────────────────────────────────────────

from kaskad.gw import hooks, net                                         # noqa: E402
from tests.gw import test_net_rx as _net_rx                              # noqa: E402


class NoRdTableTest(RuWorld):
    """Таблица без gw_rd (узел до волны 3, nft.apply ещё не прошёл): ``set_ru`` — True на ветках с меткой, без метки и
    при rx=False; хук нового адреса — без ru_failed, адрес живой. Порча «нет gw_rd → закрыто» здесь краснеет."""

    IP = "10.77.8.10"
    ikev2 = _net_rx.HookRxTest.ikev2

    def setUp(self):
        super().setUp()
        self.ikev2()
        del self.w.nft.tables["kaskad"]["sets"]["gw_rd"]
        self.assertNotIn("gw_rd", self.w.nft.tables["kaskad"]["sets"])

    def set_ru(self, rd_on, mark, rx=True):
        return net.set_ru(self.w.runner, state.load(), self.IP, rd_on, mark, rx=rx)

    def test_with_a_mark(self):
        mark = self.ru_rows[-1]["mark"]
        self.assertTrue(self.set_ru(False, mark))
        self.assertEqual(self.w.nft.elements("gw_rx").get(self.IP), mark)

    def test_without_a_mark(self):
        self.assertTrue(self.set_ru(False, None))
        self.assertNotIn(self.IP, self.w.nft.elements("gw_rx"))

    def test_no_russian_group(self):
        self.assertTrue(self.set_ru(False, None, rx=False))

    def test_hook_is_not_ru_failed(self):
        res = hooks.hook("ikev2", "up", "telefon-0001", self.IP, self.w.runner)
        self.assertFalse(res.get("ru_failed"), res)
        self.assertIn(self.IP, self.w.nft.elements("gw_live"))


class AfterPoolPruneTest(RuWorld):
    """Н-state после exit-del: ``after_pool`` убирает подсказку group удалённой строки и подсказки к удалённым группам."""

    def test_deleted_line_and_dead_group(self):
        st = self.world()
        gone = "e-ffffffffff"                                         # in the tier of «Дом», in no file of the pool
        P.group(st["gateway"]["exit_policy"], HOME)["tiers"][0]["exits"].append(gone)
        model.gw(st)["exits"].update({gone: {"group": HOME}, "e-1212121212": {"group": "g-dead00", "name": "X"},
                                      self.ru_ids[0]: {"group": HOME}})
        state.save(st)
        policy_ops.after_pool("exit del")
        recs = model.gw(state.load())["exits"]
        self.assertNotIn(gone, recs)
        self.assertEqual(recs["e-1212121212"], {"name": "X"})
        self.assertEqual(recs[self.ru_ids[0]], {"group": HOME})        # a live exit keeps its hint
