# -*- coding: utf-8 -*-
"""Две ветки — main (стабильная) и dev (экспериментальная): выход в main, M1, M2, M7.

Правила серии goinstaller-ready требуют от продукта: ветка по умолчанию — main; загрузчик умеет сменить ветку
у уже установленного и после смены сверяет версию, а не печатает успех; .install-source хранит ветку, из которой
файлы легли на самом деле; ВСЁ, включая сам загрузчик, качается по SHA коммита — у raw по имени ветки кэш на пять
минут. Здесь проверяется:

  • BootstrapBranches — bootstrap.sh: умолчание main, dev по GOAMNEZIA_BRANCH, .install-source, копия загрузчика
    из коммита (exec) и её единственность, GOAMNEZIA_REF без повторных запросов, откат на имя ветки без API,
    отказ на несуществующей ветке и на недопустимом имени, сверка версии на диске, ключ не в argv;
  • SelfUpdateScreen — «Обслуживание → Обновить goAmnezia» (_selfupdate_screen в install.sh): выбор ветки с
    умолчанием из .install-source, без прежней подмены main → dev, версия и коммит ветки у GitHub, откат только
    набранным словом и после копии, отказ на несуществующей ветке, конец ввода — отмена, устаревший коммит из
    окружения не прилипает, страница ошибки вместо загрузчика не запускается; NC-20 — перед вводом ключа сказано,
    что это за ключ, до вопроса — «доступна vX» или «у вас последняя», ключ не печатается и не ложится на диск;
  • SwitchDryRun — переезд установленного dev → main → dev вхолостую: экран меню передаёт управление настоящему
    загрузчику из коммита, тот раскладывает файлы ветки, сверяет версию и пишет .install-source;
  • OneLiners — однострочники в шапках и документах ведут на main.

Сети не нужно: curl подменяется скриптом на PATH (GitHub по карте из файла), сервер — временный каталог,
GOAMNEZIA_DRY_RUN=1. Экран меню зовётся из install.sh без main(): копия файла без последней строки.

Запуск: python3 -m unittest tests.test_branches
"""
import json
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
INSTALL = ROOT / 'install.sh'
REPO = 'anten-ka/goamnezia_pro'
PAT = 'goamnezia-branches-test-token'
SHA = {'main': '1' * 40, 'dev': '2' * 40}
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\r')


def code_version():
    m = re.search(r'^GOAMNEZIA_VERSION="([^"]+)"', (ROOT / 'lib' / 'common.sh').read_text(encoding='utf-8'), re.M)
    assert m, 'нет GOAMNEZIA_VERSION в lib/common.sh'
    return m.group(1)


def read_array(name):
    """Массив FILES / FILES_OPTIONAL из bootstrap.sh (как в tests/test_bootstrap_archive.py)."""
    text = BOOTSTRAP.read_text(encoding='utf-8')
    m = re.search(r'^%s=\(\s*(.*?)^\)' % re.escape(name), text, re.S | re.M)
    assert m, name
    out = []
    for line in m.group(1).splitlines():
        out.extend(re.findall(r'"([^"]+)"', line.split('#', 1)[0]))
    return out


FILES = read_array('FILES') + read_array('FILES_OPTIONAL')
LOADER_MARK = 'echo "LOADER %s REF=${GOAMNEZIA_REF:-} STDIN=[$(head -c 40 | tr \'\\n\' \' \')]"'
VERSION = code_version()
# Версии веток относительно кода: main ниже (переезд на неё — откат), dev выше.
VER = {'main': '0.0.1', 'dev': '99.0.0'}

# ── подставной curl ──────────────────────────────────────────────────────────
# Разбирает ровно те ключи, с которыми curl зовут bootstrap.sh (-H @-, -H 'Accept: …', -w, -o, -f) и install.sh
# (-K - с заголовками в stdin). Каждый запрос — строкой JSON в журнал GA_FAKE_LOG (адрес, заголовки, argv).
FAKE_CURL = r'''#!/usr/bin/env python3
# Подставной curl для tests/test_branches.py: GitHub без сети, ответы — по карте из GA_FAKE_GH.
import json, os, re, sys


def route(cfg, url, headers):
    api = 'https://api.github.com/repos/%s/' % cfg['repo']
    raw = 'https://raw.githubusercontent.com/%s/' % cfg['repo']
    if url.startswith(api):
        if cfg.get('api') == 'down':
            return '000', b''
        rest = url[len(api):]
        if rest.startswith('commits/'):
            ref = rest[len('commits/'):]
            sha = cfg['branches'].get(ref)
            if not sha:
                return '422', ('{"message":"No commit found for SHA: %s","status":"422"}' % ref).encode()
            if any(h.lower() == 'accept: application/vnd.github.sha' for h in headers):
                return '200', sha.encode()
            return '200', ('{"sha": "%s", "commit": {"tree": {"sha": "%s"}}}' % (sha, 'f' * 40)).encode()
        if rest.startswith('tarball/'):
            p = cfg['tarballs'].get(rest[len('tarball/'):])
            if p and os.path.isfile(p):
                with open(p, 'rb') as f:
                    return '200', f.read()
        return '404', b'{"message":"Not Found"}'
    if url.startswith(raw):
        rest = url[len(raw):]
        for ref in sorted(cfg['trees'], key=len, reverse=True):
            if rest.startswith(ref + '/'):
                path = rest[len(ref) + 1:]
                over = cfg.get('override', {}).get(ref + '/' + path)
                if over is not None:
                    return '200', over.encode()
                p = os.path.join(cfg['trees'][ref], path)
                if os.path.isfile(p):
                    with open(p, 'rb') as f:
                        return '200', f.read()
                break
        return '404', b'404: Not Found'
    return '000', b''


def main():
    with open(os.environ['GA_FAKE_GH'], encoding='utf-8') as f:
        cfg = json.load(f)
    args = sys.argv[1:]
    out = wfmt = url = None
    fail = False
    headers = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ('-o', '-w', '--max-time', '-H', '-K'):
            v = args[i + 1] if i + 1 < len(args) else ''
            i += 2
            if a == '-o':
                out = v
            elif a == '-w':
                wfmt = v
            elif a == '-H':
                headers.append(sys.stdin.readline().rstrip('\n') if v == '@-' else v)
            elif a == '-K' and v == '-':
                for line in sys.stdin.read().splitlines():
                    m = re.match(r'\s*header\s*=\s*"(.*)"\s*$', line)
                    if m:
                        headers.append(m.group(1))
            continue
        if a.startswith('--'):
            i += 1
            continue
        if a.startswith('-') and len(a) > 1:
            fail = fail or 'f' in a[1:]
            i += 1
            continue
        url = a
        i += 1
    with open(os.environ['GA_FAKE_LOG'], 'a', encoding='utf-8') as f:
        f.write(json.dumps({'url': url, 'headers': headers, 'argv': args}) + '\n')
    code, body = route(cfg, url or '', headers)
    tail = (wfmt or '').replace('\\n', '\n').replace('%{http_code}', code).encode()
    if code == '000':
        sys.stdout.buffer.write(tail)
        sys.exit(7)
    failed = fail and int(code) >= 400
    if not failed:
        if out:
            with open(out, 'wb') as f:
                f.write(body)
        else:
            sys.stdout.buffer.write(body)
    sys.stdout.buffer.write(tail)
    sys.exit(22 if failed else 0)


main()
'''


