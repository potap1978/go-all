"""A5 (SPEC-node-score §4 A5, §3.5–3.7, §3.9, §10.1 п. 20/22): the light health of the node in the collector — the round
of hostmon after the exits, ``live.json → host.health``, the alarms in the problems, the events of the node in the
feed, hostmon.json, the probes in the pause, ``health.cost``, ``hostprobe.read_health``. A fake /proc under
KASKAD_ROOT; no packet leaves the machine (the prober gets the fake network of test_hostping)."""

import json
import os
import random
from unittest import mock

from kaskad import i18n, state
from kaskad.gw import collect, hostmon, hostping, hostprobe, live, model
from tests.gw import test_hostprobe as HP
from tests.gw.test_collect_s1 import S1World
from tests.gw.test_hostping import FakeNet

GIB = 1024 ** 3


class Vfs(object):
    def __init__(self, used_pct, size_g=100):
        blocks = size_g * GIB // 4096
        self.f_frsize = self.f_bsize = 4096
        self.f_blocks = blocks
        self.f_bfree = self.f_bavail = int(blocks * (100 - used_pct) / 100.0)
        self.f_files, self.f_ffree, self.f_fsid = 1000, 900, 77


class HostWorld(S1World):
    """A gateway on, a fake /proc of a healthy node, the disk at ``self.disk`` %; probes off (no key) unless set."""

    def setUp(self):
        super().setUp()
        self.disk = 50.0
        self.tree()
        for p in (mock.patch.object(hostprobe, "_statvfs", lambda path: self.vfs(path)),
                  mock.patch.object(hostprobe, "clock", lambda: {"synced": True, "offset_s": 0.001}),
                  mock.patch.object(hostprobe, "_certs", lambda cfg: self.certs),
                  mock.patch.object(hostping, "Net", self.no_net)):
            p.start()
            self.addCleanup(p.stop)
        self.certs = []
        self.sockets = []

    def no_net(self):
        """The real network is never used: a Prober made by the collector gets a fake that counts its sockets."""
        net = FakeNet()
        real = net.socket

        def socket(*a):
            self.sockets.append(a)
            return real(*a)
        net.socket = socket
        return net

    def vfs(self, path):
        if self.disk is None:
            raise OSError(5, "EIO")
        return Vfs(self.disk)

    def tree(self):
        w = self.write
        w("/proc/stat", HP.STAT)
        w("/proc/loadavg", "0.10 0.20 0.30 1/100 1234\n")
        w("/proc/meminfo", HP.MEMINFO)
        for k in ("cpu", "memory", "io"):
            w("/proc/pressure/" + k, HP.PSI)
        w("/proc/vmstat", "nr_free_pages 1\noom_kill 0\n")
        w("/proc/net/dev", HP.NETDEV)
        w("/proc/net/softnet_stat", HP.SOFTNET)
        w("/proc/net/snmp", HP.SNMP)
        w("/proc/net/netstat", HP.NETSTAT)
        w("/proc/net/route", HP.ROUTE)
        w("/proc/sys/net/netfilter/nf_conntrack_count", "100\n")
        w("/proc/sys/net/netfilter/nf_conntrack_max", "1000\n")
        w("/proc/sys/fs/file-nr", "2048\t0\t9223372036854775807\n")
        w("/proc/uptime", "100000.50 7000.00\n")
        w("/proc/1/environ", "PATH=/bin\0HOME=/\0")
        w("/proc/cpuinfo", "processor\t: 0\nflags\t\t: fpu vme hypervisor sse\n")

    def hm(self, **kw):
        with state.locked():
            st = state.load()
            st["gateway"]["hostmon"] = kw
            state.save(st)

    def live_doc(self, lang=None):
        doc = json.loads(self.read("/run/kaskad/live.json"))
        return live.localize(doc, lang) if lang else doc

    def health(self):
        return self.live_doc()["host"]["health"]

    def host_events(self, kind=None):
        return [e for e in self.events() if str(e.get("kind")).startswith("host_") and (kind is None or
                                                                                     e["kind"] == kind)]

    def problems(self):
        return [i["kind"] for i in self.live_doc()["gateway"]["issues"]]

    def restart(self):
        """A new process of the collector (its memory and its live snapshot)."""
        collect.MEM.reset()
        hostprobe.reset()
        p = mock.patch.object(collect, "LIVE", live.Live())
        p.start()
        self.addCleanup(p.stop)

    def minutes(self, k):
        for _ in range(k * 6):
            self.round()

    def due(self):
        """The prober of the process (as the first pause makes it) with its series due now."""
        cfg = collect.MEM.host_cfg
        collect.MEM.prober = hostping.Prober(cfg["anchors"], random.Random(1), net=self.no_net())
        collect.MEM.prober.next_at = 0.0


