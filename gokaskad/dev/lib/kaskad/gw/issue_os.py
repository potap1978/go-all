"""What to offer a device first, by platform (docs/k3/SPEC-wave2-gateway.md §4), and which AmneziaWG
apps understand which line (vendor/clients.json, the goAmnezia pro catalog).

    offers(platform, available=None) -> [{"inbound", "format", "note"}]   matrix order, first = best
    first(platform, available)       -> the best offer the device can use now, or None
    plan(st, device, os=None)        -> {"platform", "offers", "missing", "notes"} for one device
    apps(platform, line)             -> [{"id", "name", "official", "min", "imports"}]
    apps_note(platform, line)        -> one short line for the issuance screen
    howto(platform, kind, files, where) -> up to 4 steps for a beginner: the app and where to get it, what to copy,
                                        how to bring it to the device, what to press; for a built-in client set
                                        up by a file — save it, run it, connect ([] — the items explain it)
    vless_apps(platform) / vless_apps_note(platform) -> the apps of VLESS Reality there (v2RayTun, Hiddify, …)

Inbound kinds of work package 6 (l2tp, pptp, sstp, ikev2) are in the matrix too: this module only says
what to offer; each adapter's issue() builds the files. Texts are short and in Russian.

Built-in clients and routers (work package 6):
    steps(platform, kind, where) -> [lines] where to click and what to type, or [] (routers, iOS/macOS, Linux)
    builtin_gone(platform, kind) -> why this system has no built-in client for the inbound, or ""
    plain_l2tp_only(platform)    -> True for routers whose L2TP client has no IPsec (Huawei, Xiaomi, ASUS)
    plain_off_note()             -> «this router needs L2TP without encryption, and the node has it off»

Where the words are read (``where``, WHERE): the SSH menu — the default; it has its own action «Сохранить в файлы»
and a terminal to copy from; the web panel — the buttons «Скопировать» and «Скачать <файл>»; the person's page —
«Скопировать» and «Скачать файл»; the Telegram bot — a link comes as a message (a tap copies it), a file as a
document. The flows are the same, the words are those of the screen (stand T-G1b, N4). ``ops.issue(where=…)``
passes it on: ``howto`` gets it as an argument, the guides the adapters build (``steps``, ``plain_off_note``) take it
from ``issued_for`` — the adapters do not need to know who reads.
"""


import contextlib
import contextvars
import re

from collections.abc import Mapping

from kaskad.i18n import tr
from kaskad.vendor import clients

PLATFORMS = ("ios", "android", "androidtv", "windows", "macos", "linux", "keenetic", "mikrotik",
             "openwrt", "tplink", "huawei", "xiaomi", "asus", "other")
ALIASES = {"android_tv": "androidtv", "iphone": "ios", "ipad": "ios", "ipados": "ios", "mac": "macos",
           "osx": "macos", "win": "windows", "netcraze": "keenetic", "routeros": "mikrotik", "tp-link": "tplink"}
AWG_LINES = {"awg1": "1.5", "awg2": "2.0", "awg3": "3.1"}
TITLES = {"wg": "WireGuard", "awg1": "AmneziaWG 1.5", "awg2": "AmneziaWG 2.0", "awg3": "AmneziaWG 3.1",
          "l2tp": "L2TP/IPsec", "pptp": "PPTP", "sstp": "SSTP", "ikev2": "IKEv2", "openvpn": "OpenVPN",
          "ocserv": "OpenConnect", "socks5": "SOCKS5", "vless": "VLESS", "hy2": "Hysteria2"}

# The name of issued settings in the apps (owner, 3.3.2: AmneziaVPN showed a Kaskad key as «Server 1», goAmnezia's keys —
# by their name): brand + protocol, the same in every language — the description of vpn://, the «#…» of a VLESS link,
# the title of the subscription. A .conf names its tunnel by the file name (owner, 2026-10-11, OWNER-FEEDBACK-3.3 №31,
# replaces goKaskad-awg31 of №18): the device's name in Latin letters — iPhone/iPad, macOS, Windows (≤ 32) and
# routers «goKaskadPro-telefon-mamy.conf»; Android and Linux (the tunnel name ≤ 15) «gKP-telefon-mam.conf»; only
# [A-Za-z0-9_.-] (FIX-332g №7: «+» and «=» are not in the name of a file the person's page /p/ lets download —
# userpage.FILE_NAME_RE); a device with several protocols that come as a file — the protocol's suffix (-awg31, -wg),
# the name shortened to make room for it. On Android and Linux the platform's word goes first («Мама · Android» —
# «mama»), and a name cut to fit ends with two characters of the device's id: «Телефон мамы» and «Телефон папы» are
# «gKP-telefon-3f.conf» and «gKP-telefon-a1.conf», not one «gKP-telefon-mam» (FIX-332g №16). Keys issued before keep
# their names (nothing renames them).
BRAND = "goKaskad PRO"
CONF_BASE = "goKaskadPro"
CONF_SHORT = "gKP"
CONF_LONG_MAX, CONF_SHORT_MAX = 32, 15
SHORT_NAME_PLATFORMS = ("android", "androidtv", "linux", "other")      # «other»: unknown — the safe length
CONF_KINDS = ("wg", "awg1", "awg2", "awg3")                             # the protocols issued as a .conf
_CONF_CODE = {"1.0": "awg10", "1.5": "awg15", "2.0": "awg20", "3.1": "awg31"}
_CONF_BAD = re.compile(r"[^A-Za-z0-9_.-]+")
_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґў",   # i18n: skip — transliteration of names, not words
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya", "i", "yi", "ye", "g",
                      "u"]))


