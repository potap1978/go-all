"""Common ground of the tests of the person's page (WP-U): a gateway with users and devices, adapters that issue every
format, a fake qrencode (the text only through stdin), and ``call`` — a request through the route table of
``kaskad.panel.routes`` (the way the server of the panel dispatches)."""

import hashlib
import json
import re
from unittest import mock

from kaskad import paths, secrets, state
from kaskad.gw import registry
from kaskad.gw.inbounds.base import DummyInbound
from kaskad.panel import routes, userpage
from tests.fakes_nft import make_state
from tests.gwcase import GwCase

UID1, UID2 = "u-00000001", "u-00000002"
NAME1 = 'Мама <b>x</b> & "кавычки"'
NAME2 = "Дача"
HOST = "203.0.113.10"
ADDED = "2026-10-05T10:00:00Z"
IP = "198.51.100.7"
UA_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/17.5 Mobile/15E148 Safari/604.1")


def priv(dev, kind):
    return "PRIVKEY-%s-%s-abcdefghijklmnop" % (dev, kind)


def passwd(dev):
    return "PASSWD-%s-zyxwvutsrqponm" % dev


def _rec(fmt, title, body, filename=None, notes=None):
    return {"format": fmt, "title": title, "body": body, "filename": filename, "notes": list(notes or [])}


class RichInbound(DummyInbound):
    """An adapter whose issue() gives an entry of every format, with secrets from ``sec``."""

    def issue(self, st, sec, device, os=None):
        k, dev = self.kind, device["id"]
        key = sec.get("dev.%s.%s.private" % (dev, k), "")
        pw = sec.get("dev.%s.password" % dev, "")
        host = (st.get("gateway") or {}).get("host")
        items = [
            _rec("vpn", "%s — ссылка vpn://" % k, "vpn://%s" % key, None, ["Вставьте ссылку в приложение."]),
            _rec("qr", "%s — QR" % k, ["QRPAYLOAD-%s-1" % key, "QRPAYLOAD-%s-2" % key], None, ["Кодов: 2."]),
            _rec("conf", "%s — файл .conf" % k, "[Interface]\nPrivateKey = %s\nEndpoint = %s\n" % (key, host),
                 "kaskad-%s.conf" % k, ["Файл положите в приложение."]),
            _rec("fields", "%s — данные" % k, [["Сервер", host], ["Пароль", pw]]),
            _rec("guide", "%s — как подключить" % k, "Откройте приложение.\nНажмите «+».", None, []),
            _rec("link", "%s — ссылка" % k, "socks5://u:%s@%s:1080" % (pw, host)),
            _rec("mobileconfig", "%s — профиль" % k, "<plist>%s</plist>" % key, "kaskad-%s.mobileconfig" % k),
            _rec("ps1", "%s — скрипт" % k, "Add-VpnConnection # %s" % pw, "kaskad-%s.ps1" % k),
            _rec("ovpn", "%s — ovpn" % k, "client\n<key>\n%s\n</key>\n" % key, "kaskad-%s.ovpn" % k),
            _rec("conf", "%s — сертификат" % k, "-----BEGIN CERTIFICATE-----\nMIIBcert%s\n-----END CERTIFICATE-----\n" % k,
                 "kaskad-%s-ca.crt" % k),
        ]
        return dict(items[0], inbound=k, items=items)


# ── a fake qrencode ─────────────────────────────────────────────────────────

