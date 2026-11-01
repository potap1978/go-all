"""``kaskadctl gw …`` of the exit policy and the groups (stage 1, T10: docs/k3/SPEC-stage1-exits §7.7, D-050, D-078).

    gw groups [--json]                              the groups: role, exits, the tier now, the last step, devices
                                                    without an exit; ``name`` and ``label`` (§3.1); the SSH menu shows
                                                    its ``lines`` («Состояние и группы», §7.6)
    gw policy [--group G] [--json]                  the policy of the exits, only shown (a raw JSON is never taken)
    gw policy-reset [--group G] [--preset P] [--dry] [--yes] [--rev N]   the fields of a preset into a group
                                                    (default «main» and «compat»)
    gw exit-move <id> --group G [--tier T] [--confirm-role] [--dry] [--yes] [--rev N]
    gw group-del <gid> [--exits-to G | --with-keys] [--reassign direct|G] [--tg-proxy-to G] [--new-exits-to G]
                       [--subs-to G] [--dry] [--yes] [--rev N]   ``--with-keys`` — a Russian group with its keys (8а)
    gw group-flap-standby <gid> on|off [--dry] [--rev N]    «Нестабильный выход — в запас на 15 мин»
    gw exit-history <id> [--days N] [--limit N] [--before TS]   the transitions with their reasons
    gw exit-speedtest <id> [--force]                the speed test now, the answer waited ≤ 60 s

Every write goes through ``gw.policy_ops`` (T7) — the same functions as the panel's buttons, with the same ``rev``
(``--rev``: compared under the lock of the write; another — code 3 «политику изменили»), the same dry run (``--dry``:
«что изменится», nothing written) and the same refusals. Groups, tiers, rules, probes and the choice of the groups of
the bot's proxy and of the new keys are set only in the panel (plans F and G: one model of the settings — the form, not
a raw JSON); the commands here are the operational ones of stage 1 (D-050). A destructive one (``policy-reset``,
``group-del``, ``exit-move`` — KT3-9: any move changes the path of traffic) asks with numbers on a terminal and refuses without ``--yes`` elsewhere;
«1» writes what the question showed (``ask_write``: the ``rev`` and the moves of its dry run — changed meanwhile, it is
counted and asked again).
"""

import math
import shlex
import sys
import time

from .. import errors, state
from ..cli import add_json_flag, emit
from ..i18n import lower_first, tr, trn
from . import model, policy as P, policy_ops as pops

DEVICES_SHOWN = 10                  # devices named in «что изменится» (the rest — a number)
SPEED_WAIT = 60                     # s: the answer of ``exit-speedtest`` (§7.7: a limit, never an endless loop)
SPEED_POLL = 2.0
ASK_ROUNDS = 3                      # «что изменится» counted and asked again when it changed while the question was up
QUOTA_GB_MAX = P.QUOTA_GB[1]        # GB: the quota of the CLI = of the core check (MCP: ^[0-9]{1,6}(\.[0-9])?$)
_sleep = time.sleep                 # tests patch them
_now = time.time


def _r():
    from . import cli
    return cli._r()


def _warn(text):
    from . import cli
    cli._warn(text)


def _tty():
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def confirm(args, question, yes_label, no_label, hint):
    """A destructive command: ``--yes`` — go on; a terminal (not --json) — the question with numbers, «2) …» by
    default (the rule of the owner: numbers, never y/n); elsewhere — USAGE with ``hint`` (how to say yes). -> bool."""
    if getattr(args, "yes", False):
        return True
    if getattr(args, "json", False) or not _tty():
        raise errors.KaskadError(tr("gw_cli.pol.need_yes", hint=hint), code=errors.USAGE)
    w = sys.stderr.write
    w(question + "\n")
    w("  1) %s\n  2) %s\n" % (yes_label, no_label))
    while True:
        w(tr("gw_cli.ask.choice", n=2))
        sys.stderr.flush()
        ans = sys.stdin.readline()
        if not ans:
            w("\n")
            return False
        ans = ans.strip() or "2"
        if ans in ("1", "2"):
            return ans == "1"
        w(tr("gw_cli.ask.bad", n=2) + "\n")


def _again(e, args):
    """A refusal of the write after «1» that a new question answers (REV-s1-t10-sec №1): the moves changed (a route
    set meanwhile — routes are outside ``rev``), or the policy changed and no ``--rev`` was given."""
    if not isinstance(e, pops.Refused):
        return False
    return e.key == "moves_changed" or (e.key == "policy_changed" and args.rev is None)


def ask_write(args, call, ask, hint):
    """A destructive write of policy_ops with the question of the CLI. ``call(want_rev, dry, want_moves)`` — the write;
    ``ask(first)`` -> (the question, the label of «1») for the dry run ``first``; ``hint`` — the command with ``--yes``.
    ``--dry`` — the dry run; ``--yes`` — the write at once (``--rev`` as given: nobody was shown anything). Else the dry
    run, «что изменится» and the question with numbers; «1» writes with the ``rev`` of THAT dry run (or ``--rev``) and
    its moves: what was shown is what is written. Changed meanwhile (the panel set a person's route into the group, or
    changed the policy) — counted and asked again, at most ASK_ROUNDS times. -> the answer, or None (cancelled)."""
    if args.dry:
        return call(args.rev, True, None)
    if args.yes:
        return call(args.rev, False, None)
    n = 0
    while True:
        first = call(args.rev, True, None)
        if not args.json and _tty():
            if n:
                sys.stderr.write(tr("gw_cli.pol.changed_meanwhile") + "\n")
            for line in moves_lines(first.get("moves")):
                sys.stderr.write(line + "\n")
        question, yes = ask(first)
        if not confirm(args, question, yes, tr("gw_cli.ask.cancel"), hint):
            return None
        try:
            return call(args.rev if args.rev is not None else first.get("rev"), False, pops.moves_sig(first.get("moves")))
        except errors.KaskadError as e:
            n += 1
            if not _again(e, args) or n >= ASK_ROUNDS:
                raise


