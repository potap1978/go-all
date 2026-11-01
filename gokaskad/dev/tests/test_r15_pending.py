"""3.3.7.15 F7 — the safe apply with a revert in 2 min (SPEC-R1 §3 F7; С-2, С-3, П-2, З-6): the wait armed before the
apply when the administrator is connected through the pool of the kind he changes (or by choice ``safe``), its
confirmation, the revert by the timer, the races of one lock, ``expire_due`` at the start of the collector, the events
of the feed (the bot pushes them), «Скачать мои новые настройки», the API and the catalog of MCP.

F3 (the settings of a protocol, полоса А) is not merged here: the apply of a port is a stand-in — the port written by
``ops._change`` (never ``ops.inbound_port``: after F3 it goes through ``pending.guard`` itself, REV-3315-g1 С-3) behind
``pending.guard`` with the record ``settings-last`` F3 writes, and the revert — a stand-in ``ops.inbound_settings_undo``
with the contract of F3 (``pending=rec``: the lock is the caller's; a busy old port — ``data.why = "port_busy"``). The
path of F3 itself — ``F3EndToEndTest`` (skipped until the merge, required after it)."""

import contextlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from kaskad import paths, state, util
from kaskad.errors import ConflictError, NestedLockError
from kaskad.gw import collect, live, model, ops, pending
from kaskad.i18n import tr
from tests.panel.panelcase import PanelCase

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PendCase(PanelCase):
    def setUp(self):
        super().setUp()
        self.runs = []                  # argv of systemd-run
        self.run_ok = True
        self.active = set()             # units whose timer «is-active»
        mine = [(["systemd-run"], self._systemd_run),
                (["systemctl", "is-active"], lambda argv, inp: (0, "active\n", "") if argv[2][:-6] in self.active
                 else (3, "inactive\n", "")),
                (["systemctl", "stop"], lambda argv, inp: (0, "", ""))]
        self.w.runner._handlers[:0] = mine             # before the handlers of the fake world (the first match wins)
        self.gateway()
        self.did = ops.device_add(self.w.runner, self.uid, "Ноутбук", kind="laptop", platform="windows",
                                  access=["wg"])["device"]["id"]
        self.ip = model.get_device(state.load(), self.did)["ips"]["wg"]
        self.n = 0
        self.undos = []
        self._undo = mock.patch.object(ops, "inbound_settings_undo", self.fake_undo, create=True)
        self._undo.start()

    def tearDown(self):
        self._undo.stop()
        super().tearDown()

    def _systemd_run(self, argv, inp):
        if "--on-active=120s" in argv or any(a.startswith("--on-active") for a in argv):
            self.runs.append(argv)
        return (0, "", "") if self.run_ok else (1, "", "Failed to start transient timer unit")

    # ── the stand-in of F3 ──
    def set_port(self, runner, kind, port):
        """The port written as F3's apply writes it — one ``_change``, no guard of its own (С-3)."""
        def mutate(new, sec):
            model.inbound(new, kind)["port"] = port
        return ops._change(runner, mutate)[1] or {"ok": True}

    def wg_port(self):
        return model.inbound(state.load(), "wg")["port"]

    def last(self):
        try:
            with open(pending.settings_last_path(), encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return {}

    def apply_port(self, port, who=None, safe=False, kind="wg", changed=None):
        """F3's path: the plan, then ``guard`` around the write and the record of the last apply."""
        self.n += 1
        aid = "a-%08x" % self.n
        st_old = state.load()
        old = (model.inbound(st_old, kind) or {}).get("port")
        plan = {"changed": changed if changed is not None else {"port": {"old": old, "new": port}}, "id": aid}

        def apply():
            res = self.set_port(self.w.runner, kind, port) if "port" in plan["changed"] else {"ok": True}
            doc = self.last()
            doc[kind] = {"id": aid, "at": int(time.time()), "old": {"port": old}, "new": {"port": port}, "marked": []}
            util.atomic_write(pending.settings_last_path(), json.dumps(doc), 0o600)
            return res
        return pending.guard(self.w.runner, st_old, plan, kind, who=who, safe=safe, apply=apply)

    def fake_undo(self, runner, kind, pending=None):
        self.undos.append((kind, (pending or {}).get("id")))
        rec = self.last().get(kind)
        if rec is None:
            raise ConflictError("nothing to revert")
        if getattr(self, "busy", None):
            raise ConflictError("busy", data={"why": "port_busy", "port": rec["old"]["port"]})
        if getattr(self, "refuse", None) is not None:          # a refusal of F3 given by the test (С-1, the stand)
            raise self.refuse
        self.set_port(runner, kind, rec["old"]["port"])
        doc = self.last()
        doc.pop(kind, None)
        util.atomic_write(os.path.join(paths.gw_var_dir(), "settings-last.json"), json.dumps(doc), 0o600)
        return {"ok": True}

    def admin(self):
        return {"via": "panel", "ip": self.ip}

    def wait(self):
        return pending.read()

    def events(self):
        d = paths.gw_pending_events()
        out = []
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                out.append(json.load(f))
        return out


class ArmTest(PendCase):
    def test_hit_arms_the_timer_and_expire_reverts(self):
        """The administrator through WireGuard changes its port: the timer (systemd-run --collect --on-active=120s
        … gw pending expire --token T), the answer carries ``pending``; not confirmed — the port is back and the event
        ``settings_reverted``. Fails without F7: no wait, the port stays."""
        old = self.wg_port()
        res = self.apply_port(51999, self.admin())
        self.assertEqual(self.wg_port(), 51999)
        p = res["pending"]
        self.assertEqual(set(p), {"token", "deadline", "scope", "left"})
        self.assertTrue(re.match(r"^[0-9a-f]{16}$", p["token"]))
        argv = self.runs[-1]
        self.assertEqual(argv[:6], ["systemd-run", "--unit=kaskad-gw-pending-" + p["token"], "--collect", "--quiet",
                                    "--on-active=120s", "--timer-property=AccuracySec=1s"])
        self.assertEqual(argv[-5:], ["gw", "pending", "expire", "--token", p["token"]])
        f = paths.gw_pending_apply()
        self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600)
        self.assertEqual(self.wait()["id"], self.last()["wg"]["id"])
        got = pending.expire(self.w.runner, p["token"], now=p["deadline"] + 1)
        self.assertEqual(got["done"], "reverted")
        self.assertEqual(self.wg_port(), old)
        self.assertIsNone(self.wait())
        self.assertEqual([(e["kind"], e["scope"], e["why"]) for e in self.events()],
                         [("settings_reverted", "wg", "no_confirm")])

    def test_confirm_keeps_and_a_foreign_token_does_nothing(self):
        res = self.apply_port(51998, self.admin())
        tok = res["pending"]["token"]
        self.assertEqual(pending.expire(self.w.runner, "0" * 16, now=time.time() + 999)["done"], "nothing")
        self.assertEqual(self.wg_port(), 51998)
        with self.assertRaises(pending.Refused) as c:
            pending.confirm(self.w.runner, "f" * 16)                    # another wait: «не успели»
        self.assertEqual(c.exception.key, "pending_gone")
        out = pending.confirm(self.w.runner, tok)
        self.assertTrue(out["confirmed"])
        self.assertIn(["systemctl", "stop", "kaskad-gw-pending-%s.timer" % tok], self.w.runner.commands(["systemctl"]))
        self.assertIsNone(self.wait())
        self.assertEqual(pending.expire(self.w.runner, tok, now=time.time() + 999)["done"], "nothing")
        self.assertEqual(self.wg_port(), 51998)
        self.assertEqual(self.undos, [])

    def test_early_timer_waits_for_the_rest(self):
        res = self.apply_port(51997, self.admin())
        p = res["pending"]
        n = len(self.runs)
        got = pending.expire(self.w.runner, p["token"], now=p["deadline"] - 30)
        self.assertEqual(got["done"], "later")
        self.assertEqual(self.wg_port(), 51997)
        self.assertEqual(len(self.runs), n + 1)
        self.assertIn("--on-active=30s", self.runs[-1])
        self.assertIn("--unit=kaskad-gw-pending-%s-1" % p["token"], self.runs[-1])   # the running unit holds its name
        self.assertEqual(self.wait()["unit"], "kaskad-gw-pending-%s-1" % p["token"])
        got = pending.expire(self.w.runner, p["token"], now=p["deadline"] - 2)       # within EARLY_S: the revert
        self.assertEqual(got["done"], "reverted")

    def test_no_timer_without_hit_and_for_mtu_alone(self):
        self.apply_port(51996, {"via": "panel", "ip": "198.51.100.7"})           # not in the pool
        self.assertIsNone(self.wait())
        self.apply_port(51995, None)                                            # the local console
        self.assertIsNone(self.wait())
        res = self.apply_port(None, self.admin(), changed={"mtu": {"old": 1420, "new": 1380}})
        self.assertIsNone(res["pending"])
        self.assertEqual(self.runs, [])

    def test_xray_kinds_never_guess_and_safe_arms(self):
        """З-6: VLESS through an exit — its address tells nothing (``admin_hit`` None) but the plan warns; ``safe``
        arms the timer for any kind."""
        st = state.load()
        self.assertIsNone(pending.admin_hit(st, st, "vless", {"via": "panel", "ip": "203.0.113.9"}))
        self.assertIsNone(pending.admin_hit(st, st, "vless", self.admin()))
        self.assertTrue(pending.via_xray("vless", {"port": {}}))
        self.assertTrue(pending.via_xray("hy2", {"tls": {}}))
        self.assertFalse(pending.via_xray("vless", {"mtu": {}}))
        self.assertFalse(pending.via_xray("wg", {"port": {}}))
        res = pending.guard(self.w.runner, st, {"changed": {"port": {}}, "id": "a-00000099"}, "vless",
                            who={"via": "panel", "ip": "203.0.113.9"}, safe=True, apply=lambda: {"ok": True})
        self.assertEqual(res["pending"]["scope"], "vless")
        self.assertEqual(len(self.runs), 1)

    def test_timer_refused_changes_nothing(self):
        before = self.read("/etc/kaskad/state.json")
        self.run_ok = False
        with self.assertRaises(pending.Refused) as c:
            self.apply_port(51994, self.admin())
        self.assertEqual((c.exception.key, c.exception.status), ("pending_timer", 500))
        self.assertEqual(self.read("/etc/kaskad/state.json"), before)
        self.assertIsNone(self.wait())

    def test_apply_failed_disarms(self):
        st = state.load()

        def boom():
            raise ConflictError("port busy")
        with self.assertRaises(ConflictError):
            pending.guard(self.w.runner, st, {"changed": {"port": {}}, "id": "a-1"}, "wg", who=self.admin(),
                          apply=boom)
        self.assertIsNone(self.wait())
        self.assertTrue(self.w.runner.commands(["systemctl", "stop"]))


