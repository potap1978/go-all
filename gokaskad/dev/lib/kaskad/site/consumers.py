"""The certificate of the domain handed to its consumers (docs/k3/SPEC-wave4.md §4.1 п. 2–3).

    install_copy(domain)     /etc/letsencrypt/live/<domain>/{fullchain,privkey}.pem -> /etc/kaskad/site/tls/current/
                             (one checked pair behind an atomically replaced ``current`` symlink, 0644/0600) ->
                             (changed, certinfo)
    drop_copy()              the copy away (the domain is cleared: every consumer is back on its self-signed one)
    reload(runner, st)       the consumers re-read: the panel (open mode and /p/), accel-ppp ``reload`` (SSTP, the
                             sessions stay), ocserv ``reload``, VLESS + TLS (a copy xray of the gateway reads and
                             re-reads by itself — no restart) -> [names of what was told]
    cert_installed(runner, lineage=None, force=False)    the deploy hook (``kaskadctl site cert-installed``)

Who takes what:
  * the panel in the open mode and the pages of people /p/ take the copy by themselves as soon as it is valid for
    the domain (kaskad.panel.conf ``panel_cert``: the running panel looks at it every 5 s, the same as at
    secrets.json); their addresses become ``https://<домен>:<порт>/…``;
  * SSTP (accel-ppp ``ssl-pemfile``/``ssl-keyfile``) and OpenConnect (ocserv ``server-cert``/``server-key``) — only
    after ``site use sstp|ocserv on`` (kaskad.site.model.tls_pair; the name in the certificate must match the
    address in the settings of a device);
  * IKEv2 stays on its own CA (4.1).

ocserv: every version of Debian 12/13 and Ubuntu 24.04/26.04 (1.1.6 … 1.4.x) re-reads ``server-cert`` and
``server-key`` on SIGHUP (NEWS 0.11.0: «Certificates and private keys are reloaded on server reload (SIGHUP)»;
doc/sample.config: «the server-cert, server-key … will be reloaded if the provided file changes, on server reload»),
and the unit's ``ExecReload`` sends SIGHUP: a renewal is a ``reload`` — sessions stay. The PATH of the certificate
is not reloadable (``[scope: vhost (non-reloadable)]``): switching the inbound to the domain and back is a restart
(the ocserv adapter does it when those lines of ocserv.conf change). A new key on a reload may fail the handshakes
of that very moment (sample.config) — new connections only, the clients retry.
"""

import os
import shutil
import ssl
import tempfile

from .. import state, util
from ..errors import KaskadError
from ..i18n import tr
from . import certinfo, model

