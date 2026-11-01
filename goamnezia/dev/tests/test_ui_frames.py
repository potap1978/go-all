# -*- coding: utf-8 -*-
"""Рамки экранов: правый край обязан сходиться на любом языке.

Владелец на первой живой установке: «неровная рамка при старте». Причина —
ширину строки считали как ${#s}, а printf '%-Ns' и вовсе по БАЙТАМ: кириллица
занимает два байта на букву, эмодзи — четыре, и правый край уезжал ровно на
столько же знаков. Рамка баннера при этом была ещё и не замкнута (у загрузчика
были углы ╔╗ и левая ║, а правой не было вовсе).

Здесь проверяется результат: все строки рамки — верх, содержимое, низ — имеют
одинаковую ВИДИМУЮ ширину в колонках терминала. Ширина считается независимо от
кода продукта (unicodedata), иначе тест повторял бы ошибку, которую ищет.

Запуск: python3 -m unittest tests.test_ui_frames
"""
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unicodedata
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASH = os.environ.get('BASH5') or shutil.which('bash') or 'bash'

ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


def display_width(s):
    """Ширина строки в колонках терминала (без раскраски)."""
    s = ANSI.sub('', s)
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        if ch in '‍' or '︀' <= ch <= '️':
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
    return w


def run_bash(script, env=None):
    e = dict(os.environ)
    e.setdefault('GOAMNEZIA_DIR', '/tmp/goamnezia-frames-test')
    e['GOAMNEZIA_DRY_RUN'] = '1'
    if env:
        e.update(env)
    p = subprocess.run([BASH, '-c', script], cwd=str(ROOT), env=e,
                       capture_output=True, text=True)
    return p.stdout + p.stderr


def frame_lines(text, corners):
    """Строки рамки: от строки с левым верхним углом до строки с левым нижним."""
    top, bottom = corners
    lines = [ANSI.sub('', l).rstrip('\n') for l in text.splitlines()]
    start = next(i for i, l in enumerate(lines) if top in l)
    end = next(i for i, l in enumerate(lines) if bottom in l and i > start)
    return lines[start:end + 1]


class BannerFrame(unittest.TestCase):
    """install.sh → show_banner (lib/common.sh)."""

    def banner(self, lang):
        return run_bash(
            'source lib/common.sh; source lib/i18n.sh; load_language %s; show_banner' % lang)

    def test_banner_frame_is_square_in_both_languages(self):
        for lang in ('ru', 'en'):
            with self.subTest(lang=lang):
                lines = frame_lines(self.banner(lang), ('┌', '└'))
                self.assertGreaterEqual(len(lines), 5, lines)
                widths = {display_width(l) for l in lines}
                self.assertEqual(len(widths), 1,
                                 'ширины строк рамки разошлись: %r' % (
                                     [(display_width(l), l) for l in lines],))

    def test_banner_frame_is_closed_on_both_sides(self):
        for lang in ('ru', 'en'):
            with self.subTest(lang=lang):
                lines = frame_lines(self.banner(lang), ('┌', '└'))
                self.assertTrue(lines[0].startswith('┌') and lines[0].endswith('┐'))
                self.assertTrue(lines[-1].startswith('└') and lines[-1].endswith('┘'))
                for l in lines[1:-1]:
                    self.assertTrue(l.startswith('│') and l.endswith('│'), l)

    def test_banner_holds_the_longest_line(self):
        """Русский подзаголовок длиннее прежних 60 знаков — он обязан влезать."""
        lines = frame_lines(self.banner('ru'), ('┌', '└'))
        body = [l for l in lines[1:-1]]
        self.assertTrue(any('Совместимо' in l for l in body), body)
        for l in body:
            self.assertNotIn('  │', l[:-3] + 'xxx')  # текст не обрезан многоточием
            self.assertNotIn('…', l)