class FakeGitHub:
    """Две ветки во временном каталоге: деревья файлов, архивы по SHA и по имени, журнал запросов."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.bin = tmp / 'bin'
        self.bin.mkdir(parents=True)
        curl = self.bin / 'curl'
        curl.write_text(FAKE_CURL, encoding='utf-8')
        curl.chmod(0o755)
        if not os.access(str(curl), os.X_OK) or not shutil.which('python3'):
            raise unittest.SkipTest('подставной curl не запустить (noexec во временном каталоге или нет python3)')
        self.log = tmp / 'curl.log'
        self.cfg_path = tmp / 'github.json'
        self.cfg = {'repo': REPO, 'api': 'up', 'branches': dict(SHA), 'trees': {}, 'tarballs': {}, 'override': {}}
        for name in ('main', 'dev'):
            root = self.make_tree(name)
            tgz = tmp / ('%s.tar.gz' % name)
            with tarfile.open(tgz, 'w:gz') as tar:
                tar.add(root, arcname=root.name)
            for ref in (name, SHA[name]):
                self.cfg['trees'][ref] = str(root)
                self.cfg['tarballs'][ref] = str(tgz)
        self.save()

    def make_tree(self, name):
        root = self.tmp / 'gh' / name / ('anten-ka-goamnezia_pro-%s' % SHA[name][:7])
        for rel in FILES:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            # Строка shell: в конце загрузчик делает source lib/i18n.sh.
            p.write_text(': TREE %s %s\n' % (name, rel), encoding='utf-8')
        (root / 'lib' / 'common.sh').write_text('GOAMNEZIA_VERSION="%s"\n' % VER[name], encoding='utf-8')
        # Загрузчик коммита — настоящий bootstrap.sh с меткой: по ней видно, что работала копия ИЗ КОММИТА, и что
        # ей досталось в stdin (при `curl … | bash` там не должно остаться строк прежней копии).
        text = BOOTSTRAP.read_text(encoding='utf-8')
        first, rest = text.split('\n', 1)
        marker = LOADER_MARK % name
        (root / 'bootstrap.sh').write_text(first + '\n' + marker + '\n' + rest, encoding='utf-8')
        return root

    def save(self):
        self.cfg_path.write_text(json.dumps(self.cfg), encoding='utf-8')
        self.reset_log()

    def reset_log(self):
        self.log.write_text('', encoding='utf-8')

    def requests(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text(encoding='utf-8').splitlines() if l.strip()]

    def urls(self):
        return [r['url'] for r in self.requests()]

    def env(self):
        return {'PATH': '%s:%s' % (self.bin, os.environ.get('PATH', '')),
                'GA_FAKE_GH': str(self.cfg_path), 'GA_FAKE_LOG': str(self.log)}


def run_bash(argv, env, stdin_text=None, cwd=ROOT, reset=None):
    """Прогон bash с повтором ровно на обвал самого bash из homebrew (код 139, SIGSEGV).

    Причина та же, что в tests/test_bootstrap_archive.py: bash из homebrew слинкован с gettext, его setlocale
    ходит в CoreFoundation (CFLocaleCopyPreferredLanguages), а тот под нагрузкой разваливается; на сервере (glibc)
    этого нет. Здесь загрузчик запускает себя второй раз (копия из коммита) и печатает до обвала, поэтому пустого
    вывода, как там, ждать нельзя: сигнал 11 сам по себе однозначен — ни загрузчик, ни экран так не выходят.
    Перед повтором reset() возвращает журналы к началу, чтобы проверки считали запросы одного прогона."""
    kw = dict(env=env, cwd=str(cwd), capture_output=True, text=True, timeout=180)
    if stdin_text is not None:
        kw['input'] = stdin_text
    else:
        kw['stdin'] = subprocess.DEVNULL
    r = subprocess.run(argv, **kw)
    for _ in range(3):
        if r.returncode != 139:
            break
        if reset:
            reset()
        r = subprocess.run(argv, **kw)
    return r


def install_source(path):
    out = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                out[k] = v
    return out


def base_env(extra):
    e = dict(os.environ)
    for k in ('GOAMNEZIA_BRANCH', 'GOAMNEZIA_REF', 'GOAMNEZIA_EXPECT_VERSION', 'GOAMNEZIA_NO_TARBALL',
              'GOAMNEZIA_PAT', 'SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY'):
        e.pop(k, None)
    e.update(extra)
    return e


# ══════════════════════════════════════════════════════════════════════════════
class BootstrapBranches(unittest.TestCase):
    """bootstrap.sh: ветка по умолчанию, .install-source, загрузчик по SHA (M1, M7)."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ga-br-'))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.gh = FakeGitHub(self.tmp)
        self.install = self.tmp / 'opt'
        self.tmpdir = self.tmp / 'tmpdir'
        self.tmpdir.mkdir()
        self.install.mkdir()
        # Состояние сервера: загрузчик обязан пройти мимо него.
        (self.install / 'config.json').write_text('{"installed":true}', encoding='utf-8')

    def bootstrap(self, **env):
        e = base_env(self.gh.env())
        e.update({'GOAMNEZIA_DIR': str(self.install), 'GOAMNEZIA_DRY_RUN': '1', 'GOAMNEZIA_PAT': PAT,
                  'GOAMNEZIA_LANG': 'ru', 'TMPDIR': str(self.tmpdir)})
        e.update(env)
        r = run_bash([BASH, str(BOOTSTRAP)], e, reset=self.gh.reset_log)
        return r, ANSI.sub('', r.stdout + r.stderr)

    def source(self):
        return install_source(self.install / '.install-source')

    def disk_version(self):
        m = re.search(r'GOAMNEZIA_VERSION="([^"]+)"',
                      (self.install / 'lib' / 'common.sh').read_text(encoding='utf-8'))
        return m.group(1) if m else None

    # ── M1: ветка по умолчанию и .install-source ─────────────────────────────
    def test_default_branch_is_main(self):
        r, out = self.bootstrap()
        self.assertEqual(r.returncode, 0, out)
        self.assertIn('https://api.github.com/repos/%s/commits/main' % REPO, self.gh.urls())
        self.assertFalse(any('/commits/dev' in u for u in self.gh.urls()), self.gh.urls())
        self.assertEqual(self.source(), {'branch': 'main', 'repo': REPO, 'ref': SHA['main']})
        self.assertEqual(self.disk_version(), VER['main'])
        self.assertIn('Ветка main — стабильная, коммит %s' % SHA['main'][:12], out)
        self.assertIn('На диске: ветка main, версия %s, коммит %s' % (VER['main'], SHA['main'][:12]), out)

    def test_dev_on_request_and_recorded_as_dev(self):
        r, out = self.bootstrap(GOAMNEZIA_BRANCH='dev')
        self.assertEqual(r.returncode, 0, out)
        self.assertIn('https://api.github.com/repos/%s/commits/dev' % REPO, self.gh.urls())
        self.assertEqual(self.source(), {'branch': 'dev', 'repo': REPO, 'ref': SHA['dev']})
        self.assertEqual(self.disk_version(), VER['dev'])
        self.assertIn('Ветка dev — экспериментальная', out)
        # Состояние сервера не тронуто, от записи .install-source не осталось времянки.
        self.assertEqual((self.install / 'config.json').read_text(encoding='utf-8'), '{"installed":true}')
        self.assertFalse((self.install / '.install-source.tmp').exists())

    def test_api_asked_for_bare_sha(self):
        self.bootstrap()
        api = [q for q in self.gh.requests() if '/commits/' in q['url']]
        self.assertEqual(len(api), 1, api)
        self.assertIn('Accept: application/vnd.github.sha', api[0]['headers'])

    # ── M7: сам загрузчик — по SHA ───────────────────────────────────────────
    def test_loader_itself_is_taken_by_sha_and_runs_once(self):
        r, out = self.bootstrap()
        self.assertEqual(r.returncode, 0, out)
        urls = self.gh.urls()
        self.assertIn('https://raw.githubusercontent.com/%s/%s/bootstrap.sh' % (REPO, SHA['main']), urls)
        # Работала копия из коммита — и ровно один раз; второй раз она себя не качает и API не спрашивает.
        self.assertEqual(out.count('LOADER main REF=%s' % SHA['main']), 1, out)
        self.assertEqual(sum('bootstrap.sh' in u for u in urls), 1, urls)
        self.assertEqual(sum('/commits/' in u for u in urls), 1, urls)
        # Всё остальное — тоже по SHA: ни одного адреса по имени ветки.
        self.assertIn('https://api.github.com/repos/%s/tarball/%s' % (REPO, SHA['main']), urls)
        self.assertFalse([u for u in urls if '/commits/' not in u and ('/main/' in u or u.endswith('/main'))], urls)
        # Времянка копии загрузчика удалена сразу после открытия.
        self.assertEqual(list(self.tmpdir.glob('goamnezia-bootstrap.*')), [])

    def test_script_from_stdin_is_drained_before_handing_over(self):
        # `curl … | bash`: остаток этой копии лежит в stdin. Копия из коммита (а за ней install.sh) его не получает.
        e = base_env(self.gh.env())
        e.update({'GOAMNEZIA_DIR': str(self.install), 'GOAMNEZIA_DRY_RUN': '1', 'GOAMNEZIA_PAT': PAT,
                  'GOAMNEZIA_LANG': 'ru', 'TMPDIR': str(self.tmpdir)})
        r = run_bash([BASH], e, stdin_text=BOOTSTRAP.read_text(encoding='utf-8'), reset=self.gh.reset_log)
        out = ANSI.sub('', r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0, out)
        self.assertIn('LOADER main REF=%s STDIN=[]' % SHA['main'], out)
        self.assertEqual(self.source(), {'branch': 'main', 'repo': REPO, 'ref': SHA['main']})

    def test_given_ref_skips_api_and_self_download(self):
        r, out = self.bootstrap(GOAMNEZIA_BRANCH='dev', GOAMNEZIA_REF=SHA['dev'])
        self.assertEqual(r.returncode, 0, out)
        urls = self.gh.urls()
        self.assertFalse([u for u in urls if '/commits/' in u or u.endswith('/bootstrap.sh')], urls)
        self.assertIn('https://api.github.com/repos/%s/tarball/%s' % (REPO, SHA['dev']), urls)
        self.assertEqual(self.source(), {'branch': 'dev', 'repo': REPO, 'ref': SHA['dev']})

    def test_invalid_given_ref_is_ignored(self):
        r, out = self.bootstrap(GOAMNEZIA_REF='not-a-sha')
        self.assertEqual(r.returncode, 0, out)
        self.assertIn('https://api.github.com/repos/%s/commits/main' % REPO, self.gh.urls())
        self.assertEqual(self.source()['ref'], SHA['main'])

    def test_api_down_falls_back_to_branch_name_without_self_download(self):
        self.gh.cfg['api'] = 'down'
        self.gh.save()
        r, out = self.bootstrap()
        self.assertEqual(r.returncode, 0, out)
        urls = self.gh.urls()
        self.assertFalse([u for u in urls if u.endswith('/bootstrap.sh')], urls)
        self.assertIn('https://api.github.com/repos/%s/tarball/main' % REPO, urls)
        self.assertEqual(self.source(), {'branch': 'main', 'repo': REPO, 'ref': 'main'})
        self.assertIn('GitHub API не назвал коммит ветки', out)
        self.assertIn('коммит —', out)

    def test_error_page_instead_of_loader_is_not_run(self):
        self.gh.cfg['override']['%s/bootstrap.sh' % SHA['main']] = '<!DOCTYPE html><title>Error</title>'
        self.gh.save()
        r, out = self.bootstrap()
        self.assertEqual(r.returncode, 0, out)
        self.assertNotIn('LOADER', out)
        self.assertIn('Загрузчик этого коммита не скачался', out)
        # Файлы всё равно из коммита.
        self.assertEqual(self.source(), {'branch': 'main', 'repo': REPO, 'ref': SHA['main']})
        self.assertEqual(list(self.tmpdir.glob('goamnezia-bootstrap.*')), [])

    def test_missing_branch_stops_with_a_clear_message(self):
        (self.install / '.install-source').write_text('branch=dev\nrepo=%s\nref=%s\n' % (REPO, SHA['dev']),
                                                      encoding='utf-8')
        r, out = self.bootstrap(GOAMNEZIA_BRANCH='nosuch')
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn('В репозитории нет ветки nosuch', out)
        self.assertIn('export GOAMNEZIA_BRANCH=dev', out)
        self.assertFalse((self.install / 'install.sh').exists())
        self.assertEqual(self.source()['branch'], 'dev')
        self.assertFalse([u for u in self.gh.urls() if 'tarball' in u or 'raw.githubusercontent' in u])

    def test_invalid_branch_name_refused_before_any_request(self):
        for bad in ('main\nrepo=evil/repo', '../main', '-main', 'ma in'):
            with self.subTest(branch=bad):
                self.gh.save()
                r, out = self.bootstrap(GOAMNEZIA_BRANCH=bad)
                self.assertNotEqual(r.returncode, 0, out)
                self.assertIn('Недопустимое имя ветки', out)
                self.assertEqual(self.gh.urls(), [])
                self.assertFalse((self.install / '.install-source').exists())

    def test_working_branch_with_slash_is_allowed(self):
        self.gh.cfg['branches']['release/0.5.0'] = SHA['dev']
        self.gh.save()
        r, out = self.bootstrap(GOAMNEZIA_BRANCH='release/0.5.0')
        self.assertEqual(r.returncode, 0, out)
        self.assertIn('Ветка release/0.5.0 — рабочая ветка', out)
        self.assertEqual(self.source(), {'branch': 'release/0.5.0', 'repo': REPO, 'ref': SHA['dev']})

    # ── сверка версии после переключения ─────────────────────────────────────
    def test_expected_version_matches_disk(self):
        r, out = self.bootstrap(GOAMNEZIA_BRANCH='dev', GOAMNEZIA_REF=SHA['dev'],
                                GOAMNEZIA_EXPECT_VERSION=VER['dev'])
        self.assertEqual(r.returncode, 0, out)
        self.assertIn('На диске: ветка dev, версия %s' % VER['dev'], out)

    def test_expected_version_mismatch_stops_and_keeps_install_source(self):
        (self.install / '.install-source').write_text('branch=main\nrepo=%s\nref=%s\n' % (REPO, SHA['main']),
                                                      encoding='utf-8')
        r, out = self.bootstrap(GOAMNEZIA_BRANCH='dev', GOAMNEZIA_REF=SHA['dev'], GOAMNEZIA_EXPECT_VERSION='7.7.7')
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn('На диске не та версия, что ждали: %s ≠ 7.7.7' % VER['dev'], out)
        self.assertIn('Повторите «Обновить goAmnezia»', out)
        self.assertEqual(self.source(), {'branch': 'main', 'repo': REPO, 'ref': SHA['main']})

    def test_pat_never_in_argv_and_every_request_authorized(self):
        for env in ({}, {'GOAMNEZIA_NO_TARBALL': '1'}):
            with self.subTest(env=env):
                self.gh.save()
                r, out = self.bootstrap(**env)
                self.assertEqual(r.returncode, 0, out)
                reqs = self.gh.requests()
                self.assertGreater(len(reqs), 2)
                for q in reqs:
                    self.assertNotIn(PAT, ' '.join(q['argv']))
                    self.assertIn('Authorization: token ' + PAT, q['headers'], q['url'])

    def test_no_pat_no_request(self):
        e = {'GOAMNEZIA_PAT': ''}
        r, out = self.bootstrap(**e)
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn('Не задан GitHub-токен', out)
        self.assertEqual(self.gh.urls(), [])


