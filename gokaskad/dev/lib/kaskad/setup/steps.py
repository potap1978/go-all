"""The steps of the quick setup (docs/k3/SPEC-setup-api.md): every step is ``kaskadctl setup step <name>`` and the
same function behind ``POST /api/setup/step/<name>`` — the panel and SSH call one thing. Steps only call operations
Kaskad already has (migrate, gw ops, site ops, ghkey, bot config, mcp keys, rules); nothing here changes the system
by itself. A step is repeatable: the second run does not break what the first one did.

Result of a step (one JSON document, never a secret unless the step is ``secret`` — then nothing of it is stored):

    {"ok": bool, "step", "state": ok | partial | error | skipped | offline | need_input | checking | unavailable,
     "text": words for people, "items": [rows of the live check], "data": {...},
     "error": {"code", "text", "action"?, "field"?}?}

Rows: {"item", "title", "state": wait | ok | warn | err | idle, "detail", "since"} (design handoff §5).
Long steps (``long``) run as a job (kaskad.setup.jobs) and report rows while they work.
"""

import ipaddress
import json
import os
import re
import socket
import tempfile
import time

from .. import errors, i18n, paths, state, sysrun, util
from ..errors import KaskadError
from ..site import model as smodel
from . import features, jobs, link, model, product
from ..i18n import fmt_date, tr, trn

STEPS = {}

NET_HOSTS = (("1.1.1.1", 443), ("77.88.8.8", 443), ("8.8.8.8", 443))
CONNECT_STUCK = 120          # s without a handshake — the hints (design §5)
DNS_HINT = 15 * 60           # s of waiting for DNS — the hints

_now = time.time
_net_cache = [0.0, None]

# texts other modules and tests read as attributes (steps.SUB_FOUND_TEXT): the key here, the words in the language of
# the moment on each read (PEP 562 module __getattr__) — never translated at import, the language changes on the fly
_TEXT_KEYS = {"OFFLINE_TEXT": "setup.steps.offline", "SUB_FOUND_TEXT": "setup.steps.subFound",
              "SUB_UNCHECKED_TEXT": "setup.steps.subUnchecked", "EXPIRED": "setup.steps.backupExpired"}


def __getattr__(name):
    key = _TEXT_KEYS.get(name)
    if key is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    return tr(key)


class Step(object):
    def __init__(self, name, stage, fn, long=False, network=False, secret=False, record=True, password=True):
        self.name, self.stage, self.fn = name, stage, fn
        self.long, self.network, self.secret, self.record, self.password = long, network, secret, record, password

    def doc(self):
        return {"name": self.name, "stage": self.stage, "long": self.long, "network": self.network,
                "secret": self.secret}


def step(name, stage, **kw):
    def deco(fn):
        STEPS[name] = Step(name, stage, fn, **kw)
        return fn
    return deco


# refusals worded for the command line, said for the page instead (3.3.3: «Взять всё равно — с --force.» means nothing
# in the wizard; it offers another free port itself, and «install again» is a button there)
_PAGE_WORDS = {"gw_ops.err.port_busy": "setup.steps.portBusy"}


_SERVICE = re.compile(r"^[a-z0-9][a-z0-9_.@-]{0,63}$")
# a process -> the units it comes with, when /proc/<pid>/cgroup cannot say (a container, no cgroup): only names whose
# unit is known; a unit counts when its file is on this server, else the command shows the holder (R-333 С1)
_UNIT_OF = {"xl2tpd": ("xl2tpd",), "pptpd": ("pptpd",), "charon": ("strongswan-starter", "strongswan", "ipsec"),
            "charon-systemd": ("strongswan",), "accel-pppd": ("accel-ppp",), "ocserv": ("ocserv",)}
# units that serve more than a VPN (a site, the system, SSH): «disable» would take them away too — no command to stop
# them, the command that shows the holder instead; every «systemd-*» unit counts as the system (R-333 П-11)
_UNIT_KEEP = ("nginx", "apache2", "httpd", "caddy", "haproxy", "lighttpd", "ssh", "sshd", "systemd-resolved",
              "systemd-networkd", "docker", "containerd", "dbus", "init", "systemd")
_UNIT_DIRS = ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system")


def _unit_of_pid(pid):
    """The systemd service of a process by /proc/<pid>/cgroup (v2 «0::/system.slice/xl2tpd.service», v1
    «1:name=systemd:/system.slice/…»): the last «*.service» of a system slice; None — not a service (a user session,
    a scope) or unknown."""
    text = util.read_text(paths.P("/proc/%d/cgroup" % int(pid))) or ""
    for line in text.splitlines():
        path = line.split(":", 2)[-1]
        if "user.slice" in path:
            return None
        units = [x[:-len(".service")] for x in path.split("/") if x.endswith(".service")]
        if units:
            return units[-1]
    return None


def _unit_file(unit):
    return any(os.path.exists(paths.P(os.path.join(d, unit + ".service"))) for d in _UNIT_DIRS)


def _free_cmd(found, port):
    """The command for SSH beside «port is held by another process» (R-333 С1: built from the data of the conflict —
    the processes and their pids — not from its words): ``("stop", "systemctl disable --now <unit>")`` when exactly one
    service holds the port and it is no shared one (a site, SSH, the system); ``("look", "ss -lntup 'sport = :N'")``
    when a process holds it but its service is unknown or shared; ``("", "")`` — not a process (a forward, another
    protocol of the gateway, Docker, a foreign rule)."""
    lst = [c for c in found if isinstance(c, dict) and c.get("kind") == "listener"]
    if not lst or len(lst) != len([c for c in found if isinstance(c, dict)]):
        return "", ""
    look = ("look", "ss -lntup 'sport = :%d'" % int(port))
    procs = sorted(set(p for c in lst for p in (c.get("procs") or []) if isinstance(p, str)))
    pids = sorted(set(p for c in lst for p in (c.get("pids") or []) if isinstance(p, int) and not isinstance(p, bool)))
    if not procs and not pids and all(isinstance(c.get("procs"), list) for c in lst):
        # ss saw the socket and no process holds it (WireGuard in the kernel): «ss» would show no program — no command
        # that sends people looking for one (R-333 П-11); no data of the holder at all — the command that shows it
        return "", ""
    if len(procs) != 1:
        return look
    units = set(u for u in (_unit_of_pid(p) for p in pids) if u)
    if not units:
        units = set([u for u in _UNIT_OF.get(procs[0], ()) if _unit_file(u)][:1])      # the first one there
    if len(units) != 1:
        return look
    unit = units.pop()
    if not _SERVICE.match(unit) or unit.split("@")[0] in _UNIT_KEEP or unit.startswith("systemd-"):
        return look
    return "stop", "systemctl disable --now %s" % unit


def _port_busy(e, title):
    """A port of a protocol is held by another program (R-333 П2: «Порт 1701/udp занят: xl2tpd. Освободите его…» —
    which protocol, what to do, where to press, the wizard did not say). -> {"text": «L2TP/IPsec не установлен: порт
    1701/udp занят другой программой — его слушает xl2tpd.», "title", "port", "protos", "who", "cmd", "do": "stop" (the
    command stops the service) | "look" (it shows who holds the port) | ""}; None — not a busy port."""
    a = getattr(e, "args", None) or ()
    s = a[0] if a and isinstance(a[0], i18n.Said) else None
    kw = dict(getattr(s, "kw", None) or {})
    if not kw.get("port"):
        return None
    found = ((getattr(e, "data", None) or {}).get("conflicts") or [])
    try:
        do, cmd = _free_cmd(found, kw["port"])
    except (TypeError, ValueError, OSError):
        do, cmd = "", ""
    who = util.redact(str(kw.get("why") or ""))
    return {"text": tr("setup.steps.portBusyKind", title=title, port=kw["port"], protos=kw.get("protos") or "", why=who),
            "title": title, "port": kw["port"], "protos": kw.get("protos") or "", "who": who, "cmd": cmd, "do": do}


def _said_err(e):
    """The words of a refusal for the page — a row of the progress, ``data.failed``, ``error.text``: the Said it was
    raised with (3.3.3 — the page says it again in its language; a CLI hint put in the page's words), else the text.
    Secrets are taken out either way (util.redact keeps a Said when it hid nothing)."""
    a = getattr(e, "args", None) or ()
    if a and isinstance(a[0], i18n.Said):
        s = a[0]
        k = _PAGE_WORDS.get(getattr(s, "key", None))
        return util.redact(tr(k, **s.kw) if k else s)
    return util.redact(str(e))


class Ctx(object):
    def __init__(self, runner, body, progress, by="cli"):
        self.runner = runner
        self.body = body or {}
        self.progress = progress
        self.by = by

    def say(self, key, st, detail="", title=None):
        if self.progress is not None:
            self.progress.item(key, st, detail, title)

    def str(self, key, required=False, max_len=200):
        v = self.body.get(key)
        if v is None or v == "":
            if required:
                raise KaskadError(tr("setup.steps.fieldRequired", field=key), code=errors.USAGE, data={"field": key})
            return None
        if isinstance(v, bool) or not isinstance(v, (str, int)):
            raise KaskadError(tr("setup.steps.fieldNotString", field=key), code=errors.USAGE, data={"field": key})
        v = str(v).strip()
        if len(v) > max_len:
            raise KaskadError(tr("setup.steps.fieldTooLong", field=key, max=max_len), code=errors.USAGE,
                              data={"field": key})
        return v


def res(state_, text="", items=None, data=None, error=None, keep=None):
    out = {"ok": state_ not in ("error", "offline", "unavailable"), "state": state_, "text": text,
           "items": items or [], "data": data or {}}
    if error:
        out["error"] = error
    if keep:
        out["keep"] = keep
    return out


def fail(text, code="error", action=None, field=None, data=None):
    err = {"code": code, "text": text}
    if action:
        err["action"] = action
    if field:
        err["field"] = field
    return res("error", text, data=data, error=err)


def core_fail(e, action=None):
    data = e.data if isinstance(getattr(e, "data", None), dict) else {}
    code = {errors.CONFLICT: "conflict", errors.LOCKED: "busy", errors.UNSUPPORTED: "unsupported",
            errors.USAGE: "bad_input", errors.ROLLED_BACK: "rolled_back"}.get(getattr(e, "code", None), "error")
    if data.get("reason"):
        code = str(data["reason"])
    return fail(_said_err(e), code=code, action=action, field=data.get("field"),
                data=dict((k, v) for k, v in data.items() if k not in ("field",)))


def online(force=False):
    """Can the node reach the internet (TCP to a few well-known 443s; cached 10 s)."""
    now = _now()
    if not force and _net_cache[1] is not None and now - _net_cache[0] < 10:
        return _net_cache[1]
    ok = False
    for host, port in NET_HOSTS:
        try:
            s = socket.create_connection((host, port), timeout=3)
            s.close()
            ok = True
            break
        except OSError:
            continue
    _net_cache[0], _net_cache[1] = now, ok
    return ok


def run(name, body=None, runner=None, progress=None, by="cli"):
    """Run a step to its end (a job calls this in its process; short steps — the panel itself). -> the result."""
    st = STEPS.get(name)
    if st is None:
        return fail(tr("setup.steps.unknownStep", name=name), code="unknown_step")
    runner = runner or sysrun.default_runner()
    doc = model.load()
    if st.password and not doc["password_set"]:
        return fail(tr("setup.steps.passwordFirst"), code="password_first", action={"stage": "welcome"})
    if st.network and not online():
        text = tr("setup.steps.offline")
        out = res("offline", text, error={"code": "offline", "text": text})
    else:
        ctx = Ctx(runner, body, progress, by)
        try:
            out = st.fn(ctx)
        except KaskadError as e:
            out = core_fail(e)
        except (OSError, ValueError) as e:
            util.log_tr("journal.setup.step_crashed", name=name, error=util.redact(repr(e))[:300])
            out = fail(tr("setup.steps.stepFailed", error=util.redact(str(e))[:300]), code="internal")
    out["step"] = name
    out.setdefault("items", [])
    if progress is not None and progress.rows and not out["items"]:
        out["items"] = [dict(r) for r in progress.rows]
    if st.record and not st.secret:
        try:
            model.update(lambda d: model.record_step(d, name, out))
        except OSError:
            pass
    out.pop("keep", None)
    return out


# ── 0. Вход ────────────────────────────────────────────────────────────────

