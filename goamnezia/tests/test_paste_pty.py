# -*- coding: utf-8 -*-
"""Вставка длинного текста в SSH-меню — общий помощник ga_read_paste (lib/common.sh, находки TK-3, TAIL-1).

Терминал в каноническом режиме режет строку на 4095 байтах в Linux и на 1024 в macOS: ссылка vpn:// линии 2.0
или 3.1 с I1–I5 до привязки ключа не доходила. Помощник читает строку сам (`read -n`), а на время вставки держит
терминал неканоническим и без эха. Здесь он проверяется в настоящем псевдотерминале, как его видит человек в SSH:
длинная строка целиком и без эха, многострочный .conf следом, пустые строки начала вставки (TK-2), Enter — назад,
Ctrl-D и конец ввода — остановка, правка Backspace и Ctrl-U, Ctrl-C — выход, как прежде, с возвращённым
терминалом, чужие ловушки на месте. Сквозная проверка через меню — test_wizard_pty.UserCardImportedKey.

Запуск: python3 -m unittest tests.test_paste_pty
"""
import fcntl
import os
import pathlib
import pty
import re
import select
import shutil
import signal
import subprocess
import termios
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASH = os.environ.get('BASH5') or shutil.which('bash') or 'bash'
SOURCE = 'source lib/common.sh; '
# Итог вызова одной строкой: код, отметка конца ввода, длина, строк, начало текста; затем режим терминала.
SHOW = ('rc=$?; printf "\\nRC=%s EOF=%s LEN=%s LINES=%s\\n" "$rc" "$GA_INPUT_EOF" "${#v}" '
        '"$(printf "%s\\n" "$v" | wc -l | tr -d " ")"; printf "V=%q\\n" "${v:0:40}"; '
        'stty -a | tr " " "\\n" | grep -E "^-?(icanon|echo)$" | tr "\\n" " "; echo; trap -p INT')
LONG = 'vpn://' + 'Ab1_-' * 1220          # 6106 знаков: длиннее строки канонического терминала и в Linux


def _session_with_tty():
    """Своя сессия, а pty — её управляющий терминал, как у SSH-входа (Ctrl-C идёт группе процессов).

    Сигналы — по умолчанию, как у входа по SSH (TEST-FLAKE-09): прогон, запущенный фоновым заданием
    неинтерактивной оболочки (`… &` — параллельные наборы), получает SIGINT и SIGQUIT игнорируемыми, игнор
    переживает exec, а bash не ставит ловушку на сигнал, игнорируемый при входе, — Ctrl-C пропадал бы."""
    for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, signal.SIG_DFL)
    os.setsid()
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass


def drive(script, feed, seconds=15):
    """bash -c script в pty; feed — байты (пишутся по очереди) и паузы (числа, секунды). Вывод — текст."""
    master, slave = pty.openpty()
    proc = subprocess.Popen([BASH, '-c', script], stdin=slave, stdout=slave, stderr=slave, cwd=str(ROOT),
                            close_fds=True, preexec_fn=_session_with_tty)
    os.close(slave)
    out = b''
    queue = list(feed)
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                continue
            if queue:
                item = queue.pop(0)
                if isinstance(item, (int, float)):
                    time.sleep(item)
                else:
                    os.write(master, item)
                continue
            if proc.poll() is not None:
                break
    finally:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
        proc.wait()
        os.close(master)
    return out.decode('utf-8', 'replace').replace('\r', '')


def result(out):
    m = re.search(r'RC=(\d+) EOF=(\d+) LEN=(\d+) LINES=(\d+)\nV=(.*)\n(.*)\n', out)
    if not m:
        raise AssertionError('нет итога вызова:\n' + out[-800:])
    return {'rc': int(m.group(1)), 'eof': int(m.group(2)), 'len': int(m.group(3)), 'lines': int(m.group(4)),
            'v': m.group(5), 'tty': m.group(6).split()}


