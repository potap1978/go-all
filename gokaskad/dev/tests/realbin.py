"""Real validators of the configs Kaskad writes (T-M6-qs330b №1: ``::ffff:0:0/96`` passed 2270 tests and the fake
``xray -test``, the real xray refused the whole config). Offline: a binary already on this machine — never a download
here (``tools/fetch_test_bins.sh`` puts xray into the cache once, ``tools/k3_test.sh`` calls it).

    xray_path()            the real xray: $KASKAD_TEST_XRAY, else the cache of tools/fetch_test_bins.sh
                           (~/.cache/kaskad-tests/xray/<version>/xray), else ``xray`` on PATH; None — none
    xray_check(cfg)        (ok, text): ``xray run -test`` of a config dict — the certificate files swapped for a
                           real pair (the fixture of the panel), the ``ext:<file>.dat:<code>`` / ``geoip:`` /
                           ``geosite:`` lists made up with exactly the codes the config names; None — no xray
    nginx_path()           ``nginx`` on PATH or $KASKAD_TEST_NGINX; None — none
    nginx_check(vhost, *more)  (ok, text): ``nginx -t`` of our files inside a minimal nginx.conf of a temporary
                           folder — every path nginx writes (temp paths, pid, lock, error log) inside it; None — none

The fake xray of the tests (tests/fakes_gw.FakeXray) asks ``xray_check`` after its own checks: every config any test
writes goes through the real xray when it is there. ``KASKAD_TEST_NO_REAL=1`` turns that off (the dedicated module
tests/test_real_validators.py still runs)."""

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
XRAY_VERSION = "v26.3.27"        # = EGRESS_XVER of install.sh: the pinned xray every node runs (3.3.4)
CERT = os.path.join(HERE, "fixtures", "panel", "node.crt")
KEY = os.path.join(HERE, "fixtures", "panel", "node.key")
_EXT = re.compile(r"^ext:([^:]+):(.+)$")


def cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "kaskad-tests")


def xray_path():
    for p in (os.environ.get("KASKAD_TEST_XRAY") or "",
              os.path.join(cache_dir(), "xray", XRAY_VERSION, "xray")):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("xray")


def in_fakes():
    """The fake xray of the tests calls the real one too (not with KASKAD_TEST_NO_REAL=1)."""
    return os.environ.get("KASKAD_TEST_NO_REAL") != "1" and xray_path() is not None


_cache = {}


def _lists(cfg):
    """{file: {"ip"|"site": set(codes)}} of every list the routing names (``ext:f:c``, ``geoip:c``, ``geosite:c``)."""
    out = {}

    def add(fname, kind, code):
        out.setdefault(fname, {}).setdefault(kind, set()).add(code.split("@", 1)[0].upper())

    for r in ((cfg.get("routing") or {}).get("rules") or []):
        for kind, field in (("ip", "ip"), ("site", "domain")):
            for v in r.get(field) or []:
                v = str(v).lstrip("!")
                m = _EXT.match(v)
                if m:
                    add(m.group(1), kind, m.group(2))
                elif v.startswith("geoip:"):
                    add("geoip.dat", "ip", v[6:])
                elif v.startswith("geosite:"):
                    add("geosite.dat", "site", v[8:])
    for s in ((cfg.get("dns") or {}).get("servers") or []):
        for v in (s.get("domains") or []) if isinstance(s, dict) else []:
            v = str(v)
            m = _EXT.match(v)
            if m:
                add(m.group(1), "site", m.group(2))
            elif v.startswith("geosite:"):
                add("geosite.dat", "site", v[8:])
    return out


def _write_lists(folder, lists):
    from tests.rules_helpers import geoip_dat, geosite_dat
    for fname, kinds in lists.items():
        with open(os.path.join(folder, fname), "wb") as f:
            if "site" in kinds:
                f.write(geosite_dat(dict((c, ["example-%s.test" % c.lower()]) for c in sorted(kinds["site"]))))
            else:
                f.write(geoip_dat([(c, ["192.0.2.0/24"]) for c in sorted(kinds["ip"])]))


def _real_files(cfg):
    """A copy whose TLS certificates are a real pair (the files of the node do not exist in a test)."""
    c = copy.deepcopy(cfg)
    for ib in c.get("inbounds") or []:
        tls = ((ib or {}).get("streamSettings") or {}).get("tlsSettings") or {}
        for cert in tls.get("certificates") or []:
            if "certificateFile" in cert:
                cert["certificateFile"] = CERT
            if "keyFile" in cert:
                cert["keyFile"] = KEY
    return c


