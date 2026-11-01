"""Backups taken before a migration or a rollback (SPEC §4.7).

``/var/backups/kaskad/<UTC id>-<edition>-<version>/`` — directory 0700, files 0600::

    iptables-save.txt  ip6tables-save.txt  nft-ruleset.txt  ip-rule.txt  ip-route-all.txt
    ss.txt  ufw-status.txt  systemctl.txt
    etc-kaskad.tar.gz            /etc/kaskad without the egress binaries (egress/xray, egress/awg/*)
    files/usr/local/bin/gokaskad (+ gokaskad.bak), files/etc/iptables/rules.v4 or
    files/etc/sysconfig/iptables, files/etc/systemd/system/kaskad-*, files/etc/sysctl.conf,
    files/etc/default/ufw
    scan.json  plan.json  manifest.json   (manifest: id, time, versions, sha256 of every file)

The snapshot holds secrets (bot token, GitHub key, egress keys): nothing of its contents is ever
printed or logged — only paths. ``prune`` keeps the newest five plus the newest snapshot taken
from a free/PRO 2.x installation (the way back to 2.x needs its program copy).
"""

import hashlib
import io
import json
import os
import re
import shutil
import tarfile

from .. import __version__, paths, util
from ..errors import KaskadError
from ..i18n import tr
from . import scan as scan_mod

KEEP = 5
MANIFEST = "manifest.json"
KIND = "kaskad-snapshot"
_NAME_RE = re.compile(r"^\d{8}T\d{6}Z-")


def _safe(s):
    return re.sub(r"[^0-9A-Za-z.\-]+", "", str(s or "")) or "unknown"


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, data):
    """Write 0600; missing parent directories are created 0700 one by one (os.makedirs would
    give the intermediate ones the umask default)."""
    d = os.path.dirname(path)
    missing = []
    while d and not os.path.isdir(d):
        missing.append(d)
        d = os.path.dirname(d)
    for m in reversed(missing):
        os.mkdir(m, 0o700)
        os.chmod(m, 0o700)
    util.atomic_write(path, data, 0o600)


def _commands(runner, inv):
    """(file name, argv, required) of command outputs to keep."""
    out = []
    if runner.which("iptables-save") or inv.backend.get("backend") not in ("none", None):
        out.append(("iptables-save.txt", ["iptables-save"], True))
    if runner.which("ip6tables-save"):
        out.append(("ip6tables-save.txt", ["ip6tables-save"], False))
    if runner.which("nft"):
        out.append(("nft-ruleset.txt", ["nft", "list", "ruleset"], False))
    if runner.which("ip"):
        out.append(("ip-rule.txt", ["ip", "rule", "show"], False))
        out.append(("ip-route-all.txt", ["ip", "route", "show", "table", "all"], False))
    if runner.which("ss"):
        out.append(("ss.txt", ["ss", "-Hltnup"], False))
    if runner.which("ufw"):
        out.append(("ufw-status.txt", ["ufw", "status", "verbose"], False))
    return out


_SKIP_DIRS = ("kaskad/egress/awg", "kaskad/egress/.x")     # AmneziaWG binaries, xray download staging
_SKIP_FILES = ("kaskad/egress/xray",)


def _etc_tar():
    """/etc/kaskad as tar.gz bytes, without the egress binaries."""
    src = paths.etc()
    buf = io.BytesIO()
    if not os.path.isdir(src):
        return None
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for d, dirs, files in os.walk(src):
            dirs.sort()
            rel_d = os.path.relpath(d, os.path.dirname(src)).replace(os.sep, "/")
            if rel_d in _SKIP_DIRS:
                dirs[:] = []
                continue
            tar.add(d, arcname=rel_d, recursive=False)
            for n in sorted(files):
                rel = rel_d + "/" + n
                if rel in _SKIP_FILES:
                    continue
                full = os.path.join(d, n)
                if os.path.islink(full) or os.path.isfile(full):
                    tar.add(full, arcname=rel, recursive=False)
    return buf.getvalue()


def _copies():
    """[(production path, local path)] of single files to copy."""
    out = [paths.command(), scan_mod.command_bak(), paths.rules_v4(), paths.rules_sysconfig(),
           paths.sysctl_conf(), paths.ufw_default()]
    sd = paths.systemd_dir()
    try:
        names = sorted(n for n in os.listdir(sd) if n.startswith("kaskad-"))
    except OSError:
        names = []
    out += [os.path.join(sd, n) for n in names]
    return [p for p in out if os.path.isfile(p)]


def _services_text(services):
    lines = ["%-28s enabled=%-10s active=%s" % (u, s.get("enabled", ""), s.get("active", ""))
             for u, s in sorted((services or {}).items())]
    return "\n".join(lines) + "\n"


