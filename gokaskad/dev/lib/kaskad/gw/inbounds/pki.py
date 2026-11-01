"""Certificates of the tunnel inbounds with the openssl command line (EC P-256), through the runner.

    new_ca(runner, workdir, cn, scope=None, eku=None)           -> (key_pem, cert_pem)  CA, 10 years
    issue(runner, workdir, ca_key, ca_cert, cn, usage, san=None) -> (key_pem, cert_pem)  usage server|client|ike
    self_signed(runner, workdir, cn, san=None)                  -> (key_pem, cert_pem)  server leaf, signs itself
    crl(runner, workdir, ca_key, ca_cert, revoked)              -> crl_pem  (``openssl ca -gencrl``)
    spki_pin(cert_pem)            -> "pin-sha256:<base64>" of the SubjectPublicKeyInfo (pure Python)
    sha256_fingerprint(cert_pem)  -> "AB:CD:..." of the DER certificate (pure Python)
    serial_hex(cert_pem), subject_cn(cert_pem), name_constraints(cert_pem)      (pure Python)

``key="rsa"`` (new_ca, issue, self_signed) makes RSA 2048 keys instead of EC P-256 — for the built-in
clients of Windows, Apple and Android (IKEv2, SSTP), where RSA is the safe common ground. Usage ``ike``
is a server certificate with the EKU serverAuth + ikeIntermediate (1.3.6.1.5.5.8.2.2) that Windows and
macOS/iOS look for on an IKEv2 server.

A CA is ``CA:TRUE, pathlen:0`` (it signs end certificates only). ``scope`` (a host: IPv4 or DNS name) makes
a CA the clients may put into their trusted roots (IKEv2: Windows LocalMachine\\Root, an Apple profile) that
vouches for that one host only, not for any site:

    nameConstraints = critical, permitted;IP:<ip>/255.255.255.255, permitted;DNS:<ip>      (an address)
    nameConstraints = critical, permitted;DNS:<name>, permitted;IP:0.0.0.0/255.255.255.255  (a name)

BOTH name forms are constrained: RFC 5280 constrains only the forms listed, so ``permitted;IP:<ip>`` alone
still let the CA sign any DNS name (and ``DNS:<name>`` alone any address) — seen with OpenSSL 3. ``DNS:<ip>``
keeps the server certificate valid where its CN (the address, no DNS SAN) is checked as a DNS name (OpenSSL)
and matches no real name; ``IP:0.0.0.0/32`` matches no address (IPv6 SANs are outside an IPv4 subtree too).
``eku`` puts an extendedKeyUsage on the CA as well — Windows narrows a chain to the EKUs of its CAs, so such a
root is no good for code signing or S/MIME. Checked with OpenSSL 3: our server certificate verifies (also with
``-purpose sslserver``); another address, a DNS name, a CN-only name, IPv6, S/MIME — «permitted subtree
violation» or a purpose error.

Temporary files live in a 0700 directory inside ``workdir`` (the adapter's own directory) and are
removed whatever happens; keys never appear on a command line. One explicit config file per call,
no ``-addext`` and no ``req -CA``: the same commands work with OpenSSL 1.1.1 and 3.x.
"""

import base64
import hashlib
import ipaddress
import os
import re
import secrets as _random
import shutil
import tempfile
import time

from kaskad import util
from kaskad.gw.inbounds.base import InboundError
from kaskad.i18n import tr

DAYS = 3650
CRL_DAYS = 3650                     # an expired CRL makes OpenVPN refuse every client: as long as the CA
RSA_BITS = 2048
_CN_BAD = re.compile(r"[^A-Za-z0-9 ._:@-]")
_DNS_BAD = re.compile(r"[^A-Za-z0-9.-]")
_EKU = {"client": "clientAuth", "ike": "serverAuth, 1.3.6.1.5.5.8.2.2"}
_SERIAL = re.compile(r"^[0-9A-F]{2,64}$")


NO_ADDRESS = "IP:0.0.0.0/255.255.255.255"


def scope_name(host):
    """The subtree of the host itself: ``IP:203.0.113.10/255.255.255.255`` or ``DNS:vpn.example.org``."""
    h = str(host or "").strip()
    try:
        return "IP:%s/255.255.255.255" % ipaddress.IPv4Address(h)
    except ValueError:
        return "DNS:%s" % _DNS_BAD.sub("", h).lower()


