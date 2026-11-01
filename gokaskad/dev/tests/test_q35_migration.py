"""Q3/Q5, the migration (the owner's rule 24.10: «важно — мигрирование, чтобы не было косяков»).

The data are written by the RELEASED code itself — the tree of a commit (``git archive``) runs ``tests/q35_old_side.py``
in a subprocess — and read by this one, and back:

1. 3.3.7.4 / 3.3.7.6 / 3.3.7.8 → this version: the policy of the scenario «Дом» (a Russian group, ⚠РФ and the consent,
   «Россия напрямую», ``ru_via``) is read as it was, a refresh of the same pool moves nothing, the group stays «Дом»
   (never «Свой»), the ids of the exits stay; nothing new turns itself on;
2. through a version: 3.3.7.6 → this one (and 3.3.7.4 → this one) — the same checks;
3. the rollback: 3.3.7.8 (abe39661) reads the data of this one — a policy with a level more (``new_tier``), the events
   ``node_ru_offline``/``node_ru_online``, the new args of ``exit_failover``, the feed in /run, monitor.json with
   ``ru_told`` — without an error, no raw key, no «{…}»;
4. an updated node without events says nothing new: the collector of this version on the root left by the old one
   says after a restart what the old one said after its restart; the bot of this version on the monitor.json and
   live.json of the old one sends nothing;
5. a clean install: the presets, the switches of the pushes and the first policy — as 3.3.7.8 had them, plus only the
   switch ``node_ru_offline`` (SPEC-groups §10, on).

No git or no commit (a tarball) — skipped."""

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from kaskad import state, util
from kaskad.gw import collect, exits, live, model
from kaskad.gw import policy as P
from tests.bot.test_monitor import S1PushCase, ev
from tests.fakes_gw import awg_line, write_ru_pool
from tests.gw.test_ru_target import DACHA_VLESS, HOME, RuWorld
from tests.q35_old_side import dead_router

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = (("3.3.7.4", "71349d4f"), ("3.3.7.6", "dd1d428b"), ("3.3.7.8", "abe39661"))
PREV = "abe39661"
RUNS = {"make": 2, "read": 3}     # the cases of tests/q35_old_side.py per mode
MONITOR = "/etc/kaskad/bot_state/monitor.json"
RU = dict(group=HOME, group_name="Дом", role="ru")
_TMP = {}


def tearDownModule():
    if _TMP.get("dir"):
        shutil.rmtree(_TMP["dir"], ignore_errors=True)


def _tmp():
    if not _TMP.get("dir"):
        _TMP["dir"] = tempfile.mkdtemp(prefix="k3-q35-mig-")
    return _TMP["dir"]