class OneWaitTest(PendCase):
    def test_same_kind_while_waiting_is_409_and_last_kept(self):
        """С-3: port WG with a hit, then MTU WG -> 409 ``pending_busy``, settings-last.wg is the first apply."""
        self.apply_port(51990, self.admin())
        first = self.last()["wg"]["id"]
        with self.assertRaises(pending.Refused) as c:
            self.apply_port(None, None, changed={"mtu": {"old": 1420, "new": 1380}})
        self.assertEqual((c.exception.key, c.exception.status), ("pending_busy", 409))
        self.assertEqual(c.exception.data["pending"]["scope"], "wg")
        self.assertEqual(self.last()["wg"]["id"], first)

    def test_other_kind_with_a_hit_or_safe_is_409_without_is_fine(self):
        self.apply_port(51991, self.admin())
        st = state.load()
        ok = pending.guard(self.w.runner, st, {"changed": {"mtu": {}}}, "awg2", who=self.admin(),
                           apply=lambda: {"ok": True})
        self.assertIsNone(ok["pending"])
        with self.assertRaises(pending.Refused):
            pending.guard(self.w.runner, st, {"changed": {"port": {}}}, "vless", safe=True, apply=lambda: {"ok": True})
        with self.assertRaises(pending.Refused):              # the network of the gateway while any wait
            pending.guard(self.w.runner, st, {"changed": {"base": {}}}, "base", apply=lambda: {"ok": True})

    def test_changed_last_reverts_nothing(self):
        res = self.apply_port(51992, self.admin())
        doc = self.last()
        doc["wg"]["id"] = "a-ffffffff"                          # replaced (or removed) meanwhile
        util.atomic_write(pending.settings_last_path(), json.dumps(doc), 0o600)
        got = pending.expire(self.w.runner, res["pending"]["token"], now=res["pending"]["deadline"])
        self.assertEqual(got["done"], "failed")
        self.assertEqual(self.wg_port(), 51992)
        self.assertEqual(self.undos, [])
        self.assertIsNone(self.wait())
        self.assertEqual([(e["kind"], e["why"]) for e in self.events()], [("settings_revert_failed", "changed")])

    def test_old_port_busy(self):
        """П-2: the old port taken meanwhile — ``port_busy`` with the port, the wait goes, a push with the port."""
        old = self.wg_port()
        res = self.apply_port(51993, self.admin())
        self.busy = True
        pending.expire(self.w.runner, res["pending"]["token"], now=res["pending"]["deadline"])
        self.assertIsNone(self.wait())
        ev = self.events()[-1]
        self.assertEqual((ev["kind"], ev["why"], ev["port"]), ("settings_revert_failed", "port_busy", old))
        self.assertIn(str(old), pending.event_text(ev["kind"], ev, "ru"))
        self.assertIn(str(old), pending.event_text(ev["kind"], ev, "en"))

    def test_disarm_takes_the_wait_off(self):
        """The manual revert of the same kind (F3 ``undo`` calls ``disarm``): no file, the timer stopped."""
        res = self.apply_port(51989, self.admin())
        with pending.lock():                         # A's manual undo (CONTRACT-pending «Добавка»): lock -> disarm
            self.assertEqual(pending.view()["scope"], "wg")
            rec = pending.disarm(self.w.runner)
        self.assertEqual(rec["token"], res["pending"]["token"])
        self.assertIsNone(self.wait())
        self.assertIn(["systemctl", "stop", "kaskad-gw-pending-%s.timer" % rec["token"]],
                      self.w.runner.commands(["systemctl"]))
        self.assertIsNone(pending.disarm(self.w.runner))


