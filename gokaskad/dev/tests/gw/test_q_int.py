"""Хвосты Q2 после слияния с 3.3.7.3 (REV-s1-q2-2): З-1 — смена российской группы рвёт старые соединения, З-2 — сбой
удаления из gw_rx в хуке виден, П-1 — потерянный ru-ports.json не отдаёт сразу порт удалённого ключа, П-2 — порчи
`no_closed` и `spread_noflush` красные."""

import os
from unittest import mock

from kaskad import nft, secrets, state, util
from kaskad.gw import exits, hooks, net, xray
from kaskad.gw import policy as P
from kaskad.sysrun import Result
from tests.fakes_gw import write_ru_pool
from tests.gw import test_net_rx as _net_rx, test_ru_ports as _ru_ports
from tests.gw.test_ru_target import HOME, RuWorld

D1_IP, SOSED, V1, V2, V3 = _ru_ports.D1_IP, _ru_ports.SOSED, _ru_ports.V1, _ru_ports.V2, _ru_ports.V3

A = "10.77.1.10"


class RuRevokedHomeTest(RuWorld):
    """``net.ru_revoked`` с ``home`` (``net.rx_home``): адрес остался в gw_rx под другой меткой."""

    def test_another_group_takes_the_old_exit_mark(self):
        before = (set(), {A: 0x3ca}, {A: 0x101})
        after = (set(), {A: 0x3c9}, {A: 0x101})
        self.assertEqual(net.ru_revoked(before, after, {A: [0x3c9]}), {0x3ca: {A}})     # ru_via R1 -> R2
        self.assertEqual(net.ru_revoked(before, after, {A: [0x3c9, 0x3ca]}), {})       # a step of the same group
        self.assertEqual(net.ru_revoked(before, after), {})                           # home not known: as before

    def test_the_exit_left_the_group(self):
        before = (set(), {A: 0x3ca}, {A: 0x101})
        for m1 in (exits.MARK_DIRECT, exits.MARK_BLOCK):                               # the group fell to its last step
            self.assertEqual(net.ru_revoked(before, (set(), {A: m1}, {A: 0x101}), {A: [0x3c9]}), {0x3ca: {A}}, m1)
            self.assertEqual(net.ru_revoked(before, (set(), {A: m1}, {A: 0x101}), {A: [0x3ca]}), {}, m1)

    def test_no_exit_mark_is_never_taken_by_home(self):
        for m0 in (exits.MARK_DIRECT, exits.MARK_BLOCK):
            self.assertEqual(net.ru_revoked((set(), {A: m0}, {}), (set(), {A: 0x3c9}, {}), {A: [0x3c9]}), {}, m0)
        self.assertEqual(net.ru_revoked((set(), {A: 0x3ca}, {A: 0x3ca}), (set(), {A: 0x3c9}, {A: 0x3ca}),
                                        {A: [0x3c9]}), {})                             # gw_route goes there anyway

    def test_rx_home_without_marks_is_not_known(self):
        st = self.world()
        self.assertIsNone(net.rx_home(st, P.load(st), []))


