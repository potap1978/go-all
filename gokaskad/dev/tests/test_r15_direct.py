"""3.3.7.15 F12а — «Напрямую» и «Для выхода» (k3/handoff/SPEC-R1-3315-2026-10-30.md §3 F12; PLAN-multi-ip-roles §2.3–2.4,
§6.2, §6.5 п. 3, 5, 7).

What is checked (the «Тесты» of F12а; the rollback on the trees of 3.3.7.13/14 — tests/test_r15_roles_rollback.py):
* every choice null — gw_post and xray as without the keys (no SNAT of the clients, no ``sendThrough`` of «direct»);
* ``direct = B`` — one SNAT rule of the clients in gw_post before masquerade and ``sendThrough: B`` of «direct» (Н4
  aligned); B gone — neither, said in the journal; back — again;
* the refusals: B with another way out (``route_ok``), not «для выхода», not here, an IPv6; ``src`` outside ``out``;
  «напрямую» null while the address of the route is not «для выхода»;
* the address of the route «для выхода: нет» with ``direct = null`` — without ``yes`` the plan (409 confirm), with
  ``yes`` ``direct = B`` and ``src = B`` for the exits without an address (the leg forgotten by conntrack); no other
  address «для выхода» — refused; the last one — refused;
* «Сбросить выбор» — ``out``, ``direct``, ``give`` gone, ``in`` stays; the page codes ``route_src``, ``direct``, the
  flag ``out``; the panel (``ver``, the body, 409 confirm and ``yes``); the journal transitions (old 3-tuples too)."""

import json
import os
import unittest
from unittest import mock

from kaskad import errors, state, util
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import addresses as A
from kaskad.gw import mip, policy_ops, xray
from tests.panel.panelcase import PanelCase
from tests.test_mip_s34 import A_IP, B_IP, LEG_EP, Node, chain, two_addresses

DIRECT = 'ip saddr @gw_nets oifname "eth0" ip daddr != @gw_nets snat to %s'


def outbounds():
    cfg = json.load(open(xray.config_path(), encoding="utf-8"))
    return dict((o.get("tag"), o) for o in cfg["outbounds"])


class Roles(Node):
    def addr(self):
        return state.load()["gateway"].get("addresses") or {}

    def gap(self):
        """The last restart of xray by the collector — longer than RESTART_GAP_S ago."""
        if os.path.exists(xray._restart_stamp()):
            os.utime(xray._restart_stamp(), (1, 1))

    def said(self):
        got = []
        real = util.log_tr
        p = mock.patch.object(util, "log_tr", lambda key, **kw: got.append(key) or real(key, **kw))
        p.start()
        self.addCleanup(p.stop)
        return got


class NullTest(Roles):
    def test_every_choice_null_draws_nothing(self):
        post, direct = chain("gw_post"), outbounds()["direct"]
        self.assertFalse([r for r in post if "ip saddr @gw_nets oifname" in r and "snat to" in r])
        self.assertNotIn("sendThrough", direct)
        mip.set_direct(self.w.runner, B_IP)
        mip.set_direct(self.w.runner, None)
        self.assertEqual((chain("gw_post"), outbounds()["direct"]), (post, direct))      # byte for byte as before
        self.assertNotIn("addresses", state.load()["gateway"])
        # the plan itself key for key as 3.3.7.14 (net.plan carries it: tests/test_s1_t1_exit_kind pins it)
        self.assertEqual(sorted(mip.plan(state.load())), ["gone_src", "in", "legs", "snat", "xsrc"])


