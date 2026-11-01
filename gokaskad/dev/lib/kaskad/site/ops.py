"""What ``kaskadctl site …`` and the panel's /api/site do (docs/k3/SPEC-wave4.md §4.1). Each function returns the
JSON of the contract; a refusal of ``cert issue``/``cert renew`` raises ``Refusal`` carrying
``{"ok": false, "reason": "port80"|"dns"|"limit"|"net"|"apt", "text", "retry_after"?}``.

    status(runner)                       STATUS
    set_domain(runner, name)             the domain (the name checked, a DNS check at once, no issue); another domain
                                         before it is cleared first — unless it WORKS (certificate or site): then
    change_domain(runner, name, say)     the new name checked (DNS, port 80 — no change on a refusal), its certificate
                                         issued while the old domain serves, then the switch (site, VLESS TLS, panel,
                                         SSTP/OpenConnect follow); a failed switch puts the old domain back as a whole
    clear(runner)                        the domain away (п. 8): consumers back on the self-signed certificate, SSTP
                                         and OpenConnect on the domain -> «нужны новые настройки», autorenew = False,
                                         our hook, timers and UFW rule away; the certificates of Let's Encrypt stay
    check_dns(runner, wait=0, say=None)  DNS (``wait`` ≤ 300 s: again every 15 s until ready)
    set_email(runner, email | None)      the address for Let's Encrypt
    issue(runner, staging=False)         certbot when needed, port 80, issue or keep a live one, hand it out
    renew(runner)                        certbot renew --cert-name (only when due)
    use(runner, kind, on, dry_run)       SSTP | OpenConnect on the certificate of the domain, or back
    tick(runner)                         the watchdog of the expiry (kaskad-site.timer)
    mode(runner, "site"|"domain", …)    nginx cover site on 443, or back to a bare domain
    template_set(runner, id|None)         deploy a cover and update STATUS while site mode is on
    panel_url(show) / rotate_panel_path   the path is masked unless an explicit caller asks to show it
    check(runner)                         local curl/--resolve health verdict of the site
    remove(runner)                       clear + /etc/kaskad/site (the removal of Kaskad, menu 15)

Port 80: Let's Encrypt checks the domain over HTTP on TCP/80 (``certonly --standalone``: certbot listens there for
the moment of the check). A foreign listener, a forward tcp/80 (our DNAT would take the check away) or an inbound
of the gateway on it -> refusal with who holds it; Kaskad does not stop what is not its own.

Who answers on TCP/80 in each mode (``_http_for_domain``, live check 4.0-C):
    site                     our nginx vhost, certbot ``webroot`` (/var/www/kaskad-acme)
    domain, no nginx         certbot ``--standalone``
    domain, Kaskad's nginx   nginx disabled and stopped (``nginx.owned()``), ``--standalone``
    domain, foreign nginx    running: our ACME-only vhost ``kaskad-acme`` for the domain, ``webroot``;
                             stopped: nothing of ours in it, ``--standalone``
    off                      none of our vhosts; Kaskad's nginx stays stopped
"""

import contextlib
import copy
import errno
import fcntl
import os
import time

from .. import i18n, paths, state, util
from ..errors import CONFLICT, ERROR, LOCKED, USAGE, ConflictError, KaskadError
from ..gw import sys as gsys
from ..i18n import fmt_date, tr, trn
from . import acme, certinfo, consumers, dns, front, model, nginx

MIN_DAYS = 30                  # a live certificate with at least this much left is not issued again (LE limits)
WARN_DAYS = 21                 # the watchdog renews below this (and tells the bot when it cannot)
WAIT_MAX = 300
WAIT_STEP = 15
UFW_COMMENT = "kaskad-acme"
REISSUE_SHOWN = 10
TITLES = {"sstp": "SSTP", "ocserv": "OpenConnect", "hy2": "Hysteria2"}
# Let's Encrypt с июня 2025 не шлёт писем об истечении: почта — для важных писем (отзыв, условия), за сроком следит
# сам Kaskad. Текст уходит и в панель, и в бота — без команд CLI (подсказку `kaskadctl site email` печатает только CLI).


# Codes of the warnings other modules act on (the answers carry them in ``warning_codes`` next to the texts in
# ``warnings``; the text — ``warning(code)`` in the language of the call).
NO_EMAIL = "no_email"
DEFAULT_PAGE = "default_page"
NGINX_BOOT = "nginx_boot"


def warning(code):
    """The text of a warning code (NO_EMAIL, DEFAULT_PAGE, NGINX_BOOT) in the language of the call."""
    return tr("site.ops." + code)


def uncoded(res, *codes):
    """The texts of ``res["warnings"]`` without the warnings of ``codes`` — by the code of each text
    (``warning_items``: ``{"code", "text"}`` one per text), never by the words (3.3.2 №5: the words are in the language
    of the call, a code is not). A result without ``warning_items`` has no coded texts — its warnings as they are."""
    items = res.get("warning_items")
    if not isinstance(items, list):
        return list(res.get("warnings") or [])
    return [i["text"] for i in items if i.get("code") not in codes]


def _items(warnings, coded):
    """``warning_items`` of ``warnings``: ``coded`` — {index in ``warnings``: code}."""
    return [{"code": coded.get(n), "text": w} for n, w in enumerate(warnings)]

_sleep = time.sleep
_mono = time.monotonic


class Refusal(KaskadError):
    """A refusal of the contract: ``doc`` = {"ok": false, "reason", "text", "retry_after"?}."""

    def __init__(self, reason, text, retry=None, code=ERROR, extra=None):
        doc = {"ok": False, "reason": reason, "text": text}
        if retry:
            doc["retry_after"] = retry
        doc.update(extra or {})
        KaskadError.__init__(self, text, code=code, data=doc)
        self.doc = doc


# ── small parts ────────────────────────────────────────────────────────────

def _load():
    return state.load()


def _save_site(fn):
    """fn(site part of the state) under the state lock; the state saved. -> what fn returned."""
    with state.locked():
        st = state.load()
        s = model.site(st)
        out = fn(s)
        state.save(st)
    return out


def _need_domain(st):
    d = model.domain_of(st)
    if not d:
        raise KaskadError(tr("site.ops.need_domain"), code=USAGE)
    return d


def port80(st, runner, for_site=False):
    """{"free": bool, "who": text | None, "conflicts": [...]} — who holds TCP/80 (forwards, inbounds of the gateway,
    listeners, Docker, a foreign DNAT); our own use of it (the domain itself) is not a conflict. ``for_site``: the
    nginx Kaskad owns is about to serve the site, so it does not hold :80 against it."""
    from ..gw import ops as gops
    found = [c for c in gops.port_conflicts(st, runner, "site", "tcp", 80) if c["kind"] != "site"]
    if nginx.ours() or nginx.acme_ours() or (for_site and nginx.owned()):    # nginx answers from our webroot
        found = [c for c in found if not (c["kind"] == "listener" and "nginx" in c["detail"].lower())]
    # ``port_conflicts`` also serves a new port-forward: its listener detail warns that a forward would steal incoming
    # connections.  For certbot that is the wrong action and confusing — the useful fact here is only who owns 80.
    from .. import conflicts
    tails = [conflicts.effect(n) for n in ("listener", "ssh")]          # in the language of the call
    holders = []
    for c in found:
        text = c["detail"]
        for tail in tails:
            if text.endswith(tail):
                text = text[:-len(tail)]
        holders.append(text)
    return {"free": not found, "who": "; ".join(holders) or None, "conflicts": found}


def port80_text(p, what="issue"):
    """``what``: "issue" | "renew" — the action to repeat."""
    return tr("site.ops.port80." + ("renew" if what == "renew" else "issue"), who=p["who"])


def _refusal_text(runner, d, reason, retry=None, what="issue", kind=None):
    """The words of a certbot refusal; for «port80» — who holds TCP/80 now, not certbot's raw output."""
    if reason == "port80":
        p = port80(_load(), runner)
        if p["who"]:
            return port80_text(p, what)
    return acme.refusal_text(d, reason, retry, kind)


def _certbot_refusal(runner, d, text, what="issue"):
    """A Refusal of certbot's output: the reason, the words, and for a limit — when to repeat (``retry_after`` UTC,
    ``retry_text`` — that part of the words, by the clock of this server) and which limit (``limit``)."""
    reason = acme.cause(text)
    retry = acme.retry_after(text) if reason == "limit" else None
    kind = acme.limit_kind(text) if reason == "limit" else None
    extra = {}
    if reason == "limit":
        extra["limit"] = kind
        when = acme.when_text(retry)
        if when:
            extra["retry_text"] = when
    return Refusal(reason, _refusal_text(runner, d, reason, retry, what, kind), retry, extra=extra)


def port443(st, runner, for_vless=False):
    """The site may share neither a forward nor another public TCP/443 listener. ``for_vless`` — VLESS of the gateway
    goes in front of the site (site.front: xray on 443, nginx behind it): VLESS itself and xray of the gateway holding
    443 for it are no conflict then."""
    from ..gw import ops as gops
    found = [c for c in gops.port_conflicts(st, runner, "site", "tcp", 443) if c["kind"] != "site"]
    if nginx.ours() or nginx.owned():
        found = [c for c in found if not (c["kind"] == "listener" and "nginx" in c["detail"].lower())]
    if for_vless:
        from ..gw.inbounds import vless as vin
        title = vin.VlessInbound.title
        ours_443 = (front.vless_port(st) == 443)
        found = [c for c in found if not (c["kind"] == "gateway" and title in c["detail"])
                 and not (ours_443 and c["kind"] == "listener" and "xray" in c["detail"].lower())]
    return {"free": not found, "who": "; ".join(c["detail"] for c in found) or None, "conflicts": found}


def port443_text(p):
    return tr("site.ops.port443", who=p["who"])


def _neighbour_text(names):
    return tr("site.ops.neighbour", names=", ".join(names))


def _ufw_has_80(runner):
    res = runner.run(["ufw", "status"])
    lines = res.out.splitlines() if res.ok else []
    ours = any(ln.split()[:1] == ["80/tcp"] and "# " + UFW_COMMENT in ln and "(v6)" not in ln for ln in lines)
    other = any(ln.split()[:1] in (["80/tcp"], ["80"]) and "ALLOW" in ln and "# " + UFW_COMMENT not in ln
                for ln in lines)
    return ours, other


def _ufw_allow80(runner):
    """UFW active and 80/tcp not open yet -> ``ufw allow 80/tcp comment kaskad-acme``. True if the rule is ours."""
    if not gsys.ufw_active(runner):
        return False
    ours, other = _ufw_has_80(runner)
    if ours:
        return True
    if other:
        return False                    # opened by the owner: his rule, we neither add a twin nor remove it later
    return runner.run(["ufw", "allow", "80/tcp", "comment", UFW_COMMENT]).ok


def _ufw_delete80(runner):
    if not gsys.ufw_active(runner):
        return False
    ours, _ = _ufw_has_80(runner)
    return ours and runner.run(["ufw", "delete", "allow", "80/tcp"]).ok