class RaceTest(PendCase):
    def test_a_confirm_wins_the_lock_over_expire(self):
        """С-2 (а): expire read nothing outside the lock — the confirm came first (the stand-in of the entry into the
        lock calls it), expire reads the file again under the lock: none — the port stays new."""
        res = self.apply_port(51980, self.admin())
        tok = res["pending"]["token"]
        real = pending.lock
        first = [True]

        @contextlib.contextmanager
        def entry(*a, **kw):
            if first[0] and not pending.held():
                first[0] = False
                pending.confirm(self.w.runner, tok)
            with real(*a, **kw):
                yield
        with mock.patch.object(pending, "lock", entry):
            got = pending.expire(self.w.runner, tok, now=res["pending"]["deadline"] + 1)
        self.assertEqual(got["done"], "nothing")
        self.assertEqual(self.wg_port(), 51980)
        self.assertEqual(self.undos, [])

    def test_b_confirm_after_the_revert_is_gone(self):
        res = self.apply_port(51981, self.admin())
        pending.expire(self.w.runner, res["pending"]["token"], now=res["pending"]["deadline"] + 1)
        with self.assertRaises(pending.Refused) as c:
            pending.confirm(self.w.runner, res["pending"]["token"])
        self.assertEqual((c.exception.key, c.exception.status), ("pending_gone", 409))

    def test_c_expire_due_with_a_live_timer_does_nothing(self):
        res = self.apply_port(51982, self.admin())
        self.active.add(self.wait()["unit"])
        n = len(self.runs)
        self.assertEqual(pending.expire_due(self.w.runner, now=res["pending"]["deadline"] + 60), "alive")
        self.assertEqual(len(self.runs), n)
        self.assertEqual(self.wg_port(), 51982)

    def test_d_expire_due_systemd_refused_reverts_and_the_loop_goes_on(self):
        old = self.wg_port()
        self.apply_port(51983, self.admin())
        self.run_ok = False
        with mock.patch.object(collect, "tick", wraps=collect.tick) as tk:
            self.assertEqual(collect.loop(self.w.runner, rounds=1), 1)
        self.assertEqual(tk.call_count, 1)
        self.assertEqual(self.wg_port(), old)
        self.assertIsNone(self.wait())

    def test_e_expire_due_then_expire_one_undo(self):
        res = self.apply_port(51984, self.admin())
        p = res["pending"]
        self.assertEqual(pending.expire_due(self.w.runner, now=p["deadline"] + 1), "reverted")
        self.assertEqual(pending.expire(self.w.runner, p["token"], now=p["deadline"] + 2)["done"], "nothing")
        self.assertEqual(len(self.undos), 1)

    def test_loop_rearms_a_wait_not_due(self):
        res = self.apply_port(51985, self.admin())
        n = len(self.runs)
        with mock.patch.object(pending.time, "time", lambda: res["pending"]["deadline"] - 40):
            self.assertEqual(pending.expire_due(self.w.runner), "rearmed")
        self.assertEqual(len(self.runs), n + 1)
        self.assertIn("--on-active=40s", self.runs[-1])
        self.assertEqual(self.wg_port(), 51985)

    def test_lock_order_and_reentry(self):
        with state.locked(), self.assertRaises(NestedLockError):
            with pending.lock():
                pass
        with pending.lock():
            with pending.lock():                          # expire -> undo -> disarm: the same thread
                self.assertTrue(pending.held())
            self.assertTrue(pending.held())
        self.assertFalse(pending.held())


