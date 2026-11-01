"""The engine of AmneziaWG (gw/awgengine.py, k3/SPEC-awg-engine.md §1–§4): the check before the module (every blocker
and warning), the switch (backup, refusal of mixing and --force, a host that cannot, the rollback when the clients do
not come back), the first inbound (the module by default, the program on «нет», 3.1 keeps the program), the offer after
an update, the reconcile of a line 3.1 that appeared, the status for the faces, export and import, the mixing guard of
``gw accel on``, the S4/MTU rule of line 2.0."""

import contextlib
import io
import json
import os
import shutil
from unittest import mock

from kaskad import cli, paths, secrets, state, util
from kaskad.errors import ConflictError, KaskadError, UnsupportedError
from kaskad.gw import awgengine, awgkmod, exits, ops
from kaskad.sysrun import FakeRunner
from kaskad.vendor import awgkeys
from tests.helpers import RootCase

K = "6.8.0-50-generic"
HOST_OK = {"host": {"ok": True, "why": "", "arch": "x86_64", "container": "", "secure_boot": False},
           "kernel": K, "headers": "installed", "apt": True, "dkms": "installed", "gcc": True, "free_mb": 5000}


def leg(idx, version, eid=None):
    return {"id": eid or "e-%010x" % idx, "kind": "awg", "iface": "kawg%d" % idx, "version": version, "name": "",
            "host": "198.51.100.%d:51820" % idx, "where": "AmneziaWG %s · 198.51.100.%d" % (version, idx)}


class FakeAd(object):
    def __init__(self, kind):
        self.kind, self.iface = kind, "kgw-" + kind

    def tool(self, runner):
        return "/opt/kaskad/bin/awg"


class EngineCase(RootCase):
    def setUp(self):
        super().setUp()
        self.auto = mock.patch.object(awgengine, "AUTO", True)
        self.auto.start()
        self.now = 1791190000.0
        self.slept = []
        self.patches = [mock.patch.object(awgengine, "_now", lambda: self.now),
                        mock.patch.object(awgengine, "_sleep", self._sleep),
                        mock.patch.object(util, "log_action", lambda text: None),
                        mock.patch.object(awgengine, "_adapters", lambda: [FakeAd(k) for k in awgkmod.AWG_KINDS])]
        for p in self.patches:
            p.start()
        self.r = FakeRunner()
        self.hs = {}                                   # iface -> "pub ts" lines of awg show latest-handshakes
        self.r.on(["/opt/kaskad/bin/awg", "show"], lambda a, i: (0, self.hs.get(a[2], ""), ""))
        self.installs, self.disables = [], []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.auto.stop()
        super().tearDown()

    def _sleep(self, s):
        self.slept.append(s)
        self.now += s

    def gw_state(self, kinds=("awg2",), enabled=True, engine=None):
        st = state.empty()
        st["engine"] = "nft"
        g = {"enabled": enabled, "net": {"base": "10.77.0.0/16"}, "host": "203.0.113.10",
             "inbounds": dict((k, {"enabled": True, "port": 40000 + i, "line": awgengine.INBOUND_LINE.get(k, ""),
                                   "params": {}}) for i, k in enumerate(kinds)),
             "users": [{"id": "u-00000001", "name": "Мама", "enabled": True}],
             "devices": [{"id": "d-00000001", "user": "u-00000001", "name": "Телефон", "kind": "phone",
                          "platform": "android", "enabled": True, "access": list(kinds),
                          "ips": dict((k, "10.77.3.10") for k in kinds)}],
             "routes": {"default": {"via": "pool", "fail": "block"}}, "exits": {}}
        if engine is not None:
            g["awg_engine"] = engine
        st["gateway"] = g
        state.save(st)
        return state.load()

    def kmod(self, enabled, loaded=None):
        doc = {"enabled": enabled}
        os.makedirs(paths.gw_dir(), exist_ok=True)
        util.atomic_write(awgkmod.state_path(), json.dumps(doc), 0o600)
        if loaded if loaded is not None else enabled:
            os.makedirs(awgkmod.module_dir(), exist_ok=True)

    def fake_install(self, fail=None):
        def install(runner, progress=None, force=False):
            self.installs.append(force)
            if fail:
                raise fail
            self.kmod(True)
            return {"ok": True, "warnings": [], "inbounds": [], "legs": None}
        return mock.patch.object(awgkmod, "install", install)

    def fake_disable(self):
        def disable(runner, progress=None):
            self.disables.append(True)
            self.kmod(False, loaded=False)
            shutil.rmtree(awgkmod.module_dir(), ignore_errors=True)       # with its version file
            return {"ok": True, "warnings": []}
        return mock.patch.object(awgkmod, "disable", disable)

    def rec(self):
        return awgengine.record(state.load())

    def legs(self, *legs):
        """The pool of exits the switch and ``gw accel on`` read (exits.pool)."""
        return mock.patch.object(exits, "pool", lambda runner=None, parser=None, roles=("abroad",): list(legs))

    def module_with_set(self, on=True):
        """The module in memory carries the set of 3.1 (its mark in /sys/module/amneziawg/version)."""
        os.makedirs(awgkmod.module_dir(), exist_ok=True)
        self.write("/sys/module/amneziawg/version", "3.1.20260812%s\n" % (("+" + awgkmod.SET_ID) if on else ""))


