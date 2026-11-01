"""The price of the schedule (SPEC-node-score-schedule-share §2.3, §2.8 п. 4): ONE function, the panel shows it at
every change of an entry, the CLI and MCP give the same numbers.

    estimate(entries, ctx) -> {"week": {"quick", "full"}, "day": {"rx_mb", "tx_mb", "cpu_min"}, "month": {…},
                               "per_run": {"quick": {…}, "full": {…}}, "basis": "measured" | "caps",
                               "basis_mode": {"quick", "full"}, "retry": None | {"per_day", "max_mb"},
                               "max_per_day": n, "per_day_even": bool, "notes": [{"code", "args", "text"}]}

``max_per_day`` — the most checks of the enabled entries on one day of the week (a monthly entry counts on its day,
as the limits NS-S3 count): «до N проверок в день»; ``per_day_even`` — every day of the week has the same number
(«N проверки в день»; no checks — 0 and True). Repeats (``retry``) are not in it.

``ctx`` = {"history": [bench_run rows], "ru": bool, "pkgs": "yes" | "no" | None, "tariff_traffic_gb": n | None,
"ru_point": False | True | {"quick": mb, "full": mb}, "retry": bool}. ``history`` — the rows of ``bench_run`` (§3.1,
B12: ``history.runs``; until B12 a caller passes [] or rows of the same contract): only the checks BY THE SCHEDULE
(``by == "schedule"``) of a mode with ``ok``/``partial`` count — a manual full check with 8b (up to + 2 GB) or with the
upload of a node in Russia measures another set of tests. Fewer than 3 such checks of a mode — the caps of RS §2.

Megabytes are decimal (1 GB = 1000 MB), as the threshold «100 GB a month».
"""

MB = 1000 * 1000
SIDE_CAP_MB = {"quick": 250, "full": 2000}       # RS §2: a side of the channel test (download, upload)
PROBES_MB = 0.03                                 # the series of probes and DNS: < 30 KB
CPU_MIN = {"quick": 0.9, "full": 1.0}            # RS §2: ≈ 41 s of a core at full load + fio/channel; full ≈ 46 s + …
NO_PKGS_CPU_MIN = 35 / 60.0                      # no sysbench: ≈ −35 s
MEASURED_MIN = 3                                 # scheduled checks of a mode for «measured»
MEASURED_LAST = 5                                # the median of the last 5
DAYS_IN_MONTH = 30.4
HEAVY_GB = 100                                   # §11.1 п. 2: a month ≥ 100 GB …
HEAVY_SHARE = 0.10                               # … or ≥ 10 % of the traffic of the tariff — what comes first
RETRY_PER_DAY = 2                                # NS-S15: up to 2 repeats a day
MODES = ("quick", "full")


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def _num(v):
    return type(v) in (int, float) and v >= 0


def _runs(history, mode):
    """The scheduled checks of ``mode`` that count, newest first (by ``ts``)."""
    out = []
    for r in history if isinstance(history, (list, tuple)) else []:
        if (isinstance(r, dict) and r.get("by") == "schedule" and r.get("mode") == mode
                and r.get("status") in ("ok", "partial")):
            out.append(r)
    out.sort(key=lambda r: r.get("ts") if _num(r.get("ts")) else 0, reverse=True)
    return out


def _measured(history, mode, key):
    vals = [r[key] for r in _runs(history, mode) if _num(r.get(key))][:MEASURED_LAST]
    return _median(vals) if len(vals) >= MEASURED_MIN else None


def _point_cap(ru_point, mode):
    if isinstance(ru_point, dict):
        v = ru_point.get(mode)
        return float(v) if _num(v) else SIDE_CAP_MB[mode]
    return SIDE_CAP_MB[mode]


def per_run(mode, ctx):
    """The price of ONE check of ``mode`` -> ({"rx_mb", "tx_mb", "cpu_min"}, basis)."""
    ctx = ctx if isinstance(ctx, dict) else {}
    hist = ctx.get("history")
    ru, point = bool(ctx.get("ru")), ctx.get("ru_point")
    rx = _measured(hist, mode, "rx")
    tx = _measured(hist, mode, "tx")
    cpu = _measured(hist, mode, "cpu_s")
    basis = "measured" if rx is not None and tx is not None else "caps"
    if basis == "measured":
        rx_mb, tx_mb = rx / MB, tx / MB
    else:
        rx_mb = SIDE_CAP_MB[mode] + PROBES_MB
        if ru:
            tx_mb = _point_cap(point, mode) if point else 0.0
        else:
            tx_mb = float(SIDE_CAP_MB[mode])
    if cpu is not None and basis == "measured":
        cpu_min = cpu / 60.0
    else:
        cpu_min = CPU_MIN[mode] - (NO_PKGS_CPU_MIN if ctx.get("pkgs") != "yes" else 0.0)
    return {"rx_mb": round(rx_mb, 2), "tx_mb": round(tx_mb, 2), "cpu_min": round(cpu_min, 2)}, basis


