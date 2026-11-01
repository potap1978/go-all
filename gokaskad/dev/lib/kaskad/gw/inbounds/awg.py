"""AmneziaWG inbounds: three independent lines, like the three containers of goAmnezia pro.

    awg1  line 1.5 (or 1.0)  10.77.2.0/24  kgw-awg1   Keenetic before 5.1, old clients
    awg2  line 2.0           10.77.3.0/24  kgw-awg2   Keenetic 5.1+, MikroTik container, AmneziaVPN
    awg3  line 3.1           10.77.4.0/24  kgw-awg3   Amnezia apps

Core: amneziawg-go + awg 3.1 (bin/ of this repository; they serve lines 1.0–3.1). install() takes the
first pair that runs and understands 3.1: the egress copy (/etc/kaskad/egress/awg/, «Прокси для API
Telegram»), then /opt/kaskad/bin/; failing both it copies ``bin/{amneziawg-go,awg}-linux-<arch>`` of a
checkout on disk (``$KASKAD_AWG_ROOTS``, else ``$KASKAD_SRC_DIR``, this checkout, /opt/kaskad-pro,
/opt/gokaskad, /root/kaskad-pro) to /opt/kaskad/bin/.

Unit kaskad-gw-awgN.service: ``amneziawg-go -f kgw-awgN`` in the foreground; ExecStartPost waits for the
UAPI socket, ``awg setconf``, address .1/24, MTU of the server (gw/mtu.py: from the budget of the outer packet and
S4; the clients keep params MTU, 1280), link up.

Acceleration (gw/awgkmod.py, ``kaskadctl gw accel on``): awg1 and awg2 run on the interface of the kernel module
when it is on, the module is loaded for the running kernel and the config is safe for it (awgkmod.kernel_for) —
the unit is then oneshot (RemainAfterExit): ``ip link add kgw-awgN type amneziawg``, ``awg setconf`` (netlink:
no UAPI socket), address, MTU, up; ExecStopPost ``ip link delete`` (a module interface outlives any process, a
failed start leaves none). awg3 (3.1) — only on a module with the set of fixes of Kaskad (awgkmod.kernel_31; else
amneziawg-go, as before). The mode is decided where the
unit text is rendered; a change of mode is a change of the unit -> restart -> the interface is recreated (one
short reconnect). A module unit that does not start -> awgkmod.mark_failed and amneziawg-go at once.

Parameters (Jc…H4, I1, the 3.x fields) come from ``vendor.awgkeys.gen_params`` and live in the state
(``inbounds[kind].line`` and ``.params``) — the same keys as goAmnezia, so its keys and the Amnezia apps
fit. Server config = ``awgkeys.render_server_conf`` without Address/MTU (wg-quick keys ``awg setconf``
does not know). Issuance: ``vpn://`` (mandatory for 3.x: the Amnezia app drops the 3.x fields when it
imports a .conf), QR chunks of the Amnezia app, QR of the .conf, the .conf itself.

apply(): devices added or removed → ``awg syncconf`` (no session drop); [Interface] changed (parameters,
port, key) → restart: every client of the line needs a new key then anyway. On the module the live interface takes
only peers: syncconf reads ``kgw-awgN.sync.conf`` — the config without HeaderProtectionKey, I1–I5 and the rest of the
masking (awgkmod.sync_conf_text: writing the key under traffic hangs or panics a module without the fix of #253;
``awg set … i1`` on a live interface — #140); a change of the masking recreates the interface (one short reconnect).

Secrets: ``awgN.server.private``, ``dev.<id>.awgN.private``, ``dev.<id>.awgN.psk``.
"""

import os
import re
import secrets as _random
import shutil
import urllib.error
import urllib.request

from kaskad import ghkey, paths, snapshot, util
from kaskad import qr as qrfit
from kaskad.gw import awgkmod, issue_os, registry
from kaskad.gw import mtu as gwmtu
from kaskad.gw.inbounds import tunnel
from kaskad.gw.inbounds.base import InboundError
from kaskad.gw.inbounds.wg import WgFamily
from kaskad.i18n import server_lang, tr
from kaskad.vendor import awgkeys

