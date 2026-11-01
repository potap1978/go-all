#!/usr/bin/env python3
"""kmod_server.py — серверная половина живой проверки kmod (tests/live/acceptance.py --checks kmod).

    kmod_server.py facts <человек> <профили через запятую> [<эпоха начала>]
    kmod_server.py iperf-up <профиль>      одноразовый контейнер iperf3 -s в сетевом пространстве профиля
    kmod_server.py iperf-down <профиль>
    kmod_server.py now                     эпоха на сервере (начало окна журнала ядра)

facts только смотрит и печатает одну строку JSON:
  profiles.<p>: container, running, engine (kernel | program | unknown по `ip -d link`, как ga_amn_engine продукта),
      engine_file (/opt/amnezia/awg/engine в контейнере), engine_block (в start.sh есть блок движка), server_fp (открытый
      ключ профиля), user_fp (ключ человека в users.json), mss {v4, v6} (сколько правил TCPMSS в mangle FORWARD; v6 —
      null, если ip6tables в контейнере нет), warp (отпечаток блока goWARP в start.sh или "none"), sh_n (start.sh
      проходит `sh -n` — так его проверяет goWARP)
  module: loaded (/sys/module/amneziawg), version, refcnt, dkms (строки `dkms status amneziawg`), hooks (наши файлы в
      /etc/kernel/*.d), modules_load (/etc/modules-load.d/amneziawg.conf есть)
  klog: bad — строки журнала ядра с эпохи начала: BUG, soft lockup, Oops, general protection fault, Call Trace,
      «blocked for more than» (journalctl -k; без journald — dmesg целиком), source — откуда читали
Отпечатки — 16 знаков sha256 от «ключ\\n», как в remote/kmod_client.sh: сверка «ключи те же» без самих ключей.

iperf-up/iperf-down: контейнер ga-kmod-iperf-<профиль> из образа профиля (в нём есть apk), в сетевом пространстве
профиля (--network container:<контейнер профиля>): сам контейнер профиля не меняется. После перезапуска профиля
пространство у него новое — сервер iperf3 ставится заново перед каждым замером.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

GA = os.environ.get("GOAMNEZIA_DIR", "/opt/goamnezia")
BAD = re.compile(r"BUG:|soft lockup|Oops|general protection fault|Call Trace|blocked for more than")
HOOK = "zz-goamnezia-amneziawg"
WARP_BEGIN, WARP_END = "# --- WARP-MANAGER BEGIN ---", "# --- WARP-MANAGER END ---"
ENGINE_BEGIN = "# --- goamnezia-engine BEGIN ---"


def sh(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=isinstance(cmd, str))
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 — одна сломанная команда не роняет весь ответ
        return 999, "", "%s: %s" % (type(e).__name__, e)


def fp(key):
    key = (key or "").strip()
    return hashlib.sha256((key + "\n").encode()).hexdigest()[:16] if key else ""


def h16(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def config():
    with open(os.path.join(GA, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def dexec(container, cmd, timeout=60):
    return sh(["docker", "exec", container, "sh", "-c", cmd], timeout=timeout)


def engine_of(container, iface):
    _, out, _ = dexec(container, "ip -d link show dev %s 2>/dev/null; [ -e /sys/class/net/%s/tun_flags ] && echo tun_flags=1; "
                                 "true" % (iface, iface))
    lines = [ln.strip() for ln in out.splitlines()]
    if any(re.match(r"(amneziawg|wireguard)(\s|$)", ln) for ln in lines):
        return "kernel"
    if any(re.match(r"tun(\s|$)", ln) or ln.startswith("tun_flags=") for ln in lines):
        return "program"
    return "unknown"


def profile_facts(name, p, pc, users):
    c, iface = pc.get("container") or "", pc.get("iface") or "awg0"
    r = {"container": c, "running": False}
    _, st, _ = sh(["docker", "inspect", "-f", "{{.State.Running}}", c])
    r["running"] = st.strip() == "true"
    peer = next((u.get("peers", {}).get(p) for u in users if u.get("name") == name), None) or {}
    r["user_fp"] = fp(peer.get("pubkey"))
    if not r["running"]:
        return r
    r["engine"] = engine_of(c, iface)
    _, out, _ = dexec(c, "cat /opt/amnezia/awg/engine 2>/dev/null; true")
    r["engine_file"] = out.strip()
    _, out, _ = dexec(c, "awg show %s public-key 2>/dev/null; true" % iface)
    r["server_fp"] = fp(out)
    _, v4, _ = dexec(c, "iptables -t mangle -S FORWARD 2>/dev/null | grep -c -- '-j TCPMSS'; true")
    _, v6, _ = dexec(c, "command -v ip6tables >/dev/null 2>&1 || { echo none; exit 0; }; "
                        "ip6tables -t mangle -S FORWARD 2>/dev/null | grep -c -- '-j TCPMSS'; true")
    r["mss"] = {"v4": int(v4.strip() or 0) if v4.strip().isdigit() else None,
                "v6": None if v6.strip() in ("", "none") or not v6.strip().isdigit() else int(v6.strip())}
    _, text, _ = dexec(c, "cat /opt/amnezia/start.sh 2>/dev/null; true")
    lines = text.splitlines()
    warp = []
    inside = False
    for ln in lines:
        if ln == WARP_BEGIN:
            inside = True
        if inside:
            warp.append(ln)
        if ln == WARP_END:
            inside = False
    r["warp"] = h16("\n".join(warp)) if warp else "none"
    r["engine_block"] = ENGINE_BEGIN in lines
    rc, _, _ = dexec(c, "sh -n /opt/amnezia/start.sh")
    r["sh_n"] = rc == 0
    return r


def module_facts():
    m = {"loaded": os.path.isdir("/sys/module/amneziawg")}
    for k in ("version", "refcnt"):
        try:
            with open("/sys/module/amneziawg/%s" % k, encoding="utf-8") as f:
                m[k] = f.read().strip()
        except OSError:
            m[k] = ""
    _, out, _ = sh("command -v dkms >/dev/null 2>&1 && dkms status amneziawg 2>/dev/null; true")
    m["dkms"] = [ln.strip() for ln in out.splitlines() if ln.strip()]
    m["hooks"] = [d for d in ("/etc/kernel/postinst.d", "/etc/kernel/header_postinst.d")
                  if os.path.exists(os.path.join(d, HOOK))]
    m["modules_load"] = os.path.exists("/etc/modules-load.d/amneziawg.conf")
    return m


def klog(since):
    rc, out, _ = sh(["journalctl", "-k", "--no-pager", "-q", "--since=@%d" % since], timeout=120)
    src = "journalctl -k"
    if rc != 0:
        _, out, _ = sh(["dmesg"], timeout=60)
        src = "dmesg"
    bad = [ln.strip()[:200] for ln in out.splitlines() if BAD.search(ln)]
    return {"bad": bad[:10], "count": len(bad), "source": src}


def facts(name, profiles, since):
    cfg = config()
    try:
        with open(os.path.join(GA, "users.json"), encoding="utf-8") as f:
            users = json.load(f).get("users") or []
    except (OSError, ValueError):
        users = []
    out = {"profiles": {}, "module": module_facts()}
    for p in profiles:
        pc = (cfg.get("profiles") or {}).get(p)
        out["profiles"][p] = profile_facts(name, p, pc, users) if pc else {"missing": True}
    if since:
        out["klog"] = klog(since)
    print(json.dumps(out, ensure_ascii=False))


def iperf(action, p):
    pc = (config().get("profiles") or {}).get(p) or {}
    c = pc.get("container") or ""
    name = "ga-kmod-iperf-%s" % p
    sh(["docker", "rm", "-f", name])
    if action == "down":
        print(json.dumps({"ok": True}))
        return
    _, img, _ = sh(["docker", "inspect", "-f", "{{.Config.Image}}", c])
    rc, _, err = sh(["docker", "run", "-d", "--name", name, "--network", "container:%s" % c, "--entrypoint", "sh",
                     img.strip(), "-c", "command -v iperf3 >/dev/null || apk add --no-cache iperf3 >/dev/null 2>&1; "
                                        "exec iperf3 -s"], timeout=120)
    if rc != 0:
        print(json.dumps({"ok": False, "why": "docker run: %s" % err.strip()[-160:]}))
        return
    for _ in range(30):
        time.sleep(2)
        rc, out, _ = sh(["docker", "exec", name, "sh", "-c", "pidof iperf3 || pgrep iperf3"])
        if rc == 0 and out.strip():
            print(json.dumps({"ok": True}))
            return
        _, st, _ = sh(["docker", "inspect", "-f", "{{.State.Running}}", name])
        if st.strip() != "true":
            break
    sh(["docker", "rm", "-f", name])
    print(json.dumps({"ok": False, "why": "iperf3 did not start (no apk repo reachable from the profile?)"}))


def main(argv):
    if len(argv) >= 3 and argv[0] == "facts":
        facts(argv[1], [p for p in argv[2].split(",") if p], int(argv[3]) if len(argv) > 3 and argv[3].isdigit() else 0)
    elif len(argv) == 2 and argv[0] in ("iperf-up", "iperf-down"):
        iperf(argv[0][6:], argv[1])
    elif argv[:1] == ["now"]:
        print(int(time.time()))
    else:
        sys.stderr.write(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