@step("password", "welcome", password=False, secret=True)
def s_password(c):
    from ..panel import auth
    pw = c.body.get("password")
    try:
        auth.check_new_password(pw)
    except KaskadError as e:
        return fail(tr("setup.steps.passwordWeak", reason=e), code="weak", field="password")
    rec = auth.set_password(pw, login=c.str("login", max_len=32))
    pw = None

    def mut(doc):
        doc["password_set"] = True
        n = link.revoke_all(doc)
        if doc.get("current") == "welcome":
            doc["current"] = product.RAIL[model.mode(doc)][0]
        doc["steps"]["password"] = {"state": "ok", "at": util.now_iso(), "text": tr("setup.steps.passwordSetShort"),
                                    "data": {}}
        return n
    revoked = model.update(mut)
    util.log_tr("journal.setup.password_revoked", n=int(revoked)) if revoked else util.log_tr("journal.setup.password")
    return res("ok", tr("setup.steps.passwordSet"),
               data={"login": rec["login"], "revoked_links": revoked})


# ── 1. Импорт ──────────────────────────────────────────────────────────────

def _others(runner):
    """VPNs of other programs on this server — shown as «не трогаем», never changed."""
    import glob
    out = []
    if runner.which("docker"):
        r = runner.run(["docker", "ps", "--format", "{{.Names}}"], timeout=20)
        names = [n.strip() for n in (r.out if r.ok else "").splitlines() if n.strip()]
        amn = [n for n in names if n.lower().startswith("amnezia")]
        if amn:
            out.append({"id": "amnezia", "title": tr("setup.steps.amneziaTitle"), "move": False,
                        "detail": trn("setup.steps.amneziaDetail", len(amn))})
    if os.path.isdir(paths.P("/usr/local/x-ui")) or os.path.isfile(paths.P("/etc/systemd/system/x-ui.service")):
        out.append({"id": "xui", "title": "3X-UI", "move": False, "detail": tr("setup.steps.xuiDetail")})
    confs = [p for p in glob.glob(paths.P("/etc/wireguard/*.conf")) if not os.path.basename(p).startswith("kgw-")]
    if confs:
        out.append({"id": "wg", "title": "WireGuard", "move": False,
                    "detail": tr("setup.steps.wgDetail",
                                 names=", ".join(os.path.basename(p)[:-5] for p in confs[:4]))})
    for d in ("/opt/goamnezia", "/etc/goamnezia"):
        if os.path.isdir(paths.P(d)):
            out.append({"id": "goamnezia", "title": "goAmnezia Pro", "move": False,
                        "detail": tr("setup.steps.goamneziaDetail")})
            break
    return out


@step("scan", "import")
def s_scan(c):
    from ..migrate import plan as plan_mod, scan as scan_mod
    inv = scan_mod.collect(c.runner)
    pl = plan_mod.build(inv)
    rep = inv.report()
    plan = pl.to_dict()
    n_fwd = len(rep.get("forwards") or [])
    cfg_keys = (rep.get("config") or {}).get("keys") or {}
    found = []
    if rep.get("edition") in ("pro", "pro-legacy", "free") or n_fwd:
        bits_ = [trn("setup.steps.forwardsCount", n_fwd)]
        if cfg_keys.get("BOT_TOKEN"):
            bits_.append(tr("setup.steps.foundBot"))
        eg = (rep.get("egress") or {}).get("keys") or 0
        if eg:
            bits_.append(tr("setup.steps.foundEgress", n=eg))
        mons = len(rep.get("monitors") or [])
        if mons:
            bits_.append(tr("setup.steps.foundMonitors", n=mons))
        ed = {"free": tr("setup.steps.editionFree"), "pro": "Kaskad PRO 2.x", "pro-legacy": "Kaskad PRO 2.0–2.4"}.get(
            rep.get("edition"), "Kaskad")
        found.append({"id": "k2", "title": ed, "detail": " · ".join(bits_), "move": bool(plan.get("import")),
                      "done": not plan.get("import") and state.engine() == "nft"})
    found += _others(c.runner)
    found.append({"id": "awgfile", "title": tr("setup.steps.awgFileTitle"), "move": False, "upload": True,
                  "detail": tr("setup.steps.awgFileDetail")})
    found.append({"id": "backup", "title": tr("setup.steps.backupTitle"), "move": False, "upload": True,
                  "available": features.has("backup", "import"),
                  "detail": tr("setup.steps.backupDetail") if features.has("backup", "import") else features.soon()})
    # «Чистый сервер» — только когда здесь и правда пусто: ни старого Kaskad и чужих VPN, ни людей, устройств,
    # протоколов, выходов, подписок, пробросов, домена, бота (kaskad.backup.here_doc — те же счёты, что у копии).
    # T-M6-qs330 №4: карточка говорила «Переносить нечего» на узле с 9 выходами и людьми.
    try:
        from .. import backup
        here = backup.here_doc(c.runner)
    except (KaskadError, OSError, ValueError) as e:
        util.log_tr("journal.setup.scan_failed", error=util.redact(str(e))[:200])
        here = {"items": [], "empty": False, "text": "", "unknown": True}
    clean = not any(f["id"] == "k2" for f in found) and len([f for f in found if not f.get("upload")]) == 0 and \
        here["empty"]
    summary = plan.get("summary") or {}
    text = tr("setup.steps.cleanServer") if clean else \
        tr("setup.steps.serverHas", what=here["text"]) if here["items"] else tr("setup.steps.serverChecked")
    return res("ok", text,
               data={"found": found, "clean": clean, "here": here, "plan": {"summary": summary, "preview": pl.preview(),
                                                            "imports": len(plan.get("import") or []),
                                                            "blockers": plan.get("blockers") or []},
                     "checked": "Kaskad 2.x, Amnezia, 3X-UI, WireGuard, goAmnezia",
                     "undo": bool((model.load()["answers"].get("import") or {}).get("done"))},
               keep={"clean": clean})


@step("import", "import", long=True)
def s_import(c):
    from ..migrate import apply as apply_mod
    c.say("forwards", "wait", tr("setup.steps.importForwardsWait"), tr("setup.steps.rowForwards"))
    try:
        r = apply_mod.run(c.runner, force=bool(c.body.get("force")))
    except KaskadError as e:
        c.say("forwards", "err", _said_err(e))
        rolled = getattr(e, "code", None) == errors.ROLLED_BACK
        return fail(tr("setup.steps.importRolledBack" if rolled else "setup.steps.importFailed",
                       error=_said_err(e)), code="rolled_back" if rolled else "error")
    n = len(r.get("imported") or [])
    if r.get("result") == "nothing":
        c.say("forwards", "ok", tr("setup.steps.importNothingRow"))
    else:
        c.say("forwards", "ok", trn("setup.steps.importForwardsDone", n))
    c.say("bot", "ok", tr("setup.steps.importBotRow"), tr("setup.steps.rowBot"))
    c.say("egress", "ok", tr("setup.steps.importEgressRow"), tr("setup.steps.rowEgress"))
    warn = [util.redact(w) for w in r.get("warnings") or []]
    for i, w in enumerate(warn):
        c.say("w%d" % i, "warn", w, tr("setup.steps.rowNote"))

    def mut(doc):
        doc["answers"]["import"] = {"done": r.get("result") != "nothing", "at": util.now_iso(), "forwards": n,
                                    "backup": r.get("backup")}
    model.update(mut)
    return res("partial" if warn else "ok", trn("setup.steps.importDone", n) if n else tr("setup.steps.importNothing"),
               data={"imported": n, "backup": r.get("backup"), "warnings": warn, "undo": n > 0})


@step("import-undo", "import", long=True)
def s_import_undo(c):
    from ..migrate import rollback as rb
    if not (model.load()["answers"].get("import") or {}).get("done"):
        return fail(tr("setup.steps.undoNothing"), code="nothing")
    c.say("undo", "wait", tr("setup.steps.undoWait"), tr("setup.steps.rowUndo"))
    r = rb.run(c.runner)
    if not r.get("ok"):
        c.say("undo", "err",
              i18n.join("; ", (util.redact(p) for p in r.get("problems") or [])) or tr("setup.steps.undoFailedRow"))
        return fail(tr("setup.steps.undoFailed", problems=i18n.join("; ", (util.redact(p) for p in r.get("problems") or []))))
    c.say("undo", "ok", tr("setup.steps.undoDoneRow"))
    model.update(lambda d: d["answers"].update({"import": {"done": False, "undone": util.now_iso()}}))
    return res("ok", tr("setup.steps.undoDone"), data={"backup": r.get("backup")})


@step("import-file", "import", long=True, secret=True)
def s_import_file(c):
    kind = c.str("kind") or "awg"
    if kind == "backup":
        return _import_backup(c)
    from ..gw import awgengine
    body = c.body.get("doc")
    if not isinstance(body, dict):
        return fail(tr("setup.steps.awgFileNeeded"), code="bad_input", field="doc")
    c.say("awg", "wait", tr("setup.steps.awgFileWait"), tr("setup.steps.awgFileTitle"))
    fd, tmp = tempfile.mkstemp(prefix="kaskad-awg-import.", dir=jobs.run_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f)
        body = None
        r = awgengine.import_(c.runner, tmp, tr("setup.steps.awgFileName"))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    c.say("awg", "ok", tr("setup.steps.awgFileDoneRow", inbounds=len(r.get("inbounds") or []),
                          users=len(r.get("users") or []), devices=len(r.get("devices") or [])))
    return res("ok", tr("setup.steps.awgFileDone"), data={"inbounds": r.get("inbounds"), "users": r.get("users"),
                                                    "devices": r.get("devices"),
                                                    "warnings": [util.redact(w) for w in r.get("warnings") or []]})


# ── 2. Режим и протоколы ───────────────────────────────────────────────────

@step("mode", "protocols")
def s_mode(c):
    mode = c.str("mode", True, 16)
    if mode not in product.RAIL:
        return fail(tr("setup.steps.modeBad"), code="bad_input", field="mode")

    def mut(doc):
        doc["mode"] = mode
        kinds = product.RAIL[mode]
        # the marks of the other mode's stages stay (T-M6-qs330b №18: classic and back wiped «Протоколы», «Выходы»,
        # «Дополнительно»): the rail shows only the stages of its mode (model.rail), so they are harmless and come back
        cur = doc.get("current")
        if cur in product.MODE_SLOT.values() or (cur not in kinds and cur != "welcome"):
            doc["current"] = product.MODE_SLOT[mode]          # the stage of the mode cards: protocols | forward
    model.update(mut)
    return res("ok", tr("setup.steps.modeNode" if mode == "node" else "setup.steps.modeClassic"),
               data={"mode": mode, "rail": model.rail(model.load())})


def github_state(runner, check=True):
    """The GitHub key without a question (owner 2026-10-09): found and working — nothing to ask; the field is shown
    only when there is no key or it stopped working. The check is one request, the key never in argv."""
    from .. import ghkey
    st = ghkey.status()
    words = {"bot": tr("setup.steps.ghFromBot"), "config": tr("setup.steps.ghFromConfig"),
             "env": tr("setup.steps.ghFromEnv"), "goinstaller": tr("setup.steps.ghFromGoinstaller")}
    out = {"state": st["state"], "source": st["source"], "from": words.get(st["source"] or "", ""), "ask": False,
           "text": ""}
    if st["state"] == "unchecked" and check:
        key, _ = ghkey.current()
        r = ghkey.check(key)
        key = None
        if r.get("reason") != "net":
            ghkey.remember(ghkey.current()[0], r)
        out["state"] = "ok" if r.get("ok") else ("bad" if r.get("reason") == "bad" else "unchecked")
        out["error"] = r.get("error") or ""
    if out["state"] == "ok":
        out["text"] = tr("setup.steps.ghFound", source=out["from"]) if out["from"] else tr("setup.steps.ghOk")
    elif out["state"] == "none":
        out.update(ask=True, text=tr("setup.steps.ghNone"))
    elif out["state"] == "bad":
        out.update(ask=True, text=tr("setup.steps.ghBad"))
    else:
        out["text"] = tr("setup.steps.ghUnchecked")
    return out