# ══════════════════════════════════════════════════════════════════════════════
DRIVER = r'''
set -uo pipefail
# shellcheck disable=SC1090,SC1091
source "$SU_APP/install.sh"
# Терминала у прогона нет: ответы на вопросы — из переменных, вызовы — в журнал.
confirm() { printf 'CONFIRM\n' >>"$SU_CALLS"; printf '  %s\n' "${1:-}" >&2; [ "${SU_CONFIRM:-0}" = 1 ]; }
confirm_phrase() {
    printf 'PHRASE %s\n' "${1:-}" >>"$SU_CALLS"
    printf '  %s\n' "${2:-}" >&2
    # Как настоящий: об отказе говорит сам, одной строкой (LH-3), — вызывающий своё «Отменено.» не печатает
    [ -n "${SU_PHRASE:-}" ] && [ "$SU_PHRASE" = "${1:-}" ] && return 0
    log_info "$(t cancelled)"
    return 1
}
_read_secret_tty() { printf -v "$1" '%s' "${SU_PAT_TTY:-}"; }
create_backup() { printf 'BACKUP %s\n' "${3:-}" >>"$SU_CALLS"; return "${SU_BACKUP_RC:-0}"; }
_backup_where() { :; }
if [ "${SU_REAL_EXEC:-0}" != 1 ]; then
    _selfupdate_exec() {
        printf 'EXEC\n' >>"$SU_CALLS"
        cp "$1" "$SU_LOADER"
        rm -f "$1"
        env | grep -E '^GOAMNEZIA_(PAT|BRANCH|REF|EXPECT_VERSION)=' | LC_ALL=C sort >"$SU_ENV"
        exit 0
    }
fi
_selfupdate_screen
printf 'RC=%s\n' "$?"
'''