class FeedTest(PendCase):
    def test_the_event_reaches_the_feed_and_the_bot(self):
        res = self.apply_port(51970, self.admin())
        pending.expire(self.w.runner, res["pending"]["token"], now=res["pending"]["deadline"] + 1)
        self.collect()
        self.assertEqual(self.events(), [])                     # the spool is taken
        with open(paths.live_json(), encoding="utf-8") as f:
            feed = [e for e in json.load(f).get("events") or [] if e.get("kind") == "settings_reverted"]
        self.assertEqual(len(feed), 1)
        self.assertIn("возвращены", feed[0]["text"])                  # the fake world names wg its own way
        from kaskad.bot import monitor
        self.assertTrue(monitor.push_on("settings_reverted", {}))
        self.assertTrue(monitor.push_on("settings_revert_failed", {}))
        for k in pending.EVENT_KINDS:
            self.assertIn(k, live.FEED_KINDS)

    def test_spool_keeps_the_newest_and_skips_junk(self):
        for i in range(pending.EVENTS_MAX + 3):
            pending.emit({"kind": "settings_reverted", "scope": "wg", "why": "no_confirm"})
        d = paths.gw_pending_events()
        with open(os.path.join(d, "zz-bad.json"), "w") as f:
            f.write("{")
        with open(os.path.join(d, "zz-foreign.json"), "w") as f:
            json.dump({"kind": "exit_dead", "scope": "wg"}, f)
        got = pending.drain()
        self.assertEqual(len(got), pending.EVENTS_MAX - 2)       # the two junk files count among the newest
        self.assertEqual(os.listdir(d), [])


class WordsTest(unittest.TestCase):
    def test_words_ru_en(self):
        for kind, a, ru, en in (
                ("settings_reverted", {"scope": "wg", "why": "no_confirm"}, "Настройки WireGuard возвращены",
                 "WireGuard settings were reverted"),
                ("settings_reverted", {"scope": "base", "why": "no_confirm"}, "Сеть шлюза возвращена",
                 "gateway network was reverted"),
                ("settings_revert_failed", {"scope": "wg", "why": "port_busy", "port": 51820}, "порт 51820 занят",
                 "port 51820 is busy"),
                ("settings_revert_failed", {"scope": "wg", "why": "changed"}, "уже изменили", "changed again"),
                ("settings_revert_failed", {"scope": "base", "why": "error"}, "Сеть шлюза", "gateway network")):
            ev = {"kind": kind, "key": "gw_live.event." + kind, "args": a}
            self.assertIn(ru, live.event_text(ev, "ru"))
            text = live.event_text(ev, "en")
            self.assertIn(en, text)
            self.assertIsNone(re.search("[А-Яа-яЁё]", text), text)


class WhoTest(unittest.TestCase):
    def test_ssh_connection(self):
        self.assertEqual(pending.who_ssh({"SSH_CONNECTION": "10.77.1.10 52000 10.77.1.1 22"}),
                         {"via": "ssh", "ip": "10.77.1.10"})
        self.assertIsNone(pending.who_ssh({}))
        for raw in ("127.0.0.1", "", "garbage", "::1", "2001:db8::1", None):
            self.assertIsNone(pending._ip_of({"ip": raw}), raw)
        self.assertEqual(str(pending._ip_of({"ip": "::ffff:10.77.1.10"})), "10.77.1.10")


class PreviewApiTest(PendCase):
    def test_issue_preview_writes_nothing_and_has_the_new_port(self):
        st_before = self.read("/etc/kaskad/state.json")
        run = paths.gw_run_dir()
        before = sorted(os.listdir(run)) if os.path.isdir(run) else []
        seen = []
        ad = ops.adapter("wg")
        real = ad.issue

        def spy(st, sec, d, os_name):
            seen.append(model.inbound(st, "wg")["port"])
            return real(st, sec, d, os_name)
        with mock.patch.object(ad, "issue", spy):
            res = ops.issue_preview(self.w.runner, self.did, "wg", {"port": 51777})
        self.assertTrue(res["preview"])
        self.assertEqual(seen, [51777])                               # the settings are made of the new port
        self.assertEqual(self.read("/etc/kaskad/state.json"), st_before)
        self.assertEqual(sorted(os.listdir(run)) if os.path.isdir(run) else [], before)    # no inlog / marks
        mine = ops.issue_preview(self.w.runner, None, "wg", {"port": 51777}, who=self.admin())
        self.assertEqual(mine["device"], self.did)
        with self.assertRaises(Exception):
            ops.issue_preview(self.w.runner, None, "wg", {"port": 51777}, who={"ip": "198.51.100.1"})
        if not hasattr(ops, "settings_mutate"):                   # before F3: the port only (after it — F3's mutate)
            with self.assertRaises(Exception):
                ops.issue_preview(self.w.runner, self.did, "wg", {"mtu": 1300})

    def test_api(self):
        self.assertIsNone(self.ok("GET", "/api/gw/pending")["pending"])
        self.assertEqual(self.err(409, "POST", "/api/gw/pending/confirm", {"token": "a" * 16})["code"], "pending_gone")
        self.assertEqual(self.err(400, "POST", "/api/gw/pending/confirm", {"token": "../x"})["code"], "field")
        self.assertEqual(self.err(400, "POST", "/api/gw/pending/confirm", {"token": "a" * 16, "x": 1})["code"], "field")
        res = self.apply_port(51960, self.admin())
        got = self.ok("GET", "/api/gw/pending")["pending"]
        self.assertEqual((got["scope"], got["token"]), ("wg", res["pending"]["token"]))
        self.assertTrue(self.ok("POST", "/api/gw/pending/confirm", {"token": got["token"]})["confirmed"])
        self.assertIsNone(self.ok("GET", "/api/gw/pending")["pending"])
        r = self.call("POST", "/api/gw/inbounds/wg/settings/issue", {"device": self.did, "port": 51778})
        self.assertEqual(r.status, 200, r)
        self.assertIn("no-store", r.headers["cache-control"])
        self.assertEqual((r.json()["device"], r.json()["preview"]), (self.did, True))
        self.assertEqual(self.wg_port(), 51960)                       # nothing written
        for body in ({"device": self.did, "port": True}, {"device": self.did, "mtu": False}, {"device": self.did, "x": 1}):
            self.assertEqual(self.call("POST", "/api/gw/inbounds/wg/settings/issue", body).status, 400, body)


