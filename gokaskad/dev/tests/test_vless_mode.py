"""VLESS: the action «mode» — step Ш2 of SPEC-vless-tls-default (§3.5–§3.7, §4.2, §5.1, решения координатора после
круга 3: С-1, С-2, З-1…З-4): the lock of a transition, the snapshot ``mode_pending`` and its undo, ``vless_to_tls``,
``vless_mode``, the price in the core, CLI/API/MCP, the import of a copy, the findings of REV-vless-sh1."""

import fcntl
import json
import os
import threading
from unittest import mock

from kaskad import backup, errors, paths, state, util
from kaskad.gw import model
from kaskad.gw import ops as gops
from kaskad.site import front
from kaskad.site import model as smodel
from kaskad.site import ops as site_ops
from tests import test_gw_vless_front as F
from tests.test_vless_want import GOOD, WantCase


def raw():
    with open(paths.state(), "rb") as f:
        return f.read()


class _Case(WantCase):
    def setUp(self):
        super().setUp()
        self.put(want="tls")

    def site_mode(self):
        return smodel.peek(state.load())["mode"]

    def hold_lock(self):
        """Another holder of the lock of a transition (another open file description = another process for flock)."""
        p = paths.vless_mode_lock()
        util.ensure_run_dir(os.path.dirname(p))
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.addCleanup(os.close, fd)
        return fd

    def left_pending(self):
        """A transition left by a dead process: the snapshot written, then the record half changed (TLS on 443)."""
        front.snap_take(self.w.runner)
        st = state.load()
        model.inbound(st, "vless").update(security="tls", sni="x.example.com")
        state.save(st)


class SnapshotUndoTest(_Case):
    """С1: Reality on 443 with a cover of the list -> R→T refused (nginx, xray, an exception): all as it was."""

    def check_undone(self, before, got=None):
        rec = self.rec()
        self.assertEqual((rec["security"], rec["port"], rec["sni"], rec["mask"], rec["want"]),
                         ("reality", 443, "www.microsoft.com", "list", "tls"))
        self.assertEqual(self.link(self.dev["id"]), before)                     # byte for byte
        self.assertEqual(self.site_mode(), "domain")
        self.assertNotIn("mode_pending", rec)
        self.assertTrue(rec["tls_error"]["at"] and rec["tls_error"]["why"])
        if got is not None:
            self.assertEqual((got["ok"], got["restored"]), (False, True))
        self.assertIn("откат", self.journal())

    def test_a_nginx_refusing_the_socket(self):
        before = self.link(self.dev["id"])
        self.nginx.refuse = "set_real_ip_from"
        got = gops.vless_mode(self.w.runner, "tls", yes=True)
        self.check_undone(before, got)
        self.assertEqual(front.reason(state.load())["reason"], "no_site")

    def test_b_xray_refusing_the_config(self):
        before = self.link(self.dev["id"])
        with mock.patch.object(front, "xray_fronts", return_value=False):
            got = gops.vless_mode(self.w.runner, "tls", yes=True)
        self.check_undone(before, got)

    def test_c_an_exception_in_the_middle(self):
        before = self.link(self.dev["id"])
        with mock.patch.object(site_ops, "_with_vless", side_effect=RuntimeError("boom /etc/kaskad/secrets.json")):
            got = gops.vless_mode(self.w.runner, "tls", yes=True)
        self.check_undone(before, got)
        self.assertNotIn("/etc/kaskad", self.rec()["tls_error"]["why"])        # no paths in state (the panel, MCP)

    def test_success_with_the_site_off(self):
        self.put(reality_consent=dict(GOOD), tls_error={"at": "x", "why": "y"})
        got = gops.vless_mode(self.w.runner, "tls", yes=True)
        self.assertTrue(got["ok"])
        rec = self.rec()
        self.assertEqual((rec["security"], rec["port"], rec["want"]), ("tls", 443, "tls"))
        for k in ("reality_consent", "tls_error", "mode_pending"):
            self.assertNotIn(k, rec)
        self.assertEqual(self.site_mode(), "site")
        self.assertEqual([r["name"] for r in got["reissue"]], ["Телефон"])

    def test_the_price_is_asked_by_the_core(self):
        before = raw()
        rc, doc, _ = self.run_json("gw", "vless", "mode", "tls")                 # devices, no --yes: CONFLICT
        self.assertEqual(rc, errors.CONFLICT)
        self.assertEqual(doc["data"]["need"], "vless_change")
        self.assertIn("Телефон", doc["data"]["text"])
        self.assertEqual(raw(), before)
        rc, doc, err = self.run_json("gw", "vless", "mode", "tls", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.rec()["security"], "tls")


