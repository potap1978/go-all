#!/usr/bin/env python3
"""ops_check.py <режим> [аргументы] — эксплуатационные проверки goAmnezia pro на самом сервере (root).

Зовёт tests/live/acceptance.py (--checks reboot,disk-full,logrotate,cert-renew,update-rollback,upgrade,selfupdate);
руками — так же:
    python3 /root/ga-live/ops_check.py health

Режимы; каждый печатает строки JSON {"check", "ok", "skip", "evidence"}:
  pre-reboot              запомнить состояние до перезагрузки (/root/ga-live/ops/reboot-pre.json, 0600)
  wait-boot [сек]         после перезагрузки: дождаться служб и правил изоляции; правило — до первого тика сторожа
  health                  службы, контейнеры профилей, изоляция, панель, страницы, бот, MCP, таймеры
  diskfull <пользователь> / до 99 % (fallocate) → меню, status.disk, шапка, панель, бэкап, запись, 100 % для root → уборка
  logrotate               конфиги, logrotate -d, logrotate -f без потери строк, бюджет журналов и journald
  certrenew               таймер продления, хук, certbot renew --dry-run --run-deploy-hooks, nginx перечитал
  rollback [снимок]       через загрузчик: кандидат с падающей миграцией → автоматический откат (снимок до/после);
                          кандидат с зависшим шагом, процесс убит → следующий вызов доделывает; возврат версии
  upgrade <дерево> [ветка] [версия]  обновление загрузчиком из дерева (GOAMNEZIA_LOCAL_TREE) → меню → «Что нового»
  selfupdate-view         «Обновить goAmnezia» без обновления (негодный ключ): выбор ветки, что спросили у GitHub

Секреты не печатаются: .env бота, токены, ключи и адреса страниц не читаются или сводятся к хешам; журнал бота —
только счётчики. Всё, что проверка создаёт (файлы-заполнители, временный таймер-страховка, кандидат, архивы теста,
метки), она убирает сама, в том числе при сбое. Подробности — tests/live/README.md.
"""
import fcntl
import glob
import hashlib
import json
import os
import pty
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time

GA = os.environ.get("GOAMNEZIA_DIR", "/opt/goamnezia")
OPS = "/root/ga-live/ops"
WHO = "live-ops"
CFG = GA + "/config.json"
USERS = GA + "/users.json"
BOT_ENV = "/opt/goamnezia-bot/.env"
FAILSAFE = "ga-live-fill-failsafe"
GIB = 1073741824
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]|\x1b[=>]|\r")


# ── общие помощники ─────────────────────────────────────────────────────────────────────────────────────
def sh(cmd, timeout=300, input=None, env=None):
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), input=input, capture_output=True, timeout=timeout,
                           env=dict(os.environ, **(env or {})))
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def emit(check, ok, evidence="", skip=False):
    print(json.dumps({"check": check, "ok": bool(ok), "skip": bool(skip), "evidence": evidence},
                     ensure_ascii=False), flush=True)


def bridge(action, *args, who=WHO, timeout=900):
    rc, out, _ = sh(["bash", GA + "/install.sh", "--action=" + action] + list(args) + ["--json"],
                    timeout=timeout, env={"GOAMNEZIA_WHO": who})
    for ln in reversed(out.strip().splitlines()):
        try:
            return json.loads(ln)
        except ValueError:
            continue
    return {"status": "no_json", "rc": rc}


