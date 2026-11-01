"""B7 (before КТ1): xray closes «Россия напрямую» without the consent ⚠РФ after ANY failure, says the truth while it
could not, and the next call closes again instead of taking it as done — REV-s1-b6 №1, Б1, Б2, П1; REV-s1-b6-sec Н-1,
Н-2, П-1, П-2. The mark ``xray.pending`` (/run): what runs may not be the file on the disk."""

import json
import os
from unittest import mock

from kaskad import paths, secrets, state, util
from kaskad.bot import monitor
from kaskad.errors import KaskadError
from kaskad.gw import collect, exits, live, net, ops, xray
from kaskad.gw import sys as gsys
from kaskad.i18n import tr
from tests.gw.test_s1_b5 import CONF, REFUSED, direct_users
from tests.gw.test_s1_b6 import CloseRuB6Case, UNIT

BUS = "Failed to connect to bus: Connection refused\n"


def mark():
    """``xray.pending`` (the code before B7 has none: red by the behaviour, not by a missing name)."""
    return getattr(xray, "pending", lambda: None)()


def open_n():
    return getattr(xray, "ru_open", lambda: 0)()


class B7Case(CloseRuB6Case):
    def unstoppable(self):
        self.w.sysd.fail.update({"disable": "Access denied\n", "stop": "Access denied\n"})
        self.w.sysd.units[UNIT]["unkillable"] = True

    def apply(self, st=None, **kw):
        return xray.apply(st or state.load(), secrets.load(), self.w.runner, exits.pool(self.w.runner), **kw)

    def raises(self, st=None, **kw):
        with self.assertRaises(KaskadError) as e:
            self.apply(st, **kw)
        return e.exception.data or {}

    def events(self):
        p = self.path("/run/kaskad/live.json")
        return json.loads(self.read("/run/kaskad/live.json"))["events"] if os.path.exists(p) else []


class AfterRenameTest(B7Case):
    def test_xray_that_did_not_stop_after_the_rename_is_closed_next_time(self):
        # REV-s1-b6 №1: the new file is on the disk, the old process with direct rules runs and did not stop; the next
        # call saw «the file is the render», noted it applied and never closed again
        new, sec = self.without_consent()
        self.unstoppable()
        with mock.patch.object(xray, "hot_apply", return_value=False), \
                mock.patch.object(gsys, "ensure_unit", side_effect=KaskadError("daemon-reload failed")):
            data = self.raises(new)
        self.assertGreater(data.get("ru_open", 0), 0)
        self.assertNotIn("ru_closed", data)
        self.assertTrue(xray.status(self.w.runner)["active"])
        del self.w.sysd.fail["stop"]
        del self.w.sysd.units[UNIT]["unkillable"]
        res = self.apply(fp="x", seq=99)
        self.assertTrue(res["started"] or res["restarted"] or res.get("ru_closed"), res)
        self.assertEqual(direct_users(json.loads(self.read(CONF))), [])
        self.assertIsNone(mark())
        self.assertEqual(open_n(), 0)
        self.assertEqual(xray.applied(), {"fp": "x", "seq": 99})
        self.assertEqual(direct_users(json.loads(self.read(CONF))), [])

    def test_not_noted_as_applied_while_open(self):
        new, sec = self.without_consent()
        self.unstoppable()
        self.w.xray.fail = REFUSED
        before = xray.applied()
        for _ in range(2):                                                       # every call closes again
            data = self.raises(new, fp="y", seq=50)
            self.assertGreater(data.get("ru_open", 0), 0)
        self.assertEqual(xray.applied(), before)
        self.assertTrue(xray.render_wanted("y"))
        kills = [c for c in self.w.sysd.calls if c[1:2] == ["kill"]]
        self.assertEqual(len(kills), 2)

    def test_another_change_lost_by_a_failed_restart_is_restarted(self):
        # REV-s1-b6 Б2: no Russia in it — still the old process runs; the next call restarts it, not «the file equals»
        st = self.socks_world()
        new = json.loads(json.dumps(st))
        new["gateway"]["rules"]["block_torrent"] = True
        with mock.patch.object(xray, "hot_apply", return_value=False), \
                mock.patch.object(gsys, "ensure_unit", side_effect=KaskadError("daemon-reload failed")):
            data = self.raises(new)
        self.assertNotIn("ru_closed", data)
        n = self.w.sysd.restarts.get(UNIT, 0)
        res = self.apply(new, fp="z", seq=7)
        self.assertTrue(res["restarted"], res)
        self.assertEqual(self.w.sysd.restarts.get(UNIT, 0), n + 1)
        self.assertIsNone(mark())
        self.assertEqual(xray.applied(), {"fp": "z", "seq": 7})
        res = self.apply(new, fp="z", seq=8)                                     # and only once
        self.assertFalse(res["restarted"])

    def test_a_failure_after_a_good_restart_does_not_stop_it(self):
        # REV-s1-b6 П1 (придирка 1): after a restart xray runs the new config — a failure after it closes nothing
        new, sec = self.without_consent()
        with mock.patch.object(xray, "hot_apply", return_value=False), \
                mock.patch.object(xray, "_restarted", side_effect=KaskadError("after the restart")):
            data = self.raises(new)
        self.assertNotIn("ru_closed", data)
        self.assertTrue(xray.status(self.w.runner)["active"])
        self.assertTrue(os.path.exists(self.path(CONF)))
        self.assertIsNone(mark())


