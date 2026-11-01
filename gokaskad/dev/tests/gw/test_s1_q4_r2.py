"""T15 Q4, круг 2 (REV-s1-q4, SEC-s1-q4): смена роли атомарна — проверки группы, яруса и политики до переноса строки
(С2б, З-1); сбой любой записи — откат, ключ ровно в одном файле, текст ошибки правдивый (С2а, З-2); откат не трогает
копию, которая была в целевом файле до переноса; квота записи снимка переживает смену роли (П1)."""

from kaskad import state
from kaskad.errors import ConflictError, KaskadError, LockedError
from kaskad.gw import exits, exitsubs, ops, policy, policy_ops
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool, write_ru_pool
from tests.gw.test_s1_ru_pool_ops import HOME, ROUTER, RuCase
from tests.test_335_rename import key
from tests.test_gw_exitsubs import IP_URL, b64

NEW_UUID = "77777777-2222-3333-4444-555555555555"


class RoleMoveAtomicTest(RuCase):
    def setUp(self):
        super().setUp()
        self._undo_wait = ops.ROLE_UNDO_WAIT
        ops.ROLE_UNDO_WAIT = 0
        self.addCleanup(setattr, ops, "ROLE_UNDO_WAIT", self._undo_wait)

    def fail_next(self, which, codes):
        """The next calls of ``--egress-replace`` («abroad») / ``--egress-ru-apply`` («ru»): a code of ``codes`` each
        (None — the real work), then the real work."""
        s = self.w.script
        real = s.egress_replace if which == "abroad" else s.egress_ru_apply
        left = list(codes)

        def fake(want, text):
            code = left.pop(0) if left else None
            if code is None:
                return real(want, text)
            return code, "Конфиг xray невалиден.\n", ""
        setattr(s, "egress_replace" if which == "abroad" else "egress_ru_apply", fake)

    def state_of(self, line):
        return (line in self.pool(), line in self.ru())

    def test_second_write_fails_abroad_to_ru(self):
        write_pool([VLESS_A, VLESS_B])
        eid = exits.exit_id(VLESS_A)
        self.fail_next("abroad", [1])                        # the line written into upstream-ru.txt, then out of upstream.txt fails
        with self.assertRaises(KaskadError) as c:
            self.move(eid, HOME)
        self.assertIn("оставлен на прежнем месте", str(c.exception))
        self.assertEqual(self.state_of(VLESS_A), (True, False))       # exactly one file: where it was
        self.assertEqual(self.pool(), [VLESS_A, VLESS_B])
        self.assertIn(eid, self.ids())
        self.assertNotEqual(self.group_of(eid), HOME)

    def test_second_write_busy_ru_to_abroad(self):
        write_ru_pool([ROUTER])
        rid = exits.exit_id(ROUTER)
        self.fail_next("ru", [6])                            # out of upstream-ru.txt: the pool busy
        with self.assertRaises(KaskadError) as c:
            self.move(rid, policy.MAIN)
        self.assertIn("повторите", str(c.exception))
        self.assertEqual(self.state_of(ROUTER), (False, True))
        self.assertEqual(self.ru_ids(), [rid])

    def test_first_write_fails_nothing_moves(self):
        write_pool([VLESS_A])
        eid = exits.exit_id(VLESS_A)
        self.fail_next("ru", [1])
        with self.assertRaises(KaskadError):
            self.move(eid, HOME)
        self.assertEqual(self.state_of(VLESS_A), (True, False))

    def test_the_undo_fails_too_honest_where(self):
        """The second write fails, and the undo (the copy out of upstream-ru.txt) fails every try: the error says the
        key is in both files, never «оставлен на прежнем месте»."""
        write_pool([VLESS_A])
        eid = exits.exit_id(VLESS_A)
        self.fail_next("abroad", [1])
        self.fail_next("ru", [None] + [1] * exitsubs.REPLACE_TRIES)
        with self.assertRaises(KaskadError) as c:
            self.move(eid, HOME)
        self.assertNotIn("оставлен", str(c.exception))
        self.assertIn("и в зарубежных, и во внутренних", str(c.exception))
        self.assertEqual(self.state_of(VLESS_A), (True, True))

    def test_the_undo_keeps_a_copy_that_was_there_before(self):
        """SEC-s1-q4 З-2 (опыт B): ROUTER in both files (SSH menu); moving the Russian copy abroad would change ids —
        refused; the foreign copy that was there before stays, the ids as before, nothing lost."""
        write_pool([ROUTER])
        write_ru_pool([ROUTER])
        before = exits.all_line_ids()
        ru_copy = [i for i, _n, r in before if r == "ru"][0]
        with self.assertRaises(KaskadError) as c:
            ops.exit_role_move(self.w.runner, ru_copy, "abroad")
        self.assertIn("оставлен на прежнем месте", str(c.exception))
        self.assertEqual((self.pool(), self.ru()), ([ROUTER], [ROUTER]))
        self.assertEqual(exits.all_line_ids(), before)

    def test_changed_meanwhile_takes_its_copy_out(self):
        """The file of the line changed between the two writes (rc 3): the copy this move added goes, the next try
        moves it — never «the same text there already» of its own try."""
        write_pool([VLESS_A, VLESS_B])
        eid = exits.exit_id(VLESS_A)
        self.fail_next("abroad", [3])
        self.move(eid, HOME)
        self.assertEqual(self.ru(), [VLESS_A])
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertEqual(self.ru_ids(), [eid])

    def test_bad_tier_refused_before_the_line_moves(self):
        """REV С2б, SEC З-1: a wrong tier — refused, no script ran, the line where it was."""
        write_pool([VLESS_A])
        eid = exits.exit_id(VLESS_A)
        n = len(self.calls())
        with self.assertRaises(policy_ops.Refused):
            self.move(eid, HOME, tier="nope")
        self.assertEqual(self.calls()[n:], [])
        self.assertEqual(self.state_of(VLESS_A), (True, False))
        write_ru_pool([ROUTER])
        rid = exits.exit_id(ROUTER)
        with self.assertRaises(policy_ops.Refused):
            self.move(rid, policy.MAIN, tier="nope")
        self.assertEqual(self.state_of(ROUTER), (False, True))
        self.assertEqual(self.calls()[n:], [])

    def test_the_write_of_the_policy_fails_the_line_goes_back(self):
        write_pool([VLESS_A])
        eid = exits.exit_id(VLESS_A)
        real = ops._change

        def busy(runner, mutate, rf=None):
            raise LockedError("Состояние занято.")
        ops._change = busy
        try:
            with self.assertRaises(LockedError):
                self.move(eid, HOME)
        finally:
            ops._change = real
        self.assertEqual(self.state_of(VLESS_A), (True, False))
        self.assertEqual(self.ids(), [eid])
        self.assertNotEqual(self.group_of(eid), HOME)
        self.move(eid, HOME)                            # and again: it moves
        self.assertEqual(self.state_of(VLESS_A), (False, True))
        self.assertEqual(self.group_of(eid), HOME)

    def test_quota_decision_survives(self):
        """REV П1: the owner's «Вернуть» of an exhausted quota (``quota.restored``) stays in the record of the
        snapshot; the rest of the record goes."""
        write_pool([VLESS_A])
        eid = exits.exit_id(VLESS_A)
        snap = exits.load_snapshot()
        snap.setdefault("exits", {})[eid] = {"state": "degraded", "reasons": ["loss"],
                                             "quota": {"period": "2026-10", "full": "2026-10", "restored": "2026-10"}}
        exits.save_snapshot(snap)
        self.move(eid, HOME)
        rec = exits.load_snapshot()["exits"][eid]
        self.assertEqual(rec, {"quota": {"period": "2026-10", "full": "2026-10", "restored": "2026-10"}})