class PendingTest(_Case):
    """A transition left behind (§3.5 п.7, С-1, З-1)."""

    def test_the_next_entry_undoes_it(self):
        before = self.link(self.dev["id"])
        self.left_pending()
        rc, doc, err = self.run_json("gw", "vless", "show")                     # read-only: nothing undone
        self.assertEqual(rc, 0, err)
        self.assertTrue(doc["pending"]["at"])
        self.assertIn("mode_pending", self.rec())
        rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("mode_pending", self.rec())
        self.assertEqual(self.link(self.dev["id"]), before)
        self.assertIn("откачена", self.journal())

    def test_show_changes_nothing_byte_for_byte(self):
        self.left_pending()
        before = raw()
        self.run_json("gw", "vless", "show")
        gops.vless_view(state.load(), self.w.runner)
        self.assertEqual(raw(), before)

    def test_a_held_lock_refuses_and_undoes_nothing(self):
        self.left_pending()
        self.hold_lock()
        before = raw()
        rc, doc, _ = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("Идёт смена режима", doc["error"])
        with self.assertRaises(errors.ConflictError):
            site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(raw(), before)

    def test_show_tells_a_live_transition_from_one_left_behind(self):
        """§4.2: ``pending`` {at, busy} — the lock held = «идёт смена режима», free = a transition to undo. Read-only."""
        self.left_pending()
        before = raw()
        rc, doc, err = self.run_json("gw", "vless", "show")
        self.assertEqual((rc, doc["pending"]["busy"]), (0, False), err)
        rc, out, err = self.run_cli("gw", "vless", "show")
        self.assertIn("--recover", out)
        fd = self.hold_lock()
        rc, doc, err = self.run_json("gw", "vless", "show")
        self.assertEqual((rc, doc["pending"]["busy"]), (0, True), err)
        rc, out, err = self.run_cli("gw", "vless", "show")
        self.assertIn("Идёт смена режима", out)
        self.assertEqual(raw(), before)
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertFalse(front.lock_busy())                                     # the probe left no lock behind
        with front.vless_guard():
            self.assertTrue(front.lock_busy())

    def test_the_watchdog_waits_while_the_network_boots(self):
        """С-1: ``net check --repair`` undoes a transition left behind — but not while kaskad-net starts (booting)."""
        from kaskad import netcheck
        self.left_pending()
        with mock.patch.object(gops, "booting", return_value=True):
            res = netcheck.check(self.w.runner, repair=True)
        self.assertIn("mode_pending", self.rec())
        self.assertNotIn("vless_pending", [p["kind"] for p in res["problems"]])

    def test_the_real_watchdog_undoes_a_transition_left_behind(self):
        """SEC-vless-sh12 №1: the real ``net check --repair`` (kaskad-net-watch), nothing mocked but the boot: the undo
        runs after the lock of the state is let go — no nested lock, rc 0, the record as in the snapshot."""
        from kaskad import netcheck
        want = dict((k, self.rec().get(k)) for k in ("security", "port", "sni", "mask", "dest"))
        self.left_pending()
        self.assertEqual(self.rec()["security"], "tls")
        with mock.patch.object(gops, "booting", return_value=False):
            rc, doc, err = self.run_json("net", "check", "--repair")
        self.assertEqual(rc, 0, err)
        pend = [p for p in doc["problems"] if p["kind"] == "vless_pending"]
        self.assertEqual(len(pend), 1, doc)
        self.assertTrue(pend[0]["repaired"], pend)
        rec = self.rec()
        self.assertNotIn("mode_pending", rec)
        self.assertEqual(dict((k, rec.get(k)) for k in want), want)
        self.assertNotIn("Ошибка в коде", self.journal() + err + json.dumps(doc, ensure_ascii=False))
        with mock.patch.object(gops, "booting", return_value=False):            # nothing left: nothing to say
            res = netcheck.check(self.w.runner, repair=True)
        self.assertNotIn("vless_pending", [p["kind"] for p in res["problems"]])

    def test_the_watchdog(self):
        self.left_pending()
        fd = self.hold_lock()
        self.assertIsNone(front.recover_watch(self.w.runner))                  # the lock taken: skipped
        self.assertIn("mode_pending", self.rec())
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertTrue(front.recover_watch(self.w.runner)["restored"])
        self.assertNotIn("mode_pending", self.rec())

    def test_a_failed_undo_keeps_the_snapshot_and_refuses_new_transitions(self):
        self.left_pending()
        with mock.patch.object(front, "snap_restore", side_effect=RuntimeError("disk")):
            with self.assertRaises(errors.ConflictError):
                gops.vless_mode(self.w.runner, "tls", yes=True)
        self.assertIn("mode_pending", self.rec())
        self.assertIn("не откатилась", self.journal())
        with self.assertRaises(errors.ConflictError):                          # З-1: never written over
            front.snap_take(self.w.runner)
        got = gops.vless_mode(self.w.runner, None, recover=True)
        self.assertTrue(got["recovered"])
        self.assertNotIn("mode_pending", self.rec())

    def test_own_sync_inside_a_transition_does_not_undo_it(self):
        with mock.patch.object(front, "recover_pending", wraps=front.recover_pending) as rp:
            got = gops.vless_mode(self.w.runner, "tls", yes=True)
        self.assertTrue(got["ok"])
        self.assertEqual(rp.call_count, 1)                                      # only the outermost entry


class LockTest(_Case):
    def test_two_threads_of_one_process(self):
        """С-2: re-entry is by (process, thread): another thread of the panel gets «busy»."""
        inside, done, seen = threading.Event(), threading.Event(), []

        def holder():
            with front.vless_guard(self.w.runner):
                inside.set()
                done.wait(10)
        t = threading.Thread(target=holder)
        t.start()
        try:
            inside.wait(10)
            with self.assertRaises(errors.ConflictError) as cm:
                gops.vless_mode(self.w.runner, "tls", yes=True)
            seen.append(cm.exception.data["need"])
        finally:
            done.set()
            t.join(10)
        self.assertEqual(seen, ["busy"])
        self.assertEqual(self.rec()["security"], "reality")

    def test_nested_entries_of_one_thread(self):
        with front.vless_guard(self.w.runner):
            with front.vless_guard(self.w.runner):
                self.assertTrue(front.guard_held())
            self.assertTrue(front.guard_held())
        self.assertFalse(front.guard_held())


