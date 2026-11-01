"""The IP certificate: issue, renewal, status, removal (docs/k3/SPEC-setup.md §1).

    issue(runner, ip=None, staging=False, force=False, progress=None)
        -> {"ok": True, "issued": bool, "kept": bool, "cert": status(), "via": "standalone" | "webroot", "text"}
        Refusal (reason port80 | challenge | limit | net | unsupported | profile | private | openssl | server) —
        the contract ``{"ok": false, "reason", "text", "retry_after"?}``; meta.json keeps the last try either way.
    tick(runner)          the timer (every 12 h): renew when needed and due, quietly; respects ``retry_after``
    needed()              the panel is open from the internet (with a domain too: the address keeps its certificate by
                          SNI — kaskad.panel.server.sni_callback)
    status(runner=None)   store.status + needed + the timer + the address it is for
    remove(runner)        the timer and the files away

The address is the public one people type (gateway.host, else what kaskad.panel.conf.detect_addresses finds) — an
IP, not a name; a private address (1:1 NAT without the public one known, a LAN) gets no certificate (Let's Encrypt
issues only for public ones). Production unless ``staging``; ``KASKAD_ACME_DIRECTORY`` points the client elsewhere
(a test CA such as pebble on the stand).
"""

import ipaddress
import os
import shutil
import time

from .. import paths, state, sysrun, util
from ..errors import KaskadError
from ..i18n import fmt_datetime, tr
from . import acme, crypto as kcrypto, http01, store
from .http01 import Refusal

UNIT = "kaskad-ipcert.service"
TIMER = "kaskad-ipcert.timer"
_LAZY = {"LIFETIME_TEXT": "ipcert.lifetime"}       # «≈ 6 дней»: setup.web imports it

_now = time.time