def tree(rev):
    """lib/ and tests/ of a commit, with the old side copied in. SkipTest without git or the commit."""
    d = os.path.join(_tmp(), rev)
    if os.path.isdir(d):
        return d
    try:
        ok = subprocess.run(["git", "-C", REPO, "cat-file", "-e", rev + "^{commit}"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("no git")
    if ok.returncode != 0:
        raise unittest.SkipTest("no commit %s" % rev)
    os.makedirs(d + ".part")
    arc = subprocess.run(["git", "-C", REPO, "archive", rev, "lib", "tests", "install.sh"], capture_output=True, timeout=120)
    if arc.returncode != 0:
        raise unittest.SkipTest("git archive %s: %s" % (rev, arc.stderr[-300:]))
    subprocess.run(["tar", "-x", "-C", d + ".part"], input=arc.stdout, check=True, timeout=120)
    shutil.copyfile(os.path.join(REPO, "tests", "q35_old_side.py"), os.path.join(d + ".part", "tests", "q35_old_side.py"))
    os.rename(d + ".part", d)
    return d


def old(rev, mode, io):
    """Runs the old side of ``rev`` in its own tree; a failure — the tail of its output."""
    d = tree(rev)
    env = dict(os.environ)
    env.pop("KASKAD_ROOT", None)
    env.pop("K3_TR_STRICT_LOG", None)          # the strictness of the old texts is not under test here
    env.update(PYTHONPATH=os.pathsep.join([os.path.join(d, "lib"), d]), Q35_MODE=mode, Q35_IO=io)
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.q35_old_side"], cwd=d, env=env, capture_output=True,
                       text=True, timeout=900)
    ran = re.search(r"^Ran (\d+) test", p.stderr, re.M)
    skipped = re.search(r"skipped=(\d+)", p.stderr)
    done = (int(ran.group(1)) if ran else 0) - (int(skipped.group(1)) if skipped else 0)
    if p.returncode != 0 or done != RUNS[mode]:              # every case of the mode really ran
        raise AssertionError("old side %s %s:\n%s" % (rev, mode, p.stderr[-6000:]))


def made(rev):
    """What ``rev`` itself wrote (``make``), once per run."""
    key = "made-" + rev
    if key not in _TMP:
        io = os.path.join(_tmp(), key)
        os.makedirs(io)
        old(rev, "make", io)
        _TMP[key] = io
    return _TMP[key]


def get(io, name):
    with open(os.path.join(io, name), encoding="utf-8") as f:
        return json.load(f)


class ForwardTest(RuWorld):
    """1, 2: the data of 3.3.7.4 / 3.3.7.6 / 3.3.7.8 read by this version."""

    def setUp(self):
        super().setUp()
        self._mem = collect.MEM
        collect.MEM = collect.Mem()

    def tearDown(self):
        collect.MEM = self._mem
        super().tearDown()

    def test_the_policy_is_read_as_it_was(self):
        for ver, rev in OLD:
            with self.subTest(ver):
                io = made(rev)
                pool = get(io, "pool.json")
                doc = get(io, "policy.json")
                self.assertEqual(P.normalize(copy.deepcopy(doc)), get(io, "policy_norm.json"))
                errors, _warn = P.validate(P.normalize(copy.deepcopy(doc)), pool)
                self.assertEqual(errors, [])
                again = get(io, "policy_refresh.json")
                mine = copy.deepcopy(again)
                P.place_new(mine, pool)                      # the first refresh of the pool after the update
                self.assertEqual(mine["groups"], again["groups"])
                home = [g for g in P.normalize(copy.deepcopy(doc))["groups"] if g.get("id") == HOME][0]
                self.assertEqual(P.preset_of(home), "ru_home")   # never «Свой» after the update
                self.assertEqual(len(home["tiers"]), 1)          # the levels of the group are not rebuilt
                self.assertEqual(sorted(home["tiers"][0]["exits"]), sorted(e["id"] for e in pool if e["role"] == "ru"))

    def test_the_ids_and_the_state_stay(self):
        st = self.world(fail="direct")
        mine = sorted(e["id"] for e in exits.pool(self.w.runner, roles=exits.ROLES))
        for ver, rev in OLD:
            with self.subTest(ver):
                io = made(rev)
                self.assertEqual(sorted(e["id"] for e in get(io, "pool.json")), mine)
                theirs = get(io, "state.json")["gateway"]
                for key in ("rf_consent", "users", "rules"):
                    self.assertEqual(theirs.get(key), st["gateway"].get(key), key)

    def test_after_the_update_the_collector_says_what_the_old_one_said(self):
        """4: the router of «Дом» fell under the old version; its root (state, snapshot, the told, /run — an update
        without a reboot), this collector restarted on it: the same events as the old one after its own restart —
        no ``exit_dead`` again, no ``node_ru_*``, no ``exit_failover``, no «all fell / all rose»."""
        for ver, rev in OLD:
            with self.subTest(ver):
                io = made(rev)
                self.world(fail="direct")
                shutil.copytree(os.path.join(io, "root"), os.environ["KASKAD_ROOT"], dirs_exist_ok=True)
                st = state.load()
                rounds = get(io, "rounds.json")
                collect.MEM = collect.Mem()
                collect._restore_node()
                collect._restore_ru_node()
                self.now = rounds["now"] - 4 * 15
                said = []
                for _ in range(4):
                    self.now += 15
                    pool = exits.pool(self.w.runner, roles=exits.ROLES)
                    pr = {"pool": pool, "probed": dict((e["id"], {"alive": dead_router(e), "detail": "",
                                                                  "handshake": None}) for e in pool),
                          "legs": None, "plan": {}, "node": {"checked": False, "offline": False, "ru_offline": False},
                          "counters": {}}
                    with mock.patch.object(collect, "_now", lambda: self.now), \
                            mock.patch.object(collect, "_mono", lambda: self.now):
                        res = collect.exits_tick(st, self.w.runner, probed=pr)
                    said += [[e.get("kind"), e.get("exit"), e.get("group")] for e in res.get("events") or []]
                self.assertEqual(said, rounds["after"])
                self.assertIn("exit_dead", [k for k, _e, _g in rounds["before"]])   # the old one did see the fall
                kinds = set(k for k, _e, _g in said)
                self.assertFalse(kinds & {"node_ru_offline", "node_ru_online", "exit_failover", "group_last_step"})

    # REV-3379 З-2: the rounds above give ``exits_tick`` a ready «node»; the paths an updated node turns on by itself —
    # the direct probe of ``node_check`` (``by_ru``) and the new look-up of a name in ``_after`` (``_reresolve``, no
    # switch) — are walked here as ``tick`` walks them, on the root the old version left.

    def updated(self, rev):
        """The root of ``rev`` (the router of «Дом» fell under it; /run kept — an update without a reboot), this
        collector started on it."""
        io = made(rev)
        self.world(fail="direct")
        shutil.copytree(os.path.join(io, "root"), os.environ["KASKAD_ROOT"], dirs_exist_ok=True)
        collect.MEM = collect.Mem()
        self.now = get(io, "rounds.json")["now"]
        self.asked, self.errors = [], []
        return state.load()

    def round(self, st, alive, name=None):
        """One round as ``tick`` makes it: the probes (``alive(e)``; xray — a fresh try of the observatory), the
        restored outages, ``node_check`` (the direct probes answer, each one in ``self.asked``), ``exits_tick``,
        ``_after``. ``name`` — the key of the AmneziaWG router of «Дом» names its Endpoint so (dynamic DNS); in the
        scenario it has an IP — only the row the round sees gets the name, the id, the policy and the snapshot are
        those of the old root. -> (the kinds of the events, the argv of ``--egress-reresolve``, the node)."""
        self.now += 15
        pool = exits.pool(self.w.runner, roles=exits.ROLES)
        for e in pool:
            if name and e.get("role") == "ru" and e.get("kind") == "awg":
                e["host"] = name
        probed = dict((e["id"], {"alive": alive(e), "detail": "", "handshake": None, "try_age": 1.0}) for e in pool)
        plan = {"only": set(probed), "idle": set(), "off": set(), "socks": {}}
        n = len(self.w.runner.calls)
        with mock.patch.object(collect, "_now", lambda: self.now), \
                mock.patch.object(collect, "_mono", lambda: self.now), \
                mock.patch.object(collect, "_note_error", lambda key, e: self.errors.append((key, repr(e)))), \
                mock.patch.object(exits, "direct_probe",
                                  side_effect=lambda t, *a, **k: self.asked.append(list(t)) or True):
            collect._restore_node()
            collect._restore_ru_node()
            node = collect.node_check(pool, probed, collect._policy(st, pool), plan)
            pr = {"pool": pool, "probed": probed, "legs": None, "plan": plan, "node": node, "counters": {}}
            res = collect.exits_tick(st, self.w.runner, probed=pr)
            collect._after(self.w.runner, st, res, pr, util.log_action)
        again = [a for a in self.w.runner.commands()[n:] if "--egress-reresolve" in a]
        self.assertEqual(self.errors, [])                              # no side job of the round failed
        return [e.get("kind") for e in res.get("events") or []], again, node

    def test_the_network_of_the_node_answers_nothing_new(self):
        """The exits answer — no direct probe; the whole Russian group silent, both direct targets answer — the node is
        checked, its Russian network is there: no ``node_ru_*``. The key of the router has an IP: no look-up."""
        for ver, rev in OLD:
            with self.subTest(ver):
                st = self.updated(rev)
                kinds, again = [], []
                for _ in range(8):                                       # 2 min: the router is back
                    k, a, node = self.round(st, lambda e: True)
                    kinds, again = kinds + k, again + a
                    self.assertFalse(node["checked"], node)
                self.assertEqual(self.asked, [])
                for _ in range(8):                                       # 2 min: «Дом» silent, the node's network is up
                    k, a, node = self.round(st, lambda e: e.get("role") != "ru")
                    kinds, again = kinds + k, again + a
                    self.assertEqual((node["checked"], node["offline"], node["ru_offline"]), (True, False, False))
                self.assertEqual(len(self.asked), 2 * 8)                 # the Russian and the foreign target a round
                self.assertEqual(again, [])
                self.assertFalse(set(kinds) & {"node_ru_offline", "node_ru_online"}, kinds)

    def test_a_dead_router_by_a_name_once_in_five_minutes(self):
        """The router of «Дом» by a name (DDNS): dead as the old version left it (≥ 60 s) — its name again at the
        first round, then once in 5 min; back, then dead again — not before it is dead 60 s. The command — the line
        of the pool, never the name; the dacha answers: no direct probe, no ``node_ru_*``."""
        name = "router.example.com:51820"
        for ver, rev in OLD:
            with self.subTest(ver):
                st = self.updated(rev)
                rid = [e["id"] for e in exits.pool(self.w.runner, roles=("ru",)) if e.get("kind") == "awg"][0]
                kinds, at = [], []
                for i in range(24):                                      # 6 min still dead
                    k, a, _n = self.round(st, dead_router, name)
                    h = collect.MEM.health
                    if i == 0:
                        self.assertEqual((h[rid]["state"], "dead" in h[rid]["reasons"]), ("off", True))
                        self.assertGreaterEqual(self.now - h[rid]["since"], collect.RERESOLVE_DEAD_S)
                        first = self.now
                    kinds += k
                    at += [self.now] * len(a)
                    self.assertEqual(a, [[exits._script(self.w.runner), "--egress-reresolve", "201"]] if a else [])
                self.assertEqual(at, [first, first + collect.RERESOLVE_EVERY_S])
                for _ in range(20):                                      # 5 min back
                    k, a, _n = self.round(st, lambda e: True, name)
                    kinds += k
                    self.assertEqual(a, [])
                h = collect.MEM.health[rid]                              # answers (the loss of the fall —
                self.assertNotEqual(h["state"], "off", h)                # «degraded» by its rule a while)
                self.assertNotIn("dead", h["reasons"], h)
                at = []
                for _ in range(8):                                       # dead again: 2 min
                    k, a, _n = self.round(st, dead_router, name)
                    kinds += k
                    at += [self.now] * len(a)
                since = collect.MEM.health[rid]["since"]
                self.assertLessEqual(since + collect.RERESOLVE_DEAD_S, self.now)
                self.assertEqual(at, [since + collect.RERESOLVE_DEAD_S])   # not at the fall: 60 s after it
                self.assertEqual(self.asked, [])
                self.assertFalse(set(kinds) & {"node_ru_offline", "node_ru_online"}, kinds)


class ForwardBotTest(S1PushCase):
    """4: the bot of this version on the monitor.json and live.json the old bot and collector left — nothing sent,
    neither at once nor after 15 min (the hold of ``node_ru_offline``)."""

    def check(self, rev):
        io = made(rev)
        self.write(MONITOR, json.dumps(get(io, "bot_monitor.json")))
        self.make_app(monitor=True)
        self.mon = self.app.monitor
        doc = get(io, "bot_live.json")
        self.now = get(io, "bot_texts.json")["now"]
        self.write("/run/kaskad/live.json", json.dumps(doc))
        sent = len(self.gw_texts())
        self.tick()
        self.tick()
        self.now += 16 * 60
        self.tick()
        self.assertEqual(self.gw_texts()[sent:], [])
        self.assertTrue(get(io, "bot_texts.json")["texts"])      # the old bot did push them: they were real


for _ver, _rev in OLD:
    setattr(ForwardBotTest, "test_nothing_new_after_the_update_from_" + _ver.replace(".", "_"),
            lambda self, rev=_rev: self.check(rev))


class FreshInstallTest(unittest.TestCase):
    """5: a clean install — as 3.3.7.8, plus only the switch ``node_ru_offline``."""

    def test_presets_switches_and_the_first_policy(self):
        io = made(PREV)
        theirs = get(io, "presets.json")
        mine = dict((role, dict((p, P.preset_fields(p, role)) for p in P.PRESETS[role] if p != P.CUSTOM))
                    for role in P.ROLES)
        self.assertEqual(json.loads(json.dumps(mine)), theirs)
        fresh = get(io, "fresh.json")
        self.assertEqual(json.loads(json.dumps(P.default_doc())), fresh["doc"])
        self.assertEqual(json.loads(json.dumps(P.default_group(HOME, "ru", None, name="Дом"))), fresh["ru"])
        sw = get(io, "notify.json")
        self.assertEqual(dict((k, v) for k, v in model.NOTIFY_PUSH.items() if k in sw), sw)
        self.assertEqual(set(model.NOTIFY_PUSH) - set(sw), {"node_ru_offline"})
        self.assertIs(model.NOTIFY_PUSH["node_ru_offline"], True)
        for ver, rev in OLD[:-1]:                                # the older ones: the common switches the same
            with self.subTest(ver):
                sw = get(made(rev), "notify.json")
                self.assertEqual(dict((k, v) for k, v in model.NOTIFY_PUSH.items() if k in sw), sw)


class RollbackTest(S1PushCase):
    """3: 3.3.7.8 reads what this version wrote."""

    def new_data(self, io):
        t = self.now
        # the layer of events of this version: node_ru_*, a failover of «Дом» named by its keys, a Russian exit
        lv = live.Live()
        names, gl = {"e-0000000000a1": "Роутер", "e-0000000000a2": "Дача"}, {HOME: "Дом"}
        lv.publish([{"kind": "node_ru_offline", "since": t}], names, gl, {}, t)
        lv.publish([dict(kind="exit_failover", from_tier="t1", to_tier="t2", back=False, moved=2, from_label="",
                         to_label="", from_exits=["e-0000000000a1"], to_exits=["e-0000000000a2"], **RU)],
                   names, gl, {}, t + 5, notify={"push": {"coalesce": False}})
        lv.publish([dict(kind="exit_dead", exit="e-0000000000a1", **RU)], names, gl, {}, t + 6)
        lv.publish([{"kind": "node_ru_online", "since": t, "until": t + 1200, "minutes": 20}], names, gl, {}, t + 1200)
        kinds = [e["kind"] for e in lv.events]
        for k in ("node_ru_offline", "node_ru_online", "exit_failover", "exit_dead"):
            self.assertIn(k, kinds)
        lv._save_feed()
        shutil.copyfile(live.feed_path(), os.path.join(io, "new_feed.json"))
        # the bot of this version sees them as they come: the hold of the long outage — ``ru_told`` in monitor.json
        for at in (t + 6, t + 15 * 60 + 5, t + 1200):
            self.now = at
            self.live([e for e in lv.events if e["ts"] <= at])
            self.tick()
        with open(self.path(MONITOR), encoding="utf-8") as f:
            mon = json.load(f)
        self.assertIn("ru_told", mon)
        doc = {"v": 1, "ts": t + 1200, "events": lv.events, "i18n": {"lang": "ru", "problems": [], "why": {}}}
        for name, obj in (("new_live.json", doc), ("new_monitor.json", mon)):
            with open(os.path.join(io, name), "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)

    def test_the_old_version_reads_the_new_data(self):
        io = tempfile.mkdtemp(dir=_tmp())
        self.new_data(io)
        w = RuPolicy("test_new_policy")
        w.io = io
        res = unittest.TestResult()
        w.run(res)
        self.assertEqual(res.errors + res.failures, [])
        self.assertEqual(res.skipped, [])
        old(PREV, "read", io)                                    # no error in the old policy, feed and bot
        got = get(io, "read_events.json")
        for kind, ru, en in got["texts"]:
            if kind in ("exit_failover", "exit_dead"):           # the kinds 3.3.7.8 knows: the new args break nothing
                for text in (ru, en):
                    self.assertNotIn("gw_live.", text)
                    self.assertNotIn("{", text)                  # («t1»/«t2» there — the words of 3.3.7.8 itself)
        self.assertIn("exit_failover", got["shown"])
        self.assertEqual(get(io, "read_bot.json")["texts"], [])  # nothing sent again after the rollback


class RuPolicy(RuWorld):
    """The policy of this version for the rollback: «Дом» with a key more — its own level (``new_tier``)."""

    def test_new_policy(self):
        if not getattr(self, "io", None):
            self.skipTest("a part of RollbackTest")
        st = self.world(fail="direct")
        write_ru_pool([awg_line(31, name="Роутер", endpoint="203.0.113.31:51820", address="10.8.31.2/32"),
                       DACHA_VLESS, awg_line(33, name="Дача 2", endpoint="203.0.113.33:51820", address="10.8.33.2/32")])
        pool = exits.pool(self.w.runner, roles=exits.ROLES)
        doc = P.normalize(copy.deepcopy(st["gateway"]["exit_policy"]))
        P.place_new(doc, pool)
        home = [g for g in doc["groups"] if g["id"] == HOME][0]
        self.assertEqual(len(home["tiers"]), 2)
        for name, obj in (("new_policy.json", doc), ("new_pool.json", pool)):
            with open(os.path.join(self.io, name), "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
