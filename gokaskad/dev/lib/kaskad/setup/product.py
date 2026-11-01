"""The product object of the quick setup (design handoff §4: «Настройка продукта — один объект, остальное общее»):
stages, videos, the words of the protocol cards, the list of «Дополнительно». The page gets it from
``GET /api/setup/product``; the protocols themselves (which exist, ports, what holds them) come from the gateway's
registry at run time — a new inbound (VLESS of the branch k3-vless) appears by itself, with neutral words until its
card is written here.
"""

from .. import __version__
from ..i18n import tr

ID = "kaskad"
NAME = "Kaskad PRO"
CMD = "kaskadctl setup link"
SSH = "setup.product.ssh"                       # a key of the catalog: ssh() gives the words

# kind -> (title, sub, minutes); title and sub are keys of the catalog (stage_doc gives the words). «welcome» (the
# entry: greeting and password) is always first and has no number.
STAGES = {
    "welcome": ("setup.product.stageWelcome", "setup.product.stageWelcomeSub", 1),
    "import": ("setup.product.stageImport", "setup.product.stageImportSub", 2),
    "protocols": ("setup.product.stageProtocols", "setup.product.stageProtocolsSub", 5),
    "forward": ("setup.product.stageForward", "setup.product.stageForwardSub", 3),
    "exits": ("setup.product.stageExits", "setup.product.stageExitsSub", 3),
    "domain": ("setup.product.stageDomain", "setup.product.stageDomainSub", 20),
    "extras": ("setup.product.stageExtras", "setup.product.stageExtrasSub", 2),
    "done": ("setup.product.stageDone", "setup.product.stageDoneSub", 3),
}
# the range «≈ 1–2 мин» of the rail and the head of a stage (design handoff v2 §9); ``min`` above is its upper end
RANGES = {"welcome": (1, 1), "import": (1, 2), "protocols": (2, 5), "forward": (2, 3), "exits": (2, 3),
          "domain": (5, 20), "extras": (1, 2), "done": (1, 3)}
# the rail: the node (gateway) and the classic Kaskad (owner 2026-10-09 §12: Вход → Импорт → Пробросы → Домен →
# Готово — forwards only: no protocols, no exits, no extras, no QR). The second stage is the slot of the mode: the
# cards «классический / узел» stand on «protocols» of the node and on «forward» of the classic Kaskad.
RAIL = {"node": ("import", "protocols", "exits", "domain", "extras", "done"),
        "classic": ("import", "forward", "domain", "done")}
MODE_SLOT = {"node": "protocols", "classic": "forward"}
DEFAULT_MODE = "node"

# videos: stubs until stage E (src null — the page shows «Ролик готовится — шаг проходится и без него»); title and
# the words of the chapters are keys of the catalog (videos() gives the words)
VIDEOS = {
    "welcome": {"title": "setup.product.videoWelcome", "dur": "0:50", "src": None,
                "chapters": [["0:00", "setup.product.chapWelcomeSteps", "steps"],
                             ["0:20", "setup.product.chapWelcomePw", "pw"],
                             ["0:38", "setup.product.chapWelcomeTrust", "trust"]]},
    "import": {"title": "setup.product.videoImport", "dur": "1:10", "src": None,
               "chapters": [["0:00", "setup.product.chapImportFound", "found"],
                            ["0:30", "setup.product.chapImportKeep", "keep"],
                            ["0:52", "setup.product.chapImportOther", "other"]]},
    "protocols": {"title": "setup.product.videoProtocols", "dur": "1:30", "src": None,
                  "chapters": [["0:00", "setup.product.chapProtocolsMode", "mode"],
                               ["0:35", "setup.product.chapProtocolsMix", "mix"],
                               ["0:58", "setup.product.chapProtocolsProtos", "protos"],
                               ["1:18", "setup.product.chapProtocolsSummary", "summary"]]},
    "forward": {"title": "setup.product.videoForward", "dur": "0:50", "src": None,
                "chapters": [["0:00", "setup.product.chapForwardFwd", "fwd"],
                             ["0:30", "setup.product.chapForwardFwdcheck", "fwdcheck"]]},
    "exits": {"title": "setup.product.videoExits", "dur": "1:20", "src": None,
              "chapters": [["0:00", "setup.product.chapExitsChoice", "choice"],
                           ["0:22", "setup.product.chapExitsAddkey", "addkey"],
                           ["0:50", "setup.product.chapExitsKeys", "keys"],
                           ["1:05", "setup.product.chapExitsRoute", "route"]]},
    "domain": {"title": "setup.product.videoDomain", "dur": "2:10", "src": None,
               "chapters": [["0:00", "setup.product.chapDomainRecord", "record"],
                            ["0:50", "setup.product.chapDomainIpcopy", "ipcopy"],
                            ["1:25", "setup.product.chapDomainDns", "dns"],
                            ["1:50", "setup.product.chapDomainSite", "site"]]},
    "extras": {"title": "setup.product.videoExtras", "dur": "0:40", "src": None,
               "chapters": [["0:00", "setup.product.chapExtrasXbot", "xbot"],
                            ["0:18", "setup.product.chapExtrasXmcp", "xmcp"],
                            ["0:30", "setup.product.chapExtrasXbak", "xbak"]]},
    "done": {"title": "setup.product.videoDone", "dur": "1:10", "src": None,
             "chapters": [["0:00", "setup.product.chapDoneDevice", "device"],
                          ["0:20", "setup.product.chapDoneQr", "qr"],
                          ["0:45", "setup.product.chapDoneStatus", "status"]]},
}

