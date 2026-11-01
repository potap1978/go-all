"""live.json of the collector (WP-P1, spec §9.2): the full round, events, the 2 s live rounds while a panel watches,
/proc and /sys, the beacon."""

import io
import json
import os
import stat
import time
from unittest import mock

from kaskad import paths, util
from kaskad.gw import collect, live, ops, registry
from kaskad.gw.inbounds.base import DummyInbound
from tests.panel.panelcase import PanelCase


class WgLike(DummyInbound):
    """A dummy with what the live round needs of a WireGuard-style adapter."""

    iface = "kgw-wg"

    def tool(self, runner):
        return "/usr/bin/wg"

    def peers_path(self):
        return os.path.join(paths.gw_dir(), "wg", "peers.json")


class LiveTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        self.did = ops.device_add(self.w.runner, self.uid, "Ноутбук", kind="laptop", platform="linux",
                                  access=["wg"])["device"]["id"]

    def doc(self):
        return json.loads(self.read("/run/kaskad/live.json"))

    def test_full_round_writes_the_snapshot(self):
        self.ads["wg"].sessions = [{"device": self.did, "ip": "10.77.1.10", "since": 1790000000, "rx": 10, "tx": 20}]
        self.collect()
        p = paths.live_json()
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        d = self.doc()
        self.assertEqual(d["v"], 1)
        self.assertEqual(sorted(d), ["activity", "devices", "events", "exits", "forwards", "gateway", "groups", "host",
                                     "i18n", "inbounds", "metrics", "no_exit", "no_exit_ru", "policy", "seq", "ts", "v"])
        # stage 1: §4.4, §7.9; no_exit_ru — T15 Q6: the last step of a Russian group apart (SPEC-groups §8.3); groups
        # and policy.node_ru_offline — T15 Q6, the screens of the Russian groups (§8.9)
        self.assertEqual(sorted(d["policy"]), ["node_offline", "node_ru_offline"])
        # 3.3.7.10: the open window of activity (nothing written yet: ts null) and the minute of m1 written
        self.assertEqual((d["activity"]["ts"], sorted(d["activity"])), (None, ["at", "cur", "secs", "ts", "w"]))
        self.assertEqual(d["metrics"]["ts"] % 60, 0)
        self.assertAlmostEqual(d["ts"], time.time(), delta=5)
        self.assertTrue(d["gateway"]["enabled"])
        self.assertEqual(d["gateway"]["services"]["dns"], True)
        wg = d["inbounds"]["wg"]
        self.assertEqual((wg["enabled"], wg["running"], wg["sessions"], wg["ports"]), (True, True, 1, "51820/udp"))
        self.assertEqual(d["devices"][self.did], {"online": True, "inbound": "wg", "addr": "10.77.1.10",
                                                  "since": 1790000000, "rx": 10, "tx": 20})
        self.assertIsInstance(d["forwards"]["tcp:443"]["packets"], int)
        self.assertEqual((d["exits"][self.aid]["kind"], d["exits"][self.aid]["state"], d["exits"][self.aid]["alive"]),
                         ("awg", "ok", True))
        self.assertEqual(d["events"], [])
        seq = d["seq"]
        self.collect()
        self.assertEqual(self.doc()["seq"], seq + 1)

    def test_online_since_is_the_start_of_the_session_not_the_last_handshake(self):
        """T-G1b N3: «в сети с 02:41» of a phone that never went away moved every ~2 minutes — WireGuard and
        AmneziaWG report the latest handshake. The collector keeps the moment it saw the device come online until the
        device is gone (also across its own restart, and in the 2 s live rounds)."""
        s = {"device": self.did, "ip": "10.77.1.10", "since": 1790000000, "rx": 10, "tx": 20}
        self.ads["wg"].sessions = [dict(s)]
        self.collect()
        self.assertEqual(self.doc()["devices"][self.did]["since"], 1790000000)
        self.ads["wg"].sessions = [dict(s, since=1790000120)]               # the next handshake, two minutes later
        self.collect()
        self.assertEqual(self.doc()["devices"][self.did]["since"], 1790000000)
        self.assertEqual(collect.LIVE.fast(self.w.runner)["devices"][self.did]["since"], 1790000000)
        with mock.patch.object(collect, "LIVE", live.Live()):              # the collector starts again: same session
            self.collect()
            self.assertEqual(self.doc()["devices"][self.did]["since"], 1790000000)
        self.ads["wg"].sessions = []                                        # gone ...
        self.collect()
        self.assertEqual((self.doc()["devices"][self.did]["online"], self.doc()["devices"][self.did]["since"]),
                         (False, None))
        self.ads["wg"].sessions = [dict(s, since=1790000600)]               # ... and back: a new session
        self.collect()
        self.assertEqual(self.doc()["devices"][self.did]["since"], 1790000600)
        doc = self.doc()                                                     # a collector stopped for long: no guess
        doc["ts"] = time.time() - live.SINCE_KEEP - 60
        self.write("/run/kaskad/live.json", json.dumps(doc))
        with mock.patch.object(collect, "LIVE", live.Live()):
            self.ads["wg"].sessions = [dict(s, since=1790000720)]
            self.collect()
            self.assertEqual(self.doc()["devices"][self.did]["since"], 1790000720)

    def test_hook_sessions_have_unknown_traffic(self):
        from kaskad.gw import hooks
        with mock.patch.object(hooks, "sessions", lambda kind=None: [
                {"kind": "wg", "device": self.did, "ip": "10.77.1.10", "since": "2026-10-04T12:00:00Z"}]):
            self.collect()
        dev = self.doc()["devices"][self.did]
        self.assertEqual((dev["online"], dev["rx"], dev["tx"], dev["since"]), (True, None, None, 1791115200))

    def test_events_of_inbounds_exits_and_the_table(self):
        self.collect()
        self.ads["wg"].running = False
        self.collect()
        d = self.doc()
        (ev,) = d["events"]
        self.assertEqual((ev["kind"], ev["inbound"]), ("inbound_down", "wg"))
        self.assertIn("не работает", ev["text"])
        self.assertIn(ev["text"], d["gateway"]["problems"])
        self.ads["wg"].running = True
        self.collect()
        self.assertEqual(self.doc()["events"][-1]["kind"], "inbound_up")
        self.w.leg(2, handshake=None, probe=False)
        t0 = self.now
        for _ in range(12):                                              # 3.3.5: half of the checks of 2 min failed
            self.now += 10                                               # (one check per 10 s)
            self.collect()
            if any(e["kind"] == "exit_dead" for e in self.doc()["events"]):
                break
        self.assertLessEqual(self.now - t0, 70)                          # a whole failure: in about a minute
        # stage 1: the group on its last step tells it too (group_last_step, SE §3.6) — after the exit
        ev = [e for e in self.doc()["events"] if e["kind"] == "exit_dead"][-1]
        self.assertEqual((ev["kind"], ev["exit"]), ("exit_dead", self.aid))
        self.w.leg(2, handshake=10)
        for _ in range(2):                                               # back after two good checks
            self.now += 10
            self.collect()
        # stage 1: the exit back and its group off the last step (group_restored, SE §3.6) — in that order
        self.assertEqual([e["kind"] for e in self.doc()["events"]][-2:], ["exit_alive", "group_restored"])
        self.w.nft.flush_ruleset()
        self.collect()
        self.assertEqual(self.doc()["events"][-1]["kind"], "gw_part_restored")
        ids = [e["id"] for e in self.doc()["events"]]
        self.assertEqual(ids, sorted(set(ids)))
        n = len(ids)
        with mock.patch.object(collect, "LIVE", live.Live()):            # the collector starts again
            self.collect()
            self.assertEqual([e["id"] for e in self.doc()["events"]][:n], ids)

    def test_a_broken_live_round_does_not_stop_the_collector(self):
        with mock.patch.object(collect.LIVE, "full", side_effect=RuntimeError("boom")):
            res = collect.tick(self.w.runner)
        self.assertTrue(res["enabled"])
        self.assertIn("live.json не записан — RuntimeError", self.read("/var/log/kaskad.log"))


class FastRoundTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway(inbounds=())
        self.wg = WgLike(kind="wg")
        registry.register(self.wg)
        ops.inbound_install(self.w.runner, "wg")
        self.did = ops.device_add(self.w.runner, self.uid, "Ноутбук", access=["wg"])["device"]["id"]
        self.write("/etc/kaskad/gw/wg/peers.json", json.dumps({"PUB1": {"device": self.did, "ip": "10.77.1.10"}}))
        self.wg.sessions = [{"device": self.did, "ip": "10.77.1.10", "since": 1790000000, "rx": 10, "tx": 20}]
        self.transfer = ["PUB1\t500\t600\n"]
        self.w.runner.on(["/usr/bin/wg", "show", "kgw-wg", "transfer"], lambda argv, inp: (0, self.transfer[0], ""))
        for name, value in (("rx_bytes", "1000"), ("tx_bytes", "2000")):
            self.write("/sys/class/net/kgw-wg/statistics/" + name, value + "\n")
        self.collect()

    def test_live_round_reads_only_the_counters(self):
        before = len(self.w.runner.calls)
        doc = collect.LIVE.fast(self.w.runner)
        self.assertEqual([c for c, _ in self.w.runner.calls[before:]], [["/usr/bin/wg", "show", "kgw-wg", "transfer"]])
        self.assertEqual((doc["devices"][self.did]["rx"], doc["devices"][self.did]["tx"]), (500, 600))
        self.assertEqual((doc["inbounds"]["wg"]["rx"], doc["inbounds"]["wg"]["tx"]), (1000, 2000))
        full = json.loads(self.read("/run/kaskad/live.json"))
        self.assertEqual(doc["seq"], full["seq"] + 1)
        self.assertEqual(doc["exits"], full["exits"])

    def test_pause_runs_live_rounds_only_while_watched(self):
        sleeps = []
        with mock.patch.object(collect, "_sleep", sleeps.append):
            before = len(self.w.runner.calls)
            collect.pause(self.w.runner, 10)
            self.assertEqual(sleeps, [2.0] * 5)
            self.assertEqual(self.w.runner.calls[before:], [])
            live.touch_want()
            seq = json.loads(self.read("/run/kaskad/live.json"))["seq"]
            self.transfer[0] = "PUB1\t700\t800\n"
            collect.pause(self.w.runner, 10)
        doc = json.loads(self.read("/run/kaskad/live.json"))
        self.assertEqual(doc["seq"], seq + 4)                                 # 4 live rounds; the 5th is the tick
        self.assertEqual(doc["devices"][self.did]["rx"], 700)
        transfers = [c for c, _ in self.w.runner.calls[before:] if c[:2] == ["/usr/bin/wg", "show"]]
        self.assertEqual(len(transfers), 4)