def _platform_titles():
    """Every platform's title in every language of the wizard (setup.product.PLATFORM_TITLES): «iPhone и iPad»,
    «iPhone / iPad», «Android TV», «Другое»…"""
    from kaskad.setup import product                     # late: the gateway does not load the wizard at import
    out = set()
    for key in product.PLATFORM_TITLES.values():
        for lang in ("ru", "en"):
            out.add(tr(key, lang) if key.startswith("setup.product.") else key)
    return out


def device_title(device_name):
    """The device's own name without the platform the wizard adds to it («Мой телефон · Android TV» — «Мой телефон»,
    setup d.devName «{who} · {p}»; owner 3.3.3: the platform is not a part of a key's name). Only a known title of a
    platform after the last « · » goes; a name that is all platform stays."""
    name = " ".join(str(device_name or "").split())
    head, sep, tail = name.rpartition(" · ")
    if sep and head.strip() and tail in _platform_titles():
        return head.strip()
    return name


def app_label(title, device_name=None):
    """«goKaskad PRO · AmneziaWG 3.1 · iPhone Маши» — the name of issued settings in an app (``title`` — the protocol's
    name; the device — owner, T-M6-332c №9: two devices of one person told apart in the app and the subscription;
    without the platform's word the wizard adds, 3.3.3 — device_title; give.js keyName is the same)."""
    name = device_title(device_name)
    return "%s · %s · %s" % (BRAND, title, name) if name else "%s · %s" % (BRAND, title)


def conf_code(kind, line=None):
    """«wg», «awg31», «awg20»… — the protocol's part of a .conf name."""
    return "wg" if kind == "wg" else _CONF_CODE.get(str(line or AWG_LINES.get(kind, "")), kind)


def translit(name):
    """The device's name in [A-Za-z0-9_.-]: Cyrillic in Latin letters (lower case), the rest — «-», no doubles."""
    out = _CONF_BAD.sub("-", "".join(_TRANSLIT.get(c.lower(), c) for c in str(name or "").strip()))
    return re.sub(r"-{2,}", "-", out).strip("-._")


def conf_filename(kind, line=None, device=None, platform_name=None):
    """The .conf of WireGuard / AmneziaWG (``line`` — the AmneziaWG line) for ``device`` on ``platform_name``:
    «goKaskadPro-telefon-mamy.conf», «gKP-telefon-mam.conf», «gKP-tel-awg31.conf». The name without .conf is the
    tunnel's name in the apps: ≤ 15 characters on Android and Linux, ≤ 32 elsewhere. No device — «goKaskadPro-awg31»."""
    p = platform(platform_name or (device or {}).get("platform"))
    short = bool(platform_name or device) and p in SHORT_NAME_PLATFORMS
    prefix, limit = (CONF_SHORT, CONF_SHORT_MAX) if short else (CONF_BASE, CONF_LONG_MAX)
    code = conf_code(kind, line)
    # the tunnel's name in the app is the key's name: no platform the wizard adds (owner 3.3.3, «goKaskadPro-<имя
    # устройства>»; R-333-qr ≤2: «Маша · iPhone и iPad» — «goKaskadPro-masha.conf», as «Появится как» says)
    name = translit(device_title((device or {}).get("name")))
    if short:
        name = _drop_platform_word(name)
    if not name:
        return "%s-%s.conf" % (prefix, code)
    did = (device or {}).get("id")
    files = [k for k in (device or {}).get("access") or [] if k in CONF_KINDS]
    if len(files) > 1:                                   # several .conf for one device: the protocol tells them apart
        room = limit - len(prefix) - len(code) - 2
        if room >= 1:
            stem = _fit(name, room, did) if short else (name[:room].rstrip("-._") or name[:1])
            return "%s-%s-%s.conf" % (prefix, stem, code)
    if short:
        return "%s-%s.conf" % (prefix, _fit(name, limit - len(prefix) - 1, did))
    return "%s.conf" % ("%s-%s" % (prefix, name))[:limit].rstrip("-._")


# the platforms' words in a name (the wizard calls a device «Мама · Android»): on Android and Linux 15 characters are
# too few to spend on them
_PLATFORM_WORD = re.compile(r"(?i)(?:^|[-._])(?:android[-._]?tv|android|linux)(?=[-._]|$)")


def _drop_platform_word(name):
    out = re.sub(r"-{2,}", "-", _PLATFORM_WORD.sub("", name)).strip("-._")
    return out or name


def _fit(name, room, did):
    """``name`` in ``room`` characters; a cut one ends with two characters of the device's id (the same start of two
    names stays two names). No id — just cut."""
    if len(name) <= room:
        return name
    tail = re.sub(r"[^a-z0-9]", "", str(did or "").lower())[-2:]
    if len(tail) == 2 and room >= 4:
        return "%s-%s" % (name[:room - 3].rstrip("-._") or name[:1], tail)
    return name[:room].rstrip("-._") or name[:1]


# The words of this module are keys of the catalog (kaskad/i18n/catalog/*/gw_issue.json): tr() turns them into the
# language of the reader at the moment the text goes out (the panel and the bot set it per request) — never at import.
_K = "gw_issue."


