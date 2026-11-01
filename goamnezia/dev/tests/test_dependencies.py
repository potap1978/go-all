"""Подготовка зависимостей: версии, обновление до установки, отказ и dry-run.

apt, id и jq — исполняемые фикстуры; пакеты машины и службы не меняются.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
BASH = os.environ.get("BASH5") or shutil.which("bash") or "bash"


class Dependencies(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ga-deps-")
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.tmp / "version").write_text("1.5")
        self.executable("id", "printf '0\\n'")
        self.executable("jq", '''
if [ "$1" = --version ]; then printf 'jq-%s\\n' "$(cat "$TEST_TMP/version")"; fi
exit 0
''')
        self.executable("apt-get", '''
printf '%s\\n' "$*" >>"$TEST_TMP/apt.calls"
case "$1" in
 update) [ "${TEST_UPDATE_FAIL:-0}" = 0 ] ;;
 install)
   [ "${TEST_INSTALL_FAIL:-0}" = 0 ] || exit 100
   [ "${TEST_KEEP_OLD:-0}" = 1 ] || printf '1.6' >"$TEST_TMP/version"
   ;;
 *) exit 99 ;;
esac
''')

    def executable(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        p.chmod(0o755)

    def shell(self, code, **overrides):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GOAMNEZIA_") and k not in {"BASH_ENV", "ENV"}}
        env.update(PATH=str(self.bin) + os.pathsep + env["PATH"], TEST_TMP=str(self.tmp),
                   TMPDIR=str(self.tmp), GOAMNEZIA_DRY_RUN="0")
        env.update(overrides)
        script = 'source "$1/lib/deps.sh"\nDEPS_TABLE=("core|jq|cmd:jq|jq|crit|deps_why_jq")\n' + code
        return subprocess.run([BASH, "-c", script, "deps-test", str(ROOT)], env=env,
                              capture_output=True, text=True, timeout=15)

    def calls(self):
        p = self.tmp / "apt.calls"
        return p.read_text().splitlines() if p.exists() else []

    def test_old_installed_version_is_not_present(self):
        p = self.shell("deps_report_json core")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertFalse(d["ok"])
        self.assertEqual(d["missing"], [{"name": "jq", "pkg": "jq", "severity": "crit"}])
        self.assertEqual(d["checks"][0], {"name": "jq", "installed": "1.5", "required": "1.6", "reason": "version"})

    def test_updates_index_then_upgrades_only_required_package(self):
        p = self.shell("deps_install_missing --yes core && deps_install_missing --yes core")
        self.assertEqual(p.returncode, 0, p.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].startswith("update "), calls)
        self.assertTrue(calls[1].startswith("install --no-remove "), calls)
        self.assertTrue(calls[1].endswith(" jq"), calls)
        self.assertFalse(any("upgrade" in x for x in calls))

    def test_compatible_version_does_not_invoke_apt(self):
        (self.tmp / "version").write_text("1.7.1")
        p = self.shell("deps_install_missing --yes core")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.calls(), [])

    def test_successful_apt_with_old_version_still_blocks_install(self):
        p = self.shell("deps_install_missing --yes core", TEST_KEEP_OLD="1")
        self.assertEqual(p.returncode, 1)
        self.assertEqual((self.tmp / "version").read_text(), "1.5")

    def test_failed_install_blocks_after_bounded_retry(self):
        p = self.shell("deps_install_missing --yes core", TEST_INSTALL_FAIL="1")
        self.assertEqual(p.returncode, 1)
        self.assertEqual([x.split()[0] for x in self.calls()], ["update", "install", "update", "install"])

    def test_stale_index_can_use_cached_compatible_package(self):
        p = self.shell("deps_install_missing --yes core", TEST_UPDATE_FAIL="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual((self.tmp / "version").read_text(), "1.6")

    def test_dry_run_never_invokes_package_manager(self):
        p = self.shell("deps_install_missing --yes core", GOAMNEZIA_DRY_RUN="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual((self.tmp / "version").read_text(), "1.5")

    def test_version_comparison_is_numeric_and_portable(self):
        p = self.shell('''
_deps_ver_ge 1.10 1.6 && _deps_ver_ge 1.6 1.6.0 &&
! _deps_ver_ge 1.5.99 1.6 && ! _deps_ver_ge unknown 1.6
''')
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_python_without_ssl_is_incompatible(self):
        self.executable("python3", '''
case "$*" in *version_info*) printf '3.10.12\\n';; *) exit 1;; esac
''')
        p = self.shell('_deps_check_cmd python3; rc=$?; printf "%s:%s" "$rc" "$DEPS_REASON"')
        self.assertEqual(p.stdout, "1:capability")

    def test_bot_requires_python_39_even_when_core_accepts_38(self):
        self.executable("python3", '''
case "$*" in *version_info*) printf '3.8.10\\n';; esac
exit 0
''')
        p = self.shell('''
DEPS_TABLE=("core|python3|cmd:python3|python3|crit|deps_why_python3"
            "botweb|python3|cmd:python3|python3|crit|deps_why_python3")
deps_report_json core
deps_report_json core botweb
''')
        core, bot = map(json.loads, p.stdout.splitlines())
        self.assertTrue(core["ok"])
        self.assertFalse(bot["ok"])
        self.assertEqual(bot["checks"][0]["required"], "3.9")
        self.assertEqual(bot["checks"][0]["reason"], "version")


if __name__ == "__main__":
    unittest.main()
