# -*- coding: utf-8 -*-
"""Быстрый путь загрузчика: весь снимок ветки одним архивом.

Поштучная загрузка — это 76 запросов к CDN подряд, и на живой установке владелец
ждал заметно долго. bootstrap.sh теперь сначала берёт tarball коммита у API
GitHub и раскладывает из него ТОЛЬКО перечисленные в FILES / FILES_OPTIONAL
пути. Здесь проверяется всё, что может пойти не так:

  1. из архива легли ровно перечисленные файлы, с правильным содержимым;
  2. лишнее из архива (docs/, tests/, README.md) на диск НЕ попало;
  3. состояние сервера (config.json, users.json, state/, backups/, tls/) не
     тронуто — иначе обновление затирало бы ключи;
  4. нет сети / архив битый / в нём не хватает обязательного файла → честный
     откат на прежний поштучный путь, установка не ломается;
  5. в выводе видно, каким путём пошло и сколько заняло.

Сети не нужно: curl подменяется скриптом на PATH, архив — локальный tar.gz.

Запуск: python3 -m unittest tests.test_bootstrap_archive
"""
import os
import pathlib
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASH = os.environ.get('BASH5') or shutil.which('bash') or 'bash'
BOOTSTRAP = ROOT / 'bootstrap.sh'
SNAP_DIR = 'potap1978-goamnezia_pro-abc1234'
FAKE_SHA = 'a' * 40

# Лишнее, чего в списках загрузчика нет и на сервере быть не должно.
EXTRA_IN_REPO = ['docs/CONTRACT.md', 'tests/run_static.sh', 'README.md',
                 'lib/upstream/xray/start.sh']
# Состояние сервера: обновление обязано пройти мимо него.
SERVER_STATE = {'config.json': '{"installed":true}',
                'users.json': '{"users":[]}',
                'agents.json': '{"agents":[]}',
                'state/amnezia-awg2/awg0.conf': 'PrivateKey = keep-me',
                'backups/old.tar.gz': 'archive',
                'tls/site.key': 'private'}


def read_array(name):
    """Достать массив FILES / FILES_OPTIONAL из bootstrap.sh."""
    text = BOOTSTRAP.read_text(encoding='utf-8')
    m = re.search(r'^%s=\(\s*(.*?)^\)' % re.escape(name), text, re.S | re.M)
    assert m, name
    out = []
    for line in m.group(1).splitlines():
        line = line.split('#', 1)[0].strip()
        out.extend(re.findall(r'"([^"]+)"', line))
    return out


FILES = read_array('FILES')
FILES_OPTIONAL = read_array('FILES_OPTIONAL')


class BootstrapArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ga-bs-'))
        self.install = self.tmp / 'opt'
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        # Загрузчик доставляет curl, jq и tar через apt-get, если их нет, — и в сухом прогоне тоже. На
        # Linux-образе без jq от root проверка ставила бы пакеты на машину прогона (сеть, чужой dpkg-замок),
        # а сорвавшийся apt ронял все проверки класса. Здесь apt-get подставной и ничего не ставит: jq
        # загрузчику дальше не нужен, curl подменён, tar есть везде.
        apt = self.bin / 'apt-get'
        apt.write_text('#!/bin/sh\necho "apt-get: подставной, в проверках ничего не ставит" >&2\nexit 100\n',
                       encoding='utf-8')
        apt.chmod(0o755)
        if not os.access(str(apt), os.X_OK):
            # /tmp с noexec: ни подставной apt-get, ни подставной curl не запустятся — загрузчик пошёл бы
            # в сеть и в apt настоящими.
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.skipTest('каталог временных файлов смонтирован с noexec — подставные команды не запустить')
        for rel, body in SERVER_STATE.items():
            p = self.install / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── подготовка снимка ────────────────────────────────────────────────────
    def make_snapshot(self, skip=(), extra_dirs=True):
        """tar.gz такого же вида, как отдаёт GitHub: всё в одном каталоге."""
        src = self.tmp / 'snap' / SNAP_DIR
        for rel in list(FILES) + list(FILES_OPTIONAL):
            if rel in skip:
                continue
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            # Содержимое — валидная строка shell: в конце загрузчик делает
            # source lib/i18n.sh, и «просто текст» уронил бы его на set -e.
            p.write_text(': SNAPSHOT %s\n' % rel, encoding='utf-8')
        if extra_dirs:
            for rel in EXTRA_IN_REPO:
                p = src / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(': EXTRA %s\n' % rel, encoding='utf-8')
        tgz = self.tmp / 'snapshot.tar.gz'
        with tarfile.open(tgz, 'w:gz') as tar:
            tar.add(src, arcname=SNAP_DIR)
        return tgz

    # ── подменённый curl ─────────────────────────────────────────────────────
    def fake_curl(self, tarball=None, tarball_code='200', raw_ok=True):
        """curl, который отвечает без сети.

        commits/<branch> → JSON с sha; tarball → готовый архив (или отказ);
        остальное — «сырые» файлы по одному, как raw.githubusercontent.
        """
        script = '''#!/usr/bin/env bash
# Подменённый curl для tests/test_bootstrap_archive.py — сети не касается.
if [ -n "${GA_AUTH_ARGV_LOG:-}" ]; then printf '%%s\\n' "$@" >> "$GA_AUTH_ARGV_LOG"; fi
out=""; url=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -H)
            if [ "$2" = @- ]; then
                IFS= read -r header
                if [ -n "${GA_AUTH_STDIN_LOG:-}" ]; then printf '%%s\\n' "$header" >> "$GA_AUTH_STDIN_LOG"; fi
            fi
            shift 2 ;;
        -w|--max-time) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
case "$url" in
    *api.github.com/repos/*/commits/*)
        printf '{"sha": "%(sha)s"}'
        exit 0 ;;
    *api.github.com/repos/*/tarball/*)
        if [ "%(tcode)s" = "200" ] && [ -f "%(tgz)s" ]; then
            cp "%(tgz)s" "$out" 2>/dev/null
            printf '200'
            exit 0
        fi
        : > "$out" 2>/dev/null
        printf '%(tcode)s'
        exit 0 ;;
esac
# raw.githubusercontent.com/<repo>/<ref>/<путь>
path="${url#*/%(sha)s/}"
if [ "%(raw)s" = "1" ] && [ -f "%(repo)s/$path" ]; then
    printf ': RAW %%s\\n' "$path" > "$out"
    printf '200'
else
    : > "$out" 2>/dev/null
    printf '404'
fi
exit 0
''' % {'sha': FAKE_SHA, 'tcode': tarball_code, 'tgz': tarball or '/nonexistent',
       'raw': '1' if raw_ok else '0', 'repo': str(ROOT)}
        p = self.bin / 'curl'
        p.write_text(script, encoding='utf-8')
        p.chmod(0o755)

    def run_bootstrap(self, env=None):
        e = dict(os.environ)
        e.update({
            'PATH': '%s:%s' % (self.bin, e.get('PATH', '')),
            'GOAMNEZIA_DIR': str(self.install),
            'GOAMNEZIA_DRY_RUN': '1',
            'GOAMNEZIA_PAT': 'test-token',
            'GOAMNEZIA_LANG': 'ru',
        })
        if env:
            e.update(env)
        r = subprocess.run([BASH, str(BOOTSTRAP)], env=e, cwd=str(ROOT),
                           capture_output=True, text=True)
        # Повтор ровно на код 139 (SIGSEGV) и только один раз.
        #
        # Это не наш дефект и не дефект bootstrap.sh. Падает сам bash, и отчёт
        # macOS называет место: reset_locale_vars → libintl_setlocale →
        # CFLocaleCopyPreferredLanguages → кэш настроек CoreFoundation. То есть
        # bash из homebrew слинкован с gettext, а его setlocale ходит в
        # CoreFoundation, который при частых коротких процессах под нагрузкой
        # разваливается. Каждое присваивание LC_ALL/LANG (см. bs_width) —
        # ещё один такой заход, а рамка приветствия считает ширину построчно.
        #
        # На сервере, где живёт продукт, этого нет: там glibc, и setlocale
        # никуда не ходит. Замерено: примерно один сбой на 25 запусков, то есть
        # около трети прогонов набора падало без всякой связи с кодом. Тест,
        # который врёт раз в три прогона, хуже отсутствующего теста: на него
        # перестают смотреть. Любой другой код возврата отдаётся как есть.
        # До трёх повторов: под нагрузкой (несколько наборов разом) обвал
        # случается чаще, и одного повтора не хватало. Условие однозначное —
        # код 139 при пустом stdout может означать только этот обвал, потому что
        # свой отказ загрузчик всегда сопровождает выводом.
        for _ in range(3):
            if not (r.returncode == 139 and not r.stdout):
                break
            r = subprocess.run([BASH, str(BOOTSTRAP)], env=e, cwd=str(ROOT),
                               capture_output=True, text=True)
        return r

    # ── проверки ─────────────────────────────────────────────────────────────
    def assert_state_untouched(self):
        for rel, body in SERVER_STATE.items():
            self.assertEqual((self.install / rel).read_text(encoding='utf-8'), body,
                             'загрузчик тронул состояние сервера: %s' % rel)

    def test_archive_lays_out_only_listed_files(self):
        self.fake_curl(tarball=self.make_snapshot(), raw_ok=False)
        r = self.run_bootstrap()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('путь: одним архивом', r.stdout)
        for rel in FILES + FILES_OPTIONAL:
            p = self.install / rel
            self.assertTrue(p.is_file(), 'не разложен: %s' % rel)
            self.assertEqual(p.read_text(encoding='utf-8'), ': SNAPSHOT %s\n' % rel)
        for rel in EXTRA_IN_REPO:
            self.assertFalse((self.install / rel).exists(),
                             'из архива приехало лишнее: %s' % rel)
        self.assert_state_untouched()

    def test_output_names_the_way_and_the_seconds(self):
        self.fake_curl(tarball=self.make_snapshot(), raw_ok=False)
        r = self.run_bootstrap()
        # NC-28: число согласовано со словом — «94 файла», «21 файл», «11 файлов»
        self.assertRegex(r.stdout, r'путь: одним архивом, \d+ файл(а|ов)?, заняло \d+с')

    def test_file_count_agrees_with_the_number(self):
        # Функции загрузчика bs и bs_files — отдельно, без запуска самого загрузчика
        script = ('eval "$(sed -n \'/^bs() {/,/^}/p;/^bs_files() {/,/^}/p\' bootstrap.sh)"; '
                  'BS_LANG=ru; for n in 1 2 5 11 14 21 22 94 111 112; do bs_files "$n"; echo; done; '
                  'BS_LANG=en; for n in 1 21 94; do bs_files "$n"; echo; done')
        r = subprocess.run([BASH, '-c', script], cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(r.stdout.splitlines(),
                         ['1 файл', '2 файла', '5 файлов', '11 файлов', '14 файлов', '21 файл', '22 файла',
                          '94 файла', '111 файлов', '112 файлов', '1 file', '21 files', '94 files'], r.stderr)

    def test_pat_is_on_stdin_for_archive_and_fallback_never_in_curl_argv(self):
        sentinel = 'goamnezia-private-test-token'
        for archive in (True, False):
            with self.subTest(archive=archive):
                argv = self.tmp / 'curl-argv.log'
                stdin = self.tmp / 'curl-stdin.log'
                argv.write_text('')
                stdin.write_text('')
                self.fake_curl(tarball=self.make_snapshot(), tarball_code='200' if archive else '500')
                r = self.run_bootstrap({'GOAMNEZIA_PAT': sentinel,
                    'GA_AUTH_ARGV_LOG': str(argv), 'GA_AUTH_STDIN_LOG': str(stdin)})
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn(sentinel, argv.read_text())
                headers = stdin.read_text().splitlines()
                self.assertGreaterEqual(len(headers), 2 if archive else len(FILES) + 2)
                self.assertTrue(all(h == 'Authorization: token ' + sentinel for h in headers))

    def test_english_output(self):
        self.fake_curl(tarball=self.make_snapshot(), raw_ok=False)
        r = self.run_bootstrap({'GOAMNEZIA_LANG': 'en'})
        self.assertRegex(r.stdout, r'way: one archive, \d+ files, took \d+s')

    def test_tarball_failure_falls_back_to_file_by_file(self):
        """Архив не отдали (500) — установка обязана пройти прежним путём."""
        self.fake_curl(tarball=None, tarball_code='500', raw_ok=True)
        r = self.run_bootstrap()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('Архив не получился', r.stdout)
        self.assertIn('путь: по одному файлу', r.stdout)
        self.assertEqual((self.install / 'install.sh').read_text(encoding='utf-8'),
                         ': RAW install.sh\n')
        self.assert_state_untouched()

    def test_broken_archive_falls_back(self):
        """Битый tar.gz распаковаться не может — тоже поштучный путь."""
        broken = self.tmp / 'broken.tar.gz'
        broken.write_bytes(b'not a gzip at all')
        self.fake_curl(tarball=broken, raw_ok=True)
        r = self.run_bootstrap()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('путь: по одному файлу', r.stdout)
        self.assertEqual((self.install / 'install.sh').read_text(encoding='utf-8'),
                         ': RAW install.sh\n')

    def test_incomplete_archive_falls_back(self):
        """В снимке нет обязательного файла → доверять снимку нельзя."""
        self.fake_curl(tarball=self.make_snapshot(skip=('lib/users.sh',)), raw_ok=True)
        r = self.run_bootstrap()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('путь: по одному файлу', r.stdout)
        self.assertEqual((self.install / 'lib/users.sh').read_text(encoding='utf-8'),
                         ': RAW lib/users.sh\n')

    def test_no_tarball_switch(self):
        """GOAMNEZIA_NO_TARBALL=1 — прежний путь без единой попытки архива."""
        self.fake_curl(tarball=self.make_snapshot(), raw_ok=True)
        r = self.run_bootstrap({'GOAMNEZIA_NO_TARBALL': '1'})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn('одним архивом', r.stdout)
        self.assertIn('путь: по одному файлу', r.stdout)

    def test_snapshot_is_taken_by_commit_sha(self):
        """Ссылка на архив идёт по SHA коммита — защита от кэша raw (§bootstrap)."""
        self.fake_curl(tarball=self.make_snapshot(), raw_ok=False)
        r = self.run_bootstrap()
        src = (self.install / '.install-source').read_text(encoding='utf-8')
        self.assertIn('ref=%s' % FAKE_SHA, src)
        self.assertIn('путь: одним архивом', r.stdout)

    def test_dry_run_installs_no_packages(self):
        """Сухой прогон не зовёт apt-get, даже если jq на машине нет (находка L2-03).

        Загрузчику из трёх утилит нужны только curl и tar, а apt-get update/install на машине разработчика
        или в CI менял бы чужую систему. PATH собираем из ссылок на всё, что есть, кроме jq; apt-get
        подменён записью в журнал — журнала быть не должно.
        """
        self.fake_curl(tarball=self.make_snapshot(), raw_ok=False)
        log = self.tmp / 'apt.log'
        apt = self.bin / 'apt-get'
        apt.write_text('#!/bin/sh\necho "apt-get $*" >> "%s"\nexit 0\n' % log, encoding='utf-8')
        apt.chmod(0o755)
        sysbin = self.tmp / 'sysbin'
        sysbin.mkdir()
        for d in os.environ.get('PATH', '').split(os.pathsep):
            if not d or not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                if name in ('jq', 'apt-get', 'curl') or os.path.lexists(sysbin / name):
                    continue
                src = os.path.join(d, name)
                if os.path.isfile(src) and os.access(src, os.X_OK):
                    (sysbin / name).symlink_to(src)
        r = self.run_bootstrap({'PATH': '%s:%s' % (self.bin, sysbin)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(log.exists(), log.read_text(encoding='utf-8') if log.exists() else '')
        self.assertIn('DRY: apt-get install -y jq', r.stdout)
        self.assertIn('сухой прогон, пакет не ставим', r.stdout)
        self.assertNotIn('apt-get install -y tar', r.stdout)
        self.assertIn('путь: одним архивом', r.stdout)

    def test_missing_required_file_everywhere_is_an_error(self):
        """Ни архива, ни raw — установка обязана упасть, а не «завершиться»."""
        self.fake_curl(tarball=None, tarball_code='500', raw_ok=False)
        r = self.run_bootstrap()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Не удалось загрузить файлов', r.stdout)


if __name__ == '__main__':
    unittest.main()
