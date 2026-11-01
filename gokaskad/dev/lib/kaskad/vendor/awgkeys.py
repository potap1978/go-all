# vendored from goAmnezia pro: lib/awgkeys.py @ bbcbad7, unchanged below this line
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""goAmnezia pro — ядро форматов и параметров AmneziaWG (CONTRACT §4.2).

Только стандартная библиотека, Python ≥ 3.8. Вызывается тонкими bash-обёртками
(lib/awg_params.sh, lib/export.sh), ботом и панелью. Всё, что касается линеек
AmneziaWG (1.0 / 1.5 / 2.0 / 3.1), ключей X25519 и форматов ключей клиента
(.conf, vpn://, чанки QR приложения Amnezia), живёт здесь, чтобы у bash, бота и
панели был один источник правды.

Подкоманды (вход — stdin/аргументы, результат — stdout, ошибки — stderr, код 2):
  gen-params --line L [--preset P]   → JSON params (все ключи из CONTRACT §3.1)
  validate --line L                  ← JSON params на stdin; exit 0 / 2
  genkey | pubkey <priv|-> | genpsk  → base64
  conf --line L                      ← JSON {params, client, server, dns, mtu}; → текст .conf клиента
  mihomo --line L [--name N]         ← тот же JSON, что у conf; → профиль mihomo (Clash Meta) с этой линией
  server-conf --line L               ← JSON {params, server, peers}; → текст awg0.conf сервера
  vpn-encode                         ← JSON {container, line, params, conf_text, …}; → vpn://…
  vpn-decode <vpn://…|->             → JSON («-» — ссылка на stdin: в argv ключ виден всем)
  qr-chunks <vpn://…|->              → JSON-массив строк для QR приложения (то же про «-»)
  client-key                         ← ссылка vpn:// или .conf клиента на stdin; → JSON с ключами
  detect-line                        ← JSON params или текст .conf; → 1.0 | 1.5 | 2.0 | 3.1

Факты о приложении Amnezia 5.0.2 сверены с исходниками amnezia-client (ветка dev):
core/utils/constants/protocolConstants.h, core/installers/awgInstaller.cpp,
core/models/protocols/awgProtocolConfig.cpp, core/controllers/selfhosted/{export,import}Controller.cpp,
core/utils/qrCodeUtils.cpp, server_scripts/awg/{configure_container.sh,template.conf}.
"""

import argparse
import base64
import binascii
import json
import math
import re
import secrets
import struct
import sys
import zlib

__all__ = [
    "LINES", "PRESETS", "PARAM_KEYS", "AWG_IFACE_KEYS", "AWG3_KEYS",
    "DEFAULT_I1", "QUIC_I1",
    "x25519", "genkey", "pubkey", "genpsk", "is_key",
    "normalize_params", "detect_line", "validate_params", "gen_params",
    "parse_conf", "params_from_conf", "render_conf", "render_server_conf",
    "conf_client_data", "mihomo_proxy", "mihomo_profile", "MIHOMO_GROUP", "MIHOMO_AUTO",
    "qcompress", "quncompress", "build_vpn_json", "vpn_encode", "vpn_decode",
    "qr_chunks", "qr_chunks_from_bytes", "qr_chunks_join", "main",
]

# ---------------------------------------------------------------------------
# Константы линеек и параметров
# ---------------------------------------------------------------------------

LINES = ("1.0", "1.5", "2.0", "3.1")
PRESETS = ("default", "quic", "router")

# Как приложение подписывает версию протокола внутри контейнера (protocol_version):
# 1.0 у него — просто отсутствие поля (awgVersionOf возвращает пустую строку).
LINE_TO_PROTOCOL_VERSION = {"1.0": "", "1.5": "1.5", "2.0": "2", "3.1": "3.1"}

J_KEYS = ("Jc", "Jmin", "Jmax")
S_KEYS = ("S1", "S2", "S3", "S4")
H_KEYS = ("H1", "H2", "H3", "H4")
I_KEYS = ("I1", "I2", "I3", "I4", "I5")
# Ключи [Interface], которые понимают клиенты AmneziaWG 1.x/2.0 (порядок — как в template.conf приложения).
AWG_IFACE_KEYS = J_KEYS + S_KEYS + H_KEYS + I_KEYS
# Поля 3.x: первые семь — «значения», последние два — переключатели on/off.
AWG3_VALUE_KEYS = ("HeaderProtectionKey", "ContentPaddingAddition", "RekeyAfterTime", "RekeyTimeout",
                   "RejectAfterTime", "KeepaliveTimeout", "MaxHandshakeAttempts")
AWG3_TOGGLE_KEYS = ("RandomTrailers", "DisableCookies")
AWG3_KEYS = AWG3_VALUE_KEYS + AWG3_TOGGLE_KEYS
# Полный набор params из config.json (CONTRACT §3.1): всё строками, «не задано» = "".
PARAM_KEYS = AWG_IFACE_KEYS + AWG3_KEYS + ("PersistentKeepalive", "MTU")

# Что приложение считает включённым переключателем: непустое и не "off" (awgProtocolConfig.cpp).
TOGGLE_VALUES = ("on", "off", "1", "0", "true", "false", "yes", "no", "t", "f")

# Размеры сообщений WireGuard без junk-префикса (awgProtocolConfig.h, AwgConstant).
# Итоговые размеры 148+S1, 92+S2, 64+S3, 32+S4 обязаны быть попарно различны — иначе
# приёмник не отличит тип пакета. Частный случай для 1.x: S1+56 ≠ S2.
MESSAGE_BASE_SIZE = {"S1": 148, "S2": 92, "S3": 64, "S4": 32}
# Ядерный модуль 1.0 требует, чтобы initiation/response влезали в MTU 1280: S1 ≤ 1132, S2 ≤ 1188.
# Для S3/S4 применяем ту же логику (64+S3 ≤ 1280, 32+S4 ≤ 1280), а S3 дополнительно
# ограничен 64 — это предел из UI приложения (cookieReplyPacketJunkSizeMax).
S_MAX = {"S1": 1132, "S2": 1188, "S3": 64, "S4": 1248}
# При включённой защите заголовков (3.x) каждый S должен быть ≥ 12 (junkPacketSizeMin приложения).
S_MIN_WITH_HEADER_PROTECTION = 12
JC_MAX = 128
JMAX_MAX = 1280
H_MAX = 0xFFFFFFFF
# Генерация: H — из [5, 2^31), как у старых версий приложения (1..4 заняты WireGuard,
# верх ограничен знаковым int32 реализаций).
H_GEN_MIN = 5
H_GEN_MAX = 2 ** 31 - 1
H_RANGE_WIDTH = (1000, 5000)
# Бюджет внешнего пакета (k3/SPEC-awg-engine.md §6.1, решение владельца 2026-10-07): пакет данных снаружи =
# MTU туннеля + IPv6 40 + UDP 8 + заголовок и тег WireGuard 32 (= 80; по IPv4 было бы 60 — считаем по IPv6) + S4
# (2.0: свой S4; 3.1: S4 = 12, хвосты не выходят за MTU собеседника; 1.x и WireGuard: 0). Бюджет по умолчанию —
# 1400: проходит мобильные сети и PPPoE; карта сервера уже 1400 — бюджет = MTU карты. Отсюда ЕДИНСТВЕННОЕ правило S4
# новых параметров 2.0: S4 ≤ бюджет − 80 − MTU клиента (при 1400 и 1280 — не больше 40). Лаборатория 07.10.2026:
# путь хоть на байт меньше внешнего пакета — скорость с ≈2300 Мбит/с падает до 0–3.
OUTER_OVERHEAD = 40 + 8 + 32
OUTER_BUDGET = 1400
S4_GEN_MIN = 15
S4_GEN_MAX = 150


def s4_max(budget=OUTER_BUDGET, mtu=None):
    """Наибольший S4, при котором пакет данных с MTU туннеля ``mtu`` (по умолчанию 1280) укладывается в бюджет
    ``budget`` (может быть меньше 15: тогда генерация берёт 15, а внешний пакет больше бюджета)."""
    return int(budget) - OUTER_OVERHEAD - int(DEFAULT_MTU if mtu is None else mtu)


# Длина I-пакета (сумма <b>/<r>/<c>/<t>) не должна превышать MTU 1280 — иначе фрагментация.
IPACKET_MAX_BYTES = 1280

# I1 по умолчанию приложения Amnezia 5.0.2 (protocolConstants.h, defaultSpecialJunk1):
# два случайных байта ID транзакции + DNS-ответ на A-запрос icloud.com.
DEFAULT_I1 = ("<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001"
              "c00c000100010000105a00044d583737>")
# Пресет quic — наша сборка, похожая на QUIC v1 Initial клиента: первый байт 0xc3
# (long header, fixed bit, тип Initial), версия 0x00000001, DCID 8 случайных байт,
# пустые SCID и token, длина 0x449e (varint = 1182), затем 1182 байт «шифротекста».
# Итого ровно 1200 байт — минимальный размер клиентского Initial по RFC 9000.
QUIC_I1 = "<b 0xc30000000108><r 8><b 0x0000449e><r 1182>"

# Приложение узнаёт контейнер по имени; любое другое имя даёт DockerContainer::None и
# ключ не подключится. Поэтому в vpn:// уходит только одно из этих двух имён.
KNOWN_CONTAINERS = ("amnezia-awg2", "amnezia-awg")
DEFAULT_CONTAINER = "amnezia-awg2"
DEFAULT_MTU = "1280"
DEFAULT_KEEPALIVE = "25"
DEFAULT_ALLOWED_IPS = "0.0.0.0/0, ::/0"

# Формат QR приложения (qrCodeUtils.cpp): сжатые байты режутся по 850, каждый чанк —
# QDataStream: qint16 magic 1984, quint8 число чанков, quint8 индекс, QByteArray
# (quint32 длина + данные), затем base64url без «=».
QR_MAGIC = 1984
QR_CHUNK_SIZE = 850
QR_MAX_CHUNKS = 255  # quint8


class AwgError(ValueError):
    """Ошибка входных данных: сообщение уходит в stderr, код выхода 2."""


# ---------------------------------------------------------------------------
# Случайность — только secrets (ключи и параметры маскировки должны быть непредсказуемы)
# ---------------------------------------------------------------------------

def _rand_int(lo, hi):
    """Случайное целое из [lo, hi] включительно."""
    if hi < lo:
        raise AwgError("bad random range %d..%d" % (lo, hi))
    return lo + secrets.randbelow(hi - lo + 1)


# ---------------------------------------------------------------------------
# X25519 (RFC 7748) на чистом Python
# ---------------------------------------------------------------------------

_P = 2 ** 255 - 19
_A24 = 121665
_BASE_U = 9


def _clamp(k):
    """Clamping скаляра по RFC 7748 §5: как `wg genkey` и OpenSSL."""
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return bytes(b)


def _cswap(swap, a, b):
    """Условный обмен без ветвления — как в RFC; константное время тут не критично,
    но форма ladder'а сохранена один в один с эталоном."""
    mask = -swap & ((1 << 256) - 1)
    dummy = mask & (a ^ b)
    return a ^ dummy, b ^ dummy


def _scalarmult(k, u):
    x_1 = u
    x_2, z_2 = 1, 0
    x_3, z_3 = u, 1
    swap = 0
    for t in range(254, -1, -1):
        k_t = (k >> t) & 1
        swap ^= k_t
        x_2, x_3 = _cswap(swap, x_2, x_3)
        z_2, z_3 = _cswap(swap, z_2, z_3)
        swap = k_t
        a = (x_2 + z_2) % _P
        aa = a * a % _P
        b = (x_2 - z_2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x_3 + z_3) % _P
        d = (x_3 - z_3) % _P
        da = d * a % _P
        cb = c * b % _P
        x_3 = pow(da + cb, 2, _P)
        z_3 = x_1 * pow(da - cb, 2, _P) % _P
        x_2 = aa * bb % _P
        z_2 = e * (aa + _A24 * e) % _P
    x_2, x_3 = _cswap(swap, x_2, x_3)
    z_2, z_3 = _cswap(swap, z_2, z_3)
    return x_2 * pow(z_2, _P - 2, _P) % _P


def x25519(scalar, u_point):
    """X25519(k, u): оба аргумента — 32 байта; скаляр clamp'ится, у точки маскируется старший бит."""
    if len(scalar) != 32 or len(u_point) != 32:
        raise AwgError("x25519 expects 32-byte scalar and point")
    k = int.from_bytes(_clamp(scalar), "little")
    u = int.from_bytes(u_point, "little") & ((1 << 255) - 1)
    return _scalarmult(k, u).to_bytes(32, "little")


def x25519_base(scalar):
    return x25519(scalar, _BASE_U.to_bytes(32, "little"))


# ---------------------------------------------------------------------------
# Ключи в формате WireGuard (base64, 32 байта)
# ---------------------------------------------------------------------------

def key_bytes(b64):
    """base64 → 32 байта; иначе AwgError."""
    try:
        raw = base64.b64decode(b64.strip(), validate=True)
    except (binascii.Error, ValueError, AttributeError):
        raise AwgError("key must be base64")
    if len(raw) != 32:
        raise AwgError("key must decode to 32 bytes")
    return raw


def is_key(b64):
    try:
        key_bytes(b64)
    except AwgError:
        return False
    return True


def genkey():
    """Приватный ключ X25519: 32 случайных байта с clamping, как `wg genkey`."""
    return base64.b64encode(_clamp(secrets.token_bytes(32))).decode("ascii")


def pubkey(priv_b64):
    return base64.b64encode(x25519_base(key_bytes(priv_b64))).decode("ascii")


def genpsk():
    """PresharedKey — просто 32 случайных байта, без clamping (как `wg genpsk`)."""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


# ---------------------------------------------------------------------------
# Параметры: нормализация, определение линейки, валидация
# ---------------------------------------------------------------------------

def normalize_params(obj):
    """Полный словарь PARAM_KEYS со строковыми значениями («не задано» = "")."""
    if obj is None:
        obj = {}
    if not isinstance(obj, dict):
        raise AwgError("params must be a JSON object")
    out = {}
    for k in PARAM_KEYS:
        v = obj.get(k, "")
        if v is None:
            v = ""
        if isinstance(v, bool):
            v = "on" if v else ""
        out[k] = str(v).strip()
    return out


def _toggle_enabled(v):
    v = (v or "").strip()
    return bool(v) and v.lower() != "off"


def has_awg3_markers(p):
    """Так приложение решает, что конфиг — 3.x (hasAwg3Markers в awgProtocolConfig.cpp)."""
    return any(p[k] for k in AWG3_VALUE_KEYS) or any(_toggle_enabled(p[k]) for k in AWG3_TOGGLE_KEYS)


def detect_line(params):
    """Правила Amnezia: 3.x-поле → 3.1; S3/S4 или дефис в H → 2.0; любой I → 1.5; иначе 1.0."""
    p = normalize_params(params)
    if has_awg3_markers(p):
        return "3.1"
    if p["S3"] or p["S4"] or any("-" in p[h] for h in H_KEYS):
        return "2.0"
    if any(p[k] for k in I_KEYS):
        return "1.5"
    return "1.0"


_INT_RE = re.compile(r"^\d{1,10}$")
_RANGE_RE = re.compile(r"^(\d{1,10})(?:-(\d{1,10}))?$")
_TAG_RE = re.compile(r"<([a-z]{1,3})(?: ([^<>]{1,8192}))?>")
_HEX_BYTES_RE = re.compile(r"^0x(?:[0-9a-fA-F]{2})+$")


def _parse_range(v):
    """'a' или 'a-b' → (a, b) или None."""
    m = _RANGE_RE.match(v)
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) is not None else a
    return a, b


