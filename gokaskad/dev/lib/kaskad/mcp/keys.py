"""SSH keys of AI agents: a public key in /root/.ssh/authorized_keys that runs only ``kaskad-mcp`` (docs/k3/SPEC-mcp.md §5).

    kaskadctl mcp key add [--label L] [--host H]   the public key from STDIN: checked, then one line is appended:
                                                   ``<OPTIONS> <type> <base64> kaskad-mcp-agent[:<label>]``
    kaskadctl mcp key list [--host H]              the agents' keys (fingerprint, type, label) and how to connect
    kaskadctl mcp key del <SHA256:…>               only OUR line with that fingerprint goes; the rest of the file stays

OPTIONS force the command and forbid the rest: ``restrict`` (OpenSSH 7.2+: no pty, no forwarding of any kind, no
~/.ssh/rc) and the same one by one for an older sshd. With such a key an agent gets the MCP server and nothing else,
even if the machine of the client is taken over. The MCP catalog has none of these commands: an agent never grants
a key to itself (lib/kaskad/mcp/actions.json, hidden).

What is refused: anything that is not ONE public key line of a known type with a well-formed blob (a private key, a
line with options of its own — ``command=``, ``from=``, ``environment=`` …, garbage, several keys), a DSA key, an RSA
key shorter than 2048 bits, a key that is already in the file — as an agent's (nothing to do) or as a full root
login (sshd takes the first matching line: a restricted copy below it would never act).
"""

import base64
import hashlib
import json
import os
import re
import struct

from .. import errors, paths, state, util
from ..cli import add_json_flag, emit
from ..errors import ConflictError, KaskadError
from ..i18n import tr

MARK = "kaskad-mcp-agent"
OPTIONS = ('restrict,command="%s",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,'
           'no-user-rc' % paths.mcp_command())
TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa",
         "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
LABEL_RE = re.compile(r"^[A-Za-z0-9._@-]{1,32}$")
KEY_MAX = 16 * 1024
RSA_MIN_BITS = 2048
_LINE = re.compile(r"^(\S+)\s+([A-Za-z0-9+/]+={0,3})(?:\s+(.*))?$")
FP_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


def _string(blob, at):
    if at + 4 > len(blob):
        raise ValueError("short")
    (n,) = struct.unpack(">I", blob[at:at + 4])
    if n > len(blob) - at - 4:
        raise ValueError("short")
    return blob[at + 4:at + 4 + n], at + 4 + n


def fingerprint(blob):
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")


def parse_public(text):
    """One public key line -> (type, blob, base64). KaskadError (USAGE) with the reason in words."""
    if not isinstance(text, str) or not text.strip():
        raise KaskadError(tr("mcp.err.empty"),
                          code=errors.USAGE)
    if len(text) > KEY_MAX:
        raise KaskadError(tr("mcp.err.too_long"), code=errors.USAGE)
    if "PRIVATE KEY" in text:
        raise KaskadError(tr("mcp.err.private"), code=errors.USAGE)
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) != 1:
        raise KaskadError(tr("mcp.err.one_line"), code=errors.USAGE)
    first = lines[0].split()[0]
    if first not in TYPES and first != "ssh-dss" and any(t in TYPES for t in lines[0].split()[1:]):
        raise KaskadError(tr("mcp.err.options"), code=errors.USAGE)
    m = _LINE.match(lines[0])
    if not m:
        raise KaskadError(tr("mcp.err.not_ssh"), code=errors.USAGE)
    ktype, b64 = m.group(1), m.group(2)
    if ktype not in TYPES:
        if ktype == "ssh-dss":
            raise KaskadError(tr("mcp.err.dsa"),
                              code=errors.USAGE)
        raise KaskadError(tr("mcp.err.type", type=util.redact(ktype[:40])),
                          code=errors.USAGE)
    try:
        blob = base64.b64decode(b64, validate=True)
        name, at = _string(blob, 0)
        if name.decode("ascii") != ktype:
            raise ValueError("type")
        if ktype == "ssh-rsa":
            _e, at = _string(blob, at)
            n, at = _string(blob, at)
            bits = (len(n.lstrip(b"\x00")) * 8)
            if bits < RSA_MIN_BITS:
                raise KaskadError(tr("mcp.err.rsa_short", bits=int(RSA_MIN_BITS)),
                                  code=errors.USAGE)
        elif ktype in ("ssh-ed25519", "sk-ssh-ed25519@openssh.com"):
            pk, at = _string(blob, at)
            if len(pk) != 32:
                raise ValueError("ed25519 length")
        else:
            _curve, at = _string(blob, at)
            _point, at = _string(blob, at)
    except KaskadError:
        raise
    except (ValueError, UnicodeDecodeError, struct.error):
        raise KaskadError(tr("mcp.err.damaged", type=ktype), code=errors.USAGE)
    return ktype, blob, b64


