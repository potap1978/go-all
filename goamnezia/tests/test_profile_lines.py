# -*- coding: utf-8 -*-
"""Место профиля — не линия (находка AWG31-01, жалоба 05.10: «ключ 3.1 из панели в AmneziaVPN — всегда 2.0» и обратное
«из вкладки 2.0 ключи получаются 3.1»).

Приложение Amnezia держит 3.1 и в amnezia-awg2; после подхвата такого контейнера Ленивый Pro ставит недостающую 2.0 в
awg3 (LZ-02). Ключ ядро собирает по фактической линии профиля (ga_profile_line) — он верен, а подписи брались по месту:
awg2 — «2.0», awg3 — «3.1». Человек выбирал «3.1» и получал ключ 2.0 (приложение честно показывало «version 2»), выбирал
«2.0» — получал 3.1. Здесь — SSH-меню на такой раскладке: выбор профиля для ключа и подписи профилей — по линии; ключ —
самого сильного профиля (3.1) по линии, а не по месту. Панель — tests/test_panel_dom.py и tests/test_admin_features.py
(PeerLineTests), страница — tests/test_pages_order.py, ключ сам по себе — tests/test_awgkeys.py.

Нужны bash ≥ 4 (BASH5 на macOS) и jq. Запуск: BASH5=/opt/homebrew/bin/bash python3 -m unittest tests/test_profile_lines.py
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JQ = shutil.which("jq")


def _bash5():
    for cand in (os.environ.get("BASH5"), shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/bin/bash"):
        if not cand or not os.path.exists(cand):
            continue
        try:
            v = subprocess.run([cand, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if v.stdout.strip().isdigit() and int(v.stdout.strip()) >= 4:
            return cand
    return None


BASH = _bash5()

# Раскладка после подхвата: amnezia-awg2 из приложения — 3.1, 2.0 — в awg3 (контейнер amnezia-awg3).
SWAPPED = {"awg2": "3.1", "awg3": "2.0"}


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOAMNEZIA_") and k not in {"BASH_ENV", "ENV"}}
    env.update(extra)
    return env


@unittest.skipUnless(BASH and JQ, "нужны bash ≥ 4 (BASH5) и jq")
class SshMenuLinesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="ga-lines-")
        tmp = Path(cls.temp.name)
        # install.sh без последней строки (main) рядом со ссылкой на lib — функции без запуска меню.
        src = (ROOT / "install.sh").read_text(encoding="utf-8").rstrip()
        assert src.endswith('main "$@"')
        (tmp / "install.sh").write_text(src[: -len('main "$@"')], encoding="utf-8")
        os.symlink(ROOT / "lib", tmp / "lib")
        gdir = tmp / "ga"
        gdir.mkdir()
        profiles = {}
        for i, (pid, line) in enumerate(sorted(SWAPPED.items())):
            profiles[pid] = {"container": "amnezia-%s" % pid, "line": line, "port": 40000 + i,
                             "subnet": "10.8.%d.0/24" % (2 + i), "installed_at": "2026-10-05T10:00:00Z", "preset": "default"}
        (gdir / "config.json").write_text(json.dumps({
            "schema": 1, "version": "1.0.8", "installed": True, "mode": "lite", "lang": "ru", "domain": "",
            "server_ip": "203.0.113.10", "endpoint_host": "", "client_dns": ["1.1.1.1"], "profiles": profiles}), encoding="utf-8")
        peers = {pid: {"ip": "10.8.%d.1" % (2 + i), "pubkey": "PUB%d" % i, "created": "2026-10-05T10:00:00Z", "active": True}
                 for i, pid in enumerate(sorted(SWAPPED))}
        (gdir / "users.json").write_text(json.dumps({"schema": 1, "users": [
            {"name": "anna", "label": "Анна", "created": "2026-10-05T10:00:00Z", "enabled": True, "quota_bytes": 0,
             "expires": "", "traffic": {}, "peers": peers}]}), encoding="utf-8")
        (gdir / ".language").write_text("ru\n", encoding="utf-8")
        cls.tmp = tmp
        cls.env = clean_env(GOAMNEZIA_DIR=str(gdir), GOAMNEZIA_DRY_RUN="1", TMPDIR=str(tmp), NO_COLOR="1",
                            GOAMNEZIA_LOCK_DIR=str(tmp / "run"), GOAMNEZIA_LOG_FILE=str(tmp / "log"),
                            GOAMNEZIA_AUDIT_LOG=str(tmp / "audit"))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_fn(self, body, stdin=""):
        p = subprocess.run([BASH, "-c", 'source "$1/install.sh" >/dev/null 2>&1; ' + body, "lines", str(self.tmp)],
                           env=self.env, input=stdin, capture_output=True, text=True, timeout=120)
        strip = lambda s: __import__("re").sub(r"\x1b\[[0-9;]*m", "", s)  # noqa: E731
        return strip(p.stdout), strip(p.stderr)

    def test_profile_names_follow_the_line(self):
        # Как в меню — подстановкой $( ): хвостовой перевод строки у t и tf разный, у подстановки — никакого.
        out, err = self.run_fn('for x in "$(_pr_name awg2)" "$(_pr_name awg3)" "$(_pr_name awg1)" '
                               '"$(_profile_31_first "awg3,awg2")" "$(_profile_31_first "awg3")"; do printf "%s\\n" "$x"; done')
        self.assertEqual(out.splitlines(), ["AmneziaWG 3.1 · порт 40000", "AmneziaWG 2.0 · порт 40001",
                                            "AmneziaWG 1.5 — не установлена", "awg2", "awg3"], err[-2000:])

    def test_key_picker_lists_the_lines_not_the_places(self):
        """«Каким профилем выдать ключ»: пункт awg3 — «AmneziaWG 2.0 · порт 40001», пункт awg2 — «AmneziaWG 3.1 · порт 40000»; совет
        «берите 3.1» — раз 3.1 среди них есть. На прежнем коде — «AmneziaWG 3.1 (awg3)» над ключом 2.0."""
        out, err = self.run_fn('_pick_user_profile anna; echo "rc=$?"', stdin="0\n")
        self.assertIn("rc=2", out, err[-2000:])
        self.assertIn("1) AmneziaWG 2.0 · порт 40001", err)       # без имени места, с портом (решение владельца 15.10)
        self.assertIn("2) AmneziaWG 3.1 · порт 40000", err)
        self.assertNotRegex(err, r"\(awg[123]\)")
        self.assertIn("Берите AmneziaWG 3.1", err)

    def test_new_person_ticks_name_the_line_and_who_it_is_for(self):
        """Галочки профилей у «Добавить» и «Добавить сразу несколько»: подпись и «кому он» — по линии профиля."""
        out, err = self.run_fn('_user_pick_profiles; echo "picked=$GA_PICKED_PROFILES"', stdin="\n")
        self.assertIn("picked=awg3,awg2", out, err[-2000:])
        rows = [ln.strip() for ln in out.splitlines()]
        i20 = next(i for i, ln in enumerate(rows) if ln.endswith("1) AmneziaWG 2.0 · порт 40001"))
        i31 = next(i for i, ln in enumerate(rows) if ln.endswith("2) AmneziaWG 3.1 · порт 40000"))
        self.assertIn("подходит почти всем приложениям Amnezia", rows[i20 + 1])     # «кому он» — у 2.0
        self.assertIn("самая сильная маскировка", rows[i31 + 1])                   # и у 3.1


if __name__ == "__main__":
    unittest.main()