class HealthDocTest(HostWorld):
    def test_live_json_host_health_by_the_contract(self):
        for _ in range(3):
            self.round()
        host = self.live_doc()["host"]
        for k in ("cpu", "mem_total", "mem_used", "load", "uptime", "cores", "busy", "steal", "iowait", "softirq"):
            self.assertIn(k, host)
        self.assertEqual(host["cores"], 2)
        h = host["health"]
        self.assertEqual(set(h) >= {"v", "at", "level", "since", "instant", "by", "cost", "hint", "top", "info",
                                    "checks", "probes"}, True)
        self.assertEqual((h["v"], h["instant"], h["by"], h["hint"]), (1, False, "collector", None))
        self.assertIn(h["level"], ("ok", "wait"))
        self.assertEqual(set(h["cost"]), {"cpu_ms_h", "since"})
        self.assertEqual(h["checks"]["disk"]["level"], "ok")
        self.assertFalse(h["probes"]["on"])
        self.assertEqual([a["addr"] for a in h["probes"]["anchors"]], list(hostping.DEFAULT_ANCHORS))

    def test_a_fast_round_keeps_the_health(self):
        self.round()
        self.round()
        doc = collect.LIVE.fast(self.w.runner)
        self.assertIn("health", doc["host"])
        self.assertEqual(doc["host"]["health"], collect.LIVE.doc["host"]["health"])
        self.assertIn("cpu", doc["host"])

    def test_hostmon_fails_the_round_and_live_json_go_on(self):
        collect._live_errors.clear()
        with mock.patch.object(hostmon, "evaluate", mock.Mock(side_effect=RuntimeError("boom"))):
            self.round()
            self.round()
        doc = self.live_doc()
        self.assertNotIn("health", doc["host"])
        self.assertIn("cpu", doc["host"])
        self.assertIn(self.aid, doc["exits"])                                   # the round of the exits went on
        self.assertEqual(self.log().count("GW collect: hostmon — RuntimeError"), 1)   # once an hour
        self.round()
        self.assertIn("health", self.live_doc()["host"])

    def test_hostmon_derive_fails_too(self):
        with mock.patch.object(hostmon, "derive", mock.Mock(side_effect=TypeError("bad"))):
            res = self.round()
        self.assertTrue(res["enabled"])
        self.assertNotIn("health", self.live_doc()["host"])

    def test_off_reads_nothing_and_says_off(self):
        self.hm(enabled=False)
        with mock.patch.object(hostprobe, "read", mock.Mock(side_effect=AssertionError("read"))):
            self.round()
            self.round()
        self.assertEqual(self.health()["level"], "off")

    def test_the_cost_grows_and_starts_anew_every_hour(self):
        self.round()
        c1 = self.health()["cost"]
        self.round()
        c2 = self.health()["cost"]
        self.assertGreater(c2["cpu_ms_h"], 0)
        self.assertGreaterEqual(c2["cpu_ms_h"], c1["cpu_ms_h"])
        self.assertEqual(c2["since"], c1["since"])
        self.now += 3600
        self.round()
        c3 = self.health()["cost"]
        self.assertGreater(c3["since"], c1["since"])
        self.assertIn("GW host: проверки здоровья сервера за час заняли", self.log())


