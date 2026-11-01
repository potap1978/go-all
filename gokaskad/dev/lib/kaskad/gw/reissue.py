"""«Ждут новых настроек» для всех протоколов (3.3.7.15 F1, PLAN-n13 Ш1) — единственный владелец reissue.json.

Устройство помечается, когда сервер сменил то, что записано в выданных ему настройках: порт (``port``), MTU
(``mtu``), параметры S3/S4 линии AmneziaWG 2.0 (``s4``), сеть протокола (``net``, при возврате F5). Жёсткая пометка —
«не подключится — выдайте заново» (порт, S4, сеть), мягкая — «желательно выдать заново» (только MTU: со старым MTU
устройство подключается). Карточка Обзора и строка бота — только по жёстким.

Файл ``/etc/kaskad/gw/reissue.json`` (0600) — прежний, формат расширен::

    {"wg": ["d-…"], "sstp": ["d-…"], …,                          ← жёсткие пометки, по видам (как в 3.3.7.14)
     "why": {kind: {id: {"what": ["port", "mtu"], "hard": true, "at": {"port": ts, "mtu": ts},
                         "inst": ["udp"],                         ← OpenVPN: экземпляры, чей порт сменили
                         "by": "a-3f2c…",                         ← id применения F3 («Вернуть прежние»)
                         "was": {…}}}}}                           ← пометка до этого применения (без своего «was»)

Чтение мягкое: незнакомое отбрасывается. Прежний файл (только WG-виды, без ``why``) — жёсткие ``mtu``, так его
показывала 3.3.7.14. Откат на 3.3.7.14/13: ``mtu._marks`` берёт только WG-виды и их списки — ``why`` и другие виды
не видит, при своей записи отбрасывает; списки несут только жёсткие пометки, так что старая версия не показывает
мягкие как «ждут». Теряются только подсказки, ошибок нет.

Снимают пометку: выдача (``issued`` — ``ops.issue`` с ``mark=True``: «Выдать», файл со страницы /p/, «Скачать мои
новые настройки» F7 перед применением тех же изменений — ``ops._preview_given``, если после F7 устройству не
выдавали текущие — ``ops._preview_forget``, П8; подписка и «Показать» на /p/ —
``mark=False``, не снимают), «Вернуть прежние» (``drop_by``) и — только ``port`` — первое подключение на новом порту
(``seen``, круг сборщика): AmneziaWG — рукопожатие пира позже смены (порт AWG меняется перезапуском службы, старая
сессия рвётся — стенд T-3315 2.3); VLESS, Hysteria2, OpenVPN (только экземпляр, чей порт сменили), OpenConnect, SSTP
— сессия в круге позже смены + 30 с (смена порта у них — перезапуск службы). У WireGuard и SOCKS5 ``port`` снимает
только выдача (``PORT_BY_ISSUE_KINDS``): порт WG меняется ``syncconf`` без перезапуска, живая сессия сама переезжает на
новый порт роумингом (сервер шлёт с нового порта, клиент принимает — П4), рукопожатие позже смены есть, а в
сохранённых настройках устройства — прежний порт; у SOCKS5 сессий по устройствам нет. ``mtu``, ``s4``, ``net`` сессия
не снимает: со старым MTU устройство подключается, рукопожатие не доказывает новый Address.

Удалённые устройства и устройства без доступа к виду отбрасываются при показе и при каждой записи.
"""

import contextlib
import errno
import fcntl
import ipaddress
import json
import os
import time

from .. import paths, util

WHATS = ("port", "mtu", "s4", "net")
HARD_WHAT = ("port", "s4", "net")            # без новых настроек не подключится
HANDSHAKE_KINDS = ("wg", "awg1", "awg2", "awg3")
NO_SESSION_KINDS = ("socks5",)               # сессий по устройствам нет: снимает только выдача
PORT_BY_ISSUE_KINDS = ("wg",) + NO_SESSION_KINDS  # «сменился порт» снимает только выдача (WG — роуминг, П4/П5 З-1)
GROWTH_KINDS = ("vless", "hy2")              # сессия xray — по росту счётчиков: доказывает только рост (``last``)
PORT_GRACE = 30                              # с: сессия круга позже смены порта + столько — уже на новом порту
OVPN_POOLS = {"udp": "ovpn_u", "tcp": "ovpn_t"}
LOCK_WAIT = 5.0
WHY = "why"

