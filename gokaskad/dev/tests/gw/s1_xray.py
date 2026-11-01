"""Helpers of the tests of T5 (the xray path of stage 1): a Decision of T2's shape, the ``exits.target`` of T4 by it
(SPEC-stage1-exits §2.2 — T4 owns the real one; these tests check what xray.py makes of its answer), a node with
SOCKS5 accounts and the lists of «Россия напрямую»."""

import copy
from unittest import mock

from kaskad import secrets
from kaskad.gw import exits, model, xray
from kaskad.rules import apply as rules_apply, lists
from tests.fakes_gw import awg_line, write_pool, VLESS_A, VLESS_B
from tests.gwcase import GwCase

MAN = {"geosite": {"codes": [c.upper() for c in lists.CATEGORIES]}, "geoip": {"codes": ["DIRECT"]}}


def decision(pins=None, states=None, steps=None, gw_all=None, quota_off=None, active_tier="t1"):
    """A Decision (gw/assign.py): the pins of «Основная» {device: exit}, the states, the steps of its devices."""
    return {"version": 1, "states": dict(states or {}), "moves": [], "events": [], "gw_all": gw_all,
            "gw_all_exit": None, "dns_via": "main", "quota_off": list(quota_off or []),
            "groups": {"main": {"pins": dict((d, {"exit": e, "tier": "t1", "since": 0, "why": "new"})
                                             for d, e in (pins or {}).items()),
                                "active_tier": active_tier, "tier_good_since": {}, "last_step": None,
                                "last_since": None, "night_on": None, "steps": dict(steps or {})}}}


def fake_target(st, device, dec):
    """``exits.target(st, device, decision)`` of T4 as §2.2 says it — only what xray.py reads (``xray``)."""
    route = model.route_of(st, device)
    via = route["via"]
    if via in ("block", "direct"):
        return {"xray": {"outboundTag": via}, "step": "chosen"}
    g = (dec.get("groups") or {}).get("main") or {}
    if via.startswith("exit:") and dec["states"].get(via[5:]) in ("ok", "degraded"):
        return {"xray": {"outboundTag": exits.tag(via[5:])}, "step": "exit"}
    step = (g.get("steps") or {}).get(device["id"])
    if step == "last_direct":
        return {"xray": {"outboundTag": "direct"}, "step": step}
    pin = (g.get("pins") or {}).get(device["id"])
    if step == "last_block" or not pin:
        return {"xray": {"outboundTag": xray.FAIL_BLOCK}, "step": "last_block"}
    return {"xray": {"outboundTag": exits.tag(pin["exit"])}, "step": "exit"}


class S1Case(GwCase):
    """The scenario of GwCase (VLESS exit ``vid`` = line 1, AmneziaWG ``aid`` = line 2) + a second VLESS exit ``bid``
    (line 3), SOCKS5 on with an account per device, the lists of RoscomVPN «ready»."""

    def setUp(self):
        super().setUp()
        self._t5 = [mock.patch.object(exits, "target", fake_target),
                    mock.patch.object(rules_apply, "manifest", lambda *a, **kw: copy.deepcopy(MAN)),
                    mock.patch.object(rules_apply, "ready", lambda *a, **kw: True)]
        for p in self._t5:
            p.start()

    def tearDown(self):
        for p in self._t5:
            p.stop()
        super().tearDown()

    def node(self, socks=True, save=True):
        st = self.scenario(save=False)
        # an UPDATED node (gen 1, SPEC-stage1-exits §1.3; tests/helpers.legacy_state): «Россия напрямую» by default
        # as in 3.3.x — a new node (gen 2, R3/D-019) has all_exit, and the lists these tests check would not be drawn
        st["gen"] = 1
        write_pool([VLESS_A, awg_line(2, name="Amsterdam"), VLESS_B])
        self.pool = exits.pool(self.w.runner)
        self.bid = exits.exit_id(VLESS_B)
        self.sec = secrets.load()
        if socks:
            st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080, "udp": False}
            for d in st["gateway"]["devices"]:
                d["access"].append("socks5")
                self.sec["dev.%s.socks5.password" % d["id"]] = "pw-" + d["id"][-4:]
        if save:
            from kaskad import state
            state.save(st)
            st = state.load()
            self.with_xports()
        self.st = st
        return st

    def dev(self, did):
        return next(d for d in self.st["gateway"]["devices"] if d["id"] == did)

    def dec(self, **kw):
        kw.setdefault("states", {self.vid: "ok", self.bid: "ok", self.aid: "ok"})
        kw.setdefault("pins", {"d-00000001": self.vid, "d-00000002": self.bid, "d-00000003": self.vid})
        kw.setdefault("gw_all", exits.tag(self.vid))
        return decision(**kw)

    def render(self, dec=None, pool=None):
        return xray.render(self.st, self.sec, self.pool if pool is None else pool, (), self.dec() if dec is None else dec)


def rules_of(cfg, prefix=None, **match):
    out = []
    for r in cfg["routing"]["rules"]:
        if prefix is not None and not str(r.get("ruleTag") or "").startswith(prefix):
            continue
        if all(r.get(k) == v for k, v in match.items()):
            out.append(r)
    return out


def chain_of(cfg, login):
    return [r for r in cfg["routing"]["rules"] if r.get("user") == [login] and str(r.get("ruleTag") or "")[:3]
            in ("vl-", "sk-", "h2-")]