def _affected(st, kinds):
    """[{"device", "name", "kind", "user_name"}] — devices whose issued settings of these inbounds stop working when the
    certificate and the address of the inbound change (F-2: whom to give the settings again)."""
    from ..gw import model as gm
    users = dict((u.get("id"), u.get("name")) for u in gm.users(st))
    out = []
    for k in kinds:
        for d in gm.devices_for(st, k):
            out.append({"device": d.get("id"), "name": d.get("name") or d.get("id"), "kind": k,
                        "user_name": users.get(d.get("user")) or ""})
    return out


def hy2_reissue(consumers_out):
    """The rows «выдать заново» of Hysteria2 out of ``consumers.reload`` / ``cert_installed`` (its certificate switched
    by itself: the domain came or went — SPEC-stage1-hy2-inbound §3.4)."""
    for c in consumers_out or []:
        if isinstance(c, dict) and c.get("name") == "hy2":
            return [r for r in c.get("reissue") or [] if isinstance(r, dict)]
    return []


def reissue_text(rows, what):
    """The F-2 line «выдайте заново …» of ``rows`` ("" when nobody)."""
    if not rows:
        return ""
    names = []
    for r in rows:
        n = "%s (%s)" % (r["name"], r["user_name"]) if r.get("user_name") else r["name"]
        if n not in names:
            names.append(n)
    more = len(names) - REISSUE_SHOWN
    kinds = sorted(set(r["kind"] for r in rows), key=lambda k: list(TITLES).index(k) if k in TITLES else 9)
    return tr("site.ops.reissue", kinds=", ".join(TITLES.get(k, k) for k in kinds), what=what,
              names=", ".join(names[:REISSUE_SHOWN]), more=tr("site.ops.reissue_more", n=more) if more > 0 else "")


def _notify(text):
    """A line to the owner in Telegram when the bot is set up (BOT_TOKEN and BOT_CHAT_ID); silent otherwise."""
    try:
        from ..bot import config as bcfg
        from ..bot.cli import _api
        cfg = bcfg.load()
        if not cfg.token or not cfg.chat_id:
            return False
        _api(cfg).send_message(cfg.chat_id, text)
        return True
    except Exception:                                   # noqa — no bot, no network: the journal has the event
        return False


# ── status ─────────────────────────────────────────────────────────────────

def cert_doc(st):
    """CERT: the certificate of Let's Encrypt for the domain (certbot's live one), else the node's self-signed one
    (the open mode of the panel and SSTP use it), else None."""
    d = model.domain_of(st)
    if d:
        info = certinfo.read(os.path.join(model.live_dir(d), "fullchain.pem"))
        if info is not None and certinfo.covers(info, d):
            return certinfo.doc(info, "letsencrypt")
    try:
        from ..panel import conf
        crt, _ = conf.node_cert()
        if crt:
            return certinfo.doc(certinfo.parse(crt), "selfsigned")
    except (ValueError, KaskadError):
        pass
    return None


def _template_doc(s):
    """Template markers are authoritative; the state is a convenient status cache."""
    try:
        from . import templates
        got = templates.status()
        tid = got.get("id") or s["template_id"] or None
        source = got.get("source") or s["template_source"] or None
        deployed = got.get("deployed_at") or s["deployed_at"] or None
        # the name from a ready index only: a status never starts its build (T-M6-qs330 №3)
        rec = templates.get(tid) if tid and tid != templates.STUB_ID and templates.index_fresh() else None
        name = (rec or {}).get("name") or (tr("site.ops.stub_name") if tid == getattr(templates, "STUB_ID", "")
                                           else tid)
        return {"id": tid, "name": name, "source": source, "deployed_at": deployed} if tid else None
    except (ImportError, KaskadError):
        return {"id": s["template_id"], "name": s["template_id"], "source": s["template_source"],
                "deployed_at": s["deployed_at"]} if s["template_id"] else None


def site_now(st=None):
    """The site as it is now, without running anything: {"domain", "mode", "template": {"id", "name"} | None}. The
    wizard says «Сайт открыт» by this, not by the record of its own step — after ``site remove`` that record stayed
    «ok» while nginx was off (3.3.0 T-M6-qs330c №4)."""
    st = _load() if st is None else st
    s = model.peek(st)
    tpl = _template_doc(s) if s["mode"] == "site" else None
    return {"domain": s["domain"] or None, "mode": s["mode"],
            "template": {"id": tpl.get("id"), "name": tpl.get("name")} if tpl else None}


def status(runner, st=None):
    from ..gw import model as gm
    from ..panel import conf
    st = _load() if st is None else st
    s = model.peek(st)
    d = s["domain"]
    act = model.active(st) is not None
    pub = conf.load()["public"]["on"]
    p80 = port80(st, runner)
    p443 = port443(st, runner, for_vless=front.vless_port(st) == 443)      # VLESS on 443: the planned layout
    # who answers on 443 in fact (3.3.0 T-M6 №15): xray of the gateway with VLESS on 443 is no conflict for the site
    # («free» above), but 443 is not free either — the site is behind xray (TLS) or waits for VLESS to step aside
    x443 = front.vless_port(st) == 443 and front.xray_holds_443(runner)
    rb = s["renew_by"] if d and s["cert"] == "letsencrypt" else ""
    if rb == acme.RENEW_TIMER and not os.path.exists(gsys.unit_path(acme.RENEW_TIMER)):
        rb = ""
    host = gm.public_host(st).lower()
    impact = dict((k, gm.devices_for(st, k)) for k in model.USE_KINDS)
    active_ids = set()
    for k in model.USE_KINDS:
        if act and s["use"][k]:
            active_ids.update(d["id"] for d in impact[k])
    proxy = conf.load()["proxy"]
    pl = people_links() if d else {"domain": "", "count": 0, "port": pub}
    panel_mask = None
    if s["mode"] == "site" and proxy.get("path") and d:
        panel_mask = "https://%s/%s/" % (d, conf.mask_path(proxy["path"]))
    return {"ok": True, "domain": d or None, "domain_unicode": model.unicode(d) if d else None, "mode": s["mode"],
            "cert": cert_doc(st), "dns": s["dns"] if d else None,
            "port80": {"free": p80["free"], "who": p80["who"]},
            "site": {"template": _template_doc(s), "root": paths.site_root(), "panel_url_masked": panel_mask,
                     "nginx": nginx.status(runner),
                     "port443": {"free": p443["free"], "who": p443["who"], "xray": x443,
                                 "behind": x443 and front.nginx_behind() and front.xray_fronts(runner),
                                 # T-3378 Н-3: the site waits behind xray, xray holds no TCP 443 — nobody listens
                                 "orphan": not x443 and s["mode"] == "site" and front.nginx_behind()
                                 and not front.xray_fronts(runner)},
                     "check": _check_view(s["check"])},
            "use": {"panel": act and (pub or s["mode"] == "site"),
                    "pages": act and (pub or s["mode"] == "site"), "sstp": act and s["use"]["sstp"],
                    "ocserv": act and s["use"]["ocserv"], **_hy2_use(st)},
            "use_impact": dict((k, len(impact[k])) for k in model.USE_KINDS),
            "use_impact_total": len(active_ids),
            "host_is_domain": bool(d) and host == d,
            # 3.3.7.5 круг 3: what «Сменить домен» and «Убрать домен» do to the links of people and the way in
            "people_links": pl, "people_warn": {"clear": people_warn(pl, "clear"), "change": people_warn(pl, "change")},
            "renew": {"by": rb or None}, "deps": {"certbot": acme.have_certbot(runner)}, "email": s["acme_email"]}


def _hy2_use(st):
    """Hysteria2 in «Сертификат домена у:» (KT3-5): {} — no Hysteria2 (the answer as before); {"hy2": bool} — on the
    domain's certificate or not."""
    from ..gw import model as gm, registry
    ad = registry.get("hy2")
    if gm.inbound(st, "hy2") is None or ad is None or not hasattr(ad, "cert_mode"):
        return {}
    return {"hy2": ad.cert_mode(st) == "domain"}


# ── the domain ─────────────────────────────────────────────────────────────

def set_domain(runner, name, say=None, keep_working=True):
    """The domain of the node. A WORKING domain (``working``: its certificate is handed out or its site is on) is not
    dropped for a new name that is not ready: that goes through ``change_domain`` — the new name checked and given its
    certificate first, the old one away last, everything back on a failure (T-M6-qs330d №1). ``keep_working`` False
    — the old way (the copy of settings restores a node exactly: kaskad.backup)."""
    d = model.parse_domain(name)
    st = _load()
    old = model.domain_of(st)
    if keep_working and old and old != d and working(model.peek(st)):
        return change_domain(runner, d, say=say)
    affected = []
    if old and old != d:            # the copy (``keep_working`` False) is an action of the system: the price answered
        affected = clear(runner, yes=not keep_working, vless_auto=not keep_working)["affected"]
        st = _load()
    doc = dns.check(d, st, runner)
    ufw = _ufw_allow80(runner)

    def put(s):
        if s["domain"] != d:
            s.update(cert="", staging=False, renew_by="")
        s["domain"], s["dns"] = d, doc
        if s["mode"] != "site":
            s["mode"] = "domain"
        s["ufw80"] = s["ufw80"] or ufw
    _save_site(put)
    if old != d:
        util.log_tr("site.ops.log_domain_set", domain=d, dns=doc["state"])
    warnings = []
    if model.peek(_load())["mode"] == "domain":
        try:
            how = _http_for_domain(runner, d)
            acme.set_renewal_mode(runner, d, how, nginx.acme_root() if how == "webroot" else None)
        except KaskadError as e:
            warnings.append(tr("site.ops.port80_not_ready", error=e))
    out = {"ok": True, "site": status(runner), "dns": doc, "affected": affected}
    if warnings:
        out["warnings"] = warnings
    if affected:
        out["text"] = reissue_text(affected, tr("site.ops.what.domain_changed"))
    return out


def people_links():
    """3.3.7.5 круг 3 (owner: people's links never break silently) — {"domain": the domain the links of people are on
    (``conf.pages_domain``) or "", "count": the people with a page or a subscription on (their links name that domain
    when shown), "port": the way in by the port is on}."""
    from ..panel import conf, userpage
    cfg = conf.load()
    d = conf.pages_domain(cfg)
    n = 0
    if d:
        try:
            n = len(set(userpage._stored_tokens()) | set(userpage._stored_sub_tokens()))
        except (KaskadError, OSError, ValueError):
            n = 0
    return {"domain": d, "count": n, "port": bool(cfg["public"]["on"])}


def people_warn(pl, action, done=False):
    """The honest lines of a change (``action`` "change") or a removal ("clear") of the domain for the people and the
    way in; ``done`` — said after it (the answer), else before (the window)."""
    out = []
    if pl["domain"] and pl["count"]:
        out.append(tr("site.ops.people_links_die", domain=model.unicode(pl["domain"]), n=int(pl["count"])))
    if pl["domain"] and not pl["port"]:          # the panel lives on the domain only (a tunnel-only node stays so)
        if action == "clear":
            out.append(tr("site.ops.port_turned_on" if done else "site.ops.port_will_on"))
        else:
            out.append(tr("site.ops.port_off_change"))
    return out


