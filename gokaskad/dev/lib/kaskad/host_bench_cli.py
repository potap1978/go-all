"""kaskadctl host score / host bench — the server against its own history and its check (SPEC-node-score §6.2 B7,
NS-D30–NS-D34; SPEC-node-score-schedule-share §4, §6.1). The wiring only (k3/handoff/process/BLOCKS.md): the score is ``bench.hostscore.score_doc``
(the answer of GET /api/host/score), the price ``bench.cost.per_run``, the start ``bench.run.start``; here — the words,
the questions and the paths of this node.

    host score [--explain] [--screen] [--json]
        the main number — ``summary.rel_total`` «N от обычного» (100 — as usual for this node, NS-D31; no number —
        «сравнивать пока не с чем: история N из 3 дней») and the hour of the checks; ``first``/``stale`` as SS §4; ``--explain`` —
        the links (the raw value now · «обычно» · rel, ▲/▼ and the numbers of the hint). No absolute score, band or
        verdict (NS-D34). ``--json`` = GET /api/host/score (the same document as MCP ``host_score``).
    host bench [--quick|--full] [--dry] [--yes | --confirm KEY] [--no-upload-ru] [--json]
        ``--dry`` — the price of one check of the mode (both modes without a flag) and what is missing of sysbench and
        fio, with the question about them (``bench.pkgs.question``); nothing starts. Each mode carries ``confirm`` — a
        key of the mode, its price and the packages to install (``confirm_key``). ``--confirm KEY`` (MCP
        ``host_bench``, SS §6.3: MCP asks nothing and never passes ``--yes``) — the price and the packages are counted
        again: the same key — the check starts and installs the packages of the key; another key — a refusal (code 2),
        nothing starts or is installed. ``--by-mcp`` without ``--confirm`` — a refusal (code 2). Without ``--dry``: in a terminal
        the questions by numbers («1) Проверить 2) Отмена», the mode, the packages); without a terminal and without
        ``--yes`` — a refusal (code 2): a check uses traffic. ``--yes`` — the price is agreed and the missing packages
        are installed (the answer «1» of the question; an apt that does not know them — the check goes without them).
        The text mode waits for the end (a line per step; Ctrl+C — stop waiting, the check goes on) and prints the
        result and the lines of ``host score``; ``--json`` answers at once: {"ok", "id", "unit", "mode", "missing", "price",
        "upload_ru"}. Test 8b (D-144, владелец 29.10): a FULL check of a node that may be in Russia measures the upload
        to the iperf3 points of the Russian providers by default (``upload_ru`` of the price — its line and the question
        «1) Замерить 2) Не замерять»; iperf3 is asked then — for a full check with the upload on only, ``plan_of``);
        ``--no-upload-ru`` turns it off (MCP — its parameter; the panel — the tick of the window; SSH — the question).
"""

import argparse
import hashlib
import json
import math
import re
import signal
import sys
import time

from . import errors, i18n
from .errors import KaskadError
from .i18n import fmt_num, tr

MODES = ("quick", "full")
POLL_S = 2.0
START_S = 15.0                      # П-3: the unit just started by systemd-run --no-block may be «unknown» a moment
PARTS = ("night", "morning", "day", "evening", "mixed")
BASE_KINDS = ("cpu", "mem", "tariff", "engines", "openssl")
REL_NEED = 3                        # = kaskad.score.REL_N_MIN / REL_TOTAL_MIN (NS-D30, NS-D31)

_sleep = time.sleep                 # tests replace these
_mono = time.monotonic


def _isatty():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _num(v):
    """A number of the CLI: whole from 10, one decimal below 10, two significant digits below 1 (``0,04``, never
    ``0,0``: the losses of a healthy server are below 0,1 %)."""
    v = float(v)
    if v == 0 or v.is_integer() or abs(v) >= 10:
        return i18n.fmt_num(v, digits=0)
    if abs(v) >= 1:
        return i18n.fmt_num(v, digits=1)
    digits = min(6, 1 - math.floor(math.log10(abs(v))))
    text = i18n.fmt_num(v, digits=digits)
    return text.rstrip("0").rstrip(",.") if text[-1:] == "0" else text


# ── score ──────────────────────────────────────────────────────────────────

def score_doc():
    from .bench import hostscore
    return hostscore.node_doc()                      # the answer of the panel: the history and the rule ▲/▼ (NS-D25)


def _last_day(rid):
    if not rid:
        return None
    try:
        from .bench import history
        r = history.run(rid)
    except Exception:                                # noqa: BLE001 — the date only decorates the line
        return None
    ts = r.get("ts") if isinstance(r, dict) else None
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    from . import tz
    return tz.day(ts)


def _link_name(lid):
    return tr("score.link." + str(lid))


def _unit(test, raw=None):
    """The unit of ``test`` in the reader's language (``score.unit.*``); a test without one — the core's own text."""
    key = "score.unit." + str(test)
    return tr(key) if test and i18n.has(key) else (raw or "")