class CheckTest(EngineCase):
    def test_a_clean_host_can(self):
        st = self.gw_state(("awg1", "awg2"))
        chk = awgengine.check(self.r, st=st, pool=[leg(2, "2.0")], host=dict(HOST_OK))
        self.assertTrue(chk["can"])
        self.assertEqual(chk["blockers"], [])
        self.assertEqual(chk["install"]["packages"], [])
        self.assertTrue(chk["facts"]["transit"])
        self.assertTrue(any("транзитом" in p for p in chk["pays"]))
        self.assertIn("вдвое меньше", " ".join(chk["benefit"]))
        self.assertIn("#254", " ".join(chk["risks"]))

    def test_every_blocker_in_words(self):
        st = self.gw_state(("awg2", "awg3"))
        cases = [
            (dict(HOST_OK, host={"ok": False, "why": "сервер — контейнер lxc"}), "host", "контейнер lxc"),
            (dict(HOST_OK, kernel="4.19.0-27-amd64"), "kernel", "слишком старое"),
            (dict(HOST_OK, headers="missing"), "headers", "linux-headers-%s" % K),
            (dict(HOST_OK, apt=False), "packages", "Нет apt"),
            (dict(HOST_OK, dkms="missing", gcc=False), "packages", "dkms нет"),
            (dict(HOST_OK, headers="available", free_mb=300), "disk", "свободно 300 МБ"),
        ]
        for host, code, text in cases:
            chk = awgengine.check(self.r, st=st, pool=[], host=host)
            self.assertIn(code, chk["codes"], code)
            self.assertIn(text, " ".join(chk["blockers"]), code)
            self.assertFalse(chk["can"], code)
        chk = awgengine.check(self.r, st=st, pool=[], host=dict(HOST_OK))
        self.assertEqual(chk["codes"], [])                            # 3.1 goes onto the module with the set
        self.assertTrue(chk["can"])
        self.assertEqual(chk["warnings"], [])

    def test_inbound_31_and_the_set_of_the_module(self):
        st = self.gw_state(("awg2", "awg3"))
        self.kmod(True)
        self.module_with_set(False)                                   # a node of an older Kaskad: no set in memory
        self.assertEqual(awgengine.mix(self.r, st=st, pool=[])[0][:24], "Протокол AmneziaWG 3.1: ")
        self.assertIn("kaskadctl gw accel on", awgengine.mix(self.r, st=st, pool=[])[0])
        self.assertNotIn("пока не умеет", awgengine.mix(self.r, st=st, pool=[])[0])
        chk = awgengine.check(self.r, st=st, pool=[], host=dict(HOST_OK))
        self.assertEqual(chk["codes"], [])                            # the switch rebuilds it: not a blocker
        self.assertIn("пересоберёт", chk["warnings"][0])
        self.module_with_set(True)
        self.assertEqual(awgengine.mix(self.r, st=st, pool=[]), [])
        self.assertEqual(awgengine.status(self.r, st=st, pool=[])["warnings"], [])
        self.assertEqual(awgengine.check(self.r, st=st, pool=[], host=dict(HOST_OK))["warnings"], [])
        os.unlink(self.path("/sys/module/amneziawg/version"))
        os.rmdir(awgkmod.module_dir())
        self.kmod(False, loaded=False)                                # on the program; the set failed here before
        doc = awgkmod.load()
        doc["set_failed"] = {"kernel": K, "reason": "error: x"}
        awgkmod.save(doc)
        self.assertTrue(awgengine.mix(self.r, st=st, pool=[]))        # as now: 3.1 on the program
        chk = awgengine.check(self.r, st=st, pool=[], host=dict(HOST_OK))
        self.assertEqual(chk["codes"], [])                            # the switch tries again
        self.assertIn("не встал (error: x)", chk["warnings"][0])

    def test_a_leg_of_an_unknown_line_mixes_with_words_what_to_do_and_31_15_go_onto_the_module(self):
        st = self.gw_state(("awg2",))
        chk = awgengine.check(self.r, st=st, pool=[leg(2, "2.0"), leg(3, "3.1"), leg(4, "1.5"), leg(5, "1.0"),
                                                   leg(6, "")], host=dict(HOST_OK))
        self.assertEqual(chk["codes"], ["mix"])
        self.assertEqual(len(chk["blockers"]), 1)              # the legs of 3.1 and 1.x: with the set, on the module
        self.assertIn("ключ AmneziaWG неизвестной линии", chk["blockers"][0])
        self.assertIn("замените этот выход ключом AmneziaWG 2.0", chk["blockers"][0])
        self.assertNotIn("#222", " ".join(chk["blockers"] + chk["warnings"]))

    def test_a_leg_of_31_and_the_set_of_the_module(self):
        st = self.gw_state(("awg2",), engine={"want": "kernel"})
        pool = [leg(2, "2.0"), leg(3, "3.1")]
        self.kmod(True)
        self.module_with_set(True)                                       # with the set: the whole node on the module
        self.assertEqual([r["module"] for r in awgengine.legs_of(st, pool + [leg(4, "1.5"), leg(5, "1.0")])],
                         [True, True, True, True])
        self.assertEqual(awgengine.mix(self.r, st=st, pool=pool), [])
        self.assertEqual(awgengine.status(self.r, st=st, pool=pool)["warnings"], [])
        with self.fake_disable():
            self.assertIsNone(awgengine.reconcile(self.r, st=st, pool=pool))
        self.assertEqual(self.disables, [])
        self.module_with_set(False)                                      # without it: #222 — the program, as before
        texts = awgengine.mix(self.r, st=st, pool=pool)
        self.assertEqual(len(texts), 1)
        self.assertIn("ключ AmneziaWG 3.1", texts[0])
        self.assertIn("нет набора исправлений", texts[0])
        self.assertIn("#222", texts[0])
        chk = awgengine.check(self.r, st=st, pool=pool, host=dict(HOST_OK))
        self.assertEqual(chk["codes"], [])                               # the switch rebuilds it with the set
        self.assertIn("пересоберёт", chk["warnings"][0])
        t15 = awgengine.mix(self.r, st=st, pool=[leg(4, "1.5")])        # a leg of 1.5 without the set: as before
        self.assertEqual(len(t15), 1)
        self.assertIn("ключ AmneziaWG 1.5", t15[0])
        self.assertIn("нет набора исправлений", t15[0])
        self.assertNotIn("#222", t15[0])
        with self.fake_install(), self.legs(*pool):
            res = awgengine.switch(self.r, "kernel", host=dict(HOST_OK), verify=False)
        self.assertNotIn("unchanged", res)
        self.assertEqual(self.installs, [False])

    def test_an_untested_kernel_is_a_warning_and_disk_counts_only_with_packages(self):
        st = self.gw_state(("awg2",))
        chk = awgengine.check(self.r, st=st, pool=[], host=dict(HOST_OK, kernel="7.2.0-1-generic", free_mb=100))
        self.assertTrue(chk["can"])
        self.assertIn("не проверялось", chk["warnings"][0])

    def test_host_facts_by_commands(self):
        r = FakeRunner(binaries=("apt-cache", "systemd-detect-virt"))
        r.on(["uname", "-m"], "x86_64\n").on(["uname", "-r"], K + "\n")
        r.on(["systemd-detect-virt", "-c"], (1, "none\n", ""))
        r.on(["apt-cache", "show", "--no-all-versions", "linux-headers-" + K], "Package: linux-headers-%s\n" % K)
        r.on(["apt-cache", "show", "--no-all-versions", "dkms"], (100, "", "E: No packages found"))
        f = awgengine.host_facts(r)
        self.assertTrue(f["host"]["ok"])
        self.assertEqual((f["kernel"], f["headers"], f["dkms"], f["gcc"]), (K, "available", "missing", False))
        self.assertEqual(r.unmatched, [])
        r.on(["systemd-detect-virt", "-c"], (0, "lxc\n", ""))
        r._handlers.insert(0, (["systemd-detect-virt", "-c"], (0, "lxc\n", "")))
        self.assertFalse(awgengine.host_facts(r)["host"]["ok"])


