"""A minimal ACME client (RFC 8555) for one certificate of one IP address with an ACME profile.

    Client(directory_url, crypto, account_key, transport=http_request)
        .profiles()                    what the CA offers (``meta.profiles`` of the directory)
        .register()                    the account (newAccount; an existing key gets its account back) -> kid
        .new_order(ip, profile)        -> (order url, order)
        .http01(authz_url)             -> (challenge, token, key authorization)  — status "valid" -> None
        .answer(challenge_url)         «ready»: Let's Encrypt fetches http://<ip>/.well-known/acme-challenge/<token>
        .poll(url, …)                  until "valid" (or "invalid": AcmeError with the CA's reason)
        .finalize(order, csr_der)      -> the order
        .certificate(url)              -> the chain PEM

Every request after the directory is a JWS (RS256, the account key of ``crypto``): ``jwk`` for newAccount, ``kid``
afterwards; a fresh nonce from each answer (``Replay-Nonce``), one retry on ``badNonce``. Errors are
``AcmeError(reason, text, retry_after)``:

    limit        rateLimited (429): ``Retry-After`` / «retry after …» of the problem document
    challenge    the check of http-01 failed: connection / unauthorized / incorrectResponse / dns / timeouts —
                 Let's Encrypt did not reach TCP/80 of this address or got a wrong answer
    unsupported  rejectedIdentifier / unsupportedIdentifier / malformed ip: the CA does not issue for this address
    profile      the CA has no profile ``shortlived`` (or refused it)
    net          no answer from the CA at all (DNS, TCP, TLS, timeouts)
    server       anything else the CA said (its words are kept, redacted)

``transport(method, url, body, headers, timeout)`` -> (status, headers with lower-case names, body bytes); the tests
give a fake CA, production — urllib (``http_request``).
"""

import calendar
import json
import re
import socket
import time
import urllib.error
import urllib.request

from .. import __version__, util
from ..errors import KaskadError
from ..i18n import tr
from . import crypto as kcrypto

LE_PROD = "https://acme-v02.api.letsencrypt.org/directory"
LE_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
PROFILE = "shortlived"
TIMEOUT = 30
POLL_EVERY = 2.0
POLL_TIMEOUT = 120.0
UA = "kaskad-ipcert/%s" % __version__
PROBLEM = "urn:ietf:params:acme:error:"
_CHALLENGE = ("connection", "unauthorized", "incorrectResponse", "dns", "tls", "caa")
_UNSUPPORTED = ("rejectedIdentifier", "unsupportedIdentifier")
_RETRY = re.compile(r"retry after\s+(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2}))?", re.I)


class AcmeError(KaskadError):
    def __init__(self, reason, text, retry_after=None, detail=""):
        KaskadError.__init__(self, text)
        self.reason = reason
        self.text = text
        self.retry_after = retry_after              # "2026-10-07T12:00:00Z" or None
        self.detail = detail                        # the CA's own words (redacted) for the journal


def http_request(method, url, body=None, headers=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers or {}, **{"User-Agent": UA}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict((k.lower(), v) for k, v in r.headers.items()), r.read(1 << 20)
    except urllib.error.HTTPError as e:
        try:
            data = e.read(1 << 16)
        except OSError:
            data = b""
        hdrs = dict((k.lower(), v) for k, v in (e.headers or {}).items())
        e.close()
        return e.code, hdrs, data
    except (urllib.error.URLError, socket.timeout, OSError, ValueError) as e:
        raise AcmeError("net", tr("ipcert.acme.net", why=_why(e)))


def _why(e):
    r = getattr(e, "reason", None)
    return util.redact(str(r if r is not None else e))[:160] or type(e).__name__


def _iso_after(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + max(0, int(seconds))))


def retry_after(headers, detail):
    """The moment Let's Encrypt allows the next try: the ``Retry-After`` header (seconds or a date), else «retry
    after <date>» of the problem text; None when nothing is said."""
    raw = (headers or {}).get("retry-after")
    if raw:
        raw = raw.strip()
        if raw.isdigit():
            return _iso_after(int(raw))
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.strptime(raw, "%a, %d %b %Y %H:%M:%S GMT"))
        except ValueError:
            pass
    found = _RETRY.findall(detail or "")
    if found:
        day, hm, sec = found[-1]
        return "%sT%s:%sZ" % (day, hm, sec or "00")
    return None