class NewKeysOfAMovedSubscriptionTest(RuCase):
    """D-068 (SE §3.8 Н4, SPEC-groups §2.9): a subscription of «Основная», one of whose keys was moved to «Дом»; the
    provider gives a new key of the same server (another uuid): it goes to upstream-ru.txt «в запас» (sub_confirm) in
    «Дом», never into upstream.txt, «Основная» or the bot's proxy. Before: it went into upstream.txt silently."""

    def moved(self):
        self.add(IP_URL, b64([key("A"), VLESS_B]))
        a = self.ids()[0]
        self.move(a, HOME)
        self.assertEqual(self.ru(), [key("A")])
        return a

    def test_new_key_of_the_same_server_goes_to_standby_at_home(self):
        a = self.moved()
        n = len(self.w.script.replace_inputs) if hasattr(self.w.script, "replace_inputs") else 0
        self.feed.bodies[IP_URL] = b64([key("A"), VLESS_B, key("A new", uuid=NEW_UUID)])
        res = self.refresh()["results"][0]
        self.assertEqual(self.pool(), [VLESS_B])                       # not into the foreign pool
        self.assertEqual(self.ru(), [key("A"), key("A new", uuid=NEW_UUID)])
        new = [i for i in self.ru_ids() if i != a]
        self.assertEqual(len(new), 1)
        pref = state.load()["gateway"]["exits"][new[0]]
        self.assertEqual((pref.get("manual"), pref.get("manual_why"), pref.get("group")), ("standby", "sub_confirm", HOME))
        self.assertEqual(res["standby"], new)
        self.assertIn("поставлен в запас в группе «Дом»", res["text"])
        self.assertEqual((getattr(self.w.script, "replace_inputs", []))[n:], [])     # the bot's pool untouched
        self.assertEqual(self.group_of(new[0]), None)                  # placed by its hint, not ticked into «main»
        row = [e for e in exits.pool(self.w.runner, roles=exits.ROLES) if e["id"] == new[0]][0]
        self.assertEqual(row["role"], "ru")
        self.assertIn(new[0], exitsubs.load()["subs"][0]["keys"])

    def test_the_observation_names_the_subscription(self):
        """The event exit_standby (why sub_confirm) names the subscription: the Obs of the collector has it."""
        from kaskad.gw import collect
        a = self.moved()
        self.feed.bodies[IP_URL] = b64([key("A"), VLESS_B, key("A new", uuid=NEW_UUID)])
        self.refresh()
        new = [i for i in self.ru_ids() if i != a][0]
        rows = [e for e in exits.pool(self.w.runner, roles=exits.ROLES) if e["id"] in (new, a)]
        obs = collect._obs(state.load(), rows, {"exits": {}}, {}, 0, 0)
        self.assertEqual((obs[new]["sub"], obs[new]["sub_name"]), (exitsubs.load()["subs"][0]["id"],
                                                                  exitsubs.name_of(exitsubs.load()["subs"][0])))
        self.assertNotIn("sub", obs[a])

    def test_the_key_instead_of_the_moved_one(self):
        """The provider takes the moved key away and gives another uuid instead: also «в запас» at home."""
        self.moved()
        self.feed.bodies[IP_URL] = b64([VLESS_B, key("A again", uuid=NEW_UUID)])
        self.refresh()
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertEqual(self.ru(), [key("A again", uuid=NEW_UUID)])

    def test_no_key_at_home_as_before(self):
        self.add(IP_URL, b64([key("A"), VLESS_B]))
        self.feed.bodies[IP_URL] = b64([key("A"), VLESS_B, key("A new", uuid=NEW_UUID)])
        res = self.refresh()["results"][0]
        self.assertEqual(self.pool(), [key("A"), VLESS_B, key("A new", uuid=NEW_UUID)])
        self.assertEqual(self.ru(), [])
        self.assertEqual(res["standby"], [])