_SEEN = {"sig": None, "port": True}          # (mtime_ns, size) файла и «есть пометка port, которую снимает сессия»


def path():
    return os.path.join(paths.gw_dir(), "reissue.json")


def _lock_path():
    return os.path.join(paths.gw_run_dir(), "reissue.lock")


@contextlib.contextmanager
def _locked(wait=LOCK_WAIT):
    """flock чтения-записи файла: его пишут выдача (без блокировки state), применения и сборщик. Не дождались —
    OSError(EAGAIN): сборщик пропускает круг, остальные получают ошибку (выдача её только пишет в журнал)."""
    d = paths.gw_run_dir()
    util.ensure_dir(d, 0o700)
    fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.time() + wait
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES) or time.time() >= deadline:
                    raise
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _now():
    return int(time.time())


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _clean_mark(m, nested=True):
    """Пометка из файла -> {"what", "hard", "at", ["inst"], ["by"], ["was"]} или None (мусор)."""
    if not isinstance(m, dict):
        return None
    what = [w for w in WHATS if w in (m.get("what") if isinstance(m.get("what"), list) else [])]
    if not what:
        return None
    at = m.get("at") if isinstance(m.get("at"), dict) else {}
    out = {"what": what, "hard": m.get("hard") is True,
           "at": dict((w, _int(at.get(w))) for w in what if _int(at.get(w)) is not None)}
    inst = [i for i in OVPN_POOLS if i in (m.get("inst") if isinstance(m.get("inst"), list) else [])]
    if inst:
        out["inst"] = inst
    if isinstance(m.get("by"), str) and m["by"]:
        out["by"] = m["by"]
    if nested and m.get("was") is not None:
        was = _clean_mark(m.get("was"), nested=False)
        if was is not None:
            out["was"] = was
    return out


def load():
    """{kind: {id: mark}} из файла. Прежний формат (списки без ``why``) — жёсткие ``mtu``."""
    try:
        doc = json.loads(util.read_text(path()) or "{}")
    except ValueError:
        return {}
    if not isinstance(doc, dict):
        return {}
    why = doc.get(WHY) if isinstance(doc.get(WHY), dict) else {}
    out = {}
    for kind, ids in doc.items():
        if kind == WHY or not isinstance(ids, list):
            continue
        for i in ids:
            if isinstance(i, str) and i:
                out.setdefault(kind, {})[i] = {"what": ["mtu"], "hard": True, "at": {}}
    for kind, recs in why.items():
        if not isinstance(kind, str) or not isinstance(recs, dict):
            continue
        for i, m in recs.items():
            m = _clean_mark(m)
            if isinstance(i, str) and i and m is not None:
                out.setdefault(kind, {})[i] = m
    return out


def _access(st):
    """{device id: set of kinds it has access to} — None, если состояния нет (тогда не чистим)."""
    if st is None:
        try:
            from .. import state
            st = state.load()
        except Exception:                      # noqa: BLE001 — без состояния пометки не трогаем
            return None
    from . import model
    try:
        return dict((d.get("id"), set(d.get("access") or [])) for d in model.devices(st) if isinstance(d, dict))
    except Exception:                          # noqa: BLE001
        return None


def _prune(marks, st):
    acc = _access(st)
    if acc is None:
        return marks
    return dict((k, dict((i, m) for i, m in recs.items() if k in acc.get(i, ()))) for k, recs in marks.items())


def _save(marks, st=None):
    """Записать (только если что-то есть или файл уже есть): списки — жёсткие пометки, ``why`` — все."""
    marks = dict((k, v) for k, v in _prune(marks, st).items() if v)
    if not marks and util.read_text(path()) is None:
        return
    doc = {}
    why = {}
    for kind, recs in sorted(marks.items()):
        hard = sorted(i for i, m in recs.items() if m["hard"])
        if hard:
            doc[kind] = hard
        why[kind] = dict((i, m) for i, m in sorted(recs.items()))
    if why:
        doc[WHY] = why
    if not os.path.isdir(paths.gw_dir()):
        os.makedirs(paths.gw_dir(), 0o700)
    util.atomic_write(path(), json.dumps(doc, sort_keys=True) + "\n", 0o600)