def problem_error(status, headers, body, during=""):
    """AcmeError of an ACME problem document (or of a bare HTTP error)."""
    try:
        doc = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    kind = str(doc.get("type") or "")
    kind = kind[len(PROBLEM):] if kind.startswith(PROBLEM) else kind
    detail = util.redact(str(doc.get("detail") or ""))[:400]
    for sub in doc.get("subproblems") or []:
        if isinstance(sub, dict) and sub.get("detail"):
            detail += " | " + util.redact(str(sub["detail"]))[:200]
            st = str(sub.get("type") or "")
            if kind in ("", "malformed", "compound") and st.startswith(PROBLEM):
                kind = st[len(PROBLEM):]
    if kind == "rateLimited" or status == 429:
        return AcmeError("limit", tr("ipcert.acme.limit"), retry_after(headers, detail), detail)
    if kind in _CHALLENGE:
        return AcmeError("challenge", tr("ipcert.acme.challenge", detail=detail or kind), None, detail)
    if kind in _UNSUPPORTED:
        return AcmeError("unsupported", tr("ipcert.acme.unsupported", detail=detail or kind), None, detail)
    if kind == "invalidProfile" or "profile" in detail.lower():
        return AcmeError("profile", tr("ipcert.acme.profile", profile=PROFILE, detail=detail or kind), None, detail)
    if kind == "badNonce":
        return AcmeError("nonce", "badNonce", None, detail)
    why = detail or kind or tr("ipcert.acme.no_detail")
    if during:
        return AcmeError("server", tr("ipcert.acme.server_during", during=during, status=int(status), detail=why),
                         None, detail)
    return AcmeError("server", tr("ipcert.acme.server", status=int(status), detail=why), None, detail)


