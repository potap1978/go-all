"""Who answers on TCP/443 when the node has both the site and VLESS (docs/k3/SPEC-vless-inbound.md §4.4, §5).

Two layouts:

    nginx in front   (no VLESS on 443 / VLESS Reality on another port / VLESS off)
                     nginx: ``listen 443 ssl`` with the certificate of the domain — as wave 4.0-C made it
    xray in front    (``site mode site`` + VLESS in mode «tls»)
                     xray of the gateway: ``0.0.0.0:443`` (``[::]:443`` with IPv6) VLESS + TLS with the certificate of
                     the domain, ``rejectUnknownSni``, ``xtls-rprx-vision``; everything that is not VLESS — a browser,
                     a scanner, Let's Encrypt, the panel on the domain, the pages of people /p/, the subscription —
                     xray hands over decrypted to its ``fallback``: nginx on the Unix socket
                     /run/kaskad-vless/site.sock with the PROXY protocol (the real address of the visitor for the bans
                     and the limits of /p/). Port 80 stays nginx's in both layouts (redirect, Let's Encrypt).

The truth of the layout is what xray of the gateway RUNS (``xray_fronts``: /etc/kaskad/gw/xray.json has VLESS + TLS on
443), not what state.json wishes: nginx must never listen on 443 while xray holds it, and must never stay behind a
socket nobody connects to. ``before_xray`` (nginx goes behind before xray takes 443) and ``after_xray`` (nginx comes
back to 443 after xray let it go — or stopped) keep the two in step around every ``xray.apply``/``stop``/``remove``.

``sync(runner)`` decides the mode of VLESS from the node (the owner's rule: a domain with a site — TLS; only an IP —
Reality) and switches it: called after ``site mode site|domain``, ``site domain clear``, ``gw inbound install vless``
and ``gw vless sync``. A switch changes what the devices were given: ``reissue`` lists them (as a change of keys).

The socket's directory: /run/kaskad-vless, 0750 root:kaskad-xray — only xray of the gateway (and root) reach it
(nginx makes its sockets 0666 — the directory is the lock); /etc/tmpfiles.d/kaskad-vless.conf brings it back at boot
before nginx starts.
"""

import contextlib
import copy
import errno
import fcntl
import json
import os
import re
import threading

from .. import paths, state, util
from ..errors import ConflictError, KaskadError
from ..i18n import tr
from . import model

SOCK_DIR = "/run/kaskad-vless"
SOCK_NAME = "site.sock"
TMPFILES = "/etc/tmpfiles.d/kaskad-vless.conf"
VLESS = "vless"
TLS_PORT = 443
REALITY_PORTS = (8443,)          # VLESS Reality while nginx holds 443 (the site is on)

# The choice of the mode (k3/handoff/SPEC-vless-tls-default.md §3.1). ``want`` of the record: what the person chose (or
# the install by default); no ``want`` — a record made before this version («legacy»: the mode follows the site, §3.4).
WANT_TLS = "tls"
WANT_REALITY = "reality"                    # Reality with a domain — only with a valid consent (reality_consent)
WANT_NODOMAIN = "reality_nodomain"          # Reality chosen while there was no domain: for ``plan`` the same as "tls"
WANTS = (WANT_TLS, WANT_REALITY, WANT_NODOMAIN)
VLESS_VIAS = ("panel", "ssh", "cli", "mcp", "setup")     # no bot: it never writes the choice of Reality (К2, П2)
RISKS_VER = "r1"                            # the text of the risks shown now (§4.3); + "a" (a site of others) | "b" (own)
RISKS_KNOWN = ("r1",)                       # versions a consent may name
RISKS_ASK_AGAIN = frozenset()               # old versions whose consent is void (a change of meaning): ask again
RISKS_VARIANTS = ("a", "b")
_RISKS_RE = re.compile(r"(r[0-9]{1,3})([a-z])")         # fullmatch: no "r1a\n"
# Reality with these reasons is forced (no choice of the person, no consent, ``want`` unchanged — В13)
FORCED = ("vless_off", "no_domain", "no_cert", "busy_443", "no_site", "tls_failed")


def sock_dir():
    return paths.P(SOCK_DIR)


def sock_path():
    return os.path.join(sock_dir(), SOCK_NAME)


def tmpfiles_path():
    return paths.P(TMPFILES)


# ── the state side ─────────────────────────────────────────────────────────

def _rec(st):
    from ..gw.inbounds import tunnel
    return tunnel.inbound_cfg(st, VLESS)


def vless_port(st):
    rec = _rec(st)
    p = (rec or {}).get("port")
    return p if isinstance(p, int) and not isinstance(p, bool) else None


def site_ready(st, ignore_mode=False):
    """(True, "") when the node has its site on 443 with a production certificate of Let's Encrypt; else (False, why).
    ``ignore_mode`` — the site is about to go on (``site mode site``): only the domain and the certificate count."""
    s = model.peek(st)
    if not s["domain"]:
        return False, tr("site.front.no_domain")
    if s["mode"] != "site" and not ignore_mode:
        return False, tr("site.front.no_site")
    info = model.active(st)
    if s["cert"] != "letsencrypt" or s["staging"] or info is None or info.get("staging"):
        return False, tr("site.front.no_cert", domain=model.unicode(s["domain"]))
    return True, ""


def tls_wanted(st):
    """The record says TLS and nothing in state forbids it (the gateway and VLESS on, the site on with a certificate of
    Let's Encrypt, the copy of the certificate in place). The adapter renders VLESS + TLS exactly then. The expiry is
    not looked at here: a certificate that ran out (the watchdog warned for 21 days) must not flip the layout of 443 by
    itself — the site and VLESS keep their places, the renewal brings a new copy."""
    from ..gw.inbounds import tunnel
    rec = _rec(st)
    if rec is None or rec.get("security") != "tls" or not tunnel.wanted(st, VLESS):
        return False
    s = model.peek(st)
    if tunnel.port_value(rec, "port", 0) != TLS_PORT or s["mode"] != "site" or not s["domain"] \
            or s["cert"] != "letsencrypt" or s["staging"]:
        return False
    return os.path.isfile(model.fullchain_path()) and os.path.isfile(model.privkey_path())