def __getattr__(name):
    """LIFETIME_TEXT in the language of the moment (PEP 562): a text of the module is not fixed at import."""
    if name in _LAZY:
        return tr(_LAZY[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def directory_url(staging):
    env = os.environ.get("KASKAD_ACME_DIRECTORY")
    if env:
        return env
    return acme.LE_STAGING if staging else acme.LE_PROD


def public_ip(runner):
    """The address the certificate is for: the host of the open mode / gateway (when it is an IP), else detected."""
    from ..panel import conf
    cfg = conf.load()
    for v in (cfg["public"].get("cert_host"), cfg["public"].get("host")):
        ip = store.norm_ip(v)
        if ip:
            return ip
    try:
        from ..gw import model
        ip = store.norm_ip(model.public_host(state.load()))
        if ip:
            return ip
    except KaskadError:
        pass
    _, host = conf.detect_addresses(runner)
    return store.norm_ip(host)


def check_ip(ip):
    if not ip:
        raise Refusal("private", tr("ipcert.err.no_ip"))
    a = ipaddress.ip_address(ip)
    if not a.is_global:
        raise Refusal("private", tr("ipcert.err.private", ip=ip))
    return ip


def needed():
    """Is an IP certificate of use now: the panel listens from the internet. A domain certificate does not end it —
    the open mode serves the IP one to clients without SNI or with an address (https://<IP>:<порт>/: the wizard
    opened by IP, old links of /p/), the domain's one to the domain's name (kaskad.panel.server.sni_callback)."""
    from ..panel import conf
    cfg = conf.load()
    return bool(cfg["public"]["on"])


def _say(progress, item, st, detail=""):
    if progress is not None:
        progress(item, st, detail)


def issue(runner=None, ip=None, staging=False, force=False, progress=None, crypto=None, transport=None,
          responder=None, email=""):
    runner = runner or sysrun.default_runner()
    crypto = crypto or kcrypto.Openssl(runner)
    try:
        ip = check_ip(store.norm_ip(ip) if ip else public_ip(runner))
    except Refusal as e:
        store.note_try(False, e.reason, e.text)
        raise
    cur = store.status(ip)
    if not force and cur["state"] == "valid" and bool(cur["staging"]) == bool(staging):
        _say(progress, "cert", "ok", tr("ipcert.progress.left", hours=int(cur["hours_left"])))
        return {"ok": True, "issued": False, "kept": True, "cert": cur, "via": None,
                "text": tr("ipcert.kept", ip=ip, hours=int(cur["hours_left"]))}
    if hasattr(crypto, "available") and not crypto.available():
        e = Refusal("openssl", tr("ipcert.err.no_openssl"))
        store.note_try(False, e.reason, e.text)
        raise e
    last = (store.meta().get("last_try") or {})
    wait = acme.epoch(last.get("retry_after") or "")
    if not force and last.get("reason") == "limit" and wait and wait > _now():
        raise Refusal("limit", last.get("text") or tr("ipcert.err.limit_short"), last.get("retry_after"))
    try:
        res = _issue(runner, ip, staging, progress, crypto, transport, responder, email)
    except acme.AcmeError as e:
        text = _text(e, ip)
        store.note_try(False, e.reason, text, e.retry_after)
        util.log_tr("ipcert.log.refused", reason=e.reason, ip=ip, detail=e.detail or e.text)
        raise Refusal(e.reason, text, e.retry_after)
    except Refusal as e:
        store.note_try(False, e.reason, e.text, e.retry_after)
        util.log_tr("ipcert.log.not_issued", ip=ip, reason=e.reason)
        raise
    except KaskadError as e:
        store.note_try(False, "server", str(e))
        raise Refusal("server", str(e))
    store.note_try(True)
    return res


def _text(e, ip):
    if e.reason == "limit":
        when = fmt_datetime(e.retry_after, utc=True, now=_now()) + " UTC" if e.retry_after else None
        return tr("ipcert.err.limit_when", ip=ip, when=when) if when else tr("ipcert.err.limit", ip=ip)
    if e.reason == "challenge":
        return tr("ipcert.err.challenge", ip=ip)
    if e.reason == "profile":
        return tr("ipcert.err.profile", profile=acme.PROFILE)
    return e.text


def _issue(runner, ip, staging, progress, crypto, transport, responder, email):
    util.ensure_dir(store.base_dir(), 0o700)
    util.ensure_dir(store.work_dir(), 0o700)
    akey = store.account_key(staging)
    if not os.path.isfile(akey):
        _say(progress, "account", "wait", tr("ipcert.progress.account_new"))
        crypto.gen_rsa(akey)
    client = acme.Client(directory_url(staging), crypto, akey, **({"transport": transport} if transport else {}))
    _say(progress, "account", "wait", tr("ipcert.progress.contact"))
    profiles = client.profiles()
    if profiles and acme.PROFILE not in profiles:
        raise acme.AcmeError("profile", tr("ipcert.err.no_profile", profile=acme.PROFILE))
    client.register(email)
    _say(progress, "account", "ok", tr("ipcert.progress.account_ok"))
    resp = responder if responder is not None else http01.choose(runner)
    via = resp.kind
    _say(progress, "check", "wait", tr("ipcert.progress.check_webroot" if via == "webroot"
                                       else "ipcert.progress.check_standalone"))
    resp.start()
    try:
        order_url, order = client.new_order(ip, acme.PROFILE)
        for az in order.get("authorizations") or []:
            got = client.http01(az)
            if got is None:
                continue
            ch, token, key_auth = got
            resp.put(token, key_auth)
            client.answer(ch["url"])
            client.poll(az, during=tr("ipcert.during.port80"))
    finally:
        resp.stop()
    _say(progress, "check", "ok", tr("ipcert.progress.port80_ok"))
    _say(progress, "cert", "wait", tr("ipcert.progress.issuing"))
    keyp = os.path.join(store.work_dir(), "privkey.pem")
    if os.path.exists(keyp):
        os.unlink(keyp)
    crypto.gen_ec(keyp)
    try:
        csr = kcrypto.csr(crypto, keyp, ip, store.work_dir())
        order = client.poll(order_url, want=("ready", "valid"), during=tr("ipcert.during.order"))
        if order.get("status") == "ready":
            client.finalize(order, csr)
            order = client.poll(order_url, want=("valid",), during=tr("ipcert.during.finalize"))
        if not order.get("certificate"):
            raise acme.AcmeError("server", tr("ipcert.err.no_cert_url"))
        chain = client.certificate(order["certificate"])
        from ..site import certinfo
        try:
            inf = certinfo.parse(chain)
        except ValueError as e:
            raise acme.AcmeError("server", tr("ipcert.err.unreadable", error=e))
        if not store.covers(inf, ip):
            raise acme.AcmeError("server", tr("ipcert.err.wrong_ip", ip=ip))
        store.install(chain, keyp)
    finally:
        if os.path.exists(keyp):
            os.unlink(keyp)
    m = store.meta()
    m.update(ip=ip, not_after=inf["not_after"], issued_at=util.now_iso(), profile=acme.PROFILE, staging=bool(staging),
             ca="letsencrypt-staging" if staging else "letsencrypt")
    store.save_meta(m)
    try:
        ensure_units(runner)
    except KaskadError as e:
        util.log_tr("ipcert.log.no_timer", error=e)
    util.log_tr("ipcert.log.issued", ip=ip, until=inf["not_after"], staging=", staging" if staging else "")
    st = store.status(ip)
    _say(progress, "cert", "ok", tr("ipcert.progress.until", until=fmt_datetime(inf["not_after"])))
    return {"ok": True, "issued": True, "kept": False, "cert": st, "via": via,
            "text": tr("ipcert.issued", ip=ip, lifetime=tr("ipcert.lifetime"))}


def tick(runner=None):
    """The timer: renew when needed and fewer than RENEW_LEFT_H hours are left (or the address changed)."""
    runner = runner or sysrun.default_runner()
    if not needed():
        return {"ok": True, "action": "none", "why": "not_needed"}
    try:
        ip = public_ip(runner)
    except KaskadError as e:
        return {"ok": False, "action": "none", "why": str(e)}
    st = store.status(ip)
    if st["state"] == "valid" and not st["staging"]:
        return {"ok": True, "action": "none", "why": "valid", "cert": st}
    try:
        res = issue(runner, ip=ip)                  # not «valid»: issued; a pending limit is respected
        return dict(res, action="renewed")
    except Refusal as e:
        return dict(e.doc, action="failed")


def unit_texts():
    # the Description lines stay as they are: a unit file of another text is rewritten (and reloaded) on every issue
    svc = "\n".join(["[Unit]",
                     "Description=Kaskad 3: renewal of the Let's Encrypt certificate of the IP address",
                     "After=network-online.target", "Wants=network-online.target", "", "[Service]", "Type=oneshot",
                     "ExecStart=%s ipcert tick --quiet" % paths.kaskadctl(), "TimeoutStartSec=10min", ""])
    timer = "\n".join(["[Unit]",
                       "Description=Kaskad 3: IP address certificate check every 12 hours",
                       "",
                       "[Timer]", "OnCalendar=*-*-* 00/12:00:00", "RandomizedDelaySec=1h", "Persistent=true", "",
                       "[Install]", "WantedBy=timers.target", ""])
    return svc, timer


def ensure_units(runner):
    from ..gw import sys as gsys
    svc, timer = unit_texts()
    written = gsys.write_unit(UNIT, svc)
    r = gsys.ensure_unit(runner, TIMER, timer)
    if written and not r["written"]:
        gsys.systemd(runner, "daemon-reload")
    return TIMER


def status(runner=None):
    from ..gw import sys as gsys
    ip = None
    try:
        ip = public_ip(runner or sysrun.default_runner())
    except KaskadError:
        pass
    doc = store.status(ip)
    doc.update(ok=True, for_ip=ip, needed=needed(), timer=os.path.exists(gsys.unit_path(TIMER)),
               serving=store.serving_pair(ip) is not None if ip else False)
    return doc


def remove(runner=None):
    from ..gw import sys as gsys
    runner = runner or sysrun.default_runner()
    gone = [u for u in (TIMER, UNIT) if gsys.remove_unit(runner, u)]
    files = store.purge()
    if gone or files:
        util.log_tr("ipcert.log.removed")
    return {"ok": True, "units": gone, "files": files}


def account_reset(staging=False):
    """For the tests and the stand: forget the account key (a new one registers next time)."""
    p = store.account_key(staging)
    if os.path.exists(p):
        os.unlink(p)
    shutil.rmtree(store.work_dir(), ignore_errors=True)
