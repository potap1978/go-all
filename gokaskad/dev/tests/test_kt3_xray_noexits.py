"""KT3-7 / KT3-8 (stand of 3.3.7.1): every exit off by the owner — a change of a device's route went through a restart
of the gateway xray (cut VLESS/SOCKS5/Hysteria2 of everyone): the hot path put ``bo gw-all`` into a routing without
gw-all, xray answered «cannot find tag». The collector asked the same ``bo`` every tick (the journal every 5–10 s)."""

from unittest import mock

from kaskad.gw import collect, exits, xray
from tests.test_gw_vless import VlessCase


class NoExitsHotTest(VlessCase):
    def node_without_exits(self):
        self.enabled_world()
        rc, _, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        self.ok("gw", "user", "add", "Мама")
        d = self.add_device()
        for eid in (self.vid, self.aid):
            self.ok("gw", "exit", eid, "--disable")
        self.assertFalse(xray.has_balancer("gw-all"))           # nothing drawn: no gw-all in the config
        return d

    def test_a_route_change_without_exits_is_live(self):
        d = self.node_without_exits()
        real = exits.decision

        def decision(*a, **kw):                  # as on the stand: the collector left «Основная» on its last step
            dec = real(*a, **kw)
            dec["gw_all"] = "block"
            return dec
        p = mock.patch.object(exits, "decision", decision)
        p.start()
        self.addCleanup(p.stop)
        for via in ("direct", "pool", "direct", "pool"):
            restarts, self.w.xray.api[:] = self.restarts(), []
            log0 = len(self.log())
            self.ok("gw", "device", "route", d["id"], via)
            verbs = [v for v, _, _ in self.w.xray.api]
            self.assertEqual(self.restarts(), restarts, (via, verbs))          # live: nobody is cut
            self.assertNotIn("bo", verbs)                                       # no gw-all — no override to put
            self.assertNotIn("cannot find tag", self.log()[log0:])
        rule = [r for r in self.cfg()["routing"]["rules"] if r.get("user") == [d["login"]]
                and str(r.get("ruleTag") or "").startswith("vl-")]
        self.assertEqual([r.get("outboundTag") for r in rule], [xray.FAIL_BLOCK])   # pool, no exit: closed

    def test_the_collector_asks_no_bo_of_an_absent_balancer(self):
        self.node_without_exits()
        calls = []
        real = xray.balancer_override
        self.w.sysd.units[xray.UNIT]["since_mono"] = 5.0
        with mock.patch.object(xray, "balancer_override",
                               lambda runner, tgt, bal="gw-all", *a, **kw: calls.append(tgt) or real(runner, tgt, bal)):
            for _ in range(3):
                collect._gw_all(self.w.runner, {"gw_all": "block"}, [])
        self.assertEqual(calls, [])
        self.assertNotIn("cannot find tag", self.log())
        self.assertEqual(collect.MEM.gw_all["target"], collect.NO_BALANCER)