class RealityTest(_Case):
    def test_without_the_consent_nothing_changes(self):
        site_ops.mode(self.w.runner, "site", yes=True)                          # TLS
        before = raw()
        rc, doc, _ = self.run_json("gw", "vless", "mode", "reality", "--site", "www.microsoft.com", "--no-check")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertEqual((doc["data"]["need"], doc["data"]["risks"], doc["data"]["site"]),
                         ("reality_consent", "r1a", "www.microsoft.com"))
        self.assertTrue(doc["data"]["block"])
        self.assertEqual(raw(), before)

    def test_with_the_consent(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        rc, doc, err = self.run_json("gw", "vless", "mode", "reality", "--site", "www.microsoft.com", "--no-check",
                                     "--accept-risks", "--risks", "r1a", "--via", "panel", "--yes")
        self.assertEqual(rc, 0, err)
        rec = self.rec()
        self.assertEqual((rec["security"], rec["port"], rec["want"]), ("reality", 8443, "reality"))
        c = rec["reality_consent"]
        self.assertEqual((c["via"], c["risks"], c["site"], c["domain"]), ("panel", "r1a", "www.microsoft.com",
                                                                          F.DOMAIN))
        self.assertTrue(front.consent_ok(rec))
        self.assertEqual(self.site_mode(), "site")

    def test_panel_mcp_setup_must_name_the_text(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        for extra in ([], ["--risks", "r1b"]):
            rc, doc, _ = self.run_json("gw", "vless", "mode", "reality", "--site", "www.microsoft.com", "--no-check",
                                       "--accept-risks", "--via", "mcp", "--yes", *extra)
            self.assertEqual(rc, errors.CONFLICT)
            self.assertEqual(doc["data"]["need"], "reality_consent")
        rc, doc, _ = self.run_json("gw", "vless", "mode", "reality", "--site", "auto", "--accept-risks", "--risks",
                                   "r1a", "--via", "mcp", "--yes")
        self.assertEqual(rc, errors.CONFLICT)                                   # З-5: «auto» with a consent — no
        rc, doc, err = self.run_json("gw", "vless", "mode", "reality", "--site", "www.microsoft.com", "--no-check",
                                     "--accept-risks", "--risks", "r1a", "--via", "mcp", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.rec()["reality_consent"]["who"], "mcp")

    def test_forced_reality_changes_the_cover_only(self):
        """Н2: TLS impossible now (443 held) -> the cover changes, ``want`` stays «tls», no consent written."""
        self.neighbour()
        rc, doc, err = self.run_json("gw", "vless", "mode", "reality", "--site", "dl.google.com", "--no-check")
        self.assertEqual(rc, 0, err)
        rec = self.rec()
        self.assertEqual((rec["sni"], rec["want"]), ("dl.google.com", "tls"))
        self.assertNotIn("reality_consent", rec)
        self.assertIn("не записан", doc["note"])

    def test_no_domain(self):
        site_ops.clear(self.w.runner, yes=True)
        self.put(want="reality", reality_consent=dict(GOOD))
        gops.vless_mode(self.w.runner, "reality")
        self.assertEqual(self.rec()["want"], "reality")                         # a consent given before stays
        self.put(want="tls")
        self.rec().pop("reality_consent", None)
        gops.vless_mode(self.w.runner, "reality")
        self.assertEqual(self.rec()["want"], "reality_nodomain")

    def test_legacy_keeps_reality_without_new_links(self):
        """З-6: a record of 3.3.7 on Reality chooses Reality with the same cover — no --yes, the link the same."""
        self.put(want=None)
        set_rec = state.load()
        model.inbound(set_rec, "vless").pop("want", None)
        state.save(set_rec)
        self.site_next_to_reality()
        before = self.link(self.dev["id"])
        got = gops.vless_mode(self.w.runner, "reality", site="www.microsoft.com", accept=True, risks="r1a",
                              via="panel", no_check=True)
        self.assertFalse(got["changed"])
        self.assertEqual(self.link(self.dev["id"]), before)
        self.assertEqual(self.rec()["want"], "reality")


class SitePriceTest(_Case):
    def test_site_off_at_tls_asks_with_the_short_risks(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.put(mask="list", sni="www.microsoft.com", dest="www.microsoft.com:443")    # the kept cover
        before = raw()
        rc, doc, _ = self.run_json("site", "mode", "domain")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("www.microsoft.com", doc["data"]["text"])
        self.assertIn("Reality выдаёт узел за другой сайт", doc["data"]["text"])
        self.assertEqual(raw(), before)
        rc, doc, err = self.run_json("site", "mode", "domain", "--yes")
        self.assertEqual(rc, 0, err)
        rec = self.rec()
        self.assertEqual((rec["security"], rec["want"]), ("reality", "tls"))
        self.assertNotIn("reality_consent", rec)
        self.assertEqual(front.reason(state.load())["reason"], "no_site")

    def test_need_pick(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.put(mask="own", sni=F.DOMAIN)                                      # no cover kept
        with self.assertRaises(errors.ConflictError) as cm:
            site_ops.mode(self.w.runner, "domain", yes=True)
        self.assertTrue(cm.exception.data["need_pick"])
        with mock.patch("kaskad.gw.inbounds.vless.checked", return_value={"ok": True, "why": ""}):
            site_ops.mode(self.w.runner, "domain", yes=True, vless_site="dl.google.com")
        self.assertEqual(self.rec()["sni"], "dl.google.com")

    def test_site_on_with_devices_asks_through_every_door(self):
        rc, doc, _ = self.run_json("site", "mode", "site")
        self.assertEqual((rc, doc["data"]["need"]), (errors.CONFLICT, "vless_change"))
        with self.assertRaises(errors.ConflictError):
            site_ops.mode(self.w.runner, "site")
        self.assertEqual(self.site_mode(), "domain")


class FindingsTest(_Case):
    """REV-vless-sh1: З-1, З-2, придирки 5, 6, 8."""

    def test_make_room_writes_tls_error(self):
        front.make_room(self.w.runner, "nginx: [emerg] /etc/nginx/x.conf AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        rec = self.rec()
        self.assertNotIn("/etc/nginx", rec["tls_error"]["why"])
        self.assertNotIn("A" * 32, rec["tls_error"]["why"])
        st = state.load()
        st.setdefault("site", {})["mode"] = "site"                              # the site took 443 itself after it
        self.assertEqual(front.reason(st)["reason"], "tls_failed")              # not «ready»
        with mock.patch.object(front, "devices_of", return_value=[]):           # an implicit sync does not try TLS
            p = front.plan(st, self.w.runner)
        self.assertEqual((p["reason"], p["security"]), ("tls_failed", "reality"))

    def test_broken_consent_own_mask_without_the_site(self):
        self.put(want="reality", reality_consent={}, mask="own", sni=F.DOMAIN, dest=F.DOMAIN + ":443", port=8443)
        p = front.plan(state.load(), self.w.runner, pick=lambda _s: {"sni": "dl.google.com", "mask": "list"})
        self.assertEqual(p["reason"], "consent_bad")
        self.assertEqual(p["fields"].get("sni"), "dl.google.com")               # a working cover, the mode the same
        self.assertNotIn("security", p["fields"])

    def test_same_moves_reality_off_443_when_the_site_holds_it(self):
        st = state.load()
        st.setdefault("site", {})["mode"] = "site"
        model.inbound(st, "vless").update(want="reality", reality_consent={})
        p = front.plan(st, self.w.runner)
        self.assertEqual((p["reason"], p["fields"].get("port", "x")), ("consent_bad", None))

    def test_legacy_reason_is_consistent(self):
        self.put(tls_error={"at": "x", "why": "y"})
        st = state.load()
        model.inbound(st, "vless").pop("want")
        st.setdefault("site", {})["mode"] = "site"
        self.assertNotEqual(front.reason(st, None)["reason"], "tls_failed")

    def test_risks_fullmatch(self):
        self.assertFalse(front.consent_ok({"reality_consent": dict(GOOD, risks="r1a\n")}))


class ImportTest(_Case):
    def ctx(self, domain):
        c = mock.Mock()
        c.state = {"site": {"domain": domain}}
        c.notes = []
        c.parts, c.exact = backup.PARTS, False
        return c

    def test_the_consent_comes_only_for_the_same_domain(self):
        rec = {"want": "reality", "reality_consent": dict(GOOD), "tls_error": {"at": "x"}, "mode_pending": {"at": "x"}}
        got = backup._vless_import(rec, self.ctx(F.DOMAIN))
        self.assertEqual((got["want"], got["reality_consent"]["domain"]), ("reality", F.DOMAIN))
        self.assertNotIn("tls_error", got)
        self.assertNotIn("mode_pending", got)
        c = self.ctx("other.example.com")
        got = backup._vless_import(rec, c)
        self.assertEqual(got["want"], "tls")                                    # «no choice» (§3.3)
        self.assertNotIn("reality_consent", got)
        self.assertTrue(c.notes)


class PriceToRealityTest(_Case):
    """SEC-vless-sh12 №4 (З-4): TLS → Reality changes the links — devices have them: without ``yes`` CONFLICT
    ``vless_change`` from the core, state byte for byte the same; with it — Reality."""

    def test_tls_to_reality_asks_the_price(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        before = raw()
        rc, doc, _ = self.run_json("gw", "vless", "mode", "reality", "--site", "www.microsoft.com", "--no-check",
                                   "--accept-risks", "--risks", "r1a")
        self.assertEqual(rc, errors.CONFLICT, doc)
        self.assertEqual(doc["data"]["need"], "vless_change")
        self.assertIn("Телефон", doc["data"]["text"])
        self.assertEqual(raw(), before)
        with self.assertRaises(errors.ConflictError) as e:                      # the core itself, not the CLI
            gops.vless_mode(self.w.runner, "reality", site="www.microsoft.com", no_check=True, accept=True,
                            risks="r1a")
        self.assertEqual(e.exception.data["need"], "vless_change")
        self.assertEqual(raw(), before)
        rc, doc, err = self.run_json("gw", "vless", "mode", "reality", "--site", "www.microsoft.com", "--no-check",
                                     "--accept-risks", "--risks", "r1a", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.rec()["security"], self.rec()["want"]), ("reality", "reality"))


class SyncLegacyTest(F.FrontCase):
    """SEC-vless-sh12 №5 (З-5, P11): ``gw vless sync`` (MCP ``vless_sync``) never changes the links devices have on its
    own — a record of 3.3.7 next to the site gets a recommendation; the switch is ``vless_mode`` with its price."""

    def legacy_next_to_the_site(self):
        from tests.test_vless_want import set_want
        self.site_next_to_reality()                                             # Reality 8443 next to the site
        set_want(None)                                                          # a record of 3.3.7

    def test_p11_with_devices_a_recommendation_only(self):
        self.legacy_next_to_the_site()
        before = raw()
        rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["changed"], doc["security"], doc["recommend"]), (False, "reality", "tls"))
        self.assertIn("gw vless mode tls", doc["why"])
        self.assertEqual(doc["reissue"], [])
        self.assertEqual(raw(), before)

    def test_p11_without_devices_it_switches(self):
        self.legacy_next_to_the_site()
        st = state.load()
        for d in model.devices(st):
            d["access"] = [k for k in d.get("access") or [] if k != "vless"]
        state.save(st)
        rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["changed"], self.rec()["security"]), (True, "tls"))

    def test_the_mcp_words_say_so(self):
        a = json.load(open(os.path.join(os.path.dirname(backup.__file__), "mcp", "actions.json"),
                           encoding="utf-8"))["actions"]["vless_sync"]["description"]
        self.assertIn("vless_mode", a["ru"])
        self.assertIn("vless_mode", a["en"])


class SiteModeViaTest(_Case):
    def test_site_mode_via_mcp_in_the_journal(self):
        """SEC-vless-sh12 придирка 3: R→T started by MCP ``site_mode`` is written as mcp, not cli."""
        rc, _, err = self.run_json("site", "mode", "site", "--yes", "--via=mcp")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.rec()["security"], "tls")
        lines = [l for l in self.journal().splitlines() if "VLESS" in l and "mcp" in l]
        self.assertTrue(lines, self.journal()[-600:])


