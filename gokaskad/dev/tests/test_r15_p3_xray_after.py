"""3.3.7.15 П3 (stand/logs/T-3315.md п. 1.7): a write of «Адреса сервера» answers BEFORE xray restarts.

The panel on the domain on 443 sits behind xray (VLESS TLS in front of the site, its fallback to nginx):
``PUT /api/gw/addresses/direct`` restarted xray inside the request and the answer died with it — «Empty reply from
server» 4 times of 4, the change applied. Now (``xray.after_answer``, ``Request.after``):

* the answer is written first, the restart comes right after it — ``direct``, ``out``, ``reset`` (``give`` — the same
  wrapper); ``spread``/``exit`` (the outbounds of the pool go live through the API; an AmneziaWG leg — SNAT) and
  ``in`` (gw_pre only) restart nothing — and owe nothing;
* the deferred render is not noted as applied and the collector holds its restart while the panel owes it (≤
  ``AFTER_HOLD_S``), so the collector never cuts the answer either; a dead panel — the collector after the hold;
* a config ``xray -test`` refuses is not deferred: the usual path, the refusal in the answer as before;
* the call after the answer: a later write's token — nothing (its own call does it); a failure — a line in the
  journal, the mark goes, the collector tells the refusal in the feed and applies once it is taken;
* the CLI (no scope) restarts inside the write as before; ``Request.after`` runs when the handler failed too.

REV-3315-p3 (the answers come over the listener of nginx, ``proxy`` — the domain on 443 sits there):
* З-1: the answer says ``xray_after``; the listener ``local`` (the SSH tunnel) restarts inside the request, its
  failure in the answer; a failure of the call after the answer — a warning of ``GET /api/gw/addresses`` until xray
  runs the state again;
* З-2: the collector asks the storm under the xray lock — a mark set by the write it waited for holds it back;
* З-4: a config refused by the test before the deferral is not tested a second time;
* З-7: a mark «from the future» (the clock went back) holds nothing back."""

import json
import os
import threading
import time
from unittest import mock

from kaskad import paths, state, util
from kaskad.errors import KaskadError
from kaskad.gw import exits, live, mip, ops, xray
from kaskad.gw import collect
from kaskad.panel import auth, routes, server
from tests.panel.test_panel_proxy import ProxyCase
from tests.test_mip_s34 import A_IP, B_IP, two_addresses
from tests.test_r15_sub6 import X6, Sub6Case


def outbounds():
    with open(xray.config_path(), encoding="utf-8") as f:
        cfg = json.load(f)
    return dict((o.get("tag"), o) for o in cfg["outbounds"])


PW = "правильный-пароль"