class RussianOnlyNodeDnsTest(RuCase):
    """REV-s1-q4 З1: a node with Russian keys only — xray draws no dns-in, so dnsmasq must not ask 127.0.0.1#10853
    (nobody listens there; with strict-order every name waits on it). Before: via_pool counted the Russian exits."""

    def dns_conf(self):
        from kaskad.gw import dns
        with open(dns.conf_path(), encoding="utf-8") as f:
            return f.read()

    def test_via_pool_by_the_foreign_exits_only(self):
        from kaskad.gw import xray
        write_ru_pool([ROUTER])
        st = state.load()
        rows = exits.pool(self.w.runner, roles=exits.ROLES)
        self.assertEqual([e["role"] for e in rows], ["ru"])
        self.assertFalse(xray.dns_via_pool(st, rows))
        write_pool([VLESS_A])
        self.assertTrue(xray.dns_via_pool(st, exits.pool(self.w.runner, roles=exits.ROLES)))

    def test_the_node_dns_with_a_russian_key_only(self):
        write_pool([])
        ops.exit_add(self.w.runner, ROUTER, group=HOME)
        ops.apply_all(self.w.runner)
        self.assertNotIn("127.0.0.1#10853", self.dns_conf())
        ops.exit_add(self.w.runner, VLESS_A)
        ops.apply_all(self.w.runner)
        self.assertIn("server=127.0.0.1#10853", self.dns_conf())



class DefaultOutboundNeverRussianTest(RuCase):
    """SEC-s1-q4 П-4: only Russian exits drawn — the first outbound of the gateway's xray (its default) is ``block``,
    never ``gr-…`` (the home router of someone). With a foreign exit — as before (the foreign one first)."""

    def test_first_outbound(self):
        from kaskad.gw import xray
        write_pool([])
        write_ru_pool([ROUTER])
        st = state.load()
        cfg = xray.render(st, {}, exits.pool(self.w.runner, roles=exits.ROLES))
        tags = [o["tag"] for o in cfg["outbounds"]]
        self.assertTrue([t for t in tags if t.startswith("gr-")], tags)
        self.assertEqual(tags[0], "block", tags)
        write_pool([VLESS_A])
        cfg = xray.render(st, {}, exits.pool(self.w.runner, roles=exits.ROLES))
        tags = [o["tag"] for o in cfg["outbounds"]]
        self.assertTrue(tags[0].startswith("gx-"), tags)