def scope_names(host):
    """Every permitted subtree of a CA scoped to ``host`` (both name forms, see the module text)."""
    h = str(host or "").strip()
    try:
        ip = ipaddress.IPv4Address(h)
    except ValueError:
        return [scope_name(h), NO_ADDRESS]
    return [scope_name(h), "DNS:%s" % ip]


def _config(cn, usage, san, key="ec", scope=None, eku=None):
    lines = ["[ req ]", "distinguished_name = dn", "prompt = no", "", "[ dn ]",
             "CN = %s" % (_CN_BAD.sub("_", cn)[:64] or "kaskad"), "", "[ ext ]"]
    if usage == "ca":
        lines += ["basicConstraints = critical, CA:TRUE, pathlen:0", "keyUsage = critical, keyCertSign, cRLSign",
                  "subjectKeyIdentifier = hash"]
        if eku:
            lines.append("extendedKeyUsage = %s" % _EKU.get(eku, "serverAuth"))
        if scope:
            lines.append("nameConstraints = critical, %s" % ", ".join("permitted;" + n for n in scope_names(scope)))
    else:
        ku = "digitalSignature, keyEncipherment" if key == "rsa" else "digitalSignature"
        lines += ["basicConstraints = critical, CA:FALSE", "keyUsage = critical, %s" % ku,
                  "extendedKeyUsage = %s" % _EKU.get(usage, "serverAuth"),
                  "subjectKeyIdentifier = hash"]
        if usage in ("server", "client", "ike"):
            lines.append("authorityKeyIdentifier = keyid")
        if san:
            try:
                ipaddress.ip_address(san)
                alt = "IP.1 = %s" % san
            except ValueError:
                alt = "DNS.1 = %s" % _CN_BAD.sub("", san)
            lines += ["subjectAltName = @alt", "", "[ alt ]", alt]
    return "\n".join(lines) + "\n"


def _serial():
    return "0x" + _random.token_hex(16)


def _run(runner, argv, what):
    res = runner.run(argv, timeout=120)
    if not res.ok:
        lines = [ln.strip() for ln in (res.err or res.out or "").splitlines() if ln.strip()]
        raise InboundError("openssl %s: %s" % (what, util.redact(lines[0][:300]) if lines else tr("gw_in.rc", rc=res.rc)))
    return res


def _write(d, name, text, mode=0o600):
    p = os.path.join(d, name)
    util.atomic_write(p, text, mode)
    return p


def _read(p, what):
    text = util.read_text(p)
    if not text or "-----BEGIN" not in text:
        raise InboundError(tr("gw_in_pki.no_result", what=what))
    return text


class _Tmp(object):
    def __init__(self, workdir):
        self.workdir = workdir

    def __enter__(self):
        if not os.path.isdir(self.workdir):
            os.makedirs(self.workdir, 0o700)
        self.path = tempfile.mkdtemp(prefix=".pki-", dir=self.workdir)
        os.chmod(self.path, 0o700)
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def _genkey(runner, d, key="ec"):
    p = os.path.join(d, "key.pem")
    if key == "rsa":
        argv = ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:%d" % RSA_BITS, "-out", p]
    else:
        argv = ["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", p]
    _run(runner, argv, "genpkey")
    return p, _read(p, "genpkey")


def _self_sign(runner, workdir, cn, usage, san, key="ec", scope=None, eku=None):
    with _Tmp(workdir) as d:
        kp, pem = _genkey(runner, d, key)
        cnf = _write(d, "req.cnf", _config(cn, usage, san, key, scope, eku))
        out = os.path.join(d, "cert.pem")
        _run(runner, ["openssl", "req", "-new", "-x509", "-config", cnf, "-extensions", "ext", "-key", kp,
                      "-sha256", "-days", str(DAYS), "-set_serial", _serial(), "-out", out], "req -x509")
        return pem, _read(out, "req -x509")


def new_ca(runner, workdir, cn, key="ec", scope=None, eku=None):
    """A CA (``CA:TRUE, pathlen:0``); ``scope`` — the one host it may vouch for (nameConstraints); ``eku`` —
    the only purpose of its chains (``ike``: serverAuth + ikeIntermediate)."""
    return _self_sign(runner, workdir, cn, "ca", None, key, scope, eku)


