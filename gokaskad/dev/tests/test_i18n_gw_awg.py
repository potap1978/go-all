"""3.3.2 E1-awg: English of the AmneziaWG kernel module and engine (gw/awgkmod.py, gw/awgengine.py), gw/sys.py and the
safe MTU (gw/mtu.py): the same paths speak the language of the context; the journal, the kernel hook and files on the
server speak the language of the server; the engine's record keeps codes (who chose, why it failed), not words."""

import os
import re
import unittest
from unittest import mock

from kaskad import paths, util
from kaskad.errors import KaskadError, UnsupportedError
from kaskad.gw import awgengine, awgkmod, mtu as gwmtu, sys as gsys
from kaskad.i18n import check, use_lang
from kaskad.sysrun import FakeRunner
from tests.helpers import RootCase
from tests.serverlang import server

CYR = re.compile("[А-Яа-яЁё]")
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "gw")
FILES = [os.path.join(LIB, n) for n in ("awgkmod.py", "awgengine.py", "sys.py", "mtu.py")]
AREAS = ["gw_awgkmod", "gw_awgengine", "gw_sys", "gw_mtu"]
K = "6.8.0-50-generic"
HOST_OK = {"host": {"ok": True, "why": "", "arch": "x86_64", "container": "", "secure_boot": False},
           "kernel": K, "headers": "installed", "apt": True, "dkms": "installed", "gcc": True, "free_mb": 5000}


def no_cyr(case, text):
    case.assertIsNone(CYR.search(text), text)


class CatalogTest(unittest.TestCase):
    def test_no_russian_left_in_code(self):
        check.assert_no_cyrillic(self, *FILES)

    def test_catalogs_complete(self):
        check.assert_catalog_complete(self, AREAS)


