"""B7, круг 2 (REV-s1-b7, REV-s1-b7-sec): the mark of «what runs may not be the file» is no point of failure (С-1 = №1),
a failure before the xray lock closes too (Н-2), the last line of D-136 (``xray.ru_guard``, ``gw.ruguard``), the words
of the owner's pushes by their cause (№2), the mark that narrows (придирка 2), no second push after a restart of the
collector (придирка 3), the wait after ``kill`` (придирка 1), ``activating`` in ``ensure_unit`` (-sec П-1). The probes of
the reviewers (scratchpad rev-b7, rev-b7-sec) are here as tests."""

import errno
import json
import os
from unittest import mock

from kaskad import state
from kaskad.bot import monitor
from kaskad.errors import KaskadError
from kaskad.gw import collect, live, net, ops, xray
from kaskad.gw import sys as gsys
from kaskad.i18n import tr
from tests.gw.test_s1_b5 import CONF, REFUSED, direct_users
from tests.gw.test_s1_b7 import B7Case, UNIT

RUN_MARK = "/run/kaskad/gw/xray-pending.json"
ETC_MARK = "/etc/kaskad/gw/xray-pending.json"
CLOSED = "/etc/kaskad/gw/xray.closed.json"

try:                                           # the code before круг 2 has none: red by the behaviour, not the import
    from kaskad.gw import ruguard
except ImportError:                            # pragma: no cover
    ruguard = None


def enospc(*a, **kw):
    raise OSError(errno.ENOSPC, "No space left on device")


class R2Case(B7Case):
    def setUp(self):
        super().setUp()
        for name, fresh in (("MEM", collect.Mem()), ("LIVE", live.Live())):     # a new collector process
            p = mock.patch.object(collect, name, fresh)
            p.start()
            self.addCleanup(p.stop)

    def run_full(self, also_etc=False):
        """/run (and /etc/kaskad/gw with ``also_etc``) takes no write of the mark."""
        from kaskad import util
        real = util.atomic_write
        bad = [self.path(RUN_MARK)] + ([self.path(ETC_MARK)] if also_etc else [])

        def write(path, *a, **kw):
            if path in bad:
                enospc()
            return real(path, *a, **kw)
        return mock.patch.object(util, "atomic_write", side_effect=write)

    def conf_direct(self):
        return direct_users(json.loads(self.read(CONF))) if os.path.exists(self.path(CONF)) else None

    def watchdog(self):
        return ops.check(state.load(), self.w.runner, repair=True)

    def kinds(self, prefix="xray_ru_"):
        return [e["kind"] for e in self.events() if e["kind"].startswith(prefix)]