class _Lazy(Mapping):
    """{name: [keys]} that answers [texts] in the language of the moment (tr at every lookup, never at import)."""

    def __init__(self, keys):
        self._keys = keys

    def __getitem__(self, name):
        return [tr(k) for k in self._keys[name]]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)


# (inbound, format, note key) — in the order to offer. AmneziaWG lines go newest first: an app that knows
# 3.1 gets 3.1, a server without awg3 offers 2.0 and so on.
MATRIX = {
    "ios": [
        ("awg3", "vpn", _K + "offer.awg_vpn_qr"),
        ("awg2", "vpn", _K + "offer.awg_vpn_qr"),
        ("awg1", "vpn", _K + "offer.awg_vpn_qr"),
        ("vless", "link", _K + "offer.vless_ios"),
        ("hy2", "link", _K + "offer.hy2"),
        ("ikev2", "mobileconfig", _K + "offer.ikev2_profile_noapp"),
        ("wg", "qr", _K + "offer.wg_qr"),
        ("ocserv", "fields", _K + "offer.ocserv_cisco"),
    ],
    "macos": [
        ("awg3", "vpn", _K + "offer.awg_vpn_conf"),
        ("awg2", "vpn", _K + "offer.awg_vpn_conf"),
        ("awg1", "vpn", _K + "offer.awg_vpn_conf"),
        ("vless", "link", _K + "offer.vless_macos"),
        ("hy2", "link", _K + "offer.hy2"),
        ("ikev2", "mobileconfig", _K + "offer.ikev2_profile"),
        ("wg", "conf", _K + "offer.wg_conf_app"),
        ("ocserv", "fields", _K + "offer.ocserv_gui"),
    ],
    "android": [
        ("awg3", "vpn", _K + "offer.awg_vpn"),
        ("awg2", "vpn", _K + "offer.awg_vpn"),
        ("awg1", "vpn", _K + "offer.awg_vpn"),
        ("vless", "link", _K + "offer.vless_android"),
        ("hy2", "link", _K + "offer.hy2"),
        ("wg", "qr", _K + "offer.wg_qr"),
        ("ikev2", "fields", _K + "offer.ikev2_android"),
        ("openvpn", "ovpn", _K + "offer.openvpn_connect"),
    ],
    "androidtv": [
        ("awg3", "vpn", _K + "offer.awg_tv"),
        ("awg2", "vpn", _K + "offer.awg_tv"),
        ("awg1", "vpn", _K + "offer.awg_tv"),
        ("vless", "link", _K + "offer.vless_tv"),
    ],
    "windows": [
        ("awg3", "vpn", _K + "offer.awg_windows"),
        ("awg2", "vpn", _K + "offer.awg_windows"),
        ("awg1", "vpn", _K + "offer.awg_windows"),
        ("vless", "link", _K + "offer.vless_desktop"),
        ("hy2", "link", _K + "offer.hy2"),
        ("ikev2", "ps1", _K + "offer.builtin_ps"),
        ("sstp", "ps1", _K + "offer.builtin_ps"),
        ("l2tp", "ps1", _K + "offer.builtin_ps"),
        ("openvpn", "ovpn", _K + "offer.openvpn_windows"),
        ("ocserv", "fields", _K + "offer.ocserv_gui"),
    ],
    "linux": [
        ("wg", "conf", _K + "offer.wg_quick"),
        ("awg3", "conf", _K + "offer.awg3_linux"),
        ("awg2", "conf", _K + "offer.awg2_linux"),
        ("awg1", "conf", _K + "offer.awg1_linux"),
        ("vless", "link", _K + "offer.vless_desktop"),
        ("hy2", "link", _K + "offer.hy2"),
        ("openvpn", "ovpn", _K + "offer.openvpn_linux"),
        ("ocserv", "guide", _K + "offer.ocserv_linux"),
        ("socks5", "link", _K + "offer.socks5_apps"),
    ],
    "keenetic": [
        ("awg2", "conf", _K + "offer.keenetic_awg2"),
        ("awg1", "conf", _K + "offer.keenetic_awg1"),
        ("wg", "conf", _K + "offer.keenetic_wg"),
        ("l2tp", "guide", _K + "offer.keenetic_l2tp"),
    ],
    "mikrotik": [
        ("wg", "conf", _K + "offer.mikrotik_wg"),
        ("l2tp", "guide", _K + "offer.mikrotik_l2tp"),
        ("sstp", "guide", _K + "offer.mikrotik_sstp"),
        ("openvpn", "ovpn", _K + "offer.openvpn_mikrotik"),
    ],
    "openwrt": [
        ("awg2", "conf", _K + "offer.openwrt_awg"),
        ("awg3", "conf", _K + "offer.openwrt_awg3"),
        ("awg1", "conf", _K + "offer.openwrt_awg"),
        ("wg", "conf", _K + "offer.openwrt_wg"),
        ("openvpn", "ovpn", _K + "offer.openvpn_openwrt"),
    ],
    "tplink": [
        ("l2tp", "guide", _K + "offer.l2tp_old_models"),
        ("pptp", "guide", _K + "offer.pptp_if_on"),
    ],
    "huawei": [
        ("l2tp", "guide", _K + "offer.l2tp_old_models"),
        ("pptp", "guide", _K + "offer.pptp_if_on"),
    ],
    "xiaomi": [
        ("l2tp", "guide", _K + "offer.l2tp_old_models"),
        ("pptp", "guide", _K + "offer.pptp_if_on"),
    ],
    "asus": [
        ("l2tp", "guide", _K + "offer.l2tp_old_models"),
        ("openvpn", "ovpn", _K + "offer.openvpn_asus"),
        ("pptp", "guide", _K + "offer.pptp_if_on"),
    ],
    "other": [
        ("wg", "conf", _K + "offer.wg_any"),
        ("awg3", "vpn", _K + "offer.awg_vpn"),
        ("awg2", "vpn", _K + "offer.awg_vpn"),
        ("awg1", "conf", _K + "offer.awg_conf"),
        ("vless", "link", _K + "offer.vless_any"),
        ("hy2", "link", _K + "offer.hy2"),
        ("openvpn", "ovpn", _K + "offer.openvpn_file"),
        ("socks5", "link", _K + "offer.socks5"),
    ],
}