class UpdateMapRuViaTest(RuWorld):
    """REV-s1-q2-2 З-1 (опыт Е1): ``update_map`` рвёт соединения старой метки при смене группы и уходе выхода."""

    two_people = _ru_ports.RuPortsTest.two_people
    running = _ru_ports.RuPortsTest.running

    def flushed(self, fn):
        calls = []

        def fake(runner, ips, mark):
            calls.append((mark, sorted(ips)))
            return len(ips)
        with mock.patch.object(net, "flush_devices", side_effect=fake):
            fn()
        return calls

    def update(self):
        return self.flushed(lambda: net.update_map(self.w.runner, state.load(), decision=self.dec(state.load())))

    def test_ru_via_to_another_group(self):
        st, v1, v2 = self.two_people()
        nft.apply(st, self.w.runner)
        old = self.w.nft.elements("gw_rx")[D1_IP]
        st = state.load()
        st["gateway"]["users"][0]["ru_via"] = HOME
        state.save(st)
        calls = self.update()
        self.assertNotEqual(self.w.nft.elements("gw_rx")[D1_IP], old)
        self.assertTrue(any(m == old and D1_IP in ips for m, ips in calls), calls)

    def test_the_exit_moved_to_another_group(self):
        st, v1, v2 = self.two_people()
        nft.apply(st, self.w.runner)
        old = self.w.nft.elements("gw_rx")[D1_IP]
        st = state.load()
        doc = st["gateway"]["exit_policy"]
        P.group(doc, SOSED)["tiers"][0]["exits"] = []
        P.group(doc, HOME)["tiers"][0]["exits"] = [v1, v2]
        state.save(st)
        calls = self.update()
        self.assertTrue(any(m == old and D1_IP in ips for m, ips in calls), calls)

    def test_gw_apply_ru_via_to_another_group(self):
        """Тот же разрыв на пути ``gw apply`` (``ops._apply`` → ``_rx_home`` по сохранённой политике)."""
        from kaskad.gw import ops
        st, v1, v2 = self.two_people()
        nft.apply(st, self.w.runner)
        old = self.w.nft.elements("gw_rx")[D1_IP]
        st = state.load()
        st["gateway"]["users"][0]["ru_via"] = HOME
        state.save(st)
        calls = self.flushed(lambda: ops._apply(state.load(), secrets.load(), self.w.runner, probe=False))
        self.assertNotEqual(self.w.nft.elements("gw_rx")[D1_IP], old)
        self.assertTrue(any(m == old and D1_IP in ips for m, ips in calls), calls)

    def test_nothing_changed_nothing_flushed(self):
        st, v1, v2 = self.two_people()
        nft.apply(st, self.w.runner)
        self.assertEqual(self.update(), [])

    def test_the_spread_branch_flushes_too(self):
        """П-2 (порча ``spread_noflush``): правило «по соединениям» в той же транзакции — разрыв тоже."""
        st, v1, v2 = self.two_people()
        nft.apply(st, self.w.runner)
        old = self.w.nft.elements("gw_rx")[D1_IP]
        st = state.load()
        st["gateway"]["users"][0]["ru_via"] = HOME
        state.save(st)
        line = "add element %s %s { %s }" % (nft.TABLE, net.LIVE, D1_IP)
        with mock.patch.dict(net.SPREAD_FAIL, {"err": "x"}), \
                mock.patch.object(net, "spread_plan", return_value=([line], None)):
            calls = self.update()
        self.assertTrue(any(m == old and D1_IP in ips for m, ips in calls), calls)

    def test_a_lost_ports_file_never_gives_the_removed_port_at_once(self):
        """П-1 (опыт Е2): файл портов потерян, конфиг на диске уже без удалённого ключа — новый ключ не берёт его порт."""
        st, v1, v2 = self.two_people()
        self.running(xray.render(st, secrets.load(), self.pool(), (), self.dec(st)))
        ports = dict((e["id"], exits.tproxy_port(e)) for e in exits.pool(self.w.runner, roles=("ru",)))
        write_ru_pool([V2])
        st = state.load()
        P.group(st["gateway"]["exit_policy"], HOME)["tiers"][0]["exits"] = []
        state.save(st)
        st = state.load()
        self.running(xray.render(st, secrets.load(), self.pool(), (), self.dec(st)))
        os.unlink(exits._ru_ports_path())
        v4 = V3.replace("cccccccc", "dddddddd").replace(".43:", ".44:").replace("#Third", "#Fourth")
        write_ru_pool([V2, V3, v4])
        got = dict((e["id"], exits.tproxy_port(e)) for e in exits.pool(self.w.runner, roles=("ru",)))
        self.assertEqual(got[v2], ports[v2])                    # the kept key — its port from the config
        self.assertNotIn(ports[v1], got.values())
        self.assertEqual(len(set(got.values())), 3)

    def test_the_closed_config_holds_its_port(self):
        """П-2 (порча ``no_closed``): порт TPROXY из ``xray.closed.json`` другому выходу не выдаётся."""
        st, v1, v2 = self.two_people()
        cfg = xray.render(st, secrets.load(), self.pool(), (), self.dec(st))
        os.unlink(exits._ru_ports_path())
        util.ensure_dir(os.path.dirname(xray.closed_path()), 0o700)
        held = exits.RU_PORT_FIRST + 5
        util.atomic_write(xray.closed_path(), util.json_text({"inbounds": [{"tag": "tproxy-e-0000000000",
                                                                               "port": held}]}), 0o600)
        self.running({"inbounds": []})
        write_ru_pool([V1, V2] + [V3.replace("cccccccc", "%08x" % (0xc0000000 + i)).replace("#Third", "#K%d" % i)
                                  .replace(".43:", ".%d:" % (50 + i)) for i in range(8)])
        got = [exits.tproxy_port(e) for e in exits.pool(self.w.runner, roles=("ru",))]
        self.assertNotIn(held, got)
        self.assertTrue(cfg)


