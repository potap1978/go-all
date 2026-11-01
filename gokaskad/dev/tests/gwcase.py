"""A RootCase for the gateway: GwWorld, the clock of the exits watchdog, registry reset, dummy adapters."""

import hashlib
import random
from unittest import mock

from kaskad import state, sysrun
from kaskad.gw import collect, exits, hostprobe, model, net, ops, registry, xray
from tests.fakes_gw import FAKE_XRAY, T0, GwWorld, register_dummies, scenario_state
from tests.fakes_nft import write_os_release
from tests.helpers import RootCase


class GwCase(RootCase):
    UFW = None

    def setUp(self):
        super().setUp()
        self.now = T0
        self._patches = [mock.patch.object(exits, "_now", lambda: self.now),
                         mock.patch.object(exits, "_mono", lambda: self.now - T0 + 1000.0),
                         mock.patch.object(collect, "_now", lambda: self.now),
                         # connections through the legs (SO_MARK needs root): the fake node answers them
                         mock.patch.object(exits, "connect_through", lambda marks, *a, **kw: self.w.connect(marks)),
                         # stage 1: the response probe of AmneziaWG (HTTP through the leg) and the node's own network
                         # (§4.6) — the fake node answers them as it answers the connections
                         mock.patch.object(exits, "http_probe", lambda marks, *a, **kw: dict(
                             (m, 20 if self._leg_answers(m) else None) for m in marks)),
                         mock.patch.object(exits, "direct_probe", lambda *a, **kw: getattr(self.w, "net_up", True)),
                         # the system users of the fake node (kaskad-xray once useradd made it)
                         mock.patch.object(net, "_getpwnam", lambda name: self.w.users.getpwnam(name)),
                         # ids, logins and random ports reproducible: rendezvous results do not flake
                         mock.patch.object(model, "_rng", random.Random(4)),
                         mock.patch.object(ops, "_rng", random.Random(5)),
                         # 3.3.4: the fake egress xray (touch_exec) is «the pinned release» of this machine
                         mock.patch.dict(xray.XRAY_SHA256, {xray.arch(): hashlib.sha256(FAKE_XRAY).hexdigest()})]
        for p in self._patches:
            p.start()
        registry.reset()
        collect.MEM.reset()                 # stage 1: the memory of the collector process (health, pins, overrides)
        hostprobe.reset()                   # A5: what hostprobe keeps per process (the lists, the facts of the node)
        self.w = self.make_world()
        self._old_runner = sysrun._default
        sysrun._default = self.w.runner
        write_os_release(self)

    def _leg_answers(self, mark):
        """The far end of the leg of ``mark`` answers (as ``FakeWorld.connect``, not counted as a round)."""
        name = "kawg%d" % (mark - exits.AWG_MARK_BASE)
        return name in self.w.ip.links and name in self.w.curl.legs_ok

    def make_world(self, **kw):
        if self.UFW is not None and "ufw" not in kw:
            kw["ufw"] = self.UFW()
        return GwWorld(now=lambda: self.now, **kw)

    def tearDown(self):
        sysrun._default = self._old_runner
        for p in self._patches:
            p.stop()
        registry.reset()
        self.assertEqual(self.w.runner.unmatched, [])
        super().tearDown()

    def dummies(self):
        return register_dummies(("wg", {"ifaces": ["kgw-wg"]}),
                                ("awg2", {"default_port": 41023, "ifaces": ["kgw-awg2"]}))

    def with_xports(self):
        """gw_xports in the fake table — a node of stage 1 (``ops._apply``: nft, then xray): without it xray.apply keeps
        the old file (B3, REV-s1-b1 З1). For the tests of xray, not of the table: the set alone."""
        from kaskad import nft
        from kaskad.gw import net
        if net.XPORTS not in ((nft.read_live(self.w.runner) or {}).get("set_info") or {}):
            self.w.nft.run_script("add table ip kaskad\nadd set ip kaskad %s { %s }\n"
                                  % (net.XPORTS, net.set_spec(net.XPORTS)), True)

    def scenario(self, save=True, **kw):
        self.ads = self.dummies()
        st, vid, aid = scenario_state(**kw)
        if save:
            state.save(st)
            st = state.load()
        self.vid, self.aid = vid, aid
        return st
