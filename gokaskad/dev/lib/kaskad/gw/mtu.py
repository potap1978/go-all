"""Безопасный MTU без автозамера (k3/SPEC-awg-engine.md §6.1, решение владельца 2026-10-07).

Внешний пакет туннеля = MTU + 80 (IPv6 40 + UDP 8 + WireGuard 32; считаем по IPv6 — с запасом) + S4 линии
(``s4_of``: 2.0 — свой S4, 3.1 — 12, 1.x и WireGuard — 0). Подробности и опыты — k3/stand/logs/design-awg-mtu.md.

    карта      MTU внешней сетевой карты: интерфейс маршрута по умолчанию (/proc/net/route), /sys/class/net/<if>/mtu;
               не прочитать — 1500
    бюджет     min(1400, карта) — внешний пакет, который проходит мобильные сети и PPPoE
    новые 2.0  S4 ≤ бюджет − 80 − 1280 (awgkeys.s4_max: одно правило на всё)
    сервер     MTU интерфейса входа = бюджет − 80 − S4, вниз до кратного 16, не выше MTU клиента; приоритет — TCP:
               если иначе пакет не укладывается в бюджет — и ниже 1232 (санитарный минимум 1024). Меняется сразу
               (gw apply), без перевыдачи: клиентский MTU в выданных настройках прежний (1280). Кратно 16 обязательно:
               клиент дополняет пакет до 16 своим MTU (при сервере 1220 закачка падала до 1)
    ниже 1232  (``low``) QUIC от сервера к клиенту через вход не пройдёт (браузеры перейдут на TCP): предупреждение и,
               у 2.0, выбор «новые настройки с меньшим S4» (``offer`` "s4": новые S3/S4 по бюджету, перезапуск входа,
               старые настройки не подключатся — до подтверждения перечень устройств, после — пометка «ждут новых
               настроек», снимается выдачей) или «оставить как есть» (запоминается для этого входа и этого S4)
    ноги       kawgN: min(1280, кратное 16 от min(1500, карта) − 80 − S4 ключа выхода), не ниже 1232
    узкая      карта < 80 + S4 + MTU клиента: клиентам с их MTU не уложиться — ничего не меняем молча, а
               предупреждаем (gw status, меню 17, панель, бот) и даём выбор (``offer`` "mtu"): «выдать новые настройки
               с меньшим MTU» (``reissue_mtu``, перечень устройств) или «оставить как есть» (запоминается для этой карты)
    MSS        зажим по итоговому MTU каждого интерфейса — входа и ноги — в обе стороны (``mss_rules``)

Состояние — ``gateway.mtu``: {"nic": {"dev", "mtu"}, "budget", "inbounds": {kind: {server, client, s4, outer,
narrow, low, offer}}, "legs": {iface: {mtu, s4}}, "keep": {kind: карта, при которой решили «оставить как есть»},
"keep_s4": {kind: S4, при котором решили «оставить как есть»}}. Пометки «ждут новых настроек» — /etc/kaskad/gw/
reissue.json, с 3.3.7.15 у всех протоколов: владелец файла — ``gw/reissue.py``, здесь — тонкие обёртки для вида MTU
(``mark_waiting``, ``issued``, ``waiting``). Его пишет ``refresh`` (каждый _apply: gw apply, установка входа, любое
изменение) и ``sync_legs``; всё остальное здесь — чистые функции.
"""

import os
import re

from .. import paths, util
from ..errors import USAGE, KaskadError
from ..i18n import saved_lang, tr
from ..vendor import awgkeys