def _hint_text(lk):
    h = lk.get("hint") or {}
    unit = _unit(h.get("test"), h.get("unit"))
    days = h.get("days")
    if days and len(days) == 2 and all(d[1] is not None for d in days):
        return tr("host_bench.link.days", a=_num(days[0][1]), b=_num(days[1][1]), unit=unit).rstrip()
    if days and len(days) == 1 and days[0][1] is not None:
        return tr("host_bench.link.day", a=_num(days[0][1]), unit=unit).rstrip()
    if h.get("now") is not None:
        return tr("host_bench.link.value", a=_num(h["now"]), unit=unit).rstrip()
    return None


def _raw(v, unit):
    return (_num(v) + (" " + unit if unit else "")) if v is not None else tr("host_bench.link.none")


def _rel_text(rel, n):
    """«96 от обычного» (NS-D30); none — fewer than 3 days of history: «сравнивать пока не с чем: история N из 3 дней»
    (NS-D35); the history is there but no number (no fresh value: the test failed) — «—», as the panel
    (REV-score-cut З-1)."""
    if rel is not None:
        return tr("host_bench.link.rel", rel=_num(rel))
    n = int(n or 0)
    if n >= REL_NEED:
        return "—"
    return tr("host_bench.link.rel_none", n=_num(n), need=_num(REL_NEED))


def _link_line(lk):
    name = _link_name(lk.get("id"))
    rel = _rel_text(lk.get("rel"), lk.get("usual_n"))
    if lk.get("test"):
        unit = tr("score.unit." + lk["test"])
        line = "  " + tr("host_bench.link.line", name=name, fresh=_raw(lk.get("fresh_raw"), unit),
                         usual=_raw(lk.get("usual_raw"), unit), rel=rel)
    else:
        line = "  " + tr("host_bench.link.group", name=name, rel=rel)
    extra = []
    if lk.get("dev") in ("up", "down"):
        extra.append(tr("host_bench.link." + lk["dev"]))
    elif lk.get("dev_pending") in ("up", "down"):
        kind = lk.get("dev_pending_kind")             # NS-D25: «ждёт» only before the repeat
        extra.append(tr("host_bench.link." + kind) if kind in ("retry_confirmed", "drop")
                     else tr("host_bench.link.pending"))
    if extra:
        hint = _hint_text(lk)
        if hint:
            extra.append(hint)
    if lk.get("source") == "probes":
        extra.append(tr("host_bench.link.probes"))
    return line + ("" if not extra else " · " + " · ".join(extra))


def score_lines(doc, explain=False, screen=False):
    """The lines of ``host score`` (and of the screen of item 22 under the health): the relative score of the node
    against its own history (NS-D30, NS-D31) — never an absolute score, a band or a verdict (NS-D34)."""
    sc = doc.get("summary") or {}
    links = doc.get("links") or []
    lines = []
    if sc.get("stale"):
        day = _last_day(sc.get("last_id"))
        lines.append(tr("host_bench.score.stale_bare", date=i18n.fmt_date(day, short=False) if day else "?"))
    elif sc.get("last_id") is None:
        lines.append(tr("host_bench.score.none"))
        lines.append("  " + tr("host_bench.score.none_hint_screen" if screen else "host_bench.score.none_hint"))
        return lines
    elif sc.get("rel_total") is not None:
        lines.append(tr("host_bench.score.rel", rel=_num(sc["rel_total"])))
    elif sc.get("rel_n"):
        lines.append(tr("host_bench.score.rel_few", n=_num(sc["rel_n"]), need=_num(REL_NEED)))
    else:
        n = max([int(lk.get("usual_n") or 0) for lk in links] or [0])
        lines.append(tr("host_bench.score.rel_none", n=_num(n), need=_num(REL_NEED)) if n < REL_NEED
                     else tr("host_bench.score.rel_dash"))     # the history is there, no fresh value (З-1)
    ch = sc.get("checked") or {}
    if not sc.get("stale") and ch.get("part") in PARTS and ch.get("hhmm"):
        lines.append("  " + tr("host_bench.score.checked", part=tr("host_bench.part." + ch["part"]), hhmm=ch["hhmm"]))
    if sc.get("first") and not sc.get("stale"):
        lines.append("  " + tr("host_bench.score.first"))
    if sc.get("retry") and sc["retry"].get("at"):
        lines.append("  " + tr("host_bench.score.retry", at=sc["retry"]["at"]))
    for b in sc.get("base_since") or []:
        if b.get("kind") in BASE_KINDS:
            lines.append("  " + tr("host_bench.score.base", date=i18n.fmt_date(b.get("day"), short=True),
                                   what=tr("host_bench.base." + b["kind"])))
    pct = (doc.get("dev_rule") or {}).get("pct")
    if isinstance(pct, int) and not isinstance(pct, bool):
        lines.append("  " + tr("host_bench.score.dev_rule", pct=pct))     # T-3377 Н-4: as «Настройки › Проверка сервера»
    if explain:
        lines.append(tr("host_bench.score.links"))
        lines.extend(_link_line(lk) for lk in links)
    return lines