def _path():
    p = paths.authorized_keys()
    return os.path.realpath(p) if os.path.islink(p) else p


def _read_lines():
    text = util.read_text(_path())
    return [] if not text else text.splitlines()


def _ours(line):
    """(type, blob, label) of OUR line, else None. Ours — the comment is MARK or MARK:<label> and the options start with
    the forced command of kaskad-mcp: a line somebody wrote by hand with the same comment but other options is not."""
    s = line.strip()
    if not s.startswith(OPTIONS + " "):
        return None
    m = _LINE.match(s[len(OPTIONS) + 1:])
    if not m or not (m.group(3) or "").startswith(MARK):
        return None
    comment = m.group(3)
    if comment != MARK and not comment.startswith(MARK + ":"):
        return None
    try:
        blob = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        return None
    return m.group(1), blob, comment[len(MARK) + 1:] if comment != MARK else ""


def _blob_of_any(line):
    """The key blob of any authorized_keys line (with or without options), or None."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    for i, tok in enumerate(s.split()):
        if tok in TYPES or tok == "ssh-dss":
            rest = s.split()[i + 1:]
            if rest:
                try:
                    return base64.b64decode(rest[0], validate=True)
                except ValueError:
                    return None
    return None


def host_guess(host=None):
    """The address of the command for the agent: the domain of the node when Let's Encrypt issued its certificate (the
    domain then surely leads here), else the address of the gateway (IP). One rule before and after a key is added —
    the wizard showed the domain first and the IP after (T-M6-qs330 №13)."""
    if host:
        return host
    try:
        st = state.load()
        from ..site import model as smodel
        s = smodel.peek(st)
        if s["domain"] and s["cert"] == "letsencrypt" and smodel.active(st) is not None:
            return s["domain"]
        from ..gw import model
        h = model.public_host(st)
        if h:
            return h
    except (KaskadError, OSError, ValueError):
        pass
    return tr("mcp.host_placeholder")


def connect(host=None, keyfile="~/.ssh/kaskad_agent"):
    """How a client connects with the agent's key: Claude Code in one command, Claude Desktop — a JSON piece."""
    h = host_guess(host)
    return {"host": h, "keyfile": keyfile,
            # IdentitiesOnly: с многими ключами в ssh-agent ssh перебрал бы их раньше -i (Too many authentication failures)
            "claude_code": "claude mcp add kaskad -- ssh -i %s -o IdentitiesOnly=yes -o BatchMode=yes root@%s kaskad-mcp"
                           % (keyfile, h),
            "claude_desktop": {"mcpServers": {"kaskad": {"command": "ssh", "args": [
                "-i", keyfile, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "root@%s" % h, "kaskad-mcp"]}}}}


def listing(host=None):
    keys = []
    for line in _read_lines():
        o = _ours(line)
        if o:
            keys.append({"fingerprint": fingerprint(o[1]), "type": o[0], "label": o[2]})
    return {"ok": True, "keys": keys, "file": paths.authorized_keys(), "connect": connect(host)}


def add(text, label=None, host=None, dry_run=False):
    if label is not None and not LABEL_RE.match(label):
        raise KaskadError(tr("mcp.err.label"), code=errors.USAGE)
    ktype, blob, b64 = parse_public(text)
    text = None
    fp = fingerprint(blob)
    with state.locked():
        lines = _read_lines()
        for line in lines:
            if _blob_of_any(line) == blob:
                if _ours(line):
                    raise ConflictError(tr("mcp.err.exists", fp=fp), data={"fingerprint": fp, "reason": "exists"})
                raise ConflictError(tr("mcp.err.root_key"), data={"fingerprint": fp, "reason": "root_key"})
        if dry_run:
            return {"ok": True, "dry_run": True, "fingerprint": fp, "type": ktype,
                    "text": tr("mcp.text.fits", type=ktype, fp=fp)}
        comment = MARK + (":" + label if label else "")
        lines.append("%s %s %s %s" % (OPTIONS, ktype, b64, comment))
        d = os.path.dirname(_path())
        if not os.path.isdir(d):
            os.makedirs(d, 0o700)
        util.atomic_write(_path(), "\n".join(lines) + "\n", mode=0o600)
    from . import seen
    seen.note_added(fp)                 # «подключено»: only calls after this moment may be this key's by elimination
    util.log_tr("journal.mcp.key_added_label", fp=fp, label=label) if label else util.log_tr("journal.mcp.key_added", fp=fp)
    return {"ok": True, "fingerprint": fp, "type": ktype, "label": label or "", "connect": connect(host),
            "text": tr("mcp.text.added")}


