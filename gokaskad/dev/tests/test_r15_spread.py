"""3.3.7.15 F12б (КТ) — «Разнести выходы по адресам» (k3/handoff/SPEC-R1-3315-2026-10-30.md §3 F12б «Тесты»;
gw/mip_spread.py).

What is checked:
* the layout: 3 exits × 2 addresses — balanced and stable (the same answer again and whatever the order of the pool);
  +1 address — no exit with an address moves (at most ⌈N/3⌉ — here none), a new exit goes to the new address first;
* two legs to one ``E:P`` — one address; a leg of such a pair with an address — the other one takes it; with an address
  not «для выхода» — left as it is; an address without ``route_ok`` never goes to a leg; an exit to an IPv6 — left;
* ``dry`` writes nothing (state.json, kaskad.nft, conntrack); the write — 409 confirm without ``yes``, with it the old
  field ``exits[id].src`` (SNAT of the leg, ``sendThrough`` of the xray exit), the leg whose address changed forgotten
  by conntrack; again — nothing to spread; fewer than two addresses «для выхода» — refused; the way back ``… main``;
* the panel ``POST /api/gw/addresses/spread`` (``ver``, the body, ``dry``, 409 confirm, ``yes``);
* REV-3315-b3: the words of «nothing to spread» by its reason (З-1), ``route_out`` in ``GET /api/gw/addresses`` (З-2),
  the way back in the panel and while the route address is not «для выхода» (П-1), no «Не трогаю» (П-2);
* the rollback: the state after a spread on the trees of 3.3.7.13 (bc541e29) and 3.3.7.14 (fc5ef683) — the same SNAT
  of the leg and ``sendThrough`` as here (``tests/r15_spread_side.py`` in a process of its own)."""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from kaskad import paths, state
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import exits, mip, mip_spread, ops, policy_ops, xray
from kaskad.i18n import tr
from tests.fakes_gw import FakeIp, VLESS_A, VLESS_B, awg_line, write_pool
from tests.panel.panelcase import PanelCase
from tests.test_mip_s34 import A_IP, B_IP, LEG_EP, Node, cache, chain, leg_conf, two_addresses

C_IP = "85.143.0.30"
OTHER_EP = ("198.51.100.32", 51820)


def view(*ips, bad_route=()):
    """The view of a cache with ``ips`` on eth0 (the first — the route's); ``bad_route`` — an answer from these leaves
    another way (source routing)."""
    c = cache([(ip, 24, "eth0", n > 0) for n, ip in enumerate(ips)], ips[0])
    for ip in bad_route:
        c["ways"][ip] = dict(c["ways"][ip], via="85.143.0.254")
    return mip.view(c)


def xr(eid):
    return {"id": eid, "kind": "xray", "outbound": {"protocol": "vless"}}


def aw(eid, iface="kawg9"):
    return {"id": eid, "kind": "awg", "iface": iface}


def st_with(src=None, out=None):
    g = {"exits": dict((eid, {"src": ip}) for eid, ip in (src or {}).items())}
    if out is not None:
        g["addresses"] = {"out": {"*": list(out)}}
    return {"gateway": g}


BACK = "панель → «Адреса сервера» → у выхода «по маршруту системы»"     # REV-3315-b3 П-1


def by_ip(moves):
    out = {}
    for m in moves:
        out[m["src"]] = out.get(m["src"], 0) + 1
    return out


# ── the layout (pure) ──────────────────────────────────────────────────────