class Client(object):
    def __init__(self, directory_url, crypto, account_key, transport=http_request, sleep=time.sleep,
                 clock=time.monotonic, poll_timeout=POLL_TIMEOUT):
        self.directory_url = directory_url
        self.crypto = crypto
        self.account_key = account_key
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self.poll_timeout = poll_timeout
        self._dir = None
        self._nonces = []
        self._jwk = None
        self.kid = None

    # ── plumbing ──
    def _request(self, method, url, body=None, headers=None):
        status, hdrs, data = self.transport(method, url, body, headers or {}, TIMEOUT)
        n = hdrs.get("replay-nonce")
        if n:
            self._nonces.append(n)
        return status, hdrs, data

    def directory(self):
        if self._dir is None:
            status, hdrs, data = self._request("GET", self.directory_url)
            if status != 200:
                raise problem_error(status, hdrs, data, tr("ipcert.during.directory"))
            try:
                doc = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise AcmeError("server", tr("ipcert.acme.dir_not_json"))
            if not isinstance(doc, dict) or not doc.get("newOrder"):
                raise AcmeError("server", tr("ipcert.acme.dir_no_neworder"))
            self._dir = doc
        return self._dir

    def profiles(self):
        meta = self.directory().get("meta") or {}
        prof = meta.get("profiles") if isinstance(meta, dict) else None
        return dict(prof) if isinstance(prof, dict) else {}

    def _nonce(self):
        if self._nonces:
            return self._nonces.pop()
        status, hdrs, data = self._request("HEAD", self.directory()["newNonce"])
        if self._nonces:
            return self._nonces.pop()
        raise problem_error(status, hdrs, data, "nonce")

    def jwk(self):
        if self._jwk is None:
            self._jwk = kcrypto.rsa_jwk(self.crypto.public_der(self.account_key))
        return self._jwk

    def thumbprint(self):
        return kcrypto.thumbprint(self.jwk())

    def _jws(self, url, payload, use_jwk=False):
        protected = {"alg": "RS256", "nonce": self._nonce(), "url": url}
        if use_jwk or not self.kid:
            protected["jwk"] = self.jwk()
        else:
            protected["kid"] = self.kid
        p64 = kcrypto.b64u(json.dumps(protected, sort_keys=True, separators=(",", ":")))
        b64 = "" if payload is None else kcrypto.b64u(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        sig = self.crypto.sign(self.account_key, data="%s.%s" % (p64, b64))
        return json.dumps({"protected": p64, "payload": b64, "signature": kcrypto.b64u(sig)}).encode("ascii")

    def post(self, url, payload, use_jwk=False, during="", ok=(200, 201)):
        """A signed POST (``payload`` None — POST-as-GET). -> (status, headers, body); AcmeError otherwise."""
        for attempt in (1, 2):
            body = self._jws(url, payload, use_jwk)
            status, hdrs, data = self._request("POST", url, body, {"Content-Type": "application/jose+json"})
            if status in ok:
                return status, hdrs, data
            err = problem_error(status, hdrs, data, during)
            if err.reason == "nonce" and attempt == 1:
                continue
            if err.reason == "nonce":
                err = AcmeError("server", tr("ipcert.acme.bad_nonce"))
            raise err
        raise AcmeError("server", tr("ipcert.acme.no_answer"))      # pragma: no cover

    @staticmethod
    def _json(data):
        try:
            doc = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise AcmeError("server", tr("ipcert.acme.not_json"))
        if not isinstance(doc, dict):
            raise AcmeError("server", tr("ipcert.acme.unclear"))
        return doc

    # ── the protocol ──
    def register(self, email=""):
        payload = {"termsOfServiceAgreed": True}
        if email:
            payload["contact"] = ["mailto:" + email]
        status, hdrs, data = self.post(self.directory()["newAccount"], payload, use_jwk=True,
                                       during=tr("ipcert.during.account"))
        self.kid = hdrs.get("location")
        if not self.kid:
            raise AcmeError("server", tr("ipcert.acme.no_account_url"))
        return self.kid

    def new_order(self, ip, profile=PROFILE):
        payload = {"identifiers": [{"type": "ip", "value": str(ip)}]}
        if profile:
            payload["profile"] = profile
        status, hdrs, data = self.post(self.directory()["newOrder"], payload, during=tr("ipcert.during.order"),
                                       ok=(201, 200))
        url = hdrs.get("location")
        if not url:
            raise AcmeError("server", tr("ipcert.acme.no_order_url"))
        return url, self._json(data)

    def http01(self, authz_url):
        """(challenge, token, key authorization) of the http-01 challenge; None when the authorization is valid
        already (Let's Encrypt keeps it for a while after a check)."""
        _, _, data = self.post(authz_url, None, during=tr("ipcert.during.authz"))
        authz = self._json(data)
        if authz.get("status") == "valid":
            return None
        for ch in authz.get("challenges") or []:
            if isinstance(ch, dict) and ch.get("type") == "http-01":
                token = str(ch.get("token") or "")
                if not re.match(r"^[A-Za-z0-9_-]{16,256}$", token):
                    raise AcmeError("server", tr("ipcert.acme.bad_token"))
                return ch, token, "%s.%s" % (token, self.thumbprint())
        raise AcmeError("unsupported", tr("ipcert.acme.no_http01"))

    def answer(self, challenge_url):
        self.post(challenge_url, {}, during=tr("ipcert.during.answer"))

    def poll(self, url, want=("valid",), during=None):
        """POST-as-GET ``url`` until its status is one of ``want``; "invalid" — the error of its challenge."""
        if during is None:
            during = tr("ipcert.during.wait")
        until = self.clock() + self.poll_timeout
        while True:
            _, hdrs, data = self.post(url, None, during=during)
            doc = self._json(data)
            st = doc.get("status")
            if st in want:
                return doc
            if st == "invalid":
                err = None
                for ch in doc.get("challenges") or []:
                    if isinstance(ch, dict) and isinstance(ch.get("error"), dict):
                        err = ch["error"]
                err = err or doc.get("error")
                body = json.dumps(err).encode("utf-8") if isinstance(err, dict) else b""
                raise problem_error(400, hdrs, body, during)
            if self.clock() >= until:
                raise AcmeError("challenge", tr("ipcert.acme.poll_timeout", sec=int(self.poll_timeout)))
            wait = hdrs.get("retry-after")
            self.sleep(min(10.0, float(wait)) if wait and wait.isdigit() else POLL_EVERY)

    def finalize(self, order, csr_der):
        _, _, data = self.post(order["finalize"], {"csr": kcrypto.b64u(csr_der)}, during=tr("ipcert.during.finalize"))
        return self._json(data)

    def certificate(self, url):
        _, _, data = self.post(url, None, during=tr("ipcert.during.download"))
        text = data.decode("ascii", "replace")
        if "-----BEGIN CERTIFICATE-----" not in text:
            raise AcmeError("server", tr("ipcert.acme.not_cert"))
        return text


def epoch(iso):
    try:
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError, OverflowError):
        return None
