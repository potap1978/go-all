"""T15 Q3 (SPEC-groups §6.1, §6.5; §13.1 «collect.py»): the probes of the Russian exits.

* the targets are data (``probe_targets.json``: the day they were checked; the Russian side has NO HTTP probe — finding
  Б3: no request to Yandex every 30 s from a home IP), so no plan of any frequency asks ``ya.ru`` over HTTP; the
  response of a Russian AmneziaWG exit is the time of its TCP connection;
* «Лениво»: «подозрение» (the sending grows, nothing comes back, 2 ticks) — a probe at once, not more often than once in
  15 s; the passive sign «rx grew» is not taken for «жив»; idle — 300 s (``LAZY_WAKE_S``), traffic — 15 s;
* Б5: the collector decides «выход мёртв» — ``--egress-reresolve N`` for an AmneziaWG exit with a NAME in its key, dead
  ≥ 60 s, not more often than once in 5 min an exit (``/run/kaskad/reresolve.json``), after the state lock, 15 s.
"""

import json
import os
import socket
import unittest
from unittest import mock

from kaskad import paths, state
from kaskad.gw import collect, exits
from kaskad.gw import policy as P
from kaskad.sysrun import FakeRunner
from tests.gw.test_collect_s1 import S1World
from tests.gw.test_ru_target import HOME, RuWorld


class MemCase(RuWorld):
    def setUp(self):
        super().setUp()
        self._mem = collect.MEM
        collect.MEM = collect.Mem()

    def tearDown(self):
        collect.MEM = self._mem
        super().tearDown()

    def plan(self, st, mono, mode="lazy"):
        pool = self.pool()
        pol = P.load(st, pool)
        P.group(pol, HOME)["checks"]["freq"] = {"mode": mode, "every_s": None}
        return collect.probe_plan(st, pol, pool, mono)

    def awg(self):
        return [r["id"] for r in self.ru_rows if r.get("kind") == "awg"][0]