class Base(ProxyCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        two_addresses(self)
        self.w.leg(2)
        self.ok("GET", "/api/gw/addresses")
        self.collect()
        auth.set_password(PW)
        self.proxy_on()
        self.tok = self.cookie_of(self.pcall("POST", "/api/login", {"password": PW}))
        self.sent = []                                  # (path, restarts of xray at the moment the answer was written)
        self.done = []                                  # (path, calls owed to the answer) — the request is over
        real, real_dispatch, case = server.Handler._send, server.Handler._dispatch, self

        def send(h, resp, head=False):
            real(h, resp, head=head)
            if h.command in ("PUT", "POST"):
                case.sent.append((h.path, case.restarts()))

        def dispatch(h, method):
            real_dispatch(h, method)
            if method in ("PUT", "POST"):
                case.done.append((h.path, len(h._after or [])))
        for p in (mock.patch.object(server.Handler, "_send", send), mock.patch.object(server, "AFTER_S", 0.05),
                  mock.patch.object(server.Handler, "_dispatch", dispatch)):
            p.start()
            self.addCleanup(p.stop)

    def restarts(self):
        return self.w.sysd.restarts.get(xray.UNIT, 0)

    def pok(self, method, path, body):
        """A write over the listener of nginx (the domain) with the admin's session."""
        r = self.pcall(method, path, body, cookie=self.tok)
        self.assertEqual(r.status, 200, "%s %s -> %r" % (method, path, r))
        return r.json()

    def perr(self, status, method, path, body):
        r = self.pcall(method, path, body, cookie=self.tok)
        self.assertEqual(r.status, status, "%s %s -> %r" % (method, path, r))
        return r.json()

    def ver(self):
        return self.ok("GET", "/api/gw/addresses")["ver"]

    def no_gap(self):
        """The last restart of xray longer than RESTART_GAP_S ago: the storm does not hold the collector."""
        if os.path.exists(xray._restart_stamp()):
            os.utime(xray._restart_stamp(), (1, 1))

    def owed(self, path, n):
        """The request of ``path`` (the last one) is over and owed ``n`` calls after its answer."""
        self.assertTrue(self.wait(lambda: self.done and self.done[-1][0] == path), path)
        self.assertEqual(self.done[-1], (path, n))

    def change(self, method, path, body, restart=True):
        """One write through the panel: its answer left before xray restarted; the restart came right after it (or
        none at all, ``restart`` False: nothing owed to the answer); the mark of the owed restart is gone either way."""
        before = self.restarts()
        got = self.pok(method, path, dict(body, ver=self.ver()))
        self.assertEqual(self.sent[-1], (path, before), "the answer of %s was written after the restart" % path)
        self.assertIs(got["xray_after"], restart, path)                          # З-1: the page reads again
        self.owed(path, 1 if restart else 0)
        if restart:
            self.assertTrue(self.wait(lambda: self.restarts() > before), "no restart after the answer of %s" % path)
        self.assertTrue(self.wait(lambda: xray.after_token() is None), "the mark of %s stays" % path)
        self.assertEqual(self.restarts(), before + (1 if restart else 0), path)
        return got


class AnswerFirstTest(Base):
    def test_direct_out_reset_answer_before_the_restart(self):
        got = self.change("PUT", "/api/gw/addresses/direct", {"ip": B_IP})
        self.assertEqual(got["direct"], B_IP)
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)         # applied right after the answer
        self.assertEqual(got["ver"], self.ver())
        n = self.restarts()
        self.no_gap()
        self.collect()
        self.assertEqual(self.restarts(), n)                                      # noted as applied: nothing owed
        got = self.change("PUT", "/api/gw/addresses/out", {"ip": B_IP, "on": False, "yes": True})
        self.assertEqual((got["out"], got["direct"]), ([A_IP], A_IP))
        self.assertEqual(outbounds()["direct"].get("sendThrough"), A_IP)
        self.change("POST", "/api/gw/addresses/reset", {})
        self.assertNotIn("sendThrough", outbounds()["direct"])
        self.assertNotIn("addresses", state.load()["gateway"])

    def test_spread_exit_and_in_restart_nothing_and_owe_nothing(self):
        got = self.change("POST", "/api/gw/addresses/spread", {"yes": True}, restart=False)        # live, the API
        self.assertEqual([(m["id"], m["src"]) for m in got["moves"]], [(self.vid, B_IP)])
        self.assertEqual(outbounds()[exits.tag(self.vid)].get("sendThrough"), B_IP)
        self.change("PUT", "/api/gw/addresses/exit", {"id": self.vid, "ip": "main"}, restart=False)
        self.assertNotIn("sendThrough", outbounds()[exits.tag(self.vid)])
        self.change("PUT", "/api/gw/addresses/exit", {"id": self.vid, "ip": B_IP}, restart=False)
        self.assertEqual(outbounds()[exits.tag(self.vid)].get("sendThrough"), B_IP)
        self.change("PUT", "/api/gw/addresses/exit", {"id": self.aid, "ip": B_IP}, restart=False)   # SNAT of the leg
        self.change("PUT", "/api/gw/addresses/in", {"all": True}, restart=False)                   # gw_pre only

    def test_a_refusal_before_the_change_owes_nothing(self):
        self.change("PUT", "/api/gw/addresses/direct", {"ip": B_IP})
        before = self.restarts()
        doc = self.perr(409, "PUT", "/api/gw/addresses/out", {"ip": B_IP, "on": False, "ver": self.ver()})
        self.assertEqual(doc["data"]["need"], "confirm")
        self.owed("/api/gw/addresses/out", 0)
        self.assertIsNone(xray.after_token())
        self.assertEqual(self.restarts(), before)

    def test_a_refused_config_is_not_deferred_the_answer_says_it(self):
        self.w.xray.fail = "Failed to start: infra/conf: bad sendThrough\n"
        before, tests = self.restarts(), []
        real = xray._test
        with mock.patch.object(xray, "_test", lambda *a: tests.append(a[3]) or real(*a)):
            got = self.pok("PUT", "/api/gw/addresses/direct", {"ip": B_IP, "ver": self.ver()})
        self.assertTrue([w for w in got["warnings"] if "bad sendThrough" in w and "прежний конфиг" in w],
                        got["warnings"])
        self.assertEqual(len(tests), 1, "З-4: the refused text tested again")    # the deferral's test, then healing
        self.assertIs(got["xray_after"], False)
        self.owed("/api/gw/addresses/direct", 0)
        self.assertIsNone(xray.after_token())
        self.assertEqual(self.restarts(), before)
        self.assertNotIn("sendThrough", outbounds()["direct"])                    # the old config runs, as before


