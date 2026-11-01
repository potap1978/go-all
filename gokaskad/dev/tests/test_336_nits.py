"""Kaskad 3.3.6 — the small findings of 3.3.5 in the core and the SSH menu (k3/handoff/BACKLOG-3.3.6.md п. 7–8):

    REV-335-4 №1  live.py ``_load_feed``: «exits» of any kind but a map (an empty list, null…) is a broken feed —
                  aside to .bad with a line in the journal;
    REV-335-4 №2  the handover of a copy failed not because the state was busy (a broken state.json, a core that does
                  not answer) — the refusal does not say «повторите»: a retry will not help;
    REV-335-5 №3  ``plan``, «returning»: a line the pool has already (the same text added by hand while it waited)
                  keeps its own id — the routes on it do not hang;
    REV-335-5 №4  an entry of «returning» whose key came back as a repeat (its other copy holds the routes) goes."""

import json
import os
import unittest
from unittest import mock

from kaskad import paths, state, util
from kaskad.errors import KaskadError, LockedError, StateError
from kaskad.gw import exits, exitsubs, live, ops
from kaskad.i18n import use_lang
from tests.fakes_gw import VLESS_B
from tests.helpers import RootCase
from tests.test_335_rename import RenameCase, b64, key
from tests.test_335_rev3 import RoutedCase
from tests.test_gw_exitsubs import IP_URL


class BrokenFeedTest(RootCase):
    """REV-335-4 №1: the feed whose «exits» is not a map — of any kind — goes aside with a line in the journal."""

    def load(self, text):
        feed = live.feed_path()
        os.makedirs(os.path.dirname(feed), exist_ok=True)
        if text is not None:
            with open(feed, "w", encoding="utf-8") as f:
                f.write(text)
        L = live.Live()
        L.said = {"e-1111111111": (100, "exit_dead")}
        L._load_feed()
        said = util.read_text(paths.P("/var/log/kaskad.log")) or ""
        return os.path.exists(feed + ".bad"), "live-feed.json" in said, L

    def test_not_a_map_of_any_kind_is_broken(self):
        for doc in ('{"v": 1, "exits": []}', '{"v": 1, "exits": null}', '{"v": 1, "exits": [1]}', '{"v": 1, "exits": ""}',
                    '{"v": 1, "exits": 0}', '[]', 'not json'):
            self.tearDown()
            self.setUp()
            bad, logged, L = self.load(doc)
            self.assertTrue(bad, doc)
            self.assertTrue(logged, doc)
            self.assertEqual(L.said, {}, doc)                       # the feed starts anew

    def test_a_feed_or_none_is_not_broken(self):
        for doc in (None, "", '{"v": 1}', '{"v": 1, "exits": {}}',
                    '{"v": 1, "exits": {"e-2222222222": {"told": {}}}}'):
            self.tearDown()
            self.setUp()
            bad, logged, _L = self.load(doc)
            self.assertFalse(bad, doc)
            self.assertFalse(logged, doc)


class HandoverBrokenTest(RoutedCase):
    """REV-335-4 №2: the routes of a copy did not move because the core failed (not the lock) — the key stays, the
    refusal gives the cause and no «повторите»; the lock busy — «повторите», as before."""

    def vias(self):
        return [r["via"] for r in self.routes()[:3]]

    def test_a_broken_state_is_no_retry(self):
        for lang, words, retry in (("ru", "Повтор не поможет", "повторите"), ("en", "A retry will not help", "try again")):
            with mock.patch.object(ops, "hand_over", side_effect=StateError("state.json is broken")), use_lang(lang):
                with self.assertRaises(KaskadError) as cm:
                    ops.exit_del(self.w.runner, self.old[1])
            self.assertNotIsInstance(cm.exception, LockedError)
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("state.json is broken", str(cm.exception))
            self.assertIn(words, str(cm.exception))
            self.assertNotIn(retry, str(cm.exception))
            self.assertEqual(self.ids(), self.old)                             # not removed
            self.assertEqual(self.vias(), ["exit:" + self.old[1]] * 3)

    def test_the_state_not_read_before_is_no_retry_either(self):
        with mock.patch.object(state, "load", side_effect=StateError("state.json is broken")), use_lang("ru"):
            with self.assertRaises(KaskadError) as cm:
                ops.exit_handover(2)
            self.assertNotIsInstance(cm.exception, LockedError)
            self.assertNotIn("повторите", str(cm.exception))
            self.assertEqual(ops.exit_handover(2, dry=True)["routes"], 0)     # the question of the menu: nothing said

    def test_the_lock_busy_still_says_retry(self):
        with mock.patch.object(ops, "hand_over", side_effect=LockedError("busy")), use_lang("ru"):
            with self.assertRaises(LockedError) as cm:
                ops.exit_handover(2)
        self.assertIn("повторите", str(cm.exception))

    def test_the_core_command_gives_code_1_and_the_cause(self):
        with mock.patch.object(ops, "hand_over", side_effect=StateError("state.json is broken")), use_lang("ru"):
            rc, out, _err = self.run_cli("gw", "exit-handover", "2", "--json")
        self.assertEqual(rc, 1)
        doc = json.loads(out)
        self.assertIs(doc["ok"], False)
        self.assertIn("state.json is broken", doc["error"])
        self.assertNotIn("повторите", doc["error"])