class DirectTest(Roles):
    def test_direct_b_aligns_the_kernel_and_xray(self):
        res = mip.set_direct(self.w.runner, B_IP)
        self.assertEqual((res["direct"], self.addr()), (B_IP, {"direct": B_IP}))
        post = chain("gw_post")
        mine = [r for r in post if r == DIRECT % B_IP]
        self.assertEqual(len(mine), 1, post)
        self.assertLess(post.index(mine[0]), [i for i, r in enumerate(post) if r.endswith("masquerade")][0])
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)
        self.assertIn("собственный трафик сервера", res["text"].lower())
        doc = self.run_json("gw", "addresses")[1]["mip"]
        self.assertEqual(doc["direct"], {"ip": B_IP, "on": True, "why": None})
        self.assertIsNone(doc["h4"])
        self.assertTrue([x for x in doc["src_lines"] if "«Напрямую» сайты видят %s" % B_IP in x], doc["src_lines"])
        page = A.page(state.load(), A.collect(self.w.runner, net=False))
        codes = dict((r["ip"], r["codes"]) for r in page["v4"])
        self.assertIn("direct", codes[B_IP])
        self.assertNotIn("route_src", codes[A_IP])                             # sites see B now

    def test_direct_gone_and_back(self):
        mip.set_direct(self.w.runner, B_IP)
        said = self.said()
        self.ok("gw", "collect", "--once")
        two_addresses(self, A_IP, None)                                        # B gone at the hoster
        self.w.leg(2)
        self.gap()                                  # «direct» is the base of xray: a restart, at most once a minute
        self.ok("gw", "collect", "--once")
        self.assertNotIn(DIRECT % B_IP, chain("gw_post"))
        self.assertNotIn("sendThrough", outbounds()["direct"])
        self.assertIn("gw_mip.log.direct_gone", said)
        st = mip.status(state.load())
        self.assertEqual(st["direct"], {"ip": B_IP, "on": False, "why": "gone"})
        self.assertTrue([x for x in mip.src_lines(st) if "сейчас не действует" in x])
        two_addresses(self)                                                    # back
        self.w.leg(2)
        self.gap()
        self.ok("gw", "collect", "--once")
        self.assertIn(DIRECT % B_IP, chain("gw_post"))
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)
        self.assertIn("gw_mip.log.direct_back", said)

    def test_the_refusals(self):
        with mock.patch.object(mip, "route_ok", lambda v, loc: loc != B_IP):
            with self.assertRaises(ConflictError) as cm:
                mip.set_direct(self.w.runner, B_IP)
            self.assertEqual(cm.exception.data["reason"], "route_other")
        for bad in ("2001:db8:10::5", "[2001:db8::1]"):
            with self.assertRaises(KaskadError) as cm:
                mip.set_direct(self.w.runner, bad)
            self.assertEqual(cm.exception.code, errors.USAGE)
            self.assertIn("только IPv4", str(cm.exception))
            with self.assertRaises(KaskadError):
                mip.set_out(self.w.runner, bad, False)
        with self.assertRaises(ConflictError) as cm:
            mip.set_direct(self.w.runner, "85.143.0.99")
        self.assertEqual(cm.exception.data["reason"], "address_not_here")
        self.assertIn("Сбросить выбор", str(cm.exception))
        self.assertNotIn("addresses", state.load()["gateway"])
        mip.set_out(self.w.runner, B_IP, False)                                # B is not «для выхода» now
        self.assertEqual(self.addr(), {"out": {"*": [A_IP]}})
        for fn in (lambda: mip.set_direct(self.w.runner, B_IP), lambda: mip.set_src(self.w.runner, [self.vid], B_IP)):
            with self.assertRaises(ConflictError) as cm:
                fn()
            self.assertEqual(cm.exception.data["reason"], "not_out")
        self.assertEqual(mip.set_src(self.w.runner, [self.vid], A_IP)["src"], A_IP)     # «для выхода»: yes

    def test_direct_null_needs_the_route_address_for_exits(self):
        mip.set_out(self.w.runner, A_IP, False, yes=True)                      # direct and the exits go to B
        self.assertEqual(self.addr()["direct"], B_IP)
        with self.assertRaises(ConflictError) as cm:
            mip.set_direct(self.w.runner, None)
        self.assertEqual(cm.exception.data["reason"], "direct_needed")
        self.assertEqual(self.addr()["direct"], B_IP)


