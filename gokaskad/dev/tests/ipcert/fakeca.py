"""A fake ACME CA (RFC 8555, the parts kaskad.ipcert uses) and fake crypto for the tests — no network, no openssl.

``FakeCA.transport`` is given to ``acme.Client``; ``answer`` of a challenge makes the CA fetch the token the way Let's
Encrypt does: over HTTP from ``fetch_base`` (the standalone listener of the test) or by reading the webroot file.
Failures on demand: ``fail_order`` (a problem document for newOrder), ``fail_check`` (the challenge becomes invalid),
``net`` (no answer at all), ``no_profile`` (the directory has no «shortlived»).
"""

import base64
import json
import os
import time
import urllib.request

from kaskad.ipcert import acme, crypto as kc

D = "https://ca.test/dir"


def cert_pem(ip="1.1.1.1", hours=160, issuer="YE1", start=None):
    """A minimal X.509 (not signed for real) that certinfo reads: SAN iPAddress, validity, issuer CN."""
    start = time.time() - 60 if start is None else start

    def gt(ts):
        return kc.der(0x18, time.strftime("%Y%m%d%H%M%SZ", time.gmtime(ts)).encode("ascii"))
    name = kc.seq(kc.set_of(kc.seq(kc.oid("2.5.4.3"), kc.der(0x0C, issuer.encode("utf-8")))))
    spki = kc.seq(kc.seq(kc.oid(kc.OID_EC)), kc.bits(b"\x04" + b"\x01" * 64))
    san = kc.seq(kc.oid(kc.OID_SAN), kc.octets(kc.seq(kc.der(0x87, kc.ip_bytes(ip)))))
    tbs = kc.seq(kc.der(0xA0, kc.integer(2)), kc.integer(12345), kc.seq(kc.oid(kc.OID_ECDSA_SHA256)), name,
                 kc.seq(gt(start), gt(start + hours * 3600)), kc.seq(), spki, kc.der(0xA3, kc.seq(san)))
    der = kc.seq(tbs, kc.seq(kc.oid(kc.OID_ECDSA_SHA256)), kc.bits(b"\x30\x00"))
    b64 = base64.b64encode(der).decode("ascii")
    return "-----BEGIN CERTIFICATE-----\n%s\n-----END CERTIFICATE-----\n" % "\n".join(
        b64[i:i + 64] for i in range(0, len(b64), 64))


def rsa_spki():
    n = (1 << 2047) + 12345
    return kc.seq(kc.seq(kc.oid(kc.OID_RSA), b"\x05\x00"), kc.bits(kc.seq(kc.integer(n), kc.integer(65537))))


