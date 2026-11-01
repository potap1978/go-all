"""Сухая синтетика: балл сервера 0–1000 по 8 звеньям и итог (решение владельца 27.10, круг 1, шаг Т1 — вариант Б
«Обзор»: радар «Балл сервера», окно последней проверки, «Проверка сервера › Сейчас»).

Кривые, веса и якоря 100 / 500 / 700 / 900 — приложение A k3/handoff/RESEARCH-node-score.md (0.4-draft — числа
score-curves.json до score-cut 23.10, коммит 3cae428a^). Калибровка К1 (28.10, ``abs-0.5-k1``): якоря процессора —
cpu_1t, cpu_mt, chacha_1t, aes_1t, mem_bw — по сырым проверкам эталонов Aeza (1 и 2 vCPU общие, выделенное ядро Ryzen 9
9950X); данные и правило — tests/fixtures/score_abs_calib_k1.json, отчёт — k3/stand/logs/K-it1-calib.md. Остальные
якоря — из исследования (канал, сеть, стабильность и диск зависят от хостера и места, двумя серверами одного города их
не задать).

Балл теста — логистическая кривая по четырём якорям в координатах (ln x, logit s); концы продолжают крайние отрезки.
Звенья (порядок экрана, радар): Канал (группа net_bw), Сеть (net_q), Процессор (cpu), Стабильность (stab), скачивание
(net_down), отдача (net_up), потери пакетов (loss — по нижней границе Уилсона ``lo``), шифрование (шифр движков узла).
Группа — среднее её тестов по весам; ``raw`` — среднее групп по весам (net_bw 20, net_q 20, cpu 25, stab 20, mem 10,
disk 5). Слабое звено (владелец 29.10, D-143) — худшее из звеньев этой проверки, только если оно ниже их среднего
больше чем на 30 % (``WEAK_GAP``; звенья без данных в среднее не входят): тогда потолок ``cap = b + 350·min(1, b/100)``,
b — его балл, итог = min(raw, cap); такого звена нет — нет и потолка, итог = raw.
Итог показывается, когда измерены 4 обязательных звена-группы и ≥ 70 % веса того, что можно измерить сейчас; иначе
``total`` None и ``why`` «incomplete» / «nothing_measured».

Только чистые функции: ни файла, ни базы, ни часов. Значения — как у ``kaskad.score``: число или {"v", "mode", "n",
"lo"}; служебные ``_engines`` / ``_cpu_flags`` — контекст шифра. Берёт только панель (GET /api/host/synth); CLI, MCP,
бот и «Поделиться» его не показывают (сторож tests/test_score_guard.py).

Калибровка по звеньям (владелец 28.10, решение 5; REV-it1 «Дополнение» №7): выверено звено, все тесты которого есть в
CALIB_TESTS (сейчас «Процессор» и «Шифрование»); остальные звенья — «предварительно». ``CALIBRATED`` — шкала выверена
целиком (все звенья), пока False. Эталоны — REFS: только для кода и тестов, наружу (API, панель) хостер, тариф,
процессор и город эталонов не выходят (владелец 29.10, D-142). Отступление от RESEARCH §4.4:
базовый эталон по группе «Процессор» ≈ 790, а не 430–570 — решение владельца 28.10 (D-140, SPEC-node-score NS-D36).
"""

import math

VERSION = "abs-0.5-k1"
A = (100, 500, 700, 900)
MARGIN = 350
WEAK_GAP = 30               # % ниже среднего по звеньям проверки: строже — не слабое звено (владелец 29.10, D-143)
MIN_MEASURED = 0.7
ALL_ENGINES = ("awg", "wg", "vless")


def _t(tid, better, w, anchors, **kw):
    return dict({"id": tid, "better": better, "w": w, "anchors": dict(zip(A, anchors))}, **kw)


