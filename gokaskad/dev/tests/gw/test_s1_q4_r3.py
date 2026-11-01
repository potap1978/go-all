"""T15 Q4, круг 3 (REV-s1-q4-2).

С-1: Д-1 держится и при сбое применения. install.sh пишет метку «ждут применения» (.ru-hold) до записи upstream-ru.txt;
ядро снимает её только когда и таблица, и xray шлюза применены по файлу с её отпечатком (gw apply или ход сборщика), а
сторож ног (ensure_legs) и его --egress-awg-heal до того ноги не поднимают.

З-1: откат смены роли на уровне политики — строка на прежнее место, закрепления и снимок не тронуты (сброс — только
после записи состояния).

З-2: выбор российской группы D-068 — где больше ключей подписки, поровну — первая по порядку групп."""

import json
import os
from unittest import mock

from kaskad import paths, state
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import collect, exits, exitsubs, ops, policy, xray
from kaskad.gw import pins as PN
from tests.fakes_gw import VLESS_A, VLESS_B, awg_line, write_pool
from tests.gw.test_pool_ru import write_ru
from tests.gw.test_s1_ru_pool_ops import HOME, ROUTER, RuCase, ru_key
from tests.test_gw_ops import CliGwCase



def ru_awg():
    return awg_line(7, name="Router", endpoint="203.0.113.50:51820", address="10.8.7.2/32")     # needs the parser


def write_hold(h, *legs):
    p = os.path.join(paths.egress_dir(), "awg", ".ru-hold")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(x + "\n" for x in (h,) + legs))