class ReturningTest(RenameCase):
    """REV-335-5 №3, №4."""

    def removed_waiting(self):
        """A key removed by hand, renamed by the subscription, «Вернуть исключённые» while the subscription lacks it:
        «returning» waits for it."""
        self.add(IP_URL, b64([key("10GB left"), VLESS_B]))
        eid = self.ids()[0]
        self.route_all(eid)
        ops.exit_del(self.w.runner, eid)
        self.feed.bodies[IP_URL] = b64([VLESS_B])
        exitsubs.restore(self.w.runner, "1", fetcher=self.feed)
        self.assertIn("returning", exitsubs.load()["subs"][0])
        return eid

    def test_a_line_added_by_hand_keeps_its_id(self):
        old = self.removed_waiting()
        ops.exit_add(self.w.runner, key("7GB left"))                    # the same text by hand, its routes on it
        mine = exits.exit_id(key("7GB left"))
        self.assertIn(mine, self.ids())
        with state.transaction() as st:
            st["gateway"]["devices"][0]["route"] = {"via": "exit:" + mine, "fail": "block"}
        self.feed.bodies[IP_URL] = b64([key("7GB left"), VLESS_B])      # the subscription gives it again
        self.refresh()
        self.assertEqual(exits.pool_id(key("7GB left")), mine)           # not renamed to the old id
        self.assertNotIn(old, self.ids())
        self.assertIn(mine, self.ids())
        self.assertEqual(state.load()["gateway"]["devices"][0]["route"]["via"], "exit:" + mine)   # the route works
        self.refresh()
        # REV-336-nits №1: the entry of «returning» is gone too — it would fire later under the old id
        self.assertNotIn("returning", exitsubs.load()["subs"][0])

    def test_without_a_line_by_hand_it_still_comes_back_with_its_id(self):
        old = self.removed_waiting()
        self.feed.bodies[IP_URL] = b64([key("7GB left"), VLESS_B])
        self.refresh()
        self.assertEqual(exits.pool_id(key("7GB left")), old)
        self.assertNotIn("returning", exitsubs.load()["subs"][0])


class ReturningDupTest(unittest.TestCase):
    """REV-335-5 №4: ``_record`` drops the entry of «returning» whose exit came back as a repeat (``dups``)."""

    def plan(self, dups):
        return {"handed": {}, "keys": [], "seen": [], "present": [], "pool": [], "ids": {}, "dups": dups, "full": 0}

    def test_a_repeat_ends_the_wait(self):
        sub = {"id": "s-1", "keys": [], "returning": {"h1": "e-aaaaaaaaaa", "h2": "e-cccccccccc"}}
        exitsubs._record({"subs": [sub]}, sub, self.plan([("e-aaaaaaaaaa", "e-bbbbbbbbbb", "ip_domain")]), 100)
        self.assertEqual(sub["returning"], {"h2": "e-cccccccccc"})          # the other one still waits
        exitsubs._record({"subs": [sub]}, sub, self.plan([("e-cccccccccc", "e-bbbbbbbbbb", "address")]), 101)
        self.assertNotIn("returning", sub)

    def test_no_repeat_keeps_the_wait(self):
        sub = {"id": "s-1", "keys": [], "returning": {"h1": "e-aaaaaaaaaa"}}
        exitsubs._record({"subs": [sub]}, sub, self.plan([]), 100)
        self.assertEqual(sub["returning"], {"h1": "e-aaaaaaaaaa"})


if __name__ == "__main__":
    unittest.main()
