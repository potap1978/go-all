"""Shared pieces of the tunnel inbounds (work package 5): WireGuard, AmneziaWG, OpenVPN, OpenConnect,
SOCKS5. Not an adapter itself — registers nothing.

Every call into the gateway core (work package 4, docs/k3/SPEC-wave2-gateway.md §7) goes through this
module, so the adapters depend on one small surface:

    kaskad.secrets      set(key, value), new_password()
    kaskad.gw.addr      subnet(kind), allocate(st, device, kind)
    kaskad.gw.model     devices_for(st, kind), route_of(st, device)
    kaskad.gw.sys       apt_install(runner, pkgs), write_unit(name, text), modprobe(runner, mods) -> failed,
                        ufw_allow(runner, port, proto, comment), ufw_delete(runner, port, proto, comment)
                        (port None: a rule for the whole protocol — ESP, GRE)

Secrets are read from the ``sec`` mapping the core passes to render/apply/issue; a secret created here
goes to ``kaskad.secrets.set`` AND into ``sec``, so render() in the same run sees it.

State the adapters write (the caller holds ``state.transaction()``): ``inbounds[kind]`` fields ``pub``,
``params``, ``line``, ``port``, ``subnet`` when missing, and ``device["ips"][addr_kind]`` through
``addr.allocate``. Clients connect to ``st["gateway"]["host"]`` (public IPv4 or domain of the node).

Other rules shared by the adapters:
  * files: written atomically and only when the text or mode differ; directories an adapter owns
    (OpenVPN ccd, ocserv per-user configs) lose files nothing renders any more;
  * units: only ``kaskad-gw-<kind>*.service``; a package unit (openvpn.service, ocserv.service) is
    disabled and masked only when Kaskad installed that package (``/etc/kaskad/gw/packages.json``) —
    a server may already run its own OpenVPN or ocserv; uninstall unmasks it again;
  * UFW: a port of a running inbound is allowed once — ``ufw_allow`` returning truthy (UFW active, rule
    added) is remembered in ``<dir>/ufw.json``; a port refused while UFW was inactive is tried again on
    the next apply; remembered ports are deleted when they change, the inbound stops or is removed.
    F2 (finding 5): ufw.json also keeps the ports the inbound WANTS and the signature of UFW's own files
    (/etc/ufw/user.rules, user6.rules, ufw.conf: mtime and size). When the files changed since (``ufw reset``,
    a rule deleted by hand, UFW enabled or disabled) the remembered ports are checked against ``ufw status``
    instead of being trusted; the watchdog (``ufw_check``, from ``gw check --repair``) adds the wanted ports
    UFW lacks — UFW enabled after the gateway («защищу потом») no longer closes every inbound for good.
"""

import json
import os
import re
import sys
import time

from kaskad import paths, util
from kaskad import secrets
from kaskad.errors import KaskadError
from kaskad.gw import addr, model
from kaskad.gw import sys as gwsys
from kaskad.gw.inbounds.base import InboundError
from kaskad.gw.inbounds.texts import TrMap
from kaskad.i18n import fmt_datetime, tr

GW_DIR = "/etc/kaskad/gw"
PACKAGES_FILE = GW_DIR + "/packages.json"
UFW_FILE = "ufw.json"
UFW_FILES = ("/etc/ufw/user.rules", "/etc/ufw/user6.rules", "/etc/ufw/ufw.conf")
UFW_CHECK_EVERY = 300               # s: the watchdog asks ``ufw status`` at most this often while UFW's files stay
DEFAULT_DNS = "10.77.0.1"
# Logins go into file names (ocserv per-user configs) and into "login:*:hash" lines.
_LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def now():
    return time.time()


_now = time.time            # the clock of ufw_check (tests patch it)


# ── state ──────────────────────────────────────────────────────────────────

def gateway(st):
    g = st.get("gateway") if isinstance(st, dict) else None
    return g if isinstance(g, dict) else {}


def inbound_cfg(st, kind):
    cfg = (gateway(st).get("inbounds") or {}).get(kind)
    return cfg if isinstance(cfg, dict) else None


def require_cfg(st, kind, title):
    cfg = inbound_cfg(st, kind)
    if cfg is None:
        raise InboundError(tr("gw_in.err.not_installed", title=title, kind=kind))
    return cfg


def wanted(st, kind):
    """The inbound should run: it is installed and enabled, and the gateway is on."""
    cfg = inbound_cfg(st, kind)
    return cfg is not None and cfg.get("enabled", True) is not False \
        and gateway(st).get("enabled", True) is not False


def dns(st):
    return str((gateway(st).get("net") or {}).get("dns") or DEFAULT_DNS)