class EventsAndProblemsTest(HostWorld):
    def test_disk_event_feed_metrics_two_languages_problem_and_clear(self):
        self.minutes(1)
        self.disk = 99.0
        self.minutes(1)
        evs = self.host_events("host_disk")
        self.assertEqual([(e["key"], e["args"]["level"], e["args"]["push"]) for e in evs],
                         [("gw_host.event.raise", "crit", True)])
        ru, en = self.live_doc("ru"), self.live_doc("en")
        t_ru = [e["text"] for e in ru["events"] if e["kind"] == "host_disk"][0]
        t_en = [e["text"] for e in en["events"] if e["kind"] == "host_disk"][0]
        self.assertTrue(t_ru.startswith("Сервер: "), t_ru)
        self.assertTrue(t_en.startswith("Server: "), t_en)
        self.assertIn("99", t_ru)
        self.assertIn("host_disk", self.problems())
        self.assertNotEqual(ru["gateway"]["problems"], en["gateway"]["problems"])
        self.assertEqual(en["host"]["health"]["top"][0]["text"], hostmon.say(en["host"]["health"]["top"][0]["msg"],
                                                                             "en"))
        import sqlite3
        c = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        kinds = [r[0] for r in c.execute("SELECT key FROM events")]
        c.close()
        self.assertIn("gw_host.event.raise", kinds)
        # cleaned within the window of the antispam: the problems and the light at once, the event at the end
        self.disk = 50.0
        self.minutes(1)
        self.assertNotIn("host_disk", self.problems())
        self.assertNotEqual(self.health()["level"], "crit")
        self.assertEqual(len(self.host_events("host_disk")), 1)
        self.minutes(15)
        self.assertEqual([e["key"] for e in self.host_events("host_disk")],
                         ["gw_host.event.raise", "gw_host.event.clear"])

    def test_one_problem_per_certificate_and_per_service(self):
        self.certs = [{"what": "site", "name": "example.com", "days_left": 5}]
        self.minutes(1)
        h = self.health()
        self.assertEqual(h["checks"]["cert"]["level"], "crit")
        self.assertNotIn("host_cert", self.problems())                         # site_cert of the summary says it
        self.w.sysd.units[collect.live.XRAY_UNIT]["active"] = False
        self.minutes(1)
        probs = self.problems()
        self.assertIn("gw_service", probs)
        self.assertFalse([p for p in probs if p.startswith("host_") and p != "host_cert"], probs)

    def test_one_problem_per_clock(self):
        self.patch_clock = mock.patch.object(hostprobe, "clock", lambda: {"synced": False, "offset_s": 0.2})
        self.patch_clock.start()
        self.addCleanup(self.patch_clock.stop)
        with mock.patch.object(collect, "clock_problem", lambda st, pool, mono=None: True):
            self.minutes(4)                                                     # «hold» 3 of the clock
            probs = self.problems()
        self.assertIn("gw_clock", probs)
        self.assertNotIn("host_clock", probs)
        self.assertEqual(self.health()["level"], "warn")                       # the light is yellow all the same
        self.minutes(1)                                                         # no quota: no gw_clock
        probs = self.problems()
        self.assertEqual(probs.count("host_clock"), 1)
        self.assertNotIn("gw_clock", probs)

    def test_a_restart_with_hostmon_json_no_second_raise_stale_alarm(self):
        self.disk = 99.0
        self.minutes(1)
        self.assertEqual(len(self.host_events("host_disk")), 1)
        doc = json.loads(self.read("/var/lib/kaskad/hostmon.json"))
        self.assertEqual((doc["owner"], doc["rules"]["disk"]["said"]), ("collector", "crit"))
        self.assertEqual(os.stat(self.path("/var/lib/kaskad/hostmon.json")).st_mode & 0o777, 0o600)
        self.restart()
        self.disk = None                                                        # the first read fails: no evaluation
        self.round()
        h = self.health()
        top = [f for f in h["top"] if f["id"] == "disk"]
        self.assertTrue(top and top[0]["stale"], h["top"])
        self.assertIn("host_disk", self.problems())
        self.disk = 99.0
        self.minutes(2)
        self.assertEqual(len(self.host_events("host_disk")), 1)                # no second raise

    def test_a_broken_hostmon_json_goes_aside(self):
        self.write("/var/lib/kaskad/hostmon.json", "{not json")
        self.round()
        self.assertTrue(os.path.exists(self.path("/var/lib/kaskad/hostmon.json.bad")))
        self.assertIn("испорчен", self.log())
        self.assertIn("health", self.live_doc()["host"])

    def test_the_file_of_the_bot_is_taken(self):
        st = hostmon.evaluate({}, {}, {"enabled": True}, {"owner": "bot"}, self.now, 1.0)[0]
        st["rules"]["disk"].update(said="crit", pushed="crit", find=hostmon.msg("gw_host.find.disk.crit", mount="/",
                                                                                 gb=1.0, pct=99))
        doc = dict(hostmon.to_disk(st), owner="bot", probes_hint=self.now)
        self.write("/var/lib/kaskad/hostmon.json", json.dumps(doc))
        self.disk = 99.0
        self.minutes(1)
        self.assertEqual(self.host_events("host_disk"), [])                    # said by the bot already
        self.assertEqual(json.loads(self.read("/var/lib/kaskad/hostmon.json"))["owner"], "collector")


