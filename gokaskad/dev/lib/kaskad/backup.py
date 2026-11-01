"""A copy of the settings in one file: export, import with a preview and a rollback (docs/k3/SPEC-subs-backup.md §2).

ONE format with the snapshots of a migration (/var/backups/kaskad, kaskad.migrate.snapshot): a manifest of kind
``kaskad-snapshot`` (id, created, reason, from, files with sha256 and size) and the files under ``files/<the production
path>``. A snapshot is that on disk (a directory 0700, files 0600); an export is that in one JSON, encrypted with a
password; the copy taken before an import is a snapshot of reason ``backup-import`` (``migrate backups`` lists it,
``backup undo`` brings it back). ``gw awg export`` stays what it was: the people and AmneziaWG of one node merged into
another one, not a copy of the node.

    parts       gateway   people, devices, protocols with their keys and ports, routes, rules, names of the exits —
                          the ``gateway`` of state.json and secrets.json (the certificate of the node goes when its
                          address changes — the new node makes its own); stage 1 (T7): with it the exit policy and
                          ``gateway.notify`` (in ``gateway``), the zone of the node (KASKAD_TZ of /etc/kaskad/config)
                          and the counters of the quotas (/var/lib/kaskad/gw/traffic.json); a ``gateway`` of a copy
                          without a policy comes as «Как раньше» (compat, SPEC-stage1-exits §1.3)
                exits     the pool «Прокси для API Telegram» (upstream.txt) and its subscriptions (subs.json)
                forwards  the forwards (``forwards`` of state.json), the names of the servers (aliases), monitoring
                site      the domain, the mail of Let's Encrypt, the template of the site — no certificate (issued anew
                          once the domain points to the new server); ``v`` 2 (SITE_V) — and ``use``: SSTP and
                          OpenConnect on the certificate of the domain (back with ``backup undo`` on this server)
                bot       the token and the chats of the Telegram bot (/etc/kaskad/config)
                mcp       the SSH keys of the AI agents (our lines of /root/.ssh/authorized_keys)
                panel     its settings: password asked or not, the path on the domain, the look — never the password
                github    the GitHub key (updates, the core of AmneziaWG) and the branch of the updates

The file (text, any mail or messenger carries it)::

    KASKAD-BACKUP 1
    {"format": "kaskad-backup", "v": 1, "cipher": "aes-256-cbc", "kdf": "pbkdf2-sha256", "iter": 600000,
     "salt": "<b64>", "check": "<hex16>", "created": "…", "version": "3.2.23", "parts": [...]}
    <base64 of the ciphertext, 76 per line>
    MAC <hex64>

Keys: PBKDF2-HMAC-SHA256(password, salt 16 random bytes, 600 000) -> 64 bytes: the first 32 encrypt (``openssl enc
-aes-256-cbc``, the key handed over in the environment of openssl — never in argv; ``-pbkdf2 -iter 1 -nosalt``: the
key is already strong and unique for the file), the last 32 sign: HMAC-SHA256 over the header line and the
ciphertext (encrypt-then-MAC, checked before anything is decrypted). ``check`` — HMAC of a fixed text: a wrong
password is told from a damaged file. The password comes from stdin or KASKAD_BACKUP_PASSWORD, never argv.

Import: the file is read and checked, ``preview`` says what will come and what it replaces (and whether the devices
need new settings: the address in their settings is the old server's IP — or a domain that will do once its A record
points here); ``apply`` takes a snapshot first, then the parts one by one; a failure brings the snapshot back.
Nothing of the file is ever printed or logged — only counts and names.
"""

import base64
import copy
import hashlib
import hmac
import json
import os
import re
import shutil
from collections.abc import Mapping

from . import __version__, paths, secrets, state, util
from .errors import CONFLICT, USAGE, ConflictError, KaskadError, UnsupportedError
from .i18n import tr, trn

FORMAT = "kaskad-backup"
VERSION = 1
MAGIC = "KASKAD-BACKUP 1"
ITER = 600000
SALT_LEN = 16
MIN_PASSWORD = 8
FILE_MAX = 16 * 1024 * 1024
KIND = "kaskad-snapshot"                         # the manifest of kaskad.migrate.snapshot
PASS_ENV = "KASKAD_BACKUP_PASSWORD"
KEY_ENV = "KASKAD_BACKUP_KEY"                    # the key of openssl (its own environment only)
PARTS = ("gateway", "exits", "forwards", "site", "bot", "mcp", "panel", "github")
ORDER = ("github", "bot", "panel", "mcp", "forwards", "exits", "gateway", "site")


class _Words(Mapping):
    """part -> its words in the language of the moment (tr at every lookup, never at import)."""

    def __init__(self, prefix):
        self._prefix = prefix

    def __getitem__(self, part):
        if part not in PARTS:
            raise KeyError(part)
        return tr(self._prefix + part)

    def __iter__(self):
        return iter(PARTS)

    def __len__(self):
        return len(PARTS)


TITLES = _Words("backup.title.")                 # the long name of a part (the panel, the preview, «Переношу: …»)
SHORT = _Words("backup.short.")                  # the short one (lists, the journal)
BOT_KEYS = ("BOT_TOKEN", "BOT_CHAT_ID", "BOT_ADMINS", "MENU_STYLE")
GITHUB_KEYS = ("GITHUB_PAT", "KASKAD_BRANCH")
GATEWAY_KEYS = ("KASKAD_TZ",)                    # stage 1 (T7): the zone of the node goes with the gateway
NODE_GW_KEYS = ("enabled", "enabled_at", "awg_engine", "mtu")      # of this machine, not of the settings
NODE_SECRETS = ("tls.server.key", "tls.server.crt")                  # the certificate of the node's own address
SITE_KEYS = ("domain", "acme_email", "staging", "template_id", "template_source")
# The format of the part ``site`` (Н-2, остаток, R-n2.md): 2 — with ``use`` ({"sstp", "ocserv"}: on the certificate of
# the domain); a part without ``v`` is 1 (a copy or a snapshot of an older Kaskad) — its ``use`` is unknown and nothing
# is done with SSTP/OpenConnect. ``VERSION`` of the payload stays 1: an older Kaskad reads the keys it knows of a part
# and leaves ``v``/``use`` aside (an export to it, ``backup undo`` after a rollback of Kaskad) — a payload ``v`` 2 it
# would refuse whole
SITE_V = 2

F_STATE = "files/etc/kaskad/state.json"
F_SECRETS = "files/etc/kaskad/secrets.json"
F_POOL = "files/etc/kaskad/egress/upstream.txt"
F_POOL_RU = "files/etc/kaskad/egress/upstream-ru.txt"   # T15 Q4: the keys of the Russian groups (home routers), the same part
F_SUBS = "files/etc/kaskad/egress/subs.json"
F_TOFU = "files/etc/kaskad/egress/tofu-pins.json"   # 3.3.4: the pins taken on the update — «servers» only, no «pending»
F_ALIASES = "files/etc/kaskad/aliases"
F_MONITORS = "files/etc/kaskad/monitors/"
F_CONFIG = "files/etc/kaskad/config"
F_PANEL = "files/etc/kaskad/panel/config.json"
F_APPEARANCE = "files/etc/kaskad/panel/appearance.json"
F_LAYOUT = "files/etc/kaskad/panel/layout.json"
F_KEYS = "files/root/.ssh/authorized_keys"
F_TRAFFIC = "files/var/lib/kaskad/gw/traffic.json"   # stage 1 (T7): the counters of the quotas (gw.pins, §3.5)
_MONITOR_NAME = re.compile(r"^[0-9A-Za-z._:-]{1,64}\.conf$")
_TOFU_SERVER = re.compile(r"^[0-9A-Za-z.:\[\]_-]{1,300}\|[0-9A-Za-z.*:\[\]_-]{1,300}$")   # host:port|sni (egress_parse)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TOFU_NAME = re.compile(r"^[A-Za-z0-9*]([A-Za-z0-9*._-]{0,252})$")   # the name a chain checked out for (egress_parse _VCN)
_CFG_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")

# the words of the common refusals: backup.WRONG_PASSWORD etc. stay readable from outside (tests, callers) — each read
# is a tr() in the language of the moment (module __getattr__ below)
_MESSAGES = {"WRONG_PASSWORD": "backup.err.wrong_password", "DAMAGED": "backup.err.damaged",
             "NO_UNDO": "backup.err.no_undo", "NOT_A_COPY": "backup.err.not_a_copy"}