def host(st):
    """The address clients connect to: public IPv4 or domain of the node."""
    h = str(gateway(st).get("host") or "").strip()
    if not h:
        raise InboundError(tr("gw_in.err.no_host"))
    return h


def hostport(h, port):
    return ("[%s]:%d" % (h, port)) if ":" in h else ("%s:%d" % (h, port))


def port_value(cfg, key, default):
    try:
        p = int(cfg.get(key) or 0)
    except (TypeError, ValueError):
        p = 0
    return p if 1 <= p <= 65535 else default


TLS_VERSIONS = ("1.2-1.3", "1.3", "1.2")    # 3.3.7.15 F4: ``inbounds.<kind>.tls.versions`` (VLESS TLS, SSTP, ocserv)
TLS_DEFAULT = TLS_VERSIONS[0]


def tls_versions(cfg):
    """The TLS versions an inbound accepts (F4): ``cfg["tls"]["versions"]``; no field, not a dict or a value this
    version does not know — "1.2-1.3", as before 3.3.7.15 (written strictly, read softly: a downgrade never breaks)."""
    t = (cfg or {}).get("tls")
    v = t.get("versions") if isinstance(t, dict) else None
    return v if isinstance(v, str) and v in TLS_VERSIONS else TLS_DEFAULT


def subnet(cfg, addr_kind):
    """Subnet of an inbound: from its state record (wg, awg*), else from the core (OpenVPN, ocserv)."""
    s = cfg.get("subnet") if cfg else None
    return str(s) if s else str(addr.subnet(addr_kind))


# ── devices ────────────────────────────────────────────────────────────────

def devices(st, kind):
    """Devices that use this inbound now (model.devices_for), without disabled devices or users;
    sorted by id so configs come out the same on every run."""
    users = {}
    for u in gateway(st).get("users") or []:
        if isinstance(u, dict):
            users[u.get("id")] = u
    out = []
    for d in model.devices_for(st, kind) or []:
        if not isinstance(d, dict) or not d.get("id") or d.get("enabled", True) is False:
            continue
        u = users.get(d.get("user"))
        if u is not None and u.get("enabled", True) is False:
            continue
        out.append(d)
    return sorted(out, key=lambda d: str(d["id"]))


def find_device(st, device):
    """The device record of the state: by id or login, or for a dict — the state's record with its id
    (a copy taken before prepare() would miss the addresses it allocated)."""
    by_dict = isinstance(device, dict)
    want = device.get("id") if by_dict else device
    for d in gateway(st).get("devices") or []:
        if isinstance(d, dict) and want and (d.get("id") == want or (not by_dict and d.get("login") == want)):
            return d
    if by_dict:
        return device
    raise InboundError(tr("gw_in.err.no_device", device=device))


def device_name(device):
    return str(device.get("name") or device.get("id") or "")


def route_of(st, device):
    """The route of a device (device → user → routes.default), as the core resolves it."""
    return model.route_of(st, device)


def device_ip(device, addr_kind):
    return (device.get("ips") or {}).get(addr_kind)


def ensure_ip(st, device, addr_kind):
    """Address of the device on an inbound; a new one from the core allocator if it has none.
    -> (ip, created)."""
    ip = device_ip(device, addr_kind)
    if ip:
        return ip, False
    ip = addr.allocate(st, device, addr_kind)
    ips = device.setdefault("ips", {})
    if ip and not ips.get(addr_kind):
        ips[addr_kind] = ip
    if not ips.get(addr_kind):
        raise InboundError(tr("gw_in.err.no_free_addr", device=device_name(device)))
    return ips[addr_kind], True


def login(device):
    """Login of a device for password inbounds, or None if it cannot be used safely."""
    v = str(device.get("login") or device.get("id") or "")
    return v if _LOGIN_RE.match(v) else None


def safe_id(device):
    """The device id can be a file name and a certificate CN as it is (OpenVPN ccd)."""
    return bool(_LOGIN_RE.match(str(device.get("id") or "")))


# ── secrets ────────────────────────────────────────────────────────────────

def sget(sec, key):
    try:
        v = sec.get(key) if sec is not None else None
    except AttributeError:
        v = None
    return v if isinstance(v, str) and v else None


def sput(sec, key, value):
    """A new secret: into secrets.json at once and into ``sec``. A ``kaskad.secrets.Store`` gets it through its
    ``set`` — a key deleted earlier in the same change (``gw device reset``) and made again here must not be
    deleted again when the core saves the store."""
    secrets.set(key, value)
    if sec is None:
        return
    if callable(getattr(sec, "delete_prefix", None)) and callable(getattr(sec, "set", None)):
        sec.set(key, value)
        return
    try:
        sec[key] = value
    except TypeError:
        pass


