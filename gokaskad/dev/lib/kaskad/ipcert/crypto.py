"""Keys, signatures and the CSR of the IP certificate.

The crypto itself is the ``openssl`` command (``Openssl``): key generation, the public key, ``dgst -sha256 -sign``
with ``-hex`` (text output: the runner speaks text). The structures are built here in DER, so the result does not
depend on the version of openssl or its config (``openssl req`` of 1.1/3.x/LibreSSL differs on empty subjects and
``-addext``): the CSR has an empty subject and one extension — subjectAltName with the iPAddress, as Let's Encrypt
wants for the profile ``shortlived``.

    Openssl(runner)          .gen_rsa(path) .gen_ec(path) .public_der(path) .sign(path, data|file)
    rsa_jwk(spki)            {"e", "kty": "RSA", "n"} of a SubjectPublicKeyInfo
    thumbprint(jwk)          RFC 7638, base64url
    csr(crypto, key, ip, workdir)   DER of the CSR, signed by ``key`` (EC P-256: ecdsa-with-SHA256)

Keys never leave their 0600 files and never go to argv: openssl gets the path. Data to sign goes on stdin (text) or
as a file in a 0700 work directory (the binary CSR body).
"""

import base64
import hashlib
import ipaddress
import json
import os
import re
import tempfile

from .. import util
from ..errors import KaskadError
from ..i18n import tr

OPENSSL = "openssl"
TIMEOUT = 120

OID_SAN = "2.5.29.17"
OID_EXT_REQ = "1.2.840.113549.1.9.14"
OID_ECDSA_SHA256 = "1.2.840.10045.4.3.2"
OID_SHA256_RSA = "1.2.840.113549.1.1.11"
OID_RSA = "1.2.840.113549.1.1.1"
OID_EC = "1.2.840.10045.2.1"