def __getattr__(name):
    if name in _MESSAGES:
        return tr(_MESSAGES[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


# ── small parts ────────────────────────────────────────────────────────────

def _sha(text):
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def _parts(value, default=PARTS):
    """«gateway,exits» | ["gateway"] | None -> a tuple in the order of PARTS. Unknown names — USAGE."""
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        value = [x.strip() for x in value.split(",") if x.strip()]
    bad = [x for x in value if x not in PARTS]
    if bad:
        raise KaskadError(tr("backup.err.unknown_parts", bad=", ".join(bad), known=", ".join(PARTS)), code=USAGE)
    return tuple(p for p in PARTS if p in value)


def _json(text, default=None):
    try:
        return util.json_kept(text) if text else default       # numbers as state.load reads them (REV-33714 С-1)
    except ValueError:
        return default


def _read(path):
    return util.read_text(path)


def config_values(text, keys):
    """KEY → value of ``keys`` in a 2.x config text (the bot's parser: never sourced)."""
    from .bot import config as bcfg
    vals = bcfg.parse(text or "")
    return dict((k, vals[k]) for k in keys if k in vals)


def _quote(v):
    v = str(v)
    for a, b in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"), ("`", "\\`")):
        v = v.replace(a, b)
    return '"%s"' % util.EXTRA_BREAKS.sub(" ", v.replace("\n", " ").replace("\r", " "))    # SEC-3372 П-3


def _config_text(vals):
    return "".join("%s=%s\n" % (k, _quote(v)) for k, v in vals.items())


def _set_config(vals):
    """KEY="value" lines of /etc/kaskad/config replaced the way save_config_val does it (the other lines stay)."""
    p = paths.config()
    with util.config_locked():                 # one writer of the file at a time (util.config_locked)
        lines = util.config_lines(util.read_text(p))
        keep = [l for l in lines if not (_CFG_LINE.match(l) and _CFG_LINE.match(l).group(1) in vals)]
        util.ensure_dir(paths.etc(), 0o700)
        util.atomic_write(p, "\n".join(keep + ["%s=%s" % (k, _quote(v)) for k, v in vals.items()]) + "\n", 0o600)


def _mcp_lines():
    from .mcp import keys
    return [l.strip() for l in (util.read_text(keys._path()) or "").splitlines() if keys._ours(l)]


def _engine(st):
    return st.get("engine") or "legacy"


# ── what a copy holds: from this node ──────────────────────────────────────

def collect(runner, parts=PARTS, st=None):
    """{rel: text} of the parts of this node (the files of the format) and the summary. Nothing is written."""
    st = state.load() if st is None else st
    files = {}
    part_state = {"schema": state.SCHEMA}
    if "gateway" in parts:
        g = copy.deepcopy(st.get("gateway") or {})
        if g:
            # ⚠РФ (rf §3.2, §3.6): the default of a node without a stored one goes by its generation — the copy carries
            # the effective one, so the generation of the node it is imported on does not choose it
            from .gw import model
            rules = g.get("rules") if isinstance(g.get("rules"), dict) else {}
            g["rules"] = dict(rules, default=model.rules_conf(st)["default"])
        part_state["gateway"] = g
        sec = secrets.load()
        files[F_SECRETS] = util.json_text({"schema": secrets.SCHEMA, "values": dict(sec)})
        traffic = _read(paths.traffic_json())
        if traffic and isinstance(_json(traffic), dict):
            files[F_TRAFFIC] = traffic
    if "forwards" in parts:
        part_state["forwards"] = copy.deepcopy(st.get("forwards") or [])
        al = _read(paths.aliases())
        if al and al.strip():
            files[F_ALIASES] = al
        try:
            names = sorted(os.listdir(paths.monitors()))
        except OSError:
            names = []
        for n in names:
            if _MONITOR_NAME.match(n):
                t = _read(os.path.join(paths.monitors(), n))
                if t is not None:
                    files[F_MONITORS + n] = t
    if "site" in parts and isinstance(st.get("site"), dict) and st["site"].get("domain"):
        part_state["site"] = dict((k, st["site"].get(k)) for k in SITE_KEYS + ("mode",) if k in st["site"])
        from .site import model as smodel
        part_state["site"].update(v=SITE_V, use=dict((k, bool(smodel.peek(st)["use"][k])) for k in smodel.USE_KINDS))
    if len(part_state) > 1:
        files[F_STATE] = util.json_text(part_state)
    if "exits" in parts:
        pool = _read(paths.egress_up())
        if pool and pool.strip():
            files[F_POOL] = pool
        ru = _read(paths.egress_up_ru())               # T15 Q4: only when there are Russian keys (an older Kaskad
        if ru and ru.strip():                           # refuses a file it does not know — a copy without them is as before)
            files[F_POOL_RU] = ru
        from .gw import exitsubs
        subs = exitsubs.load()
        if subs["subs"] or subs.get("ids"):
            doc = {"schema": exitsubs.SCHEMA, "next": subs["next"], "subs": subs["subs"]}
            if subs.get("ids"):
                doc["ids"] = subs["ids"]                # 3.3.5: the ids of the exits kept for renamed keys
            files[F_SUBS] = util.json_text(doc)
        tofu = _tofu_export()
        if tofu is not None:
            files[F_TOFU] = tofu
    cfg_keys = (BOT_KEYS if "bot" in parts else ()) + (GITHUB_KEYS if "github" in parts else ()) + \
        (GATEWAY_KEYS if "gateway" in parts else ())
    if cfg_keys:
        vals = dict((k, v) for k, v in config_values(_read(paths.config()), cfg_keys).items() if v)
        if vals:
            files[F_CONFIG] = _config_text(vals)
    if "mcp" in parts:
        lines = _mcp_lines()
        if lines:
            files[F_KEYS] = "\n".join(lines) + "\n"
    if "panel" in parts:
        from .panel import conf
        cfg = conf.load()
        files[F_PANEL] = util.json_text({"schema": 1, "require_password": cfg["require_password"],
                                         "public": {"on": cfg["public"]["on"], "port": cfg["public"]["port"]},
                                         "proxy": {"path": cfg["proxy"]["path"], "on": cfg["proxy"]["on"]}})
        for rel, p in ((F_APPEARANCE, conf.appearance_path()), (F_LAYOUT, conf.layout_path())):
            t = _read(p)
            if t is not None and isinstance(_json(t), dict):
                files[rel] = t
    return files


def summary(files, parts):
    """Counts and names of what ``files`` holds, per part — no secret."""
    st = _json(files.get(F_STATE), {}) or {}
    out = {}
    if "gateway" in parts:
        g = st.get("gateway") or {}
        out["gateway"] = {"present": bool(g), "users": len(g.get("users") or []),
                          "devices": len(g.get("devices") or []),
                          "inbounds": sorted(k for k, v in (g.get("inbounds") or {}).items() if isinstance(v, dict)),
                          "host": _copy_host(g.get("host")), "enabled": bool(g.get("enabled"))}
    if "exits" in parts:
        subs = (_json(files.get(F_SUBS), {}) or {}).get("subs") or []
        out["exits"] = {"keys": len([l for l in (files.get(F_POOL) or "").splitlines() if l.strip()]),
                        "subs": len(subs)}
        ru = len([l for l in (files.get(F_POOL_RU) or "").splitlines() if l.strip()])
        if ru:
            out["exits"]["ru_keys"] = ru
    if "forwards" in parts:
        out["forwards"] = {"forwards": len(st.get("forwards") or []),
                           "names": len([l for l in (files.get(F_ALIASES) or "").splitlines() if "=" in l]),
                           "monitors": len([r for r in files if r.startswith(F_MONITORS)])}
    if "site" in parts:
        s = st.get("site") or {}
        out["site"] = {"domain": str(s.get("domain") or ""), "mode": str(s.get("mode") or ""),
                       "template": str(s.get("template_id") or "")}
    vals = config_values(files.get(F_CONFIG), BOT_KEYS + GITHUB_KEYS)
    if "bot" in parts:
        out["bot"] = {"token": bool(vals.get("BOT_TOKEN")), "chat": bool(vals.get("BOT_CHAT_ID"))}
    if "github" in parts:
        out["github"] = {"key": bool(vals.get("GITHUB_PAT")), "branch": vals.get("KASKAD_BRANCH") or ""}
    if "mcp" in parts:
        out["mcp"] = {"keys": len([l for l in (files.get(F_KEYS) or "").splitlines() if l.strip()])}
    if "panel" in parts:
        pc = _json(files.get(F_PANEL), {}) or {}
        out["panel"] = {"present": bool(pc), "require_password": bool(pc.get("require_password")),
                        "public": bool((pc.get("public") or {}).get("on")),
                        "proxy": bool((pc.get("proxy") or {}).get("path"))}
    return out


def _manifest(files, parts, reason, runner=None, st=None):
    st = state.load() if st is None else st
    g = st.get("gateway") or {}
    node = {"host": str(g.get("host") or ""), "domain": str((st.get("site") or {}).get("domain") or "")}
    return {"kind": KIND, "id": "%s-k3-%s" % (util.stamp(), re.sub(r"[^0-9A-Za-z.\-]+", "", __version__)),
            "created": util.now_iso(), "reason": reason,
            "from": {"edition": "k3", "version": __version__, "engine": _engine(st)}, "to": __version__,
            "parts": list(parts), "node": node, "summary": summary(files, parts),
            "files": dict((rel, {"sha256": _sha(t), "size": len(t.encode("utf-8", "surrogateescape"))})
                          for rel, t in sorted(files.items())), "errors": {}}


def summary_doc(runner, parts=None):
    """``backup summary``: what a copy of this node would hold — counts, no secret (MCP, the wizard)."""
    parts = _parts(parts)
    files = collect(runner, parts)
    snap = last_snapshot()
    undo = {"available": bool(snap), "created": "", "path": snap or "", "agent_keys_gone": undo_keys_gone(snap)}
    if snap:
        from .migrate import snapshot as msnap
        undo["created"] = (msnap.manifest(snap) or {}).get("created") or ""
    sm = summary(files, parts)
    # names of the protocols for the SSH menu (16 → 6 → 1), never their codes (awg2, wg): summary keeps the codes
    names = _inbound_titles((sm.get("gateway") or {}).get("inbounds") or [])
    return {"ok": True, "parts": list(parts), "titles": dict((p, TITLES[p]) for p in parts),
            "summary": sm, "inbound_titles": names, "size": sum(len(t) for t in files.values()), "undo": undo}


# ── the cipher ─────────────────────────────────────────────────────────────

def _keys(password, salt, iters):
    if not isinstance(password, str) or not password:
        raise KaskadError(tr("backup.err.need_password"), code=USAGE)
    k = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, dklen=64)
    return k[:32], k[32:]


def _check(mac_key):
    return hmac.new(mac_key, b"kaskad-backup-password", hashlib.sha256).hexdigest()[:16]


def _openssl(runner, decrypt, key, data):
    if not runner.which("openssl"):
        raise UnsupportedError(tr("backup.err.no_openssl"))
    argv = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "1", "-md", "sha256", "-nosalt", "-a", "-A"]
    if decrypt:
        argv.append("-d")
    argv += ["-pass", "env:%s" % KEY_ENV]
    res = runner.run(argv, input=data, timeout=300, env={KEY_ENV: key.hex()})
    if not res.ok:
        raise KaskadError(tr("backup.err.openssl_decrypt" if decrypt else "backup.err.openssl_encrypt",
                             err=util.redact((res.err or res.out or "").strip()[:200])))
    return res.out


def check_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise KaskadError(tr("backup.err.password_short", n=MIN_PASSWORD), code=USAGE)
    if "\n" in password or "\r" in password:
        raise KaskadError(tr("backup.err.password_line"), code=USAGE)
    return password


def seal(runner, payload, password, parts):
    """The JSON ``payload`` -> the text of the file."""
    check_password(password)
    salt = os.urandom(SALT_LEN)
    enc, mac = _keys(password, salt, ITER)
    header = {"format": FORMAT, "v": VERSION, "cipher": "aes-256-cbc", "kdf": "pbkdf2-sha256", "iter": ITER,
              "salt": base64.b64encode(salt).decode(), "check": _check(mac), "created": util.now_iso(),
              "version": __version__, "parts": list(parts)}
    hline = json.dumps(header, sort_keys=True, separators=(",", ":"))
    # ASCII only: the pipe to openssl takes the locale's encoding (a service may run with LANG=C)
    body = "".join(_openssl(runner, False, enc, json.dumps(payload, ensure_ascii=True, sort_keys=True)).split())
    sig = hmac.new(mac, (hline + "\n" + body).encode("ascii"), hashlib.sha256).hexdigest()
    wrapped = "\n".join(body[i:i + 76] for i in range(0, len(body), 76))
    return "%s\n%s\n%s\nMAC %s\n" % (MAGIC, hline, wrapped, sig)