# Inbounds whose login is the device login and whose password is the device's shared one (``dev.<id>.password``):
# all of them run it inside TLS or IPsec. PPTP (MS-CHAPv2 without TLS, breakable) and SOCKS5 (the password in
# plain text) have secrets of their own — a password caught there opens nothing else.
SHARED_PASSWORD_KINDS = ("l2tp", "sstp", "ikev2", "ocserv")


def shared_password_key(device_id):
    return "dev.%s.password" % device_id


def ensure_secret(sec, key, n=14):
    """A password of its own under ``key`` (letters and digits, as the shared one). -> (pw, created)."""
    pw = sget(sec, key)
    if pw:
        return pw, False
    pw = secrets.new_password(n)
    sput(sec, key, pw)
    return pw, True


def ensure_password(sec, device):
    """The device password shared by the password inbounds of SHARED_PASSWORD_KINDS (``dev.<id>.password``).
    -> (pw, created)."""
    return ensure_secret(sec, shared_password_key(device["id"]))


# ── files ──────────────────────────────────────────────────────────────────

def kind_dir(name):
    return paths.P(GW_DIR + "/" + name)


def mkdirs(path):
    """Create a directory that is not ours alone (/etc/kaskad/gw) if missing; never change its mode."""
    if not os.path.isdir(path):
        os.makedirs(path, 0o700)


def ensure_dir(path):
    """An adapter's own directory: created if missing, always 0700."""
    mkdirs(os.path.dirname(path))
    util.ensure_dir(path, 0o700)


def _mode(path):
    try:
        return os.stat(path).st_mode & 0o7777
    except OSError:
        return None


def write_files(files):
    """Write ``{path: (text, mode)}`` atomically, only what differs. -> sorted changed paths."""
    changed = []
    for p in sorted(files):
        text, mode = files[p]
        if util.read_text(p) == text and _mode(p) == mode:
            continue
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            os.makedirs(d, 0o700)
        util.atomic_write(p, text, mode)
        changed.append(p)
    return changed


def prune(directory, keep):
    """Remove files of an adapter-owned directory that are not in ``keep`` (paths). -> removed names."""
    removed = []
    if not os.path.isdir(directory):
        return removed
    keep = set(keep)
    for n in sorted(os.listdir(directory)):
        p = os.path.join(directory, n)
        if p in keep or not os.path.isfile(p):
            continue
        try:
            os.unlink(p)
            removed.append(n)
        except OSError:
            pass
    return removed


def read_json(path, default):
    raw = util.read_text(path)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def remove_tree(directory):
    """Delete an adapter directory (configs and keys of a removed inbound). Never fails."""
    if not os.path.isdir(directory):
        return
    for d, dirs, files in os.walk(directory, topdown=False):
        for n in files:
            try:
                os.unlink(os.path.join(d, n))
            except OSError:
                pass
        for n in dirs:
            try:
                os.rmdir(os.path.join(d, n))
            except OSError:
                pass
    try:
        os.rmdir(directory)
    except OSError:
        pass


# ── commands, systemd ──────────────────────────────────────────────────────

# Lines that say nothing about why a command failed: systemctl's notes on enable/disable, the plugin
# list strongSwan 6 prints on every swanctl call (Ubuntu 26.04 builds most plugins into libstrongswan).
_NOISE = re.compile(r"^(Created symlink |Removed |Synchronizing state of |Executing: |"
                    r"plugin '[^']*': failed to load|agent plugin requires )")
_FAILED = re.compile(r"fail|error|invalid|refus|denied|not found|cannot|can't|unable", re.I)


