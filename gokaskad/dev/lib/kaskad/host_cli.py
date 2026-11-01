"""kaskadctl host — the light health of the server (SPEC-node-score §3.8, A8): item 22 of the SSH menu, MCP.

    host health [--json]      the light, the findings with advice, the checks, the probes. The source as
                              ``hostprobe.read_health`` (§3.9 п. 4): a fresh live.json of the collector, a fresh
                              host-health.json of the bot, else the rules «instant» by a snapshot of its own.
                              ``--json``: {"ok", "health", "live": {"age", "fresh"}, "instant", "by", "hostmon"}
                              (the answer of GET /api/host/health + the settings). Exit 0; a read error — 1.
    host set [--on|--off] [--probes on|off] [--anchors A,B,C|default] [--push-level crit|warn] [--json]
                              the settings ``gateway.hostmon`` and ``gateway.notify.host_level`` under the state
                              lock; the collector takes them at its next round (≤ 10 s). An anchor is checked by
                              ``hostping.valid_anchor`` (the one check of the probes and of ``model.hostmon``).
                              ``--json``: {"ok", "hostmon", "push_level", "changed", "notes", "warnings"}.
    host probes-hint [--json] one line in the language of the server «Можно включить лёгкие пробы сети: …» when the
                              key ``gateway.hostmon.probes`` is absent and the collector works (the gateway on, the
                              engine nft); otherwise nothing. Exit 0. The installer prints it in the result of an
                              update (§10.1 п. 20, 22).

    host tariff [show|set …]  the port speed and the monthly traffic of the plan (the rule «port», the price of the
                              checks) — ``host_tariff.py`` (its own docstring).
    host score | host bench   the server against its own history («N от обычного», NS-D30–NS-D34) and its check
                              (B7): ``host_bench_cli``.

The threshold of the deviation of a link (NS-D25, ``host set dev-threshold``) belongs to stage B (/api/host/score).
"""

import argparse
import ipaddress
import threading
import time
import unicodedata

from . import errors, i18n, state
from .errors import KaskadError
from .i18n import tr

PUSH_LEVELS = ("crit", "warn")
ONOFF = ("on", "off")
MARK = {"crit": "✗", "warn": "!"}
# the units of the rules (hostmon.HOST_RULES[].unit) -> the key of the text of a value
SHOWN_MAX = 64                     # signs of a bad value said back in an error (REV-ns-a8-sec П-1)
RESOLVE_S = 2.0                    # s: the addresses of the name gateway.host (getaddrinfo has no timeout of its own)
UNIT_KEYS = {"pct": "host_cli.unit.pct", "per_core": "host_cli.unit.per_core", "h": "host_cli.unit.h",
             "per_s": "host_cli.unit.per_s", "s": "host_cli.unit.s", "days": "host_cli.unit.days",
             "x": "gw_host.n.x"}


# ── helpers ────────────────────────────────────────────────────────────────

def _say(key, **args):
    from .gw import hostmon
    return hostmon.say(hostmon.msg(key, **args))


def _value(rule_id, value):
    """A value of a check said with its unit («12 %», «1,8 на ядро», «6 дн.»); None — nothing to say."""
    from .gw import hostmon
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    rule = hostmon.RULES.get(rule_id) or {}
    key = UNIT_KEYS.get(rule.get("unit"))
    if rule.get("unit") == "x":
        return _say(key, n=round(float(value), 1))
    v = round(float(value), 2)
    return _say(key, v=v) if key else _say("host_cli.unit.count", v=v)


def _cap(text):
    text = str(text or "")
    return text[:1].upper() + text[1:]


def _shown(value):
    """A value given by a person, said back in an error: no control or format signs (ESC, OSC, bidi — each one «?»),
    at most SHOWN_MAX signs (REV-ns-a8-sec П-1: A6/A7 may pass the input of a person to ``apply_set``)."""
    text = "".join("?" if unicodedata.category(c)[0] == "C" else c for c in str(value)[:SHOWN_MAX + 1])
    return text if len(text) <= SHOWN_MAX else text[:SHOWN_MAX - 1] + "…"