class ConsentVariantTest(WantCase):
    """SEC-vless-sh12 №7 (решение координатора З-7): a consent to the own site (r1b) is not one to a foreign cover."""

    OWN = dict(GOOD, risks="r1b", site="own")

    def setUp(self):
        super().setUp()
        self.site_next_to_reality()                                             # Reality 8443 next to the site
        st = state.load()
        model.inbound(st, "vless").update(want="reality", reality_consent=dict(self.OWN), mask="own", sni=F.DOMAIN,
                                          dest="%s:443" % F.DOMAIN)
        state.save(st)
        self.assertEqual(front.reason(state.load(), self.w.runner)["reason"], "chosen")

    def test_a_foreign_cover_asks_the_text_of_variant_a(self):
        before = raw()
        rc, doc, _ = self.run_json("gw", "vless", "mask", "www.microsoft.com", "--no-check")
        self.assertEqual(rc, errors.CONFLICT, doc)
        self.assertEqual((doc["data"]["need"], doc["data"]["risks"], doc["data"]["site"]),
                         ("reality_consent", "r1a", "www.microsoft.com"))
        self.assertIn("www.microsoft.com", doc["data"]["block"])
        self.assertEqual(raw(), before)
        rc, doc, _ = self.run_json("gw", "vless", "mask", "www.microsoft.com", "--no-check", "--accept-risks",
                                   "--risks", "r1b", "--via", "panel")
        self.assertEqual(rc, errors.CONFLICT, doc)                              # the text of variant B is not it
        self.assertEqual(raw(), before)
        rc, doc, err = self.run_json("gw", "vless", "mask", "www.microsoft.com", "--no-check", "--accept-risks",
                                     "--risks", "r1a")
        self.assertEqual(rc, 0, err)
        c = self.rec()["reality_consent"]
        self.assertEqual((c["risks"], c["site"], self.rec()["want"], self.rec()["sni"]),
                         ("r1a", "www.microsoft.com", "reality", "www.microsoft.com"))
        self.assertEqual(front.reason(state.load(), self.w.runner)["reason"], "chosen")
        self.assertIn("новое согласие", self.journal())

    def test_the_own_site_again_needs_nothing(self):
        rc, doc, err = self.run_json("gw", "vless", "mask", "own", "--no-check")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.rec()["reality_consent"]["risks"], "r1b")

    def test_a_consent_to_variant_a_covers_any_foreign_site(self):
        st = state.load()
        model.inbound(st, "vless").update(reality_consent=dict(GOOD), mask="list", sni="www.microsoft.com")
        state.save(st)
        rc, doc, err = self.run_json("gw", "vless", "mask", "dl.google.com", "--no-check")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.rec()["reality_consent"]["risks"], "r1a")

    def test_a_cover_picked_by_the_system_takes_the_consent_away(self):
        """The site off: the own cover goes, a foreign one comes without the person asked — «no choice», not chosen."""
        site_ops.mode(self.w.runner, "domain", yes=True, vless_site="www.microsoft.com")
        rec = self.rec()
        self.assertNotEqual(rec.get("mask"), "own")
        self.assertNotIn("reality_consent", rec)
        self.assertEqual(rec["want"], "tls")
        self.assertNotEqual(front.reason(state.load(), self.w.runner)["reason"], "chosen")
        self.assertIn("согласие снято", self.journal())

    def test_the_panel(self):
        call = SecretsAndPanelTest.call.__get__(self)
        before = raw()
        status, doc, _ = self.call_mask(call, {"site": "www.microsoft.com", "check": False, "accept_risks": True})
        self.assertEqual(status, 400, doc)                                      # risks of the text shown: required
        status, doc, _ = self.call_mask(call, {"site": "www.microsoft.com", "check": False})
        self.assertEqual((status, doc["data"]["need"], doc["data"]["risks"]), (409, "reality_consent", "r1a"))
        self.assertEqual(raw(), before)
        status, doc, _ = self.call_mask(call, {"site": "www.microsoft.com", "check": False, "accept_risks": True,
                                               "risks": "r1a"})
        self.assertEqual(status, 200, doc)
        self.assertEqual((self.rec()["reality_consent"]["via"], self.rec()["reality_consent"]["risks"]), ("panel", "r1a"))

    def test_the_menu_asks_too(self):
        """The SSH menu: the refusal «reality_consent» shows the block and asks; the answer — the text shown (risks)."""
        from tests.test_ssh_live332 import function
        body = function("k3_gw_vless_mask_set")
        self.assertIn("reality_consent", body)
        self.assertIn('k3_gw_vless_mask_consent "$site" "${extra[@]}"', body)  # «Взять без проверки» asks too
        self.assertIn("extra+=(--no-check)", body)
        ask = function("k3_gw_vless_mask_consent")
        self.assertIn('--accept-risks --risks "$risks" --via ssh', ask)
        self.assertIn(".data.block", ask)

    @staticmethod
    def call_mask(call, body):
        return call("PUT", "/api/gw/vless/mask", body)

    def test_mcp_asks_the_human(self):
        a = json.load(open(os.path.join(os.path.dirname(front.__file__), "..", "mcp", "actions.json")))["actions"]
        vm = a["vless_mask"]
        self.assertEqual((vm["args"], vm["ask_human"]), (["--via=mcp"], True))
        self.assertEqual(vm["params"]["accept_risks"]["phrase_block"], "vless")
        from tests.test_mcp import McpCase, call
        case = McpCase("run_session")
        case.setUp()
        self.addCleanup(case.tearDown)
        s = case.run_session([call(1, "vless_mask", {"site": "dl.google.com", "confirm": "vless_mask",
                                                     "accept_risks": "да"})])
        e = s.error(1)
        self.assertEqual((e["data"]["risks"], e["data"]["site"]), ("r1a", "dl.google.com"))
        self.assertEqual(s.calls, [])