def bridge_blind(action, *args, who=WHO):
    """Действие с секретом в ответе (user-key): ответ не читаем вовсе, только код выхода."""
    p = subprocess.run(["bash", GA + "/install.sh", "--action=" + action] + list(args) + ["--json"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
                       env=dict(os.environ, GOAMNEZIA_WHO=who))
    return p.returncode


def jload(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def cfg():
    return jload(CFG) or {}


def uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def btime():
    with open("/proc/stat") as f:
        for ln in f:
            if ln.startswith("btime "):
                return int(ln.split()[1])
    return 0


def unit_files(pattern):
    _, out, _ = sh(["systemctl", "list-unit-files", pattern, "--no-legend", "--no-pager"])
    res = {}
    for ln in out.splitlines():
        p = ln.split()
        if len(p) >= 2:
            res[p[0]] = p[1]
    return res


def prop(unit, *names):
    _, out, _ = sh(["systemctl", "show", unit, "-p", ",".join(names)])
    d = {}
    for ln in out.splitlines():
        k, _, v = ln.partition("=")
        d[k] = v
    return d


def enabled(unit):
    return unit_files(unit).get(unit) == "enabled"


def services():
    """Службы, которые обязаны работать: включённые goamnezia-*.service, nginx и docker."""
    svc = sorted(u for u, st in unit_files("goamnezia*.service").items() if st == "enabled")
    svc += [u for u in ("nginx.service", "docker.service") if enabled(u)]
    return {u: prop(u, "ActiveState", "SubState", "NRestarts", "MainPID") for u in svc}


def failed_units():
    _, out, _ = sh(["systemctl", "list-units", "--state=failed", "--no-legend", "--plain", "--no-pager"])
    return [ln.split()[0] for ln in out.splitlines()
            if ln.split() and re.match(r"(goamnezia|nginx|docker|systemd-journald|ssh)", ln.split()[0])]


def profiles():
    """Профили с контейнером из config.json (включённые)."""
    return {p: v for p, v in sorted((cfg().get("profiles") or {}).items())
            if isinstance(v, dict) and v.get("container") and v.get("enabled") is not False}


def docker_state(container):
    rc, out, _ = sh(["docker", "inspect", "-f", "{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}|"
                     "{{.HostConfig.RestartPolicy.Name}}", container], timeout=30)
    return out.strip().split("|") if rc == 0 else None


def iso_count(container, iface):
    rc, out, _ = sh(["docker", "exec", container, "iptables", "-S", "FORWARD"], timeout=30)
    if rc != 0:
        return None
    return sum(1 for ln in out.splitlines() if ln.strip() == "-A FORWARD -i %s -o %s -j DROP" % (iface, iface))


def isolation_wanted():
    return cfg().get("client_isolation") is not False


def curl_code(url, *extra, timeout=20):
    _, out, _ = sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", str(timeout)]
                   + list(extra) + [url], timeout=timeout + 5)
    return out.strip() or "000"


def curl_json(url, timeout=60):
    _, out, _ = sh(["curl", "-s", "--max-time", str(timeout), "-w", "\n%{http_code}", url], timeout=timeout + 5)
    body, _, code = out.rpartition("\n")
    try:
        return code, json.loads(body)
    except ValueError:
        return code, None


def site_code(domain):
    return curl_code("https://%s/" % domain, "--resolve", "%s:443:127.0.0.1" % domain, "-A", "Mozilla/5.0 ga-live-ops")


def statvfs():
    s = os.statvfs("/")
    return {"avail": s.f_bavail * s.f_frsize, "free": s.f_bfree * s.f_frsize,
            "total": (s.f_blocks - s.f_bfree + s.f_bavail) * s.f_frsize,
            "pct": -(-(s.f_blocks - s.f_bfree) * 100 // max(1, s.f_blocks - s.f_bfree + s.f_bavail))}


def mb(n):
    return int(n // 1048576)


def users_doc():
    return jload(USERS)


VOLATILE_PEER = ("last_handshake", "last_rx", "last_tx", "rx", "tx", "active")


def users_norm(doc):
    """users.json без счётчиков трафика (их меняют тики статистики и лимитов)."""
    if doc is None:
        return None
    out = []
    for u in doc.get("users") or []:
        u = {k: v for k, v in u.items() if k != "traffic"}
        u["peers"] = {p: {k: v for k, v in (x or {}).items() if k not in VOLATILE_PEER}
                      for p, x in (u.get("peers") or {}).items()}
        out.append(u)
    return {"schema": doc.get("schema"), "users": out}


def user_entry(doc, name):
    for u in (doc or {}).get("users") or []:
        if isinstance(u, dict) and u.get("name") == name:
            return u
    return None


def backups():
    return sorted(os.path.basename(p) for p in glob.glob(GA + "/backups/*"))


def stage_dirs():
    return sorted(glob.glob("/tmp/goamnezia_backup.*") + glob.glob(GA + "/backups/*.tmp*")
                  + glob.glob(GA + "/backups/.goamnezia*"))


def json_tmp_leftovers():
    return sorted(glob.glob(GA + "/config.json.*") + glob.glob(GA + "/users.json.*"))


# ── меню в псевдотерминале ──────────────────────────────────────────────────────────────────────────────
def menu_run(keys, timeout=300, argv=None, env_extra=None, extra_prompts=(), kill_on=None):
    """Запустить меню (`bash install.sh`, как это делает загрузчик) в pty и провести его по keys: по одной клавише
    на каждое приглашение главного меню («▸») и подменю («Выбор:»); «Нажмите Enter» — Enter, вопрос
    «[1 — да, 0 — нет]» — 0. Ключи кончились — «0». argv — другая команда (загрузчик, который сам передаёт
    управление меню), env_extra — её переменные, extra_prompts — ещё приглашения, которые берут ключ из keys
    (выбор ветки в «Обновить goAmnezia»), kill_on — строка вывода, на которой всю группу процессов убить (SIGKILL:
    оборванная посреди шага миграция). → {rc, text, menu_at, sent, segments, killed}."""
    pid, fd = pty.fork()
    if pid == 0:
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root",
               "TERM": "xterm-256color", "LANG": "C.UTF-8", "COLUMNS": "120", "LINES": "50", "GOAMNEZIA_WHO": WHO}
        env.update(env_extra or {})
        try:
            os.execve("/bin/bash", argv or ["bash", GA + "/install.sh"], env)
        finally:
            os._exit(127)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 120, 0, 0))
    except OSError:
        pass
    raw, t0, last, queue, sent, pos, menu_at, segments = b"", time.time(), time.time(), list(keys), [], 0, None, []
    killed = False
    while time.time() - t0 < timeout:
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
        if time.time() - last < 1.0:
            continue
        text = ANSI.sub("", raw.decode("utf-8", "replace"))
        tail = text[pos:].rstrip(" \t")
        if kill_on and not killed and re.search(kill_on, text):
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
            killed = True
            continue
        if re.search(r"▸\s*$", tail):
            if menu_at is None:
                menu_at = round(time.time() - t0, 1)
            key = queue.pop(0) if queue else "0"
        elif re.search(r"(Выбор|Choice|Select):\s*$", tail):
            key = queue.pop(0) if queue else "0"
        elif re.search(r"(Нажмите Enter|Press Enter)[.…]*\s*$", tail):
            key = ""
        elif re.search(r"\[1 — (да|yes), 0 — (нет|no)\]:\s*$", tail):
            key = "0"
        elif any(re.search(x, tail) for x in extra_prompts):
            key = queue.pop(0) if queue else "0"
        else:
            continue
        segments.append(text[pos:])
        os.write(fd, (key + "\n").encode())
        sent.append(key)
        pos = len(text)
        last = time.time()
    text = ANSI.sub("", raw.decode("utf-8", "replace"))
    segments.append(text[pos:])
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
        _, status = os.waitpid(pid, 0)
        rc = "killed (timeout)" if not killed else os.waitstatus_to_exitcode(status)
    try:
        os.close(fd)
    except OSError:
        pass
    return {"rc": rc, "text": text, "menu_at": menu_at, "sent": sent, "segments": segments, "killed": killed,
            "elapsed": round(time.time() - t0, 1)}


# ── здоровье: службы, контейнеры, изоляция, панель, страницы, бот, MCP, таймеры ─────────────────────────
def check_services(pre=None):
    st = services()
    bad = [u for u, p in st.items() if p.get("ActiveState") != "active"]
    failed = failed_units()
    ev = ", ".join("%s %s" % (u.replace(".service", ""), p.get("ActiveState")) for u, p in st.items())
    emit("services active", not bad and not failed, "%s; failed units: %s" % (ev, ", ".join(failed) or "-"))
    if pre and pre.get("services"):
        lost = sorted(set(pre["services"]) - set(st))
        emit("enabled services same as before", not lost, "missing after: %s" % (", ".join(lost) or "-"))
    return st


def check_containers():
    ps = bridge("profiles-status")
    live = {p.get("profile"): p for p in ps.get("profiles") or [] if isinstance(p, dict)}
    parts, ok = [], ps.get("status") == "success"
    for p, v in profiles().items():
        d = docker_state(v["container"]) or ["missing"]
        s = live.get(p) or {}
        good = d[0] == "running" and s.get("state") == "running" and s.get("port_published") is True
        ok = ok and good
        parts.append("%s %s%s %s :%s published=%s restart=%s" % (
            p, v["container"], " (adopted)" if v.get("imported") else "", d[0], v.get("port"),
            s.get("port_published"), d[3] if len(d) > 3 else "?"))
    emit("profile containers up", ok and bool(parts), "; ".join(parts) or "no profiles")


def check_isolation():
    want = 1 if isolation_wanted() else 0
    parts, ok = [], True
    for p, v in profiles().items():
        n = iso_count(v["container"], v.get("iface") or "awg0")
        ok = ok and n == want
        parts.append("%s %s%s ×%s" % (p, v["container"], " (adopted)" if v.get("imported") else "", n))
    emit("isolation rule FORWARD -i if -o if -j DROP", ok and bool(parts),
         "client_isolation=%s, want ×%d: %s" % (bool(want), want, "; ".join(parts)))


def check_panel():
    port = (cfg().get("panel") or {}).get("port") or 1985
    c1, j1 = curl_json("http://127.0.0.1:%s/api/session" % port)
    c2, j2 = curl_json("http://127.0.0.1:%s/api/overview" % port)
    st = ((j1 or {}).get("data") or {}).get("state")
    emit("panel answers (SSH tunnel address)", c1 == "200" and st == "ok" and c2 == "200" and (j2 or {}).get("ok") is True,
         "127.0.0.1:%s /api/session %s state=%s, /api/overview %s ok=%s" % (port, c1, st, c2, (j2 or {}).get("ok")))


def check_pages():
    if not enabled("goamnezia-pages.service"):
        return emit("connection pages answer", True, "goamnezia-pages is not enabled here", skip=True)
    port = (cfg().get("pages") or {}).get("port") or 1987
    fake = "p-" + secrets.token_urlsafe(32)[:43]
    code = curl_code("http://127.0.0.1:%s/%s/" % (port, fake))
    emit("connection pages answer", code == "404",
         "goamnezia-pages %s; unknown page on 127.0.0.1:%s → %s (404 = service alive)" % (
             prop("goamnezia-pages.service", "ActiveState").get("ActiveState"), port, code))


def check_bot(pre=None, wait=90):
    if not enabled("goamnezia-bot.service"):
        return emit("bot alive", True, "goamnezia-bot is not installed here", skip=True)
    p = prop("goamnezia-bot.service", "ActiveState", "MainPID", "NRestarts", "InvocationID")
    pid, conn = p.get("MainPID", "0"), False
    deadline = time.time() + wait
    while time.time() < deadline:
        _, out, _ = sh(["ss", "-tnpH", "state", "established", "( dport = :443 )"])
        if pid != "0" and re.search(r"pid=%s," % re.escape(pid), out):
            conn = True
            break
        time.sleep(3)
    _, jr, _ = sh(["journalctl", "-u", "goamnezia-bot.service", "_SYSTEMD_INVOCATION_ID=" + p.get("InvocationID", ""),
                   "--no-pager", "-o", "cat"])
    started = "bot starting" in jr
    errs = sum(1 for ln in jr.splitlines() if re.search(r"Traceback|ERROR|Conflict|Unauthorized|InvalidToken", ln))
    env_now = sha(BOT_ENV)
    same = None if not (pre and pre.get("bot_env_sha256")) else env_now == pre["bot_env_sha256"]
    admins = 0
    try:
        with open(BOT_ENV, encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("ALLOWED_IDS="):
                    admins = len([x for x in re.split(r"[,\s]+", ln.split("=", 1)[1].strip().strip("\"'")) if x])
    except OSError:
        pass
    ok = p.get("ActiveState") == "active" and started and conn and same is not False and admins > 0
    emit("bot alive", ok, "active=%s restarts=%s; «bot starting» in journal %s; long-poll TCP to :443 %s; errors in "
         "journal since start %d; .env %s; admins bound %d" % (
             p.get("ActiveState"), p.get("NRestarts"), "yes" if started else "no", "yes" if conn else "no", errs,
             {None: "not compared", True: "unchanged", False: "CHANGED"}[same], admins))


def mcp_stdio():
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "ga-live-ops", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "server_status", "arguments": {}}}]
    exe = "/usr/local/bin/goamnezia-mcp"
    if not os.path.exists(exe):
        return False, "no %s" % exe
    rc, out, _ = sh([exe], input="\n".join(json.dumps(m) for m in msgs).encode() + b"\n", timeout=180)
    res = {}
    for ln in out.splitlines():
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        if isinstance(j, dict) and j.get("id") in (1, 2):
            res[j["id"]] = j
    init = (res.get(1) or {}).get("result") or {}
    call = (res.get(2) or {}).get("result") or {}
    sc = call.get("structuredContent") or {}
    ok = bool(init.get("serverInfo")) and call and not call.get("isError") and sc.get("status") == "success"
    return ok, "stdio initialize → %s %s; server_status → %s" % (
        (init.get("serverInfo") or {}).get("name"), (init.get("serverInfo") or {}).get("version"),
        sc.get("status") or ("error" if call.get("isError") else "no answer"))


def check_mcp():
    ok, ev = mcp_stdio()
    if enabled("goamnezia-mcp-http.service"):
        st = prop("goamnezia-mcp-http.service", "ActiveState").get("ActiveState")
        ok = ok and st == "active"
        ev += "; mcp-http %s" % st
    else:
        ev += "; mcp-http off (not enabled)"
    emit("MCP answers", ok, ev)


def check_timers(pre=None):
    names = sorted(u for u, s in unit_files("goamnezia*.timer").items() if s == "enabled")
    names += [u for u in ("certbot.timer", "logrotate.timer") if enabled(u)]
    parts, ok = [], bool(names)
    for u in names:
        p = prop(u, "ActiveState", "NextElapseUSecRealtime", "NextElapseUSecMonotonic")
        nxt = p.get("NextElapseUSecRealtime") or p.get("NextElapseUSecMonotonic") or ""
        good = p.get("ActiveState") == "active" and nxt not in ("", "n/a", "0", "infinity")
        ok = ok and good
        parts.append("%s%s" % (u[len("goamnezia-"):-len(".timer")] if u.startswith("goamnezia-") else u,
                               "" if good else "=" + p.get("ActiveState", "?")))
    lost = sorted(set((pre or {}).get("timers") or []) - set(names))
    emit("timers in place", ok and not lost, "%d active with a next run: %s; missing vs before: %s" % (
        len(names), ", ".join(parts), ", ".join(lost) or "-"))


def m_health(pre=None):
    check_services(pre)
    check_containers()
    check_isolation()
    check_panel()
    check_pages()
    check_bot(pre)
    check_mcp()
    check_timers(pre)


# ── перезагрузка ────────────────────────────────────────────────────────────────────────────────────────
def watch_first_run():
    """Когда (секунд от загрузки) в этой загрузке впервые запустился тик сторожа; None — ещё не запускался."""
    _, out, _ = sh(["journalctl", "-b", "-u", "goamnezia-watch.service", "-o", "short-monotonic", "--no-pager"], timeout=30)
    for ln in out.splitlines():
        m = re.match(r"\[\s*([\d.]+)\]", ln)
        if m and re.search(r"Starting|Started", ln):
            return float(m.group(1))
    return None


def m_pre_reboot():
    os.makedirs(OPS, mode=0o700, exist_ok=True)
    with open("/proc/sys/kernel/random/boot_id") as f:
        boot_id = f.read().strip()
    pre = {"boot_id": boot_id, "bot_env_sha256": sha(BOT_ENV), "services": sorted(services()),
           "timers": sorted([u for u, s in unit_files("goamnezia*.timer").items() if s == "enabled"]
                            + [u for u in ("certbot.timer", "logrotate.timer") if enabled(u)]),
           "containers": {v["container"]: (docker_state(v["container"]) or ["missing"])[0] for v in profiles().values()}}
    old = os.umask(0o077)
    with open(OPS + "/reboot-pre.json", "w") as f:
        json.dump(pre, f)
    os.umask(old)
    emit("state before the reboot saved", True, "%d services, %d timers, containers %s, bot %s" % (
        len(pre["services"]), len(pre["timers"]),
        ", ".join("%s %s" % kv for kv in pre["containers"].items()), "yes" if pre["bot_env_sha256"] else "no"))