class BootstrapFrame(unittest.TestCase):
    """bootstrap.sh — самая первая рамка, которую видит человек."""

    def banner(self, lang):
        # Без root загрузчик печатает рамку и выходит — ровно то, что нужно. От root (контейнер CI на
        # Linux) он идёт дальше, до проверки токена, — поэтому GOAMNEZIA_PAT из окружения убран: с ним
        # загрузчик от root скачал бы продукт и поставил его на машину, где идут проверки. Каталог
        # установки — временный, а curl — подставной и падает: адрес ветки загрузчик спрашивает у
        # GitHub ещё до рамки, а проверкам сеть не нужна (tests/README.md).
        tmp = pathlib.Path(tempfile.mkdtemp(prefix='ga-frames-'))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        (tmp / 'bin').mkdir()
        (tmp / 'bin' / 'curl').write_text('#!/bin/sh\nexit 7\n', encoding='utf-8')
        (tmp / 'bin' / 'curl').chmod(0o755)
        if not os.access(str(tmp / 'bin' / 'curl'), os.X_OK):
            # /tmp с noexec: подставной curl не запустится, загрузчик пошёл бы в сеть настоящим.
            self.skipTest('каталог временных файлов смонтирован с noexec — подставной curl не запустить')
        e = dict(os.environ)
        e['GOAMNEZIA_LANG'] = lang
        e['GOAMNEZIA_DIR'] = str(tmp / 'goamnezia')
        e['PATH'] = os.pathsep.join([str(tmp / 'bin'), e.get('PATH', '')])
        e.pop('GOAMNEZIA_DRY_RUN', None)
        e.pop('GOAMNEZIA_PAT', None)
        p = subprocess.run([BASH, str(ROOT / 'bootstrap.sh')], cwd=str(ROOT), env=e,
                           capture_output=True, text=True)
        # До трёх повторов ровно на обвал bash (код 139 при пустом выводе) —
        # та же причина, что и в tests/test_bootstrap_archive.py: bash из
        # homebrew слинкован с gettext, его setlocale ходит в CoreFoundation, а
        # тот при частых коротких процессах под нагрузкой разваливается. Каждое
        # присваивание LC_ALL/LANG — ещё один заход, а рамка приветствия считает
        # ширину построчно. На сервере этого нет: там glibc. Свой отказ
        # загрузчик всегда сопровождает выводом, поэтому условие однозначное.
        for _ in range(3):
            if not (p.returncode == 139 and not (p.stdout + p.stderr).strip()):
                break
            p = subprocess.run([BASH, str(ROOT / 'bootstrap.sh')], cwd=str(ROOT), env=e,
                               capture_output=True, text=True)
        return p.stdout + p.stderr

    def test_bootstrap_frame_is_square_and_closed(self):
        for lang in ('ru', 'en'):
            with self.subTest(lang=lang):
                lines = frame_lines(self.banner(lang), ('╔', '╚'))
                self.assertEqual(len(lines), 5, lines)
                widths = {display_width(l) for l in lines}
                self.assertEqual(len(widths), 1,
                                 'ширины строк рамки разошлись: %r' % (
                                     [(display_width(l), l) for l in lines],))
                self.assertTrue(lines[0].strip().endswith('╗'))
                self.assertTrue(lines[-1].strip().endswith('╝'))
                for l in lines[1:-1]:
                    self.assertTrue(l.strip().startswith('║') and l.strip().endswith('║'), l)


class StrWidth(unittest.TestCase):
    """ga_str_width — примитив, на котором стоит выравнивание рамок."""

    def width(self, s):
        out = run_bash('source lib/common.sh; ga_str_width %s' % _q(s))
        return int(out.strip())

    def test_counts_columns_not_bytes(self):
        cases = [
            ('abc', 3),
            ('привет', 6),          # кириллица: 12 байт, 6 колонок
            ('Lite + сайт', 11),
            ('', 0),
            ('🛡', 2),               # эмодзи — две колонки
            ('á', 1),          # комбинирующее ударение — ноль колонок
        ]
        for s, expect in cases:
            with self.subTest(s=s):
                self.assertEqual(self.width(s), expect)

    def test_ansi_has_no_width(self):
        self.assertEqual(self.width('\033[1;33mжёлтый\033[0m'), 6)


class WinWrapKeepsBreaks(unittest.TestCase):
    """LH-4 живой проверки 16.10: абзац ⚠, уже перенесённый по 76 (_ui_w), ga_win_wrap на широком окне склеивал заново
    по ширине окна — на одном экране ⚠ шёл на 160 колонок, а «Кому что сделать» — по 76. NB2-1: первая правка
    переносила построчно и на окне уже 77 колонок рвала каждую строку на кусок и хвост, теряя отступ продолжения."""

    TEXT = ' '.join(['Профиль AmneziaWG будет удалён и поставлен заново слово%d' % i for i in range(4)])

    def on_pty(self, script):
        import pty
        master, slave = pty.openpty()
        try:
            r = subprocess.run([BASH, '-c', script], cwd=str(ROOT), stdout=subprocess.PIPE, stderr=slave,
                               stdin=subprocess.DEVNULL, timeout=30)
        finally:
            os.close(slave)
            os.close(master)
        return r.stdout.decode('utf-8')

    def wrap(self, text, cols):
        return self.on_pty('source lib/common.sh; COLUMNS=%d; ga_win_wrap "$(ga_wrap %s 72 4)" 4' % (cols, _q(text)))

    def test_wide_window_keeps_the_76_column_breaks(self):
        for cols in (80, 160):
            with self.subTest(cols=cols):
                out = self.wrap(self.TEXT, cols)
                self.assertEqual(out, run_bash('source lib/common.sh; ga_wrap %s 72 4' % _q(self.TEXT)))
                self.assertGreater(len(out.split('\n')), 2, out)

    def test_narrow_window_rewraps_evenly_with_indent(self):
        for cols in (40, 60, 76):
            with self.subTest(cols=cols):
                w = cols - 4 - 1
                out = self.wrap(self.TEXT, cols)
                lines = out.split('\n')
                self.assertEqual(' '.join(out.split()), self.TEXT)
                self.assertTrue(all(display_width(x) <= w for x in lines), out)
                # Отступ: первая строка с колонки 0, каждая следующая — ровно с 4 пробелов.
                self.assertFalse(lines[0].startswith(' '), out)
                for x in lines[1:]:
                    self.assertTrue(x.startswith('    ') and not x.startswith('     '), out)
                # Без «гребёнки»: кроме последней, строки заняты хотя бы наполовину.
                for x in lines[:-1]:
                    self.assertGreaterEqual(display_width(x), w // 2, out)

    def test_multiline_text_that_fits_is_left_alone(self):
        text = 'первая строка\n    вторая строка\nтретья'
        self.assertEqual(self.on_pty('source lib/common.sh; COLUMNS=30; ga_win_wrap %s 4' % _q(text)), text)


def _q(s):
    return "$'" + s.encode('unicode_escape').decode('ascii').replace("'", "\\'") + "'"


if __name__ == '__main__':
    unittest.main()
