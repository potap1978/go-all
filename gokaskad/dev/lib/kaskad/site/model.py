"""The domain of the node (docs/k3/SPEC-wave4.md §2, §4.1): its part of state.json, its files, the names.

state.json, the top-level key ``site`` (not ``gateway.site``: the domain serves the panel without the gateway and
outlives «Удалить шлюз»; no key = ``off``; schema 1 stays — the key is optional and read leniently)::

    "site": {"domain": "vpn.example.com", "mode": "off" | "domain" | "site", "cert": "" | "letsencrypt",
             "staging": false, "acme_email": "", "use": {"sstp": false, "ocserv": false},
             "dns": {the last DNS check, see site.dns} | null,
             "ufw80": false,            — the rule «ufw allow 80/tcp comment kaskad-acme» is ours (remove with the domain)
             "ufw443": false,           — the analogous kaskad-site rule while site mode is on
             "template_id": "", "template_source": "", "deployed_at": "", "check": null,
             "renew_by": "" | "certbot.timer" | "kaskad-cert-renew.timer",
             "prev": null | {"domain": "old.example.com", "until": 1760000000}}
                                        — the domain just replaced by a change (ops.change_domain): the open mode of
                                          the panel still answers it with its own certificate until ``until`` (unix
                                          time), so a wizard opened on it learns the result and moves (PREV_GRACE)

Files (only Kaskad's own; certificates of others on the machine are never touched):

    /etc/kaskad/site/tls/current/fullchain.pem  0644   the copy the consumers take (the panel, /p/, SSTP, ocserv):
    /etc/kaskad/site/tls/current/privkey.pem    0600   ``current`` is an atomically replaced symlink to one
                                                        checked certificate/key pair made by
                                                        ``kaskadctl site cert-installed`` from /etc/letsencrypt/live/
    /etc/kaskad/site/tls/{fullchain,privkey}.pem        compatibility aliases for configurations written before
                                                        4.0-B; relative links to ``current/<name>``
    /etc/kaskad/site/pkgs                      packages Kaskad installed for the domain (certbot): the line of the
                                               removal of Kaskad (menu 15) purges them
    /etc/letsencrypt/live/<domain>/            certbot's (read only; renewal/<domain>.conf: autorenew, renew_hook)

The domain is kept in ASCII (IDNA, «xn--…»): certbot, the SAN of the certificate and the addresses in the issued
settings need it so; ``unicode()`` gives the form people typed.
"""

import copy
import ipaddress
import os
import re

from .. import paths, util
from ..errors import USAGE, KaskadError
from ..i18n import tr
from . import certinfo

MODES = ("off", "domain", "site")
USE_KINDS = ("sstp", "ocserv")
DEFAULT = {"domain": "", "mode": "off", "cert": "", "staging": False, "acme_email": "",
           "use": {"sstp": False, "ocserv": False}, "dns": None, "ufw80": False, "ufw443": False,
           "renew_by": "", "template_id": "", "template_source": "", "deployed_at": "", "check": None, "prev": None}
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")


# ── paths ──────────────────────────────────────────────────────────────────

def site_dir():       return paths.P("/etc/kaskad/site")
def tls_dir():        return paths.P("/etc/kaskad/site/tls")
def current_path():   return paths.P("/etc/kaskad/site/tls/current")
def fullchain_path(): return paths.P("/etc/kaskad/site/tls/current/fullchain.pem")
def privkey_path():   return paths.P("/etc/kaskad/site/tls/current/privkey.pem")
def pkgs_path():      return paths.P("/etc/kaskad/site/pkgs")
def le_dir():         return paths.P("/etc/letsencrypt")
def le_log():         return paths.P("/var/log/letsencrypt/letsencrypt.log")


def live_dir(domain):
    return os.path.join(le_dir(), "live", domain)


def renewal_conf(domain):
    return os.path.join(le_dir(), "renewal", domain + ".conf")


# ── names ──────────────────────────────────────────────────────────────────

def _hint():
    return tr("site.model.domain_hint")


def parse_domain(value):
    """The name people typed -> its ASCII form for certbot (IDNA: «впн.пример.рф» -> «xn--b1awf.xn--e1afmkfd.xn--p1ai»).
    Refused: an IP address, «*» (a wildcard needs DNS-01, not HTTP-01), one label, a label longer than 63 or empty,
    the whole longer than 253, «_» and other characters a host name cannot have. KaskadError(USAGE)."""
    s = str(value or "").strip().rstrip(".")
    if s.lower().startswith(("http://", "https://")):
        s = s.split("://", 1)[1].split("/", 1)[0]
    if not s:
        raise KaskadError(tr("site.model.no_domain", hint=_hint()), code=USAGE)
    if "*" in s:
        raise KaskadError(tr("site.model.wildcard"), code=USAGE)
    try:
        ipaddress.ip_address(s.strip("[]"))
        raise KaskadError(tr("site.model.ip", name=s, hint=_hint()), code=USAGE)
    except ValueError:
        pass
    labels = s.split(".")
    if len(labels) < 2:
        raise KaskadError(tr("site.model.one_label", name=s, hint=_hint()), code=USAGE)
    out = []
    for lab in labels:
        if not lab:
            raise KaskadError(tr("site.model.empty_label", name=s, hint=_hint()), code=USAGE)
        try:
            a = lab.lower() if lab.isascii() else lab.encode("idna").decode("ascii").lower()
        except (UnicodeError, ValueError) as e:
            if "too long" in str(e):
                raise KaskadError(tr("site.model.label_long_dns", label=lab), code=USAGE)
            raise KaskadError(tr("site.model.idna", label=lab, hint=_hint()), code=USAGE)
        if len(a) > 63:
            raise KaskadError(tr("site.model.label_long", label=lab), code=USAGE)
        if not _LABEL.match(a):
            raise KaskadError(tr("site.model.bad_chars", label=lab, hint=_hint()), code=USAGE)
        out.append(a)
    if out[-1].isdigit():
        raise KaskadError(tr("site.model.digits_tld", name=s, hint=_hint()), code=USAGE)
    name = ".".join(out)
    if len(name) > 253:
        raise KaskadError(tr("site.model.too_long"), code=USAGE)
    return name