class MarkNoFailurePointTest(R2Case):
    def test_run_full_closes_and_the_watchdog_does_not_reopen(self):
        # -sec С-1 (Probe.test_run_full_watchdog_reopens_silently), REV-s1-b7 №1 (rev7d): no mark in /run — the file
        # still goes; the watchdog cannot start xray with direct rules; the owner hears of it
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        with self.run_full():
            data = self.raises(new)
        self.assertGreater(data.get("ru_closed", 0), 0)
        self.assertFalse(os.path.exists(self.path(CONF)))
        self.assertFalse(self.w.sysd.units[UNIT]["active"])
        self.watchdog()
        self.assertFalse(self.w.sysd.units[UNIT]["active"])                    # LoadCredential: no file
        events = []
        collect._ru_watch(data, events, lambda *_: None, self.w.runner)
        self.assertEqual([e["kind"] for e in events], ["xray_ru_closed"])

    def test_run_full_open_is_told_by_the_mark_in_etc(self):
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        self.unstoppable()
        with self.run_full():
            data = self.raises(new)
        self.assertGreater(data.get("ru_open", 0), 0)
        self.assertTrue(os.path.exists(self.path(ETC_MARK)))
        self.assertFalse(os.path.exists(self.path(CONF)))
        self.assertGreater(xray.ru_open(), 0)
        kinds = [p["kind"] for p in ops.check(state.load(), self.w.runner)]
        self.assertIn("gw_xray_ru_open", kinds)

    def test_no_place_for_the_mark_renames_the_file(self):
        # neither /run nor /etc takes the mark: the file of the closing is the mark (xray.closed.json)
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        self.unstoppable()
        with self.run_full(also_etc=True):
            data = self.raises(new)
        self.assertGreater(data.get("ru_open", 0), 0)
        self.assertFalse(os.path.exists(self.path(CONF)))
        self.assertTrue(os.path.exists(self.path(CLOSED)))
        self.assertTrue(xray.pending())                                         # its paths
        self.assertGreater(xray.ru_open(self.w.runner), 0)                      # the unit is not stopped
        self.assertIn("gw_xray_ru_open", [p["kind"] for p in ops.check(state.load(), self.w.runner)])
        events = []
        collect._ru_watch(data, events, lambda *_: None, self.w.runner)        # by the result, no mark
        self.assertEqual([e["kind"] for e in events], ["xray_ru_open"])
        # xray stops at last: the next call closes by the closed file, and it goes
        del self.w.sysd.fail["stop"]
        del self.w.sysd.units[UNIT]["unkillable"]
        data = self.raises(skip_rejected=True)
        self.assertGreater(data.get("ru_closed", 0), 0)
        self.assertFalse(os.path.exists(self.path(CLOSED)))
        self.assertIsNone(xray.pending())

    def test_a_good_config_is_applied_without_the_mark(self):
        # REV-s1-b7 №1 (а) (T1a): a change with nothing to close is applied though the mark cannot be written
        st = self.socks_world()
        new = json.loads(json.dumps(st))
        new["gateway"]["rules"]["block_torrent"] = True
        with self.run_full(also_etc=True):
            res = self.apply(new)
        self.assertTrue(res["changed"])
        self.assertTrue(self.w.sysd.units[UNIT]["active"])

    def test_consent_gone_config_good_run_full(self):
        # -sec RunFullNoRefusal: the config is fine, only /run is full — it is applied; no direct rule anywhere
        new, sec = self.without_consent()
        with self.run_full(also_etc=True):
            for _ in range(3):
                res = self.apply(new)
                self.watchdog()
        self.assertTrue(res["changed"] or res.get("restart_owed") is None)
        self.assertEqual(self.conf_direct(), [])
        self.assertTrue(self.w.sysd.units[UNIT]["active"])

    def test_the_watchdog_never_starts_a_file_of_direct_rules(self):
        # the file could not leave (EROFS): the watchdog does not start xray with it, it says why
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        with mock.patch.object(xray, "_take_file", return_value=False):
            data = self.raises(new)
        self.assertGreater(data.get("ru_open", 0), 0)
        self.assertTrue(self.conf_direct())
        self.assertFalse(self.w.sysd.units[UNIT]["active"])
        probs = self.watchdog()
        self.assertFalse(self.w.sysd.units[UNIT]["active"])
        self.assertIn(tr("gw_ops.check.xray_ru_file"), [p["detail"] for p in probs])
        self.assertNotIn("gw_service", [p["kind"] for p in probs if "xray" in p["detail"]])

    def test_the_watchdog_still_starts_a_safe_file(self):
        self.socks_world()
        self.w.runner.run(["systemctl", "stop", UNIT])
        self.watchdog()
        self.assertTrue(self.w.sysd.units[UNIT]["active"])