def end_sessions(fps, runner=None):
    """End the sessions of the removed keys ``fps`` (one look at the logs for all of them), the journal line said.
    {"ended": n, "unsure": bool} — never raises: the keys are gone already, a failure only leaves sessions open."""
    from . import sessions
    fps = sorted(set(fps))
    if not fps:
        return {"ended": 0, "unsure": False}
    if runner is None:
        from ..sysrun import default_runner
        runner = default_runner()
    try:
        cut = sessions.end_for(fps, runner)
    except OSError:
        cut = {"ended": 0, "unsure": False}
    if cut["ended"]:
        util.log_tr("journal.mcp.sessions_ended" if len(fps) == 1 else "journal.mcp.sessions_ended_many",
                    fp=", ".join(fps), n=cut["ended"])
    return cut


def delete(fp, runner=None, end=True):
    """Remove the key and end the sessions it opened (kaskad.mcp.sessions): «удалён» means the agent is cut off now,
    not when its connection drops some day. ``end=False`` — the caller ends them itself, once for several keys
    (``end_sessions``: the rollback of an import, kaskad.backup)."""
    if not FP_RE.match(str(fp or "")):
        raise KaskadError(tr("mcp.err.fp"), code=errors.USAGE)
    with state.locked():
        lines = _read_lines()
        keep, gone = [], 0
        for line in lines:
            o = _ours(line)
            if o and fingerprint(o[1]) == fp:
                gone += 1
                continue
            keep.append(line)
        if not gone:
            raise KaskadError(tr("mcp.err.no_key", fp=fp),
                              data={"gone": True})
        util.atomic_write(_path(), "\n".join(keep) + ("\n" if keep else ""), mode=0o600)
    from . import seen
    seen.note_gone(fp)
    util.log_tr("journal.mcp.key_removed", fp=fp)
    cut = end_sessions([fp], runner) if end else {"ended": 0, "unsure": False}
    text = tr("mcp.text.removed_all") if cut["unsure"] else \
        tr("mcp.text.removed_cut") if cut["ended"] else tr("mcp.text.removed")
    return {"ok": True, "fingerprint": fp, "removed": gone, "ended": cut["ended"], "unsure": cut["unsure"],
            "text": text}


# ── kaskadctl mcp key … ───────────────────────────────────────────────────

def _stdin():
    import sys
    try:
        if sys.stdin.isatty():
            sys.stderr.write(tr("mcp.cli.paste") + "\n")
    except (AttributeError, ValueError):
        pass
    return sys.stdin.read(KEY_MAX + 1)


def _connect_lines(c):
    return [tr("mcp.cli.claude_code"), "  " + c["claude_code"],
            "Claude Desktop (claude_desktop_config.json):",
            '  "kaskad": ' + json.dumps(c["claude_desktop"]["mcpServers"]["kaskad"], ensure_ascii=False)]


def cmd_add(args):
    res = add(_stdin(), label=args.label, host=args.host, dry_run=args.dry_run)
    if res.get("dry_run"):
        emit(args, res, res["text"])
        return errors.OK
    emit(args, res, [res["text"], tr("mcp.cli.fingerprint", fp=res["fingerprint"])] + _connect_lines(res["connect"]))
    return errors.OK


def cmd_list(args):
    res = listing(host=args.host)
    lines = [tr("mcp.cli.none")] if not res["keys"] else \
        ["%s  %s%s" % (k["fingerprint"], k["type"], ("  " + k["label"]) if k["label"] else "") for k in res["keys"]]
    emit(args, res, lines + [""] + _connect_lines(res["connect"]))
    return errors.OK


def cmd_del(args):
    res = delete(args.fingerprint)
    emit(args, res, res["text"])
    return errors.OK


def register(subparsers):
    p = subparsers.add_parser("mcp", help=tr("mcp.help.mcp"))
    sub = p.add_subparsers(dest="mcp_cmd", metavar=tr("mcp.help.command"))
    sub.required = True
    pk = sub.add_parser("key", help=tr("mcp.help.key"))
    ksub = pk.add_subparsers(dest="key_cmd", metavar=tr("mcp.help.command"))
    ksub.required = True
    p1 = add_json_flag(ksub.add_parser("add", help=tr("mcp.help.add")))
    p1.add_argument("--label", default=None, help=tr("mcp.help.label"))
    p1.add_argument("--host", default=None, help=tr("mcp.help.host"))
    p1.add_argument("--dry-run", action="store_true", help=tr("mcp.help.dry_run"))
    p1.set_defaults(func=cmd_add)
    p1 = add_json_flag(ksub.add_parser("list", help=tr("mcp.help.list")))
    p1.add_argument("--host", default=None)
    p1.set_defaults(func=cmd_list)
    p1 = add_json_flag(ksub.add_parser("del", help=tr("mcp.help.del")))
    p1.add_argument("fingerprint")
    p1.set_defaults(func=cmd_del)