OVERHEAD = awgkeys.OUTER_OVERHEAD          # 80
BUDGET_MAX = awgkeys.OUTER_BUDGET          # 1400
CLIENT_MTU = int(awgkeys.DEFAULT_MTU)      # 1280
MTU_MIN = 1232                             # меньше QUIC не проходит (1200 байт UDP + 32): пол ног; вход — ниже можно
SERVER_FLOOR = 1024                        # санитарный минимум серверного MTU входа
S4_KINDS = ("awg2",)                       # входы, чей S4 наш и меняется новыми параметрами (линия 2.0)
CLIENT_FLOOR = 1200                        # ниже клиентам не выдаём и при узкой карте
LEG_MTU = 1280                             # ноги к выходам: было 1200 (QUIC не проходил)
LEG_PATH_MAX = 1500                        # путь до своих выходов чистый
NIC_DEFAULT = 1500
KINDS = ("wg", "awg1", "awg2", "awg3")     # входы семейства WireGuard: у них MTU интерфейса и зажим MSS
LEG_RE = re.compile(r"^kawg\d+$")


def floor16(n):
    return int(n) // 16 * 16


def s4_of(params):
    """Добавка S4 к пакету данных: число S4 параметров (2.0 — свой, 3.1 — 12), иначе 0 (1.x, WireGuard)."""
    v = str((params or {}).get("S4") or "").strip()
    return int(v) if v.isdigit() else 0


def budget_of(nic_mtu):
    return min(BUDGET_MAX, int(nic_mtu))


def server_mtu(budget, s4, client):
    """MTU интерфейса входа на сервере: пакет в бюджете, кратно 16, не выше MTU клиента; ниже 1232 — если иначе не
    уложиться (TCP важнее QUIC), но не ниже 1024."""
    return min(int(client), max(SERVER_FLOOR, floor16(int(budget) - OVERHEAD - int(s4))))


def s4_fit(budget):
    """Наибольший S4, при котором сервер остаётся на 1232 и выше."""
    return int(budget) - OVERHEAD - MTU_MIN


def outer(mtu, s4):
    return int(mtu) + OVERHEAD + int(s4)


def narrow(nic_mtu, s4, client):
    """Карта не пропустит пакеты клиентов с их MTU (клиент → сервер полного размера)."""
    return int(nic_mtu) < outer(client, s4)


def reissue_mtu(budget, nic_mtu, s4):
    """Клиентский MTU для новых настроек при узкой карте: в бюджет; не выйдет выше 1232 — хотя бы в карту и 1232;
    1200…1280."""
    m = floor16(int(budget) - OVERHEAD - int(s4))
    if m < MTU_MIN:
        m = min(MTU_MIN, floor16(int(nic_mtu) - OVERHEAD - int(s4)))
    return max(CLIENT_FLOOR, min(CLIENT_MTU, m))


def leg_mtu(nic_mtu, s4):
    """MTU ноги kawgN к выходу с добавкой ``s4`` (тот же расчёт — egress_parse.py ``leg_mtu``)."""
    return min(LEG_MTU, max(MTU_MIN, floor16(min(LEG_PATH_MAX, int(nic_mtu)) - OVERHEAD - int(s4))))


# ── карта сервера ──────────────────────────────────────────────────────────

def _default_dev():
    """Интерфейс маршрута по умолчанию IPv4 с наименьшей метрикой (/proc/net/route; install.sh egress_nic_mtu читает
    так же), None — нет."""
    best = None
    for line in (util.read_text(paths.P("/proc/net/route")) or "").splitlines()[1:]:
        f = line.split()
        if len(f) < 8 or f[1] != "00000000" or f[7] != "00000000":
            continue
        try:
            metric = int(f[6])
        except ValueError:
            continue
        if best is None or metric < best[0]:
            best = (metric, f[0])
    return best[1] if best else None


def read_nic():
    """{"dev", "mtu", "read"}: карта маршрута по умолчанию; не прочитать — 1500 и read False."""
    dev = _default_dev()
    if dev and re.match(r"^[A-Za-z0-9_.:@-]{1,15}$", dev):
        v = (util.read_text(paths.P("/sys/class/net/%s/mtu" % dev)) or "").strip()
        if v.isdigit() and 576 <= int(v) <= 65536:
            return {"dev": dev, "mtu": int(v), "read": True}
    return {"dev": dev, "mtu": NIC_DEFAULT, "read": False}