def _cancelled(args):
    emit(args, {"ok": True, "cancelled": True}, tr("gw_cli.ask.cancelled"))
    return errors.OK


def _cmd(*parts, **flags):
    """A command line to copy: ``kaskadctl`` + ``parts`` + the given ``flags`` ({"--exits-to": "main"}), quoted."""
    out = ["kaskadctl"] + [str(p) for p in parts]
    for k, v in flags.items():
        if v is True:
            out.append(k)
        elif v not in (None, False, ""):
            out += [k, str(v)]
    return " ".join(shlex.quote(x) for x in out)


# ── words ──────────────────────────────────────────────────────────────────

def _labels(doc):
    return dict((g.get("id"), P.label(g)) for g in (doc or {}).get("groups") or [] if isinstance(g, dict))


def _exit_names(st):
    """{exit id: the name people see} of the pool (no pool — {})."""
    from . import exits
    try:
        return dict((e["id"], exits.label(st, e)) for e in exits.pool(None, roles=exits.ROLES))     # Russian too (№31г)
    except Exception:                                   # noqa: BLE001 — names only: the ids then
        return {}


def _exit_name(eid, st=None):
    """The name people see of one exit (the summaries of the CLI, §8: the name, not the id); unknown — the id."""
    return _exit_names(state.load() if st is None else st).get(eid) or str(eid)


def _place(p, groups, names):
    """A place of a device (``moves``) in words: «Основная · Вена», «Основная: с сервера напрямую», «напрямую»."""
    if not isinstance(p, dict):
        return tr("gw_cli.pol.place_none")
    step = p.get("step")
    if p.get("group") is None:
        return tr("gw_cli.pol.step.direct" if step == "direct" else "gw_cli.pol.step.block")
    g = groups.get(p["group"], p["group"])
    if step == "exit":
        return "%s · %s" % (g, names.get(p.get("exit"), p.get("exit") or ""))
    return "%s: %s" % (g, tr("gw_cli.pol.step.last_direct" if step == "last_direct" else "gw_cli.pol.step.last_block"))


def moves_lines(moves, st=None, groups=None):
    """«Что изменится» of a dry run or a write (``policy_ops.impact``): how many devices change their path, where to,
    up to DEVICES_SHOWN of them by name. ``groups`` — {gid: name} read before the write (a deleted group keeps its
    name in «что изменилось»)."""
    m = moves if isinstance(moves, dict) else {}
    n = int(m.get("total") or 0)
    if not n:
        return [tr("gw_cli.pol.moves_none")]
    st = state.load() if st is None else st
    groups = dict(_labels(pops._base(st)), **(groups or {}))
    names = _exit_names(st)
    out = [tr("gw_cli.pol.moves", n=n, devices=trn("gw_cli.n.devices", n))]
    for gid, g in sorted((m.get("by_group") or {}).items()):
        k = int(g.get("total") or 0)
        if k:
            out.append("  " + tr("gw_cli.pol.moves_group", group=groups.get(gid, gid), n=k))
        for key in ("last_direct", "last_block"):
            if g.get(key):
                out.append("    " + tr("gw_cli.pol.moves_" + key, n=g[key]))
    for key in ("direct", "block"):
        if m.get(key):
            out.append("  " + tr("gw_cli.pol.moves_" + key, n=m[key]))
    devs = [d for d in m.get("devices") or [] if isinstance(d, dict)]
    for d in devs[:DEVICES_SHOWN]:
        who = d.get("name") or d.get("device")
        # T-q Н-move: a device moved by its Russian side only (its foreign path the same) — the Russian path, not
        # «Основная · X → Основная · X»
        if d.get("from") != d.get("to") or not d.get("ru"):
            out.append("  %s: %s → %s" % (who, _place(d.get("from"), groups, names), _place(d.get("to"), groups, names)))
        if d.get("ru"):
            out.append("  " + tr("gw_cli.pol.moves_ru_dev", name=who, a=_place(d.get("ru_from"), groups, names),
                                 b=_place(d.get("ru_to"), groups, names)))
    more = n - min(len(devs), DEVICES_SHOWN)
    if more > 0:
        out.append("  " + tr("gw_cli.pol.moves_more", n=more))
    return out


def _done(args, res, text, groups=None, more=()):
    """The answer of a write of policy_ops: ``--dry`` — «что изменится» (nothing written; ``more`` — lines of its own
    after the head: the keys ``group-del --with-keys`` drops); else the line, «что изменилось» and the warnings to
    stderr. ``groups`` — as in ``moves_lines``."""
    if res.get("dry"):
        lines = ([tr("gw_cli.pol.dry_head")] + list(more) + moves_lines(res.get("moves"), groups=groups)
                 + [tr("gw_cli.pol.dry_tail")])
    else:
        for w in res.get("warnings") or []:
            _warn(w)
        lines = [text] + (moves_lines(res.get("moves"), groups=groups) if (res.get("moves") or {}).get("total") else [])
    emit(args, res, lines)
    return errors.OK


def _rev_arg(p):
    p.add_argument("--rev", type=int, default=None, help=tr("gw_cli.help.rev"))
    return p


def _dry_arg(p):
    p.add_argument("--dry", action="store_true", help=tr("gw_cli.help.dry"))
    return p


def _role_word(role):
    return tr("gw_cli.role.ru" if role == "ru" else "gw_cli.role.abroad")


def _fail_word(fail):
    return tr("gw_cli.groups.fail_direct" if fail == "direct" else "gw_cli.groups.fail_block")


def _preset_word(preset):
    key = "gw_cli.preset." + str(preset)
    from ..i18n import has
    return tr(key) if has(key) else str(preset)


# ── gw groups ──────────────────────────────────────────────────────────────

def _now_word(g):
    from . import live
    if not g.get("exits"):
        return tr("gw_cli.groups.now_empty")
    if g.get("last_step"):
        at = live._clock(g.get("no_exit_since"))
        return tr("gw_cli.groups.now_last_at", time=at) if at else tr("gw_cli.groups.now_last")
    if g.get("active_tier_label"):
        return g["active_tier_label"]
    return "—"