class LayoutTest(unittest.TestCase):
    def test_three_exits_two_addresses_balanced_and_stable(self):
        pool = [xr("x-aaaa"), xr("x-bbbb"), xr("x-cccc")]
        v = view(A_IP, B_IP)
        p = mip_spread.plan({}, v, pool, {})
        self.assertEqual(sorted(by_ip(p["moves"]).values()), [1, 2], p)               # 2 and 1, not 3 and 0
        self.assertEqual(mip_spread.plan({}, v, list(reversed(pool)), {})["moves"], p["moves"])    # the pool's order
        self.assertEqual(mip_spread.plan({}, v, pool, {})["moves"], p["moves"])                     # again: the same
        self.assertEqual(p["cands"], sorted([A_IP, B_IP], key=mip._ipkey))
        # the exits with an address of their own are counted: one on A already -> the other two not both on A
        p = mip_spread.plan(st_with({"x-aaaa": A_IP}), v, pool, {})
        self.assertEqual([m["id"] for m in p["moves"]], ["x-bbbb", "x-cccc"])
        self.assertGreaterEqual(by_ip(p["moves"]).get(B_IP, 0), 1, p)

    def test_one_more_address_moves_no_exit_with_an_address(self):
        ids = ["x-%04d" % n for n in range(9)]
        pool = [xr(x) for x in ids]
        first = mip_spread.plan({}, view(A_IP, B_IP), pool, {})
        st = st_with(dict((m["id"], m["src"]) for m in first["moves"]))
        again = mip_spread.plan(st, view(A_IP, B_IP, C_IP), pool, {})
        moved = [m for m in again["moves"] if m["id"] in ids]
        self.assertLessEqual(len(moved), math.ceil(len(ids) / 3.0))
        self.assertEqual(moved, [])                                            # the layout of a spread stays for good
        new = mip_spread.plan(st, view(A_IP, B_IP, C_IP), pool + [xr("x-new1"), xr("x-new2")], {})
        self.assertEqual([(m["id"], m["src"]) for m in new["moves"]], [("x-new1", C_IP), ("x-new2", C_IP)])
        # (A and B hold 4 and 5: the empty address gets the new ones first)

    def test_a_tie_by_the_hash_of_the_exit(self):
        got = mip_spread.layout([{"key": "x-1", "n": 1, "cands": [A_IP, B_IP]}], {})
        want = exits.hrw("x-1", [{"id": A_IP}, {"id": B_IP}])["id"]
        self.assertEqual(got, {"x-1": want})
        self.assertEqual(mip_spread.layout([{"key": "x-1", "n": 1, "cands": [A_IP, B_IP]}], {want: 5}),
                         {"x-1": B_IP if want == A_IP else A_IP})              # the load first
        self.assertEqual(mip_spread.layout([{"key": "x-1", "n": 1, "cands": []}], {}), {})

    def test_two_legs_to_one_server_one_address(self):
        pool = [aw("a-1", "kawg2"), aw("a-2", "kawg3"), aw("a-3", "kawg4")]
        eps = {"a-1": LEG_EP, "a-2": LEG_EP, "a-3": OTHER_EP}
        v = view(A_IP, B_IP)
        p = mip_spread.plan({}, v, pool, eps)
        src = dict((m["id"], m["src"]) for m in p["moves"])
        self.assertEqual(sorted(src), ["a-1", "a-2", "a-3"])
        self.assertEqual(src["a-1"], src["a-2"])                               # one E:P — one address
        self.assertNotEqual(src["a-1"], src["a-3"])                            # the pair counts 2 for the balance
        # one leg of the pair with an address — the other takes it (it went from it already: the SNAT is by Endpoint)
        p = mip_spread.plan(st_with({"a-1": B_IP}), v, pool, eps)
        m2 = [m for m in p["moves"] if m["id"] == "a-2"][0]
        self.assertEqual((m2["src"], m2["follow"]), (B_IP, True))
        self.assertEqual(mip_spread._reconnect({}, v, [m2]), [])               # no reconnect for it
        # the address of the other leg is not «для выхода» — left as it is, never another address
        p = mip_spread.plan(st_with({"a-1": B_IP}, out=[A_IP, C_IP]), view(A_IP, B_IP, C_IP), pool, eps)
        self.assertEqual([s for s in p["skipped"] if s["id"] == "a-2"], [{"id": "a-2", "why": "same_endpoint"}])
        self.assertNotIn("a-2", [m["id"] for m in p["moves"]])

    def test_no_route_ok_no_address_for_a_leg(self):
        pool = [aw("a-1", "kawg2"), aw("a-2", "kawg3"), xr("x-1"), xr("x-2")]
        eps = {"a-1": LEG_EP, "a-2": OTHER_EP}
        p = mip_spread.plan({}, view(A_IP, B_IP, bad_route=[B_IP]), pool, eps)
        src = dict((m["id"], m["src"]) for m in p["moves"])
        self.assertEqual((src["a-1"], src["a-2"]), (A_IP, A_IP))                # B: another way out — never to a leg
        self.assertEqual((src["x-1"], src["x-2"]), (B_IP, B_IP))                # xray balances around them
        p = mip_spread.plan({}, view(A_IP, B_IP, bad_route=[A_IP, B_IP]), pool, eps)
        self.assertEqual(sorted(s["id"] for s in p["skipped"] if s["why"] == "route"), ["a-1", "a-2"])
        self.assertEqual(sorted(m["id"] for m in p["moves"]), ["x-1", "x-2"])

    def test_the_exits_left_as_they_are(self):
        six = {"id": "x-six", "kind": "xray", "outbound": None, "host": "[2001:db8::7]:443"}
        pool = [six, aw("a-noleg"), {"id": "b-1", "kind": "bad"}, xr("x-1")]
        p = mip_spread.plan({}, view(A_IP, B_IP), pool, {})
        self.assertEqual(p["skipped"], [{"id": "a-noleg", "why": "no_leg"}, {"id": "x-six", "why": "family"}])
        self.assertEqual([m["id"] for m in p["moves"]], ["x-1"])
        # «для выхода» chosen: only those, and only the ones on the machine
        p = mip_spread.plan(st_with(out=[B_IP, C_IP]), view(A_IP, B_IP, C_IP), [xr("x-1"), xr("x-2")], {})
        self.assertEqual(set(by_ip(p["moves"])), {B_IP, C_IP})
        p = mip_spread.plan(st_with(out=[B_IP, C_IP]), view(A_IP, B_IP), [xr("x-1")], {})
        self.assertEqual(p["cands"], [B_IP])                                   # C gone