def self_signed(runner, workdir, cn, san=None, key="ec"):
    return _self_sign(runner, workdir, cn, "self", san, key)


def issue(runner, workdir, ca_key, ca_cert, cn, usage, san=None, key="ec"):
    if usage not in ("server", "client", "ike"):
        raise ValueError("usage must be server, client or ike")
    with _Tmp(workdir) as d:
        kp, pem = _genkey(runner, d, key)
        cnf = _write(d, "req.cnf", _config(cn, usage, san, key))
        csr = os.path.join(d, "req.csr")
        _run(runner, ["openssl", "req", "-new", "-config", cnf, "-key", kp, "-sha256", "-out", csr], "req")
        cak = _write(d, "ca.key", ca_key)
        cac = _write(d, "ca.pem", ca_cert)
        out = os.path.join(d, "cert.pem")
        _run(runner, ["openssl", "x509", "-req", "-in", csr, "-CA", cac, "-CAkey", cak, "-set_serial", _serial(),
                      "-sha256", "-days", str(DAYS), "-extfile", cnf, "-extensions", "ext", "-out", out], "x509 -req")
        return pem, _read(out, "x509 -req")


def _utc(ts):
    return time.strftime("%y%m%d%H%M%SZ", time.gmtime(int(ts)))


def crl(runner, workdir, ca_key, ca_cert, revoked, days=CRL_DAYS):
    """A CRL of the CA listing ``revoked`` = [(serial hex, revoked at: epoch)], by ``openssl ca -gencrl`` with a
    throwaway database (index.txt); an empty list is a valid empty CRL. -> crl_pem."""
    rows = []
    for serial, at in sorted(set((str(s).upper(), int(a)) for s, a in revoked)):
        if not _SERIAL.match(serial):
            raise InboundError(tr("gw_in_pki.bad_serial", serial=repr(serial)))
        # R <expiry> <revoked at> <serial> unknown <subject>: gencrl reads only the type, the date and the serial
        rows.append("R\t491231235959Z\t%s\t%s\tunknown\t/CN=revoked\n" % (_utc(at), serial))
    with _Tmp(workdir) as d:
        index = _write(d, "index.txt", "".join(rows))
        cnf = _write(d, "ca.cnf", "\n".join([
            "[ ca ]", "default_ca = kaskad", "", "[ kaskad ]", "database = %s" % index, "default_md = sha256",
            "default_crl_days = %d" % int(days), "unique_subject = no", ""]))
        cak = _write(d, "ca.key", ca_key)
        cac = _write(d, "ca.pem", ca_cert)
        out = os.path.join(d, "crl.pem")
        _run(runner, ["openssl", "ca", "-gencrl", "-config", cnf, "-keyfile", cak, "-cert", cac, "-out", out],
             "ca -gencrl")
        text = util.read_text(out)
        if not text or "-----BEGIN X509 CRL-----" not in text:
            raise InboundError(tr("gw_in_pki.no_result", what="ca -gencrl"))
        return text


# ── pure Python: certificate fingerprints ──────────────────────────────────

def pem_der(pem, label="CERTIFICATE"):
    begin, end = "-----BEGIN %s-----" % label, "-----END %s-----" % label
    if begin not in pem or end not in pem:
        raise ValueError("no %s block" % label)
    body = pem.split(begin, 1)[1].split(end, 1)[0]
    return base64.b64decode("".join(body.split()))


