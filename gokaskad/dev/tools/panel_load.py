#!/usr/bin/env python3
"""Load acceptance of the web panel (docs/k3/SPEC-wave3.md §3.6) — run it ON THE NODE as root.

N simulated tabs for M minutes: each holds an SSE stream (GET /api/events) and polls GET /api/overview — the
next request only after the answer, then a pause of --poll s (the fallback path of the page). Measured:

    CPU of kaskad-panel.service and kaskad-gw-collect.service — their cgroup (v2 cpu.stat usage_usec, v1
        cpuacct.usage), else /proc/<MainPID>/stat (utime+stime+cutime+cstime): % of ONE core over the run and
        the worst 10 s window;
    forks per second of the whole node (/proc/stat "processes"): the collector forks every round anyway;
    commands the panel itself ran (GET /api/health "commands"; must stay 0 — reads never run a command);
    SSE events per type, reconnects, overview answers and their latency (p50/p95), errors.

Verdict: panel + collector ≤ --limit % (default 5) of one core and the panel ran no command.

    python3 tools/panel_load.py                       3 tabs, 10 minutes, http://127.0.0.1:1980 (the tunnel)
    python3 tools/panel_load.py --tabs 3 --minutes 10 --poll 10 --limit 5
    echo 'пароль' | python3 tools/panel_load.py --url https://203.0.113.10:41234 --insecure --password-stdin

Stdlib only, Python 3.8+.
"""

import argparse
import http.client
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse

UNITS = ("kaskad-panel.service", "kaskad-gw-collect.service")
WINDOW = 10.0


# ── CPU and forks ──────────────────────────────────────────────────────────

def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _main_pid(unit):
    try:
        out = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", unit], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return int(out) if out.isdigit() and int(out) > 0 else None


class Meter(object):
    """CPU seconds used so far by a unit: its cgroup, else its main process (with waited-for children)."""

    def __init__(self, unit):
        self.unit = unit
        self.how = None
        self.path = None
        self.pid = None
        for p, how in (("/sys/fs/cgroup/system.slice/%s/cpu.stat" % unit, "cgroup v2"),
                       ("/sys/fs/cgroup/cpu,cpuacct/system.slice/%s/cpuacct.usage" % unit, "cgroup v1"),
                       ("/sys/fs/cgroup/cpuacct/system.slice/%s/cpuacct.usage" % unit, "cgroup v1")):
            if os.path.exists(p):
                self.path, self.how = p, how
                break
        if self.path is None:
            self.pid = _main_pid(unit)
            if self.pid:
                self.how = "/proc/%d/stat" % self.pid
        self.tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def seconds(self):
        if self.how == "cgroup v2":
            for line in (_read(self.path) or "").splitlines():
                k, _, v = line.partition(" ")
                if k == "usage_usec" and v.strip().isdigit():
                    return int(v) / 1e6
            return None
        if self.how == "cgroup v1":
            v = (_read(self.path) or "").strip()
            return int(v) / 1e9 if v.isdigit() else None
        if self.pid:
            raw = _read("/proc/%d/stat" % self.pid)
            if not raw:
                return None
            f = raw.rsplit(")", 1)[-1].split()
            try:
                return sum(int(x) for x in f[11:15]) / float(self.tick)
            except (ValueError, IndexError):
                return None
        return None


def node_forks():
    for line in (_read("/proc/stat") or "").splitlines():
        if line.startswith("processes "):
            return int(line.split()[1])
    return None


# ── the client side ────────────────────────────────────────────────────────