# ── the action (the CLI world of tests/test_mip_s34.Node: VLESS_A + the leg kawg2, A and B) ──

class Spread(Node):
    def state_bytes(self):
        return open(paths.state(), "rb").read()

    def nft_bytes(self):
        return open(paths.nft_file(), "rb").read()

    def outbound(self, eid):
        cfg = json.load(open(xray.config_path(), encoding="utf-8"))
        return [o for o in cfg["outbounds"] if o.get("tag") == exits.tag(eid)][0]


class ActionTest(Spread):
    def test_dry_then_the_confirmed_write_then_nothing(self):
        st0, nft0, ct0 = self.state_bytes(), self.nft_bytes(), list(self.w.conntrack.calls)
        dry = mip_spread.spread_exits(self.w.runner, dry=True)
        self.assertEqual((dry["dry"], dry["flushed"]), (True, 0))
        self.assertEqual(sorted(m["id"] for m in dry["moves"]), sorted([self.vid, self.aid]))
        self.assertEqual(sorted(by_ip(dry["moves"]).values()), [1, 1])          # one on A, one on B
        self.assertTrue(dry["consequences"])
        self.assertIn("сайты по-прежнему видят адреса выходов", dry["consequences"][-1])
        self.assertEqual((self.state_bytes(), self.nft_bytes(), self.w.conntrack.calls), (st0, nft0, ct0))
        with self.assertRaises(ConflictError) as cm:                           # without yes: the question
            mip_spread.spread_exits(self.w.runner)
        self.assertEqual(cm.exception.data["need"], "confirm")
        self.assertEqual(cm.exception.data["consequences"], dry["consequences"])
        self.assertEqual((self.state_bytes(), self.nft_bytes()), (st0, nft0))
        res = mip_spread.spread_exits(self.w.runner, yes=True)
        self.assertEqual(res["moves"], dry["moves"])                           # the dry run said what was done
        src = dict((m["id"], m["src"]) for m in res["moves"])
        self.assertEqual(mip.src_map(state.load()), src)                       # the field of 3.3.7.13, nothing else
        self.assertNotIn("addresses", state.load()["gateway"])
        post = chain("gw_post")
        self.assertIn("ip daddr %s udp dport %d snat to %s" % (LEG_EP + (src[self.aid],)), post)
        self.assertEqual(self.outbound(self.vid).get("sendThrough"), src[self.vid])
        argv = ["-D", "-p", "udp", "-d", LEG_EP[0], "--dport", str(LEG_EP[1])]
        moved = src[self.aid] != A_IP                                          # A — the source of the route
        self.assertEqual((res["flushed"], self.w.conntrack.calls.count(argv)), (1, 1) if moved else (0, 0))
        self.assertIn("Готово: выходов — 2", res["text"])
        self.assertIn(BACK, res["text"])                                       # the way back — in the panel (П-1)
        self.assertNotIn("kaskadctl", res["text"])
        self.assertTrue([w for w in res["warnings"] if "«Прокси для API Telegram»" in w])
        st1 = (self.state_bytes(), os.stat(paths.state()).st_mtime_ns)
        with mock.patch.object(ops, "_change", side_effect=AssertionError("a write")):
            again = mip_spread.spread_exits(self.w.runner, yes=True)           # everything has an address now
        self.assertEqual((again["moves"], again["consequences"]), ([], []))
        self.assertIn("Разносить нечего: у каждого выхода уже свой адрес", again["text"])
        self.assertIn(BACK, again["text"])
        self.assertEqual((self.state_bytes(), os.stat(paths.state()).st_mtime_ns), st1)     # no write, no apply
        self.ok("gw", "addresses", "exit", self.aid, "main")                   # the way back — the old command
        self.ok("gw", "addresses", "exit", self.vid, "main")
        self.assertEqual(mip.src_map(state.load()), {})
        self.assertFalse([r for r in chain("gw_post") if "udp dport %d snat to" % LEG_EP[1] in r])

    def test_the_leg_on_the_route_address_is_not_forgotten(self):
        with mock.patch.object(mip_spread, "layout", lambda units, load: dict((u["key"], A_IP) for u in units)):
            res = mip_spread.spread_exits(self.w.runner, yes=True)
        self.assertEqual(mip.src_map(state.load()), {self.vid: A_IP, self.aid: A_IP})
        self.assertEqual(res["flushed"], 0)                                    # it went from A before
        self.assertFalse([c for c in res["consequences"] if "переподключатся" in c])

    def test_the_refusals(self):
        two_addresses(self, A_IP, None)                                        # one address
        self.w.leg(2)
        for dry in (True, False):
            with self.assertRaises(ConflictError) as cm:
                mip_spread.spread_exits(self.w.runner, dry=dry, yes=True)
            self.assertEqual(cm.exception.data["reason"], "spread_one")
            self.assertIn("Сбросить выбор", str(cm.exception))
        two_addresses(self)
        self.w.leg(2)
        mip.set_out(self.w.runner, B_IP, False)                                # B not «для выхода»: one again
        with self.assertRaises(ConflictError) as cm:
            mip_spread.spread_exits(self.w.runner, yes=True)
        self.assertEqual(cm.exception.data["reason"], "spread_one")
        self.assertEqual(mip.src_map(state.load()), {})
        for dry in (True, False):
            with self.assertRaises(policy_ops.Refused):                        # another window wrote: 409 first
                mip_spread.spread_exits(self.w.runner, dry=dry, yes=True, want_ver="0" * 12)
        mip.set_out(self.w.runner, B_IP, True)
        with self.assertRaises(policy_ops.Refused):
            mip_spread.spread_exits(self.w.runner, yes=True, want_ver="0" * 12)
        self.assertEqual(mip.src_map(state.load()), {})

    def test_three_exits_and_two_legs_to_one_server(self):
        """The pool VLESS_A, the leg kawg2, VLESS_B, the leg kawg4 to the same server as kawg2 (E:P of awg_conf)."""
        awg2, awg4 = awg_line(2), awg_line(4, name="Rotterdam")
        write_pool([VLESS_A, awg2, VLESS_B, awg4])
        a4 = exits.exit_id(awg4)
        self.w.leg(4)
        leg_conf([e for e in exits.pool(self.w.runner) if e["id"] == a4][0]["iface"])
        self.ok("gw", "apply")
        res = mip_spread.spread_exits(self.w.runner, yes=True)
        src = mip.src_map(state.load())
        self.assertEqual(len(src), 4, res)
        self.assertEqual(src[self.aid], src[a4])                               # one E:P — one address
        self.assertEqual(sorted(by_ip(res["moves"]).values()), [2, 2])
        rules = [r for r in chain("gw_post") if "udp dport %d snat to" % LEG_EP[1] in r]
        self.assertEqual(set(rules), {"ip daddr %s udp dport %d snat to %s" % (LEG_EP + (src[a4],))})   # one address