# platform -> [notes] in the language of the moment (ops.offers, plan)
NOTES = _Lazy({
    "ios": [_K + "note.ios"],
    "androidtv": [_K + "note.androidtv"],
    "keenetic": [_K + "note.keenetic"],
    "mikrotik": [_K + "note.mikrotik"],
    "openwrt": [_K + "note.openwrt"],
    "tplink": [_K + "note.tplink"],
    "huawei": [_K + "note.ppp_routers"],
    "xiaomi": [_K + "note.ppp_routers"],
})

# our platform -> (catalog platform, catalog app ids or None = every app)
_CATALOG = {
    "ios": ("ios", None), "android": ("android", None), "androidtv": ("android_tv", None),
    "windows": ("windows", None), "macos": ("macos", None), "linux": ("linux", None),
    "keenetic": ("router", ("keenetic",)), "openwrt": ("router", ("openwrt",)),
    "mikrotik": ("router", ("mikrotik",)),
}
_IMPORTS = (("vpn", "vpn://"), ("qr-vpn", "QR"), ("conf", ".conf"), ("qr-conf", "QR .conf"))
# catalog app id -> key of its name: the names of clients.json with words in them («MikroTik (контейнер)»)
_APP_NAMES = {"mikrotik": _K + "app.mikrotik"}


def platform(value):
    p = str(value or "").strip().lower()
    p = ALIASES.get(p, p)
    return p if p in MATRIX else "other"


def platform_of(device, os=None):
    return platform(os or (device or {}).get("platform"))


def offers(platform_name, available=None):
    """Matrix rows for a platform, best first; only kinds in ``available`` when it is given."""
    rows = MATRIX[platform(platform_name)]
    out = []
    for kind, fmt, note in rows:
        if available is not None and kind not in available:
            continue
        out.append({"inbound": kind, "format": fmt, "note": tr(note)})
    return out


def first(platform_name, available):
    rows = offers(platform_name, available)
    return rows[0] if rows else None


def usable(st, device):
    """Inbounds the device can use now: in its access, installed and enabled on the node."""
    inbounds = ((st or {}).get("gateway") or {}).get("inbounds") or {}
    out = []
    for kind in device.get("access") or []:
        cfg = inbounds.get(kind)
        if isinstance(cfg, dict) and cfg.get("enabled", True) is not False and kind not in out:
            out.append(kind)
    return out


def plan(st, device, os=None):
    """For one device: what it can use now (best first) and what the matrix would suggest adding."""
    p = platform_of(device, os)
    avail = usable(st, device)
    have = offers(p, avail)
    ordered = [o["inbound"] for o in have]
    for kind in avail:                       # granted, but the matrix does not list it for this platform
        if kind not in ordered:
            have.append({"inbound": kind, "format": "", "note": ""})
    missing = [o for o in offers(p) if o["inbound"] not in avail]
    return {"platform": p, "offers": have, "missing": missing, "notes": list(NOTES.get(p, []))}


def apps(platform_name, line):
    """AmneziaWG apps that import keys of ``line`` on this platform, official first."""
    p = platform(platform_name)
    if p not in _CATALOG:
        return []
    cat_platform, ids = _CATALOG[p]
    out = []
    for app in clients().get("apps") or []:
        if app.get("static") is False or (ids is not None and app.get("id") not in ids):
            continue
        info = (app.get("platforms") or {}).get(cat_platform)
        if not info:
            continue
        support = str((info.get("support") or {}).get(line) or "")
        if support in ("", "no", "unknown"):
            continue
        imp = app.get("import") or {}
        name = tr(_APP_NAMES[app.get("id")]) if app.get("id") in _APP_NAMES else app.get("name")
        out.append({"id": app.get("id"), "name": name,
                    "official": bool(app.get("official")), "min": "" if support == "yes" else support,
                    "imports": [label for key, label in _IMPORTS if imp.get(key)]})
    out.sort(key=lambda a: not a["official"])
    return out


def apps_note(platform_name, line, limit=3):
    """"Приложения: AmneziaVPN 5.0.1+ (vpn://, QR), AmneziaWG 3.1.4+ (.conf, QR .conf)" or ""."""
    parts = []
    for a in apps(platform_name, line)[:limit]:
        s = a["name"] + (" %s+" % a["min"] if a["min"] else "")
        if a["imports"]:
            s += " (%s)" % ", ".join(a["imports"])
        parts.append(s)
    return tr(_K + "apps", apps=", ".join(parts)) if parts else ""