class CheckLinesTest(RootCase):
    def test_free_disk_is_named_as_disk_in_gb_and_the_reconnect_is_honest(self):
        # T-N5 3.2.19: «ОЗУ 1,9 ГБ, свободно 26267 МБ» read as memory (№11); «≈2 с» was 14–23 s on the stand (№2)
        chk = {"can": True, "blockers": [], "warnings": [], "pays": [],
               "facts": {"kernel": K, "cpus": 1, "mem_total": 2040 * 2 ** 20, "free_mb": 26267},
               "install": awgengine.install_plan(dict(HOST_OK, headers="available"))}
        text = "\n".join(awgengine.check_lines(chk))
        self.assertIn("ОЗУ 2,0 ГБ, на диске свободно 25,7 ГБ", text)
        self.assertNotIn("26267 МБ", text)
        self.assertIn("Клиенты AmneziaWG переподключатся сами, обычно за 15–25 секунд; WireGuard не затрагивается.", text)
        self.assertNotIn("≈2 с", text)
        chk["facts"]["free_mb"] = 300
        self.assertIn("на диске свободно 300 МБ", "\n".join(awgengine.check_lines(chk)))


class SwitchTest(EngineCase):
    def test_kernel_backup_install_and_record(self):
        self.gw_state(("awg2",))
        secrets.set("awg2.server.private", "SERVERKEY")
        self.write("/etc/kaskad/gw/awg2/kgw-awg2.conf", "[Interface]\n")
        with self.fake_install():
            res = awgengine.switch(self.r, "kernel", why="человек: меню", host=dict(HOST_OK))
        self.assertTrue(res["ok"])
        self.assertEqual((res["was"], res["engine"]), ("program", "kernel"))
        self.assertEqual(self.installs, [False])
        b = res["backup"]
        self.assertTrue(os.path.basename(b).startswith("awg-engine-"))
        self.assertEqual(os.stat(b).st_mode & 0o777, 0o700)
        for rel in ("state.json", "secrets.json", "awg2/kgw-awg2.conf"):
            self.assertEqual(os.stat(os.path.join(b, rel)).st_mode & 0o777, 0o600, rel)
        rec = self.rec()
        self.assertEqual((rec["want"], rec["why"], rec["reason"]), ("kernel", "человек: меню", None))
        self.assertEqual(awgengine.active(), "kernel")

    def test_only_three_backups_are_kept(self):
        self.gw_state(("awg2",))
        for i in range(5):
            self.now += 10
            awgengine.backup("t%d" % i)
        left = sorted(os.listdir(paths.backups()))
        self.assertEqual(len(left), 3)

    def test_mixing_is_refused_with_code_3_and_force_passes(self):
        self.gw_state(("awg2", "awg3"))
        with self.fake_install(), self.legs(leg(3, "")):
            with self.assertRaises(ConflictError) as cm:
                awgengine.switch(self.r, "kernel", host=dict(HOST_OK))
            self.assertEqual(cm.exception.code, 3)
            self.assertIn("--force", str(cm.exception))
            self.assertEqual(self.installs, [])
            self.assertEqual(self.rec()["code"], "mix")
            res = awgengine.switch(self.r, "kernel", force=True, host=dict(HOST_OK))
        self.assertEqual(self.installs, [True])
        self.assertTrue(self.rec()["force"])
        self.assertEqual(res["engine"], "kernel")

    def test_a_host_that_cannot_is_refused_with_code_5_and_remembered(self):
        self.gw_state(("awg2",))
        with self.fake_install():
            with self.assertRaises(UnsupportedError) as cm:
                awgengine.switch(self.r, "kernel", host=dict(HOST_OK, host={"ok": False, "why": "включён Secure Boot"}))
        self.assertEqual(cm.exception.code, 5)
        self.assertEqual(self.installs, [])
        rec = self.rec()
        self.assertEqual((rec["want"], rec["code"]), ("kernel", "host"))
        self.assertIn("Secure Boot", awgengine.status(self.r, pool=[])["text"])
        self.assertFalse(awgengine.status(self.r, pool=[])["offer"]["show"])

    def test_a_failed_build_leaves_the_program_with_the_reason(self):
        self.gw_state(("awg2",))
        with self.fake_install(fail=KaskadError("Модуль не собрался под ядро %s: error: x" % K)):
            with self.assertRaises(KaskadError):
                awgengine.switch(self.r, "kernel", host=dict(HOST_OK))
        rec = self.rec()
        self.assertEqual((rec["want"], rec["code"]), ("kernel", "build"))
        self.assertEqual(awgengine.active(), "program")

    def test_dry_run_changes_nothing(self):
        self.gw_state(("awg2", "awg3"))
        with self.fake_install(), self.legs(leg(3, "")):
            res = awgengine.switch(self.r, "kernel", dry_run=True, host=dict(HOST_OK))
        self.assertFalse(res["ok"])
        self.assertTrue(res["dry_run"])
        self.assertEqual(self.installs, [])
        self.assertFalse(os.path.isdir(paths.backups()))
        self.assertIsNone(self.rec()["want"])

    def test_rollback_when_the_alive_clients_do_not_come_back(self):
        self.gw_state(("awg2",))
        self.hs["kgw-awg2"] = "PUBA %d\nPUBB 0\n" % (self.now - 30)        # A was alive, B never connected
        with self.fake_install(), self.fake_disable():
            res = awgengine.switch(self.r, "kernel", host=dict(HOST_OK))
        self.assertFalse(res["ok"])
        self.assertTrue(res["rolled_back"])
        self.assertEqual(res["engine"], "program")
        self.assertEqual(self.disables, [True])
        self.assertGreaterEqual(sum(self.slept), awgengine.WAIT_CLIENTS)
        rec = self.rec()
        self.assertEqual((rec["want"], rec["code"]), ("program", "rollback"))
        self.assertIn("не переподключился за 3 мин", rec["reason"])

    def test_clients_that_come_back_keep_the_new_engine(self):
        self.gw_state(("awg2",))
        self.hs["kgw-awg2"] = "PUBA %d\n" % (self.now - 30)
        t0 = self.now

        def sleep(s):
            self.now += s
            self.hs["kgw-awg2"] = "PUBA %d\n" % (t0 + 7)

        with mock.patch.object(awgengine, "_sleep", sleep), self.fake_install(), self.fake_disable():
            res = awgengine.switch(self.r, "kernel", host=dict(HOST_OK))
        self.assertTrue(res["ok"])
        self.assertEqual(self.disables, [])
        self.assertEqual(self.rec()["want"], "kernel")

    def test_on_the_module_without_the_set_the_switch_rebuilds_it(self):
        self.gw_state(("awg2", "awg3"), engine={"want": "kernel"})
        self.kmod(True)
        self.module_with_set(False)
        with self.fake_install(), self.legs():
            res = awgengine.switch(self.r, "kernel", host=dict(HOST_OK), verify=False)
        self.assertNotIn("unchanged", res)
        self.assertEqual(self.installs, [False])
        self.module_with_set(True)
        with self.fake_install(), self.legs():
            self.assertTrue(awgengine.switch(self.r, "kernel", host=dict(HOST_OK), verify=False)["unchanged"])
        self.assertEqual(self.installs, [False])

    def test_to_the_program_and_back(self):
        self.gw_state(("awg2",))
        self.kmod(True)
        with self.fake_disable():
            res = awgengine.switch(self.r, "program", why="человек: панель")
        self.assertEqual(res["engine"], "program")
        self.assertEqual(self.disables, [True])
        self.assertEqual(self.rec()["want"], "program")
        s = awgengine.status(self.r, pool=[])
        self.assertEqual(s["text"], "Движок AmneziaWG: программа — так выбрано")
        self.assertFalse(s["offer"]["show"])
        with self.fake_disable():
            res = awgengine.switch(self.r, "program")
        self.assertTrue(res["unchanged"])
        self.assertEqual(self.disables, [True])