class InboundReasonTest(PanelCase):
    """C14: what the adapter said and why an inbound does not run reach the panel through live.json — the collector
    asks systemd once per round and only about inbounds that are down; a request of the panel runs nothing."""

    UNIT = "kaskad-gw-wg.service"

    def setUp(self):
        super().setUp()
        self.gateway()
        real = self.ads["wg"].status
        self.ads["wg"].status = lambda runner: dict(real(runner), units=[self.UNIT],
                                                    warnings=["правило «L2TP только через IPsec» не поставлено", 5])
        self.write("/etc/systemd/system/" + self.UNIT, "[Unit]\n")

    def shows(self, since):
        return [c for c, _ in self.w.runner.calls[since:] if c[:2] == ["systemctl", "show"] and "Id" in c]

    def test_why_and_warnings_of_a_down_inbound(self):
        self.w.sysd.units[self.UNIT] = {"enabled": True, "active": False, "result": "exit-code", "status": 1}
        self.ads["wg"].running = False
        before = len(self.w.runner.calls)
        self.collect()
        self.assertEqual(len(self.shows(before)), 1)                         # one command, only for this inbound
        wg = json.loads(self.read("/run/kaskad/live.json"))["inbounds"]["wg"]
        self.assertEqual(wg["why"], "служба kaskad-gw-wg.service завершилась с ошибкой (код 1)")
        self.assertEqual(wg["warnings"], ["правило «L2TP только через IPsec» не поставлено"])
        before = len(self.w.runner.calls)
        row = [r for r in self.ok("GET", "/api/gw/inbounds")["inbounds"] if r["kind"] == "wg"][0]
        self.assertEqual(self.w.runner.calls[before:], [])                    # the request ran nothing
        self.assertEqual((row["running"], row["why"], row["warnings"]), (False, wg["why"], wg["warnings"]))
        # it runs again: systemd is not asked, nothing to explain
        self.ads["wg"].running = True
        self.w.sysd.units[self.UNIT]["active"] = True
        before = len(self.w.runner.calls)
        self.collect()
        self.assertEqual(self.shows(before), [])
        wg = json.loads(self.read("/run/kaskad/live.json"))["inbounds"]["wg"]
        self.assertEqual((wg["running"], wg["why"]), (True, None))
        row = [r for r in self.ok("GET", "/api/gw/inbounds")["inbounds"] if r["kind"] == "wg"][0]
        self.assertEqual((row["running"], row["why"], len(row["warnings"])), (True, None, 1))

    def test_an_old_snapshot_explains_nothing(self):
        self.ads["wg"].running = False
        self.collect()
        doc = json.loads(self.read("/run/kaskad/live.json"))
        doc["ts"] = time.time() - 3600                                       # the collector stopped long ago
        util.atomic_write(paths.live_json(), json.dumps(doc), 0o600)
        row = [r for r in self.ok("GET", "/api/gw/inbounds")["inbounds"] if r["kind"] == "wg"][0]
        self.assertEqual((row["running"], row["why"], row["warnings"]), (None, None, []))

    def test_down_reason_texts(self):
        from kaskad import sysrun
        out = {"rc": 0, "text": ""}
        r = sysrun.FakeRunner()
        r.on(["systemctl", "show"], lambda argv, inp: (out["rc"], out["text"], ""))

        def why(text, units=("a.service", "b.service"), rc=0):
            out["rc"], out["text"] = rc, text
            return live.down_reason(r, list(units))
        a = "Id=a.service\nLoadState=loaded\nActiveState=active\nSubState=running\nResult=success\nExecMainStatus=0\n"
        self.assertEqual(why(a + "\nId=b.service\nLoadState=loaded\nActiveState=activating\nSubState=auto-restart\n"
                             "Result=exit-code\nExecMainStatus=2\n"),
                         "служба b.service завершилась с ошибкой (код 2) — systemd перезапускает её")
        self.assertEqual(why(a + "\nId=b.service\nLoadState=not-found\nActiveState=inactive\n"),
                         "службы b.service нет на узле — переустановите протокол")
        self.assertIn("слишком часто перезапускалась", why("Id=a.service\nActiveState=failed\nResult=start-limit-hit\n"))
        self.assertEqual(why("Id=a.service\nActiveState=inactive\nResult=success\n"), "служба a.service остановлена")
        self.assertEqual(why(a), "")                                           # everything runs: nothing to say
        self.assertEqual(why("", rc=1), "")                                    # systemctl failed: unknown, not a guess
        n = len(r.calls)
        self.assertEqual(live.down_reason(r, ["../etc/passwd", "a b"]), "")    # names systemd would not take
        self.assertEqual(len(r.calls), n)