# ── по входам ──────────────────────────────────────────────────────────────

# The state is read here directly (not through gw/model.py): the inbound adapters call this module, and their tests
# run them against a stand-in of the core.

def _g(st, create=False):
    g = (st or {}).get("gateway")
    if not isinstance(g, dict):
        if not create:
            return {}
        g = st["gateway"] = {}
    return g


def _inbound(st, kind):
    ib = _g(st).get("inbounds")
    rec = ib.get(kind) if isinstance(ib, dict) else None
    return rec if isinstance(rec, dict) else None


def installed(st):
    """Входы семейства WireGuard, что стоят."""
    return [k for k in KINDS if _inbound(st, k) is not None]


def _rec(st):
    m = _g(st).get("mtu")
    return m if isinstance(m, dict) else {}


def nic_of(st):
    """Карта, записанная последним ``refresh`` (нет — 1500)."""
    n = _rec(st).get("nic")
    v = n.get("mtu") if isinstance(n, dict) else None
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and v > 0 else NIC_DEFAULT


def budget(st):
    return budget_of(nic_of(st))


def client_mtu(st, kind):
    """MTU в выданных настройках: AmneziaWG — params.MTU, WireGuard — ``mtu`` записи (по умолчанию 1280)."""
    rec = _inbound(st, kind) or {}
    v = rec.get("mtu") if kind == "wg" else (rec.get("params") or {}).get("MTU")
    v = str(v if v is not None else "").strip()
    return int(v) if v.isdigit() and 576 <= int(v) <= 9000 else CLIENT_MTU


def inbound_s4(st, kind):
    if kind == "wg":
        return 0
    return s4_of((_inbound(st, kind) or {}).get("params"))


def kept(st, kind):
    """Человек решил «оставить как есть» при этой же карте."""
    k = _rec(st).get("keep")
    return isinstance(k, dict) and k.get(kind) == nic_of(st)


def kept_s4(st, kind):
    """Человек решил «оставить как есть» при этом же S4 (сервер ниже 1232)."""
    k = _rec(st).get("keep_s4")
    return isinstance(k, dict) and k.get(kind) == inbound_s4(st, kind)


def inbound_plan(st, kind):
    nic, s4, client = nic_of(st), inbound_s4(st, kind), client_mtu(st, kind)
    b = budget_of(nic)
    srv = server_mtu(b, s4, client)
    out = {"server": srv, "client": client, "s4": s4, "outer": outer(srv, s4), "narrow": narrow(nic, s4, client),
           "low": srv < MTU_MIN, "offer": None}
    if out["narrow"]:
        out["kept"] = kept(st, kind)
        out["new_client"] = reissue_mtu(b, nic, s4)
    # новый S4 лечит, только если с ним сервер встанет на 1232 и выше (бюджет ≥ 1327)
    if out["low"] and kind in S4_KINDS and s4 > s4_fit(b) >= awgkeys.S4_GEN_MIN:
        out["new_s4_max"] = max(awgkeys.S4_GEN_MIN, awgkeys.s4_max(b, client))
        out["kept_s4"] = kept_s4(st, kind)
        if not out["kept_s4"]:
            out["offer"] = "s4"
    if out["offer"] is None and out["narrow"] and not out["kept"]:
        out["offer"] = "mtu"
    return out


MANUAL_MIN = 1280                          # F2: MTU вручную — не ниже (меньше — только «новые настройки с меньшим MTU»)


def manual_limit(st, kind):
    """F2 (3.3.7.15, В5): наибольший MTU клиентов ``kind``, при котором внешний пакет в бюджете: min(1400, карта) − 80 −
    S4 (``budget``, awgkeys.OUTER_BUDGET/OUTER_OVERHEAD)."""
    return budget(st) - OVERHEAD - inbound_s4(st, kind)


