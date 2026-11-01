"""SEC-3371 П-1: stdout of kaskadctl never gives a raw byte 0x80–0x9F of a text that is not UTF-8 (an old name read
with ``surrogateescape``): «?» instead — under the locale C (surrogateescape) and under UTF-8 (strict: no crash)."""

import io
import os
import subprocess
import sys
import unittest

from kaskad import cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SafeStdoutTest(unittest.TestCase):
    def test_a_lone_byte_is_replaced(self):
        for errors in ("surrogateescape", "strict"):
            raw = io.BytesIO()
            s = io.TextIOWrapper(raw, encoding="utf-8", errors=errors)
            cli.safe_stdout(s)
            s.write("a\udc9b2J Вена\n")
            s.flush()
            self.assertEqual(raw.getvalue(), "a?2J Вена\n".encode("utf-8"), errors)

    def test_not_a_text_wrapper_is_left(self):
        cli.safe_stdout(io.StringIO())                          # no reconfigure: nothing raised

    def test_the_entry_of_kaskadctl_calls_it(self):
        lib = os.path.join(ROOT, "lib")
        env = dict(os.environ, LC_ALL="C", LANG="C")
        code = ("import runpy, sys; sys.path.insert(0, %r); sys.argv = ['kaskadctl', 'version']; "
                "runpy.run_module('kaskad', run_name='__main__', alter_sys=True)") % lib
        r = subprocess.run([sys.executable, "-I", "-c", code], env=env, capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(lib, "kaskad", "__main__.py"), encoding="utf-8") as f:
            self.assertIn("safe_stdout()", f.read())
        # the locale C: stdout of python is surrogateescape — a raw 0x9B without safe_stdout
        code = "import sys; sys.path.insert(0, %r); from kaskad import cli; %s sys.stdout.write('a\\udc9b2J\\n')"
        for call, want in (("cli.safe_stdout();", b"a?2J\n"), ("", b"a\x9b2J\n")):
            r = subprocess.run([sys.executable, "-I", "-c", code % (lib, call)], env=env, capture_output=True, timeout=60)
            self.assertEqual(r.stdout, want, (call, r.stderr))

if __name__ == "__main__":
    unittest.main()