class WriteFailsTest(B7Case):
    def test_enospc_of_the_new_file_closes(self):
        # REV-s1-b6 Б1, -sec П-1: an OSError of the file closes as a refusal does, said as a KaskadError
        new, sec = self.without_consent()
        real = util.atomic_write

        def full(path, *a, **kw):
            if path.endswith("xray.new.json"):
                raise OSError(28, "No space left on device")
            return real(path, *a, **kw)
        with mock.patch.object(util, "atomic_write", side_effect=full):
            data = self.raises(new)
        self.assertGreater(data.get("ru_closed", 0), 0)
        self.assertFalse(xray.status(self.w.runner)["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/gw/xray.new.json")))

    def test_rename_failing_closes(self):
        new, sec = self.without_consent()
        real = os.rename

        def rename(a, b):
            if b == xray.config_path():
                raise OSError(30, "Read-only file system")
            return real(a, b)
        with mock.patch.object(xray.os, "rename", side_effect=rename):
            data = self.raises(new)
        self.assertGreater(data.get("ru_closed", 0), 0)
        self.assertFalse(xray.status(self.w.runner)["active"])
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/gw/xray.new.json")))

    def test_the_file_that_cannot_go_is_not_closed(self):
        # -sec П-1: neither unlink nor rename — the file of direct rules stays (a watchdog restart would run it): ru_open
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        real, real_mv = os.unlink, os.replace

        def unlink(p, *a, **kw):
            if p == xray.config_path():
                raise PermissionError(13, "Permission denied")
            return real(p, *a, **kw)

        def replace(p, *a, **kw):
            if p == xray.config_path():
                raise PermissionError(13, "Permission denied")
            return real_mv(p, *a, **kw)
        with mock.patch.object(xray.os, "unlink", side_effect=unlink), \
                mock.patch.object(xray.os, "replace", side_effect=replace):
            data = self.raises(new)
        self.assertGreater(data.get("ru_open", 0), 0)
        self.assertNotIn("ru_closed", data)
        self.assertGreater(open_n(), 0)

    def test_a_file_that_cannot_be_removed_is_renamed(self):
        # круг 2: unlink refused — the file leaves the name the unit reads all the same (xray.closed.json): closed
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        real = os.unlink

        def unlink(p, *a, **kw):
            if p == xray.config_path():
                raise PermissionError(13, "Permission denied")
            return real(p, *a, **kw)
        with mock.patch.object(xray.os, "unlink", side_effect=unlink):
            data = self.raises(new)
        self.assertGreater(data.get("ru_closed", 0), 0)
        self.assertFalse(os.path.exists(self.path(CONF)))