def _cut(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


_HEALTH = {"ok": "gw_cli.health.ok", "degraded": "gw_cli.health.degraded", "standby": "gw_cli.health.standby",
           "off": "gw_cli.health.off", "unknown": "gw_cli.health.unknown"}
_REASON = {"dead": "gw_cli.reason.dead", "disabled": "gw_cli.reason.disabled", "maintenance": "gw_cli.reason.maintenance",
           "quota": "gw_cli.reason.quota", "country": "gw_cli.reason.country", "manual_standby": "gw_cli.reason.manual",
           "unstable": "gw_cli.reason.unstable"}
_METRIC = {"rtt": "gw_cli.metric.rtt", "loss": "gw_cli.metric.loss", "jitter": "gw_cli.metric.jitter",
           "speed": "gw_cli.metric.speed", "service": "gw_cli.metric.service", "country": "gw_cli.metric.country"}


def _reason_words(row, grp):
    """The reasons of an exit (``exit_fields``: [{code, args}]) in words: «нет ответа», «условие: отклик» …; a code
    with no words of its own is not shown (no jargon)."""
    out = []
    for r in row.get("reasons") or []:
        code = (r or {}).get("code")
        if code in _REASON:
            out.append(tr(_REASON[code]))
        elif code == "rule":
            rid = ((r or {}).get("args") or {}).get("rule")
            rule = next((x for x in (grp or {}).get("rules") or [] if isinstance(x, dict) and x.get("id") == rid), {})
            if rule.get("name"):
                out.append(tr("gw_cli.reason.rule_named", name=rule["name"]))
            else:
                conds = ((rule.get("when") or {}).get("conds") or [{}])
                metric = (conds[0] or {}).get("metric") if conds else None
                out.append(tr("gw_cli.reason.rule", what=tr(_METRIC[metric])) if metric in _METRIC
                           else tr("gw_cli.reason.rule_any"))
    return out


def exit_group_line(row, doc, tag=None, ms=None):
    """One exit under the table of the groups: «Вена — Основная · Уровень 1 · ряд 1 · ухудшен: условие: отклик · 340
    мс · устройств: 2». ``tag`` — its place in the queue (``queue_rows``), ``ms`` — the response the collector
    chooses by, instead of the last check's."""
    grp, ti = P.tier_of(doc, row.get("id"))
    parts = []
    if grp is not None:
        parts.append(P.label(grp))
        parts.append(P.tier_label(grp, ti))
    else:
        parts.append(tr("gw_cli.groups.no_group"))
    if tag:
        parts.append(tag)
    if row.get("enabled", True) is False:
        state_w = tr("gw_cli.health.disabled")
    else:
        state_w = tr(_HEALTH.get(row.get("health"), "gw_cli.health.unknown"))
        why = _reason_words(row, grp)
        if why:
            state_w += ": " + ", ".join(why)
        if row.get("unstable") and "unstable" not in [(r or {}).get("code") for r in row.get("reasons") or []]:
            state_w += " · " + tr("gw_cli.reason.unstable")
    parts.append(state_w)
    rtt = ms if _num(ms) else row.get("rtt_ms")
    if _num(rtt):
        parts.append(tr("gw_cli.groups.rtt", ms="%d" % round(rtt)))
    parts.append(tr("gw_cli.groups.devices", n=int(row.get("devices") or 0)))
    return "  %s — %s" % (_cut(row.get("name") or row.get("id"), 30), " · ".join(parts))


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# the method of a tier -> what the collector measures its rows by (assign._ranked, «by»), as the panel's BY_OF
_BY_OF = {"best_rtt": ("rtt", "x_norm"), "weights": ("weights",), "capacity": ("capacity",), "even": ("even",)}


def _ranks_fit(g, t, r):
    """The rows of the collector were made for the tier as it is saved — the same flag of the order, a method that
    makes them (the panel's ``ranksFit``); after «Сохранить» they are the old ones till the next round."""
    pc = _per_conn(g)
    man = not pc and t.get("order") == "manual"
    if (r.get("order") or "auto") != ("manual" if man else "auto"):
        return False
    if not r.get("rows"):
        return True
    return r.get("by") in (("per_connection",) if pc else ("manual",) if man else _BY_OF.get(t.get("method"), ("even",)))


def queue_rows(rows, pol, ranks=None):
    """The exits under the table of the groups in the order of the queue — as «Уровни» of the panel show them
    (PLAN-order Ш5, ``shownOrder``): the groups of the policy, their tiers in turn; in a manual tier — its list (the
    first working exit from the top gets new devices); in an automatic one — the rows of «strength» of the collector
    (``live.groups.<gid>.ranks``, fresh and made for this tier: row 1, row 2 …, then the exits not taking part), else
    the saved list; exits of no group — last, as they came. -> [(row, tag, ms)]: ``tag`` — «место 2» of a manual tier,
    «ряд 1» of a ranked one, else None; ``ms`` — the response the collector chooses by («Лучший отклик»), else None."""
    by_id, out, seen = {}, [], set()
    for row in rows or []:
        if isinstance(row, dict) and row.get("id") is not None:
            by_id.setdefault(row["id"], row)
    for g in (pol or {}).get("groups") or []:
        if not isinstance(g, dict):
            continue
        granks = (ranks or {}).get(g.get("id"))
        for t in g.get("tiers") or []:
            if not isinstance(t, dict):
                continue
            ex = [e for e in t.get("exits") or [] if isinstance(e, str)]
            r = granks.get(t.get("id")) if isinstance(granks, dict) else None
            order, tags = list(ex), {}
            if _per_conn(g):
                pass
            elif t.get("order") == "manual":
                tags = dict((e, tr("gw_cli.groups.place", n=i + 1)) for i, e in enumerate(ex))
            elif isinstance(r, dict) and isinstance(r.get("rows"), list) and _ranks_fit(g, t, r):
                ranked = r.get("by") not in ("even", "per_connection") and not r.get("nodata")
                order = []
                for ri, rrow in enumerate(r["rows"]):
                    for x in rrow if isinstance(rrow, list) else ():
                        e = x.get("id") if isinstance(x, dict) else None
                        if e in ex and e not in order:
                            order.append(e)
                            tags[e] = (tr("gw_cli.groups.row", n=ri + 1) if ranked else None,
                                       x.get("v") if r.get("by") == "rtt" and _num(x.get("v")) else None)
                for x in r.get("out") or []:
                    e = x.get("id") if isinstance(x, dict) else None
                    if e in ex and e not in order:
                        order.append(e)
                order += [e for e in ex if e not in order]
            for e in order:
                if e in by_id and e not in seen:
                    seen.add(e)
                    tag = tags.get(e)
                    out.append((by_id[e],) + (tag if isinstance(tag, tuple) else (tag, None)))
    for row in rows or []:
        if isinstance(row, dict) and row.get("id") not in seen:
            seen.add(row.get("id"))
            out.append((row, None, None))
    return out


def _kept_method(t):
    """The method of a tier in words («Лучший отклик»); unknown — as stored."""
    m = (t or {}).get("method", "even")
    return tr("gw_cli.method." + m) if m in P.METHODS else str(m)


def _per_conn(g):
    return bool(g.get("per_connection")) and g.get("id") == P.MAIN


def _method_word(g, t):
    """How a tier chooses (PLAN-order Ш3): «по соединениям» of «main» (neither the method nor the order act), «По
    порядку (вручную)» (``order`` manual), else its method."""
    if _per_conn(g):
        return tr("gw_cli.method.per_connection")
    if (t or {}).get("order") == "manual":
        return tr("gw_cli.method.manual")
    return _kept_method(t)


def _manual_tiers(g):
    """[(index, tier)] of the tiers of ``g`` in the manual order (none at «по соединениям»: the flag does not act)."""
    if _per_conn(g):
        return []
    return [(i, t) for i, t in enumerate(g.get("tiers") or []) if isinstance(t, dict) and t.get("order") == "manual"]


def groups_lines(doc, rows=None, pol=None, ranks=None):
    """The table of §7.6 «Состояние и группы» (the SSH menu prints these lines; ``gw groups`` without --json too):
    the groups, under it the tiers in the manual order (PLAN-order Ш3), each exit in the order of the queue
    (``queue_rows``; ``ranks`` — {gid: live.groups.<gid>.ranks}), «Нет выхода в интернет: N устройств», where the
    settings are."""
    head = (tr("gw_cli.groups.h_group"), tr("gw_cli.groups.h_role"), tr("gw_cli.groups.h_exits"),
            tr("gw_cli.groups.h_now"), tr("gw_cli.groups.h_last"))
    w = (16, 12, 8, 22)

    def fmt(c):
        return "".join(_cut(c[i], w[i] - 1).ljust(w[i]) for i in range(4)) + str(c[4])
    lines = [fmt(head).rstrip()]
    for g in doc.get("groups") or []:
        lines.append(fmt((g.get("label"), _role_word(g.get("role")), "%d" % len(g.get("exits") or []), _now_word(g),
                          _fail_word(g.get("fail")))))
    manual = [(g, i, t) for g in doc.get("groups") or [] for i, t in _manual_tiers(g)]
    if manual:
        lines.append("")
        for g, i, t in manual:
            lines.append(tr("gw_cli.groups.manual", group=g.get("label") or P.label(g),
                            tier=t.get("label") or P.tier_label(g, i), method=_kept_method(t)))
    if rows:
        pol = pol if pol is not None else pops._base(state.load())
        lines.append("")
        for row, tag, ms in queue_rows(rows, pol, ranks):
            lines.append(exit_group_line(row, pol, tag, ms))
    n = doc.get("no_exit")
    if n:
        lines.append("")
        lines.append(tr("gw_cli.groups.no_exit", n=n, devices=trn("gw_cli.n.devices", n)))
    elif n is None and doc.get("groups"):
        lines.append("")
        lines.append(tr("gw_cli.groups.no_live"))
    lines.append("")
    lines.append(tr("gw_cli.groups.hint"))
    return lines


def _exit_rows(st):
    """The rows of ``gw exits`` with the fields of stage 1 (``exit_fields``), or [] when the pool is not readable."""
    from . import exits, ops
    try:
        rows = ops.exits_view(_r(), st, roles=exits.ROLES)          # the Russian exits under their groups too (№31г)
        pops.exit_fields(st, rows)
        return rows
    except Exception:                                   # noqa: BLE001 — the table of the groups still shows
        return []


def _live_ranks():
    """{gid: rows of «strength» of its tiers} of a fresh live.json (the same the panel draws «Уровни» by); {} else."""
    from . import live
    lv, age = live.read()
    if not live.fresh(age) or not isinstance(lv, dict):
        return {}
    return dict((gid, g["ranks"]) for gid, g in (lv.get("groups") or {}).items()
                if isinstance(g, dict) and isinstance(g.get("ranks"), dict))


def cmd_groups(args):
    st = state.load()
    doc = pops.groups(st)
    rows = _exit_rows(st)
    doc["lines"] = groups_lines(doc, rows, pops._base(st), _live_ranks())
    emit(args, doc, doc["lines"])
    return errors.OK


# ── gw policy, gw policy-reset ─────────────────────────────────────────────

def _freq_word(grp):
    fr = ((grp.get("checks") or {}).get("freq") or {})
    mode = fr.get("mode", "often")
    if mode == "custom":
        return tr("gw_cli.freq.custom", s=fr.get("every_s"))
    key = "gw_cli.freq." + str(mode)
    from ..i18n import has
    return tr(key) if has(key) else str(mode)


def _new_tier_word(grp):
    """Where a new or moved exit of the group goes — the words of the panel («Новые и перенесённые …»); an unknown
    tier id acts as «last» (``policy.new_tier``), so it reads as «last» here too."""
    ru = grp.get("role") == "ru"
    where = grp.get("new_exits_tier") or "last"
    if where == "first":
        return tr("gw_cli.pol.nt.first")
    for i, t in enumerate(grp.get("tiers") or []):
        if isinstance(t, dict) and where != "last" and t.get("id") == where:
            return tr("gw_cli.pol.nt.tier", tier=t.get("label") or P.tier_label(grp, i))
    return tr("gw_cli.pol.nt.last_ru" if ru else "gw_cli.pol.nt.last")


def _night_word(grp):
    nr = grp.get("night_rebalance") or {}
    if not nr.get("enabled"):
        return tr("gw_cli.word.no")
    return tr("gw_cli.pol.night_at", at=nr.get("at") or "04:00")


def policy_lines(doc, names=None):
    names = names or {}
    lines = []
    for g in doc.get("groups") or []:
        if not isinstance(g, dict):
            continue
        lines.append(tr("gw_cli.pol.group", name=g.get("label") or P.label(g), role=_role_word(g.get("role")),
                        preset=_preset_word(P.preset_of(g))))
        lines.append("  " + tr("gw_cli.pol.fail", how=_fail_word(g.get("fail"))))
        lines.append("  " + tr("gw_cli.pol.flap", how=tr("gw_cli.word.yes" if g.get("flap_standby") else "gw_cli.word.no")))
        lines.append("  " + tr("gw_cli.pol.freq", how=_freq_word(g)))
        for i, t in enumerate(g.get("tiers") or []):
            if not isinstance(t, dict):
                continue
            ids = list(t.get("exits") or [])
            ex = ", ".join(_cut(names.get(e, e), 30) for e in ids) or tr("gw_cli.pol.tier_empty")
            lines.append("  " + tr("gw_cli.pol.tier", tier=t.get("label") or P.tier_label(g, i), method=_method_word(g, t),
                                   count=trn("gw_cli.n.keys" if g.get("role") == "ru" else "gw_cli.n.exits", len(ids)),
                                   exits=ex))
            if t.get("order") == "manual" and not _per_conn(g):
                lines.append("    " + tr("gw_cli.pol.manual", method=_kept_method(t)))
        lines.append("  " + tr("gw_cli.pol.new_tier_ru" if g.get("role") == "ru" else "gw_cli.pol.new_tier",
                               how=_new_tier_word(g)))
        lines.append("  " + tr("gw_cli.pol.night", how=_night_word(g)))
        rules = [r for r in g.get("rules") or [] if isinstance(r, dict) and r.get("enabled", True)]
        lines.append("  " + tr("gw_cli.pol.rules", n=len(rules)))
    lines.append(tr("gw_cli.pol.panel_hint"))
    return lines


def cmd_policy(args):
    st = state.load()
    doc = pops.view(st)
    if args.group:
        grp = P.group(doc["policy"], args.group)
        if grp is None:
            raise pops.gone("group", args.group)
        doc["policy"] = dict(doc["policy"], groups=[grp])
    emit(args, doc, policy_lines(doc["policy"], _exit_names(st)))
    return errors.OK


def cmd_policy_reset(args):
    gid = args.group or P.MAIN
    preset = args.preset or "compat"

    def call(rev, dry, moves):
        return pops.reset(_r(), preset=preset, group=gid, want_rev=rev, dry=dry, want_moves=moves)

    def ask(_first):
        return (tr("gw_cli.pol.reset_q", group=_group_label(gid), preset=_preset_word(preset)),
                tr("gw_cli.pol.reset_yes", preset=_preset_word(preset)))
    res = ask_write(args, call, ask, _cmd("gw", "policy-reset", "--group", gid, "--preset", preset, "--yes"))
    if res is None:
        return _cancelled(args)
    return _done(args, res, tr("gw_cli.pol.reset_done", group=_group_label(gid), preset=_preset_word(preset)))


def _group_label(gid, st=None):
    grp = P.group(pops._base(state.load() if st is None else st), gid)
    return P.label(grp) if grp else gid


# ── over one exit ──────────────────────────────────────────────────────────

MANUAL_FLAGS = (("standby", "standby"), ("restore", "restore"), ("maintenance", "maintenance"))


def exit_action(args, then=None):
    """``gw exit <id> --standby|--restore|--maintenance|--accept-country|--enable|--disable [--dry] [--rev]`` and the
    quota: the buttons over an exit (§7.3, §7.9) through policy_ops — ``--enable``/``--disable`` too, with or without
    ``--dry``/``--rev``: the one path of the button of the panel (``/enabled``: the bot's proxy rebuilt, the journal).
    None — no such flag given (the name only). The summary names the exit by its name, not its id (§8).
    ``then`` — with ``--enable``/``--disable --name`` (REV-3371 П-2): the rename, run AFTER the switch; its result
    joins the answer (``rename`` in JSON, its lines in the text). A refusal of it after the switch — the switch said
    first, then the refusal: never «saved» for a name that is not."""
    for flag, how in MANUAL_FLAGS:
        if getattr(args, flag, False):
            res = pops.exit_manual(_r(), args.id, how, want_rev=args.rev, dry=args.dry)
            return _done(args, res, tr("gw_cli.exit.manual_" + how, name=_exit_name(args.id)))
    if getattr(args, "accept_country", False):
        res = pops.exit_country(_r(), args.id, want_rev=args.rev, dry=args.dry)
        return _done(args, res, tr("gw_cli.exit.country_done", name=_exit_name(args.id), country=res.get("country") or ""))
    if args.enable or args.disable:
        res = pops.exit_enabled(_r(), args.id, bool(args.enable), want_rev=args.rev, dry=args.dry)
        if then is not None and not res.get("dry"):
            from . import rename
            try:
                r = then()
            except errors.KaskadError as e:
                if getattr(args, "json", False):  # REV-3371-2 П-2: one JSON document — the switch inside the refusal
                    e.data = dict(e.data or {}, switch=res)
                else:
                    _done(args, res, tr("gw_cli.exit.saved", name=_exit_name(args.id)))
                raise
            return _done(args, dict(res, rename=r),
                         "\n".join([tr("gw_cli.exit.saved", name=_exit_name(args.id))] + rename.said(r)))
        return _done(args, res, tr("gw_cli.exit.saved", name=_exit_name(args.id)))
    if args.no_quota or quota_given(args):
        res = pops.exit_set(_r(), args.id, quota=None if args.no_quota else quota_of(args), want_rev=args.rev,
                            dry=args.dry)
        return _done(args, res, tr("gw_cli.exit.quota_off" if args.no_quota else "gw_cli.exit.quota_set",
                                   name=_exit_name(args.id)))
    return None


QUOTA_ARGS = (("quota_gb", "limit_gb"), ("quota_period", "period"), ("quota_reset_day", "reset_day"),
              ("quota_warn", "warn_pct"), ("quota_action", "action"))


def quota_given(args):
    return any(getattr(args, a, None) is not None for a, _k in QUOTA_ARGS)


def quota_of(args):
    """The quota of the flags over the one the exit has (a flag not given keeps its value); a new quota without
    ``--quota-gb`` — the check of the core says what is missing."""
    pref = model.exit_pref(state.load(), args.id) if model._EXIT_ID.match(str(args.id)) else {}
    q = dict(pref.get("quota") or {}) if isinstance(pref.get("quota"), dict) else {}
    q = dict((k, v) for k, v in q.items() if k in pops.QUOTA_KEYS)
    for a, k in QUOTA_ARGS:
        v = getattr(args, a, None)
        if v is not None:
            q[k] = v
    gb = getattr(args, "quota_gb", None)
    if gb is not None and (not math.isfinite(gb) or gb > QUOTA_GB_MAX):
        # nan, inf, 1e308: the words of a field, never a trace of the check of the core (REV-s1-t10-sec, придирка 1)
        raise pops.bad_field("quota.limit_gb", tr("gw_cli.exit.quota_gb_bad", max="%d" % QUOTA_GB_MAX))
    return q


def exit_args(px, g):
    """The flags of stage 1 of ``gw exit`` (the parser of gw/cli.py keeps --enable, --disable, --name): ``g`` — its
    group of the actions, one of them at a time."""
    g.add_argument("--standby", action="store_true", help=tr("gw_cli.help.standby"))
    g.add_argument("--restore", action="store_true", help=tr("gw_cli.help.restore"))
    g.add_argument("--maintenance", action="store_true", help=tr("gw_cli.help.maintenance"))
    g.add_argument("--accept-country", action="store_true", help=tr("gw_cli.help.accept_country"))
    g.add_argument("--no-quota", action="store_true", help=tr("gw_cli.help.no_quota"))
    px.add_argument("--quota-gb", type=float, default=None, help=tr("gw_cli.help.quota_gb"))
    px.add_argument("--quota-period", default=None, choices=P.QUOTA_PERIODS, help=tr("gw_cli.help.quota_period"))
    px.add_argument("--quota-reset-day", type=int, default=None, help=tr("gw_cli.help.quota_reset_day"))
    px.add_argument("--quota-warn", type=int, default=None, help=tr("gw_cli.help.quota_warn"))
    px.add_argument("--quota-action", default=None, choices=P.QUOTA_ACTIONS, help=tr("gw_cli.help.quota_action"))
    _dry_arg(px)
    _rev_arg(px)


def _role_changes(eid, gid):
    """Does moving ``eid`` into ``gid`` change its role (abroad ↔ ru)? -> (changes, the target group or {}). An exit
    with no group yet, or no such target — False: the core says what is wrong."""
    doc = pops._base(state.load())
    src, to = P.group_of(doc, eid), P.group(doc, gid)
    if src is None or to is None:
        return False, to or {}
    return src.get("role", "abroad") != to.get("role", "abroad"), to


def cmd_exit_move(args):
    role = bool(args.confirm_role)
    done = lambda res: _done(args, res, tr("gw_cli.move.done", name=_exit_name(args.id),        # noqa: E731
                                           group=_group_label(args.group)))
    changes, to = _role_changes(args.id, args.group) if role else (False, {})
    if not changes:
        # the same role (with --confirm-role too: nothing to warn about — придирка 2) — the plain move, asked as every
        # change of the path of traffic (KT3-9: ``--yes``, or the question with numbers on a terminal; as group-del);
        # another role without --confirm-role — 409 of the core with the command that says yes (придирка 3)
        def plain(rev, dry, moves):
            try:
                return pops.exit_move(_r(), args.id, args.group, tier=args.tier, confirm_role=role, want_rev=rev,
                                      dry=dry, want_moves=moves)
            except pops.Refused as e:
                if e.key != "confirm_role":
                    raise
                raise errors.KaskadError("%s %s" % (e, tr("gw_cli.move.role_hint", cmd=_cmd(
                    "gw", "exit-move", args.id, "--group", args.group,
                    **{"--tier": args.tier, "--confirm-role": True}))), code=e.code, data=e.data)
        res = ask_write(args, plain, lambda _f: (tr("gw_cli.move.q", name=_exit_name(args.id),
                                                    group=_group_label(args.group)), tr("gw_cli.move.role_yes")),
                        _cmd("gw", "exit-move", args.id, "--group", args.group, **{"--tier": args.tier, "--yes": True}))
        return _cancelled(args) if res is None else done(res)

    def call(rev, dry, moves):
        return pops.exit_move(_r(), args.id, args.group, tier=args.tier, confirm_role=True, want_rev=rev, dry=dry,
                              want_moves=moves)

    def ask(_first):
        warn = tr("gw_cli.move.role_to_ru" if to.get("role") == "ru" else "gw_cli.move.role_to_abroad")
        return warn + " " + tr("gw_cli.move.role_q", group=P.label(to) or args.group), tr("gw_cli.move.role_yes")
    res = ask_write(args, call, ask, _cmd("gw", "exit-move", args.id, "--group", args.group,
                                          **{"--tier": args.tier, "--confirm-role": True, "--yes": True}))
    return _cancelled(args) if res is None else done(res)


def cmd_group_del(args):
    """``--with-keys`` (В1, как в панели: решение владельца 8а) — a Russian group goes together with its keys
    (``group_delete(exits="drop")``: the dry run names them, the question too); without it — as before, the last Russian
    group with keys is 409 ``ru_last``, its text now tells ``--with-keys``."""
    keys = bool(getattr(args, "with_keys", False))
    if keys and args.exits_to:
        raise errors.KaskadError(tr("gw_cli.gdel.keys_or_move"), code=errors.USAGE)
    if keys and args.subs_to:                          # the drop never moves subscriptions (policy_ops._group_out):
        raise errors.KaskadError(tr("gw_cli.gdel.keys_no_subs"), code=errors.USAGE)   # not a silent no-op (REV-3312 П-2)
    kw = dict(exits_to=args.exits_to, reassign=args.reassign, tg_proxy_to=args.tg_proxy_to,
              new_exits_to=args.new_exits_to, subs_to=args.subs_to, exits="drop" if keys else None)
    st0 = state.load()
    before = _labels(pops._base(st0))                  # «что изменилось» names the deleted group (придирка 2 sec)
    label = before.get(args.group) or args.group
    names = _exit_names(st0) if keys else {}           # read before the write: the dropped keys leave the pool

    def said(ids):
        return ", ".join(names.get(i) or str(i) for i in ids or [])

    def call(rev, dry, moves):
        try:
            return pops.group_delete(_r(), args.group, want_rev=rev, dry=dry, want_moves=moves, **kw)
        except pops.Refused as e:
            more = None
            if e.key == "ru_last" and not keys:
                more = tr("gw_cli.gdel.ru_last_hint")
            elif keys and e.key == "field" and (e.data or {}).get("field") == "exits":
                raise pops.Refused(tr("gw_cli.gdel.keys_ru_only"), e.key, e.status, e.code, e.data)
            elif keys and e.key == "need" and (e.data or {}).get("refs") and not args.reassign:
                more = tr("gw_cli.gdel.keys_reassign")
            if more is None:
                raise
            raise pops.Refused("%s %s" % (e, more), e.key, e.status, e.code, e.data)

    def ask(first):
        if keys and first.get("drop"):
            return tr("gw_cli.gdel.q_keys", group=label, keys=said(first["drop"])), tr("gw_cli.gdel.yes_keys")
        return tr("gw_cli.gdel.q", group=label), tr("gw_cli.gdel.yes")
    hint = _cmd("gw", "group-del", args.group, **{"--exits-to": args.exits_to, "--reassign": args.reassign,
                                                 "--tg-proxy-to": args.tg_proxy_to, "--new-exits-to": args.new_exits_to,
                                                 "--subs-to": args.subs_to, "--with-keys": keys, "--yes": True})
    res = ask_write(args, call, ask, hint)
    if res is None:
        return _cancelled(args)
    if res.get("dry"):
        more = [tr("gw_cli.gdel.dry_keys", keys=said(res["drop"]))] if res.get("drop") else []
        return _done(args, res, None, groups=before, more=more)
    if res.get("dropped"):
        return _done(args, res, tr("gw_cli.gdel.done_keys", group=label, keys=said(res["dropped"])), groups=before)
    return _done(args, res, tr("gw_cli.gdel.done", group=label), groups=before)


def cmd_group_flap(args):
    res = pops.group_flap_standby(_r(), args.group, args.on == "on", want_rev=args.rev, dry=args.dry)
    return _done(args, res, tr("gw_cli.flap.on" if args.on == "on" else "gw_cli.flap.off", group=_group_label(args.group)))


def cmd_exit_history(args):
    from . import live, metrics
    res = metrics.transitions(args.id, days=args.days, limit=args.limit, before=args.before)
    lines = []
    if not res.get("db"):
        lines.append(tr("gw_cli.hist.no_db"))
    for it in res.get("items") or []:
        lines.append("%s  %s → %s%s" % (_when(it.get("ts")), tr(_HEALTH.get(it.get("from"), "gw_cli.health.unknown")),
                                        tr(_HEALTH.get(it.get("to"), "gw_cli.health.unknown")),
                                        (" (%s)" % tr(_REASON[it["why"]])) if it.get("why") in _REASON else ""))
    if res.get("db") and not res.get("items"):
        lines.append(tr("gw_cli.hist.none"))
    if res.get("next"):
        lines.append(tr("gw_cli.hist.more", before=res["next"]))
    emit(args, dict(res, ok=True, id=args.id), lines)
    return errors.OK


def _when(ts):
    from ..i18n import fmt_datetime
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return "—"
    return fmt_datetime(ts, now=_now())


SPEED_REFUSED = {"busy": errors.LOCKED, "budget_day": errors.CONFLICT, "budget_month": errors.CONFLICT,
                 "disabled": errors.CONFLICT, "quota": errors.CONFLICT, "no_exit": errors.CONFLICT}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _speed_failed(eid, since):
    """A failed answer of the speed test started at ``since`` (REV-s1-t10-sec №2): the unit writes its refusal
    («занято», the budget, the exit off or removed by its quota) and a failed test with ``ok`` false at once —
    ``probe-results.json`` (``manual``), then metrics.db (``speed``). -> {"why", "ts"} | None."""
    from . import metrics
    from .probe import run as prun
    got = []
    try:
        for r in prun.read_results().get("results") or []:
            if isinstance(r, dict) and r.get("exit") == eid and r.get("kind") == "speed" and r.get("manual") \
                    and _num(r.get("ts")) and r["ts"] >= int(since):
                got.append((r["ts"], bool(r.get("ok")), str(r.get("why") or "error")))
    except Exception:                                   # noqa: BLE001 — a broken file: the base still tells
        pass
    conn = metrics._reader()
    if conn is not None:
        try:
            row = conn.execute("SELECT ts, ok, why FROM speed WHERE exit = ? AND ts >= ? ORDER BY ts DESC LIMIT 1",
                               (eid, int(since))).fetchone()
            if row:
                got.append((row[0], bool(row[1]), str(row[2] or "error")))
        except Exception:                               # noqa: BLE001 — a broken base: the file still tells
            pass
        finally:
            try:
                conn.close()
            except Exception:                           # noqa: BLE001
                pass
    if not got:
        return None
    ts, ok, why = max(got, key=lambda x: x[0])          # the newest answer of either place
    return None if ok else {"why": why, "ts": ts}


def _why_words(code):
    """The words of a code of the unit (``gw_probe.why.<code>``); a code with a placeholder or unknown — general ones."""
    from ..i18n import has
    key = "gw_probe.why." + str(code)
    text = tr(key) if has(key) else ""
    return text if text and "{" not in text else tr("gw_cli.speed.why_other")


def cmd_exit_speedtest(args):
    """«Проверить скорость сейчас» (§5.3): a job of the unit of probes, then the result waited at most SPEED_WAIT s (the
    result also comes to the feed); none in time — code 1, ``data.running`` and the words «придёт позже». A refusal of
    the unit («занято», the budget, the exit off) or a failed test — at once, with its reason and ``running`` false
    (REV-s1-t10-sec №2). ``--force`` passes the day's budget only: the month's stays, and the unit runs one test at a
    time on the node."""
    from .probe import unit as probe_unit
    if args.id not in pops.pool_ids():
        raise pops.gone("exit", args.id)
    start = _now()
    probe_unit.manual(_r(), args.id, force=bool(args.force))
    end = start + SPEED_WAIT
    while True:
        _pts, speed = pops._last_metrics([args.id], _now())
        got = speed.get(args.id)
        if got and _num(got.get("ts")) and got["ts"] >= int(start):
            emit(args, {"ok": True, "id": args.id, "speed": got},
                 tr("gw_cli.speed.done", name=_exit_name(args.id), mbps=got.get("mbps")))
            return errors.OK
        bad = _speed_failed(args.id, start)
        if bad:
            why = bad["why"]
            text = tr("gw_cli.speed.refused" if why in SPEED_REFUSED else "gw_cli.speed.failed",
                      name=_exit_name(args.id), why=lower_first(_why_words(why)))
            if why == "budget_day" and not args.force:
                text += " " + tr("gw_cli.speed.force_hint")
            raise errors.KaskadError(text, code=SPEED_REFUSED.get(why, errors.ERROR),
                                     data={"id": args.id, "running": False, "why": why})
        if _now() >= end:
            raise errors.KaskadError(tr("gw_cli.speed.later", s=SPEED_WAIT), code=errors.ERROR,
                                     data={"id": args.id, "running": True})
        _sleep(SPEED_POLL)


# ── parser ─────────────────────────────────────────────────────────────────

def register(sub):
    """The commands of T10 into the parser of ``gw`` (``sub``: its subparsers)."""
    add_json_flag(sub.add_parser("groups", help=tr("gw_cli.help.groups"))).set_defaults(func=cmd_groups)
    p1 = add_json_flag(sub.add_parser("policy", help=tr("gw_cli.help.policy")))
    p1.add_argument("--group", default=None, help=tr("gw_cli.help.group_arg"))
    p1.set_defaults(func=cmd_policy)
    p1 = add_json_flag(sub.add_parser("policy-reset", help=tr("gw_cli.help.policy_reset")))
    p1.add_argument("--group", default=None, help=tr("gw_cli.help.group_arg"))
    p1.add_argument("--preset", default=None, choices=[x for x in P.PRESETS["abroad"] + P.PRESETS["ru"] if x != P.CUSTOM],
                    help=tr("gw_cli.help.preset"))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.yes"))
    _rev_arg(_dry_arg(p1)).set_defaults(func=cmd_policy_reset)
    p1 = add_json_flag(sub.add_parser("exit-move", help=tr("gw_cli.help.exit_move")))
    p1.add_argument("id")
    p1.add_argument("--group", required=True, help=tr("gw_cli.help.group_to"))
    p1.add_argument("--tier", default=None, help=tr("gw_cli.help.tier"))
    p1.add_argument("--confirm-role", action="store_true", help=tr("gw_cli.help.confirm_role"))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.yes"))
    _rev_arg(_dry_arg(p1)).set_defaults(func=cmd_exit_move)
    p1 = add_json_flag(sub.add_parser("group-del", help=tr("gw_cli.help.group_del")))
    p1.add_argument("group")
    for flag, key in (("--exits-to", "exits_to"), ("--reassign", "reassign"), ("--tg-proxy-to", "tg_proxy_to"),
                      ("--new-exits-to", "new_exits_to"), ("--subs-to", "subs_to")):
        p1.add_argument(flag, default=None, help=tr("gw_cli.help.gdel_" + key))
    p1.add_argument("--with-keys", action="store_true", help=tr("gw_cli.help.gdel_with_keys"))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.yes"))
    _rev_arg(_dry_arg(p1)).set_defaults(func=cmd_group_del)
    p1 = add_json_flag(sub.add_parser("group-flap-standby", help=tr("gw_cli.help.group_flap")))
    p1.add_argument("group")
    p1.add_argument("on", choices=("on", "off"))
    _rev_arg(_dry_arg(p1)).set_defaults(func=cmd_group_flap)
    p1 = add_json_flag(sub.add_parser("exit-history", help=tr("gw_cli.help.exit_history")))
    p1.add_argument("id")
    p1.add_argument("--days", type=int, default=7, help=tr("gw_cli.help.days"))
    p1.add_argument("--limit", type=int, default=100, help=tr("gw_cli.help.limit"))
    p1.add_argument("--before", type=int, default=None, help=tr("gw_cli.help.before"))
    p1.set_defaults(func=cmd_exit_history)
    p1 = add_json_flag(sub.add_parser("exit-speedtest", help=tr("gw_cli.help.exit_speedtest")))
    p1.add_argument("id")
    p1.add_argument("--force", action="store_true", help=tr("gw_cli.help.speed_force"))
    p1.set_defaults(func=cmd_exit_speedtest)
