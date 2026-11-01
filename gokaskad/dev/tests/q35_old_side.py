"""Q3/Q5, the migration (the owner's rule 24.10): the side of a RELEASED version. ``tests/test_q35_migration.py`` copies
this file into the tree of a commit of 3.3.7.4 / 3.3.7.6 / 3.3.7.8 and runs it there (``Q35_MODE``, ``Q35_IO``):

* ``make`` — the data of that version: the policy of the scenario «Дом» (a Russian group, ⚠РФ and the consent,
  «Россия напрямую»), its own placement of the same pool, its presets and switches of the pushes, the collector of that
  version for 4 rounds, a restart and 4 rounds more (what it says after a restart), the whole root on the disk; the
  bot of that version — pushes of a Russian group and its monitor.json;
* ``read`` — the rollback: that version reads the data written by the new one (a policy with a level more, the events
  ``node_ru_*``, the new args of ``exit_failover``, monitor.json with ``ru_told``) without an error.

Only what all three versions have is used. Not a test of this tree: no ``test_`` in the name, every case is skipped
without ``Q35_MODE``."""

import copy
import json
import os
import shutil
import unittest
from unittest import mock

from kaskad import state
from kaskad.gw import collect, exits, live, model
from kaskad.gw import policy as P
from tests.bot.test_monitor import S1PushCase, ev
from tests.gw.test_ru_target import HOME, RuWorld

MODE = os.environ.get("Q35_MODE")
IO = os.environ.get("Q35_IO", "")
OK = {"checked": False, "offline": False, "ru_offline": False}
MONITOR = "/etc/kaskad/bot_state/monitor.json"
RU = dict(group=HOME, group_name="Дом", role="ru")


def put(name, obj):
    with open(os.path.join(IO, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True)


def get(name):
    with open(os.path.join(IO, name), encoding="utf-8") as f:
        return json.load(f)


def need(mode):
    if MODE != mode:
        raise unittest.SkipTest("Q35_MODE=%s" % mode)


def said(e):
    return [e.get("kind"), e.get("exit"), e.get("group")]


def dead_router(e):
    """The probes of the scenario: the AmneziaWG router of «Дом» dead, the rest alive."""
    return not (e.get("role") == "ru" and e.get("kind") == "awg")


class Rounds(RuWorld):
    def setUp(self):
        need("make")
        super().setUp()
        self._mem = collect.MEM
        collect.MEM = collect.Mem()

    def tearDown(self):
        collect.MEM = self._mem
        super().tearDown()

    def pr(self, alive):
        pool = exits.pool(self.w.runner, roles=exits.ROLES)
        probed = dict((e["id"], {"alive": alive(e), "detail": "", "handshake": None}) for e in pool)
        return {"pool": pool, "probed": probed, "legs": None, "plan": {}, "node": dict(OK), "counters": {}}

    def rounds(self, st, n, alive=lambda e: True):
        out = []
        for _ in range(n):
            self.now += 15
            with mock.patch.object(collect, "_now", lambda: self.now), \
                    mock.patch.object(collect, "_mono", lambda: self.now):
                res = collect.exits_tick(st, self.w.runner, probed=self.pr(alive))
            out += [said(e) for e in res.get("events") or []]
        return out

    def test_make(self):
        self.now = 100000.0
        st = self.world(fail="direct")
        pool = exits.pool(self.w.runner, roles=exits.ROLES)
        doc = st["gateway"]["exit_policy"]
        again = copy.deepcopy(doc)
        P.place_new(again, pool)                         # a refresh of the pool with the same keys
        put("pool.json", pool)
        put("policy.json", doc)
        put("policy_norm.json", P.normalize(copy.deepcopy(doc)))
        put("policy_refresh.json", again)
        put("presets.json", dict((role, dict((p, P.preset_fields(p, role)) for p in P.PRESETS[role] if p != P.CUSTOM))
                                 for role in P.ROLES))
        put("fresh.json", {"doc": P.default_doc(), "ru": P.default_group(HOME, "ru", None, name="Дом")})
        put("notify.json", model.NOTIFY_PUSH)
        before = self.rounds(st, 4) + self.rounds(st, 8, dead_router)    # the router of «Дом» fell before the update
        collect.MEM = collect.Mem()                      # a restart of the collector of this version
        collect._restore_node()
        after = self.rounds(st, 4, dead_router)
        put("rounds.json", {"before": before, "after": after, "now": self.now})
        put("state.json", state.load())
        shutil.copytree(os.environ["KASKAD_ROOT"], os.path.join(IO, "root"))


class Bot(S1PushCase):
    def setUp(self):
        need("make")
        super().setUp()

    def test_make_bot(self):
        t = self.now
        evs = [ev("exit_dead", t, name="Роутер", **RU), ev("exit_alive", t + 30, name="Роутер", **RU),
               ev("group_last_step", t + 40, name="Дом", step="direct", devices=2, **RU),
               ev("group_restored", t + 50, name="Дом", exit="e-1", exit_name="Роутер", after_s=10, **RU),
               ev("exit_dead", t + 60, name="Вена")]
        self.live(evs)
        self.tick()
        self.tick()
        with open(self.path("/run/kaskad/live.json"), encoding="utf-8") as f:
            put("bot_live.json", json.load(f))
        with open(self.path(MONITOR), encoding="utf-8") as f:
            put("bot_monitor.json", json.load(f))
        put("bot_texts.json", {"texts": self.gw_texts(), "now": self.now})


class Read(RuWorld):
    """The rollback: this version reads what the new one wrote."""

    def setUp(self):
        need("read")
        super().setUp()

    def test_policy(self):
        doc, pool = get("new_policy.json"), get("new_pool.json")
        norm = P.normalize(copy.deepcopy(doc))
        errors, _warn = P.validate(norm, pool)
        self.assertEqual(errors, [])
        again = copy.deepcopy(norm)
        P.place_new(again, pool)
        self.assertEqual(again["groups"], norm["groups"])   # nothing moved by the old placement

    def test_events(self):
        """The feed of the old panel: no error; the texts of every kind — for the new side to judge (a kind the old
        version does not know is said by its raw key: the known limit of any rollback, written in the report)."""
        evs = get("new_live.json")["events"]
        shown = live.feed_events(copy.deepcopy(evs))
        texts = [[e.get("kind"), live.event_text(e, "ru"), live.event_text(e, "en")] for e in shown]
        os.makedirs(os.path.dirname(live.feed_path()), exist_ok=True)
        shutil.copyfile(os.path.join(IO, "new_feed.json"), live.feed_path())
        lv = live.Live()
        lv._load_feed()                                     # the feed the new collector left in /run
        put("read_events.json", {"shown": [e.get("kind") for e in shown], "texts": texts})


class ReadBot(S1PushCase):
    def setUp(self):
        need("read")
        super().setUp()

    def test_bot(self):
        """The old bot on the monitor.json and live.json the new one left: no error, nothing sent again (the new bot
        had seen these events)."""
        before = len(self.gw_texts())
        self.write(MONITOR, json.dumps(get("new_monitor.json")))
        self.make_app(monitor=True)
        self.mon = self.app.monitor
        doc = get("new_live.json")
        self.now = max(self.now, doc["ts"])
        self.write("/run/kaskad/live.json", json.dumps(doc))
        self.tick()
        self.tick()
        put("read_bot.json", {"texts": self.gw_texts()[before:]})