class ProbesTest(HostWorld):
    def test_an_updated_node_without_the_key_no_socket_and_one_hint(self):
        self.minutes(1)
        self.assertEqual(self.sockets, [])
        hints = [e for e in self.host_events("host_loss") if e["key"] == "gw_host.event.probes_hint"]
        self.assertEqual(len(hints), 1)
        a = hints[0]["args"]
        self.assertEqual((a["check"], a["level"], a["push"]), ("loss", "na", False))
        self.assertIn("1.1.1.1", hints[0]["text"])
        en = [e["text"] for e in self.live_doc("en")["events"] if e.get("key") == "gw_host.event.probes_hint"][0]
        self.assertTrue(en.startswith("You can turn on light network probes"), en)
        self.assertIsNotNone(json.loads(self.read("/var/lib/kaskad/hostmon.json"))["probes_hint"])
        self.restart()                                                          # at once: no second hint
        self.round()
        self.assertEqual(len([e for e in self.host_events() if e["key"] == "gw_host.event.probes_hint"]), 1)

    def test_a_new_node_gen2_without_hostmon_probes_off_one_hint(self):
        with state.locked():
            st = state.load()
            st["gen"] = 2
            st["gateway"].pop("hostmon", None)
            state.save(st)
        self.assertTrue(state.born_new(state.load()))
        self.assertFalse(model.hostmon(state.load())["probes"])
        self.minutes(1)
        collect.pause(self.w.runner, 10)
        self.assertEqual(self.sockets, [])
        self.assertEqual(len([e for e in self.host_events() if e["key"] == "gw_host.event.probes_hint"]), 1)

    def test_probes_true_the_series_go_and_reach_hostmon(self):
        self.hm(probes=True)
        self.round()
        self.assertEqual(self.host_events(), [])                                # the key is there: no hint
        self.due()
        with mock.patch.object(collect, "_sleep", lambda s: None):
            collect.pause(self.w.runner, 10)
        self.assertTrue(self.sockets)
        self.assertIsNotNone(collect.MEM.ping)
        self.round()
        pr = self.health()["probes"]
        self.assertTrue(pr["on"])
        self.assertEqual([a["via"] for a in pr["anchors"]], ["icmp"] * 3)
        self.assertEqual(pr["loss10"], 0.0)

    def test_all_anchors_bad_the_defaults_reach_hostmon(self):
        self.hm(probes=True, anchors=["10.0.0.1", "127.0.0.1"])
        self.round()
        self.assertEqual(collect.MEM.host_cfg["anchors"], list(hostping.DEFAULT_ANCHORS))
        self.due()
        with mock.patch.object(collect, "_sleep", lambda s: None):
            collect.pause(self.w.runner, 10)
        self.round()
        rows = self.health()["probes"]["anchors"]
        self.assertEqual([a["addr"] for a in rows], list(hostping.DEFAULT_ANCHORS))
        self.assertTrue(all(a["loss10"] == 0.0 for a in rows))

    def test_anchors_of_a_kbk_checked_trimmed_one_line(self):
        bad = ["10.0.0.1", "224.0.0.1", "64:ff9b::7f00:1"] + ["1.0.%d.%d" % (i // 250, i % 250 + 1)
                                                              for i in range(1000)]
        self.hm(probes=True, anchors=bad)
        model._HM_TOLD[0] = None
        for _ in range(3):
            got = model.hostmon(state.load())["anchors"]
        self.assertEqual(len(got), 5)
        self.assertTrue(all(hostping.valid_anchor(a) == a for a in got))
        self.assertNotIn("10.0.0.1", got)
        self.assertEqual(self.log().count("gateway.hostmon.anchors"), 1)
        self.assertNotIn("10.0.0.1", self.log())                               # only the good ones in the journal

    def test_offline_no_loss(self):
        self.hm(probes=True)
        self.round()
        dark = {"anchors": [{"addr": a, "via": "icmp", "n": 3, "lost": 3, "runs": 1, "rtt": [], "na": None}
                            for a in hostping.DEFAULT_ANCHORS], "excl": False, "why": None, "trial": []}
        with mock.patch.dict(collect.MEM.node, {"offline": True}):
            for _ in range(60):
                collect.MEM.ping = dark
                self.now += 10
                st = state.load()
                res = {}
                collect._host_round(st, res)
                collect.MEM.node["offline"] = True
        self.assertFalse([e for e in res.get("events") or [] if e["kind"] == "host_loss"])
        self.assertIsNone(collect.MEM.host_state["rules"]["loss"].get("said"))


class PauseTest(HostWorld):
    def test_the_pause_is_never_longer_and_fast_rounds_go(self):
        self.hm(probes=True)
        self.round()
        slept, fast = [], []
        clock = [0.0]

        def mono():
            return 1000.0 + clock[0]

        def step(prober, hm, m, own=(), offline=False):
            clock[0] += 1.4                                                      # a series takes ≈ 1,4 s
            return {"anchors": [], "excl": False, "why": None, "trial": []}
        with mock.patch.object(collect, "_sleep", lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s))), \
                mock.patch.object(collect, "_mono", mono), mock.patch.object(hostping, "step", step), \
                mock.patch.object(live, "want_fresh", lambda: True), \
                mock.patch.object(collect.LIVE, "fast", lambda runner: fast.append(1)):
            collect.pause(self.w.runner, 10)
        self.assertLessEqual(clock[0], 10.0 + 1e-9)
        self.assertGreaterEqual(clock[0], 10.0 - 1e-9)
        self.assertTrue(fast)


class ReadHealthTest(HostWorld):
    def test_three_sources(self):
        self.round()
        h, by = hostprobe.read_health(self.now)
        self.assertEqual((by, h["by"], h["instant"]), ("collector", "collector", False))
        # the collector silent: the file of the bot (≤ 180 s)
        bot = {"v": 1, "at": self.now + 100, "level": "warn", "instant": True, "by": "bot",
               "top": [{"id": "disk", "msg": hostmon.msg("gw_host.find.disk.warn", mount="/", gb=8.0, pct=92),
                        "text": "x"}], "info": [], "checks": {}}
        self.write("/run/kaskad/host-health.json", json.dumps({"v": 1, "at": self.now + 100, "health": bot}))
        h, by = hostprobe.read_health(self.now + 120)
        self.assertEqual((by, h["level"]), ("bot", "warn"))
        self.assertNotEqual(h["top"][0]["text"], "x")                         # said again by its msg
        # both old: its own snapshot, the rules «instant» only, the hint of the bot on bash
        self.write("/etc/kaskad/config", 'BOT_IMPL="bash"\n')
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            state.save(st)
        h, by = hostprobe.read_health(self.now + 1000)
        self.assertEqual((by, h["by"], h["instant"], h["hint"]), ("panel", "panel", True, "bot_python"))
        self.assertIn("disk", h["checks"])
        self.assertNotIn("cpu", h["checks"])
        self.assertEqual(i18n.tr("gw_host.hint.bot_python", "en")[:6], "Health")