def manual_view(st, kind):
    """Поле MTU окна «Настройки протокола»: {"cur", "min", "limit", "open": можно ли менять, "why": почему нельзя,
    "server": серверный MTU сейчас, "nic": MTU сетевой карты (конец шкалы полоски пакета, №13 r2), "budget": пакет
    снаружи не больше (min(1400, карта)), "overhead": заголовки (80), "s4": S4 протокола (WireGuard — 0)}. Значение
    ниже 1280 (поставленное «новыми настройками с меньшим MTU») показывается как есть."""
    lim = manual_limit(st, kind)
    out = {"cur": client_mtu(st, kind), "min": MANUAL_MIN, "limit": lim, "open": lim >= MANUAL_MIN, "why": "",
           "server": inbound_plan(st, kind)["server"], "nic": nic_of(st), "budget": budget(st), "overhead": OVERHEAD,
           "s4": inbound_s4(st, kind)}
    if not out["open"]:
        out["why"] = tr("gw_mtu.manual.closed", title=TITLES.get(kind, kind), nic=nic_of(st), s4=inbound_s4(st, kind),
                        min=MANUAL_MIN, budget=util.n_bytes(budget(st)), cur=out["cur"])
    return out


def manual_check(st, kind, n):
    """F2: MTU клиентов, поставленный человеком, — до записи. -> {"ok", "limit", "server", "outer", "warnings"};
    отказ (KaskadError USAGE): не WireGuard/AmneziaWG; не целое число (``bool`` — тоже нет); меньше 1280; больше
    предела — внешний пакет вышел бы за бюджет (В5: выше предела нельзя, подтверждения нет); предел меньше 1280 (узкая
    карта, S4 > 40 у старых параметров 2.0) — поле недоступно, причина словами."""
    title = TITLES.get(kind, kind)
    if kind not in KINDS:
        raise KaskadError(tr("gw_mtu.manual.kinds", title=title), code=USAGE)
    v = manual_view(st, kind)
    if not v["open"]:
        raise KaskadError(v["why"], code=USAGE)
    if isinstance(n, bool) or not isinstance(n, int) or n < MANUAL_MIN:
        raise KaskadError(tr("gw_mtu.manual.range", title=title, min=MANUAL_MIN, limit=v["limit"]), code=USAGE)
    b, s4 = budget(st), inbound_s4(st, kind)
    if n > v["limit"]:
        raise KaskadError(tr("gw_mtu.manual.over", title=title, limit=v["limit"], budget=util.n_bytes(b),
                             server=server_mtu(b, s4, v["limit"])), code=USAGE)
    srv = server_mtu(b, s4, n)
    return {"ok": True, "limit": v["limit"], "server": srv, "outer": outer(n, s4), "warnings": []}


def server_for(st, kind):
    """Серверный MTU входа сейчас (по записанной карте) — его берёт env юнита входа."""
    return inbound_plan(st, kind)["server"]


def s4_limit(st):
    """Наибольший S4 для новых параметров 2.0 на этом сервере."""
    return awgkeys.s4_max(budget(st))