class CoreTest(Base):
    """The core under the scope directly (the thread of the panel's request does the same)."""

    def direct(self, ip):
        with xray.after_answer() as owed:
            mip.set_direct(self.w.runner, ip)
        return owed["token"]

    def test_deferred_not_noted_collector_held_then_the_call(self):
        before, text = self.restarts(), util.read_text(xray.config_path())
        token = self.direct(B_IP)
        self.assertRegex(token, r"^[0-9a-f]{16}$")
        self.assertEqual((self.restarts(), util.read_text(xray.config_path())), (before, text))   # nothing yet
        self.assertEqual(xray.after_token(), token)
        self.no_gap()
        self.assertFalse(xray.restart_due())                                      # the collector holds its restart
        self.collect()
        self.assertEqual((self.restarts(), util.read_text(xray.config_path())), (before, text))
        res = ops.xray_after_answer(self.w.runner, token)
        self.assertTrue(res["restarted"])
        self.assertEqual(self.restarts(), before + 1)
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)
        self.assertIsNone(xray.after_token())
        self.no_gap()
        self.collect()
        self.assertEqual(self.restarts(), before + 1)                            # noted: the collector draws nothing

    def test_a_dead_panel_the_collector_after_the_hold(self):
        before = self.restarts()
        token = self.direct(B_IP)                                                 # no call after the answer at all
        self.no_gap()
        self.collect()
        self.assertEqual(self.restarts(), before)                                 # held back
        with open(xray._after_path(), "w", encoding="utf-8") as f:                # the hold is over
            json.dump({"token": token, "at": int(time.time()) - xray.AFTER_HOLD_S - 1}, f)
        self.assertTrue(xray.restart_due())
        self.collect()
        self.assertEqual(self.restarts(), before + 1)
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)
        self.assertIsNone(ops.xray_after_answer(self.w.runner, "f" * 16))         # the mark of another — nothing

    def test_a_later_write_owns_the_restart(self):
        first = self.direct(B_IP)
        second = self.direct(A_IP)
        self.assertTrue(first and second and first != second)
        before = self.restarts()
        self.assertIsNone(ops.xray_after_answer(self.w.runner, first))           # not its turn: nothing
        self.assertEqual((self.restarts(), xray.after_token()), (before, second))
        self.assertTrue(ops.xray_after_answer(self.w.runner, second)["restarted"])
        self.assertEqual((self.restarts(), outbounds()["direct"].get("sendThrough")), (before + 1, A_IP))
        self.assertIsNone(xray.after_token())

    def test_the_failed_call_journal_feed_and_the_collector_catches_up(self):
        token = self.direct(B_IP)
        got = []
        real = util.log_tr
        p = mock.patch.object(util, "log_tr", lambda key, **kw: got.append((key, kw)) or real(key, **kw))
        p.start()
        self.addCleanup(p.stop)
        before = self.restarts()
        self.w.xray.fail = "Failed to start: boom\n"                              # xray takes it no more by then
        self.assertIsNone(ops.xray_after_answer(self.w.runner, token))
        said = [kw["err"] for k, kw in got if k == "gw_ops.log.xray_after_failed"]
        self.assertEqual(len(said), 1, got)
        self.assertIn("boom", said[0])
        self.assertIsNone(xray.after_token())                                     # the collector is free again
        self.assertEqual(self.restarts(), before)
        self.no_gap()
        self.collect()                                                            # not noted: drawn again
        doc, _ = live.read()
        self.assertEqual([e["kind"] for e in doc["events"] if e["kind"] == "xray_rejected"], ["xray_rejected"])
        self.w.xray.fail = None
        with open(paths.xray_rejected(), encoding="utf-8") as f:
            rej = json.load(f)
        rej["at"] = 1                                                             # REJECT_RETRY_S passed
        with open(paths.xray_rejected(), "w", encoding="utf-8") as f:
            json.dump(rej, f)
        self.no_gap()
        self.collect()
        self.assertEqual(self.restarts(), before + 1)
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)

    def test_the_state_busy_the_mark_stays_until_the_hold_ends(self):
        token = self.direct(B_IP)
        got = []
        real = util.log_tr
        with mock.patch.object(util, "log_tr", lambda key, **kw: got.append(key) or real(key, **kw)), \
                mock.patch.object(ops, "AFTER_LOCK_S", 0.2), state.locked():
            box = {}
            t = threading.Thread(target=lambda: box.setdefault("res", ops.xray_after_answer(self.w.runner, token)))
            t.start()
            t.join(5)
        self.assertIsNone(box["res"])
        self.assertIn("gw_ops.log.xray_after_failed", got)
        self.assertEqual(xray.after_token(), token)                               # not touched without the lock

    def test_the_cli_restarts_inside_the_write_as_before(self):
        before = self.restarts()
        mip.set_direct(self.w.runner, B_IP)
        self.assertEqual(self.restarts(), before + 1)
        self.assertIsNone(xray.after_token())