class ReadHealthLangTest(HostWorld):
    """REV-ns-a5 З-1: the texts in the language of the reader from each of the three sources (the server speaks ru)."""

    def said(self, h, lang):
        f = [x for x in h["top"] if x["id"] == "disk"][0]
        return f["text"], hostmon.say(f["msg"], lang)

    def test_the_language_of_the_reader_from_every_source(self):
        self.disk = 99.0
        self.minutes(1)
        for lang in ("en", "ru"):
            with i18n.use_lang(lang):
                h, by = hostprobe.read_health(self.now)
            self.assertEqual(by, "collector")
            text, want = self.said(h, lang)
            self.assertEqual(text, want)
            h, by = hostprobe.read_health(self.now, lang=lang)
            self.assertEqual(self.said(h, lang)[0], want)
        self.assertNotEqual(hostmon.say(h["top"][0]["msg"], "en"), hostmon.say(h["top"][0]["msg"], "ru"))
        bot = dict(h, by="bot", instant=True)
        self.write("/run/kaskad/host-health.json", json.dumps({"v": 1, "at": self.now + 100, "health": bot}))
        h, by = hostprobe.read_health(self.now + 120, lang="en")
        self.assertEqual(by, "bot")
        self.assertEqual(*self.said(h, "en"))
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            state.save(st)
        with i18n.use_lang("en"):
            h, by = hostprobe.read_health(self.now + 1000)
        self.assertEqual(by, "panel")
        self.assertEqual(*self.said(h, "en"))
        for lang, ctx in (("en", "ru"), ("ru", "en")):                       # an explicit lang beats the context
            with i18n.use_lang(ctx):
                h, by = hostprobe.read_health(self.now + 1000, lang=lang)
            self.assertEqual(by, "panel")
            self.assertEqual(*self.said(h, lang))


class CollectOnceTest(HostWorld):
    def test_gw_collect_once_writes_the_health(self):
        self.ok("gw", "collect", "--once")
        self.assertIn("health", self.live_doc()["host"])


class NoSocketTest(HostWorld):
    """REV-ns-a5 З-2, REV-ns-a5-sec З-3: the guards «not one socket» at the level of the collector — state.json → a
    round → the pause with a series due (each test fails when its guard is spoiled)."""

    def pause_due(self):
        if collect.MEM.prober is None and collect.MEM.host_cfg is not None:
            self.due()
        elif collect.MEM.prober is not None:
            collect.MEM.prober.next_at = 0.0
        with mock.patch.object(collect, "_sleep", lambda s: None):
            collect.pause(self.w.runner, 10)

    def test_probes_not_exactly_true_no_socket(self):
        absent = object()
        for v in (absent, False, 1, 1.0, "true", "yes", [True], {"on": True}, None):
            self.sockets.clear()
            collect.MEM.prober = None
            if v is absent:
                self.hm()
            else:
                self.hm(probes=v)
            self.round()
            self.assertFalse(collect.MEM.host_cfg["probes"], v)
            self.pause_due()
            self.assertEqual(self.sockets, [], v)                              # the pause asks hostping.step only
        self.hm(probes=True)                                                    # the world itself opens them
        self.round()
        collect.MEM.prober = None
        self.pause_due()
        self.assertTrue(self.sockets)

    def test_node_offline_no_socket(self):
        self.hm(probes=True)
        self.round()
        collect.MEM.node["offline"] = True
        self.pause_due()
        self.assertEqual(self.sockets, [])                                      # NS-D22 in the collector
        collect.MEM.node["offline"] = False
        self.pause_due()
        self.assertTrue(self.sockets)

    def test_gateway_off_no_socket_and_the_file_is_read_again(self):
        self.hm(probes=True)
        self.minutes(1)
        self.pause_due()
        self.assertTrue(self.sockets)
        self.sockets.clear()
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            state.save(st)
        self.assertEqual(self.round(), {"enabled": False})
        self.pause_due()
        self.assertEqual(self.sockets, [])                                      # the gateway off: no probe at all
        # meanwhile the bot owns hostmon.json and says the disk (§2.1 «Смена хозяина»)
        bst = hostmon.evaluate({}, {}, {"enabled": True}, {"owner": "bot"}, self.now, 1.0)[0]
        bst["rules"]["disk"].update(said="crit", pushed="crit", since=self.now - 600,
                                    find=hostmon.msg("gw_host.find.disk.crit", mount="/", gb=1.0, pct=99))
        self.write("/var/lib/kaskad/hostmon.json", json.dumps(dict(hostmon.to_disk(bst), owner="bot",
                                                                   probes_hint=self.now)))
        self.disk = 99.0
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = True
            state.save(st)
        self.minutes(1)
        self.assertEqual(self.host_events("host_disk"), [])                    # said by the bot: its file taken


