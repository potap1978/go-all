"""T7, круг 2 проверок (REV-s1-t7, REV-s1-t7-r45-sec): the order «gone, rev, then the first policy» of the writes over
a group on a node with no policy stored (С-1), the names and the speed test outside ``rev`` there too (П-2), the
subscriptions of a deleted group under their lock (З-1), the pins of a copy of a key that goes (З-2), the text of an
empty exit form (П-1), an old hint of a key added again without a group (П-3), the limits of a policy (безопасность
З-1, П-1)."""

import threading
import time
from unittest import mock

from kaskad import errors, snapshot, state, util
from kaskad.gw import exits, exitsubs, ops, policy as P, policy_ops as pops
from tests.fakes_gw import VLESS_B
from tests.panel.test_panel_policy import PolicyCase


class NoPolicy(PolicyCase):
    def no_policy(self, gen):
        """A node whose state keeps no ``exit_policy``: ``gen`` 1 — updated from 3.3.x with the gateway on; 2 — a new
        one (its first write gives «Стандарт»)."""
        st = state.load()
        st["gen"] = gen
        st["gateway"].pop("exit_policy", None)
        state.save(st)
        snapshot.forget()


class ResetOnNodeWithoutPolicy(NoPolicy):
    def check(self, gen):
        self.no_policy(gen)
        doc = self.policy()
        rev = doc["rev"]
        self.assertEqual(doc["policy"]["groups"][0]["preset"], "compat")
        body = {"preset": "standard", "group": "main", "rev": rev}
        dry = self.ok("POST", "/api/gw/policy/reset?dry=1", body)
        self.assertTrue(dry["dry"])
        self.assertEqual(dry["rev"], rev)
        self.assertEqual(self.policy()["rev"], rev)                  # a dry run writes nothing
        out = self.ok("POST", "/api/gw/policy/reset", body)          # the rev of the read — the first time
        self.assertEqual(out["preset"], "standard")
        after = self.policy()
        self.assertEqual(after["policy"]["groups"][0]["preset"], "standard")
        self.assertEqual(after["rev"], out["rev"])
        self.assertGreater(out["rev"], rev)
        self.assertEqual(self.err(409, "POST", "/api/gw/policy/reset", body)["code"], "policy_changed")

    def test_updated_node_gen_1(self):
        self.check(1)

    def test_new_node_before_its_first_policy(self):
        self.check(2)

    def test_gone_group_before_rev(self):
        self.no_policy(1)
        doc = self.err(404, "POST", "/api/gw/policy/reset", {"preset": "standard", "group": "g-abcdef", "rev": 1})
        self.assertEqual(doc["code"], "gone")
        doc = self.err(404, "DELETE", "/api/gw/groups/g-abcdef?rev=1")
        self.assertEqual(doc["code"], "gone")

    def test_set_policy_and_buttons_on_updated_node(self):
        self.no_policy(1)
        doc = self.policy()
        self.ok("PUT", "/api/gw/policy", {"policy": doc["policy"], "rev": doc["rev"]})
        self.no_policy(1)
        self.ok("POST", "/api/gw/exits/%s/manual" % self.aid, {"state": "standby", "rev": self.rev()})


class NamesOutsideRev(NoPolicy):
    def test_rename_and_speed_test_keep_rev_on_updated_node(self):
        self.no_policy(1)
        r0 = self.policy()["rev"]
        self.ok("PUT", "/api/gw/groups/main", {"name": "Моя"})
        self.assertIsNotNone(P.stored(state.load()))                # written: «Как раньше», the readers' view
        self.assertEqual(self.policy()["rev"], r0)
        sp = self.ok("GET", "/api/gw/probes/speed")
        self.ok("PUT", "/api/gw/probes/speed", {"max_mb": 20, "ver": sp["ver"]})
        self.assertEqual(self.policy()["rev"], r0)
        self.assertEqual(self.policy()["policy"]["groups"][0]["preset"], "compat")


class DeleteGroupSubs(PolicyCase):
    def setUp(self):
        super().setUp()
        self.gid = self.add_group("Кино")
        self.subs = {"subs": [{"id": "s-abcdef", "group": self.gid}, {"id": "s-000001", "group": "main"}]}

    def patched(self, locked):
        saved = []
        return saved, [mock.patch.object(pops, "_subs", side_effect=lambda: self.subs),
                       mock.patch.object(exitsubs, "load", side_effect=lambda: self.subs),
                       mock.patch.object(exitsubs, "save", side_effect=saved.append),
                       mock.patch.object(exitsubs, "_locked", locked)]

    def test_busy_lock_of_subscriptions_writes_nothing(self):
        def busy():
            raise errors.LockedError("занято")
        saved, ps = self.patched(busy)
        before = self.state_bytes()
        for p in ps:
            p.start()
        try:
            doc = self.err(409, "DELETE", "/api/gw/groups/%s?rev=%s&subs_to=main" % (self.gid, self.rev()))
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(doc["code"], errors.LOCKED)
        self.assertEqual(self.state_bytes(), before)                 # the group is there, nothing half-done
        self.assertIn(self.gid, [g["id"] for g in self.policy()["policy"]["groups"]])
        self.assertEqual(saved, [])

    def test_the_subscriptions_move_with_the_group_gone(self):
        import contextlib
        held = []

        @contextlib.contextmanager
        def locked():
            held.append(True)
            yield
            held.pop()
        saved, ps = self.patched(locked)
        seen = []
        real = pops.run

        def run(*a, **kw):
            seen.append(bool(held))                                  # the write goes under the lock of the subs
            return real(*a, **kw)
        ps.append(mock.patch.object(pops, "run", run))
        for p in ps:
            p.start()
        try:
            self.ok("DELETE", "/api/gw/groups/%s?rev=%s&subs_to=main" % (self.gid, self.rev()))
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(seen, [True])
        self.assertEqual([s["group"] for s in saved[-1]["subs"]], ["main", "main"])
        self.assertNotIn(self.gid, [g["id"] for g in self.policy()["policy"]["groups"]])

    def test_a_refused_delete_leaves_the_subscriptions(self):
        import contextlib

        @contextlib.contextmanager
        def locked():
            yield
        saved, ps = self.patched(locked)
        for p in ps:
            p.start()
        try:
            self.err(409, "DELETE", "/api/gw/groups/%s?rev=%s&subs_to=main" % (self.gid, self.rev() - 1))
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(saved, [])