def _hard_of(what):
    return any(w in HARD_WHAT for w in what)


def _merge(old, what, hard, by, now, inst):
    """Новая пометка поверх прежней: устройства и ``what`` — объединение, ``hard`` не понижается, ``at`` того, что
    сменили сейчас, — сейчас. С ``by`` прежняя пометка (без своего «was») запоминается для «Вернуть прежние»; без
    ``by`` пометка больше не «чистая» одного применения — ``by`` и ``was`` снимаются."""
    base = old or {"what": [], "hard": False, "at": {}}
    out = {"what": [w for w in WHATS if w in base["what"] or w in what],
           "hard": bool(base["hard"] or hard), "at": dict(base.get("at") or {})}
    for w in what:
        out["at"][w] = now
    ins = [i for i in OVPN_POOLS if i in (base.get("inst") or []) or i in (inst or [])]
    if ins:
        out["inst"] = ins
    if by:
        out["by"] = by
        if old is not None:
            out["was"] = dict((k, v) for k, v in old.items() if k != "was")
    return out


def mark(kind, ids, what, hard, by=None, now=None, inst=None, st=None):
    """Устройствам ``ids`` нужны новые настройки ``kind``: ``what`` — что сменилось, ``hard`` — без них не
    подключится, ``by`` — id применения F3, ``inst`` — экземпляры OpenVPN, чей порт сменили. -> отмеченные id."""
    what = [w for w in WHATS if w in (what or [])]
    ids = [i for i in ids or [] if isinstance(i, str) and i]
    if not what or not ids:
        return []
    now = _now() if now is None else int(now)
    with _locked():
        marks = load()
        recs = marks.setdefault(kind, {})
        for i in ids:
            recs[i] = _merge(recs.get(i), what, hard, by, now, inst)
        _save(marks, st)
    return ids


def issued(kind, dev_id, st=None):
    """Выдали настройки ``kind`` устройству — снимается всё у пары."""
    with _locked():
        marks = load()
        if dev_id not in marks.get(kind, {}):
            return False
        marks[kind].pop(dev_id, None)
        _save(marks, st)
    return True


def drop_by(kind, by, st=None):
    """«Вернуть прежние»: пометки применения ``by`` — назад к тому, что было до него (или сняты). -> их id."""
    if not by:
        return []
    with _locked():
        marks = load()
        got = []
        for i, m in list(marks.get(kind, {}).items()):
            if m.get("by") != by:
                continue
            got.append(i)
            if m.get("was"):
                marks[kind][i] = m["was"]
            else:
                marks[kind].pop(i)
        if got:
            _save(marks, st)
    return got


def marked_by(kind, by):
    """id устройств, чья пометка ``kind`` — всё ещё «чистая» пометка применения ``by``."""
    return sorted(i for i, m in load().get(kind, {}).items() if by and m.get("by") == by)


def _sig():
    try:
        s = os.stat(path())
    except OSError:
        return None
    return (s.st_mtime_ns, s.st_size)


def _in_pool(ip, pool, st):
    if st is None:
        return True
    try:
        from . import addr
        return ipaddress.ip_address(str(ip)) in ipaddress.ip_network(addr.subnet(pool, st))
    except Exception:                          # noqa: BLE001 — не знаем экземпляр: не снимаем
        return False


def fresh(kind, s, at, now):
    """Сессия ``s`` вида ``kind`` доказывает подключение позже ``at`` (смена порта, выдача — и пометка F1, и «порт
    закрыт?» Е4): WireGuard/AmneziaWG — рукопожатие (``since``) позже ``at``; VLESS, Hysteria2 — рост счётчиков
    (``last``) не раньше ``at + PORT_GRACE`` (сессия xray живёт ещё ACTIVE с после последнего роста, а её ``since`` —
    до смены: REV-3315-a1 С-1); SOCKS5 — никогда; остальные (служба перезапускается при смене порта) — круг не
    раньше ``at + PORT_GRACE``. Сессия без доказательства (только вид) — нет."""
    if not isinstance(s, dict) or not isinstance(at, int) or kind in NO_SESSION_KINDS:
        return False
    if kind in HANDSHAKE_KINDS:
        since = _int(s.get("since"))
        return since is not None and since > at
    if now < at + PORT_GRACE:
        return False
    if kind in GROWTH_KINDS:
        last = _int(s.get("last"))
        return last is not None and last >= at + PORT_GRACE
    return s.get("device") is not None or s.get("ip") is not None