def create(runner, inv, plan=None, reason="migrate"):
    """Take a snapshot; returns its directory. Raises KaskadError if a required part (the
    iptables rules) cannot be saved — the caller must then change nothing."""
    base = paths.backups()
    util.ensure_dir(base, 0o700)
    if reason == "rollback":
        edition, version = "k3", __version__
    else:
        edition, version = inv.edition, inv.version
    first = "%s-%s-%s" % (util.stamp(), _safe(edition), _safe(version))
    name, n = first, 2
    path = os.path.join(base, name)
    while os.path.exists(path):             # same second: suffix keeps names in creation order
        name = "%s-%d" % (first, n)
        path = os.path.join(base, name)
        n += 1
    os.makedirs(path, 0o700)
    os.chmod(path, 0o700)
    manifest = {"kind": KIND, "id": name, "created": util.now_iso(), "reason": reason,
                "from": {"edition": edition if reason != "rollback" else "k3",
                         "version": version, "engine": inv.engine,
                         "installed": {"edition": inv.edition, "version": inv.version}},
                "to": "legacy" if reason == "rollback" else __version__,
                "files": {}, "errors": {}}

    def put(rel, data, **meta):
        data = data.encode("utf-8", "surrogateescape") if isinstance(data, str) else data
        _write(os.path.join(path, rel), data)
        entry = {"sha256": _sha256(data), "size": len(data)}
        entry.update(meta)
        manifest["files"][rel] = entry

    try:
        for fname, argv, required in _commands(runner, inv):
            r = runner.run(argv, timeout=60)
            if r.ok:
                put(fname, r.out, source=" ".join(argv))
            elif required:
                raise KaskadError(tr("migrate.snap.cmd_failed", cmd=argv[0],
                                     error=util.redact((r.err or r.out).strip()[:200])))
            else:
                manifest["errors"][fname] = util.redact((r.err or r.out).strip()[:200])
        put("systemctl.txt", _services_text(inv.services), source="systemctl is-enabled/is-active")
        tar = _etc_tar()
        if tar is not None:
            put("etc-kaskad.tar.gz", tar, source=scan_mod.display(paths.etc()))
        for src in _copies():
            with open(src, "rb") as f:
                data = f.read()
            prod = scan_mod.display(src)
            meta = {"source": prod, "mode": "%04o" % (os.stat(src).st_mode & 0o7777)}
            if src in (paths.command(), scan_mod.command_bak()):
                info = scan_mod.command_info(src)
                meta.update(kind=info.get("kind"), version=info.get("version"))
            put("files" + prod, data, **meta)
        put("scan.json", util.json_text(inv.report()))
        if plan is not None:
            put("plan.json", util.json_text(plan.to_dict()))
        _write(os.path.join(path, MANIFEST), util.json_text(manifest))
    except KaskadError:
        shutil.rmtree(path, ignore_errors=True)
        raise
    except (OSError, IOError) as e:
        shutil.rmtree(path, ignore_errors=True)
        raise KaskadError(tr("migrate.snap.failed", error=e))
    prune(keep_path=path)
    return path


def manifest(path):
    text = util.read_text(os.path.join(path, MANIFEST))
    if text is None:
        return None
    try:
        m = json.loads(text)
    except ValueError:
        return None
    return m if isinstance(m, dict) and m.get("kind") == KIND else None


def read_file(path, rel):
    """Bytes of a snapshot file, checked against the manifest sha256."""
    m = manifest(path)
    if m is None or rel not in m.get("files", {}):
        raise KaskadError(tr("migrate.snap.no_file", path=scan_mod.display(path), file=rel))
    try:
        with open(os.path.join(path, rel), "rb") as f:
            data = f.read()
    except (OSError, IOError) as e:
        raise KaskadError(tr("migrate.snap.unreadable", file=rel, error=e))
    if _sha256(data) != m["files"][rel]["sha256"]:
        raise KaskadError(tr("migrate.snap.damaged", file=rel))
    return data


def read_text(path, rel):
    return read_file(path, rel).decode("utf-8", "surrogateescape")


def snapshots():
    """[(path, manifest)] of our snapshots, oldest first."""
    base = paths.backups()
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(base, n)
        if not _NAME_RE.match(n) or not os.path.isdir(p):
            continue
        m = manifest(p)
        if m is not None:
            out.append((p, m))
    return out


def prune(keep=KEEP, keep_path=None):
    """Delete old snapshots: keep the newest ``keep`` and the newest one taken from a free / PRO
    2.x installation. Only directories with our manifest are ever deleted."""
    snaps = snapshots()
    keep_set = set(p for p, _m in snaps[-keep:])
    if keep_path:
        keep_set.add(keep_path)
    for p, m in reversed(snaps):
        if (m.get("from") or {}).get("edition") in ("pro", "free"):
            keep_set.add(p)
            break
    removed = []
    for p, _m in snaps:
        if p not in keep_set:
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p)
    return removed


def listing():
    """Snapshots for ``migrate backups``, newest first (no contents, only metadata)."""
    out = []
    for p, m in reversed(snapshots()):
        files = m.get("files", {})
        cmd = files.get("files/usr/local/bin/gokaskad", {})
        out.append({"id": m.get("id"), "path": scan_mod.display(p), "created": m.get("created"),
                    "reason": m.get("reason"), "from": m.get("from"), "to": m.get("to"),
                    "files": len(files), "size": sum(int(f.get("size", 0)) for f in files.values()),
                    "command_version": cmd.get("version", ""), "command_kind": cmd.get("kind", "")})
    return out