def _check_ipacket(v):
    """Синтаксис сигнатурного пакета amneziawg-go: последовательность тегов <b 0x..>, <r N>, <c>, <t>…
    Незнакомые теги (например, из будущих версий) пропускаем, чтобы не отвергать чужие
    рабочие конфиги; считаем только общий размер известных тегов."""
    pos = 0
    total = 0
    for m in _TAG_RE.finditer(v):
        if m.start() != pos:
            return "invalid tag syntax"
        pos = m.end()
        name, arg = m.group(1), m.group(2)
        if name == "b":
            if not arg or not _HEX_BYTES_RE.match(arg):
                return "<b> needs 0x followed by an even number of hex digits"
            total += (len(arg) - 2) // 2
        elif name == "r":
            if not arg or not _INT_RE.match(arg) or int(arg) < 1:
                return "<r> needs a positive byte count"
            total += int(arg)
        elif name in ("c", "t"):
            if arg is not None:
                return "<%s> takes no argument" % name
            total += 4
    if pos == 0 or pos != len(v):
        return "invalid tag syntax"
    if total > IPACKET_MAX_BYTES:
        return "packet is %d bytes, limit is %d" % (total, IPACKET_MAX_BYTES)
    return None


def validate_params(line, params):
    """Список ошибок (пустой = всё в порядке). Каждое правило контракта — отдельное сообщение."""
    if line not in LINES:
        return ["unknown line %r (expected one of %s)" % (line, ", ".join(LINES))]
    p = normalize_params(params)
    errs = []

    for k in J_KEYS + ("S1", "S2") + H_KEYS:
        if not p[k]:
            errs.append("%s is required" % k)

    ints = {}
    for k in J_KEYS + S_KEYS:
        if not p[k]:
            continue
        if not _INT_RE.match(p[k]):
            errs.append("%s must be a non-negative integer" % k)
        else:
            ints[k] = int(p[k])

    if "Jc" in ints and not 1 <= ints["Jc"] <= JC_MAX:
        errs.append("Jc must be within 1..%d" % JC_MAX)
    if "Jmax" in ints and ints["Jmax"] > JMAX_MAX:
        errs.append("Jmax must be <= %d" % JMAX_MAX)
    if "Jmin" in ints and "Jmax" in ints and not ints["Jmin"] < ints["Jmax"]:
        errs.append("Jmin must be < Jmax")
    for k in S_KEYS:
        if k in ints and ints[k] > S_MAX[k]:
            errs.append("%s must be <= %d" % (k, S_MAX[k]))

    # Итоговые размеры пакетов попарно различны (для 1.x это и есть S1+56 ≠ S2).
    sizes = [(k, MESSAGE_BASE_SIZE[k] + ints[k]) for k in S_KEYS if k in ints]
    for i in range(len(sizes)):
        for j in range(i + 1, len(sizes)):
            if sizes[i][1] == sizes[j][1]:
                ka, kb = sizes[i][0], sizes[j][0]
                if (ka, kb) == ("S1", "S2"):
                    errs.append("S1+56 must not equal S2 (initiation and response would have the same size)")
                else:
                    errs.append("%d+%s equals %d+%s (packet sizes must differ)"
                                % (MESSAGE_BASE_SIZE[ka], ka, MESSAGE_BASE_SIZE[kb], kb))

    if p["HeaderProtectionKey"]:
        if not is_key(p["HeaderProtectionKey"]):
            errs.append("HeaderProtectionKey must be base64 of 32 bytes")
        for k in S_KEYS:
            if k in ints and ints[k] < S_MIN_WITH_HEADER_PROTECTION:
                errs.append("%s must be >= %d when HeaderProtectionKey is set"
                            % (k, S_MIN_WITH_HEADER_PROTECTION))

    spans = []
    for h in H_KEYS:
        v = p[h]
        if not v:
            continue
        span = _parse_range(v)
        if span is None:
            errs.append("%s must be an integer or a range a-b" % h)
            continue
        a, b = span
        if not (1 <= a <= H_MAX and 1 <= b <= H_MAX):
            errs.append("%s must be within 1..%d" % (h, H_MAX))
            continue
        if a > b:
            errs.append("%s range start must not exceed its end" % h)
            continue
        spans.append((h, a, b))
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            ha, a1, b1 = spans[i]
            hb, a2, b2 = spans[j]
            if a1 <= b2 and a2 <= b1:
                if a1 == b1 and a2 == b2:
                    errs.append("%s and %s must differ" % (ha, hb))
                else:
                    errs.append("%s and %s ranges overlap" % (ha, hb))

    for k in I_KEYS:
        if p[k]:
            problem = _check_ipacket(p[k])
            if problem:
                errs.append("%s: %s" % (k, problem))

    for k in ("ContentPaddingAddition", "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime",
              "KeepaliveTimeout", "MaxHandshakeAttempts"):
        if p[k]:
            span = _parse_range(p[k])
            if span is None or span[0] > span[1]:
                errs.append("%s must be an integer or a range a-b" % k)
    for k in AWG3_TOGGLE_KEYS:
        if p[k] and p[k].lower() not in TOGGLE_VALUES:
            errs.append("%s must be on or off" % k)

    if p["PersistentKeepalive"]:
        span = _parse_range(p["PersistentKeepalive"])
        if span is None or span[0] > span[1] or span[1] > 65535:
            errs.append("PersistentKeepalive must be an integer or a range a-b")
        elif span[0] != span[1] and line != "3.1":
            errs.append("PersistentKeepalive range is allowed only for line 3.1")
    # MTU здесь проверяется только на ФОРМАТ. Продуктовый диапазон (1200-1500)
    # живёт в одном месте — ga_valid_mtu в lib/common.sh, — и его спрашивают все
    # пути, принимающие MTU снаружи. Второй диапазон здесь означал бы, что один
    # и тот же MTU принимается одним путём и отвергается другим; кроме того,
    # этот же валидатор разбирает конфиги ПОДХВАЧЕННЫХ контейнеров приложения,
    # и отвергать чужой рабочий контейнер из-за нашего договора мы не вправе.
    if p["MTU"] and not _INT_RE.match(p["MTU"]):
        errs.append("MTU must be an integer")

    # Ограничения линеек: что старые клиенты и роутеры не переварят.
    has_i = any(p[k] for k in I_KEYS)
    has_s34 = bool(p["S3"] or p["S4"])
    has_ranges = any("-" in p[h] for h in H_KEYS)
    if line == "1.0" and has_i:
        errs.append("line 1.0 forbids I1-I5")
    if line in ("1.0", "1.5") and has_s34:
        errs.append("line %s forbids S3/S4" % line)
    if line in ("1.0", "1.5") and has_ranges:
        errs.append("line %s forbids H ranges" % line)
    if line in ("1.0", "1.5", "2.0") and has_awg3_markers(p):
        errs.append("line %s forbids 3.x fields (%s)" % (line, ", ".join(AWG3_KEYS)))
    return errs