class SeamsTest(HostWorld):
    """REV-ns-a5 З-2: the minute of the node and of the probes in metrics.db, the services in the rule."""

    def rows(self, sql):
        import sqlite3
        c = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def test_the_minute_rows_of_the_node_the_probes_and_the_canary(self):
        self.hm(probes=True)
        self.round()
        self.due()
        collect.MEM.host_busy = 5.0
        self.now += 30                                                          # the pause within the next minute
        with mock.patch.object(collect, "_sleep", lambda s: None):
            collect.pause(self.w.runner, 10)
        self.assertIsNotNone(collect.MEM.canary_ms)
        self.minutes(1)
        self.assertTrue(self.rows("SELECT ts FROM host_m1"))
        self.assertTrue([r for r in self.rows("SELECT canary_ms FROM host_m1") if r[0] is not None])
        got = set(r[0] for r in self.rows("SELECT anchor FROM host_ping_m1"))
        self.assertLessEqual(set(hostping.DEFAULT_ANCHORS), got)                # and «*», the whole node

    def test_no_canary_while_the_cpu_is_busy(self):
        self.round()
        self.due()
        collect.MEM.host_busy = 80.0
        with mock.patch.object(collect, "_sleep", lambda s: None):
            collect.pause(self.w.runner, 10)
        self.assertIsNone(collect.MEM.canary_ms)

    def test_a_service_down_the_light_red(self):
        self.minutes(1)
        self.assertNotEqual(self.health()["level"], "crit")
        self.w.sysd.units[collect.live.XRAY_UNIT]["active"] = False
        self.minutes(1)
        h = self.health()
        self.assertEqual(h["checks"]["services"]["level"], "crit")
        self.assertEqual(h["level"], "crit")
        self.assertNotIn("host_services", self.problems())                     # gw_service says it (NS-D4)

    def test_the_pause_counts_its_cost(self):
        self.hm(probes=True)
        self.round()
        self.due()
        before = collect.MEM.cost_s
        with mock.patch.object(collect, "_sleep", lambda s: None):
            collect.pause(self.w.runner, 10)
        self.assertGreater(collect.MEM.cost_s, before)


class Batch2Test(HostWorld):
    """Круг 2 of A5 (REV-ns-a5 П-2…П-4, REV-ns-a5-sec З-1, З-2, П-1)."""

    def test_args_kind_and_key_in_hostmon_json_do_not_break_live_json(self):
        self.round()
        doc = json.loads(self.read("/var/lib/kaskad/hostmon.json"))
        doc["rules"]["disk"] = {"said": "crit", "since": self.now - 60, "pushed": "crit",
                                "find": {"key": "gw_host.find.disk.crit",
                                         "args": {"kind": "x", "key": "y", "mount": "/", "gb": 1.0, "pct": 99}}}
        self.write("/var/lib/kaskad/hostmon.json", json.dumps(doc))
        self.restart()
        self.disk = None
        before = self.live_doc()["seq"]
        self.round()
        doc = self.live_doc()
        self.assertGreater(doc["seq"], before)                                  # live.json written
        self.assertIn("host_disk", self.problems())
        self.assertNotIn("live.json", self.log())

    def test_the_anchors_parsed_only_the_first_64(self):
        calls = []
        real = hostping.valid_anchor
        with mock.patch.object(hostping, "valid_anchor", lambda v, own=(): calls.append(v) or real(v, own)):
            good, bad = hostping.valid_anchors(["10.0.0.1"] * 100000)
            self.assertEqual((good, bad), ([], 100000))
            self.assertEqual(len(calls), hostping.SCAN_MAX)
            del calls[:]
            self.hm(probes=True, anchors=["x"] * 100000 + ["9.9.9.9"])
            got = model.hostmon(state.load())["anchors"]
        self.assertEqual(got, list(hostping.DEFAULT_ANCHORS))                  # the 100001-th never looked at
        self.assertEqual(len(calls), hostping.SCAN_MAX)

    def test_an_address_of_an_interface_is_no_anchor(self):
        self.write("/proc/net/fib_trie", "Main:\n  +-- 0.0.0.0/0 3 0 5\n     |-- 0.0.0.0\n        /0 universe UNICAST\n"
                   "Local:\n  +-- 0.0.0.0/0 3 0 5\n     |-- 9.9.9.9\n        /32 host LOCAL\n"
                   "     |-- 9.9.9.255\n        /32 link BROADCAST\n     |-- 127.0.0.1\n        /32 host LOCAL\n")
        self.write("/proc/net/if_inet6", "2a0104f8000000000000000000000002 02 40 00 80 eth0\n"
                   "00000000000000000000000000000001 01 80 10 80 lo\n")
        self.assertEqual(hostprobe.own_addrs(1.0), ("9.9.9.9", "127.0.0.1", "2a01:4f8::2", "::1"))
        self.hm(probes=True, anchors=["9.9.9.9", "2a01:4f8::2", "1.1.1.1"])
        self.round()
        self.assertEqual(collect.MEM.host_cfg["anchors"], ["1.1.1.1"])
        self.assertIn("9.9.9.9", collect.MEM.host_own)
        self.due()
        self.assertEqual(collect.MEM.prober.anchors, ["1.1.1.1"])              # the probes and the rule: one list
        hostprobe.reset()
        self.hm(probes=False, anchors=["9.9.9.9", "1.1.1.1"])                  # probes off: the files not read
        self.round()
        self.assertIsNone(hostprobe._C.own_at)

    def test_the_series_waits_when_the_pause_has_no_room(self):
        self.hm(probes=True)
        self.round()
        clock, runs = [0.0], []

        def mono():
            return 1000.0 + clock[0]

        def step(prober, hm, m, own=(), offline=False):
            runs.append(clock[0])
            clock[0] += 1.4
            return {"anchors": [], "excl": False, "why": None, "trial": []}
        with mock.patch.object(collect, "_sleep", lambda s: clock.__setitem__(0, clock[0] + s)), \
                mock.patch.object(collect, "_mono", mono), mock.patch.object(hostping, "step", step), \
                mock.patch.object(live, "want_fresh", lambda: False):
            collect.pause(self.w.runner, collect.RECHECK)
        self.assertLessEqual(clock[0], collect.RECHECK + 1e-9)                  # 2 + 2 + 1 s: not 5,4
        self.assertEqual(len(runs), 2)

    def test_the_light_since_survives_a_restart(self):
        self.disk = 99.0
        self.minutes(1)
        since = self.health()["since"]
        self.assertEqual(self.health()["level"], "crit")
        self.now += 600
        self.restart()
        self.round()
        self.assertEqual(self.health()["since"], since)

    def test_the_cost_line_speaks_its_language(self):
        self.round()
        collect.MEM.cost_s = 0.04321
        self.now += 3600
        self.round()
        line = [l for l in self.log().splitlines() if "проверки здоровья" in l][-1]
        self.assertIn("0,001%", line)
        self.assertTrue(i18n.tr("gw_host.log.cost", "en", ms="43", pct="0.001").endswith("(0.001% of one core)"))