class FixG1Test(PendCase):
    """REV-3315-g1: the findings С-1, С-2, З-1, З-2, З-3, З-4, П-2, П-3 and the joints of lane A (fix-a1: ``plan["id"]``
    None, F3's refusal «moved»). Each test fails without its fix."""

    def armed(self, port=51960):
        res = self.apply_port(port, self.admin())
        return res, self.wait()

    def test_c1_busy_lock_in_the_revert_retries_then_reverts(self):
        """С-1: F3's undo meets a busy state lock (``LockedError``) or a transition of VLESS under way (``ConflictError``
        ``need: busy``) -> the wait stays, its timer again in RETRY_S s (a new unit, ``n`` grew), no event; the next
        expire with the lock free -> ``settings_reverted``, the port old. Without the fix: the wait gone, «error»."""
        from kaskad.errors import LockedError
        for refusal in (LockedError("busy"), ConflictError("busy", data={"need": "busy"})):
            with self.subTest(refusal=type(refusal).__name__):
                old = self.wg_port()
                res, rec = self.armed()
                runs = len(self.runs)
                self.refuse = refusal
                got = pending.expire(self.w.runner, rec["token"], now=rec["deadline"] + 1)
                self.assertEqual(got["done"], "retry")
                again = self.wait()
                self.assertIsNotNone(again)
                self.assertEqual((again["token"], again["n"]), (rec["token"], rec["n"] + 1))
                self.assertEqual(self.events(), [])
                self.assertEqual(len(self.runs), runs + 1)
                self.assertIn("--on-active=%ds" % pending.RETRY_S, self.runs[-1])
                self.assertIn("--unit=" + again["unit"], self.runs[-1])
                self.refuse = None
                got = pending.expire(self.w.runner, rec["token"], now=rec["deadline"] + 1 + pending.RETRY_S)
                self.assertEqual(got["done"], "reverted")
                self.assertEqual(self.wg_port(), old)
                self.assertEqual([e["kind"] for e in self.events()], ["settings_reverted"])
                self.assertIsNone(self.wait())
                pending.drain()

    def test_c1_the_ceiling_and_a_nested_lock_are_final(self):
        from kaskad.errors import LockedError
        for refusal, at in ((LockedError("busy"), pending.RETRY_MAX + 1), (NestedLockError("bug"), 1)):
            with self.subTest(refusal=type(refusal).__name__):
                res, rec = self.armed()
                self.refuse = refusal
                got = pending.expire(self.w.runner, rec["token"], now=rec["deadline"] + at)
                self.assertEqual(got["done"], "failed")
                self.assertIsNone(self.wait())
                self.assertEqual([(e["kind"], e["why"]) for e in self.events()], [("settings_revert_failed", "error")])
                pending.drain()
                self.refuse = None
                self.set_port(self.w.runner, "wg", 51820)

    def test_lane_a_moved_is_final_nothing_to_revert(self):
        """fix-a1: F3's undo refuses with ``why: "moved"`` (the port or MTU changed by another action) -> final, no
        retry timer, the event «не возвращены: их уже изменили» (``why: changed``)."""
        res, rec = self.armed()
        runs = len(self.runs)
        self.refuse = ConflictError("moved", data={"why": "moved", "now": {"port": 51111}})
        got = pending.expire(self.w.runner, rec["token"], now=rec["deadline"] + 1)
        self.assertEqual(got["done"], "failed")
        self.assertIsNone(self.wait())
        self.assertEqual(len(self.runs), runs)
        self.assertEqual([(e["kind"], e["why"]) for e in self.events()], [("settings_revert_failed", "changed")])
        self.assertIn("уже изменили", pending.event_text("settings_revert_failed", {"scope": "wg", "why": "changed"}))

    def test_lane_a_no_id_hit_or_safe_refused_else_applied(self):
        """fix-a1 + CONTRACT «Добавка координатора 3»: ``plan["id"] = None`` (VLESS on 443 in front of the site;
        settings-last can't be written — nothing to revert). The administrator hit or ``safe`` -> 409
        ``pending_no_undo``, nothing applied, no timer (before the fix: TypeError of ``arm``); neither — applied, no
        wait."""
        st = state.load()
        called = []
        plan = {"changed": {"port": {"old": 51820, "new": 51955}}, "id": None}
        for who, safe, why in ((self.admin(), False, "hit"), (None, True, "safe"), (self.admin(), True, "hit")):
            with self.subTest(why=why, safe=safe):
                with self.assertRaises(pending.Refused) as c:
                    pending.guard(self.w.runner, st, plan, "wg", who=who, safe=safe, apply=lambda: called.append(1))
                self.assertEqual((c.exception.key, c.exception.status, c.exception.data["why"]),
                                 ("pending_no_undo", 409, why))
                self.assertNotIn("{", str(c.exception))
        self.assertEqual((called, self.runs, self.wait()), ([], [], None))
        for lang in ("ru", "en"):
            from kaskad.i18n import tr
            for key in ("gw_pending.err.no_undo", "gw_pending.err.no_undo_hit"):
                self.assertNotIn("{", tr(key, lang, what="WireGuard"))
        res = pending.guard(self.w.runner, st, plan, "wg", who={"ip": "198.51.100.7"},
                            apply=lambda: called.append(2) or {"ok": True})
        self.assertIsNone(res["pending"])
        self.assertEqual((called, self.runs, self.wait()), ([2], [], None))

    def test_c2_admin_hit_only_when_it_cuts_and_its_words(self):
        """С-2 (CONTRACT «Добавка координатора 2»): ``admin_hit(..., changed)`` — a code only when the change cuts (MTU
        alone — None); ``hit_text`` — the words of SPEC F7, RU and EN; no code — ""."""
        from kaskad.gw import registry
        st = state.load()
        who = self.admin()
        self.assertIsNone(pending.admin_hit(st, st, "wg", who, {"mtu": {"old": 1420, "new": 1380}}))
        self.assertIsNone(pending.admin_hit(st, st, "wg", who))
        self.assertEqual(pending.admin_hit(st, st, "wg", who, {"port": {}}), "pool")
        self.assertIsNone(pending.admin_hit(st, st, "wg", {"ip": "198.51.100.7"}, {"port": {}}))
        ru = pending.hit_text("pool", "wg", "ru")
        self.assertIn(registry.title("wg"), ru)
        self.assertIn("если за 2 мин не подтвердите — настройки вернутся сами", ru.lower())
        self.assertIn("сеть шлюза", pending.hit_text("base", "base", "ru").lower())
        for code in ("pool", "base"):
            en = pending.hit_text(code, "wg", "en")
            self.assertIn("2 min", en)
            self.assertNotIn("{", en)
        self.assertEqual((pending.hit_text(None, "wg"), pending.hit_text("", "wg")), ("", ""))

    def test_z1_left_counts_the_time_of_the_apply(self):
        """З-1: the deadline is set before the apply (≈ 25 s at AWG) -> ``left`` of the answer is what is left after
        it. Without the fix — always 120."""
        clock = [time.time()]
        st_old = state.load()

        def apply():
            clock[0] += 30
            return {"ok": True}
        with mock.patch.object(pending.time, "time", lambda: clock[0]):
            res = pending.guard(self.w.runner, st_old, {"changed": {"port": {}}, "id": "a-00000001"}, "wg",
                                who=self.admin(), apply=apply)
        self.assertLessEqual(res["pending"]["left"], pending.WAIT_S - 30)
        self.assertGreaterEqual(res["pending"]["left"], pending.WAIT_S - 31)

    def test_z2_an_overdue_wait_without_its_timer_is_reverted_by_a_round(self):
        """З-2: the timer lost (``daemon-reexec``, ``reset-failed``) — not only the start of the collector: any round
        reverts a wait OVERDUE_S s past its deadline; the round brings the event into the feed at once. Not overdue
        yet — nothing, no command. Without the fix the wait hangs till the next restart of the collector."""
        old = self.wg_port()
        res, rec = self.armed()
        rec = dict(rec, deadline=int(time.time()) - 30)
        pending._write(rec)
        self.assertIsNone(pending.overdue(self.w.runner))                 # 30 s past: the timer may still come
        self.assertEqual(self.wg_port(), 51960)
        pending._write(dict(rec, deadline=int(time.time()) - 120))
        self.collect()
        self.assertEqual(self.wg_port(), old)
        self.assertIsNone(self.wait())
        self.assertEqual(self.events(), [])                               # the same round took the spool
        with open(paths.live_json(), encoding="utf-8") as f:
            feed = [e for e in json.load(f).get("events") or [] if e.get("kind") == "settings_reverted"]
        self.assertEqual(len(feed), 1)

    def test_z2_a_busy_lock_in_the_round_is_quiet(self):
        """З-2: an apply holds the lock of the wait — the round does not wait LOCK_WAIT s and writes no line each
        round ("busy": the next round tries again); a nested lock (a bug) is said."""
        from kaskad.errors import LockedError
        res, rec = self.armed()
        pending._write(dict(rec, deadline=int(time.time()) - 120))
        for err, done, said in ((LockedError("busy"), "busy", 0), (NestedLockError("bug"), "error", 1)):
            with mock.patch.object(pending, "lock", side_effect=err) as lk, \
                    mock.patch.object(pending.util, "log_action") as la:
                self.assertEqual(pending.overdue(self.w.runner), done)
            self.assertEqual(lk.call_args.args, (pending.DUE_WAIT,))
            self.assertEqual(la.call_count, said)
        self.assertIsNotNone(self.wait())

    def test_z2_busy_at_zero_says_the_revert_goes_on(self):
        res, rec = self.armed()
        e = pending._busy(rec, rec["deadline"] + 5)
        self.assertEqual(e.data["pending"]["left"], 0)
        self.assertIn("возвращаются сейчас", str(e))
        self.assertNotIn("ещё 0 с", str(e))

    def test_z3_a_failed_round_loses_no_event(self):
        """З-3: the files of the spool go only once the feed has their events — a round that fails before the feed
        keeps them; the next round brings the event. Without the fix the drain at the start of the round lost it."""
        pending.emit({"kind": "settings_reverted", "scope": "wg", "why": "no_confirm"})
        with mock.patch.object(collect.LIVE, "full", side_effect=RuntimeError("boom")):
            self.collect()
        self.assertEqual(len(self.events()), 1)
        self.collect()
        self.assertEqual(self.events(), [])
        with open(paths.live_json(), encoding="utf-8") as f:
            feed = [e for e in json.load(f).get("events") or [] if e.get("kind") == "settings_reverted"]
        self.assertEqual(len(feed), 1)

    def test_z4_preview_logs_a_line_and_needs_an_enabled_kind(self):
        """З-4: «Скачать мои новые настройки» — a line in the journal (as ``issue``: the device and the kind, no
        secret); a kind turned off — refused as ``issue`` refuses."""
        from kaskad.errors import KaskadError
        with mock.patch.object(ops.util, "log_tr", wraps=ops.util.log_tr) as lt:
            ops.issue_preview(self.w.runner, self.did, "wg", {"port": 51777})
        calls = [c for c in lt.call_args_list if c.args and c.args[0] == "gw_pending.log.preview"]
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0].kwargs["id"], calls[0].kwargs["kind"]), (self.did, "wg"))
        with mock.patch.object(model, "enabled_kinds", return_value=[]):
            with self.assertRaises(KaskadError):
                ops.issue_preview(self.w.runner, self.did, "wg", {"port": 51777})

    def test_p2_a_stale_wait_reverts_nothing(self):
        """П-2: a wait a day past its deadline (a rollback of the version, an update much later) — its «old» values
        are of days ago: nothing is reverted, the event «changed», the port untouched."""
        res, rec = self.armed()
        got = pending.expire(self.w.runner, rec["token"], now=rec["deadline"] + 2 * 86400)
        self.assertEqual(got["done"], "failed")
        self.assertEqual(self.undos, [])
        self.assertEqual(self.wg_port(), 51960)
        self.assertEqual([(e["kind"], e["why"]) for e in self.events()], [("settings_revert_failed", "changed")])

    def test_p3_the_line_of_the_timer_in_words(self):
        """П-3: the line of ``gw pending expire`` in the journal — words, not a code."""
        from kaskad.i18n import tr
        seen = set()
        for code in ("nothing", "later", "retry", "reverted", "failed"):
            for lang in ("ru", "en"):
                w = pending.done_text(code, lang)
                self.assertTrue(w and "{" not in w and w != code, (code, lang))
                seen.add(w)
            self.assertNotIn(code, tr("gw_pending.cli.expire", "ru", done=pending.done_text(code, "ru")))
        self.assertEqual(len(seen), 10)