def refresh(st, nic=None):
    """Перечитать карту и записать план входов в ``gateway.mtu`` (state меняется на месте). -> что изменилось
    (строки для журнала)."""
    nic = read_nic() if nic is None else nic
    g = _g(st, create=True)
    m = g.get("mtu") if isinstance(g.get("mtu"), dict) else {}
    old = dict(m.get("inbounds") or {})
    m["nic"] = {"dev": nic.get("dev"), "mtu": int(nic["mtu"])}
    m["budget"] = budget_of(nic["mtu"])
    g["mtu"] = m
    plans = dict((kind, inbound_plan(st, kind)) for kind in installed(st))
    m["inbounds"] = plans
    for name, now in (("keep", lambda k: m["nic"]["mtu"]), ("keep_s4", lambda k: plans[k]["s4"])):
        keep = m.get(name)                                   # решение для другой карты, другого S4 или удалённого
        if isinstance(keep, dict):                           # входа — забыть
            for k in list(keep):
                if k not in plans or keep[k] != now(k):
                    keep.pop(k)
            if not keep:
                m.pop(name, None)
    notes = []
    for kind, p in sorted(plans.items()):
        was = (old.get(kind) or {}).get("server") if isinstance(old.get(kind), dict) else None
        if was is not None and was != p["server"]:
            notes.append(tr("gw_mtu.log.server", saved_lang(), kind=kind, was=was, now=p["server"],   # the journal
                            nic=m["nic"]["mtu"], budget=m["budget"], s4=p["s4"]))
    return notes


# ── ноги к выходам ─────────────────────────────────────────────────────────

def leg_env_path(iface):
    return os.path.join(paths.egress_dir(), "awg", ".%s.env" % iface)


def _env_with_mtu(text, value):
    lines = [ln for ln in (text or "").splitlines() if not ln.startswith("MTU=")]
    return "\n".join(lines + ["MTU=%d" % value]) + "\n"


def sync_legs(st, runner, pool_exits):
    """Ноги AmneziaWG пула: MTU по карте (``leg_mtu``) — в их env (юнит берёт ${MTU}) и на живой интерфейс
    (``ip link set``, без перезапуска); итог — в ``gateway.mtu.legs``. -> что поменяли (для журнала)."""
    nic = nic_of(st)
    legs, notes = {}, []
    for e in pool_exits or []:
        iface = e.get("iface")
        if e.get("kind") != "awg" or not iface or not LEG_RE.match(str(iface)):
            continue
        s4 = int(e.get("s4") or 0)
        want = leg_mtu(nic, s4)
        legs[iface] = {"mtu": want, "s4": s4}
        env = leg_env_path(iface)
        text = util.read_text(env)
        if text is not None:
            new = _env_with_mtu(text, want)
            if new != text:
                util.atomic_write(env, new, 0o600)
        live = (util.read_text(paths.P("/sys/class/net/%s/mtu" % iface)) or "").strip()
        if live.isdigit() and int(live) != want:
            res = runner.run(["ip", "link", "set", "dev", iface, "mtu", str(want)])
            if res.ok:
                notes.append(tr("gw_mtu.log.leg", saved_lang(), iface=iface, was=live, now=want))   # the journal
    g = _g(st, create=True)
    if not isinstance(g.get("mtu"), dict):
        g["mtu"] = {}
    g["mtu"]["legs"] = legs
    return notes


def leg_mtus(st):
    """{iface: MTU} ног, записанных ``sync_legs``."""
    legs = _rec(st).get("legs")
    out = {}
    for k, v in (legs.items() if isinstance(legs, dict) else []):
        if LEG_RE.match(str(k)) and isinstance(v, dict) and isinstance(v.get("mtu"), int):
            out[k] = v["mtu"]
    return out


# ── MSS ────────────────────────────────────────────────────────────────────

def _clamp(cond, mss):
    return "%s tcp flags syn tcp option maxseg size > %d tcp option maxseg size set %d" % (cond, mss, mss)


def mss_rules(st, names):
    """Зажим MSS по MTU каждого интерфейса в обе стороны (MSS = MTU − 40): входы WireGuard/AmneziaWG — ``names``
    {kind: [имена интерфейсов]} (уже проверенные net.py), и ноги kawgN (записанные ``sync_legs``); ноги, которых план
    ещё не знает, — по нижней границе 1232."""
    out = []
    for kind in KINDS:
        if not names.get(kind):
            continue
        mss = server_for(st, kind) - 40
        for name in names[kind]:
            for d in ("iifname", "oifname"):
                out.append(_clamp('%s "%s"' % (d, name), mss))
    legs = leg_mtus(st)
    for iface in sorted(legs, key=lambda n: int(n[4:])):
        for d in ("iifname", "oifname"):
            out.append(_clamp('%s "%s"' % (d, iface), legs[iface] - 40))
    for d in ("iifname", "oifname"):
        cond = " ".join(['%s "kawg*"' % d] + ['%s != "%s"' % (d, n) for n in sorted(legs, key=lambda n: int(n[4:]))])
        out.append(_clamp(cond, MTU_MIN - 40))
    return out


