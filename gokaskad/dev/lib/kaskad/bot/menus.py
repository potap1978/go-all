"""Keyboards, fixed texts, labels and pagination of the bot. Texts of the bash bot are kept where people know
them (menus, «Введите IP:», ALERT, hosting); button texts are plain text (no HTML), message texts are HTML.

Pagination: a page holds at most ``PAGE_ROWS`` entries and ``PAGE_CHARS`` characters of text (so a message
never hits Telegram's 4096), keyboards at most ``PAGE_ROWS`` entry buttons (+ navigation; Telegram allows 100).
The callback of a page is ``<base>~<start index>``.
"""

from .lang import tr, trn  # noqa: F401 — trn: menus.trn for the handler modules
from .telegram import h, inline

PAGE_ROWS = 30
PAGE_CHARS = 3300
BUTTON_TEXT = 60


def back_row():
    """The row «⬅️ Меню» (to the main menu)."""
    return [(tr("bot.menu.btn_back"), "m")]


def main_inline():
    return [
        [("🔀 AWG", "a_u"), ("🔀 VLESS", "a_t"), ("🔀 MTProto", "a_mt")],
        [("🛠 Custom", "a_c"), (tr("bot.menu.btn_rules"), "lr")],
        [("🏓 Ping", "pm"), (tr("bot.menu.btn_monitor"), "mm")],
        [(tr("bot.menu.btn_system"), "sys"), (tr("bot.menu.btn_gateway"), "gw"), (tr("bot.menu.btn_domain"), "sd")],
        [(tr("bot.menu.btn_delete"), "dr"), (tr("bot.menu.btn_flush"), "fa")],
        [(tr("bot.menu.btn_hosting"), "promo"), (tr("bot.menu.btn_lang"), "lang")],
        [(tr("bot.menu.btn_reply_kbd"), "sw_reply")],
    ]


def reply_rows():
    """The reply keyboard; its texts are matched in both languages (registry.reply_key)."""
    return [
        ["🔀 AWG/WG", "🔀 VLESS"],
        ["🔀 MTProto", "🛠 Custom"],
        [tr("bot.menu.btn_rules"), "🏓 Ping"],
        [tr("bot.menu.btn_monitor"), tr("bot.menu.btn_system")],
        [tr("bot.menu.btn_delete"), tr("bot.menu.btn_flush")],
        [tr("bot.menu.btn_hosting"), tr("bot.menu.btn_gateway")],
        [tr("bot.menu.btn_domain"), tr("bot.menu.btn_lang")],
        ["/inline"],
    ]


def cancel_btn(data):
    """The button «⬅️ Отмена» that goes to ``data``."""
    return (tr("bot.menu.btn_cancel"), data)


def promo():
    """The partner hosting: the addresses come from setup.product.HOSTING (one place for the bot, the wizard, the
    panel and the SSH menu), the words — from the catalog."""
    from ..setup import product
    return tr("bot.menu.promo", eu_url=product.hosting_url("adminvps"), by_url=product.hosting_url("cloudvps"))


def help_text():
    return tr("bot.menu.help")


PING_HINT = ("<code>sysctl -w net.ipv4.icmp_echo_ignore_all=0</code>\n"
             "<code>iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT</code>")


def stale_button(lang=None):
    return tr("bot.menu.stale_button", lang)


def stale_toast(lang=None):
    return tr("bot.menu.stale_toast", lang)


def stale_message(lang=None):
    return tr("bot.menu.stale_message", lang)


def old_button():
    return tr("bot.menu.old_button")


def dialog_expired():
    return tr("bot.menu.dialog_expired")


def no_access(chat, lang=None):
    return tr("bot.menu.no_access", lang, chat=h(chat))


def not_bound(chat, lang=None):
    return tr("bot.menu.not_bound", lang, chat=h(chat))


INBOUND_TITLES = {"wg": "WireGuard", "awg1": "AmneziaWG 1.5", "awg2": "AmneziaWG 2.0", "awg3": "AmneziaWG 3.1",
                  "l2tp": "L2TP/IPsec", "pptp": "PPTP", "sstp": "SSTP", "ikev2": "IKEv2", "openvpn": "OpenVPN",
                  "ocserv": "OpenConnect", "socks5": "SOCKS5", "vless": "VLESS", "hy2": "Hysteria2"}
PLATFORMS = ("ios", "android", "androidtv", "windows", "macos", "linux", "keenetic", "mikrotik", "openwrt", "tplink",
             "huawei", "xiaomi", "asus", "other")