def _f3_merged():
    """F3 of lane A is here: ``ops.inbound_settings(..., who=…)`` goes through ``pending.guard``."""
    import inspect
    fn = getattr(ops, "inbound_settings", None)
    return callable(fn) and "who" in inspect.signature(fn).parameters


@unittest.skipUnless(_f3_merged(), "F3 (lane A) not merged yet: required after the merge")
class F3EndToEndTest(PendCase):
    """REV-3315-g1 С-3: the path of F3 itself — ``ops.inbound_settings`` -> ``pending.guard`` -> its ``apply`` writes
    settings-last -> the timer -> ``expire`` -> ``ops.inbound_settings_undo(pending=rec)`` -> the old port; and the
    refusal «moved» of F3 when the port was changed by another action."""

    def setUp(self):
        super().setUp()
        self._undo.stop()                                  # F3's own undo, not the stand-in
        self._undo = mock.patch.object(pending, "WAIT_S", pending.WAIT_S)
        self._undo.start()

    def test_guard_apply_expire_undo(self):
        old = self.wg_port()
        res = ops.inbound_settings(self.w.runner, "wg", port=51944, yes=True, who=self.admin())
        p = res["pending"]
        self.assertIsNotNone(p)
        self.assertEqual(self.wg_port(), 51944)
        self.assertEqual(self.wait()["id"], self.last()["wg"]["id"])
        got = pending.expire(self.w.runner, p["token"], now=p["deadline"] + 1)
        self.assertEqual(got["done"], "reverted")
        self.assertEqual(self.wg_port(), old)
        self.assertIsNone(self.wait())
        self.assertEqual([e["kind"] for e in self.events()], ["settings_reverted"])

    def test_moved_by_another_action(self):
        res = ops.inbound_settings(self.w.runner, "wg", port=51945, yes=True, who=self.admin())
        p = res["pending"]
        self.set_port(self.w.runner, "wg", 51946)          # the port changed by another action meanwhile
        got = pending.expire(self.w.runner, p["token"], now=p["deadline"] + 1)
        self.assertEqual(got["done"], "failed")
        self.assertEqual(self.wg_port(), 51946)
        self.assertIsNone(self.wait())
        self.assertEqual([(e["kind"], e["why"]) for e in self.events()], [("settings_revert_failed", "changed")])

    def test_a_failure_after_the_write_keeps_the_wait_and_the_record(self):
        """REV-3315-g2 Н-2 × F3: the port is written, a step after it fails — the answer is ``ok`` with the words of
        it, the wait and settings-last stay: the timer brings the old port back; without a wait — «Вернуть прежние» by
        hand. Fails before the fix: the error went out of ``apply`` — the record put back, the wait dropped, the new
        port without its revert."""
        old = self.wg_port()
        real = ops._settings_marks

        def marks(st, kind, req):
            if (model.inbound(st, "wg") or {}).get("port") == req.get("port"):    # the state after the write only
                raise TypeError("boom after the write")
            return real(st, kind, req)
        with mock.patch.object(ops, "_settings_marks", marks):
            res = ops.inbound_settings(self.w.runner, "wg", port=51947, yes=True, who=self.admin())
        self.assertTrue(res["ok"])
        self.assertEqual(self.wg_port(), 51947)
        self.assertIn("TypeError", res["text"])
        self.assertNotIn("boom", json.dumps(res, ensure_ascii=False, default=str))
        p = res["pending"]
        self.assertIsNotNone(p)
        self.assertEqual(self.wait()["id"], self.last()["wg"]["id"])
        self.assertEqual(self.last()["wg"]["id"], res["id"])
        self.assertIn("boom after the write", util.read_text(paths.log()))
        got = pending.expire(self.w.runner, p["token"], now=p["deadline"] + 1)
        self.assertEqual(got["done"], "reverted")
        self.assertEqual(self.wg_port(), old)
        with mock.patch.object(ops, "_settings_marks", marks):          # no administrator hit: no wait
            res = ops.inbound_settings(self.w.runner, "wg", port=51948, yes=True)
        self.assertIsNone(res["pending"])
        self.assertEqual(self.last()["wg"]["id"], res["id"])
        ops.inbound_settings_undo(self.w.runner, "wg")
        self.assertEqual(self.wg_port(), old)


    def _after_write(self, port, act):
        """``_settings_marks`` runs ``act`` on the state after the write only (the plan reads the state before it)."""
        real = ops._settings_marks

        def marks(st, kind, req):
            if (model.inbound(st, "wg") or {}).get("port") == port:
                act()
            return real(st, kind, req)
        return mock.patch.object(ops, "_settings_marks", marks)

    def test_g32_ctrl_c_right_after_the_write(self):
        """REV-3315-g3 Г3-2: SIGINT (Ctrl+C in the CLI or the SSH menu) right after ``_change`` of F3 — the steps after
        the write run to their end: ``ok``, the wait and settings-last stay. Fails before the fix: KeyboardInterrupt,
        the wait and the record dropped, the new port without its revert."""
        try:
            with self._after_write(51949, lambda: os.kill(os.getpid(), signal.SIGINT)):
                res = ops.inbound_settings(self.w.runner, "wg", port=51949, yes=True, who=self.admin())
        except KeyboardInterrupt:
            self.fail("Ctrl+C after the write cut the change of the port")
        self.assertTrue(res["ok"])
        self.assertNotIn("after_failed", res)
        self.assertEqual(self.wg_port(), 51949)
        self.assertEqual(self.wait()["id"], res["id"])
        self.assertEqual(self.last()["wg"]["id"], res["id"])

    def test_g34_after_failed_the_flag_and_no_button_without_a_record(self):
        """REV-3315-g3 Г3-4 and CONTRACT-pending-3315 добавка 6: a step after the write failed — ``after_failed: true``
        (the API too); no record of the last application — the words name no «Вернуть прежние». Fails before the
        fix: no flag, the button named without a record."""
        def boom():
            raise TypeError("boom after the write")
        with self._after_write(51950, boom):
            res = ops.inbound_settings(self.w.runner, "wg", port=51950, yes=True)
        self.assertIs(res["after_failed"], True)
        self.assertIn("«Вернуть прежние»", res["text"])                     # the record is there: the button is
        with self._after_write(51951, boom), mock.patch.object(ops, "_last_can_write", lambda: False):
            res = ops.inbound_settings(self.w.runner, "wg", port=51951, yes=True)
        self.assertIs(res["after_failed"], True)
        self.assertEqual(self.wg_port(), 51951)
        self.assertIn("TypeError", res["text"])
        self.assertNotIn("Вернуть прежние", res["text"])
        self.assertTrue(res["warnings"])                                    # «… для этого применения не будет»
        ok = ops.inbound_settings(self.w.runner, "wg", port=51952, yes=True)
        self.assertNotIn("after_failed", ok)
        ver = self.ok("GET", "/api/gw/inbounds/wg/settings")["ver"]
        with self._after_write(51953, boom):
            doc = self.ok("PUT", "/api/gw/inbounds/wg/settings", {"port": 51953, "ver": ver, "yes": True})
        self.assertIs(doc["after_failed"], True)
        self.assertEqual(self.wg_port(), 51953)
        for lang in ("ru", "en"):
            words = tr("gw_ops.settings.after_failed_no_undo", lang, title="WireGuard", err="E")
            self.assertNotIn("«", words)
            self.assertNotIn("return the previous", words)