def cmd_error(res):
    """The line of a failed command's output that says why (stderr, then stdout; noise skipped),
    for a message, without secrets."""
    lines = [ln.strip() for ln in (res.err or "").splitlines() + (res.out or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not _NOISE.match(ln)]
    pick = next((ln for ln in lines if _FAILED.search(ln)), lines[0] if lines else None)
    return util.redact(pick[:300]) if pick else tr("gw_in.rc", rc=res.rc)


def which(runner, name, fallback):
    return runner.which(name) or fallback


def systemctl(runner, *args):
    res = runner.run(["systemctl"] + list(args))
    if not res.ok:
        raise InboundError("systemctl %s: %s" % (" ".join(args), cmd_error(res)))
    return res


def is_active(runner, unit):
    return runner.run(["systemctl", "is-active", unit]).out.strip() == "active"


def is_enabled(runner, unit):
    res = runner.run(["systemctl", "is-enabled", unit])
    return res.ok and res.out.strip() == "enabled"


def unit_file(name):
    return os.path.join(paths.systemd_dir(), name)


def unit_installed(name):
    return os.path.exists(unit_file(name))


def install_units(runner, texts):
    """Write our units (kaskad.gw.sys.write_unit) and reload systemd."""
    for name in sorted(texts):
        gwsys.write_unit(name, texts[name])
    systemctl(runner, "daemon-reload")


def refresh_units(runner, texts):
    """Rewrite INSTALLED units whose text differs from what this version renders (an update changed
    the unit: a new config path, new options) and reload systemd. Units that are not installed are
    left to install(). Returns the names rewritten — the caller restarts its service then (stand,
    2026-10-04: the WireGuard config moved to /etc/wireguard, the old unit kept the old path)."""
    names = []
    for name in sorted(texts):
        if unit_installed(name) and util.read_text(unit_file(name)) != texts[name]:
            gwsys.write_unit(name, texts[name])
            names.append(name)
    if names:
        systemctl(runner, "daemon-reload")
    return names


# Строки юнита, смена которых не требует перезапуска службы: daemon-reload достаточно (ExecReload берётся при
# следующем reload, Description — только подпись).
_SOFT_UNIT = re.compile(r"^(ExecReload|Description)=")


def unit_needs_restart(old, new):
    """A rewritten unit needs a restart unless only soft lines (ExecReload=, Description=) changed. No old text
    (unit just installed) -> True."""
    if old is None or new is None:
        return True
    def hard(text):
        return [ln.rstrip() for ln in text.splitlines() if ln.strip() and not _SOFT_UNIT.match(ln.strip())]
    return hard(old) != hard(new)


# ── перезапуски входов (T-N5 3.2.19: каждый стоит клиентам AmneziaWG 15–25 с) ─────────────────────────────
RESTARTS_FILE = "restarts.json"
RESTART_WHY = TrMap({                # why -> words in the language of the moment (gw/ifwatch.py reads it too)
    "mtu": "gw_in.restart_why.mtu",
    "unit": "gw_in.restart_why.unit",
    "addr": "gw_in.restart_why.addr",
    "params": "gw_in.restart_why.params",
    "syncconf": "gw_in.restart_why.syncconf",
    "engine": "gw_in.restart_why.engine",
})


def restarts_path():
    return os.path.join(paths.gw_dir(), RESTARTS_FILE)


def who():
    """Who acts, for the journal (T-M6-qs330b №8: «кто перезапустил вход при загрузке»): the command of this process
    without its options («kaskadctl gw apply», «kaskadctl gw collect») and the systemd unit it runs in
    (/proc/self/cgroup: kaskad-gw-collect.service, kaskad-net-watch.service, a session of SSH). Never fails."""
    words = []
    for a in sys.argv[:4]:
        a = os.path.basename(str(a))
        if not a or a.startswith("-"):
            break
        words.append(a)
    unit = ""
    for ln in (util.read_text(paths.P("/proc/self/cgroup")) or "").splitlines():
        name = ln.split(":", 2)[-1].rstrip("/").rsplit("/", 1)[-1]
        if name.endswith((".service", ".scope")):
            unit = name
            break
    cmd = " ".join(words) or "?"
    return "%s (%s)" % (cmd, unit) if unit else cmd


def note_restart(kind, why, mtu_was=None, mtu_now=None, by=None):
    """Remember the last restart of an inbound and its reason: /etc/kaskad/gw/restarts.json {kind: {"at", "why",
    "mtu_was", "mtu_now", "by"}} (0600; gw status — «последний перезапуск входа», the summary of an update) and a line
    in the journal with who restarted it (``who``). Never fails."""
    by = by or who()
    rec = {"at": int(now()), "why": why, "by": by}
    if mtu_was is not None and mtu_now is not None and mtu_was != mtu_now:
        rec["mtu_was"], rec["mtu_now"] = int(mtu_was), int(mtu_now)
    with util.journal_lang():
        log(kind, tr("gw_in.log.restart", why=RESTART_WHY.get(why, why),
                     mtu=(" %d → %d" % (rec["mtu_was"], rec["mtu_now"])) if "mtu_now" in rec else "", by=by))
    try:
        doc = read_json(restarts_path(), {})
        if not isinstance(doc, dict):
            doc = {}
        doc[kind] = rec
        util.atomic_write(restarts_path(), json.dumps(doc, indent=2, sort_keys=True) + "\n", 0o600)
    except (OSError, ValueError):
        pass


RESTART_GROUP = 300        # s: restarts this close to the latest one are one action (an engine switch restarts every inbound)


def _kind_order(k):
    m = re.match(r"([a-z]+)(\d*)$", k)
    return (m.group(1), int(m.group(2) or 0)) if m else (k, 0)


def last_restart(kinds=None):
    """The latest recorded restart among ``kinds`` (all when None): {"kind", "at", "why", …, "kinds", "group"} or None.
    ``group`` — every inbound restarted in the same action (within RESTART_GROUP s of the latest; T-N5 3.2.19-r2, Н-3:
    after an engine switch awg1–awg3 were restarted, and gw status named only one of them), ``kinds`` — their names."""
    doc = read_json(restarts_path(), {})
    recs = []
    for k, v in (doc.items() if isinstance(doc, dict) else []):
        if kinds is not None and k not in kinds or not isinstance(v, dict) or not isinstance(v.get("at"), int):
            continue
        recs.append(dict(v, kind=k))
    if not recs:
        return None
    best = max(recs, key=lambda r: (r["at"], _kind_order(r["kind"])))
    group = sorted((r for r in recs if r["at"] >= best["at"] - RESTART_GROUP), key=lambda r: _kind_order(r["kind"]))
    return dict(best, kinds=[r["kind"] for r in group], group=group)


def _why_text(rec, with_mtu=True):
    why = RESTART_WHY.get(rec.get("why"), rec.get("why") or "?")
    if with_mtu and rec.get("why") == "mtu" and rec.get("mtu_now"):
        why += " %s → %s" % (rec.get("mtu_was"), rec["mtu_now"])
    return why


def restart_text(rec):
    """«awg2 — 07.10, 23:06, причина: MTU 1216 → 1184» for gw status; several inbounds of one action — all of them:
    «awg1, awg2, awg3 — 08.10, 01:16, причина: смена движка (модуль ядра / программа)»."""
    if not rec:
        return ""
    group = rec.get("group") or [rec]
    when = fmt_datetime(rec["at"])
    names = ", ".join(r["kind"] for r in group)
    whys = []                                                 # one reason per kind, in the order of the kinds
    for r in group:
        t = _why_text(r)
        if t not in whys:
            whys.append(t)
    if len(whys) == 1:
        return tr("gw_in.restart.single", names=names, when=when, why=whys[0])
    # different reasons (MTU of each inbound its own, or an MTU and an engine switch): each with its inbounds
    parts = []
    for t in whys:
        parts.append("%s (%s)" % (t, ", ".join(r["kind"] for r in group if _why_text(r) == t)))
    return tr("gw_in.restart.several", names=names, when=when, whys="; ".join(parts))


def remove_units(runner, names):
    """Stop, disable and delete our units. Never fails because a unit is already gone."""
    removed = False
    for name in names:
        if unit_installed(name):
            runner.run(["systemctl", "disable", "--now", name])
            try:
                os.unlink(unit_file(name))
                removed = True
            except OSError:
                pass
    if removed:
        runner.run(["systemctl", "daemon-reload"])


def run_unit(runner, unit, title):
    """The unit must run: start it if it does not, make sure it starts at boot. -> True if started."""
    if not unit_installed(unit):
        raise InboundError(tr("gw_in.err.no_unit", title=title, unit=unit))
    if is_active(runner, unit):
        if not is_enabled(runner, unit):
            _enable(runner, unit)
        return False
    _enable(runner, unit, now=True)
    return True


def _enable(runner, unit, now=False):
    """``systemctl enable --no-reload [--now]`` and a daemon-reload only when it made a link (as kaskad.gw.sys.enable;
    the contract of the adapters stays write_unit/apt_install/…): a bare enable reloads systemd every time, even for
    an enabled unit — «Reloading…» in the journal on every apply (T-M6-qs330b №14)."""
    res = systemctl(runner, "enable", "--no-reload", *((["--now"] if now else []) + [unit]))
    if "Created symlink" in (res.err or "") + (res.out or ""):
        systemctl(runner, "daemon-reload")


def stop_unit(runner, unit):
    """The unit must not run (inbound or gateway switched off). -> True if something was stopped."""
    if not unit_installed(unit):
        return False
    if is_active(runner, unit) or is_enabled(runner, unit):
        systemctl(runner, "disable", "--now", unit)
        return True
    return False


# ── packages ───────────────────────────────────────────────────────────────

def pkg_installed(runner, pkg):
    res = runner.run(["dpkg-query", "-W", "-f=${Status}", pkg])
    return res.ok and res.out.strip().endswith(" installed")


def install_packages(runner, pkgs, distro_units=None):
    """Install the missing packages (kaskad.gw.sys.apt_install: package services do not start), then
    disable and mask the package units of packages Kaskad installed. -> {"installed", "masked"}."""
    path = paths.P(PACKAGES_FILE)
    rec = read_json(path, {})
    if not isinstance(rec, dict):
        rec = {}
    before = json.dumps(rec, sort_keys=True)
    missing = [p for p in pkgs if not pkg_installed(runner, p)]
    if missing:
        gwsys.apt_install(runner, missing)
        for p in missing:
            rec.setdefault(p, {})["by_kaskad"] = True
    masked = []
    for pkg in sorted(distro_units or {}):
        info = rec.get(pkg)
        if not isinstance(info, dict) or not info.get("by_kaskad"):
            continue
        done = info.setdefault("masked", [])
        for unit in distro_units[pkg]:
            if unit in done:
                continue
            runner.run(["systemctl", "disable", "--now", unit])      # a unit the package lacks: fine
            systemctl(runner, "mask", unit)
            done.append(unit)
            masked.append(unit)
    if json.dumps(rec, sort_keys=True) != before:
        mkdirs(os.path.dirname(path))
        util.atomic_write(path, json.dumps(rec, indent=2, sort_keys=True) + "\n", 0o600)
    return {"installed": missing, "masked": masked}


def record_packages(pkgs, why):
    """Packages Kaskad installed itself outside install_packages (the toolchain and the headers of the kernel module,
    gw/awgkmod.py) — into packages.json with ``by_kaskad``: the full removal (menu 15) names them in its line «Пакеты,
    которые ставил Kaskad» (T-N5 3.2.19: it named only wireguard-tools, ≈200 МБ of the module stayed unnamed). A record
    of a package already there is kept. Never fails."""
    path = paths.P(PACKAGES_FILE)
    try:
        rec = read_json(path, {})
        if not isinstance(rec, dict):
            rec = {}
        new = [p for p in pkgs or [] if p not in rec]
        if not new:
            return []
        for p in new:
            rec[p] = {"by_kaskad": True, "for": why}
        mkdirs(os.path.dirname(path))
        util.atomic_write(path, json.dumps(rec, indent=2, sort_keys=True) + "\n", 0o600)
        return new
    except (OSError, ValueError):
        return []


def unmask_packages(runner, pkgs):
    """Give the package units back (uninstall). They stay disabled. -> unmasked units."""
    path = paths.P(PACKAGES_FILE)
    rec = read_json(path, {})
    if not isinstance(rec, dict):
        return []
    out = []
    for pkg in pkgs:
        info = rec.get(pkg)
        if not isinstance(info, dict):
            continue
        for unit in info.get("masked") or []:
            runner.run(["systemctl", "unmask", unit])
            out.append(unit)
        info["masked"] = []
    if out:
        util.atomic_write(path, json.dumps(rec, indent=2, sort_keys=True) + "\n", 0o600)
    return out


def modprobe(runner, mods):
    """Load kernel modules (kaskad.gw.sys.modprobe). -> the modules that failed to load."""
    return list(gwsys.modprobe(runner, list(mods)) or [])


def module_available(runner, name):
    """A kernel module can be loaded (or is built in / loaded already)."""
    if runner.run(["modinfo", "-n", name]).ok:
        return True
    return os.path.isdir(paths.P("/sys/module/" + name))


def arch(runner):
    """amd64 / arm64 / None for other CPUs."""
    m = runner.run(["uname", "-m"]).out.strip()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(m)


# ── UFW ────────────────────────────────────────────────────────────────────

def _ufw_item(port, proto):
    """(port, proto) of a UFW rule; port None (or 0) is a whole protocol — ESP, GRE (``ufw allow proto esp``); a range
    «20000:20099» (Hysteria2 port hopping) is ONE rule — the string as UFW writes it."""
    if port in (None, 0, "0", ""):
        return (None, str(proto))
    if isinstance(port, str) and ":" in port:
        lo, hi = (int(x) for x in port.split(":", 1))
        if not 1 <= lo <= hi <= 65535:
            raise ValueError(port)
        return ("%d:%d" % (lo, hi), str(proto))
    return (int(port), str(proto))


def _ufw_key(item):
    p = item[0]
    first = int(p.split(":", 1)[0]) if isinstance(p, str) else (p or 0)
    return (p is None, first, isinstance(p, str), item[1])   # ports first, whole protocols after them


def _ufw_items(raw):
    out = []
    for item in raw or []:
        try:
            out.append(_ufw_item(item[0], item[1]))
        except (TypeError, ValueError, IndexError):
            continue
    return sorted(set(out), key=_ufw_key)


def ufw_signature():
    """[mtime_ns, size] (None when missing) of UFW's own files: every ``ufw allow/delete/reset/enable/disable``
    rewrites one of them. Read without a fork."""
    out = []
    for f in UFW_FILES:
        try:
            s = os.stat(paths.P(f))
            out.append([int(s.st_mtime_ns), int(s.st_size)])
        except OSError:
            out.append(None)
    return out


def _ufw_record(path, kind):
    """ufw.json: {"kind", "want": [(port, proto)] or None (unknown: a file of 3.1.0), "open": [...], "sig"}."""
    doc = read_json(path, None)
    if isinstance(doc, list):                                   # 3.1.0: only the confirmed ports
        return {"kind": kind, "want": None, "open": _ufw_items(doc), "sig": None}
    if not isinstance(doc, dict):
        return {"kind": kind, "want": None, "open": [], "sig": None}
    want = doc.get("want")
    return {"kind": str(doc.get("kind") or kind), "want": _ufw_items(want) if isinstance(want, list) else None,
            "open": _ufw_items(doc.get("open")), "sig": doc.get("sig")}


def _spec(item):
    """How ``ufw status`` prints the rule of (port, proto): "51820/udp", "Anywhere/esp" for a whole protocol."""
    port, proto = item
    if isinstance(port, str):
        return "%s/%s" % (port, proto)                          # a range: «20000:20099/udp»
    return ("%d/%s" % (port, proto)) if port is not None else "Anywhere/%s" % proto


def ufw_live(runner):
    """What UFW really has now — one ``ufw status``: None when UFW is not there or not active, else {"allow": set of
    specs allowed from anywhere, "deny": set of specs denied or rejected} (IPv4 lines; v6 twins skipped)."""
    if not runner.which("ufw"):
        return None
    res = runner.run(["ufw", "status"])
    lines = (res.out or "").splitlines() if res.ok else []
    if not lines or lines[0].strip() != "Status: active":
        return None
    out = {"allow": set(), "deny": set()}
    for line in lines[1:]:
        head = line.split("#", 1)[0]
        tok = head.split()
        if len(tok) < 3 or "(v6)" in line or tok[0] in ("To", "--"):
            continue
        action, rest = tok[1].upper(), tok[2:]
        if rest[0].upper() in ("IN", "OUT", "FWD"):         # «ALLOW IN» (status verbose, rules with a direction)
            if rest[0].upper() != "IN":
                continue
            rest = rest[1:]
        # From of a whole-protocol rule is «Anywhere/esp», «Anywhere/gre» (``ufw allow proto esp from any to any``)
        if action == "ALLOW" and rest and rest[0].split("/", 1)[0] == "Anywhere":
            out["allow"].add(tok[0])
        elif action in ("DENY", "REJECT"):
            out["deny"].add(tok[0])
    return out


def _write_ufw(path, directory, rec):
    doc = {"kind": rec["kind"], "want": [list(x) for x in rec["want"] or []],
           "open": [list(x) for x in rec["open"]], "sig": rec["sig"]}
    if read_json(path, None) != json.loads(json.dumps(doc)) and (doc["want"] or doc["open"] or os.path.isdir(directory)):
        ensure_dir(directory)
        util.atomic_write(path, json.dumps(doc, sort_keys=True) + "\n", 0o600)


def sync_ufw(runner, directory, kind, want):
    """UFW follows ``want`` = [(port, proto)]: rules an earlier apply added and that are not wanted any
    more are deleted; wanted ports not added yet are allowed. Only ports ``ufw_allow`` confirmed (a truthy
    return: UFW active, rule in place) are remembered, so UFW is touched only on changes (``ufw`` takes
    ~0.5 s a call on 1 vCPU) and a port refused while UFW was inactive is tried again on the next apply.
    A port None is a rule for the whole protocol (ESP of IPsec, GRE of PPTP).

    F2: the remembered ports are trusted only while UFW's files are as they were after the last sync
    (``ufw_signature``); otherwise one ``ufw status`` says which of them are really there (``ufw reset``, a rule
    deleted by hand, UFW switched off). ``want`` itself is kept for the watchdog (``ufw_check``)."""
    want = _ufw_items(want)
    path = os.path.join(directory, UFW_FILE)
    rec = _ufw_record(path, kind)
    have = rec["open"]
    if have and rec["sig"] != ufw_signature():
        live = ufw_live(runner)
        have = [x for x in have if live is not None and _spec(x) in live["allow"]]
    comment = "kaskad-gw %s" % kind
    opened = []
    for port, proto in have:
        if (port, proto) in want:
            opened.append((port, proto))
        else:
            gwsys.ufw_delete(runner, port, proto, comment)
    for port, proto in want:
        if (port, proto) not in have and gwsys.ufw_allow(runner, port, proto, comment):
            opened.append((port, proto))
    _write_ufw(path, directory, {"kind": kind, "want": want, "open": sorted(opened, key=_ufw_key),
                                 "sig": ufw_signature()})


def close_ufw(runner, directory, kind):
    sync_ufw(runner, directory, kind, [])


def _ufw_stamp():
    return os.path.join(paths.gw_run_dir(), "ufw-check.json")


def ufw_records():
    """[(directory, record)] of every ufw.json under /etc/kaskad/gw (``_ufw_record``; a file of 3.1.0 wants what it
    had opened)."""
    base = paths.P(GW_DIR)
    out = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for n in names:
        d = os.path.join(base, n)
        path = os.path.join(d, UFW_FILE)
        if os.path.isfile(path):
            rec = _ufw_record(path, n)
            if rec["want"] is None:
                rec["want"] = list(rec["open"])
            out.append((d, rec))
    return out


def _title(kind):
    """The name of the protocol in a problem for people (the panel, the SSH menu): «51820/udp (WireGuard)»."""
    from kaskad.gw import registry
    return registry.title(kind)


def ufw_check(runner, repair=False, log=None, now=None):
    """The watchdog of the inbounds' UFW rules (``gw check``; F2, finding 5): UFW active -> every port an inbound
    wants (its ufw.json) must be allowed in ``ufw status``; missing ones are added with ``repair``. A port the
    owner denies on purpose (DENY/REJECT of the same rule) is reported, never fought. Cheap: nothing runs while
    UFW's files are as at the last look and it was less than UFW_CHECK_EVERY ago (a few stat calls); no UFW or UFW
    off — nothing. Returns problems ``{"kind": "gw_ufw", "detail", "repaired", "error"?}``."""
    if not runner.which("ufw"):
        return []
    log = log or (lambda kind, text: util.log_action(text))
    now = _now() if now is None else now
    sig = ufw_signature()
    stamp = read_json(_ufw_stamp(), {})
    if isinstance(stamp, dict) and stamp.get("sig") == sig and 0 <= now - float(stamp.get("at") or 0) < UFW_CHECK_EVERY:
        return []
    live = ufw_live(runner)
    probs, missing, denied = [], [], []
    if live is not None:
        for directory, rec in ufw_records():
            for item in rec["want"]:
                spec = _spec(item)
                if spec in live["deny"]:
                    denied.append("%s (%s)" % (spec, _title(rec["kind"])))
                elif spec not in live["allow"]:
                    missing.append((directory, rec, item))
    if denied:
        probs.append({"kind": "gw_ufw", "repaired": False,
                      "detail": tr("gw_in.ufw.denied", ports=", ".join(denied))})
    if missing:
        p = {"kind": "gw_ufw", "repaired": False,
             "detail": tr("gw_in.ufw.missing", ports=", ".join("%s (%s)" % (_spec(item), _title(rec["kind"]))
                                                              for _, rec, item in missing))}
        probs.append(p)
        if repair:
            try:
                done, ok = {}, True
                for directory, rec, item in missing:
                    if gwsys.ufw_allow(runner, item[0], item[1], "kaskad-gw %s" % rec["kind"]):
                        rec["open"] = sorted(set(rec["open"]) | set([item]), key=_ufw_key)
                    else:
                        ok = False                               # UFW went off meanwhile: next look
                    done[directory] = rec
                for directory, rec in done.items():
                    rec["sig"] = ufw_signature()
                    _write_ufw(os.path.join(directory, UFW_FILE), directory, rec)
                p["repaired"] = ok
                if ok:
                    with util.journal_lang():
                        log("gw_ufw", tr("gw_in.log.ufw_repaired", detail=tr("gw_in.ufw.missing", ports=", ".join(
                            "%s (%s)" % (_spec(item), rec["kind"]) for _, rec, item in missing))))
            except (KaskadError, InboundError) as e:
                p["error"] = str(e)
    if repair or not missing:
        try:
            util.atomic_write(_ufw_stamp(), json.dumps({"sig": ufw_signature(), "at": now}) + "\n", 0o600)
        except OSError:
            pass
    return probs


# ── issuance and logs ──────────────────────────────────────────────────────

def record(fmt, title, body, filename=None, notes=None):
    """One entry of an issuance (§4): format qr|conf|vpn|ovpn|fields|guide|link. ``body`` is a string;
    for "qr" — a list of strings, one QR code each; for "fields" — a list of [label, value] pairs."""
    return {"format": fmt, "title": title, "body": body, "filename": filename, "notes": list(notes or [])}


def result(kind, records):
    """issue() result: the preferred entry's fields on top, every entry (preferred first) in "items"."""
    out = dict(records[0])
    out["inbound"] = kind
    out["items"] = records
    return out


def log(kind, text):
    util.log_action("gw %s: %s" % (kind, text))


def log_tr(kind, key, **kw):
    """``log`` of a catalog text in the language of the server (the journal). Arguments that are words of the catalog
    too: ``with util.journal_lang(): log(kind, tr(...))`` around the whole line."""
    with util.journal_lang():
        log(kind, tr(key, **kw))