class ServerAfterTest(Base):
    def test_after_runs_when_the_handler_failed_and_its_error_goes_to_the_journal(self):
        ran, said = [], []

        def fail():
            ran.append(1)
            raise KaskadError("x")

        def boom(req):
            req.after.append(fail)
            req.after.append(lambda: ran.append(2))
            raise routes.HttpError(409, "нет")
        r = routes.Route("PUT", r"/api/test/after", boom, "admin", True)
        with mock.patch.object(routes, "find", lambda m, p: (r, r.rx.match(p)) if p == "/api/test/after"
                               else (None, None)), \
                mock.patch.object(server, "log_t", lambda key, **kw: said.append(key)):
            self.err(409, "PUT", "/api/test/after", {})
            self.assertTrue(self.wait(lambda: len(ran) == 2))
        self.assertEqual(ran, [1, 2])
        self.assertEqual(said, ["journal.srv.handler_error"])

    def test_the_stop_of_the_panel_lets_the_owed_call_finish(self):
        ran = []

        gate = threading.Event()

        def slow(req):
            req.after.append(lambda: gate.wait(5) and ran.append(1))
            return routes.json_response({"ok": True})
        r = routes.Route("PUT", r"/api/test/after", slow, "admin", True)
        with mock.patch.object(routes, "find", lambda m, p: (r, r.rx.match(p)) if p == "/api/test/after"
                               else (None, None)):
            self.ok("PUT", "/api/test/after", {})
            self.assertEqual(ran, [])                                             # the answer came first
            threading.Timer(0.2, gate.set).start()
            self.panel.stop()                                                     # waits for the owed call
        self.assertEqual(ran, [1])


class GiveRealityTest(Sub6Case):
    """F13 ``give``: VLESS Reality starts listening on IPv6 — a restart of xray, owed to the answer as ``direct``."""

    def test_give6_deferred_then_listen_on_ipv6_after_the_answer(self):
        before = self.w.sysd.restarts.get(xray.UNIT, 0)
        with xray.after_answer() as owed:
            mip.set_give6(self.w.runner, X6, yes=True)
        self.assertTrue(owed["token"])
        self.assertEqual((self.w.sysd.restarts.get(xray.UNIT, 0), self.reality()["listen"]), (before, "0.0.0.0"))
        self.assertTrue(ops.xray_after_answer(self.w.runner, owed["token"])["restarted"])
        self.assertEqual((self.w.sysd.restarts.get(xray.UNIT, 0), self.reality()["listen"]), (before + 1, "::"))
        with xray.after_answer() as owed:                                         # «Сбросить выбор» — back, the same way
            mip.reset_roles(self.w.runner, yes=True)
        self.assertTrue(owed["token"])
        ops.xray_after_answer(self.w.runner, owed["token"])
        self.assertEqual((self.w.sysd.restarts.get(xray.UNIT, 0), self.reality()["listen"]), (before + 2, "0.0.0.0"))
        self.assertIsNone(xray.after_token())