class ReadersTest(PanelCase):
    def test_read_fresh_and_the_beacon(self):
        self.assertEqual(live.read(), (None, None))
        util.atomic_write(paths.live_json(), json.dumps({"v": 1, "ts": time.time() - 100}), 0o600)
        doc, age = live.read()
        self.assertAlmostEqual(age, 100, delta=5)
        self.assertFalse(live.fresh(age))
        util.atomic_write(paths.live_json(), json.dumps({"v": 2, "ts": time.time()}), 0o600)
        self.assertEqual(live.read(), (None, None))                           # another format: not believed
        self.assertFalse(live.want_fresh())
        live.touch_want()
        self.assertTrue(live.want_fresh())
        old = time.time() - 60
        os.utime(paths.live_want(), (old, old))
        self.assertFalse(live.want_fresh())

    def test_host_from_proc(self):
        self.write("/proc/stat", "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 1 1 1\n")
        self.write("/proc/meminfo", "MemTotal:        2000000 kB\nMemFree:  100 kB\nMemAvailable:    1500000 kB\n")
        self.write("/proc/loadavg", "0.10 0.20 0.30 1/123 4567\n")
        self.write("/proc/uptime", "3600.55 7000.00\n")
        h = live.Host()
        first = h.sample()
        self.assertEqual(first, {"cpu": None, "mem_total": 2048000000, "mem_used": 512000000, "load": [0.1, 0.2, 0.3],
                                 "uptime": 3600})
        self.write("/proc/stat", "cpu  150 0 150 900 0 0 0 0 0 0\n")
        self.assertEqual(h.sample()["cpu"], 50.0)
        self.assertEqual(live.epoch("2026-10-04T12:00:00Z"), 1791115200)
        self.assertEqual((live.epoch("1790000000"), live.epoch(0), live.epoch("вчера"), live.epoch(True)),
                         (1790000000, None, None, None))

    def test_interface_counters(self):
        for d, rx, tx in (("kgw-l2tp0", 10, 20), ("kgw-l2tp1", 1, 2), ("eth0", 1000, 1000)):
            self.write("/sys/class/net/%s/statistics/rx_bytes" % d, "%d\n" % rx)
            self.write("/sys/class/net/%s/statistics/tx_bytes" % d, "%d\n" % tx)
        self.assertEqual(live.iface_bytes(["kgw-l2tp*"]), (11, 22))
        self.assertEqual(live.iface_bytes(["kgw-none"]), (None, None))
        self.assertEqual(live.iface_bytes(["../etc"]), (None, None))
