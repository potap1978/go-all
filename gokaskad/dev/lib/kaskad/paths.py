"""Every filesystem path Kaskad 3 touches, in one place.

Tests set ``KASKAD_ROOT`` to a temporary directory: every path below is then resolved
inside it (``/etc/kaskad/state.json`` -> ``$KASKAD_ROOT/etc/kaskad/state.json``).
Production leaves it empty. Always call the functions; never cache the strings at import
time, so a test can change ``KASKAD_ROOT`` between cases.
"""

import os


def root():
    return os.environ.get("KASKAD_ROOT", "").rstrip("/")


def P(path):
    """Resolve an absolute production path under KASKAD_ROOT."""
    if not path.startswith("/"):
        raise ValueError("absolute path expected: %r" % path)
    return root() + path


# /etc/kaskad — state and the 2.x files (2.x files keep their formats; Kaskad 3 only reads them).
def etc():            return P("/etc/kaskad")
def state():          return P("/etc/kaskad/state.json")
def config():         return P("/etc/kaskad/config")            # 2.x KEY="value" (BOT_TOKEN, GITHUB_PAT, ...)
def aliases():        return P("/etc/kaskad/aliases")           # 2.x IP=name|note|country|isp (2.1: IP=name)
def monitors():       return P("/etc/kaskad/monitors")          # 2.x <ip>.conf
def bot_state():      return P("/etc/kaskad/bot_state")
def egress_dir():     return P("/etc/kaskad/egress")
def egress_tofu():    return P("/etc/kaskad/egress/tofu-pins.json")   # pins taken on the update to 3.3.4 (egress_parse «tofu»)
def egress_tofu_done(): return P("/etc/kaskad/egress/.tofu-done")     # the first run of «tofu» on this node is done
def egress_up():      return P("/etc/kaskad/egress/upstream.txt")
def egress_up_ru():   return P("/etc/kaskad/egress/upstream-ru.txt")   # the keys of the Russian groups (T15): never the bot's
def net_dir():        return P("/etc/kaskad/net")
def nft_file():       return P("/etc/kaskad/net/kaskad.nft")    # full table, applied at boot by kaskad-net.service
def counters_file():  return P("/etc/kaskad/net/counters.json")
def egress_parser():  return P("/etc/kaskad/egress/egress_parse.py")   # written by install.sh egress_ensure_parser
def egress_xray():    return P("/etc/kaskad/egress/xray")             # xray of «Прокси для API Telegram»
def egress_awg_tool(): return P("/etc/kaskad/egress/awg/awg")         # awg of the egress legs kawgN

# The gateway (wave 2): secrets apart from state.json, configs of the core, runtime data.
def secrets_file():   return P("/etc/kaskad/secrets.json")      # 0600, only through kaskad.secrets
def gw_dir():         return P("/etc/kaskad/gw")                # dnsmasq.conf, xray.json, exits.json, <kind>/...
def gw_run_dir():     return P("/run/kaskad/gw")                # leases of hooks, sessions of the collector

# Wave 3: live snapshot of the collector, the web panel, the rules lists (docs/k3/SPEC-wave3.md).
def live_json():      return P("/run/kaskad/live.json")         # written by kaskad-gw-collect, read by panel and bot
def live_want():      return P("/run/kaskad/live.want")         # beacon: a panel SSE client wants 2 s updates
def watch_json():     return P("/run/kaskad/watch.json")        # the last report of the minute watchdog (net check)
def update_cache():   return P("/etc/kaskad/update.json")       # the last update check (menu 11, update check)
def panel_dir():      return P("/etc/kaskad/panel")             # password.json, appearance.json, layout.json, tls/
def panel_run_dir():  return P("/run/kaskad/panel")             # status.json of the running panel
def rules_dir():      return P("/var/lib/kaskad/rules")         # the downloaded lists (last good kept)