class NitsTest(_Case):
    """SEC-vless-sh12 придирки 2 и 5."""

    def test_clean_why_hides_every_path(self):
        got = front.clean_why("load [/opt/kaskad/gw/xray/config.json] failed; ~/x ./opt/kaskad /proc/1/environ /sys/x "
                              "~root/.ssh/id ../up/f /dev/shm/k key 'yBkXk1pZr9t6nQ2vW8sL3mH5jC0aE7fD4gU1iO6pT2s'")
        for bad in ("/opt", "~/x", "./opt", "/proc", "/sys", "~root", "../up", "/dev", "yBkXk1"):
            self.assertNotIn(bad, got)
        self.assertIn("[<file>]", got)                                         # the bracket after it stays
        self.assertIn("TLS 1.3/HTTP2", front.clean_why("TLS 1.3/HTTP2 refused"))

    def test_p6_uninstall_waits_for_no_transition(self):
        self.hold_lock()
        before = raw()
        rc, doc, _ = self.run_json("gw", "inbound", "uninstall", "vless")
        self.assertEqual(rc, errors.CONFLICT, doc)
        self.assertEqual(raw(), before)

    def test_p6_uninstall_undoes_a_transition_left_behind_first(self):
        want = self.rec()["sni"]
        self.left_pending()
        rc, _, err = self.run_json("gw", "inbound", "uninstall", "vless")
        self.assertEqual(rc, 0, err)
        kept = model.kept_inbound(state.load(), "vless") or {}
        self.assertNotIn("mode_pending", kept)
        self.assertEqual(kept["sni"], want)


