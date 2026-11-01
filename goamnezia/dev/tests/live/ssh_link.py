#!/usr/bin/env python3
"""Общий канал SSH живых проверок (находка TOOL-01): одно соединение на сервер на весь прогон и повтор при обрыве.

Тестовые серверы под фоновым перебором паролей рвут свежие рукопожатия (MaxStartups): «Connection closed by …
port 22», kex_exchange_identification, ssh выходит с кодом 255 — и из-за одного такого обрыва падал весь прогон
приёмки. Поэтому:

  • канал: ControlMaster=auto, ControlPath — в своём временном каталоге 0700, ControlPersist — на время прогона
    (и не дольше PERSIST секунд простоя, если прогон убит и закрыть канал некому). Рукопожатие — одно на сервер,
    дальше команды идут по готовому соединению. В конце (и при исключении) — `ssh -O exit`, каталог удаляется;
  • повтор при коде 255 с растущей паузой: TRIES попыток, пауза PAUSE·2ⁿ, не больше MAX_PAUSE:
      – команда не начиналась (ssh не дошёл до неё: обрыв на рукопожатии, отказ соединения) — повтор всегда, на
        сервере ничего не случилось. Начало видно по метке: удалённая оболочка первой печатает в stderr строку
        MARK; метки нет — команда не стартовала;
      – команда начиналась, и связь оборвалась посреди — повтор только у безопасных (safe=True: чтение,
        проверки, заливка файлов). Шаг, который меняет сервер (обновление, перезапуск, изменения через мост),
        вслепую не повторяется: его итог возвращается как есть, решает вызывающий;
      – отказ, который повтором не лечится (ключ не принят, ключ хоста не тот, нет такого хоста), — без повтора;
  • в журнал (stderr) — только хост, попытка, пауза и строка самого ssh об обрыве. Ни команды, ни ввода, ни
    вывода: в них бывают ключи и адреса страниц.

Bash-скриптам (bot_live.sh) — тот же канал командной строкой:

    d=$(python3 ssh_link.py open)                                     # каталог канала на прогон
    python3 ssh_link.py run --dir "$d" [--key K] [-o OPT] [--safe] [--stdin] root@host -- 'команда'
    python3 ssh_link.py close --dir "$d" [--key K] [-o OPT] root@host…  # закрыть соединения, убрать каталог

acceptance.py с GA_LIVE_SSH_DIR=<каталог> ездит по тому же каналу и не закрывает его — закроет открывший.
Паузу и число попыток задают GA_LIVE_SSH_PAUSE и GA_LIVE_SSH_TRIES (проверки с подставным ssh — доли секунды).
"""
import argparse
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

MARK = "GA-LIVE-SSH-STARTED"
TRIES = 5
PAUSE = 3.0
MAX_PAUSE = 30.0
PERSIST = 600
BASE_OPTS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
             "-o", "ServerAliveCountMax=4")
# Строки самого ssh об обрыве соединения (до команды или посреди неё) и об отказе, который повтором не лечится.
DROP = re.compile(r"kex_exchange_identification[^\n]*|banner exchange[^\n]*|Connection (?:closed|reset) by [^\n]*"
                  r"|ssh: connect to host [^\n]*|Connection to \S+ closed by remote host[^\n]*|client_loop:[^\n]*"
                  r"|packet_write_wait:[^\n]*|Timeout, server \S+ not responding[^\n]*|mux_client_[^\n]*"
                  r"|ssh_dispatch_run_fatal:[^\n]*|Broken pipe[^\n]*")
FATAL = re.compile(r"Permission denied|Host key verification failed|REMOTE HOST IDENTIFICATION HAS CHANGED"
                   r"|Could not resolve hostname|Bad configuration option|Too many authentication failures")


def _env_number(name, default, kind=float):
    try:
        v = kind(os.environ.get(name) or default)
    except ValueError:
        return default
    return v if v > 0 else default


def _short_tmp():
    """Родитель каталога канала. Путь сокета ограничен (104 байта в macOS, 108 в Linux), а ssh ещё дописывает к
    нему 17 знаков, пока создаёт сокет: длинный $TMPDIR macOS (/var/folders/…/T/) не годится — тогда /tmp."""
    for d in (os.environ.get("TMPDIR"), "/tmp"):
        if d and len(d.rstrip("/")) <= 40 and os.path.isdir(d) and os.access(d, os.W_OK):
            return d
    return None


def verdict(rc, err, started):
    """Что делать с итогом ssh: done — это ответ команды; retry — до команды не дошло, повтор безопасен всегда;
    lost — связь оборвалась посреди начатой команды (повтор — только безопасной); fatal — повтор не поможет."""
    if rc != 255:
        return "done"
    if FATAL.search(err):
        return "fatal"
    if not started:
        return "retry"
    return "lost" if DROP.search(err) else "done"