# ── «ждут новых настроек» ──────────────────────────────────────────────────

def reissue_path():
    from . import reissue
    return reissue.path()


def mark_waiting(kind, ids):
    """Устройствам ``ids`` нужны новые настройки входа ``kind`` (сменились S3/S4 линии — ``ops.mtu_s4``): жёсткая
    пометка ``s4``, объединяется с прежними (3.3.7.14 заменяла список — пометка порта терялась, Д7)."""
    from . import reissue
    reissue.mark(kind, list(ids), ["s4"], True)


def issued(kind, dev_id):
    """Выдали настройки входа ``kind`` устройству — пометка снимается (любого протокола: 3.3.7.15 F1, ``ops.issue``)."""
    from . import reissue
    reissue.issued(kind, dev_id)


def _device_names(st, kind):
    users = dict((u.get("id"), u.get("name")) for u in _g(st).get("users") or [] if isinstance(u, dict))
    out = []
    for d in _g(st).get("devices") or []:
        if isinstance(d, dict) and kind in (d.get("access") or []):
            name = d.get("name") or d.get("id")
            out.append("%s (%s)" % (name, users[d.get("user")]) if users.get(d.get("user")) else name)
    return out


def waiting(st):
    """{kind: [{"id", "name"}]} — устройства входов WireGuard/AmneziaWG, что ждут новых настроек (жёсткие пометки:
    «не подключится»; мягкие — «желательно выдать заново» — только в ``reissue.view``), и всё ещё пользуются входом."""
    from . import reissue
    return dict((k, [{"id": r["id"], "name": r["name"]} for r in rows if r["hard"]])
                for k, rows in reissue.waiting(st, kinds=KINDS).items() if any(r["hard"] for r in rows))


# ── для людей ──────────────────────────────────────────────────────────────

TITLES = {"wg": "WireGuard", "awg1": "AmneziaWG 1.5", "awg2": "AmneziaWG 2.0", "awg3": "AmneziaWG 3.1"}


def low_text(kind, p, budget):
    return tr("gw_mtu.low", title=TITLES.get(kind, kind), s4=p["s4"], server=p["server"],
              s4_max=p.get("new_s4_max") or awgkeys.s4_max(budget))


def offer_text(kind, p, nic):
    if p.get("offer") == "s4" or (p.get("low") and p.get("new_s4_max") and not p["narrow"]):
        return low_text(kind, p, budget_of(nic))
    return narrow_text(kind, p, nic)


def narrow_text(kind, p, nic):
    return tr("gw_mtu.narrow", title=TITLES.get(kind, kind), nic=nic, client=p["client"], s4=p["s4"],
              need=util.n_bytes(outer(p["client"], p["s4"])), overhead=OVERHEAD, new_client=p["new_client"])


def offer_detail(kind, p, nic):
    """The same for a face that has its own headline («WireGuard: сетевая карта сервера уже, чем нужно клиентам.»,
    «…: сервер уменьшил MTU ниже 1232.» in the panel): no title and no repetition of the headline (T-N5 3.2.19-r2, Н-5)."""
    if p.get("offer") == "s4" or (p.get("low") and p.get("new_s4_max") and not p["narrow"]):
        return tr("gw_mtu.detail.low", s4=p["s4"], server=p["server"],
                  s4_max=p.get("new_s4_max") or awgkeys.s4_max(budget_of(nic)))
    return tr("gw_mtu.detail.narrow", nic=nic, client=p["client"], s4=p["s4"],
              need=util.n_bytes(outer(p["client"], p["s4"])), overhead=OVERHEAD, new_client=p["new_client"])