def _name_addrs(name, timeout=RESOLVE_S):
    """The addresses of the name ``gateway.host`` by the node's resolver (IPv4 and IPv6), ≤ ``timeout`` s; a hanging
    or failing resolver — none (the check of the interfaces and of a literal host still stands)."""
    import socket
    box = {}

    def run():
        try:
            box["ips"] = [ai[4][0] for ai in socket.getaddrinfo(name, None, 0, socket.SOCK_STREAM)]
        except (OSError, UnicodeError, ValueError):
            box["ips"] = []
    t = threading.Thread(target=run, name="kaskad-host-dns", daemon=True)
    t.start()
    t.join(timeout)
    out = []
    for ip in box.get("ips") or []:
        ip = str(ip).split("%")[0]
        if ip not in out:
            out.append(ip)
    return out[:16]


def _host_name(st):
    """``gateway.host`` when it is a name (not an address), else None."""
    from .gw import model
    h = str(model.peek(st).get("host") or "").strip()
    if not h or len(h) > 253:
        return None
    try:
        ipaddress.ip_address(h)
        return None
    except ValueError:
        return h


def own_addrs(st):
    """The addresses an anchor may not be (REV-ns-a8-sec З-1): ``model.own_addrs`` (``gateway.host`` when an address)
    and the addresses of the interfaces — ``hostprobe.own_addrs`` (/proc/net/fib_trie, /proc/net/if_inet6; A5 круг 2),
    the very list the collector checks in its round. Unreadable /proc — the state alone."""
    from .gw import hostprobe, model
    own = list(model.own_addrs(st))
    try:
        own += [a for a in hostprobe.own_addrs() if a not in own]
    except Exception:                                         # noqa: BLE001 — unreadable /proc: the state alone
        pass
    return own


def _collector_on(st):
    from .gw import hostprobe
    return hostprobe._collector_on(st)