class BeforeTheLockTest(R2Case):
    def test_a_failure_of_the_binary_closes(self):
        # -sec Н-2 (PreLock): the copy of xray failed before the lock — the consent went: closed all the same
        new, sec = self.without_consent()
        with mock.patch.object(xray, "_install_binary",
                               side_effect=KaskadError("copy_failed: [Errno 28] No space left on device")):
            data = self.raises(new)
        self.assertGreater(data.get("ru_closed", 0), 0)
        self.assertFalse(self.w.sysd.units[UNIT]["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))

    def test_a_failure_of_the_binary_keeps_what_is_still_wanted(self):
        st = self.socks_world()
        with mock.patch.object(xray, "_install_binary", side_effect=KaskadError("copy_failed")):
            data = self.raises(st)
        self.assertNotIn("ru_closed", data)
        self.assertTrue(self.w.sysd.units[UNIT]["active"])                     # the render still wants them

    def test_a_failure_of_the_render_closes_everything_direct(self):
        st = self.socks_world()
        with mock.patch.object(xray, "render", side_effect=ValueError("boom")):
            with self.assertRaises(KaskadError) as e:
                self.apply(st)
        self.assertGreater(e.exception.data.get("ru_closed", 0), 0)
        self.assertFalse(self.w.sysd.units[UNIT]["active"])


class LastLineTest(R2Case):
    def test_a_config_with_direct_russia_without_the_consent_is_never_applied(self):
        # D-136: whatever code drew it (here a planted bug of the render), no direct path without the consent
        st = self.socks_world()
        planted = json.loads(self.read(CONF))
        new, sec = self.without_consent()
        real = xray.render

        def render(*a, **kw):
            cfg = real(*a, **kw)
            cfg["routing"]["rules"] = planted["routing"]["rules"]
            return cfg
        with mock.patch.object(xray, "render", side_effect=render):
            data = self.raises(new)
        self.assertGreater(data.get("ru_guard", 0), 0)
        self.assertGreater(data.get("ru_closed", 0), 0)
        self.assertFalse(self.w.sysd.units[UNIT]["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))
        events = []
        collect._ru_watch(data, events, lambda *_: None, self.w.runner)
        self.assertIn("ru_guard", [e["kind"] for e in events])
        ev = next(e for e in events if e["kind"] == "ru_guard")
        self.assertEqual(ev["where"], "xray")
        self.assertTrue(monitor.push_on("ru_guard", {}))
        self.assertIn("ru_guard", monitor.GW_KINDS)
        events = []
        collect._ru_watch(data, events, lambda *_: None, self.w.runner)        # told once
        self.assertNotIn("ru_guard", [e["kind"] for e in events])

    def test_the_redrawn_config_passes_the_last_line_too(self):
        # a healed pool redraws the config under the lock: that one is checked as well
        st = self.socks_world()
        planted = json.loads(self.read(CONF))
        new, sec = self.without_consent()
        real, calls = xray.render, []

        def render(*a, **kw):
            cfg = real(*a, **kw)
            calls.append(1)
            if len(calls) > 1:                                                  # the redraw only
                cfg["routing"]["rules"] = planted["routing"]["rules"]
            return cfg
        new["gateway"]["rules"]["block_torrent"] = True
        with mock.patch.object(xray, "render", side_effect=render), \
                mock.patch.object(xray, "_test", side_effect=[KaskadError("refused"), None]), \
                mock.patch.object(xray, "_healed", return_value=True):
            data = self.raises(new)
        self.assertEqual(len(calls), 2)
        self.assertGreater(data.get("ru_guard", 0), 0)
        self.assertEqual(self.conf_direct() or [], [])

    def test_nft_sets_without_the_consent_stay_empty(self):
        new, sec = self.without_consent()
        ip = "10.77.1.10"
        self.assertEqual(net.rd_addresses(new, {"rd": [ip]}), [])
        self.assertEqual(net.ruonly_addresses(new, {"ruonly": [ip]}), [])
        self.assertEqual(ruguard.tripped()["where"], "nft")
        st = self.socks_world()                                                # with the consent: as drawn
        self.assertEqual(net.rd_addresses(st, {"rd": [ip]}), [ip])

    def test_the_syntax_probe_is_no_trip(self):
        # ``net.probe`` checks a twin table with a sample address in gw_rd — never applied, no event
        net.probe(self.w.runner)
        self.assertIsNone(ruguard.tripped())

    def test_the_hook_does_not_put_an_address_without_the_consent(self):
        new, sec = self.without_consent()
        net.set_rd(self.w.runner, new, "10.77.1.10", True)                     # «add» turned into «delete»
        self.assertNotIn("10.77.1.10", self.w.nft.elements("gw_rd"))

    def test_texts(self):
        for lang in ("ru", "en"):
            for where in ("xray", "nft"):
                t = live.event_text({"kind": "ru_guard", "key": "gw_live.event.ru_guard",
                                     "args": {"n": 2, "where": where}}, lang)
                self.assertIn("2", t)
                self.assertNotIn("{", t)


class PushWordsTest(R2Case):
    def test_stopped_by_the_collector_says_socks_and_vless_are_down(self):
        # REV-s1-b7 №2 (rev7e): the collector stopped xray — the push says what is down, not «stopped or running»
        self.without_consent()
        self.w.xray.fail = REFUSED
        for _ in range(3):
            collect.tick(self.w.runner)
        self.assertFalse(self.w.sysd.units[UNIT]["active"])
        evs = [e for e in self.events() if e["kind"] == "xray_ru_closed"]
        self.assertEqual(len(evs), 1)
        for lang in ("ru", "en"):
            text = live.event_text(evs[0], lang)
            self.assertIn("SOCKS5", text)
            self.assertEqual(text, tr("gw_live.event.xray_ru_closed_stopped", lang))
        self.assertTrue(monitor.push_on("xray_ru_closed", {}))
        rej = [e for e in self.events() if e["kind"] == "xray_rejected"]
        self.assertTrue(rej and all((e.get("args") or {}).get("stopped") for e in rej), rej)
        for lang in ("ru", "en"):                                              # not «the previous one runs»
            self.assertNotIn(tr("gw_live.event.xray_rejected", lang, error="").split("—")[-1].strip(),
                             live.event_text(rej[0], lang))

    def test_the_mark_gone_says_restarted_or_stopped(self):
        events = []
        collect.MEM.ru_open = 2
        collect._ru_watch({}, events, lambda *_: None, self.w.runner)
        self.assertEqual(events, [{"kind": "xray_ru_closed", "n": 2, "why": "restarted"}])
        ev = {"kind": "xray_ru_closed", "key": "gw_live.event.xray_ru_closed", "args": {"n": 2, "why": "restarted"}}
        self.assertEqual(live.event_text(ev, "ru"), tr("gw_live.event.xray_ru_closed", "ru"))

    def test_one_wording_of_the_permission(self):
        # придирка 4: «без выбора или согласия ⚠РФ» in the bot and the feed, no «разрешения»
        for key in ("bot.mon.gw_xray_ru_closed", "bot.mon.gw_xray_ru_open", "gw_live.event.xray_ru_closed",
                    "gw_live.event.xray_ru_closed_stopped", "gw_live.event.xray_ru_open"):
            self.assertNotIn("разрешения", tr(key, "ru"))
            self.assertIn("⚠РФ", tr(key, "ru"))
            self.assertIn("⚠RU", tr(key, "en"))
        for key in ("gw_ops.check.xray_ru_open", "gw_collect.log.xray_ru_open"):
            self.assertNotIn("отказа нового конфига", tr(key, "ru", n=1))


class MarkLifeTest(R2Case):
    def test_the_mark_narrows_when_the_consent_comes_back(self):
        # придирка 2 (T3): the consent back while xray does not stop — no longer «open», no problem in the check
        st0 = self.socks_world()
        cons = st0["gateway"].get("rf_consent")
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        self.unstoppable()
        self.raises(new)
        self.assertGreater(xray.ru_open(), 0)
        with state.transaction() as st:
            st["gateway"]["rf_consent"] = cons
        try:
            self.apply(state.load(), skip_rejected=True)
        except KaskadError:
            pass
        self.assertEqual(xray.ru_open(), 0)
        self.assertNotIn("gw_xray_ru_open", [p["kind"] for p in ops.check(state.load(), self.w.runner)])

    def test_a_restarted_collector_does_not_push_again(self):
        # придирка 3 (T5): the number told is kept in the mark
        self.socks_world()
        self.w.xray.fail = REFUSED
        self.unstoppable()
        self.run_cli("rules", "profile", "user", "Мама", "all_exit")
        collect.tick(self.w.runner)
        collect.tick(self.w.runner)
        collect.MEM.__init__()                                                 # a new process of the collector
        collect.tick(self.w.runner)
        self.assertEqual(self.kinds().count("xray_ru_open"), 1)

    def test_kill_is_waited_for(self):
        # придирка 1: ``systemctl kill`` does not wait — «active» right after it is not «did not stop»
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        self.w.sysd.fail.update({"disable": "Access denied\n", "stop": "Access denied\n"})
        answers = iter([False, False, False, True])
        real = gsys.unit_stopped

        def stopped(runner, unit):
            if unit == UNIT:
                return next(answers, True)
            return real(runner, unit)
        with mock.patch.object(xray, "KILL_WAIT_S", 2.0), mock.patch.object(gsys, "unit_stopped", stopped), \
                mock.patch.object(xray.time, "sleep") as sleep:
            data = self.raises(new)
        self.assertGreater(data.get("ru_closed", 0), 0)
        self.assertTrue(sleep.called)

    def test_kill_wait_ends(self):
        with mock.patch.object(gsys, "unit_stopped", return_value=False), mock.patch.object(xray.time, "sleep"), \
                mock.patch.object(xray.time, "monotonic", side_effect=[0.0, 1.0, 6.0]):
            self.assertFalse(xray._wait_stopped(self.w.runner, 5.0))


class EnsureUnitActivatingTest(R2Case):
    def test_activating_is_restarted_not_joined(self):
        # -sec П-1: a start under way read the old file; ``start`` would join it — restart instead
        self.socks_world()
        self.w.sysd.units[UNIT].update(active=False, activating=True)
        n = self.w.sysd.restarts.get(UNIT, 0)
        r = gsys.ensure_unit(self.w.runner, UNIT, xray.unit_text(xray.run_binary()), restart=True)
        self.assertTrue(r["restarted"])
        self.assertEqual(self.w.sysd.restarts.get(UNIT, 0), n + 1)

    def test_inactive_is_started(self):
        self.socks_world()
        self.w.runner.run(["systemctl", "stop", UNIT])
        r = gsys.ensure_unit(self.w.runner, UNIT, xray.unit_text(xray.run_binary()), restart=True)
        self.assertTrue(r["started"])