def header_of(text):
    """(header dict, header line, body) of the text of a file — its form checked, nothing decrypted."""
    if not isinstance(text, str):
        raise KaskadError(tr("backup.err.not_a_copy"), code=USAGE)
    if len(text) > FILE_MAX:
        raise KaskadError(tr("backup.err.too_big", mb=FILE_MAX // 2 ** 20), code=USAGE)
    lines = [l.strip() for l in text.replace("\r", "\n").split("\n") if l.strip()]
    if not lines or lines[0] != MAGIC:
        first = lines[0] if lines else ""
        if first.startswith("KASKAD-BACKUP "):
            raise KaskadError(tr("backup.err.newer", version=first.split(" ", 1)[1][:16]), code=USAGE)
        doc = _json(text.strip(), None)
        if isinstance(doc, dict) and doc.get("kind") == "kaskad-awg-export":
            raise KaskadError(tr("backup.err.awg_export"), code=USAGE)
        raise KaskadError(tr("backup.err.not_a_copy"), code=USAGE)
    if len(lines) < 4 or not lines[-1].startswith("MAC "):
        raise KaskadError(tr("backup.err.truncated"), code=USAGE)
    header = _json(lines[1], None)
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise KaskadError(tr("backup.err.not_a_copy"), code=USAGE)
    if header.get("v") != VERSION:
        raise KaskadError(tr("backup.err.format", v=repr(header.get("v"))), code=USAGE)
    it = header.get("iter")
    if not isinstance(it, int) or isinstance(it, bool) or not 100000 <= it <= 10000000 or \
            header.get("cipher") != "aes-256-cbc" or header.get("kdf") != "pbkdf2-sha256":
        raise KaskadError(tr("backup.err.damaged"), code=USAGE)
    try:
        salt = base64.b64decode(header.get("salt") or "", validate=True)
    except (ValueError, TypeError):
        raise KaskadError(tr("backup.err.damaged"), code=USAGE)
    if not 8 <= len(salt) <= 64:
        raise KaskadError(tr("backup.err.damaged"), code=USAGE)
    header["_salt"] = salt
    body = "".join(lines[2:-1])
    if not re.match(r"^[A-Za-z0-9+/=]+$", body):
        raise KaskadError(tr("backup.err.damaged"), code=USAGE)
    return header, lines[1], body, lines[-1][4:].strip()


def unseal(runner, text, password):
    """The text of a file + the password -> the payload. Wrong password, damage — KaskadError in words."""
    header, hline, body, sig = header_of(text)
    enc, mac = _keys(password, header["_salt"], header["iter"])
    if not hmac.compare_digest(_check(mac), str(header.get("check") or "")):
        raise KaskadError(tr("backup.err.wrong_password"), code=USAGE, data={"reason": "password"})
    want = hmac.new(mac, (hline + "\n" + body).encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig):
        raise KaskadError(tr("backup.err.damaged"), code=USAGE, data={"reason": "damaged"})
    plain = _openssl(runner, True, enc, body + "\n")
    doc = _json(plain, None)
    plain = None
    return check_payload(doc)


def check_payload(doc):
    """The payload (or a snapshot read from disk): the kind, the manifest, every file its sha256 and a known name."""
    if not isinstance(doc, dict) or doc.get("kind") != FORMAT or doc.get("v") != VERSION:
        raise KaskadError(tr("backup.err.damaged"), code=USAGE)
    m, files = doc.get("manifest"), doc.get("files")
    if not isinstance(m, dict) or m.get("kind") != KIND or not isinstance(files, dict):
        raise KaskadError(tr("backup.err.damaged"), code=USAGE)
    listed = m.get("files") or {}
    for rel, t in files.items():
        try:
            if not isinstance(t, str) or rel not in listed or listed[rel].get("sha256") != _sha(t):
                raise KaskadError(tr("backup.err.damaged"), code=USAGE)
        except UnicodeEncodeError:                # a lone surrogate outside «surrogateescape» (\ud800…)
            raise KaskadError(tr("backup.err.damaged"), code=USAGE)
        if not _known(rel):
            raise KaskadError(tr("backup.err.foreign_file", rel=rel[:80]), code=USAGE)
    if set(listed) - set(files):
        raise KaskadError(tr("backup.err.damaged"), code=USAGE)
    for rel in list(files):                       # the whole copy before any part is applied (REV-3371-2 Б-2)
        t = _text(files[rel])
        if t is not files[rel]:                   # the copy stays whole: a ticket of the panel is checked again
            files[rel] = t
            listed[rel] = dict(listed[rel], sha256=_sha(t), size=len(t.encode("utf-8")))
    m["parts"] = [p for p in (m.get("parts") or []) if p in PARTS]
    return doc


def _text(t):
    """A text of the copy as this node reads its own files (``util.read_text``: UTF-8, «replace»): a lone surrogate
    (a crafted copy, «\\udc9b» in its JSON) or the raw bytes of a snapshot («surrogateescape») -> U+FFFD. Without it a
    write of the part (``atomic_write`` encodes UTF-8) failed half-way through the import (REV-3371-2 Б-2)."""
    try:
        t.encode("utf-8")
        return t
    except UnicodeEncodeError:
        pass
    try:
        return t.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    except UnicodeEncodeError:
        return t.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def _known(rel):
    if rel in (F_STATE, F_SECRETS, F_POOL, F_POOL_RU, F_SUBS, F_TOFU, F_ALIASES, F_CONFIG, F_PANEL, F_APPEARANCE, F_LAYOUT, F_KEYS,
               F_TRAFFIC):
        return True
    return rel.startswith(F_MONITORS) and bool(_MONITOR_NAME.match(rel[len(F_MONITORS):]))


# ── export ─────────────────────────────────────────────────────────────────

def payload(runner, parts, reason="export"):
    files = collect(runner, parts)
    return {"kind": FORMAT, "v": VERSION, "manifest": _manifest(files, parts, reason), "files": files}


def export(runner, password, parts=None, path=None):
    """The copy of this node -> {"ok", "text" (the file), "name", "parts", "summary", "file"?}. ``path`` — also written
    there (0600). The password is checked (≥ 8 characters), never kept."""
    parts = _parts(parts)
    check_password(password)
    doc = payload(runner, parts)
    text = seal(runner, doc, password, parts)
    password = None
    name = "kaskad-%s-%s.kbk" % (re.sub(r"[^0-9A-Za-z.-]+", "-", doc["manifest"]["node"]["host"] or "copy")[:40] or "copy",
                                 util.stamp()[:8])
    out = {"ok": True, "text": text, "name": name, "parts": list(parts), "summary": doc["manifest"]["summary"],
           "size": len(text)}
    if path:
        if os.path.isdir(path):
            path = os.path.join(path, name)
        d = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(d):
            raise KaskadError(tr("backup.err.no_dir", path=d), code=USAGE)
        util.atomic_write(path, text, 0o600)
        out["file"] = path
    util.log_tr("backup.log.export", parts=",".join(parts), dest=" → %s" % path if path else "")
    return out


# ── the snapshot before an import (the format of kaskad.migrate.snapshot) ──

def take_snapshot(runner, parts=PARTS, reason="backup-import"):
    """This node's parts on disk: /var/backups/kaskad/<id>/ (0700) with manifest.json and files/… (0600)."""
    from .migrate import snapshot as msnap
    doc = payload(runner, parts, reason)
    base = paths.backups()
    util.ensure_dir(base, 0o700)
    name, n = doc["manifest"]["id"], 2
    path = os.path.join(base, name)
    while os.path.exists(path):
        path = os.path.join(base, "%s-%d" % (doc["manifest"]["id"], n))
        n += 1
    doc["manifest"]["id"] = os.path.basename(path)
    try:
        os.makedirs(path, 0o700)
        os.chmod(path, 0o700)
        for rel, t in doc["files"].items():
            msnap._write(os.path.join(path, rel), t.encode("utf-8", "surrogateescape"))
        msnap._write(os.path.join(path, msnap.MANIFEST), util.json_text(doc["manifest"]).encode("utf-8"))
    except OSError as e:
        shutil.rmtree(path, ignore_errors=True)
        raise KaskadError(tr("backup.err.snapshot_failed", error=e))
    msnap.prune(keep_path=path)
    return path


def read_snapshot(path):
    """A snapshot of ours (reason backup-import or any with the files of the format) -> the payload."""
    from .migrate import snapshot as msnap
    m = msnap.manifest(path)
    if m is None or not m.get("parts"):
        raise KaskadError(tr("backup.err.no_snapshot", path=path))
    files = {}
    for rel in m.get("files") or {}:
        if _known(rel):
            files[rel] = msnap.read_text(path, rel)
    m = dict(m, files=dict((r, v) for r, v in (m.get("files") or {}).items() if r in files))
    return check_payload({"kind": FORMAT, "v": VERSION, "manifest": m, "files": files})


def undo_keys_gone(path=None):
    """How many agent keys here «Вернуть как было» (``undo``) would remove — not in the snapshot: their agents are cut
    off, and the question before the return says so (R-333 П3‴). The same comparison as _apply_mcp; 0 when unknown."""
    from .mcp import keys
    path = path if path is not None else last_snapshot()
    if not path:
        return 0
    try:
        doc = read_snapshot(path)
    except (KaskadError, OSError, ValueError):
        return 0
    if "mcp" not in (doc["manifest"].get("parts") or []):
        return 0
    want = set(l.strip() for l in (doc["files"].get(F_KEYS) or "").splitlines() if keys._ours(l))
    return len(set(_mcp_lines()) - want)


def last_snapshot(reason="backup-import"):
    from .migrate import snapshot as msnap
    for p, m in reversed(msnap.snapshots()):
        if m.get("reason") == reason and m.get("parts"):
            return p
    return None


# ── preview ────────────────────────────────────────────────────────────────

def _here_ip(runner):
    try:
        from .gw import net
        ip, how = net.detect_host(runner)
        return ip if how != "route-private" else ""
    except (KaskadError, OSError, ValueError):
        return ""


def _machine_addresses(runner, st):
    """The addresses of this machine (gw/addresses.py: found on it, its NAT pairs, added by hand), looked at without
    the network; an empty set when they cannot be read."""
    try:
        from .gw import addresses
        return set(addresses.known_list(addresses.collect(runner, st, net=False)))
    except (KaskadError, OSError, ValueError):
        return set()


def _is_ip(s):
    import ipaddress
    try:
        ipaddress.ip_address(str(s))
        return True
    except ValueError:
        return False


def preview(runner, doc, parts=None, st=None, same_node=False, agents=False, exact=False):
    """What the import brings and what it replaces here -> {"ok", "parts": [{"part", "title", "file", "here",
    "action", "text"}], "address": {...}, "warnings", "blockers", "created", "version", "changes" (what is lost and
    replaced here by name — ``changes``), "agents"?: {"keys", "on"} (the copy holds keys of AI agents)}. Nothing
    changes. ``same_node`` — the copy is of this very server (``backup undo``): no words of a move (the A record of
    the domain, the bot on «the old server») — T-M6 №19. ``agents`` — the keys of the AI agents of the copy are added
    here (SEC-3372 З-3, the decision of the coordinator): never by default — a key of a copy is an agent that runs this
    server over SSH (all its actions, the export of a copy and the update among them), so only a person's explicit
    tick (the panel), ``--agents`` (kaskadctl) or the question of the menu brings them; a return (``exact``) always —
    and without the warning about «keys of the copy»: a return brings back this node's own keys (REV-3372-2 З-1)."""
    m = doc["manifest"]
    have = tuple(p for p in m["parts"] if p in PARTS)
    parts = tuple(p for p in _parts(parts, have) if p in have)
    st = state.load() if st is None else st
    here_files = collect(runner, parts, st)
    file_sum, here_sum = summary(doc["files"], parts), summary(here_files, parts)
    rows, warnings, blockers = [], [], []
    offer = None
    for p in parts:
        f, h = file_sum.get(p) or {}, here_sum.get(p) or {}
        row = _row(p, f, h)
        if p == "mcp" and f.get("keys"):
            offer = {"keys": f["keys"], "on": bool(agents or exact)}
            if exact:                                     # undo / the rollback: the node's own keys, no warning
                pass
            elif agents:
                warnings.append(tr("backup.preview.mcp_on", n=f["keys"]))
            else:                                         # SEC-3372 З-3: not without the explicit tick
                row = {"action": "none", "text": tr("backup.row.mcp_off", keys=f["keys"])}
        rows.append(dict(row, part=p, title=TITLES[p], file=f, here=h))
    engine = _engine(st)
    fst = _json(doc["files"].get(F_STATE), {}) or {}
    if engine != "nft" and (("gateway" in parts and fst.get("gateway")) or ("forwards" in parts and fst.get("forwards"))):
        blockers.append(tr("backup.preview.need_nft"))
    addr = _address(runner, doc, parts, st, same_node)
    warnings += addr["warnings"]
    if "bot" in parts and (file_sum.get("bot") or {}).get("token") and not same_node:
        warnings.append(tr("backup.preview.bot_token"))
    if "gateway" in parts and isinstance(fst.get("gateway"), dict) and _tariff_foreign(fst["gateway"]):
        warnings.append(tr("backup.preview.tariff_dropped"))
    gone = _vless_consent_gone(fst, parts, st, exact) if "gateway" in parts else None
    if gone is not None:                                  # REV-3378-final п.4: said before, as the tariff
        warnings.append(tr("backup.preview.vless_consent", domain=gone or "—"))
    ch = changes(doc["files"], here_files, [r["part"] for r in rows if r["action"] != "none"], st)
    out = {"ok": not blockers, "parts": rows, "address": addr, "warnings": warnings, "blockers": blockers,
            "created": m.get("created") or "", "version": (m.get("from") or {}).get("version") or "",
            "changes": ch, "text": _preview_text(rows, addr, warnings, blockers) +
            [tr("backup.preview.lost", text=x["text"]) for x in ch["lost"]] + ["~ %s" % x["text"] for x in ch["changed"]]}
    if offer:
        out["agents"] = offer
    return out


def _row(p, f, h):
    """{"action": add|replace|keep|none, "text"} of one part."""
    if p == "gateway":
        if not f.get("present"):
            return {"action": "none", "text": tr("backup.row.gateway_none")}
        what = tr("backup.row.gateway", users=f["users"], devices=f["devices"],
                  inbounds=", ".join(_inbound_titles(f["inbounds"])) or tr("backup.row.no"))
        busy = h.get("users") or h.get("devices") or h.get("inbounds")
        return {"action": "replace" if busy else "add",
                "text": what + (tr("backup.row.gateway_here", users=h["users"], devices=h["devices"],
                                   inbounds=", ".join(_inbound_titles(h["inbounds"])) or tr("backup.row.no"))
                                if busy else "")}
    if p == "exits":
        what = tr("backup.row.exits", keys=f["keys"], subs=f["subs"])
        busy = h.get("keys") or h.get("subs")
        return {"action": "replace" if busy else ("add" if f["keys"] or f["subs"] else "none"),
                "text": what + (tr("backup.row.exits_here", keys=h["keys"], subs=h["subs"]) if busy else "")}
    if p == "forwards":
        what = tr("backup.row.forwards", forwards=f["forwards"], names=f["names"])
        busy = h.get("forwards")
        return {"action": "replace" if busy else ("add" if f["forwards"] or f["names"] or f["monitors"] else "none"),
                "text": what + (tr("backup.row.forwards_here", forwards=h["forwards"]) if busy else "")}
    if p == "site":
        if not f.get("domain"):
            return {"action": "none", "text": tr("backup.row.site_none")}
        busy = h.get("domain") and h["domain"] != f["domain"]
        return {"action": "replace" if busy else "add",
                "text": tr("backup.row.site", domain=f["domain"],
                           instead=tr("backup.row.site_instead", domain=h["domain"]) if busy else "")}
    if p == "bot":
        if not f.get("token"):
            return {"action": "none", "text": tr("backup.row.bot_none")}
        return {"action": "replace" if h.get("token") else "add",
                "text": tr("backup.row.bot") + (tr("backup.row.bot_here") if h.get("token") else "")}
    if p == "github":
        if not f.get("key"):
            return {"action": "none", "text": tr("backup.row.github_none")}
        return {"action": "keep" if h.get("key") else "add",
                "text": tr("backup.row.github") + (tr("backup.row.github_keep") if h.get("key") else "") +
                        (tr("backup.row.github_branch", branch=h["branch"]) if h.get("branch") else "")}
    if p == "mcp":
        return {"action": "add" if f.get("keys") else "none",
                "text": tr("backup.row.mcp", keys=f.get("keys") or 0, here=h.get("keys") or 0)}
    if p == "panel":
        return {"action": "replace" if f.get("present") else "none",
                "text": tr("backup.row.panel", ask=tr("backup.row.yes") if f.get("require_password")
                           else tr("backup.row.no"))}
    return {"action": "none", "text": ""}


def _copy_host(value):
    """The address for clients of a copy (``gateway.host``), checked as ``gw host`` checks it (``net.parse_host``: a
    public IPv4 or a domain); anything else — "" (REV-mip-s12: the copy is encrypted and signed, yet its value goes to
    the screen, to the settings of the devices and to the menu — never unchecked)."""
    from .gw import net
    if not value:
        return ""
    try:
        return net.parse_host(value)
    except KaskadError:
        return ""


def _address(runner, doc, parts, st, same_node=False):
    """The address in the settings the devices were given: the old server's IP, or a domain (``same_node``: the copy
    is of this server — a domain needs no new A record). An IP is «changed» only when this machine does not have it
    (PLAN-multi-ip Ш2, Л1): a second address chosen for clients on this very server is not the route's source, yet it
    is this server's — it stays, with the certificate of the node issued on it. ``same`` — the old IP is this
    machine's (its manual addresses come back with the copy, ``_addresses_of_this_machine``)."""
    fst = _json(doc["files"].get(F_STATE), {}) or {}
    g = fst.get("gateway") or {}
    old = _copy_host(g.get("host")) or _copy_host((doc["manifest"].get("node") or {}).get("host"))
    out = {"old": old, "here": "", "kind": "", "changed": False, "same": False, "warnings": []}
    if "gateway" not in parts or not g or not old:
        return out
    here = _here_ip(runner) or str((st.get("gateway") or {}).get("host") or "")
    out["here"] = here
    if _is_ip(old):
        out["kind"] = "ip"
        out["same"] = old in _machine_addresses(runner, st)
        out["changed"] = bool(here) and here != old and not out["same"]
        if not here:
            out["warnings"].append(tr("backup.addr.unknown", old=old))
        if out["changed"]:
            out["warnings"].append(tr("backup.addr.changed", old=old, here=here))
    else:
        out["kind"] = "domain"
        if not same_node:
            out["warnings"].append(tr("backup.addr.domain", old=old, here=here or tr("backup.addr.this_server")))
    return out


def _preview_text(rows, addr, warnings, blockers):
    lines = ["• %s — %s: %s" % (SHORT[r["part"]], tr("backup.action." + r["action"]), r["text"]) for r in rows]
    lines += ["! %s" % w for w in warnings]
    lines += ["✗ %s" % b for b in blockers]
    return lines


# ── what this node holds and what an import takes from it ─────────────────

HERE_PARTS = ("gateway", "exits", "forwards", "site", "bot", "mcp")   # what a person made here (not panel, github)
LIST_MAX = 5                                                          # names shown, the rest — «и ещё N»


def _n(n, what):
    """«3 человека» — the count of ``what`` (backup.n.<what>.one|few|many) in the language of the moment."""
    return trn("backup.n." + what, n)


def _names(names):
    names = [str(x) for x in names if x]
    if len(names) <= LIST_MAX:
        return ", ".join(names)
    return tr("backup.names_more", names=", ".join(names[:LIST_MAX]), n=len(names) - LIST_MAX)


def _inbound_titles(kinds):
    from .gw import issue_os
    return [issue_os.TITLES.get(k, k) for k in kinds]


def here_items(sm):
    """Words of what a node holds — ``summary`` of HERE_PARTS: [{"part", "what", "n", "text"}]; [] — the node is empty
    (no people, devices, protocols, exits, subscriptions, forwards, domain, bot, agents' keys). The quick setup says
    «Чистый сервер» only then (T-M6-qs330 №4: it said so on a node with 9 exits and people)."""
    out = []

    def put(part, what, n, text):
        if n:
            out.append({"part": part, "what": what, "n": n, "text": text})
    g = sm.get("gateway") or {}
    put("gateway", "users", g.get("users") or 0, _n(g.get("users") or 0, "users"))
    put("gateway", "devices", g.get("devices") or 0, _n(g.get("devices") or 0, "devices"))
    ib = g.get("inbounds") or []
    put("gateway", "inbounds", len(ib), tr("backup.here.protocols", names=", ".join(_inbound_titles(ib))))
    e = sm.get("exits") or {}
    put("exits", "keys", e.get("keys") or 0, _n(e.get("keys") or 0, "exits"))
    put("exits", "subs", e.get("subs") or 0, _n(e.get("subs") or 0, "subs"))
    f = sm.get("forwards") or {}
    put("forwards", "forwards", f.get("forwards") or 0, _n(f.get("forwards") or 0, "forwards"))
    d = (sm.get("site") or {}).get("domain") or ""
    put("site", "domain", 1 if d else 0, tr("backup.here.domain", domain=d))
    put("bot", "bot", 1 if (sm.get("bot") or {}).get("token") else 0, tr("backup.here.bot"))
    k = (sm.get("mcp") or {}).get("keys") or 0
    put("mcp", "keys", k, _n(k, "agent_keys"))
    return out


def here_doc(runner, st=None):
    """What this node holds: {"items": here_items, "empty": bool, "text": «3 человека, 9 выходов, …»}. Nothing is
    written; no secret (counts, kinds of protocols, the domain)."""
    files = collect(runner, HERE_PARTS, st)
    items = here_items(summary(files, HERE_PARTS))
    return {"items": items, "empty": not items, "text": ", ".join(i["text"] for i in items)}


def _group_names(g):
    """{gid: the shown name} of the groups of the exits of a part ``gateway`` (``policy.label``: «Основная» for «main»
    without a name; stage 1)."""
    from .gw import policy as P
    pol = g.get("exit_policy") if isinstance(g, dict) else None
    groups = pol.get("groups") if isinstance(pol, dict) else None
    out = {}
    for grp in groups if isinstance(groups, list) else []:
        if isinstance(grp, dict) and grp.get("id"):
            out[str(grp["id"])] = P.label(grp)
    return out


def _pool_ids(files):
    """The ids of the exits of the pool of a side (``files`` — a payload or ``collect`` of this node), no parser."""
    from .gw import exits as gx
    raw = files.get(F_POOL) if isinstance(files, dict) else None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()] if isinstance(raw, str) else []
    ru = files.get(F_POOL_RU) if isinstance(files, dict) else None    # T15: the exits of both files
    if isinstance(ru, bytes):
        ru = ru.decode("utf-8", "replace")
    ru = [ln.strip() for ln in (ru or "").splitlines() if ln.strip()] if isinstance(ru, str) else []
    return [eid for eid, _ in gx.line_ids(lines)] + [eid for eid, _ in gx.line_ids(ru, "ru")]


def _route_words(r, exit_names, group_names=None, gw=None, pool=()):
    """The route of a copy for people; the fail step as the decision takes it — the route's own policy or the one of its
    group in ``gw`` (the part ``gateway`` of the same side: ``exits.fail_of``, REV-s1-b2 З1) with the exits of the pool
    of that side (``pool`` — their ids) placed (REV-s1-b4 П-1)."""
    from .gw import exits as gx, model as gm, policy as P
    r = r if isinstance(r, dict) and r.get("via") else {"via": "pool", "fail": "block"}
    via = str(r.get("via"))
    group = via.startswith(gm.GROUP_VIA)
    if via == "pool":
        w = tr("backup.route.pool")
    elif via == "direct":
        w = tr("backup.route.direct")
    elif via == "block":
        w = tr("backup.route.block")
    elif via.startswith("exit:"):
        w = tr("backup.route.exit", name=exit_names.get(via[5:]) or via[5:])
    elif group:                                     # stage 1 (REV-s1-t4 З6): the name of the group, not its id
        gid = via[len(gm.GROUP_VIA):]
        w = tr("backup.route.group", name=(group_names or {}).get(gid) or P.label({"id": gid}))
    else:
        w = via
    if via == "pool" or via.startswith("exit:") or group:
        fail = gx.fail_of({"gateway": gw if isinstance(gw, dict) else {}}, r, pool=list(pool or ()))
        w += tr("backup.route.fail", how=tr("backup.route.fail_direct") if fail == "direct"
                else tr("backup.route.fail_block"))
    return w


def _exit_label(line, names, parser):
    from .gw import exits
    eid = exits.pool_id(line)                       # 3.3.5: the id a subscription kept for it
    if names.get(eid):
        return names[eid]
    e = {"name": "", "host": ""}
    if parser is not None:
        try:
            lab = parser.pool_label(line) or {}
            e = {"name": exits.term_text(lab.get("name")), "host": exits.term_text(lab.get("host"))}
        except Exception:                           # noqa: BLE001 — a foreign parser: the kind alone names it
            pass
    kind = ("AmneziaWG" if line.startswith("awg://") else exits.proto_title(exits.link_proto(line))
            or tr("backup.exit_key"))
    return exits.own_name(e) or " · ".join(x for x in (kind, e["host"]) if x)


def _sub_label(s):
    import urllib.parse
    try:
        host = urllib.parse.urlsplit(str(s.get("url") or "")).hostname or ""
    except ValueError:
        host = ""
    return tr("backup.sub_label", host=host or s.get("id") or "")        # the address of a subscription is a secret: the host only


def changes(files, here_files, parts, here_st=None):
    """What the import of ``files`` (a payload) takes from this node (``here_files`` — ``collect`` of it) for the parts it
    applies: {"lost": [{"part", "what", "n", "text"}] — gone after the import (people and devices made after the copy,
    protocols, exits, subscriptions and forwards the copy has not), "changed": [{"part", "what", "text"}] — replaced by
    the copy's (the default route, a forward's target, the domain, the bot), "empty": this node holds nothing of
    these parts, "confirm": the import needs an explicit «да» (something is lost or replaced)}. Names and counts only — no key, no
    address of a subscription, no token."""
    fst = _json(files.get(F_STATE), {}) or {}
    hst = _json(here_files.get(F_STATE), {}) or {}
    lost, changed = [], []
    fg, hg = fst.get("gateway") or {}, hst.get("gateway") or {}
    names = {}
    for g in (fg, hg):
        for eid, rec in (g.get("exits") or {}).items():
            if isinstance(rec, dict) and rec.get("name"):
                names[eid] = str(rec["name"])
    if "gateway" in parts and fg:
        fu = set(u.get("id") for u in fg.get("users") or [] if isinstance(u, dict))
        users = dict((u.get("id"), u) for u in hg.get("users") or [] if isinstance(u, dict))
        gone = [u for u in users.values() if u.get("id") not in fu]
        if gone:
            lost.append({"part": "gateway", "what": "users", "n": len(gone),
                         "text": "%s: %s" % (_n(len(gone), "users"),
                                             _names(u.get("name") or u.get("id") for u in gone))})
        fd = set(d.get("id") for d in fg.get("devices") or [] if isinstance(d, dict))
        gone = [d for d in hg.get("devices") or [] if isinstance(d, dict) and d.get("id") not in fd]
        if gone:
            lost.append({"part": "gateway", "what": "devices", "n": len(gone),
                         "text": "%s: %s" % (_n(len(gone), "devices"), _names(
                             "%s (%s)" % (d.get("name") or d.get("id"), (users.get(d.get("user")) or {}).get("name"))
                             if (users.get(d.get("user")) or {}).get("name") else (d.get("name") or d.get("id"))
                             for d in gone))})
        fk = set(k for k, v in (fg.get("inbounds") or {}).items() if isinstance(v, dict))
        gone = [k for k in sorted(hg.get("inbounds") or {}) if isinstance(hg["inbounds"][k], dict) and k not in fk]
        if gone:
            lost.append({"part": "gateway", "what": "inbounds", "n": len(gone),
                         "text": tr("backup.here.protocols", names=", ".join(_inbound_titles(gone)))})
        rf = ((fg.get("routes") or {}).get("default"))
        rh = ((hg.get("routes") or {}).get("default"))
        wf = _route_words(rf, names, _group_names(fg), fg, _pool_ids(files))
        wh = _route_words(rh, names, _group_names(hg), hg, _pool_ids(here_files))
        used = bool(hg.get("users") or hg.get("devices") or
                    [k for k, v in (hg.get("inbounds") or {}).items() if isinstance(v, dict)])
        if used and wf != wh:                    # the route of a gateway nobody uses yet is nothing to lose
            changed.append({"part": "gateway", "what": "route", "text": tr("backup.ch.route", old=wh, new=wf)})
        if used and hg.get("enabled") and not fg.get("enabled"):
            changed.append({"part": "gateway", "what": "enabled", "text": tr("backup.ch.gateway_off")})
    if "exits" in parts:
        from .gw import exits
        both = (files.get(F_POOL) or "") + "\n" + (files.get(F_POOL_RU) or "")          # T15: both files of the pool
        fl = set(exits.exit_id(l) for l in both.splitlines() if l.strip())
        gone = [l.strip() for l in ((here_files.get(F_POOL) or "") + "\n" + (here_files.get(F_POOL_RU) or "")).splitlines()
                if l.strip() and exits.exit_id(l) not in fl]
        if gone:
            try:
                parser = exits.load_parser()
            except KaskadError:
                parser = None
            lost.append({"part": "exits", "what": "keys", "n": len(gone),
                         "text": "%s: %s" % (_n(len(gone), "exits"),
                                             _names(_exit_label(l, names, parser) for l in gone))})
        fs = set(s.get("id") for s in (_json(files.get(F_SUBS), {}) or {}).get("subs") or [] if isinstance(s, dict))
        gone = [s for s in (_json(here_files.get(F_SUBS), {}) or {}).get("subs") or []
                if isinstance(s, dict) and s.get("id") not in fs]
        if gone:
            lost.append({"part": "exits", "what": "subs", "n": len(gone),
                         "text": "%s: %s" % (_n(len(gone), "subs"),
                                             _names(_sub_label(s) for s in gone))})
    if "forwards" in parts and "forwards" in fst:
        def key(f):
            return (str(f.get("proto") or ""), f.get("port"))

        def words(f):
            return "%s %s → %s:%s" % (f.get("proto"), f.get("port"), f.get("to"), f.get("to_port") or f.get("port"))
        ff = dict((key(f), f) for f in fst.get("forwards") or [] if isinstance(f, dict))
        hf = [f for f in hst.get("forwards") or [] if isinstance(f, dict)]
        gone = [f for f in hf if key(f) not in ff]
        if gone:
            lost.append({"part": "forwards", "what": "forwards", "n": len(gone),
                         "text": "%s: %s" % (_n(len(gone), "forwards"),
                                             _names(words(f) for f in gone))})
        for f in hf:
            o = ff.get(key(f))
            if o is not None and (str(o.get("to")), o.get("to_port")) != (str(f.get("to")), f.get("to_port")):
                changed.append({"part": "forwards", "what": "forward",
                                "text": tr("backup.ch.forward", old=words(f), new=words(o))})
    if "site" in parts:
        d_here = str((hst.get("site") or {}).get("domain") or "")
        d_file = str((fst.get("site") or {}).get("domain") or "")
        if d_here and d_file and d_here != d_file:
            changed.append({"part": "site", "what": "domain", "text": tr("backup.ch.domain", old=d_here, new=d_file)})
    if "bot" in parts:
        tf = config_values(files.get(F_CONFIG), BOT_KEYS).get("BOT_TOKEN")
        th = config_values(here_files.get(F_CONFIG), BOT_KEYS).get("BOT_TOKEN")
        if tf and th and tf != th:
            changed.append({"part": "bot", "what": "bot", "text": tr("backup.ch.bot")})
    empty = not here_items(summary(here_files, [p for p in HERE_PARTS if p in parts]))
    return {"lost": lost, "changed": changed, "empty": empty, "confirm": bool(lost or changed)}


def changes_sig(ch):
    """A digest of ``changes`` — the import checks that what was shown is still what it takes."""
    return _sha(json.dumps([[x.get("text") for x in ch.get("lost") or []],
                            [x.get("text") for x in ch.get("changed") or []]], ensure_ascii=False, sort_keys=True))


# ── apply ──────────────────────────────────────────────────────────────────

class _Ctx(object):
    """``exact`` — a rollback or ``undo``: a part missing from the copy is made empty here (the import had added it),
    the keys of the agents are exactly the copy's; an import only adds and replaces what the copy holds."""

    def __init__(self, runner, doc, addr, say, exact=False, tag="import", same_node=False, parts=PARTS):
        self.exact = exact
        self.parts = tuple(parts)                         # what this run imports (the domain of the node after it)
        self.same_node = same_node                        # the copy is of this server: the marks ⚠РФ come as they are
        self.tag = tag                                    # the journal: «BACKUP import|undo|rollback: …» (qs330e №2)
        self.quiet = False                                # a part a return goes through silently: no journal line
        self.runner = runner
        self.doc = doc
        self.files = doc["files"]
        self.state = _json(self.files.get(F_STATE), {}) or {}
        self.addr = addr
        self.warnings = []
        self.notes = []
        self.agents_cut = 0                               # agent connections the removed keys took with them (R-333 П3‴)
        self.moved = []                                   # [(kind, words)]: what the gateway part changed for devices
        self.moved_text = None                            # … and its line in ``warnings`` (``_moved_warning``)
        self.say = say or (lambda text: None)

    def log(self, text):
        if not self.quiet:
            util.log_action("BACKUP %s: %s" % (self.tag, text))


def _apply_github(ctx):
    vals = config_values(ctx.files.get(F_CONFIG), GITHUB_KEYS)
    here = config_values(_read(paths.config()), GITHUB_KEYS)
    put = {}
    if ctx.exact:
        put = dict((k, vals.get(k, "")) for k in GITHUB_KEYS if vals.get(k) or here.get(k))
    if vals.get("GITHUB_PAT") and not here.get("GITHUB_PAT"):
        put["GITHUB_PAT"] = vals["GITHUB_PAT"]
    if vals.get("KASKAD_BRANCH") and not here.get("KASKAD_BRANCH"):
        put["KASKAD_BRANCH"] = vals["KASKAD_BRANCH"]          # this node's own branch (its install chose it) stays
    if put:
        _set_config(put)


def _apply_bot(ctx):
    vals = config_values(ctx.files.get(F_CONFIG), BOT_KEYS)
    if not vals.get("BOT_TOKEN") and not ctx.exact:
        return
    _set_config(dict((k, vals.get(k, "")) for k in BOT_KEYS if k in vals or k != "MENU_STYLE"))
    from .gw import sys as gsys
    if not vals.get("BOT_TOKEN"):
        if os.path.exists(gsys.unit_path("kaskad-bot.service")):
            ctx.runner.run(["systemctl", "restart", "kaskad-bot.service"], timeout=60)
        return
    if os.path.exists(gsys.unit_path("kaskad-bot.service")):
        ctx.runner.run(["systemctl", "restart", "kaskad-bot.service"], timeout=60)
        ctx.notes.append(tr("backup.note.bot_restarted"))
    else:
        ctx.notes.append(tr("backup.note.bot_start"))


def _apply_panel(ctx):
    from .panel import conf
    pc = _json(ctx.files.get(F_PANEL), None)
    if isinstance(pc, dict):
        cfg = conf.load()
        cfg["require_password"] = pc.get("require_password") is True
        path = ((pc.get("proxy") or {}).get("path") or "")
        if path and conf.PROXY_PATH_RE.match(path) and not cfg["proxy"]["on"]:
            cfg["proxy"]["path"] = path                  # the same address on the domain once it is back
        conf.save(cfg)
        # a note for what did not come back by itself — the open mode is this server's to switch on; on the same server
        # (``backup undo``) it is still on and nothing is to be said (3.3.0 T-M6 №19)
        if (pc.get("public") or {}).get("on") and not cfg["public"]["on"]:
            ctx.notes.append(tr("backup.note.panel_public", port=(pc.get("public") or {}).get("port") or "—"))
    for rel, p in ((F_APPEARANCE, conf.appearance_path()), (F_LAYOUT, conf.layout_path())):
        t = ctx.files.get(rel)
        if t is not None and isinstance(_json(t), dict):
            util.ensure_dir(paths.panel_dir(), 0o700)
            util.atomic_write(p, t, 0o600)
    if conf.service_state(ctx.runner) == "active":
        try:
            conf.reload_service(ctx.runner)
        except KaskadError as e:
            ctx.warnings.append(tr("backup.warn.panel_reload", error=e))


def _apply_mcp(ctx):
    from .mcp import keys
    have = set(l.strip() for l in _mcp_lines())
    if ctx.exact:                                         # the agents' keys exactly as in the copy
        want = set(l.strip() for l in (ctx.files.get(F_KEYS) or "").splitlines() if keys._ours(l))
        gone = []
        for line in have - want:
            o = keys._ours(line)
            try:
                fp = keys.fingerprint(o[1])
                keys.delete(fp, ctx.runner, end=False)
                gone.append(fp)
            except KaskadError:
                pass
        have = have & want
        # a removed key's open sessions end (as on every delete) — once for all the keys, and said: an agent cut off
        # by «Вернуть как было» is not a surprise (R-333 З2″); unsure — sessions of other agents may be cut too
        if gone:
            cut = keys.end_sessions(gone, ctx.runner)
            ctx.agents_cut += cut["ended"]
            ctx.notes.append(tr("backup.note.mcp_cut_all" if cut["unsure"] else
                                "backup.note.mcp_cut" if cut["ended"] else "backup.note.mcp_removed", n=len(gone)))
    added = 0
    for line in (ctx.files.get(F_KEYS) or "").splitlines():
        o = keys._ours(line)
        if not o or line.strip() in have:
            continue
        try:
            keys.add("%s %s" % (o[0], base64.b64encode(o[1]).decode()), label=o[2] or None)
            added += 1
        except ConflictError:
            continue
    if added:
        ctx.notes.append(tr("backup.note.mcp_added", n=added))


def _apply_forwards(ctx):
    from . import compat, conflicts, nft
    fwds = ctx.state.get("forwards")
    if fwds is None and ctx.exact and (state.load().get("forwards") or []):
        fwds = []
    if fwds is not None and not ctx.exact:
        keep = []
        for f in fwds:                                    # a forward never cuts SSH to this server
            try:
                found = conflicts.check(f.get("proto"), f.get("port"), ctx.runner)
            except KaskadError:
                found = []
            if any(c.get("kind") == "ssh" for c in found):
                ctx.warnings.append(tr("backup.warn.forward_ssh", proto=f.get("proto"), port=f.get("port"),
                                       why=conflicts.message(f["proto"], f["port"], found)))
                continue
            if found:
                ctx.warnings.append(tr("backup.warn.forward_conflict", proto=f.get("proto"), port=f.get("port"),
                                       why=conflicts.message(f["proto"], f["port"], found)))
            keep.append(f)
        fwds = keep
    if fwds is not None:
        with state.locked():
            st = state.load()
            if _engine(st) != "nft":
                raise UnsupportedError(tr("backup.err.forwards_need_nft"))
            new = copy.deepcopy(st)
            new["forwards"] = []
            for f in fwds:
                state.upsert_forward(new, f)
            res = nft.apply(new, ctx.runner)
            state.save(new)                               # ⚠РФ: forwards only — no rfgate.check
            ctx.warnings += [w for w in res.get("warnings") or [] if isinstance(w, str)]
        try:
            compat.ensure(ctx.runner)
        except KaskadError as e:
            ctx.warnings.append(str(e))
        ctx.log(tr("backup.log.forwards", n=len(new["forwards"])))
    al = ctx.files.get(F_ALIASES)
    if al is None and ctx.exact and os.path.exists(paths.aliases()):
        al = ""
    if al is not None:
        from .bot import aliases as balias
        util.atomic_write(paths.aliases(), balias.clean_file(al), 0o600)     # fields of 2.x — no «\», no control (SEC-3371 Б-1)
    mons = [(r[len(F_MONITORS):], t) for r, t in sorted(ctx.files.items()) if r.startswith(F_MONITORS)]
    if mons:
        # REV-3371-2 Б-1 (3.3.7.2): bash of 2.x reads these files (it did ``source``) — never as they came: each one
        # parsed (bot.monitors.parse: the address, the numbers) and written in our format as «<ip>.conf»; one that is
        # not a monitor — skipped, said and journalled
        from .bot import monitors as bmon
        util.ensure_dir(paths.monitors(), 0o700)
        for n, t in mons:
            m, text = bmon.clean_text(t)
            if m is None:
                ctx.warnings.append(tr("backup.warn.monitor_bad", name=n))
                util.log_action("BACKUP %s: monitor file %s skipped (not a monitor)" % (ctx.tag, n))
                continue
            if t != text:
                util.log_action("BACKUP %s: monitor file %s rewritten checked (lines besides the monitor dropped)"
                                % (ctx.tag, n))
            util.atomic_write(bmon.conf_path(m["ip"]), text, 0o600)


def _tofu_servers(doc):
    """{server: record} of a tofu-pins.json (egress_parse «tofu», 3.3.4): the pins and the «verified» marks taken on the
    update — only well-formed ones; never «pending» (a server that did not answer is tried by the node that had the key
    before 3.3.4 only)."""
    servers = doc.get("servers") if isinstance(doc, dict) else None
    out = {}
    for k, r in (servers.items() if isinstance(servers, dict) else ()):
        if not isinstance(k, str) or not _TOFU_SERVER.match(k) or not isinstance(r, dict):
            continue
        if r.get("verified") is True:
            rec = {"verified": True}
            if "name" in r:                            # the chain checked out for this name (verifyPeerCertByName)
                if not (isinstance(r["name"], str) and _TOFU_NAME.match(r["name"])):
                    continue
                rec["name"] = r["name"]
        elif isinstance(r.get("pin"), str) and _HEX64.match(r["pin"]):
            rec = {"pin": r["pin"]}
        else:
            continue
        if isinstance(r.get("at"), int) and not isinstance(r.get("at"), bool):
            rec["at"] = r["at"]
        rec["auto"] = r.get("auto") is not False
        out[k] = rec
    return out


def _tofu_export():
    """F_TOFU of this node: its «servers» — or None when there are none (no pin was taken here: most nodes). Only a
    copy with pins carries the file: 3.3.3 refuses a copy with a file it does not know, so a copy of 3.3.4 without
    pins opens there too. The file in the copy says the run was done: the import does not run it again
    (_tofu_import); a copy without it — as a copy before 3.3.4 (see _tofu_import: its keys with allowInsecure are
    refused on a node of 3.3.4 that has done its first run)."""
    servers = _tofu_servers(_json(_read(paths.egress_tofu()), None))
    if not servers:
        return None
    return util.json_text({"v": 1, "servers": servers})


def _tofu_import(text):
    """The pins of the copy into tofu-pins.json of this node before the pool is replaced: the ones this node took itself
    are kept (its own win), a server pinned now leaves «pending»; the first run of «tofu» is marked done — the keys of
    the copy are not pinned again here (a key with allowInsecure that has no pin in the copy stays refused, as on the
    node it came from). No F_TOFU (a copy before 3.3.4, or of 3.3.4 with no pins) — nothing is done here: the keys
    of the copy with allowInsecure and no pin are taken by the first run only on a node that has not had it yet (no
    .tofu-done — practically never: any node of 3.3.4 marks it on its first rebuild of the pool, with or without such
    keys); on a working node of 3.3.4 they are refused as new keys — they are to be issued again with a fingerprint
    (pcs) or by a certificate that checks out (README «Что нового в 3.3.4»)."""
    if text is None:
        return
    got = _tofu_servers(_json(text, {}))
    cur = _json(_read(paths.egress_tofu()), {})
    cur = cur if isinstance(cur, dict) else {}
    own = cur.get("servers") if isinstance(cur.get("servers"), dict) else {}
    new = dict(cur)
    new["v"] = 1
    servers = dict(got)
    servers.update(own)
    new["servers"] = servers
    pend = cur.get("pending") if isinstance(cur.get("pending"), dict) else {}
    pend = dict((k, v) for k, v in pend.items() if k not in servers)
    new.pop("pending", None)
    if pend:
        new["pending"] = pend
    if not os.path.isdir(paths.egress_dir()):
        os.makedirs(paths.egress_dir(), 0o700)
    if new != cur:
        util.atomic_write(paths.egress_tofu(), json.dumps(new, indent=1, sort_keys=True) + "\n", 0o600)
    if not os.path.exists(paths.egress_tofu_done()):
        util.atomic_write(paths.egress_tofu_done(), "", 0o600)


def _apply_exits(ctx):
    from .gw import exits, exitsubs
    pool = [l.strip() for l in (ctx.files.get(F_POOL) or "").splitlines() if l.strip()]
    subs = _json(ctx.files.get(F_SUBS), None)
    _tofu_import(ctx.files.get(F_TOFU))                # before the pool: its rebuild builds the keys with their pins
    doc = {"next": 1, "subs": []}
    if isinstance(subs, dict) and isinstance(subs.get("subs"), list):
        doc = {"next": subs.get("next") or 1, "subs": subs["subs"]}
        if isinstance(subs.get("ids"), dict):
            doc["ids"] = subs["ids"]                    # 3.3.5: the routes of the copy name these ids
    exitsubs.save(doc)                                  # before the pool too: its rebuild (gw apply) reads the ids
    for _ in range(3):
        cur = exits.pool_lines()
        if cur == pool:
            break
        rc, warns, _notes = exitsubs.replace_pool(ctx.runner, cur, pool)    # good news of the keys: the journal
        ctx.warnings += warns
        if rc == 0:
            break
    else:
        raise KaskadError(tr("backup.err.pool_busy"))
    ru = [l.strip() for l in (ctx.files.get(F_POOL_RU) or "").splitlines() if l.strip()]
    for _ in range(3):                                  # T15 Q4: upstream-ru.txt as the copy has it (its legs, no bot)
        cur = exits.pool_lines("ru")
        if cur == ru:
            break
        rc, warns, _notes = exitsubs.write_pool(ctx.runner, "ru", cur, ru, set())
        ctx.warnings += warns
        if rc == 0:
            break
    else:
        raise KaskadError(tr("backup.err.pool_busy"))
    exitsubs.timer_sync(ctx.runner, exitsubs.load())
    if ru:                                              # the Russian keys counted too (REV-s1-q4 П2)
        ctx.log(tr("backup.log.exits_ru", keys=len(pool) + len(ru), ru=len(ru), subs=len(doc["subs"])))
    else:
        ctx.log(tr("backup.log.exits", keys=len(pool), subs=len(doc["subs"])))


def _apply_gateway(ctx):
    """The gateway of this node goes (its protocols uninstalled), the one of the file takes its place: its protocols are
    installed again with their kept records — the same ports when free, the same keys from secrets.json — so what the
    devices were given works again when the address is the same."""
    from .gw import model, ops, rev
    from .rules import rfgate
    g_file = copy.deepcopy(ctx.state.get("gateway") or {})
    _vless_kept_import(g_file, ctx)
    if not g_file:
        if ctx.exact and (state.load().get("gateway") or {}):
            ctx.say(tr("backup.say.gateway_purge"))
            res = ops.purge(ctx.runner, users=True)
            ctx.warnings += [w for w in res.get("warnings") or [] if isinstance(w, str)]
        return
    sec_doc = _json(ctx.files.get(F_SECRETS), {}) or {}
    values = dict(sec_doc.get("values") or {})
    st0 = state.load()
    if _engine(st0) != "nft":
        raise UnsupportedError(tr("backup.err.gateway_need_nft"))
    if model.enabled(st0):
        ctx.say(tr("backup.say.gateway_off"))
        ops.disable(ctx.runner)
    for kind in model.installed_kinds(state.load()):
        ctx.say(tr("backup.say.inbound_remove", kind=_inbound_titles([kind])[0]))
        ops.inbound_uninstall(ctx.runner, kind)
    was_on = bool(g_file.get("enabled"))
    inbounds = dict((k, v) for k, v in (g_file.get("inbounds") or {}).items()
                    if k in model.INBOUND_KINDS and isinstance(v, dict))
    host, auto = _copy_host(g_file.get("host")), g_file.get("host_auto", True)
    if not host:
        auto = True                                         # nothing usable in the copy: found anew, as on a new node
    if ctx.addr.get("changed") and ctx.addr.get("here"):
        host, auto = ctx.addr["here"], True                 # an IP of the old server: the new one's own
        here_sec = secrets.load()
        for k in NODE_SECRETS:                              # the certificate of the node is of its address
            if k in here_sec:
                values[k] = here_sec[k]
            else:
                values.pop(k, None)
    ever_on = was_on or bool(g_file.get("enabled_at")) or bool((g_file.get("net") or {}).get("base")
                                                                 if isinstance(g_file.get("net"), dict) else False)
    for k in NODE_GW_KEYS:
        g_file.pop(k, None)
    if not isinstance(g_file.get("exit_policy"), dict) and ever_on:
        # stage 1 (T7, SPEC-stage1-exits §1.3): a copy of 3.3.x whose gateway was switched on (``enabled``,
        # ``enabled_at``, the network ``net.base`` of ``gw enable``) has no policy — it comes as «Как раньше», never as
        # the «Стандарт» this node's generation would give it at ``gw enable``. A gateway never switched on (a node of
        # stage 1 before its first enable, a copy of 3.3.x with people only) has no behaviour to keep: no policy written
        # — ``ensure_initial`` chooses at ``gw enable`` by this node's generation (проверка T7, З-3)
        from .gw import policy as gpol
        g_file["exit_policy"] = gpol.default_doc("compat")
    g_file["inbounds"] = {}
    g_file["enabled"] = False
    g_file["host"], g_file["host_auto"] = host, bool(auto)
    if not ctx.same_node:
        # ⚠РФ (rf §3.2): the consent was given by another person on another server — it does not come; «the migration
        # is done» is set, so a «Россия напрямую» of the copy waits for the tick here (mode need)
        g_file.pop("rf_consent", None)
        g_file.pop("rf_legacy", None)
        g_file["rf_gate"] = 1
        rules = g_file.get("rules") if isinstance(g_file.get("rules"), dict) else {}
        if rules.get("default") not in model.DEFAULT_PROFILES:
            # a copy without the default of its node is of 3.3.x: there the default was «Россия напрямую»
            # (RULES_DEFAULTS) — written explicitly, else this node's generation would choose another profile
            g_file["rules"] = dict(rules, default=model.RULES_DEFAULTS["default"])
    _bench_marks_of_this_machine(g_file)
    _tariff_of_this_machine(ctx, g_file)
    with state.locked():
        st = state.load()
        _geo_of_this_machine(g_file, st)
        _engine_of_this_machine(g_file, st)
        _addresses_of_this_machine(g_file, st, ctx.same_node or bool(ctx.addr.get("same")))
        new = copy.deepcopy(st)
        new["gateway"] = g_file
        for kind, rec in inbounds.items():
            if kind == "vless":
                rec = _vless_import(rec, ctx)
            model.keep_inbound(new, kind, rec)
        rfgate.check(st, new, refuse=False)           # never refuses an import: the mode need, the line below
        if rfgate.mode(new) == "need":                # chosen and not applied — also when the points are the node's own
            ctx.notes.append(tr("rules.rf.import_need"))
        # the version of the exit policy is never the copy's: max(this node's, the copy's) + 1 — a form opened before the
        # restore gets 409 (SPEC-stage1-exits §3.1, T1)
        rev.bump(new, before=st)
        state.save(new)
        store = secrets.Store(values)
        store.dirty = True
        store.deleted = set(k for k in secrets.load() if k not in values)
        store.save()
        # T7: the zone and the counters — under the same lock (REV-s1-t3 З5), AFTER the secrets: a failed write of them
        # (a full disk) never leaves the people of the copy without their keys — a warning, the rest restored (проверка
        # безопасности T7, З-2)
        try:
            _gateway_files(ctx)
        except OSError as e:
            ctx.warnings.append(tr("backup.warn.gateway_files", error=e.strerror or type(e).__name__))
    _bench_timer(ctx)
    if was_on:
        ctx.say(tr("backup.say.gateway_on"))
        base = ((g_file.get("net") or {}).get("base")) or None
        # an automatic IP of this very server (``same``: the machine has it) stays — never detected again here: after
        # the hoster made a new address primary (T-mip-lab Н6) that would move the devices to it without a word
        # (PLAN-multi-ip REV С-1, решение 4)
        keep = bool(auto) and bool(ctx.addr.get("same")) and _is_ip(host)
        # F6: the network of the copy with its devices — as before (rebase_ok; a change by hand is «Сеть шлюза» in the
        # panel, gw enable --base refuses it)
        res = ops.enable(ctx.runner, base=base, host=host if keep or not auto else None, host_auto=keep,
                         rebase_ok=True)
        ctx.warnings += [w for w in res.get("warnings") or [] if isinstance(w, str)]
    moved = []
    for kind in model.INBOUND_KINDS:
        if kind not in inbounds:
            continue
        ctx.say(tr("backup.say.inbound_install", kind=_inbound_titles([kind])[0],
                   keys=tr("backup.say.keys_previous") if ctx.exact else tr("backup.say.keys_copy")))
        res = ops.inbound_install(ctx.runner, kind)
        prev = res.get("previous") or {}
        if prev.get("changed"):
            moved.append((kind, "%s: %s" % (_inbound_titles([kind])[0], ", ".join(prev["changed"]))))
        ctx.warnings += [w for w in res.get("warnings") or [] if isinstance(w, str)]
        if inbounds[kind].get("enabled") is False:
            ops.inbound_enable(ctx.runner, kind, False)
    ctx.moved = moved
    _moved_warning(ctx)
    ctx.log(tr("backup.log.gateway", users=len(g_file.get("users") or []), devices=len(g_file.get("devices") or []),
               inbounds=", ".join(_inbound_titles(sorted(inbounds))) or "—"))


def _moved_warning(ctx, back=None):
    """The line «сменились настройки протоколов (…) — выдайте настройки заново» of the gateway part in ``warnings``;
    ``back`` — a kind whose links came back as they were since (VLESS on TLS again with the site of this server,
    ``_site_back``, Н-2): said again without it, or taken away when nothing else moved."""
    if back:
        ctx.moved = [m for m in ctx.moved if m[0] != back]
    new = tr("backup.warn.moved", changes="; ".join(t for _, t in ctx.moved)) if ctx.moved else None
    old = ctx.moved_text
    if old is not None and old in ctx.warnings:
        i = ctx.warnings.index(old)
        if new is None:
            del ctx.warnings[i]
        else:
            ctx.warnings[i] = new
    elif new is not None:
        ctx.warnings.append(new)
    ctx.moved_text = new


def _bench_marks_of_this_machine(g_file):
    """The check of the server (SS §2.1, NS-S4; B11): the marks of the first manual check and of the consent belong to
    the machine (``mid``); a copy of another machine comes without them — its schedule is not armed here until the
    first manual check and the consent HERE (the consent of another person never arms a timer here)."""
    from .bench import machine
    hm = g_file.get("hostmon") if isinstance(g_file.get("hostmon"), dict) else None
    b = hm.get("bench") if hm is not None and isinstance(hm.get("bench"), dict) else None
    if b is None:
        return
    here = machine.mid()
    if not (here and machine.same(b.get("mid"), here)):
        for k in machine.MARKS + ("mid",):
            b.pop(k, None)


def _tariff_of_this_machine(ctx, g_file):
    """The tariff of the server (C1; REV-ns-c1 З-1, решение координатора 19.10) belongs to the machine: its ``mid`` is
    written with it (``host_tariff.apply``). A copy of another machine — another or no machine-id here, a tariff
    without ``mid`` — comes without it: the port of another server would feed the health rule «port» (C7) and its
    traffic the price of the schedule (B10). A line in the journal and a note; whether the copy has ``hostmon.bench``
    or not does not matter."""
    if not _tariff_foreign(g_file):
        return
    g_file["hostmon"].pop("tariff", None)
    ctx.log(tr("backup.log.tariff_dropped"))
    ctx.notes.append(tr("backup.note.tariff_dropped"))


def _tariff_foreign(g_file):
    """The copy has a tariff that is not of this machine (``_tariff_of_this_machine`` drops it) — the import and its
    preview say the same (T-3377 Н-13: the preview was silent, the line came only after «--yes»)."""
    from .bench import machine
    hm = g_file.get("hostmon") if isinstance(g_file, dict) and isinstance(g_file.get("hostmon"), dict) else None
    if hm is None or "tariff" not in hm:
        return False
    t = hm.get("tariff")
    here = machine.mid()
    return not (here and isinstance(t, dict) and machine.same(t.get("mid"), here))


def _geo_of_this_machine(g_file, st_now):
    """The country of the node (``hostmon.geo``, the coordinator 19.10) belongs to the machine: the copy never brings
    it, whatever its machine-id (SEC-ns-join З-1, the coordinator 19.10) — the record of this machine in the state as it
    is now stays, or none (``bench.geo.restore``; under the lock of the state). The collector finds it again by the next
    geo pass of the exits, the check of the server by its delays."""
    from .bench import geo as bgeo
    bgeo.restore(g_file, st_now)


def _engine_of_this_machine(g_file, st_now):
    """The engine of AmneziaWG (``gateway.awg_engine``: the choice, who made it, the reason, «pending») belongs to the
    machine (NODE_GW_KEYS): the copy never brings it — the record of this machine as it is now stays (under the lock of
    the state), through an import, «Вернуть как было» and the rollback alike. Before (T-s1fin, было до) it was gone:
    ``null`` — «a node before 3.3, not asked: the program» while the module ran, and a node on the program by choice
    could be put on the module by the first AmneziaWG inbound installed again (``awgengine.first``)."""
    rec = (st_now.get("gateway") or {}).get("awg_engine")
    if isinstance(rec, dict):
        g_file["awg_engine"] = copy.deepcopy(rec)


def _addresses_of_this_machine(g_file, st_now, same):
    """The addresses added by hand and the addresses the inbounds take connections on (``gateway.addresses`` —
    ``manual``, ``in``; PLAN-multi-ip §3), the addresses of the exits (``exits[id].src``, Ш4) belong to the machine: a
    copy of this very server (``backup undo``, or the copy's IP for clients is one of this machine's — ``_address``)
    brings them back; a copy of another server never does — its addresses are not here, and a manual one would let
    ``gw host`` take it without a word. The record of this machine as it is now stays (or none)."""
    if same:
        return
    rec = (st_now.get("gateway") or {}).get("addresses")
    if isinstance(rec, dict):
        g_file["addresses"] = copy.deepcopy(rec)
    else:
        g_file.pop("addresses", None)
    # Ш4: the address an exit is reached from is an address of the machine too — the copy's go, this machine's stay
    # (an exit of the copy that is an exit here keeps the address chosen here; ``in`` came with ``addresses`` above)
    now = (st_now.get("gateway") or {}).get("exits") or {}
    for eid, r in list((g_file.get("exits") or {}).items()):
        if not isinstance(r, dict):
            continue
        r.pop("src", None)
        mine = now.get(eid) if isinstance(now.get(eid), dict) else {}
        if mine.get("src"):
            r["src"] = mine["src"]
        if not r:
            g_file["exits"].pop(eid)


def _bench_timer(ctx):
    """After the restore of ``gateway`` (§2.4): the timer of the schedule as the restored state wants it (removed when
    not armed). A failing systemctl — a warning, the restore goes on."""
    from .bench import sched_unit
    try:
        r = sched_unit.apply(ctx.runner, state.load())
    except Exception as e:                           # noqa: BLE001 — the timer never stops a restore
        r = {"applied": False, "why": type(e).__name__}
    if not r.get("applied"):
        ctx.warnings.append(tr("bench.log.sched_not_applied", why=r.get("why") or "-"))


def _gateway_files(ctx):
    """The zone of the node (KASKAD_TZ) and traffic.json of the copy (stage 1, T7). Under the state lock the caller
    holds — the collector counts the traffic under it and reads the file again when another writer changed it (its
    signature, ``collect._traffic_doc``), so no counter of the copy is overwritten from its memory. A copy without them
    leaves this node's as they are; a zone this node has no file of — a warning."""
    from . import tz
    zone = config_values(ctx.files.get(F_CONFIG), GATEWAY_KEYS).get("KASKAD_TZ")
    if zone:
        try:
            tz.set_name(zone)
        except ValueError as e:
            ctx.warnings.append(str(e))
    text = ctx.files.get(F_TRAFFIC)
    if text and isinstance(_json(text), dict):
        util.ensure_dir(os.path.dirname(paths.traffic_json()), 0o700)
        util.atomic_write(paths.traffic_json(), text, 0o600)


def _vless_import(rec, ctx):
    """The VLESS record of a copy (SPEC-vless-tls-default §5.1, REV-vless-sh1 З-4): ``tls_error`` and ``mode_pending``
    are of the other moment — not taken; the consent to Reality comes only when it names the domain this node has
    after the import (the same owner); else it goes and a choice of Reality becomes «no choice» (``want`` "tls", §3.3):
    Reality with a domain is never had without a consent to that domain (Н2)."""
    from .site import model as smodel
    rec = copy.deepcopy(rec)
    rec.pop("tls_error", None)
    rec.pop("mode_pending", None)
    dom = _domain_after(ctx)
    c = rec.get("reality_consent")
    if c is not None and not (isinstance(c, dict) and dom and str(c.get("domain") or "") == dom):
        rec.pop("reality_consent", None)
        if rec.get("want") == "reality":
            rec["want"] = "tls"
            ctx.notes.append(tr("backup.note.vless_consent", domain=str((c or {}).get("domain") or "—")
                                if isinstance(c, dict) else "—"))
    return rec


def _vless_consent_gone(fst, parts, st, exact):
    """``preview``: a choice of Reality in the copy whose consent ``_vless_import`` drops (it names another domain than
    this node has after the import) -> the domain of that consent ("" — none named), else None."""
    rec = (((fst.get("gateway") or {}).get("inbounds") or {}).get("vless")) if isinstance(fst, dict) else None
    if not isinstance(rec, dict) or rec.get("want") != "reality" or rec.get("reality_consent") is None:
        return None
    from .site import model as smodel
    dom = smodel.domain_of(st)
    if "site" in parts:
        d = str((fst.get("site") or {}).get("domain") or "")
        if d or exact:
            dom = d
    c = rec["reality_consent"]
    if isinstance(c, dict) and dom and str(c.get("domain") or "") == dom:
        return None
    return str(c.get("domain") or "") if isinstance(c, dict) else ""


def _domain_after(ctx):
    """The domain this node has after the import (SEC-vless-sh12 №2а): the copy's only when its part «site» is imported
    (a return — ``exact`` — makes an empty one empty); otherwise the node keeps its own."""
    from .site import model as smodel
    if "site" in ctx.parts:
        d = str((ctx.state.get("site") or {}).get("domain") or "")
        if d or ctx.exact:
            return d
    return smodel.domain_of(state.load())


_VLESS_KEPT_DROP = ("want", "reality_consent", "mode_pending", "tls_error")


def _vless_kept_import(g_file, ctx):
    """The kept record of a removed VLESS in a copy (SEC-vless-sh12 №2б): the choice, the consent, a transition and its
    error are of the other moment and node — never taken (``gw inbound install`` would pick them up past
    ``_vless_import``); the record installed later follows this node's site like one of 3.3.7. A snapshot of this very
    node (``same_node``: the undo of an import, ``backup undo``) comes back whole (REV-vless-sh2 заметно 2): only the
    transition and its error go, the choice and the consent by the rule of ``_vless_import`` (the domain after)."""
    kept = g_file.get("removed_inbounds") if isinstance(g_file, dict) else None
    rec = kept.get("vless") if isinstance(kept, dict) else None
    if not isinstance(rec, dict):
        return
    if ctx.same_node:
        kept["vless"] = _vless_import(rec, ctx)
        return
    for k in _VLESS_KEPT_DROP:
        rec.pop(k, None)


def _apply_site(ctx):
    from .site import model as smodel
    from .site import ops as sops
    s = ctx.state.get("site") or {}
    d = str(s.get("domain") or "")
    if not d:
        if ctx.exact and smodel.domain_of(state.load()):
            res = sops.clear(ctx.runner, yes=True, vless_auto=True)
            ctx.warnings += [w for w in res.get("warnings") or [] if isinstance(w, str)]
        return
    res = sops.set_domain(ctx.runner, d, keep_working=False)       # the copy restores the node exactly
    ctx.warnings += [w for w in res.get("warnings") or [] if isinstance(w, str)]
    if s.get("acme_email"):
        try:
            sops.set_email(ctx.runner, s["acme_email"])
        except KaskadError as e:
            ctx.warnings.append(tr("backup.warn.acme_email", error=e))
    tid, tsrc = str(s.get("template_id") or ""), str(s.get("template_source") or "")

    def put(cur):
        if tid:
            cur["template_id"], cur["template_source"] = tid, tsrc
    sops._save_site(put)
    if ctx.same_node:
        _site_back(ctx, d, s)
        _use_back(ctx, d, s)
    # notes only for what is not here after the import (3.3.0 T-M6 №19): on another server — the certificate, the site,
    # TLS of VLESS; ``backup undo`` on the same server brings them back (``_site_back``) — a note only for what did not
    # come back, in the words of this server (Н-2: not «on the old server», not «once its A record points here»)
    from .gw import model as gmodel
    st = state.load()
    here = smodel.peek(st)
    cert_here = here["cert"] == "letsencrypt" and smodel.active(st) is not None
    if not cert_here:
        ctx.notes.append(tr("backup.note.cert_back" if ctx.same_node else "backup.note.cert", domain=d,
                            site=tr("backup.note.cert_site") if s.get("mode") == "site" else ""))
    elif s.get("mode") == "site" and here["mode"] != "site":
        ctx.notes.append(tr("backup.note.site_mode", domain=d))
    vl = ((ctx.state.get("gateway") or {}).get("inbounds") or {}).get("vless")
    vl_here = gmodel.inbound(st, "vless") or {}
    if isinstance(vl, dict) and vl.get("security") == "tls" and vl_here.get("security") != "tls":
        ctx.notes.append(tr("backup.note.vless_tls_back" if ctx.same_node else "backup.note.vless_tls", domain=d))
    if ctx.same_node:
        use_here = smodel.peek(st)["use"]
        for k in _use_of(s):
            if not use_here[k]:
                ctx.notes.append(tr("backup.note.use_back", title=sops.TITLES[k], domain=d))


def _use_of(s):
    """The kinds the part ``site`` says were on the certificate of the domain (``v`` >= 2; an older part — none)."""
    from .site import model as smodel
    v, use = s.get("v"), s.get("use")
    if not isinstance(v, int) or isinstance(v, bool) or v < 2 or not isinstance(use, dict):
        return []
    return [k for k in smodel.USE_KINDS if use.get(k) is True]


def _use_back(ctx, d, s):
    """Н-2, остаток (R-n2.md): a snapshot of this very server — SSTP and OpenConnect back on the certificate of the
    domain ``d`` when they were on it (``use`` of the part, SITE_V 2) and the certificate is here again (``_site_back``),
    the way ``site.ops._switch_back`` brings them back. Their devices get what they had before the import: the line
    «give the settings again» of ``site.use`` is not said. Best effort: a failure — a warning; what did not come back —
    a note of ``_apply_site``. An older snapshot (no ``v``) — nothing."""
    from .site import model as smodel
    from .site import ops as sops
    for k in _use_of(s):
        st = state.load()
        here = smodel.peek(st)
        if here["domain"] != d or here["use"][k] or here["cert"] != "letsencrypt" or smodel.active(st) is None:
            continue
        try:
            res = sops.use(ctx.runner, k, True)
        except Exception as e:                          # noqa: BLE001 — the note «turn it on» says the rest
            ctx.warnings.append(tr("backup.warn.use_back", title=sops.TITLES[k], domain=d,
                                   error=util.redact(str(e)) or type(e).__name__))
            continue
        ctx.warnings += [w for w in res.get("warnings") or [] if isinstance(w, str)]
        with util.journal_lang():
            ctx.log(tr("backup.log.use_back", title=sops.TITLES[k], domain=d))


def _site_back(ctx, d, s):
    """Н-2 (T-3379): a snapshot of this very server (``backup undo``, the rollback of a failed import) — the domain
    ``d`` of the snapshot whose certificate ``set_domain(keep_working=False)`` had taken away (an import of another
    domain clears the site, the certificate and TLS of VLESS, and the domain set again starts without a certificate)
    gets them back as they were, the way ``site.ops._switch_back`` brings an old domain back: the certificate of
    Let's Encrypt still on disk (not expired, for ``d``, of the same kind as the snapshot's — production or staging)
    handed out again, the site switched on again when it was on — VLESS back on TLS with it by its record (the choice
    of the snapshot; ``keep_site``: TLS refused — the site on all the same, VLESS on Reality next to it, said).
    Nothing is issued (no request to Let's Encrypt). Best effort: a failure is a warning, the notes of ``_apply_site``
    name what did not come back. A copy of another server never takes a certificate found on this disk — it does not
    come here (``same_node`` False)."""
    from .site import certinfo
    from .site import model as smodel
    from .site import ops as sops
    st = state.load()
    here = smodel.peek(st)
    if here["domain"] != d:
        return
    if here["cert"] != "letsencrypt" or smodel.active(st) is None:
        live = sops._live(d)
        if live is None or certinfo.expired(live) or not certinfo.covers(live, d) \
                or bool(live["staging"]) != bool(s.get("staging")):
            return
        said = []
        try:
            sops._finish(ctx.runner, d, live["staging"], said)
        except Exception as e:                          # noqa: BLE001 — the rest of the return still goes on
            ctx.warnings.append(tr("backup.warn.cert_back", domain=d, error=util.redact(str(e)) or type(e).__name__))
            return
        # «Hysteria2 moved to the domain's certificate — give its links again» counts from the import's moment: the
        # devices are back to the links they had before it when Hysteria2 is on the certificate of the snapshot again;
        # on another one (the certificate was not handed out before the import) the line stays — never a silent change
        if not _hy2_as_before(ctx):
            ctx.warnings += [w for w in said if isinstance(w, str)]
        with util.journal_lang():
            ctx.log(tr("backup.log.cert_back", domain=d, until=str(live.get("not_after") or "")[:10]))
    if s.get("mode") != "site" or smodel.peek(state.load())["mode"] == "site":
        return
    try:
        on = sops.mode(ctx.runner, "site", yes=True, keep_site=True)
    except Exception as e:                              # noqa: BLE001 — the note «turn the site on» says the rest
        ctx.warnings.append(tr("backup.warn.site_back", domain=d, error=util.redact(str(e)) or type(e).__name__))
        return
    # what the site on said (TLS of VLESS refused — why), without its «give the VLESS links again»: that counts from
    # the import's moment, the devices are back to the links they had before it
    vtext = (on.get("vless") or {}).get("text")
    ctx.warnings += [w for w in on.get("warnings") or [] if isinstance(w, str) and w != vtext]
    if any(k == "vless" for k, _ in ctx.moved) and _vless_links_back(ctx):
        _moved_warning(ctx, back="vless")


def _hy2_as_before(ctx):
    """Hysteria2 of this node (if any) is on the certificate its record in the snapshot names (``cert_mode``: of the
    domain or its own) — its links are those of before the import."""
    from .gw import model as gmodel
    rec = gmodel.inbound(state.load(), "hy2")
    if rec is None:
        return True
    was = ((ctx.state.get("gateway") or {}).get("inbounds") or {}).get("hy2")
    return isinstance(was, dict) and (was.get("cert_mode") or "self") == (rec.get("cert_mode") or "self")


def _vless_links_back(ctx):
    """The links of VLESS on this node are again those of the snapshot's record (``front.link_key``: TLS on the same
    domain — or the same Reality): what the gateway part said moved for VLESS (Reality while the domain had no
    certificate yet) is not so any more."""
    from .gw import model as gmodel
    from .site import front
    rec = ((ctx.state.get("gateway") or {}).get("inbounds") or {}).get("vless")
    st = state.load()
    if not isinstance(rec, dict) or gmodel.inbound(st, "vless") is None:
        return False
    was = copy.deepcopy(st)
    gmodel.set_inbound(was, "vless", copy.deepcopy(rec))
    return front.link_key(was) == front.link_key(st)


_APPLY = {"github": _apply_github, "bot": _apply_bot, "panel": _apply_panel, "mcp": _apply_mcp,
          "forwards": _apply_forwards, "exits": _apply_exits, "gateway": _apply_gateway, "site": _apply_site}


def apply(runner, doc, parts=None, say=None, rollback=True, exact=False, same_node=False, tag=None, agents=False):
    """The import: preview (blockers refuse), a snapshot of this node, the parts in ORDER; a failure — the snapshot
    back (best effort, said). ``exact`` (a rollback, ``undo``): every part of ``parts`` is made as in ``doc`` — empty
    ones too. ``tag`` — the word of the journal: import | undo | rollback (an ``exact`` run without it). ``agents`` —
    the keys of the AI agents of the copy too (``preview``: never by default). -> {"ok", "parts", "backup",
    "warnings", "notes", "address", "text"}."""
    tag = tag or ("rollback" if exact else "import")
    pv = preview(runner, doc, parts, same_node=same_node, agents=agents, exact=exact)
    if pv["blockers"]:
        raise ConflictError(" ".join(pv["blockers"]), data={"preview": pv})
    parts = [r["part"] for r in pv["parts"] if exact or r["action"] != "none"]
    snap = take_snapshot(runner, PARTS) if rollback else None
    # ``exact`` — undo and the rollback: a snapshot of this very server, so the marks ⚠РФ come back as they were
    ctx = _Ctx(runner, doc, pv["address"], say, exact=exact, tag=tag, same_node=same_node or exact, parts=parts)
    done = []
    current = None
    # the words of the run: a return (``exact`` — undo, the rollback) says «Возвращаю» and names only what it really
    # brings back or takes away; an empty part of the copy with nothing here is made silently (T-M6-qs330d №6)
    rows = dict((r["part"], r) for r in pv["parts"])

    def told(p):
        r = rows.get(p) or {}
        return not exact or r.get("action") != "none" or any(v for v in (r.get("here") or {}).values())
    try:
        for p in ORDER:
            if p in parts:
                current = p
                ctx.quiet = not told(p)
                if told(p):
                    ctx.say(tr("backup.say.returning" if exact else "backup.say.moving", title=TITLES[p]))
                _APPLY[p](ctx)
                done.append(p)
    except Exception as e:     # SEC-3372 З-2: ANY failure — rolled back (a copy of an unexpected shape: «forwards»: ["x"]
        # failed with AttributeError past the rollback, the bot token and the keys of the copy stayed on the node)
        if isinstance(e, (KaskadError, OSError, ValueError)):          # a write that failed (disk, encoding), a refusal
            why = util.redact(str(e) or type(e).__name__)
        else:
            why = tr("backup.err.shape", kind=type(e).__name__)
        with util.journal_lang():
            util.log_action(tr("backup.log.failed", tag=tag, part=SHORT.get(current, current),
                               why=why if isinstance(e, (KaskadError, OSError, ValueError))
                               else "%s: %s" % (type(e).__name__, util.redact(str(e))[:200])))
        back = ""
        if snap:
            try:
                ctx.say(tr("backup.say.rollback"))
                r2 = apply(runner, read_snapshot(snap), done + [current], say=say, rollback=False, exact=True)
                back = tr("backup.rollback.done", snap=snap)
                # what the rollback did besides (agents cut off with the keys the import had added — R-333 З2″)
                notes = [n for n in (r2.get("notes") or []) if n]
                if notes:
                    back += " " + tr("backup.rollback.notes", notes="; ".join(notes))
            except Exception as e2:                        # SEC-3372 З-2: said, whatever broke the return
                back = tr("backup.rollback.failed", error=util.redact(str(e2) or type(e2).__name__), snap=snap)
        raise KaskadError(tr("backup.err.failed", part=SHORT.get(current, current), why=why.rstrip("."), back=back),
                          code=getattr(e, "code", None), data={"backup": snap, "part": current, "done": done})
    if exact:
        # a return names what it brought back or took away — the words of «Возвращаю: …» in the console, not every
        # part it went through (T-M6-qs330e №2: «перенесено — … бот, ключи агентов, пробросы» with none of them here);
        # the same in the text (the panel shows it after «Вернуть как было»)
        back = [SHORT[p] for p in done if told(p)]
        with util.journal_lang():
            util.log_action(tr("backup.log.returned", tag=tag, parts=", ".join(SHORT[p] for p in done if told(p))
                               or tr("backup.log.returned_none")))
        text = [tr("backup.text.returned", parts=", ".join(back)) if back else tr("backup.text.returned_none")]
    else:
        with util.journal_lang():
            util.log_action(tr("backup.log.imported", parts=", ".join(SHORT[p] for p in done),
                               snap=tr("backup.log.imported_snap", snap=snap) if snap else ""))
        text = [tr("backup.text.imported", parts=", ".join(SHORT[p] for p in done)) if done
                else tr("backup.text.imported_none")]
    text += ["• %s" % n for n in ctx.notes]
    text += ["! %s" % w for w in pv["warnings"] + ctx.warnings]
    if snap:
        text.append(tr("backup.text.saved", snap=snap))
    # agents_cut — agent connections cut off on the way: the panel shows the result as a warning, not as «all is well»
    return {"ok": True, "parts": done, "backup": snap, "warnings": pv["warnings"] + ctx.warnings,
            "notes": ctx.notes, "agents_cut": ctx.agents_cut, "address": pv["address"], "text": text}


def undo(runner, say=None, path=None):
    """The newest snapshot taken before an import, brought back (every part it holds)."""
    path = path or last_snapshot()
    if not path:
        raise ConflictError(tr("backup.err.no_undo"), data={"gone": True})
    doc = read_snapshot(path)
    pre = take_snapshot(runner, PARTS, reason="backup-undo")     # «undo» is not the last word either
    res = apply(runner, doc, None, say=say, rollback=False, exact=True, same_node=True,     # the copy is of this server
                tag="undo")
    res["backup"] = pre
    res["from"] = path
    util.log_tr("backup.log.undo", path=path)
    return res