class HookRxFailTest(RuWorld):
    """REV-s1-q2-2 З-2 (опыт Е3): nft отказал в удалении из gw_rx (не ENOENT) — адрес не становится живым с чужой меткой."""

    IP = "10.77.8.10"
    ikev2 = _net_rx.HookRxTest.ikev2

    def refused(self, fn):
        real = self.w.runner.run

        def refuse(argv, **kw):
            inp = kw.get("input") or ""
            if "delete element" in inp and "gw_rx" in inp:
                return Result(argv, 1, "", "Error: Could not process rule: Cannot allocate memory\n")
            return real(argv, **kw)
        with mock.patch.object(self.w.runner, "run", side_effect=refuse):
            return fn()

    def test_a_stale_mark_keeps_the_address_closed(self):
        self.ikev2()
        net.set_ru(self.w.runner, state.load(), self.IP, False, self.ru_rows[0]["mark"])   # the old owner, no «down»
        st = state.load()
        st["gateway"]["devices"][0]["rules"] = "all_exit"
        state.save(st)
        self.refused(lambda: hooks.hook("ikev2", "up", "telefon-0001", self.IP, self.w.runner))
        self.assertIn(self.IP, self.w.nft.elements("gw_rx"))
        self.assertNotIn(self.IP, self.w.nft.elements("gw_live"))

    def test_no_mark_there_the_refusal_is_harmless(self):
        self.ikev2()
        st = state.load()
        st["gateway"]["devices"][0]["rules"] = "all_exit"
        state.save(st)
        self.assertNotIn(self.IP, self.w.nft.elements("gw_rx"))
        self.assertTrue(self.refused(lambda: net.set_ru(self.w.runner, state.load(), self.IP, False, None)))

    def test_mark_branch_unreadable_map_is_closed(self):
        """REV-q-int П-1 (проба R2b): с меткой — nft отказал в записи, удалении и чтении gw_rx: False (адрес не живой)."""
        self.ikev2()
        old, new = self.ru_rows[0]["mark"], self.ru_rows[-1]["mark"]
        net.set_ru(self.w.runner, state.load(), self.IP, False, old)       # the old owner, no «down»
        real = self.w.runner.run

        def refuse(argv, **kw):
            inp = kw.get("input") or ""
            if "gw_rx" in inp and ("delete element" in inp or "add element" in inp):
                return Result(argv, 1, "", "Error: Could not process rule: Cannot allocate memory\n")
            if "list" in argv and "gw_rx" in argv:
                return Result(argv, 1, "", "Error: Could not process rule: Cannot allocate memory\n")
            return real(argv, **kw)
        with mock.patch.object(self.w.runner, "run", side_effect=refuse):
            self.assertFalse(net.set_ru(self.w.runner, state.load(), self.IP, False, new))
        self.assertEqual(self.w.nft.elements("gw_rx").get(self.IP), old)

    def test_mark_branch_no_rx_left_is_free(self):
        """REV-q-int П-1, REV-q-int-2 П-1: запись метки отказала, но в gw_rx адреса нет и в gw_rd нет чужой записи
        (чтение удалось) — True. До REV-q-int здесь был False при любой читаемой карте; True теперь только без чужого
        gw_rd (``StaleRdTest``)."""
        self.ikev2()
        new = self.ru_rows[-1]["mark"]
        real = self.w.runner.run

        def refuse(argv, **kw):
            inp = kw.get("input") or ""
            if "gw_rx" in inp and ("delete element" in inp or "add element" in inp):
                return Result(argv, 1, "", "Error: Could not process rule: Cannot allocate memory\n")
            return real(argv, **kw)
        self.assertNotIn(self.IP, self.w.nft.elements("gw_rx"))
        with mock.patch.object(self.w.runner, "run", side_effect=refuse):
            self.assertTrue(net.set_ru(self.w.runner, state.load(), self.IP, False, new))