# ── the first screen of an issuance: four steps for a beginner ─────────────

WHERE = ("menu", "panel", "page", "bot", "agent")
_where = contextvars.ContextVar("kaskad_issue_where", default="menu")


def audience(where=None):
    """Who reads the steps: ``where`` when it is one of WHERE, else the one ``issued_for`` set, else the SSH menu."""
    return where if where in WHERE else _where.get()


@contextlib.contextmanager
def issued_for(where):
    """Within the block ``steps()``, ``plain_off_note()`` and ``howto()`` speak the words of ``where`` (``ops.issue``
    sets it around the adapter's issue: its guides need no parameter)."""
    token = _where.set(where if where in WHERE else "menu")
    try:
        yield
    finally:
        _where.reset(token)


# Where to get the app the link goes into: (app, key of where). The versions are those of clients.json.
_AWG_APP = {
    "android": ("AmneziaVPN", _K + "from.awg_android"),
    "androidtv": ("AmneziaVPN", _K + "from.tv_google_play"),
    "ios": ("DefaultVPN", _K + "from.awg_ios"),
    "windows": ("AmneziaVPN", _K + "from.awg_windows"),
    "macos": ("AmneziaVPN", _K + "from.awg_site"),
    "other": ("AmneziaVPN", _K + "from.awg_other"),
}
# Apps whose labels no source confirms (DefaultVPN: an AmneziaVPN fork, only English declared in the App Store) —
# the paste step without quoted labels (docs/k3/GLOSSARY-en.md, third-party labels).
_AWG_APP_NEUTRAL = ("DefaultVPN",)
_TARGET = {"android": _K + "target.phone", "ios": _K + "target.phone", "androidtv": _K + "target.tv",
           "windows": _K + "target.computer", "macos": _K + "target.computer", "other": _K + "target.device"}
# The same steps in the words of each screen (keys by ``where``): a link — selected with the mouse in the menu, the
# button «Скопировать» of the panel and of the page, a tap on the message of the bot; a file — «Сохранить в файлы»
# of the menu, «Скачать <файл>» of the panel, «Скачать файл» of the page, a document of the bot; the MCP agent
# («agent») — neutral words: no buttons, the text comes in its answer.
_COPY_AT = {w: _K + "copy." + w for w in WHERE}                 # {scheme}: vpn:// or vless://
_SEND_AT = {"menu": _K + "send.default", "panel": _K + "send.default", "page": _K + "send.page",
            "bot": _K + "send.bot", "agent": _K + "send.default"}
_FILE_AT = {w: _K + "get." + w for w in WHERE if w != "menu"}
_QR_AT = {w: _K + "qr." + w for w in WHERE}   # a phone's QR: the menu draws it in the terminal, the others show it
_COPY_CONF_AT = {"menu": _K + "copy_conf.text", "panel": _K + "copy_conf.button", "page": _K + "copy_conf.button",
                 "agent": _K + "copy_conf.text"}
_IOS_MOVE_AT = {"menu": _K + "ios_move.menu", "panel": _K + "ios_move.menu", "page": _K + "ios_move.page",
                "bot": _K + "ios_move.bot", "agent": _K + "ios_move.agent"}
_OVPN_MOVE_AT = {"menu": _K + "ovpn_move.menu", "panel": _K + "ovpn_move.menu", "page": _K + "ovpn_move.page",
                 "bot": _K + "ovpn_move.bot", "agent": _K + "ovpn_move.agent"}
# The connection the inbound's file adds — the PowerShell script on Windows (native.windows_*), the .mobileconfig on
# iOS and macOS (ikev2.NAME): the name the steps point at.
_CONN_NAME = {"ikev2": "Kaskad IKEv2", "sstp": "Kaskad SSTP", "l2tp": "Kaskad L2TP", "pptp": "Kaskad PPTP"}
# MikroTik: a RouterOS 7 script kaskad-<kind>.rsc makes the interface kaskad-<kind> (wg.py, accel.py).
_ROUTEROS = ("wg", "l2tp", "sstp", "pptp")


def _target(p):
    return tr(_TARGET[p])


def _get(w, what=None):
    """«Сохраните файл: «Сохранить в файлы»…» of the menu, «Скачайте файл кнопкой «Скачать» ниже.» of the panel…
    ``what`` — the words of the thing (``tr(gw_issue.what.*)``), None — «файл»."""
    if w == "menu":
        return tr(_K + "get.menu_file") if what is None else tr(_K + "get.menu", what=what)
    return tr(_FILE_AT[w], what=tr(_K + "what.file") if what is None else what)


def _routeros_steps(w, script, iface):
    net = tr(_K + "ros.net")
    done = tr(_K + "ros.done", iface=iface)
    if w == "menu":
        return [tr(_K + "ros.copy_menu") + " " + net, tr(_K + "ros.paste_menu", script=script), done]
    if w in ("bot", "agent"):
        return [_get(w, tr(_K + "what.script", name=script)) + " " + net,
                tr(_K + "ros.import_file", script=script), done]
    button = tr(_K + "button.download") if w == "panel" else tr(_K + "button.download_file")
    return [tr(_K + "ros.copy_button") + " " + net, tr(_K + "ros.paste_button", button=button, script=script), done]