# ---------------------------------------------------------------------------
# Генерация параметров по линейкам
# ---------------------------------------------------------------------------

def _gen_s1_s2():
    while True:
        s1 = _rand_int(15, 150)
        s2 = _rand_int(15, 150)
        if s1 + 56 != s2:
            return s1, s2


def _gen_s3_s4(s1, s2, limit=S4_GEN_MAX):
    """S3/S4 так, чтобы все четыре итоговых размера были попарно различны; S4 ≤ limit (бюджет — ``s4_max``)."""
    taken = {MESSAGE_BASE_SIZE["S1"] + s1, MESSAGE_BASE_SIZE["S2"] + s2}
    hi = max(S4_GEN_MIN, min(S4_GEN_MAX, int(limit)))
    for _ in range(1000):
        s3 = _rand_int(15, 64)
        s4 = _rand_int(S4_GEN_MIN, hi)
        sizes = (MESSAGE_BASE_SIZE["S3"] + s3, MESSAGE_BASE_SIZE["S4"] + s4)
        if sizes[0] != sizes[1] and not (set(sizes) & taken):
            return s3, s4
    raise AwgError("could not pick S3/S4")


def _gen_h_singles():
    vals = []
    while len(vals) < 4:
        v = _rand_int(H_GEN_MIN, H_GEN_MAX)
        if v not in vals:
            vals.append(v)
    return [str(v) for v in vals]


def _gen_h_ranges():
    """Четыре непересекающихся диапазона a-b шириной 1000–5000 внутри [5, 2^31)."""
    spans = []
    for _ in range(1000):
        width = _rand_int(H_RANGE_WIDTH[0], H_RANGE_WIDTH[1])
        a = _rand_int(H_GEN_MIN, H_GEN_MAX - width)
        b = a + width
        if all(b < a2 or a > b2 for a2, b2 in spans):
            spans.append((a, b))
            if len(spans) == 4:
                return ["%d-%d" % s for s in spans]
    raise AwgError("could not pick H ranges")


def regen_s4(params, budget=OUTER_BUDGET):
    """Те же параметры 2.0 с новыми S3/S4 (S4 ≤ ``s4_max(budget, MTU)``; S1, S2, H, I и MTU прежние). Клиенты со
    старыми параметрами после этого не подключатся — их настройки выдают заново."""
    p = normalize_params(params)
    s1, s2 = int(p["S1"] or 15), int(p["S2"] or 15)
    s3, s4 = _gen_s3_s4(s1, s2, s4_max(budget, p["MTU"] or DEFAULT_MTU))
    p["S3"], p["S4"] = str(s3), str(s4)
    return p


def gen_params(line, preset="default", budget=OUTER_BUDGET):
    """Параметры маскировки для линейки (CONTRACT §4.2, правила генерации). ``budget`` — бюджет внешнего пакета
    (Kaskad: min(1400, MTU карты сервера)): S4 линии 2.0 ≤ ``s4_max(budget)``."""
    if line not in LINES:
        raise AwgError("unknown line %r" % (line,))
    if preset not in PRESETS:
        raise AwgError("unknown preset %r" % (preset,))
    p = {k: "" for k in PARAM_KEYS}
    p["Jc"] = str(_rand_int(4, 6))
    p["Jmin"] = "10"
    p["Jmax"] = "50"
    p["MTU"] = DEFAULT_MTU
    p["PersistentKeepalive"] = DEFAULT_KEEPALIVE

    if line == "3.1":
        # Ровно как awgInstaller.cpp 5.0.2: заголовки защищены ключом, поэтому H и S
        # больше не рандомизируют; тайминги — диапазоны из protocolConstants.h.
        for k in S_KEYS:
            p[k] = "12"
        for i, h in enumerate(H_KEYS, start=1):
            p[h] = str(i)
        p["HeaderProtectionKey"] = genkey()
        p["RekeyAfterTime"] = "100-120"
        p["RekeyTimeout"] = "3-7"
        p["RejectAfterTime"] = "150-180"
        p["KeepaliveTimeout"] = "5-15"
        p["MaxHandshakeAttempts"] = "15-20"
        p["RandomTrailers"] = "on"
        p["DisableCookies"] = "on"
        p["PersistentKeepalive"] = "25-35"
        # ContentPaddingAddition приложение при установке не задаёт — и мы не задаём.
    else:
        s1, s2 = _gen_s1_s2()
        p["S1"], p["S2"] = str(s1), str(s2)
        if line == "2.0":
            s3, s4 = _gen_s3_s4(s1, s2, s4_max(budget, p["MTU"]))
            p["S3"], p["S4"] = str(s3), str(s4)
            for h, v in zip(H_KEYS, _gen_h_ranges()):
                p[h] = v
        else:
            for h, v in zip(H_KEYS, _gen_h_singles()):
                p[h] = v

    # Сигнатурные пакеты: только с 1.5; пресет router = «без I» для роутеров и старых клиентов.
    if line != "1.0" and preset != "router":
        p["I1"] = QUIC_I1 if preset == "quic" else DEFAULT_I1
    return p