class StoppedIsKnownTest(B7Case):
    def test_bus_down_is_not_stopped(self):
        # REV-s1-b6-sec Н-1: an unreachable bus answers nothing — «not active» is not «stopped»
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        orig = xray.stop

        def stop(runner):
            for v in ("disable", "stop", "kill", "is-active", "show"):
                self.w.sysd.fail[v] = BUS
            return orig(runner)
        with mock.patch.object(xray, "stop", side_effect=stop):
            data = self.raises(new)
        self.assertGreater(data.get("ru_open", 0), 0)
        self.assertNotIn("ru_closed", data)
        self.assertTrue(self.w.sysd.units[UNIT]["active"])
        self.assertFalse(os.path.exists(self.path(CONF)))                      # the file went before the stop
        for v in ("disable", "stop", "kill", "is-active", "show"):
            self.w.sysd.fail.pop(v)
        data = self.raises(skip_rejected=True)                                  # the next tick: the mark closes it
        self.assertGreater(data.get("ru_closed", 0), 0)
        self.assertFalse(self.w.sysd.units[UNIT]["active"])
        self.assertIsNone(mark())

    def test_activating_is_not_stopped(self):
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        self.unstoppable()
        self.w.sysd.units[UNIT]["activating"] = True
        data = self.raises(new)
        self.assertGreater(data.get("ru_open", 0), 0)
        self.assertNotIn("ru_closed", data)

    def test_the_file_is_gone_before_the_stop(self):
        # -sec Н-1 (б): the watchdog restarts a needed xray without the xray lock (gw/ops.py) — between «disable» and
        # the stop it would start the old file; no file — the restart fails (LoadCredential)
        new, sec = self.without_consent()
        self.w.xray.fail = REFUSED
        seen = []
        orig = xray.stop

        def stop(runner):
            seen.append(os.path.exists(self.path(CONF)))
            return orig(runner)
        with mock.patch.object(xray, "stop", side_effect=stop):
            self.raises(new)
        self.assertEqual(seen, [False])

    def test_xports_branch_closes_by_the_redrawn_config(self):
        # -sec П-2: the closing without gw_xports takes the config of the healed pool as the exception branch does
        new, sec = self.without_consent()
        got = []
        orig = xray._close_ru

        def close(runner, cfg, out, ran=None, why=None):
            got.append((cfg, dict(ran or {})))
            return orig(runner, cfg, out, ran, why)
        with mock.patch.object(net, "xports_add", return_value=False), mock.patch.object(xray, "_close_ru", close):
            self.apply(new)
        cfg, ran = got[0]
        self.assertIs(cfg, ran.get("want", cfg))


class TheOwnerSeesItTest(B7Case):
    def test_the_collector_tells_open_once_then_closed(self):
        # REV-s1-b6-sec Н-2: its own event, not the hourly xray_rejected; the bot always sends it
        self.socks_world()
        self.w.xray.fail = REFUSED
        self.unstoppable()
        rc, out, err = self.run_cli("rules", "profile", "user", "Мама", "all_exit")
        self.assertEqual(rc, 0, err)
        self.assertGreater(open_n(), 0)
        for _ in range(3):
            collect.tick(self.w.runner)
        kinds = [e["kind"] for e in self.events() if e["kind"].startswith("xray_ru_")]
        self.assertEqual(kinds, ["xray_ru_open"])
        probs = ops.check(state.load(), self.w.runner)
        self.assertIn("gw_xray_ru_open", [p["kind"] for p in probs])
        self.assertTrue(monitor.push_on("xray_ru_open", {}))
        self.assertTrue(monitor.push_on("xray_ru_closed", {}))
        self.w.sysd.fail.clear()
        del self.w.sysd.units[UNIT]["unkillable"]
        collect.tick(self.w.runner)
        kinds = [e["kind"] for e in self.events() if e["kind"].startswith("xray_ru_")]
        self.assertEqual(kinds, ["xray_ru_open", "xray_ru_closed"])
        self.assertEqual(open_n(), 0)
        self.assertFalse(self.w.sysd.units[UNIT]["active"])
        self.assertNotIn("gw_xray_ru_open", [p["kind"] for p in ops.check(state.load(), self.w.runner)])
        for e in self.events():
            if e["kind"].startswith("xray_ru_"):
                for lang in ("ru", "en"):
                    text = live.event_text(e, lang)
                    self.assertTrue(text and "{" not in text and "gw_live." not in text, text)

    def test_the_texts_say_what_was_tried(self):
        # REV-s1-b6 П2 (придирка 2): not «systemctl stop» that already failed
        for lang in ("ru", "en"):
            for key in ("gw_xray.warn.ru_open", "gw_ops.warn.xray_ru_open"):
                text = tr(key, lang, err="")
                self.assertIn("kill", text)
                self.assertNotIn("systemctl stop", text)
                self.assertIn("kaskadctl gw apply", text)


