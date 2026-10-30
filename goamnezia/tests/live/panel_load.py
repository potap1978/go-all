#!/usr/bin/env python3
"""Нагрузка «открытые вкладки панели» и замер CPU — сравнительный прогон CPU-аудита 03.10.2026.

Запускается НА сервере (python3 стандартной библиотеки), ходит на 127.0.0.1:<порт> панели по петле — как браузер
через SSH-туннель (доверие туннелю, без входа). Каждая вкладка повторяет запросы app.js своей версии:
  old — до CPU-аудита: setInterval 10 с без проверки «предыдущий ещё идёт»; Обзор — overview ‖ traffic, раз в 5 мин
        logs/status ‖ backup-last; «Пользователи» — users, затем share; «Трафик» — traffic; опрос сессии раз в 10 с;
  new — следующий автоопрос через 10 с ПОСЛЕ ответа на предыдущий, тот же набор запросов;
  none — вкладок нет: фон машины (таймеры, VPN) в том же окне.
На старте вкладка просит то же, что app.js: каталог действий, сессию, вид, раздел и «Настройки».

Каждую секунду снимаются: CPU cgroup службы панели вместе со всеми детьми — мостом, jq, docker (usage_usec из
/sys/fs/cgroup/system.slice/<юнит>/cpu.stat), занятость машины (/proc/stat) и число порождённых процессов
(/proc/stat processes). Итог — JSON в stdout: средняя и пики (секунда, p95 секунд, окно 5 с) панели и машины,
процессов в секунду, CPU-с панели за окно, задержки по маршрутам и — если панель отдаёт заголовок
X-GoAmnezia-Snapshot — отдельно «тёплые» (все чтения из готового снимка) и «холодные» ответы.

Два кода рядом на одних данных, не трогая установленный: вторая панель своей службой (своя cgroup), со своим
деревом кода и каталогом панели, на соседнем порту, например:
  systemd-run --unit=ga-perf-new --collect -E GOAMNEZIA_DIR=/opt/goamnezia -E GOAMNEZIA_ADMIN_DIR=/root/side-new \\
      -E GOAMNEZIA_ADMIN_STATIC=$T/admin-web/static -E GOAMNEZIA_ACTION_BIN=$T/install.sh \\
      -E GOAMNEZIA_ACTIONS_JSON=$T/lib/actions.json -E GOAMNEZIA_ADMIN_PORT=1995 -E GOAMNEZIA_TRUST_TUNNEL=1 \\
      -E GOAMNEZIA_BOT_DIR=/root/side-new/bot -E GOAMNEZIA_AUDIT_LOG=/root/side-new/audit.log \\
      /usr/bin/python3 $T/admin-web/server.py
  python3 panel_load.py --client new --tabs users,overview --seconds 300 --port 1995 --unit ga-perf-new.service
Читающие действия моста миграций не запускают; пишущие из чужого дерева запускать нельзя — отпечаток кода другой,
и миграция «той же версии» переложила бы юниты и vhost установки кодом этого дерева.

Вкладка card:<имя> (PERF-01) — открытие карточки человека раз в 10 с после ответа, как app.js: карточка, ключ первой
вкладки (format=all), затем страницы ‖ подписка; итог открытия — строка «card-open», окно «Адреса подписки» —
«POST /api/action/sub-show». Каждое открытие — две строки аудита (выдача ключа и показ адресов), как у человека.

usage: panel_load.py [--tabs users,overview,card:<имя>] [--client old|new|none] [--seconds 300] [--warmup 20]
                     [--port 1985] [--unit goamnezia-admin.service]
"""
import argparse
import http.client
import json
import os
import statistics
import threading
import time

CG = "/sys/fs/cgroup/system.slice/goamnezia-admin.service/cpu.stat"


def cg_usage():
    try:
        with open(CG) as fh:
            for line in fh:
                if line.startswith("usage_usec"):
                    return int(line.split()[1])
    except OSError:
        return 0
    return 0