def b64u(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pem_der(pem):
    """DER of the first PEM block of ``pem``."""
    m = re.search(r"-----BEGIN [^-]+-----(.*?)-----END [^-]+-----", pem or "", re.S)
    if not m:
        raise ValueError(tr("ipcert.crypto.no_pem"))
    return base64.b64decode("".join(m.group(1).split()))


# ── DER: writing ───────────────────────────────────────────────────────────

def _length(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def der(tag, body):
    return bytes([tag]) + _length(len(body)) + body


def seq(*parts):
    return der(0x30, b"".join(parts))


def set_of(*parts):
    return der(0x31, b"".join(parts))


def integer(n):
    raw = n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return der(0x02, raw)


def oid(dotted):
    nums = [int(x) for x in dotted.split(".")]
    out = bytearray([40 * nums[0] + nums[1]])
    for n in nums[2:]:
        chunk = [n & 0x7F]
        n >>= 7
        while n:
            chunk.append(0x80 | (n & 0x7F))
            n >>= 7
        out.extend(reversed(chunk))
    return der(0x06, bytes(out))


def octets(data):
    return der(0x04, data)


def bits(data):
    return der(0x03, b"\x00" + data)


# ── DER: reading ───────────────────────────────────────────────────────────

def tlv(data, pos):
    """(tag, start of the value, its length) of the element at ``pos``."""
    tag = data[pos]
    n = data[pos + 1]
    pos += 2
    if n & 0x80:
        k = n & 0x7F
        if not 1 <= k <= 4:
            raise ValueError(tr("ipcert.crypto.der_len"))
        n = int.from_bytes(data[pos:pos + k], "big")
        pos += k
    if pos + n > len(data):
        raise ValueError(tr("ipcert.crypto.der_cut"))
    return tag, pos, n


def children(data, start, end):
    out = []
    pos = start
    while pos < end:
        tag, vp, vl = tlv(data, pos)
        out.append((tag, vp, vl))
        pos = vp + vl
    return out


def rsa_jwk(spki):
    """{"e", "kty", "n"} of an RSA SubjectPublicKeyInfo (DER)."""
    try:
        _, sp, sl = tlv(spki, 0)
        alg, key = children(spki, sp, sp + sl)[:2]
        _, ap, al = alg
        if spki[ap:ap + al][:len(oid(OID_RSA))] != oid(OID_RSA):
            raise ValueError(tr("ipcert.crypto.not_rsa"))
        _, kp, kl = key
        inner = spki[kp + 1:kp + kl]                        # BIT STRING: the first byte is the unused bits
        _, rp, rl = tlv(inner, 0)
        n_el, e_el = children(inner, rp, rp + rl)[:2]
        n = inner[n_el[1]:n_el[1] + n_el[2]].lstrip(b"\x00")
        e = inner[e_el[1]:e_el[1] + e_el[2]].lstrip(b"\x00")
    except (IndexError, ValueError) as ex:
        raise KaskadError(tr("ipcert.crypto.bad_account_key", error=ex))
    return {"e": b64u(e), "kty": "RSA", "n": b64u(n)}


def thumbprint(jwk):
    raw = json.dumps(jwk, sort_keys=True, separators=(",", ":")).encode("ascii")
    return b64u(hashlib.sha256(raw).digest())


def ip_bytes(ip):
    return ipaddress.ip_address(ip).packed


def csr_info(spki, ip):
    """CertificationRequestInfo: version 0, empty subject, the key, extensionRequest {subjectAltName: iPAddress}."""
    san = seq(oid(OID_SAN), octets(seq(der(0x87, ip_bytes(ip)))))
    attrs = der(0xA0, seq(oid(OID_EXT_REQ), set_of(seq(san))))
    return seq(integer(0), seq(), spki, attrs)


def csr(crypto, key_path, ip, workdir):
    """DER of a CSR for ``ip`` signed with the EC key ``key_path``."""
    spki = crypto.public_der(key_path)
    info = csr_info(spki, ip)
    fd, tmp = tempfile.mkstemp(prefix=".csr-", dir=workdir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(info)
        sig = crypto.sign(key_path, path=tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return seq(info, seq(oid(OID_ECDSA_SHA256)), bits(sig))


# ── openssl ────────────────────────────────────────────────────────────────

class Openssl(object):
    """The crypto of the ACME client through the openssl command (``runner`` — kaskad.sysrun)."""

    def __init__(self, runner):
        self.runner = runner

    def available(self):
        return bool(self.runner.which(OPENSSL))

    def _run(self, argv, input=None, what="openssl"):
        res = self.runner.run([OPENSSL] + argv, input=input, timeout=TIMEOUT)
        if not res.ok:
            detail = util.redact((res.err or res.out or "").strip()[:300]) or tr("ipcert.crypto.rc", rc=res.rc)
            raise KaskadError(tr("ipcert.crypto.failed", what=what, detail=detail))
        return res.out

    def _gen(self, path, argv, what):
        d = os.path.dirname(path)
        util.ensure_dir(d, 0o700)
        tmp = os.path.join(d, ".new-" + os.path.basename(path))
        try:
            self._run(["genpkey"] + argv + ["-out", tmp], what=what)
            os.chmod(tmp, 0o600)
            os.rename(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return path

    def gen_rsa(self, path):
        return self._gen(path, ["-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
                         tr("ipcert.crypto.what_account_key"))

    def gen_ec(self, path):
        return self._gen(path, ["-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"],
                         tr("ipcert.crypto.what_cert_key"))

    def public_der(self, path):
        return pem_der(self._run(["pkey", "-in", path, "-pubout"], what=tr("ipcert.crypto.what_public_key")))

    def sign(self, key_path, data=None, path=None):
        """SHA-256 signature by ``key_path`` of ``data`` (text, on stdin) or of the file ``path``: raw bytes
        (RSA PKCS#1 v1.5; EC — DER ECDSA-Sig-Value, what X.509 wants)."""
        argv = ["dgst", "-sha256", "-sign", key_path, "-hex"]
        if path is not None:
            argv.append(path)
        out = self._run(argv, input=data, what=tr("ipcert.crypto.what_sign"))
        hexed = out.strip().rsplit("= ", 1)[-1].strip()
        if not re.match(r"^[0-9a-fA-F]+$", hexed):
            raise KaskadError(tr("ipcert.crypto.bad_sign"))
        return bytes.fromhex(hexed)