class PasteInTerminal(unittest.TestCase):

    def paste(self, feed, args='-s'):
        out = drive(SOURCE + 'ga_read_paste v %s; ' % args + SHOW, [0.3] + feed)
        return result(out), out

    def assertTerminalBack(self, r):
        # Терминал после вызова — прежний: канонический и с эхом.
        self.assertEqual(r['tty'], ['icanon', 'echo'])

    def test_a_long_link_arrives_whole_and_without_echo(self):
        r, out = self.paste([LONG.encode() + b'\n'])
        self.assertEqual((r['rc'], r['eof'], r['len'], r['lines']), (0, 0, len(LONG), 1))
        self.assertNotIn(LONG[6:60], out.split('V=')[0])            # ссылки на экране нет
        self.assertTerminalBack(r)

    def test_conf_lines_that_follow_are_read_until_the_pause(self):
        conf = ('[Interface]\nPrivateKey = x\n\n[Peer]\nPublicKey = y\nI1 = <b 0x%s>\nAllowedIPs = 0.0.0.0/0\n'
                % ('ab' * 2500))
        r, _ = self.paste([conf.encode()])
        # пустая строка между секциями — часть вставки, а длинная строка I1 — целиком
        self.assertEqual((r['rc'], r['len'], r['lines']), (0, len(conf) - 1, 7))
        self.assertTerminalBack(r)

    def test_blank_lines_before_a_paste_are_skipped(self):
        # Текст из чата с переводом строки впереди (TK-2): пустая первая строка — не «назад», если следом что-то
        # пришло; и ссылка за ней — тоже целиком.
        r, _ = self.paste([b'\n\n' + LONG.encode() + b'\n'])
        self.assertEqual((r['rc'], r['len'], r['lines']), (0, len(LONG), 1))

    def test_enter_alone_is_back(self):
        r, _ = self.paste([b'\n'])
        self.assertEqual((r['rc'], r['eof'], r['len']), (0, 0, 0))
        self.assertTerminalBack(r)

    def test_ctrl_d_at_the_start_ends_the_input_at_once(self):
        # Как в каноническом режиме: Ctrl-D на пустой строке — конец ввода, без Enter; отметка — для мастера (INST-02).
        r, _ = self.paste([b'\x04'])
        self.assertEqual((r['rc'], r['eof'], r['len']), (1, 1, 0))
        self.assertTerminalBack(r)

    def test_backspace_and_ctrl_u_edit_a_typed_path(self):
        r, _ = self.paste([b'/root/ab\x7fc.txt\n'])
        self.assertEqual(r['v'], '/root/ac.txt')
        r, _ = self.paste([b'junk\x15/root/x\x08y\n'])
        self.assertEqual(r['v'], '/root/y')

    def test_a_path_typed_slowly_waits_for_enter(self):
        r, _ = self.paste([b'/ro', 0.5, b'ot/a', 0.5, b'.txt', 0.3, b'\n'])
        self.assertEqual((r['rc'], r['v']), (0, '/root/a.txt'))

    def test_a_last_line_without_a_newline_is_kept(self):
        r, _ = self.paste([b'abc\ndef'])
        self.assertEqual((r['rc'], r['lines']), (0, 2))
        self.assertEqual(r['v'], "$'abc\\ndef'")

    def test_line_limit(self):
        r, _ = self.paste([b'one\n'], args='-s -l 1')
        self.assertEqual((r['rc'], r['v']), (0, 'one'))

    def test_ctrl_c_leaves_like_before_and_restores_the_terminal(self):
        # Ctrl-C посреди вставки прерывает, как прежде (SIGINT, код 130), а терминал — снова канонический и с эхом:
        # оболочка снаружи переживает сигнал своей ловушкой и показывает режим терминала.
        inner = SOURCE + 'ga_read_paste v -s; echo after'
        out = drive("trap 'echo TRAPPED' INT; %s -c '%s'; echo inner=$?; "
                    "stty -a | tr ' ' '\\n' | grep -E '^-?(icanon|echo)$' | tr '\\n' ' '; echo" % (BASH, inner),
                    [0.5, b'half a key', 0.3, b'\x03'])
        self.assertIn('inner=130', out)
        self.assertNotIn('after', out)
        self.assertIn('icanon echo', out)

    def test_the_callers_traps_are_kept(self):
        out = drive(SOURCE + "trap 'echo mine' INT; ga_read_paste v -s; " + SHOW, [0.3, b'abc\n'])
        self.assertIn("trap -- 'echo mine' SIGINT", out)
        self.assertEqual(result(out)['v'], 'abc')


class PasteWithoutTerminal(unittest.TestCase):

    def run_bash(self, data):
        script = SOURCE + 'ga_read_paste v -s; ' + SHOW.replace('stty -a', 'true')
        return result(subprocess.run([BASH, '-c', script], input=data, capture_output=True, cwd=str(ROOT),
                                     timeout=30).stdout.decode('utf-8', 'replace'))

    def test_end_of_input_is_a_stop_not_an_answer(self):
        r = self.run_bash(b'')
        self.assertEqual((r['rc'], r['eof'], r['len']), (1, 1, 0))

    def test_a_pipe_gives_the_long_line_and_what_follows(self):
        r = self.run_bash(LONG.encode() + b'\r\nline2\n')
        self.assertEqual((r['rc'], r['len'], r['lines']), (0, len(LONG) + len('\nline2'), 2))


if __name__ == '__main__':
    unittest.main()
