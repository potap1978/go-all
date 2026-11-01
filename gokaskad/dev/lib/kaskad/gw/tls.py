"""The node's TLS certificate for the TLS inbounds (docs/k3/SPEC-wave2-gateway.md §3.3): SSTP now, and the open
mode of the web panel (docs/k3/SPEC-wave3.md §3.1, kaskad.panel.conf); the domain mode of wave 4 puts a
certificate of a public CA in the same place.

    ensure(st, sec, runner, rec)  make the certificate if it is missing or was made for another address
                                  -> what was created (no secrets); ``rec["cert_host"]`` remembers the address
    made_for(sec, host)           the certificate exists and its CN is ``host``
    files(sec)                    {/etc/kaskad/gw/tls/server.crt: (pem, 0644), …/server.key: (pem, 0600)}
    cert_path(), key_path()
    fingerprint(sec)              SHA-256 of the certificate "AB:CD:…" (what the owner compares)

Self-signed RSA 2048 with the client-facing address (``gateway.host``, an IP or a name) as CN and SAN:
Windows' SSTP client compares the address typed into the connection with them, and RSA is what every
client takes (the routers' TLS stacks and old Android apps are not sure about EC). Secrets
``tls.server.key`` / ``tls.server.crt``.
"""

import os

from kaskad import paths
from kaskad.i18n import tr
from kaskad.gw.inbounds import pki, tunnel
from kaskad.gw.inbounds.base import InboundError

DIR = "/etc/kaskad/gw/tls"
KEY = "tls.server.key"
CRT = "tls.server.crt"


def tls_dir():
    return paths.P(DIR)


def cert_path():
    return os.path.join(tls_dir(), "server.crt")


def key_path():
    return os.path.join(tls_dir(), "server.key")


def ready(sec):
    return bool(tunnel.sget(sec, KEY) and tunnel.sget(sec, CRT))


def _host(st):
    try:
        return tunnel.host(st)
    except InboundError:
        return ""


def made_for(sec, host):
    """The node's certificate exists and was made for ``host`` (its CN; "kaskad-gw" without an address)."""
    if not ready(sec):
        return False
    try:
        return pki.subject_cn(tunnel.sget(sec, CRT)) == (host or "kaskad-gw")
    except (ValueError, IndexError):
        return False


def ensure(st, sec, runner, rec):
    """The certificate for the current address; a new one when the address changed (the old one would
    not match what clients type). ``rec`` is the inbound record that keeps ``cert_host``. A record without
    ``cert_host`` takes over a certificate already made for this very address (the web panel's open mode
    makes it before SSTP is installed): its fingerprint stays what the owner checked."""
    host = _host(st)
    if ready(sec) and str(rec.get("cert_host") or "") == host:
        return []
    if not rec.get("cert_host") and made_for(sec, host):
        rec["cert_host"] = host
        return []
    if runner is None:
        raise InboundError(tr("gw_tls.apply_only"))
    tunnel.ensure_dir(tls_dir())
    key, crt = pki.self_signed(runner, tls_dir(), host or "kaskad-gw", host or None, key="rsa")
    tunnel.sput(sec, KEY, key)
    tunnel.sput(sec, CRT, crt)
    rec["cert_host"] = host
    return [tr("gw_tls.made", host=host) if host else tr("gw_tls.made_node")]


def files(sec):
    key, crt = tunnel.sget(sec, KEY), tunnel.sget(sec, CRT)
    if not key or not crt:
        return {}
    return {cert_path(): (crt, 0o644), key_path(): (key, 0o600)}


def fingerprint(sec):
    crt = tunnel.sget(sec, CRT)
    return pki.sha256_fingerprint(crt) if crt else ""
