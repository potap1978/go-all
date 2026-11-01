"""The GitHub key of the node (a personal access token): the private repository anten-ka/kaskad-pro gives the
AmneziaWG core (``bin/``) and the updates of gokaskad only with it.

One order of lookup for everybody (install.sh ``kaskad_gh_token``, ``kaskad.gw.inbounds.awg.gh_token``):

    bot          /etc/kaskad/bot_state/system   UPDATE_TOKEN=<key>     menu 8 → 6, the updater, the panel
    config       /etc/kaskad/config             GITHUB_PAT="<key>"     written together with the line above
    env          GH_TOKEN / GITHUB_TOKEN of the environment             how the loaders of the go series pass it
    goinstaller  /opt/go-installer/token (the first line)               goInstaller's own key — not ours to change

``save()`` writes «bot» and «config» the way the menu does when a key worked (``bot_set_state "system"
"UPDATE_TOKEN=…"`` and ``save_config_val GITHUB_PAT …``): the bot's file keeps its other lines, /etc/kaskad/config
keeps every other line and is written atomically, 0600 (bash ``source``s it — a key is only ``[A-Za-z0-9_]``, so
nothing in it can run). ``delete()`` clears both; a key of the environment or of goInstaller stays in use, and
``status()`` says where the key in use comes from.

``check(key)`` asks GitHub for the first byte of ``bin/awg-linux-amd64`` of main, then of test — the request
``fetch_core`` makes, so «работает» means «the core downloads». The result for the key in use is kept in
/etc/kaskad/panel/github.json (0600): ``{"fp": the first 16 hex of its SHA-256, "ok", "reason", "error", "at"}`` —
never the key itself. The key goes nowhere else: not to the log, not to an answer, not to argv (only to the
``Authorization`` header of the request to GitHub).
"""

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import paths, util
from .i18n import tr

KEY_RE = re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_]{20,240}")           # whole string (fullmatch): no "\n" at the end
REPO = os.environ.get("AWG_REPO") or "anten-ka/kaskad-pro"   # the repository of the core (awg.CORE_REPO)
REFS = ("main", "test")                                     # its branches, in the order fetch_core tries them
OURS = ("bot", "config")                 # the sources save() and delete() write
CHECK_TIMEOUT = 10                       # s per request; main, then test — at most two
CHECK_FILE = "bin/awg-linux-amd64"       # the smallest file of the core: what fetch_core downloads
_LAZY = {"BAD_FORMAT": "ghkey.bad_format"}   # BAD_FORMAT: panel.api and setup.steps import it