@step("protocols-plan", "protocols")
def s_protocols_plan(c):
    from ..gw import awgengine, model as gmodel, ops as gops
    probe = gops.probe(c.runner)
    kinds = list(gmodel.INBOUND_KINDS)
    rows = gops.inbound_plan(c.runner, kinds)
    cards = []
    for r in rows:
        card = product.protocol_card(r["kind"])
        cards.append(dict(r, why=card["why"] or r.get("why") or "", tags=card["tags"], mb=card["mb"], on=card["on"],
                          why_not=r.get("why") if not r.get("supported") else "",
                          awg=r["kind"] in product.AWG_KINDS))
    try:
        chk = awgengine.check(c.runner)
        module = {"can": bool(chk.get("can")), "reasons": chk.get("blockers") or [],
                  "codes": chk.get("codes") or [], "build": tr("setup.steps.moduleBuildTime"),
                  "text": tr("setup.steps.moduleCan") if chk.get("can") else
                          tr("setup.steps.moduleCannot",
                             reasons="; ".join(chk.get("blockers") or [tr("setup.steps.moduleNoReason")]))}
    except KaskadError as e:
        module = {"can": False, "reasons": [str(e)], "codes": [], "build": "", "text": _said_err(e)}
    # is the build needed at all (T-M6-qs330 №11: «Соберём модуль…» with the module built): in memory already (with
    # the set of 3.1 — for a new inbound 3.1), or the program chosen — nothing to build
    try:
        from ..gw import awgkmod
        module.update(ready=awgkmod.module_loaded(), ready31=awgkmod.set_loaded(),
                      program=awgengine.record(state.load())["want"] == "program")
    except (KaskadError, OSError, ValueError):
        module.update(ready=False, ready31=False, program=False)
    need_key = any(r.get("needs_key") for r in rows)
    gh = github_state(c.runner, check=online()) if (need_key or any(r.get("needs_core") for r in rows)) else \
        github_state(c.runner, check=False)
    return res("ok", "", data={"gateway": {"supported": probe["supported"], "reason": probe["reason"],
                                           "packages": probe.get("packages") or []},
                               "protocols": cards, "module": module, "github": gh, "base_mb": product.BASE_MB,
                               "defaults": [k for k in kinds if product.protocol_card(k)["on"]]})


@step("github-key", "protocols", secret=True, network=True)
def s_github_key(c):
    from .. import ghkey
    key = (c.body.get("key") or "").strip() if isinstance(c.body.get("key"), str) else ""
    if not ghkey.valid(key):
        return fail(ghkey.BAD_FORMAT, code="bad_format", field="key")
    r = ghkey.check(key)
    if r.get("ok"):
        ghkey.save(key, r)
        key = None
        util.log_tr("journal.setup.gh_saved")
        return res("ok", tr("setup.steps.ghSaved"))
    if r.get("reason") == "net" and c.body.get("force"):
        ghkey.save(key)
        key = None
        util.log_tr("journal.setup.gh_saved_unchecked")
        return res("partial", tr("setup.steps.ghSavedUnchecked"))
    key = None
    if r.get("reason") == "net":
        return fail(r.get("error") or tr("setup.steps.ghNoAnswer"), code="net", field="key",
                    action={"step": "github-key", "force": True, "label": tr("setup.steps.ghSaveAnyway")})
    return fail(r.get("error") or tr("setup.steps.ghRejected"), code="bad", field="key")


def _ports_said(port, fixed):
    """« · порт 51820» of the record's port; an inbound with fixed ports only (L2TP/IPsec, PPTP, IKEv2 — the plan's
    ``fixed``) says them too, as the others do (3.3.2 №4): « · порты 1701, 500, 4500/udp», « · порт 1723/tcp»."""
    if port:
        return tr("setup.steps.portSuffix", port=port)
    by = []                                             # [(proto, [ports])] in the order of the plan
    for f in fixed or []:
        p, n = f.get("proto"), f.get("port")
        if not p or not isinstance(n, int):
            continue
        for x in by:
            if x[0] == p:
                x[1].append(n)
                break
        else:
            by.append((p, [n]))
    if not by:
        return ""
    many = sum(len(x[1]) for x in by) > 1
    said = ", ".join(", ".join(str(n) for n in ns) + "/" + p for p, ns in by)
    return tr("setup.steps.portsSuffix", port=said) if many else tr("setup.steps.portSuffix", port=said)


@step("protocols", "protocols", long=True, network=True)
def s_protocols(c):
    from ..gw import model as gmodel, ops as gops
    kinds = c.body.get("kinds")
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
        return fail(tr("setup.steps.protocolsNone"), code="bad_input", field="kinds")
    bad = [k for k in kinds if k not in gmodel.INBOUND_KINDS]
    if bad:
        return fail(tr("setup.steps.protocolUnknown", kinds=", ".join(bad)), code="bad_input", field="kinds")
    ports = c.body.get("ports") if isinstance(c.body.get("ports"), dict) else {}
    force = c.body.get("force") if isinstance(c.body.get("force"), dict) else {}
    engine = c.body.get("awg_engine") if c.body.get("awg_engine") in ("kernel", "program") else None
    kinds = [k for k in gmodel.INBOUND_KINDS if k in kinds]               # the order of the registry
    plan = gops.inbound_plan(c.runner, kinds)
    titles = dict((r["kind"], r["title"]) for r in plan)
    fixed = dict((r["kind"], r.get("fixed") or []) for r in plan)
    awg = [k for k in kinds if k in product.AWG_KINDS]
    if awg:
        c.say("module", "idle", tr("setup.steps.queued"), tr("setup.steps.rowModule"))
    for k in kinds:
        c.say(k, "idle", tr("setup.steps.queued"), titles.get(k, k))
    st = state.load()
    if not (st.get("gateway") or {}).get("enabled"):
        c.say("gateway", "wait", tr("setup.steps.gatewayWait"), tr("setup.steps.rowGateway"))
        try:
            gops.enable(c.runner)
        except KaskadError as e:
            c.say("gateway", "err", _said_err(e))
            return core_fail(e)
        c.say("gateway", "ok", tr("setup.steps.gatewayOn"))
    done, failed, warns = [], [], []
    for k in kinds:
        first_awg = k in awg and k == awg[0]
        if first_awg:
            c.say("module", "wait", tr("setup.steps.moduleBuilding") if engine != "program" else
                  tr("setup.steps.moduleProgram"))

        def say(text, k=k):
            c.say("module" if k in awg else k, "wait", text)
        c.say(k, "wait", tr("setup.steps.installing"))
        try:
            out = gops.inbound_install(c.runner, k, port=ports.get(k), force=bool(force.get(k)),
                                       awg_engine=engine if first_awg else None, progress=say if k in awg else None)
        except KaskadError as e:
            failed.append(k)
            code = "port_busy" if getattr(e, "code", None) == errors.CONFLICT else "error"
            busy = _port_busy(e, titles.get(k, k)) if code == "port_busy" else None
            text = busy.pop("text") if busy else _said_err(e)
            c.say(k, "err", text)
            if first_awg:
                c.say("module", "idle", tr("setup.steps.moduleNotNeeded"))
            warns.append(dict({"kind": k, "code": code, "text": text}, **(busy or {})))
            continue
        rec = out.get("record") or {}
        port = next((v for f, v in sorted(rec.items()) if f.endswith("port") and isinstance(v, int)), None)
        c.say(k, "ok", tr("setup.steps.installed") + _ports_said(port, fixed.get(k)))
        done.append(k)
        if "awg_engine" in out:
            w = [util.redact(x) for x in out.get("warnings") or []]
            if out["awg_engine"] == "kernel":
                c.say("module", "ok", tr("setup.steps.moduleWorks"))
            else:
                c.say("module", "warn", i18n.join("; ", w) or tr("setup.steps.moduleAsProgram"))
        elif first_awg:
            c.say("module", "ok", tr("setup.steps.moduleWasThere"))
    model.update(lambda d: d["answers"].update({"protocols": done}))
    if not done:
        return res("error", tr("setup.steps.protocolsNoneInstalled"), data={"installed": done, "failed": warns},
                   error={"code": "install_failed", "text": warns[0]["text"] if warns else tr("setup.steps.error")})
    if failed:
        return res("partial", tr("setup.steps.protocolsPartial", done=len(done), total=len(kinds)),
                   data={"installed": done, "failed": warns})
    return res("ok", tr("setup.steps.protocolsAll"), data={"installed": done})


def _tcp_ms(host, port, timeout=3.0):
    t0 = time.monotonic()
    s = socket.create_connection((host, int(port)), timeout=timeout)
    s.close()
    return int((time.monotonic() - t0) * 1000)


@step("forward-check", "forward", record=False)
def s_forward_check(c):
    to = c.str("to", True, 64)
    port = state.parse_port(c.str("to_port", True, 5) or c.str("port", True, 5))
    proto = c.str("proto") or "tcp"
    try:
        ipaddress.ip_address(to)
    except ValueError:
        return fail(tr("setup.steps.fwdBadAddr"), code="bad_input", field="to")
    if proto == "udp":
        return res("ok", tr("setup.steps.fwdUdp"), data={"checked": False})
    try:
        ms = _tcp_ms(to, port)
    except OSError:
        return res("error", tr("setup.steps.fwdUnreachable", host=to, port=port),
                   error={"code": "unreachable", "text": tr("setup.steps.fwdUnreachableShort")},
                   data={"checked": True})
    return res("ok", tr("setup.steps.fwdReachable", host=to, port=port, ms=ms),
               data={"checked": True, "ms": ms})


@step("forward", "forward")
def s_forward(c):
    from .. import forwards
    proto = c.str("proto") or "tcp"
    port = state.parse_port(c.str("port", True, 5))
    if proto == "tcp" and port == 443 and not c.body.get("force_443") and \
            (model.load()["answers"].get("domain_plan") != "none"):
        return res("need_input", tr("setup.steps.fwd443"),
                   error={"code": "port443_site", "text": tr("setup.steps.fwd443Short"),
                          "action": {"step": "forward", "force_443": True, "label": tr("setup.steps.fwd443Anyway")}})
    try:
        r = forwards.add_forward(proto, port, c.str("to", True, 64), c.str("to_port", True, 5) or port,
                                 name=c.str("name", max_len=64) or "", force=bool(c.body.get("force")))
    except KaskadError as e:
        if getattr(e, "code", None) == errors.CONFLICT:
            return core_fail(e, action={"step": "forward", "force": True, "label": tr("setup.steps.addAnyway")})
        raise
    f = r["forward"]
    model.update(lambda d: d["answers"].update({"forward": {"proto": f["proto"], "port": f["port"], "to": f["to"],
                                                            "to_port": f["to_port"]}}))
    return res("ok", tr("setup.steps.fwdWorks", proto=f["proto"], port=f["port"], to=f["to"], to_port=f["to_port"]),
               data={"forward": f})


# ── 3. Выходы ──────────────────────────────────────────────────────────────

@step("exits-choice", "exits")
def s_exits_choice(c):
    from ..gw import ops as gops
    choice = c.str("choice", True, 16)
    if choice not in ("direct", "exits", "later"):
        return fail(tr("setup.steps.exitsChoiceBad"), code="bad_input", field="choice")
    text = ""
    pending = False
    if choice == "direct":
        gops.default_route(c.runner, "direct")
        text = tr("setup.steps.exitsDirect")
    elif choice == "exits":
        # «Через выходы» after «Напрямую» (T-M6-qs330 №5): the route goes back to the pool. No key yet — the pool with
        # nothing in it would cut the devices off; the first key added on this stage turns the route to the pool
        via = _route_via()
        if via in ("direct", "block"):
            if gops.exits_view(c.runner):
                gops.default_route(c.runner, "pool")
            else:
                pending = True
    model.update(lambda d: d["answers"].update({"exits": choice, "route_pool_on_add": pending}))
    if choice == "later":
        # owner 2026-10-09 (DECISIONS-3.3.0 D1): «Позже» — the route «напрямую» with an honest note; the exits added
        # later in the panel get «Блокировать» on failure (the step «route» and the panel are fail-closed). Keys added
        # already stay used: turning them off by «Позже» would send the devices from the IP of this server unasked —
        # the text says what the route is (T-M6-qs330 №5)
        rows = gops.exits_view(c.runner)
        if rows and _route_via() not in ("direct", "block"):
            text = tr("setup.steps.exitsLaterKept")
        else:
            if _route_via() != "direct":
                gops.default_route(c.runner, "direct")
            text = tr("setup.steps.exitsLaterDirect")
        model.update(lambda d: model.set_stage(d, "exits", "skip"))
    return res("ok", text, data={"choice": choice, "route": _route_doc()})


def _route_doc():
    from ..gw import exits as gexits
    try:
        return gexits.default_route(state.load())
    except (KaskadError, OSError, ValueError):
        return None


def _route_via():
    r = _route_doc()
    return (r or {}).get("via") or "pool"


def _route_after_add(c):
    """The first key after «Через выходы» (the route was «напрямую» then): the route goes to the pool."""
    from ..gw import ops as gops
    if not model.load()["answers"].get("route_pool_on_add"):
        return
    model.update(lambda d: d["answers"].update({"route_pool_on_add": False}))
    if _route_via() in ("direct", "block"):
        gops.default_route(c.runner, "pool")
        c.say("route", "ok", tr("setup.steps.routeViaExits"), tr("setup.steps.rowRoute"))


