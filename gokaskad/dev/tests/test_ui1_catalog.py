"""UI-1 F09: the catalog of «Установите приложение» (lib/kaskad/gw/install_apps.json) — at least two apps for every
client platform × protocol the panel issues through an app (AmneziaWG, VLESS, Hysteria2); incy and Karing wherever
their developers ship an official build, and only with links of the developers themselves.

Not counted: WireGuard (one official app everywhere), SOCKS5 (Telegram), routers (one way per firmware) — there one
app is the whole answer."""

import unittest

from kaskad.gw import install_apps

CLIENTS = ("ios", "android", "androidtv", "windows", "macos", "linux")
KINDS = ("awg1", "awg2", "awg3", "vless", "hy2")

# where the developers ship an official build that takes vless:// / a subscription / QR (sources — k3/stand/logs/R-ui1.md)
WANT = {
    ("vless", "ios"): {"incy", "karing"}, ("vless", "android"): {"incy", "karing"}, ("vless", "androidtv"): {"incy"},
    ("vless", "windows"): {"incy", "karing"}, ("vless", "macos"): {"incy", "karing"}, ("vless", "linux"): {"incy", "karing"},
    ("hy2", "ios"): {"incy", "karing"}, ("hy2", "android"): {"incy", "karing"}, ("hy2", "windows"): {"incy", "karing"},
    ("hy2", "macos"): {"incy", "karing"}, ("hy2", "linux"): {"incy", "karing"},
}
OFFICIAL = {
    "incy": ("https://apps.apple.com/app/id6756943388", "https://play.google.com/store/apps/details?id=llc.itdev.incy",
             "https://github.com/INCY-DEV/incy-platforms/"),
    "karing": ("https://apps.apple.com/app/id6472431552", "https://github.com/KaringX/karing/"),
}


class CatalogTest(unittest.TestCase):
    def apps(self, kind, p):
        return install_apps.block(kind, p)["apps"]

    def test_two_apps_at_least(self):
        thin = []
        for kind in KINDS:
            for p in CLIENTS:
                entry = (install_apps.catalog()["protocols"].get(kind) or {}).get(p)
                if not isinstance(entry, list):
                    continue                        # no issuance through an app on this platform
                if len(self.apps(kind, p)) < 2:
                    thin.append("%s × %s: %s" % (kind, p, [a["id"] for a in self.apps(kind, p)]))
        self.assertEqual(thin, [])

    def test_incy_and_karing_where_official(self):
        missing = []
        for (kind, p), want in sorted(WANT.items()):
            have = set(a["id"] for a in self.apps(kind, p))
            if not want <= have:
                missing.append("%s × %s: нет %s" % (kind, p, sorted(want - have)))
        self.assertEqual(missing, [])

    def test_links_only_of_the_developers(self):
        bad = []
        for kind in KINDS:
            for p in CLIENTS:
                for a in self.apps(kind, p):
                    if a["id"] not in OFFICIAL:
                        continue
                    for ln in a["links"]:
                        if not ln["url"].startswith(OFFICIAL[a["id"]]):
                            bad.append("%s %s %s: %s" % (a["id"], kind, p, ln["url"]))
        self.assertEqual(bad, [])

    def test_vendor_links_first_own_only_added(self):
        # incy on Android: the vendor's Google Play and GitHub; on Windows (the vendor has no such platform) — our GitHub
        android = [a for a in self.apps("vless", "android") if a["id"] == "incy"][0]
        self.assertEqual([x["kind"] for x in android["links"]], ["googleplay", "github"])
        windows = [a for a in self.apps("vless", "windows") if a["id"] == "incy"][0]
        self.assertEqual([x["url"] for x in windows["links"]], ["https://github.com/INCY-DEV/incy-platforms/releases/latest"])
        self.assertIn("pre-alpha", windows["note"])
        # AmneziaWG is not among the protocols of the desktop incy: it stays out of AWG lists on Windows and Linux
        for kind in ("awg1", "awg2", "awg3"):
            for p in ("windows", "linux"):
                self.assertNotIn("incy", [a["id"] for a in self.apps(kind, p)], (kind, p))

    def test_new_pairs_are_marked_unverified(self):
        """REV-ui1 З-4: пары UI-1 без проверки на устройстве — с «не проверено»: incy везде, кроме iOS × VLESS
        (проверено в 3.3.2); Karing на Android, Windows и Linux (Hysteria2 у Karing — karing.app/quickstart, а не реклама
        провайдера в README). Прежние пары — без метки."""
        def mark(app, kind, p):
            a = [x for x in self.apps(kind, p) if x["id"] == app]
            self.assertTrue(a, (app, kind, p))
            return bool(a[0]["unverified"])
        for kind, p in (("vless", "windows"), ("vless", "linux"), ("vless", "android"), ("vless", "androidtv"), ("vless", "macos"),
                        ("hy2", "ios"), ("hy2", "android"), ("hy2", "windows"), ("hy2", "macos"), ("hy2", "linux")):
            self.assertTrue(mark("incy", kind, p), ("incy", kind, p))
        for kind in ("vless", "hy2"):
            for p in ("android", "windows", "linux"):
                self.assertTrue(mark("karing", kind, p), ("karing", kind, p))
        self.assertFalse(mark("incy", "vless", "ios"))
        self.assertFalse(mark("karing", "vless", "ios"))
        self.assertFalse(mark("karing", "vless", "macos"))
        # REV-3373 З-4: Karing × Hysteria2 на iOS и macOS на устройстве не проверена — с меткой, как везде
        for p in ("ios", "macos"):
            self.assertTrue(mark("karing", "hy2", p), ("karing", "hy2", p))

    def test_incy_note_fits_every_protocol(self):
        """REV-ui1 придирка 4: заметка incy привязана к платформе и видна и в списке Hysteria2 — без «vless://»."""
        for kind in ("vless", "hy2"):
            for p in ("windows", "linux"):
                a = [x for x in self.apps(kind, p) if x["id"] == "incy"][0]
                self.assertNotIn("vless", a["note"].lower(), (kind, p))


if __name__ == "__main__":
    unittest.main()