class PinsFollowWithRunner(PolicyCase):
    def test_pins_go_to_the_copy_that_stays_without_routes_to_it(self):
        self.both_ok()
        self.collect()
        a = self.pinned()
        self.assertIsNotNone(a)
        b = self.vid if a == self.aid else self.aid
        self.assertEqual(ops.hand_over({a: b}, runner=self.w.runner), 0)     # no route names the copy
        self.assertEqual(self.pinned(), b)


class ExitFormText(PolicyCase):
    def test_empty_form_names_the_fields_it_takes(self):
        doc = self.err(400, "PUT", "/api/gw/exits/%s" % self.aid, {})
        self.assertIn("quota", doc["error"])
        self.assertNotIn("enabled", doc["error"])


class OldHintOfKey(PolicyCase):
    def test_key_added_again_without_group_forgets_an_old_hint(self):
        gid = self.add_group("Кино")
        eid = exits.exit_id(VLESS_B)
        with state.transaction() as st:                              # a panel stopped during the script of an add
            st["gateway"]["exits"][eid] = {"group": gid}
        self.ok("POST", "/api/gw/exits", {"key": VLESS_B})
        st = state.load()
        self.assertNotEqual((st["gateway"]["exits"].get(eid) or {}).get("group"), gid)
        self.assertEqual(P.group_of(P.load(st), eid)["id"], "main")


class PolicyLimits(PolicyCase):
    def test_too_many_tiers_refused_fast(self):
        doc = P.default_doc("standard")
        doc["groups"][0]["tiers"] = [P.default_tier("z%d" % i) for i in range(2000)]
        t0 = time.time()
        errs, _w = P.validate(doc)
        self.assertLess(time.time() - t0, 2)
        self.assertIn("ep.err.tiers_many", [e["code"] for e in errs])
        doc["groups"][0]["tiers"] = [P.default_tier("z%d" % i) for i in range(P.MAX_TIERS)]
        errs, _w = P.validate(doc)
        self.assertNotIn("ep.err.tiers_many", [e["code"] for e in errs])

    def test_tier_duplicates_still_warned(self):
        doc = P.default_doc("standard")
        doc["groups"][0]["tiers"] = [dict(P.default_tier("a"), name="Быстрые"), P.default_tier("b"),
                                     dict(P.default_tier("c"), name="быстрые ")]
        _e, warns = P.validate(doc)
        dup = [w for w in warns if w["code"] == "ep.warn.tier_dup"]
        self.assertEqual([w["field"] for w in dup], ["groups[0].tiers[2].name"])

    def test_unknown_fields_bounded(self):
        doc = self.policy()
        pol = doc["policy"]
        pol["groups"][0]["junk"] = "x" * (P.MAX_BYTES + 1)
        before = self.state_bytes()
        out = self.err(400, "PUT", "/api/gw/policy", {"policy": pol, "rev": doc["rev"]})
        self.assertIn("ep.err.too_big", str(out))
        self.assertEqual(self.state_bytes(), before)


class ConfigLock(PolicyCase):
    def test_writers_of_the_config_wait_for_each_other(self):
        from kaskad import i18n
        from kaskad.bot import config as bconf
        inside, go, done = threading.Event(), threading.Event(), threading.Event()

        def holder():
            with util.config_locked():
                inside.set()
                go.wait(5)

        def writer():
            bconf.save_value("BOT_X", "1")
            done.set()
        t1 = threading.Thread(target=holder)
        t1.start()
        inside.wait(5)
        t2 = threading.Thread(target=writer)
        t2.start()
        time.sleep(0.3)
        self.assertFalse(done.is_set())                              # waits for the lock of the other writer
        go.set()
        t1.join(5)
        t2.join(5)
        self.assertTrue(done.is_set())
        with util.config_locked():                                   # nested in one thread — no deadlock
            i18n.set_lang("ru")
        text = self.read("/etc/kaskad/config")
        self.assertIn('BOT_X="1"', text)
        self.assertIn('KASKAD_LANG="ru"', text)