class ImportCopyTest(_Case):
    """SEC-vless-sh12 №2: the real ``backup.apply`` — an import of some parts and the kept record of a removed VLESS."""

    def setUp(self):
        super().setUp()
        from tests.test_backup import FakeOpenssl
        self.w.runner.binaries.add("openssl")
        self.w.runner.on(["openssl"], FakeOpenssl(self.w.runner))
        for p in (mock.patch.object(backup, "ITER", 100000),
                  mock.patch.object(backup, "_here_ip", lambda runner: state.load()["gateway"].get("host") or "")):
            p.start()
            self.addCleanup(p.stop)

    def copy(self, edit=None):
        from tests.test_backup import PW
        doc = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)
        if edit is not None:
            stj = json.loads(doc["files"][backup.F_STATE])
            edit(stj)
            doc["files"][backup.F_STATE] = json.dumps(stj)
        return doc

    def test_p1_the_gateway_alone_takes_the_domain_of_the_node(self):
        self.put(want="reality", reality_consent=dict(GOOD))
        doc = self.copy()
        st = state.load()
        st["site"]["domain"] = "other.example.com"                             # this node: another domain
        state.save(st)
        res = backup.apply(self.w.runner, doc, parts=["gateway"])
        self.assertEqual(res["parts"], ["gateway"])
        rec = self.rec()
        self.assertEqual(smodel.domain_of(state.load()), "other.example.com")
        self.assertNotIn("reality_consent", rec)
        self.assertEqual(rec.get("want"), "tls")
        self.assertNotEqual(front.reason(state.load(), self.w.runner)["reason"], "chosen")

    def test_p1_the_same_domain_keeps_the_consent(self):
        self.put(want="reality", reality_consent=dict(GOOD))
        backup.apply(self.w.runner, self.copy(), parts=["gateway"])
        self.assertEqual((self.rec()["want"], self.rec()["reality_consent"]["domain"]), ("reality", F.DOMAIN))

    def kept_copy(self, **extra):
        def edit(stj):
            g = stj["gateway"]
            rec = g["inbounds"].pop("vless")
            rec.pop("enabled", None)
            rec.update(extra)
            g.setdefault("removed_inbounds", {})["vless"] = rec
        return self.copy(edit)

    def test_p10b_the_kept_record_never_brings_a_consent(self):
        doc = self.kept_copy(want="reality", reality_consent=dict(GOOD, domain="a.example.com"))
        backup.apply(self.w.runner, doc)
        kept = model.kept_inbound(state.load(), "vless") or {}
        for k in ("want", "reality_consent", "mode_pending", "tls_error"):
            self.assertNotIn(k, kept)
        rc, _, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        rec = self.rec()
        self.assertNotIn("reality_consent", rec)
        self.assertNotEqual(rec.get("want"), "reality")
        self.assertNotEqual(front.reason(state.load(), self.w.runner)["reason"], "chosen")

    def test_q3_the_undo_of_this_node_keeps_the_choice_of_the_removed_vless(self):
        """REV-vless-sh2 заметно 2: a snapshot of this very node (the undo of an import, ``backup undo``) comes back
        whole — the kept record of a removed VLESS keeps its choice and consent; only a transition and its error go."""
        self.put(want="reality", reality_consent=dict(GOOD), tls_error={"at": "x", "why": "old"})
        rc, _, err = self.run_json("gw", "inbound", "uninstall", "vless")
        self.assertEqual(rc, 0, err)
        doc = self.copy()
        for kw in ({"exact": True}, {"same_node": True}):
            backup.apply(self.w.runner, doc, **kw)
            kept = model.kept_inbound(state.load(), "vless") or {}
            self.assertEqual((kept.get("want"), (kept.get("reality_consent") or {}).get("domain")),
                             ("reality", F.DOMAIN), kw)
            self.assertNotIn("tls_error", kept)
            self.assertNotIn("mode_pending", kept)

    def test_x3_a_snapshot_without_a_domain_drops_the_consent(self):
        """REV-vless-sh2 X3: the snapshot of the node when it had no domain (``exact``) — the domain after is none, and
        a consent naming a domain is not taken (whatever domain the node has now)."""
        self.put(want="reality", reality_consent=dict(GOOD))

        def edit(stj):
            stj["site"]["domain"] = ""
            stj["site"]["mode"] = "off"
        doc = self.copy(edit)
        backup.apply(self.w.runner, doc, exact=True)
        rec = self.rec()
        self.assertNotIn("reality_consent", rec)
        self.assertNotEqual(rec.get("want"), "reality")

    def test_p10_a_snapshot_of_the_copy_is_never_applied(self):
        snap = {"rec": {"security": "reality", "port": 443, "sni": "evil.example.net", "dest": "evil.example.net:443",
                        "mask": "custom"}, "site": "domain"}
        doc = self.kept_copy(want="reality", reality_consent=dict(GOOD, domain="a.example.com"),
                             tls_error={"at": "x", "why": "copy"},
                             mode_pending={"at": "2026-01-01T00:00:00Z", "pid": 1, "snap": snap})
        backup.apply(self.w.runner, doc)
        kept = model.kept_inbound(state.load(), "vless") or {}
        self.assertNotIn("mode_pending", kept)
        self.assertNotIn("tls_error", kept)
        rc, _, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        rec = self.rec()
        self.assertNotEqual(rec.get("sni"), "evil.example.net")
        self.assertNotIn("mode_pending", rec)
        self.assertNotIn("откачена", self.journal())

    def test_install_never_applies_a_snapshot_of_a_kept_record(self):
        """The same guard at the install itself: a kept record that has ``mode_pending`` (written by older code)."""
        st = state.load()
        rec = model.inbound(st, "vless")
        model.keep_inbound(st, "vless", dict(rec, mode_pending={"at": "x", "pid": 1, "snap": {
            "rec": dict(rec, sni="evil.example.net", dest="evil.example.net:443", mask="custom"), "site": "domain"}}))
        del st["gateway"]["inbounds"]["vless"]
        state.save(st)
        rc, _, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        self.assertNotEqual(self.rec().get("sni"), "evil.example.net")
        self.assertNotIn("mode_pending", self.rec())