def cmd_score(args):
    from .cli import emit
    try:
        doc = score_doc()
        text = None if getattr(args, "json", False) else score_lines(doc, args.explain, getattr(args, "screen", False))
    except KaskadError:
        raise
    except Exception as e:                           # noqa: BLE001 — a read error: code 1 with the reason
        raise KaskadError(tr("host_bench.read_fail", error=e), code=errors.ERROR)
    emit(args, doc, text)
    return errors.OK


# ── share (C9: what the clip of «Поделиться» shows — for the eyes) ──────────

SHARE_UNIT = {"Мбит/с": "host_bench.share.u.mbps", "мс": "host_bench.share.u.ms"}     # i18n: skip — bench.share.UNITS


def _share_v(m):
    v = m["v"]
    d = 0 if abs(v) >= 10 else 1 if abs(v) >= 1 else 2
    num = fmt_num(round(v, d), None, d) if d else fmt_num(v)
    return num + ("%" if m["unit"] == "%" else " " + tr(SHARE_UNIT.get(m["unit"], "host_bench.share.u.mbps")))


def share_lines(doc):
    """The text of ``kaskadctl host share``: the numbers of the frame, the extra by the switches, what the filter
    took away, where the clip is built."""
    if not doc.get("ready"):
        return [tr("host_bench.share.stale")]
    out = [tr("host_bench.share.title", date=doc["date"])]
    out.append(" · ".join("%s %s" % (tr("host_bench.share.m." + m["id"]), _share_v(m)) for m in doc["metrics"])
               or tr("host_bench.share.no_metrics"))
    if doc["rel_total"] is not None:
        out.append(tr("host_bench.share.total", n=fmt_num(doc["rel_total"])))
    else:
        out.append(tr("host_bench.share.total_none", n=doc["rel_n"], need=doc["rel_need"]))
    cnt = dict((c, doc["days"].count(c)) for c in ("green", "yellow", "red", "grey"))
    out.append(tr("host_bench.share.days", ok=cnt["green"], warn=cnt["yellow"], fail=cnt["red"], none=cnt["grey"]))
    ex = doc["extra"]
    t = ex.get("tariff")
    if t:
        parts = []
        if t.get("vcpu") is not None:
            parts.append(tr("host_bench.share.vcpu", n=t["vcpu"]))
        if t.get("ram_gb") is not None:
            parts.append(tr("host_bench.share.ram", v=fmt_num(t["ram_gb"], None, 1)))
        if t.get("port_mbit") is not None:
            parts.append(tr("host_bench.share.port", v=fmt_num(t["port_mbit"])))
        out.append(tr("host_bench.share.tariff", v=" · ".join(parts)))
    if ex.get("country"):
        out.append(tr("host_bench.share.country", v=ex["country"]))
    if ex.get("cpu"):
        out.append(tr("host_bench.share.cpu", v=ex["cpu"]))
    for f in doc.get("dropped") or ():
        out.append(tr("host_bench.share.dropped." + f))
    out.append(tr("host_bench.share.where"))
    return out


def cmd_share(args):
    from .cli import emit
    from .bench import share
    flags = [f for f in share.FLAGS if getattr(args, f, False)]
    try:
        doc = share.node_share(flags)
    except KaskadError:
        raise
    except Exception as e:                           # noqa: BLE001 — a read error: code 1 with the reason
        raise KaskadError(tr("host_bench.read_fail", error=e), code=errors.ERROR)
    emit(args, doc, None if getattr(args, "json", False) else share_lines(doc))
    return errors.OK


# ── bench ──────────────────────────────────────────────────────────────────

def cost_ctx(runner, ru="?"):
    """The context of ``bench.cost.per_run`` on this node: the scheduled checks of the history, the packages. ``ru`` —
    ``country_offline()`` when the caller has it already."""
    from .bench import history, pkgs
    try:
        hist = history.runs(by="schedule", limit=50)["runs"]
    except Exception:                                # noqa: BLE001 — no history: the caps of the tests
        hist = []
    if ru == "?":
        ru = country_offline()
    return {"history": hist, "ru": False, "pkgs": "no" if pkgs.missing(runner) else "yes",
            "geo": "unknown_ru" if ru is None else None}


def country_offline():
    """REV-ns-p3a П-3: the country of the node as the check finds it, WITHOUT the network (the geo pass of the exits,
    the kept country, the setting; no delays) -> True | False | None. None — the check measures as for Russia unless
    its delays find the country (``suite.node_ru``): the price stays the upper bound (the caps abroad — a price never
    lower than the traffic, the key of the consent of MCP), and the person is told why the upload may be missing."""
    try:
        from . import state
        from .bench import suite
        return suite.node_ru(state.load(), series={"anchors": []}, ms=lambda addr, port: None)
    except Exception:                                # noqa: BLE001 — a state that cannot be read: nothing to say
        return False


def _will_install(plan):
    """The packages a consent installs: the missing ones apt knows (``available`` False — the check goes without)."""
    return bool(plan["missing"]) and plan["available"] is not False


