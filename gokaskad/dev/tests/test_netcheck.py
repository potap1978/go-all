"""netcheck.py — the watchdog behind ``kaskadctl net check [--repair]`` (spec §3.5)."""

import json
import os
import stat
from unittest import mock

from kaskad import compat, netcheck, nft, state
from tests.fakes_nft import F443, F51820, World, save_state
from tests.helpers import RootCase, lock_held_elsewhere

V3 = '#!/bin/bash\n# Kaskad PRO 3\nKASKAD_VERSION="3.0.1"\nmain() { :; }\ncase "$1" in\n  *) main ;;\nesac\n'
V3_OLDER = V3.replace("3.0.1", "3.0.0")
V2 = '#!/bin/bash\nKASKAD_VERSION="2.13"\ncase "$1" in\n  *) : ;;\nesac\n'
FREE = "#!/bin/bash\n# Kaskad\nconfigure_custom_rule() { :; }\nshow_promo() { :; }\n"
T0 = 1790000000.0


class NetcheckTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = World()
        self.now = T0
        self._patch = mock.patch.object(netcheck, "_now", lambda: self.now)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.assertEqual(self.w.runner.unmatched, [])
        super().tearDown()

    def healthy(self, fwds=(F443, F51820)):
        st = save_state(fwds)
        nft.apply(st, self.w.runner)
        compat.ensure(self.w.runner)
        self.write("/opt/kaskad/bin/gokaskad", V3, 0o755)
        self.write("/usr/local/bin/gokaskad", V3, 0o755)
        return st

    def log(self):
        p = self.path("/var/log/kaskad.log")
        return self.read("/var/log/kaskad.log") if os.path.exists(p) else ""

    def check(self, repair=True):
        return netcheck.check(self.w.runner, repair=repair)

    def kinds(self, res):
        return [p["kind"] for p in res["problems"]]

    # ── nothing to do ──
    def test_engine_legacy_is_a_no_op(self):
        save_state([F443], engine="legacy")
        self.write("/opt/kaskad/bin/gokaskad", V3, 0o755)
        self.write("/usr/local/bin/gokaskad", FREE, 0o755)
        for repair in (False, True):
            res = self.check(repair)
            self.assertEqual(res, {"ok": True, "engine": "legacy", "problems": [], "skipped": False})
        self.assertEqual(self.w.runner.calls, [])
        self.assertEqual(self.read("/usr/local/bin/gokaskad"), FREE)
        self.assertFalse(os.path.exists(netcheck.stamp_file()))

    def test_no_state_file_is_engine_legacy(self):
        self.assertEqual(self.check()["engine"], "legacy")
        self.assertEqual(self.w.runner.calls, [])

    def test_healthy(self):
        self.healthy()
        self.assertEqual(self.check(False), {"ok": True, "engine": "nft", "problems": [], "skipped": False})
        res = self.check(True)
        self.assertEqual((res["ok"], res["problems"]), (True, []))
        self.assertEqual(self.log(), "")

    def test_broken_state(self):
        self.write("/etc/kaskad/state.json", "{broken")
        res = self.check()
        self.assertFalse(res["ok"])
        self.assertEqual(self.kinds(res), ["state"])
        self.assertEqual(self.w.runner.calls, [])

    # ── report mode never changes anything ──
    def test_report_mode_only_reports(self):
        self.healthy()
        self.w.nft.flush_ruleset()
        compat.remove(self.w.runner)
        self.w.sysctl.values["net.ipv4.ip_forward"] = "0"
        self.write("/usr/local/bin/gokaskad", FREE, 0o755)
        nft_file = self.read("/etc/kaskad/net/kaskad.nft")
        calls = len(self.w.runner.calls)
        res = self.check(False)
        self.assertFalse(res["ok"])
        self.assertEqual(self.kinds(res), ["table_missing", "compat_missing", "ip_forward", "command"])
        self.assertTrue(all(not p["repaired"] for p in res["problems"]))
        new_calls = [a for a, _ in self.w.runner.calls[calls:]]
        self.assertEqual([a for a in new_calls if a[:1] == ["nft"] and a[1:2] != ["-j"]], [])
        self.assertFalse(any("-I" in a for a in new_calls if a[:1] == ["iptables"]))
        self.assertEqual(self.w.sysctl.values["net.ipv4.ip_forward"], "0")
        self.assertEqual(self.read("/usr/local/bin/gokaskad"), FREE)
        self.assertEqual(self.read("/etc/kaskad/net/kaskad.nft"), nft_file)
        self.assertEqual(self.log(), "")
        self.assertFalse(os.path.exists(netcheck.stamp_file()))

    # ── 1. table ──
    def test_missing_table_is_restored(self):
        self.healthy()
        self.w.nft.set_counter("fwd_tcp_443", 8, 800)
        nft.save_counters(self.w.runner)
        self.w.nft.flush_ruleset()                       # nft flush ruleset / systemctl restart nftables
        res = self.check()
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.kinds(res), ["table_missing"])
        self.assertTrue(res["problems"][0]["repaired"])
        self.assertEqual(len(self.w.nft.forwards()), 2)
        self.assertEqual(self.w.nft.counter("fwd_tcp_443"), {"packets": 8, "bytes": 800})
        self.assertIn("NET restore", self.log())

    def test_missing_table_without_file(self):
        self.healthy()
        os.unlink(self.path("/etc/kaskad/net/kaskad.nft"))
        self.w.nft.flush_ruleset()
        res = self.check()
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(self.w.nft.forwards()), 2)
        self.assertTrue(os.path.exists(self.path("/etc/kaskad/net/kaskad.nft")))

    def test_missing_table_falls_back_to_apply_when_the_file_cannot_load(self):
        self.healthy()
        self.w.nft.flush_ruleset()
        # every render_full text fails (file and re-rendered file); the update script still works
        self.w.nft.fail_apply = lambda text: "Error: boom\n" if text.startswith("table ip kaskad\n") else None
        res = self.check()
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.kinds(res), ["table_missing"])
        self.assertEqual(len(self.w.nft.forwards()), 2)
        self.assertIn("NET repair", self.log())

    def test_unreadable_table_is_reported(self):
        self.healthy()
        self.w.nft.json_ok = False
        res = self.check()
        self.assertFalse(res["ok"])
        self.assertEqual(self.kinds(res), ["nft"])

    # ── 2. drift ──
    def test_drift_is_applied(self):
        self.healthy()
        self.w.nft.drop_rule("fwd_dnat", 0)                                   # forward removed by hand
        self.w.nft.run_script('add counter ip kaskad fwd_tcp_9999\n'
                              'add rule ip kaskad fwd_dnat tcp dport 9999 ct mark set ct mark or 0x4b000000 '
                              'dnat to 192.0.2.9:9999 comment "kaskad:9999:tcp"\n', True)
        res = self.check()
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.kinds(res), ["drift"])
        self.assertIn("нет проброса tcp:443", res["problems"][0]["detail"])
        self.assertIn("лишний проброс tcp:9999", res["problems"][0]["detail"])
        self.assertEqual(self.w.nft.forwards(), {("tcp", 443): ("203.0.113.5", 443),
                                                 ("udp", 51820): ("198.51.100.7", 51820)})
        self.assertIsNone(self.w.nft.counter("fwd_tcp_9999"))
        self.assertIn("NET repair: таблица не совпадает с настройками", self.log())

    def test_drift_right_after_an_update_is_planned_work(self):
        # T-N5 3.2.19-r2, Н-6: right after selfupd the new code wants rules the old one did not put — «NET repair: … —
        # применил настройки заново» read like a breakdown; it is the planned work of the update, said so
        import kaskad
        from kaskad import util
        self.healthy()
        self.assertEqual(nft.applied_version(), kaskad.__version__)
        util.atomic_write(nft.applied_file(), json.dumps({"version": "3.2.0"}), 0o600)   # applied by the old code
        self.w.nft.drop_rule("fwd_dnat", 0)
        res = self.check()
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.kinds(res), ["drift"])
        self.assertTrue(res["problems"][0]["planned"])
        self.assertIn("NET после обновления (3.2.0 → %s): настройки сети применены заново — так и должно быть"
                      % kaskad.__version__, self.log())
        self.assertNotIn("NET repair", self.log())
        self.assertEqual(nft.applied_version(), kaskad.__version__)
        # the next drift under the same version is a breakdown again — with the details
        self.now += 7200
        self.w.nft.drop_rule("fwd_dnat", 0)
        res = self.check()
        self.assertNotIn("planned", res["problems"][0])
        self.assertIn("NET repair: таблица не совпадает с настройками", self.log())
        # a table applied by a version before 3.2.20 (no record at all)
        os.unlink(nft.applied_file())
        self.now += 7200
        self.w.nft.drop_rule("fwd_dnat", 0)
        self.check()
        self.assertIn("NET после обновления до %s: настройки сети применены заново" % kaskad.__version__, self.log())

    def test_failed_repair_is_reported(self):
        self.healthy()
        self.w.nft.drop_rule("fwd_pre", 0)
        self.w.nft.fail_check = "Error: Could not process rule: Operation not supported\n"
        res = self.check()
        self.assertFalse(res["ok"])
        self.assertEqual(self.kinds(res), ["drift"])
        self.assertIn("Operation not supported", res["problems"][0]["error"])
        self.assertEqual(self.log(), "")

    # ── 3. compat rule, with the hourly log throttle ──
    def test_compat_rule_restored_and_logged_once_an_hour(self):
        self.healthy()
        compat.remove(self.w.runner)                                          # iptables -F of the free script
        res = self.check()
        self.assertTrue(res["ok"])
        self.assertEqual(self.kinds(res), ["compat_missing"])
        self.assertTrue(compat.present(self.w.runner))
        self.assertEqual(self.log().count("правило совместимости"), 1)
        for minutes in (1, 2):
            self.now = T0 + 60 * minutes
            compat.remove(self.w.runner)
            self.assertTrue(self.check()["ok"])
            self.assertTrue(compat.present(self.w.runner))
        self.assertEqual(self.log().count("правило совместимости"), 1)
        self.now = T0 + 3601
        compat.remove(self.w.runner)
        self.check()
        lines = [ln for ln in self.log().splitlines() if "правило совместимости" in ln]
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("(ещё 2 раз после прошлой записи)"), lines[1])

    def test_no_iptables_no_compat_problem(self):
        self.w = World(binaries=("nft", "systemctl", "sysctl", "ss", "uname"))       # Debian 12 image
        self.healthy()
        res = self.check()
        self.assertEqual((res["ok"], res["problems"]), (True, []))
        self.assertEqual(self.w.runner.commands(["iptables"]), [])

    # ── 4. ip_forward ──
    def test_ip_forward_switched_back_on(self):
        self.healthy()
        self.w.sysctl.values["net.ipv4.ip_forward"] = "0"
        res = self.check()
        self.assertTrue(res["ok"])
        self.assertEqual(self.kinds(res), ["ip_forward"])
        self.assertEqual(self.w.sysctl.values["net.ipv4.ip_forward"], "1")
        self.assertIn("net.ipv4.ip_forward=1", self.read("/etc/sysctl.d/90-kaskad.conf"))
        self.assertIn("NET repair: net.ipv4.ip_forward был 0 — включил", self.log())

    # ── 5. the command ──
    def command_case(self, content, detail):
        self.healthy()
        if content is None:
            os.unlink(self.path("/usr/local/bin/gokaskad"))
        else:
            self.write("/usr/local/bin/gokaskad", content, 0o755)
        res = self.check()
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.kinds(res), ["command"])
        self.assertIn(detail, res["problems"][0]["detail"])
        self.assertEqual(self.read("/usr/local/bin/gokaskad"), V3)
        mode = stat.S_IMODE(os.stat(self.path("/usr/local/bin/gokaskad")).st_mode)
        self.assertEqual(mode, 0o755)
        self.assertIn("NET repair: Команду gokaskad подменила другая версия — вернул 3.x", self.log())

    def test_command_overwritten_by_the_free_script(self):
        self.command_case(FREE, "бесплатный Kaskad")

    def test_command_overwritten_by_2x(self):
        self.command_case(V2, "Kaskad 2.13")

    def test_command_deleted(self):
        self.command_case(None, "команды нет")

    def test_any_3x_command_is_left_alone(self):
        self.healthy()
        self.write("/usr/local/bin/gokaskad", V3_OLDER, 0o755)
        self.assertEqual(self.check()["problems"], [])
        self.assertEqual(self.read("/usr/local/bin/gokaskad"), V3_OLDER)

    def test_no_3x_copy_nothing_to_restore(self):
        self.healthy()
        self.write("/usr/local/bin/gokaskad", FREE, 0o755)
        self.write("/opt/kaskad/bin/gokaskad", V2, 0o755)
        self.assertEqual(self.check()["problems"], [])
        os.unlink(self.path("/opt/kaskad/bin/gokaskad"))
        self.assertEqual(self.check()["problems"], [])
        self.assertEqual(self.read("/usr/local/bin/gokaskad"), FREE)

    # ── 6. counters ──
    def test_counters_saved_every_run_when_changed(self):
        self.healthy()
        self.w.nft.set_counter("fwd_tcp_443", 1, 100)
        self.check()
        self.assertIn("packets 1 bytes 100", self.read("/etc/kaskad/net/kaskad.nft"))
        self.assertEqual(json.loads(self.read("/etc/kaskad/net/watch.json"))["counters_at"], T0)
        self.w.nft.set_counter("fwd_tcp_443", 2, 200)
        self.now = T0 + 30                       # two runs in the same minute: the second one waits
        self.check()
        self.assertIn("packets 1 bytes 100", self.read("/etc/kaskad/net/kaskad.nft"))
        self.now = T0 + 60
        self.check()
        self.assertIn("packets 2 bytes 200", self.read("/etc/kaskad/net/kaskad.nft"))
        self.assertEqual(nft.saved_counters()["fwd_tcp_443"], {"packets": 2, "bytes": 200})
        self.now = T0 + 120                      # nothing changed: no rewrite, the stamp stays
        self.check()
        self.assertEqual(json.loads(self.read("/etc/kaskad/net/watch.json"))["counters_at"], T0 + 60)

    # ── locking ──
    def test_busy_lock_skips_the_run(self):
        self.healthy()
        self.w.nft.flush_ruleset()
        with mock.patch.object(netcheck, "LOCK_TIMEOUT", 0.2):
            with lock_held_elsewhere():                  # «busy» — another process (T1: not the same thread)
                res = self.check()
        self.assertEqual(res, {"ok": True, "engine": "nft", "problems": [], "skipped": True})
        self.assertIsNone(self.w.nft.table())
        self.assertEqual(self.check(False)["skipped"], False)                # report mode needs no lock
