"""gw/awgkmod.py — why the module did not build, from make.log (23.10.2026; 20.10: Ubuntu 6.8, 458 MB — «gcc-13: fatal
error: Killed signal terminated program cc1», and the person saw only «make: Leaving directory»). Every kind of failure
in words, RU and EN, with the path of make.log — within the 300 characters the engine's record, the panel and the bot
keep; out of memory below 2 GB — a server from 2 GB recommended (the owner, 23.10: no temporary swap, below 2 GB not
supported). Only the words change: the set of 3.1 that did not build is taken off and the module built without it, as
before (REV-kmod3 С1); the path in set_failed leads to the errors of the build with the set (REV-kmod3 З2)."""

import json
import os
import re
from unittest import mock

from kaskad import i18n, util
from kaskad.gw import awgengine, awgkmod
from kaskad.sysrun import Result
from tests import test_gw_awgkmod
from tests.test_gw_awgkmod import K, KmodCase

LOG = "/var/lib/dkms/amneziawg/%s/build/make.log" % awgkmod.VERSION
TREE = "/var/lib/dkms/amneziawg/%s/build/" % awgkmod.VERSION
KEPT = "/var/lib/kaskad/gw/awg-kmod-set-make.log"
OOM_LOG = ("DKMS make.log for amneziawg-%s for kernel %s (x86_64)\n"
           "make: Entering directory '/usr/src/linux-headers-%s'\n"
           "  CC [M]  %ssend.o\n"
           "gcc-13: fatal error: Killed signal terminated program cc1\n"
           "compilation terminated.\n"
           "make[2]: *** [scripts/Makefile.build:243: send.o] Error 1\n"
           "make: Leaving directory '/usr/src/linux-headers-%s'\n")
COMPILE = ("  CC [M]  {b}socket.o\n{b}socket.c:434:9: error: passing argument 2 of 'setup_udp_tunnel_sock' from "
           "incompatible pointer type [-Werror=incompatible-pointer-types]\n{b}socket.c:440:5: error: too many "
           "arguments to function 'udp_tunnel_sock_release'\n{b}socket.c:441:5: error: third\n"
           "make[2]: *** Error 1\n").format(b=TREE)
NOTHING = "  CC [M]  main.o\nmake: Leaving directory '/x'\n"
MEM_SMALL = "MemTotal:  469000 kB\nMemFree:  60000 kB\nMemAvailable:  421888 kB\nSwapTotal:  0 kB\n"
MEM_BIG = "MemTotal:  4013000 kB\nMemFree:  60000 kB\nMemAvailable:  421888 kB\nSwapTotal:  0 kB\n"
MEM_2GB = "MemTotal:  1872000 kB\nMemAvailable:  421888 kB\n"          # a «2 GB» plan: the kernel keeps its part
DKMS_FAILED = "Error! Bad return status for module build on kernel: %s (x86_64)\nConsult %s for more information.\n"


def oom_log(kernel=K):
    return OOM_LOG % (awgkmod.VERSION, kernel, kernel, TREE, kernel)


def lang(code):
    return mock.patch.object(awgkmod, "tr", lambda key, *a, **kw: i18n.tr(key, code, **kw))


