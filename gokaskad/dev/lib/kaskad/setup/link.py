"""The way into the quick setup (docs/k3/SPEC-setup.md §2.2): ``https://<IP>:<port>/setup-<secret>/?t=<token>``.

    * the secret path — 32 characters of base64url (192 bits); any other path under /setup- answers the very 404 of
      an unknown path (nothing to tell a scanner);
    * the token — one-time (SHA-256 kept), alive while the link is: until the end of the setup, at most TTL (24 h)
      from the last ``kaskadctl setup link``; the first browser that opens it gets a session, the same link again —
      «used» (the page says «запросите новую: kaskadctl setup link»);
    * after the stage «Пароль» every unused token is revoked and new links carry none: only the panel password opens
      the wizard (``password_set``);
    * optionally bound to the address of the first browser (``bind``): another address gets the 404;
    * the move to the domain (stage 4, T-M6-qs330 №2): the cookie of the wizard belongs to the host it was set on, so
      a browser with a session asks for a one-time token of the move (``issue_move``: MOVE_TTL, also after the
      password) and opens ``https://<домен>:<port>/setup-<secret>/?t=<token>`` — a session there, no second login.

The port is the one of the open mode of the panel (kaskad.panel.conf: 30000–60999): 443 belongs to what the wizard
itself installs later (SSTP, OpenConnect, the site of the domain). The host is ``conf.public_host`` — the IP, or the
domain once its certificate is valid.
"""

import hashlib
import hmac
import re
import secrets as _random
import time

from .. import util
from . import model

SECRET_LEN = 32
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{%d}$" % SECRET_LEN)
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
PREFIX = "/setup-"
TOKENS_MAX = 20
MOVE_TTL = 15 * 60          # s: the move token outlives the certificate of the domain being issued (up to minutes)

_now = time.time


def _hash(token):
    return hashlib.sha256(str(token).encode("utf-8", "ignore")).hexdigest()


def new_secret():
    return _random.token_urlsafe(24)[:SECRET_LEN]


def base_of(doc):
    return PREFIX + doc["link"]["secret"]


def url(doc, token=None, cfg=None):
    """The URL people open (None when the panel is not open from the internet)."""
    from ..panel import conf
    cfg = conf.load() if cfg is None else cfg
    pub = conf.public_origin(cfg)          # the wizard has its own secret at the root (not under the panel's)
    if not pub or not doc["link"].get("secret"):
        return None
    out = pub.rstrip("/") + base_of(doc) + "/"
    return out + ("?t=" + token if token else "")


def issue(doc, by="cli", bind=None, new_path=False, now=None):
    """A new link in ``doc`` (the caller saves it under model.locked): a path when there is none (or ``new_path``),
    the deadline now + TTL, a token unless the password is set. -> the token or None."""
    now = _now() if now is None else now
    link = doc["link"]
    if new_path or not SECRET_RE.match(link.get("secret") or "") or doc["status"] != "active":
        link["secret"] = new_secret()
        link["bound_ip"] = None
        doc["tokens"] = []
    if doc["status"] != "active":
        doc["status"] = "active"
        doc["started"] = doc["started"] or util.now_iso()
        doc["finished_at"] = None
    link["expires"] = int(now + model.TTL)
    if bind is not None:
        link["bind"] = bool(bind)
        if not bind:
            link["bound_ip"] = None
    doc["tokens"] = [t for t in doc["tokens"] if float(t.get("expires") or 0) > now][-TOKENS_MAX:]
    if doc["password_set"]:
        return None
    token = _random.token_urlsafe(32)
    doc["tokens"].append({"hash": _hash(token), "created": int(now), "expires": int(link["expires"]), "used": None,
                          "revoked": False, "by": str(by)[:16]})
    return token


def issue_move(doc, via, now=None):
    """A one-time token for the move of an open session to the domain (the caller saves ``doc`` under model.locked):
    MOVE_TTL, never past the link; ``via`` — the kind of the session that asked («password» stays «password»).
    -> the token."""
    now = _now() if now is None else now
    doc["tokens"] = [t for t in doc["tokens"] if float(t.get("expires") or 0) > now][-TOKENS_MAX:]
    token = _random.token_urlsafe(32)
    exp = min(int(now + MOVE_TTL), int(float(doc["link"]["expires"] or 0)))
    doc["tokens"].append({"hash": _hash(token), "created": int(now), "expires": exp, "used": None, "revoked": False,
                          "by": "move", "via": "password" if via == "password" else "link"})
    return token


def move_url(doc, host, token, cfg=None):
    """https://<host>:<port of the open mode>/setup-<secret>/?t=<token> (None when the panel is not open)."""
    from ..panel import conf
    cfg = conf.load() if cfg is None else cfg
    pub = cfg.get("public") or {}
    if not pub.get("on") or not pub.get("port") or not host or not doc["link"].get("secret"):
        return None
    h = conf.host_for_url(host)
    base = "https://%s/" % h if pub["port"] == 443 else "https://%s:%d/" % (h, pub["port"])
    return base.rstrip("/") + base_of(doc) + "/" + ("?t=" + token if token else "")


def match(doc, path, now=None):
    """(base, rest) when ``path`` is under the secret path of an active setup, else None. ``rest`` is "/…"; the bare
    base (no slash) gives rest None — the caller redirects to base + "/"."""
    if not path.startswith(PREFIX) or doc["status"] != "active":
        return None
    secret = doc["link"].get("secret") or ""
    if not SECRET_RE.match(secret):
        return None
    head = path[len(PREFIX):len(PREFIX) + SECRET_LEN]
    if len(head) != SECRET_LEN or not hmac.compare_digest(head.encode("ascii", "ignore"), secret.encode("ascii")):
        return None
    tail = path[len(PREFIX) + SECRET_LEN:]
    if tail == "":
        return base_of(doc), None
    if not tail.startswith("/"):
        return None
    return base_of(doc), tail


def allowed_ip(doc, ip):
    link = doc["link"]
    return not (link.get("bind") and link.get("bound_ip") and link["bound_ip"] != str(ip or ""))


def redeem(doc, token, ip, now=None, info=None):
    """One use of a token (the caller saves ``doc``): "ok" | "used" | "revoked" | "expired" | "unknown". ``info`` (a
    dict) gets «by» of the token found and «via» of the session it opens: «password» for the move of a password
    session, else «link»."""
    now = _now() if now is None else now
    if not token or not TOKEN_RE.match(str(token)):
        return "unknown"
    h = _hash(token)
    for t in doc["tokens"]:
        if not hmac.compare_digest(str(t["hash"]), h):
            continue
        if info is not None:
            info["by"] = t.get("by")
        if t.get("revoked"):
            return "revoked"
        if t.get("used"):
            return "used"
        if float(t.get("expires") or 0) <= now or not model.active(doc, now):
            return "expired"
        t["used"] = int(now)
        if info is not None:
            info["via"] = "password" if t.get("by") == "move" and t.get("via") == "password" else "link"
        if doc["link"].get("bind") and not doc["link"].get("bound_ip"):
            doc["link"]["bound_ip"] = str(ip or "")
        return "ok"
    return "unknown"


def revoke_all(doc):
    """After the password: every unused token burns. -> how many."""
    n = 0
    for t in doc["tokens"]:
        if not t.get("used") and not t.get("revoked"):
            t["revoked"] = True
            n += 1
    return n


def secret_masked(doc):
    s = doc["link"].get("secret") or ""
    return (s[:4] + "••••" + s[-4:]) if s else ""