def _session_proves(kind, s, m, now, st):
    """Сессия ``s`` доказывает, что у устройства новые настройки с новым портом. WireGuard — нет: рукопожатие позже
    смены даёт и живая сессия со старыми настройками, переехавшая роумингом (``fresh`` для «порт закрыт?» это верно —
    порт открыт; для пометки — нет); SOCKS5 — сессий нет. AmneziaWG — да: смена порта перезапускает службу."""
    if kind in PORT_BY_ISSUE_KINDS:
        return False
    at = (m.get("at") or {}).get("port")
    if at is None or not fresh(kind, s, at, now):
        return False
    if kind == "openvpn" and m.get("inst"):
        return any(_in_pool(s.get("ip"), OVPN_POOLS[i], st) for i in m["inst"])
    return True


def seen(sessions, now=None, st=None):
    """Круг сборщика: снять ``port`` у устройств, подключившихся на новом порту (кроме ``PORT_BY_ISSUE_KINDS``).
    Файл читается, только если он сменился или в нём есть пометка ``port``, которую снимает сессия; пишется — только
    при изменении; занят — круг пропускает. -> снятые
    [(kind, id)]."""
    sig = _sig()
    if sig is None or (sig == _SEEN["sig"] and not _SEEN["port"]):
        return []
    now = _now() if now is None else int(now)
    try:
        with _locked(wait=0):
            marks = load()
            got = []
            for s in sessions or []:
                kind, dev = s.get("kind"), s.get("device")
                m = marks.get(kind, {}).get(dev) if isinstance(dev, str) else None
                if m is None or "port" not in m["what"] or not _session_proves(kind, s, m, now, st):
                    continue
                rest = [w for w in m["what"] if w != "port"]
                got.append((kind, dev))
                if not rest:
                    marks[kind].pop(dev)
                    continue
                # подключился на новом порту — у него новые настройки: пометка больше не «чистая» одного применения
                marks[kind][dev] = {"what": rest, "hard": _hard_of(rest),
                                    "at": dict((w, t) for w, t in (m.get("at") or {}).items() if w in rest)}
            if got:
                _save(marks, st)
            _SEEN["sig"] = _sig()
            _SEEN["port"] = any("port" in m["what"] for k, recs in marks.items() if k not in PORT_BY_ISSUE_KINDS
                                for m in recs.values())
    except OSError as e:
        if e.errno in (errno.EAGAIN, errno.EACCES):
            return []
        raise
    return got


def _names(st):
    from . import model
    users = dict((u.get("id"), u.get("name")) for u in model.users(st))
    out = {}
    for d in model.devices(st):
        name = d.get("name") or d.get("id")
        out[d.get("id")] = "%s (%s)" % (name, users[d.get("user")]) if users.get(d.get("user")) else name
    return out


def waiting(st, kinds=None):
    """{kind: [{"id", "name", "what", "hard"}]} — в порядке устройств; удалённые и без доступа к виду — нет."""
    from . import model
    marks = load()
    names = _names(st)
    out = {}
    for d in model.devices(st):
        for kind in sorted(marks):
            if kinds is not None and kind not in kinds:
                continue
            m = marks[kind].get(d.get("id"))
            if m is None or kind not in (d.get("access") or []):
                continue
            out.setdefault(kind, []).append({"id": d.get("id"), "name": names.get(d.get("id")), "what": m["what"],
                                             "hard": m["hard"]})
    return out


def view(st):
    """Для панели, бота и меню: {kind: {"title", "hard": [{"id", "name", "what"}], "soft": [...]}} — без голого
    ``kind`` в словах (Д6): название протокола — ``registry.title``."""
    from . import registry
    out = {}
    for kind, rows in waiting(st).items():
        v = out[kind] = {"title": registry.title(kind), "hard": [], "soft": []}
        for r in rows:
            v["hard" if r["hard"] else "soft"].append({"id": r["id"], "name": r["name"], "what": r["what"]})
    return out