class FirstOfferReconcileTest(EngineCase):
    def test_first_inbound_goes_to_the_module_by_default(self):
        self.gw_state(())
        with self.fake_install(), mock.patch.object(awgengine, "host_facts", lambda r: dict(HOST_OK)):
            res = awgengine.first(self.r, kind="awg2")
        self.assertEqual(res["engine"], "kernel")
        rec = self.rec()
        self.assertEqual((rec["want"], rec["why"]), ("kernel", "установка: по умолчанию"))

    def test_first_with_no_is_the_program_and_nothing_is_installed(self):
        self.gw_state(())
        with self.fake_install():
            res = awgengine.first(self.r, choice="program", kind="awg2")
        self.assertEqual(res["engine"], "program")
        self.assertEqual(self.installs, [])
        self.assertEqual(self.rec()["want"], "program")
        with self.fake_install():
            self.assertIsNone(awgengine.first(self.r, kind="awg1"))           # a person chose: left alone
        self.assertEqual(self.installs, [])

    def test_first_inbound_31_goes_to_the_module_too(self):
        self.gw_state(())
        with self.fake_install(), self.legs(), mock.patch.object(awgengine, "host_facts", lambda r: dict(HOST_OK)):
            res = awgengine.first(self.r, kind="awg3")
        self.assertEqual(res["engine"], "kernel")
        self.assertEqual(self.installs, [False])
        rec = self.rec()
        self.assertEqual((rec["want"], rec["code"]), ("kernel", None))

    def test_a_failure_of_the_first_is_a_warning_not_an_error(self):
        self.gw_state(())
        with mock.patch.object(awgengine, "host_facts", lambda r: dict(HOST_OK, headers="missing")):
            res = awgengine.first(self.r, kind="awg2")
        self.assertEqual(res["engine"], "program")
        self.assertIn("заголовков", res["warnings"][0])
        self.assertEqual(self.rec()["code"], "headers")
        with self.fake_install():
            self.assertIsNone(awgengine.first(self.r, kind="awg1"))           # sticky: a person decides now
        self.assertEqual(self.installs, [])

    def test_ops_install_of_the_first_awg_inbound_asks_the_engine_first(self):
        self.gw_state((), enabled=False)
        seen = []
        with mock.patch.object(awgengine, "first", lambda r, **kw: seen.append(kw) or {"engine": "kernel",
                                                                                         "warnings": ["w"]}):
            self.assertIsNone(ops._awg_first(self.r, state.load(), "wg", None, None))
            self.assertEqual(ops._awg_first(self.r, state.load(), "awg2", None, None)["engine"], "kernel")
            ops._awg_first(self.r, state.load(), "awg2", "program", None)
            self.gw_state(("awg1",), enabled=False)
            self.assertIsNone(ops._awg_first(self.r, state.load(), "awg2", None, None))   # not the first one
        self.assertEqual([k.get("choice") for k in seen], [None, "program"])

    def test_offer_after_an_update(self):
        self.gw_state(("awg2",))
        with mock.patch.object(awgengine, "host_facts", lambda r: dict(HOST_OK)):
            res = awgengine.offer(self.r)
        self.assertTrue(res["pending"])
        s = awgengine.status(self.r, pool=[])
        self.assertTrue(s["offer"]["show"])
        self.assertTrue(s["offer"]["pending"])
        self.assertEqual(s["offer"]["blocked"], [])
        self.assertIn("рекомендуется", s["text"])
        self.assertEqual(awgengine.active(), "program")                       # never switched without a person

    def test_offer_on_a_container_records_why_and_shows_nothing(self):
        self.gw_state(("awg2",))
        with mock.patch.object(awgengine, "host_facts",
                               lambda r: dict(HOST_OK, host={"ok": False, "why": "сервер — контейнер lxc"})):
            res = awgengine.offer(self.r)
        self.assertFalse(res["pending"])
        s = awgengine.status(self.r, pool=[])
        self.assertFalse(s["offer"]["show"])
        self.assertIn("контейнер lxc", s["text"])

    def test_offer_with_a_leg_of_an_unknown_line_shows_the_blocker_instead_of_the_button(self):
        self.gw_state(("awg2", "awg3"))
        pool = [leg(3, "3.1"), leg(4, "1.5"), leg(5, "")]
        with mock.patch.object(awgengine, "host_facts", lambda r: dict(HOST_OK)), self.legs(*pool):
            self.assertTrue(awgengine.offer(self.r)["pending"])
        s = awgengine.status(self.r, pool=pool)
        self.assertTrue(s["offer"]["show"])
        self.assertEqual(len(s["offer"]["blocked"]), 1)        # the unknown leg; 3.1 and 1.5 are no blockers
        self.assertIn("неизвестной линии", s["offer"]["blocked"][0])
        self.assertEqual(awgengine.status(self.r, pool=[leg(3, "3.1")])["offer"]["blocked"], [])

    def test_reconcile_moves_the_whole_node_to_the_program(self):
        self.gw_state(("awg2",), engine={"want": "kernel"})
        self.kmod(True)
        with self.fake_disable():
            self.assertIsNone(awgengine.reconcile(self.r, pool=[leg(2, "2.0")]))
            res = awgengine.reconcile(self.r, pool=[leg(2, "2.0"), leg(3, "3.1")])
        self.assertTrue(res["switched"])
        self.assertEqual(self.disables, [True])
        rec = self.rec()
        self.assertEqual((rec["want"], rec["code"]), ("kernel", "mix"))
        self.assertIn("нога 3.1", rec["reason"])
        self.assertNotIn("пока не умеет", rec["reason"])
        s = awgengine.status(self.r, pool=[leg(2, "2.0"), leg(3, "3.1")])
        self.assertIn("3.1", s["text"])
        self.assertTrue(s["offer"]["show"])            # the switch rebuilds the module with the set: the button
        self.assertEqual(s["offer"]["blocked"], [])

    def test_reconcile_inbound_31_and_the_set(self):
        self.gw_state(("awg2", "awg3"), engine={"want": "kernel"})
        self.kmod(True)
        self.module_with_set(True)
        with self.fake_disable():
            self.assertIsNone(awgengine.reconcile(self.r, pool=[]))          # the module takes 3.1: nothing to do
        self.module_with_set(False)                                          # a module without the set
        with self.fake_disable():
            res = awgengine.reconcile(self.r, pool=[])
        self.assertTrue(res["switched"])
        self.assertIn("протокол 3.1 без набора исправлений модуля", self.rec()["reason"])

    def test_reconcile_keeps_a_forced_mix_and_warns(self):
        self.gw_state(("awg2", "awg3"), engine={"want": "kernel", "force": True})
        self.kmod(True)
        with self.fake_disable():
            self.assertIsNone(awgengine.reconcile(self.r, pool=[]))
        self.assertEqual(self.disables, [])
        s = awgengine.status(self.r, pool=[])
        self.assertIn("смешанный режим", s["warnings"][0])
        self.assertIn("смешивание включено вручную", s["text"])

    def test_nothing_runs_without_auto(self):
        self.gw_state(("awg2", "awg3"), engine={"want": "kernel"})
        self.kmod(True)
        with mock.patch.object(awgengine, "AUTO", False), self.fake_disable(), self.fake_install():
            self.assertIsNone(awgengine.reconcile(self.r, pool=[]))
            self.assertIsNone(awgengine.first(self.r, kind="awg2"))
        self.assertEqual((self.disables, self.installs), ([], []))