def xray_check(cfg):
    """(ok, output) of the real ``xray run -test`` on ``cfg``; None when there is no xray."""
    xray = xray_path()
    if xray is None:
        return None
    c = _real_files(cfg)
    key = json.dumps(c, sort_keys=True)
    if key in _cache:
        return _cache[key]
    folder = tempfile.mkdtemp(prefix="kaskad-xray-test-")
    try:
        _write_lists(folder, _lists(c))
        path = os.path.join(folder, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
        env = dict(os.environ, XRAY_LOCATION_ASSET=folder, LANG="C", LC_ALL="C")
        p = subprocess.run([xray, "run", "-test", "-c", path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=env, timeout=60, cwd=folder)
        text = p.stdout.decode("utf-8", "replace")
        got = (p.returncode == 0 and "Configuration OK" in text, text)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    _cache[key] = got
    return got


def nginx_path():
    p = os.environ.get("KASKAD_TEST_NGINX") or ""
    if p and os.path.isfile(p) and os.access(p, os.X_OK):
        return p
    return shutil.which("nginx")


# nginx -t of root makes its temp paths and chowns them to the user of the config (nobody by default): without
# these lines -p keeps the paths built in (/var/lib/nginx/body…) and the nginx of the server could no longer write its
# temp files — big answers cut (stand T-3310b). The module of a path built without — its directive is unknown.
_NGINX_TEMPS = (("client_body_temp_path", "body", None), ("proxy_temp_path", "proxy", "proxy"),
                ("fastcgi_temp_path", "fastcgi", "fastcgi"), ("uwsgi_temp_path", "uwsgi", "uwsgi"),
                ("scgi_temp_path", "scgi", "scgi"))


def _nginx_build(nginx):
    """((major, minor, patch) or None, configure arguments) of ``nginx -V``."""
    try:
        p = subprocess.run([nginx, "-V"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        text = p.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None, ""
    got = re.search(r"nginx/(\d+)\.(\d+)\.(\d+)", text)
    return (tuple(int(x) for x in got.groups()) if got else None), text


def nginx_sandbox(folder, includes, build=(None, "")):
    """The nginx.conf of a check in ``folder``: pid, lock, error log and the temp paths there, ``includes`` in http."""
    version, configure = build
    tmp = os.path.join(folder, "tmp")
    os.makedirs(tmp, exist_ok=True)
    os.makedirs(os.path.join(folder, "logs"), exist_ok=True)
    temps = ["  %s %s;" % (name, os.path.join(tmp, sub)) for name, sub, module in _NGINX_TEMPS
             if not module or ("--without-http_%s_module" % module) not in configure]
    return ("pid %s/nginx.pid;\nlock_file %s/nginx.lock;\nerror_log %s/logs/error.log;\nevents {}\nhttp {\n"
            "  access_log off;\n%s\n%s\n}\n" % (folder, folder, folder, "\n".join(temps),
                                                "\n".join("  include %s;" % f for f in includes)))


def nginx_check(vhost, *more):
    """(ok, output) of ``nginx -t`` with ``vhost`` (and ``more``: server blocks of ours) in a minimal nginx.conf of a
    temporary prefix: the certificate paths swapped for the real fixture pair, unix sockets and roots left as they
    are. Nothing outside the folder changes (nginx_sandbox; the early error log — ``-e``, nginx ≥ 1.19.5).
    None — no nginx."""
    nginx = nginx_path()
    if nginx is None:
        return None
    build = _nginx_build(nginx)
    folder = tempfile.mkdtemp(prefix="kaskad-nginx-test-")
    try:
        includes = []
        for i, one in enumerate((vhost,) + more):
            text = re.sub(r"(?m)^(\s*ssl_certificate)\s+\S+;", r"\1 %s;" % CERT, one)
            text = re.sub(r"(?m)^(\s*ssl_certificate_key)\s+\S+;", r"\1 %s;" % KEY, text)
            includes.append(os.path.join(folder, "vhost.conf" if not i else "vhost%d.conf" % i))
            with open(includes[-1], "w", encoding="utf-8") as f:
                f.write(text)
        conf = os.path.join(folder, "nginx.conf")
        with open(conf, "w", encoding="utf-8") as f:
            f.write(nginx_sandbox(folder, includes, build))
        early = ["-e", os.path.join(folder, "logs", "error.log")] if build[0] and build[0] >= (1, 19, 5) else []
        p = subprocess.run([nginx, "-t", "-p", folder, "-c", conf] + early,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        return p.returncode == 0, p.stdout.decode("utf-8", "replace")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
