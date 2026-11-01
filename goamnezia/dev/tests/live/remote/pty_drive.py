#!/usr/bin/env python3
"""pty_drive.py — провести мастер или меню goAmnezia по сценарию в псевдотерминале (на сервере, под root).

    python3 pty_drive.py --script steps.json --log /root/ga-live/lazy/run.log [--done run.json] -- bash install.sh

Зовёт tests/live/acceptance.py (проверка lazy-wizard); руками — так же. steps.json:

    {"env": {"GOAMNEZIA_LANG": "ru"},
     "steps": [{"name": "mode", "expect": "Выбор \\(Enter — 4, Lite\\):\\s*$", "send": "1"}, ...],
     "auto": [{"expect": "Нажмите Enter[.…]*\\s*$", "send": ""}],
     "after": "zero", "stall": 300, "timeout": 3600, "cols": 120, "rows": 50}

Шаги — строго по порядку: шаг ждёт, пока вывод затихнет на секунду (у шага — "quiet": меньше, если программа
перерисовывает строку состояния каждую секунду) и его expect (регулярное выражение, re.S) совпадёт с текстом после
прошлого ответа, и отвечает send. auto — ответы в любой момент, когда очередной шаг не совпал
(«Нажмите Enter»). Шаги кончились: after=zero — на меню («▸», «Выбор:») и вопросы «[1 — да, 0 — нет]» отвечать «0»,
пока программа не выйдет; after=wait — только ждать выхода. Вывод затих на stall секунд, и ничто не совпало —
программа ждёт ввода, которого сценарий не знает: вся группа процессов снимается (SIGKILL), в сводке — на каком шаге.

Журнал без ANSI — в --log (0600): в нём секреты (пароль панели, адреса страниц и панели), поэтому в stdout — только
сводка JSON (шаги, код выхода, время); её же — в --done (0600, пишется последней: её появление значит «закончил»).
"""
import argparse
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]|\x1b[=>]|\x1b\][^\x07]*\x07|\r")
MENU = r"(▸|(Выбор|Choice|Select)[^\n:]*:)\s*$"
# Подсказка вопроса бывает перенесена на другую строку посередине («[1 — да, 0» / «— нет]:») — пробелы любые.
YESNO = r"\[1\s+—\s+(да|yes),\s+0\s+—\s+(нет|no)\]:\s*$"
ENTER = r"(Нажмите Enter|Press Enter)[^\n]*[.…]*\s*$"


def write_0600(path, data):
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(path + ".tmp", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--done")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        ap.error("no command")
    with open(a.script, encoding="utf-8") as f:
        sc = json.load(f)
    steps, auto = list(sc.get("steps") or []), list(sc.get("auto") or [])
    after, stall, timeout = sc.get("after", "zero"), float(sc.get("stall", 300)), float(sc.get("timeout", 3600))
    # Размер окна: по умолчанию 120×50; «cols»/«rows» — окно поменьше (QR первого ключа в 100×40 не влезает, NC-05).
    cols, rows = int(sc.get("cols", 120)), int(sc.get("rows", 50))
    pid, fd = pty.fork()
    if pid == 0:
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root",
               "TERM": "xterm-256color", "LANG": "C.UTF-8", "COLUMNS": str(cols), "LINES": str(rows),
               "GOAMNEZIA_WHO": "live-pty"}
        env.update(sc.get("env") or {})
        try:
            os.execvpe(cmd[0], cmd, env)
        finally:
            os._exit(127)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass
    raw, t0, last, pos, done, sent_auto, stalled = b"", time.time(), time.time(), 0, [], 0, None
    i, flushed = 0, time.time()
    while time.time() - t0 < timeout:
        if time.time() - flushed > 5:
            # Журнал по ходу (раз в 5 с): долгую установку видно, не дожидаясь конца.
            write_0600(a.log, ANSI.sub("", raw.decode("utf-8", "replace")))
            flushed = time.time()
        r, _, _ = select.select([fd], [], [], 0.3)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if not data:
                break
            raw += data
            last = time.time()
            continue
        # Тишина перед сверкой — секунда; у шага можно меньше ("quiet"): строка состояния окна привязки
        # (install_bot.sh --pair) перерисовывается раз в секунду, и секунды тишины там не бывает вовсе.
        if time.time() - last < (float(steps[i].get("quiet", 1.0)) if i < len(steps) else 1.0):
            continue
        text = ANSI.sub("", raw.decode("utf-8", "replace"))
        tail = text[pos:].rstrip(" \t")
        key = None
        if i < len(steps) and re.search(steps[i]["expect"], tail, re.S):
            key = steps[i].get("send", "")
            done.append({"step": steps[i].get("name", str(i)), "at": round(time.time() - t0, 1)})
            i += 1
        else:
            for rule in auto:
                if re.search(rule["expect"], tail, re.S):
                    key = rule.get("send", "")
                    sent_auto += 1
                    break
            if key is None and i >= len(steps) and after == "zero":
                if re.search(ENTER, tail):
                    key = ""
                elif re.search(MENU, tail) or re.search(YESNO, tail):
                    key = "0"
        if key is None:
            if time.time() - last > stall:
                stalled = steps[i].get("name", str(i)) if i < len(steps) else "after-steps"
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
                break
            continue
        os.write(fd, (key + "\n").encode())
        pos = len(text)
        last = time.time()
    text = ANSI.sub("", raw.decode("utf-8", "replace"))
    rc = None
    for _ in range(50):
        wp, status = os.waitpid(pid, os.WNOHANG)
        if wp:
            rc = os.waitstatus_to_exitcode(status)
            break
        time.sleep(0.2)
    if rc is None:
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        rc = "killed (timeout)" if stalled is None else "killed (stalled)"
    write_0600(a.log, text)
    summary = {"rc": rc, "steps_done": done, "steps_total": len(steps), "next_step": steps[i].get("name") if i < len(steps) else None,
               "stalled_at": stalled, "auto_answers": sent_auto, "elapsed": round(time.time() - t0, 1), "log": a.log}
    out = json.dumps(summary, ensure_ascii=False)
    if a.done:
        write_0600(a.done, out + "\n")
    print(out)
    sys.exit(0)


if __name__ == "__main__":
    main()