class StatusTest(EngineCase):
    def test_texts(self):
        self.gw_state(())
        s = awgengine.status(self.r, pool=[])
        self.assertFalse(s["has_awg"])
        self.assertIn("по умолчанию для новых протоколов", s["text"])
        self.assertFalse(s["offer"]["show"])
        self.gw_state(("awg2",))
        self.kmod(True)
        self.assertEqual(awgengine.status(self.r, pool=[])["text"], "Движок AmneziaWG: модуль ядра")
        self.kmod(True, loaded=False)
        if os.path.isdir(awgkmod.module_dir()):
            os.rmdir(awgkmod.module_dir())
        s = awgengine.status(self.r, pool=[])
        self.assertTrue(s["fallback"])
        self.assertIn("откат", s["text"])
        self.assertFalse(s["offer"]["show"])


class StaleKernelTest(EngineCase):
    """T-N5 3.2.19-r2, Н-1: the reason «для ядра X нет заголовков» is about one kernel — after a reboot into another
    one the faces (gw status, the engine page, the panel) judge by the running kernel, not by the old words."""
    OLD, NEW = "6.8.0-48-generic", "6.8.0-146-generic"

    def boot(self, kernel):
        self.write("/proc/sys/kernel/osrelease", kernel + "\n")

    def test_a_reason_about_another_kernel_is_not_shown_and_the_module_is_offered(self):
        self.gw_state(("awg2",))
        self.boot(self.OLD)
        with mock.patch.object(awgengine, "host_facts", lambda r: dict(HOST_OK, kernel=self.OLD, headers="missing")):
            res = awgengine.offer(self.r)
        self.assertFalse(res["pending"])
        self.assertEqual((self.rec()["code"], self.rec()["kernel"]), ("headers", self.OLD))
        s = awgengine.status(self.r, pool=[])
        self.assertIn("нет заголовков", s["text"])
        self.assertFalse(s["offer"]["show"])
        self.boot(self.NEW)                                    # apt full-upgrade + reboot
        s = awgengine.status(self.r, pool=[])
        self.assertNotIn(self.OLD + " нет", s["text"])
        self.assertNotIn("заголовков", s["text"])
        self.assertIn("теперь на ядре %s" % self.NEW, s["text"])
        self.assertTrue(s["offer"]["show"])
        self.assertEqual(s["offer"]["blocked"], [])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("kaskad.gw.cli._r", lambda: self.r), \
                mock.patch.object(exits, "pool", lambda runner=None, parser=None, roles=("abroad",): []):
            cli.main(["gw", "awg", "status"])
        self.assertNotIn("заголовков", out.getvalue())
        self.assertIn("Перейти на модуль ядра", out.getvalue())
        # the next offer (an update) under the new kernel: the old reason is wiped, the advice stands alone
        with mock.patch.object(awgengine, "host_facts", lambda r: dict(HOST_OK, kernel=self.NEW)):
            self.assertTrue(awgengine.offer(self.r)["pending"])
        rec = self.rec()
        self.assertEqual((rec["reason"], rec["code"], rec["pending"]), (None, None, True))

    def test_a_record_from_before_3_2_20_is_judged_by_the_kernel_in_its_words(self):
        why = ("Для работающего ядра %s нет заголовков (пакета linux-headers-%s) — модуль не собрать."
               % (self.OLD, self.OLD))
        self.gw_state(("awg2",), engine={"want": "kernel", "reason": why, "code": "headers"})
        self.boot(self.OLD)
        self.assertIn("нет заголовков", awgengine.status(self.r, pool=[])["text"])
        self.boot(self.NEW)
        s = awgengine.status(self.r, pool=[])
        self.assertNotIn("заголовков", s["text"])
        self.assertTrue(s["offer"]["show"])

    def test_a_reason_not_about_the_kernel_stays(self):
        self.gw_state(("awg2",), engine={"want": "kernel", "reason": "Здесь модуль ядра не загрузить: включён Secure "
                                         "Boot.", "code": "host", "kernel": self.OLD})
        self.boot(self.NEW)
        s = awgengine.status(self.r, pool=[])
        self.assertIn("Secure Boot", s["text"])
        self.assertFalse(s["offer"]["show"])
        self.assertIsNone(awgengine.stale_kernel(self.rec(), now=""))            # the running kernel unknown