class NotNeededTest(B7Case):
    def test_a_stop_that_did_not_stop_is_not_noted(self):
        # «xray не нужен» and the stop refused: not noted as applied, KNOWN stopped only
        self.socks_world()
        self.unstoppable()
        with mock.patch.object(xray, "needed", return_value=False):
            res = self.apply(fp="n", seq=123)
        self.assertNotEqual(xray.applied(), {"fp": "n", "seq": 123})
        self.assertGreater(res.get("ru_open", 0), 0)
        self.w.sysd.fail.clear()
        del self.w.sysd.units[UNIT]["unkillable"]
        with mock.patch.object(xray, "needed", return_value=False):
            self.apply(fp="n", seq=124)
        self.assertEqual(xray.applied(), {"fp": "n", "seq": 124})
        self.assertIsNone(mark())


class MarkTest(B7Case):
    def test_the_mark_reads_and_widens(self):
        self.assertIsNone(mark())
        xray._note_pending({("socks-in", "a")}, open_n=1)
        xray._note_pending({("vless-in", "b")})
        self.assertEqual(mark(), {("socks-in", "a"), ("vless-in", "b")})
        self.assertEqual(open_n(), 1)
        st = os.stat(xray._pending_path())
        self.assertEqual(st.st_mode & 0o777, 0o600)
        with open(xray._pending_path(), "w") as f:
            f.write("{broken")
        self.assertEqual(mark(), set())                                  # a restart owed all the same
        self.assertEqual(open_n(), 0)
        xray._clear_pending()
        self.assertIsNone(mark())
        self.assertTrue(xray._pending_path().startswith(paths.gw_run_dir()))


class RefreshNotesTest(B7Case):
    """REV-s1-b6 П3 (придирка 3): the good news of the keys after a refresh of a subscription — in the text of the CLI
    and in the panel (``subsDone``), not only in the JSON."""

    def test_the_cli_says_the_news(self):
        from kaskad.gw import exitsubs
        res = {"ok": True, "text": "все", "results": [{"ok": True, "text": "Подписка обновлена", "warnings": [],
                                                         "notes": ["ключ 2 проверен — работает"]}]}
        with mock.patch.object(exitsubs, "refresh", return_value=res):
            rc, out, err = self.run_cli("gw", "exit-sub", "refresh")
        self.assertEqual(rc, 0, err)
        self.assertIn("Подписка обновлена", out)
        self.assertIn("ключ 2 проверен — работает", out)

    def test_the_panel_says_the_news(self):
        js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib",
                               "kaskad", "panel", "static", "app.js"), encoding="utf-8").read()
        i = js.index("function subsDone(")
        body = js[i:js.index("\n  }\n", i)]
        self.assertIn("x.notes", body)
        self.assertIn("concat(warns, good)", body)
        self.assertIn(".concat(good)", body)


if __name__ == "__main__":
    import unittest
    unittest.main()