def runs_per_month(entries):
    """{"quick", "full"}: the checks a month of the enabled entries after ``validate`` (a weekly entry — its days ×
    30.4 / 7, a monthly one — 1)."""
    from .schedule import validate
    out = {m: 0.0 for m in MODES}
    for e in validate(entries)[0]:
        if e["on"]:
            out[e["mode"]] += 1.0 if e["monthly"] else len(e["days"]) * DAYS_IN_MONTH / 7.0
    return out


def runs_per_day(entries):
    """[7 counts] of the enabled entries after ``validate`` per day of the week (ISO 1–7 -> index 0–6)."""
    from .schedule import validate
    n = [0] * 7
    for e in validate(entries)[0]:
        if e["on"]:
            for d in e["days"]:
                n[d - 1] += 1
    return n


def heavy(month_mb, tariff_gb):
    """§11.1 п. 2: ≥ 100 GB a month or ≥ 10 % of the tariff (when it is known)."""
    if month_mb >= HEAVY_GB * 1000 - 1e-6:
        return True
    if _num(tariff_gb) and tariff_gb > 0 and month_mb >= HEAVY_SHARE * tariff_gb * 1000 - 1e-6:
        return True
    return False


def _note(code, **args):
    """A note: ``args`` — numbers (for the panel), ``text`` — in the language of the caller, numbers by ``fmt_num``."""
    from ..i18n import fmt_num, tr
    shown = {k: fmt_num(v, digits=1 if isinstance(v, float) and v < 10 else 0) for k, v in args.items()}
    return {"code": code, "args": args, "text": tr("bench.cost.note." + code, **shown)}


def _fmt(x):
    return float("%.2f" % x)


def estimate(entries, ctx):
    """See the head of the module."""
    ctx = ctx if isinstance(ctx, dict) else {}
    month_runs = runs_per_month(entries)
    one, basis_mode = {}, {}
    for m in MODES:
        one[m], basis_mode[m] = per_run(m, ctx)
    month = {"rx_mb": 0.0, "tx_mb": 0.0, "cpu_min": 0.0}
    for m in MODES:
        for k in month:
            month[k] += month_runs[m] * one[m][k]
    day = {k: v / DAYS_IN_MONTH for k, v in month.items()}
    week = {m: _fmt(month_runs[m] / DAYS_IN_MONTH * 7) for m in MODES}
    used = [m for m in MODES if month_runs[m] > 0]
    basis = "measured" if used and all(basis_mode[m] == "measured" for m in used) else "caps"
    notes = []
    if ctx.get("ru"):
        notes.append(_note("upload_ru_point") if ctx.get("ru_point") else _note("no_upload_ru"))
    if ctx.get("pkgs") != "yes":
        notes.append(_note("no_pkgs"))
    total_mb = month["rx_mb"] + month["tx_mb"]
    tariff = ctx.get("tariff_traffic_gb")
    if _num(tariff) and tariff > 0 and total_mb > 0:
        pct = total_mb / (tariff * 1000.0) * 100
        if pct < 0.1:                                  # «≈ 0,0%» says «nothing»: «< 0,1%» (REV-ns-b10 П-3)
            notes.append(_note("tariff_share_small", pct=0.1))
        else:
            notes.append(_note("tariff_share", pct=round(pct, 1) if pct < 10 else int(round(pct))))
    if heavy(total_mb, tariff):
        notes.append(_note("heavy", gb=int(round(total_mb / 1000.0))))
    retry = None
    if ctx.get("retry", True) is not False and used:
        caps = dict(ctx, history=[])                       # the worst case: the cap of the mode on both sides
        worst = max(sum(per_run(m, caps)[0][k] for k in ("rx_mb", "tx_mb")) for m in used)
        retry = {"per_day": RETRY_PER_DAY, "max_mb": int(round(RETRY_PER_DAY * worst))}
        notes.append(_note("retry", n=RETRY_PER_DAY, mb=retry["max_mb"]))
    per_day = runs_per_day(entries)
    return {"week": week, "max_per_day": max(per_day), "per_day_even": len(set(per_day)) == 1,
            "day": {k: _fmt(v) for k, v in day.items()},
            "month": {k: _fmt(v) for k, v in month.items()},
            "per_run": one, "basis": basis, "basis_mode": basis_mode, "retry": retry, "notes": notes}