class OutTest(Roles):
    def test_the_route_address_off_moves_direct_and_the_exits(self):
        self.w.conntrack.legs.add((LEG_EP[0], str(LEG_EP[1])))
        post0 = chain("gw_post")
        with self.assertRaises(ConflictError) as cm:
            mip.set_out(self.w.runner, A_IP, False)
        e = cm.exception
        self.assertEqual((e.data["need"], len(e.data["consequences"])), ("confirm", 3), e.data)
        self.assertIn("сайты будут видеть %s" % B_IP, e.data["consequences"][0])
        self.assertIn("выходы", e.data["consequences"][1])
        self.assertIn("собственный трафик сервера", e.data["consequences"][2])
        self.assertNotIn("addresses", state.load()["gateway"])
        self.assertEqual(chain("gw_post"), post0)
        res = mip.set_out(self.w.runner, A_IP, False, yes=True)
        self.assertEqual((res["out"], res["direct"]), ([B_IP], B_IP))
        self.assertEqual(sorted(x["id"] for x in res["moved"]), sorted([self.vid, self.aid]))
        g = state.load()["gateway"]
        self.assertEqual((g["exits"][self.vid]["src"], g["exits"][self.aid]["src"]), (B_IP, B_IP))
        post = chain("gw_post")
        self.assertIn(DIRECT % B_IP, post)
        self.assertIn("ip daddr %s udp dport %d snat to %s" % (LEG_EP + (B_IP,)), post)
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)
        self.assertEqual(res["flushed"], 1)                                    # the leg forgotten by conntrack
        self.assertEqual(len(res["warnings"]) >= 3, True)
        page = A.page(state.load(), A.collect(self.w.runner, net=False))
        self.assertEqual(dict((r["ip"], r["out"]) for r in page["v4"]), {A_IP: False, B_IP: True})

    def test_nowhere_to_move_and_the_last_one(self):
        with mock.patch.object(mip, "route_ok", lambda v, loc: loc != B_IP):
            with self.assertRaises(ConflictError) as cm:
                mip.set_out(self.w.runner, A_IP, False, yes=True)
            self.assertEqual(cm.exception.data["reason"], "out_no_other")
        self.assertNotIn("addresses", state.load()["gateway"])
        mip.set_out(self.w.runner, B_IP, False)                                # nothing depends on B: no question
        with self.assertRaises(ConflictError) as cm:
            mip.set_out(self.w.runner, A_IP, False, yes=True)
        self.assertEqual(cm.exception.data["reason"], "out_last")
        mip.set_out(self.w.runner, B_IP, True)
        self.assertEqual(self.addr(), {"out": {"*": [B_IP, A_IP]}})
        with self.assertRaises(ConflictError):
            mip.set_out(self.w.runner, "85.143.0.99", True)                    # not here

    def test_another_address_off_without_dependents(self):
        res = mip.set_out(self.w.runner, B_IP, False)
        self.assertEqual((res["out"], res["direct"], res["moved"]), ([A_IP], None, []))
        self.assertEqual(mip.set_out(self.w.runner, B_IP, False)["out"], [A_IP])     # again: nothing
        mip.set_src(self.w.runner, [self.aid], A_IP)
        mip.set_out(self.w.runner, B_IP, True)
        mip.set_src(self.w.runner, [self.aid], B_IP)
        with self.assertRaises(ConflictError) as cm:                           # an exit goes from B
            mip.set_out(self.w.runner, B_IP, False)
        self.assertEqual(len(cm.exception.data["consequences"]), 2)            # the exit and the server's own traffic


class ResetTest(Roles):
    def test_reset_takes_the_roles_only(self):
        self.ok("gw", "addresses", "in", A_IP, B_IP)
        mip.set_direct(self.w.runner, B_IP)
        mip.set_out(self.w.runner, B_IP, True)
        with state.locked():
            st = state.load()
            st["gateway"]["addresses"]["give"] = {"items": [{"ip": "2001:db8:10::5", "n": 1}]}
            st["gateway"]["addresses"]["out"] = {"*": [A_IP, B_IP]}
            state.save(st)
        res = mip.reset_roles(self.w.runner)
        self.assertIn("сброшен", res["text"])
        self.assertEqual(self.addr(), {"in": {"*": [A_IP, B_IP]}})
        self.assertNotIn(DIRECT % B_IP, chain("gw_post"))
        mip.reset_roles(self.w.runner)                                         # again: never refused
        with self.assertRaises(policy_ops.Refused):                          # another window wrote: 409
            mip.reset_roles(self.w.runner, want_ver="0" * 12)