SUB_CHECK_TIMEOUT = 4.0      # s: the name of the server of a subscription looked up before «добавить?»


def _sub_already(sub):
    from ..gw import exitsubs
    return tr("setup.steps.subAlreadyAuto" if sub.get("auto") else "setup.steps.subAlreadyManual",
              name=exitsubs.name_of(sub))


def sub_check(url, timeout=None):
    """A subscription before «добавить?» (T-M6-qs330 №8: a name that does not exist got a green tick): the form of the
    address, the same one already added, the name of its server found and not an inner address — nothing downloaded.
    -> the result of a step: error | ok with data {"sub_already"} | ok with data {"sub_checked"}."""
    import threading
    from ..gw import exitsubs, subfetch
    try:
        same = exitsubs.find_same(url)
        url, _ = exitsubs.normalize(url)
    except KaskadError as e:
        return fail(_said_err(e), code="bad_input", field="text")
    if same is not None:
        return res("ok", _sub_already(same), data={"sub_already": True, "sub_checked": True})
    box = {}

    def look():
        try:
            subfetch.check_url(url)
            subfetch.check_cert(url)                # the certificate as the add checks it (T-M6-qs330b №10)
            box["ok"] = True
        except KaskadError as e:
            box["err"] = str(e)
            box["data"] = _cert_name_data(e)
        except Exception as e:                      # noqa: BLE001 — the lookup must not take the preview down
            box["err"] = tr("setup.steps.subCheckFailed", error=e)
    th = threading.Thread(target=look, name="kaskad-sub-check", daemon=True)
    th.start()
    th.join(SUB_CHECK_TIMEOUT if timeout is None else timeout)
    url = None
    if box.get("err"):
        if box.get("data"):
            return fail(util.redact(box["err"]), code="cert_name", field="text", data=box["data"])
        return fail(util.redact(box["err"]), code="bad_url", field="text")
    return res("ok", tr("setup.steps.subFound") if box.get("ok") else tr("setup.steps.subUnchecked"),
               data={"sub_already": False, "sub_checked": bool(box.get("ok"))})


@step("exits-preview", "exits", secret=True, record=False)
def s_exits_preview(c):
    from ..gw import ops as gops
    text = c.body.get("text")
    r = gops.exit_preview(c.runner, text)
    if r.get("subscription"):
        sub = features.has("gw", "exit-sub", "add")
        data = {"items": [], "subscription": True, "sub_available": sub, "sub_text": "" if sub else features.soon()}
        if not sub:
            return res("ok", r.get("text") or "", data=data)
        out = sub_check(text.strip() if isinstance(text, str) else "")
        text = None
        out["data"] = dict(data, **out.get("data") or {})
        return out
    text = None
    return res("ok", r.get("text") or "", data={"items": r["items"], "subscription": False,
                                               "module_build": bool(r.get("module_build"))})


def _cert_name_data(e):
    """{"cert_domain", "same_server"} of a refusal «Сертификат выдан на домен …» (subfetch.cert_name_error) — straight
    or inside the JSON of ``kaskadctl gw exit-sub add`` — else None. The page offers «Добавить по домену» only with
    ``same_server`` (the domain leads to the same address: the secret path goes to the same server)."""
    d = e.data if isinstance(getattr(e, "data", None), dict) else {}
    for x in (d, d.get("data")):
        if isinstance(x, dict) and isinstance(x.get("cert_domain"), str) and "same_server" in x:
            return {"cert_domain": x["cert_domain"], "same_server": x["same_server"] is True}
    return None


def _sub_add(c, url):
    """``gw exit-sub add`` with the address on stdin -> (its answer, None) or (None, «уже добавлена…») — the same
    subscription again is not a failure (T-M6-qs330 №8)."""
    try:
        return features.call(c.runner, ["gw", "exit-sub", "add"], stdin=url + "\n"), None
    except KaskadError as e:
        d = e.data if isinstance(getattr(e, "data", None), dict) else {}
        inner = d.get("data") if isinstance(d.get("data"), dict) else {}
        if getattr(e, "code", None) == errors.CONFLICT and inner.get("sub"):
            from ..gw import exitsubs
            sub = next((s for s in exitsubs.load()["subs"] if s.get("id") == inner["sub"]), None)
            return None, _sub_already(sub) if sub else _said_err(e)
        raise


@step("exits-add", "exits", long=True, network=True, secret=True)
def s_exits_add(c):
    from ..gw import ops as gops
    text = c.body.get("text")
    if isinstance(text, str) and text.strip().lower().startswith(("http://", "https://")):
        c.say("sub", "wait", tr("setup.steps.subAdding"), tr("setup.steps.rowSub"))
        try:
            doc, already = _sub_add(c, text.strip())
        except KaskadError as e:
            if (e.data or {}).get("reason") == "soon":
                c.say("sub", "idle", features.soon())
                return res("unavailable", features.soon(), error={"code": "soon", "text": features.soon()})
            named = _cert_name_data(e)
            if named:
                text = None
                c.say("sub", "err", _said_err(e))
                return fail(_said_err(e), code="cert_name", data=named)
            raise
        text = None
        if already:
            c.say("sub", "ok", tr("setup.steps.subAlreadyRow"))
            return res("ok", already, data={"already": True})
        c.say("sub", "ok", tr("setup.steps.subAddedRow"))
        _route_after_add(c)
        return res("ok", tr("setup.steps.subAdded"), data={"result": doc})
    try:
        pre = gops.exit_preview(c.runner, text)
    except KaskadError:
        pre = {}
    if pre.get("module_build"):
        c.say("module", "wait", tr("setup.steps.moduleBuildingLong"), tr("setup.steps.rowModule"))
    c.say("add", "wait", tr("setup.steps.keyAdding"), tr("setup.steps.rowExitKey"))
    try:
        r = gops.exit_add(c.runner, text)
    except KaskadError as e:
        text = None
        c.say("add", "err", _said_err(e))
        if pre.get("module_build"):
            c.say("module", "idle", tr("setup.steps.moduleNotNeeded"))
        data = e.data if isinstance(getattr(e, "data", None), dict) else {}
        if data.get("needs_key"):
            return fail(_said_err(e), code="github_key", action={"step": "github-key"})
        return core_fail(e)
    text = None
    if pre.get("module_build"):
        c.say("module", "ok", tr("setup.steps.moduleReady"))
    for x in r.get("results") or []:
        st_ = {"added": "ok", "in_pool": "warn", "not_added": "err"}.get(x.get("result"), "warn")
        c.say("k-" + str(x.get("id")), st_, {"added": tr("setup.steps.keyAdded"),
                                             "in_pool": x.get("why") or tr("setup.steps.keyInPool")}.get(
            x.get("result"), x.get("why") or tr("setup.steps.keyNotAdded")),
            "%s · %s" % (x.get("title") or "", x.get("where") or ""))
    c.say("add", "ok", r.get("text") or tr("setup.steps.done"))
    _route_after_add(c)
    return res("ok", r.get("text") or "", data={"added": r.get("added"), "results": r.get("results"),
                                               "warnings": [util.redact(w) for w in r.get("warnings") or []]})


@step("exits", "exits", record=False)
def s_exits(c):
    from ..gw import exits as gexits, ops as gops
    rows = gops.exits_view(c.runner)
    st = state.load()
    alive = sum(1 for r in rows if r.get("usable"))
    for r in rows:
        r["state"] = "ok" if r.get("usable") else ("idle" if not r.get("enabled") else "err")
    route = gexits.default_route(st)
    subs = features.has("gw", "exit-sub", "list")
    # the subscriptions as rows of ExitsInput (3.3.3): name, keys, auto — no address, no key
    return res("ok", tr("setup.steps.exitsAlive" if alive else "setup.steps.exitsNoneAlive", alive=alive,
                        total=len(rows)) if rows else tr("setup.steps.exitsNone"),
               data={"exits": rows, "alive": alive, "total": len(rows), "route": route,
                     "subs": subs, "sub_rows": _subs_rows() if subs else []})


@step("exit-del", "exits")
def s_exit_del(c):
    from ..gw import ops as gops
    r = gops.exit_del(c.runner, c.str("id", True, 32))
    text = tr("setup.steps.exitRemoved") + (" " + r["text"] if r.get("text") else "")    # routes to the other copy
    return res("ok", text, data={"left": r.get("left")})


_SUB_ID = re.compile(r"^s-[0-9a-f]{6}$")


# a subscription of the exits away (× in ExitsInput, 3.3.3) — the question of the panel first (R-333i З1): with its keys
# or {"keep": true} — its keys stay as added by hand («gw exit-sub del --keep-keys»)
@step("exit-sub-del", "exits")
def s_exit_sub_del(c):
    from ..gw import exitsubs
    sid = c.str("id", True, 16)
    if not _SUB_ID.match(sid):
        return fail(tr("setup.steps.subNoSuch"), code="bad_input", field="id")
    r = exitsubs.delete(c.runner, sid, keep_keys=c.body.get("keep") is True)
    return res("ok", r.get("text") or "", data={"removed": r.get("removed")})


@step("route", "exits")
def s_route(c):
    from ..gw import ops as gops
    via = c.str("via", True, 40)
    fail_ = c.str("fail") or "block"                  # Kaskad is fail-closed: «блокировать», unless chosen otherwise
    if fail_ not in ("block", "direct"):
        return fail(tr("setup.steps.routeFailBad"), code="bad_input", field="fail")
    r = gops.default_route(c.runner, via, fail_)
    model.update(lambda d: d["answers"].update({"route_pool_on_add": False}))     # chosen by hand: not changed later
    text = ""
    if fail_ == "direct":
        text = tr("setup.steps.routeFailDirect")
    return res("ok", text, data={"route": r["route"]})


# ── 4. Домен и сайт ────────────────────────────────────────────────────────

@step("domain", "domain")                     # «без домена» works offline; DNS says «unknown» itself
def s_domain(c):
    from ..site import ops as sops
    if c.body.get("clear"):
        # the price of VLESS (§3.6, REV-vless4 «было до» 3): the domain away moves VLESS from TLS to Reality — with
        # devices the core refuses without ``yes``; ``yes`` only from the request, after the page showed the price
        # (as the step «site»); the cover of Reality the core picks itself (``vless_auto``)
        yes = c.body.get("yes") is True
        try:
            r = sops.clear(c.runner, way_in=True, yes=yes, vless_auto=True)
        except KaskadError as e:
            if (getattr(e, "data", None) or {}).get("need") != "vless_change":
                raise
            return core_fail(e, action={"step": "domain", "clear": True, "yes": True,
                                        "label": tr("setup.steps.domainVlessAnyway")})
        text = " ".join([r.get("text") or tr("setup.steps.domainCleared")] + list(r.get("warnings") or []))
        return res("ok", text, data={"affected": r.get("affected")})
    if c.body.get("plan") in ("none", "later"):
        model.update(lambda d: d["answers"].update({"domain_plan": "none"}))
        return res("ok", tr("setup.steps.noDomain"))
    name = c.str("name", True, 253)
    zone = c.str("zone", max_len=253)                   # the field «Ваш домен» (_zone_of)
    if sops.replaces_working(name):
        # a working domain stays until the new name is checked AND has its certificate (T-M6-qs330d №1): here only
        # the check — nothing changes on the server; the switch is the step «cert» with this name
        d = smodel.parse_domain(name)
        try:
            doc = sops.precheck(c.runner, d)
        except sops.Refusal as e:
            if e.doc.get("reason") == "dns" and isinstance(e.doc.get("dns"), dict):
                view = dns_doc(e.doc["dns"])
                _domain_try(name, None, view["dns"], zone=zone)
                return res("ok", e.doc.get("text") or "", data=dict(view, kept=e.doc.get("kept"), switch=True))
            _domain_try(name, None, "error", e.doc.get("reason") or "error", zone)
            return res("error", e.doc.get("text") or "", error={"code": e.doc.get("reason") or "error",
                                                                "text": e.doc.get("text") or ""})
        _domain_try(name, d, "ok", zone=zone)
        kept = smodel.domain_of(state.load())
        return res("ok", "", data=dict(dns_doc(doc), kept=kept, switch=True))
    r = sops.set_domain(c.runner, name)
    # the name (not a secret) — the page restores the stage after a reload and checks DNS again
    _domain_answer(r.get("domain") or name, None, zone)
    # a domain that was set before (not working) went away: whom to give the settings again — ``text`` of set_domain
    warn = list(r.get("warnings") or []) + ([r["text"]] if r.get("affected") and r.get("text") else [])
    return res("ok", r.get("text") or "", data=dict(dns_doc(r["dns"]), affected=r.get("affected"),
                                                   warnings=[util.redact(w) for w in warn], reissue=_reissue(r)))