def confirm_key(mode, one, plan):
    """The key of the consent (MCP ``host_bench``): 8 hex of sha256 of the mode, its price and the packages it
    installs. Another price, another mode or other packages — another key (С-1 REV-ns-b7)."""
    doc = {"mode": mode, "price": [one.get(k) for k in ("rx_mb", "tx_mb", "cpu_min", "max_min", "basis")],
           "install": sorted(set(plan["missing"]) | set(plan.get("install") or [])) if _will_install(plan) else []}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()[:8]


def upload_ru_of(ru):
    """D-144: test 8b concerns this node — it may be in Russia (``country_offline`` True or None: a check measures an
    unknown country as Russia). Abroad the upload goes over HTTP: no question, no iperf3."""
    return ru is not False


def upload_question(lang=None):
    """The question of 8b beside the full kind (numbers, never y/n): its line (``run.iperf_consent``) and
    «1) Замерить отдачу 2) Не замерять» — 1 by default (D-144)."""
    from .bench import run
    return {"text": run.iperf_consent(lang), "choices": [(1, tr("host_bench.upload.yes", lang)),
                                                        (2, tr("host_bench.upload.no", lang))], "default": 1}


def price(modes, runner=None):
    """{"modes": {mode: {"rx_mb", "tx_mb", "cpu_min", "basis", "max_min", "confirm"}}, "pkgs": plan + "question",
    "geo", "upload_ru": the question of 8b (``upload_question``) + "pkgs" (the plan with iperf3) when the full kind is
    priced and the node may be in Russia, else None}.
    The programs (С2 REV-score-rename): ``pkgs`` — of the tests 1–7, for the quick kind and a full one with the upload
    turned off; ``upload_ru.pkgs`` — of a full one that measures the upload (iperf3 is asked only when 8b may go;
    ``plan_of``). The key of the full kind covers the second (the upper bound: turned off installs less).
    The price of a manual check is the upper bound: abroad — the caps; a full one of a node that may be in Russia —
    its upload never below the cap of 8b (С1: the scheduled checks the price is measured by never run 8b, NS-S5), so
    the upload on or off keeps the key."""
    from . import sysrun
    from .bench import cost, pkgs, run
    runner = runner or sysrun.default_runner()
    ru = country_offline()
    ctx = cost_ctx(runner, ru)
    u = upload_ru_of(ru) and "full" in modes
    base = pkgs.plan(runner, by="cli")
    up_plan = pkgs.plan(runner, by="cli", iperf=True) if u and "iperf3" in pkgs.missing(runner, iperf=True) else base
    for pl in (base, up_plan):
        pl["question"] = pkgs.question(pl) if pl["missing"] else None
    out = {}
    for m in modes:
        one, basis = cost.per_run(m, ctx)
        if m == "full" and u and one["tx_mb"] < cost.SIDE_CAP_MB["full"]:
            one, basis = dict(one, tx_mb=float(cost.SIDE_CAP_MB["full"])), "caps"     # С1: 8b — up to its cap
        one = dict(one, basis=basis, max_min=run.DEADLINE_S // 60)
        out[m] = dict(one, confirm=confirm_key(m, one, up_plan if m == "full" else base))
    up = dict(upload_question(), pkgs=up_plan) if u else None
    return {"modes": out, "pkgs": base, "geo": ctx.get("geo"), "upload_ru": up}


def plan_of(p, mode, off=False):
    """The programs of what will run (С2 REV-score-rename): a full check with the upload on — ``upload_ru.pkgs`` (with
    iperf3), else ``pkgs`` (the quick kind, the upload turned off, a node abroad: never iperf3)."""
    uq = p.get("upload_ru")
    if mode == "full" and not off and isinstance(uq, dict) and isinstance(uq.get("pkgs"), dict):
        return uq["pkgs"]
    return p["pkgs"]


def price_lines(p):
    lines = [tr("host_bench.price.title")]
    basis = set()
    for m, v in p["modes"].items():
        lines.append("  " + tr("host_bench.price.line", mode=tr("host_bench.mode." + m), rx=_num(v["rx_mb"]),
                               tx=_num(v["tx_mb"]), cpu=_num(v["cpu_min"]), min=v["max_min"]))
        basis.add(v["basis"])
    lines.append("  " + tr("host_bench.price.measured" if basis == {"measured"} else "host_bench.price.caps"))
    if p.get("geo") == "unknown_ru":
        lines.append("  " + tr("host_bench.price.geo_unknown"))     # REV-ns-p3a П-3: the price and «what measured»
    if p.get("upload_ru"):
        lines.append("  " + tr("host_bench.price.upload_ru"))       # D-144: the full kind measures the upload by 8b
    pk = p["pkgs"]
    if pk["missing"]:
        lines.append("  " + tr("host_bench.price.pkgs", pkgs=" ".join(pk["missing"])))
        q = pk.get("question") or {}
        if q.get("text") and not q.get("choices"):
            lines.append("  " + q["text"])                     # apt does not know them: the check goes without
    return lines


def _ask(title, choices, default=1):
    """A question by numbers in the terminal: «1) … 2) …», «Выбор [1]:» (Enter — the default). -> the number."""
    while True:
        sys.stdout.write(title + "\n")
        for n, text in choices:
            sys.stdout.write(" %d) %s\n" % (n, text))
        sys.stdout.write(tr("host_bench.ask.choice", n=default))
        sys.stdout.flush()
        got = sys.stdin.readline()
        if not got:
            return None                              # the end of the input: as «Отмена»
        got = got.strip()
        if got == "":
            return default
        if got.isdigit() and int(got) in [n for n, _t in choices]:
            return int(got)
        sys.stdout.write(tr("host_bench.ask.bad") + "\n")


def _say(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _start_installing(mode, by, install, runner, plan, iperf=False, with_iperf3=None):
    """``run.start`` with the packages: their line first, and Ctrl+C is ignored while apt works (ignored signals pass to
    apt-get; a handler would not: it would cut apt half way). The line says so (З-5 REV-ns-b7)."""
    from .bench import pkgs, run
    if not (install and plan.get("missing")):
        return run.start(mode, by, install=install, runner=runner, iperf=iperf, with_iperf3=with_iperf3)
    pk = " ".join(plan["missing"])
    _say(tr("host_bench.installing", pkgs=pk, mb=pkgs._mb(plan["kib"])) if plan.get("kib")   # MB as the question (П-8)
         else tr("host_bench.installing_bare", pkgs=pk))
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return run.start(mode, by, install=install, runner=runner, iperf=iperf, with_iperf3=with_iperf3)
    finally:
        signal.signal(signal.SIGINT, old)


def _wait(rid, runner):
    """Wait for the check ``rid`` in the text mode: a line per step. -> the status doc of ``run.status`` or None."""
    from .bench import run
    t0 = _mono()
    seen, end = None, t0 + run.RUNTIME_MAX_S + 60
    try:
        while _mono() < end:
            st = run.status(rid, runner)
            if st.get("state") == "unknown" and _mono() - t0 < START_S:
                _sleep(POLL_S)                       # not active yet (П-3): no «итог не найден» while it starts
                continue
            if st.get("state") != "running":
                return st
            pr = st.get("progress") or {}
            key = (pr.get("k"), pr.get("i"), pr.get("n"))
            if (pr.get("n") or pr.get("m")) and key != seen:
                seen = key
                _say("  " + _progress_text(pr))
            _sleep(POLL_S)
    except KeyboardInterrupt:
        _say("")
        _say(tr("host_bench.wait_left"))
        return {"state": "left"}
    _say(tr("host_bench.wait_long", id=rid))
    return {"state": "left"}


def _progress_text(pr):
    """«этап k из m» (the steps of the check, T-3377 Н-8) and «шаг i из n» inside it; a progress without the stage (a
    check started before the update) — the step alone, as before."""
    k, m, i, n = pr.get("k"), pr.get("m"), pr.get("i"), pr.get("n")
    if not (isinstance(k, int) and isinstance(m, int) and m):
        return tr("host_bench.progress", i=i, n=n)
    if n:
        return tr("host_bench.progress_stage", k=k, m=m, i=i, n=n)
    return tr("host_bench.progress_stage0", k=k, m=m)


def result_lines(st, rid):
    from .bench import run
    if st.get("state") == "left":
        return []
    doc = (st.get("result") or {}) if st.get("state") == "done" else {}
    r = doc.get("run") if isinstance(doc.get("run"), dict) else None
    if r is None:
        return [tr("host_bench.done.unknown", id=rid)]
    status = r.get("status")
    why = run.why_text(r.get("why"), r.get("why_args")) if r.get("why") else ""
    if status not in ("ok", "partial", "failed", "skipped", "cancelled"):
        return [tr("host_bench.done.unknown", id=rid)]
    lines = [tr("host_bench.done." + status, why=why) if status not in ("ok", "partial")
             else tr("host_bench.done." + status)]
    if status in ("ok", "partial"):
        try:
            lines.extend(score_lines(score_doc()))
        except Exception:                            # noqa: BLE001 — the result of the check stays said
            pass
    return lines


def cmd_bench(args):
    from . import sysrun
    from .bench import run
    from .cli import emit
    if (getattr(args, "bench_cmd", None) or getattr(args, "scheduled", False)
            or getattr(args, "retry", None) is not None):
        from . import host_cli                       # the timer, its repeat, «schedule apply» (B11)
        return host_cli.cmd_bench(args)
    if args.quick and args.full:
        raise KaskadError(tr("bench_run.err.bad_arg", what="--quick --full"), code=errors.USAGE)
    runner = sysrun.default_runner()
    asked = "full" if args.full else ("quick" if args.quick else None)
    if args.dry:
        p = price([asked] if asked else list(MODES), runner)
        emit(args, dict(p, ok=True, dry=True), price_lines(p))
        return errors.OK
    key = getattr(args, "confirm", None)
    by_mcp = getattr(args, "by_mcp", False)
    if by_mcp and key is None:                       # SS §6.3: MCP asks nothing, its consent is the key
        raise KaskadError(tr("host_bench.confirm_need"), code=errors.USAGE)
    if key is not None and args.yes:
        raise KaskadError(tr("bench_run.err.bad_arg", what="--yes --confirm"), code=errors.USAGE)
    talk = key is None and not args.yes and not getattr(args, "json", False) and _isatty()
    if key is None and not args.yes and not talk:
        raise KaskadError(tr("host_bench.need_yes"), code=errors.USAGE)
    busy = run.active(runner)                        # before the price and the questions (П-5); run.start checks again
    if busy:
        raise KaskadError(tr("bench_run.err.running"), data={"kind": "running", "id": busy[0]})
    mode = asked or "quick"
    no_up = bool(getattr(args, "no_upload_ru", False))
    if by_mcp:                                       # Б-1: MCP — the limits of the panel, its own count; CLI — none
        over = _over(int(time.time()), mode, "mcp")
        if over:
            raise KaskadError(over["text"], code=errors.USAGE,
                              data={"kind": "limit", "limits": over["limits"], "today": over["today"]})
    p = price([asked] if asked else list(MODES), runner)
    install = None
    if key is not None:
        if key != p["modes"][mode]["confirm"]:       # the price, the mode or the packages changed: nothing starts
            raise KaskadError(tr("host_bench.confirm_bad"), code=errors.USAGE, data={"kind": "confirm"})
        if _will_install(plan_of(p, mode, no_up)):
            install = True                           # the packages of the key (turned off — without iperf3)
    elif talk:
        for line in price_lines(p):
            _say(line)
        if asked is None:
            ch = _ask(tr("host_bench.ask.mode"), [
                (1, tr("host_bench.ask.mode_one", mode=tr("host_bench.mode.quick"),
                       mb=_num(p["modes"]["quick"]["rx_mb"] + p["modes"]["quick"]["tx_mb"]))),
                (2, tr("host_bench.ask.mode_one", mode=tr("host_bench.mode.full"),
                       mb=_num(p["modes"]["full"]["rx_mb"] + p["modes"]["full"]["tx_mb"]))),
                (0, tr("host_bench.ask.cancel"))])
            if ch not in (1, 2):
                _say(tr("host_bench.cancelled"))
                return errors.OK
            mode = "quick" if ch == 1 else "full"
        else:
            ch = _ask(tr("host_bench.ask.mode_one", mode=tr("host_bench.mode." + mode),
                         mb=_num(p["modes"][mode]["rx_mb"] + p["modes"][mode]["tx_mb"])),
                      [(1, tr("host_bench.ask.go")), (2, tr("host_bench.ask.cancel"))])
            if ch != 1:
                _say(tr("host_bench.cancelled"))
                return errors.OK
        uq = p.get("upload_ru") or {}
        if mode == "full" and uq.get("choices") and not no_up:      # D-144: 8b — on by default, «2» turns it off
            ch = _ask(uq["text"], uq["choices"], uq.get("default") or 1)   # first: the programs depend on it (С2)
            if ch is None:
                _say(tr("host_bench.cancelled"))
                return errors.OK
            no_up = ch == 2
        q = plan_of(p, mode, no_up).get("question") or {}
        if q.get("choices"):
            ch = _ask(q["text"], q["choices"], q.get("default") or 1)
            if ch != 1:
                _say(tr("host_bench.cancelled"))
                return errors.OK
            install = True
    elif _will_install(plan_of(p, mode, no_up)):
        install = True                               # --yes: the answer «1» of the question
    by = "ssh" if getattr(args, "by_ssh", False) else ("mcp" if by_mcp else "cli")
    iperf = mode == "full" and not no_up             # D-144: a full check measures the upload in Russia by default
    plan = plan_of(p, mode, no_up)
    with3 = "iperf3" in (plan.get("missing") or ())  # С2: only when 8b may go
    if getattr(args, "json", False):
        got = run.start(mode, by, install=install, runner=runner, iperf=iperf, with_iperf3=with3)
    else:
        got = _start_installing(mode, by, install, runner, plan, iperf, with3)
    one = p["modes"].get(mode) or price([mode], runner)["modes"][mode]
    doc = {"ok": True, "id": got["id"], "unit": got["unit"], "mode": mode, "missing": got["missing"], "price": one,
           "upload_ru": bool(iperf and p.get("upload_ru"))}
    if getattr(args, "json", False):
        emit(args, doc)
        return errors.OK
    _say(tr("host_bench.started", mode=tr("host_bench.mode_in." + mode), id=got["id"], min=one["max_min"]))
    _say("  " + tr("host_bench.wait_hint"))
    st = _wait(got["id"], runner)
    for line in result_lines(st, got["id"]):
        _say(line)
    return errors.OK


# ── the button of the panel (6C «Проверить сервер»; POST /api/host/bench) ──────────────────────────────────────

START_KEYS = ("mode", "confirm", "install", "iperf")
START_MAX = 1024                    # bytes of the body (it is ≈ 60)
PANEL_PER_DAY = 6                   # checks started from the panel in a day of the node — the limits of the schedule
PANEL_FULL_PER_DAY = 2              # (bench.schedule.PER_DAY, FULL_PER_DAY; NS-S3): of them full
_KEY = re.compile(r"[0-9a-f]{8}")


def _panel_today(now, by="panel"):
    """The checks started by ``by`` (the panel; MCP — its own count, REV-ns-p4a Б-1) today (the day of KASKAD_TZ), a
    skip not counted -> (all, full)."""
    from . import tz
    from .bench import history
    day = tz.day(now)
    rs = [r for r in history.runs(frm=day, to=day, by=by, limit=500, now=now)["runs"] if r["status"] != "skipped"]
    return len(rs), sum(1 for r in rs if r["mode"] == "full")


def _over(now, mode, by):
    """The limit of a day of ``by`` (PANEL_PER_DAY / PANEL_FULL_PER_DAY): None or {"text", "limits", "today"} — the
    text ``host_bench.<by>.limit`` (all) or ``.limit_full``."""
    n, full = _panel_today(now, by)
    if n < PANEL_PER_DAY and not (mode == "full" and full >= PANEL_FULL_PER_DAY):
        return None
    many = n >= PANEL_PER_DAY
    return {"text": tr("host_bench.%s.%s" % (by, "limit" if many else "limit_full"), n=n if many else full),
            "limits": {"per_day": PANEL_PER_DAY, "full_per_day": PANEL_FULL_PER_DAY}, "today": {"all": n, "full": full}}


def panel_start(doc, runner=None, now=None):
    """POST /api/host/bench — the check from the panel, as MCP ``host_bench``: the body {"mode", "confirm"?,
    "install"?} is checked strictly; the price and the packages are counted again (``price``) and the check starts only
    with the key ``modes.<mode>.confirm`` of THIS price: no key — 409 ``need_confirm`` with the price (the window of
    the price, «отказ с ценой»), another key — 409 ``confirm_changed`` with the new price; nothing starts or is
    installed. ``install`` — required when the key installs packages (no field — 409 ``need_install``, З-1 REV-ns-p4a):
    true — they are installed; false — the answer «нет» (remembered: the schedule is not armed, NS-S4; the check goes
    without them). ``iperf`` — test 8b of a full check (D-144): on unless false (the tick of the window; the quick
    kind never runs it). The limits of the schedule for a day (``PANEL_PER_DAY`` / ``PANEL_FULL_PER_DAY``) —
    429 ``limit``; another check runs — 409 ``running``. -> {"ok", "id", "unit", "mode", "missing", "price",
    "upload_ru"}.
    Refusals — ``host_sched.Refused`` (the texts in the reader's language)."""
    from . import sysrun
    from .bench import run
    from .bench import schedule as bsched
    from .host_sched import Refused
    if not isinstance(doc, dict):
        raise Refused(400, "invalid", tr("panel.bench.badValue", field="body"), field="body")
    for k in doc:
        if k not in START_KEYS:
            raise Refused(400, "invalid", tr("panel.bench.badQuery", field=bsched._key_shown(k)), field="body")
    mode, key, install, up = doc.get("mode"), doc.get("confirm"), doc.get("install"), doc.get("iperf")
    if mode not in MODES:
        raise Refused(400, "invalid", tr("panel.bench.badValue", field="mode"), field="mode")
    if key is not None and not (isinstance(key, str) and _KEY.fullmatch(key)):
        raise Refused(400, "invalid", tr("panel.bench.badValue", field="confirm"), field="confirm")
    if install is not None and not isinstance(install, bool):
        raise Refused(400, "invalid", tr("panel.bench.badValue", field="install"), field="install")
    if up is not None and not isinstance(up, bool):
        raise Refused(400, "invalid", tr("panel.bench.badValue", field="iperf"), field="iperf")
    now = int(time.time() if now is None else now)
    runner = runner or sysrun.default_runner()
    busy = run.active(runner)
    if busy:
        raise Refused(409, "running", tr("bench_run.err.running"), id=busy[0])
    over = _over(now, mode, "panel")
    if over:
        raise Refused(429, "limit", over["text"], limits=over["limits"], today=over["today"])
    p = price([mode], runner)
    one = p["modes"][mode]
    plan = plan_of(p, mode, up is False)            # С2: iperf3 only when 8b may go
    if key is None or key != one["confirm"]:
        raise Refused(409, "need_confirm" if key is None else "confirm_changed",
                      tr("host_bench.panel.need_confirm" if key is None else "host_bench.panel.confirm_changed"),
                      price=one, pkgs=plan)
    if _will_install(plan) and install is None:
        # З-1 REV-ns-p4a: the programs — a consent of their own (the tick of the window of the price), never by default;
        # nothing is installed or started
        raise Refused(409, "need_install", tr("host_bench.panel.need_install"), price=one, pkgs=plan)
    answer = install if _will_install(plan) else None
    iperf = mode == "full" and up is not False
    try:
        got = run.start(mode, "panel", install=answer, runner=runner, iperf=iperf,
                        with_iperf3="iperf3" in (plan.get("missing") or ()))
    except KaskadError as e:
        data = getattr(e, "data", None) or {}
        if data.get("kind") == "running":
            raise Refused(409, "running", str(e), id=data.get("id"))
        raise
    return {"ok": True, "id": got["id"], "unit": got["unit"], "mode": mode, "missing": got["missing"], "price": one,
            "upload_ru": bool(iperf and p.get("upload_ru"))}


def panel_price(runner=None, now=None):
    """GET /api/host/bench/price — the window «Проверить сервер» before the consent (T-3377 Н-15): what ``panel_start``
    without a key refuses with (409 ``running``, 429 ``limit``, 409 ``need_confirm`` with the price and the packages),
    said as a plain read — the browser writes every 4xx to the console as an error, and the window asked twice per
    opening. -> {"ok", "running": id | None, "modes": {mode: price with its key}, "limit": {mode: {"text", "limits",
    "today"}}, "pkgs", "upload_ru"}: ``upload_ru`` — the question of 8b (D-144) when the full kind has a price and the
    node may be in Russia (the window shows its tick, on; its ``pkgs`` — the programs with the tick on, ``pkgs`` — of
    the quick kind and the tick off, С2), else null; a mode over the limit of the day has no price (as POST: no key for it); another check runs —
    no prices at all (as POST, nothing is counted). Starts, installs and writes nothing. As a few other reads of the
    panel (``/api/site``, ``/api/bot``, the journal of a protocol — ``runs`` of kaskad.panel.routes) it runs commands —
    read-only ones, the same POST without a key ran (``systemctl list-units``, ``apt-get -s``, ``apt-cache show``):
    only when a person opens the window, never polled; a foreign page is refused, one count at a time
    (kaskad.panel.host._price_once)."""
    from . import sysrun
    from .bench import run
    now = int(time.time() if now is None else now)
    runner = runner or sysrun.default_runner()
    busy = run.active(runner)
    if busy:
        return {"ok": True, "running": busy[0], "modes": {}, "limit": {}, "pkgs": None, "upload_ru": None}
    limit = dict((m, o) for m, o in ((m, _over(now, m, "panel")) for m in MODES) if o)
    free = [m for m in MODES if m not in limit]
    p = price(free, runner) if free else {"modes": {}, "pkgs": None}
    return {"ok": True, "running": None, "modes": p["modes"], "limit": limit, "pkgs": p["pkgs"],
            "upload_ru": p.get("upload_ru")}


def register(s):
    """``host score`` and ``host bench`` under the subparsers ``s`` of ``kaskadctl host`` (host_cli.register)."""
    from .cli import add_json_flag
    sc = add_json_flag(s.add_parser("score", help=tr("host_bench.help.score")))
    sc.add_argument("--explain", action="store_true", help=tr("host_bench.help.explain"))
    sc.add_argument("--screen", action="store_true", help=argparse.SUPPRESS)      # the screen of item 22 (SSH menu)
    sc.set_defaults(func=cmd_score)
    sh = add_json_flag(s.add_parser("share", help=tr("host_bench.help.share")))
    for f in ("tariff", "country", "cpu"):                                  # C9: off by default
        sh.add_argument("--" + f, action="store_true", help=tr("host_bench.help.share_" + f))
    sh.set_defaults(func=cmd_share)
    b = add_json_flag(s.add_parser("bench", help=tr("host_bench.help.bench")))
    b.add_argument("--quick", action="store_true", help=tr("host_bench.help.quick"))
    b.add_argument("--full", action="store_true", help=tr("host_bench.help.full"))
    b.add_argument("--dry", action="store_true", help=tr("host_bench.help.dry"))
    b.add_argument("--yes", action="store_true", help=tr("host_bench.help.yes"))
    b.add_argument("--confirm", metavar="KEY", help=tr("host_bench.help.confirm"))
    b.add_argument("--no-upload-ru", dest="no_upload_ru", action="store_true", help=tr("host_bench.help.no_upload_ru"))
    b.add_argument("--by-ssh", dest="by_ssh", action="store_true", help=argparse.SUPPRESS)    # item 22: who started
    b.add_argument("--by-mcp", dest="by_mcp", action="store_true", help=argparse.SUPPRESS)    # MCP host_bench
    b.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)      # the service of the timer (B11)
    b.add_argument("--retry", metavar="ID", help=argparse.SUPPRESS)                # the transient timer of a repeat
    b.add_argument("--links", default="", help=argparse.SUPPRESS)                   # its links (SS §2.8 п. 2)
    bs = b.add_subparsers(dest="bench_cmd")
    from . import host_sched                         # schedule (apply — the timer, B11) | history | show | … (B14)
    host_sched.register(bs, add_json_flag)
    b.set_defaults(func=cmd_bench)