class TellTest(unittest.TestCase):
    def test_direct_gone_and_back_and_old_tuples(self):
        said = []

        def log(key, **kw):
            said.append(key)
        st = {"gateway": {"addresses": {"direct": B_IP}}}
        ok = ((), False, (), "")
        mip.tell(ok, ((), False, (), B_IP), log=log, st=st)
        self.assertEqual(said, ["gw_mip.log.direct_gone"])
        del said[:]
        mip.tell(((), False, (), B_IP), ok, log=log, st=st)
        self.assertEqual(said, ["gw_mip.log.direct_back"])
        del said[:]
        mip.tell(((), False, (), B_IP), ok, log=log, st={"gateway": {}})        # taken off by a person: not «back»
        self.assertEqual(said, [])
        self.assertEqual(mip.tell(((), False, ()), ok, log=log, st=st), [])   # a tuple of 3.3.7.14 shape
        self.assertEqual(mip.gone_state({"gone_direct": B_IP})[3], B_IP)

    def test_soft_reading(self):
        for v in (None, "mix", 5, "fe80::1", "10.0.0.1"):
            self.assertIsNone(mip.direct_of({"gateway": {"addresses": {"direct": v}}}), v)
        for v in (None, [], {"*": []}, {"*": ["x", 5]}, {"*": ["10.0.0.1"]}):
            self.assertIsNone(mip.out_list({"gateway": {"addresses": {"out": v}}}), v)
        self.assertEqual(mip.out_list({"gateway": {"addresses": {"out": {"*": [B_IP, "x", B_IP]}}}}), [B_IP])


class BackupTest(unittest.TestCase):
    def test_out_and_direct_of_a_copy_stay_on_its_machine(self):
        from kaskad import backup
        g_file = {"addresses": {"out": {"*": [B_IP]}, "direct": B_IP}}
        other = json.loads(json.dumps(g_file))
        backup._addresses_of_this_machine(other, {"gateway": {}}, False)
        self.assertNotIn("addresses", other)
        same = json.loads(json.dumps(g_file))
        backup._addresses_of_this_machine(same, {"gateway": {}}, True)
        self.assertEqual(same, g_file)


class PanelRolesTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        two_addresses(self)
        self.w.leg(2)
        self.ok("GET", "/api/gw/addresses")

    def test_direct_out_reset(self):
        ver = self.ok("GET", "/api/gw/addresses")["ver"]
        for path, body, method in (("/api/gw/addresses/direct", {"ip": B_IP}, "PUT"),
                                   ("/api/gw/addresses/out", {"ip": B_IP, "on": False}, "PUT"),
                                   ("/api/gw/addresses/reset", {}, "POST")):
            self.assertEqual(self.err(400, method, path, body)["code"], "ver_required", path)
            self.assertEqual(self.err(409, method, path, dict(body, ver="0" * 12))["code"], "object_changed", path)
            self.assertEqual(self.err(400, method, path, dict(body, ver=ver, x=1))["code"], "field", path)
        for body in ({"ver": ver}, {"ip": 5, "ver": ver}):
            self.err(400, "PUT", "/api/gw/addresses/direct", body)
        self.err(400, "PUT", "/api/gw/addresses/out", {"ip": B_IP, "on": "no", "ver": ver})
        self.assertNotIn("addresses", state.load()["gateway"])
        got = self.ok("PUT", "/api/gw/addresses/direct", {"ip": B_IP, "ver": ver})
        self.assertEqual(got["direct"], B_IP)
        doc = self.err(409, "PUT", "/api/gw/addresses/out", {"ip": B_IP, "on": False, "ver": got["ver"]})
        self.assertEqual(doc["need"] if "need" in doc else doc.get("data", {}).get("need"), "confirm", doc)
        got = self.ok("PUT", "/api/gw/addresses/out", {"ip": B_IP, "on": False, "yes": True, "ver": got["ver"]})
        self.assertEqual((got["out"], got["direct"]), ([A_IP], A_IP))
        got = self.ok("POST", "/api/gw/addresses/reset", {"ver": got["ver"]})
        self.assertNotIn("addresses", state.load()["gateway"])
        self.assertEqual(got["ver"], self.ok("GET", "/api/gw/addresses")["ver"])