def _builtin_steps(p, kind, w="menu"):
    """(file the steps need, steps) of a built-in client set up by a file: the PowerShell script on Windows, the
    .mobileconfig on iOS and macOS, the RouterOS script on MikroTik; (None, []) — none of these."""
    if p == "windows" and kind in _CONN_NAME:
        script = "kaskad-%s.ps1" % kind
        return script, [_get(w, tr(_K + "what.script", name=script)),
                        tr(_K + "win.terminal"),
                        tr(_K + "win.run", script=script),
                        tr(_K + "win.connect", name=_CONN_NAME[kind])]
    if p in ("ios", "macos") and kind == "ikev2":
        profile = "kaskad-ikev2.mobileconfig"
        if p == "ios":
            return profile, [_get(w, tr(_K + "what.profile", name=profile)), tr(_IOS_MOVE_AT[w]),
                             tr(_K + "ios.install"),
                             tr(_K + "ios.turn_on", name=_CONN_NAME[kind])]
        return profile, [_get(w, tr(_K + "what.profile", name=profile)),
                         tr(_K + "mac.install"),
                         tr(_K + "mac.turn_on", name=_CONN_NAME[kind])]
    if p == "mikrotik" and kind in _ROUTEROS:
        script, iface = "kaskad-%s.rsc" % kind, "kaskad-%s" % kind
        return script, _routeros_steps(w, script, iface)
    return None, []


def _wg_connect(p):
    """WireGuard Windows «&Activate» is «Подключить» in Russian; the macOS app calls the same button «Подключен»
    (wireguard-apple ru.lproj) — the Russian step there is neutral."""
    return _K + ("howto.wg_connect_macos" if p == "macos" else "howto.wg_connect")


def _min_version(platform_name, line, app_name):
    for a in apps(platform_name, line):
        if a["name"] == app_name and a["min"]:
            return " " + tr(_K + "or_newer", ver=a["min"])
    return ""


def _install(app, where_key, ver=""):
    """«Поставьте приложение <app><ver> <where>.»"""
    return tr(_K + "howto.install", app=app, ver=ver, where=tr(where_key))


def howto(platform_name, kind, files=None, where=None):
    """Up to four steps for a beginner, in the order of the issuance screen: which app and where to get it,
    what to copy below, how to bring it to the device, what to press there; for a built-in client set up by a
    file (PowerShell script, .mobileconfig, RouterOS script) — how to save it, run it and connect. [] when the
    items explain themselves (built-in clients with their own guide, Linux) — the menu then shows the items at
    once. ``files`` — the file names of this issuance: steps around a file it has not got (Windows L2TP without
    IPsec has no script) are not given; None — not checked. ``where`` — whose words (WHERE; the SSH menu by
    default, or the one ``issued_for`` set): the same steps, the actions of that screen."""
    w = audience(where)
    p = platform(platform_name)
    need, given = _builtin_steps(p, kind, w)
    if need:
        return given if files is None or need in files else []
    if kind in AWG_LINES:
        if p in _AWG_APP:
            app, where_app = _AWG_APP[p]
            ver = _min_version(p, AWG_LINES[kind], app)
            return [_install(app, where_app, ver),
                    tr(_COPY_AT[w], scheme="vpn://"),
                    tr(_SEND_AT[w], target=_target(p)),
                    tr(_K + ("howto.awg_paste_neutral" if app in _AWG_APP_NEUTRAL else "howto.awg_paste"), app=app)]
        if p in ("keenetic", "openwrt"):
            where_ui = tr(_K + "howto.ui_keenetic") if p == "keenetic" else tr(_K + "howto.ui_openwrt")
            return [_get(w), tr(_K + "howto.open_ui", where=where_ui), tr(_K + "howto.router_check")]
        return []
    if kind == "wg":
        if p in ("android", "ios"):
            store = _K + "from.google_play" if p == "android" else _K + "from.app_store"
            return [_install("WireGuard", store),
                    tr(_K + "howto.wg_scan_" + p),      # Android «Scan from QR code», iOS «Create from QR code»
                    tr(_QR_AT[w]),
                    tr(_K + "howto.wg_name")]
        if p in ("windows", "macos"):
            store = tr(_K + "from.wireguard_site") if p == "windows" else tr(_K + "from.app_store")
            if w == "bot":                      # the .conf comes as a document: imported as a file, not pasted
                menu = tr(_K + "howto.wg_import_windows") if p == "windows" else tr(_K + "howto.wg_import_macos")
                return [tr(_K + "howto.install_wg", where=store),
                        _get(w, tr(_K + "what.named_file", name=(files or [conf_filename("wg")])[0])),
                        tr(_K + "howto.wg_import", menu=menu), tr(_wg_connect(p))]
            return [tr(_K + "howto.install_wg", where=store),
                    tr(_COPY_CONF_AT[w]),
                    tr(_K + "howto.wg_paste_" + p),     # Windows «Add Tunnel → Add empty tunnel…», macOS «Add Empty Tunnel…»
                    tr(_wg_connect(p))]
        return []
    if kind == "vless":
        return _vless_steps(p, w)
    if kind == "hy2":
        return _hy2_steps(p, w)
    if kind == "openvpn" and p in ("android", "ios", "windows", "macos"):
        where_app = {"android": _K + "from.google_play", "ios": _K + "from.app_store"}.get(p, _K + "from.openvpn_site")
        return [_install("OpenVPN Connect", where_app), _get(w),
                tr(_OVPN_MOVE_AT[w], target=_target(p)),
                tr(_K + "howto.ovpn_connect")]
    return []