class ListingBothFilesTest(RuCase):
    """REV-s1-q4 З2: the list of subscriptions counts the keys of both files (a subscription of a Russian group, a key
    moved by a change of role). Before: «0 keys in the pool»."""

    def test_counts(self):
        from tests.gw.test_s1_ru_pool_ops import RU_URL, ru_key
        self.feed.bodies[exitsubs.normalize(RU_URL)[0]] = b64([ru_key("A")])
        exitsubs.add(self.w.runner, RU_URL, fetcher=self.feed, group=HOME)
        self.add(IP_URL, b64([key("A"), VLESS_B]))
        self.move(self.ids()[0], HOME)
        rows = exitsubs.listing()
        self.assertEqual([r["keys"] for r in rows], [1, 2])



class OtherFileRefusedEverywhereTest(RuCase):
    """REV-s1-q4 З3, SEC-s1-q4 З-4: «ключ уже в другом файле» on every way of adding — a subscription link through
    ``exit_add`` (install.sh drops the keys of upstream-ru.txt and marks ``egress_in_ru``: the core refuses), and a
    preview that fails (no way round the refusal); the id of the Russian exit never goes to another line."""

    def test_a_subscription_link_whose_keys_are_russian(self):
        write_ru_pool([ROUTER])
        rid = exits.exit_id(ROUTER)
        self.w.script.add_result = (4, "Не добавлено ключей: 1 — они уже во внутренних выходах.\n@K3:egress_in_ru:1\n"
                                       "Новых ключей не добавлено (дубликаты или пусто).\n", "")
        with self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, IP_URL)
        self.assertEqual(c.exception.data["code"], "key_in_other_role")
        self.assertIn("внутренних", str(c.exception))
        self.assertEqual(self.ru_ids(), [rid])

    def test_partly_russian_is_a_warning(self):
        write_ru_pool([ROUTER])
        self.w.script.add_result = (0, "Не добавлено ключей: 1 — они уже во внутренних выходах.\n@K3:egress_in_ru:1\n"
                                       "Добавлено ключей: 1.\n", "")
        res = ops.exit_add(self.w.runner, IP_URL)
        self.assertTrue([w for w in res["warnings"] if "внутренних" in str(w)])

    def test_a_failed_preview_is_no_way_round(self):
        write_ru_pool([ROUTER])
        rid = exits.exit_id(ROUTER)
        real = exits.preview

        def broken(*a, **kw):
            raise KaskadError("разборщик упал")
        exits.preview = broken
        try:
            with self.assertRaises(ConflictError) as c:
                ops.exit_add(self.w.runner, ROUTER.replace("#Router", "#Again"))
        finally:
            exits.preview = real
        self.assertEqual(c.exception.data["code"], "key_in_other_role")
        self.assertNotIn("--egress-add", self.calls())
        self.assertEqual(self.pool(), [])
        self.assertEqual(self.ru_ids(), [rid])



class HealBothFilesTest(RuCase):
    """SEC-s1-q4 З-3: after a refusal of xray, ``exits.heal`` asks the parser about the lines of BOTH files (the same
    xray takes them); the numbers of the Russian lines are those of the pool (201+). Before: upstream.txt only — a
    Russian line xray refuses poisoned the config of the gateway."""

    def test_asked_both_and_numbered(self):
        write_pool([VLESS_A])
        write_ru_pool([ROUTER])
        asked = []

        class P(object):
            @staticmethod
            def xray_heal(lines, xray, timeout, run):
                asked.append(list(lines))
                return {"refused": [2], "new": [2], "legacy": [], "ext": [], "unchecked": False}
        real = exits.load_parser
        exits.load_parser = lambda runner: P()
        try:
            r = exits.heal(self.w.runner, "/bin/xray")
        finally:
            exits.load_parser = real
        self.assertEqual(asked, [[VLESS_A, ROUTER]])
        self.assertEqual((r["refused"], r["new"]), ([201], [201]))


class LineSeparatorRefusedTest(RuCase):
    """SEC-s1-q4 П-3: a key with a Unicode line separator inside (U+2028, U+0085, \\v …) — one line for bash, two for
    the core and the gateway: the second would get its own exit 20N without the hint of the group. Not written."""

    def test_not_written(self):
        for sep in ("\u2028", "\u0085", "\x0b", "\u2029"):
            with self.subTest(sep=repr(sep)):
                bad = ROUTER.replace("#Router", "#Ro" + sep + "uter")
                with self.assertRaises(ConflictError):
                    ops.exit_add(self.w.runner, bad, group=HOME)
                self.assertEqual(self.ru(), [])
                self.assertFalse([c for c in self.calls() if "--egress-ru-apply" in c])


if __name__ == "__main__":
    import unittest
    unittest.main()