class ScreenHarness:
    """install.sh без main(): SCRIPT_DIR — копия кода с lib рядом, GOAMNEZIA_DIR — «сервер»."""

    def __init__(self, tmp, gh, server=None):
        self.tmp = tmp
        self.gh = gh
        self.app = tmp / 'app'
        self.app.mkdir()
        text = INSTALL.read_text(encoding='utf-8')
        tail = '\nmain "$@"\n'
        assert text.endswith(tail), 'install.sh больше не кончается строкой main "$@" — поправьте прогон'
        (self.app / 'install.sh').write_text(text[:-len(tail)] + '\n', encoding='utf-8')
        (self.app / 'lib').symlink_to(ROOT / 'lib')
        self.server = server or self.app
        self.server.mkdir(exist_ok=True)
        (self.server / '.language').write_text('ru', encoding='utf-8')
        if self.server != self.app:
            # В боевой раскладке это один каталог (/opt/goamnezia): экран читает .install-source рядом с кодом.
            (self.app / '.install-source').symlink_to(self.server / '.install-source')
        self.driver = tmp / 'driver.sh'
        self.driver.write_text(DRIVER, encoding='utf-8')
        self.calls = tmp / 'calls.log'
        self.loader = tmp / 'loader.sh'
        self.envf = tmp / 'loader.env'
        (tmp / 'run').mkdir()
        (tmp / 'tmpdir').mkdir()

    def set_source(self, branch, repo=REPO, ref=None):
        (self.server / '.install-source').write_text(
            'branch=%s\nrepo=%s\nref=%s\n' % (branch, repo, ref or SHA.get(branch, branch)), encoding='utf-8')

    def run(self, keys, **env):
        for p in (self.calls, self.loader, self.envf):
            if p.exists():
                p.unlink()
        self.calls.write_text('', encoding='utf-8')
        e = base_env(self.gh.env())
        e.update({
            'GOAMNEZIA_DRY_RUN': '1',
            'GOAMNEZIA_DIR': str(self.server),
            'GOAMNEZIA_LOCK_DIR': str(self.tmp / 'run'),
            'GOAMNEZIA_LOG_FILE': str(self.tmp / 'goamnezia.log'),
            'GOAMNEZIA_AUDIT_LOG': str(self.tmp / 'audit.log'),
            'GOAMNEZIA_NOTIFY_DIR': str(self.tmp / 'run' / 'notify'),
            'GOAMNEZIA_PAT': PAT,
            'GOAMNEZIA_LANG': 'ru',
            'TMPDIR': str(self.tmp / 'tmpdir'),
            'TERM': 'dumb',
            'SU_APP': str(self.app), 'SU_CALLS': str(self.calls),
            'SU_LOADER': str(self.loader), 'SU_ENV': str(self.envf),
        })
        e.update(env)
        r = run_bash([BASH, str(self.driver)], e, stdin_text=keys, reset=self.reset)
        return r, ANSI.sub('', r.stdout + r.stderr)

    def reset(self):
        # Обвал bash мог прийтись между mktemp и удалением времянки — повтор начинается с чистого каталога.
        self.calls.write_text('', encoding='utf-8')
        self.gh.reset_log()
        for p in self.server.glob('.bootstrap.*'):
            p.unlink()

    def calls_list(self):
        return [l for l in self.calls.read_text(encoding='utf-8').splitlines() if l]

    def loader_env(self):
        out = {}
        if self.envf.exists():
            for line in self.envf.read_text(encoding='utf-8').splitlines():
                k, v = line.split('=', 1)
                out[k] = v
        return out


