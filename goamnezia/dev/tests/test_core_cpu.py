# -*- coding: utf-8 -*-
"""Дешёвый мост (CPU-аудит 03.10.2026): то же самое, но меньшим числом процессов.

* Отметка «core уже проверен» (lib/deps.sh, deps_stamp_valid): верят ей только чтения и тики (GA_DEPS_STAMP=1);
  смена пакета, файла команды или пути в PATH, истёкшие сутки — снова полная проверка.
* Снимок config.json на вызов читающего действия (lib/common.sh, ga_config_memo_load): ga_config_get отвечает
  РОВНО то же, что jq, — на значениях, null, отсутствии, объектах, массивах, путях через скаляр.
* ga_emit_json одним jq: ответ байт в байт тот же, что у прежней реализации (она — здесь же, копией).
* _action_meta: известно ли действие, readonly, аргументы и правила — те же, что в lib/actions.json.

Пакеты машины не трогаются: команды — фикстуры во временном каталоге. stat для отпечатка — функция оболочки с
GNU-выводом (stat -L -c '%n %i %Z %s', как его зовёт lib/deps.sh): у macOS свой синтаксис stat.

Запуск: python3 -m unittest tests/test_core_cpu.py
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
BASH = os.environ.get("BASH5") or shutil.which("bash") or "bash"
JQ = shutil.which("jq")

# Подменный stat с выводом GNU `stat -L -c '%n %i %Z %s' -- файлы…` (как его зовёт lib/deps.sh) на любой машине —
# функцией оболочки над этим python-файлом. Не исполняемым файлом во временном каталоге: на macOS свежесозданный
# исполняемый файл под параллельной нагрузкой изредка падает SIGSEGV уже после печати (замер 04.10.2026: 26 из 600),
# и проверка отметки мигала бы от чужого сбоя.
STAT_SHIM = r'''import os, sys
a = sys.argv[1:]
if a[:3] != ["-L", "-c", "%n %i %Z %s"]:
    sys.exit(64)
a = a[3:]
if a[:1] == ["--"]:
    a = a[1:]
rc = 0
for f in a:
    try:
        st = os.stat(f)
    except OSError:
        rc = 1
        continue
    print("%s %d %d %d" % (f, st.st_ino, int(st.st_ctime), st.st_size))
sys.exit(rc)
'''

# Прежняя ga_emit_json (до CPU-03) — эталон для сравнения ответов.
OLD_EMIT = r'''
ga_emit_json_old() {
    local status="${1:-error}"
    local message="${2:-}"
    [ $# -ge 2 ] && shift 2 || shift $#
    if command -v jq >/dev/null 2>&1; then
        local -x GA_EJ_STATUS="$status" GA_EJ_MESSAGE="$message"
        local filter='{"status":$ENV.GA_EJ_STATUS,"message":$ENV.GA_EJ_MESSAGE}'
        local kv key val i=0 nj=0 jvals=""
        for kv in "$@"; do
            case "$kv" in *=*) : ;; *) continue ;; esac
            key="${kv%%=*}"
            val="${kv#*=}"
            [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
            i=$((i+1))
            if [ "${val#@str:}" != "$val" ]; then
                local -x "GA_EJ_V${i}=${val#@str:}"
                filter="${filter} + {\"${key}\":\$ENV.GA_EJ_V${i}}"
                continue
            fi
            if [ "${val#@json:}" != "$val" ] && [ -n "${val#@json:}" ] \
               && printf '%s' "${val#@json:}" | jq . >/dev/null 2>&1; then
                jvals+="${val#@json:}"$'\n'
                filter="${filter} + {\"${key}\":\$__j[${nj}]}"
                nj=$((nj+1))
            else
                local -x "GA_EJ_V${i}=${val#@json:}"
                filter="${filter} + {\"${key}\":\$ENV.GA_EJ_V${i}}"
            fi
        done
        if [ "$nj" -gt 0 ]; then
            printf '%s' "$jvals" | jq -cn "[inputs] as \$__j | ${filter}" && return 0
        else
            jq -cn "$filter" && return 0
        fi
    fi
    return 1
}
'''


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOAMNEZIA_") and k not in {"BASH_ENV", "ENV"}}
    env.update(extra)
    return env


@unittest.skipUnless(JQ, "нужен jq")
class DepsStampTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ga-cpu-deps-")
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        (self.tmp / "version").write_text("1.7.1")
        self.fake_jq(self.bin)
        (self.tmp / "stat_shim.py").write_text(STAT_SHIM)
        (self.tmp / "fns.sh").write_text('stat() { "$PY" "$TEST_TMP/stat_shim.py" "$@"; }\n', encoding="utf-8")

    def fake_jq(self, where, extra=""):
        p = where / "jq"
        p.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$TEST_TMP/jq.calls\"\n"
                     "if [ \"$1\" = --version ]; then printf 'jq-%s\\n' \"$(cat \"$TEST_TMP/version\")\"; fi\n"
                     + extra + "exit 0\n", encoding="utf-8")
        p.chmod(0o755)

    def check(self, stamp=True, path_extra=None):
        """check_deps_present в отдельной оболочке; rc и сколько раз звали jq --version (полная проверка)."""
        calls = self.tmp / "jq.calls"
        if calls.exists():
            calls.unlink()
        path = (str(path_extra) + os.pathsep if path_extra else "") + str(self.bin) + os.pathsep + os.environ["PATH"]
        env = clean_env(PATH=path, TEST_TMP=str(self.tmp), TMPDIR=str(self.tmp), GOAMNEZIA_LOCK_DIR=str(self.run_dir),
                        GOAMNEZIA_DIR=str(self.tmp / "ga"), GA_DEPS_STAMP="1" if stamp else "0", PY=sys.executable)
        script = ('source "$TEST_TMP/fns.sh"\n'
                  'source "$1/lib/common.sh"\nsource "$1/lib/deps.sh"\n'
                  'DEPS_TABLE=("core|jq|cmd:jq|jq|crit|deps_why_jq")\n'
                  'check_deps_present; echo "rc=$?"\n')
        p = subprocess.run([BASH, "-c", script, "deps", str(ROOT)], env=env, capture_output=True, text=True, timeout=30)
        n = calls.read_text().count("--version") if calls.exists() else 0
        return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr, n

    def test_reads_trust_the_stamp_until_something_changes(self):
        self.assertEqual(self.check(), ("rc=0", 1))           # первая — полная, кладёт отметку
        self.assertTrue((self.run_dir / "deps-core.ok").exists())
        self.assertEqual(self.check(), ("rc=0", 0))           # дальше — по отметке, без jq --version
        self.assertEqual(self.check(stamp=False), ("rc=0", 1))  # меню и изменения — всегда полностью
        # Пакет заменили (новый файл команды) — отпечаток другой: снова полная проверка, и старый jq ловится.
        (self.tmp / "version").write_text("1.5")
        os.unlink(self.bin / "jq")
        self.fake_jq(self.bin, extra="# заменён\n")
        rc, n = self.check()
        self.assertEqual((rc, n), ("rc=1", 1))
        # Отказ отметку не обновил: следующий вызов снова проверяет полностью.
        self.assertEqual(self.check()[1], 1)

    def test_other_command_first_in_path_is_not_covered_by_the_stamp(self):
        self.assertEqual(self.check(), ("rc=0", 1))
        other = self.tmp / "other"
        other.mkdir()
        self.fake_jq(other)
        self.assertEqual(self.check(path_extra=other), ("rc=0", 1))

    def test_stamp_expires_after_a_day(self):
        self.assertEqual(self.check(), ("rc=0", 1))
        stamp = self.run_dir / "deps-core.ok"
        body = stamp.read_text().split("\n", 1)
        stamp.write_text(str(int(time.time()) - 86401) + "\n" + body[1])
        self.assertEqual(self.check(), ("rc=0", 1))

    def test_foreign_or_broken_stamp_is_ignored(self):
        self.assertEqual(self.check(), ("rc=0", 1))
        stamp = self.run_dir / "deps-core.ok"
        stamp.write_text("garbage")
        self.assertEqual(self.check(), ("rc=0", 1))


@unittest.skipUnless(JQ, "нужен jq")
class ConfigMemoTests(unittest.TestCase):
    CONFIG = {
        "mode": "pro", "domain": "vpn.example.com", "installed": True, "port": 443, "ratio": 1.5, "zero": 0,
        "off": False, "nothing": None, "empty": "", "note": "две\nстроки", "tab": "a\tb", "ru": "Привет",
        "client_dns": ["1.1.1.1", "1.0.0.1"], "site": {"x-y": "dash", "ok": "yes", "deep": {"a": {"b": "c"}}},
        "profiles": {"awg2": {"container": "amnezia-awg2", "port": 51820, "enabled": False, "installed_at": "2026-10-01",
                              "params": {"Jc": 4, "MTU": 1280}}},
        "list": [{"x": 1}], "1x": "digit-key",
    }
    PATHS = [".mode", ".domain", ".installed", ".port", ".ratio", ".zero", ".off", ".nothing", ".empty", ".note",
             ".tab", ".ru", ".client_dns", ".client_dns[0]", ".client_dns[5]", ".site", '.site["x-y"]', ".site.ok",
             ".site.deep", ".site.deep.a.b", ".site.missing.deeper", ".profiles.awg2.container", ".profiles.awg2.port",
             ".profiles.awg2.enabled", ".profiles.awg2.params", ".profiles.awg2.params.MTU", ".profiles.awg1.installed_at",
             ".profiles.awg1.container", ".missing", ".mode.sub", ".list.x", ".list[0].x", '.["1x"]', ".nothing.more",
             ".", ".bad path", ".off.x"]

    def run_get(self, cfg_text, memo):
        with tempfile.TemporaryDirectory(prefix="ga-cpu-cfg-") as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(cfg_text, encoding="utf-8")
            env = clean_env(GOAMNEZIA_DIR=tmp, TMPDIR=tmp, GOAMNEZIA_LOG_FILE=str(Path(tmp) / "log"))
            script = ['source "$1/lib/common.sh"']
            if memo:
                script.append('ga_config_memo_load; printf "memo=%s\\n" "$GA_CFG_MEMO_ON"')
            for path in self.PATHS:
                script.append(f"out=$(ga_config_get {json.dumps(path)} 2>/dev/null); rc=$?; "
                              "printf '%s\\t%s\\t%s\\n' \"$rc\" \"$(printf '%s' \"$out\" | base64 | tr -d '\\n')\" "
                              f"{json.dumps(path)}")
            p = subprocess.run([BASH, "-c", "\n".join(script), "cfg", str(ROOT)], env=env, capture_output=True,
                               text=True, timeout=60)
            return p.stdout.splitlines()

    def test_memo_answers_exactly_like_jq(self):
        text = json.dumps(self.CONFIG, ensure_ascii=False)
        plain = self.run_get(text, memo=False)
        memo = self.run_get(text, memo=True)
        self.assertEqual(memo[0], "memo=1")
        self.assertEqual(memo[1:], plain)
        self.assertEqual(len(plain), len(self.PATHS))

    def test_broken_or_odd_config_falls_back_to_jq(self):
        for text in ('{"mode": "pro"', '{"mode": "a"} {"mode": "b"}', '[1, 2]', '"text"', ""):
            with self.subTest(text=text):
                plain = self.run_get(text, memo=False)
                memo = self.run_get(text, memo=True)
                self.assertEqual(memo[0], "memo=0")
                self.assertEqual(memo[1:], plain)

    def test_write_by_this_process_turns_the_snapshot_off(self):
        with tempfile.TemporaryDirectory(prefix="ga-cpu-cfg-") as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"mode": "lite"}), encoding="utf-8")
            env = clean_env(GOAMNEZIA_DIR=tmp, TMPDIR=tmp, GOAMNEZIA_LOCK_DIR=str(Path(tmp) / "run"),
                            GOAMNEZIA_LOG_FILE=str(Path(tmp) / "log"))
            script = ('source "$1/lib/common.sh"\nga_config_memo_load\n'
                      'printf "%s %s\\n" "$GA_CFG_MEMO_ON" "$(ga_config_get .mode)"\n'
                      'ga_config_set .mode pro >/dev/null 2>&1\n'
                      'printf "%s %s\\n" "$GA_CFG_MEMO_ON" "$(ga_config_get .mode)"\n')
            p = subprocess.run([BASH, "-c", script, "cfg", str(ROOT)], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(p.stdout.split("\n")[:2], ["1 lite", "0 pro"], p.stderr)


@unittest.skipUnless(JQ, "нужен jq")
class EmitJsonParityTests(unittest.TestCase):
    CASES = [
        ["success", "ok"],
        ["success", "Готово", "users=@json:[{\"name\":\"anna\"}]", "n=@json:5", "flag=@json:false", "nil=@json:null"],
        ["error", "нет", "code=users_corrupt", "label=@str:@json:true", "raw=plain text"],
        ["success", "ok", "empty=@json:", "bad=@json:abc", "obj=@json:{\"a\":[1,2,{\"b\":null}]}"],
        ["success", "ok", "two=@json:1 2", "after=@json:[3]"],
        ["success", "ok", "pretty=@json:[\n  {\n    \"x\": 1\n  }\n]", "s=@json:\"str\""],
        ["success", "ok", "half=@json:[1,", "rest=@json:2]", "n=@json:7"],
        ["success", "ok", "bad key=@json:1", "dup=@json:1", "dup=@json:2"],
        ["success", "ok", "big=@json:12345678901234567890", "f=@json:1.50", "u=@json:\"\\u00e9\""],
    ]

    def emit(self, fn, args):
        script = 'source "$1/lib/common.sh"\n' + OLD_EMIT + '\nfn="$2"; shift 2\n"$fn" "$@"\n'
        p = subprocess.run([BASH, "-c", script, "emit", str(ROOT), fn] + args, env=clean_env(),
                           capture_output=True, text=True, timeout=30)
        return p.stdout

    def test_one_jq_answer_is_byte_for_byte_the_old_one(self):
        for args in self.CASES:
            with self.subTest(args=args):
                new = self.emit("ga_emit_json", args)
                old = self.emit("ga_emit_json_old", args)
                self.assertEqual(new, old)
                json.loads(new.strip().splitlines()[-1])


@unittest.skipUnless(JQ, "нужен jq")
class ActionMetaTests(unittest.TestCase):
    def test_meta_matches_the_catalogue(self):
        spec = json.loads((ROOT / "lib" / "actions.json").read_text(encoding="utf-8"))
        names = ["status", "user-add", "user-attach-key", "user-key", "traffic", "users-add-bulk", "no-such-action"]
        with tempfile.TemporaryDirectory(prefix="ga-cpu-meta-") as tmp:
            # install.sh без последней строки (main) рядом со ссылкой на lib — функции без запуска меню.
            src = (ROOT / "install.sh").read_text(encoding="utf-8").rstrip()
            self.assertTrue(src.endswith('main "$@"'))
            (Path(tmp) / "install.sh").write_text(src[: -len('main "$@"')], encoding="utf-8")
            os.symlink(ROOT / "lib", Path(tmp) / "lib")
            script = ['source "$1/install.sh"']
            for n in names:
                script.append(f'_action_meta {json.dumps(n)}; '
                              'printf "%s\\x1e%s\\x1e%s\\x1e%s\\x1d" "$GA_ACT_KNOWN" "$GA_ACT_READONLY" "$GA_ACT_ARGS" "$GA_ACT_SPEC"')
            env = clean_env(GOAMNEZIA_DIR=str(Path(tmp) / "ga"), GOAMNEZIA_DRY_RUN="1", TMPDIR=tmp,
                            GOAMNEZIA_LOCK_DIR=str(Path(tmp) / "run"), GOAMNEZIA_LOG_FILE=str(Path(tmp) / "log"),
                            GOAMNEZIA_AUDIT_LOG=str(Path(tmp) / "audit"))
            p = subprocess.run([BASH, "-c", "\n".join(script), "meta", tmp], env=env, capture_output=True,
                               text=True, timeout=60)
        got = p.stdout.split("\x1d")[: len(names)]
        self.assertEqual(len(got), len(names), p.stderr[-2000:])
        for n, rec in zip(names, got):
            known, ro, args, rules = rec.split("\x1e")
            a = spec["actions"].get(n)
            if a is None:
                self.assertEqual((known, ro, args, rules), ("n", "false", "", ""), n)
                continue
            params = {k: v for k, v in (a.get("params") or {}).items() if not (v or {}).get("stdin")}
            self.assertEqual(known, "y", n)
            self.assertEqual(ro, str(a.get("readonly", False)).lower(), n)
            self.assertEqual(args.split(), list(params), n)
            want = []
            for k, v in params.items():
                pt = spec["param_types"].get(v.get("ref") or k, {})
                def s(x):
                    return "" if x is None else (json.dumps(x) if isinstance(x, bool) else str(x))
                want.append("\x1f".join([k, s(v.get("required", False)), s(v.get("default", "")), pt.get("type", "string"),
                                         pt.get("pattern", ""), ",".join(map(str, pt.get("enum", []))),
                                         s(pt.get("minimum", "")), s(pt.get("maximum", ""))]))
            self.assertEqual(rules.split("\n") if rules else [], want, n)


if __name__ == "__main__":
    unittest.main()