def unicode(domain):
    """«xn--…» back to the letters people typed (the ASCII name itself when it is plain)."""
    if not domain:
        return domain
    try:
        return ".".join(lab.encode("ascii").decode("idna") if lab.startswith("xn--") else lab
                        for lab in domain.split("."))
    except (UnicodeError, ValueError):
        return domain


def parse_email(value):
    s = str(value or "").strip()
    if not _EMAIL.match(s) or ".." in s:
        raise KaskadError(tr("site.model.bad_email"), code=USAGE)
    return s


# ── state ──────────────────────────────────────────────────────────────────

def peek(st):
    """The site part of ``st`` with every key present, WITHOUT changing ``st`` (a copy)."""
    raw = (st or {}).get("site")
    out = copy.deepcopy(DEFAULT)
    if not isinstance(raw, dict):
        return out
    d = raw.get("domain")
    out["domain"] = d if isinstance(d, str) and d else ""
    mode = raw.get("mode")
    out["mode"] = mode if out["domain"] and mode in ("domain", "site") else \
        ("domain" if out["domain"] else "off")
    out["cert"] = raw.get("cert") if raw.get("cert") in ("", "letsencrypt") else ""
    out["staging"] = raw.get("staging") is True
    e = raw.get("acme_email")
    out["acme_email"] = e if isinstance(e, str) else ""
    use = raw.get("use") if isinstance(raw.get("use"), dict) else {}
    for k in USE_KINDS:
        out["use"][k] = use.get(k) is True and bool(out["domain"])
    out["dns"] = raw.get("dns") if isinstance(raw.get("dns"), dict) else None
    out["ufw80"] = raw.get("ufw80") is True
    out["ufw443"] = raw.get("ufw443") is True
    rb = raw.get("renew_by")
    out["renew_by"] = rb if rb in ("certbot.timer", "kaskad-cert-renew.timer") else ""
    for k in ("template_id", "template_source", "deployed_at"):
        v = raw.get(k)
        out[k] = v if isinstance(v, str) else ""
    out["check"] = raw.get("check") if isinstance(raw.get("check"), dict) else None
    prev = raw.get("prev")
    if isinstance(prev, dict) and isinstance(prev.get("domain"), str) and prev["domain"] \
            and isinstance(prev.get("until"), int) and not isinstance(prev.get("until"), bool):
        out["prev"] = {"domain": prev["domain"], "until": prev["until"]}
    return out


def site(st):
    """The site part of ``st`` normalized IN PLACE (for a change under the state lock)."""
    s = peek(st)
    st["site"] = s
    return s


def domain_of(st):
    return peek(st)["domain"]


# ── the copy for the consumers ─────────────────────────────────────────────

def copy_info():
    """certinfo of the atomically selected pair (None — no copy or unreadable)."""
    if not os.path.isfile(privkey_path()):
        return None
    return certinfo.read(fullchain_path())


def active(st):
    """certinfo of the copy when the consumers may take it: a domain is set, the copy is for it and has not
    expired; else None. Staging certificates count too (the stand asked for one on purpose)."""
    d = domain_of(st)
    if not d:
        return None
    info = copy_info()
    if info is None or not certinfo.covers(info, d) or certinfo.expired(info):
        return None
    return info


def use_for(st, kind):
    """The domain when the inbound ``kind`` (sstp | ocserv) is switched to the certificate of the domain
    (``site use``) and the copy is there; else None (the inbound keeps its self-signed one). The copy's validity is
    checked when switching (``site use … on`` needs a live certificate), not at every render: an inbound must not
    flip back and forth between two certificates around an expiry — the renewal brings a new copy, and a reload
    is all it takes."""
    s = peek(st)
    if kind not in USE_KINDS or not s["use"].get(kind) or not s["domain"]:
        return None
    if not (os.path.isfile(fullchain_path()) and os.path.isfile(privkey_path())):
        return None
    return s["domain"]


def tls_pair(st, kind):
    """(certificate path, key path) of the domain's copy for ``kind``, or None."""
    if use_for(st, kind) is None:
        return None
    return fullchain_path(), privkey_path()


def read_pkgs():
    return [ln.strip() for ln in (util.read_text(pkgs_path()) or "").splitlines() if ln.strip()]


def add_pkgs(names):
    """Packages Kaskad installed for the domain and the site — one line each, no repeats (the same file and format
    as ``templates.record_pkg`` of git)."""
    have = read_pkgs()
    new = have + [n for n in names if n not in have]
    if new != have:
        util.ensure_dir(site_dir(), 0o755)
        util.atomic_write(pkgs_path(), "".join(n + "\n" for n in new), 0o644)
    return new