def _has_ipv6_route():
    """The node has an IPv6 default route (/proc/net/ipv6_route, as ``hostprobe._route``); unreadable — True (no
    warning without a reason)."""
    from . import paths
    try:
        with open(paths.P("/proc/net/ipv6_route"), "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except (OSError, ValueError):
        return True
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 10 and f[0] == "0" * 32 and f[1] == "00" and f[9] != "lo":
            return True
    return False


# ── health ─────────────────────────────────────────────────────────────────

def health_doc(now=None, st=None):
    """The answer of ``host health --json`` (the contract of GET /api/host/health, §3.8, + the settings)."""
    from .gw import hostmon, hostprobe, live, model
    now = time.time() if now is None else now
    st = state.load() if st is None else st
    h, by = hostprobe.read_health(now, st)
    h = hostmon.health_in(h)                                  # the texts in the language of this reader
    try:
        _doc, age = live.read(now)
    except Exception:                                         # noqa: BLE001 — a broken live.json: no age
        age = None
    return {"ok": True, "health": h, "live": {"age": age, "fresh": bool(live.fresh(age))},
            "instant": bool((h or {}).get("instant")), "by": by, "hostmon": model.hostmon(st)}


def _check_lines(h, shown_top):
    """The table of the checks, folded: the warnings not in the top, «В норме: …», one line per reason of «ждёт» and
    «не проверяется»."""
    from .gw import hostmon
    checks = h.get("checks") if isinstance(h.get("checks"), dict) else {}
    bad, fine, groups, order = [], [], {}, []
    for rule in hostmon.HOST_RULES:
        rid = rule["id"]
        c = checks.get(rid)
        if not isinstance(c, dict):
            continue
        name = _say("gw_host.check." + rid)
        shown = c.get("shown") or c.get("level")
        if shown in ("warn", "crit"):
            if rid not in shown_top:
                val = _value(rid, c.get("value"))
                bad.append("  %s %s%s" % (MARK[shown], name, (" — " + val) if val else ""))
            continue
        if shown in ("ok", "info"):
            fine.append(name)
            continue
        why = c.get("why_text") or (hostmon.say(c["why_msg"]) if isinstance(c.get("why_msg"), dict) else "")
        if c.get("level") == "na":
            key = ("na", why)
        elif isinstance(c.get("why_msg"), dict) and c["why_msg"].get("key") == "gw_host.why.wait":
            key = ("wait", _say("gw_host.why.wait_any"))      # «ждёт истории» — one line, not one per minutes (П-4)
        else:
            key = ("wait", why or _say("gw_host.why.wait_any"))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(name)
    out = list(bad)
    if fine:
        out.append("  ✓ " + tr("host_cli.ok_list", names=", ".join(fine)))
    for key in order:
        names = ", ".join(groups[key])
        if key[0] == "na":
            out.append("  · " + (tr("host_cli.na_list", why=key[1], names=names) if key[1]
                                 else tr("host_cli.na_list_bare", names=names)))
        else:
            out.append("  … %s: %s" % (_cap(key[1]), names))
    return out


def _probe_lines(h, cfg, st, screen=False):
    out = []
    if not cfg.get("enabled"):
        return out
    if not cfg.get("probes"):
        out.append(tr("host_cli.probes.off_screen" if screen else "host_cli.probes.off"))
        return out
    out.append(tr("host_cli.probes.on", addrs=", ".join(cfg.get("anchors") or [])))
    if not _collector_on(st):
        out.append("  " + tr("host_cli.probes.idle" if st.get("engine") == "nft" else "host_cli.probes.idle_legacy"))
        return out
    pr = h.get("probes") if isinstance(h.get("probes"), dict) else None
    if not pr:
        return out
    parts = []
    if isinstance(pr.get("loss10"), (int, float)):
        parts.append(_say("host_cli.probes.loss", v=round(float(pr["loss10"]), 1)))
    if isinstance(pr.get("rtt10"), (int, float)):
        parts.append(_say("host_cli.probes.rtt", v=round(float(pr["rtt10"]), 1)))
    if parts:
        out.append("  " + tr("host_cli.probes.last10", what=", ".join(parts)))
    pts = []
    for a in pr.get("anchors") or []:
        if not isinstance(a, dict) or not a.get("addr"):
            continue
        bits = []
        if isinstance(a.get("loss10"), (int, float)):
            bits.append(_say("host_cli.probes.loss", v=round(float(a["loss10"]), 1)))
        if isinstance(a.get("rtt10"), (int, float)):
            bits.append(_say("host_cli.probes.rtt", v=round(float(a["rtt10"]), 1)))
        pts.append("%s — %s" % (a["addr"], ", ".join(bits) if bits else _say("host_cli.probes.none")))
    if pts:
        out.append("  " + tr("host_cli.probes.points", list="; ".join(pts)))
    return out


def health_text(doc, st=None, screen=False):
    """The lines of ``host health`` for people. ``screen`` (``--screen``, the screen of item 22 of the SSH menu prints
    the lines as they are): «turn on» points to the number on that screen, not to a command of kaskadctl (П-2)."""
    h = doc.get("health") if isinstance(doc.get("health"), dict) else {}
    cfg = doc.get("hostmon") or {}
    level = h.get("level") if h.get("level") in ("ok", "warn", "crit", "wait", "off") else "wait"
    lines = [tr("host_cli.light", light=_say("gw_host.light." + level))]
    if not cfg.get("enabled", True):                          # the check off: how to turn it on (REV-ns-a8 З-2)
        lines.append("  " + tr("host_cli.off_hint_screen" if screen else "host_cli.off_hint"))
    if doc.get("instant") and level != "off":
        lines.append("  " + _say("gw_host.light.instant"))
    if h.get("hint") == "bot_python":
        lines.append("  " + _say("gw_host.hint.bot_python"))
    top = [f for f in h.get("top") or [] if isinstance(f, dict) and f.get("level") in MARK]
    for f in top:
        lines.append("%s %s" % (MARK[f["level"]], f.get("text") or _say("gw_host.check." + str(f.get("id")))))
        if f.get("better_msg") or f.get("stale_msg"):
            from .gw import hostmon
            lines.append("  " + hostmon.say(f.get("better_msg") or f.get("stale_msg")))
        if f.get("advice"):
            lines.append("  " + f["advice"])
    for i in h.get("info") or []:
        if isinstance(i, dict) and i.get("text"):
            lines.append("· " + i["text"])
    if level != "off":
        chk = _check_lines(h, set(str(f.get("id")) for f in top))
        if chk:
            lines.append(tr("host_cli.checks"))
            lines.extend(chk)
        lines.extend(_probe_lines(h, cfg, state.load() if st is None else st, screen))
    return lines


def cmd_health(args):
    from .cli import emit
    try:
        st = state.load()
        doc = health_doc(st=st)
        text = None if getattr(args, "json", False) else health_text(doc, st, getattr(args, "screen", False))
    except Exception as e:                                   # noqa: BLE001 — a read error: code 1 with the reason
        raise KaskadError(tr("host_cli.read_fail", error=e), code=errors.ERROR)
    emit(args, doc, text)
    return errors.OK


# ── set ────────────────────────────────────────────────────────────────────

def parse_anchors(text, own=(), name_own=(), name=None):
    """``--anchors A,B,C`` -> (list of addresses | None for «default», warnings). A bad one, more than MAX_ANCHORS —
    KaskadError (USAGE) with the words; the order is kept, repeats dropped. ``own`` — the addresses of the node,
    ``name_own`` — those of its name ``name`` (``gateway.host``)."""
    from .gw import hostping
    raw = str(text if text is not None else "").strip()
    if raw.lower() == "default":
        return None, []
    items = [x.strip() for x in raw.split(",") if x.strip()]
    if not items:
        raise KaskadError(tr("host_cli.set.no_anchors"), code=errors.USAGE)
    out = []
    for a in items:
        good = hostping.valid_anchor(a, tuple(own) + tuple(name_own))
        if good is None:
            if hostping.valid_anchor(a, own) is not None:
                raise KaskadError(tr("host_cli.set.name_anchor", addr=_shown(a), host=_shown(name)), code=errors.USAGE)
            if hostping.valid_anchor(a, ()) is not None:
                raise KaskadError(tr("host_cli.set.own_anchor", addr=_shown(a)), code=errors.USAGE)
            raise KaskadError(tr("host_cli.set.bad_anchor", addr=_shown(a)), code=errors.USAGE)
        if good not in out:
            out.append(good)
    if len(out) > hostping.MAX_ANCHORS:
        raise KaskadError(tr("host_cli.set.too_many", max=hostping.MAX_ANCHORS, n=len(out)), code=errors.USAGE)
    warn = []
    if any(ipaddress.ip_address(a).version == 6 for a in out) and not _has_ipv6_route():
        warn = [tr("host_cli.set.no_ipv6", addr=a) for a in out if ipaddress.ip_address(a).version == 6]
    return out, warn


def _onoff(value, what):
    if value is None:
        return None
    v = str(value).strip().lower()
    if v not in ONOFF:
        raise KaskadError(tr("host_cli.set.bad_onoff", what=what, value=_shown(value)), code=errors.USAGE)
    return v == "on"


def _dev_pct(value):
    """``--dev-threshold`` -> an int 5…30 (NS-D25: percent of the raw value); else a refusal, nothing written."""
    from . import score
    v = str(value).strip().rstrip("%").strip()
    if not (v.isdigit() and len(v) <= 3 and score.PCT_MIN <= int(v) <= score.PCT_MAX):
        raise KaskadError(tr("host_cli.set.bad_dev", value=_shown(value), lo=score.PCT_MIN, hi=score.PCT_MAX),
                          code=errors.USAGE)
    return int(v)


CODE_LEN = 10                   # hex of the ``code`` of a change of the checks (as the schedule and the tariff)
NEEDS_CODE = ("bench_retry", "dev_pct", "dev_spread")         # MCP: these only with the code of a preview (З-2)


def set_code(retry, rule, asked):
    """The ``code`` of a change of ``host set``: the settings of the checks now (the repeat, the ▲/▼ rule) + all that is
    asked. Another change, or the settings changed meanwhile (the panel, kaskadctl) — another code."""
    import hashlib
    import json
    raw = json.dumps({"r": retry, "d": rule, "a": asked}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()[:CODE_LEN]


def apply_set(on=None, probes=None, anchors=None, push_level=None, bench_retry=None, dev_pct=None, dev_spread=None,
              dry=False, code=None, yes=False, by_mcp=False):
    """Write the settings (each None — unchanged; ``anchors`` a text as ``--anchors``) under the state lock ->
    the answer of ``host set --json``. Every argument is checked before anything is written. ``bench_retry`` —
    the repeat after a sharp drop (SS §2.8 п. 5; off — the repeats waiting stop), ``dev_pct``/``dev_spread`` — the rule
    of the ▲/▼ marks (NS-D25, ``gateway.hostmon.dev``). ``dry`` — the same answer + ``dry`` and ``code``, nothing
    written; ``code`` — write only the change that ``dry`` showed, checked under the lock (REV-ns-b14 З-2: MCP
    ``host_set`` changes the checks only after the human saw it, as the schedule). The repeats turned on make the
    traffic grow: with a heavy list (``host_sched.needs_consent``) — only with ``yes`` (the human's «yes»: ``--yes`` or
    the question in a terminal), never ``by_mcp`` (REV-ns-b14-2 З-1: the rule С-1 of the panel and the schedule)."""
    from .gw import model
    probes_v = _onoff(probes, "--probes")
    retry_v = _onoff(bench_retry, "--bench-retry")
    spread_v = _onoff(dev_spread, "--dev-spread")
    pct_v = None if dev_pct is None else _dev_pct(dev_pct)
    level = None
    if push_level is not None:
        level = str(push_level).strip().lower()
        if level not in PUSH_LEVELS:
            raise KaskadError(tr("host_cli.set.bad_level", value=_shown(push_level)), code=errors.USAGE)
    if (on is None and probes_v is None and anchors is None and level is None and retry_v is None and pct_v is None
            and spread_v is None):
        raise KaskadError(tr("host_cli.set.nothing"), code=errors.USAGE)
    changed, notes, warnings = [], [], []
    name, name_own = None, []
    if anchors is not None and str(anchors).strip().lower() != "default":
        name = _host_name(state.load())                       # the resolver — before the lock, ≤ RESOLVE_S
        name_own = _name_addrs(name) if name else []
    with state.locked():
        st = state.load()
        own = own_addrs(st)
        if _host_name(st) != name:                            # gateway.host changed meanwhile: not its addresses
            name, name_own = None, []
        addrs, reset = None, False
        if anchors is not None:
            addrs, warnings = parse_anchors(anchors, own, name_own, name)
            reset = addrs is None
        g = st.get("gateway")
        if not isinstance(g, dict):
            g = st["gateway"] = {}
        hm = dict(g["hostmon"]) if isinstance(g.get("hostmon"), dict) else {}
        dev0 = hm.get("dev")
        before = (model.hostmon(st), _level_of(st), model.bench(st)["retry"], model.dev_rule(st))
        key = set_code(before[2], before[3], [on, probes_v, anchors, level, retry_v, pct_v, spread_v])
        if code is not None and not (isinstance(code, str) and len(code) == CODE_LEN and code == key):
            raise KaskadError(tr("host_cli.set.code_bad"), code=errors.CONFLICT, data={"kind": "code_mismatch"})
        consent, rcost = _retry_consent(st, before[2], retry_v)
        if consent and not dry and (by_mcp or not yes):          # checked under the lock, before anything is written
            from .host_sched import Refused
            mb = _retry_mb_said(rcost)
            if by_mcp:                                            # SS §6.3: MCP asks no questions
                raise Refused(409, "heavy", tr("host_cli.set.retry_heavy_mcp", mb=mb), cost=rcost)
            raise Refused(409, "need_confirm", tr("host_cli.set.retry_need_yes", mb=mb), cost=rcost)
        if on is not None:
            hm["enabled"] = on
        if probes_v is not None:
            hm["probes"] = probes_v
        if reset:
            hm.pop("anchors", None)
        elif addrs is not None:
            hm["anchors"] = addrs
        if retry_v is not None:
            b = dict(hm["bench"]) if isinstance(hm.get("bench"), dict) else {}
            b["retry"] = retry_v
            hm["bench"] = b
        if pct_v is not None or spread_v is not None:
            rule = dict(before[3])
            if pct_v is not None:
                rule["pct"] = pct_v
            if spread_v is not None:
                rule["use_spread"] = spread_v
            hm["dev"] = rule
        g["hostmon"] = hm
        if level is not None:
            n = dict(g["notify"]) if isinstance(g.get("notify"), dict) else {}
            n["host_level"] = level
            g["notify"] = n
        after = (model.hostmon(st), _level_of(st), model.bench(st)["retry"], model.dev_rule(st))
        if not dry and (after != before or hm.get("dev") != dev0):  # a broken rule replaced by the default: saved too
            state.save(st)
    cfg, lvl, retry_now, rule_now = after
    collector = _collector_on(st)
    if on is not None and on != before[0]["enabled"]:
        changed.append("enabled")
        notes.append(tr("host_cli.set.enabled_on" if on else "host_cli.set.enabled_off"))
    if probes_v is not None and (probes_v != before[0]["probes"] or not before[0]["probes_key"]):
        changed.append("probes")
        if not probes_v:
            notes.append(tr("host_cli.set.probes_off"))
        elif not cfg["enabled"]:                              # the check itself off — the true reason (З-2)
            notes.append(tr("host_cli.set.probes_on_disabled"))
        elif collector:
            notes.append(tr("host_cli.set.probes_on"))
        elif st.get("engine") == "nft":
            notes.append(tr("host_cli.set.probes_on_idle"))
        else:                                                 # the old engine: the gateway needs nftables (П-3)
            notes.append(tr("host_cli.set.probes_on_legacy"))
    if anchors is not None and cfg["anchors"] != before[0]["anchors"]:
        changed.append("anchors")
    if anchors is not None:
        notes.append(tr("host_cli.set.anchors_default" if reset else "host_cli.set.anchors",
                        addrs=", ".join(cfg["anchors"])))
    if level is not None:
        if lvl != before[1]:
            changed.append("push_level")
        notes.append(tr("host_cli.set.level_" + lvl))
    if retry_v is not None:
        if retry_now != before[2]:
            changed.append("bench_retry")
            if not retry_now and not dry:                     # the repeats waiting go too (as the panel, B13)
                from .bench import sched_unit
                runner = sched_unit.system_runner()
                if runner is not None:
                    sched_unit.stop_retries(runner)
        notes.append(tr("host_cli.set.retry_" + ("on" if retry_now else "off")))
    if pct_v is not None or spread_v is not None:
        if rule_now != before[3]:
            changed.append("dev")
        notes.append(tr("host_cli.set.dev_" + ("spread" if rule_now["use_spread"] else "plain"), pct=rule_now["pct"]))
    if not changed:
        notes.insert(0, tr("host_cli.set.same"))
    elif collector and set(changed) & {"enabled", "anchors"} and "probes" not in changed and not dry:
        notes.append(tr("host_cli.set.soon"))
    doc = {"ok": True, "hostmon": cfg, "push_level": lvl, "bench_retry": retry_now, "dev_rule": rule_now,
           "changed": changed, "notes": notes, "warnings": warnings}
    if dry:
        doc.update(dry=True, code=key, consent=consent)
        notes.insert(0, tr("host_cli.set.preview"))
        if consent:                                               # the price of the repeats, before the «yes»
            notes.insert(1, tr("host_cli.set.retry_heavy_mcp" if by_mcp else "host_cli.set.retry_heavy_dry",
                               mb=_retry_mb_said(rcost)))
            doc["cost"] = rcost
    return doc


def _retry_consent(st, retry_was, retry_v):
    """The repeats off -> on with the list of ``st``: (the human's «yes» needed, the price with the repeats) — the same
    rule as the panel and the schedule (``host_sched.needs_consent``: heavy and the cap of the repeats grows)."""
    if retry_v is not True or retry_was:
        return False, None
    from . import host_sched
    from .gw import model
    m = model.bench(st)
    now = int(time.time())
    new = host_sched.cost(m["schedule"], m, now, True, st=st)
    return host_sched.needs_consent(host_sched.cost(m["schedule"], m, now, False, st=st), new), new


def _retry_mb_said(c):
    from .host_sched import _mb
    return _mb(((c or {}).get("retry") or {}).get("max_mb") or 0).replace("≈", "").strip()     # «до 8 ГБ», not «до ≈»


def _level_of(st):
    from .gw import model
    lvl = model.notify(st).get("host_level")
    return lvl if lvl in PUSH_LEVELS else "crit"


def cmd_set(args):
    from .cli import emit
    if args.on and args.off:
        raise KaskadError(tr("host_cli.set.on_off_both"), code=errors.USAGE)
    on = True if args.on else (False if args.off else None)
    code = getattr(args, "code", None)
    asked = dict(bench_retry=args.bench_retry, dev_pct=args.dev_threshold, dev_spread=args.dev_spread)
    # MCP (--by-mcp): the checks (the repeat — up to 2 checks a day more, the ▲/▼ rule) change only with the code of the
    # preview — the first call writes nothing, the second, with the code, writes exactly that (REV-ns-b14 З-2)
    by_mcp, yes = getattr(args, "by_mcp", False), getattr(args, "yes", False)
    dry = by_mcp and code is None and any(v is not None for v in asked.values())
    kw = dict(on=on, probes=args.probes, anchors=args.anchors, push_level=args.push_level, code=code, by_mcp=by_mcp,
              **asked)
    if not (dry or yes or by_mcp or getattr(args, "json", False)) and args.bench_retry is not None:
        from . import host_sched
        if host_sched._isatty():                                  # a person at a terminal: the question, numbers
            pv = apply_set(dry=True, **kw)
            if pv.get("consent"):
                if not host_sched._ask_heavy(pv["cost"], tr("host_cli.set.retry_ask", mb=_retry_mb_said(pv["cost"]))):
                    raise host_sched.Refused(409, "cancelled", tr("host_sched.cancelled"), code=errors.ERROR)
                yes = True
    doc = apply_set(dry=dry, yes=yes, **kw)
    emit(args, doc, doc["warnings"] + doc["notes"])
    return errors.OK


# ── bench: the schedule (B11; the rest of the commands — B7/B14) ───────────

def schedule_apply(runner=None):
    """``kaskadctl host bench schedule apply`` — the timer as the state wants it (the repeat after «таймер не обновлён»)."""
    from . import state, sysrun
    from .bench import fix_in, log_fixed, sched_unit
    runner = runner or sysrun.default_runner()
    with state.transaction() as st:
        m = fix_in(st)
        doc = sched_unit.apply(runner, st)
    log_fixed(m)
    return doc


def cmd_bench(args):
    from .cli import emit
    from .bench import sched_run
    bc = getattr(args, "bench_cmd", None)
    if bc and not (bc == "schedule" and getattr(args, "sched_cmd", None) == "apply"):
        from . import host_sched                    # the list, the history (B14)
        return host_sched.cmd(args)
    if bc == "schedule":
        doc = schedule_apply()
        lines = [] if doc.get("applied") else [tr("bench.log.sched_not_applied", why=doc.get("why") or "-")]
        emit(args, doc, lines)
        return errors.OK if doc.get("applied") else errors.ERROR
    if args.retry is not None:
        emit(args, sched_run.retry(args.retry, links=[x for x in (args.links or "").split(",") if x]), [])
        return errors.OK
    if args.scheduled:
        emit(args, sched_run.scheduled(), [])
        return errors.OK
    raise KaskadError(tr("host_cli.bench.what"), code=errors.USAGE)


# ── probes-hint ────────────────────────────────────────────────────────────

def probes_hint(st=None):
    """The line «Можно включить лёгкие пробы сети: …» in the language of the server, or None: only when the key
    ``gateway.hostmon.probes`` is absent (§10.1 п. 22) and the collector works (without it no probe goes, NS-D1)."""
    from .gw import hostmon, model
    st = state.load() if st is None else st
    cfg = model.hostmon(st)
    if cfg["probes_key"] or not cfg["enabled"] or not _collector_on(st):
        return None
    lang = i18n.saved_lang()
    return hostmon.say(hostmon.msg("gw_host.event.probes_hint", addrs=", ".join(cfg["anchors"])), lang)


def cmd_probes_hint(args):
    from .cli import emit
    try:
        line = probes_hint()
    except KaskadError:
        line = None                                           # a broken state.json: no hint, never a failed update
    emit(args, {"ok": True, "hint": line}, line or "")
    return errors.OK


def register(sub):
    from .cli import add_json_flag
    p = sub.add_parser("host", help=tr("host_cli.help"))
    s = p.add_subparsers(dest="host_cmd")
    hp = add_json_flag(s.add_parser("health", help=tr("host_cli.help.health")))
    hp.add_argument("--screen", action="store_true", help=argparse.SUPPRESS)      # the screen of item 22 (SSH menu)
    hp.set_defaults(func=cmd_health)
    st = add_json_flag(s.add_parser("set", help=tr("host_cli.help.set")))
    st.add_argument("--on", action="store_true", help=tr("host_cli.help.on"))
    st.add_argument("--off", action="store_true", help=tr("host_cli.help.off"))
    st.add_argument("--probes", metavar="on|off", help=tr("host_cli.help.probes"))
    st.add_argument("--anchors", metavar="A,B,C", help=tr("host_cli.help.anchors"))
    st.add_argument("--push-level", dest="push_level", metavar="crit|warn", help=tr("host_cli.help.push_level"))
    st.add_argument("--bench-retry", dest="bench_retry", metavar="on|off", help=tr("host_cli.help.bench_retry"))
    st.add_argument("--dev-threshold", dest="dev_threshold", metavar="5…30", help=tr("host_cli.help.dev_threshold"))
    st.add_argument("--dev-spread", dest="dev_spread", metavar="on|off", help=tr("host_cli.help.dev_spread"))
    st.add_argument("--code", metavar="C", help=tr("host_cli.help.set_code"))
    st.add_argument("--yes", action="store_true", help=tr("host_cli.help.set_yes"))
    st.add_argument("--by-mcp", dest="by_mcp", action="store_true", help=argparse.SUPPRESS)
    st.set_defaults(func=cmd_set)
    add_json_flag(s.add_parser("probes-hint", help=tr("host_cli.help.probes_hint"))).set_defaults(
        func=cmd_probes_hint)
    from . import host_tariff                                                        # C1: host tariff [show|set]
    host_tariff.register(s, add_json_flag)
    from . import host_bench_cli                      # host score, host bench (B7; the timer of B11 — cmd_bench here)
    host_bench_cli.register(s)