# Stage 1 (docs/k3/SPEC-stage1-exits.md §2.5, §2.7, §3.4, §3.5, §5.7): the data of the collector. It alone writes
# pins.json, traffic.json, metrics.db and the xray decision; the unit of probes writes probe-results.json.
def gw_var_dir():     return P("/var/lib/kaskad/gw")            # pins.json, traffic.json, probe-results.json, xray-seq
def pins_json():      return P("/var/lib/kaskad/gw/pins.json")  # the pins of devices by group (gw.pins, §3.4)
def traffic_json():   return P("/var/lib/kaskad/gw/traffic.json")   # the counters of the quota period (gw.pins, §3.5)
def metrics_db():     return P("/var/lib/kaskad/metrics.db")    # the history for charts, sqlite (gw.metrics, §3.5)
# The checks of the node (SPEC-node-score-schedule-share.md §3.2, NS-S9): the runner of a check writes its file into the
# queue, the collector alone carries it into metrics.db (bench_run, bench, bench_epoch); a broken file goes to bad/.
def bench_dir():      return P("/var/lib/kaskad/bench")         # results.json, the queue spool/, skip-push.json
def bench_spool():    return P("/var/lib/kaskad/bench/spool")   # <id>.json, epoch-<ts>-tariff.json (bench.spool)
def probe_results():  return P("/var/lib/kaskad/gw/probe-results.json")   # results of the unit of probes (§5.7)
def xray_decision():  return P("/etc/kaskad/gw/xray.decision")  # {"fp", "seq"} of the applied render of xray (§2.7)
def xray_seq():       return P("/var/lib/kaskad/gw/xray-seq")   # the number of the last render taken (§2.5)
def xray_rejected():  return P("/run/kaskad/xray.rejected")     # {"hash", "at", "error"}: xray -test said no (§2.7)
def xray_apply_lock(): return P("/run/kaskad/xray-apply.lock")  # flock around the changes of xray (§2.5)
def vless_mode_lock(): return P("/run/kaskad/vless-mode.lock")   # flock of a transition of the VLESS mode (Н1)

# /opt/kaskad — code (install.sh extracts the embedded payload here).
def opt():            return P("/opt/kaskad")
def lib_current():    return P("/opt/kaskad/lib/current")
def bin_dir():        return P("/opt/kaskad/bin")
def kaskadctl():      return P("/opt/kaskad/bin/kaskadctl")
def script_copy():    return P("/opt/kaskad/bin/gokaskad")      # copy of the current 3.x install.sh; systemd units run it
def command():        return P("/usr/local/bin/gokaskad")       # the user's command (goInstaller looks here)
def mcp_command():    return "/usr/local/bin/kaskad-mcp"         # the MCP server for AI agents (the forced command of
                                                                 # an agent's key: the path sshd runs, never under ROOT)
def authorized_keys(): return P("/root/.ssh/authorized_keys")   # keys of the AI agents (kaskad.mcp.keys) live here

# Runtime, logs, backups, systemd, persisted iptables.
def run_dir():        return P("/run/kaskad")
def lock():           return P("/run/kaskad/state.lock")
def log():            return P("/var/log/kaskad.log")
def backups():        return P("/var/backups/kaskad")
def systemd_dir():    return P("/etc/systemd/system")
def rules_v4():       return P("/etc/iptables/rules.v4")        # Debian/Ubuntu netfilter-persistent
def rules_sysconfig(): return P("/etc/sysconfig/iptables")      # RHEL family iptables-services
def sysctl_conf():    return P("/etc/sysctl.conf")
def sysctl_dir():     return P("/etc/sysctl.d")
def ufw_default():    return P("/etc/default/ufw")
def policy_rc_d():    return P("/usr/sbin/policy-rc.d")         # apt: 101 = do not start services on install

# systemd-networkd: its drop-in that keeps it off our ip rules (install.sh writes the same file).
def networkd_dropin(): return P("/etc/systemd/networkd.conf.d/90-kaskad.conf")
def networkd_bins():  return (P("/usr/lib/systemd/systemd-networkd"), P("/lib/systemd/systemd-networkd"))

# apt/dpkg locks (kaskad.aptlock only asks who holds them, never takes them) and /proc for the holder's name.
def apt_locks():
    return tuple(P(p) for p in ("/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock",
                                "/var/cache/apt/archives/lock", "/var/lib/apt/lists/lock"))
def proc(pid, name):  return P("/proc/%d/%s" % (int(pid), name))
def apt_install_lock(): return P("/run/kaskad/apt.lock")       # one install of packages by Kaskad at a time

# Wave 4 (docs/k3/SPEC-wave4.md §5): the masquerade site and its catalog of templates.
def site_root():      return P("/var/www/kaskad-site")          # web root of the site (nginx serves it, 4.0-C)
def site_backup_dir(): return P("/var/backups/kaskad/site")     # the previous site, one copy (site.templates)
def assets_dir():     return P("/opt/kaskad/assets")            # templates_catalog.json (fetched on demand)
def site_tpl_health(): return P("/var/lib/kaskad/site/tpl-health.json")   # «жив ли шаблон» (site.tplhealth), 0600

# 3.3.7.15, полоса Г (SPEC-R1 F14, F7): «Разложить заново» и безопасное применение с откатом.
def gw_regroup_dir(): return P("/run/kaskad/gw/regroup")       # F14: a request per group <gid> (0600), the collector takes it
def gw_pending_apply(): return P("/var/lib/kaskad/gw/pending-apply.json")   # F7: the live wait for a confirmation (0600)
def gw_pending_lock(): return P("/run/kaskad/gw/pending.lock")  # F7: one flock of guard/confirm/disarm/expire (С-2)
def gw_pending_events(): return P("/var/lib/kaskad/gw/pending-events")    # F7: events of other processes for the feed
