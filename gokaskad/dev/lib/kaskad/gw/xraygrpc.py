"""A user added to a running xray through gRPC ``HandlerService/AlterInbound`` — for the inbounds whose users the CLI
``xray api adu`` does not know (Hysteria2 in 26.3.27: T0 С0.13; SPEC-stage1-exits §2.4, finding С5; T5).

    add_user(api, tag, email, account) -> bool

``account`` — (type, bytes) of the adapter (``xray_account``): the full name of the protobuf message of the account and
its encoded bytes, e.g. ("xray.proxy.hysteria.account.Account", field 1 = auth). The message is built by hand (as
``grpc_hy.py`` of T0 did — Python has no HTTP/2 in its stdlib, and no protobuf):

    AlterInboundRequest{tag = 1, operation = 2 TypedMessage{type = 1 "xray.app.proxyman.command.AddUserOperation",
        value = 2 AddUserOperation{user = 1 User{email = 2, account = 3 TypedMessage{type = 1, value = 2}}}}}

framed for gRPC (a byte 0 and the length in 4 bytes, big endian) and sent by the system ``curl`` over HTTP/2 without
TLS (``--http2-prior-knowledge``): the body goes through stdin — the secret of the account never reaches argv or the
journal. Success — the line ``grpc-status: 0`` in curl's verbose stderr. No curl, a curl without HTTP2, a timeout,
another status — False: the caller restarts xray (``hot_apply`` -> ``restart``), as on any failed batch.
"""

import os
import re
import shutil
import struct
import subprocess

SERVICE = "xray.app.proxyman.command.HandlerService/AlterInbound"
ADD_USER = "xray.app.proxyman.command.AddUserOperation"
TIMEOUT = 8                         # s — as every call of ``xray api`` (-timeout 8, §2.5)
_STATUS = re.compile(r"grpc-status:\s*(\d+)", re.I)
_curl_ok = {}                       # path -> bool: ``curl -V`` lists HTTP2 (once per process)


def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def field(num, data):
    """A length-delimited field (string, bytes, a message)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return varint(num << 3 | 2) + varint(len(data)) + data


def typed(type_name, value):
    """xray.common.serial.TypedMessage{type = 1, value = 2}."""
    return field(1, type_name) + field(2, value)


def add_user_message(tag, email, account):
    """The bytes of AlterInboundRequest for one new user (``account`` — (type, bytes))."""
    a_type, a_value = account
    user = field(2, email) + field(3, typed(a_type, a_value))
    return field(1, tag) + field(2, typed(ADD_USER, field(1, user)))


def frame(msg):
    """One gRPC message: not compressed (0) + the length (4 bytes, big endian)."""
    return b"\x00" + struct.pack(">I", len(msg)) + msg


def _curl(curl=None):
    return curl or shutil.which("curl") or next((p for p in ("/usr/bin/curl", "/bin/curl") if os.access(p, os.X_OK)),
                                                None)


def http2(curl):
    """``curl -V`` lists the feature HTTP2 (nghttp2): Debian 12/13 and Ubuntu 22.04/24.04 have it."""
    if curl not in _curl_ok:
        try:
            p = subprocess.run([curl, "-V"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            feats = [ln for ln in p.stdout.decode("utf-8", "replace").splitlines() if ln.lower().startswith("features")]
            _curl_ok[curl] = p.returncode == 0 and any(re.search(r"\bHTTP2\b", ln) for ln in feats)
        except (OSError, subprocess.SubprocessError):
            _curl_ok[curl] = False
    return _curl_ok[curl]


def _env():
    """The environment of the call without the proxies of curl (http_proxy, HTTPS_PROXY, ALL_PROXY, NO_PROXY…) and
    without CURL_HOME (another curlrc)."""
    return dict((k, v) for k, v in os.environ.items()
                if not k.lower().endswith("_proxy") and k.upper() != "CURL_HOME")


def call(api, msg, curl=None, timeout=TIMEOUT):
    """POST one framed message to ``http://<api>/<SERVICE>``. -> (ok, why) — ``why`` for the journal (never the body)."""
    curl = _curl(curl)
    if not curl:
        return False, "no curl"
    if not http2(curl):
        return False, "curl without HTTP2"
    # SEC-s1-t45 З-4: -q (first) — no ~/.curlrc; --noproxy '*' and an environment without *_proxy — the body (the auth
    # of a user) never goes to a proxy of the shell of root
    argv = [curl, "-q", "--noproxy", "*", "-sS", "-v", "--http2-prior-knowledge", "-m", str(int(timeout)),
            "-H", "content-type: application/grpc", "-H", "te: trailers",
            "--data-binary", "@-", "-o", "/dev/null", "http://%s/%s" % (api, SERVICE)]
    try:
        p = subprocess.run(argv, input=frame(msg), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout + 2, env=_env())
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as e:
        return False, str(e)[:120]
    err = p.stderr.decode("utf-8", "replace")
    codes = _STATUS.findall(err)
    if p.returncode != 0:
        return False, "curl rc %d" % p.returncode
    if not codes:
        return False, "no grpc-status"
    if codes[-1] != "0":
        return False, "grpc-status %s" % codes[-1]
    return True, ""


def add_user(api, tag, email, account, curl=None, timeout=TIMEOUT):
    """Add one user to the inbound ``tag`` of the xray whose API listens at ``api`` ("127.0.0.1:10854"). -> bool."""
    return call(api, add_user_message(tag, email, account), curl, timeout)[0]