# приложение A (0.4-draft; процессор — К1): (id, лучше, вес, якоря 100/500/700/900, пол, потолок быстрой проверки, …)
GROUPS = (
    {"id": "net_bw", "weight": 20, "tests": (
        _t("net_down", "higher", 1, (30, 300, 600, 1500), floor=0.01, quick_cap=700),
        _t("net_up", "higher", 1, (20, 200, 500, 1200), floor=0.01, quick_cap=700))},
    {"id": "net_q", "weight": 20, "tests": (
        _t("rtt", "lower", 1, (120, 25, 10, 3), floor=0.05),
        _t("loss", "lower", 1.5, (5, 0.5, 0.1, 0.01), floor=0.005, per_sample=True, min_n=10000),
        _t("jitter", "lower", 1, (30, 3, 1, 0.3), floor=0.05, min_n=1000),
        _t("dns", "lower", 0.5, (300, 40, 15, 5), floor=0.05))},
    {"id": "cpu", "weight": 25, "tests": (
        _t("cpu_1t", "higher", 1, (600, 2400, 3600, 6000)),
        _t("cpu_mt", "higher", 1, (910, 5500, 11000, 33000)),
        _t("chacha_1t", "higher", 1.5, (150, 630, 1100, 2000)),
        _t("aes_1t", "higher", 0.5, (300, 2300, 4000, 6900)))},
    {"id": "stab", "weight": 20, "tests": (
        _t("steal_p95", "lower", 1.5, (20, 3, 1, 0.2), floor=0.05),
        _t("cpu_cv", "lower", 1, (30, 8, 4, 1.5), floor=0.3),
        _t("cpu_sustain", "higher", 1, (0.5, 0.9, 0.95, 0.98), clamp_max=1.0),
        _t("cpu_canary_cv", "lower", 1, (40, 10, 5, 2), floor=0.5),
        _t("net_cv", "lower", 1, (60, 25, 12, 5), floor=1))},
    {"id": "mem", "weight": 10, "tests": (
        _t("mem_gb", "higher", 1, (0.5, 2, 4, 16)),
        _t("mem_bw", "higher", 0.5, (3000, 12000, 20000, 35000)))},
    {"id": "disk", "weight": 5, "tests": (
        _t("disk_rr", "higher", 1, (1000, 15000, 40000, 120000)),
        _t("disk_rw", "higher", 1, (500, 8000, 25000, 80000)),
        _t("disk_fsync", "lower", 1, (20, 2, 0.8, 0.2), floor=0.02))},
)
TESTS = dict((t["id"], t) for g in GROUPS for t in g["tests"])
# 8 звеньев в порядке радара; required — звенья-группы (без них итога нет)
LINKS = (
    {"id": "net_bw", "groups": ("net_bw",), "required": True},
    {"id": "net_q", "groups": ("net_q",), "required": True},
    {"id": "cpu", "groups": ("cpu",), "required": True},
    {"id": "stab", "groups": ("stab",), "required": True},
    {"id": "down", "tests": ("net_down",)},
    {"id": "up", "tests": ("net_up",)},
    {"id": "loss", "tests": ("loss",), "lo": True},
    {"id": "crypto", "tests": ("chacha_1t", "aes_1t"),
     "only_if": {"chacha_1t": ({"engine": ("awg", "wg")}, {"engine": ("vless",), "no_cpu_flag": "aes"}),
                 "aes_1t": ({"engine": ("vless",), "cpu_flag": "aes"},)}},
)
LINK_IDS = tuple(b["id"] for b in LINKS)

# тесты, якоря которых сняты с эталонных серверов (К1, tests/fixtures/score_abs_calib_k1.json; правило — там же)
CALIB_TESTS = ("cpu_1t", "cpu_mt", "chacha_1t", "aes_1t", "mem_bw")
CALIB_DATE = "2026-10-28"
# эталоны К1 (без адресов и номеров серверов): anchor — какой якорь задаёт, scope — «all» все ядра / «core» одно ядро,
# date — последний день замеров. Только внутри: API и панель их не показывают (D-142, сторож test_panel_t1_api)
REFS = (
    {"anchor": 500, "scope": "all", "hoster": "Aeza", "tariff": "MSKs-1", "vcpu": 1, "dedicated": False,
     "cpu": "AMD Ryzen 9 9950X", "city": "msk", "date": CALIB_DATE},
    {"anchor": 700, "scope": "all", "hoster": "Aeza", "tariff": "MSKs-2", "vcpu": 2, "dedicated": False,
     "cpu": "AMD Ryzen 9 9950X", "city": "msk", "date": CALIB_DATE},
    {"anchor": 900, "scope": "core", "hoster": "Aeza", "tariff": "MSK-1", "vcpu": 1, "dedicated": True,
     "cpu": "AMD Ryzen 9 9950X", "city": "msk", "date": "2026-10-23"},           # волна 1 B9: 20.10 и 23.10
)


def link_tests(lid):
    """The tests of a link (its groups' tests or its own), in order; () — no such link."""
    b = next((x for x in LINKS if x["id"] == lid), None)
    if b is None:
        return ()
    if "groups" in b:
        return tuple(t["id"] for g in GROUPS if g["id"] in b["groups"] for t in g["tests"])
    return tuple(b["tests"])