class Batch2MoreTest(HostWorld):
    """Круг 2: the rest of the spoils of both checkers (REV-ns-a5-sec M9) and the room of the canary; REV-ns-a5 M3
    (``force`` of the hint) — ``Batch3Test.test_the_hint_forces_the_file_mid_process`` (REV-ns-a56-2 A5 П-1)."""

    def test_the_hint_is_written_at_once_even_with_the_levels_unchanged(self):
        self.hm(probes=False)
        self.minutes(1)
        self.assertEqual([e for e in self.host_events() if e["key"] == "gw_host.event.probes_hint"], [])
        self.hm()                                                               # the key gone (a .kbk of an old node)
        self.restart()                                                          # its file read: the levels the same
        self.round()
        self.assertIsNotNone(json.loads(self.read("/var/lib/kaskad/hostmon.json"))["probes_hint"])
        self.restart()
        self.round()
        self.assertEqual(len([e for e in self.host_events() if e["key"] == "gw_host.event.probes_hint"]), 1)

    def test_the_light_health_off_nothing_in_the_pause(self):
        self.hm(enabled=False, probes=True)
        self.round()
        collect.MEM.host_busy = 5.0
        collect.MEM.prober = hostping.Prober(list(hostping.DEFAULT_ANCHORS), random.Random(1), net=self.no_net())
        collect.MEM.prober.next_at = 0.0
        with mock.patch.object(collect, "_sleep", lambda s: None):
            collect.pause(self.w.runner, 10)
        self.assertEqual(self.sockets, [])
        self.assertIsNone(collect.MEM.canary_ms)

    def test_the_canary_waits_when_the_pause_has_no_room(self):
        self.round()
        collect.MEM.host_busy = 5.0
        collect._host_pause(hostping.CANARY_S / 2)
        self.assertIsNone(collect.MEM.canary_ms)
        collect._host_pause(hostping.CANARY_S)
        self.assertIsNotNone(collect.MEM.canary_ms)