class LogReasonTest(KmodCase):
    """_build_error: every kind of failure in make.log in words, with the path of the log."""

    def failed(self, log_text, meminfo=MEM_SMALL, kernel=K):
        if meminfo is not None:
            self.write("/proc/meminfo", meminfo)
        if log_text is not None:
            self.write(LOG, log_text)
        return awgkmod._build_error(Result(["dkms", "build"], 10, DKMS_FAILED % (kernel, self.path(LOG)), ""))

    def test_killed_is_memory_with_the_free_memory_and_2_gb(self):
        why = self.failed(oom_log())
        self.assertEqual(why, "не хватило памяти для сборки (свободно 412 МБ). Рекомендуем сервер от 2 ГБ памяти. "
                              "Журнал: %s" % self.path(LOG))
        with lang("en"):
            why = self.failed(oom_log())
        self.assertEqual(why, "not enough memory for the build (412 MB free). We recommend a server with at least "
                              "2 GB of memory. Log: %s" % self.path(LOG))

    def test_out_of_memory_without_killed(self):                 # REV-kmod3 З3: gcc and ld say it otherwise
        for text in ("  CC [M]  main.o\ncc1: out of memory allocating 65536 bytes after a total of 9 bytes\n"
                     "make[2]: *** [scripts/Makefile.build:243: main.o] Error 1\n",
                     "virtual memory exhausted: Cannot allocate memory\nmake[2]: *** [x] Error 1\n",
                     "  CC [M]  main.o\nKilled\nmake: *** [Makefile:5: all] Error 2\n"):
            self.assertIn("не хватило памяти для сборки (свободно 412 МБ). Рекомендуем сервер от 2 ГБ", self.failed(text))

    def test_the_quoted_source_is_not_the_reason(self):
        why = self.failed("main.c:7:5: error: implicit declaration of function 'foo'\n"
                          '    7 |     pr_err("out of memory: cannot allocate memory, Killed");\n      |     ^~~~~~\n'
                          "make[2]: *** [x] Error 1\n")
        self.assertTrue(why.startswith("ошибка компиляции"), why)

    def test_a_server_of_2_gb_and_more_is_told_to_free_memory(self):        # REV-kmod3 П1
        for mem in (MEM_BIG, MEM_2GB):
            why = self.failed(oom_log(), mem)
            self.assertEqual(why, "не хватило памяти для сборки (свободно 412 МБ) — освободите память и повторите. "
                                  "Журнал: %s" % self.path(LOG))
        with lang("en"):
            self.assertIn("(412 MB free) — free some memory and try again. Log: ", self.failed(oom_log(), MEM_BIG))

    def test_unknown_memory(self):
        why = self.failed(oom_log(), meminfo=None)
        self.assertEqual(why, "не хватило памяти для сборки. Рекомендуем сервер от 2 ГБ памяти. Журнал: %s"
                         % self.path(LOG))

    def test_no_space(self):
        why = self.failed("  CC [M]  main.o\nmain.c:1: fatal error: cannot write: No space left on device\n")
        self.assertEqual(why, "нет места на диске — освободите место (df -h) и повторите. Журнал: %s" % self.path(LOG))
        with lang("en"):
            self.assertIn("no space left on the disk", self.failed("No space left on device\n"))

    def test_compiler_errors_the_first_two_without_the_build_tree(self):
        why = self.failed(COMPILE)
        self.assertTrue(why.startswith("ошибка компиляции (журнал: %s): socket.c:434:9: error: passing argument 2"
                                       % self.path(LOG)), why)
        self.assertNotIn(TREE + "socket.c", why)
        self.assertNotIn("third", why)
        why = self.failed('ERROR: modpost: "udp_tunnel_sock_release" [amneziawg.ko] undefined!\n')
        self.assertIn("ERROR: modpost", why)
        with lang("en"):
            self.assertIn("compile error (log: ", self.failed(COMPILE))

    def test_undefined_symbols_not_undefined_behavior(self):     # REV-kmod3 П7
        self.assertIn("undefined reference to `foo'", self.failed("ld: main.o: undefined reference to `foo'\n"))
        self.assertIn('"foo" [amneziawg.ko] undefined!',
                      self.failed('WARNING: modpost: "foo" [amneziawg.ko] undefined!\nmake[1]: *** [x] Error 2\n'))
        why = self.failed("main.c:5: warning: iteration 3 invokes undefined behavior\n/bin/sh: 1: gcc-13: not found\n"
                          "make[2]: *** [x] Error 127\n")
        self.assertNotIn("undefined behavior", why)
        self.assertIn("Error 127", why)
        self.assertIn("Error 2", self.failed("  CC [M]  main.o\nmake[1]: *** [Makefile:12: all] Error 2\n"))

    def test_a_log_that_says_nothing_dkms_words_and_the_path(self):        # REV-kmod3 З4
        for text in (NOTHING, ""):
            why = self.failed(text)
            self.assertEqual(why, "Error! Bad return status for module build on kernel: %s (x86_64) (журнал: %s)"
                             % (K, self.path(LOG)))
        with lang("en"):
            self.assertTrue(self.failed(NOTHING).endswith("(x86_64) (log: %s)" % self.path(LOG)))

    def test_no_log_dkms_words_alone(self):
        self.assertEqual(self.failed(None), "Error! Bad return status for module build on kernel: %s (x86_64)" % K)

    def test_secrets_hidden_before_the_cut(self):                # REV-kmod3 П6
        uuid = "0123abcd-4567-89ef-0123-456789abcdef"
        head = "main.c:1:1: error: "
        line = head + "a" * (awgkmod.ERRORS_MAX - len(head) - len("vless://") - 10) + " vless://" + uuid + "@h:443\n"
        why = self.failed(line)
        self.assertNotIn(uuid[:10], why)
        self.assertIn("vless://<", why)

    def test_a_copy_not_made_names_make_log(self):
        self.write(LOG, COMPILE)
        with mock.patch.object(awgkmod, "set_in_src", lambda src=None: True), \
                mock.patch.object(util, "atomic_write", side_effect=OSError(28, "No space left on device")):
            why = awgkmod._build_error(Result(["dkms", "build"], 10, "", ""))
        self.assertIn("(журнал: %s)" % self.path(LOG), why)

    def test_the_copy_is_named_and_the_build_tree_of_the_log_still_cut(self):
        other = "/var/lib/dkms/amneziawg/%s/%s/x86_64/log/" % (awgkmod.VERSION, K)       # a log DKMS names elsewhere
        self.write(other + "make.log", "%ssocket.c:434:9: error: x\n" % self.path(other))
        res = Result(["dkms", "build"], 10, DKMS_FAILED % (K, self.path(other + "make.log")), "")
        with mock.patch.object(awgkmod, "set_in_src", lambda src=None: True):
            why = awgkmod._build_error(res)
        self.assertEqual(why, "ошибка компиляции (журнал: %s): socket.c:434:9: error: x" % self.path(KEPT))

    def test_both_catalogs_have_the_words(self):
        FILL = dict(log="/var/lib/dkms/make.log", errors="error", why="oom", mb=100, mem="")
        for code in ("ru", "en"):
            for k in ("why.oom", "why.oom_busy", "why.free", "why.no_space", "why.compile", "why.dkms_log"):
                # every «{name}» of the six filled: K3_TR_STRICT=1 (k3_test.sh) fails an unfilled one
                self.assertNotEqual(i18n.tr("gw_awgkmod." + k, code, **FILL), "gw_awgkmod." + k, (code, k))