# ── VLESS Reality (docs/k3/SPEC-vless-inbound.md §8) ───────────────────────

# Apps that import a vless:// link with Reality and flow xtls-rprx-vision, by platform: (app of the steps, key of where
# to get it, the others). Free apps first; the names are those of the stores (checked 2026-10).
_VLESS_APP = {
    # Первым — то, что ставится в России (k3/research/ios-apps-ru-2026-10.md, platforms-2026-10.md; владелец 2026-10-11):
    # iPhone и Mac — Karing (есть в российском App Store; Streisand и Hiddify там нет), ПК — v2rayN (Hiddify стоит
    # с 03.2026), Android — v2RayTun. Совпадает с первым приложением gw/install_apps.json protocols.vless.
    "ios": ("Karing", _K + "from.app_store", ("incy", "Shadowrocket")),
    "android": ("v2RayTun", _K + "from.vless_android", ("Happ", "v2rayNG", "Hiddify")),
    "androidtv": ("v2RayTun", _K + "from.tv_google_play", ("Happ",)),
    "windows": ("v2rayN", _K + "from.v2rayn_releases", ("Happ", "Hiddify")),
    "macos": ("Karing", _K + "from.app_store", ("Hiddify", "v2rayN")),
    "linux": ("v2rayN", _K + "from.v2rayn_releases", ("Happ", "Hiddify")),
    "other": ("Hiddify", _K + "from.vless_other", ("v2rayN",)),
}


def vless_apps(platform_name):
    """[app names] for VLESS Reality on the platform, the one of the steps first ([] — routers)."""
    p = platform(platform_name)
    if p not in _VLESS_APP:
        return []
    app, _, others = _VLESS_APP[p]
    return [app] + list(others)


def vless_apps_note(platform_name):
    """"Приложения: Streisand, Hiddify" or "" (routers)."""
    names = vless_apps(platform_name)
    return tr(_K + "apps", apps=", ".join(names)) if names else ""


def _vless_steps(p, w):
    if p not in _VLESS_APP:
        return []
    app, where_app, _ = _VLESS_APP[p]
    get = _install(app, where_app)
    if p in ("ios", "android") and w in ("menu", "panel", "agent"):
        return [get, tr(_K + "howto.vless_scan", app=app), tr(_QR_AT[w]), tr(_K + "howto.turn_on")]
    paste = tr(_K + "howto.vless_paste", app=app)
    if p in ("ios", "android", "androidtv"):
        return [get, tr(_COPY_AT[w], scheme="vless://"), tr(_SEND_AT[w], target=_target(p)), paste]
    return [get, tr(_COPY_AT[w], scheme="vless://"), paste]


# ── Hysteria2 (docs/k3/SPEC-stage1-hy2-inbound.md §4.2) ────────────────────

# Apps that import a hysteria2:// link (with Salamander), by platform: (app of the steps, where to get it, the others).
# Level «unverified» in gw/install_apps.json until the stand; the first one is the first of protocols.hy2 there.
_HY2_APP = {
    "ios": ("Karing", _K + "from.app_store", ("Shadowrocket", "V2Box")),
    "android": ("v2RayTun", _K + "from.vless_android", ("Hiddify", "NekoBox", "v2rayNG")),
    "windows": ("v2rayN", _K + "from.v2rayn_releases", ("Hiddify", "Karing", "Clash Verge Rev")),
    "macos": ("Karing", _K + "from.app_store", ("Hiddify", "v2rayN")),
    "linux": ("v2rayN", _K + "from.v2rayn_releases", ("Hiddify", "Clash Verge Rev")),
    "other": ("Hiddify", _K + "from.vless_other", ("v2rayN",)),
}


def hy2_apps(platform_name):
    """[app names] for Hysteria2 on the platform, the one of the steps first ([] — routers, TV)."""
    p = platform(platform_name)
    if p not in _HY2_APP:
        return []
    app, _, others = _HY2_APP[p]
    return [app] + list(others)


def hy2_apps_note(platform_name):
    names = hy2_apps(platform_name)
    return tr(_K + "apps", apps=", ".join(names)) if names else ""


def _hy2_steps(p, w):
    if p not in _HY2_APP:
        return []
    app, where_app, _ = _HY2_APP[p]
    get = _install(app, where_app)
    if p in ("ios", "android") and w in ("menu", "panel", "agent"):
        return [get, tr(_K + "howto.vless_scan", app=app), tr(_QR_AT[w]), tr(_K + "howto.turn_on")]
    paste = tr(_K + "howto.vless_paste", app=app)
    if p in ("ios", "android"):
        return [get, tr(_COPY_AT[w], scheme="hysteria2://"), tr(_SEND_AT[w], target=_target(p)), paste]
    return [get, tr(_COPY_AT[w], scheme="hysteria2://"), paste]


# ── built-in clients and routers (work package 6) ──────────────────────────

# Routers whose L2TP client has no IPsec: they need «L2TP без шифрования» on the node.
PLAIN_L2TP_ONLY = ("huawei", "xiaomi", "asus")