class InternalCallsTest(ImportCopyTest):
    """SEC-vless-sh12 №3 (§3.7 «внутренние вызовы передают yes»): the import, the undo of a domain switch, the removal
    and the setup are actions of the system — never stuck on the price; no kept cover — the core picks one."""

    def tls_own(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.put(mask="own", sni=F.DOMAIN)                                     # TLS, no kept cover: need_pick
        self.assertEqual(self.rec()["security"], "tls")
        self.assertTrue(front.predict(state.load(), self.w.runner, "domain", drop_domain=True)["need_pick"])

    def test_p2b_a_copy_with_another_domain(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        doc = self.copy(lambda stj: stj["site"].update(domain="other.example.com"))
        res = backup.apply(self.w.runner, doc)
        self.assertTrue(res["ok"])
        self.assertEqual(smodel.domain_of(state.load()), "other.example.com")

    def test_p2b_with_no_kept_cover(self):
        self.tls_own()
        doc = self.copy(lambda stj: stj["site"].update(domain="other.example.com"))
        self.assertTrue(backup.apply(self.w.runner, doc)["ok"])
        rec = self.rec()
        self.assertEqual(smodel.domain_of(state.load()), "other.example.com")
        self.assertNotEqual(rec.get("sni"), F.DOMAIN)                          # never the cover of the domain gone

    def test_p3_the_site_removed_whole(self):
        from kaskad.site import nginx
        self.tls_own()
        got = site_ops.remove(self.w.runner)
        self.assertTrue(got["ok"])
        self.assertNotIn("site", state.load())
        self.assertFalse(os.path.lexists(nginx.enabled_path()))
        self.assertFalse(os.path.lexists(nginx.vhost_path()))
        rec = self.rec()
        self.assertEqual(rec["security"], "reality")
        self.assertNotEqual(rec.get("sni"), F.DOMAIN)
        self.assertNotIn("mode_pending", rec)

    def test_p3_through_the_cli_of_the_removal(self):
        self.tls_own()
        rc, doc, err = self.run_json("site", "remove")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("site", state.load())

    def test_p12_a_domain_switch_and_its_undo(self):
        self.tls_own()
        site_ops.clear(self.w.runner, yes=True, vless_auto=True)
        self.assertEqual(self.rec()["security"], "reality")
        self.assertNotEqual(self.rec().get("sni"), F.DOMAIN)

    def test_p12_a_domain_switch_is_never_stuck(self):
        self.tls_own()
        s = dict(smodel.peek(state.load()), mode="domain")     # the site of the new name is not this test's (its cert)
        with mock.patch.object(site_ops, "issue", return_value={"consumers": []}):
            got = site_ops._switch(self.w.runner, s, "new.example.com", {"state": "ok"})
        self.assertTrue(got["ok"])
        self.assertEqual(smodel.domain_of(state.load()), "new.example.com")

    def test_p12_the_switch_back_is_never_stuck(self):
        self.tls_own()
        problems = site_ops._switch_back(self.w.runner, dict(smodel.peek(state.load()), domain="old.example.com"),
                                         F.DOMAIN)
        self.assertFalse([p for p in problems if "VLESS" in p or "устройств" in p], problems)
        self.assertNotIn(F.DOMAIN, smodel.domain_of(state.load()))             # the name of the failed switch went

    def test_p12_a_person_still_names_the_cover(self):
        """Not weakened: a person's ``yes`` without a cover to name is still asked for one."""
        self.tls_own()
        before = raw()
        with self.assertRaises(errors.ConflictError) as e:
            site_ops.clear(self.w.runner, yes=True)
        self.assertTrue(e.exception.data["need_pick"])
        self.assertEqual(raw(), before)

    def test_the_setup_clears_the_domain(self):
        """REV-vless4 «было до» 3: «Убрать домен» of the wizard is an action of a person — VLESS on TLS with devices
        goes to Reality only after the price was shown: without ``yes`` of the request a refusal with the price (and
        the step to send again with ``yes``, as the step «site»), nothing changed; ``yes`` only JSON true. The cover
        is picked by the core (``vless_auto``) — the text says so, not «the one you choose»."""
        from kaskad.setup import steps
        self.tls_own()
        before = raw()
        c = mock.Mock()
        c.runner = self.w.runner
        # 3.3.7.5: the wizard's clear keeps a way in (way_in=True); here the panel is not on the domain — the port stays
        with mock.patch.object(site_ops, "people_links", return_value={"domain": "", "count": 0, "port": True}):
            for body in ({"clear": True}, {"clear": True, "yes": "true"}, {"clear": True, "yes": 1}):
                c.body = body
                got = steps.s_domain(c)
                self.assertEqual((got["ok"], got["error"]["code"], got["data"]["need"]), (False, "conflict", "vless_change"),
                                 body)
                self.assertEqual(got["error"]["action"]["step"], "domain")
                self.assertIs(got["error"]["action"]["yes"], True)
                self.assertTrue(got["error"]["action"]["clear"])
                self.assertIn("выдать заново", got["text"])
                self.assertIn("сайт, который подберёт узел", got["text"])
                self.assertNotIn("который вы выберете", got["text"])
                self.assertIn("снова задайте домен", got["text"])              # REV-vless-fix З2: the way back
                self.assertNotIn("снова включите сайт", got["text"])
                self.assertEqual(raw(), before, body)                        # nothing changed: domain, VLESS, port
            c.body = {"clear": True, "yes": True}
            got = steps.s_domain(c)
        self.assertEqual((got["ok"], got["state"]), (True, "ok"), got)
        self.assertEqual(smodel.domain_of(state.load()), "")
        self.assertEqual(self.rec()["security"], "reality")
        self.assertNotEqual(self.rec().get("sni"), F.DOMAIN)

    def test_the_setup_clears_without_devices_at_once(self):
        """No devices with VLESS — nobody to give links again: no question, the domain goes at once."""
        from kaskad.setup import steps
        self.tls_own()
        self.ok("gw", "device", "del", self.dev["id"])
        c = mock.Mock()
        c.runner, c.body = self.w.runner, {"clear": True}
        with mock.patch.object(site_ops, "people_links", return_value={"domain": "", "count": 0, "port": True}):
            got = steps.s_domain(c)
        self.assertEqual((got["ok"], got["state"]), (True, "ok"), got)
        self.assertEqual(smodel.domain_of(state.load()), "")


class McpTest(F.FrontCase):
    def test_the_schema(self):
        a = json.load(open(os.path.join(os.path.dirname(front.__file__), "..", "mcp", "actions.json")))["actions"]
        vm = a["vless_mode"]
        self.assertEqual((vm["args"], vm["ask_human"], vm.get("destructive")), (["--via=mcp"], True, False))
        self.assertNotIn("via", vm["params"])
        self.assertEqual(vm["params"]["accept_risks"]["phrase_block"], "vless")
        self.assertTrue(a["site_mode"]["ask_human"])
        self.assertIn("yes", a["site_mode"]["params"])
        self.assertNotIn("--yes", a["site_mode"].get("args") or [])            # yes — only from the human's answer
        self.assertEqual(a["site_mode"]["args"], ["--via=mcp"])                 # SEC-vless-sh12 придирка 3
        dc = a["site_domain_clear"]                                             # З-6: a path through the price
        self.assertTrue(dc["ask_human"])
        self.assertEqual((dc["params"]["yes"]["flag"], dc["params"]["vless_site"]["flag"]), ("--yes", "--vless-site"))
        self.assertNotIn("--yes", dc["args"])
        # Ш2-б: --no-check, как у vless_mask (сайт Reality не проходит проверку по известной человеку причине)
        self.assertEqual(vm["params"]["no_check"], {"ref": "mask_no_check", "flag": "--no-check"})

    def test_site_domain_clear_and_site_mode_through_mcp(self):
        """З-6: ``yes``/``vless_site`` reach ``site domain --clear``; ``site_mode`` is written to the journal as mcp."""
        from tests.test_mcp import McpCase, call
        case = McpCase("run_session")
        case.setUp()
        self.addCleanup(case.tearDown)
        s = case.run_session([call(1, "site_domain_clear", {"confirm": "site_domain_clear", "yes": True,
                                                            "vless_site": "www.microsoft.com"}),
                              call(2, "site_mode", {"mode": "site", "yes": True})])
        self.assertEqual([c["argv"] for c in s.calls],
                         [["site", "domain", "--yes", "--vless-site=www.microsoft.com", "--clear", "--json"],
                          ["site", "mode", "site", "--yes", "--via=mcp", "--json"]])

    def test_a_wrong_phrase_gets_the_block_of_reality_with_the_site_of_the_call(self):
        from tests.test_mcp import McpCase, call
        case = McpCase("run_session")
        case.setUp()
        self.addCleanup(case.tearDown)
        s = case.run_session([call(1, "vless_mode", {"mode": "reality", "site": "dl.google.com",
                                                     "accept_risks": "да, понимаю"}),
                              call(2, "rules_default", {"profile": "ru_direct", "confirm_not_rf": "yes"})])
        e = s.error(1)
        self.assertEqual(e["code"], -32602)
        self.assertEqual((e["data"]["risks"], e["data"]["site"]), ("r1a", "dl.google.com"))     # З-3: the site of
        self.assertIn("dl.google.com", e["data"]["block"])                                        # the call
        self.assertIn(e["data"]["block"], e["message"])
        self.assertNotIn("orgs", e["data"])
        self.assertNotIn("⚠", e["message"])
        self.assertIn("⚠", json.dumps(s.error(2), ensure_ascii=False))           # ⚠РФ keeps its own block
        self.assertEqual(s.calls, [])


class SecretsAndPanelTest(_Case):
    """§6 Ш2: secrets not in argv or the log; the panel's routes — admin session and CSRF, the same refusals as the core."""

    def call(self, method, path, body=None):
        import kaskad.panel.api  # noqa: F401 — registers the routes
        import kaskad.site.panel  # noqa: F401
        from kaskad.panel import api, routes
        from kaskad.panel.routes import HttpError
        from kaskad.panel.routes import Request
        r, m = routes.find(method, path)
        self.assertNotIn(r, (None, "method"), path)
        req = Request(method, path, body=json.dumps(body).encode("utf-8") if body is not None else b"", match=m,
                      session={"admin": True}, client_ip="127.0.0.1")
        with mock.patch.object(api, "RUNNER", self.w.runner):
            try:
                resp = r.handler(req)
            except HttpError as e:
                resp = routes.error_response(e)
        return resp.status, json.loads(resp.body.decode("utf-8")), r

    def test_no_secret_in_argv_or_the_log(self):
        from kaskad import secrets
        rc, _, err = self.run_json("gw", "vless", "mode", "tls", "--yes")                      # R→T
        self.assertEqual(rc, 0, err)
        rc, _, err = self.run_json("gw", "vless", "mode", "reality", "--site", "www.microsoft.com", "--no-check",
                                   "--accept-risks", "--risks", "r1a", "--via", "panel", "--yes")   # T→R
        self.assertEqual(rc, 0, err)
        sec = secrets.load()
        hidden = [sec["vless.server.private"], sec["dev.%s.vless.uuid" % self.dev["id"]]]
        argv = " ".join(" ".join(map(str, a)) for a, _ in self.w.runner.calls)
        self.assertTrue(argv and self.journal())
        for s in hidden:
            self.assertTrue(s)
            self.assertNotIn(s, argv)
            self.assertNotIn(s, self.journal())

    def test_routes_need_the_admin_and_csrf(self):
        for method, path in (("POST", "/api/gw/vless/mode"), ("PUT", "/api/site/mode"), ("DELETE", "/api/site/domain")):
            from kaskad.panel import routes
            import kaskad.panel.api  # noqa: F401
            import kaskad.site.panel  # noqa: F401
            r, _ = routes.find(method, path)
            self.assertEqual((r.auth, r.csrf), ("admin", True), path)

    def test_the_panel_without_risks_is_400_and_changes_nothing(self):
        site_ops.mode(self.w.runner, "site", yes=True)                          # TLS
        before = raw()
        for body in ({"mode": "reality", "accept_risks": True, "site": "www.microsoft.com"},
                     {"mode": "reality", "accept_risks": True, "risks": "r1a"}):
            status, doc, _ = self.call("POST", "/api/gw/vless/mode", body)
            self.assertEqual(status, 400, doc)
        status, doc, _ = self.call("POST", "/api/gw/vless/mode", {"mode": "reality", "site": "www.microsoft.com"})
        self.assertEqual((status, doc["data"]["need"]), (409, "reality_consent"))
        self.assertEqual(raw(), before)

    def test_the_site_on_asks_the_price_like_the_core(self):
        """§3.7, З-3: the same refusal through the panel and a direct call of ``site.ops.mode``; ``yes`` goes through."""
        before = raw()
        with self.assertRaises(errors.ConflictError) as core:
            site_ops.mode(self.w.runner, "site")
        status, doc, _ = self.call("PUT", "/api/site/mode", {"mode": "site"})
        self.assertEqual((status, doc["data"]["need"]), (409, "vless_change"))
        self.assertEqual(doc["data"]["text"], core.exception.data["text"])
        self.assertEqual(raw(), before)
        status, doc, _ = self.call("PUT", "/api/site/mode", {"mode": "site", "yes": True})
        self.assertEqual(status, 200, doc)
        self.assertEqual((self.rec()["security"], self.site_mode()), ("tls", "site"))