class ReviewTest(Base):
    """REV-3315-p3 З-1, З-2, З-7."""

    def test_z1_the_tunnel_restarts_inside_the_request_its_failure_in_the_answer(self):
        self.w.sysd.fail_units[xray.UNIT] = "Job for kaskad-gw-xray.service failed.\n"
        before = self.restarts()
        got = self.ok("PUT", "/api/gw/addresses/direct", {"ip": B_IP, "ver": self.ver()})       # the SSH tunnel
        self.assertIs(got["xray_after"], False)
        self.assertTrue([w for w in got["warnings"] if "xray шлюза не запущен" in w], got["warnings"])
        self.owed("/api/gw/addresses/direct", 0)
        self.assertIsNone(xray.after_token())
        self.assertEqual(self.restarts(), before)                                  # failed: not counted
        del self.w.sysd.fail_units[xray.UNIT]
        got = self.ok("PUT", "/api/gw/addresses/direct", {"ip": None, "ver": self.ver()})
        self.assertEqual((got["xray_after"], got["warnings"]), (False, []))
        self.owed("/api/gw/addresses/direct", 0)
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])                    # up again inside the request
        self.assertNotIn("sendThrough", outbounds()["direct"])

    def test_z1_a_failed_call_after_the_answer_is_said_by_get_addresses_until_xray_runs_the_state(self):
        self.assertEqual(self.ok("GET", "/api/gw/addresses")["warnings"], [])
        self.w.sysd.fail_units[xray.UNIT] = "Job for kaskad-gw-xray.service failed.\n"
        got = self.pok("PUT", "/api/gw/addresses/direct", {"ip": B_IP, "ver": self.ver()})
        self.assertEqual((got["xray_after"], got["warnings"]), (True, []))
        self.assertTrue(self.wait(lambda: xray.after_failed() is not None))
        warns = self.ok("GET", "/api/gw/addresses")["warnings"]
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("xray не перезапустился после последнего изменения адресов", warns[0])
        self.assertIn("failed", warns[0])
        del self.w.sysd.fail_units[xray.UNIT]
        self.no_gap()
        self.collect()                                                             # the collector makes it good
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)
        self.assertIsNone(xray.after_failed())
        self.assertEqual(self.ok("GET", "/api/gw/addresses")["warnings"], [])

    def test_z2_the_collector_waiting_for_the_xray_lock_sees_the_mark_of_the_write(self):
        with xray.after_answer():
            mip.set_direct(self.w.runner, B_IP)                                    # the state wants a restart
        os.unlink(xray._after_path())                                              # … no mark when the tick starts
        self.no_gap()
        self.assertTrue(xray.restart_due())
        st = state.load()
        pool = exits.pool(self.w.runner, roles=exits.ROLES)
        dec = exits.decision(st, pool_exits=pool)
        want = {"st": st, "pool": pool, "decision": dec, "seq": xray.next_seq(),
                "fp": collect.fingerprint(st, dec, pool)}
        waiting, box = threading.Event(), {}
        real_lock = xray._lock

        def lock(wait):
            waiting.set()
            return real_lock(wait)
        held = xray._lock(0)                                                       # a write of the panel holds it
        before = self.restarts()
        with mock.patch.object(xray, "_lock", lock):
            t = threading.Thread(target=lambda: box.setdefault("res", collect._apply_xray(
                self.w.runner, want, [], lambda line: None)))
            t.start()
            self.assertTrue(waiting.wait(5))
            xray._after_owe({"token": None})                                       # … and sets its mark under it
            xray._unlock(held)
            t.join(10)
        self.assertTrue(box["res"].get("deferred"), box["res"])
        self.assertEqual(self.restarts(), before)

    def test_z2_the_mark_is_there_before_the_xray_lock_is_let_go(self):
        seen = []
        real = xray._unlock

        def unlock(f):
            seen.append(xray.after_token())
            real(f)
        with mock.patch.object(xray, "_unlock", unlock), xray.after_answer() as owed:
            mip.set_direct(self.w.runner, B_IP)
        self.assertTrue(owed["token"])
        self.assertIn(owed["token"], seen)                                         # set under the xray lock

    def test_z7_a_mark_from_the_future_holds_nothing_back(self):
        with open(xray._after_path(), "w", encoding="utf-8") as f:
            json.dump({"token": "a" * 16, "at": int(time.time()) + 3600}, f)
        self.assertFalse(xray._after_held())
        self.no_gap()
        self.assertTrue(xray.restart_due())
        with open(xray._after_path(), "w", encoding="utf-8") as f:
            json.dump({"token": "a" * 16, "at": int(time.time()) - 1}, f)
        self.assertTrue(xray._after_held())
        self.assertFalse(xray.restart_due())
