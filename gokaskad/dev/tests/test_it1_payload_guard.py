"""REV-it1-b С-2 «как не повторять»: install.sh b669caa8 собрали из грязного дерева — в payload уехал служебный
panel/static/__seed.html. Теперь build_payload.py отказывает, пока в панели лежат файлы ``__*`` (сборка и --check),
а --check --clean (проверка выпуска) — ещё и на изменённых или новых файлах lib/ и install.sh."""
import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("k3_build_payload_guard", os.path.join(ROOT, "tools", "build_payload.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mini_lib(d):
    st = os.path.join(d, "lib", "kaskad", "panel", "static")
    os.makedirs(os.path.join(st, "pages"))
    for name, text in (("lib/kaskad/__init__.py", ""), ("lib/kaskad/panel/static/index.html", "<!doctype html>"),
                       ("lib/kaskad/panel/static/pages/a.js", "1")):
        with open(os.path.join(d, name), "w") as f:
            f.write(text)
    return st


class ScratchFilesTest(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.d = tempfile.mkdtemp(prefix="k3pg-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.st = mini_lib(self.d)

    def test_clean_static_is_packed(self):
        names = [n for n, _ in self.mod.collect(os.path.join(self.d, "lib"))]
        self.assertEqual(names, ["kaskad/__init__.py", "kaskad/panel/static/index.html", "kaskad/panel/static/pages/a.js"])

    def test_scratch_file_is_refused_by_name(self):
        for rel in ("__seed.html", os.path.join("pages", "__probe.js")):
            with open(os.path.join(self.st, rel), "w") as f:
                f.write("x")
        with self.assertRaises(self.mod.ScratchFiles) as cm:
            self.mod.collect(os.path.join(self.d, "lib"))
        self.assertIn("kaskad/panel/static/__seed.html", str(cm.exception))
        self.assertIn("kaskad/panel/static/pages/__probe.js", str(cm.exception))
        # main: и сборка, и --check — код 1 и понятная строка, без трассировки; install.sh не тронут
        script = os.path.join(self.d, "install.sh")
        with open(script, "w") as f:
            f.write("#!/bin/sh\n")
        for extra in ([], ["--check"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = self.mod.main(extra + ["--lib", os.path.join(self.d, "lib"), "--script", script])
            self.assertEqual(rc, 1, extra)
            self.assertIn("служебные файлы", out.getvalue())
        with open(script) as f:
            self.assertEqual(f.read(), "#!/bin/sh\n")

    def test_python_dunder_files_are_not_scratch(self):
        with open(os.path.join(self.d, "lib", "kaskad", "__main__.py"), "w") as f:
            f.write("")
        self.assertIn("kaskad/__main__.py", [n for n, _ in self.mod.collect(os.path.join(self.d, "lib"))])


@unittest.skipUnless(shutil.which("git"), "нет git")
class CleanTreeTest(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.d = tempfile.mkdtemp(prefix="k3pg-")
        self.addCleanup(shutil.rmtree, self.d, True)
        mini_lib(self.d)
        with open(os.path.join(self.d, "install.sh"), "w") as f:
            f.write("#!/bin/sh\n")
        g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com"] + list(a), cwd=self.d,  # noqa: E731
                                      check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        g("init", "-q")
        g("add", "-A")
        g("commit", "-qm", "base")
        self.mod.ROOT = self.d

    def run_clean(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.mod.check(os.path.join(self.d, "install.sh"), os.path.join(self.d, "lib"), clean=True)
        return rc, out.getvalue()

    def test_dirty_tree_fails_before_anything_else(self):
        self.assertEqual(self.mod.dirty(["lib", "install.sh"]), [])
        with open(os.path.join(self.d, "lib", "kaskad", "panel", "static", "pages", "new.js"), "w") as f:
            f.write("2")
        self.assertEqual(self.mod.dirty(["lib", "install.sh"]), ["lib/kaskad/panel/static/pages/new.js"])
        rc, out = self.run_clean()
        self.assertEqual(rc, 1)
        self.assertIn("дерево не чистое", out)
        self.assertIn("pages/new.js", out)

    def test_not_a_git_tree(self):
        shutil.rmtree(os.path.join(self.d, ".git"))
        self.assertIsNone(self.mod.dirty(["lib"]))
        rc, out = self.run_clean()
        self.assertEqual(rc, 1)
        self.assertIn("не git-дерево", out)


if __name__ == "__main__":
    unittest.main()