def proc_stat():
    busy = total = forks = 0
    with open("/proc/stat") as fh:
        for line in fh:
            if line.startswith("cpu "):
                v = [int(x) for x in line.split()[1:]]
                # user nice system idle iowait irq softirq steal
                total = sum(v[:8])
                busy = total - v[3] - v[4]
            elif line.startswith("processes "):
                forks = int(line.split()[1])
    return busy, total, forks


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.lat = {}
        self.err = {}
        self.cls = {}

    def add(self, key, dt, ok, kind=None):
        with self.lock:
            self.lat.setdefault(key, []).append(dt)
            if kind:
                self.cls.setdefault(kind, []).append(dt)
            if not ok:
                self.err[key] = self.err.get(key, 0) + 1


class Tab:
    def __init__(self, page, client, port, stats, stop, lang="ru"):
        self.page, self.client, self.port, self.stats, self.stop, self.lang = page, client, port, stats, stop, lang
        self.slow_at = 0.0

    def req(self, method, path, body=None, out=None):
        t0 = time.monotonic()
        ok, kind = False, None
        try:
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=70)
            hdr = {"Accept": "application/json", "X-GoAmnezia-Admin": "1", "X-GoAmnezia-Lang": self.lang}
            if body is not None:
                hdr["Content-Type"] = "application/json"
            c.request(method, path, body=body, headers=hdr)
            r = c.getresponse()
            data = r.read()
            c.close()
            ok = r.status == 200
            how = r.getheader("X-GoAmnezia-Snapshot") or ""
            if how:
                # Тёплый — все части из готового снимка; холодный — хоть одна собрана этим запросом или чужим идущим.
                kind = "warm" if all(x.endswith("=hit") for x in how.split(",")) else "cold"
            if ok:
                try:
                    doc = json.loads(data.decode())
                    ok = doc.get("ok", True) is not False
                    if out is not None:
                        out.append(doc)
                except ValueError:
                    ok = False
        except Exception:
            ok, kind = False, None
        key = path.split("?")[0] if method == "GET" else method + " " + path
        self.stats.add(key, time.monotonic() - t0, ok, kind)
        return ok

    def par(self, *calls):
        ths = [threading.Thread(target=self.req, args=c) for c in calls]
        for th in ths:
            th.start()
        for th in ths:
            th.join()

    def refresh(self):
        if self.page == "users":
            self.req("GET", "/api/users")
            self.req("GET", "/api/share")
        elif self.page == "overview":
            self.par(("GET", "/api/overview"), ("GET", "/api/traffic"))
            if time.monotonic() - self.slow_at > 300:
                self.slow_at = time.monotonic()
                self.par(("GET", "/api/logs/status"), ("POST", "/api/action/backup-last", "{}"))
        elif self.page == "traffic":
            self.req("GET", "/api/traffic")
        elif self.page == "profiles":
            self.req("GET", "/api/profiles")
        elif self.page.startswith("card:"):
            self.card_open(self.page.split(":", 1)[1])

    def card_open(self, name):
        """Открытие карточки (app.js openUserCard): карточка → ключ первой вкладки → страницы ‖ подписка; затем окно
        «Адреса подписки», если подписка есть."""
        t0 = time.monotonic()
        got = []
        if not self.req("GET", "/api/users/%s" % name, out=got) or not got:
            return
        peers = ((got[0].get("data") or {}).get("user") or {}).get("peers") or {}
        pid = next((p for p in ("awg3", "awg2", "awg1") if p in peers), "")
        if pid:
            self.req("GET", "/api/users/%s/key?profile=%s&format=all" % (name, pid))
        subs = []
        ths = [threading.Thread(target=self.req, args=("GET", "/api/pages?name=%s" % name)),
               threading.Thread(target=self.req, args=("POST", "/api/action/sub-list", json.dumps({"name": name}), subs))]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        self.stats.add("card-open", time.monotonic() - t0, True)
        if any(((d.get("data") or {}).get("subs") or []) for d in subs):
            self.req("POST", "/api/action/sub-show", json.dumps({"name": name}))

    def session_loop(self):
        while not self.stop.is_set():
            self.req("GET", "/api/session")
            self.stop.wait(10)

    def refresh_loop(self):
        if self.client == "old":
            # setInterval: тик каждые 10 с, обновление уходит, даже если прежнее ещё идёт
            nxt = time.monotonic() + 10
            while not self.stop.is_set():
                self.stop.wait(max(0, nxt - time.monotonic()))
                if self.stop.is_set():
                    break
                threading.Thread(target=self.refresh, daemon=True).start()
                nxt += 10
        else:
            while not self.stop.is_set():
                self.stop.wait(10)
                if self.stop.is_set():
                    break
                self.refresh()

    def start(self):
        def boot():
            self.par(("GET", "/api/actions"), ("GET", "/api/session"), ("GET", "/api/appearance"))
            self.refresh()
            self.req("GET", "/api/settings")
        ths = [threading.Thread(target=boot, daemon=True),
               threading.Thread(target=self.session_loop, daemon=True),
               threading.Thread(target=self.refresh_loop, daemon=True)]
        for th in ths:
            th.start()
        return ths


def pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabs", default="users,overview")
    ap.add_argument("--client", default="old", choices=["old", "new", "none"])
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--port", type=int, default=1985)
    ap.add_argument("--unit", default="goamnezia-admin.service")
    a = ap.parse_args()
    global CG
    CG = "/sys/fs/cgroup/system.slice/%s/cpu.stat" % a.unit
    stats = Stats()
    stop = threading.Event()
    tabs = [] if a.client == "none" else [Tab(p, a.client, a.port, stats, stop) for p in a.tabs.split(",") if p]
    for t in tabs:
        t.start()
    time.sleep(a.warmup)
    samples = []
    c0, (b0, t0, f0) = cg_usage(), proc_stat()
    start = time.monotonic()
    prev = (c0, b0, t0, f0, start)
    own0 = os.times()
    while time.monotonic() - start < a.seconds:
        time.sleep(1)
        c, (b, t, f) = cg_usage(), proc_stat()
        now = time.monotonic()
        dt = now - prev[4]
        samples.append({"panel": (c - prev[0]) / 1e6 / dt * 100, "busy": (b - prev[1]) / max(1, t - prev[2]) * 100,
                        "forks": (f - prev[3]) / dt})
        prev = (c, b, t, f, now)
    own1 = os.times()
    c1, (b1, t1, f1) = cg_usage(), proc_stat()
    span = time.monotonic() - start
    stop.set()
    panel = [s["panel"] for s in samples]
    busy = [s["busy"] for s in samples]
    forks = [s["forks"] for s in samples]

    def max5(v):
        return max((sum(v[i:i + 5]) / 5 for i in range(0, max(1, len(v) - 4))), default=0.0)
    out = {
        "client": a.client, "tabs": a.tabs, "seconds": round(span, 1),
        "panel_cpu_s": round((c1 - c0) / 1e6, 2),
        "panel_avg_pct": round((c1 - c0) / 1e6 / span * 100, 2),
        "panel_max_1s_pct": round(max(panel, default=0), 1), "panel_p95_1s_pct": round(pct(panel, 0.95), 1),
        "panel_max_5s_pct": round(max5(panel), 1),
        "machine_avg_pct": round((b1 - b0) / max(1, t1 - t0) * 100, 2),
        "machine_max_1s_pct": round(max(busy, default=0), 1), "machine_p95_1s_pct": round(pct(busy, 0.95), 1),
        "forks_per_s_avg": round((f1 - f0) / span, 2), "forks_per_s_max": round(max(forks, default=0), 1),
        "emulator_cpu_s": round((own1.user - own0.user) + (own1.system - own0.system), 2),
        "requests": {k: {"n": len(v), "p50": round(statistics.median(v), 3), "p95": round(pct(v, 0.95), 3),
                         "max": round(max(v), 3), "err": stats.err.get(k, 0)} for k, v in sorted(stats.lat.items())},
        "by_kind": {k: {"n": len(v), "p50": round(statistics.median(v), 3), "p95": round(pct(v, 0.95), 3),
                        "max": round(max(v), 3)} for k, v in sorted(stats.cls.items())},
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