class HeldLegsCoreTest(CliGwCase):
    def world(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        self.ru_awg = ru_awg()
        write_ru([self.ru_awg])
        write_hold(exits.lines_hash([self.ru_awg]), "kawg201")
        if "kawg201" in self.w.ip.links:
            self.w.ip.drop_link("kawg201")

    def test_hold_parsed_and_the_watchdog_leaves_the_leg(self):
        self.world()
        self.assertEqual(exits.ru_hold(), {"hash": exits.lines_hash([self.ru_awg]), "legs": ["kawg201"], "lines": [201]})
        res = exits.ensure_legs(self.w.runner, exits.pool(self.w.runner, roles=exits.ROLES), force=True)
        self.assertNotIn("kawg201", res["missing"])
        self.assertNotIn("kawg201", self.w.ip.links)

    def test_gw_apply_with_xray_failing_keeps_the_mark_then_the_collector_releases(self):
        self.world()
        self.w.xray.fail = "Failed to build outbound config\n"
        self.ok("gw", "apply")                                         # xray failing is a warning of gw apply
        self.assertIsNotNone(exits.ru_hold())
        self.assertNotIn("kawg201", self.w.ip.links)
        collect.tick(self.w.runner)                                    # xray still refuses: the leg stays closed
        self.assertIsNotNone(exits.ru_hold())
        self.assertNotIn("kawg201", self.w.ip.links)
        self.w.xray.fail = None
        self.now += 400                                                # past HOLD_RETRY_S
        with mock.patch.object(xray, "REJECT_RETRY_S", 0):             # the refused text is tested again
            collect.tick(self.w.runner)
        self.assertIsNone(exits.ru_hold())
        self.assertIn("kawg201", self.w.ip.links)                      # brought up right after the release

    def test_the_collector_releases_only_with_the_table_applied_in_its_round(self):
        """xray by the new file, but the table was not applied this round (its retry not due): the mark stays."""
        self.world()
        self.w.xray.fail = "Failed to build outbound config\n"
        self.ok("gw", "apply")
        self.w.xray.fail = None
        collect.MEM.hold_try = collect._mono()                         # a full apply just tried: not due
        with mock.patch.object(xray, "REJECT_RETRY_S", 0):
            res = collect.tick(self.w.runner)
        self.assertNotIn("nft_ok", res["exits"])
        self.assertIsNotNone(exits.ru_hold())
        self.assertNotIn("kawg201", self.w.ip.links)

    def test_gw_apply_releases_and_brings_the_leg_up(self):
        self.world()
        self.ok("gw", "apply")
        self.assertIsNone(exits.ru_hold())
        self.assertIn("kawg201", self.w.ip.links)

    def test_another_file_never_releases(self):
        """The mark waits for another file (install.sh wrote it before the file and still holds the lock of the pool):
        applied by this one — it stays. The lock of the pool free — tests/gw/test_s1_q2_hold.py (REV-s1-q4-3 З-1)."""
        from tests.gw.test_s1_q2_hold import pool_lock_held
        self.world()
        write_hold("0123456789abcdef", "kawg201")
        with pool_lock_held():
            self.ok("gw", "apply")
            collect.tick(self.w.runner)
        self.assertEqual(exits.ru_hold()["hash"], "0123456789abcdef")
        self.assertNotIn("kawg201", self.w.ip.links)

    def test_release_needs_the_lines_it_was_applied_by(self):
        self.world()
        rows = exits.pool(self.w.runner, roles=exits.ROLES)
        write_ru([self.ru_awg, VLESS_B])                                    # the file changed after the pool was read
        write_hold(exits.lines_hash([self.ru_awg, VLESS_B]), "kawg201")
        self.assertFalse(exits.ru_hold_release(rows, "test"))
        self.assertTrue(exits.ru_hold_release(exits.pool(self.w.runner, roles=exits.ROLES), "test"))
        self.assertIsNone(exits.ru_hold())


class RoleUndoAtItsPlaceTest(RuCase):
    """З-1 (exp test_rev2_py): the pool [A, B], A moved to «Дом», the apply of the policy fails — A back at ITS place
    (not at the end: the lines below would shift), its pin and its record of the snapshot untouched."""

    def test_order_pins_and_snapshot_survive(self):
        write_pool([VLESS_A, VLESS_B])
        eid = exits.exit_id(VLESS_A)
        snap = exits.load_snapshot()
        snap.setdefault("exits", {})[eid] = {"alive": True, "state": "ok", "reasons": [], "norm_rtt": 40}
        exits.save_snapshot(snap)
        PN._write(paths.pins_json(), {"version": PN.VERSION, "saved": 1, "gw_all": None, "groups": {
            "main": {"pins": {"d-00000001": {"exit": eid, "tier": "t1", "since": 1, "why": "new"}}}}})
        real_apply, real_en, real_req = ops._apply, ops.model.enabled, ops.require_engine

        def bad_apply(*a, **kw):
            raise KaskadError("Конфиг xray невалиден.")
        ops._apply = bad_apply
        ops.model.enabled = lambda st: True
        ops.require_engine = lambda st: None
        try:
            with self.assertRaises(KaskadError):
                self.move(eid, HOME)
        finally:
            ops._apply, ops.model.enabled, ops.require_engine = real_apply, real_en, real_req
        self.assertEqual(exits.pool_lines("abroad"), [VLESS_A, VLESS_B])
        self.assertEqual(exits.pool_lines("ru"), [])
        self.assertEqual((exits.load_snapshot().get("exits") or {}).get(eid, {}).get("norm_rtt"), 40)
        with open(paths.pins_json(), encoding="utf-8") as f:
            self.assertIn("d-00000001", json.load(f)["groups"]["main"]["pins"])

    def test_undo_inserts_at_the_place_from_the_middle(self):
        """The undo of ``_role_back`` itself: the line of place 2 of [A, B, C] — back at place 2."""
        c = ru_key("C", uuid="cccccccc-1111-2222-3333-444444444444", host="203.0.113.62")
        write_pool([VLESS_A, c])
        write_ru([VLESS_B])
        self.assertIsNone(ops._role_back(self.w.runner, "x", "abroad", "ru", VLESS_B, 2, True))
        self.assertEqual(exits.pool_lines("abroad"), [VLESS_A, VLESS_B, c])
        self.assertEqual(exits.pool_lines("ru"), [])


class StandbyGroupTest(RuCase):
    """З-2 (D-068, порча M3): the Russian group holding most keys of the subscription; a tie — the first by order."""
    DACHA = "g-0d0d0d"

    def setUp(self):
        super().setUp()
        with state.transaction() as st:
            doc = st["gateway"]["exit_policy"]
            doc["groups"].append(policy.default_group(self.DACHA, "ru", name="Дача"))

    def place(self, where):
        """{exit: group} into the first tier of each group."""
        with state.transaction() as st:
            doc = st["gateway"]["exit_policy"]
            for g in doc["groups"]:
                if g.get("id") in where.values():
                    t = (g.get("tiers") or [None])[0]
                    if t is None:
                        t = policy.default_tier("t1")
                        g["tiers"] = [t]
                    t["exits"] = [e for e, gid in where.items() if gid == g["id"]]

    def standby(self, keys):
        lines = [ru_key(n, uuid="%s-1111-2222-3333-444444444444" % (c * 8), host="203.0.113.%d" % (70 + i))
                 for i, (n, c) in enumerate(zip("PQR", "pqr"))]
        ids = {}
        eids = [exits.pool_id(l, ids) for l in lines]
        where = dict((eids[i], g) for i, g in enumerate(keys))
        self.place(where)
        sub = {"id": "s-1", "keys": eids}
        return exitsubs._standby(sub, ["e-0123456789"], lines, ids)

    def test_most_keys_wins(self):
        self.assertEqual(self.standby([HOME, self.DACHA, self.DACHA]), self.DACHA)
        self.assertEqual(state.load()["gateway"]["exits"]["e-0123456789"]["group"], self.DACHA)

    def test_a_tie_the_first_by_order(self):
        self.assertEqual(self.standby([self.DACHA, HOME]), HOME)       # HOME comes before DACHA in the groups


class LineBreakReasonTest(RuCase):
    """REV-s1-q4-2 П-2: a key with an invisible line break in its name — refused WITH the reason (was: «код 1»)."""

    def test_the_reason_is_said(self):
        bad = ROUTER.replace("#Router", "#Ro uter")
        with self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, bad, group=HOME)
        self.assertIn("разрыв строки", str(c.exception))
        self.assertEqual(self.ru(), [])