def m_wait_boot(limit=420):
    pre = jload(OPS + "/reboot-pre.json") or {}
    with open("/proc/sys/kernel/random/boot_id") as f:
        boot_id = f.read().strip()
    emit("new boot", boot_id != pre.get("boot_id"), "boot_id changed=%s, uptime at first login %ds" % (
        boot_id != pre.get("boot_id"), uptime()))
    bt, prof, first_rule, svc_ok_at = btime(), profiles(), {}, None
    want = 1 if isolation_wanted() else 0
    while uptime() < limit:
        st = services()
        if svc_ok_at is None and st and all(p.get("ActiveState") == "active" for p in st.values()):
            svc_ok_at = uptime()
        for p, v in prof.items():
            if p not in first_rule and iso_count(v["container"], v.get("iface") or "awg0") == want:
                first_rule[p] = uptime()
        if svc_ok_at is not None and len(first_rule) == len(prof):
            break
        time.sleep(2)
    st = services()
    emit("services came up by themselves", svc_ok_at is not None,
         "all %d enabled services active at +%ss after boot (%s)" % (
             len(st), int(svc_ok_at) if svc_ok_at else "never", ", ".join(u.replace(".service", "") for u in st))
         if svc_ok_at else "not active within %ds: %s" % (limit, ", ".join(
             u for u, p in st.items() if p.get("ActiveState") != "active")))
    parts, ok_c = [], True
    for p, v in prof.items():
        d = docker_state(v["container"]) or ["missing", ""]
        started = d[1]
        rc, out, _ = sh(["date", "-d", started, "+%s"]) if started else (1, "", "")
        s_at = int(out.strip()) - bt if rc == 0 and out.strip().isdigit() else None
        ok_c = ok_c and d[0] == "running"
        r_at = first_rule.get(p)
        gap = int(r_at - s_at) if (r_at is not None and s_at is not None) else None
        parts.append("%s%s started +%ss, rule +%ss%s" % (
            v["container"], " (adopted)" if v.get("imported") else "", s_at if s_at is not None else "?",
            int(r_at) if r_at is not None else "never", " (window %ss)" % gap if gap is not None else ""))
    emit("containers started by Docker after boot", ok_c, "; ".join(p.split(", rule")[0] for p in parts))
    # Правило обязано появиться само при старте контейнера (start.sh своих профилей, служба goamnezia-isolation —
    # у подхваченных, OPS-05), а не ждать первого тика сторожа (OnBootSec=2min): до него люди профиля видят друг друга.
    tick = watch_first_run()
    late = [p for p, r_at in first_rule.items() if tick is not None and r_at >= tick]
    iso = prop("goamnezia-isolation.service", "ActiveState", "UnitFileState")
    emit("isolation rule after boot (incl. adopted), before the watch tick", len(first_rule) == len(prof) and not late,
         "; ".join(parts) + "; first watch tick at %s; goamnezia-isolation %s/%s%s" % (
             "+%ds" % tick if tick is not None else "not yet", iso.get("UnitFileState") or "-",
             iso.get("ActiveState") or "-",
             ("; rule only after the watch tick: %s" % ", ".join(late)) if late else ""))


# ── заполненный диск ────────────────────────────────────────────────────────────────────────────────────
FILLS = [OPS + "/fill-1.bin", OPS + "/fill-2.bin", OPS + "/fill-3.bin"]


def fill_cleanup(*_):
    for f in FILLS:
        try:
            os.remove(f)
        except OSError:
            pass
    sh(["systemctl", "stop", FAILSAFE + ".timer", FAILSAFE + ".service"], timeout=30)


def fallocate(path, size):
    """fallocate до size байт; места впритык — поменьше, шагами по 4 МБ (метаданные экстентов)."""
    for _ in range(64):
        if size <= 0:
            return 0
        rc, _, _ = sh(["fallocate", "-l", str(size), path], timeout=120)
        if rc == 0:
            return size
        try:
            os.remove(path)
        except OSError:
            pass
        size -= 4 * 1048576
    return 0


def fill_to_zero(path):
    """Дописать нули до ENOSPC (остаток после fallocate). → (байт записано, дошли ли до ENOSPC)."""
    buf, n = b"\0" * 65536, 0
    try:
        with open(path, "wb", buffering=0) as f:
            while True:
                n += f.write(buf)
    except OSError as e:
        return n, e.errno == 28
    return n, False


def menu_disk_line(segments):
    for s in segments:
        m = re.search(r"Свободно на диске: (\d+) МБ[^\n]*|Free disk space: (\d+) MB[^\n]*", s)
        if m:
            return m.group(0).strip()
    return ""