def qr_rows(text, n=21):
    """A module matrix that looks like a QR code: three finder patterns and bits of sha256(text) elsewhere."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    rows = [[0] * n for _ in range(n)]

    def finder(x0, y0):
        for y in range(7):
            for x in range(7):
                rows[y0 + y][x0 + x] = 1 if (x in (0, 6) or y in (0, 6) or (2 <= x <= 4 and 2 <= y <= 4)) else 0
    finder(0, 0)
    finder(n - 7, 0)
    finder(0, n - 7)
    bit = 0
    for y in range(n):
        for x in range(n):
            if (x < 8 and y < 8) or (x >= n - 8 and y < 8) or (x < 8 and y >= n - 8):
                continue
            rows[y][x] = (h[(bit // 8) % len(h)] >> (bit % 8)) & 1
            bit += 1
    return rows


def svg_of(rows, form="rect", margin=0):
    """qrencode 4.x ``-t SVG`` output in one of its shapes: rect (a rect per module), rle (rects with widths),
    use (a symbol and <use>), path (--svg-path strokes). ``margin`` — the quiet zone qrencode would add."""
    n = len(rows)
    side = n + 2 * margin
    head = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<!-- Created with qrencode 4.1.1 -->\n'
            '<svg width="%.2fcm" height="%.2fcm" viewBox="0 0 %d %d" preserveAspectRatio="none" version="1.1" '
            'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">\n' % (side / 9.5, side / 9.5, side, side))
    out = [head]
    if form == "use":
        out.append('\t<defs><rect id="p" width="1" height="1"/></defs>\n')
    out.append('\t<g id="QRcode">\n\t\t<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>\n' % (side, side))
    if form == "path":
        d = []
        for y in range(n):
            for x in range(n):
                if rows[y][x]:
                    d.append("M%d,%dh1" % (x, y))
        out.append('\t\t<path style="stroke:#000000" transform="translate(%d,%s)" d="%s"/>\n'
                   % (margin, margin + 0.5, "".join(d)))
    else:
        out.append('\t\t<g id="Pattern" transform="translate(%d,%d)">\n' % (margin, margin))
        for y in range(n):
            x = 0
            while x < n:
                if not rows[y][x]:
                    x += 1
                    continue
                e = x
                if form == "rle":
                    while e < n and rows[y][e]:
                        e += 1
                else:
                    e = x + 1
                if form == "use":
                    out.append('\t\t\t<use x="%d" y="%d" xlink:href="#p"/>\n' % (x, y))
                else:
                    out.append('\t\t\t<rect x="%d" y="%d" width="%d" height="1" fill="#000000"/>\n' % (x, y, e - x))
                x = e
        out.append("\t\t</g>\n")
    out.append("\t</g>\n</svg>\n")
    return "".join(out)


def matrix_of_path(d, n):
    """Draw the path data of the page back into a matrix (what the page's SVG does)."""
    rows = [[0] * n for _ in range(n)]
    x = y = 0
    for m in re.finditer(r"(?:M(\d+) (\d+)\.5|m(\d+) 0)h(\d+)", d):
        if m.group(1) is not None:
            x, y = int(m.group(1)), int(m.group(2))
        else:
            x += int(m.group(3))
        for i in range(int(m.group(4))):
            rows[y][x + i] = 1
        x += int(m.group(4))
    return rows


# ── the case ────────────────────────────────────────────────────────────────

class PageCase(GwCase):
    """The gateway of ``setUp``: users Мама (u-…1: android phone, iPhone, windows laptop, a disabled tablet) and
    Дача (a Keenetic router); inbounds wg, awg2, ikev2 whose issue() gives every format; secrets for each device;
    a fake qrencode; the clock of the page guards in ``self.clock``."""

    QR_FORM = "rect"

    def setUp(self):
        super().setUp()
        self.clock = [5000.0]
        p = mock.patch.object(userpage, "_mono", lambda: self.clock[0])
        p.start()
        self.addCleanup(p.stop)
        userpage.GUARDS.reset()
        self.addCleanup(userpage.GUARDS.reset)
        self.ads = {}
        for kind in ("wg", "awg2", "ikev2"):
            ad = RichInbound(kind=kind)
            registry.register(ad)
            self.ads[kind] = ad
        self.qr_calls = []
        self.qr_fail = False
        self.qr_form = self.QR_FORM
        self.make_state()
        self.install_qrencode()

    def install_qrencode(self):
        def fake(argv, inp):
            self.qr_calls.append((list(argv), inp))
            if self.qr_fail:
                return 1, "", "Failed to encode the input data: Input data too large\n"
            return 0, svg_of(qr_rows(inp), self.qr_form), ""
        self.w.runner.binaries.add("qrencode")
        self.w.runner.on(["qrencode"], fake)

    def device(self, dev_id, user, name, kind, platform, access, enabled=True):
        return {"id": dev_id, "user": user, "name": name, "kind": kind, "platform": platform, "route": None,
                "enabled": enabled, "added": ADDED, "login": dev_id.replace("d-", "dev-"),
                "ips": {}, "access": list(access)}

    def make_state(self):
        st = make_state()
        st["gateway"] = {
            "enabled": True, "host": HOST, "net": {"base": "10.77.0.0/16", "dns": "10.77.0.1"},
            "inbounds": {"wg": {"enabled": True, "port": 51820}, "awg2": {"enabled": True, "port": 41023},
                         "ikev2": {"enabled": True}},
            "users": [{"id": UID1, "name": NAME1, "route": None, "enabled": True, "added": ADDED},
                      {"id": UID2, "name": NAME2, "route": None, "enabled": True, "added": ADDED}],
            "devices": [
                self.device("d-00000001", UID1, "Телефон", "phone", "android", ["wg", "awg2"]),
                self.device("d-00000002", UID1, "iPhone Маши", "phone", "ios", ["wg"]),
                self.device("d-00000003", UID1, "Ноутбук", "laptop", "windows", ["wg", "ikev2"]),
                self.device("d-00000004", UID1, "Старый планшет", "tablet", "android", ["wg"], enabled=False),
                self.device("d-00000005", UID2, "Роутер на даче", "router", "keenetic", ["wg"]),
            ],
            "routes": {"default": {"via": "pool", "fail": "block"}}, "exits": {}}
        state.save(st)
        sec = secrets.load()
        for d in st["gateway"]["devices"]:
            sec.set("dev.%s.password" % d["id"], passwd(d["id"]))
            for kind in d["access"]:
                sec.set("dev.%s.%s.private" % (d["id"], kind), priv(d["id"], kind))
        sec.save()

    # ── requests ──
    def call(self, method, path, body=None, headers=None, ip=IP, query=None, json_body=True):
        route, m = routes.find(method, path)
        self.assertNotIn(route, (None, "method"), "no route for %s %s" % (method, path))
        hdrs = {"user-agent": UA_IPHONE}
        if method == "POST" and json_body:
            hdrs["content-type"] = "application/json"
        hdrs.update(headers or {})
        raw = b""
        if body is not None:
            raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        req = routes.Request(method, path, query=query, headers=hdrs, body=raw, match=m, client_ip=ip, scheme="https")
        try:
            return route.handler(req)
        except routes.HttpError as e:
            return routes.error_response(e)

    @staticmethod
    def sig(resp):
        """What an observer sees of a response: status, headers, body."""
        return (resp.status, tuple(sorted(resp.headers.items())), resp.body)

    def token(self, uid=UID1):
        userpage.enable(uid)
        return userpage._stored_tokens()[uid]

    def shell(self, token, **kw):
        return self.call("GET", "/p/%s" % token, **kw)

    def nonce_of(self, resp):
        m = re.search(r'name="kaskad-nonce" content="([^"]*)"', resp.body.decode("utf-8"))
        self.assertIsNotNone(m, resp.body[:300])
        return m.group(1)

    def keys(self, token, nonce=None, **kw):
        if nonce is None:
            nonce = self.nonce_of(self.shell(token))
        return self.call("POST", "/p/%s/keys" % token, body={"nonce": nonce}, **kw)

    def keys_json(self, token, **kw):
        resp = self.keys(token, **kw)
        self.assertEqual(resp.status, 200, resp.body[:400])
        return json.loads(resp.body.decode("utf-8"))

    def write_access(self, **doc):
        self.write("/etc/kaskad/panel/access.json", json.dumps(doc))

    def all_secret_values(self):
        out = []
        for key, val in secrets.load().items():
            if isinstance(val, str) and len(val) >= 8:
                out.append((key, val))
                out.extend((key, ln) for ln in val.splitlines() if len(ln) >= 12 and "-----" not in ln)
        return out

    def assert_no_secrets(self, text):
        for key, val in self.all_secret_values():
            self.assertNotIn(val, text, "the secret %s is in the first HTML" % key)
        for pat in ("PRIVKEY", "PASSWD", "QRPAYLOAD"):
            self.assertNotIn(pat, text)

    def secret_path(self):
        return paths.secrets_file()
