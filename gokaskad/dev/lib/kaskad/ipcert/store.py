"""Files of the IP certificate and what it says (docs/k3/SPEC-setup.md §1.4).

    /etc/kaskad/tls/ip/                      0700
        <stamp>/fullchain.pem  privkey.pem   one issue (the key 0600, the chain 0644)
        current -> <stamp>                   the pair the panel serves; switched by one rename of a link
        meta.json                            0600: {"ip", "not_after", "issued_at", "profile", "staging", "ca",
                                             "last_try": {"at", "ok", "reason", "text", "retry_after"}}
    /etc/kaskad/tls/acme/account-<ca>.key    0600, RSA 2048: the account of Let's Encrypt (production / staging)

``info()`` — certinfo.parse of the current chain (or None); ``status(ip)`` — the contract of the API and the menu;
``serving_pair(ip)`` — (chain, key) when the panel may serve it: a real (not staging) certificate for this very
address with more than ``SERVE_MIN_H`` hours left, else None (the panel keeps the node certificate).
"""

import json
import os
import shutil
import time

from .. import paths, util

RENEW_LEFT_H = 72            # renew when fewer hours are left (the shortlived profile lives 160 h)
SERVE_MIN_H = 1              # an almost expired certificate is not served any more (the node certificate is)
KEEP = 2                     # issues kept on disk (the current one and the one before)

_now = time.time


def base_dir():      return paths.P("/etc/kaskad/tls/ip")
def current_dir():   return os.path.join(base_dir(), "current")
def fullchain_path(): return os.path.join(current_dir(), "fullchain.pem")
def privkey_path():  return os.path.join(current_dir(), "privkey.pem")
def meta_path():     return os.path.join(base_dir(), "meta.json")
def account_dir():   return paths.P("/etc/kaskad/tls/acme")
def work_dir():      return os.path.join(base_dir(), ".work")


def account_key(staging):
    return os.path.join(account_dir(), "account-%s.key" % ("staging" if staging else "le"))


def meta():
    raw = util.read_text(meta_path())
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    return doc if isinstance(doc, dict) else {}


def save_meta(doc):
    util.ensure_dir(base_dir(), 0o700)
    util.atomic_write(meta_path(), util.json_text(doc), 0o600)


def note_try(ok, reason=None, text=None, retry_after=None):
    doc = meta()
    doc["last_try"] = {"at": util.now_iso(), "ok": bool(ok), "reason": reason, "text": text,
                       "retry_after": retry_after}
    save_meta(doc)
    return doc


def info():
    from ..site import certinfo
    return certinfo.read(fullchain_path())


def norm_ip(ip):
    import ipaddress
    try:
        return str(ipaddress.ip_address(str(ip or "").strip().strip("[]")))
    except ValueError:
        return None


def hours_left(inf):
    return (inf["not_after_ts"] - _now()) / 3600.0


def covers(inf, ip):
    ip = norm_ip(ip)
    return bool(inf and ip and ip in [norm_ip(n) for n in inf.get("names") or []])


def status(ip=None):
    """{"state": none | valid | expiring | expired | other_ip, "ip", "not_after", "hours_left", "days_left",
    "staging", "issuer", "last_try", "renew_left_h"} — no key material, nothing secret."""
    inf = info()
    m = meta()
    out = {"state": "none", "ip": m.get("ip"), "not_after": None, "hours_left": None, "days_left": None,
           "staging": False, "issuer": None, "profile": m.get("profile"), "last_try": m.get("last_try"),
           "renew_left_h": RENEW_LEFT_H}
    if inf is None:
        return out
    left = hours_left(inf)
    names = [n for n in inf.get("names") or [] if norm_ip(n)]
    out.update(ip=names[0] if names else m.get("ip"), not_after=inf["not_after"], hours_left=round(left, 1),
               days_left=round(left / 24.0, 1), staging=bool(inf["staging"]), issuer=inf["issuer"])
    if left <= 0:
        out["state"] = "expired"
    elif ip is not None and not covers(inf, ip):
        out["state"] = "other_ip"
    elif left < RENEW_LEFT_H:
        out["state"] = "expiring"
    else:
        out["state"] = "valid"
    return out


def serving_pair(ip):
    """(chain PEM, key PEM) for the panel, or None (see the module doc)."""
    inf = info()
    if inf is None or inf["staging"] or not covers(inf, ip) or hours_left(inf) <= SERVE_MIN_H:
        return None
    crt, key = util.read_text(fullchain_path()), util.read_text(privkey_path())
    if not crt or not key:
        return None
    return crt, key


def install(chain_pem, key_src):
    """Put a new issue in place: <stamp>/ with the chain and the key (moved from ``key_src``), then the link
    ``current`` switched by one rename; old issues beyond KEEP go. -> the directory."""
    util.ensure_dir(base_dir(), 0o700)
    d = os.path.join(base_dir(), util.stamp())
    n = 1
    while os.path.exists(d):
        d = os.path.join(base_dir(), "%s-%d" % (util.stamp(), n))
        n += 1
    os.makedirs(d, 0o700)
    util.atomic_write(os.path.join(d, "fullchain.pem"), chain_pem, 0o644)
    os.chmod(key_src, 0o600)
    shutil.move(key_src, os.path.join(d, "privkey.pem"))
    tmp = os.path.join(base_dir(), ".current.new")
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(os.path.basename(d), tmp)
    os.rename(tmp, current_dir())
    _gc(os.path.basename(d))
    return d


def _gc(keep_name):
    try:
        names = sorted(n for n in os.listdir(base_dir())
                       if n[:1].isdigit() and os.path.isdir(os.path.join(base_dir(), n)))
    except OSError:
        return
    for n in [x for x in names[:-KEEP] if x != keep_name]:
        shutil.rmtree(os.path.join(base_dir(), n), ignore_errors=True)


def purge():
    """Every file of the IP certificate and the accounts away. -> True when something was there."""
    had = False
    for d in (base_dir(), account_dir()):
        if os.path.lexists(d):
            shutil.rmtree(d, ignore_errors=True)
            had = True
    return had