class StaleRdTest(RuWorld):
    """REV-q-int-2 Н-1/Н-2: прежний владелец адреса шёл «Россия напрямую» (адрес в gw_rd), его «down» не пришёл; у нового
    записи nft отказали — адрес свободен, только если в таблице нет ни записи gw_rx, ни чужой записи gw_rd."""

    IP = "10.77.8.10"
    ikev2 = _net_rx.HookRxTest.ikev2

    def setUp(self):
        super().setUp()
        self.ikev2()
        net.set_ru(self.w.runner, state.load(), self.IP, True, None)       # the old owner: Russia direct, no «down»
        self.assertIn(self.IP, self.w.nft.elements("gw_rd"))
        self.new = self.ru_rows[-1]["mark"]

    def refused(self, fn, only=None):
        real = self.w.runner.run

        def refuse(argv, **kw):
            inp = kw.get("input") or ""
            if "element" in inp and (only is None or only in inp):
                return Result(argv, 1, "", "Error: Could not process rule: Cannot allocate memory\n")
            return real(argv, **kw)
        with mock.patch.object(self.w.runner, "run", side_effect=refuse):
            return fn()

    def set_ru(self, rd_on, mark, rx=True):
        return lambda: net.set_ru(self.w.runner, state.load(), self.IP, rd_on, mark, rx=rx)

    def test_mark_branch_all_refused(self):
        """Н-1 (проба test_r5 STALE rd): с меткой, все записи отказали — False, gw_rd прежнего на месте."""
        self.assertFalse(self.refused(self.set_ru(False, self.new)))
        self.assertIn(self.IP, self.w.nft.elements("gw_rd"))

    def test_mark_branch_only_rd_refused(self):
        """С меткой, gw_rx записался, gw_rd — нет: False (раньше «таблица без gw_rd» — True)."""
        self.assertFalse(self.refused(self.set_ru(False, self.new), only="gw_rd"))

    def test_no_mark_branch_all_refused(self):
        """Н-2 (проба NOMARK): без метки, все записи отказали — False."""
        self.assertFalse(self.refused(self.set_ru(False, None)))

    def test_no_mark_branch_only_rd_refused(self):
        """Без метки, удаление из gw_rx прошло, gw_rd — нет: False."""
        self.assertFalse(self.refused(self.set_ru(False, None), only="gw_rd"))

    def test_no_russian_group_refused(self):
        """Политика без российской группы (rx=False): запись gw_rd отказала — False."""
        self.assertFalse(self.refused(self.set_ru(False, None, rx=False)))

    def test_own_rd_entry_is_free(self):
        """Новый владелец сам «Россия напрямую» (rd_on): запись в gw_rd — его, отказ записи не закрывает адрес."""
        self.assertTrue(self.refused(self.set_ru(True, None)))
        self.assertTrue(self.refused(self.set_ru(True, None, rx=False)))

    def test_unreadable_rd_is_closed(self):
        """gw_rd не прочитать — False (fail-closed), даже когда в gw_rx адреса нет."""
        real = self.w.runner.run

        def refuse(argv, **kw):
            if "element" in (kw.get("input") or "") or ("list" in argv and "gw_rd" in argv):
                return Result(argv, 1, "", "Error: Could not process rule: Cannot allocate memory\n")
            return real(argv, **kw)
        with mock.patch.object(self.w.runner, "run", side_effect=refuse):
            self.assertFalse(net.set_ru(self.w.runner, state.load(), self.IP, False, self.new))
            self.assertFalse(net.set_ru(self.w.runner, state.load(), self.IP, False, None))

    def test_hook_keeps_the_address_closed(self):
        """Н-1 насквозь: хук нового владельца с российской группой — адрес не в gw_live, ru_failed."""
        res = self.refused(lambda: hooks.hook("ikev2", "up", "telefon-0001", self.IP, self.w.runner))
        self.assertTrue(res.get("ru_failed"))
        self.assertNotIn(self.IP, self.w.nft.elements("gw_live"))

class RulesApplyRuExitsTest(RuWorld):
    """REV-q-int З-1 (проба R3): обновление списков (``rules.ops.apply_system``) рисует xray с российскими выходами."""

    two_people = _ru_ports.RuPortsTest.two_people

    def ru_bits(self):
        import json
        cfg = json.loads(util.read_text(xray.config_path()) or "{}")
        obs = sorted(o["tag"] for o in cfg.get("outbounds") or [] if str(o.get("tag", "")).startswith("gr-"))
        ins = sorted(i["tag"] for i in cfg.get("inbounds") or [] if str(i.get("tag", "")).startswith("tproxy-e"))
        return obs, ins

    def test_rules_update_keeps_the_russian_exits(self):
        from kaskad.gw import ops as gops
        from kaskad.rules import ops as rops
        self.two_people()
        with mock.patch.object(xray, "_fresher", lambda seq: True):
            gops._apply(state.load(), secrets.load(), self.w.runner, probe=False)
            before = self.ru_bits()
            self.assertEqual(len(before[0]), 2, before)              # both Russian exits drawn by «gw apply»
            rops.apply_system(self.w.runner)
        self.assertEqual(self.ru_bits(), before)


class RuHeldTextTest(RuWorld):
    """REV-q-int П-3: ``ru_held`` в панели и боте говорит, что делать человеку, — без команд (RU и EN)."""

    def test_the_panel_text_says_what_to_do(self):
        from kaskad import i18n
        from kaskad.gw import ops as gops
        for lang, where, what in (("ru", "«Выходы»", "изменение внутреннего выхода"),
                                  ("en", "“Exits”", "change of a domestic exit")):
            with i18n.use_lang(lang):
                text = gops._script_warnings(["@K3:egress_warn:ru_held"])
            text = " ".join(str(w) for w in text)
            self.assertIn(where, text, lang)
            self.assertIn(what, text, lang)                 # REV-q-int-2 П-2: which action to repeat
            self.assertNotIn("kaskadctl", text, lang)
            self.assertNotIn("gw apply", text, lang)