PANEL_UNIT = "kaskad-panel.service"


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _write(path, data, mode):
    """A new pair directory is private until ``current`` names it, so no per-file rename is needed here."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, mode)


def _pair_dir(path):
    """Our generation directory behind a relative ``current`` symlink, or None for anything else."""
    if not path or os.path.isabs(path) or os.path.dirname(path):
        return None
    if not path.startswith(".pair-"):
        return None
    return os.path.join(model.tls_dir(), path)


def _current_pair():
    try:
        return _pair_dir(os.readlink(model.current_path()))
    except OSError:
        return None


def _remove_pair(path):
    if path:
        shutil.rmtree(path, ignore_errors=True)


def _remove_old_pairs(keep=None):
    """Only our hidden generation directories are disposable; a manually added TLS file is never touched here."""
    try:
        names = os.listdir(model.tls_dir())
    except OSError:
        return
    for name in names:
        path = _pair_dir(name)
        if path and path != keep:
            _remove_pair(path)


def _install_legacy_links():
    """Keep the old direct paths as relative aliases of the atomically selected pair.

    Versions before 4.0-B wrote the two files directly in ``tls/``.  An already generated accel-ppp or ocserv
    configuration may still name those paths until its next apply.  Once both aliases exist, replacing ``current``
    switches the old and new consumer paths together.  Each alias itself is replaced atomically too, so an interrupted
    upgrade never leaves a truncated PEM file there.
    """
    tls = model.tls_dir()
    for name in ("fullchain.pem", "privkey.pem"):
        dst = os.path.join(tls, name)
        target = os.path.join("current", name)
        try:
            if os.path.islink(dst) and os.readlink(dst) == target:
                continue
        except OSError:
            pass
        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix=".legacy-", dir=tls)
            tmp = os.path.join(tmpdir, name)
            os.symlink(target, tmp)
            os.replace(tmp, dst)
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)


def install_copy(domain):
    """See the module doc. KaskadError when certbot's files are missing or are not for ``domain``."""
    live = model.live_dir(domain)
    chain = _read(os.path.join(live, "fullchain.pem"))
    key = _read(os.path.join(live, "privkey.pem"))
    if not chain or not key:
        raise KaskadError(tr("site.consumers.no_files", path=live))
    try:
        info = certinfo.parse(chain.decode("ascii", "replace"))
    except ValueError as e:
        raise KaskadError(tr("site.consumers.unreadable", path=live, error=e))
    if not certinfo.covers(info, domain):
        raise KaskadError(tr("site.consumers.other_names", path=live, domain=domain,
                             names=", ".join(info["names"]) or tr("site.consumers.no_names")))
    util.ensure_dir(model.site_dir(), 0o755)
    util.ensure_dir(model.tls_dir(), 0o755)
    pair = None
    link = None
    old = _current_pair()
    try:
        pair = tempfile.mkdtemp(prefix=".pair-", dir=model.tls_dir())
        cert_path = os.path.join(pair, "fullchain.pem")
        key_path = os.path.join(pair, "privkey.pem")
        _write(cert_path, chain, 0o644)
        _write(key_path, key, 0o600)
        # It is not enough for the PEM to name the domain: reject a key from another certificate before replacing
        # the pair visible to the panel, accel-ppp and ocserv.
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert_path, key_path)
        os.chmod(pair, 0o755)
        if _read(model.fullchain_path()) == chain and _read(model.privkey_path()) == key:
            _install_legacy_links()
            return False, info
        # On an upgrade, first make any old absolute consumer paths follow the existing ``current`` pair.  The next
        # replacement of that one symlink then moves all four visible paths to the new checked pair at once.
        if old:
            _install_legacy_links()
        link = os.path.join(model.tls_dir(), ".current-%s" % os.path.basename(pair))
        os.symlink(os.path.basename(pair), link)
        os.replace(link, model.current_path())
        link = None
    except (OSError, ssl.SSLError) as e:
        raise KaskadError(tr("site.consumers.pair", error=e))
    finally:
        if link:
            try:
                os.unlink(link)
            except OSError:
                pass
        if pair and pair != _current_pair():
            _remove_pair(pair)
    _remove_pair(old)
    _remove_old_pairs(keep=pair)
    # First install has no old ``current`` to point the aliases at, so establish them after its atomic switch.
    _install_legacy_links()
    return True, info


def drop_copy():
    removed = False
    pair = _current_pair()
    try:
        os.unlink(model.current_path())
        removed = True
    except OSError:
        pass
    _remove_pair(pair)
    _remove_old_pairs()
    for p in (os.path.join(model.tls_dir(), "fullchain.pem"), os.path.join(model.tls_dir(), "privkey.pem")):
        try:
            os.unlink(p)
            removed = True
        except OSError:
            pass
    return removed


def _reload_panel(runner):
    """The running panel re-reads its settings and writes access.json (in the panel's own process — the API — a
    reload a moment later; elsewhere SIGHUP through systemd). Without a running panel access.json is written here."""
    from ..panel import conf
    try:
        from ..panel import server
        panel = server.PANEL
    except ImportError:
        panel = None
    if panel is not None:
        import threading
        threading.Timer(0.3, panel.reload).start()
        return True
    if conf.reload_service(runner):
        return True
    try:
        conf.write_access()
    except OSError:
        pass
    return False


def _unit_active(runner, unit):
    return runner.run(["systemctl", "is-active", unit]).out.strip() == "active"


def reload(runner, st):
    """The consumers re-read the certificate. -> [{"name": "panel"|"pages"|"sstp"|"ocserv", "action"}]."""
    from ..gw.inbounds import accel, ocserv
    from ..panel import conf
    out = []
    pub = conf.load()["public"]["on"]
    told = _reload_panel(runner)
    if pub:
        out.append({"name": "panel", "action": "reload" if told else "next start"})
        out.append({"name": "pages", "action": "reload" if told else "next start"})
    if model.use_for(st, "sstp") and _unit_active(runner, accel.UNIT):
        res = runner.run(["systemctl", "reload", accel.UNIT])
        out.append({"name": "sstp", "action": "reload" if res.ok else "reload failed"})
    if model.use_for(st, "ocserv") and _unit_active(runner, ocserv.UNIT):
        res = runner.run(["systemctl", "reload", ocserv.UNIT])
        out.append({"name": "ocserv", "action": "reload" if res.ok else "reload failed"})
    from . import front
    if front.tls_wanted(st):
        # VLESS + TLS on 443 (site.front): the copy xray reads; xray re-reads it by itself within the hour — no restart
        from ..gw import xray
        from ..gw.inbounds import vless
        got = vless.sync_tls(True)
        action = "copy" if got else "same"
        if "fullchain.pem" in got and "privkey.pem" not in got and _unit_active(runner, xray.UNIT):
            # the same key (certbot --reuse-key): xray re-reads the pair only when both differ — restart it once
            res = runner.run(["systemctl", "restart", xray.UNIT])
            action = "restart" if res.ok else "restart failed"
        out.append({"name": "vless", "action": action})
    got = _hy2(runner, st)
    if got:
        out.append(got)
    return out