CORE = ("amneziawg-go", "awg")
# What tells a 3.1 core from older ones (the egress check of Kaskad 2.13): the UAPI key of
# amneziawg-go and the config key of awg. A 1.x/2.0 core rejects a whole config with a 3.x field.
MARKERS = {"amneziawg-go": b"disable_cookies", "awg": b"DisableCookies"}
# Download of the core when nothing is on disk (the panel, the bot, the CLI and the menu alike): bin/ of the
# repository, main then test, with the GitHub key — the same files and the same key as install.sh egress_awg_fetch.
CORE_REPO = ghkey.REPO                      # AWG_REPO of the environment, else anten-ka/kaskad-pro
CORE_REFS = ghkey.REFS
CORE_MAX = 64 * 1024 * 1024                  # bytes per file: a bigger answer is not the core
UAPI_DIR = "/var/run/amneziawg"
PORT_RANGE = (30000, 60999)
AVOID_PORTS = (51820, 51821, 1194, 4443, 8443, 10808, 10851, 12345, 53, 443, 80)
APP_PLATFORMS = ("ios", "android", "androidtv", "windows", "macos")
ROUTERS = ("keenetic", "openwrt", "mikrotik", "tplink", "huawei", "xiaomi", "asus")


def _repo_roots():
    env = os.environ.get("KASKAD_AWG_ROOTS")
    if env is not None:
        return [r for r in env.split(":") if r]
    here = os.path.dirname(os.path.abspath(__file__))
    checkout = os.path.normpath(os.path.join(here, "..", "..", "..", ".."))
    roots = [os.environ.get("KASKAD_SRC_DIR", ""), checkout, "/opt/kaskad-pro", "/opt/gokaskad", "/root/kaskad-pro"]
    return [r for r in roots if r]


def _has_marker(path, marker):
    try:
        with open(path, "rb") as f:
            return marker in f.read()
    except OSError:
        return False


def _pair_ok(runner, d):
    """Both binaries are in ``d``, understand 3.1 and run here (a foreign architecture fails --version)."""
    for name in CORE:
        p = os.path.join(d, name)
        if not os.path.isfile(p) or not _has_marker(p, MARKERS[name]):
            return False
    return all(runner.run([os.path.join(d, name), "--version"]).ok for name in CORE)


def core_dirs():
    """Where an installed core may be, in the order install() prefers them."""
    return [os.path.join(paths.egress_dir(), "awg"), paths.bin_dir()]


def find_core(runner):
    for d in core_dirs():
        if _pair_ok(runner, d):
            return d
    return None


def find_source(runner, arch):
    for root in _repo_roots():
        b = os.path.join(root, "bin")
        src = {name: os.path.join(b, "%s-linux-%s" % (name, arch)) for name in CORE}
        if all(os.path.isfile(p) and _has_marker(p, MARKERS[n]) for n, p in src.items()):
            return src
    return None


def gh_token():
    """The GitHub key in the order of install.sh kaskad_gh_token (kaskad.ghkey): bot_state/system UPDATE_TOKEN,
    GITHUB_PAT of /etc/kaskad/config, GH_TOKEN / GITHUB_TOKEN, the key of goInstaller. '' when there is none.
    Never logged."""
    return ghkey.current()[0]


_marks = {}                 # path -> (signature, has the 3.1 marker): the panel asks on every view, a core is megabytes


def _marked(path, marker):
    sig = snapshot.signature(path)
    if sig is None:
        return False
    hit = _marks.get(path)
    if hit is None or hit[0] != sig:
        hit = (sig, _has_marker(path, marker))
        _marks[path] = hit
    return hit[1]