def _why(err):
    """Строка самого ssh об обрыве — для журнала; длинное, похожее на ключ, — под маской (на всякий случай)."""
    m = DROP.findall(err)
    why = m[-1].strip() if m else "ssh exit 255"
    return re.sub(r"[A-Za-z0-9+/_-]{24,}={0,2}", "<redacted>", why)[:160]


def _unmark(err):
    """(stderr без метки, была ли метка): метка — отдельной строкой, перед ней мог что-то сказать .bashrc."""
    if MARK + "\n" in err:
        return err.replace(MARK + "\n", "", 1), True
    return err, False


class Channel:
    """Каталог 0700 под сокеты ControlMaster и закрытие соединений в конце. dir — чужой канал (GA_LIVE_SSH_DIR):
    ездим по нему, а закрывает его открывший."""

    def __init__(self, dir=None, persist=PERSIST, log=None):
        self.owned = not dir
        if self.owned:
            self.dir = tempfile.mkdtemp(prefix="ga-ssh-", dir=_short_tmp())
        else:
            self.dir = dir
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self.persist = int(persist)
        self.log = log or sys.stderr
        self.hosts = {}
        self.closed = False

    def opts(self):
        return ["-o", "ControlMaster=auto", "-o", "ControlPath=%s/%%C" % self.dir,
                "-o", "ControlPersist=%d" % self.persist]

    def say(self, text):
        print("ssh: %s" % text, file=self.log, flush=True)

    def close(self):
        """Закрыть мастер-соединения и убрать каталог (только свой канал). Повторный вызов — ничего."""
        if self.closed:
            return
        self.closed = True
        if not self.owned:
            return
        for host in list(self.hosts.values()):
            try:
                subprocess.run(host.argv(control="exit"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=20)
            except (OSError, subprocess.SubprocessError):
                pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


_DEFAULT = None


def default_channel():
    """Канал прогона: один на процесс (свой — или переданный через GA_LIVE_SSH_DIR), закрывается при выходе."""
    global _DEFAULT
    if _DEFAULT is None or _DEFAULT.closed:
        _DEFAULT = Channel(os.environ.get("GA_LIVE_SSH_DIR") or None)
        atexit.register(_DEFAULT.close)
    return _DEFAULT


class Host:
    """Машина прогона: ssh по ключу через общий канал, run с повтором, pipe — трубой на другую машину."""

    def __init__(self, target, key=None, channel=None, opts=()):
        self.target, self.key = target, key
        self.channel = channel or default_channel()
        self.opts = list(opts)
        self.channel.hosts[target] = self

    def argv(self, cmd=None, mark=False, control=None):
        a = ["ssh"] + (["-i", self.key] if self.key else []) + list(BASE_OPTS) + self.opts + self.channel.opts()
        if control:
            a += ["-O", control]
        a.append(self.target)
        if cmd is not None:
            a.append("printf '%%s\\n' %s >&2; %s" % (MARK, cmd) if mark else cmd)
        return a

    def _again(self, v, safe, attempt, tries, why):
        """Пауза перед следующей попыткой → True; повтора не будет → False (и почему — в журнал)."""
        if v == "lost" and not safe:
            self.channel.say("%s: the connection dropped in the middle of a step that changes the server (%s) — "
                             "not repeated blindly" % (self.target, why))
            return False
        if v not in ("retry", "lost"):
            return False
        if attempt >= tries:
            self.channel.say("%s: gave up after %d attempts (%s)" % (self.target, tries, why))
            return False
        pause = min(MAX_PAUSE, _env_number("GA_LIVE_SSH_PAUSE", PAUSE) * 2 ** (attempt - 1))
        self.channel.say("%s: %s (%s) — attempt %d/%d in %s s" % (
            self.target, "no connection before the command" if v == "retry" else "the connection dropped mid-way",
            why, attempt + 1, tries, ("%.2f" % pause).rstrip("0").rstrip(".")))
        time.sleep(pause)
        return True

    def run(self, cmd, input=None, timeout=900, safe=False, tries=None):
        """Команда на машине → (rc, stdout, stderr) строками. safe — повтор и при обрыве посреди команды.
        Тайм-аут (subprocess.TimeoutExpired) — вызывающему, как раньше."""
        tries = tries or _env_number("GA_LIVE_SSH_TRIES", TRIES, int)
        attempt = 0
        while True:
            attempt += 1
            kw = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
            p = subprocess.run(self.argv(cmd, mark=True), capture_output=True, timeout=timeout, **kw)
            err, started = _unmark(p.stderr.decode("utf-8", "replace"))
            v = verdict(p.returncode, err, started)
            if not self._again(v, safe, attempt, tries, _why(err)):
                return p.returncode, p.stdout.decode("utf-8", "replace"), err

    def ensure(self):
        """Соединение есть (или заведено с повтором): дальше — без новых рукопожатий."""
        return self.run("true", safe=True, timeout=120)[0] == 0

    def pipe(self, cmd, dest, dest_cmd, timeout=300, safe=False, tries=None):
        """stdout команды здесь — трубой в stdin команды на dest: ключ идёт сервер → клиент, не оседая ни на диске,
        ни в памяти harness. Повтор — по правилам run для обеих сторон. → (rc, stdout, stderr) стороны dest."""
        tries = tries or _env_number("GA_LIVE_SSH_TRIES", TRIES, int)
        self.ensure()
        dest.ensure()
        attempt = 0
        while True:
            attempt += 1
            with tempfile.TemporaryFile() as serr:
                src = subprocess.Popen(self.argv(cmd, mark=True), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=serr)
                try:
                    p = subprocess.run(dest.argv(dest_cmd, mark=True), stdin=src.stdout, capture_output=True,
                                       timeout=timeout)
                except subprocess.TimeoutExpired:
                    src.kill()
                    raise
                finally:
                    src.stdout.close()
                    try:
                        src.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        src.kill()
                        src.wait()
                serr.seek(0)
                s_err, s_started = _unmark(serr.read().decode("utf-8", "replace"))
            err, started = _unmark(p.stderr.decode("utf-8", "replace"))
            # Решает худшая сторона: отказ без повтора, затем обрыв посреди команды, затем обрыв до неё.
            v, why, h = min([(verdict(src.returncode, s_err, s_started), _why(s_err), self),
                             (verdict(p.returncode, err, started), _why(err), dest)],
                            key=lambda side: ("fatal", "lost", "retry", "done").index(side[0]))
            if not h._again(v, safe, attempt, tries, why):
                return p.returncode, p.stdout.decode("utf-8", "replace"), err

    def stream(self, cmd, input=None, timeout=None, safe=False, tries=None):
        """Для командной строки: вывод команды идёт сразу на свой stdout и stderr (человек видит ход шага), метка
        вырезается. → код возврата."""
        tries = tries or _env_number("GA_LIVE_SSH_TRIES", TRIES, int)
        attempt = 0
        while True:
            attempt += 1
            p = subprocess.Popen(self.argv(cmd, mark=True), stdin=subprocess.PIPE if input is not None else
                                 subprocess.DEVNULL, stdout=None, stderr=subprocess.PIPE)
            seen = {"started": False, "tail": b""}

            def pump():
                for line in iter(p.stderr.readline, b""):
                    if not seen["started"] and line.rstrip(b"\r\n") == MARK.encode():
                        seen["started"] = True
                        continue
                    seen["tail"] = (seen["tail"] + line)[-8192:]
                    sys.stderr.buffer.write(line)
                    sys.stderr.buffer.flush()

            reader = threading.Thread(target=pump, daemon=True)
            reader.start()
            if input is not None:
                try:
                    p.stdin.write(input)
                except BrokenPipeError:
                    pass
                finally:
                    try:
                        p.stdin.close()
                    except BrokenPipeError:
                        pass
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
                reader.join(5)
                self.channel.say("%s: no answer within %s s" % (self.target, timeout))
                return 124
            reader.join(30)
            err = seen["tail"].decode("utf-8", "replace")
            v = verdict(p.returncode, err, seen["started"])
            if not self._again(v, safe, attempt, tries, _why(err)):
                return p.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description="Shared SSH channel of the live checks (TOOL-01)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("open", help="create a channel directory (0700) and print its path")
    for name in ("run", "close"):
        p = sub.add_parser(name)
        p.add_argument("--dir", required=True, help="channel directory from «open»")
        p.add_argument("--key", help="ssh identity file")
        p.add_argument("-o", dest="opts", action="append", default=[], help="extra ssh option (repeatable)")
        if name == "run":
            p.add_argument("--safe", action="store_true", help="a read, a check or an upload: repeat it after a "
                           "connection drop in the middle too")
            p.add_argument("--stdin", action="store_true", help="send this stdin to the command (read in full first, "
                           "so every attempt gets all of it)")
            p.add_argument("--timeout", type=float, help="seconds for one attempt")
            p.add_argument("target")
            p.add_argument("command", nargs=argparse.REMAINDER)
        else:
            p.add_argument("targets", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "open":
        print(Channel().dir)
        return 0
    chan = Channel(a.dir)
    opts = [x for o in a.opts for x in ("-o", o)]
    if a.cmd == "close":
        chan.owned = True
        for t in a.targets:
            Host(t, a.key, chan, opts)
        chan.close()
        return 0
    words = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not words:
        ap.error("run: no command")
    data = sys.stdin.buffer.read() if a.stdin else None
    return Host(a.target, a.key, chan, opts).stream(" ".join(words), input=data, timeout=a.timeout, safe=a.safe)


if __name__ == "__main__":
    sys.exit(main())