class EngineEnglishTest(RootCase):
    def test_check_lines_and_status_text(self):
        chk = {"can": True, "blockers": [], "warnings": [], "pays": [],
               "facts": {"kernel": K, "cpus": 1, "mem_total": 2040 * 2 ** 20, "free_mb": 26267},
               "install": awgengine.install_plan(dict(HOST_OK, headers="available"))}
        with use_lang("en"):
            chk["install"] = awgengine.install_plan(dict(HOST_OK, headers="available"))
            text = "\n".join(awgengine.check_lines(chk))
            self.assertIn("The kernel module can be turned on here.", text)
            self.assertIn("RAM 2.0 GB, free disk space 25.7 GB", text)
            self.assertIn("AmneziaWG clients reconnect on their own, usually within 15–25 seconds", text)
            no_cyr(self, text)
            self.assertEqual(awgengine.fmt_bytes(300 * 2 ** 20), "300 MB")
            self.assertEqual(awgengine.text_of({"active": "program", "want": None, "has_awg": True}),
                             "AmneziaWG engine: userspace — the kernel module hasn’t been turned on yet (recommended)")
            self.assertEqual(awgengine.TITLE["kernel"], "kernel module")
            no_cyr(self, awgengine.RECONNECT + " ".join(awgengine.BENEFIT + awgengine.RISKS) + awgengine.MIX_COST)
        with use_lang("ru"):
            self.assertIn("ОЗУ 2,0 ГБ, на диске свободно 25,7 ГБ", "\n".join(awgengine.check_lines(chk)))
            self.assertEqual(awgengine.fmt_bytes(300 * 2 ** 20), "300 МБ")
            self.assertEqual(awgengine.TITLE["program"], "программа amneziawg-go")

    def test_blockers_mix_and_warning(self):
        ins = [{"kind": "awg3", "line": "3.1", "title": "AmneziaWG 3.1", "module": False}]
        legs = [{"id": "e1", "iface": "kawg1", "line": "3.1", "name": "nl", "module": False},
                {"id": "e2", "iface": "kawg2", "line": "", "name": "x", "module": False}]
        f = dict(HOST_OK, headers="missing", apt=True, free_mb=100, dkms="missing", gcc=False,
                 host={"ok": False, "why": "x"})
        with use_lang("en"):
            bl, warns = awgengine.blockers_of(f, ins, legs)
            codes = [c for c, _ in bl]
            self.assertEqual(codes, ["host", "headers", "packages", "disk", "mix", "mix", "mix"])
            for _, t in bl:
                no_cyr(self, t)
            self.assertIn("Exit “nl” — an AmneziaWG 3.1 key", bl[5][1])
            self.assertEqual(awgengine.mix_warning([1, 2])[0][:22], "mixed mode: 2 lines of")
            self.assertIn("1 line of", awgengine.mix_warning([1])[0])
        with use_lang("ru"):
            self.assertIn("5 линий AmneziaWG", awgengine.mix_warning([1] * 5)[0])
            self.assertIn("21 линия AmneziaWG", awgengine.mix_warning([1] * 21)[0])

    def test_import_errors_in_english(self):
        with use_lang("en"):
            with self.assertRaises(KaskadError) as cm:
                awgengine.read_export(self.path("/nope.json"))
            self.assertEqual(str(cm.exception), "No file %s — check the path." % self.path("/nope.json"))
            p = self.write("/x.json", "not json")
            with self.assertRaises(KaskadError) as cm:
                awgengine.read_export(p)
            self.assertTrue(str(cm.exception).startswith("File %s isn’t a Kaskad AmneziaWG export file" % p))

    def test_failure_code_from_data_not_words(self):
        # the words are in the language of the moment: the code of the record comes from data.kind
        self.assertEqual(awgengine._code_of(KaskadError("Self-test failed", data={"kind": "selftest"})), "selftest")
        self.assertEqual(awgengine._code_of(KaskadError("anything", data={"kind": "headers"})), "headers")
        self.assertEqual(awgengine._code_of(KaskadError("Самопроверка модуля не прошла")), "build")
        self.assertEqual(awgengine._code_of(UnsupportedError("no")), "host")
        self.assertEqual(awgengine._code_of(KaskadError("apt", data={"kind": "apt_busy"})), "packages")   # gw/sys
        with use_lang("en"):
            e = awgkmod._fail("selftest", "gw_awgkmod.err.selftest", detail="x")
        self.assertEqual((awgengine._code_of(e), str(e)[:21]), ("selftest", "The module self-test "))

    def test_why_is_a_code_shown_in_words(self):
        self.assertEqual(awgengine.why_code("человек: меню"), "person:menu")                 # an old install.sh / record
        self.assertEqual(awgengine.why_code("person:menu-update"), "person:menu-update")
        self.assertEqual(awgengine.why_code("первая нога AmneziaWG"), "awg:first-leg")
        self.assertEqual(awgengine.why_code("CLI"), "cli")
        self.assertEqual(awgengine.why_code("person: panel"), "person:panel")              # the English words of a code
        self.assertEqual(awgengine.why_code("MCP"), "mcp")
        self.assertEqual(awgengine.why_code("скрипт владельца"), "скрипт владельца")  # free text stays as is
        with use_lang("en"):
            self.assertEqual(awgengine.why_text("awg:first-leg"), "first AmneziaWG leg")
            self.assertEqual(awgengine.why_text("человек: меню (после обновления)"), "person: SSH menu (after an update)")
        with use_lang("ru"):
            self.assertEqual(awgengine.why_text("install:default"), "установка: по умолчанию")

    def test_record_keeps_the_code(self):
        self.write("/etc/kaskad/state.json", '{"schema": 1, "engine": "nft", "forwards": []}\n')
        awgengine.save_record(want="kernel", why="человек: меню")
        from kaskad import state
        raw = (state.load().get("gateway") or {}).get("awg_engine") or {}
        self.assertEqual(raw.get("why"), "person:menu")
        with use_lang("en"):
            self.assertEqual(awgengine.record(state.load())["why"], "person: SSH menu")
        self.assertEqual(awgengine.record(state.load())["why_code"], "person:menu")