def core_on_disk():
    """A 3.1 core is on this disk: installed (core_dirs) or in bin/ of a checkout (either architecture). Runs
    nothing — the panel's reads ask it: the files and their markers only (no ``--version``, no ``uname``)."""
    for d in core_dirs():
        if all(_marked(os.path.join(d, n), MARKERS[n]) for n in CORE):
            return True
    for root in _repo_roots():
        b = os.path.join(root, "bin")
        for arch in ("amd64", "arm64"):
            if all(_marked(os.path.join(b, "%s-linux-%s" % (n, arch)), MARKERS[n]) for n in CORE):
                return True
    return False


def needs_key():
    """AmneziaWG cannot be installed for want of a GitHub key: no key to download the core, and no core on disk.
    The panel shows «Ввести ключ GitHub» right on the card by it (``needs_key`` of GET /api/gw/inbounds)."""
    return not gh_token() and not core_on_disk()


def _wanted(arch):
    return tr("gw_in_awg.core_files", arch=arch)


def fetch_core(runner, arch):
    """Download ``bin/{amneziawg-go,awg}-linux-<arch>`` (main, then test) with the GitHub key into the egress core
    directory /etc/kaskad/egress/awg — where the menu's egress_ensure_awg puts it too. -> the directory."""
    tok = gh_token()
    if not tok:
        raise InboundError(tr("gw_in_awg.err.no_key", arch=arch, files=_wanted(arch)),
                           data={"needs_key": True, "key": "none"})
    dst = os.path.join(paths.egress_dir(), "awg")
    os.makedirs(dst, 0o700, exist_ok=True)
    stage = os.path.join(dst, ".stage.%s" % _random.token_hex(4))
    os.mkdir(stage, 0o700)
    last, refused = tr("gw_in_awg.fetch.error"), False            # refused: GitHub said no to the key (the panel offers another one)
    try:
        for ref in CORE_REFS:
            got = {}
            for name in CORE:
                url = "https://raw.githubusercontent.com/%s/%s/bin/%s-linux-%s" % (CORE_REPO, ref, name, arch)
                req = urllib.request.Request(url, headers={"Authorization": "token " + tok, "User-Agent": "kaskad"})
                try:
                    with ghkey.open_github(req, 90) as r:            # the key stays with GitHub
                        data = r.read(CORE_MAX + 1)
                except urllib.error.HTTPError as e:
                    last, refused = tr("gw_in_awg.fetch.http", code=e.code), e.code in (401, 403, 404)
                    break
                except (urllib.error.URLError, OSError, ValueError):
                    last, refused = tr("gw_in_awg.fetch.offline"), False
                    break
                if not data or len(data) > CORE_MAX or MARKERS[name] not in data:
                    last, refused = tr("gw_in_awg.fetch.not_core", name=name, ref=ref), False
                    break
                path = os.path.join(stage, name)
                with open(path, "wb") as f:
                    f.write(data)
                os.chmod(path, 0o755)
                got[name] = path
            if len(got) < len(CORE):
                continue
            if not all(runner.run([got[n], "--version"]).ok for n in CORE):
                last, refused = tr("gw_in_awg.fetch.not_runs"), False
                continue
            for name in CORE:
                os.rename(got[name], os.path.join(dst, name))
            tunnel.log_tr("awg", "gw_in_awg.log.fetched", ref=ref, dir=dst)
            return dst
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    raise InboundError(tr("gw_in_awg.err.fetch", why=last), data={"needs_key": True, "key": "bad"} if refused else None)


def install_core(runner):
    """The core directory for the units: an existing 3.1 pair, else a copy of bin/ to /opt/kaskad/bin."""
    d = find_core(runner)
    if d:
        return d
    arch = tunnel.arch(runner)
    if arch is None:
        raise InboundError(tr("gw_in_awg.err.arch"))
    src = find_source(runner, arch)
    if src is None:
        return fetch_core(runner, arch)              # nothing on disk: GitHub with the key (panel, bot, CLI, menu)
    dst = paths.bin_dir()
    if not os.path.isdir(dst):
        os.makedirs(dst, 0o755)
    for name in CORE:
        tmp = os.path.join(dst, ".%s.%s" % (name, _random.token_hex(4)))
        try:
            shutil.copyfile(src[name], tmp)
            os.chmod(tmp, 0o755)
            os.rename(tmp, os.path.join(dst, name))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    if not _pair_ok(runner, dst):
        raise InboundError(tr("gw_in_awg.err.core_not_runs", dir=os.path.dirname(src["awg"])))
    return dst