class FixB3Test(Spread):
    """REV-3315-b3: З-1 (the words of «nothing to spread» say the reason as it is), П-1 (the way back is the panel's
    and works in every state), П-2 (no «Не трогаю»)."""

    def three_addresses(self):
        self.w.ip = FakeIp(node_ip=A_IP, extra_addrs=["%s/32" % B_IP, "%s/32" % C_IP])
        self.w.runner._handlers = [h for h in self.w.runner._handlers if h[0] != ["ip"]]
        self.w.runner.on(["ip"], self.w.ip)

    def test_z1_every_exit_without_an_address_left_says_why(self):
        """VLESS pinned to B, the leg without its config: nothing moves, yet not «у каждого выхода уже свой адрес» —
        the leg has none; the line of the reason stays. Fails before the fix: the words of «every exit has one»."""
        self.ok("gw", "addresses", "exit", self.vid, B_IP)
        os.remove(os.path.join(paths.egress_dir(), "awg", ".%s.conf" % self.leg))
        for dry in (True, False):
            with self.subTest(dry=dry):
                res = mip_spread.spread_exits(self.w.runner, dry=dry, yes=True)
                self.assertEqual(res["moves"], [])
                self.assertEqual(res["skipped"], [{"id": self.aid, "why": "no_leg"}])
                self.assertNotIn("у каждого выхода уже свой адрес", res["text"])
                self.assertNotIn("верните выходам адрес маршрута", res["text"])
                self.assertTrue(res["text"].startswith(tr("gw_mip_spread.nothing_skipped")), res["text"])
                self.assertIn("Без изменений: «", res["text"])                      # П-2
                self.assertIn(tr("gw_mip_spread.why.no_leg"), res["text"])
        self.assertEqual(mip.src_map(state.load()), {self.vid: B_IP})
        for lang in ("ru", "en"):                                              # П-2: no first person, RU and EN
            self.assertNotIn("Не трогаю", tr("gw_mip_spread.skipped", lang, lines="x"))
        self.assertEqual(tr("gw_mip_spread.skipped", "en", lines="x"), "Unchanged: x.")

    def test_z1_no_exits(self):
        """An empty pool: «выходов пока нет», not «у каждого выхода уже свой адрес». Fails before the fix."""
        write_pool([])
        res = mip_spread.spread_exits(self.w.runner, dry=True)
        self.assertEqual((res["moves"], res["skipped"]), ([], []))
        self.assertEqual(res["text"], tr("gw_mip_spread.no_exits"))

    def test_p1_the_way_back_while_the_route_address_is_not_for_exits(self):
        """The route address A «для выхода: нет»: «по маршруту системы» is refused (``not_out``) — the way back says
        to turn «Для выхода» on at A first; done so, it works. Fails before the fix: the command of SSH that refuses."""
        self.three_addresses()
        self.ok("gw", "apply")
        mip.set_out(self.w.runner, A_IP, False, yes=True)                      # the exits go to another address
        write_pool(exits.pool_lines() + [VLESS_B])                             # a new exit — without an address
        vb = exits.exit_id(VLESS_B)
        res = mip_spread.spread_exits(self.w.runner, yes=True)
        self.assertEqual([m["id"] for m in res["moves"]], [vb])
        back = tr("gw_mip_spread.back_out", ip=A_IP)
        self.assertIn(back, res["text"])
        self.assertNotIn(BACK + ".", res["text"])
        again = mip_spread.spread_exits(self.w.runner, dry=True)
        self.assertIn("у каждого выхода уже свой адрес", again["text"])
        self.assertIn(back, again["text"])
        with self.assertRaises(ConflictError) as cm:                           # what the words say: refused…
            mip.set_src(self.w.runner, [vb], "main")
        self.assertEqual(cm.exception.data["reason"], "not_out")
        mip.set_out(self.w.runner, A_IP, True)                                 # …until «Для выхода» on A
        mip.set_src(self.w.runner, [vb], "main")
        self.assertNotIn(vb, mip.src_map(state.load()))
        for lang in ("ru", "en"):
            self.assertNotIn("kaskadctl", tr("gw_mip_spread.back", lang) + tr("gw_mip_spread.back_out", lang, ip="x"))


class PanelSpreadTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        two_addresses(self)
        self.w.leg(2)
        leg_conf([e for e in exits.pool(self.w.runner) if e.get("kind") == "awg"][0]["iface"])
        self.ok("GET", "/api/gw/addresses")

    def test_the_route(self):
        path = "/api/gw/addresses/spread"
        ver = self.ok("GET", "/api/gw/addresses")["ver"]
        self.assertEqual(self.err(400, "POST", path, {})["code"], "ver_required")
        self.assertEqual(self.err(409, "POST", path, {"ver": "0" * 12})["code"], "object_changed")
        self.assertEqual(self.err(400, "POST", path, {"ver": ver, "x": 1})["code"], "field")
        self.err(400, "POST", path, {"ver": ver, "yes": "yes"})
        dry = self.ok("POST", path, {"dry": True})                             # the dry run: no ver needed
        self.assertEqual((dry["dry"], len(dry["moves"])), (True, 2))
        self.assertEqual(self.ok("POST", path + "?dry=1", {})["moves"], dry["moves"])
        self.assertEqual(mip.src_map(state.load()), {})
        doc = self.err(409, "POST", path, {"ver": ver})
        self.assertEqual(doc["need"] if "need" in doc else doc.get("data", {}).get("need"), "confirm", doc)
        self.assertEqual(mip.src_map(state.load()), {})
        got = self.ok("POST", path, {"ver": ver, "yes": True})
        self.assertEqual(got["moves"], dry["moves"])
        self.assertEqual(mip.src_map(state.load()), dict((m["id"], m["src"]) for m in got["moves"]))
        self.assertEqual(got["ver"], self.ok("GET", "/api/gw/addresses")["ver"])
        self.assertNotEqual(got["ver"], ver)

    def test_route_out_in_the_api(self):
        """REV-3315-b3 З-2: ``GET /api/gw/addresses`` gives ``mip.route_out`` as ``kaskadctl gw addresses`` does — the
        route address not «для выхода» while an exit without an address of its own goes from it. Fails before the
        fix: always None (no pool)."""
        self.assertIsNone(self.ok("GET", "/api/gw/addresses")["mip"]["route_out"])
        mip.set_out(self.w.runner, A_IP, False, yes=True)                      # the exits there go to B
        self.assertIsNone(self.ok("GET", "/api/gw/addresses")["mip"]["route_out"])
        write_pool(exits.pool_lines() + [VLESS_B])                             # a new exit — from the route address
        ro = self.ok("GET", "/api/gw/addresses")["mip"]["route_out"]
        self.assertEqual(ro, {"ip": A_IP, "ids": [exits.exit_id(VLESS_B)]})
        with mock.patch.object(exits, "pool", side_effect=KaskadError("the parser is broken")):
            doc = self.ok("GET", "/api/gw/addresses")                          # the page still opens
        self.assertIsNone(doc["mip"]["route_out"])