def _zone_of(name, zone):
    """``zone`` (the field «Ваш домен» of the wizard) when ``name`` is it or one label before it, else "" — the page
    splits the name into its fields by it after a reload, without guessing (REV-334 №4: example.co.nz came back as the
    subdomain «example» of «co.nz»)."""
    n, z = str(name or "").strip().lower(), str(zone or "").strip().lower()
    if "." not in z.strip("."):
        return ""
    if n == z or (n.endswith("." + z) and "." not in n[:-len(z) - 1]):
        return z
    return ""


def _domain_answer(name, ready, zone=None):
    """The name of the stage in the answers (the page restores it after a reload) and ``domain_next`` — a new name that
    passed ``precheck`` while a working domain stays: the move of the wizard may be prepared to it (web.api_move).
    ``domain`` is the domain of the node (set, or working); a new name next to a working one lives in ``domain_try``
    until it becomes the domain (_domain_try — T-M6-qs330e №3). ``domain_zone`` — the domain as typed (_zone_of)."""
    zone = _zone_of(name, zone)
    a = model.load()["answers"]
    if a.get("domain_plan") == "have" and a.get("domain") == name and a.get("domain_next") == ready \
            and "domain_try" not in a and (a.get("domain_zone") or "") == zone:
        return                                          # the DNS poll every 10 s writes nothing new

    def mut(d):
        d["answers"].update({"domain_plan": "have", "domain": name})
        d["answers"].pop("domain_try", None)
        if zone:
            d["answers"]["domain_zone"] = zone
        else:
            d["answers"].pop("domain_zone", None)
        if ready:
            d["answers"]["domain_next"] = ready
        else:
            d["answers"].pop("domain_next", None)
    model.update(mut)


def _zone_keep(name, zone):
    """The check of the domain of the node with the field «Ваш домен» split another way (REV-334 П2: «vpn.example.com»
    typed whole into «Ваш домен», then moved by «Перенести» — the name the same, the zone ``example.com``): the zone in
    the answers follows the page, so after a reload the fields come back as they are now. Only the zone of
    ``answers.domain`` itself, when ``name`` is it (_zone_of); a zone that does not fit it, or none (an old page),
    changes nothing."""
    a = model.load()["answers"]
    z = _zone_of(name, zone)
    if not z or str(a.get("domain") or "").strip().lower() != str(name).strip().lower() \
            or (a.get("domain_zone") or "") == z:
        return                                          # the DNS poll every 10 s writes nothing new

    def mut(d):
        if str(d["answers"].get("domain") or "").strip().lower() == str(name).strip().lower():
            d["answers"]["domain_zone"] = z
    model.update(mut)


def _domain_try(name, ready, how, why="", zone=None):
    """A new name typed next to a WORKING domain and its last check (T-M6-qs330e №3: a refused «m6e…» stayed in
    ``answers.domain`` and came back into the field after a reload instead of the working domain). ``domain`` stays the
    working one; ``domain_try`` = {"name", "state": ok | wait | none | elsewhere | cloudflare | error, "why": the code
    of a refusal, "zone": as in _domain_answer, "at"} — the page shows it as «Последняя проверка: … — …»;
    ``domain_next`` as in _domain_answer."""
    work = smodel.domain_of(state.load())
    a = model.load()["answers"]
    cur = a.get("domain")
    try:
        same = bool(cur) and smodel.parse_domain(cur) == work
    except KaskadError:
        same = False
    keep = cur if same else smodel.unicode(work)
    rec = {"name": name, "state": how, "why": why or "", "zone": _zone_of(name, zone)}
    old = a.get("domain_try") if isinstance(a.get("domain_try"), dict) else {}
    if a.get("domain_plan") == "have" and a.get("domain") == keep and a.get("domain_next") == ready \
            and dict((k, old.get(k)) for k in rec) == rec:
        return                                          # the DNS poll every 10 s writes nothing new

    def mut(d):
        if d["answers"].get("domain") != keep:
            d["answers"].pop("domain_zone", None)           # the zone was of the name that is not the domain any more
        d["answers"].update({"domain_plan": "have", "domain": keep, "domain_try": dict(rec, at=util.now_iso())})
        if ready:
            d["answers"]["domain_next"] = ready
        else:
            d["answers"].pop("domain_next", None)
    model.update(mut)


def _reissue(r):
    """Whom to give the settings again after a step of «Домен и сайт» (site.ops: ``affected`` — the devices of the
    domain's certificate, ``vless.reissue`` — of VLESS that changed its mode): [{"id", "name"}], each once. The page
    puts «Выдать заново» on the warning by it (T-M6-qs330e №1)."""
    if not isinstance(r, dict):
        return []
    v = r.get("vless") if isinstance(r.get("vless"), dict) else {}
    rows = list(r.get("affected") or []) + (list(v.get("reissue") or []) if v.get("changed") else [])
    out, seen = [], set()
    for x in rows:
        i = isinstance(x, dict) and (x.get("device") or x.get("id"))
        if i and i not in seen:
            seen.add(i)
            out.append({"id": i, "name": x.get("name") or i})
    return out


def dns_doc(d):
    """The DNS check as the page shows it: state ok | wait (not yet / partial / unknown) | cloudflare | elsewhere |
    none, with the advice of kaskad.site.dns."""
    s = d.get("state")
    if d.get("cloudflare"):
        view = "cloudflare"
    elif d.get("ready"):
        view = "ok"
    elif s in ("elsewhere", "none"):
        view = s
    else:
        view = "wait"
    return {"dns": view, "dns_state": s, "ready": bool(d.get("ready")), "a": d.get("a") or [],
            "own": d.get("own") or [], "advice": d.get("advice") or "", "checked_at": d.get("checked_at"),
            "hint_after": DNS_HINT}


@step("dns", "domain", record=False, network=True)
def s_dns(c):
    """DNS of the domain — or of ``name``, a new name next to a working domain: only looked at, nothing saved (the
    page waits for its A record without touching the server — T-M6-qs330d №1)."""
    from ..site import dns as sdns
    from ..site import ops as sops
    name = c.str("name", max_len=253)
    zone = c.str("zone", max_len=253)                   # the field «Ваш домен» (_zone_of)
    st = state.load()
    cur = smodel.domain_of(st)
    if name and smodel.parse_domain(name) != cur:
        d = smodel.parse_domain(name)
        on = sops.working(smodel.peek(st))
        doc = dict(dns_doc(sdns.check(d, st, c.runner)), kept=cur if on else None, switch=on)
        if on:
            _domain_try(name, d if doc["dns"] == "ok" else None, doc["dns"], zone=zone)
        else:
            _domain_answer(name, d if doc["dns"] == "ok" else None, zone)
        return res("ok" if doc["ready"] else "checking",
                   doc["advice"] or (tr("setup.steps.dnsReady") if doc["ready"] else tr("setup.steps.dnsWait")),
                   data=doc)
    if name:
        _zone_keep(name, zone)
    d = sops.check_dns(c.runner)
    doc = dns_doc(d)
    return res("ok" if doc["ready"] else "checking",
               doc["advice"] or (tr("setup.steps.dnsReady") if doc["ready"] else tr("setup.steps.dnsWait")), data=doc)


@step("cert", "domain", long=True, network=True)
def s_cert(c):
    """The certificate of the domain — or, with ``name`` other than a working domain, the change of the domain
    (site.ops.change_domain: the new name checked and given its certificate while the old one serves, then the switch;
    a failure leaves the old domain as it was)."""
    from ..site import ops as sops
    name = c.str("name", max_len=253)
    zone = c.str("zone", max_len=253)                   # the field «Ваш домен» (_zone_of)
    switch = bool(name) and sops.replaces_working(name)
    if not switch:
        c.say("cert", "wait", tr("setup.steps.certWait"), tr("setup.steps.rowCert"))
    try:
        if switch:
            r = sops.change_domain(c.runner, name, say=c.say)
            _domain_answer(name, None, zone)
        else:
            if name and smodel.parse_domain(name) != smodel.domain_of(state.load()):
                sops.set_domain(c.runner, name)          # no working domain: the name as the step «domain» sets it
            # the wizard puts the site on by its own card «Сайт и панель на домене» (§3.2: autostart without devices of
            # VLESS, «Не включать сайт» → the consent) — not the §3.3 step of the certificate
            r = sops.issue(c.runner, vless_auto=False)
    except sops.Refusal as e:
        c.say("cert", "err", e.doc.get("text") or "")
        if switch:                                      # the working domain stays; the try is remembered as refused
            _domain_try(name, None, "error", e.doc.get("reason") or "error", zone)
        # a limit: when (UTC and, as ``retry_text``, the part of the words by the server's clock — the page says it by
        # the browser's) and which limit (T-M6-qs330c №2)
        return res("error", e.doc.get("text") or "", data={"retry_after": e.doc.get("retry_after")},
                   error={"code": e.doc.get("reason") or "error", "text": e.doc.get("text") or "",
                          "retry_after": e.doc.get("retry_after"), "retry_text": e.doc.get("retry_text"),
                          "limit": e.doc.get("limit")})
    cert = r.get("cert") or {}
    if not switch:
        c.say("cert", "ok", tr("setup.steps.certIssued" if r.get("issued") else "setup.steps.certValid",
                               until=fmt_date((cert.get("not_after") or "")[:10])))
    try:
        impact = (sops.status(c.runner).get("use_impact") or {})
    except KaskadError:
        impact = {}
    served = cert_served(c.runner)
    # warnings («выдайте заново» of VLESS and of the devices of the domain…) come in the result of the job — the page
    # shows them from state.job.result, after the move of the wizard too (T-M6-qs330e №1). «Почта не задана» goes
    # apart (``email``): the page asks for the mail right in that note (3.3.2 №5)
    warns, email = _email_split(r)
    data = {"cert": cert, "warnings": warns, "reissue": _reissue(r), "email": email,
            "consumers": r.get("consumers"), "use_impact": impact, "served": served}
    if switch:
        v = r.get("vless") or {}
        data.update(changed_from=r.get("changed_from"), domain=r["site"]["domain"],
                    site_on=r["site"]["mode"] == "site", template=r.get("template") or None,
                    vless={"security": v.get("security"), "changed": bool(v.get("changed")), "port": v.get("port"),
                           "text": v.get("text") or "", "error": v.get("error") or ""} if v else None)
        text = tr("setup.steps.domainChanged", old=smodel.unicode(r.get("changed_from")),
                  new=smodel.unicode(r["site"]["domain"]))
        return res("ok", text, data=data)
    return res("ok", r.get("text") or "", data=data)


def _email_split(r):
    """``(other warnings, email)`` of a result of site.ops: the warning of the code ``sops.NO_EMAIL`` («Почта для
    Let's Encrypt не задана») becomes ``email = {"need": True, "text": …}`` — the page shows it with a field for the
    mail (step «site-email»); with the mail given — ``{"need": False}``. By the code (``warning_codes`` /
    ``warning_items``), never by the words: they are in the language of the call (live check 3.3.2 №1). ``need`` — by
    the fact, the mail in the settings: a check of a domain whose certificate is still valid answers without the code
    (nothing was issued), and the field must come back while there is no mail (T-M6-332b №4)."""
    from ..site import ops as sops
    rest = [util.redact(w) for w in sops.uncoded(r, sops.NO_EMAIL)]
    if not smodel.peek(state.load())["acme_email"]:
        return rest, {"need": True, "text": sops.warning(sops.NO_EMAIL)}
    return rest, {"need": False}


@step("site-email", "domain")
def s_site_email(c):
    """The mail of Let's Encrypt from the stage «Домен и сайт» (3.3.2 №5) — the same function as ``kaskadctl site email``
    and the panel (Настройки → Сайт): site.ops.set_email (an account of certbot already there is updated, else the mail
    is used at the next issue). ``email`` "" clears it."""
    from ..site import ops as sops
    e = c.str("email", max_len=254) or ""
    try:
        r = sops.set_email(c.runner, e)
    except KaskadError as err:
        return fail(str(err), code="bad_input", field="email")
    text = tr("setup.steps.siteEmailSet", email=r["email"]) if r["email"] else tr("setup.steps.siteEmailCleared")
    return res("partial" if r.get("warnings") else "ok", text,
               data={"email": r["email"], "warnings": [util.redact(w) for w in r.get("warnings") or []]})


CERT_SERVED_WAIT = 10.0         # s: how long the step waits for the panel to answer the domain with its certificate
CERT_SERVED_POLL = 0.5