class SelfUpdateScreen(unittest.TestCase):
    """«Обслуживание → Обновить goAmnezia»: выбор ветки, версия, откат (M2)."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ga-su-'))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.gh = FakeGitHub(self.tmp)
        self.h = ScreenHarness(self.tmp, self.gh)

    def assert_exec(self, branch, out):
        self.assertIn('EXEC', self.h.calls_list(), out)
        env = self.h.loader_env()
        self.assertEqual(env.get('GOAMNEZIA_BRANCH'), branch, out)
        self.assertEqual(env.get('GOAMNEZIA_REF'), SHA[branch])
        self.assertEqual(env.get('GOAMNEZIA_EXPECT_VERSION'), VER.get(branch, VERSION))
        self.assertEqual(env.get('GOAMNEZIA_PAT'), PAT)
        # Запущен бы был загрузчик ИМЕННО ЭТОГО коммита.
        self.assertEqual(self.h.loader.read_text(encoding='utf-8').splitlines()[1], LOADER_MARK % branch)
        # Времянка загрузчика в каталоге продукта не осталась.
        self.assertEqual(list(self.h.server.glob('.bootstrap.*')), [])
        return env

    def test_menu_offers_two_branches_and_marks_the_installed_one(self):
        self.h.set_source('main')
        r, out = self.h.run('0\n')
        self.assertIn('1) main — стабильная (сервер стоит отсюда)', out)
        self.assertIn('2) dev — экспериментальная', out)
        self.assertNotIn('рабочая ветка', out)
        self.assertIn('Ветка (Enter — main, 0 — назад)', out)
        self.assertIn('RC=0', out)
        self.assertEqual(self.gh.urls(), [])

    def test_enter_keeps_main_no_redirect_to_dev(self):
        # Прежний экран подменял main на dev. Теперь Enter — ветка, откуда поставлен сервер.
        self.h.set_source('main')
        r, out = self.h.run('\n', SU_PHRASE='откатить')
        urls = self.gh.urls()
        self.assertIn('https://api.github.com/repos/%s/commits/main' % REPO, urls)
        self.assertFalse([u for u in urls if '/dev' in u], urls)
        self.assert_exec('main', out)

    def test_enter_follows_dev_when_installed_from_dev(self):
        self.h.set_source('dev')
        r, out = self.h.run('\n', SU_CONFIRM='1')
        self.assertIn('2) dev — экспериментальная (сервер стоит отсюда)', out)
        self.assertIn('Доступна v%s — у вас v%s.' % (VER['dev'], VERSION), out)
        self.assertIn('Ветка dev, коммит %s.' % SHA['dev'][:12], out)
        # NC-20: сначала — что доступно, потом вопрос
        self.assertLess(out.index('Доступна v'), out.index('Обновить goAmnezia pro до v%s из ветки dev?' % VER['dev']))
        self.assertNotIn('Смена ветки', out)
        self.assert_exec('dev', out)
        self.assertEqual(self.h.calls_list(), ['CONFIRM', 'EXEC'])

    def test_switch_main_to_dev_is_an_update_with_plain_confirm(self):
        self.h.set_source('main')
        r, out = self.h.run('2\n', SU_CONFIRM='1')
        self.assertIn('Смена ветки: main → dev.', out)
        self.assertIn('Обновить goAmnezia pro до v%s из ветки dev?' % VER['dev'], out)
        self.assert_exec('dev', out)
        # Запросы: коммит головы (голый SHA), версия и загрузчик — по SHA, а не по имени ветки.
        urls = self.gh.urls()
        self.assertEqual(urls, [
            'https://api.github.com/repos/%s/commits/dev' % REPO,
            'https://raw.githubusercontent.com/%s/%s/lib/common.sh' % (REPO, SHA['dev']),
            'https://raw.githubusercontent.com/%s/%s/bootstrap.sh' % (REPO, SHA['dev']),
        ])
        self.assertIn('Accept: application/vnd.github.sha', self.gh.requests()[0]['headers'])

    def test_update_declined_changes_nothing(self):
        self.h.set_source('main')
        r, out = self.h.run('2\n')
        self.assertIn('Отменено', out)
        self.assertEqual(self.h.calls_list(), ['CONFIRM'])
        self.assertFalse([u for u in self.gh.urls() if u.endswith('/bootstrap.sh')])

    def test_switch_dev_to_main_downgrade_needs_the_word_and_a_backup_first(self):
        self.h.set_source('dev')
        r, out = self.h.run('1\n', SU_PHRASE='откатить')
        flat = ' '.join(out.split())
        self.assertIn('Смена ветки: dev → main.', out)
        self.assertIn('В ветке main версия %s — ниже установленной %s. Это откат' % (VER['main'], VERSION), flat)
        self.assertIn('Не снимется копия — отката не будет.', flat)
        # Обычного «1» мало: набранное слово, потом копия, потом загрузчик.
        self.assertEqual(self.h.calls_list(), ['PHRASE откатить', 'BACKUP data', 'EXEC'])
        self.assert_exec('main', out)

    def test_downgrade_without_the_word_is_cancelled(self):
        self.h.set_source('dev')
        r, out = self.h.run('1\n', SU_CONFIRM='1')
        self.assertIn('Отменено', out)
        self.assertEqual(self.h.calls_list(), ['PHRASE откатить'])

    def test_downgrade_without_a_backup_is_cancelled(self):
        self.h.set_source('dev')
        r, out = self.h.run('1\n', SU_PHRASE='откатить', SU_BACKUP_RC='1')
        self.assertIn('Копию снять не удалось — откат отменён, ничего не изменилось.', out)
        self.assertEqual(self.h.calls_list(), ['PHRASE откатить', 'BACKUP data'])
        self.assertEqual(list(self.h.server.glob('.bootstrap.*')), [])

    def test_same_version_other_commit_is_said_so(self):
        # Номер тот же, коммит другой (здесь — другая ветка): это обновление файлов, так и сказано
        self.gh.cfg['override']['%s/lib/common.sh' % SHA['dev']] = 'GOAMNEZIA_VERSION="%s"\n' % VERSION
        self.gh.save()
        self.h.set_source('main')
        r, out = self.h.run('2\n', SU_CONFIRM='1')
        flat = ' '.join(out.split())
        self.assertIn('Номер версии тот же — v%s, но в ветке dev другой коммит (%s, у вас %s)'
                      % (VERSION, SHA['dev'][:12], SHA['main'][:12]), flat)
        self.assertIn('Обновить goAmnezia pro до v%s из ветки dev?' % VERSION, out)
        env = self.h.loader_env()
        self.assertEqual(env.get('GOAMNEZIA_EXPECT_VERSION'), VERSION)

    def test_latest_is_said_before_the_reinstall_question(self):
        # NC-20: тот же коммит — «у вас последняя», и вопрос — «переустановить», а не «обновить»
        self.gh.cfg['override']['%s/lib/common.sh' % SHA['dev']] = 'GOAMNEZIA_VERSION="%s"\n' % VERSION
        self.gh.save()
        self.h.set_source('dev')
        r, out = self.h.run('\n')
        latest = 'У вас последняя версия ветки dev — v%s.' % VERSION
        ask = 'Переустановить v%s из ветки dev? Обычно незачем' % VERSION
        self.assertIn(latest, out)
        self.assertIn(ask, out)
        self.assertLess(out.index(latest), out.index(ask))
        self.assertNotIn('Доступна v', out)
        self.assertNotIn('Обновить goAmnezia pro до', out)
        # Отказ — ничего не качаем и не запускаем
        self.assertEqual(self.h.calls_list(), ['CONFIRM'])
        self.assertFalse([u for u in self.gh.urls() if u.endswith('/bootstrap.sh')])

    def test_key_is_explained_before_it_is_asked_and_never_printed_or_stored(self):
        self.h.set_source('main')
        r, out = self.h.run('2\n', SU_CONFIRM='1', GOAMNEZIA_PAT='', SU_PAT_TTY=PAT)
        flat = ' '.join(out.split())
        why = 'Ключ доступа — тот же, что вы вставляли в команду установки: репозиторий закрытый, без ключа он не отдаёт файлы.'
        self.assertIn(why, flat)
        self.assertIn('Ключ нигде не сохраняется.', flat)
        # NC6-03: где взять ключ — одной строкой, без паники
        self.assertIn('Где его взять: в закрытой группе Anten-ka Club, ветка goinstall.', flat)
        self.assertLess(flat.index(why), flat.index('Ключ доступа к репозиторию (ввод не показывается)'))
        # Версия — после ключа и до вопроса
        self.assertLess(flat.index('Ключ доступа к репозиторию'), flat.index('Доступна v%s' % VER['dev']))
        self.assertLess(flat.index('Доступна v%s' % VER['dev']), flat.index('Обновить goAmnezia pro до'))
        self.assertNotIn(PAT, out)
        # На диске ключа нет: ни рядом с кодом и .install-source, ни в журналах, ни во временном каталоге
        # (loader.env и журнал подставного GitHub — записи самого прогона, их не смотрим).
        places = [self.h.server, self.tmp / 'tmpdir']
        files = [self.tmp / 'goamnezia.log', self.tmp / 'audit.log']
        for place in places:
            for f in place.rglob('*'):
                if f.is_file() and not f.is_symlink() and 'lib' not in f.relative_to(place).parts:
                    files.append(f)
        for f in files:
            if f.exists():
                self.assertNotIn(PAT, f.read_text(encoding='utf-8', errors='replace'), str(f))
        self.assertEqual(self.h.loader_env().get('GOAMNEZIA_PAT'), PAT)   # загрузчику — только окружением

    def test_key_from_the_environment_is_not_asked_and_not_explained(self):
        self.h.set_source('main')
        r, out = self.h.run('2\n', SU_CONFIRM='1')
        self.assertNotIn('Ключ доступа к репозиторию', out)
        self.assertNotIn('вставляли в команду установки', out)
        self.assertNotIn(PAT, out)

    def test_english_screen(self):
        self.h.set_source('main')
        (self.h.server / '.language').write_text('en', encoding='utf-8')
        r, out = self.h.run('2\n', SU_CONFIRM='1', GOAMNEZIA_PAT='', SU_PAT_TTY=PAT, GOAMNEZIA_LANG='en')
        flat = ' '.join(out.split())
        self.assertIn('The access key is the same one you pasted into the install command', flat)
        self.assertIn('Where to get it: the private Anten-ka Club group, the goinstall topic.', flat)
        self.assertIn('Available: v%s — you have v%s.' % (VER['dev'], VERSION), flat)
        self.assertIn('Update goAmnezia pro to v%s from branch dev?' % VER['dev'], flat)
        self.assertNotIn(PAT, out)

    def test_zero_and_end_of_input_go_back_without_network(self):
        self.h.set_source('dev')
        for keys in ('0\n', ''):
            with self.subTest(keys=keys):
                self.gh.save()
                r, out = self.h.run(keys, SU_CONFIRM='1')
                self.assertIn('RC=0', out)
                self.assertEqual(self.gh.urls(), [])
                self.assertEqual(self.h.calls_list(), [])

    def test_missing_branch_is_named(self):
        del self.gh.cfg['branches']['main']
        self.gh.save()
        self.h.set_source('dev')
        r, out = self.h.run('1\n', SU_PHRASE='откатить')
        self.assertIn('Ветки main в репозитории нет.', out)
        self.assertEqual(self.h.calls_list(), [])
        self.assertEqual(len(self.gh.urls()), 1)

    def test_api_down_falls_back_to_branch_name_and_drops_a_stale_commit(self):
        self.gh.cfg['api'] = 'down'
        self.gh.save()
        self.h.set_source('main')
        r, out = self.h.run('2\n', SU_CONFIRM='1', GOAMNEZIA_REF='9' * 40, GOAMNEZIA_EXPECT_VERSION='9.9.9')
        self.assertIn('GitHub API не назвал коммит ветки', ' '.join(out.split()))
        self.assertIn('Доступна v%s — у вас v%s.' % (VER['dev'], VERSION), out)
        self.assertIn('Ветка dev, коммит —.', out)
        urls = self.gh.urls()
        self.assertIn('https://raw.githubusercontent.com/%s/dev/lib/common.sh' % REPO, urls)
        self.assertIn('https://raw.githubusercontent.com/%s/dev/bootstrap.sh' % REPO, urls)
        env = self.h.loader_env()
        self.assertEqual(env.get('GOAMNEZIA_BRANCH'), 'dev', out)
        self.assertNotIn('GOAMNEZIA_REF', env)
        self.assertNotIn('GOAMNEZIA_EXPECT_VERSION', env)

    def test_error_page_instead_of_loader_is_not_run(self):
        self.gh.cfg['override']['%s/bootstrap.sh' % SHA['dev']] = '<html>rate limited</html>'
        self.gh.save()
        self.h.set_source('main')
        r, out = self.h.run('2\n', SU_CONFIRM='1')
        self.assertIn('Загрузчик скачать не удалось', out)
        self.assertNotIn('EXEC', self.h.calls_list())
        self.assertEqual(list(self.h.server.glob('.bootstrap.*')), [])

    def test_working_branch_is_offered_third_and_is_the_default(self):
        self.gh.cfg['branches']['release/0.5.0'] = SHA['dev']
        self.gh.save()
        self.h.set_source('release/0.5.0', ref=SHA['dev'])
        r, out = self.h.run('\n', SU_CONFIRM='1')
        self.assertIn('3) release/0.5.0 — рабочая ветка (сервер стоит отсюда)', out)
        self.assertIn('Ветка (Enter — release/0.5.0, 0 — назад)', out)
        self.assertIn('https://api.github.com/repos/%s/commits/release/0.5.0' % REPO, self.gh.urls())
        self.assertEqual(self.h.loader_env().get('GOAMNEZIA_BRANCH'), 'release/0.5.0')

    def test_token_typed_by_hand_goes_only_to_curl_stdin(self):
        self.h.set_source('main')
        r, out = self.h.run('2\n', SU_CONFIRM='1', GOAMNEZIA_PAT='', SU_PAT_TTY=PAT)
        reqs = self.gh.requests()
        self.assertEqual(len(reqs), 3, out)
        for q in reqs:
            self.assertNotIn(PAT, ' '.join(q['argv']))
            self.assertIn('Authorization: token ' + PAT, q['headers'])
        self.assertEqual(self.h.loader_env().get('GOAMNEZIA_PAT'), PAT)

    def test_untrusted_install_source_is_not_used(self):
        for branch, repo in (('main', 'evil repo'), ('../main', REPO), ('main', '')):
            with self.subTest(branch=branch, repo=repo):
                self.gh.save()
                self.h.set_source(branch, repo=repo, ref='x')
                r, out = self.h.run('\n', SU_CONFIRM='1')
                self.assertIn('Откуда поставлен сервер, неизвестно', out)
                self.assertEqual(self.gh.urls(), [])

    def test_old_redirect_is_gone(self):
        body = re.search(r'^_selfupdate_screen\(\) \{\n(.*?)^\}', INSTALL.read_text(encoding='utf-8'),
                         re.S | re.M).group(1)
        self.assertNotRegex(body, r'\[ "\$branch" = main \] && branch=dev')


# ══════════════════════════════════════════════════════════════════════════════
class SwitchDryRun(unittest.TestCase):
    """Переезд установленного dev → main → dev вхолостую: экран → настоящий загрузчик коммита → диск."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ga-sw-'))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.gh = FakeGitHub(self.tmp)
        self.server = self.tmp / 'opt'
        self.h = ScreenHarness(self.tmp, self.gh, server=self.server)

    def disk(self):
        m = re.search(r'GOAMNEZIA_VERSION="([^"]+)"',
                      (self.server / 'lib' / 'common.sh').read_text(encoding='utf-8'))
        return install_source(self.server / '.install-source'), (m.group(1) if m else None)

    def test_dev_to_main_and_back(self):
        # Сервер поставлен из dev однострочником.
        e = base_env(self.gh.env())
        e.update({'GOAMNEZIA_DIR': str(self.server), 'GOAMNEZIA_DRY_RUN': '1', 'GOAMNEZIA_PAT': PAT,
                  'GOAMNEZIA_LANG': 'ru', 'GOAMNEZIA_BRANCH': 'dev', 'TMPDIR': str(self.tmp / 'tmpdir')})
        r = run_bash([BASH, str(BOOTSTRAP)], e, reset=self.gh.reset_log)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.disk(), ({'branch': 'dev', 'repo': REPO, 'ref': SHA['dev']}, VER['dev']))

        # dev → main: версия ниже — слово, копия, загрузчик коммита main раскладывает main и сверяет версию.
        self.gh.save()
        r, out = self.h.run('1\n', SU_PHRASE='откатить', SU_REAL_EXEC='1')
        self.assertEqual(r.returncode, 0, out)
        self.assertEqual(self.h.calls_list(), ['PHRASE откатить', 'BACKUP data'])
        self.assertIn('LOADER main REF=%s' % SHA['main'], out)
        self.assertIn('На диске: ветка main, версия %s, коммит %s' % (VER['main'], SHA['main'][:12]), out)
        self.assertEqual(self.disk(), ({'branch': 'main', 'repo': REPO, 'ref': SHA['main']}, VER['main']))
        # Загрузчик коммита второй раз себя не качал и API не спрашивал: коммит ему передал экран.
        urls = self.gh.urls()
        self.assertEqual(sum('/commits/' in u for u in urls), 1, urls)
        self.assertEqual(sum(u.endswith('/bootstrap.sh') for u in urls), 1, urls)
        self.assertEqual(list(self.server.glob('.bootstrap.*')), [])

        # main → dev: версия выше — обычное «1».
        self.gh.save()
        r, out = self.h.run('2\n', SU_CONFIRM='1', SU_REAL_EXEC='1')
        self.assertEqual(r.returncode, 0, out)
        self.assertIn('2) dev — экспериментальная', out)
        self.assertIn('1) main — стабильная (сервер стоит отсюда)', out)
        self.assertIn('LOADER dev REF=%s' % SHA['dev'], out)
        self.assertEqual(self.disk(), ({'branch': 'dev', 'repo': REPO, 'ref': SHA['dev']}, VER['dev']))