# ---------------------------------------------------------------------------
# .conf клиента и awg0.conf сервера
# ---------------------------------------------------------------------------

_COMMENTED_I_RE = re.compile(r"^#\s*(I[1-5])\s*=\s*(.*)$")


def parse_conf(text):
    """Разбор INI-подобного конфига WireGuard/AmneziaWG → (interface: dict, peers: [dict]).
    Закомментированные `# I1 = …` в [Interface] читаем как параметры: так их хранит
    серверный конфиг приложения (I-пакеты шлёт только клиент, серверу они не нужны,
    но приложение читает их оттуда для выпуска клиентских ключей)."""
    iface = {}
    peers = []
    cur = None
    # Метка порядка байтов (BOM) в начале: так сохраняет Блокнот Windows, и строка «﻿[Interface]» не
    # открывала секцию — PrivateKey пропускался, и привязка старого ключа отвечала «ожидается [Interface] и
    # PrivateKey», хотя оба на месте (находка TK-1). Снимаем её здесь, в единственном разборе конфигов.
    for raw in (text or "").replace("\r", "").replace("﻿", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("["):
            name = line.strip("[]").strip().lower()
            if name == "interface":
                cur = iface
            elif name == "peer":
                cur = {}
                peers.append(cur)
            else:
                cur = None
            continue
        if line.startswith("#"):
            m = _COMMENTED_I_RE.match(line)
            if m and cur is iface and not iface.get(m.group(1)):
                iface[m.group(1)] = m.group(2).strip()
            continue
        if cur is None or "=" not in line:
            continue
        k, _, v = line.partition("=")
        cur[k.strip()] = v.strip()
    return iface, peers


def params_from_conf(text):
    """Параметры линейки из текста .conf (клиентского или серверного)."""
    iface, peers = parse_conf(text)
    src = {k: iface.get(k, "") for k in AWG_IFACE_KEYS + AWG3_KEYS}
    src["MTU"] = iface.get("MTU", "")
    src["PersistentKeepalive"] = peers[0].get("PersistentKeepalive", "") if peers else ""
    return normalize_params(src)


def line_filter(line, params):
    """Убрать из параметров то, чего линейка не знает: 1.0 — I*, S3/S4, 3.x; 1.5 — S3/S4, 3.x; 2.0 — 3.x."""
    p = normalize_params(params)
    if line == "1.0":
        for k in I_KEYS:
            p[k] = ""
    if line in ("1.0", "1.5"):
        p["S3"] = p["S4"] = ""
    if line in ("1.0", "1.5", "2.0"):
        for k in AWG3_KEYS:
            p[k] = ""
    return p


def _fmt_endpoint(host, port):
    host = (host or "").strip()
    if not host:
        raise AwgError("server.endpoint_host is required")
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host  # IPv6 в квадратных скобках
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise AwgError("server.port must be an integer")
    if not 1 <= port <= 65535:
        raise AwgError("server.port must be within 1..65535")
    return "%s:%d" % (host, port)


def _split_endpoint(endpoint):
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return "", ""
    if endpoint.startswith("["):
        host, _, rest = endpoint[1:].partition("]")
        return host, rest.lstrip(":")
    host, _, port = endpoint.rpartition(":")
    if not host:
        return endpoint, ""
    return host, port


def _keepalive_for_line(line, value):
    v = (value or "").strip() or DEFAULT_KEEPALIVE
    # Диапазон keepalive понимают только клиенты 3.x; остальным отдаём нижнюю границу.
    if line != "3.1" and "-" in v:
        v = v.split("-", 1)[0]
    return v


def render_conf(line, data):
    """Текст .conf для клиента. Порядок строк — как в template.conf приложения, плюс MTU
    (приложение MTU в тексте не пишет, но amneziawg-tools и роутеры его читают)."""
    if line not in LINES:
        raise AwgError("unknown line %r" % (line,))
    if not isinstance(data, dict):
        raise AwgError("conf expects a JSON object")
    p = line_filter(line, data.get("params"))
    client = data.get("client") or {}
    server = data.get("server") or {}
    for k in ("ip", "privkey"):
        if not client.get(k):
            raise AwgError("client.%s is required" % k)
    if not server.get("pubkey"):
        raise AwgError("server.pubkey is required")
    ip = str(client["ip"]).strip()
    address = ip if "/" in ip else ip + "/32"
    dns = [str(d).strip() for d in (data.get("dns") or []) if str(d).strip()]
    mtu = str(data.get("mtu") or p["MTU"] or DEFAULT_MTU).strip()
    keepalive = _keepalive_for_line(line, p["PersistentKeepalive"])
    endpoint = _fmt_endpoint(server.get("endpoint_host"), server.get("port") or 0)

    lines = ["[Interface]", "Address = %s" % address]
    if dns:
        lines.append("DNS = %s" % ", ".join(dns))
    lines.append("PrivateKey = %s" % client["privkey"])
    lines.append("MTU = %s" % mtu)
    for k in AWG_IFACE_KEYS + AWG3_KEYS:
        if p[k]:
            lines.append("%s = %s" % (k, p[k]))
    lines += ["", "[Peer]", "PublicKey = %s" % server["pubkey"]]
    if client.get("psk"):
        lines.append("PresharedKey = %s" % client["psk"])
    lines.append("AllowedIPs = %s" % (client.get("allowed_ips") or DEFAULT_ALLOWED_IPS))
    lines.append("Endpoint = %s" % endpoint)
    lines.append("PersistentKeepalive = %s" % keepalive)
    return "\n".join(lines) + "\n"


def render_server_conf(line, data):
    """awg0.conf сервера в формате configure_container.sh приложения."""
    if line not in LINES:
        raise AwgError("unknown line %r" % (line,))
    if not isinstance(data, dict):
        raise AwgError("server-conf expects a JSON object")
    p = line_filter(line, data.get("params"))
    server = data.get("server") or {}
    for k in ("privkey", "subnet", "port"):
        if not server.get(k):
            raise AwgError("server.%s is required" % k)
    subnet = str(server["subnet"]).strip()
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$", subnet):
        raise AwgError("server.subnet must look like 10.8.2.0/24")
    # Address сервера — сетевой адрес подсети (10.8.2.0/24), как у Amnezia: приложение
    # раздаёт клиентам адреса от последнего AllowedIPs, и .1 остаётся первому клиенту,
    # поэтому наш аллокатор и аллокатор приложения не сталкиваются.
    lines = ["[Interface]",
             "PrivateKey = %s" % server["privkey"],
             "Address = %s" % subnet,
             "ListenPort = %d" % int(server["port"])]
    # MTU профиля — и в конфиг сервера: awg-quick поднимает интерфейс с ним, а без строки берёт своё (1420), и
    # настройка продукта расходилась с живым интерфейсом (жалоба 29.09.2026: подхваченный конфиг с MTU = 1360
    # после первой же пересборки остался без строки). Пусто — строки нет, как у приложения Amnezia.
    mtu = str(p.get("MTU") or "").strip()
    if mtu:
        if not _INT_RE.match(mtu):
            raise AwgError("params.MTU must be an integer")
        lines.append("MTU = %s" % mtu)
    for k in J_KEYS + S_KEYS + H_KEYS + AWG3_KEYS:
        if p[k]:
            lines.append("%s = %s" % (k, p[k]))
    # I1–I5 серверу не нужны (сигнатурные пакеты шлёт клиент), но приложение хранит их
    # здесь закомментированными и читает обратно при выпуске ключей — повторяем формат.
    for k in I_KEYS:
        if p[k]:
            lines.append("# %s = %s" % (k, p[k]))
    for peer in data.get("peers") or []:
        if not peer.get("pubkey") or not peer.get("ip"):
            raise AwgError("each peer needs pubkey and ip")
        ip = str(peer["ip"]).strip()
        lines += ["", "[Peer]", "PublicKey = %s" % peer["pubkey"]]
        if peer.get("psk"):
            lines.append("PresharedKey = %s" % peer["psk"])
        lines.append("AllowedIPs = %s" % (ip if "/" in ip else ip + "/32"))
    return "\n".join(lines) + "\n"


def conf_client_data(text):
    """Текст .conf клиента (render_conf) → те же данные, из которых он собран: {params, client, server, dns, mtu}.

    Служба подписок (lib/pages_server.py) получает у моста ключ готовым .conf (user-key) и строит из него профиль
    mihomo: второго пути к приватным ключам у неё нет, и прокси mihomo по линии — ровно тот ключ, что лежит в
    .conf этой линии. render_conf(line, conf_client_data(render_conf(line, d))) — тот же текст (tests/test_awgkeys.py)."""
    iface, peers = parse_conf(text)
    if not peers:
        raise AwgError("client .conf has no [Peer] section")
    peer = peers[0]
    host, port = _split_endpoint(peer.get("Endpoint", ""))
    return {
        "params": params_from_conf(text),
        "client": {"ip": iface.get("Address", "").strip(), "privkey": iface.get("PrivateKey", "").strip(),
                   "psk": peer.get("PresharedKey", "").strip(), "allowed_ips": peer.get("AllowedIPs", "").strip()},
        "server": {"pubkey": peer.get("PublicKey", "").strip(), "endpoint_host": host, "port": port},
        "dns": [d.strip() for d in iface.get("DNS", "").split(",") if d.strip()],
        "mtu": iface.get("MTU", "").strip(),
    }


# ---------------------------------------------------------------------------
# Профиль mihomo (Clash Meta): Clash Verge Rev, FlClash, Mihomo Party, Clash Meta for Android
# ---------------------------------------------------------------------------
#
# Поля сверены с первоисточником 03.10.2026: документация wiki.metacubex.one (config/proxies/wg) и исходник
# MetaCubeX/mihomo adapter/outbound/wireguard.go (WireGuardOption, AmneziaWGOption); образец — 3X-UI
# internal/sub/clash_service.go, buildAmneziaWGProxy. AmneziaWG в mihomo — тот же type: wireguard плюс
# amnezia-wg-option. Что какая версия ядра понимает (release notes MetaCubeX/mihomo):
#   • 1.0 (jc, jmin, jmax, s1, s2, h1–h4) — с 1.18.9;
#   • 1.5 (i1–i5) — с 1.19.13;
#   • 2.0 (s3, s4, диапазоны в h1–h4) — с 1.19.14 (падение на большом s4 исправлено в 1.19.28);
#   • 3.0 и 3.1 (header-protection-key, тайминги, random-trailers, disable-cookies) — с 1.19.30, и только с
#     version: 3: без поля или с другим числом mihomo берёт прежнюю реализацию 1.x/2.0. Ошибки 3.x
#     (random-trailers и disable-cookies) исправлены в 1.19.31 — для линии 3.1 нужен он или новее.
# Типы — как у mihomo: jc, jmin, jmax, s1–s4, mtu, port, persistent-keepalive — целые; h1–h4, i1–i5 и тайминги
# 3.x — строки (диапазоны «100-120» он разбирает сам); random-trailers и disable-cookies — да/нет. Диапазона
# keepalive (3.1: «25-35») у mihomo нет — persistent-keepalive целое, и туда идёт нижняя граница. Ключ защиты
# заголовков — base64, как в .conf (mihomo переводит его в hex сам).
MIHOMO_GROUP = "goAmnezia"
# Группа «приложение выбирает само» (fallback): первая живая линия в порядке профиля; живость — GET адреса проверки
# через саму линию раз в MIHOMO_CHECK_INTERVAL секунд. Адрес — голый IP, без имени: на только что поднятом туннеле
# 3.1 первый DNS-запрос через него теряется (живая проверка на test1, 04.10.2026: привычная проверка gstatic сразу после
# запуска падала, и «Auto» на 5 минут уходил на 2.0, хотя 3.1 работал). Ответ годится любой (expected-status у mihomo
# по умолчанию «*»): 1.1.1.1 по http отвечает 301 — связь через линию есть.
MIHOMO_AUTO = "Auto"
MIHOMO_CHECK_URL = "http://1.1.1.1/cdn-cgi/trace"
MIHOMO_CHECK_INTERVAL = 300
# Поля amnezia-wg-option: (ключ params, ключ mihomo, тип). Порядок — как в документации mihomo.
_MIHOMO_AWG = (
    ("Jc", "jc", int), ("Jmin", "jmin", int), ("Jmax", "jmax", int),
    ("S1", "s1", int), ("S2", "s2", int), ("S3", "s3", int), ("S4", "s4", int),
    ("H1", "h1", str), ("H2", "h2", str), ("H3", "h3", str), ("H4", "h4", str),
    ("I1", "i1", str), ("I2", "i2", str), ("I3", "i3", str), ("I4", "i4", str), ("I5", "i5", str),
    ("HeaderProtectionKey", "header-protection-key", str),
    ("ContentPaddingAddition", "content-padding-addition", str),
    ("RekeyAfterTime", "rekey-after-time", str), ("RekeyTimeout", "rekey-timeout", str),
    ("RejectAfterTime", "reject-after-time", str), ("KeepaliveTimeout", "keepalive-timeout", str),
    ("MaxHandshakeAttempts", "max-handshake-attempts", str),
    ("RandomTrailers", "random-trailers", bool), ("DisableCookies", "disable-cookies", bool),
)
# mihomo с dns, которые не разбирает его dns.ParseNameServer, отвергает весь профиль (заметка 3X-UI) — DNS
# туннеля отдаём только голыми IP-адресами (продукт других и не пишет, ga_export_dns_json).
_MIHOMO_DNS_RE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|[0-9A-Fa-f:]{2,39})$")