def _keep_way_in(runner, d):
    """Before «Убрать домен»: the way in by the port switched on when it is off while the panel and the links of people
    live on the domain (else nobody keeps an address; a node kept to the SSH tunnel by choice stays so).
    -> ``people_links()`` as it was."""
    pl = people_links()
    if pl["domain"] and not pl["port"]:
        from ..panel import conf
        conf.set_public(runner, True)               # KaskadError — nothing changed yet, the domain stays
        consumers._reload_panel(runner)
        util.log_tr("site.ops.log_port_back_on", domain=d)
    return pl


def clear(runner, way_in=False, yes=False, vless_site=None, vless_auto=False):
    """See the module doc. -> {"ok", "site": STATUS, "affected", "text"?, "kept", "warnings"?}. ``way_in`` — «Убрать
    домен» of a person (the panel, the bot, the menu, the CLI, the wizard): with the way in by the port off it is
    switched on first (under the secret prefix) — else neither the panel nor the people keep an address; a refusal
    there leaves the domain as it is. The answer says what happens to the links of people (3.3.7.5 круг 3).
    The links of VLESS would change (TLS on this domain -> Reality) and devices have them: without ``yes`` CONFLICT
    (§3.6, ``vless_price``) — asked before anything changes, the way in too. ``vless_auto`` — an action of the system
    (an import, the undo of a domain switch, the removal, the setup): no cover to name is picked by the core
    (``vless_price``)."""
    with front.vless_guard(runner):
        return _clear(runner, yes, vless_site, vless_auto, way_in)


def _clear(runner, yes, vless_site, vless_auto=False, way_in=False):
    from ..gw import ops as gops
    st = _load()
    if model.peek(st)["domain"]:
        vless_price(st, runner, "domain", yes=yes, vless_site=vless_site, drop_domain=True, vless_auto=vless_auto)
    s = model.peek(st)
    d = s["domain"]
    if not d:
        return {"ok": True, "site": status(runner, st), "affected": [], "kept": []}
    pl = _keep_way_in(runner, d) if way_in else None
    # A domain clear cannot leave a vhost with a certificate path that is about
    # to disappear.  ``mode(domain)`` also puts certbot back on standalone.
    off = {}
    if s["mode"] == "site":
        off = mode(runner, "domain", yes=True, vless_site=vless_site, vless_auto=vless_auto)     # the price is asked above
        st = _load()
        s, d = model.peek(st), model.domain_of(st)
    was = [k for k in model.USE_KINDS if s["use"][k]]
    affected = _affected(st, was)

    def reset(ns):
        ns.update(domain="", mode="off", cert="", staging=False, dns=None, renew_by="",
                  use=dict((k, False) for k in model.USE_KINDS), ufw443=False, template_id="",
                  template_source="", deployed_at="", check=None, prev=None)

    if was:
        gops._change(runner, lambda new, sec: reset(model.site(new)))  # the inbounds re-rendered: self-signed again
    else:
        _save_site(reset)
    consumers.drop_copy()
    try:
        nginx.remove(runner, rollback=False)      # the ACME-only vhost of domain mode (the site is already away)
    except KaskadError as e:
        util.log_tr("site.ops.log_acme_not_removed", error=e)
    acme.set_autorenew(d, False)
    acme.drop_hook(d)
    acme.remove_units(runner)
    if s["ufw80"]:
        _ufw_delete80(runner)
        _save_site(lambda ns: ns.update(ufw80=False))
    affected = affected + hy2_reissue(consumers.reload(runner, _load()))
    kept = [model.live_dir(d)] if os.path.isdir(model.live_dir(d)) else []
    util.log_tr("site.ops.log_cleared_kept" if kept else "site.ops.log_cleared", domain=d)
    out = {"ok": True, "site": status(runner), "affected": affected, "kept": kept}
    if affected:
        out["text"] = reissue_text(affected, tr("site.ops.what.domain_cleared"))
    # what the site off said — VLESS left TLS with it: whom to give the links again (the line of gw.ops._reissue) and
    # «vless» — goes into this answer, as «Сменить домен» passes them (``_switch``); before, they were lost (T-3378 Н-12)
    warnings = list(off.get("warnings") or [])
    if off.get("vless"):
        out["vless"] = off["vless"]
    if pl is not None:
        warnings += people_warn(pl, "clear", done=True) or []
    if warnings:
        out["warnings"] = warnings
    return out


# ── a change of a working domain (T-M6-qs330d №1) ─────────────────────────

PREV_GRACE = 30 * 60            # s: the open mode of the panel still answers the replaced domain (model «prev»)


def working(s):
    """The domain of ``s`` (model.peek) is in use: its certificate of Let's Encrypt is handed out or its site is on."""
    return bool(s["domain"]) and (s["cert"] == "letsencrypt" or s["mode"] == "site")


def replaces_working(name):
    """``name`` would replace a working domain (the panel and the bot warn and wait longer). Not a domain — False."""
    try:
        d = model.parse_domain(name)
    except KaskadError:
        return False
    s = model.peek(_load())
    return bool(s["domain"]) and s["domain"] != d and working(s)


def _kept_text(old):
    return tr("site.ops.kept", domain=model.unicode(old))


def precheck(runner, name, st=None):
    """Everything about a new name that can be checked without changing anything: its A/AAAA records lead to this
    server (not through Cloudflare), TCP/80 can answer Let's Encrypt (who holds it — as ``issue`` sees it). -> the DNS
    doc (``ready`` False for partial/unknown: Let's Encrypt itself is the last word); Refusal("dns" | "port80") with
    ``dns`` and ``kept`` (the working domain that stays) otherwise. Nothing is saved."""
    st = _load() if st is None else st
    d = model.parse_domain(name)
    s = model.peek(st)
    old = s["domain"] if s["domain"] != d else ""
    doc = dns.check(d, st, runner)
    extra = {"dns": doc, "domain": d, "kept": old or None}
    if doc["state"] in ("none", "elsewhere") or doc["cloudflare"]:
        text = doc["advice"] or acme.refusal_text(d, "dns")
        raise Refusal("dns", text + (" " + _kept_text(old) if old else ""), code=CONFLICT, extra=extra)
    # Kaskad's own nginx is no holder: the site answers Let's Encrypt through it, and in domain mode it is stopped
    # before certbot --standalone (as ``issue`` does — 3.2.x left it running there)
    own = s["mode"] == "site" or (s["mode"] == "domain" and bool(runner.which("nginx")) and nginx.owned())
    p = port80(st, runner, for_site=own)
    if not p["free"]:
        raise Refusal("port80", port80_text(p) + (" " + _kept_text(old) if old else ""), code=CONFLICT, extra=extra)
    return doc


def _say_none(*a, **kw):
    return None


def _new_cert(runner, d, s, st):
    """The certificate of the new name ``d`` while the working domain keeps serving: a live one of at least MIN_DAYS
    is taken as it is; else certbot — through our nginx (the site or our ACME vhost in a foreign nginx: the ACME vhost
    answers for ``d`` too, next to what serves now) or ``--standalone`` on a free :80 (Kaskad's nginx is stopped in
    domain mode). Its deploy hook does nothing for a name that is not the domain yet. -> (certinfo, issued);
    Refusal on a failure — nothing of the working domain changed (a rule «ufw allow 80» opened here goes again)."""
    live = _live(d)
    if live is not None and not certinfo.expired(live) and certinfo.days_left(live) >= MIN_DAYS \
            and certinfo.covers(live, d) and not live["staging"]:
        util.log_tr("site.ops.log_has_cert", domain=d, days=certinfo.days_left(live))
        return live, False
    try:
        acme.ensure_certbot(runner)
    except KaskadError as e:
        raise Refusal("apt", tr("site.ops.certbot_missing", error=e))
    opened = False
    if not s["ufw80"] and gsys.ufw_active(runner) and not _ufw_has_80(runner)[0]:
        opened = _ufw_allow80(runner)                   # ours only for the run: away again if it fails
    undo = None
    webroot = None
    _free_owned_80(runner, s)                           # domain mode: our nginx left on :80 by 3.2.x (no site of it)
    try:
        if s["mode"] == "site" or nginx.acme_ours():
            names = [d] if s["mode"] == "site" else [s["domain"], d]
            try:
                undo = nginx.acme_extra(runner, names)
            except KaskadError as e:
                raise Refusal("nginx_test", tr("site.ops.acme_extra_refused", domain=model.unicode(d), error=e),
                              code=CONFLICT)
            webroot = nginx.acme_root()
        util.log_tr("site.ops.log_issue_change", domain=d, old=s["domain"])
        res, text = acme.run_certbot(runner, acme.issue_argv(d, s["acme_email"], False, False, webroot=webroot))
        if not res.ok:
            refusal = _certbot_refusal(runner, d, text)
            util.log_tr("site.ops.log_le_refused", reason=refusal.doc["reason"], domain=d, detail=util.redact(
                " | ".join(ln.strip() for ln in (res.err or res.out or "").splitlines()[-4:] if ln.strip())))
            raise refusal
        new = _live(d)
        if new is None or not certinfo.covers(new, d):
            raise Refusal("dns", tr("site.ops.no_files", domain=model.unicode(d), path=model.live_dir(d)))
    except Exception:
        if opened:
            _ufw_delete80(runner)
        raise
    finally:
        if undo is not None:
            undo()
    util.log_tr("site.ops.log_cert_switch", domain=d, until=new["not_after"][:10])
    return new, live is None or new["sha256"] != live["sha256"]


def change_domain(runner, name, say=None):
    """A working domain replaced by ``name`` (T-M6-qs330d №1: the wizard took the old domain, its site and VLESS on TLS
    away for a new name without an A record). In this order:

      1. ``precheck`` — DNS of the new name and TCP/80, nothing changed (a refusal: the old domain as it was);
      2. ``_new_cert`` — its certificate while the old domain keeps serving (a refusal: the same);
      3. the switch — the old domain away (``clear``), the new one set and its certificate handed out (the panel,
         SSTP/OpenConnect when they were on the domain), the site back with the same template and VLESS on TLS with the
         new name when the site was on; the old name answered by the open mode of the panel for PREV_GRACE more;
      4. any failure in 3 — the old domain back as a whole (``_switch_back``) and Refusal("switch").

    ``say(key, state, detail, title)`` hears the progress (the wizard's rows). -> the answer of ``set_domain`` +
    {"changed_from", "cert", "issued", "vless"?, "template"?, "warnings"}."""
    with front.vless_guard(runner):
        return _change_domain(runner, name, say or _say_none)