def _tlv(der, pos):
    """(tag, value start, value length) of the DER element at ``pos``."""
    tag = der[pos]
    length = der[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(der[pos:pos + n], "big")
        pos += n
    return tag, pos, length


def spki_der(cert_pem):
    """DER SubjectPublicKeyInfo of an X.509 certificate (the 7th field of tbsCertificate, 6th without
    the optional version)."""
    der = pem_der(cert_pem)
    _, p, _ = _tlv(der, 0)                    # Certificate
    _, p, _ = _tlv(der, p)                    # tbsCertificate
    tag, vp, vl = _tlv(der, p)
    if tag == 0xA0:                           # [0] version
        p = vp + vl
    for _ in range(5):                        # serial, signature, issuer, validity, subject
        _, vp, vl = _tlv(der, p)
        p = vp + vl
    tag, vp, vl = _tlv(der, p)
    if tag != 0x30:
        raise ValueError("certificate has no SubjectPublicKeyInfo")
    return der[p:vp + vl]


def spki_pin(cert_pem):
    """``pin-sha256:...`` as openconnect --servercert takes it."""
    return "pin-sha256:" + base64.b64encode(hashlib.sha256(spki_der(cert_pem)).digest()).decode("ascii")


def sha256_fingerprint(cert_pem):
    h = hashlib.sha256(pem_der(cert_pem)).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def thumbprint(cert_pem):
    """SHA-1 of the DER certificate, upper hex — the Thumbprint of Windows' certificate stores."""
    return hashlib.sha1(pem_der(cert_pem)).hexdigest().upper()


# ── pure Python: fields of our own certificates (DER walk) ─────────────────

_OID_CN = bytes.fromhex("550403")                 # 2.5.4.3 commonName
_OID_NAME_CONSTRAINTS = bytes.fromhex("551d1e")   # 2.5.29.30


def _items(der, start, end):
    """[(tag, value start, value length)] of the DER elements in der[start:end]."""
    out, p = [], start
    while p < end:
        tag, vp, vl = _tlv(der, p)
        out.append((tag, vp, vl))
        p = vp + vl
    return out


def _tbs(der):
    """The fields of tbsCertificate without the optional [0] version: serial, signature, issuer, validity,
    subject, spki, then the optional [1], [2], [3] (extensions)."""
    _, p, _ = _tlv(der, 0)
    _, vp, vl = _tlv(der, p)
    fields = _items(der, vp, vp + vl)
    return fields[1:] if fields and fields[0][0] == 0xA0 else fields


def serial_hex(cert_pem):
    """The serial number as ``openssl`` writes it in index.txt: upper hex, no leading zero bytes."""
    der = pem_der(cert_pem)
    _, vp, vl = _tbs(der)[0]
    return der[vp:vp + vl].lstrip(b"\x00").hex().upper() or "00"


def subject_cn(cert_pem):
    """The CN of the subject ("" if none)."""
    der = pem_der(cert_pem)
    _, vp, vl = _tbs(der)[4]
    for _, sp, sl in _items(der, vp, vp + vl):                 # RDN (SET)
        for _, ap, al in _items(der, sp, sp + sl):             # AttributeTypeAndValue (SEQUENCE)
            parts = _items(der, ap, ap + al)
            if len(parts) == 2 and der[parts[0][1]:parts[0][1] + parts[0][2]] == _OID_CN:
                return der[parts[1][1]:parts[1][1] + parts[1][2]].decode("utf-8", "replace")
    return ""


def _general_name(der, tag, vp, vl):
    value = der[vp:vp + vl]
    if tag == 0x82:                                            # [2] dNSName
        return "DNS:" + value.decode("ascii", "replace").lower()
    if tag == 0x87 and vl == 8:                                # [7] iPAddress, IPv4 + mask
        return "IP:%s/%s" % (ipaddress.IPv4Address(value[:4]), ipaddress.IPv4Address(value[4:]))
    return "other"


def name_constraints(cert_pem):
    """Permitted subtrees of a CA certificate: ``["IP:203.0.113.10/255.255.255.255"]``, ``["DNS:vpn.example.org"]``;
    ``[]`` when the extension permits nothing by name; None when the certificate has no nameConstraints (a CA
    for any name — the IKEv2 CA of an earlier build)."""
    der = pem_der(cert_pem)
    for tag, vp, vl in _tbs(der)[6:]:
        if tag != 0xA3:                                        # [3] extensions
            continue
        _, sp, sl = _tlv(der, vp)
        for _, ep, el in _items(der, sp, sp + sl):             # Extension
            parts = _items(der, ep, ep + el)
            if not parts or der[parts[0][1]:parts[0][1] + parts[0][2]] != _OID_NAME_CONSTRAINTS:
                continue
            _, op, ol = parts[-1]                              # extnValue OCTET STRING
            _, np, nl = _tlv(der, op)                          # NameConstraints SEQUENCE
            out = []
            for st, stp, stl in _items(der, np, np + nl):
                if st != 0xA0:                                 # [0] permittedSubtrees ([1] excluded)
                    continue
                for _, gp, gl in _items(der, stp, stp + stl):  # GeneralSubtree
                    base = _items(der, gp, gp + gl)
                    if base:
                        out.append(_general_name(der, *base[0]))
            return out
    return None