class KmodEnglishTest(RootCase):
    def test_lines_and_problems(self):
        st = {"enabled": True, "loaded": False, "kernel": K, "version": awgkmod.VERSION,
              "fallback": {"reason": None}, "inbounds": [{"kind": "awg2", "text": awgkmod.CORE_TEXT["userspace"]}],
              "legs": [], "kernel_lines": ["1.5", "2.0"], "leg_lines": ["2.0"],
              "set": {"id": awgkmod.SET_ID, "failed": {"reason": "x"}}, "selftest": {"ok": False, "detail": "d", "at": "t"},
              "warnings": awgkmod.warnings({"enabled": True, "loaded": False, "kernel": K})}
        with use_lang("en"):
            st["inbounds"][0]["text"] = awgkmod.CORE_TEXT["userspace"]
            st["warnings"] = awgkmod.warnings({"enabled": True, "loaded": False, "kernel": K})
            text = "\n".join(awgkmod.lines(st))
            self.assertIn("AmneziaWG acceleration: on, but fell back to amneziawg-go", text)
            self.assertIn("inbounds: awg2 — userspace", text)
            self.assertIn("self-test: FAILED (d, t)", text)
            no_cyr(self, text)
            self.assertIn("issue #222", awgkmod.LEG31_WHY)
            no_cyr(self, awgkmod.LEG31_WHY + awgkmod.TRAILERS_LOSS_TEXT)
            self.assertEqual(awgkmod.module_rules("3.1", {"S4": "4"})[-1:],
                             ["S4 = 4: line 3.1 on the kernel module needs S4 of at least 12"])
            self.assertEqual(awgkmod.check_file(self.path("/none.conf")), ["no file %s" % self.path("/none.conf")])
        with use_lang("ru"):
            self.assertIn("нет набора исправлений", awgkmod.LEG31_WHY)

    def test_host_refusals(self):
        r = FakeRunner(binaries=("systemd-detect-virt",))
        r.on(["uname", "-m"], "x86_64\n").on(["systemd-detect-virt", "-c"], (0, "lxc\n", ""))
        with use_lang("en"):
            why = awgkmod.supported_host(r)["why"]
        self.assertTrue(why.startswith("the server is a lxc container"), why)

    def test_hook_and_journal_speak_the_server_language(self):
        with server("en"):
            with use_lang("ru"):                          # a Russian request on an English server
                hook = awgkmod.hook_text()
                awgkmod._log("gw_awgkmod.log.loaded", kernel=K)
        self.assertIn('say "module built for kernel $ver"', hook)
        no_cyr(self, hook)
        log = util.read_text(paths.log()) or ""
        self.assertIn("AWG KMOD: kernel %s: module loaded" % K, log)
        with server("ru"):
            with use_lang("en"):
                self.assertIn('say "модуль собран под ядро $ver"', awgkmod.hook_text())


class SysMtuEnglishTest(RootCase):
    def test_sys_errors(self):
        r = FakeRunner()
        with use_lang("en"):
            with self.assertRaises(KaskadError) as cm:
                gsys.systemd(r, "start", "bad unit")
            self.assertEqual(str(cm.exception), "invalid service name 'bad unit'")
            with self.assertRaises(KaskadError) as cm:
                gsys.apt_install(r, ["Bad!"])
            self.assertEqual(str(cm.exception), "invalid package name 'Bad!'")

    def test_networkd_journal_in_server_language(self):
        with mock.patch.object(gsys, "networkd_installed", lambda: True), \
                server("en"), use_lang("ru"):
            self.assertTrue(gsys.networkd_keep_foreign())
        self.assertIn("written — a restart of systemd-networkd no longer wipes Kaskad’s rules", util.read_text(paths.log()))

    def _st(self, nic):
        return {"gateway": {"inbounds": {"awg2": {"params": {"S4": "40", "MTU": "1280"}}},
                            "mtu": {"nic": {"dev": "eth0", "mtu": nic}}}}

    def test_mtu_texts(self):
        st = self._st(1250)
        p = gwmtu.inbound_plan(st, "awg2")
        with use_lang("en"):
            text = gwmtu.narrow_text("awg2", p, 1250)
            self.assertTrue(text.startswith("AmneziaWG 2.0: the server’s network interface MTU (1250) is lower"), text)
            self.assertIn("1400 bytes", text)
            no_cyr(self, gwmtu.offer_detail("awg2", p, 1250))
            lines = gwmtu.status_lines(gwmtu.view(st))
            self.assertEqual(lines[0], "MTU: NIC 1250 (eth0), budget 1250")
            no_cyr(self, "\n".join(lines))
        with use_lang("ru"):
            self.assertEqual(gwmtu.status_lines(gwmtu.view(st))[0], "MTU: карта 1250 (eth0), бюджет 1250")

    def test_refresh_notes_go_to_the_journal_in_the_server_language(self):
        st = self._st(1500)
        gwmtu.refresh(st, {"dev": "eth0", "mtu": 1500})
        with server("en"), use_lang("ru"):
            notes = gwmtu.refresh(st, {"dev": "eth0", "mtu": 1300})
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Server MTU of inbound awg2: "), notes)


if __name__ == "__main__":
    unittest.main()