def _change_domain(runner, name, say):
    d = model.parse_domain(name)
    st = _load()
    s = model.peek(st)
    old = s["domain"]
    say("dns", "wait", tr("site.ops.say.dns_wait", domain=model.unicode(d)), tr("site.ops.say.dns_title"))
    try:
        doc = precheck(runner, d, st)
    except Refusal as e:
        say("dns", "err", e.doc.get("text") or "")
        raise
    say("dns", "ok", i18n.join(" → ", [model.unicode(d), ", ".join(doc.get("a") or []) or tr("site.ops.this_server")]))
    say("cert", "wait", tr("site.ops.say.cert_wait", domain=model.unicode(d), old=model.unicode(old)),
        tr("site.ops.say.cert_title"))
    try:
        new, issued = _new_cert(runner, d, s, st)
    except Refusal as e:
        say("cert", "err", e.doc.get("text") or "")
        e.doc.setdefault("kept", old)
        e.doc["text"] = "%s %s" % (e.doc.get("text") or "", _kept_text(old))
        e.args = (e.doc["text"],)
        raise
    say("cert", "ok", tr("site.ops.say.cert_issued" if issued else "site.ops.say.cert_valid",
                         until=fmt_date(new["not_after"][:10])))
    say("switch", "wait", "%s → %s" % (model.unicode(old), model.unicode(d)), tr("site.ops.say.switch_title"))
    vkey = front.link_key(_load()) if front._rec(st) is not None else None    # the links of VLESS before the switch
    try:
        out = _switch(runner, s, d, doc)
    except Exception as e:                              # noqa: BLE001 — whatever broke, the old domain comes back
        why = util.redact(str(e)) or e.__class__.__name__
        util.log_tr("site.ops.log_switch_failed", old=old, domain=d, why=why)
        notes = []
        problems = _switch_back(runner, s, d, vkey=vkey, notes=notes)
        text = tr("site.ops.switch_failed_partial", why=why, old=model.unicode(old), problems="; ".join(problems)) \
            if problems else tr("site.ops.switch_failed", why=why, old=model.unicode(old))
        if notes:
            text = " ".join([text] + notes)
        say("switch", "err", text)
        raise Refusal("switch", text, code=CONFLICT, extra={"kept": old, "domain": d})
    say("switch", "ok", tr("site.ops.say.switch_ok", domain=model.unicode(d), old=model.unicode(old)))
    out.update(changed_from=old, issued=issued, cert=certinfo.doc(new, "letsencrypt"))
    util.log_tr("site.ops.log_changed", old=old, domain=d)
    return out


def _switch(runner, s, d, doc):
    """Step 3 of ``change_domain`` (see there). Raises on the first failure — the caller puts the old domain back."""
    from ..gw import model as gm
    old = s["domain"]
    was_use = [k for k in model.USE_KINDS if s["use"][k]]
    host_old = gm.public_host(_load()).lower() == old
    warnings = people_warn(people_links(), "change", done=True)     # 3.3.7.5 круг 3: the port stays as it is
    gone = clear(runner, yes=True, vless_auto=True)  # a change of the domain: VLESS goes on TLS with the new one
    prev = None
    old_live = _live(old)
    if old_live is not None and not certinfo.expired(old_live) and certinfo.covers(old_live, old):
        prev = {"domain": old, "until": int(time.time()) + PREV_GRACE}

    def put(ns):
        ns.update(domain=d, dns=doc, mode="domain", cert="", staging=False, renew_by="", prev=prev)
    _save_site(put)
    util.log_tr("site.ops.log_domain_set", domain=d, dns=doc["state"])
    how = _http_for_domain(runner, d)
    acme.set_renewal_mode(runner, d, how, nginx.acme_root() if how == "webroot" else None)
    got = issue(runner, vless_auto=False)                # the live certificate of ``d``: hook, renewal, consumers
    warnings += uncoded(got, NO_EMAIL)
    # «no mail» is not said on a change (since 3.3.0) — only its code goes on: the stage «Домен» of the wizard offers
    # the field for the mail by it (3.3.2 №5)
    out = {"ok": True, "consumers": got.get("consumers") or [], "warning_codes": [] if s["acme_email"] else [NO_EMAIL]}
    if s["mode"] == "site":
        m = mode(runner, "site", yes=True, keep_site=True)      # the template in the root stays (_kept_template)
        warnings += m.get("warnings") or []
        if m.get("vless"):
            out["vless"] = m["vless"]
        out["template"] = m.get("template")
    for k in was_use:
        u = use(runner, k, True)
        warnings += u.get("warnings") or []
    affected = gone.get("affected") or []
    if affected:
        warnings.append(reissue_text(affected, tr("site.ops.what.domain_changed_to", domain=model.unicode(d))))
    if host_old:
        warnings.append(tr("site.ops.host_old", old=model.unicode(old), domain=d))
    out.update(site=status(runner), dns=doc, affected=affected, warnings=warnings)
    return out


def _switch_back(runner, s, d, vkey=None, notes=None):
    """The old domain of ``s`` back as it was (a failed switch to ``d``): whatever of ``d`` went up away, the old
    name, its certificate handed out again, its site with the same template (VLESS on TLS with it), SSTP/OpenConnect
    on it. The certificate of ``d`` stays on disk without renewal. ``vkey`` — ``front.link_key`` of VLESS before the
    switch: the links of VLESS did not come back as they were (TLS refused on the old name too — the site with VLESS on
    Reality next to it) — that is a problem too; the warnings of the site on and whom to give the links again go into
    ``notes`` (whole sentences for the text of the refusal, REV-vless-sh3 заметно 1). -> [what did not come back] (log
    lines too)."""
    notes = [] if notes is None else notes
    old = s["domain"]
    problems = []

    def attempt(what, fn):
        try:
            return fn()
        except Exception as e:                           # noqa: BLE001 — the rest is still worth bringing back
            problems.append("%s: %s" % (what, util.redact(str(e)) or e.__class__.__name__))
            util.log_tr("site.ops.log_rb_failed", what=what, error=e)
            return None
    if model.domain_of(_load()):
        attempt(tr("site.ops.rb.clear", domain=d), lambda: clear(runner, yes=True, vless_auto=True))
    for fn in (lambda: acme.set_autorenew(d, False), lambda: acme.drop_hook(d)):
        attempt(tr("site.ops.rb.autorenew", domain=d), fn)

    def put(ns):
        ns.update(domain=old, dns=s["dns"], mode="domain", cert="", staging=False, renew_by="", prev=None,
                  acme_email=s["acme_email"])
    _save_site(put)
    if s["ufw80"] and attempt("UFW 80/tcp", lambda: _ufw_allow80(runner)):
        _save_site(lambda ns: ns.update(ufw80=True))
    how = attempt(tr("site.ops.rb.port80", domain=old), lambda: _http_for_domain(runner, old))
    if how:
        attempt(tr("site.ops.rb.renewal", domain=old), lambda: acme.set_renewal_mode(
            runner, old, how, nginx.acme_root() if how == "webroot" else None))
    live = _live(old)
    if live is not None and certinfo.covers(live, old) and not certinfo.expired(live):
        attempt(tr("site.ops.rb.cert", domain=old), lambda: _finish(runner, old, live["staging"], []))
    elif s["cert"] == "letsencrypt":
        problems.append(tr("site.ops.rb.no_cert", domain=model.unicode(old)))
    if s["mode"] == "site" and model.peek(_load())["cert"] == "letsencrypt":
        on = attempt(tr("site.ops.rb.site", domain=old), lambda: mode(runner, "site", yes=True, keep_site=True))
        # its reissue line counts from the state during the switch — ours below counts from before it
        said = [w for w in (on or {}).get("warnings") or [] if w != ((on or {}).get("vless") or {}).get("text")]
    else:
        said = []
    for k in model.USE_KINDS:
        if s["use"][k] and model.peek(_load())["cert"] == "letsencrypt":
            attempt(tr("site.ops.rb.use", title=TITLES[k], domain=old), lambda k=k: use(runner, k, True))
    moved, line = _vless_not_back(vkey)
    if moved:
        problems.append(tr("site.ops.rb.vless_links"))
        for w in said + [line]:
            if w and w not in notes:
                notes.append(w)
    if problems:
        util.log_tr("site.ops.log_rb_partial", domain=old, problems="; ".join(problems))
    else:
        util.log_tr("site.ops.log_rb_done", domain=old)
    return problems


def _vless_not_back(vkey):
    """The links of VLESS after the undo of a change of the domain against those before it (``vkey``). -> (moved, the
    line of the change and whom to give them again — ``gw.ops._reissue``, "" when nobody has them)."""
    st = _load()
    if vkey is None or front._rec(st) is None or front.link_key(st) == vkey:
        return False, ""
    from ..gw import ops as gops
    rec = front._rec(st)
    what = tr("site.front.to_tls", domain=model.unicode(model.domain_of(st))) \
        if (rec.get("security") or "reality") == "tls" else \
        tr("site.front.to_reality", sni=rec.get("sni") or "—", port=rec.get("port") or "—")
    _, text = gops._reissue(st, "vless", what)
    return True, text


def check_dns(runner, wait=0, say=None):
    """DNS of the domain, saved as the last check. ``wait`` seconds (≤ 300): again every 15 s until ready;
    ``say(text)`` hears the progress."""
    st = _load()
    d = _need_domain(st)
    try:
        wait = int(wait or 0)
    except (TypeError, ValueError):
        raise KaskadError(tr("site.ops.wait_range", max=WAIT_MAX), code=USAGE)
    if not 0 <= wait <= WAIT_MAX:
        raise KaskadError(tr("site.ops.wait_range", max=WAIT_MAX), code=USAGE)
    t0 = _mono()
    while True:
        doc = dns.check(d, st, runner)
        spent = int(_mono() - t0)
        if doc["ready"] or spent + WAIT_STEP > wait:
            break
        if say:
            say(tr("site.ops.dns_waiting", domain=model.unicode(d), state=doc["state"], spent=spent, wait=wait))
        _sleep(WAIT_STEP)

    def put(s):
        if s["domain"] == d:
            s["dns"] = doc
    _save_site(put)
    return doc


def set_email(runner, email):
    """``email`` None or "" clears it. The account of Let's Encrypt is updated when certbot already has one."""
    e = model.parse_email(email) if email else ""
    _save_site(lambda s: s.update(acme_email=e))
    warnings = []
    accounts = os.path.join(model.le_dir(), "accounts")
    if e and acme.have_certbot(runner) and os.path.isdir(accounts) and os.listdir(accounts):
        res = runner.run(["certbot", "update_account", "--email", e, "--non-interactive", "--no-eff-email"],
                         timeout=120)
        if not res.ok:
            warnings.append(tr("site.ops.email_later"))
    util.log_tr("site.ops.log_email_set" if e else "site.ops.log_email_cleared")
    return {"ok": True, "email": e, "warnings": warnings}


# ── the certificate ────────────────────────────────────────────────────────

def _live(d):
    return certinfo.read(os.path.join(model.live_dir(d), "fullchain.pem"))


def _finish(runner, d, staging, warnings, vless_auto=False):
    """After an issue (or a live certificate kept): our hook in the renewal file, autorenew on, the renewal and the
    watchdog timers, the state, the copy for the consumers. ``vless_auto`` — the first certificate of the domain
    (SPEC-vless-tls-default §3.3): VLESS that wants TLS goes on it by itself when nobody has its links
    (``gw.ops.vless_after_cert``); whom to give links again of Hysteria2 and VLESS — one line (В12)."""
    acme.set_autorenew(d, True)
    if acme.hook_state(d) == "foreign":
        warnings.append(tr("site.ops.foreign_hook", domain=model.unicode(d)))
    else:
        acme.add_hook(d)
    by = acme.ensure_renewal(runner)
    acme.ensure_watch(runner)
    _save_site(lambda s: s.update(cert="letsencrypt", staging=bool(staging), renew_by=by) if s["domain"] == d else None)
    got = consumers.cert_installed(runner)
    rows = hy2_reissue(got.get("consumers"))
    v = None
    if vless_auto:
        from ..gw import ops as gops
        v = gops.vless_after_cert(runner)
    vrows = [{"device": r.get("id"), "name": r.get("name"), "kind": "vless", "user_name": r.get("user_name") or ""}
             for r in ((v or {}).get("rows") or [])]
    if rows or vrows:           # Hysteria2 (and VLESS) moved to the domain's certificate: the links given out are old
        what = tr("site.ops.what.hy2_vless_cert" if rows and vrows else
                  "site.ops.what.hy2_cert" if rows else "site.ops.what.vless_cert")
        warnings.append(reissue_text(rows + vrows, what))
    if v and v.get("line"):
        warnings.append(v["line"])
    return got.get("consumers") or []