class FakeCrypto(object):
    def __init__(self):
        self.signed = []
        self.keys = []

    def available(self):
        return True

    def _gen(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("-----BEGIN PRIVATE KEY-----\nZmFrZQ==\n-----END PRIVATE KEY-----\n")
        os.chmod(path, 0o600)
        self.keys.append(path)
        return path

    gen_rsa = _gen
    gen_ec = _gen

    def public_der(self, path):
        return rsa_spki()

    def sign(self, key_path, data=None, path=None):
        self.signed.append(data if data is not None else "<file>")
        return b"\x01\x02"


class FakeCA(object):
    def __init__(self, ip="1.1.1.1", hours=160):
        self.ip = ip
        self.hours = hours
        self.n = 0
        self.nonces = set()
        self.used = []
        self.orders = {}
        self.calls = []
        self.fetch_base = None          # "http://127.0.0.1:<port>" — the standalone listener
        self.webroot = None
        self.responder = None           # a Standalone: the CA fetches from its port
        self.fail_order = None          # (status, problem dict)
        self.fail_check = None          # problem dict
        self.net = False
        self.no_profile = False
        self.issued = 0
        self.profile = None
        self.issuer = "YE1"
        self.accounts = 0

    def _nonce(self):
        self.n += 1
        n = "nonce%d" % self.n
        self.nonces.add(n)
        return n

    def _resp(self, status, doc=None, headers=None, raw=None):
        h = {"replay-nonce": self._nonce()}
        h.update(headers or {})
        body = raw if raw is not None else (json.dumps(doc).encode("utf-8") if doc is not None else b"")
        return status, h, body

    def transport(self, method, url, body=None, headers=None, timeout=None):
        if self.net:
            raise acme.AcmeError("net", "нет связи")
        self.calls.append((method, url))
        if url == D:
            meta = {} if self.no_profile else {"profiles": {"classic": "", "shortlived": "6 days"}}
            return 200, {}, json.dumps({"newNonce": "https://ca.test/nonce", "newAccount": "https://ca.test/acct",
                                        "newOrder": "https://ca.test/order", "meta": meta}).encode("utf-8")
        if url.endswith("/nonce"):
            return self._resp(200)
        jws = json.loads(body.decode("ascii"))
        prot = json.loads(base64.urlsafe_b64decode(jws["protected"] + "=="))
        assert prot["url"] == url, (prot, url)
        assert prot["nonce"] in self.nonces and prot["nonce"] not in self.used, prot
        self.used.append(prot["nonce"])
        payload = json.loads(base64.urlsafe_b64decode(jws["payload"] + "==")) if jws["payload"] else None
        if url.endswith("/acct"):
            assert "jwk" in prot and "kid" not in prot
            assert payload["termsOfServiceAgreed"] is True
            self.accounts += 1
            return self._resp(201, {"status": "valid"}, {"location": "https://ca.test/acct/1"})
        assert prot.get("kid") == "https://ca.test/acct/1", prot
        if url.endswith("/order"):
            if self.fail_order:
                st, prob = self.fail_order
                return self._resp(st, prob, {"retry-after": "3600"} if st == 429 else None)
            assert payload["identifiers"] == [{"type": "ip", "value": self.ip}], payload
            self.profile = payload.get("profile")
            self.orders["o1"] = {"status": "pending", "authorizations": ["https://ca.test/authz/1"],
                                 "finalize": "https://ca.test/fin/1", "identifiers": payload["identifiers"]}
            self.authz = {"status": "pending", "challenges": [
                {"type": "dns-01", "url": "https://ca.test/ch/dns", "token": "x" * 43},
                {"type": "http-01", "url": "https://ca.test/ch/1", "token": "tok_" + "A" * 39, "status": "pending"}]}
            return self._resp(201, self.orders["o1"], {"location": "https://ca.test/o/1"})
        if url.endswith("/authz/1"):
            return self._resp(200, self.authz)
        if url.endswith("/ch/1"):
            token = self.authz["challenges"][1]["token"]
            got = self._fetch(token)
            want = "%s.%s" % (token, kc.thumbprint(kc.rsa_jwk(rsa_spki())))
            if self.fail_check or got != want:
                prob = self.fail_check or {"type": acme.PROBLEM + "incorrectResponse", "detail": "got %r" % got}
                self.authz["status"] = "invalid"
                self.authz["challenges"][1]["error"] = prob
            else:
                self.authz["status"] = "valid"
                self.orders["o1"]["status"] = "ready"
            return self._resp(200, self.authz["challenges"][1])
        if url.endswith("/o/1"):
            return self._resp(200, self.orders["o1"])
        if url.endswith("/fin/1"):
            assert payload["csr"]
            self.csr = base64.urlsafe_b64decode(payload["csr"] + "==")
            self.orders["o1"].update(status="valid", certificate="https://ca.test/cert/1")
            self.issued += 1
            return self._resp(200, self.orders["o1"])
        if url.endswith("/cert/1"):
            return self._resp(200, raw=(cert_pem(self.ip, self.hours, self.issuer) + cert_pem(self.ip, 24 * 365, "ISRG")).encode())
        raise AssertionError("unexpected %s %s" % (method, url))

    def _fetch(self, token):
        if self.webroot:
            try:
                with open(os.path.join(self.webroot, ".well-known", "acme-challenge", token)) as f:
                    return f.read()
            except OSError:
                return None
        base = self.fetch_base or ("http://127.0.0.1:%d" % self.responder.port if self.responder else None)
        if base:
            try:
                with urllib.request.urlopen("%s/.well-known/acme-challenge/%s" % (base, token),
                                            timeout=5) as r:
                    return r.read().decode("ascii")
            except OSError:
                return None
        return None