KIND_OF_PLATFORM = {"ios": "phone", "android": "phone", "androidtv": "tv", "windows": "pc", "macos": "pc",
                    "linux": "pc", "keenetic": "router", "mikrotik": "router", "openwrt": "router",
                    "tplink": "router", "huawei": "router", "xiaomi": "router", "asus": "router"}
KINDS = ("phone", "tv", "pc", "router", "laptop", "tablet")
KIND_ICONS = {"phone": "📱", "tablet": "📱", "tv": "📺", "pc": "💻", "laptop": "💻", "router": "📡"}


def platform_titles():
    """[(platform, title)] in the order of the keyboard."""
    return [(p, tr("bot.platform." + p)) for p in PLATFORMS]


def kind_name(kind):
    """The default name of a device of ``kind`` (Телефон, Роутер…); «Устройство» for anything else."""
    return tr("bot.kind." + kind) if kind in KINDS else tr("bot.kind.device")


def back():
    return inline([back_row()])


def kbd_proto():
    return inline([[("TCP", "a_cp_tcp"), ("UDP", "a_cp_udp")], back_row()])


def platform_title(p):
    return tr("bot.platform." + p) if p in PLATFORMS else (p or tr("bot.platform.other"))


def inbound_title(kind):
    return INBOUND_TITLES.get(kind, kind)


def button(text):
    """A button label: one line, at most BUTTON_TEXT characters."""
    s = " ".join(str(text).split())
    return s if len(s) <= BUTTON_TEXT else s[:BUTTON_TEXT - 1] + "…"


# ── servers: names from aliases ────────────────────────────────────────────

def ip_short(ip, table):
    """``name (ip)`` or ``ip`` — plain text (buttons, logs)."""
    name = (table.get(ip) or {}).get("name")
    return "%s (%s)" % (name, ip) if name else ip


def ip_short_html(ip, table):
    return h(ip_short(ip, table))


def ip_tg(ip, table):
    """fmt_ip_tg of 2.x: <b>name</b> <code>ip</code> country | isp, and the note in italics on its own line."""
    a = table.get(ip) or {}
    out = ("<b>%s</b> " % h(a["name"])) if a.get("name") else ""
    out += "<code>%s</code>" % h(ip)
    if a.get("country") or a.get("isp"):
        out += " " + h(a.get("country") or "")
        if a.get("isp"):
            out += " | " + h(a["isp"])
    if a.get("note"):
        out += "\n  <i>%s</i>" % h(a["note"])
    return out


# ── pagination ─────────────────────────────────────────────────────────────

def page_starts(items, rows=PAGE_ROWS, chars=PAGE_CHARS, size=None):
    starts = [0]
    n = c = 0
    for i, it in enumerate(items):
        s = size(it) if size else 0
        if n and (n >= rows or c + s > chars):
            starts.append(i)
            n = c = 0
        n += 1
        c += s
    return starts


class Page(object):
    __slots__ = ("items", "start", "prev", "next", "number", "total")

    def __init__(self, items, start, prev, nxt, number, total):
        self.items, self.start, self.prev, self.next, self.number, self.total = items, start, prev, nxt, number, total

    def nav(self, base):
        """The row of «назад / N из M / дальше» buttons ([] for a single page)."""
        if self.total <= 1:
            return []
        row = []
        if self.prev is not None:
            row.append((tr("bot.menu.page_prev"), "%s~%d" % (base, self.prev)))
        row.append((tr("bot.menu.page_of", n=self.number, total=self.total), "noop"))
        if self.next is not None:
            row.append((tr("bot.menu.page_next"), "%s~%d" % (base, self.next)))
        return row


def paginate(items, start=0, rows=PAGE_ROWS, chars=PAGE_CHARS, size=None):
    items = list(items)
    starts = page_starts(items, rows, chars, size)
    idx = 0
    for i, s in enumerate(starts):
        if s <= max(0, int(start or 0)):
            idx = i
    begin = starts[idx]
    end = starts[idx + 1] if idx + 1 < len(starts) else len(items)
    return Page(items[begin:end], begin, starts[idx - 1] if idx > 0 else None,
                starts[idx + 1] if idx + 1 < len(starts) else None, idx + 1, len(starts))


def split_page(data, base):
    """``<base>~<n>`` -> n (0 when absent or broken)."""
    rest = data[len(base):]
    if rest.startswith("~") and rest[1:].isdigit():
        return int(rest[1:])
    return 0