def issue(runner, staging=False, vless_auto=True):
    """See the module doc. ``vless_auto`` False — a compound action that puts the site back itself (the change of the
    domain): no §3.3 step after the first certificate. -> {"ok", "issued", "kept", "cert": CERT, "warnings",
    "consumers", "text"}."""
    st = _load()
    d = _need_domain(st)
    s = model.peek(st)
    staging = bool(staging)
    first = vless_auto and (s["cert"] != "letsencrypt" or bool(s["staging"]))   # §3.3: the first one of the domain
    warnings = []
    live = _live(d)
    if live is not None and not certinfo.expired(live) and certinfo.days_left(live) >= MIN_DAYS \
            and certinfo.covers(live, d) and (not live["staging"] or staging):
        cons = _finish(runner, d, live["staging"], warnings, vless_auto=first and not live["staging"])
        text = tr("site.ops.still_valid", domain=model.unicode(d), days=certinfo.days_left(live))
        return {"ok": True, "issued": False, "kept": True, "cert": certinfo.doc(live, "letsencrypt"),
                "warnings": warnings, "consumers": cons, "text": text}
    force = live is not None and live["staging"] and not staging
    _free_owned_80(runner, s)
    p = port80(st, runner)
    if not p["free"]:
        raise Refusal("port80", port80_text(p), code=CONFLICT)
    doc = dns.check(d, st, runner)
    _save_site(lambda s: s.update(dns=doc) if s["domain"] == d else None)
    if doc["state"] in ("none", "elsewhere") or doc["cloudflare"]:
        raise Refusal("dns", doc["advice"] or acme.refusal_text(d, "dns"))
    if not doc["ready"] and doc["advice"]:
        warnings.append(doc["advice"])
    try:
        acme.ensure_certbot(runner)
    except KaskadError as e:
        raise Refusal("apt", tr("site.ops.certbot_missing", error=e))
    if _ufw_allow80(runner):
        _save_site(lambda s: s.update(ufw80=True))
    email = model.peek(st)["acme_email"]
    codes, coded = [], {}
    if not email:
        coded[len(warnings)] = NO_EMAIL
        warnings.append(warning(NO_EMAIL))
        codes.append(NO_EMAIL)
    if staging:
        warnings.append(tr("site.ops.staging"))
    if force:
        warnings.append(tr("site.ops.over_staging", domain=model.unicode(d)))
    util.log_tr("site.ops.log_issue", domain=d, staging=" (staging)" if staging else "")
    webroot = nginx.acme_root() if s["mode"] == "site" or nginx.acme_ours() else None
    if webroot:
        util.ensure_dir(webroot, 0o755)
    res, text = acme.run_certbot(runner, acme.issue_argv(d, email, staging, force, webroot=webroot))
    if not res.ok:
        refusal = _certbot_refusal(runner, d, text)
        util.log_tr("site.ops.log_le_refused", reason=refusal.doc["reason"], domain=d, detail=util.redact(
            " | ".join(ln.strip() for ln in (res.err or res.out or "").splitlines()[-4:] if ln.strip())))
        raise refusal
    new = _live(d)
    if new is None or not certinfo.covers(new, d):
        raise Refusal("dns", tr("site.ops.no_files", domain=model.unicode(d), path=model.live_dir(d)))
    issued = live is None or new["sha256"] != live["sha256"]
    cons = _finish(runner, d, new["staging"], warnings, vless_auto=first and not new["staging"])
    util.log_tr("site.ops.log_cert_issued" if issued else "site.ops.log_cert_kept", domain=d,
                until=new["not_after"][:10])
    text = tr("site.ops.issued" if issued else "site.ops.kept_by_certbot", domain=model.unicode(d))
    return {"ok": True, "issued": issued, "kept": not issued, "cert": certinfo.doc(new, "letsencrypt"),
            "warnings": warnings, "warning_codes": codes, "warning_items": _items(warnings, coded), "consumers": cons,
            "text": text}


def renew(runner):
    """``certbot renew --cert-name`` (certbot renews only when due). -> {"ok", "renewed", "cert"}."""
    st = _load()
    d = _need_domain(st)
    live = _live(d)
    if live is None or not acme.have_certbot(runner):
        raise KaskadError(tr("site.ops.no_cert_renew", domain=model.unicode(d)), code=CONFLICT)
    res, text = acme.run_certbot(runner, acme.renew_argv(d))
    if not res.ok:
        reason = acme.cause(text)
        retry = acme.retry_after(text) if reason == "limit" else None
        util.log_tr("site.ops.log_renew_failed", domain=d, reason=reason)
        raise Refusal(reason, _refusal_text(runner, d, reason, retry, "renew"), retry)
    new = _live(d) or live
    renewed = new["sha256"] != live["sha256"]
    if renewed:
        consumers.cert_installed(runner)
        util.log_tr("site.ops.log_renewed", domain=d, until=new["not_after"][:10])
    return {"ok": True, "renewed": renewed, "cert": certinfo.doc(new, "letsencrypt")}


def cert_installed(runner, lineage=None):
    return consumers.cert_installed(runner, lineage=lineage)


VHOST_LOCK_WAIT = 60


def vhost_refresh(runner, check=False):
    """``kaskadctl site vhost [--refresh]`` (BC2a; k3_post_update after the code of an update is in place): our site
    vhost by this version's template with the settings it has now (nginx.refresh). No domain, not «site» mode, no
    panel path, no vhost of ours or no nginx — nothing at all (``skipped``). ``check`` — only whether the file is
    current, nothing written. One refresh at a time (a flock: the daemons call k3_post_update at once at boot); the
    same text — no write, no reload. A refused ``nginx -t`` — the previous file is back, nginx not reloaded, a journal
    line; a failed reload after it — the previous file is back as well (the next refresh tries again). The same flock
    is taken by every other write of the site vhost (``_write_site``: a new panel path, ``site mode``, nginx behind
    xray), and the domain and the panel path are read under it.
    -> {"ok", "changed", "reload"?, "current"?, "skipped"?}; Refusal ``nginx_test``; KaskadError — the file not written."""
    def where():
        from ..panel import conf
        s = model.peek(state.load())
        path = conf.load()["proxy"].get("path") or ""
        if s["mode"] != "site" or not s["domain"] or not path or not runner.which("nginx") or not nginx.ours():
            return None
        return s["domain"], path
    no_site = {"ok": True, "changed": False, "skipped": "no_site"}
    first = where()
    if first is None:
        return no_site
    if check:
        got = nginx.refresh(runner, *first, dry=True)     # the same read: the state may change in between (REV-bc4 П-3)
        out = {"ok": True, "changed": False, "current": not got.get("would")}
        if got.get("skipped"):
            out["skipped"] = got["skipped"]
        return out
    with _vhost_lock():
        at = where()                    # again under the lock: a new panel path may have been written meanwhile
        if at is None:
            return no_site
        try:
            got = nginx.refresh(runner, *at)
        except nginx.NginxTestError as e:
            util.log_tr("site.ops.log_vhost_refused", error=e)
            raise Refusal("nginx_test", _nginx_test_text(e), code=CONFLICT)
        except KaskadError as e:
            util.log_tr("site.ops.log_vhost_refused", error=e)
            raise KaskadError(tr("site.ops.vhost_write_failed", error=e), code=ERROR)    # not a refusal of the panel
    if got.get("changed"):
        util.log_tr("site.ops.log_vhost_refreshed")
    out = {"ok": True, "changed": bool(got.get("changed")), "reload": got.get("reload")}
    if got.get("skipped"):
        out["skipped"] = got["skipped"]
    return out