class LengthTest(KmodCase):
    """The whole message «the module did not build for kernel K: …» within 300 characters, the path of make.log in what
    the engine keeps — every kind, RU and EN, a long name of a kernel too (the path as on a server: without the root
    of the test)."""

    KERNELS = ("6.8.0-134-generic", "6.1.0-26-cloud-amd64", "6.12.48+deb13-rt-cloud-amd64-unsigned-long")
    LOGS = (oom_log(), "Killed\n", "No space left on device\n", COMPILE, NOTHING, "",
            "x" * 200 + ": error: " + "y" * 400 + "\n" + "z" * 300 + " undefined reference\n")

    def test_within_300_with_the_path(self):
        for code in ("ru", "en"):
            with lang(code):
                for kernel in self.KERNELS:
                    for text in self.LOGS:
                        for mem in (MEM_SMALL, MEM_BIG, None):
                            if mem is None and os.path.exists(self.path("/proc/meminfo")):
                                os.remove(self.path("/proc/meminfo"))
                            self.write(LOG, text)
                            if mem:
                                self.write("/proc/meminfo", mem)
                            res = Result(["dkms", "build"], 10, DKMS_FAILED % (kernel, self.path(LOG)), "")
                            why = awgkmod._build_error(res).replace(self.path(LOG), LOG)
                            e = awgkmod._fail("build", "gw_awgkmod.err.build", kernel=kernel, why=why)
                            self.assertLessEqual(len(str(e)), 300, (code, kernel, str(e)))
                            self.assertIn(LOG, awgengine._short(e), (code, kernel, text[:20]))


class SetTakenOffTest(KmodCase):
    """Only the words changed: a set of 3.1 that did not build — out of memory too — is taken off and the module built
    without it, as before (REV-kmod3 С1); the path in set_failed leads to the errors of the build with the set, which
    the build without it wrote over (REV-kmod3 З2)."""

    def builds(self):
        return [c for c in self.w.calls if c[:2] == ["dkms", "build"]]

    def build(self, failed_log):
        """dkms build: with the set — fails with ``failed_log`` in make.log; without it — builds, make.log anew."""
        def run(argv, input):
            self.w.calls.append(argv)
            if awgkmod.set_in_src():
                self.write(LOG, failed_log)
                return 10, DKMS_FAILED % (K, self.path(LOG)), ""
            self.write(LOG, "DKMS make.log for amneziawg-%s for kernel %s (x86_64)\nmake: Leaving directory\n"
                       % (awgkmod.VERSION, K))
            return 0, "", ""
        self.w.r._handlers.insert(0, (["dkms", "build"], run))

    def test_upgrade_out_of_memory_builds_without_the_set(self):            # REV-kmod3 С1
        test_gw_awgkmod.SetTest.old_node(self)         # the module built and loaded without the set
        doc = self.state()
        doc.pop("set_failed", None)
        util.atomic_write(awgkmod.state_path(), json.dumps(doc), 0o600)
        self.write("/proc/meminfo", MEM_SMALL)
        self.build(oom_log())
        res = awgkmod.upgrade(self.w.r)
        self.assertEqual((res["ran"], res["set"]), (True, False))
        st = self.state()
        self.assertTrue(st["enabled"])
        self.assertIn(K, self.w.dkms["installed"])
        self.assertEqual(len(self.builds()), 2)
        self.assertFalse(awgkmod.set_in_src())
        self.assertIn("не хватило памяти для сборки (свободно 412 МБ). Рекомендуем сервер от 2 ГБ памяти. Журнал: ",
                      st["set_failed"]["reason"])

    def test_the_path_in_set_failed_leads_to_the_errors(self):              # REV-kmod3 З2
        self.build(COMPILE)
        awgkmod.install(self.w.r, force=True)
        reason = self.state()["set_failed"]["reason"]
        self.assertEqual(len(self.builds()), 2)
        shown = re.search(r"журнал: (\S+)\)", reason).group(1)
        self.assertEqual(shown, self.path(KEPT))
        self.assertIn("socket.c:434:9: error: passing argument 2", util.read_text(shown))
        self.assertEqual(os.stat(shown).st_mode & 0o777, 0o600)
        self.assertNotIn("error:", self.read(LOG))                 # the build without the set wrote make.log anew