# The words of the protocol cards (the registry gives the title, the ports and whether it works here). Owner's choice
# 2026-10-09: AmneziaWG 2.0, AmneziaWG 3.1, WireGuard, L2TP/IPsec are ticked at once; the rest — by a tick.
# ``why`` and ``tags`` are keys of the catalog: protocol_card() gives the words. The tags must be exactly the words of
# the page's dictionary (setup.js colours them: pr.tagRec, pr.tagWeak) — «рекомендуем»/«recommended», «слабое
# шифрование»/«weak encryption».
TAG_REC = "setup.product.tagRecommended"
TAG_WEAK = "setup.product.tagWeak"
PROTOCOLS = {
    "awg2": {"why": "setup.product.whyAwg2", "tags": [TAG_REC], "mb": 6, "on": True},
    "awg3": {"why": "setup.product.whyAwg3", "tags": [TAG_REC], "mb": 6, "on": True},
    "wg": {"why": "setup.product.whyWg", "tags": [], "mb": 2, "on": True},
    "l2tp": {"why": "setup.product.whyL2tp", "tags": [], "mb": 18, "on": True},
    "awg1": {"why": "setup.product.whyAwg1", "tags": [], "mb": 6, "on": False},
    "ikev2": {"why": "setup.product.whyIkev2", "tags": [], "mb": 14, "on": False},
    "sstp": {"why": "setup.product.whySstp", "tags": [], "mb": 12, "on": False},
    "openvpn": {"why": "setup.product.whyOpenvpn", "tags": [], "mb": 26, "on": False},
    "ocserv": {"why": "setup.product.whyOcserv", "tags": [], "mb": 22, "on": False},
    "socks5": {"why": "setup.product.whySocks5", "tags": [], "mb": 3, "on": False},
    "vless": {"why": "setup.product.whyVless", "tags": [], "mb": 0, "on": False},   # xray шлюза уже в BASE_MB
    "hy2": {"why": "setup.product.whyHy2", "tags": [], "mb": 0, "on": False},       # тоже xray шлюза; выключен (SPEC §2)
    "pptp": {"why": "setup.product.whyPptp", "tags": [TAG_WEAK], "mb": 8, "on": False},
}
GENERIC = {"why": "", "tags": [], "mb": 0, "on": False}
BASE_MB = 40                       # dnsmasq, conntrack, xray of the gateway — what «узел» puts in besides the inbounds
AWG_KINDS = ("awg1", "awg2", "awg3")

# «Дополнительно», version 1 (owner 2026-10-09): every item says whether it works on this node now. Items of other
# branches (backup) answer «появится в этой версии» until their commands exist. «Автообновление подписки выходов» is
# gone (3.3.3, UX-333, OWNER-FEEDBACK №37): the subscription goes into the one field of the stage «Выходы».
EXTRAS = (                          # title and desc are keys of the catalog: extras() gives the words
    {"id": "bot", "title": "setup.product.extraBot", "desc": "setup.product.extraBotDesc", "hl": "xbot"},
    {"id": "mcp", "title": "setup.product.extraMcp", "desc": "setup.product.extraMcpDesc", "hl": "xmcp"},
    {"id": "backup", "title": "setup.product.extraBackup", "desc": "setup.product.extraBackupDesc", "hl": "xbak"},
)