def _mihomo_int(key, value):
    v = str(value or "").strip()
    if not _INT_RE.match(v):
        raise AwgError("%s must be an integer for mihomo, got %r" % (key, v))
    return int(v)


def mihomo_proxy(line, data, name):
    """Прокси mihomo по линии из тех же данных, что render_conf ({params, client, server, dns, mtu}) →
    список пар (ключ, значение) в порядке вывода; amnezia-wg-option — вложенный такой же список.
    Значения — str, int, bool или список строк (allowed-ips, dns). version: 3 — только у линии 3.1."""
    if line not in LINES:
        raise AwgError("unknown line %r" % (line,))
    if not isinstance(data, dict):
        raise AwgError("mihomo expects a JSON object")
    if not str(name or "").strip():
        raise AwgError("proxy name is required")
    p = line_filter(line, data.get("params"))
    client = data.get("client") or {}
    server = data.get("server") or {}
    for k in ("ip", "privkey"):
        if not client.get(k):
            raise AwgError("client.%s is required" % k)
    if not server.get("pubkey"):
        raise AwgError("server.pubkey is required")
    # Тот же разбор адреса сервера, что у .conf: пустой хост и порт вне 1..65535 — отказ; IPv6 — без скобок.
    host, port = _split_endpoint(_fmt_endpoint(server.get("endpoint_host"), server.get("port") or 0))
    v4 = v6 = ""
    for item in str(client["ip"]).split(","):
        addr = item.strip().split("/")[0].strip()
        if not addr:
            continue
        if ":" in addr:
            v6 = v6 or addr
        else:
            v4 = v4 or addr
    if not v4 and not v6:
        raise AwgError("client.ip is required")
    mtu = _mihomo_int("MTU", data.get("mtu") or p["MTU"] or DEFAULT_MTU)
    keepalive = _keepalive_for_line(line, p["PersistentKeepalive"]).split("-", 1)[0]
    allowed = [a.strip() for a in str(client.get("allowed_ips") or DEFAULT_ALLOWED_IPS).split(",") if a.strip()]
    dns = [str(d).strip() for d in (data.get("dns") or []) if str(d).strip()]

    proxy = [("name", str(name).strip()), ("type", "wireguard"), ("server", host), ("port", int(port))]
    if v4:
        proxy.append(("ip", v4))
    if v6:
        proxy.append(("ipv6", v6))
    proxy.append(("private-key", str(client["privkey"]).strip()))
    proxy.append(("public-key", str(server["pubkey"]).strip()))
    if client.get("psk"):
        proxy.append(("pre-shared-key", str(client["psk"]).strip()))
    proxy += [("allowed-ips", allowed), ("udp", True), ("mtu", mtu),
              ("persistent-keepalive", _mihomo_int("PersistentKeepalive", keepalive))]
    if dns and all(_MIHOMO_DNS_RE.match(d) for d in dns):
        proxy += [("remote-dns-resolve", True), ("dns", dns)]

    awg = []
    if line == "3.1":
        awg.append(("version", 3))
    for src, key, kind in _MIHOMO_AWG:
        value = p[src]
        if kind is bool:
            if _toggle_enabled(value):
                awg.append((key, True))
        elif value:
            awg.append((key, _mihomo_int(src, value) if kind is int else value))
    if awg:
        proxy.append(("amnezia-wg-option", awg))
    return proxy