class Batch3Test(HostWorld):
    """REV-ns-a56-2 A5 З-1 (the limits of the price: the cache of 60 s, /proc only with the health and the probes on,
    1 MB and 256 addresses, the mark of a set without the repr of the list) and П-1 (``since`` by the level and ≤ now,
    the time of the series for the canary, ``force`` of the hint)."""

    FIB = "Local:\n  +-- 0.0.0.0/0 3 0 5\n     |-- 9.9.9.9\n        /32 host LOCAL\n"

    def counted_heads(self):
        calls = []
        real = hostprobe._head

        def head(path, limit):
            calls.append(path)
            return real(path, limit)
        p = mock.patch.object(hostprobe, "_head", head)
        p.start()
        self.addCleanup(p.stop)
        return calls

    def test_the_addresses_are_read_once_in_60_s(self):
        self.write("/proc/net/fib_trie", self.FIB)
        calls = self.counted_heads()
        self.assertEqual(hostprobe.own_addrs(1.0), ("9.9.9.9",))
        hostprobe.own_addrs(59.0)
        self.assertEqual(len(calls), 2)                                         # two files, once
        hostprobe.own_addrs(61.5)
        self.assertEqual(len(calls), 4)
        hostprobe.reset()
        del calls[:]
        self.hm(probes=True)
        self.minutes(1)                                                         # 6 rounds of 10 s
        self.assertEqual(len(calls), 2)

    def test_the_addresses_not_read_with_the_health_off(self):
        self.write("/proc/net/fib_trie", self.FIB)
        calls = self.counted_heads()
        self.hm(enabled=False, probes=True)
        self.minutes(1)
        self.assertEqual(calls, [])
        self.assertIsNone(hostprobe._C.own_at)

    def test_a_proc_file_read_up_to_1_mb_and_256_addresses(self):
        pad = "  +-- 10.0.0.0/8 2 0 2\n" * (hostprobe.OWN_BYTES // 20)
        self.write("/proc/net/fib_trie", self.FIB + pad + "     |-- 7.7.7.7\n        /32 host LOCAL\n")
        own = hostprobe.own_addrs(1.0)
        self.assertIn("9.9.9.9", own)
        self.assertNotIn("7.7.7.7", own)                                        # beyond the first MB
        hostprobe.reset()
        self.write("/proc/net/fib_trie", "Local:\n" + "".join("     |-- 10.1.%d.%d\n        /32 host LOCAL\n"
                                                              % (i // 250, i % 250 + 1) for i in range(300)))
        own = hostprobe.own_addrs(1.0)
        self.assertEqual(len(own), hostprobe.OWN_MAX)
        self.assertEqual(own[0], "10.1.0.1")

    def test_the_mark_of_a_bad_set_is_short(self):
        old = model._HM_TOLD[0]
        self.addCleanup(model._HM_TOLD.__setitem__, 0, old)
        model._HM_TOLD[0] = None
        self.hm(probes=True, anchors=["x" * 200] * 100000)
        model.hostmon(state.load())
        sig = model._HM_TOLD[0]
        self.assertIsInstance(sig, tuple)                                       # never repr(values)
        self.assertEqual(sig[0], 100000)
        self.assertLessEqual(len(sig), hostping.SCAN_MAX + 1)
        self.assertTrue(all(len(v) <= 64 for v in sig[1:]))

    def crit_then(self, edit):
        """A red light, then a new process whose light has its ``top`` edited by ``edit(top, since)`` -> (the since
        before, health.since after)."""
        self.disk = 99.0
        self.minutes(1)
        self.assertEqual(self.health()["level"], "crit")
        since = self.health()["since"]
        self.now += 600
        self.restart()
        real = hostmon.light

        def light(state, *a, **kw):
            lt = real(state, *a, **kw)
            return dict(lt, top=edit(list(lt["top"]), since)) if lt["level"] == "crit" and lt["top"] else lt
        with mock.patch.object(hostmon, "light", light):
            self.round()
        return since, self.health()["since"]

    def test_the_since_after_a_restart_is_of_the_level_of_the_light(self):
        since, got = self.crit_then(lambda top, since: [dict(top[0], level="warn", since=since - 5000)] + top)
        self.assertEqual(got, since)                                            # not the older warn

    def test_the_since_after_a_restart_is_never_in_the_future(self):
        _since, got = self.crit_then(lambda top, since: [dict(f, since=self.now + 10 ** 6) for f in top])
        self.assertEqual(got, int(self.now))

    def pause_with_a_series(self, spent, room):
        self.round()
        collect.MEM.host_busy = 5.0
        clock = [0.0]

        def step(prober, hm, m, own=(), offline=False):
            clock[0] += spent
            return {"anchors": [], "excl": False, "why": None, "trial": []}
        with mock.patch.object(collect, "_mono", lambda: 1000.0 + clock[0]), \
                mock.patch.object(hostping, "step", step):
            collect._host_pause(room)
        return collect.MEM.canary_ms

    def test_the_canary_counts_the_time_of_the_series(self):
        room = hostping.SERIES_S + hostping.CANARY_S
        self.assertIsNone(self.pause_with_a_series(room - hostping.CANARY_S / 2, room))
        collect.MEM.canary = None
        self.assertIsNotNone(self.pause_with_a_series(hostping.SERIES_S - 0.5, room))

    def test_the_hint_forces_the_file_mid_process(self):
        """REV-ns-a5 M3: the hint told — hostmon.json written at once even when the levels said are the same (the
        mark of the state held still here) and the file is younger than an hour."""
        with mock.patch.object(collect, "_host_sig", lambda state: "same"):
            self.hm(probes=False)
            self.minutes(1)
            self.assertIsNone(json.loads(self.read("/var/lib/kaskad/hostmon.json"))["probes_hint"])
            self.hm()                                                           # the key gone mid-process
            self.round()
        self.assertIsNotNone(json.loads(self.read("/var/lib/kaskad/hostmon.json"))["probes_hint"])