# Где на сервере включить «L2TP без шифрования»: меню 17 → «Протоколы» → L2TP/IPsec (пятый в списке — порядок
# model.INBOUND_KINDS) → второй пункт карточки (install.sh, k3_gw_proto_card: номера подряд). Тест сверяет.
# L2TP_PLAIN_PATH — the path in the language of the moment (module __getattr__ below).
_L2TP_PLAIN_PATH = _K + "l2tp_plain_path"
# The same switch in the words of each screen (``where``): the menu path above, the switch of «Протоколы» in the
# panel, the owner — for the person on his page (he has no access to the node), the bot — the menu or the panel.
_PLAIN_ON = {w: _K + "plain_on." + w for w in WHERE}            # {path}: L2TP_PLAIN_PATH


def _plain_on(w):
    return tr(_PLAIN_ON[w], path=tr(_L2TP_PLAIN_PATH))


class _PlainOn(Mapping):
    """PLAIN_ON[where] — the words of ``where`` in the language of the moment."""

    def __getitem__(self, w):
        if w not in _PLAIN_ON:
            raise KeyError(w)
        return _plain_on(w)

    def __iter__(self):
        return iter(_PLAIN_ON)

    def __len__(self):
        return len(_PLAIN_ON)


PLAIN_ON = _PlainOn()


def __getattr__(name):
    if name == "L2TP_PLAIN_PATH":
        return tr(_L2TP_PLAIN_PATH)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


# (platform, kind) -> keys of the steps; {fields} — the fields of a PPP client, {plain_on} — where to switch on
# «L2TP без шифрования» in the words of the reader (``steps`` fills both).
STEPS = {
    ("keenetic", "l2tp"): [_K + "steps.keenetic_add", _K + "steps.keenetic_l2tp", _K + "steps.keenetic_save"],
    ("keenetic", "pptp"): [_K + "steps.keenetic_add", _K + "steps.keenetic_pptp", _K + "steps.keenetic_save"],
    ("tplink", "l2tp"): [_K + "steps.tplink_check", _K + "steps.tplink_l2tp_client", _K + "steps.tplink_l2tp_wan"],
    ("tplink", "pptp"): [_K + "steps.tplink_check", _K + "steps.tplink_pptp_client", _K + "steps.tplink_pptp_wan"],
    ("huawei", "l2tp"): [_K + "steps.huawei_l2tp", _K + "steps.huawei_l2tp_fields", _K + "steps.huawei_l2tp_plain"],
    ("huawei", "pptp"): [_K + "steps.huawei_pptp"],
    ("xiaomi", "l2tp"): [_K + "steps.xiaomi_add", _K + "steps.xiaomi_l2tp", _K + "steps.xiaomi_l2tp_plain"],
    ("xiaomi", "pptp"): [_K + "steps.xiaomi_pptp"],
    ("asus", "l2tp"): [_K + "steps.asus_l2tp", _K + "steps.asus_l2tp_plain"],
    ("asus", "pptp"): [_K + "steps.asus_pptp"],
    ("mikrotik", "l2tp"): [_K + "steps.mikrotik"],
    ("mikrotik", "sstp"): [_K + "steps.mikrotik"],
    ("mikrotik", "pptp"): [_K + "steps.mikrotik"],
    ("ios", "l2tp"): [_K + "steps.ios_l2tp_add", _K + "steps.ios_l2tp_fields"],
    ("macos", "l2tp"): [_K + "steps.macos_l2tp_add", _K + "steps.macos_l2tp_fields"],
    ("android", "ikev2"): [_K + "steps.android_ikev2_ca", _K + "steps.android_ikev2_add",
                           _K + "steps.android_ikev2_note"],
    ("linux", "l2tp"): [_K + "steps.linux_l2tp"],
    ("linux", "pptp"): [_K + "steps.linux_pptp"],
    ("linux", "sstp"): [_K + "steps.linux_sstp"],
    ("linux", "ikev2"): [_K + "steps.linux_ikev2"],
}

_GONE = {
    ("ios", "pptp"): _K + "gone.apple_pptp",
    ("macos", "pptp"): _K + "gone.apple_pptp",
    ("ios", "sstp"): _K + "gone.ios_sstp",
    ("macos", "sstp"): _K + "gone.macos_sstp",
    ("android", "pptp"): _K + "gone.android_ppp",
    ("android", "l2tp"): _K + "gone.android_ppp",
    ("androidtv", "pptp"): _K + "gone.androidtv",
    ("androidtv", "l2tp"): _K + "gone.androidtv",
    ("androidtv", "sstp"): _K + "gone.androidtv",
    ("androidtv", "ikev2"): _K + "gone.androidtv",
}


def steps(platform_name, kind, where=None):
    """Where to click and what to type on this system for a built-in PPP/IPsec client ([] if nothing); what the
    node must switch on is said in the words of ``where`` (``audience``)."""
    keys = STEPS.get((platform(platform_name), kind), [])
    if not keys:
        return []
    fields, on = tr(_K + "ppp_fields"), _plain_on(audience(where))
    return [tr(k, fields=fields, plain_on=on) for k in keys]


def builtin_gone(platform_name, kind):
    """Why this system has no built-in client for the inbound ("" — it has one, or an app does it)."""
    key = _GONE.get((platform(platform_name), kind))
    return tr(key) if key else ""


def plain_l2tp_only(platform_name):
    return platform(platform_name) in PLAIN_L2TP_ONLY


def plain_off_note(where=None):
    """The note of an issuance for a router that knows L2TP only without IPsec while the node has it off."""
    return tr(_K + "plain_off_note", plain_on=_plain_on(audience(where)))