class Client(object):
    def __init__(self, url, insecure=False, cookie=None):
        u = urllib.parse.urlsplit(url)
        self.https = u.scheme == "https"
        self.host = u.hostname
        self.port = u.port or (443 if self.https else 80)
        self.hosthdr = u.netloc
        self.cookie = cookie
        self.ctx = None
        if self.https:
            self.ctx = ssl.create_default_context()
            if insecure:
                self.ctx.check_hostname = False
                self.ctx.verify_mode = ssl.CERT_NONE

    def conn(self, timeout=30):
        if self.https:
            return http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self.ctx)
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def headers(self, extra=None):
        h = {"Host": self.hosthdr, "Accept": "application/json"}
        if self.cookie:
            h["Cookie"] = "kaskad_session=" + self.cookie
        h.update(extra or {})
        return h

    def request(self, method, path, body=None):
        c = self.conn()
        try:
            data = json.dumps(body).encode() if body is not None else None
            h = self.headers({"X-Kaskad-Admin": "1", "Content-Type": "application/json"} if data else None)
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            return r.status, r.getheader("Set-Cookie") or "", r.read()
        finally:
            c.close()


class Stats(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.events = {}
        self.reconnects = 0
        self.latency = []
        self.errors = []

    def add_event(self, name):
        with self.lock:
            self.events[name] = self.events.get(name, 0) + 1

    def error(self, text):
        with self.lock:
            if len(self.errors) < 20:
                self.errors.append(text)


def sse_tab(client, stats, stop):
    backoff = 1.0
    while not stop.is_set():
        c = client.conn(timeout=40)
        try:
            c.request("GET", "/api/events", headers=client.headers({"Accept": "text/event-stream"}))
            r = c.getresponse()
            if r.status != 200:
                stats.error("SSE: HTTP %d" % r.status)
                raise OSError("status %d" % r.status)
            backoff = 1.0
            name = None
            while not stop.is_set():
                line = r.fp.readline()
                if not line:
                    raise OSError("closed")
                line = line.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line == "" and name:
                    stats.add_event(name)
                    name = None
                elif line.startswith(":"):
                    stats.add_event("heartbeat")
        except (OSError, http.client.HTTPException) as e:
            if stop.is_set():
                break
            with stats.lock:
                stats.reconnects += 1
            stats.error("SSE: %s" % e)
            stop.wait(backoff)
            backoff = min(60.0, backoff * 2)
        finally:
            c.close()


def poll_tab(client, stats, stop, pause):
    while not stop.is_set():
        t = time.monotonic()
        try:
            status, _, body = client.request("GET", "/api/overview")
            ms = (time.monotonic() - t) * 1000.0
            if status == 200 and json.loads(body.decode("utf-8")).get("ok"):
                with stats.lock:
                    stats.latency.append(ms)
            else:
                stats.error("overview: HTTP %d" % status)
        except (OSError, ValueError, http.client.HTTPException) as e:
            stats.error("overview: %s" % e)
        stop.wait(pause)


def percentile(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1))))]