# ══════════════════════════════════════════════════════════════════════════════
class OneLiners(unittest.TestCase):
    """Однострочники в шапках и документах ведут на стабильную main; dev — только с GOAMNEZIA_BRANCH=dev."""

    ONE = re.compile(r'raw\.githubusercontent\.com/anten-ka/goamnezia_pro/([A-Za-z0-9._/-]+?)/bootstrap\.sh')

    def test_headers_and_docs_point_to_main(self):
        for rel in ('bootstrap.sh', 'install.sh', 'README.md', 'DOCS_HUMAN.md'):
            with self.subTest(file=rel):
                text = (ROOT / rel).read_text(encoding='utf-8')
                refs = self.ONE.findall(text)
                self.assertIn('main', refs, rel)
                for line in text.splitlines():
                    m = self.ONE.search(line)
                    if m and m.group(1) != 'main':
                        self.assertIn('GOAMNEZIA_BRANCH=%s' % m.group(1), line, line)

    def test_bootstrap_default_branch_is_main(self):
        self.assertRegex(BOOTSTRAP.read_text(encoding='utf-8'), r'(?m)^BRANCH="\$\{GOAMNEZIA_BRANCH:-main\}"$')

    def test_readme_no_longer_says_one_branch(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertTrue('Ветка одна' not in text, 'README всё ещё говорит «Ветка одна — dev»')
        self.assertTrue('GOAMNEZIA_BRANCH=dev' in text, 'в README нет однострочника для dev')


if __name__ == '__main__':
    unittest.main()