def m_diskfull(probe):
    os.makedirs(OPS, mode=0o700, exist_ok=True)
    for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(s, lambda *_: (fill_cleanup(), sys.exit(3)))
    c0 = cfg()
    domain = c0.get("domain")
    u0 = users_doc()
    if not probe or not user_entry(u0, probe):
        probe = next((u.get("name") for u in (u0 or {}).get("users") or [] if u.get("enabled") is not False), "")
    marker = "%s/.migrated_%s" % (GA, c0.get("version"))
    marker0 = sha(marker)
    bk0, stage0, st0 = backups(), stage_dirs(), services()
    v0 = statvfs()
    # Страховка: процесс проверки умрёт (обрыв SSH, kill) — файлы-заполнители удалит таймер через 15 минут.
    fill_cleanup()
    rc, _, err = sh(["systemd-run", "--unit=" + FAILSAFE, "--on-active=15min", "--timer-property=AccuracySec=10s",
                     "/bin/sh", "-c", "rm -f %s/fill-*.bin" % OPS])
    if rc != 0:
        return emit("disk-full: failsafe timer", False, "systemd-run failed: %s" % err.strip()[:160])
    try:
        target = int(v0["total"] * 0.013)
        got = fallocate(FILLS[0], v0["avail"] - target)
        v1 = statvfs()
        emit("disk filled to 98-99 %", got > 0 and v1["pct"] in (98, 99),
             "fallocate %d MB → Use %d%%, free for users %d MB, root reserve %d MB (was %d%%, %d MB free)" % (
                 mb(got), v1["pct"], mb(v1["avail"]), mb(v1["free"] - v1["avail"]), v0["pct"], mb(v0["avail"])))
        if not got:
            return

        # 1. меню и экран диагностики (SSH «статус»)
        m = menu_run(["4", "7", "0", "0"], timeout=240)
        first = m["segments"][0] if m["segments"] else ""
        drawn = m["menu_at"] is not None and "goAmnezia pro v" in m["text"]
        emit("menu opens at 99 %", drawn and m["rc"] == 0, "banner and main menu in %ss, keys %s, exit %s" % (
            m["menu_at"], "→".join(k or "Enter" for k in m["sent"]), m["rc"]))
        dl = menu_disk_line(m["segments"])
        emit("SSH diagnostics warn about the disk (menu 4 → 7)", "мало" in dl or "low" in dl, "«%s»" % dl if dl else
             "no disk line on the diagnostics screen")
        q = bridge("quality")
        qd = (q.get("quality") or {}).get("disk_free_mb")
        port = (c0.get("panel") or {}).get("port") or 1985
        pc, pj = curl_json("http://127.0.0.1:%s/api/quality" % port)
        lc, lj = curl_json("http://127.0.0.1:%s/api/logs/status" % port)
        pq = (((pj or {}).get("data") or {}).get("quality") or {}).get("disk_free_mb")
        pl = ((lj or {}).get("data") or {}).get("free_mb")
        emit("panel and bridge report low disk", isinstance(qd, int) and qd < 1024 and pc == "200" and
             isinstance(pq, int) and pq < 1024, "quality.disk_free_mb=%s (warn < 1024: SSH diagnostics, bot, panel "
             "«Диагностика»); panel /api/quality %s → %s MB, /api/logs/status %s → free %s MB" % (qd, pc, pq, lc, pl))
        st = bridge("status")
        sd = st.get("disk") if isinstance(st.get("disk"), dict) else {}
        hdr = next((ln.strip() for ln in first.splitlines()
                    if re.search(r"Диск (заполнен на|почти полон)|The disk is (almost full|\d+% full)", ln)), "")
        oc, oj = curl_json("http://127.0.0.1:%s/api/overview" % port)
        od = (((oj or {}).get("data") or {}).get("disk") or {}) if isinstance(oj, dict) else {}
        emit("main menu header, status and panel overview warn about the disk (OPS-01)",
             sd.get("level") in ("low", "critical") and bool(hdr) and od.get("level") in ("low", "critical"),
             "status.disk: level %s, used %s%%, free %s MB (low < %s, critical < %s); main menu header: %s; panel "
             "/api/overview %s → disk.level %s (the overview draws the warning from it)" % (
                 sd.get("level") or "-", sd.get("used_pct"), sd.get("free_mb"), sd.get("low_mb"), sd.get("critical_mb"),
                 "«%s»" % hdr[:120] if hdr else "no disk line", oc, od.get("level") or "-"))

        # 2. бэкап
        b = bridge("backup-create")
        bk1, stage1 = backups(), stage_dirs()
        emit("backup-create refuses clearly", b.get("status") != "success" and b.get("code") == "low_disk",
             "status %s, code %s: «%s»" % (b.get("status"), b.get("code"), (b.get("message") or "")[:160]))
        emit("no broken archive left", bk1 == bk0 and stage1 == stage0, "backups/ %d → %d files, new: %s; "
             "stage dirs new: %s" % (len(bk0), len(bk1), ", ".join(sorted(set(bk1) - set(bk0))) or "-",
                                     ", ".join(sorted(set(stage1) - set(stage0))) or "-"))

        # 3. запись config/users при 99 %
        e0 = user_entry(users_doc(), probe) or {}
        q0 = int(e0.get("quota_bytes") or 0)
        r1 = bridge("user-quota", "--name=" + probe, "--quota_gb=%d" % (q0 // GIB + 1))
        mid = user_entry(users_doc(), probe) or {}
        r2 = bridge("user-quota", "--name=" + probe, "--quota_gb=%d" % (q0 // GIB))
        u1 = users_doc()
        back = users_norm(u1) == users_norm(u0)
        emit("writes at 99 % succeed, users.json intact", r1.get("status") == "success" and
             mid.get("quota_bytes") == (q0 // GIB + 1) * GIB and r2.get("status") == "success" and back,
             "%s quota +1 GB → %s, back → %s; users.json valid=%s, same as before=%s" % (
                 probe, r1.get("status"), r2.get("status"), u1 is not None, back))

        # 4. 100 % и для root: запись обязана сорваться, не испортив файлы
        lang0 = c0.get("lang") or "ru"
        other = "en" if lang0 == "ru" else "ru"
        lmark = GA + "/.language"
        lmark0 = open(lmark, "rb").read() if os.path.exists(lmark) else None
        fallocate(FILLS[1], statvfs()["free"] - 1048576)
        _, enospc = fill_to_zero(FILLS[2])
        v2 = statvfs()
        s_cfg, s_usr = sha(CFG), sha(USERS)
        w1 = bridge("user-quota", "--name=" + probe, "--quota_gb=%d" % (q0 // GIB + 2))
        w2 = bridge("lang-set", "--lang=" + other)
        site_hard = site_code(domain) if domain else "-"
        rc_log, _, _ = sh(["logger", "-t", "ga-live-ops", "disk full probe"], timeout=15)
        a_cfg, a_usr = sha(CFG), sha(USERS)
        cfg_ok, usr_ok = jload(CFG) is not None, users_doc() is not None
        mk = os.path.getsize(marker) if os.path.exists(marker) else -1
        try:
            lmark_hard = open(lmark, "rb").read()
        except OSError:
            lmark_hard = None
        lang_kept = lmark0 is None or lmark_hard == lmark0
        left = json_tmp_leftovers()
        for f in FILLS[1:]:
            try:
                os.remove(f)
            except OSError:
                pass
        emit("100 % for root: writes fail, files intact (atomic)", enospc and s_cfg == a_cfg and s_usr == a_usr and
             cfg_ok and usr_ok and not left and mk != 0 and lang_kept,
             "ENOSPC for data writes: %s (statvfs free %d MB = ext4 metadata reserve); user-quota → %s %s; lang-set %s "
             "→ %s %s; users.json/config.json unchanged=%s/%s, valid JSON=%s/%s; temp leftovers: %s; .language kept=%s; "
             "migration marker %s" % (
                 "yes" if enospc else "NO", mb(v2["free"]), w1.get("status"), w1.get("code") or "", other,
                 w2.get("status"), w2.get("code") or "", s_usr == a_usr, s_cfg == a_cfg, usr_ok, cfg_ok,
                 ", ".join(left) or "-", lang_kept,
                 "EMPTIED by the write at ENOSPC (next call re-runs the reapply steps)" if mk == 0 else "%d bytes" % mk))
        lied = [n for n, w, same in (("user-quota", w1, s_usr == a_usr), ("lang-set", w2, s_cfg == a_cfg))
                if w.get("status") == "success" and same]
        emit("100 %: a write that did not happen is reported as an error", not lied,
             "user-quota → %s, lang-set %s → %s (config.json lang still %s)%s" % (
                 w1.get("status"), other, w2.get("status"), (jload(CFG) or {}).get("lang"),
                 "; reported success without writing: %s" % ", ".join(lied) if lied else ""))
        st1 = services()
        restarted = [u for u, p in st1.items() if p.get("NRestarts") != (st0.get(u) or {}).get("NRestarts")]
        down = [u for u, p in st1.items() if p.get("ActiveState") != "active"]
        jd = prop("systemd-journald.service", "ActiveState").get("ActiveState")
        emit("services survive the full disk (logs)", not down and not restarted and jd == "active",
             "site https://%s/ at 100 %% → %s; logger rc %s; journald %s; down: %s; restarted: %s" % (
                 domain or "-", site_hard, rc_log, jd, ", ".join(down) or "-", ", ".join(restarted) or "-"))
    finally:
        fill_cleanup()
        # Проверка не меняет настроек: язык, если запись всё-таки прошла, и маркер .language — как были.
        try:
            if (jload(CFG) or {}).get("lang") != c0.get("lang") and c0.get("lang"):
                bridge("lang-set", "--lang=" + c0["lang"])
            if lmark0 is not None and (open(lmark, "rb").read() if os.path.exists(lmark) else None) != lmark0:
                with open(lmark, "wb") as f:
                    f.write(lmark0)
        except (OSError, NameError):
            pass

    # после уборки
    v3 = statvfs()
    gone = not any(os.path.exists(f) for f in FILLS)
    ft = prop(FAILSAFE + ".timer", "LoadState").get("LoadState")
    emit("fill removed, disk back", gone and abs(v3["avail"] - v0["avail"]) < 256 * 1048576 and ft != "loaded",
         "Use %d%%, free %d MB (before %d MB); failsafe timer %s" % (v3["pct"], mb(v3["avail"]), mb(v0["avail"]), ft))
    q = bridge("quality")
    qd = (q.get("quality") or {}).get("disk_free_mb")
    b = bridge("backup-create")
    name = b.get("file") or ""
    removed = False
    if b.get("status") == "success" and re.fullmatch(r"goamnezia_backup_\d{8}_\d{6}(_\d+)?\.tar\.gz(\.enc)?", name):
        for f in (GA + "/backups/" + name, GA + "/backups/" + name + ".sha256"):
            try:
                os.remove(f)
                removed = True
            except OSError:
                pass
    ticks = rerun_failed_ticks()
    st2, fl = services(), failed_units()
    fatal = fl
    emit("after: back to normal", isinstance(qd, int) and qd >= 1024 and b.get("status") == "success" and
         all(p.get("ActiveState") == "active" for p in st2.values()) and not fatal and
         users_norm(users_doc()) == users_norm(u0),
         "quality.disk_free_mb=%s; backup-create %s (test archive removed=%s); services active=%s; failed: %s; "
         "tick runs that failed while the disk was full, run again: %s; users.json same as before=%s; "
         "migration marker same=%s" % (
             qd, b.get("status"), removed, all(p.get("ActiveState") == "active" for p in st2.values()),
             ", ".join(fatal) or "-", ", ".join(ticks) or "-", users_norm(users_doc()) == users_norm(u0),
             sha(marker) == marker0))


# ── ротация журналов ────────────────────────────────────────────────────────────────────────────────────
def grep_file(path, needle):
    try:
        with open(path, "rb") as f:
            return needle.encode() in f.read()
    except OSError:
        return False


def m_logrotate():
    d = "/etc/logrotate.d"
    nginx = shutil.which("nginx") is not None and os.path.isdir("/var/log/nginx")
    need = ["goamnezia"] + (["goamnezia-nginx"] if nginx else [])
    have = [f for f in need if os.path.isfile(os.path.join(d, f))]
    foreign = os.path.exists(os.path.join(d, "nginx"))
    desc = []
    for f in have:
        txt = open(os.path.join(d, f), encoding="utf-8").read()
        g = lambda k: (re.search(r"^\s*%s\s+(\S+)" % k, txt, re.M) or [None, "-"])[1]  # noqa: E731
        desc.append("%s (%s, maxsize %s, rotate %s%s)" % (f, "hourly" if re.search(r"^\s*hourly", txt, re.M) else "?",
                                                          g("maxsize"), g("rotate"),
                                                          ", copytruncate" if "copytruncate" in txt else ""))
    emit("rotation configs present", len(have) == len(need) and not foreign,
         "; ".join(desc) + "; Debian nginx config moved aside: %s" % ("no (duplicate!)" if foreign else "yes"))
    paths = [os.path.join(d, f) for f in have]
    rc, out, err = sh(["logrotate", "-d"] + paths, timeout=120)
    errs = [ln for ln in (out + err).splitlines() if re.search(r"(^|\s)error:", ln, re.I)]
    rc2, out2, err2 = sh(["logrotate", "-d", "/etc/logrotate.conf"], timeout=120)
    errs2 = [ln for ln in (out2 + err2).splitlines() if re.search(r"(^|\s)error:", ln, re.I)]
    emit("logrotate -d: configs valid", rc == 0 and not errs and rc2 == 0 and not errs2,
         "ours rc %d, errors %d%s; whole /etc/logrotate.conf rc %d, errors %d%s" % (
             rc, len(errs), (" «%s»" % errs[0][:120]) if errs else "", rc2, len(errs2),
             (" «%s»" % errs2[0][:120]) if errs2 else ""))
    u = prop("goamnezia-logrotate.service", "ExecStart", "Result", "ExecMainStatus")
    t = prop("goamnezia-logrotate.timer", "ActiveState")
    emit("hourly rotation unit", t.get("ActiveState") == "active" and u.get("Result") == "success" and
         all(p in u.get("ExecStart", "") for p in paths),
         "goamnezia-logrotate.timer %s; last run %s (status %s); unit rotates %s" % (
             t.get("ActiveState"), u.get("Result"), u.get("ExecMainStatus"),
             ", ".join(os.path.basename(p) for p in paths if p in u.get("ExecStart", ""))))

    # Принудительная ротация посреди записи: строки до неё — в .1, после — в новом файле.
    domain = cfg().get("domain")
    m1, m2 = "ga-live-rot-" + secrets.token_hex(4), "ga-live-rot-" + secrets.token_hex(4)
    acc, aud = "/var/log/nginx/access.log", "/var/log/goamnezia-audit.log"
    user = next((x.get("name") for x in (users_doc() or {}).get("users") or []
                 if x.get("peers") and not re.match(r"^(live|ga-live)", x.get("name", ""))), None)
    prof = sorted(((user_entry(users_doc(), user) or {}).get("peers") or {}).keys())[:1] if user else []

    def hit(marker):
        if domain and nginx:
            curl_code("https://%s/" % domain, "--resolve", "%s:443:127.0.0.1" % domain, "-A", marker)
        if user and prof:
            bridge_blind("user-key", "--name=" + user, "--profile=" + prof[0], "--format=conf", who=marker)
        time.sleep(1.5)

    ino0 = {p: (os.stat(p).st_ino if os.path.exists(p) else None) for p in (acc, aud)}
    hit(m1)
    pre_ok = {p: grep_file(p, m1) for p in (acc, aud)}
    rc3, out3, err3 = sh(["logrotate", "-f"] + paths, timeout=300)
    time.sleep(2)
    hit(m2)
    res, ok = [], rc3 == 0
    for p, label, mode in ((acc, "nginx access.log", "create+USR1"), (aud, "goamnezia-audit.log", "copytruncate")):
        if not os.path.exists(p) and not pre_ok[p]:
            res.append("%s: not written here" % label)
            continue
        ino1 = os.stat(p).st_ino if os.path.exists(p) else None
        old = p + ".1"
        in_old = grep_file(old, m1)
        new_in_new, new_in_old = grep_file(p, m2), grep_file(old, m2)
        good = pre_ok[p] and in_old and new_in_new and not new_in_old
        ok = ok and good
        res.append("%s (%s): before→.1 %s, after→new file %s, after in .1 %s, inode %s" % (
            label, mode, "yes" if in_old else "NO", "yes" if new_in_new else "NO", "YES" if new_in_old else "no",
            "new" if ino1 != ino0[p] else "same"))
    emit("logrotate -f keeps writers: new lines go to the new file", ok,
         "rc %d; %s; nginx %s" % (rc3, "; ".join(res), prop("nginx.service", "ActiveState").get("ActiveState")))

    # Бюджет: journald и компоненты продукта.
    jd = {}
    for f in sorted(glob.glob("/etc/systemd/journald.conf.d/*.conf")):
        for ln in open(f, encoding="utf-8"):
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.strip().split("=", 1)
                jd[k] = v
    _, du, _ = sh(["journalctl", "--disk-usage"])
    m = re.search(r"take up ([\d.]+)([KMGT])", du)
    used = float(m.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1048576}[m.group(2)] if m else -1
    cap = re.match(r"(\d+)M", jd.get("SystemMaxUse", ""))
    ls = bridge("logs-status")
    areas = ls.get("areas") or []
    over = [a.get("area") for a in areas if int(a.get("used_mb") or 0) > int(a.get("budget_mb") or 0)]
    lb = prop("goamnezia-logbudget.timer", "ActiveState").get("ActiveState")
    emit("log budget and journald limits", bool(cap) and 0 <= used <= int(cap.group(1)) and not over and
         ls.get("status") == "success" and lb == "active",
         "journald SystemMaxUse=%s SystemKeepFree=%s, journal %.0f MB; logs-status: %s; over budget: %s; "
         "logbudget timer %s" % (jd.get("SystemMaxUse", "-"), jd.get("SystemKeepFree", "-"), used,
                                  ", ".join("%s %s/%s MB" % (a.get("area"), a.get("used_mb"), a.get("budget_mb"))
                                            for a in areas), ", ".join(over) or "-", lb))


# ── продление сертификата ───────────────────────────────────────────────────────────────────────────────
def nginx_pids():
    try:
        master = open("/run/nginx.pid").read().strip()
    except OSError:
        return None, []
    _, out, _ = sh(["pgrep", "-P", master])
    return master, sorted(out.split())


def served_cert(domain):
    rc, out, _ = sh("echo | openssl s_client -connect 127.0.0.1:443 -servername %s 2>/dev/null | "
                    "openssl x509 -noout -fingerprint -sha256 -enddate" % domain, timeout=30)
    return out.strip().replace("\n", " ")


def m_certrenew():
    c = cfg()
    domain, kind = c.get("domain"), (c.get("site") or {}).get("cert")
    if not domain or kind != "letsencrypt":
        return emit("cert-renew", True, "no Let's Encrypt certificate here (domain %s, cert %s)" % (domain, kind), skip=True)
    ct = prop("certbot.timer", "ActiveState", "NextElapseUSecRealtime", "UnitFileState")
    own = prop("goamnezia-cert-renew.timer", "ActiveState", "LoadState")
    tick = prop("goamnezia-cert.timer", "ActiveState")
    svc = prop("certbot.service", "ExecStart")
    cron = [ln.strip() for ln in open("/etc/cron.d/certbot", encoding="utf-8")
            if ln.strip() and not ln.startswith(("#", "SHELL", "PATH"))] if os.path.exists("/etc/cron.d/certbot") else []
    sched = (ct.get("UnitFileState") == "enabled" and ct.get("ActiveState") == "active") or own.get("ActiveState") == "active"
    emit("renewal scheduled", sched, "certbot.timer %s/%s, next %s (runs «%s»); own goamnezia-cert-renew.timer %s; "
         "cron.d/certbot: %s; product cert-tick timer %s" % (
             ct.get("UnitFileState"), ct.get("ActiveState"), ct.get("NextElapseUSecRealtime") or "-",
             "certbot -q renew" if "renew" in svc.get("ExecStart", "") else "?", own.get("LoadState"),
             "defers to systemd" if cron and all("/run/systemd/system" in x for x in cron) else (cron[0][:60] if cron else "none"),
             tick.get("ActiveState")))
    conf = "/etc/letsencrypt/renewal/%s.conf" % domain
    txt = open(conf, encoding="utf-8").read() if os.path.exists(conf) else ""
    hook = "/etc/letsencrypt/renewal-hooks/deploy/goamnezia-nginx-reload.sh"
    htxt = open(hook, encoding="utf-8").read() if os.path.exists(hook) else ""
    rh = (re.search(r"^renew_hook\s*=\s*(.+)$", txt, re.M) or [None, ""])[1].strip()
    auth = (re.search(r"^authenticator\s*=\s*(\S+)", txt, re.M) or [None, "?"])[1]
    emit("renewal hook reloads nginx", "reload nginx" in htxt and os.access(hook, os.X_OK),
         "deploy hook %s: executable=%s, «systemctl reload nginx» %s; renewal conf renew_hook «%s»; authenticator %s" % (
             os.path.basename(hook), os.access(hook, os.X_OK), "yes" if "reload nginx" in htxt else "NO", rh or "-", auth))
    m0, w0 = nginx_pids()
    cert0 = served_cert(domain)
    t0 = time.time()
    rc, out, err = sh(["certbot", "renew", "--dry-run", "--run-deploy-hooks", "--no-random-sleep-on-renew",
                       "--cert-name", domain], timeout=600)
    took = int(time.time() - t0)
    good = rc == 0 and "all simulated renewals succeeded" in (out + err)
    emit("certbot renew --dry-run", good, "rc %d in %ds: %s" % (rc, took, "«all simulated renewals succeeded» (staging)"
                                                                if good else (out + err).strip().splitlines()[-1:]))
    time.sleep(3)
    m1, w1 = nginx_pids()
    ran = []
    try:
        log = open("/var/log/letsencrypt/letsencrypt.log", encoding="utf-8", errors="replace").read()
        log = log[log.rfind("'--dry-run', '--run-deploy-hooks'"):]
        ran = [os.path.basename(x.strip()) for x in re.findall(r"Running deploy-hook command: (.+)", log)]
    except OSError:
        pass
    emit("deploy hook reloaded nginx", good and m1 == m0 and bool(w1) and not (set(w1) & set(w0)) and
         any("goamnezia-nginx-reload" in x or "reload nginx" in x for x in ran),
         "hooks run: %s; master pid same=%s, workers %d → %d, all new=%s; nginx %s" % (
             ", ".join(ran) or "none", m1 == m0, len(w0), len(w1), bool(w1) and not (set(w1) & set(w0)),
             prop("nginx.service", "ActiveState").get("ActiveState")))
    cert1 = served_cert(domain)
    code = site_code(domain)
    emit("site 200 after renewal, certificate unchanged", code == "200" and cert1 == cert0 and bool(cert0),
         "https://%s/ → %s (verified TLS); served cert same=%s (%s)" % (domain, code, cert1 == cert0,
                                                                       (re.search(r"notAfter=.*", cert1) or [""])[0]))
    t = bridge("cert-tick")
    emit("product cert-tick", t.get("status") == "success", "status %s: %s" % (t.get("status"), (t.get("message") or "")[:120]))


# ── неудачное обновление и откат ────────────────────────────────────────────────────────────────────────
RB = OPS + "/rb"
CAND_VERSION_SUFFIX = "-rbtest"
RESUME_VERSION_SUFFIX = "-rbresume"
INJECT = """
# ga-live update-rollback: намеренно падающий и намеренно зависающий шаги миграции (только на стенде, не в репозитории)
_ga_rbtest_crash() {
    [ -f %(rb)s/crash ] || return 0
    echo "ga-live rbtest: the migration step crashes now" >&2
    set -u
    : "${_ga_rbtest_unset}"
}
_ga_rbtest_hang() {
    [ -f %(rb)s/hang ] || return 0
    rm -f %(rb)s/hang
    echo "ga-live rbtest: the migration step hangs now" >&2
    sleep 900
}
_ga_rbtest_after() { touch %(rb)s/after_ran 2>/dev/null || true; }
""" % {"rb": RB}
# Метки продукта, которые проверка обновления может оставить (и обязана убрать): что заводит неудачное или прерванное
# обновление, возврат версии и сама проверка.
TEST_DOTS = re.compile(r"^\.(migrated_|preupgrade_|migrating_|update_failed\.json$|changelog_pending$|prev_code)")


def code_files():
    fs = [GA + "/install.sh", GA + "/bootstrap.sh"] + glob.glob(GA + "/lib/*.sh") + glob.glob(GA + "/lib/*.py") \
        + glob.glob(GA + "/lib/nginx/*") + glob.glob(GA + "/lib/lang/*.sh") + glob.glob(GA + "/lib/lang/*/*.sh") \
        + glob.glob(GA + "/admin-web/*.py") + glob.glob(GA + "/goamnezia-bot/*.py") + glob.glob(GA + "/goamnezia-mcp/*.py")
    return {p[len(GA) + 1:]: sha(p) for p in sorted(fs) if os.path.isfile(p)}


def code_version():
    _, out, _ = sh(["bash", GA + "/install.sh", "--version"], timeout=60)
    m = re.search(r"goAmnezia pro (\S+)", out)
    return m.group(1) if m else "?"


def version_in(common_sh):
    try:
        m = re.search(r'^GOAMNEZIA_VERSION="([0-9][0-9A-Za-z.+-]*)"', open(common_sh, encoding="utf-8").read(), re.M)
    except OSError:
        return ""
    return m.group(1) if m else ""


def dotfiles():
    return sorted(f for f in os.listdir(GA) if f.startswith("."))


def dot_state():
    """Метки продукта в каталоге: имя → содержимое (каталог — None)."""
    out = {}
    for f in dotfiles():
        p = os.path.join(GA, f)
        try:
            out[f] = open(p, "rb").read() if os.path.isfile(p) else None
        except OSError:
            out[f] = None
    return out


def dots_restore(before):
    """Вернуть метки как были: лишние — только из TEST_DOTS — убрать, пропавшие и изменённые метки миграций — вернуть."""
    fixed = []
    for f in dotfiles():
        if f not in before and TEST_DOTS.match(f):
            p = os.path.join(GA, f)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                fixed.append("-" + f)
            except OSError:
                pass
    for f, data in before.items():
        if data is None or not re.match(r"^\.(migrated_|preupgrade_)", f):
            continue
        p = os.path.join(GA, f)
        try:
            cur = open(p, "rb").read()
        except OSError:
            cur = None
        if cur != data:
            with open(p, "wb") as fh:
                fh.write(data)
            os.chmod(p, 0o600)
            fixed.append("=" + f)
    return fixed


def archive_kind(name):
    rc, out, _ = sh("tar -xzOf %s/backups/%s --wildcards '*metadata.json' 2>/dev/null | head -c 4000" % (GA, name), timeout=60)
    try:
        md = json.loads(out)
    except ValueError:
        return {}
    return {"kind": md.get("kind"), "version": md.get("goamnezia_version"), "created_by": md.get("created_by")}


def sha_ok(name):
    p = GA + "/backups/" + name
    s = open(p + ".sha256").read().split()[0] if os.path.exists(p + ".sha256") else ""
    return bool(s) and s == sha(p)


def wait_timer_window(need=75, limit=300):
    """Дождаться окна, в котором тики, зовущие мост (watch/quota/stats), не придут раньше чем через need секунд:
    иначе тик проведёт первую попытку миграции вместо нас, и строка «миграция упала» наблюдала бы вторую. «next» в
    прошлом — тик ещё ждёт своей случайной задержки (RandomizedDelaySec) и вот-вот придёт: окна нет. → ждали ли зря."""
    t0 = time.time()
    while time.time() - t0 < limit:
        _, out, _ = sh(["systemctl", "list-timers", "--all", "-o", "json", "goamnezia-watch.timer",
                        "goamnezia-quota.timer", "goamnezia-stats.timer"])
        try:
            nxt = [t.get("next") or 0 for t in json.loads(out)]
        except ValueError:
            return False
        now = time.time() * 1e6
        if all(n - now >= need * 1e6 for n in nxt if n):
            return True
        time.sleep(5)
    return False


def rerun_failed_ticks():
    """Прогоны тиков (oneshot goamnezia-*.service), упавшие во время проверки, — ещё раз сейчас: заодно видно,
    что тики снова работают, и стенд не остаётся с failed до их следующего срока. → ["unit ok|FAILED"]."""
    out = []
    running = services()
    for u in failed_units():
        if u.startswith("goamnezia-") and u not in running:
            sh(["systemctl", "start", u], timeout=600)
            out.append("%s %s" % (u.replace(".service", ""),
                                  "ok" if prop(u, "Result").get("Result") == "success" else "FAILED"))
    return out


def journal_since(epoch, pattern):
    """Какие единицы (unit или имя процесса) после epoch писали в журнал строку с pattern."""
    _, out, _ = sh(["journalctl", "--since", "@%d" % int(epoch), "--no-pager", "-o", "json", "--grep", pattern],
                   timeout=60)
    who = []
    for ln in out.splitlines():
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        who.append(j.get("_SYSTEMD_UNIT") or j.get("SYSLOG_IDENTIFIER") or "?")
    return who


def crashes_since(epoch):
    """Кто упал на внедрённом шаге после установки кандидата (по журналу): тик, мост."""
    return journal_since(epoch, "ga-live rbtest: the migration step crashes now")


# Загрузчик из репозитория (acceptance.py кладёт его рядом с проверками): кандидат ставится им, как «Обновить
# goAmnezia», — с копией прежнего кода рядом (.prev_code), и откатывать продукту есть чем (OPS-02).
BOOTSTRAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bootstrap.sh")
CODE_TOP = ("install.sh", "templates_catalog.json")
CODE_DIRS = ("lib", "goamnezia-bot", "goamnezia-mcp", "admin-web")


def copy_code_tree(dst):
    """Дерево кода, каким его взял бы загрузчик: копия установленного (install.sh, каталог шаблонов, lib/, бот, MCP,
    панель); данные продукта в него не попадают. → сколько файлов."""
    n = 0
    os.makedirs(dst, mode=0o700, exist_ok=True)
    for rel in CODE_TOP:
        if os.path.isfile(os.path.join(GA, rel)):
            shutil.copy2(os.path.join(GA, rel), os.path.join(dst, rel))
            n += 1
    for d in CODE_DIRS:
        for root, dirs, files in os.walk(os.path.join(GA, d)):
            dirs[:] = [x for x in dirs if x not in ("__pycache__", "venv", ".venv")]
            for name in files:
                if name.endswith(".ga-new"):
                    continue
                src = os.path.join(root, name)
                out = os.path.join(dst, os.path.relpath(src, GA))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copy2(src, out)
                n += 1
    return n


def install_source():
    out = {}
    try:
        for ln in open(GA + "/.install-source", encoding="utf-8").read().splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                out[k] = v
    except OSError:
        pass
    return out


def build_candidate(dst, ins, cmn, v_old, new_v, steps):
    """Кандидат new_v: копия установленного кода, в install.sh после _migrate_state_conf — шаги steps (и их тела
    перед main), в lib/common.sh — версия. → текст ошибки или ""."""
    anchor = "    _migrate_profile_line\n    _migrate_state_conf\n"
    tail = '\nmain "$@"\n'
    vline = 'GOAMNEZIA_VERSION="%s"' % v_old
    if ins.count(anchor) != 1 or not ins.endswith(tail) or cmn.count(vline) != 1:
        return "anchors not found (migration steps %d, tail %s, version line %d)" % (
            ins.count(anchor), ins.endswith(tail), cmn.count(vline))
    shutil.rmtree(dst, ignore_errors=True)
    copy_code_tree(dst)
    cand = ins.replace(anchor, anchor + steps)
    with open(dst + "/install.sh", "w", encoding="utf-8") as f:
        f.write(cand[: -len(tail)] + INJECT + tail)
    with open(dst + "/lib/common.sh", "w", encoding="utf-8") as f:
        f.write(cmn.replace(vline, 'GOAMNEZIA_VERSION="%s"' % new_v))
    return ""


def snap_compare_remote(before_name, label):
    """snapshot.py сейчас против сохранённого снимка (его снял acceptance.py до проверки). → (ok, сводка)."""
    snaps = "/root/ga-live/snapshots"
    before = "%s/%s.json" % (snaps, before_name)
    if not before_name or not os.path.exists(before):
        return None, "no saved snapshot %s" % before_name
    rc, out, err = sh("umask 077; python3 /root/ga-live/snapshot.py %s > %s/%s.json && python3 /root/ga-live/compare.py "
                      "%s %s/%s.json" % (label, snaps, label, before, snaps, label), timeout=900)
    lines = out.splitlines()
    bad = [ln.split("—")[0].strip() for ln in lines if ln.startswith(("FAIL", "WARN"))]
    summary = next((ln for ln in reversed(lines) if ln.startswith("summary")), (err.strip() or "no output")[-160:])
    return rc == 0, summary + ("; " + "; ".join(bad[:6]) if bad else "")


def m_rollback(before_snap=""):
    """Обновление, которое не встало (OPS-02), и прерванная миграция (OPS-03), — через загрузчик, как у человека.
    1) кандидат <v>-rbtest падает на шаге миграции → продукт сам возвращает код, данные, юниты и .install-source,
       говорит об этом в SSH и в status.update_rollback; снимок до/после отката;
    2) кандидат <v>-rbresume «зависает» на шаге, процесс убит (SIGKILL) → следующий вызов моста доделывает миграцию
       с того же снимка; затем прежняя версия возвращается тем же загрузчиком (путь «откат версии»)."""
    for d in (RB, RB + "/orig", RB + "/orig/lib"):
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
    for f in ("crash", "hang", "after_ran"):
        try:
            os.remove(os.path.join(RB, f))
        except OSError:
            pass
    if not os.path.isfile(BOOTSTRAP):
        return emit("update: bootstrap.sh next to the checks", False, "acceptance.py uploads it; without the loader "
                    "there is no copy of the previous code and nothing to roll back to")
    swap = ("install.sh", "lib/common.sh")
    v_old, c0, src0 = code_version(), cfg(), install_source()
    code0, dots0, bk0 = code_files(), dot_state(), backups()
    lang = c0.get("lang") if c0.get("lang") in ("ru", "en") else "ru"
    for f in swap:
        shutil.copy2(os.path.join(GA, f), os.path.join(RB, "orig", f))
    ins = open(GA + "/install.sh", encoding="utf-8").read()
    cmn = open(GA + "/lib/common.sh", encoding="utf-8").read()
    new_v, res_v = v_old + CAND_VERSION_SUFFIX, v_old + RESUME_VERSION_SUFFIX
    err = build_candidate(RB + "/cand", ins, cmn, v_old, new_v, "    _ga_rbtest_crash\n    _ga_rbtest_after\n") \
        or build_candidate(RB + "/cand2", ins, cmn, v_old, res_v, "    _ga_rbtest_hang\n    _ga_rbtest_after\n")
    if err:
        return emit("update: build the candidates", False, err)
    copy_code_tree(RB + "/pristine")
    emit("update: candidates built on the stand", True, "%s → %s (a migration step aborts: set -u, unbound variable) and "
         "%s (a step hangs, the process is killed), both after _migrate_state_conf with a sentinel step right after it; "
         "installed by bootstrap.sh from a local tree, as «Обновить goAmnezia» does" % (v_old, new_v, res_v))
    env0 = {"GOAMNEZIA_BRANCH": src0.get("branch") or "dev", "GOAMNEZIA_LANG": lang}
    if re.fullmatch(r"[0-9a-f]{40}", src0.get("ref", "")):
        env0["GOAMNEZIA_REF"] = src0["ref"]
    moved = False  # на диске код не прежний — finally вернёт прежний
    try:
        # ── 1. миграция падает → автоматический откат ──
        open(RB + "/crash", "w").close()
        window = wait_timer_window()
        t_inst = time.time()
        moved = True
        m = menu_run(["0"], timeout=900, argv=["bash", BOOTSTRAP], env_extra=dict(env0, GOAMNEZIA_LOCAL_TREE=RB + "/cand"))
        text = m["text"]
        crash_line = next((ln.strip() for ln in text.splitlines() if "unbound" in ln or "не заданы границы" in ln), "")
        who = (["menu"] if crash_line or "the migration step crashes now" in text else []) + crashes_since(t_inst - 1)
        v1, c1, bk1 = code_version(), cfg(), backups()
        new_bk = sorted(set(bk1) - set(bk0) - {x for x in bk1 if x.endswith(".sha256")})
        kinds = {n: archive_kind(n) for n in new_bk}
        pre = [n for n, k in kinds.items() if k.get("kind") == "preupgrade"]
        emit("update: migration crashed (as injected)", bool(who),
             "crashed in: %s%s; loader + menu exit %s%s" % (
                 ", ".join(who) or "nobody", (" «%s»" % crash_line[-90:]) if crash_line else "", m["rc"],
                 "" if window else "; no quiet window between timer ticks was found"))
        emit("update: preupgrade snapshot taken before the migration", bool(pre) and all(sha_ok(n) for n in pre),
             "; ".join("%s kind=%s state version=%s sha256 ok=%s" % (n, k.get("kind"), k.get("version"), sha_ok(n))
                       for n, k in kinds.items()) or "no new archive")
        code1, src1 = code_files(), install_source()
        code_diff = sorted(k for k in set(code0) | set(code1) if code0.get(k) != code1.get(k))
        notice = jload(GA + "/.update_failed.json") or {}
        prev_left = os.path.exists(GA + "/.prev_code")
        back = v1 == v_old and c1.get("version") == c0.get("version") and not code_diff and src1 == src0 and not prev_left
        emit("update: automatic rollback to %s (code, data, .install-source)" % v_old,
             back and notice.get("code_restored") is True and notice.get("data_restored") is True,
             "code %s, config.version %s, code files differing from before: %s, .install-source as before: %s, "
             ".prev_code left: %s; notice: stage %s, code restored %s, data restored %s from %s" % (
                 v1, c1.get("version"), ", ".join(code_diff[:6]) or "none (%d files)" % len(code0), src1 == src0,
                 prev_left, notice.get("stage"), notice.get("code_restored"), notice.get("data_restored"),
                 os.path.basename(notice.get("archive") or "") or "-"))
        if back:
            moved = False
        st = bridge("status")
        ur = st.get("update_rollback") if isinstance(st.get("update_rollback"), dict) else {}
        told = ("Обновление до %s не удалось" % new_v) in text or ("The update to %s failed" % new_v) in text
        hdr = re.search(r"(Обновление до %s не удалось \(\d\d\.\d\d\.\d{4}\)|The update to %s failed \(\d\d\.\d\d\.\d{4}\))"
                        % (re.escape(new_v), re.escape(new_v)), text)
        port = (c0.get("panel") or {}).get("port") or 1985
        oc, oj = curl_json("http://127.0.0.1:%s/api/overview" % port)
        orb = (((oj or {}).get("data") or {}).get("update_rollback") or {}) if isinstance(oj, dict) else {}
        emit("update: the person is told — SSH message, menu header, status.update_rollback, panel overview",
             told and bool(hdr) and ur.get("failed_version") == new_v and ur.get("version") == v_old
             and ur.get("code_restored") is True and ur.get("data_restored") is True
             and orb.get("failed_version") == new_v,
             "SSH: «update to %s failed» %s, menu header line %s; status.update_rollback: failed_version %s, version %s, "
             "stage %s, code/data restored %s/%s; panel /api/overview %s → update_rollback.failed_version %s; menu "
             "after the rollback drawn: %s" % (
                 new_v, "yes" if told else "no", "yes" if hdr else "no", ur.get("failed_version"), ur.get("version"),
                 ur.get("stage"), ur.get("code_restored"), ur.get("data_restored"), oc, orb.get("failed_version") or "-",
                 "yes" if m["menu_at"] is not None else "no"))
        if before_snap:
            ok, summ = snap_compare_remote(before_snap, "rb-after-auto")
            emit("update: snapshot before / after the automatic rollback", bool(ok), summ)
        w = bridge("watch-tick")
        after = os.path.exists(RB + "/after_ran")
        marked = ".migrated_" + new_v in dotfiles()
        emit("update: next run stays on %s (the failed version is not marked done)" % v_old,
             w.get("status") == "success" and not after and not marked and code_version() == v_old,
             "watch-tick → %s; code %s; .migrated_%s %s; steps after the crash point ran: %s" % (
                 w.get("status"), code_version(), new_v, "written" if marked else "absent", "yes" if after else "no"))
        # Тестовые следы первой части: отметка об откате (иначе неделю висит в шапке меню) и архив preupgrade.
        for f in (GA + "/.update_failed.json",):
            try:
                os.remove(f)
            except OSError:
                pass
        for f in ("crash", "after_ran"):
            try:
                os.remove(os.path.join(RB, f))
            except OSError:
                pass

        # ── 2. миграция прервана (SIGKILL посреди шага) → следующий вызов доделывает ──
        open(RB + "/hang", "w").close()
        wait_timer_window()
        t2 = time.time()
        moved = True
        m2 = menu_run(["0"], timeout=900, argv=["bash", BOOTSTRAP], kill_on=r"the migration step hangs now",
                      env_extra=dict(env0, GOAMNEZIA_LOCAL_TREE=RB + "/cand2"))
        time.sleep(2)
        v2, c2 = code_version(), cfg()
        wip = os.path.exists(GA + "/.migrating_" + res_v)
        prev2 = jload_kv(GA + "/.prev_code/meta")
        emit("update: migration killed mid-step — the interruption is recorded",
             m2["killed"] and v2 == res_v and c2.get("version") == c0.get("version") and wip and prev2.get("to") == res_v,
             "process group killed on the hanging step: %s (exit %s); code %s, config.version %s, .migrating_%s %s, "
             ".prev_code from %s to %s" % (m2["killed"], m2["rc"], v2, c2.get("version"), res_v,
                                            "present" if wip else "absent", prev2.get("from"), prev2.get("to")))
        w2 = bridge("watch-tick")
        tick_first = [u for u in journal_since(t2, "Доделываю прерванное обновление|Finishing the interrupted update")
                      if u != "?"]
        v3, c3 = code_version(), cfg()
        after2 = os.path.exists(RB + "/after_ran")
        emit("update: next run resumes the interrupted migration and completes it (OPS-03)",
             w2.get("status") == "success" and after2 and v3 == res_v and c3.get("version") == res_v
             and os.path.exists(GA + "/.migrated_" + res_v) and not os.path.exists(GA + "/.migrating_" + res_v)
             and not os.path.exists(GA + "/.prev_code"),
             "watch-tick → %s%s; steps after the interruption point ran: %s; config.version %s; .migrated_%s %s, "
             ".migrating_%s %s, .prev_code %s" % (
                 w2.get("status"), (" (resumed first by: %s)" % ", ".join(sorted(set(tick_first)))) if tick_first else "",
                 "yes" if after2 else "NO", c3.get("version"), res_v,
                 "present" if os.path.exists(GA + "/.migrated_" + res_v) else "absent", res_v,
                 "left" if os.path.exists(GA + "/.migrating_" + res_v) else "removed",
                 "left" if os.path.exists(GA + "/.prev_code") else "removed"))
        # Прежнюю версию — тем же загрузчиком (так вернёт её и человек): путь «откат версии» в миграции.
        m3 = menu_run(["0"], timeout=900, argv=["bash", BOOTSTRAP],
                      env_extra=dict(env0, GOAMNEZIA_LOCAL_TREE=RB + "/pristine"))
        v4, c4, code4, src4 = code_version(), cfg(), code_files(), install_source()
        said = ("Откат с версии %s на %s" % (res_v, v_old)) in m3["text"] or \
               ("Rollback from version %s to %s" % (res_v, v_old)) in m3["text"]
        diff4 = sorted(k for k in set(code0) | set(code4) if code0.get(k) != code4.get(k))
        if v4 == v_old and not diff4:
            moved = False
        emit("update: previous version back through the loader (version rollback)",
             v4 == v_old and c4.get("version") == c0.get("version") and not diff4 and src4 == src0 and said,
             "loader + menu exit %s; «rollback from %s to %s» said: %s; code %s, config.version %s, code files differing: "
             "%s, .install-source as before: %s" % (m3["rc"], res_v, v_old, "yes" if said else "no", v4, c4.get("version"),
                                                   ", ".join(diff4[:6]) or "none", src4 == src0))
    finally:
        if moved:
            # Продукт не вернул прежний код сам — возвращаем его мы, версию в config.json приводит миграция.
            for f in swap:
                sh(["cp", "-f", os.path.join(RB, "orig", f), os.path.join(GA, f)])
            if os.path.isdir(RB + "/pristine"):
                sh("cp -a %s/pristine/. %s/" % (RB, GA))
            shutil.rmtree(GA + "/.prev_code", ignore_errors=True)
            bridge("watch-tick")
        fixed = dots_restore(dots0)
        extra = sorted(set(backups()) - set(bk0))
        for n in extra:
            try:
                os.remove(GA + "/backups/" + n)
            except OSError:
                pass
        reran = rerun_failed_ticks()
        for f in ("crash", "hang", "after_ran"):
            try:
                os.remove(os.path.join(RB, f))
            except OSError:
                pass
        for d in ("cand", "cand2", "pristine"):
            shutil.rmtree(os.path.join(RB, d), ignore_errors=True)
        code9 = code_files()
        diff9 = sorted(k for k in set(code0) | set(code9) if code0.get(k) != code9.get(k))
        if not diff9:
            shutil.rmtree(RB, ignore_errors=True)
        st, fl = services(), failed_units()
        emit("update: test traces removed, stand as before", not diff9 and code_version() == v_old and
             sorted(dot_state()) == sorted(dots0) and backups() == bk0 and install_source() == src0 and not fl and
             all(p.get("ActiveState") == "active" for p in st.values()),
             "code as before: %s; markers fixed: %s; test archives removed: %d; tick runs that failed, run again: %s; "
             "services active: %s; failed: %s%s" % (
                 "yes" if not diff9 else ", ".join(diff9[:6]), ", ".join(fixed) or "-", len(extra), ", ".join(reran) or "-",
                 all(p.get("ActiveState") == "active" for p in st.values()), ", ".join(fl) or "-",
                 "; the test had to put the previous code back itself" if moved else ""))


def jload_kv(path):
    out = {}
    try:
        for ln in open(path, encoding="utf-8").read().splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                out[k] = v
    except OSError:
        pass
    return out


# ── обновление настоящим путём загрузчика ───────────────────────────────────────────────────────────────
def prev_watch(stop, box):
    """Поток: когда .prev_code появился и когда пропал, что в его meta и сколько записей в манифесте."""
    t0, p = time.time(), GA + "/.prev_code"
    while not stop.is_set():
        if os.path.isdir(p):
            if box.get("seen") is None:
                box["seen"] = round(time.time() - t0, 1)
            meta = jload_kv(p + "/meta")
            if meta:
                box["meta"] = meta
            try:
                box["entries"] = sum(1 for _ in open(p + "/manifest"))
            except OSError:
                pass
        elif box.get("seen") is not None and box.get("gone") is None:
            box["gone"] = round(time.time() - t0, 1)
        time.sleep(0.2)


def audit_lines(action, since):
    out = []
    try:
        for ln in open("/var/log/goamnezia-audit.log", encoding="utf-8", errors="replace").read().splitlines()[-400:]:
            try:
                j = json.loads(ln)
            except ValueError:
                continue
            if j.get("action") == action and j.get("ts", "") >= since:
                out.append(j.get("details", ""))
    except OSError:
        pass
    return out


def m_upgrade(tree, branch="", expect=""):
    """Обновление, как у человека: загрузчик нового дерева (GOAMNEZIA_LOCAL_TREE) → exec меню → миграция → меню."""
    import threading
    if not (os.path.isfile(tree + "/bootstrap.sh") and os.path.isfile(tree + "/install.sh")):
        return emit("upgrade: the new tree", False, "no bootstrap.sh / install.sh in %s" % tree)
    tv = version_in(tree + "/lib/common.sh")
    v0, c0, src0 = code_version(), cfg(), install_source()
    branch = branch or src0.get("branch") or "dev"
    env_sha0, dots0 = sha(BOT_ENV), dot_state()
    lang = c0.get("lang") if c0.get("lang") in ("ru", "en") else "ru"
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    wait_timer_window()
    stop, box = threading.Event(), {}
    th = threading.Thread(target=prev_watch, args=(stop, box), daemon=True)
    th.start()
    env = {"GOAMNEZIA_LOCAL_TREE": tree, "GOAMNEZIA_BRANCH": branch, "GOAMNEZIA_LANG": lang}
    if expect:
        env["GOAMNEZIA_EXPECT_VERSION"] = expect
    m = menu_run(["0"], timeout=900, argv=["bash", tree + "/bootstrap.sh"], env_extra=env)
    time.sleep(1)
    stop.set()
    th.join(5)
    text = m["text"]
    v1, c1, src1 = code_version(), cfg(), install_source()
    saved = re.search(r"(Прежняя версия сохранена рядом для отката|The previous version is kept next to the product for "
                      r"a rollback):?\s*(\S+)", text)
    emit("loader from the local tree, then the menu",
         v1 == (expect or tv) and m["rc"] == 0 and m["menu_at"] is not None,
         "tree %s, %s → %s; branch kept %s → %s, ref %s; loader + menu exit %s, main menu drawn in %ss" % (
             tv, v0, v1, src0.get("branch"), src1.get("branch"),
             "as before" if src1.get("ref") == src0.get("ref") else "changed", m["rc"], m["menu_at"]))
    done = audit_lines("update-done", since)
    meta = box.get("meta") or {}
    same = v0 == v1  # переустановка той же версии (новый код): отметки update-done и «Что нового» не бывает
    emit(".prev_code kept while updating, removed after the migration",
         bool(saved) and box.get("seen") is not None and box.get("gone") is not None and meta.get("from") == v0
         and meta.get("to") == v1 and not os.path.exists(GA + "/.prev_code") and (bool(done) or same),
         "loader: «%s %s»; .prev_code seen at +%ss (from %s to %s, branch %s, %s manifest entries), removed at +%ss; "
         "audit update-done: %s" % (
             saved.group(1) if saved else "no line about the previous version", saved.group(2) if saved else "",
             box.get("seen"), meta.get("from"), meta.get("to"), meta.get("branch"), box.get("entries"), box.get("gone"),
             ", ".join(done) or "none"))
    title = ("Обновление %s → %s. Что нового" % (v0, v1), "Upgrade %s → %s. What is new" % (v0, v1))
    shown1 = any(x in text for x in title)
    mig = os.path.exists(GA + "/.migrated_" + v1) and not os.path.exists(GA + "/.migrating_" + v1)
    m2 = menu_run(["0"], timeout=300)
    shown2 = any(x in m2["text"] for x in title)
    emit("migration done, «What's new» shown once" if not same else "same version reinstalled, no «What's new»",
         c1.get("version") == v1 and mig and (shown1 or same) and not (same and shown1) and not shown2
         and not os.path.exists(GA + "/.changelog_pending") and not os.path.exists(GA + "/.update_failed.json"),
         "config.version %s, .migrated_%s %s; «%s» in the first menu: %s, in the second: %s; pending changelog: %s; "
         "update notice: %s" % (c1.get("version"), v1, "present" if mig else "absent", title[0], "yes" if shown1 else "no",
                                "yes" if shown2 else "no", "yes" if os.path.exists(GA + "/.changelog_pending") else "no",
                                "yes" if os.path.exists(GA + "/.update_failed.json") else "no"))
    reran = rerun_failed_ticks()
    st, fl = services(), failed_units()
    iso = st.get("goamnezia-isolation.service", {}).get("ActiveState", "not installed")
    env_same = env_sha0 is None or sha(BOT_ENV) == env_sha0
    emit("services after the update", all(p.get("ActiveState") == "active" for p in st.values()) and not fl
         and env_same, "%s; goamnezia-isolation %s; failed: %s; tick runs that failed, run again: %s; bot .env %s" % (
             ", ".join("%s %s" % (u.replace(".service", ""), p.get("ActiveState")) for u, p in st.items()), iso,
             ", ".join(fl) or "-", ", ".join(reran) or "-",
             "not here" if env_sha0 is None else ("unchanged" if env_same else "CHANGED")))


# ── «Обновить goAmnezia»: только просмотр ───────────────────────────────────────────────────────────────
DUMMY_PAT = "ga-live-not-a-token"
BRANCH_PROMPT = r"Enter — [^,]+, 0 — [^)]+\):\s*$"


def m_selfupdate_view():
    """«Обслуживание → Обновить goAmnezia» без обновления: ключ — заведомо не ключ (GitHub отвечает 401), поэтому ни
    загрузчик, ни файлы не скачиваются. Смотрим выбор ветки, какую ветку спросили у GitHub и что ничего не сменилось."""
    src0, v0, code0 = install_source(), code_version(), code_files()
    cur = src0.get("branch") or "?"
    runs = {}
    for label, key in (("1 (main)", "1"), ("Enter", "")):
        runs[label] = menu_run(["4", "6", key, "0", "0"], timeout=300, env_extra={"GOAMNEZIA_PAT": DUMMY_PAT},
                               extra_prompts=(BRANCH_PROMPT,))
    scr = runs["1 (main)"]["text"]
    has_main = re.search(r"1\) main — [^\n]*", scr)
    has_dev = re.search(r"2\) dev — [^\n]*", scr)
    pr = re.search(r"(Ветка|Branch) \(Enter — ([^,]+), 0 — [^)]+\):", scr)
    here = re.search(r"%s\) %s — [^\n]*(сервер стоит отсюда|installed from here)" % (
        {"main": "1", "dev": "2"}.get(cur, "3"), re.escape(cur)), scr)
    emit("self-update screen: branch choice", bool(has_main and has_dev and pr and here) and pr.group(2) == cur,
         "«%s»; «%s»; prompt default «%s»; current branch %s marked: %s" % (
             has_main.group(0).strip() if has_main else "no main line", has_dev.group(0).strip() if has_dev else "no dev line",
             pr.group(2) if pr else "?", cur, "yes" if here else "no"))
    for label, want in (("1 (main)", "main"), ("Enter", cur)):
        t = runs[label]["text"]
        asked = re.findall(r"(?:Спрашиваю GitHub, что лежит в ветке|Asking GitHub what branch) (\S+?)(?:…|\.\.\.|\s+holds)", t)
        refused = next((ln.strip() for ln in t.splitlines() if re.search(
            r"Загрузчик скачать не удалось|Ветки \S+ в репозитории нет|could not be downloaded|has no branch", ln)), "")
        switched = re.search(r"Смена ветки: |Branch change: ", t) is not None
        emit("self-update: choice %s asks GitHub about %s only and refuses without switching" % (label, want),
             asked == [want] and bool(refused) and not switched,
             "asked GitHub about: %s; refusal: «%s»; exit %s" % (", ".join(asked) or "nothing", refused[:110] or "-",
                                                                runs[label]["rc"]))
    same = install_source() == src0 and code_version() == v0 and code_files() == code0
    emit("self-update view changed nothing", same, ".install-source as before: %s (branch %s), code %s" % (
        install_source() == src0, install_source().get("branch"), code_version()))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "health"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if mode == "pre-reboot":
        m_pre_reboot()
    elif mode == "wait-boot":
        m_wait_boot(int(arg or 420))
    elif mode == "health":
        pre = jload(OPS + "/reboot-pre.json") if arg == "after-reboot" else None
        m_health(pre)
    elif mode == "diskfull":
        m_diskfull(arg)
    elif mode == "logrotate":
        m_logrotate()
    elif mode == "certrenew":
        m_certrenew()
    elif mode == "rollback":
        m_rollback(arg)
    elif mode == "upgrade":
        m_upgrade(arg, sys.argv[3] if len(sys.argv) > 3 else "", sys.argv[4] if len(sys.argv) > 4 else "")
    elif mode == "selfupdate-view":
        m_selfupdate_view()
    else:
        sys.exit("unknown mode %s" % mode)


if __name__ == "__main__":
    main()