class TargetsTest(unittest.TestCase):
    def test_the_file(self):
        with open(os.path.join(os.path.dirname(exits.__file__), "probe_targets.json"), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertRegex(doc["checked"], r"^\d{4}-\d\d-\d\d$")                 # §6.1: the day of the check
        self.assertIsNone(doc["ru"]["http"])
        self.assertEqual(doc["ru"]["tcp"], [["77.88.8.8", 443], ["77.88.8.1", 443]])
        self.assertEqual(doc["ru"]["dns"], [["77.88.8.8", 53], ["77.88.8.1", 53]])
        self.assertIsNone(exits.probe_targets("ru")["http"])
        self.assertEqual(exits.probe_targets("abroad")["http"], exits.PROBE_URL)


class NoHttpToYandexTest(MemCase):
    def test_no_plan_asks_yandex_over_http(self):
        """§13.1: «ни одного HTTP к ya.ru вне проверки сервиса» — every frequency, with traffic and without; the
        Russian AmneziaWG exit is probed «жив» (TCP to 77.88.8.8:443) and its response is that connection."""
        st = self.world()
        awg = self.awg()
        for mode in P.FREQ_MODES:
            for busy in (False, True):
                with self.subTest(mode=mode, busy=busy):
                    collect.MEM.health = dict((i, {"state": "ok"}) for i in self.ru_ids)
                    collect.MEM.awg_at.clear()
                    collect.MEM.http_at.clear()
                    collect.MEM.busy_at.clear()
                    if busy:
                        collect.MEM.busy_at[awg] = 1000.0
                    with mock.patch("kaskad.gw.xray.probe_port", return_value=1):
                        p = self.plan(st, 1000.0, mode if mode != "custom" else "often")
                    self.assertFalse([u for _e, _m, u in p["http"] if "ya.ru" in u or "yandex" in u], p["http"])
                    self.assertNotIn(awg, [e for e, _m, _u in p["http"]])
                    self.assertIn(awg, p["targets"])
                    self.assertEqual(list(p["targets"][awg]), list(exits.probe_targets("ru")["tcp"]))
                    self.assertIn(awg, p["conn"])

    def test_the_foreign_exit_keeps_its_http_probe(self):
        """A foreign AmneziaWG exit keeps the response probe of 3.3.x (Cloudflare generate_204)."""
        st = self.world()
        collect.MEM.health = {}
        pool = self.pool()
        pol = P.load(st, pool)
        with mock.patch("kaskad.gw.xray.probe_port", return_value=1):
            p = collect.probe_plan(st, pol, pool, 1000.0)
        ab = [r["id"] for r in pool if r["role"] == "abroad" and r["kind"] == "awg"]
        self.assertTrue(ab)
        self.assertEqual(sorted(e for e, _m, _u in p["http"]), sorted(ab))
        self.assertEqual(set(u for _e, _m, u in p["http"]), {exits.PROBE_URL})


class ConnRttTest(unittest.TestCase):
    def test_connect_through_tells_the_time(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        self.addCleanup(srv.close)
        took = {}
        got = exits.connect_through([0], [("127.0.0.1", srv.getsockname()[1])], timeout=2, took=took)
        self.assertEqual(got, {0: True})
        self.assertIsInstance(took[0], int)
        self.assertGreaterEqual(took[0], 0)
        self.assertEqual(exits.connect_through([0], [("127.0.0.1", srv.getsockname()[1])], timeout=2), {0: True})

    def test_the_response_of_the_russian_awg_exit(self):
        probed = {"e-1": {"alive": True, "conn_ms": 23}, "e-2": {"alive": True, "conn_ms": 40},
                  "e-3": {"alive": False, "conn_ms": 5}}
        collect._conn_rtt(probed, {"e-1", "e-3"})
        self.assertEqual(probed["e-1"].get("rtt"), 23)
        self.assertIsNone(probed["e-2"].get("rtt"))           # a foreign one: its HTTP probe says the response
        self.assertIsNone(probed["e-3"].get("rtt"))           # failed: no response
        self.assertFalse([p for p in probed.values() if "conn_ms" in p])


class SuspicionTest(MemCase):
    def test_counters(self):
        """§6.1: tx grows, rx does not — one tick more; rx grows (an answer came) — none; two ticks — suspicion."""
        collect._note_suspect({"e-1": (100, 100)})
        self.assertNotIn("e-1", collect.MEM.suspect)
        collect._note_suspect({"e-1": (100, 400)})
        collect._note_suspect({"e-1": (100, 700)})
        self.assertEqual(collect.MEM.suspect["e-1"], 2)
        self.assertTrue(collect._suspect_due("e-1", 1000.0))
        collect._note_suspect({"e-1": (900, 800)})                     # rx grew: the leg answers
        self.assertNotIn("e-1", collect.MEM.suspect)
        collect._note_suspect({"e-1": (900, 800)})                     # nothing at all: idle, no suspicion
        self.assertNotIn("e-1", collect.MEM.suspect)
        collect._note_suspect({"e-1": (900, 900)})
        collect._note_suspect({"e-1": (10, 1000)})                     # the counter went back (a new leg): anew
        self.assertNotIn("e-1", collect.MEM.suspect)
        collect._note_suspect({})
        self.assertEqual((collect.MEM.suspect, collect.MEM.rxtx), ({}, {}))

    def test_a_suspected_idle_exit_is_probed_at_once(self):
        """A router that died while idle, a person begins to use it (SYNs out, nothing back): checked now, not after
        LAZY_WAKE_S — still not more often than once in 15 s."""
        st = self.world()
        awg = self.awg()
        collect.MEM.health = dict((i, {"state": "ok"}) for i in self.ru_ids)
        collect.MEM.awg_at[awg] = 1000.0
        with mock.patch("kaskad.gw.xray.probe_port", return_value=1):
            p = self.plan(st, 1100.0)
            self.assertIn(awg, p["idle"])
            self.assertNotIn(awg, p["only"])                         # idle, checked 100 s ago: not yet
            collect.MEM.suspect[awg] = collect.SUSPECT_TICKS
            self.assertIn(awg, self.plan(st, 1100.0)["only"])         # suspected: now
            collect.MEM.awg_at[awg] = 1095.0
            self.assertNotIn(awg, self.plan(st, 1100.0)["only"])      # probed 5 s ago: the 15 s hold
            collect.MEM.suspect[awg] = 1
            collect.MEM.awg_at[awg] = 1000.0
            self.assertNotIn(awg, self.plan(st, 1100.0)["only"])      # one tick is no suspicion

    def test_idle_and_traffic_periods(self):
        """§13.1: «простой → 300 с, трафик → 15 с»."""
        st = self.world()
        awg = self.awg()
        collect.MEM.health = dict((i, {"state": "ok"}) for i in self.ru_ids)
        collect.MEM.awg_at[awg] = 1000.0
        with mock.patch("kaskad.gw.xray.probe_port", return_value=1):
            self.assertNotIn(awg, self.plan(st, 1000.0 + collect.LAZY_WAKE_S - 1)["only"])
            self.assertIn(awg, self.plan(st, 1000.0 + collect.LAZY_WAKE_S)["only"])
            collect.MEM.busy_at[awg] = 1010.0
            self.assertNotIn(awg, self.plan(st, 1010.0)["only"])
            p = self.plan(st, 1015.0)
            self.assertIn(awg, p["only"])
            self.assertEqual(p["period"][awg], P.LAZY_P)
        self.assertEqual(collect.LAZY_WAKE_S, 300)

    def test_the_round_notes_the_counters(self):
        """``probe_round`` keeps the counters of this round for the suspicion of the next one."""
        st = self.world()
        awg = self.awg()
        seq = iter([{awg: (100, 100)}, {awg: (100, 300)}, {awg: (100, 600)}])
        with mock.patch.object(collect, "_counters", lambda *a: next(seq)), \
                mock.patch.object(exits, "probe", lambda *a, **k: {}), \
                mock.patch.object(exits, "ensure_legs", lambda *a, **k: None), \
                mock.patch.object(collect, "node_check", lambda *a, **k: {}), \
                mock.patch("kaskad.gw.xray.probe_port", return_value=1):
            for _ in range(3):
                collect.probe_round(self.w.runner, st=st)
        self.assertEqual(collect.MEM.suspect.get(awg), 2)


ROWS = [{"id": "e-00000000d1", "kind": "awg", "index": 201, "host": "router.example.com:51820"},
        {"id": "e-00000000d2", "kind": "awg", "index": 202, "host": "203.0.113.31:51820"},
        {"id": "e-00000000d3", "kind": "xray", "index": 203, "host": "home.example.com:443"},
        {"id": "e-00000000d4", "kind": "awg", "index": 3, "host": "[2001:db8::1]:51820"},
        {"id": "e-00000000d5", "kind": "awg", "index": 4, "host": "?"}]


def dead(since):
    return {"state": "off", "reasons": ["dead"], "since": since}


class ReresolveTest(MemCase):
    def test_who_is_due(self):
        """A NAME in the key (not an IP, not an xray exit — xray resolves on every connection), dead ≥ 60 s, any
        role; turned off by the owner — no."""
        health = dict((r["id"], dead(1000.0)) for r in ROWS)
        self.assertEqual(collect.reresolve_due(ROWS, health, 1059.0), [])
        self.assertEqual(collect.reresolve_due(ROWS, health, 1060.0), [("e-00000000d1", 201)])
        health["e-00000000d1"] = {"state": "off", "reasons": ["dead", "disabled"], "since": 1000.0}
        self.assertEqual(collect.reresolve_due(ROWS, health, 2000.0), [])
        health["e-00000000d1"] = {"state": "ok", "reasons": [], "since": 1000.0}
        self.assertEqual(collect.reresolve_due(ROWS, health, 2000.0), [])

    def test_once_in_five_minutes_through_run(self):
        runner = FakeRunner().on([exits._script(None), "--egress-reresolve"], (0, "", ""))
        with mock.patch.object(collect, "_now", lambda: 2000.0):
            collect.MEM.health = {"e-00000000d1": dead(1000.0)}
            self.assertEqual(collect._reresolve(runner, ROWS), [("e-00000000d1", 0)])
        argv = runner.commands([exits._script(None)])
        self.assertEqual(argv, [[exits._script(None), "--egress-reresolve", "201"]])
        with open(os.path.join(paths.run_dir(), "reresolve.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"tries": {"e-00000000d1": 2000.0}})
        with mock.patch.object(collect, "_now", lambda: 2299.0):
            self.assertEqual(collect._reresolve(runner, ROWS), [])
        collect.MEM = collect.Mem()                                        # a restarted collector: /run keeps it
        collect.MEM.health = {"e-00000000d1": dead(1000.0)}
        with mock.patch.object(collect, "_now", lambda: 2299.0):
            self.assertEqual(collect._reresolve(runner, ROWS), [])
        with mock.patch.object(collect, "_now", lambda: 2300.0):
            self.assertEqual(collect._reresolve(runner, ROWS), [("e-00000000d1", 0)])
        self.assertEqual(len(runner.commands([exits._script(None)])), 2)

    def test_one_exit_a_round(self):
        """Two dead names: one call a round (a round never waits for two timeouts), the other — the next round."""
        rows = ROWS + [{"id": "e-00000000d6", "kind": "awg", "index": 206, "host": "dacha.example.com:51820"}]
        runner = FakeRunner().on([exits._script(None), "--egress-reresolve"], (0, "", ""))
        collect.MEM.health = {"e-00000000d1": dead(1000.0), "e-00000000d6": dead(1000.0)}
        with mock.patch.object(collect, "_now", lambda: 2000.0):
            self.assertEqual(collect._reresolve(runner, rows), [("e-00000000d1", 0)])
            self.assertEqual(collect._reresolve(runner, rows), [("e-00000000d6", 0)])
            self.assertEqual(collect._reresolve(runner, rows), [])

    def test_the_timeout_and_a_pool_being_changed(self):
        seen = []

        class R(FakeRunner):
            def run(self, argv, input=None, check=False, timeout=60, env=None):
                seen.append(timeout)
                return super().run(argv, input, check, timeout, env)
        runner = R().on([exits._script(None), "--egress-reresolve"], (6, "", ""))
        collect.MEM.health = {"e-00000000d1": dead(1000.0)}
        with mock.patch.object(collect, "_now", lambda: 2000.0):
            self.assertEqual(collect._reresolve(runner, ROWS), [("e-00000000d1", 6)])
            self.assertEqual(collect._reresolve(runner, ROWS), [("e-00000000d1", 6)])   # 6 — not a try: again
        self.assertEqual(seen, [collect.RERESOLVE_TIMEOUT] * 2)
        self.assertEqual(collect.RERESOLVE_TIMEOUT, 15)

    def test_the_entry_of_install_sh_keeps_the_code(self):
        """``install.sh --egress-reresolve N`` gives the code of ``egress_reresolve`` as it is: 6 (the pool is being
        changed) must reach the collector — «|| exit 1» made it 1, a try lost for 5 min."""
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sh = os.path.join(root, "install.sh")
        with open(sh, encoding="utf-8") as f:
            line = [x for x in f if x.lstrip().startswith("--egress-reresolve)")]
        self.assertEqual(len(line), 1)
        case = line[0].split("#")[0].strip()
        for rc in (0, 1, 6):
            got = subprocess.run(["bash", "-c", 'egress_reresolve() { return %d; }; set -- --egress-reresolve 201; '
                                  'case "$1" in %s esac; exit 0' % (rc, case)], capture_output=True, timeout=10)
            self.assertEqual(got.returncode, rc, case)

    def test_nothing_while_the_node_is_offline(self):
        runner = FakeRunner()
        collect.MEM.health = {"e-00000000d1": dead(1000.0)}
        collect.MEM.node["offline"] = True
        with mock.patch.object(collect, "_now", lambda: 2000.0):
            self.assertEqual(collect._reresolve(runner, ROWS), [])
        self.assertEqual(runner.calls, [])


class ReresolveAfterTheLockTest(S1World):
    def test_after_the_lock(self):
        """Б5: «после снятия блокировки состояния» — the tick calls it with the state lock let go."""
        held = []
        with mock.patch.object(collect, "_reresolve", side_effect=lambda *a: held.append(state.held()) or []):
            self.round()
            self.round()
        self.assertEqual(held, [False, False])