def _yaml_unsafe(o):
    """Знак, которого парсер YAML «как есть» видеть не должен: управляющие C0 и C1, DEL, разделители строк
    U+2028/2029, BOM, U+FFFE/FFFF и одиночные суррогаты."""
    return o < 0x20 or 0x7F <= o <= 0x9F or o in (0x2028, 0x2029, 0xFEFF, 0xFFFE, 0xFFFF) or 0xD800 <= o <= 0xDFFF


def _yaml_str(text):
    """Строка YAML в двойных кавычках: экранируем кавычку, обратную косую и всё _yaml_unsafe (одиночный
    суррогат — знак замены). Остальной Юникод (подпись человека кириллицей) идёт как есть, в UTF-8."""
    out = ['"']
    for ch in str(text):
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch in "\n\t\r":
            out.append({"\n": "\\n", "\t": "\\t", "\r": "\\r"}[ch])
        elif 0xD800 <= o <= 0xDFFF:
            out.append("\\ufffd")
        elif _yaml_unsafe(o):
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return _yaml_str(value)


def _yaml_pairs(pairs, indent):
    """Пары (ключ, значение) блоком YAML: списки скаляров — в строку [..], вложенные пары — блоком ниже."""
    pad = " " * indent
    out = []
    for key, value in pairs:
        if isinstance(value, list) and value and isinstance(value[0], tuple):
            out.append("%s%s:" % (pad, key))
            out.extend(_yaml_pairs(value, indent + 2))
        elif isinstance(value, list):
            out.append("%s%s: [%s]" % (pad, key, ", ".join(_yaml_scalar(v) for v in value)))
        else:
            out.append("%s%s: %s" % (pad, key, _yaml_scalar(value)))
    return out


def mihomo_profile(proxies, group=MIHOMO_GROUP, title=""):
    """Полный профиль mihomo без внешних библиотек: прокси (списки пар mihomo_proxy), группы и одно правило MATCH.
    Клиенты с оболочкой (Clash Verge Rev, FlClash) порт, режим и журнал ставят свои и берут из профиля proxies,
    proxy-groups и rules; голому ядру mihomo хватает и этого: локальный mixed-port 7890 без выхода в сеть
    (allow-lan: false). title — строка комментария в начале (подпись человека).

    Линий несколько — приложение выбирает само (страница подключения так и обещает): первым в группе выбора стоит
    «Auto» — fallback по линиям в порядке продукта (3.1 → 2.0 → 1.5 → 1.0) с проверкой доступности. Ядро, которое
    3.1 ещё не умеет (version: 3 оно пропускает, и ключ 3.1 не рукопожмётся), уходит на 2.0 само, а не ждёт, пока
    человек выберет линию руками; руками выбрать линию в группе по-прежнему можно."""
    if not proxies:
        raise AwgError("no proxies for the mihomo profile")
    names = [dict(p).get("name") for p in proxies]
    auto = len(names) > 1
    if len(set(names)) != len(names) or group in names or (auto and (MIHOMO_AUTO in names or group == MIHOMO_AUTO)):
        raise AwgError("proxy names must be unique and differ from the group names")
    out = ["# goAmnezia pro — mihomo (Clash Meta): Clash Verge Rev, FlClash, Mihomo Party, Clash Meta for Android"]
    # Подпись в комментарии экранировать нечем — знаки, которые сломали бы разбор, просто выбрасываем.
    title = " ".join("".join(ch for ch in str(title or "") if not _yaml_unsafe(ord(ch))).split())
    if title:
        out.append("# " + title)
    out += ["mixed-port: 7890", "allow-lan: false", "mode: rule", "log-level: warning", "proxies:"]
    for proxy in proxies:
        body = _yaml_pairs(proxy, 4)
        out.append("  - " + body[0].lstrip())
        out.extend(body[1:])
    out += ["proxy-groups:", "  - name: %s" % _yaml_str(group), "    type: select", "    proxies:"]
    out += ["      - %s" % _yaml_str(n) for n in ([MIHOMO_AUTO] if auto else []) + names]
    if auto:
        out += ["  - name: %s" % _yaml_str(MIHOMO_AUTO), "    type: fallback",
                "    url: %s" % _yaml_str(MIHOMO_CHECK_URL), "    interval: %d" % MIHOMO_CHECK_INTERVAL, "    proxies:"]
        out += ["      - %s" % _yaml_str(n) for n in names]
    out += ["rules:", "  - %s" % _yaml_str("MATCH," + group)]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# vpn:// — qCompress + base64url
# ---------------------------------------------------------------------------

def qcompress(data, level=8):
    """Qt qCompress: 4 байта big-endian длины исходных данных + zlib-поток.
    Без этого префикса qUncompress в приложении вернёт пустой массив."""
    return len(data).to_bytes(4, "big") + zlib.compress(data, level)


def quncompress(blob):
    if len(blob) < 4:
        raise AwgError("vpn payload too short")
    try:
        return zlib.decompress(blob[4:])
    except zlib.error:
        pass
    # Терпимость к «сырому» zlib без префикса — как у эталонного декодера.
    try:
        return zlib.decompress(blob)
    except zlib.error:
        raise AwgError("vpn payload is not zlib data")


def b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(s):
    s = "".join(str(s).split())
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError):
        raise AwgError("bad base64url payload")


def _json_compact(obj):
    # Ключи сортируем: QJsonObject хранит ключи отсортированными, так что вывод
    # неотличим по структуре от приложения и детерминирован для тестов.
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _replace_dns_line(text, new_value):
    out = []
    done = False
    for raw in text.split("\n"):
        if not done and raw.strip().startswith("DNS") and "=" in raw:
            k, _, _ = raw.partition("=")
            if k.strip() == "DNS":
                out.append("%s= %s" % (k, new_value))
                done = True
                continue
        out.append(raw)
    return "\n".join(out)


def build_vpn_json(data):
    """JSON «подключения» приложения (без SSH-учётки): один контейнер, defaultContainer,
    description, dns1/dns2, hostName. Структура — selfHostedAdminServerConfig.cpp +
    awgProtocolConfig.cpp 5.0.2."""
    if not isinstance(data, dict):
        raise AwgError("vpn-encode expects a JSON object")
    conf_text = data.get("conf_text") or ""
    if "[Interface]" not in conf_text or "[Peer]" not in conf_text:
        raise AwgError("conf_text must contain [Interface] and [Peer]")
    iface, peers = parse_conf(conf_text)
    peer = peers[0] if peers else {}
    if not iface.get("PrivateKey") or not iface.get("Address") or not peer.get("PublicKey"):
        raise AwgError("conf_text must have PrivateKey, Address and peer PublicKey")

    params = normalize_params(data.get("params"))
    conf_params = params_from_conf(conf_text)
    for k in PARAM_KEYS:
        if not params[k]:
            params[k] = conf_params[k]
    line = data.get("line") or detect_line(params)
    if line not in LINES:
        raise AwgError("unknown line %r" % (line,))

    ep_host, ep_port = _split_endpoint(peer.get("Endpoint", ""))
    host = str(data.get("hostName") or ep_host).strip()
    if not host:
        raise AwgError("hostName is required (no Endpoint in conf_text)")
    try:
        port = int(data.get("port") or ep_port or 0)
    except ValueError:
        raise AwgError("port must be an integer")
    if not 1 <= port <= 65535:
        raise AwgError("port must be within 1..65535")

    container = str(data.get("container") or DEFAULT_CONTAINER).strip()
    if container not in KNOWN_CONTAINERS:
        container = DEFAULT_CONTAINER

    dns = [str(d).strip() for d in (data.get("dns") or []) if str(d).strip()]
    conf_dns = [d.strip() for d in iface.get("DNS", "").split(",") if d.strip()]
    if not dns:
        dns = conf_dns
    config_text = conf_text
    # Приложение хранит в тексте плейсхолдеры $PRIMARY_DNS/$SECONDARY_DNS и подставляет
    # их при подключении из dns1/dns2 — повторяем, когда DNS в тексте совпадают с заданными,
    # чтобы приложение могло применить свои настройки DNS, а декодер восстановил текст один в один.
    if conf_dns and conf_dns == dns and len(dns) <= 2:
        config_text = _replace_dns_line(conf_text, ", ".join(["$PRIMARY_DNS", "$SECONDARY_DNS"][:len(dns)]))

    client_ip = iface["Address"].split(",")[0].strip().split("/")[0]
    client_priv = iface["PrivateKey"]
    client_pub = pubkey(client_priv)
    psk = peer.get("PresharedKey") or peer.get("PreSharedKey") or ""
    allowed = [a.strip() for a in peer.get("AllowedIPs", DEFAULT_ALLOWED_IPS).split(",") if a.strip()]

    # last_config — то, что читают клиенты при подключении (десктоп-демон, Android, iOS
    # берут отсюда ключи и адреса, а не из текста): port здесь int, всё остальное строки.
    last = {
        "config": config_text,
        "hostName": host,
        "port": port,
        "client_ip": client_ip,
        "client_priv_key": client_priv,
        "client_pub_key": client_pub,
        "server_pub_key": peer["PublicKey"],
        "clientId": client_pub,
        "allowed_ips": allowed or ["0.0.0.0/0", "::/0"],
        "persistent_keep_alive": peer.get("PersistentKeepalive") or params["PersistentKeepalive"] or DEFAULT_KEEPALIVE,
        "mtu": iface.get("MTU") or params["MTU"] or DEFAULT_MTU,
    }
    if psk:
        last["psk_key"] = psk
    for k in AWG_IFACE_KEYS + AWG3_KEYS:
        if params[k]:
            last[k] = params[k]

    # Объект контейнера — серверная часть конфига: port тут строка (fromJson читает toString()),
    # I1–I5 всегда присутствуют (так пишет AwgServerConfig::toJson), остальное — только непустое.
    awg = {"port": str(port), "transport_proto": "udp"}
    proto_ver = LINE_TO_PROTOCOL_VERSION[line]
    if proto_ver:
        awg["protocol_version"] = proto_ver
    subnet = str(data.get("subnet") or "").strip()
    if subnet:
        addr, _, cidr = subnet.partition("/")
        awg["subnet_address"] = addr
        if cidr:
            awg["subnet_cidr"] = cidr
    for k in J_KEYS + S_KEYS + H_KEYS + AWG3_KEYS:
        if params[k]:
            awg[k] = params[k]
    for k in I_KEYS:
        awg[k] = params[k]
    awg["last_config"] = _json_compact(last)

    top = {
        "containers": [{"container": container, "awg": awg}],
        "defaultContainer": container,
        "hostName": host,
    }
    description = str(data.get("description") or "").strip()
    if description:
        top["description"] = description
    if dns:
        top["dns1"] = dns[0]
    if len(dns) > 1:
        top["dns2"] = dns[1]
    return top