def link_calibrated(lid):
    """True — every test of the link has the anchors of the reference servers (CALIB_TESTS); else «предварительно»."""
    tids = link_tests(lid)
    return bool(tids) and all(t in CALIB_TESTS for t in tids)


CALIB_LINKS = tuple(lid for lid in LINK_IDS if link_calibrated(lid))
CALIBRATED = len(CALIB_LINKS) == len(LINK_IDS)          # шкала выверена целиком


def _logit(s):
    return math.log(s / (1000.0 - s))


def _num(x):
    """A finite float or None (bool, text, NaN, ±inf — not a number)."""
    if x is None or isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        x = float(x)
    except (OverflowError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _u(t, v):
    v = math.log(v)
    return v if t["better"] == "higher" else -v


def curve(t):
    """The anchor points (u, L) of a test; ValueError when they are not strictly monotone."""
    pts = [(_u(t, float(t["anchors"][a])), _logit(a)) for a in A]
    if any(pts[i + 1][0] <= pts[i][0] for i in range(3)):
        raise ValueError("anchors of %s are not strictly monotone" % t["id"])
    return pts


def _parts(m):
    """(v, n, lo, mode) of a value: a number or {"v", "n", "lo", "mode"}; v None — not measured."""
    if isinstance(m, dict):
        if m.get("ok") is False or m.get("wait"):
            return None, None, None, None
        return _num(m.get("v")), _num(m.get("n")), _num(m.get("lo")), m.get("mode") or "full"
    return _num(m), None, None, "full"


def score_of(tid, x, mode="full", n=None):
    """The score 0–1000 of the value ``x`` of the test ``tid`` (float, not rounded); None — no such test or no value.
    The ends extend the outer segments; a quick channel test — at most its ``quick_cap``; loss — at least 100/n %."""
    t = TESTS.get(tid)
    x = _num(x)
    if t is None or x is None:
        return None
    lo = t.get("floor") or 1e-6 * min(float(v) for v in t["anchors"].values())
    if t.get("per_sample") and n:
        lo = max(lo, 100.0 / n)                     # «0 из n» — это «меньше 100/n %»
    x = min(max(x, lo), t.get("clamp_max", float("inf")))
    pts, q = curve(t), _u(t, x)
    i = 0 if q <= pts[1][0] else (1 if q <= pts[2][0] else 2)
    (x0, y0), (x1, y1) = pts[i], pts[i + 1]
    s = 1000.0 / (1.0 + math.exp(-(y0 + (y1 - y0) * (q - x0) / (x1 - x0))))
    if mode == "quick" and "quick_cap" in t and s > t["quick_cap"]:
        s = float(t["quick_cap"])
    return s


def test_score(tid, m):
    """The score of a value of the check / the fresh value; None — not measured or too few samples (``min_n``)."""
    t = TESTS.get(tid)
    v, n, _lo, mode = _parts(m)
    if t is None or v is None:
        return None
    if n is not None and n < t.get("min_n", 0):
        return None                                 # мало выборок (потери < 10 000 пакетов, дрожание < 1000)
    return score_of(tid, v, mode, n)


def _link_value(tid, m):
    """The score of a test inside a test link: loss — by its lower bound ``lo`` (no floor 100/n) and at once, without
    ``min_n`` (the bound is wide on few packets itself), else as the test."""
    if tid == "loss":
        v, n, lo, mode = _parts(m)
        if v is None:
            return None
        return score_of("loss", lo if lo is not None else v, mode)
    return test_score(tid, m)


def _applies(conds, engines, flags):
    if not conds:
        return True
    for c in conds:
        if "engine" in c and not set(c["engine"]) & set(engines):
            continue
        if "cpu_flag" in c and c["cpu_flag"] not in flags:
            continue
        if "no_cpu_flag" in c and c["no_cpu_flag"] in flags:
            continue
        return True
    return False


def _ctx(vals):
    e, f = vals.get("_engines"), vals.get("_cpu_flags")
    return (list(e) if isinstance(e, (list, tuple)) else list(ALL_ENGINES),
            list(f) if isinstance(f, (list, tuple)) else ["aes"])


def total(vals, skip=()):
    """{"links": {link: score | None}, "groups": {group: score}, "raw", "cap", "total", "weak", "limited",
    "measured", "why"} of the values {test: value}; scores rounded down to integers (on screen at most 999).
    ``skip`` — links still being measured (решение владельца 28.10 №4, вариант А: первые часы нового узла «Сеть» ещё
    копит пробы): they and their groups are out of the weight, the measured share, the weak link and the required
    ones; their score is None."""
    vals = vals if isinstance(vals, dict) else {}
    skip = set(skip or ())
    skip_g = set(i for b in LINKS if b["id"] in skip for i in b.get("groups") or ())
    engines, flags = _ctx(vals)
    tests, waiting = {}, set()
    for tid in TESTS:
        m = vals.get(tid)
        s = test_score(tid, m)
        if s is not None:
            tests[tid] = s
        elif _parts(m)[0] is not None:
            waiting.add(tid)                         # значение есть, выборок мало — вне доли измеренного
    groups, got_w, now_w = {}, 0.0, 0.0
    for g in GROUPS:
        if g["id"] in skip_g:
            continue
        num = den = all_w = wait_w = 0.0
        for t in g["tests"]:
            all_w += t["w"]
            if t["id"] not in tests:
                if t["id"] in waiting:
                    wait_w += t["w"]
                continue
            num += t["w"] * tests[t["id"]]
            den += t["w"]
        if den:
            groups[g["id"]] = num / den
        if all_w > wait_w:
            got_w += g["weight"] * den / (all_w - wait_w)
            now_w += g["weight"]
    links = {}
    for b in LINKS:
        if b["id"] in skip:
            links[b["id"]] = None
        elif "groups" in b:
            got = [groups[i] for i in b["groups"] if i in groups]
            links[b["id"]] = got[0] if got else None
        else:
            use = [_link_value(i, vals.get(i)) for i in b["tests"]
                   if _applies(b.get("only_if", {}).get(i), engines, flags)]
            use = [s for s in use if s is not None]
            links[b["id"]] = min(use) if use else None
    out = {"links": dict((k, _shown(v)) for k, v in links.items()),
           "groups": dict((k, _shown(v)) for k, v in groups.items()),
           "raw": None, "cap": None, "total": None, "weak": None, "limited": False,
           "measured": round(got_w / now_w, 3) if now_w else 0.0, "why": None}
    if not groups:
        out["why"] = "nothing_measured"
        return out
    weights = dict((g["id"], g["weight"]) for g in GROUPS)
    raw = sum(weights[k] * s for k, s in groups.items()) / sum(weights[k] for k in groups)
    weak = weak_link(out["links"])
    if weak:
        b = links[weak]
        cap = b + MARGIN * min(1.0, b / 100.0)
        out.update(cap=_shown(cap), weak=weak, limited=cap < raw)
    else:
        cap = raw                                    # нет слабого звена — нет и предела (D-143)
    out["raw"] = _shown(raw)
    need = [x["id"] for x in LINKS if x.get("required") and x["id"] not in skip]
    if any(links.get(i) is None for i in need) or out["measured"] < MIN_MEASURED:
        out["why"] = "incomplete"
        return out
    out["total"] = _shown(min(raw, cap))
    return out


def weak_link(shown):
    """The weak link of the shown scores {link: int | None} of ONE check (владелец 29.10, D-143): the worst link, only
    when it is below the mean of the links with a score by MORE than ``WEAK_GAP`` % (29 % — not weak, 31 % — weak;
    exactly 30 % — not); the links without data are not in the mean. Ties — the first in the order of the radar. The
    numbers are the shown ones (whole, as on the radar), the comparison is in whole numbers: what a person counts on
    the screen is what the rule says. -> link id | None."""
    have = [(k, v) for k, v in ((k, (shown or {}).get(k)) for k in LINK_IDS)
            if type(v) is int or (isinstance(v, float) and math.isfinite(v))]
    if len(have) < 2:
        return None
    k, b = min(have, key=lambda x: (x[1], LINK_IDS.index(x[0])))
    # b < mean·(1 − gap/100)  <=>  100·n·b < (100 − gap)·sum
    if 100 * len(have) * b < (100 - WEAK_GAP) * sum(v for _k, v in have):
        return k
    return None


def _shown(s):
    """On screen: floor, at most 999 (30 Mbit/s -> 100, not 99.99…); None stays None."""
    return None if s is None else int(min(999, math.floor(s + 1e-9)))


def median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = len(xs)
    return xs[k // 2] if k % 2 else int(round((xs[k // 2 - 1] + xs[k // 2]) / 2.0))


def quartiles(xs):
    """(p25, p75) of the scores (inclusive, as statistics.quantiles), None when fewer than 2."""
    xs = sorted(x for x in xs if x is not None)
    if len(xs) < 2:
        return None
    def q(p):
        i = p * (len(xs) - 1)
        a = int(math.floor(i))
        b = min(a + 1, len(xs) - 1)
        return int(round(xs[a] + (xs[b] - xs[a]) * (i - a)))
    return q(0.25), q(0.75)