# Links of the wizard (3.3.3, UX-333): only real pages. «exit_guide» — the guide «goAmnezia Pro на зарубежном VPS» for
# the line «Нет сервера за рубежом?» of the stage «Выходы»: None until the guide is published (the line goes without a
# link). «Купить домен» is the partner address of AdminVPS from HOSTING (doc(): links.domain_buy, OWNER-FEEDBACK №47;
# the page says the domains are in the section «Домены» of the site) — no second copy here.
LINKS = {"exit_guide": None}
# The partner hosting (owner 2026-10-12, OWNER-FEEDBACK №45) — the ONE place of these addresses: the wizard takes them
# from doc()["links"]["hosting"] (GET /api/setup/product), the panel from hosting() (GET /api/hosting, with the login of
# the panel), the bot by substitution (bot.menu.promo:
# {eu_url}/{by_url}), the SSH texts too (%s; tools/build_payload.py writes K3_HOST_URL_<ID> from here into install.sh).
# ``region`` is a key of the catalog (hosting() gives the words); ``exit`` — a server there can be an exit (not in
# Russia): AdminVPS sells Russia too, the page says «choose a country other than Russia».
HOSTING = (
    {"id": "adminvps", "name": "AdminVPS", "url": "https://vk.cc/ct29NQ", "region": "setup.product.hostRuEu"},
    {"id": "cloudvps", "name": "Cloud VPS", "url": "https://vk.cc/cUxAhj", "region": "setup.product.hostBy"},
)

DEVICES = ("iphone", "android", "windows", "mac", "router")       # the quick buttons; the full list — platforms
# names of the platforms; the two that have words in them are keys of the catalog (platform_title() gives the words)
PLATFORM_TITLES = {"ios": "setup.product.platformIos", "android": "Android", "androidtv": "Android TV",
                   "windows": "Windows", "macos": "macOS", "linux": "Linux", "keenetic": "Keenetic",
                   "mikrotik": "MikroTik", "openwrt": "OpenWrt", "tplink": "TP-Link", "huawei": "Huawei",
                   "xiaomi": "Xiaomi", "asus": "ASUS", "other": "setup.product.platformOther"}
DEVICE_PLATFORM = {"iphone": "ios", "android": "android", "windows": "windows", "mac": "macos", "router": "keenetic"}


def _tr(text):
    """The words of a key of this catalog; a name that is no key (Android, ASUS) — as it is."""
    return tr(text) if text and text.startswith("setup.product.") else text


def ssh():
    return tr(SSH)


def hosting():
    """The partner hosting in words of the current language (a new list each time)."""
    return [dict(x, region=tr(x["region"])) for x in HOSTING]


def hosting_url(id_):
    return next(x["url"] for x in HOSTING if x["id"] == id_)


def protocol_card(kind):
    card = dict(GENERIC, **PROTOCOLS.get(kind, {}))
    card["why"] = _tr(card["why"])
    card["tags"] = [_tr(t) for t in card["tags"]]
    return card


def protocol_cards():
    return dict((k, protocol_card(k)) for k in PROTOCOLS)


def extras():
    """«Дополнительно» in words of the current language (a new list each time — the callers add their fields)."""
    return [dict(x, title=tr(x["title"]), desc=tr(x["desc"])) for x in EXTRAS]


def videos():
    return dict((k, dict(v, title=tr(v["title"]), chapters=[[t, tr(c), hl] for t, c, hl in v["chapters"]]))
                for k, v in VIDEOS.items())


def platform_title(p):
    t = PLATFORM_TITLES.get(p)
    return _tr(t) if t else p


def platform_titles():
    return dict((p, _tr(t)) for p, t in PLATFORM_TITLES.items())


def stage_doc(kind):
    t, s, m = STAGES[kind]
    return {"kind": kind, "title": tr(t), "sub": tr(s), "min": m, "range": list(RANGES.get(kind, (m, m)))}


def doc(mode=None):
    """The product object for the page (no state, no secrets), in words of the current language."""
    mode = mode if mode in RAIL else DEFAULT_MODE
    return {"id": ID, "name": NAME, "version": __version__, "logo": "static/logo.svg",
            "accent": "#45D0C0", "accentLight": "oklch(.53 .17 288)", "hue": 288, "cmd": CMD, "ssh": ssh(),
            "modes": sorted(RAIL), "mode": mode,
            "steps": [stage_doc(k) for k in RAIL[mode]], "welcome": stage_doc("welcome"),
            "videos": videos(), "extras": extras(), "devices": list(DEVICES),
            "platforms": platform_titles(), "device_platform": DEVICE_PLATFORM,
            "protocol_cards": protocol_cards(), "base_mb": BASE_MB, "links": dict(LINKS, domain_buy=hosting_url("adminvps"), hosting=hosting())}