def vpn_compressed(data):
    return qcompress(_json_compact(build_vpn_json(data)).encode("utf-8"))


def vpn_encode(data):
    return "vpn://" + b64url_encode(vpn_compressed(data))


def _strip_vpn_prefix(s):
    s = str(s).strip()
    return s[len("vpn://"):] if s.startswith("vpn://") else s


def vpn_decode(vpn):
    """vpn:// → наш JSON: контейнер, линейка, hostName/port, dns, params, conf_text, ключи, raw."""
    raw = b64url_decode(_strip_vpn_prefix(vpn))
    try:
        top = json.loads(quncompress(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AwgError("vpn payload is not JSON")
    if not isinstance(top, dict):
        raise AwgError("vpn payload is not a JSON object")
    cont = None
    for c in top.get("containers") or []:
        if isinstance(c, dict) and isinstance(c.get("awg"), dict):
            cont = c
            break
    if cont is None:
        raise AwgError("no awg container in vpn payload")
    awg = cont["awg"]
    last_raw = awg.get("last_config") or ""
    try:
        last = json.loads(last_raw) if last_raw else {}
    except json.JSONDecodeError:
        raise AwgError("last_config is not JSON")
    if not isinstance(last, dict):
        last = {}

    conf_text = last.get("config") or ""
    dns1 = str(top.get("dns1") or "")
    dns2 = str(top.get("dns2") or "")
    if dns1:
        conf_text = conf_text.replace("$PRIMARY_DNS", dns1)
    if dns2:
        conf_text = conf_text.replace("$SECONDARY_DNS", dns2)
    iface, peers = parse_conf(conf_text)
    peer = peers[0] if peers else {}

    conf_params = params_from_conf(conf_text)
    params = {}
    for k in PARAM_KEYS:
        v = last.get(k) or awg.get(k) or conf_params[k]
        params[k] = str(v).strip() if v is not None else ""
    if last.get("persistent_keep_alive"):
        params["PersistentKeepalive"] = str(last["persistent_keep_alive"])
    if last.get("mtu"):
        params["MTU"] = str(last["mtu"])
    params = normalize_params(params)

    ep_host, ep_port = _split_endpoint(peer.get("Endpoint", ""))
    host = str(top.get("hostName") or last.get("hostName") or ep_host)
    try:
        port = int(last.get("port") or awg.get("port") or ep_port or 0)
    except (TypeError, ValueError):
        port = 0
    subnet = ""
    if awg.get("subnet_address"):
        subnet = str(awg["subnet_address"])
        if awg.get("subnet_cidr"):
            subnet += "/" + str(awg["subnet_cidr"])
    return {
        "container": cont.get("container", ""),
        "line": detect_line(params),
        "protocol_version": str(awg.get("protocol_version") or ""),
        "hostName": host,
        "port": port,
        "description": str(top.get("description") or ""),
        "dns": [d for d in (dns1, dns2) if d],
        "subnet": subnet,
        "params": params,
        "conf_text": conf_text,
        "client": {
            "ip": str(last.get("client_ip") or iface.get("Address", "").split("/")[0]),
            "privkey": str(last.get("client_priv_key") or iface.get("PrivateKey", "")),
            "pubkey": str(last.get("client_pub_key") or ""),
            "psk": str(last.get("psk_key") or peer.get("PresharedKey") or ""),
        },
        "server": {
            "pubkey": str(last.get("server_pub_key") or peer.get("PublicKey", "")),
            "endpoint_host": host,
            "port": port,
        },
        "raw": top,
    }


_VPN_TOKEN_RE = re.compile(r"vpn://[A-Za-z0-9_\-=+/]+")
# Строка-продолжение ссылки, разбитой переносами (почта с переносом по 76 знакам, окно Telegram Desktop,
# редактор с жёстким переносом): только символы base64url, по краям — пробелы.
_VPN_CONT_RE = re.compile(r"[ \t]*([A-Za-z0-9_\-=+/]+)[ \t]*")
_VPN_DECODE_ERRORS = (AwgError, ValueError, TypeError, KeyError)


def _vpn_from_text(s):
    """Ссылка vpn:// из текста → словарь vpn_decode, или None, если ссылки нет.

    Сначала строгая ссылка одной строкой. Не разобралась (обрезок — первая строка ссылки, разбитой
    переносами) — дописываем к ней следующие строки из одних символов base64url по одной, пока не
    разберётся: раньше брался только первый кусок, и привязка отвечала «не удалось прочитать ключ», хотя
    ссылка была целиком (находка TK-7). По одной, а не все разом: подпись под ссылкой («Hello») тоже
    состоит из таких символов, и склейка с ней ломала бы и целую ссылку."""
    m = _VPN_TOKEN_RE.search(s)
    if not m:
        return None
    token = m.group(0)
    try:
        return vpn_decode(token)
    except _VPN_DECODE_ERRORS as first_err:
        rest = s[m.end():].lstrip(" \t")
        if not rest.startswith("\n"):
            raise first_err
        for line in rest[1:].split("\n")[:200]:
            c = _VPN_CONT_RE.fullmatch(line)
            if not c:
                break
            token += c.group(1)
            try:
                return vpn_decode(token)
            except _VPN_DECODE_ERRORS:
                continue
        raise first_err


def client_key(text):
    """Ключ клиента из того, что ему когда-то выдали: ссылка vpn:// или текст .conf.

    Нужен привязке старого ключа (подхват из приложения Amnezia): у сервера есть
    только открытый ключ пира, а приватный лежит в ссылке или файле, которые
    админ отправлял человеку. Ссылку ищем в тексте целиком — её обычно
    вставляют из переписки вместе с подписью. Открытый ключ считаем из
    приватного сами: поле client_pub_key в ссылке ничего не доказывает.
    → {privkey, pubkey, psk, server_pubkey, ip}."""
    # BOM снимаем и здесь: вставка из редактора, который его копирует, начинается с него же (TK-1).
    s = (text or "").replace("\r", "").replace("﻿", "")
    d = _vpn_from_text(s)
    if d is not None:
        priv = d["client"]["privkey"].strip()
        psk = d["client"]["psk"].strip()
        srv = d["server"]["pubkey"].strip()
        ip = d["client"]["ip"].strip()
    else:
        iface, peers = parse_conf(s)
        peer = peers[0] if peers else {}
        priv = str(iface.get("PrivateKey", "")).strip()
        psk = str(peer.get("PresharedKey", "")).strip()
        srv = str(peer.get("PublicKey", "")).strip()
        ip = str(iface.get("Address", "")).split(",")[0].split("/")[0].strip()
    if not priv or not is_key(priv):
        raise AwgError("no client private key: expected a vpn:// link or a .conf with [Interface] PrivateKey")
    return {"privkey": priv, "pubkey": pubkey(priv), "psk": psk if is_key(psk) else "",
            "server_pubkey": srv if is_key(srv) else "", "ip": ip}


# ---------------------------------------------------------------------------
# Чанки QR приложения Amnezia
# ---------------------------------------------------------------------------

def qr_chunks_from_bytes(compressed, chunk_size=QR_CHUNK_SIZE):
    if not compressed:
        raise AwgError("nothing to split into QR chunks")
    count = int(math.ceil(len(compressed) / float(chunk_size)))
    if count > QR_MAX_CHUNKS:
        raise AwgError("payload needs %d QR chunks, the app supports at most %d" % (count, QR_MAX_CHUNKS))
    chunks = []
    for i in range(count):
        piece = compressed[i * chunk_size:(i + 1) * chunk_size]
        header = struct.pack(">hBBI", QR_MAGIC, count, i, len(piece))
        chunks.append(b64url_encode(header + piece))
    return chunks


def qr_chunks(vpn):
    """Строки для QR: приложение сканирует их в любом порядке и собирает vpn:// обратно."""
    return qr_chunks_from_bytes(b64url_decode(_strip_vpn_prefix(vpn)))


def qr_chunks_join(chunks):
    """Обратная сборка (как parseQrCodeChunk в приложении) — для тестов и импорта."""
    parts = {}
    total = None
    for chunk in chunks:
        raw = b64url_decode(chunk)
        if len(raw) < 8:
            raise AwgError("QR chunk too short")
        magic, count, idx, length = struct.unpack(">hBBI", raw[:8])
        if magic != QR_MAGIC:
            raise AwgError("QR chunk has wrong magic %d" % magic)
        if total is None:
            total = count
        elif total != count:
            raise AwgError("QR chunks belong to different payloads")
        payload = raw[8:]
        if len(payload) != length:
            raise AwgError("QR chunk %d length mismatch" % idx)
        parts[idx] = payload
    if total is None or len(parts) != total or set(parts) != set(range(total)):
        raise AwgError("QR chunk set is incomplete")
    return "vpn://" + b64url_encode(b"".join(parts[i] for i in range(total)))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_stdin():
    data = sys.stdin.read()
    if not data.strip():
        raise AwgError("expected input on stdin")
    return data


def _read_json_stdin():
    try:
        obj = json.loads(_read_stdin())
    except json.JSONDecodeError as e:
        raise AwgError("stdin is not valid JSON: %s" % e)
    if not isinstance(obj, dict):
        raise AwgError("stdin must be a JSON object")
    return obj


def _read_params_stdin():
    """JSON params или текст .conf (удобно для validate/detect-line при импорте)."""
    data = _read_stdin()
    stripped = data.lstrip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as e:
            raise AwgError("stdin is not valid JSON: %s" % e)
        return normalize_params(obj)
    if "[Interface]" in data or "PrivateKey" in data:
        return params_from_conf(data)
    raise AwgError("stdin must be JSON params or a .conf text")


def _print_json(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _build_parser():
    ap = argparse.ArgumentParser(prog="awgkeys.py", description="goAmnezia pro: AmneziaWG parameters, keys and key formats")
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True

    p = sub.add_parser("gen-params", help="generate obfuscation parameters for a line")
    p.add_argument("--line", required=True, choices=LINES)
    p.add_argument("--preset", default="default", choices=PRESETS)

    p = sub.add_parser("validate", help="validate JSON params from stdin; exit 2 on problems")
    p.add_argument("--line", required=True, choices=LINES)

    sub.add_parser("genkey", help="new X25519 private key (base64)")
    p = sub.add_parser("pubkey", help="public key for a private key ('-' = read stdin)")
    p.add_argument("privkey")
    sub.add_parser("genpsk", help="new preshared key (base64)")

    p = sub.add_parser("conf", help="client .conf from JSON on stdin")
    p.add_argument("--line", required=True, choices=LINES)
    p = sub.add_parser("mihomo", help="mihomo (Clash Meta) profile with one proxy from the conf JSON on stdin")
    p.add_argument("--line", required=True, choices=LINES)
    p.add_argument("--name", default="", help="proxy name (default: AmneziaWG <line>)")
    p = sub.add_parser("server-conf", help="server awg0.conf from JSON on stdin")
    p.add_argument("--line", required=True, choices=LINES)

    sub.add_parser("vpn-encode", help="vpn:// link from JSON on stdin")
    # «-» — ссылку читать из stdin: в ней приватный ключ клиента, а argv виден любому локальному
    # пользователю в /proc/*/cmdline (находка SR-17). Ядро зовёт только так; аргумент — для тестов и руками.
    p = sub.add_parser("vpn-decode", help="JSON from a vpn:// link ('-' = read stdin)")
    p.add_argument("vpn")
    p = sub.add_parser("qr-chunks", help="Amnezia app QR chunks for a vpn:// link ('-' = read stdin)")
    p.add_argument("vpn")
    sub.add_parser("client-key", help="client keys from a vpn:// link or a client .conf on stdin -> JSON (contains the private key)")
    sub.add_parser("detect-line", help="detect AmneziaWG line from JSON params or .conf on stdin")
    p = sub.add_parser("parse-conf", help="server awg0.conf/wg0.conf from stdin -> JSON (line, params, subnet, port, peers); secrets only with --with-secrets")
    p.add_argument("--with-secrets", action="store_true", help="include the server private key and peer preshared keys")
    return ap


def parse_server_conf(text, with_secrets=False):
    """Серверный конфиг контейнера (awg0.conf приложения или наш) → словарь для импорта.

    Без with_secrets приватный ключ сервера и PSK пиров в выводе отсутствуют:
    import-scan показывает результат человеку и агенту, а секреты нужны только
    самому подхвату (import-adopt), который пишет их в state/ с правами 0600.
    Публичный ключ сервера считается из приватного — файла *.key у чужого
    контейнера может и не быть.
    """
    iface, peers = parse_conf(text)
    params = params_from_conf(text)
    address = iface.get("Address", "").split(",")[0].strip()
    port = iface.get("ListenPort", "").strip()
    priv = iface.get("PrivateKey", "").strip()
    pub = pubkey(priv) if is_key(priv) else ""
    out_peers = []
    for peer in peers:
        allowed = peer.get("AllowedIPs", "").strip()
        ip = allowed.split(",")[0].strip().split("/")[0] if allowed else ""
        item = {"pubkey": peer.get("PublicKey", "").strip(), "allowed_ips": allowed, "ip": ip,
                "psk_present": bool(peer.get("PresharedKey", "").strip())}
        if with_secrets:
            item["psk"] = peer.get("PresharedKey", "").strip()
        out_peers.append(item)
    out = {"line": detect_line(params), "params": params, "address": address,
           "subnet": address if "/" in address else "", "listen_port": int(port) if port.isdigit() else 0,
           "server_pubkey": pub, "server_privkey_present": bool(priv), "peers": out_peers}
    if with_secrets:
        out["server_privkey"] = priv
    return out


def _run(args):
    if args.cmd == "gen-params":
        _print_json(gen_params(args.line, args.preset))
    elif args.cmd == "validate":
        errs = validate_params(args.line, _read_params_stdin())
        if errs:
            for e in errs:
                sys.stderr.write("awgkeys: validate: %s\n" % e)
            return 2
    elif args.cmd == "genkey":
        sys.stdout.write(genkey() + "\n")
    elif args.cmd == "pubkey":
        priv = _read_stdin().strip() if args.privkey == "-" else args.privkey
        sys.stdout.write(pubkey(priv) + "\n")
    elif args.cmd == "genpsk":
        sys.stdout.write(genpsk() + "\n")
    elif args.cmd == "conf":
        sys.stdout.write(render_conf(args.line, _read_json_stdin()))
    elif args.cmd == "mihomo":
        name = args.name.strip() or "AmneziaWG %s" % args.line
        sys.stdout.write(mihomo_profile([mihomo_proxy(args.line, _read_json_stdin(), name)]))
    elif args.cmd == "server-conf":
        sys.stdout.write(render_server_conf(args.line, _read_json_stdin()))
    elif args.cmd == "vpn-encode":
        sys.stdout.write(vpn_encode(_read_json_stdin()) + "\n")
    elif args.cmd == "vpn-decode":
        _print_json(vpn_decode(_read_stdin().strip() if args.vpn == "-" else args.vpn))
    elif args.cmd == "qr-chunks":
        _print_json(qr_chunks(_read_stdin().strip() if args.vpn == "-" else args.vpn))
    elif args.cmd == "client-key":
        _print_json(client_key(_read_stdin()))
    elif args.cmd == "detect-line":
        sys.stdout.write(detect_line(_read_params_stdin()) + "\n")
    elif args.cmd == "parse-conf":
        _print_json(parse_server_conf(_read_stdin(), with_secrets=args.with_secrets))
    else:  # pragma: no cover — argparse не пустит сюда
        raise AwgError("unknown command")
    return 0


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        return _run(args)
    except (AwgError, ValueError, TypeError, KeyError) as e:
        # Любой кривой вход — код 2 и одна строка в stderr, без трейсбека: обёртки
        # и бот показывают это сообщение пользователю.
        sys.stderr.write("awgkeys: %s: %s\n" % (args.cmd, e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