def _iface_section(text):
    """The [Interface] part of a config (what syncconf cannot change on a running AmneziaWG line)."""
    head = []
    for line in (text or "").splitlines():
        if line.strip().lower() == "[peer]":
            break
        if line.strip() and not line.lstrip().startswith("#"):
            head.append(line.strip())
    return head


class AwgFamily(WgFamily):
    engine = "amneziawg-go"
    tool_name = "awg"
    lines = ("2.0",)           # allowed lines of this inbound, the first is the default
    container = "amnezia-awg2"

    port_random = True          # the port of a new inbound is drawn at the install (inbound_plan says «random»)

    def default_port(self):
        while True:
            p = PORT_RANGE[0] + _random.randbelow(PORT_RANGE[1] - PORT_RANGE[0] + 1)
            if p not in AVOID_PORTS:
                return p

    # ── line and parameters ──
    def line(self, st):
        cfg = self.cfg(st)
        line = str(cfg.get("line") or self.lines[0])
        if line not in self.lines:
            raise InboundError(tr("gw_in_awg.err.line", line=line, kind=self.kind, lines=", ".join(self.lines)))
        return line

    def params(self, st):
        return awgkeys.normalize_params(self.cfg(st).get("params"))

    def prepare_line(self, st, cfg):
        line = self.line(st)
        if cfg.get("line") != line:
            cfg["line"] = line
        params = cfg.get("params")
        if isinstance(params, dict) and params and not awgkeys.validate_params(line, params):
            return []
        if isinstance(params, dict) and params:
            filtered = awgkeys.line_filter(line, params)
            if not awgkeys.validate_params(line, filtered):
                cfg["params"] = filtered
                return [tr("gw_in_awg.made.params_fitted", line=line)]
        cfg["params"] = awgkeys.gen_params(line, budget=gwmtu.budget(st))     # S4 of 2.0 within the budget (§6.1)
        return [tr("gw_in_awg.made.params", line=line)]

    # ── core ──
    def core_dir(self, runner):
        """The core the unit runs (amneziawg-go of its ExecStart, or awg of its ``setconf`` line — the unit on
        the kernel module has no amneziawg-go), else the one install() would take."""
        for line in (util.read_text(tunnel.unit_file(self.unit)) or "").splitlines():
            if line.startswith("ExecStart=") and line.rstrip().endswith(" -f " + self.iface):
                return os.path.dirname(line[len("ExecStart="):].split()[0])
            tok = line.partition("=")[2].split() if line.startswith("Exec") else []
            if len(tok) >= 3 and os.path.basename(tok[0]) == "awg" and tok[1:3] == ["setconf", self.iface]:
                return os.path.dirname(tok[0])
        return find_core(runner) or paths.bin_dir()

    def tool(self, runner):
        return os.path.join(self.core_dir(runner), "awg")

    def unit_core(self):
        """What the installed unit runs: "kernel" (the module), "userspace" (amneziawg-go), None — no unit."""
        text = util.read_text(tunnel.unit_file(self.unit))
        if text is None:
            return None
        rx = r"^ExecStart=\S+ link add dev %s type %s$" % (re.escape(self.iface), awgkmod.NAME)
        return "kernel" if re.search(rx, text, re.M) else "userspace"

    def sync_path(self):
        """What ``awg syncconf`` gives a live interface of the module: the peers, not the masking."""
        return os.path.join(self.dir(), self.iface + ".sync.conf")

    def render(self, st, sec):
        files = WgFamily.render(self, st, sec)
        files[self.sync_path()] = (awgkmod.sync_conf_text(files[self.conf_path()][0]), 0o600)
        return files

    def syncconf(self, runner):
        """Peers onto the live interface: amneziawg-go takes the whole config (as before); the module — only the
        peers (sync_path), never HeaderProtectionKey or I1–I5 (#253, #140)."""
        conf = self.sync_path() if self.unit_core() == "kernel" else self.conf_path()
        res = runner.run([self.tool(runner), "syncconf", self.iface, conf])
        return None if res.ok else tunnel.cmd_error(res)

    def core_mode(self, runner):
        """The mode the unit should have now: "kernel" when acceleration may take this inbound (awgkmod.kernel_for:
        on, a kernel line, the rendered config safe for the module, the module loaded), else "userspace". Asks
        nothing while acceleration is off."""
        if self.kind not in awgkmod.KERNEL_KINDS:
            return "userspace"
        return "kernel" if awgkmod.kernel_for(runner, self.kind, util.read_text(self.conf_path())) else "userspace"

    # ── render ──
    def server_conf(self, st, sec, peers):
        priv = tunnel.sget(sec, self.server_key())
        if not priv:
            raise InboundError(tr("gw_in_wg.err.no_server_key", title=self.title))
        text = awgkeys.render_server_conf(self.line(st), {
            "params": self.params(st),
            "server": {"privkey": priv, "subnet": str(self.network(st)), "port": self.port(st)},
            "peers": [{"pubkey": p["pub"], "ip": p["ip"], "psk": p["psk"]} for p in peers],
        })
        body = []
        ids = [p["id"] for p in peers]
        for ln in text.splitlines():
            if re.match(r"^\s*(Address|MTU)\s*=", ln):
                continue
            if ln.strip() == "[Peer]" and ids:
                body.append("# device %s" % ids.pop(0))
            body.append(ln)
        head = "# Kaskad 3 gateway: AmneziaWG %s inbound %s (awg setconf). Written by kaskadctl, edits are lost." % (
            self.line(st), self.iface)
        return head + "\n" + "\n".join(body) + "\n"

    def unit_texts(self, runner, core_dir=None, mode=None):
        d = core_dir or self.core_dir(runner)
        go, awg = os.path.join(d, "amneziawg-go"), os.path.join(d, "awg")
        ip = tunnel.which(runner, "ip", "/usr/sbin/ip")
        conf = self.conf_path()
        if (mode or self.core_mode(runner)) == "kernel":
            return {self.unit: self._kernel_unit(ip, awg, conf)}
        sock = "%s/%s.sock" % (UAPI_DIR, self.iface)
        # "$$" is a literal "$" for the shell: systemd expands "$i" itself otherwise
        wait = "i=0; while [ ! -S %s ]; do i=$$((i+1)); [ $$i -ge 50 ] && exit 1; sleep 0.1; done" % sock
        text = "\n".join([
            "[Unit]",
            "Description=Kaskad 3 gateway: AmneziaWG inbound %s (%s)" % (self.kind, self.iface),
            "After=network-online.target kaskad-net.service",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            "EnvironmentFile=%s" % self.env_path(),
            "Environment=LOG_LEVEL=error",
            "ExecStartPre=-%s link delete dev %s" % (ip, self.iface),
            "ExecStart=%s -f %s" % (go, self.iface),
            "ExecStartPost=/bin/sh -c '%s'" % wait,
            "ExecStartPost=%s setconf %s %s" % (awg, self.iface, conf),
            "ExecStartPost=%s address replace ${ADDR} dev %s" % (ip, self.iface),
            "ExecStartPost=%s link set dev %s mtu ${MTU} up" % (ip, self.iface),
            "ExecReload=%s syncconf %s %s" % (awg, self.iface, conf),
            "ExecStopPost=-%s link delete dev %s" % (ip, self.iface),
            "Restart=on-failure",
            "RestartSec=3",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ])
        return {self.unit: text}

    def _kernel_unit(self, ip, awg, conf):
        """The inbound on the kernel module: no process — the interface is the module's; ExecStopPost removes it
        (also after a failed start: nothing half-configured stays)."""
        return "\n".join([
            "[Unit]",
            "Description=Kaskad 3 gateway: AmneziaWG inbound %s (%s, kernel module)" % (self.kind, self.iface),
            "After=network-online.target kaskad-net.service",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=oneshot",
            "RemainAfterExit=yes",
            "EnvironmentFile=%s" % self.env_path(),
            "ExecStartPre=-%s link delete dev %s" % (ip, self.iface),
            "ExecStart=%s link add dev %s type %s" % (ip, self.iface, awgkmod.NAME),
            "ExecStart=%s setconf %s %s" % (awg, self.iface, conf),
            "ExecStart=%s address replace ${ADDR} dev %s" % (ip, self.iface),
            "ExecStart=%s link set dev %s mtu ${MTU} up" % (ip, self.iface),
            "ExecReload=%s syncconf %s %s" % (awg, self.iface, self.sync_path()),
            "ExecStopPost=-%s link delete dev %s" % (ip, self.iface),
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ])

    def apply(self, st, sec, runner):
        """WgFamily.apply; a unit on the kernel module that does not run afterwards -> this inbound on amneziawg-go
        (awgkmod.mark_failed: until another kernel or ``gw accel on``) and applied again at once."""
        try:
            res, err = WgFamily.apply(self, st, sec, runner), None
        except InboundError as e:
            res, err = None, e
        if self.unit_core() == "kernel" and tunnel.wanted(st, self.kind) and not tunnel.is_active(runner, self.unit):
            # the reason is kept on disk (awgkmod state, its journal): in the language of the server
            awgkmod.mark_failed(runner, self.kind, err or tr("gw_in_awg.unit_failed", server_lang(), unit=self.unit))
            tunnel.log_tr(self.kind, "gw_in_awg.log.to_userspace")
            return WgFamily.apply(self, st, sec, runner)
        if err is not None:
            raise err
        return res

    def status(self, runner):
        out = WgFamily.status(self, runner)
        core = self.unit_core()
        if core:
            out["core"] = core
            warn = awgkmod.inbound_warnings(runner, self.kind, core)
            if warn:
                out["warnings"] = list(out.get("warnings") or []) + warn
        return out

    # ── interface of §3 ──
    def requirements(self, runner):
        arch = tunnel.arch(runner)
        why, warning, needs_core, no_key = "", "", False, False
        if arch is None:
            why = tr("gw_in_awg.why.arch")
        elif find_core(runner) is None and find_source(runner, arch) is None:
            # install() downloads it (fetch_core); the SSH menu fetches it before the plan (egress_ensure_awg)
            needs_core = True
            if gh_token():
                warning = tr("gw_in_awg.warn.will_fetch")
            else:
                why, no_key = tr("gw_in_awg.why.no_core"), True
        return {"kind": self.kind, "engine": self.engine, "packages": [], "modules": ["tun"],
                "ports": [{"proto": "udp", "port": self.default_port()}], "supported": not why, "why": why,
                "warning": warning, "needs_core": needs_core, "needs_key": no_key}

    def needs_key(self):
        """The panel's card: this line cannot be installed until a GitHub key is given (runs nothing)."""
        return needs_key()

    def install(self, runner):
        d = install_core(runner)
        tunnel.modprobe(runner, ["tun"])
        tunnel.ensure_dir(self.dir())
        tunnel.install_units(runner, self.unit_texts(runner, d))
        tunnel.log_tr(self.kind, "gw_in_awg.log.installed", dir=d)

    def needs_restart(self, old_conf, new_conf):
        return _iface_section(old_conf) != _iface_section(new_conf)

    # ── issuance ──
    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        p = issue_os.platform_of(dev, os)
        data = self.client_data(st, sec, dev)
        line, params = self.line(st), self.params(st)
        conf = awgkeys.render_conf(line, {
            "params": params,
            "client": {"ip": data["ip"], "privkey": data["priv"], "psk": data["psk"]},
            "server": {"pubkey": data["server_pub"], "endpoint_host": data["host"], "port": data["port"]},
            "dns": [data["dns"]], "mtu": str(data["mtu"]),
        })
        vpn = awgkeys.vpn_encode({
            "container": self.container, "line": line, "params": params, "conf_text": conf,
            "description": issue_os.app_label("AmneziaWG %s" % line, dev.get("name")),   # the name in AmneziaVPN / DefaultVPN
            "hostName": data["host"], "port": data["port"], "dns": [data["dns"]], "subnet": str(self.network(st)),
        })
        # one or two parts of one size (qr.part_size, 3.3.3 №38, owner: «не более 2 частей» — the standard app expects
        # 2): the app's own 850 bytes made 2 codes of version 24 and a short one; the window sizes its frame for them
        raw = awgkeys.b64url_decode(vpn[len("vpn://"):])
        chunks = awgkeys.qr_chunks_from_bytes(raw, chunk_size=qrfit.part_size(len(raw)))
        apps = issue_os.apps_note(p, line)
        lost = tr("gw_in_awg.conf_lost")
        vpn_rec = tunnel.record("vpn", tr("gw_in_awg.rec.vpn", line=line), vpn, None,
                                [n for n in (apps, tr("gw_in_awg.vpn_paste")) if n])
        qr_app = tunnel.record("qr", tr("gw_in_awg.rec.qr_app", line=line), chunks, None,
                               [tr("gw_in_awg.qr_chunks", n=len(chunks))] if len(chunks) > 1 else [])
        qr_conf = tunnel.record("qr", tr("gw_in_awg.rec.qr_conf", line=line), [conf], None,
                                [tr("gw_in_awg.qr_conf_note")])
        conf_notes = [lost] if line == "3.1" else []
        if p == "keenetic":
            conf_notes.append(tr("gw_in_awg.hint.keenetic"))
            if line == "3.1":
                conf_notes.append(tr("gw_in_awg.hint.keenetic_31"))
        elif p == "openwrt":
            conf_notes.append(tr("gw_in_awg.hint.openwrt"))
        elif p == "mikrotik":
            conf_notes.append(tr("gw_in_awg.hint.mikrotik"))
        elif p == "linux":
            conf_notes.append("sudo awg-quick up ./%s (amneziawg-tools%s)."
                              % (issue_os.conf_filename(self.kind, line, device=dev, platform_name=p), " 3.1" if line == "3.1" else ""))
        if apps and p in ROUTERS + ("linux",):
            conf_notes.append(apps)
        conf_rec = tunnel.record("conf", tr("gw_in_awg.rec.conf", line=line), conf, issue_os.conf_filename(self.kind, line, device=dev, platform_name=p),
                                 conf_notes)
        if p in APP_PLATFORMS:
            recs = [vpn_rec, qr_app, qr_conf, conf_rec]
        elif p in ROUTERS:
            recs = [conf_rec]
        elif p == "linux":
            recs = [conf_rec, vpn_rec]
        else:
            recs = [vpn_rec, conf_rec, qr_app, qr_conf]
        return tunnel.result(self.kind, recs)


class Awg1Inbound(AwgFamily):
    kind = "awg1"
    title = "AmneziaWG 1.5"
    lines = ("1.5", "1.0")
    container = "amnezia-awg1"


class Awg2Inbound(AwgFamily):
    kind = "awg2"
    title = "AmneziaWG 2.0"
    lines = ("2.0",)
    container = "amnezia-awg2"


class Awg3Inbound(AwgFamily):
    kind = "awg3"
    title = "AmneziaWG 3.1"
    lines = ("3.1",)
    container = "amnezia-awg3"


registry.register(Awg1Inbound)
registry.register(Awg2Inbound)
registry.register(Awg3Inbound)