# ── the rollback: the trees of 3.3.7.13 and 3.3.7.14 read what a spread writes ──

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REV13, REV14 = "bc541e29", "fc5ef683"
SIDE = "tests/r15_spread_side.py"
_TMP = {}


def tearDownModule():
    if _TMP.get("dir"):
        shutil.rmtree(_TMP["dir"], ignore_errors=True)


def _tmp():
    if not _TMP.get("dir"):
        _TMP["dir"] = tempfile.mkdtemp(prefix="k3-r15-spread-")
    return _TMP["dir"]


def tree(rev):
    """lib/, tests/ and install.sh of ``rev`` with the side copied in. SkipTest without git or the commit."""
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
    arc = subprocess.run(["git", "-C", REPO, "archive", rev, "lib", "tests", "install.sh"], capture_output=True,
                         timeout=120)
    if arc.returncode != 0:
        raise unittest.SkipTest("git archive %s: %s" % (rev, arc.stderr[-300:]))
    subprocess.run(["tar", "-x", "-C", d + ".part"], input=arc.stdout, check=True, timeout=120)
    shutil.copyfile(os.path.join(REPO, SIDE), os.path.join(d + ".part", SIDE))
    os.rename(d + ".part", d)
    return d


def side(where, out, src=None):
    io = os.path.join(_tmp(), "io")
    os.makedirs(io, exist_ok=True)
    env = dict(os.environ)
    env.pop("KASKAD_ROOT", None)
    if where != REPO:
        env.pop("K3_TR_STRICT_LOG", None)          # the strictness of the old texts is not under test here
    env.update(PYTHONPATH=os.pathsep.join([os.path.join(where, "lib"), where]), R15S_IO=io, R15S_OUT=out,
               R15S_SRC=json.dumps(src or {}), KASKAD_LANG="ru")
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.r15_spread_side"], cwd=where, env=env,
                       capture_output=True, text=True, timeout=900)
    ran = re.search(r"^Ran (\d+) test", p.stderr, re.M)
    if p.returncode != 0 or not ran or "skipped" in p.stderr.splitlines()[-1]:
        raise AssertionError("side %s in %s:\n%s" % (out, where, p.stderr[-6000:]))
    with open(os.path.join(io, out + ".json"), encoding="utf-8") as f:
        return json.load(f)


class RollbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.new = side(REPO, "new")
        cls.old = dict((rev, side(tree(rev), rev, cls.new["src"])) for rev in (REV13, REV14))

    def test_the_old_versions_draw_what_a_spread_wrote(self):
        new = self.new
        self.assertEqual((new["apply"], new["validate"]), (0, ""))
        self.assertEqual(len(new["src"]), 2)
        self.assertEqual(sorted(set(new["src"].values())), sorted([A_IP, B_IP]))
        self.assertTrue([r for r in new["snat"] if "udp dport" in r])
        self.assertTrue(new["send"])
        for rev, d in self.old.items():
            with self.subTest(rev):
                self.assertEqual((d["apply"], d["validate"]), (0, ""))
                self.assertEqual(d["src"], new["src"])                         # read, kept
                self.assertEqual(d["snat"], new["snat"])                       # the SNAT of the leg as here
                self.assertEqual(d["send"], new["send"])                       # sendThrough of the xray exit as here


if __name__ == "__main__":
    unittest.main()