def cert_served(runner, wait=None):
    """The step «cert» says «ok» only when the open mode already answers the domain's name with the domain's
    certificate (T-M6-qs330b №4): the copy is passed to the panel by SIGHUP (site.consumers) — here a handshake with
    itself confirms it; still the old one after a third of the wait — the panel is told once more. -> True | False
    (the old certificate all the wait long) | None (no open mode, no domain certificate, no handshake: nothing to
    wait for — the page checks again before it moves)."""
    from ..panel import conf
    from ..site import consumers
    wait = CERT_SERVED_WAIT if wait is None else wait
    end, nudged = time.monotonic() + wait, False
    while True:
        got = conf.domain_served()
        if got["ready"]:
            return True
        if got["reason"] != "stale":
            return None
        left = end - time.monotonic()
        if left <= 0:
            util.log_tr("journal.setup.old_cert", sec=int(wait), host=got["host"])
            return False
        if not nudged and left < wait * 2 / 3:
            nudged = True
            consumers._reload_panel(runner)
        time.sleep(min(CERT_SERVED_POLL, left))


@step("site", "domain", long=True, network=True)
def s_site(c):
    from ..site import ops as sops
    tid = c.str("template", max_len=120)
    # the price of VLESS (§3.7, REV-vless-sh3 заметно 4): devices with links of VLESS that the site on would change —
    # the core refuses without ``yes``; the page shows the text and sends the step again with ``yes`` true
    yes = c.body.get("yes") is True
    c.say("site", "wait", tr("setup.steps.siteWait"), tr("setup.steps.rowSite"))
    try:
        r = sops.mode(c.runner, "site", tid if tid and tid != "random" else None, yes=yes, via="setup",
                      keep_site=True)
    except sops.Refusal as e:
        c.say("site", "err", e.doc.get("text") or "")
        return res("error", e.doc.get("text") or "", error={"code": e.doc.get("reason") or "error",
                                                            "text": e.doc.get("text") or ""})
    except KaskadError as e:
        if (getattr(e, "data", None) or {}).get("need") != "vless_change":
            raise
        c.say("site", "warn", (e.data or {}).get("text") or "")
        return core_fail(e, action={"step": "site", "yes": True, "template": tid,
                                    "label": tr("setup.steps.siteVlessAnyway")})
    c.say("site", "ok", tr("setup.steps.siteOpen"))
    # VLESS follows the site as after «site mode site» (site.front.sync inside sops.mode): TLS with the domain
    v = (r.get("vless") or {}) if isinstance(r, dict) else {}
    if v.get("changed"):
        c.say("vless", "ok" if v.get("security") == "tls" else "warn",
              tr("setup.steps.vlessTls") if v.get("security") == "tls" else (v.get("error") or "Reality"), "VLESS")
    try:
        url = sops.panel_url(show=True)["url"]
    except KaskadError:
        url = None
    return res("ok", tr("setup.steps.siteDone"),
               data={"panel_url": url, "template": (r.get("template") or {}) if isinstance(r, dict) else {},
                     "vless": {"security": v.get("security"), "changed": bool(v.get("changed")), "port": v.get("port"),
                               "text": v.get("text") or "", "error": v.get("error") or ""} if v else None,
                     "warnings": [util.redact(w) for w in (r.get("warnings") or [] if isinstance(r, dict) else [])],
                     "reissue": _reissue(r)})


def site_now():
    """The site of the node now (site.ops.site_now) for the state of the page; None when it cannot be read."""
    from ..site import ops as sops
    try:
        out = sops.site_now()
        peek = smodel.peek(state.load())
        out["email"] = bool(peek["acme_email"])     # the mail of Let's Encrypt is given (3.3.2 №5)
        # the domain works with its certificate of Let's Encrypt — the page does not ask to issue it again; the name in
        # letters too (R-333 З2: «впн.пример.рф» is «xn--…» on the server)
        out["cert"] = bool(out.get("domain")) and peek["cert"] == "letsencrypt"
        out["domain_u"] = smodel.unicode(out.get("domain")) if out.get("domain") else None
        return out
    except (KaskadError, OSError, ValueError):
        return None


_VLESS_RISKS_FIELDS = ("ver", "site", "need_pick", "title", "text", "price", "phrase")


def vless_now():
    """VLESS of the node for the stage «Домен и сайт» (SPEC-vless-tls-default §3.2, Ш4): None when VLESS is not
    installed (or the state cannot be read); else {security, devices — how many devices have VLESS, reason, forced,
    want, legacy, recommend {mode, devices, text} | None, node_domain, reality_risks {ver, site, need_pick, title,
    text, price, phrase} | None} — ``gw.ops.vless_view`` (no network, read-only). No keys, no ids of links."""
    from ..gw import model as gmodel
    from ..gw import ops as gops
    try:
        st = state.load()
        if gmodel.inbound(st, gops.VLESS) is None:
            return None
        v = gops.vless_view(st)
        rr = v.get("reality_risks")
        rec = v.get("recommend")
        return {"security": v.get("security"), "devices": len(gops._reissue_devices(st, gops.VLESS)),
                "reason": v.get("reason") or "", "forced": bool(v.get("forced")), "want": v.get("want"),
                "legacy": bool(v.get("legacy")), "node_domain": v.get("node_domain") or "",
                "recommend": {"mode": rec.get("mode"), "devices": rec.get("devices"), "text": rec.get("text")}
                if isinstance(rec, dict) else None,
                "reality_risks": dict((k, rr.get(k)) for k in _VLESS_RISKS_FIELDS) if isinstance(rr, dict) else None}
    except (KaskadError, OSError, ValueError):
        return None


_RISKS_VER = re.compile(r"^r[0-9]{1,3}[ab]$")
_SITE_WORDS = ("own", "kept")


@step("vless-mode", "domain", long=True, network=True)
def s_vless_mode(c):
    """The mode of VLESS from the card «Сайт и панель на домене» (§3.2, §4.2): ``gw vless mode tls|reality`` with
    ``via``/``who`` «setup». Body {mode, site?, accept_risks?, risks?, yes?}. The core refuses and nothing changes:
    «reality_consent» (data: block, title, risks, site, phrase — the page shows the block, then again with
    ``accept_risks`` true, ``risks`` and ``site`` of the text shown), «vless_change» (data: text, devices — the price;
    again with ``yes`` true), «tls_impossible» (why in words). ``accept_risks`` and ``yes`` — only JSON true."""
    from ..gw import ops as gops
    from ..gw.inbounds import vless as vin
    mode = c.str("mode", required=True, max_len=16)
    if mode not in ("tls", "reality"):
        return fail(tr("setup.steps.vlessModeBad"), code="bad_input", field="mode")
    site = c.str("site", max_len=253)
    if site is not None:
        site = site.lower()
        if site not in _SITE_WORDS and vin.valid_host(site) != site:
            return fail(tr("setup.steps.vlessSiteBad"), code="bad_input", field="site")
    risks = c.str("risks", max_len=8)
    if risks is not None and not _RISKS_VER.match(risks):
        return fail(tr("setup.steps.vlessRisksBad"), code="bad_input", field="risks")
    accept = c.body.get("accept_risks") is True
    yes = c.body.get("yes") is True
    c.say("vless", "wait", tr("setup.steps.vlessWait"), "VLESS")
    try:
        r = gops.vless_mode(c.runner, mode, site=site if mode == "reality" else None, accept=accept, risks=risks,
                            via="setup", who="setup", yes=yes)
    except KaskadError as e:
        d = getattr(e, "data", None) or {}
        need = d.get("need")
        if need in ("reality_consent", "vless_change"):
            # the page shows the block / the price and sends the step again with the answer
            c.say("vless", "warn", d.get("title") or d.get("text") or "", "VLESS")
            return core_fail(e)
        if need == "tls_impossible":
            c.say("vless", "err", _said_err(e), "VLESS")
            return fail(_said_err(e), code="tls_impossible",
                        data={"need": "tls_impossible", "reason": d.get("reason") or "", "fix": d.get("fix")})
        c.say("vless", "err", _said_err(e), "VLESS")
        raise
    if not r.get("ok", True):                           # TLS refused: the snapshot came back (§3.5 п.6)
        text = util.redact(r.get("error") or "")
        c.say("vless", "err", text, "VLESS")
        return fail(text, code="tls_failed", data={"restored": bool(r.get("restored")), "reissue": r.get("reissue")
                                                   or [], "text": r.get("text") or ""})
    sec = r.get("security") or mode
    text = tr("setup.steps.vlessModeTls") if sec == "tls" else \
        tr("setup.steps.vlessModeReality", site=r.get("sni") or "—")
    c.say("vless", "ok", text, "VLESS")
    data = {"vless": {"security": sec, "changed": bool(r.get("changed")), "port": r.get("port"), "want": r.get("want"),
                      "text": r.get("text") or ""},
            "reissue": [{"id": x.get("id"), "name": x.get("name")} for x in r.get("reissue") or [] if isinstance(x, dict)]}
    so = r.get("site_out")
    if isinstance(so, dict):                            # TLS switched the site on: the new address of the panel, as «site»
        from ..site import ops as sops
        try:
            data["panel_url"] = sops.panel_url(show=True)["url"]
        except KaskadError:
            data["panel_url"] = None
        data["template"] = so.get("template") or {}
        data["warnings"] = [util.redact(w) for w in (so.get("warnings") or []) if isinstance(w, str)]
    return res("ok", text, data=data)


@step("templates", "domain", record=False)
def s_templates(c):
    """A page of the catalog (T-M6-qs330 №3): the panel never parses the 14 MB catalog — ``prepare`` brings the
    catalog and builds its index apart (systemd-run --scope), the reads are seeks in the index. Pages of PER_PAGE;
    the categories with their counts — the chips; «gone» — known as removed by its author (grey, not selectable)."""
    from ..site import templates, tplhealth
    page = c.body.get("page") if isinstance(c.body.get("page"), int) and not isinstance(c.body.get("page"), bool) \
        else 1
    try:
        templates.prepare(c.runner)
        r = templates.search(c.str("category", max_len=40), c.str("query", max_len=80), page=page)
        cats = templates.catalog()["categories"]
    except KaskadError as e:
        data = e.data if isinstance(getattr(e, "data", None), dict) else {}
        if data.get("needs_key"):
            return fail(_said_err(e), code="github_key", action={"step": "github-key"})
        return core_fail(e)
    r["categories"] = [{"id": x["id"], "title": x["title"], "count": x["count"]} for x in cats]   # the chips
    health = tplhealth.cached([it["id"] for it in r["items"]])
    for it in r["items"]:
        h = health.get(it["id"]) or {}
        it["gone"] = h.get("state") == "gone"
    return res("ok", "", data=r)


@step("template", "domain", long=True, network=True)
def s_template(c):
    from ..site import ops as sops
    tid = c.str("template", max_len=120)
    c.say("tpl", "wait", tr("setup.steps.tplWait"), tr("setup.steps.rowTpl"))
    r = sops.template_set(c.runner, None if tid in (None, "random") else tid)
    if not r.get("ok"):
        c.say("tpl", "err", r.get("text") or "")
        return res("error", r.get("text") or "", error={"code": r.get("reason") or "error", "text": r.get("text") or ""})
    c.say("tpl", "ok", r.get("name") or r.get("id") or tr("setup.steps.done"))
    return res("ok", r.get("text") or "", data=dict((k, r.get(k)) for k in ("id", "name", "category", "fallback")))


# ── 5. Дополнительно ───────────────────────────────────────────────────────
# «Telegram-бот» and «Ключ ИИ-агента» are the panel's components with the panel's handlers (kaskad.panel.ux) behind the
# session of the wizard (kaskad.setup.web: /api/setup/ux/…) — one logic, no steps of their own (3.3.3). The list says
# which items there are and whether each works on this node now.

@step("extras-list", "extras", record=False)
def s_extras_list(c):
    items = []
    for x in product.extras():
        it = dict(x, available=True, text="")
        if x["id"] == "backup":
            it["available"] = features.has("backup", "export")
            it["fields"] = ["password"]
        if not it["available"]:
            it["text"] = features.soon()
        items.append(it)
    return res("ok", "", data={"items": items})


def _summary_subs(ex):
    """[{"name", "keys"}] of the subscriptions the keys of ``ex`` came from, in the order of the keys."""
    out, seen = [], {}
    for e in ex:
        src = e.get("source")
        if not isinstance(src, dict):
            continue
        sid = src.get("sub") or src.get("name")
        if sid not in seen:
            seen[sid] = {"name": src.get("name") or "", "keys": 0}
            out.append(seen[sid])
        seen[sid]["keys"] += 1
    return out