@contextlib.contextmanager
def _vhost_lock():
    """flock of /run/kaskad/site-vhost.lock — waits up to VHOST_LOCK_WAIT s; no run dir or no lock in time — on
    without it (the write is one transaction with its own rollback anyway)."""
    fd = None
    try:
        util.ensure_run_dir()
        fd = os.open(os.path.join(paths.run_dir(), "site-vhost.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + VHOST_LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
    except OSError:
        if fd is not None:
            os.close(fd)
            fd = None
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)


# ── cover site / nginx (4.0-C) ────────────────────────────────────────────

def _site_cert(st):
    """A production LE copy suitable for nginx, or a concise refusal."""
    d = _need_domain(st)
    s = model.peek(st)
    info = model.active(st)
    if s["cert"] != "letsencrypt" or info is None or info.get("staging") or s["staging"]:
        raise Refusal("no_cert", tr("site.ops.site_no_cert", domain=model.unicode(d)), code=CONFLICT)
    return d, info


def _template_state(result):
    try:
        from . import templates
        got = templates.status()
    except (ImportError, KaskadError):
        got = {}
    return {"template_id": got.get("id") or result.get("id") or "",
            "template_source": got.get("source") or result.get("source") or "",
            "deployed_at": got.get("deployed_at") or ""}


def _enable_proxy(runner, domain, panel_path):
    from ..panel import conf
    conf.set_proxy(True, path=panel_path, domain=domain)
    # A live panel opens/closes its Unix socket on HUP; an uninstalled panel
    # simply uses these settings at its first start.
    consumers._reload_panel(runner)


def _nginx_test_text(error):
    """Explain whether nginx rejected Kaskad's vhost or a neighbouring file."""
    if error.ours:
        return tr("site.ops.nginx_test_ours", error=error)
    return tr("site.ops.nginx_test_other", error=error)


def _http_for_domain(runner, d):
    """Domain mode: make TCP/80 answer Let's Encrypt for ``d`` (see the module doc). -> "standalone" | "webroot".
    NginxTestError when nginx refused our ACME-only vhost (the previous files are back)."""
    if not runner.which("nginx"):
        nginx.remove(runner)
        return "standalone"
    if nginx.owned():
        nginx.remove(runner)
        if nginx.active(runner) or gsys.unit_enabled(runner, "nginx"):
            nginx.stop(runner)
            util.log_tr("site.ops.log_nginx_stopped")
        return "standalone"
    if nginx.active(runner):
        nginx.write_acme(runner, d)
        return "webroot"
    nginx.remove(runner)
    return "standalone"


def _free_owned_80(runner, s):
    """Domain mode with Kaskad's own nginx still running (3.2.x left it on :80 after the site was switched off):
    stop it before certbot --standalone needs the port.  -> True if it was stopped."""
    if s["mode"] != "domain" or not runner.which("nginx") or not nginx.owned() or not nginx.active(runner):
        return False
    try:
        how = _http_for_domain(runner, s["domain"])
        acme.set_renewal_mode(runner, s["domain"], how, nginx.acme_root() if how == "webroot" else None)
        return True
    except KaskadError as e:
        util.log_tr("site.ops.log_80_not_freed", error=e)
        return False


def site_checks(st, runner, for_vless):
    """What switching the site on needs before anything changes: no foreign vhost, 443 and 80 free (VLESS on 443 is no
    conflict when ``for_vless`` — it goes in front of the site or steps aside). Refusal (CONFLICT) else."""
    s = model.peek(st)
    if not s["domain"]:
        raise Refusal("no_domain", tr("site.ops.site_no_domain"), code=CONFLICT)
    neighbours = nginx.neighbours(runner)
    if neighbours:
        raise Refusal("neighbour", _neighbour_text(neighbours), code=CONFLICT)
    p443 = port443(st, runner, for_vless=for_vless)
    if not p443["free"]:
        raise Refusal("port443", port443_text(p443), code=CONFLICT)
    p80 = port80(st, runner, for_site=True)
    if not p80["free"]:
        raise Refusal("port80", port80_site_text(p80), code=CONFLICT)


def vless_price(st, runner, value, yes=False, vless_site=None, drop_domain=False, vless_auto=False):
    """§3.6/§3.7 — the decision in the core (З-3): the site on/off (or the domain away) would change the links of VLESS
    (``front.predict``) and devices have them -> without ``yes`` CONFLICT {need "vless_change", text, site,
    devices, need_pick}; nothing changes. The site off with no cover to name (``need_pick``) needs ``vless_site``
    whatever ``yes`` says — unless ``vless_auto``: an action of the system (§3.7 «внутренние вызовы», SEC-vless-sh12
    №3) — then ``sync`` picks it by the rule «Подобрать автоматически» (``vless.pick_mask``). Nothing to ask — None;
    else the prediction."""
    if front._rec(st) is None:
        return None
    p = front.predict(st, runner, value, drop_domain=drop_domain)
    if not p["changes"] or not p["devices"]:
        return None
    pick = value == "domain" and p["need_pick"] and not vless_site and not vless_auto
    if yes and not pick:
        return p
    n, who = len(p["devices"]), ", ".join(r["name"] for r in p["devices"][:5])
    if value == "domain":
        # the cover after the site goes off is never the node's own site (it is off): the short risks of variant A
        # («a foreign site»), the site named — or «the one you choose» (REV-vless-sh2 заметно 1)
        rec = front._rec(st) or {}
        site = vless_site or (None if p["need_pick"] else p["to"].get("sni"))
        if site and site == front.model.domain_of(st):
            site = None
        # none named: «the one you choose» — or, for an action of the system, the one the node picks (``vless_auto``)
        shown = site or tr("site.ops.vless_site_auto" if vless_auto else "gw_in_vless.consent.site_pick")
        text = tr("site.ops.vless_price_off", site=shown, short=tr("gw_in_vless.consent.short.a", site=shown), n=n,
                  who=who)
        # how to get back: the site off — switch it on again; the domain away (REV-vless-fix З2) — the domain, its
        # certificate and the site again (no site without a domain)
        if front.want_of(rec) == front.WANT_REALITY and front.consent_variant(rec) == "b":     # fit_consent: the
            text += " " + tr("site.ops.vless_price_clear_choice" if drop_domain     # choice goes with the site
                             else "site.ops.vless_price_off_choice")
        elif (rec.get("security") or "reality") == "tls":
            text += " " + tr("site.ops.vless_price_clear_back" if drop_domain else "site.ops.vless_price_off_back")
        if pick:
            text += " " + tr("site.ops.vless_need_pick")
    else:
        to = p["to"]
        text = tr("site.ops.vless_price_tls" if to.get("security") == "tls" else "site.ops.vless_price_port",
                  port=to.get("port"), n=n, who=who)
    raise ConflictError(text, data={"need": "vless_change", "kind": "site", "text": text,
                                    "site": vless_site or (p["to"].get("sni") if value == "domain" else None),
                                    "need_pick": bool(pick), "devices": p["devices"], "to": p["to"]})


def mode(runner, value, template_id=None, fresh=False, vless_want=None, vless=True, tx=False, yes=False,
         vless_site=None, via=None, who=None, vless_auto=False, keep_site=False):
    """Switch ``domain`` <-> ``site``; under the lock of a transition of VLESS (``front.vless_guard``, Н1). ``site``
    without ``template_id`` keeps the template already in the web root (the site switched on again looks as before —
    T-M6-qs330c №5); a new random one only with ``fresh`` (``site mode site --random``) or when the root has none.
    VLESS (SPEC-vless-tls-default §3.5–§3.7): the links of VLESS would change and devices have them — without ``yes``
    CONFLICT (``vless_price``); the site on that switches VLESS to TLS is done by ``gw.ops.vless_to_tls`` as a whole
    (a refusal = the site off again, Reality as it was). ``tx`` — called by that transition (no price, no hand-off,
    nginx refusing the socket is a refusal, not ``make_room``); ``vless_want`` — explicit for ``plan``; ``vless``
    False — the site off without the step of VLESS (the undo of a transition); ``vless_site`` — the cover of Reality
    when the site goes off and none is kept (§3.6 need_pick); ``vless_auto`` — an action of the system: none named,
    the core picks it (``vless_price``). ``keep_site`` — a compound action (the change of the domain and its undo, the
    wizard, the setup of VLESS): the site on must not hang on TLS — the transition refused (undone by its snapshot),
    the site goes on all the same and VLESS stays on Reality next to it (off 443, «tls_failed», REV-vless-sh2 №1)."""
    if value not in ("site", "domain"):
        raise KaskadError(tr("site.ops.bad_mode"), code=USAGE)
    with front.vless_guard(runner):
        return _mode(runner, value, template_id, fresh, vless_want, vless, tx, yes, vless_site, via, who, vless_auto,
                     keep_site)


def _mode(runner, value, template_id, fresh, vless_want, vless, tx, yes, vless_site, via, who, vless_auto=False,
          keep_site=False):
    st = _load()
    s = model.peek(st)
    fell = None                                         # the transition to TLS refused, the site goes on all the same
    if vless and not tx and s["domain"]:
        if vless_price(st, runner, value, yes=yes, vless_site=vless_site, vless_auto=vless_auto) is not None \
                and value == "domain" and vless_site:
            from ..gw.inbounds import vless as vin
            fields = vin.mask_for_mode(st, vless_site)                   # network: before the lock of state
            front._note([(k, fields[k]) for k in ("sni", "dest", "mask", "mask_check") if k in fields])
        rec = front._rec(st)
        if value == "site" and rec is not None and (rec.get("security") or "reality") != "tls" \
                and front.predict(st, runner, "site")["security"] == "tls":
            try:
                return _site_by_vless(runner, template_id, fresh, via, who)
            except KaskadError as e:
                v = (e.data or {}).get("vless") or {}
                if not keep_site or (e.data or {}).get("reason") != "vless_tls" or not v.get("restored"):
                    raise
                fell = v                                # undone by the snapshot: the site off, VLESS as it was
                st = _load()
                s = model.peek(st)
    if value == "site":
        if not s["domain"]:
            raise Refusal("no_domain", tr("site.ops.site_no_domain"), code=CONFLICT)
        d, _ = _site_cert(st)
        # VLESS of the gateway goes in front of the site (xray on 443, nginx behind it) when it can: then VLESS on 443 is
        # no conflict — it is the plan (site.front)
        # switching the site on is an explicit action for VLESS (SPEC-vless-tls-default §3.7): a record that wants TLS
        # tries it again (after a refusal too); a choice of Reality never goes in front — it steps off 443 for the site
        vwant = None if fell else (vless_want or front.site_want(st))
        behind = False if fell else front.will_front(st, runner, want=vwant)
        aside = not behind and front.steps_aside(st, force=bool(fell))
        site_checks(st, runner, for_vless=behind or aside)
        # The template first: its failure leaves everything as it was (no nginx installed, renewal untouched).
        deployed = (None if template_id is not None or fresh else _kept_template()) or \
            _deploy_template(runner, template_id)
        since = dict(front._rec(st) or {}) or None
        moved_aside = None
        try:
            if aside:
                moved_aside = front.step_aside(runner)
            try:
                out = _site_on(runner, s, d, deployed, behind=behind)
            except Refusal as e:
                if not behind or e.doc.get("reason") != "nginx_test" or tx:
                    raise
                # nginx refused to sit behind xray (no realip module, an old nginx…): the site takes 443 itself and
                # VLESS steps aside to another port, on Reality
                moved = front.make_room(runner, tr("site.ops.behind_refused", error=e))
                out = _site_on(runner, s, d, deployed, behind=False)
                if moved:
                    out["vless"] = moved
                    out.setdefault("warnings", []).append(moved["error"])
                    _reissue_warning(out, moved)
                    return out
        except Exception:
            if moved_aside is not None:
                front.step_back(runner, moved_aside)
            if s["mode"] != "site":
                _settle_owned(runner)
            raise
        if fell:
            out.setdefault("warnings", []).append(tr("site.ops.vless_tls_fell", why=front.tls_why(fell.get("why")) or "—"))
        return _with_vless(runner, out, want=vwant, since=since if moved_aside is not None else None)

    # Leaving site must leave a working configuration even if another person's
    # nginx file has become invalid since ours was enabled.
    if not vless:
        return _site_off(runner, s)
    return _with_vless(runner, _site_off(runner, s))


def _site_by_vless(runner, template_id, fresh, via, who):
    """The site on that switches VLESS Reality -> TLS: ``gw.ops.vless_to_tls`` does it as a whole (the price is asked
    already). -> the answer of the site with «vless»; the transition refused -> Refusal «vless_tls» (the site off
    again, VLESS as it was, ``restored``)."""
    from ..gw import ops as gops
    got = gops.vless_to_tls(runner, via=via or "cli", who=who, yes=True,
                            site_args={"template_id": template_id, "fresh": fresh})
    if not got.get("ok"):                               # the site is off again, VLESS as it was (``restored``)
        raise KaskadError(got.get("error") or "", code=ERROR,
                          data={"reason": "vless_tls", "vless": dict((k, v) for k, v in got.items() if k != "site_out")})
    out = got.pop("site_out", None) or {"ok": True, "site": status(runner)}
    out["vless"] = got
    if got.get("changed"):
        _reissue_warning(out, got)
        out["site"] = status(runner)
    return out


def _with_vless(runner, out, want=None, since=None):
    """After the site went on or off: VLESS of the gateway follows (site.front.sync — TLS in front of the site, or
    Reality); what the devices of VLESS need again goes into the answer («vless»: changed, security, reissue, text).
    ``want`` — explicit for ``plan`` (the site switched on, §3.7); ``since`` — the VLESS record before VLESS stepped
    off 443 for the site (the change is counted from it)."""
    if not out.get("ok") or (out.get("unchanged") and since is None):
        return out
    try:
        got = front.sync(runner, want=want, since=since)
    except KaskadError as e:
        util.log_tr("site.ops.log_vless_not_switched", error=e)
        out.setdefault("warnings", []).append(tr("site.ops.vless_not_switched", error=e))
        w = front.after_xray(runner)                    # nginx back where xray lets it be
        if w:
            out["warnings"].append(w)
        return out
    if got.get("security") is None:
        return out
    out["vless"] = got
    if got.get("error"):
        out.setdefault("warnings", []).append(got["error"])
    if got.get("changed"):
        _reissue_warning(out, got)
        out["site"] = status(runner)
    return out


def _reissue_warning(out, got):
    """VLESS changed its mode with the site (TLS <-> Reality, its port, its cover): the links given out stop working —
    the line of gw.ops._reissue («… — выданные раньше ссылки VLESS не подключатся. Выдайте их заново …») goes into
    ``warnings`` of the answer: the CLI prints them, the panel («Домен и сайт») and the wizard show them (3.3.0 T-M6 №5).
    Nothing when VLESS kept its mode or nobody was given a link."""
    text = got.get("text") if got.get("changed") else ""
    if text and text not in (out.get("warnings") or []):
        out.setdefault("warnings", []).append(text)


TEMPLATE_REASONS = ("download_failed", "no_index", "app_shell", "broken_assets", "timeout", "too_big", "bad_repo",
                    "nokey", "net", "gone")             # gone (3.2.21): удалён автором — site.tplhealth


def port80_site_text(p):
    return tr("site.ops.port80_site", who=p["who"])


def _deploy_template(runner, template_id):
    """templates.deploy for ``site mode site``; a failed template -> Refusal("template", template_reason=…)."""
    from . import templates
    try:
        deployed = templates.deploy(runner, template_id)
    except KaskadError as e:
        if e.code in (USAGE, LOCKED):
            raise
        why = (getattr(e, "data", None) or {}).get("reason")
        if why is None:                                 # git from apt did not install
            raise Refusal("apt", tr("site.ops.tpl_prepare", error=e), code=CONFLICT)
        treason = {"bad": "nokey", "nokey": "nokey", "net": "net"}.get(why, "download_failed")
        raise Refusal("template", tr("site.ops.tpl_failed", error=e), code=CONFLICT,
                      extra={"template_reason": treason, "template_id": template_id})
    if not deployed.get("ok"):
        treason = deployed.get("reason")
        treason = treason if treason in TEMPLATE_REASONS else "download_failed"
        raise Refusal("template", tr("site.ops.tpl_failed_kept",
                                     error=deployed.get("text") or templates_text(treason, template_id)),
                      code=CONFLICT,
                      extra={"template_reason": treason, "template_id": deployed.get("id") or template_id})
    return deployed


def _kept_template():
    """The template already in the web root, as a result of templates.deploy — or None (no site there, the stub, a
    removed root: ``site remove`` deletes it)."""
    try:
        from . import templates
        got = templates.status()
        tid = got.get("id")
        if not got.get("site") or not tid or got.get("stub"):
            return None
        rec = templates.get(tid) if templates.index_fresh() else None
    except (ImportError, KaskadError, OSError):
        return None
    util.log_tr("site.ops.log_tpl_kept", tid=tid)
    return {"ok": True, "id": tid, "name": (rec or {}).get("name") or tid, "source": got.get("source"),
            "fallback": False, "kept": True, "warnings": []}


def templates_text(code, tid):
    from . import templates
    return templates._text(code, tid or "?")


def _settle_owned(runner):
    """``site mode site`` failed half-way from domain mode: Kaskad's own nginx (maybe just installed — Debian enables
    it) must not hold :80 against certbot --standalone, now or after a reboot."""
    try:
        if runner.which("nginx") and nginx.owned() and (nginx.active(runner) or gsys.unit_enabled(runner, "nginx")):
            nginx.stop(runner)
    except KaskadError as e:
        util.log_tr("site.ops.log_settle_failed", error=e)


def _site_on(runner, s, d, deployed, behind=False):
    """The rest of ``site mode site`` once the template is in the root: nginx, renewal, vhost, panel proxy, UFW.
    ``behind`` — VLESS of the gateway is about to take 443 (site.front): the vhost listens on its socket at once."""
    try:
        nginx.ensure(runner)
    except KaskadError as e:
        raise Refusal("apt", tr("site.ops.nginx_install", error=e), code=CONFLICT)
    from ..panel import conf
    cfg = conf.load()
    panel_path = cfg["proxy"].get("path") or conf.new_proxy_path()
    old_renew = acme.set_renewal_mode(runner, d, "webroot", nginx.acme_root())
    util.ensure_dir(nginx.acme_root(), 0o755)
    if behind:
        front.ensure_sock_dir(runner)
    try:
        _write_site(runner, d, panel_path, behind=behind)
    except nginx.NginxTestError as e:
        acme.restore_renewal(d, old_renew)
        raise Refusal("nginx_test", _nginx_test_text(e), code=CONFLICT)
    except KaskadError:
        acme.restore_renewal(d, old_renew)
        raise
    _enable_proxy(runner, d, panel_path)
    opened = nginx.ufw_allow443(runner)

    def put(ns):
        ns.update(mode="site", ufw443=ns["ufw443"] or opened, check=None)
        ns.update(_template_state(deployed))
    _save_site(put)
    try:
        conf.write_access()     # 3.3.7.5: the links of people go to the domain now (the reload above may have run first)
    except OSError:
        pass
    util.log_tr("site.ops.log_site_on_stub" if deployed.get("fallback") else "site.ops.log_site_on", domain=d)
    return {"ok": True, "site": status(runner), "template": _template_doc(model.peek(_load())),
            "fallback": bool(deployed.get("fallback")), "warnings": list(deployed.get("warnings") or [])}


def _write_site(runner, d, panel_path, behind=None):
    """Our site vhost.  Kaskad's own nginx: Debian's «Welcome to nginx!» goes away and unknown names on :80 get 444 —
    the IP and scanners learn neither the server nor our domain; someone else's nginx keeps its default.  The 444 and
    the TLS handshake rejection only when no foreign vhost listens there.  ``behind`` (None — as xray of the gateway
    runs now, site.front.xray_fronts): the site listens on the socket behind xray instead of 443.  Every way the site
    is (re)written while it is on — ``site mode site``, the wizard, a new panel path, nginx moved behind xray or back —
    ends with nginx running AND enabled: domain mode had disabled it (3.3.0 T-M6 №2).
    -> True when nginx is Kaskad's own."""
    own = nginx.owned()
    if behind is None:
        behind = front.xray_fronts(runner)
    with _vhost_lock():     # not between a ``vhost --refresh`` read and its write (it would put the old path back)
        nginx.write(runner, d, panel_path, reject_handshake=not behind and nginx.can_reject_handshake(runner),
                    reject80=nginx.can_reject80(runner, skip_default=own), drop_default=own,
                    behind=front.sock_path() if behind else None)
    nginx.enable(runner)
    return own


def _site_off(runner, s):
    if not s["domain"]:
        raise Refusal("no_domain", tr("site.ops.off_no_domain"), code=CONFLICT)
    if s["mode"] != "site":
        # Already domain: the same arrangement of TCP/80 again (it repairs a node left by 3.2.x with Kaskad's nginx
        # still on :80 after the site was switched off).
        try:
            how = _http_for_domain(runner, s["domain"])
        except nginx.NginxTestError as e:
            raise Refusal("nginx_test", tr("site.ops.acme_refused", error=e), code=CONFLICT)
        acme.set_renewal_mode(runner, s["domain"], how, nginx.acme_root() if how == "webroot" else None)
        return {"ok": True, "site": status(runner), "unchanged": True}
    try:
        how = _http_for_domain(runner, s["domain"])
    except nginx.NginxTestError as e:
        raise Refusal("nginx_test", tr("site.ops.off_refused", error=e), code=CONFLICT)
    old_renew = acme.set_renewal_mode(runner, s["domain"], how, nginx.acme_root() if how == "webroot" else None)
    try:
        from ..panel import conf
        conf.set_proxy(False, domain="")
        consumers._reload_panel(runner)
        if s["ufw443"]:                     # VLESS on 443 (site.front) opens it again with its own rule (sync_ufw)
            nginx.ufw_delete443(runner)
    except Exception:
        acme.restore_renewal(s["domain"], old_renew)
        raise
    _save_site(lambda ns: ns.update(mode="domain", ufw443=False, check=None))
    util.log_tr("site.ops.log_site_off_webroot" if how == "webroot" else "site.ops.log_site_off_standalone")
    return {"ok": True, "site": status(runner)}


def template_set(runner, template_id=None):
    """Deploy a template and mirror its markers into state while site mode is on."""
    from . import templates
    res = templates.deploy(runner, template_id)
    if res.get("ok") and model.peek(_load())["mode"] == "site":
        _save_site(lambda ns: ns.update(_template_state(res)))
    return res


def panel_url(show=False):
    """The only core method which may return the full path (explicit ``show``). 3.3.7.5 — one address of the panel:
    the main one is the domain's (the site on, nginx) or else the open mode's (https://<адрес>:<порт>/<секрет>/);
    ``fallback`` — the open mode beside the domain (the way in by the port), masked unless ``show``.
    -> {"ok", "url", "masked", "kind": "domain"|"public", "fallback": str|None, "fallback_masked": str|None}."""
    from ..panel import conf
    st = _load()
    s = model.peek(st)
    cfg = conf.load()
    path = cfg["proxy"].get("path") or ""
    pub, pub_masked = conf.public_url(cfg), conf.public_url_masked(cfg)
    # the main address is the domain's exactly when the links of people are there — one condition (3.3.7.5 П-4)
    if path and conf.pages_domain(cfg):
        full = "https://%s/%s/" % (s["domain"], path)
        masked = "https://%s/%s/" % (s["domain"], conf.mask_path(path))
        return {"ok": True, "url": full if show else masked, "masked": masked, "kind": "domain",
                "fallback": (pub if show else pub_masked) if pub else None, "fallback_masked": pub_masked if pub else None}
    if pub:
        return {"ok": True, "url": pub if show else pub_masked, "masked": pub_masked, "kind": "public",
                "fallback": None, "fallback_masked": None}
    raise KaskadError(tr("site.ops.panel_off"), code=CONFLICT)


def rotate_panel_path(runner):
    """A new path first passes ``nginx -t``; the old path remains live on a refusal."""
    from ..panel import conf
    st = _load()
    if model.peek(st)["mode"] != "site":
        # 3.3.7.5: no site — the secret prefix of the open mode alone (no nginx to rewrite); the old one stops at once
        if not conf.public_url(conf.load()):
            raise KaskadError(tr("site.ops.path_site_only"), code=CONFLICT)
        conf.rotate_secret()
        consumers._reload_panel(runner)
        util.log_tr("site.ops.log_path_rotated")
        return panel_url(show=False)
    d, _ = _site_cert(st)
    new_path = conf.new_proxy_path()
    try:
        _write_site(runner, d, new_path)
    except nginx.NginxTestError as e:
        raise Refusal("nginx_test", tr("site.ops.path_refused", error=e), code=CONFLICT)
    _enable_proxy(runner, d, new_path)
    util.log_tr("site.ops.log_path_rotated")
    return panel_url(show=False)


def _curl_code(runner, url, resolve):
    res = runner.run(["curl", "--max-time", "10", "--resolve", resolve, "-sS", "-o", "/dev/null", "-w",
                      "%{http_code}", url], timeout=15)
    got = (res.out or "").strip().splitlines()
    code = got[-1].strip() if got else ""
    return int(code) if code.isdigit() and len(code) == 3 else 0


def _check_view(doc):
    """The stored last check for a reader: its warnings said again in the language of the call from their codes (the
    check stored them in the language of its own moment)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("warning_codes"), list):
        return doc
    out = dict(doc)
    out["warnings"] = [warning(c) for c in doc["warning_codes"] if c in (NO_EMAIL, DEFAULT_PAGE, NGINX_BOOT)]
    return out


def _store_check(doc):
    doc = dict(doc)
    doc["checked_at"] = util.now_iso()
    _save_site(lambda s: s.update(check=doc))
    return doc


def check(runner):
    """A local curl through ``--resolve``: useful health evidence, not fake external proof."""
    st = _load()
    s = model.peek(st)
    if s["mode"] != "site":
        return _store_check({"ok": False, "verdict": "no_site"})
    d = s["domain"]
    cert = cert_doc(st)
    if not d or not cert or cert.get("staging"):
        return _store_check({"ok": False, "verdict": "no_cert"})
    dns_doc = s["dns"] or {}
    own = next((x for x in dns_doc.get("own") or [] if ":" not in x), None)
    if not dns_doc.get("ready") or not own:
        return _store_check({"ok": False, "verdict": "dns_mismatch"})
    from ..panel import conf
    path = conf.load()["proxy"].get("path") or ""
    if not path:
        return _store_check({"ok": False, "verdict": "panel_unreachable"})
    root = _curl_code(runner, "https://%s/" % d, "%s:443:%s" % (d, own))
    panel = _curl_code(runner, "https://%s/%s/" % (d, path), "%s:443:%s" % (d, own))
    miss = _curl_code(runner, "https://%s/app-%s/" % (d, "x" * 86), "%s:443:%s" % (d, own))
    http = _curl_code(runner, "http://%s/" % d, "%s:80:%s" % (d, own))
    if root < 200 or root >= 400 or http != 301 or miss != 404:
        return _store_check({"ok": False, "verdict": "http_error", "http": {"root": root, "redirect": http}})
    if panel not in (200, 302):
        doc = {"ok": False, "verdict": "panel_unreachable", "http": {"panel": panel}}
        problem = conf.proxy_access_problem() if panel in (0, 502, 503, 504) else None
        if problem:                                # 3.2.21: /run/kaskad 0700 — nginx without the panel's socket (502)
            doc.update(why=problem["why"], fix=problem["fix"])
        return _store_check(doc)
    if cert.get("days_left", 0) < MIN_DAYS:
        return _store_check({"ok": False, "verdict": "cert_expiring", "days_left": cert.get("days_left")})
    codes = _check_warning_codes(runner)
    return _store_check({"ok": True, "verdict": "ok", "warnings": [warning(c) for c in codes], "warning_codes": codes})


def _check_warnings(runner):
    """The texts of ``_check_warning_codes``."""
    return [warning(c) for c in _check_warning_codes(runner)]


def _check_warning_codes(runner):
    """What does not break the site now but will or gives the server away (someone else's nginx keeps Debian's
    default; nginx not enabled — the next reboot takes the site down, 3.3.0 T-M6 №2)."""
    out = []
    if runner.which("nginx") and nginx.default_page():
        out.append(DEFAULT_PAGE)
    if runner.which("nginx") and not gsys.unit_enabled(runner, "nginx"):
        out.append(NGINX_BOOT)
    return out


def nginx_problems(runner, st=None, repair=False, log=None):
    """The minute watchdog (``net check``) and «Проверка сервера»: while the site is on, nginx serves it — it must run
    and start with the system (3.3.0 T-M6 №2: nginx left disabled since domain mode, the site died with a reboot).
    ``repair``: ``systemctl enable --now --no-block nginx`` (never waits on a start). -> problems
    [{"kind": "site_nginx", "detail", "repaired", "error"?}]."""
    st = _load() if st is None else st
    s = model.peek(st)
    if s["mode"] != "site" or not s["domain"] or not runner.which("nginx") or not nginx.ours():
        return []
    on, boot = nginx.active(runner), gsys.unit_enabled(runner, "nginx")
    if on and boot:
        return []
    where = model.unicode(s["domain"])
    if not on:
        detail = tr("site.ops.nginx_down" if boot else "site.ops.nginx_down_noboot", domain=where)
    else:
        detail = tr("site.ops.nginx_noboot", domain=where)
    p = {"kind": "site_nginx", "detail": detail, "repaired": False}
    if repair:
        res = runner.run(["systemctl", "enable", "--now", "--no-block", "nginx"])
        if res.ok:
            p["repaired"] = True
            (log or (lambda kind, text: util.log_action(text)))("site_nginx",
                                                                tr("site.ops.log_repaired", detail=detail))
        else:
            p["error"] = nginx._tail(res)
    return [p]


# ── SSTP and OpenConnect ───────────────────────────────────────────────────

def use(runner, kind, on, dry_run=False):
    """See the module doc. -> {"ok", "use": {"sstp", "ocserv"}, "affected": [...], "text", "warnings", "dry_run"?}."""
    from ..gw import ops as gops
    if kind not in model.USE_KINDS:
        raise KaskadError(tr("site.ops.use_kind"), code=USAGE)
    st = _load()
    s = model.peek(st)
    d = s["domain"]
    on = bool(on)
    if on:
        _need_domain(st)
        info = model.active(st)
        if info is None:
            raise KaskadError(tr("site.ops.use_no_cert", domain=model.unicode(d)), code=CONFLICT)
    title = TITLES[kind]
    same = s["use"][kind] == on
    affected = [] if same else _affected(st, [kind])
    what = tr("site.ops.what.use_on", domain=model.unicode(d)) if on else tr("site.ops.what.use_off")
    text = reissue_text(affected, what)
    warnings = []
    from ..gw import model as gm
    if on and kind not in gm.installed_kinds(st):
        warnings.append(tr("site.ops.use_not_installed", title=title))
    if on and (model.active(st) or {}).get("staging"):
        warnings.append(tr("site.ops.use_staging", title=title))
    if dry_run or same:
        use_now = dict(s["use"])
        if dry_run and not same:
            use_now[kind] = on
        out = {"ok": True, "use": use_now, "affected": affected, "text": text, "warnings": warnings}
        if dry_run:
            out["dry_run"] = True
        return out

    def mutate(new, sec):
        model.site(new)["use"][kind] = on
    _, res = gops._change(runner, mutate)
    for w in (res or {}).get("warnings") or []:
        warnings.append(util.redact(w))
    util.log_tr("site.ops.log_use_on" if on else "site.ops.log_use_off", title=title, domain=d, n=len(affected))
    return {"ok": True, "use": model.peek(_load())["use"], "affected": affected, "text": text, "warnings": warnings}


# ── the watchdog ───────────────────────────────────────────────────────────

def tick(runner):
    """kaskad-site.timer, daily: a new certificate the hook did not bring is handed out; under 21 days left —
    ``certbot renew``; it did not help — an event in the journal and a line to the bot.
    -> {"ok", "days_left", "action": "none"|"ok"|"installed"|"renewed"|"failed"|"missing"}."""
    st = _load()
    s = model.peek(st)
    d = s["domain"]
    if not d or s["cert"] != "letsencrypt":
        return {"ok": True, "days_left": None, "action": "none"}
    _free_owned_80(runner, s)
    live = _live(d)
    if live is None:
        util.log_tr("site.ops.log_tick_missing", domain=d, path=model.live_dir(d))
        return {"ok": True, "days_left": None, "action": "missing"}
    action = "ok"
    copy_now = model.copy_info()
    if copy_now is None or copy_now["sha256"] != live["sha256"]:
        try:
            consumers.cert_installed(runner)
            action = "installed"
        except KaskadError as e:
            msg = tr("site.ops.tick_not_handed", domain=model.unicode(d), error=e)
            util.log_action("SITE: " + msg)
            _notify("⚠️ Kaskad: " + msg)
            return {"ok": True, "days_left": certinfo.days_left(live), "action": "failed", "text": msg}
    days = certinfo.days_left(live)
    if days < WARN_DAYS:
        reason = None
        if acme.have_certbot(runner):
            res, text = acme.run_certbot(runner, acme.renew_argv(d))
            reason = None if res.ok else acme.cause(text)
        else:
            reason = "apt"
        new = _live(d) or live
        if new["sha256"] != live["sha256"]:
            try:
                consumers.cert_installed(runner)
            except KaskadError as e:
                msg = tr("site.ops.tick_renewed_not_handed", domain=model.unicode(d), until=fmt_date(new["not_after"][:10]),
                         error=e)
                util.log_action("SITE: " + msg)
                _notify("⚠️ Kaskad: " + msg)
                return {"ok": True, "days_left": certinfo.days_left(new), "action": "failed", "text": msg}
            util.log_tr("site.ops.log_tick_renewed", domain=d, until=new["not_after"][:10])
            return {"ok": True, "days_left": certinfo.days_left(new), "action": "renewed"}
        why = _refusal_text(runner, d, reason, what="renew") if reason in ("limit", "net", "dns", "port80") \
            else tr("site.ops.certbot_not_renewed" if reason is None else "site.ops.certbot_not_installed")
        msg = trn("site.ops.expiring", max(days, 0), domain=model.unicode(d), date=fmt_date(live["not_after"][:10]), why=why)
        util.log_action("SITE: " + msg)
        _notify("⚠️ Kaskad: " + msg)
        return {"ok": True, "days_left": days, "action": "failed", "text": msg}
    return {"ok": True, "days_left": days, "action": action}


# ── removal ────────────────────────────────────────────────────────────────

def remove(runner):
    """The removal of Kaskad (menu 15): clear + /etc/kaskad/site and the key ``site`` of state.json. The certificates
    of Let's Encrypt stay (limits); ``packages`` — what Kaskad installed for the domain (the menu's purge line).

    Only ``/etc/kaskad/site/pkgs`` stays while those packages are still installed: who installed nginx does not change
    with the domain gone, and a site switched on again later must find nginx Kaskad's own — else Debian's «Welcome to
    nginx!» stayed and nginx was never enabled (3.3.0 T-M6 №2, №16). The full removal (install.sh) deletes
    /etc/kaskad anyway."""
    import shutil
    packages = model.read_pkgs()
    res = clear(runner, yes=True, vless_auto=True)  # the removal is an action of the system: never stuck on the price
    if nginx.restore_default(runner):
        util.log_tr("site.ops.log_default_back")
    acme.remove_units(runner)
    nginx.remove_roots()
    front.drop_sock_dir()
    shutil.rmtree(model.site_dir(), ignore_errors=True)
    still = [p for p in packages if runner.which(p)]
    if still:
        model.add_pkgs(still)
    with state.locked():
        st = state.load()
        if "site" in st:
            st = copy.deepcopy(st)
            st.pop("site", None)
            state.save(st)
    util.log_tr("site.ops.log_removed")
    return {"ok": True, "kept": res.get("kept") or [], "packages": packages, "affected": res.get("affected") or []}