def _hy2_due(st):
    """Hysteria2 is there and its certificate is not the one it should take now (``want_mode`` != ``cert_mode``)."""
    from ..gw import model as gm
    from ..gw import registry
    ad = registry.get("hy2")
    return gm.inbound(st, "hy2") is not None and ad is not None and hasattr(ad, "want_mode") and \
        ad.want_mode(st) != ad.cert_mode(st)


def _hy2(runner, st):
    """Hysteria2 (SPEC-stage1-hy2-inbound §3.4): its own copy of the domain's pair (tls-hy2/domain). The certificate it
    should take changed (a domain came, was cleared, ``cert self``) — the gateway applied again: the config and the
    links switch together (``prepare`` records the mode). The same mode — the copy renewed and xray restarted when it
    changed (not known yet whether xray re-reads the certificate of a QUIC inbound by itself — the stand says).
    -> {"name": "hy2", "action"} or None (no Hysteria2)."""
    from ..gw import model as gm
    from ..gw import registry
    rec = gm.inbound(st, "hy2")
    ad = registry.get("hy2")
    if rec is None or ad is None or not hasattr(ad, "want_mode"):
        return None
    from ..gw.inbounds import hy2 as hin
    if ad.want_mode(st) != ad.cert_mode(st):
        # the links given out name the other certificate (host, sni, insecure/pinSHA256): whom to give them again
        users = dict((u.get("id"), u.get("name")) for u in gm.users(st))
        rows = [{"device": d.get("id"), "name": d.get("name") or d.get("id"), "kind": "hy2",
                 "user_name": users.get(d.get("user")) or ""} for d in gm.devices_for(st, "hy2")]
        if state.held() or not gm.enabled(st):
            return {"name": "hy2", "action": "next apply", "reissue": rows}
        from ..gw import ops as gops
        try:
            gops._change(runner, lambda new, sec: None)
        except KaskadError as e:
            util.log_tr("site.consumers.log_hy2_failed", error=e)
            return {"name": "hy2", "action": "switch failed", "reissue": rows}
        return {"name": "hy2", "action": "switch", "reissue": rows}
    if ad.cert_mode(st) != "domain":
        return None
    got = hin.sync_tls("domain")
    from ..gw import xray
    if got and rec.get("enabled", True) is not False and _unit_active(runner, xray.UNIT):
        res = runner.run(["systemctl", "restart", xray.UNIT])
        return {"name": "hy2", "action": "restart" if res.ok else "restart failed"}
    return {"name": "hy2", "action": "copy" if got else "same"}


def lineage_ours(domain, lineage):
    """RENEWED_LINEAGE of certbot is /etc/letsencrypt/live/<name>: ours only for our domain."""
    if not lineage:
        return True
    return os.path.basename(os.path.normpath(lineage)) == domain


def cert_installed(runner, lineage=None, force=False):
    """The deploy hook: the copy for the consumers and their reload. Without a domain, or for a certificate of
    another name (``lineage``: RENEWED_LINEAGE), nothing is done. -> {"ok", "consumers", "changed", "skipped"?}."""
    st = state.load()
    domain = model.domain_of(st)
    if not domain:
        return {"ok": True, "consumers": [], "changed": False, "skipped": "no_domain"}
    if not lineage_ours(domain, lineage):
        return {"ok": True, "consumers": [], "changed": False, "skipped": "lineage"}
    changed, info = install_copy(domain)
    consumers = reload(runner, st) if (changed or force) else []
    if not (changed or force) and _hy2_due(st):
        # KT3-5: certbot's deploy hook copied the pair while the state did not name the certificate yet (``site cert
        # issue`` records «letsencrypt» after certbot) — Hysteria2 stayed on its self-signed one until some later full
        # apply. The copy is the same now, the mode Hysteria2 wants is not: switch it here, with whom to give it again
        got = _hy2(runner, st)
        consumers = [got] if got else []
    if changed:
        names = ", ".join(c["name"] for c in consumers)
        util.log_tr("site.consumers.log_handed" if names else "site.consumers.log_handed_panel", domain=domain,
                    until=info["not_after"][:10], names=names)
    return {"ok": True, "consumers": consumers, "changed": changed}