def _subs_rows():
    """The subscriptions of the exits as «exit-sub list» shows them — no address, no key; «stale»/«error» as the panel
    shows them (R-333i П2: not always «обновляется сама»), «exits» — the ids of its keys (the question before × warns
    of the one chosen as the route)."""
    from ..gw import exitsubs
    try:
        rows = exitsubs.listing()
    except (KaskadError, OSError, ValueError):
        return []
    return [{"id": r["id"], "name": r["name"], "auto": bool(r["auto"]), "keys": r["keys"], "text": r["text"],
             "stale": bool(r.get("stale")), "error": bool(r.get("error")), "exits": list(r.get("exits") or [])}
            for r in rows]


def _soon():
    return res("unavailable", features.soon(), error={"code": "soon", "text": features.soon()})


# Копия настроек (kaskad.backup, ветка k3-feat) — в процессе, как панель (api.backup_*): файл копии и пароль только в
# теле запроса и в ответе экспорта, шаг secret — в setup.json и журнал не попадают. Команда kaskadctl backup не годится:
# файл копии на её вход требует пароль в окружении, а экспорт пишет файл на диск узла, а не в браузер.
@step("backup", "extras", secret=True)
def s_backup(c):
    """{password, parts?} -> data {file: the text of the copy (.kbk), name, size, parts} — the page saves it."""
    if not features.has("backup", "export"):
        return _soon()
    from .. import backup
    pw = c.body.get("password")
    if not isinstance(pw, str) or not pw:
        return fail(tr("setup.steps.backupPasswordNeeded"), code="bad_input", field="password")
    try:
        r = backup.export(c.runner, pw, c.body.get("parts") or None)
    finally:
        pw = None
    return res("ok", tr("setup.steps.backupReady"),
               data={"file": r["text"], "name": r["name"], "size": r["size"], "parts": r["parts"]})


# Перенос копии — в два шага (T-M6-qs330 №4: «Открыть копию» применяла её сразу, без предпросмотра).
#   backup-open {file, password}   короткий, в процессе панели: копия расшифрована и разобрана (kaskad.backup.unseal и
#                                  preview — тот же предпросмотр, что у панели), система не меняется. Расшифрованная
#                                  копия лежит до переноса в /run/kaskad/setup/backup-<билет>.json (0600, каталог 0700,
#                                  tmpfs — не переживает перезагрузку) не дольше BACKUP_TTL; странице — только билет и
#                                  предпросмотр (имена и числа, без ключей). Файл, а не память панели: перенос идёт
#                                  отдельным процессом (systemd-run, jobs.py) и из SSH (kaskadctl setup step), а пароль
#                                  после этого шага не нужен никому — странице держать его незачем.
#   backup-open {ticket}           предпросмотр заново по открытой копии (сервер изменился); {drop: true} — закрыть копию
#   import-file {kind: "backup", ticket, confirm}   долгий: перенос той же kaskad.backup.apply; на непустом сервере, если
#                                  что-то пропадёт или заменится, — только с confirm; изменилось с предпросмотра — отказ
#                                  «changed», показать заново.
BACKUP_TTL = 15 * 60
_TICKET = re.compile(r"^[0-9a-f]{32}$")


def _ticket_path(ticket):
    return os.path.join(jobs.run_dir(), "backup-%s.json" % ticket)


def _drop_tickets():
    import glob
    for p in glob.glob(os.path.join(jobs.run_dir(), "backup-*.json")):
        try:
            os.unlink(p)
        except OSError:
            pass


def _put_ticket(doc, sig):
    import secrets as _random
    _drop_tickets()                                    # one open copy at a time
    ticket = _random.token_hex(16)
    util.ensure_dir(jobs.run_dir(), 0o700)
    util.atomic_write(_ticket_path(ticket), json.dumps({"created": _now(), "sig": sig, "doc": doc}), 0o600)
    return ticket


def _get_ticket(ticket):
    """-> the record {"created", "sig", "doc"}; KaskadError «expired» — no such ticket, or older than BACKUP_TTL."""
    if not isinstance(ticket, str) or not _TICKET.match(ticket):
        raise KaskadError(tr("setup.steps.backupExpired"), code=errors.USAGE, data={"reason": "expired"})
    p = _ticket_path(ticket)
    raw = util.read_text(p)
    try:
        rec = json.loads(raw) if raw else None
    except ValueError:
        rec = None
    if not isinstance(rec, dict) or not isinstance(rec.get("doc"), dict) or \
            _now() - float(rec.get("created") or 0) > BACKUP_TTL:
        try:
            os.unlink(p)
        except OSError:
            pass
        raise KaskadError(tr("setup.steps.backupExpired"), code=errors.USAGE, data={"reason": "expired"})
    return rec


def _set_sig(ticket, rec, sig):
    rec["sig"] = sig
    util.atomic_write(_ticket_path(ticket), json.dumps(rec), 0o600)


def _backup_part_text(pv, r):
    """The words of a part in the wizard. The keys of AI agents: the wizard has no tick for them (``preview`` without
    ``agents``) — so not «отдельной отметкой», but where they can be brought later (REV-3372-2 З-3)."""
    ag = pv.get("agents") or {}
    if r["part"] == "mcp" and r["action"] == "none" and ag.get("keys") and not ag.get("on"):
        return tr("setup.steps.backupAgentsLater", keys=ag["keys"])
    return r["text"]


def _backup_view(pv, ticket, created):
    ch = pv.get("changes") or {}
    return {"ticket": ticket, "expires": int(created + BACKUP_TTL), "created": pv.get("created") or "",
            "version": pv.get("version") or "",
            "parts": [{"part": r["part"], "title": r["title"], "action": r["action"], "text": _backup_part_text(pv, r)}
                      for r in pv.get("parts") or []],
            "lost": ch.get("lost") or [], "changed": ch.get("changed") or [], "empty": bool(ch.get("empty")),
            "confirm": bool(ch.get("confirm")), "warnings": [util.redact(w) for w in pv.get("warnings") or []],
            "blockers": [util.redact(b) for b in pv.get("blockers") or []], "address": pv.get("address") or {}}


@step("backup-open", "import", secret=True)
def s_backup_open(c):
    """{file, password} | {ticket} | {drop} -> data: the preview (``_backup_view``) — nothing on the system changes."""
    if not features.has("backup", "import"):
        return _soon()
    from .. import backup
    if c.body.get("drop"):
        _drop_tickets()
        return res("ok", tr("setup.steps.backupClosed"))
    ticket = c.body.get("ticket")
    if ticket:
        rec = _get_ticket(ticket)
        doc, created = rec["doc"], float(rec["created"])
        doc = backup.check_payload(doc)
    else:
        text = c.body.get("file") if isinstance(c.body.get("file"), str) else c.body.get("doc")
        pw = c.body.get("password")
        if not isinstance(text, str) or not text.strip():
            return fail(tr("setup.steps.backupFileNeeded"), code="bad_input", field="file")
        if not isinstance(pw, str) or not pw:
            return fail(tr("setup.steps.backupPasswordEnter"), code="bad_input", field="password")
        try:
            doc = backup.unseal(c.runner, text, pw)
        except KaskadError as e:
            out = core_fail(e)
            if (getattr(e, "data", None) or {}).get("reason") == "password":
                out["error"]["field"] = "password"
            return out
        finally:
            text = pw = None
        ticket, created = None, _now()
    pv = backup.preview(c.runner, doc)
    sig = backup.changes_sig(pv["changes"])
    if ticket is None:
        ticket = _put_ticket(doc, sig)
        util.log_tr("journal.setup.backup_preview", parts=",".join(doc["manifest"].get("parts") or []))
    else:
        _set_sig(ticket, rec, sig)
    doc = None
    view = _backup_view(pv, ticket, created)
    if pv["blockers"]:
        return res("need_input", tr("setup.steps.backupBlocked", blockers=" ".join(view["blockers"])), data=view)
    return res("ok", tr("setup.steps.backupOpenConfirm") if view["confirm"] else tr("setup.steps.backupOpen"),
               data=view)


def _import_backup(c):
    """import-file {kind: "backup", ticket, confirm} — the copy opened by «backup-open» applied (a snapshot first, a
    rollback on failure; «вернуть как было» — SSH 16 → 6 or the panel)."""
    if not features.has("backup", "import"):
        return _soon()
    from .. import backup
    ticket = c.body.get("ticket")
    if not ticket:
        return fail(tr("setup.steps.backupPreviewFirst"), code="preview_first", field="file")
    rec = _get_ticket(ticket)
    doc = backup.check_payload(rec["doc"])
    pv = backup.preview(c.runner, doc)
    if backup.changes_sig(pv["changes"]) != rec.get("sig"):
        return fail(tr("setup.steps.backupChanged"), code="changed", data={"ticket": ticket})
    if pv["changes"]["confirm"] and c.body.get("confirm") is not True:
        return fail(tr("setup.steps.backupConfirm"), code="confirm")
    c.say("backup", "wait", tr("setup.steps.backupApplying"), tr("setup.steps.rowBackup"))
    r = backup.apply(c.runner, doc, None, say=lambda t: c.say("backup", "wait", t, tr("setup.steps.rowBackup")))
    doc = rec = None
    try:
        os.unlink(_ticket_path(ticket))
    except OSError:
        pass
    warn = [util.redact(w) for w in r.get("warnings") or []]
    c.say("backup", "warn" if warn else "ok", tr("setup.steps.backupAppliedRow"))
    return res("partial" if warn else "ok", tr("setup.steps.backupApplied"),
               data={"parts": r.get("parts"), "backup": r.get("backup"), "warnings": warn, "text": r.get("text"),
                     "undo": True})


def _rules_view():
    """What the item «Россия напрямую» of the wizard shows: the profile of the node, the blocks, and ⚠РФ — the mode and
    the block with the organisations it names (the same ``rfgate`` the panel and kaskadctl read)."""
    from ..gw import model as gmodel
    from ..rules import rfgate
    st = state.load()
    conf = gmodel.rules_conf(st)
    rf = rfgate.state(st)
    nd = rfgate.need_data(rfgate.block_orgs(st))
    return {"profile": conf["default"], "ads": bool(conf["block_ads"]), "torrent": bool(conf["block_torrent"]),
            "rf": {"required": rf["required"], "mode": rf["mode"], "orgs": nd["orgs"], "names": nd["names"],
                   "block": nd["block"], "consent": nd["consent"], "uncovered": list(rf.get("uncovered") or [])}}


@step("rules", "extras")
def s_rules(c):
    """«Россия напрямую», реклама, торренты — the switches of kaskad.rules. ``ru_direct: true`` turns «Россия напрямую»
    on only with ``confirm_not_rf: true`` — the tick ⚠РФ «скрипт не используется на территории РФ» the person put
    (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.3, D-019): without it — ``need_input`` with the block, nothing changes
    (a new node stays «всё через выход»). ``orgs`` — the ids of the block the page showed (``data.rf.orgs``): the
    consent covers only them, without them or with a narrower list — ``need_input`` with the block of now
    (SEC-s1-r67 №1). ``ru_direct`` only a JSON boolean (the string "false" is not true — it is ignored, like ads and
    torrent). ``{}`` changes nothing: ``data`` — what the item shows (``_rules_view``)."""
    from ..rules import ops as rops
    from ..rules import rfgate
    out = {}
    on = c.body.get("ru_direct")
    if isinstance(on, bool):
        rf = rops.rf_of(on and c.body.get("confirm_not_rf") is True, via="setup", lang=i18n.get_lang(),
                        orgs=rfgate.shown_orgs(c.body.get("orgs")), must_show=True)
        try:
            rops.set_default(c.runner, "ru_direct" if on else "all_exit", rf=rf)
        except KaskadError as e:
            need = getattr(e, "data", None) or {}
            if need.get("need") != "rf_consent":
                raise
            text = tr("rules.rf.need", block=need.get("block"), consent=need.get("consent"))
            return res("need_input", text, data=dict(_rules_view(), need=need),
                       error={"code": "rf_consent", "text": text, "field": "confirm_not_rf"})
        out["ru_direct"] = on
    ads = c.body.get("ads") if isinstance(c.body.get("ads"), bool) else None
    tor = c.body.get("torrent") if isinstance(c.body.get("torrent"), bool) else None
    if ads is not None or tor is not None:
        rops.set_blocks(c.runner, ads=ads, torrent=tor)
        out.update(dict((k, v) for k, v in (("ads", ads), ("torrent", tor)) if v is not None))
    changed = bool(out)
    out.update(_rules_view())
    return res("ok", tr("setup.steps.rulesSaved") if changed else "", data=out)