def health(client):
    try:
        status, _, body = client.request("GET", "/api/health")
        return json.loads(body.decode("utf-8")).get("commands") if status == 200 else None
    except (OSError, ValueError, http.client.HTTPException):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Нагрузка веб-панели Kaskad (SPEC-wave3 §3.6)")
    ap.add_argument("--url", default="http://127.0.0.1:1980")
    ap.add_argument("--tabs", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--poll", type=float, default=10.0, help="пауза опроса overview после ответа, с (0 — без опроса)")
    ap.add_argument("--limit", type=float, default=5.0, help="норма: панель + сборщик, %% одного ядра")
    ap.add_argument("--password-stdin", action="store_true", help="войти паролем из stdin (иначе — как SSH-туннель)")
    ap.add_argument("--insecure", action="store_true", help="HTTPS без проверки сертификата (самоподписанный)")
    args = ap.parse_args(argv)

    client = Client(args.url, insecure=args.insecure)
    if args.password_stdin:
        pw = sys.stdin.readline().rstrip("\r\n")
        status, cookie, body = client.request("POST", "/api/login", {"password": pw})
        if status != 200:
            sys.stderr.write("Вход не удался: HTTP %d %s\n" % (status, body[:200].decode("utf-8", "replace")))
            return 2
        client.cookie = cookie.split(";", 1)[0].split("=", 1)[1]
    status, _, body = client.request("GET", "/api/session")
    if status != 200 or not json.loads(body.decode("utf-8")).get("auth"):
        sys.stderr.write("Панель не пускает (нет входа): запустите на узле через туннель или с --password-stdin.\n")
        return 2

    meters = [Meter(u) for u in UNITS]
    for m in meters:
        if m.how is None:
            sys.stderr.write("Не нашёл %s (ни cgroup, ни процесса) — его CPU не измерен.\n" % m.unit)
    stats = Stats()
    stop = threading.Event()
    threads = []
    for _ in range(args.tabs):
        threads.append(threading.Thread(target=sse_tab, args=(client, stats, stop), daemon=True))
        if args.poll > 0:
            threads.append(threading.Thread(target=poll_tab, args=(client, stats, stop, args.poll), daemon=True))
    cmd0 = health(client)
    f0 = node_forks()
    t0 = time.monotonic()
    cpu0 = [m.seconds() for m in meters]
    for t in threads:
        t.start()
    duration = args.minutes * 60.0
    peaks = [0.0 for _ in meters]
    last = (time.monotonic(), [m.seconds() for m in meters])
    try:
        while time.monotonic() - t0 < duration:
            time.sleep(min(WINDOW, max(0.1, duration - (time.monotonic() - t0))))
            now, cur = time.monotonic(), [m.seconds() for m in meters]
            for i, (a, b) in enumerate(zip(last[1], cur)):
                if a is not None and b is not None and now > last[0]:
                    peaks[i] = max(peaks[i], 100.0 * (b - a) / (now - last[0]))
            last = (now, cur)
    except KeyboardInterrupt:
        sys.stderr.write("Прервано — отчёт по прошедшему времени.\n")
    stop.set()
    wall = time.monotonic() - t0
    cpu1 = [m.seconds() for m in meters]
    f1 = node_forks()
    cmd1 = health(client)

    lines = ["Панель %s: вкладок %d, %.1f мин, опрос overview %s" % (
        args.url, args.tabs, wall / 60.0, ("каждые %g с после ответа" % args.poll) if args.poll > 0 else "выключен")]
    total = 0.0
    measured = True
    for m, a, b, pk in zip(meters, cpu0, cpu1, peaks):
        if a is None or b is None:
            lines.append("CPU %s: не измерен" % m.unit)
            measured = False
            continue
        pct = 100.0 * (b - a) / wall
        total += pct
        lines.append("CPU %s: %.2f %% ядра (худшие %d с: %.2f %%; %s)" % (m.unit, pct, WINDOW, pk, m.how))
    ok_cpu = measured and total <= args.limit
    lines.append("Вместе: %.2f %% ядра — норма ≤ %g %%: %s" % (total, args.limit,
                                                               "OK" if ok_cpu else ("НЕ ОК" if measured else "?")))
    if f0 is not None and f1 is not None:
        lines.append("Форков на узле: %.2f в секунду (сборщик и всё остальное на узле)" % ((f1 - f0) / wall))
    ran = (cmd1 - cmd0) if isinstance(cmd0, int) and isinstance(cmd1, int) else None
    lines.append("Команд самой панели за прогон: %s" % ("неизвестно (панель не считает)" if ran is None else ran))
    lat = stats.latency
    lines.append("SSE: %s; переподключений %d" % (", ".join("%s %d" % kv for kv in sorted(stats.events.items()))
                                                   or "событий нет", stats.reconnects))
    if args.poll > 0:
        lines.append("overview: ответов %d, p50 %s мс, p95 %s мс" % (
            len(lat), "%.1f" % percentile(lat, 50) if lat else "—", "%.1f" % percentile(lat, 95) if lat else "—"))
    if stats.errors:
        lines.append("Ошибки (%d, первые): %s" % (len(stats.errors), "; ".join(stats.errors[:5])))
    verdict = ok_cpu and (ran is None or ran == 0)
    lines.append("ИТОГ: %s" % ("принято" if verdict else "НЕ принято"))
    print("\n".join(lines))
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