class ReviewB2Test(Roles):
    """REV-3315-b2: Н-1 («по маршруту» при адресе маршрута не «для выхода»), П-2, П-3."""

    def test_main_refused_while_the_route_address_is_not_for_exits(self):
        mip.set_out(self.w.runner, A_IP, False, yes=True)                      # «напрямую» and the exits -> B
        with self.assertRaises(ConflictError) as cm:
            mip.set_src(self.w.runner, [self.vid], "main")
        self.assertEqual(cm.exception.data["reason"], "not_out")
        self.assertIn("Адрес маршрута %s" % A_IP, str(cm.exception))
        self.assertIn("Сбросить выбор", str(cm.exception))
        self.assertEqual(mip.exit_src(state.load(), self.vid), B_IP)            # stays on B
        rc, doc, _ = self.run_json("gw", "addresses", "exit", self.aid, "main")  # the CLI says the same
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "not_out"))
        mip.set_out(self.w.runner, A_IP, True)                                 # «для выхода» again: allowed
        self.assertIsNone(mip.set_src(self.w.runner, [self.vid], "main")["src"])

    def test_the_status_says_the_exits_left_on_the_route_address(self):
        with state.locked():
            st = state.load()
            st["gateway"]["addresses"] = {"out": {"*": [B_IP]}}                # A not «для выхода», no exit has src
            state.save(st)

        def loose():
            got = self.run_json("gw", "addresses")[1]["mip"]["src_lines"]
            return [x for x in got if "без своего адреса подключаются с него" in x]
        self.assertEqual(len(loose()), 1)
        self.assertIn(A_IP, loose()[0])
        self.assertIn("Вена", loose()[0])
        mip.set_src(self.w.runner, [self.vid, self.aid], B_IP)
        self.assertEqual(loose(), [])

    def test_off_on_an_address_not_here(self):
        with self.assertRaises(ConflictError) as cm:
            mip.set_out(self.w.runner, "85.143.0.99", False)
        self.assertEqual(cm.exception.data["reason"], "address_not_here")
        self.assertNotIn("addresses", state.load()["gateway"])
        mip.set_out(self.w.runner, B_IP, False)
        mip.set_out(self.w.runner, B_IP, True)
        self.assertEqual(self.addr(), {"out": {"*": [B_IP, A_IP]}})
        two_addresses(self, A_IP, None)                                        # B gone: «нет» on it still allowed
        self.w.leg(2)
        self.assertEqual(mip.set_out(self.w.runner, B_IP, False)["out"], [A_IP])

    def test_the_text_says_what_moved(self):
        mip.set_direct(self.w.runner, B_IP)
        res = mip.set_out(self.w.runner, A_IP, False, yes=True)                # only the exits move (direct is B)
        self.assertIn("Выходы, что шли с него, теперь — с %s" % B_IP, res["text"])
        self.assertNotIn("«Напрямую»", res["text"])

    def test_the_text_says_only_direct_moved(self):
        mip.set_src(self.w.runner, [self.vid, self.aid], B_IP)
        res = mip.set_out(self.w.runner, A_IP, False, yes=True)                # only «напрямую» moves
        self.assertEqual(res["moved"], [])
        self.assertIn("«Напрямую» теперь — с %s" % B_IP, res["text"])
        self.assertNotIn("выходы", res["text"].lower().split("готово")[-1].split(".", 1)[-1])


if __name__ == "__main__":
    unittest.main()