def __getattr__(name):
    """BAD_FORMAT in the language of the moment (PEP 562): a text of the module is not fixed at import."""
    if name in _LAZY:
        return tr(_LAZY[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def bot_file():
    return os.path.join(paths.bot_state(), "system")


def status_path():
    return os.path.join(paths.panel_dir(), "github.json")


def valid(key):
    return isinstance(key, str) and bool(KEY_RE.fullmatch(key))


def _value(path, name):
    """``NAME=value`` / ``NAME="value"`` of a 2.x file — its first line of ``name`` (bash: ``grep … | head -1``;
    ``save_config_val`` leaves one); '' when there is none."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith(name + "="):
                    v = line.split("=", 1)[1].strip()
                    if len(v) >= 2 and v[0] == v[-1] == '"':
                        v = v[1:-1]
                    return re.sub(r"\\(.)", r"\1", v).strip()
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def _goinstaller():
    try:
        with open(os.environ.get("GOINST_TOKEN_FILE") or "/opt/go-installer/token", encoding="utf-8") as f:
            return "".join(f.readline().split())
    except (OSError, UnicodeDecodeError):
        return ""


def current():
    """(key, source) in the order of install.sh ``kaskad_gh_token``; ('', None) when there is none."""
    for src, get in (("bot", lambda: _value(bot_file(), "UPDATE_TOKEN")),
                     ("config", lambda: _value(paths.config(), "GITHUB_PAT")),
                     ("env", lambda: os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""),
                     ("goinstaller", _goinstaller)):
        key = (get() or "").strip()
        if key:
            return key, src
    return "", None


def fingerprint(key):
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:16]


def _last():
    raw = util.read_text(status_path())
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    return doc if isinstance(doc, dict) else {}


def remember(key, res):
    """Keep the result of a check of ``key`` (its fingerprint, never the key)."""
    doc = {"schema": 1, "fp": fingerprint(key), "ok": bool(res.get("ok")), "reason": str(res.get("reason") or ""),
           "error": util.redact(str(res.get("error") or "")), "at": util.now_iso()}
    util.ensure_dir(paths.panel_dir(), 0o700)
    util.atomic_write(status_path(), json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n", 0o600)


def status():
    """What the panel shows — never the key: ``{"set", "source": bot|config|env|goinstaller|None, "ours", "state":
    none|ok|bad|unchecked, "checked", "error", "repo"}``. ``unchecked`` — set, but not checked by the panel yet (a key
    of the menu, of goInstaller) or GitHub did not answer the last check (``error`` says so)."""
    key, src = current()
    out = {"set": bool(key), "source": src, "ours": src in OURS, "state": "none", "checked": None, "error": "",
           "repo": REPO}
    if not key:
        return out
    out["state"] = "unchecked"
    last = _last()
    if last.get("fp") == fingerprint(key):
        at = last.get("at") if isinstance(last.get("at"), str) else None
        if last.get("ok") is True:
            out["state"], out["checked"] = "ok", at
        else:
            # GitHub answered «no» — checked and bad; it did not answer — still unchecked (no time of a check)
            out["state"] = "bad" if last.get("reason") == "bad" else "unchecked"
            out["checked"] = at if out["state"] == "bad" else None
            out["error"] = str(last.get("error") or "")
    return out


class _KeyStaysHome(urllib.request.HTTPRedirectHandler):
    """urllib carries every header of a request over a redirect, «Authorization» too: the key would go to whatever
    host GitHub (or something in between) redirects to. A redirect to another host goes without it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            new.remove_header("Authorization")
        return new


_OPENER = urllib.request.build_opener(_KeyStaysHome())


def open_github(req, timeout):
    """A request with the key to GitHub (the check here, the core in ``awg.fetch_core``): redirects to another host go
    without the key."""
    return _OPENER.open(req, timeout=timeout)


def check(key, timeout=CHECK_TIMEOUT):
    """Does GitHub give the AmneziaWG core with ``key``? -> ``{"ok": True, "ref"}`` or ``{"ok": False, "reason":
    "bad" | "net", "error": <Russian text>}`` — «bad»: GitHub refused the key (401/403) or does not show the
    repository with it (404 on every branch); «net»: no answer (the key may be fine)."""
    for ref in REFS:
        url = "https://raw.githubusercontent.com/%s/%s/%s" % (REPO, ref, CHECK_FILE)
        req = urllib.request.Request(url, headers={"Authorization": "token " + key, "User-Agent": "kaskad",
                                                   "Range": "bytes=0-0"})
        try:
            with open_github(req, timeout) as r:
                r.read(1)
            return {"ok": True, "ref": ref}
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            if code == 404:
                continue
            if code in (401, 403):
                return {"ok": False, "reason": "bad",
                        "error": tr("ghkey.refused", code=code)}
            return {"ok": False, "reason": "net", "error": tr("ghkey.http", code=code)}
        except (urllib.error.URLError, OSError, ValueError):
            return {"ok": False, "reason": "net", "error": tr("ghkey.net")}
    return {"ok": False, "reason": "bad", "error": tr("ghkey.no_repo", repo=REPO)}


def _write_bot(key):
    """``bot_set_state "system" "UPDATE_TOKEN=<key>"``, keeping the other lines of the file; ``key`` None — the line
    goes (and the file, when nothing else is in it)."""
    p = bot_file()
    lines = [ln for ln in (util.read_text(p) or "").splitlines() if ln.strip() and not ln.startswith("UPDATE_TOKEN=")]
    if key:
        lines.insert(0, "UPDATE_TOKEN=%s" % key)
    if lines:
        util.ensure_dir(paths.bot_state(), 0o700)
        util.atomic_write(p, "\n".join(lines) + "\n", 0o600)
    elif os.path.exists(p):
        os.unlink(p)


def save(key, res=None):
    """Write ``key`` where the menu writes a key that works (bot_state/system and GITHUB_PAT of /etc/kaskad/config);
    ``res`` — its check, kept for ``status()``. ValueError for anything but a GitHub key (it goes into a file bash
    sources)."""
    if not valid(key):
        raise ValueError("not a GitHub key")
    from .bot import config as bconf
    _write_bot(key)
    bconf.save_value("GITHUB_PAT", key)
    if res is not None:
        remember(key, res)


def delete():
    """Clear our key (bot_state/system and GITHUB_PAT); a key of the environment or of goInstaller stays in use.
    -> True when there was one of ours."""
    from .bot import config as bconf
    had_bot, had_conf = bool(_value(bot_file(), "UPDATE_TOKEN")), bool(_value(paths.config(), "GITHUB_PAT"))
    _write_bot(None)
    if had_conf:
        bconf.save_value("GITHUB_PAT", "")
    return had_bot or had_conf