class CatalogTest(unittest.TestCase):
    def test_hidden_with_a_reason(self):
        with open(os.path.join(REPO, "lib", "kaskad", "mcp", "actions.json"), encoding="utf-8") as f:
            hidden = json.load(f)["hidden"]
        for cmd in ("gw pending confirm", "gw pending expire"):
            self.assertTrue(hidden[cmd]["ru"] and hidden[cmd]["en"], cmd)


class RollbackTest(unittest.TestCase):
    """«Прошлая версия читает новые» (the owner's rule 24.10): 3.3.7.14 (fc5ef683) and 3.3.7.13 (bc541e29) know no
    ``gw pending`` — the timer left from the window is an error of argparse (code 2), nothing done; the new events of
    the feed are told by the text their writer stored, never by a raw key."""

    def old_tree(self, rev):
        try:
            ok = subprocess.run(["git", "-C", REPO, "cat-file", "-e", rev + "^{commit}"], capture_output=True,
                                timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise unittest.SkipTest("no git")
        if ok.returncode != 0:
            raise unittest.SkipTest("no commit %s" % rev)
        d = tempfile.mkdtemp(prefix="kaskad-old-")
        self.addCleanup(subprocess.run, ["rm", "-rf", d])
        arc = subprocess.run(["git", "-C", REPO, "archive", rev, "lib"], capture_output=True, timeout=120)
        subprocess.run(["tar", "-x", "-C", d], input=arc.stdout, check=True, timeout=120)
        return d

    def test_old_versions(self):
        code = r"""
import json, sys
from kaskad.gw import live
out = {}
for kind in ("group_regrouped", "settings_reverted", "settings_revert_failed"):
    out[kind] = live.event_text({"kind": kind, "key": "gw_live.event." + kind, "args": {"scope": "wg"},
                                 "text": "STORED " + kind}, "ru")
print(json.dumps(out))
"""
        for rev in ("fc5ef683", "bc541e29"):
            with self.subTest(rev=rev):
                d = self.old_tree(rev)
                root = tempfile.mkdtemp(prefix="kaskad-oldroot-")
                self.addCleanup(subprocess.run, ["rm", "-rf", root])
                env = dict(os.environ, PYTHONPATH=os.path.join(d, "lib"), KASKAD_ROOT=root, LANG="C.UTF-8")
                r = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=120)
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
                said = json.loads(r.stdout.decode())
                for kind, text in said.items():
                    self.assertEqual(text, "STORED " + kind)
                r = subprocess.run([sys.executable, "-m", "kaskad", "gw", "pending", "expire", "--token", "0" * 16],
                                   capture_output=True, env=env, timeout=120)
                self.assertEqual(r.returncode, 2, r.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