class ExportImportTest(EngineCase):
    def test_round_trip_to_another_node(self):
        self.gw_state(("awg2",), enabled=False)
        secrets.set("awg2.server.private", "SERVERKEY")
        secrets.set("dev.d-00000001.awg2.private", "DEVKEY")
        secrets.set("dev.d-00000001.awg2.psk", "PSK")
        secrets.set("bot.token", "NOT-EXPORTED")
        out = os.path.join(self.root, "awg-export.json")
        res = awgengine.export(out)
        self.assertEqual((res["inbounds"], res["users"], res["devices"], res["secrets"]), (["awg2"], 1, 1, 3))
        self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)
        with open(out, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("NOT-EXPORTED", text)
        doc = json.loads(text)
        # another node: empty gateway (switched off), its own secrets
        st = state.empty()
        st["engine"] = "nft"
        st["gateway"] = {"enabled": False, "inbounds": {}, "users": [], "devices": []}
        state.save(st)
        os.unlink(paths.secrets_file())
        secrets.set("bot.token", "OTHER")
        res = awgengine.import_(self.r, out)
        self.assertEqual((res["inbounds"], res["users"], res["devices"]), (["awg2"], 1, 1))
        st = state.load()
        self.assertEqual(st["gateway"]["inbounds"]["awg2"], doc["inbounds"]["awg2"])
        self.assertEqual(st["gateway"]["devices"][0]["ips"], {"awg2": "10.77.3.10"})
        sec = secrets.load()
        self.assertEqual((sec["awg2.server.private"], sec["dev.d-00000001.awg2.psk"], sec["bot.token"]),
                         ("SERVERKEY", "PSK", "OTHER"))
        res = awgengine.import_(self.r, out)                                  # again: nobody twice
        self.assertEqual((res["users"], res["devices"]), (0, 0))
        self.assertEqual(len(state.load()["gateway"]["devices"]), 1)

    def test_a_foreign_file_is_refused(self):
        p = self.write("/tmp/x.json", json.dumps({"kind": "other"}))
        with self.assertRaises(KaskadError):
            awgengine.import_(self.r, p)
        p = self.write("/tmp/y.json", json.dumps({"kind": awgengine.EXPORT_KIND, "v": 1, "inbounds": {},
                                                  "users": [], "devices": [], "secrets": {"bot.token": "x"}}))
        with self.assertRaises(KaskadError) as cm:
            awgengine.import_(self.r, p)
        self.assertIn("чужие секреты", str(cm.exception))

    def test_the_file_is_checked_in_words_before_anything(self):
        # T-N5 3.2.19, №8: the menu asked «импортировать?» first and then said «Expecting value: line 1 column 1»
        with self.assertRaises(KaskadError) as cm:
            awgengine.check_export(os.path.join(self.root, "nonexistent.json"))
        self.assertIn("Нет файла", str(cm.exception))
        p = self.write("/tmp/junk.json", "not json at all")
        with self.assertRaises(KaskadError) as cm:
            awgengine.check_export(p)
        self.assertIn("не файл экспорта AmneziaWG Kaskad: внутри не JSON", str(cm.exception))
        self.assertNotIn("Expecting", str(cm.exception))
        with self.assertRaises(KaskadError) as cm:
            awgengine.check_export(os.path.dirname(p))
        self.assertIn("это папка", str(cm.exception))
        p = self.write("/tmp/half.json", json.dumps({"kind": awgengine.EXPORT_KIND, "v": 1, "inbounds": {}}))
        with self.assertRaises(KaskadError) as cm:
            awgengine.check_export(p, "Файл")
        self.assertEqual(str(cm.exception), "Файл неполный: нет раздела «secrets» — сделайте экспорт заново.")
        p = self.write("/tmp/ok.json", json.dumps({"kind": awgengine.EXPORT_KIND, "v": 1, "inbounds": {"awg2": {}},
                                                   "users": [{}], "devices": [{}, {}], "secrets": {}}))
        self.assertEqual(awgengine.check_export(p), {"ok": True, "inbounds": ["awg2"], "users": 1, "devices": 2})
        # nothing changed — the file on disk, byte for byte (state.load() fills a missing «added» with the clock: a
        # comparison of two loads flaked at a second boundary)
        st = util.read_text(paths.state())
        with mock.patch("kaskad.gw.cli._r", lambda: self.r), contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["gw", "awg", "import", "--file", p, "--check"]), 0)
            self.assertNotEqual(cli.main(["gw", "awg", "import", "--file", p + ".nope", "--check"]), 0)
        self.assertIn("Файл экспорта AmneziaWG: входы awg2, пользователей 1, устройств 2.", out.getvalue())
        self.assertEqual(util.read_text(paths.state()), st)                             # nothing changed