def _deployed():
    from ..gw import xray
    try:
        with open(xray.config_path(), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    return cfg if isinstance(cfg, dict) else None


def _running(runner):
    """xray of the gateway runs (None runner — not asked: the file alone)."""
    if runner is None:
        return True
    from ..gw import sys as gsys
    from ..gw import xray
    try:
        return gsys.unit_active(runner, xray.UNIT)
    except Exception:                                   # noqa: BLE001 — no answer: not running
        return False


def xray_fronts(runner=None):
    """xray of the gateway holds 443 with VLESS + TLS now: the deployed /etc/kaskad/gw/xray.json says so AND (with a
    ``runner``) the service runs — after ``gw disable`` or a failed start the file still says TLS, nobody listens."""
    return config_fronts(_deployed()) and _running(runner)


_UDP_PROTOCOLS = ("hysteria", "hysteria2")              # QUIC: UDP only
_UDP_NETWORKS = ("hysteria", "kcp", "mkcp", "quic")     # transports of xray over UDP


def _on_tcp(ib):
    """The inbound of xray listens on TCP: Hysteria2 (443/udp by default — the wizard's protocols) and the UDP
    transports do not, a dokodemo-door only with "tcp" in its ``network`` (its default)."""
    if ib.get("protocol") in _UDP_PROTOCOLS:
        return False
    if str((ib.get("streamSettings") or {}).get("network") or "tcp").lower() in _UDP_NETWORKS:
        return False
    if ib.get("protocol") == "dokodemo-door":
        nets = str((ib.get("settings") or {}).get("network") or "tcp").lower()
        return "tcp" in [n.strip() for n in nets.split(",")]
    return True


def xray_holds_443(runner=None):
    """xray of the gateway listens on TCP 443 with anything (VLESS Reality too) — nginx cannot have 443 then.
    Hysteria2 on 443/udp is no holder (T-3378 Н-3: nginx stayed behind a socket nobody connects to)."""
    cfg = _deployed()
    held = any(isinstance(ib, dict) and ib.get("port") == TLS_PORT and _on_tcp(ib)
               for ib in (cfg or {}).get("inbounds") or [])
    return held and _running(runner)


def config_fronts(cfg):
    """The same for a config dict about to be written."""
    return any(isinstance(ib, dict) and ib.get("protocol") == "vless" and ib.get("port") == TLS_PORT
               and (ib.get("streamSettings") or {}).get("security") == "tls" for ib in (cfg or {}).get("inbounds") or [])


# ── the nginx side ─────────────────────────────────────────────────────────

def nginx_behind():
    """Our site vhost listens on the socket (behind xray), not on 443."""
    from . import nginx
    return ("unix:" + sock_path()) in (util.read_text(nginx.vhost_path()) or "")


def ensure_sock_dir(runner=None):
    """/run/kaskad-vless 0750 root:kaskad-xray and its tmpfiles.d line (the directory after a reboot)."""
    import grp
    from ..gw import net
    util.ensure_dir(sock_dir(), 0o750)
    try:
        gid = grp.getgrnam(net.XRAY_USER).gr_gid
        os.chown(sock_dir(), 0, gid)
    except (KeyError, OSError):
        pass                                    # tests and a node without the user yet: root-only, xray retries later
    line = "d %s 0750 root %s -\n" % (SOCK_DIR, net.XRAY_USER)
    if util.read_text(tmpfiles_path()) != "# Kaskad 3: the socket of the site behind xray (VLESS + TLS on 443)\n" + line:
        util.ensure_dir(os.path.dirname(tmpfiles_path()), 0o755)
        util.atomic_write(tmpfiles_path(), "# Kaskad 3: the socket of the site behind xray (VLESS + TLS on 443)\n" + line,
                          0o644)


def drop_sock_dir():
    for p in (tmpfiles_path(), sock_path()):
        try:
            os.unlink(p)
        except OSError:
            pass
    try:
        os.rmdir(sock_dir())
    except OSError:
        pass


def _rewrite(runner, behind):
    """Our site vhost again with nginx in front or behind (the same domain and panel path). -> True if rewritten."""
    from . import nginx
    from . import ops as sops
    st = state.load()
    s = model.peek(st)
    if not nginx.ours() or s["mode"] != "site" or not s["domain"]:
        return False
    from ..panel import conf
    path = conf.load()["proxy"].get("path") or ""
    if not path:
        return False
    if behind:
        ensure_sock_dir(runner)
    sops._write_site(runner, s["domain"], path, behind=behind)
    if not behind and s["ufw443"]:
        try:
            nginx.ufw_allow443(runner)              # VLESS took the shared rule with it: the site's own back
        except KaskadError:
            pass
    if behind:
        util.log_tr("site.front.log_behind", sock=sock_path())
    else:
        util.log_tr("site.front.log_back")
    return True


def before_xray(runner, cfg):
    """xray is about to take 443 (``cfg`` fronts) while nginx is in front: nginx goes behind first (its 443 free).
    KaskadError (nginx refused) — xray must not take 443: the caller keeps its old config."""
    if config_fronts(cfg) and not nginx_behind():
        return _rewrite(runner, True)
    return False


def after_xray(runner, stopped=False):
    """nginx follows what xray runs now: behind while xray fronts, else back on 443 — unless xray holds 443 with
    Reality (nginx could not bind it; ``sync`` moves VLESS away). Never raises: "" or a warning text."""
    try:
        fronts = not stopped and xray_fronts(runner)
        if fronts and not nginx_behind():
            _rewrite(runner, True)
        elif not fronts and nginx_behind():
            if not stopped and xray_holds_443(runner):
                why = tr("site.front.behind_no_tls")
                util.log_tr("site.front.log_behind_no_tls")
                return why
            _rewrite(runner, False)
    except Exception as e:                              # noqa: BLE001 — never break gw apply / disable
        util.log_tr("site.front.log_not_rewritten", error=e)
        return tr("site.front.not_rewritten", error=e)
    return ""


# ── the mode of VLESS ──────────────────────────────────────────────────────

def _panel_443():
    """The open mode of the panel listens on 443 itself (panel/conf.py): VLESS cannot take 443 (З10)."""
    try:
        from ..panel import conf
        pub = conf.load().get("public") or {}
    except Exception:                                   # noqa: BLE001 — no readable config: not on 443
        return False
    return bool(pub.get("on")) and pub.get("port") == TLS_PORT


def _foreign_443_kind(runner, st):
    """Who but our site and the gateway's VLESS holds 443: ("", "") — nobody; ("panel_443", text) — the open mode of
    the panel; ("busy", text) — another nginx vhost, a neighbour, a listener, a forward, Docker. ``runner`` None — only
    what the files say (the neighbours, the panel's config): no ``ss``, no ``nginx -T`` (a read for people)."""
    from . import nginx
    from . import ops as sops
    if nginx.neighbours(runner):
        return "busy", tr("site.front.neighbour", names=", ".join(nginx.neighbours(runner)))
    if _panel_443():
        return "panel_443", tr("site.front.panel_443")
    if runner is None:
        return "", ""
    if nginx._foreign_listen(runner, 443):
        return "busy", tr("site.front.foreign_vhost")
    p = sops.port443(st, runner, for_vless=True)
    return ("", "") if p["free"] else ("busy", tr("site.front.busy443", who=p["who"]))


def _foreign_443(runner, st):
    """Someone but our site and the gateway's VLESS holds 443 (another nginx vhost, a listener, a forward, Docker, the
    open mode of the panel): a text, else ""."""
    return _foreign_443_kind(runner, st)[1]


def want_of(rec):
    """``want`` of the VLESS record — "tls" | "reality" | "reality_nodomain", or None (a record made before the choice
    existed, or a value this version does not know: the mode follows the site as in 3.3.7, §3.4)."""
    w = (rec or {}).get("want")
    return w if w in WANTS else None


def site_want(st):
    """What switching the site ON asks of VLESS explicitly (§3.7: the site on is an explicit action — TLS again after a
    refusal): "tls" for a record that wants TLS (or chose Reality without a domain), else None (a «legacy» record
    follows the site anyway; a choice of Reality stays Reality)."""
    return WANT_TLS if want_of(_rec(st)) in (WANT_TLS, WANT_NODOMAIN) else None


def will_front(st, runner, want=None):
    """``site mode site`` is about to run: will VLESS go in front of the site (then nginx starts behind at once)?
    A choice of Reality (``want`` "reality" of the record — with a consent or with a broken one, §3.1 п.1/п.3) never
    goes in front: the site takes 443 itself, VLESS Reality steps aside (``steps_aside``). An explicit ``want`` "tls"
    overrides the record (the transition R→T)."""
    if want is None and want_of(_rec(st)) == WANT_REALITY:
        return False
    return tls_possible(st, runner, ignore_mode=True)[0]


def steps_aside(st, force=False):
    """``site mode site`` with VLESS chosen on Reality and on 443: VLESS moves off 443 (REALITY_PORTS, else a free one)
    before nginx takes it — the security and the cover stay. Only for a choice of Reality: a «legacy» record or one that
    wants TLS keeps the old refusal («443 is held by VLESS») — unless ``force``: its transition to TLS was refused and
    undone, the site goes on all the same (``site.ops.mode`` ``keep_site``)."""
    rec = _rec(st)
    from ..gw.inbounds import tunnel
    return rec is not None and (force or want_of(rec) == WANT_REALITY) and vless_port(st) == TLS_PORT \
        and (rec.get("security") or "reality") != "tls" and tunnel.wanted(st, VLESS)


def step_aside(runner):
    """VLESS (Reality, a choice) leaves 443 for the site: the port only. -> the record's fields before (for ``undo``)."""
    from ..gw import model as gm
    from ..gw import ops as gops
    st = state.load()
    rec = _rec(st) or {}
    before = dict((k, rec.get(k)) for k in ("port",))
    port = gops.free_port_for(st, runner, VLESS, REALITY_PORTS)

    def mutate(new, sec):
        gm.inbound(new, VLESS).update(port=port)
    gops._change(runner, mutate)
    util.log_tr("site.front.log_aside", port=port)
    return before


def step_back(runner, before):
    """``site mode site`` failed after ``step_aside``: VLESS back on its port (best effort, never raises)."""
    from ..gw import model as gm
    from ..gw import ops as gops
    try:
        def mutate(new, sec):
            r = gm.inbound(new, VLESS)
            if r is not None:
                r.update(before)
        gops._change(runner, mutate)
    except Exception as e:                              # noqa: BLE001 — the refusal of the site is what people see
        util.log_tr("site.front.log_not_rewritten", error=e)


def tls_possible(st, runner, ignore_mode=False):
    """(True, "") when VLESS can take 443 with TLS in front of the site now; else (False, why — for people)."""
    from ..gw.inbounds import tunnel
    rec = _rec(st)
    if rec is None:
        return False, tr("site.front.no_vless")
    if not tunnel.wanted(st, VLESS):
        return False, tr("site.front.vless_off")
    ok, why = site_ready(st, ignore_mode=ignore_mode)
    if not ok:
        return False, why
    if not (os.path.isfile(model.fullchain_path()) and os.path.isfile(model.privkey_path())):
        return False, tr("site.front.no_copy")
    why = _foreign_443(runner, st)
    return (False, why) if why else (True, "")


def consent_ok(rec):
    """The record holds a valid consent to Reality with a domain (the form of ``rfgate._consent``, SEC-s1-r2 З1): a
    dict with a time ``at``, ``via`` of VLESS_VIAS and ``risks`` — a known version of the text with its variant
    ("r1a" | "r1b"), not one of RISKS_ASK_AGAIN. Anything else — ``{}``, ``{"at": None}``, ``via`` "bot", a version to
    ask again — is no consent (``consent_bad``). The domain is not compared (the same owner changing the domain keeps
    the consent — В6; the import of a copy checks it itself)."""
    c = (rec or {}).get("reality_consent")
    if not isinstance(c, dict):
        return False
    at, via, risks = c.get("at"), c.get("via"), c.get("risks")
    if not isinstance(at, str) or not at.strip() or via not in VLESS_VIAS or not isinstance(risks, str):
        return False
    m = _RISKS_RE.fullmatch(risks)
    if not m or m.group(2) not in RISKS_VARIANTS:
        return False
    return m.group(1) in RISKS_KNOWN and m.group(1) not in RISKS_ASK_AGAIN


def consent_variant(rec):
    """The variant of the text a valid consent of the record was given to — "a" (a foreign cover site) | "b" (the
    node's own site), or None (no valid consent, ``consent_ok``)."""
    if not consent_ok(rec):
        return None
    return _RISKS_RE.fullmatch(rec["reality_consent"]["risks"]).group(2)


def mask_needs_consent(rec, mask):
    """SEC-vless-sh12 №7 (решение координатора): a consent to variant B («свой сайт», r1b) is not one to a foreign cover:
    Reality chosen with a domain whose cover becomes ``mask`` (not «own») needs a new consent to the text of variant A
    first. -> bool."""
    return want_of(rec) == WANT_REALITY and mask != "own" and consent_variant(rec) == "b"


def fit_consent(r):
    """The same rule where nobody is asked (a cover picked by the system: the site off, a step aside, ``sync``): the
    consent of variant B goes and the choice of Reality becomes «no choice» (``want`` "tls", §3.3 — as the import of a
    copy does): Reality under a foreign cover is never had on a consent to the text of the own site. Changes ``r``;
    -> True when it went (the caller writes the journal line, ``log_consent_gone``)."""
    if not mask_needs_consent(r, r.get("mask")):
        return False
    r.pop("reality_consent", None)
    r["want"] = WANT_TLS
    return True


def log_consent_gone(gone):
    if gone:
        util.log_tr("gw_in_vless.log.consent_b_gone")


def devices_of(st):
    """The devices with VLESS in their access — whom to give the links again when they change."""
    from ..gw import ops as gops
    return gops._reissue_devices(st, VLESS)


def plan(st, runner, pick=None, want=None):
    """What VLESS should be now: {"security", "port", "fields": {…record changes…}, "why", "reason", "want",
    "explicit", "auto"} (``why`` — why not TLS, for people). The only place the mode is decided (§3.1):

    ``want`` — EXPLICIT: only the transition passes it (a decision of the person, written into state by the caller
    after a success). Without it — IMPLICIT (any ``sync``): ``want`` of the record, and nothing is done the person
    did not ask for (З-1):
      1. "reality" with a valid consent (or no domain) — Reality; the site on — off 443; the cover kept, else own;
      2. "tls" | "reality_nodomain": TLS impossible — Reality (forced, the reason; only the site off, never on here,
         with devices — no change, «ready»: the site goes on with the transition, Н-17); explicit — TLS; implicit:
         on TLS — stays; a refusal of TLS before (``tls_error``) — no change («tls_failed»); devices of VLESS — no change
         («ready», a recommendation with the price); nobody to give links again — TLS («auto», §3.3);
      3. "reality" with a domain and no valid consent — no change («consent_bad»);
      4. no ``want`` (a record of 3.3.7) — as before: TLS exactly when the site is ready.
    «No change» still moves Reality off 443 when the site holds it (nginx owns 443 then).
    ``pick(st)`` — a Reality cover site by the country (network; vless.pick_mask) when one is needed."""
    rec = dict(_rec(st) or {})
    explicit = want is not None
    w = want if explicit else want_of(rec)
    ok, why = tls_possible(st, runner)
    cur_sec = rec.get("security") or "reality"
    out = {"want": w, "explicit": explicit, "auto": False}

    def tls():
        out.update(security="tls", port=TLS_PORT, fields={"security": "tls", "port": TLS_PORT}, why="", reason="")
        return out

    def same(reason, text):
        fields = {}
        if cur_sec != "tls" and model.peek(st)["mode"] == "site" and rec.get("port") == TLS_PORT:
            fields["port"] = None                  # nginx holds 443: Reality cannot stay there (caller: a free port)
        if cur_sec != "tls" and rec.get("mask") == "own":
            from ..gw.inbounds import vless as vin
            if not vin.own_domain(st):             # the own site went off: its 443 answers nobody — a forced change of
                fields.update((pick or vin.pick_mask)(st))      # the cover only (the mode and ``want`` stay; З-2)
        out.update(security=cur_sec, port=fields.get("port", rec.get("port")), fields=fields, why=text,
                   reason=reason)
        return out

    def reality(reason, text):
        out.update(_reality_fields(st, rec, pick), why=text, reason=reason)
        return out

    domain = bool(model.domain_of(st))
    if w == WANT_REALITY and domain and not consent_ok(rec):
        return same("consent_bad", tr("site.front.consent_bad"))
    if w == WANT_REALITY:
        return reality("chosen" if domain else "no_domain", tr("site.front.chosen") if domain else why)
    if w is None:                                   # legacy: TLS ⇔ the site is ready (3.3.7)
        return tls() if ok else reality(_why_reason(st, runner) if not ok else "", why)
    if not ok:
        if not explicit and site_due(st, rec, w) and tls_possible(st, runner, ignore_mode=True)[0]:
            return same("ready", tr("site.front.ready"))    # Н-17: the site goes on with the transition — no change
        return reality(_why_reason(st, runner), why)
    if explicit or cur_sec == "tls":
        return tls()
    if isinstance(rec.get("tls_error"), dict):
        return same("tls_failed", tr("site.front.tls_failed"))
    if devices_of(st):
        return same("ready", tr("site.front.ready"))
    out["auto"] = True                              # §3.3: nobody to give links again — TLS by itself (cost 0)
    return tls()


def site_due(st, rec, w):
    """T-3378 Н-17 (решение координатора): «TLS уже можно» with the site off — the site goes on as a part of the
    transition (``gw.ops.vless_to_tls``). The site on this domain was never switched on (``template_id`` empty: set
    when a site is deployed, kept when it is switched off, reset with the domain — ``site.ops.clear``), VLESS on
    Reality wanting TLS, no refusal of TLS before, devices have links (without them §3.3 switches by itself). A site
    switched off by hand keeps «no_site» (§3.6: «Включите сайт на домене»). The domain, its certificate and a free 443
    — the caller's (``reason``: the checks before; ``plan``: ``tls_possible(…, ignore_mode=True)``)."""
    s = model.peek(st)
    return w in (WANT_TLS, WANT_NODOMAIN) and (rec.get("security") or "reality") != "tls" \
        and not isinstance(rec.get("tls_error"), dict) and s["mode"] != "site" and not s["template_id"] \
        and bool(devices_of(st))


def _reality_fields(st, rec, pick):
    """{"security": "reality", "port", "fields"} — Reality next to (or instead of) the site: off 443 when the site holds
    it; the cover kept (T-M6-qs330d №7), else the node's own site when VLESS is not on 443, else one by the country."""
    from ..gw.inbounds import vless as vin
    cur_sec = rec.get("security") or "reality"
    port = rec.get("port")
    fields = {"security": "reality"}
    if model.peek(st)["mode"] == "site" and port == TLS_PORT:
        fields["port"] = None                      # nginx holds 443: a free one of REALITY_PORTS, else random (caller)
    own = vin.own_domain(st)
    on_443 = (fields.get("port", port) == TLS_PORT)
    # back from TLS: the cover Reality had before stays (T-M6-qs330d №7 — a new one at every switch made the owner give
    # the links out again for nothing); a new one only when there is none to keep, or by «gw vless mask»
    need_mask = (cur_sec == "tls" and not kept_mask(rec)) \
        or (rec.get("mask") == "own" and (not own or on_443 or rec.get("sni") != own)) or not vin.valid_host(rec.get("sni"))
    if need_mask:
        if own and not on_443:
            fields.update(sni=own, dest="%s:443" % own, mask="own", mask_check=vin.checked(own))
        else:
            fields.update((pick or vin.pick_mask)(st))
    return {"security": "reality", "port": fields.get("port", port), "fields": fields}


def _why_reason(st, runner):
    """The reason of a forced Reality (TLS impossible now): the first of the table §3.1 that holds."""
    from ..gw.inbounds import tunnel
    rec = _rec(st)
    if rec is None or not tunnel.wanted(st, VLESS):
        return "vless_off"
    s = model.peek(st)
    if not s["domain"]:
        return "no_domain"
    ok, _ = site_ready(st, ignore_mode=True)
    if not ok or not (os.path.isfile(model.fullchain_path()) and os.path.isfile(model.privkey_path())):
        return "no_cert"
    if _foreign_443_kind(runner, st)[0]:
        return "busy_443"
    if s["mode"] != "site":
        return "no_site"
    return "busy_443"                               # tls_possible said no for a reason of 443 the files do not show


def reason(st, runner=None):
    """Why VLESS is on Reality now — for every interface (``vless_view``): {"reason", "forced", "sub", "why"}.
    ``reason`` "" — VLESS is on TLS (or not installed). The table of §3.1, the first that holds: vless_off, no_domain,
    no_cert, busy_443 (``sub`` "panel_443" — the open mode of the panel), no_site, tls_failed, ready (TLS can be had —
    a recommendation with the price), chosen (Reality by a valid consent), consent_bad, legacy (a record of 3.3.7).
    ``runner`` None — no ``ss``/``nginx -T`` (only the files): a 443 held by an unknown listener is not seen then.
    Read-only."""
    from ..gw.inbounds import tunnel
    rec = _rec(st)
    out = {"reason": "", "forced": False, "sub": "", "why": ""}
    if rec is None:
        return out
    w = want_of(rec)

    def give(r, why="", sub=""):
        out.update(reason=r, forced=(r in FORCED and w != WANT_REALITY), sub=sub, why=why)
        return out

    if not tunnel.wanted(st, VLESS):
        return give("vless_off", tr("site.front.vless_off"))
    s = model.peek(st)
    if not s["domain"]:
        return give("no_domain", tr("site.front.no_domain"))
    if w == WANT_REALITY:
        return give("chosen", tr("site.front.chosen")) if consent_ok(rec) \
            else give("consent_bad", tr("site.front.consent_bad"))
    if tls_wanted(st):
        return out
    ok, why = site_ready(st, ignore_mode=True)
    if not ok:
        return give("no_cert", why)
    if not (os.path.isfile(model.fullchain_path()) and os.path.isfile(model.privkey_path())):
        return give("no_cert", tr("site.front.no_copy"))
    kind, why = _foreign_443_kind(runner, st)
    if kind:
        return give("busy_443", why, sub="panel_443" if kind == "panel_443" else "")
    if s["mode"] != "site":
        if site_due(st, rec, w):                    # Н-17: the site never on here, devices — TLS can be had (§3.3)
            return give("ready", tr("site.front.ready"))
        return give("no_site" if w else "legacy", tr("site.front.no_site"))
    if w is None:                                   # a record of 3.3.7 follows the site (a refusal is not remembered)
        return give("legacy", "")
    if isinstance(rec.get("tls_error"), dict):
        return give("tls_failed", tr("site.front.tls_failed"))
    return give("ready", tr("site.front.ready"))


def _link_of(st, p):
    """What a link of VLESS names under the plan ``p``: (security, host, port, sni) — sni None when a cover would be
    picked over the network (unknown before)."""
    from ..gw import model as gm
    rec = _rec(st) or {}
    f = p.get("fields") or {}
    if (p.get("security") or "reality") == "tls":
        dom = model.domain_of(st)
        return ("tls", dom, TLS_PORT, dom)
    sni = None if f.get("_pick") else f.get("sni", rec.get("sni"))
    return ("reality", gm.public_host(st), f.get("port", rec.get("port")), sni)


def predict(st, runner, site_mode=None, want=None, drop_domain=False):
    """What a change would do to the links of VLESS (§3.7): ``plan`` on a COPY of state with ``site.mode`` =
    ``site_mode`` (None — as it is) — no network, nothing written. Switching the site ON is an explicit action:
    ``want`` "tls" for a record that wants TLS (``site_want``). -> {"changes": bool, "need_pick": bool, "from": {…},
    "to": {…}, "devices": [rows], "reason", "security", "port"}. ``port`` None in ``to`` — a free one of
    REALITY_PORTS (8443 first)."""
    cp = copy.deepcopy(st)
    if drop_domain and isinstance(cp.get("site"), dict):
        cp["site"].update(domain="", mode="off")
    if site_mode is not None and not drop_domain:
        site = cp.setdefault("site", {})
        if isinstance(site, dict):
            site["mode"] = site_mode
        if want is None and site_mode == "site":
            want = site_want(st)
    picked = {"need": False}

    def pick(_st):
        picked["need"] = True
        return {"_pick": True}

    rec = _rec(st) or {}
    p = plan(cp, runner, pick=pick, want=want)
    if p["fields"].get("port", "x") is None:
        p["fields"]["port"] = REALITY_PORTS[0]
    was = _link_of(st, {"security": rec.get("security") or "reality", "fields": {}})
    now = _link_of(cp, p)
    keys = ("security", "host", "port", "sni")
    devs = devices_of(st)
    return {"changes": was != now, "need_pick": picked["need"], "from": dict(zip(keys, was)),
            "to": dict(zip(keys, now)), "devices": devs, "reason": p.get("reason") or "",
            "security": p["security"], "port": now[2]}


def kept_mask(rec):
    """The record (VLESS on TLS now) keeps the cover site of its Reality from before — a site of the lists or one
    chosen by hand (not «own»: the node's own site is what TLS replaced). -> bool."""
    from ..gw.inbounds import vless as vin
    return (rec or {}).get("mask") in ("list", "custom") and bool(vin.valid_host((rec or {}).get("sni")))


def make_room(runner, why):
    """``site mode site`` where VLESS cannot go in front (nginx refused the socket): VLESS on 443 steps aside — Reality on
    a free port of REALITY_PORTS (else random) — so the site can take 443 itself. -> the answer of ``sync`` with
    ``error`` = why, or None when VLESS is not on 443."""
    from ..gw import model as gm
    from ..gw import ops as gops
    from ..gw.inbounds import vless as vin
    st = state.load()
    rec = _rec(st)
    if rec is None or vless_port(st) != TLS_PORT:
        return None
    fields = {"security": "reality", "port": gops.free_port_for(st, runner, VLESS, REALITY_PORTS)}
    if (rec.get("security") == "tls" and not kept_mask(rec)) or rec.get("mask") == "own" \
            or not vin.valid_host(rec.get("sni")):
        fields.update(vin.pick_mask(st))
    if want_of(rec) is not None:                    # a refusal of TLS, as in ``sync``: the reason «tls_failed», and an
        fields["tls_error"] = {"at": util.now_iso(), "why": clean_why(why)}     # implicit sync does not try it again

    gone = []

    def mutate(new, sec):
        r = gm.inbound(new, VLESS)
        r.update(fields)
        gone[:] = [fit_consent(r)]
    gops._change(runner, mutate)
    log_consent_gone(any(gone))
    st1 = state.load()
    now = gm.inbound(st1, VLESS) or {}
    rows, text = gops._reissue(st1, VLESS, tr("site.front.moved", port=now.get("port"), sni=now.get("sni") or "—"))
    error = tr("site.front.room_error", why=why, port=now.get("port"))
    util.log_tr("site.front.log_room_error", why=why, port=now.get("port"))
    return {"ok": False, "changed": True, "security": "reality", "old": rec.get("security") or "reality",
            "port": now.get("port"), "why": why, "reissue": rows, "text": text, "error": error}


def _changed(rec, fields):
    """What ``fields`` change in ``rec`` (a record of 3.3.0 without ``security`` is Reality — no change)."""
    cur = dict(rec or {})
    cur.setdefault("security", "reality")
    cur["security"] = cur["security"] or "reality"
    return dict((k, v) for k, v in fields.items() if k != "mask_check" and cur.get(k) != v)


def _note(fields_set=(), fields_drop=()):
    """Fields of the VLESS record that the config of xray never sees (``want``, ``tls_error``): a plain write under the
    lock of state — nothing to apply."""
    from ..gw import model as gm
    with state.locked():
        st = state.load()
        r = gm.inbound(st, VLESS)
        if r is None:
            return
        before = dict(r)
        for k, v in fields_set:
            r[k] = v
        for k in fields_drop:
            r.pop(k, None)
        gone = fit_consent(r)
        if r != before:
            state.save(st)
    log_consent_gone(gone)


# ── the lock of a transition of the mode (SPEC-vless-tls-default §3.5 Н1, решения координатора С-1, С-2, З-1) ─────

SNAP_FIELDS = ("security", "port", "sni", "dest", "mask", "mask_check", "want", "reality_consent")
_GUARD = threading.local()          # per thread: {"pid", "fd", "depth"} — re-entry by (process, thread), as state.locked
_WHY_MAX = 300
# any absolute path (/proc, /sys, /dev too — SEC-vless-sh12 придирка 2), a home one (~/…, ~user/…) or a relative one
# (./…, ../…); a closing bracket after it stays
_PATH_RE = re.compile(r"(?<![\w.~/])(?:~[\w.-]*|\.\.?)?/[^\s'\"<>;,\[\]()]+")
_BLOB_RE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")


def tls_why(text):
    """«VLESS не перешёл на TLS: X» -> «X»: the reason said once in a text that names the refusal itself (REV-vless-sh3
    придирка 2)."""
    s = str(text or "")
    pre = tr("site.front.tls_error", why="")
    return s[len(pre):] if pre and s.startswith(pre) else s


def clean_why(text):
    """The words of a refusal of xray/nginx for state, the panel and MCP (``tls_error.why``): secrets hidden
    (util.redact), file paths and long keys (base64/hex runs) out, at most _WHY_MAX characters."""
    s = util.redact(str(text or ""))
    s = _PATH_RE.sub("<file>", s)
    s = _BLOB_RE.sub("…", s)
    s = " ".join(s.split())
    return s if len(s) <= _WHY_MAX else s[:_WHY_MAX - 1] + "…"


def guard_held():
    """This thread of this process holds the lock of the transition now."""
    return getattr(_GUARD, "pid", None) == os.getpid() and getattr(_GUARD, "depth", 0) > 0


def _busy(st=None):
    """«Идёт смена режима VLESS» for the one who did not get the lock. The time it started is known only for a transition
    R→T (``snap_take`` writes ``mode_pending.at``); the site off, a cover or the keys hold the lock with no record —
    then the text has no «(начата —)» (T-3378 Н-15: the lock itself is not touched for a time in the words)."""
    pend = (_rec(st if st is not None else state.load()) or {}).get("mode_pending")
    at = pend.get("at") if isinstance(pend, dict) else ""
    text = tr("gw_in_vless.busy", at=at) if at else tr("gw_in_vless.busy_now")
    return ConflictError(text, data={"need": "busy", "kind": "vless_mode", "at": at or None})


def _take(wait):
    p = paths.vless_mode_lock()
    util.ensure_run_dir(os.path.dirname(p))
    fd = os.open(p, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
    import time
    t0 = time.time()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES) or time.time() - t0 >= wait:
                os.close(fd)
                if e.errno in (errno.EAGAIN, errno.EACCES):
                    return None
                raise
            time.sleep(0.2)


@contextlib.contextmanager
def vless_guard(runner=None, wait=0, recover=True):
    """Every entry that changes VLESS or leads to ``sync`` runs under this lock (§3.5 Н1): flock of
    ``paths.vless_mode_lock()`` with O_CLOEXEC (children do not inherit it; the kernel drops it with the process, even on
    SIGKILL; /run is tmpfs). Re-entry is counted by (process, thread) — a nested entry of the same thread does not
    take it again; another thread or process gets «идёт смена режима» (CONFLICT ``gw_in_vless.busy``) at once
    (``wait`` — seconds to wait first: the install step, решения координатора). The outermost entry with ``runner``
    undoes a transition left by a dead process first (``recover_pending``); when that undo fails, the entry is
    refused (З-1: nothing is built on a snapshot not restored)."""
    if guard_held():
        _GUARD.depth += 1
        try:
            yield
        finally:
            _GUARD.depth -= 1
        return
    fd = _take(wait)
    if fd is None:
        raise _busy()
    _GUARD.pid, _GUARD.fd, _GUARD.depth = os.getpid(), fd, 1
    try:
        if recover and runner is not None:
            got = recover_pending(runner)
            if got and not got.get("restored"):
                raise ConflictError(tr("gw_in_vless.recover_failed_entry", why=got.get("why") or "—"),
                                    data={"need": "recover", "kind": "vless_mode"})
        yield
    finally:
        _GUARD.pid, _GUARD.depth = None, 0
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def lock_busy():
    """Somebody holds the lock of a transition now (``gw vless show`` → ``pending.busy``, Н1): a probe without waiting —
    a shared flock taken and dropped at once, the file opened read-only (no lock file — nobody ever took it).
    Read-only; this thread holding it counts as busy."""
    if guard_held():
        return True
    try:
        fd = os.open(paths.vless_mode_lock(), os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EAGAIN, errno.EACCES):
            return True
        raise
    finally:
        os.close(fd)                                    # closing drops the probe's lock too
    return False


def pending(st):
    """{"at", "pid", "snap"} of a transition R→T started and not finished (``mode_pending``), or None."""
    p = (_rec(st) or {}).get("mode_pending")
    return p if isinstance(p, dict) and isinstance(p.get("snap"), dict) else None


def snap_take(runner):
    """§3.5 п.3: the record before a transition and ``site.mode`` into ``mode_pending`` — a plain write under the lock
    of state (nothing to apply). A snapshot not restored is never written over (З-1): CONFLICT. -> the snapshot."""
    from ..gw import model as gm
    with state.locked():
        st = state.load()
        r = gm.inbound(st, VLESS)
        if r is None:
            raise KaskadError(tr("site.front.no_vless_err"))
        if isinstance(r.get("mode_pending"), dict):
            raise ConflictError(tr("gw_in_vless.pending_left"), data={"need": "recover", "kind": "vless_mode"})
        snap = {"rec": copy.deepcopy(dict((k, r[k]) for k in SNAP_FIELDS if k in r)),
                "site": model.peek(st)["mode"]}
        r["mode_pending"] = {"at": util.now_iso(), "pid": os.getpid(), "snap": snap}
        state.save(st)
    return snap


def link_key(st):
    """What every link of VLESS is built from on this node (the device adds only its UUID): compared before and after
    an undo (§3.5 п.6.3)."""
    from ..gw import model as gm
    rec = _rec(st) or {}
    sec = rec.get("security") or "reality"
    if sec == "tls":
        return ("tls", model.domain_of(st), TLS_PORT)
    return ("reality", gm.public_host(st), rec.get("port"), rec.get("sni"), rec.get("pub"), rec.get("short_id"),
            rec.get("fp"))


def snap_restore(runner, snap, why, before=None):
    """§3.5 п.6: the site off again when the transition switched it on (``site mode domain`` without the step of VLESS),
    then the record back as it was in one ``gops._change`` (applied: xray reads Reality on 443 again), ``mode_pending``
    off, ``tls_error`` {at, why}. -> {"restored": bool, "changed": [what differs]}. ``before`` — ``link_key`` taken
    before the transition."""
    from ..gw import model as gm
    from ..gw import ops as gops
    from . import ops as site_ops
    if snap.get("site") != "site" and model.peek(state.load())["mode"] == "site":
        site_ops.mode(runner, "domain", vless=False)
    rec0 = snap.get("rec") or {}
    err = {"at": util.now_iso(), "why": clean_why(why)}

    def mutate(new, sec):
        r = gm.inbound(new, VLESS)
        if r is None:
            return
        for k in SNAP_FIELDS:
            if k in rec0:
                r[k] = copy.deepcopy(rec0[k])
            else:
                r.pop(k, None)
        r.pop("mode_pending", None)
        if want_of(r) is not None:
            r["tls_error"] = err
    gops._change(runner, mutate)
    after_xray(runner)
    st = state.load()
    now = link_key(st)
    diff = [] if before is None or tuple(before) == now else ["%s → %s" % ("/".join(map(str, before)),
                                                                          "/".join(map(str, now)))]
    return {"restored": not diff, "changed": diff}


def recover_pending(runner):
    """A transition left by a process that died (§3.5 п.7): ``mode_pending`` and the lock free (the caller holds it
    now) -> undo by the snapshot (п.6.1–6.2), a line of the journal. -> None (nothing to undo) | {"restored", "why"}.
    The undo failing leaves ``mode_pending`` (``restored`` False, ``gw_in_vless.log.recover_failed``)."""
    st = state.load()
    pend = pending(st)
    if pend is None:
        return None
    try:
        got = snap_restore(runner, pend["snap"], tr("gw_in_vless.interrupted", at=pend.get("at") or "—"))
    except Exception as e:                              # noqa: BLE001 — the record keeps the snapshot; the next entry tries
        why = clean_why(e) or e.__class__.__name__
        util.log_tr("gw_in_vless.log.recover_failed", why=why)
        return {"restored": False, "why": why}
    util.log_tr("gw_in_vless.log.recovered", at=pend.get("at") or "—")
    return {"restored": True, "why": ""}


def recover_watch(runner):
    """The watchdog (``net check --repair``, not while kaskad-net starts — С-1): a transition left behind is undone
    under the lock without waiting; the lock taken — skipped till the next run. -> the answer of
    ``recover_pending`` | None."""
    if pending(state.load()) is None:
        return None
    fd = _take(0)
    if fd is None:
        return None
    _GUARD.pid, _GUARD.fd, _GUARD.depth = os.getpid(), fd, 1
    try:
        return recover_pending(runner)
    finally:
        _GUARD.pid, _GUARD.depth = None, 0
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def sync(runner, pick=None, want=None, since=None):
    """``_sync`` under the lock of a transition (``vless_guard``, Н1); an implicit switch Reality → TLS (§3.3: nobody to
    give links again; a record of 3.3.7 following its site) goes through ``gw.ops.vless_to_tls`` — a refusal is undone by
    the snapshot (З-2)."""
    with vless_guard(runner):
        return _sync(runner, pick=pick, want=want, since=since)


def _sync(runner, pick=None, want=None, since=None):
    """Switch VLESS to what ``plan`` says. -> {"ok", "changed", "security", "old", "port", "why", "reason", "reissue",
    "text"}. Nothing to do (no VLESS, nothing differs) — changed False. A TLS switch xray did not take (it refused the
    config, nginx refused the socket) is undone: VLESS stays on Reality, ``error`` says why, and the record keeps
    ``tls_error`` {at, why} — an implicit ``sync`` does not try TLS again (З-1); the first TLS that works removes it.
    ``want`` — explicit (``plan``); ``since`` — the record before the caller's own change (``site mode site`` moved
    VLESS off 443 first): what changed and whom to give links again is counted from it. A record of 3.3.7 (no
    ``want``) that ends on TLS gets ``want`` "tls" (§3.4)."""
    from ..gw import model as gm
    from ..gw import ops as gops
    st = state.load()
    rec = _rec(st)
    if rec is None:
        after_xray(runner)
        return {"ok": True, "changed": False, "security": None}
    base = dict(since) if isinstance(since, dict) else rec
    p = plan(st, runner, pick=pick, want=want)
    old = base.get("security") or "reality"
    if p.get("reason") == "consent_bad":
        util.log_tr("gw_in_vless.log.consent_bad")
    if want is None and since is None and p["security"] == "tls" and (rec.get("security") or "reality") != "tls":
        from ..gw import ops as gops
        return gops.vless_to_tls(runner, via="auto", who=None, yes=True)
    diff = _changed(rec, p["fields"])
    if not diff and since is None:
        after_xray(runner)
        _settled(rec, rec.get("security") or "reality", runner)
        return {"ok": True, "changed": False, "security": old, "port": rec.get("port"), "why": p["why"],
                "reason": p.get("reason") or ""}
    error = ""
    if diff:
        fields = dict(p["fields"])
        if "port" in fields and fields["port"] is None:
            fields["port"] = gops.free_port_for(st, runner, VLESS, REALITY_PORTS)
        if fields.get("security") == "tls":
            ensure_sock_dir(runner)

        gone = []

        def mutate(new, sec):
            r = gm.inbound(new, VLESS)
            if r is None:
                raise KaskadError(tr("site.front.no_vless_err"))
            r.update(fields)
            gone[:] = [fit_consent(r)]
            return dict(r)

        _, res = gops._change(runner, mutate)
        log_consent_gone(any(gone))
        if fields.get("security") == "tls" and gm.enabled(state.load()) and not xray_fronts(runner):
            # xray or nginx refused: Reality next to the site — off 443 (nginx's), masked by the node's own site
            from ..gw.inbounds import vless as vin
            # «xray holds 443 without TLS» is the symptom of the refusal, gone with Reality off 443 below — not its why
            # (its hint «gw vless sync» never tries TLS again, REV-vless-sh3 придирка 2)
            warn = "; ".join(w for w in ((res or {}).get("warnings") or [])
                             if isinstance(w, str) and w != tr("site.front.behind_no_tls"))
            why_tls = clean_why(warn) or tr("site.front.xray_refused")
            error = tr("site.front.tls_error", why=why_tls)
            st2 = state.load()
            own = model.domain_of(st2)
            # the port it had (xray still holds it — the refused config never ran: no conflict in ``st``), else 8443
            was = [rec.get("port")] if rec.get("port") not in (None, TLS_PORT) else []
            back = {"security": "reality", "port": gops.free_port_for(st, runner, VLESS, was + list(REALITY_PORTS))}
            back.update(dict((k, rec.get(k)) for k in ("sni", "dest", "mask", "mask_check") if k in rec))
            if own:
                back.update(sni=own, dest="%s:443" % own, mask="own", mask_check=vin.checked(own))
            back["tls_error"] = {"at": util.now_iso(), "why": why_tls}

            def undo(new, sec):
                gm.inbound(new, VLESS).update(back)
            gops._change(runner, undo)
            util.log_tr("site.front.log_kept_reality", error=error)
    after_xray(runner)
    st1 = state.load()
    now = gm.inbound(st1, VLESS) or {}
    new_sec = now.get("security") or "reality"
    if not error:
        _settled(now, new_sec, runner)
    what = tr("site.front.to_tls", domain=model.unicode(model.domain_of(st1))) \
        if new_sec == "tls" else tr("site.front.to_reality", sni=now.get("sni") or "—", port=now.get("port"))
    changed = bool(_changed(base, dict((k, now.get(k)) for k in ("security", "port", "sni"))))
    rows, text = gops._reissue(st1, VLESS, what) if changed else ([], "")
    if diff:
        util.log_tr("site.front.log_mode", old=old, new=new_sec, old_port=base.get("port"), port=now.get("port"),
                    why=("; " + p["why"]) if p["why"] and new_sec != "tls" else "")
    out = {"ok": not error, "changed": changed, "security": new_sec, "old": old, "port": now.get("port"),
           "why": p["why"] if new_sec != "tls" else "", "reason": "" if new_sec == "tls" else (
               "tls_failed" if error else p.get("reason") or ""), "reissue": rows, "text": text}
    if error:
        out["error"] = error
    return out


def _settled(rec, sec, runner):
    """After a ``sync`` without a refusal: VLESS works on TLS -> ``tls_error`` goes (З6) and a record of 3.3.7 gets
    ``want`` "tls" (§3.4: the same behaviour, the new rules from now on)."""
    if sec != "tls" or not xray_fronts(runner):
        return
    put = [] if rec.get("want") is not None else [("want", WANT_TLS)]
    drop = ["tls_error"] if "tls_error" in rec else []
    if put or drop:
        _note(put, drop)


def risks_data(st, lang=None, site=None, devices=None):
    """The text of the risks of Reality with a domain (§4.3, version RISKS_VER) — what a consent is given to:
    {"ver": "r1a" | "r1b", "variant", "site" (the cover that will be set — a name, "own", or None + ``need_pick``),
    "title", "text", "short", "price", "phrase", "keep"}. ``site`` None — the saved cover, else the node's own site,
    else none (an interface picks one before showing the text, З-5). ``devices`` — their number (None: from state).
    No network, nothing written."""
    from ..gw.inbounds import vless as vin
    from ..i18n import trn
    rec = _rec(st) or {}
    if site is None:
        if rec.get("mask") in ("list", "custom", "own") and vin.valid_host(rec.get("sni")):
            site = "own" if rec.get("mask") == "own" else rec.get("sni")
        elif vin.own_domain(st):
            site = "own"
    variant = "b" if site == "own" else "a"
    dom = model.domain_of(st)
    shown = model.unicode(dom) if site == "own" else (site or tr("gw_in_vless.consent.site_pick", lang))
    cur_port = rec.get("port")
    port = cur_port if cur_port not in (None, TLS_PORT) else REALITY_PORTS[0]
    n = len(devices_of(st)) if devices is None else int(devices)
    cur_sni = rec.get("sni") if (rec.get("security") or "reality") == "reality" else None
    want_sni = vin.own_domain(st) if site == "own" else site
    same = cur_sni is not None and want_sni == cur_sni and not (model.peek(st)["mode"] == "site"
                                                                and cur_port == TLS_PORT)
    # no devices — no links given out: no line of the price at all, «the same links» too (макет М3, DQA-3378)
    price = "" if not n else (tr("gw_in_vless.consent.price_same", lang) if same else
                              trn("gw_in_vless.consent.price", n, lang))
    kw = {"site": shown, "domain": model.unicode(dom) if dom else "", "port": port}
    body = tr("gw_in_vless.consent.%s.body" % variant, lang, **kw)
    body += (";\n- " + price) if price else "."             # the list ends with its last point (макет М3)
    text = body + "\n\n" + tr("gw_in_vless.consent.%s.tail" % variant, lang, **kw)
    return {"ver": RISKS_VER + variant, "variant": variant, "site": site, "need_pick": site is None,
            "title": tr("gw_in_vless.consent.%s.title" % variant, lang), "text": text, "price": price,
            "short": tr("gw_in_vless.consent.short.%s" % variant, lang, **kw),
            "phrase": tr("gw_in_vless.consent.phrase", lang), "keep": tr("gw_in_vless.consent.keep", lang)}