def view(st, live_nic=None):
    """Для gw status, меню, панели и бота: карта, бюджет, входы, ноги, узкие места (без побочных эффектов)."""
    nic = nic_of(st)
    n = _rec(st).get("nic")
    dev = n.get("dev") if isinstance(n, dict) else None
    rows = []
    wait = waiting(st)
    for kind in installed(st):
        p = inbound_plan(st, kind)
        p["kind"] = kind
        p["title"] = TITLES.get(kind, kind)
        if p["narrow"] or p["low"]:
            p["text"] = offer_text(kind, p, nic)
            p["detail"] = offer_detail(kind, p, nic)
        p["waiting"] = [d["name"] for d in wait.get(kind) or []]
        if p["offer"]:                                       # F-2: who would need new settings — before the choice
            p["devices"] = _device_names(st, kind)
        rows.append(p)
    out = {"nic": nic, "dev": dev, "budget": budget_of(nic), "s4_max": awgkeys.s4_max(budget_of(nic)),
           "inbounds": rows, "legs": [{"iface": k, "mtu": v} for k, v in sorted(leg_mtus(st).items(),
                                                                                key=lambda kv: int(kv[0][4:]))],
           "ask": [r["kind"] for r in rows if r["offer"]]}
    if live_nic is not None and live_nic.get("read") and int(live_nic["mtu"]) != nic:
        out["nic_now"] = int(live_nic["mtu"])
    from .inbounds import tunnel                             # lazy: tunnel → model → … (no cycle at import time)
    last = tunnel.last_restart(KINDS)
    if last:
        out["restart"] = dict(last, text=tunnel.restart_text(last))
    return out


def status_lines(v, with_text=True):
    """«MTU: карта 1500 (eth0), бюджет 1400» и строки входов и ног для ``gw status`` (там текст об узкой карте уже
    стоит у входа — ``with_text`` False)."""
    lines = [tr("gw_mtu.st.head", nic=v["nic"], dev=" (%s)" % v["dev"] if v.get("dev") else "", budget=v["budget"])]
    if v.get("nic_now"):
        lines.append("  ! " + tr("gw_mtu.st.nic_now", nic=v["nic_now"]))
    for r in v["inbounds"]:
        lines.append("  " + tr("gw_mtu.st.inbound", kind=r["kind"], server=r["server"], client=r["client"]))
        if r["offer"]:
            if with_text:
                lines.append("    ! " + r["text"])
            cmd = "s4" if r["offer"] == "s4" else "reissue"
            lines.append("      " + tr("gw_mtu.st.choice", cmd=cmd, kind=r["kind"]))
        if r.get("kept_s4"):
            lines.append("    " + tr("gw_mtu.st.kept_s4", s4=r["s4"], server=r["server"]))
        elif r["low"] and not r["offer"] and not r["narrow"]:
            lines.append("    " + tr("gw_mtu.st.low"))
        if r["narrow"] and r.get("kept"):
            lines.append("    " + tr("gw_mtu.st.kept_narrow", client=r["client"]))
        if r.get("waiting"):
            lines.append("    " + tr("gw_mtu.st.waiting", names=", ".join(r["waiting"])))
    for leg in v["legs"]:
        lines.append("  " + tr("gw_mtu.st.leg", iface=leg["iface"], mtu=leg["mtu"]))
    if v.get("restart"):                                     # T-N5 3.2.19: перезапуск входа стоит клиентам 15–25 с
        many = len(v["restart"].get("kinds") or []) > 1
        lines.append("  " + tr("gw_mtu.st.restart_many" if many else "gw_mtu.st.restart_one", text=v["restart"]["text"]))
    return lines