class AccelGuardTest(EngineCase):
    def test_accel_on_refuses_mixing_before_any_command(self):
        self.gw_state(("awg2", "awg3"))
        with self.assertRaises(ConflictError) as cm, self.legs(leg(3, "")):
            awgkmod.install(self.r)
        self.assertEqual(cm.exception.code, 3)
        self.assertIn("--force", str(cm.exception))
        self.assertEqual(self.r.calls, [])

    def test_cli_codes(self):
        self.gw_state(("awg2", "awg3"))
        with mock.patch.object(awgengine, "host_facts", lambda r: dict(HOST_OK)), self.legs(leg(3, "")), \
                mock.patch("kaskad.gw.cli._r", lambda: self.r), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["gw", "awg", "engine", "kernel", "--json"])
            self.assertEqual(rc, 3)
            rc = cli.main(["gw", "awg", "check", "--json"])
            self.assertEqual(rc, 0)
            rc = cli.main(["gw", "accel", "on", "--json"])
            self.assertEqual(rc, 3)
        last = json.loads(out.getvalue().strip().splitlines()[1])
        self.assertEqual(last["codes"], ["mix"])


class S4MtuTest(EngineCase):
    """§6.1: one rule of S4 — the budget of the outer packet (1400 or a narrower card) − 80 − the client MTU."""

    def test_generated_s4_fits_the_budget(self):
        self.assertEqual(awgkeys.s4_max(), 40)                       # 1400 − 80 − 1280
        self.assertEqual(awgkeys.s4_max(1380), 20)
        self.assertEqual(awgkeys.s4_max(1500, 1420), 0)
        for budget, top in ((1400, 40), (1380, 20), (1300, 15), (1500, 140)):
            for _ in range(300):                                    # random: only the bounds are checked
                p = awgkeys.gen_params("2.0", budget=budget)
                s4 = int(p["S4"])
                self.assertTrue(15 <= s4 <= top, (budget, s4))
                self.assertEqual(p["MTU"], "1280")
            # the top of the range is reachable: the generator asked for [15, top] (deterministic, no sampling)
            asked = []
            real = awgkeys._rand_int

            def spy(lo, hi):
                asked.append((lo, hi))
                return real(lo, hi)
            with mock.patch.object(awgkeys, "_rand_int", spy):
                awgkeys.gen_params("2.0", budget=budget)
            self.assertIn((15, top), asked)
            # S1 = S2 = 100 (sizes 248, 192) never collide with 32 + top: the top itself is a valid S4
            with mock.patch.object(awgkeys, "_rand_int", lambda lo, hi: hi if (lo, hi) == (15, top) else real(lo, hi)):
                self.assertEqual(awgkeys._gen_s3_s4(100, 100, awgkeys.s4_max(budget))[1], top)
        for line in ("1.0", "1.5"):
            self.assertEqual(awgkeys.gen_params(line)["S4"], "")
        self.assertEqual(awgkeys.gen_params("3.1")["S4"], "12")
        self.assertFalse(hasattr(awgkeys, "s4_limit"))              # the old rule (≤ 1500 − 48 − 32 − MTU) is gone

    def test_gw_status_warns_of_a_narrow_card(self):
        self.gw_state(("awg2",), enabled=False)
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg2"]["params"] = {"S4": "60", "MTU": "1280"}
            st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": 1400}}
        rows = dict((r["kind"], r) for r in ops.inbound_list(self.r, with_status=False))
        self.assertEqual(rows["awg2"]["mtu"], {"server": 1248, "client": 1280, "narrow": True, "low": False,
                                               "offer": "mtu", "kept": False})
        self.assertIn("карта сервера 1400 уже, чем нужно клиентам с MTU 1280 при S4 = 60", rows["awg2"]["warnings"][0])
        with state.transaction() as st:
            st["gateway"]["mtu"]["nic"]["mtu"] = 1500                # a card of 1500: S4 = 120 — the server below 1232
            st["gateway"]["inbounds"]["awg2"]["params"] = {"S4": "120", "MTU": "1280"}
        rows = dict((r["kind"], r) for r in ops.inbound_list(self.r, with_status=False))
        self.assertEqual(rows["awg2"]["mtu"], {"server": 1200, "client": 1280, "narrow": False, "low": True,
                                               "offer": "s4", "kept": False})
        self.assertIn("сервер уменьшил MTU до 1200; HTTP/3 через этот протокол перейдёт на TCP", rows["awg2"]["warnings"][0])