# ── 6. Готово ──────────────────────────────────────────────────────────────

@step("platforms", "done", record=False)
def s_platforms(c):
    from ..gw import ops as gops
    plat = c.str("platform")
    if plat:
        return res("ok", "", data=gops.offers(plat))
    from ..gw import model as gmodel
    return res("ok", "", data={"platforms": [{"id": p, "title": product.platform_title(p)}
                                            for p in gmodel.PLATFORMS]})


@step("person", "done")
def s_person(c):
    from ..gw import model as gmodel, ops as gops
    name = c.str("user", True, 64)
    dev_name = c.str("device", True, 200)
    plat = c.str("platform") or "other"
    if plat not in gmodel.PLATFORMS:
        return fail(tr("setup.steps.platformBad", platforms=", ".join(gmodel.PLATFORMS)), code="bad_input",
                    field="platform")
    st = state.load()
    from .. import names                     # T13: «мама» is the person «Мама» (a new one would be refused as a duplicate)
    user = next((u for u in gmodel.users(st) if u.get("name") == name), None) or \
        next((u for u in gmodel.users(st) if names.same(u.get("name"), name)), None)
    if user is None:
        user = gops.user_add(c.runner, name)
    offers = gops.offers(plat)["offers"]
    access = [o["inbound"] for o in offers if o.get("enabled")][:3]
    if c.body.get("inbound") and c.body["inbound"] in [o["inbound"] for o in offers if o.get("enabled")]:
        access = [c.body["inbound"]] + [k for k in access if k != c.body["inbound"]]
    if not access:
        return fail(tr("setup.steps.noInbound"), code="no_inbound", action={"stage": "protocols"})
    # the device name of the wizard is Kaskad's own «{кто} · {платформа}» (setup.js devName — no field for it): a long
    # person is cut with «…» to fit names.MAX here too, never a refusal of words nobody typed (REV-s1-t13 Л-1). The
    # string comes from the request (the wizard of a signed-in admin), so ANY string is cut here — not only Kaskad's own;
    # the cut is no check: device_add → names.check still refuses control characters, «|» and the rest (REV-s1-t13-2 п. 1)
    head, sep, tail = dev_name.rpartition(" · ")
    dev_name = names.fit(head, sep + tail) if sep else names.fit(dev_name)
    r = gops.device_add(c.runner, user["id"], dev_name, platform=plat, access=access)
    d = r["device"]
    model.update(lambda doc: doc["answers"].update({"device": d["id"], "device_at": int(_now()), "device_user": name,
                                                    "device_platform": plat}))
    return res("ok", tr("setup.steps.deviceCreated", name=d["name"]), data={"user": user["id"], "device": d["id"],
                                                                     "access": d.get("access"), "platform": plat,
                                                                     "offers": offers},
               keep={"device": d["id"]})


@step("grant", "done", record=False)
def s_grant(c):
    """«Добавить устройству» of the issue window (give.js, state Б — FIX-332g №2): the device of this wizard gets one
    more protocol that is on the server; the page issues it next. A protocol that is not on the server — the stage
    «Режим и протоколы» (the page sends there itself)."""
    from ..gw import model as gmodel, ops as gops
    did = c.str("device", max_len=32) or model.load()["answers"].get("device")
    if not did:
        return fail(tr("setup.steps.noDevice"), code="no_device")
    kind = c.str("inbound", True, 8)
    if kind not in gmodel.enabled_kinds(state.load()):
        return fail(tr("setup.steps.noInbound"), code="no_inbound", action={"stage": "protocols"})
    r = gops.device_grant(c.runner, did, kind)
    return res("ok", "", data={"device": did, "inbound": kind, "access": (r.get("device") or {}).get("access")})


@step("issue", "done", secret=True, record=False)
def s_issue(c):
    """Settings of a device for its platform — the only step with secrets: QR (vpn://), files (.conf, .ovpn,
    .mobileconfig, PowerShell), fields to type (L2TP, IKEv2, SSTP, PPTP: address, login, password, PSK)."""
    from ..gw import ops as gops
    from .. import qr
    did = c.str("device", max_len=32) or model.load()["answers"].get("device")
    if not did:
        return fail(tr("setup.steps.noDevice"), code="no_device")
    r = gops.issue(c.runner, did, inbound=c.str("inbound", max_len=8), os_name=c.str("os", max_len=16), where="panel")
    if c.body.get("png"):
        qr.add_pngs(r)
    model.update(lambda doc: doc["answers"].update({"issued_at": int(_now()), "issued_kind": r.get("kind")}))
    return res("ok", "", data=r)


def _live_device(did):
    from ..gw import live
    doc, age = live.read()
    if doc is None or not live.fresh(age):
        return None
    return (doc.get("devices") or {}).get(did)


@step("connection", "done", record=False)
def s_connection(c):
    from ..gw import model as gmodel
    doc = model.load()
    did = c.str("device", max_len=32) or doc["answers"].get("device")
    if not did:
        return fail(tr("setup.steps.noDevice"), code="no_device")
    since = int(doc["answers"].get("issued_at") or doc["answers"].get("device_at") or _now())
    d = _live_device(did)
    rows = []
    if d is None:
        return res("checking", tr("setup.steps.collectorWait"),
                   data={"conn": "unknown", "since": since})
    online_ = bool(d.get("online"))
    traffic = (d.get("rx") or 0) + (d.get("tx") or 0)
    if not online_:
        conn = "stuck" if _now() - since > CONNECT_STUCK else "wait"
    else:
        conn = "traffic" if traffic > 50 * 1024 else "connected"
    data = {"conn": conn, "since": since, "online_since": d.get("since"), "inbound": d.get("inbound"),
            "bytes": traffic, "stuck_after": CONNECT_STUCK}
    if conn == "stuck":
        st = state.load()
        dev = gmodel.get_device(st, did)
        on = set(gmodel.enabled_kinds(st))
        hints = [{"code": "port", "text": tr("setup.steps.hintPort")}]
        kind = doc["answers"].get("issued_kind")
        if kind == "awg3" and "awg2" in on:
            hints.append({"code": "line31", "text": tr("setup.steps.hintLine31"),
                          "action": {"step": "issue", "inbound": "awg2", "label": tr("setup.steps.hintLine31Label")}})
        tcp = [k for k in ("sstp", "ocserv", "openvpn") if k in on and k in (dev.get("access") or [])]
        if kind in product.AWG_KINDS + ("wg", "l2tp", "ikev2") and tcp:
            hints.append({"code": "udp", "text": tr("setup.steps.hintUdp"),
                          "action": {"step": "issue", "inbound": tcp[0], "label": tr("setup.steps.hintUdpLabel")}})
        data["hints"] = hints
    texts = {"wait": "setup.steps.connWait", "stuck": "setup.steps.connStuck",
             "connected": "setup.steps.connConnected", "traffic": "setup.steps.connTraffic"}
    return res("ok" if conn in ("connected", "traffic") else "checking", tr(texts[conn]), items=rows, data=data)


@step("trace", "done", record=False)
def s_trace(c):
    from ..gw import trace
    doc = model.load()
    did = c.str("device", max_len=32) or doc["answers"].get("device")
    target = c.str("target") or "foreign"
    host = {"ru": "ya.ru", "foreign": "youtube.com"}.get(target, target)
    r = trace.trace(state.load(), did, host)
    return res("ok", "", data=dict(r, target=target, host=host))


def _online_of(devs):
    """Names of the devices on line by the collector (live.json), None when it has no fresh data."""
    from ..gw import live
    try:
        doc, age = live.read()
    except (OSError, ValueError):
        return None
    if doc is None or not live.fresh(age):
        return None
    on = doc.get("devices") or {}
    return [d.get("name") for d in devs if (on.get(d.get("id")) or {}).get("online")]


@step("summary", "done", record=False)
def s_summary(c):
    from ..gw import model as gmodel, ops as gops, registry as gregistry
    from ..panel import conf
    doc = model.load()
    st = state.load()
    cfg = conf.load()
    rows = []
    try:
        ex = gops.exits_view(c.runner)
    except KaskadError:
        ex = []
    skipped = [k for k in product.RAIL[model.mode(doc)] if doc["stages"].get(k) == "skipped"]   # of this mode only
    out = {"panel_url": conf.proxy_url(cfg) or conf.public_url(cfg),
           "protocols": list(gmodel.enabled_kinds(st)),
           # the names for people (gw.registry.title): the page knew only what the plan of stage 2 and the offers of the
           # device had, the rest went out as the code (T-M6-332 №2: «…, AmneziaWG 3.1, l2tp, VLESS»)
           "titles": dict((k, gregistry.title(k)) for k in gmodel.enabled_kinds(st)),
           # «Выходы» как в ExitsInput (3.3.3): ключи, добавленные вручную, по именам; ключи подписки — одной записью
           # «{подписка} (N ключей)» (the page says it in its language)
           "exits": {"names": [e.get("name") for e in ex if not e.get("source")],
                     "subs": _summary_subs(ex), "alive": sum(1 for e in ex if e.get("usable")), "total": len(ex)},
           # «на связи» — the same fact as «Устройство подключилось» (the collector), not what this page saw itself
           # (T-M6-qs330 №12: «ждём» with a device connected); None — the collector has not told yet
           "people": [{"id": u.get("id"), "name": u.get("name"),
                       "devices": [d.get("name") for d in gmodel.devices(st, user=u.get("id"))],
                       "online": _online_of(gmodel.devices(st, user=u.get("id")))}
                      for u in gmodel.users(st)],
           "domain": ((st.get("site") or {}).get("domain") if isinstance(st.get("site"), dict) else None),
           "skipped": skipped, "github": github_state(c.runner, check=False)}
    out["ports"] = _host_ports(c.runner, st, cfg)
    return res("ok", "", items=rows, data=out)


# IP protocols an inbound needs besides its ports (R-333 З6): PPTP does not connect through a host's firewall without
# GRE (IP 47) — Kaskad's own UFW opens it too (gw/inbounds/accel.py)
_IP_PROTOS = {"pptp": ({"name": "gre", "num": 47},)}


def _host_ports(runner, st, cfg):
    """«Откройте порты у хостера» (3.3.3, UX-333 §7 PortsSummary): what listens on this server for the outside — the
    real ports, not a plan. {"inbounds": [{"kind", "title", "ports": ["51820/udp"]}] (installed and on), "forwards":
    [{"proto", "port"}], "domain": the domain of the site or None (443/tcp and 80/tcp of Let's Encrypt), "panel": the
    port of the panel's own address or None (behind the site on the domain — 443 already)}. The page names the rows."""
    from ..gw import ops as gops
    from ..panel import conf
    inb = []
    try:
        for r in gops.inbound_list(runner, st, with_status=False):
            if r.get("installed") and r.get("enabled") and r.get("ports"):
                row = {"kind": r["kind"], "title": r.get("title") or r["kind"], "ports": list(r["ports"])}
                if _IP_PROTOS.get(r["kind"]):
                    row["ip_protos"] = [dict(x) for x in _IP_PROTOS[r["kind"]]]
                inb.append(row)
    except (KaskadError, OSError, ValueError):
        inb = []
    fw = []
    for f in st.get("forwards") or []:
        try:
            fw.append({"proto": state.parse_proto(f.get("proto")), "port": state.parse_port(f.get("port"))})
        except (state.StateError, KaskadError, ValueError, TypeError):
            continue
    pub = (cfg or {}).get("public") or {}
    panel = pub.get("port") if pub.get("on") and isinstance(pub.get("port"), int) else None
    site = st.get("site") if isinstance(st.get("site"), dict) else {}
    return {"inbounds": inb, "forwards": fw, "domain": site.get("domain") or None, "panel": panel}


@step("finish", "done")
def s_finish(c):
    first = []

    def mut(doc):
        # 3.3.7.5 круг 3: the first end of the wizard (never before — no result of «finish», no stage «done»; a link
        # issued again clears finished_at but keeps both) — a new install: the port may go off by itself later
        # (kaskad.panel.portoff: after the first sign-in through the domain, while no link of people has the port)
        if "finish" not in doc["steps"] and doc["stages"].get("done") != "done" and not doc.get("finished_at"):
            first.append(True)
        doc["status"] = "finished"
        doc["finished_at"] = util.now_iso()
        doc["stages"]["done"] = "done"
        link.revoke_all(doc)
    model.update(mut)
    if first:
        from ..panel import portoff
        portoff.mark_pending()
    _drop_tickets()
    util.log_tr("journal.setup.finished")
    return res("ok", tr("setup.steps.finished"))
