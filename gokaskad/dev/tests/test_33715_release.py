"""Kaskad 3.3.7.15 — the protocol settings in one window, the safe apply, «ждут новых настроек» for every protocol, the
gateway network, «Адреса сервера» with IPv6, «Разложить заново» (SPEC-R1 §0, §3): one version (3.3.7.15 newer than
3.3.7.14 for the update), and «Что нового» of 3.3.7.15 for the admin, tied to what the panel, the core, the menu and
the bot say (the features themselves — tests/test_r15_*.py). What was cut from the release (SPEC-R1 §0а: the traffic
by addresses, ALPN h2, the sets of domestic groups, the /24 of a protocol) is not promised."""
import json
import re
import subprocess
import unittest

import kaskad
from kaskad.bot import monitor
from tests.test_3371_release import entry, text


def _flat(s):
    return re.sub(r"\s+", " ", s)


def _bullets(raw):
    return [_flat(b) for b in re.split(r"(?m)^- ", raw)[1:]]


def _item(raw, marker):
    """The one item of the entry with ``marker`` in it."""
    found = [b for b in _bullets(raw) if marker in b]
    assert len(found) == 1, (marker, len(found))
    return found[0]


def _sh(lang, name, key):
    m = re.search(r"(?m)^M_%s_%s='([^']*)'$" % (lang, key), text("lib/kaskad/i18n/sh/%s.%s.sh" % (name, lang)))
    return m.group(1)


def _cat(lang, name):
    return json.loads(text("lib/kaskad/i18n/catalog/%s/%s.json" % (lang, name)))


def _user(lang):
    """The words of the user's page (user-i18n-<lang>.js: ``key: '…'``)."""
    return dict(re.findall(r"(?m)^\s*([A-Za-z_]+):\s*'((?:[^'\\]|\\.)*)'",
                           text("lib/kaskad/panel/static/user-i18n-%s.js" % lang)))


def _give(lang):
    """The words of the settings block of /p/ (give-i18n-<lang>.js: ``key: '…'``)."""
    return dict(re.findall(r"(?m)^\s*([A-Za-z_]+):\s*'((?:[^'\\]|\\.)*)'",
                           text("lib/kaskad/panel/static/give-i18n-%s.js" % lang)))


def _js(lang, name="i18n-%s.js"):
    return dict(re.findall(r'(?m)^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', text("lib/kaskad/panel/static/" + name % lang)))


def _two(forms):
    """The form of a plural text for n = 2 (RU «2 устройства», EN «2 devices» — the second form in both)."""
    return forms.split("|")[1]


LANGS = (("ru", "«%s»"), ("en", '"%s"'))


class Release33715Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.15")
        self.ru_raw, sep, self.en_raw = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.15")
        self.raw = {"ru": self.ru_raw, "en": self.en_raw}

    def words(self, lang):
        """The texts of every place the entry quotes, in ``lang``."""
        return (_js(lang), _js(lang, "pages/addresses-i18n-%s.js"), _js(lang, "pages/exits-groups-i18n-%s.js"))

    def test_one_version(self):
        self.assertEqual(kaskad.__version__, "3.3.7.15")
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.13, 3\.3\.7\.14 и 3\.3\.7\.15\)")
        first = re.compile(r"(?m)^\*\*(\d+(?:\.\d+){2,3}) — ").search(self.readme, self.readme.index("### Что нового"))
        self.assertEqual(first.group(1), "3.3.7.15")
        self.assertLess(self.readme.index("**3.3.7.15 — "), self.readme.index("**3.3.7.14 — "))

    def test_the_update_sees_it_newer(self):
        fn = re.search(r"(?m)^k3_ver_lt\(\) \{.*\}$", text("install.sh")).group(0)
        for a, b, lt in (("3.3.7.14", "3.3.7.15", 0), ("3.3.7.15", "3.3.7.14", 1), ("3.3.7.15", "3.3.7.15", 1),
                         ("3.3.7.9", "3.3.7.15", 0), ("3.3.7.15", "3.3.8", 0), ("3.3.3", "3.3.7.15", 0)):
            rc = subprocess.run(["bash", "-c", fn + '\nk3_ver_lt "$1" "$2"', "_", a, b], capture_output=True).returncode
            self.assertEqual(rc, lt, (a, b))

    def test_ten_items_both_languages(self):
        self.assertEqual(len(_bullets(self.ru_raw)), 10)
        self.assertEqual(len(_bullets(self.en_raw)), 10)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en_raw.split("**", 2)[2]), "Cyrillic in the English part")
        self.assertIsNone(re.search(r"[Лл]юд|[Чч]еловек", self.ru_raw), "«Пользователи», not «люди»")

    def test_what_was_cut_is_not_promised(self):
        """SPEC-R1 §0а: no traffic by addresses, ALPN h2, sets of domestic groups, /24 of a protocol, IPv4 hand-out."""
        ru, en = _flat(self.ru_raw), _flat(self.en_raw)
        for w in ("ALPN", "h2", "/24", "учёт", "трафик по адрес", "Стандарт", "набор", "сеть протокола",
                  "следующем выпуске", "по адресам IPv4"):
            self.assertNotIn(w, ru)
        for w in ("ALPN", "h2", "/24", "accounting", "traffic by address", "Standard", "preset", "network of a protocol",
                  "next release", "IPv4 addresses hand"):
            self.assertNotIn(w, en)

    def test_the_protocol_settings(self):
        for lang, q in LANGS:
            core, _, _ = self.words(lang)
            ops = _cat(lang, "gw_ops")
            part = _item(self.raw[lang], q % core["ps.mtuF"])
            for w in (core["nav.inbounds"], core["ps.settings"], core["ps.mtuF"], core["ps.tlsVer"], core["c.apply"],
                      core["ps.chg"], core["ps.undoShort"], ops["gw_ops.settings.tls.both"],
                      ops["gw_ops.settings.tls.only13"], ops["gw_ops.settings.tls.only12"],
                      ops["gw_ops.settings.tls_warn.sstp"].split(":")[0], core["ps.closed"].split(" — ")[-1]):
                self.assertIn(q % w, part, lang)
            self.assertIn(core["ps.tlsD"], part, lang)
            for w in ("1280", "1400", "80", "S4", "VLESS TLS", "SSTP", "OpenConnect", "WireGuard", "AmneziaWG"):
                self.assertIn(w, part, lang)

    def test_the_safe_apply(self):
        for lang, q in LANGS:
            core, _, _ = self.words(lang)
            pend, ops = _cat(lang, "gw_pending"), _cat(lang, "gw_ops")
            part = _item(self.raw[lang], q % core["ps.dlMine"])
            for w in (pend["gw_pending.hit.pool"].format(what="WireGuard").split(". ")[0], core["ps.dlMine"],
                      ops["gw_ops.settings.via_xray"].rstrip("."), core["ps.safeBtn"], core["ps.confirm"],
                      core["ps.undoShort"], pend["gw_pending.event.reverted"].format(what="WireGuard")):
                self.assertIn(q % w, part, lang)
            self.assertIn("`kaskadctl gw pending confirm`", part, lang)
            self.assertIn("kaskadctl gw pending confirm", _sh(lang, "gwproto", "GWP_PEND_AUTO"))
            self.assertIn({"ru": "2 мин", "en": "2 min"}[lang], part, lang)

    def test_waiting_for_new_settings(self):
        for lang, q in LANGS:
            core, _, _ = self.words(lang)
            part = _item(self.raw[lang], q % core["ps.waitChip"])
            for w in (core["ps.waitChip"], core["ps.waitHard"], core["ps.waitSoft"], core["ps.whyPort"],
                      core["ps.whyMtu"], core["ps.whyNet"], core["ps.filterWait"], core["nav.peopleShort"],
                      _two(core["ps.ovWait"]).format(title="WireGuard", n=2),
                      _cat(lang, "bot")["bot.gw.waiting"].format(title="SSTP", devices="…")):
                self.assertIn(q % w, part, lang)
        # П4/П5 (T-3315 2.3): a connection on the new port takes «the port changed» off, but not of WireGuard (a live
        # session roams to the new port with the old settings) and SOCKS5 — only a new issue; AmneziaWG restarts: as
        # the rest. What is an issue and what is not (С-1, П-1), the old port back by «Вернуть прежние» (П-2)
        want = {"ru": ("у WireGuard и SOCKS5 — только новая выдача",
                       "«Скачать .conf» (или другой файл настроек) на странице пользователя",
                       "свои новые настройки, скачанные перед применением",
                       "«Показать настройки» на странице и обновление подписки выдачей не считаются",
                       "Прежний порт возвращайте кнопкой «Вернуть прежние»"),
                "en": ("for WireGuard and SOCKS5 — only a new issue",
                       '"Download .conf" (or another settings file) on the user’s page',
                       "your new settings downloaded before applying",
                       '"Show settings" on the page and a refresh of the subscription are not an issue',
                       'Bring the old port back with "Restore previous"')}
        for lang in ("ru", "en"):
            flat = _flat(self.raw[lang])
            for w in want[lang]:
                self.assertIn(w, flat, lang)
            for w in ({"ru": ("(кроме SOCKS5)", "AmneziaWG и SOCKS5"), "en": ("(but SOCKS5)", "AmneziaWG and SOCKS5")}
                      [lang]):
                self.assertNotIn(w, flat, lang)
            # the hint of the panel by the list of the waiting says how the mark goes — in the words of the buttons;
            # the file button of /p/ is «Скачать {ext}» of give-i18n (П7, REV-3315-p256 П-3), not «Скачать файл»
            core, _, _ = self.words(lang)
            hint = core["mtu.waitText"]
            people = core["nav.peopleShort"][:-1] if lang == "ru" else core["nav.peopleShort"]   # «в Пользователях»
            for w in (core["pp.issue"], people, _give(lang)["download"].replace("{ext}", ".conf"),
                      _user(lang)["reveal"]):
                self.assertIn(w, hint, lang)
            self.assertNotIn(_cat(lang, "gw_issue")["gw_issue.button.download_file"].strip("«»“”"), hint, lang)

    def test_the_gateway_network(self):
        for lang, q in LANGS:
            core, _, _ = self.words(lang)
            part = _item(self.raw[lang], q % core["gw.title"])
            for w in ("%s › %s" % (core["nav.settings"], core["st.gw"]), core["st.net"], core["gw.change"],
                      core["gw.title"], core["gw.undo"].replace(" {base}", ""), core["gw.suggest"].format(base="10.78.0.0/16")):
                self.assertIn(q % w, part, lang)
            rfc = _cat(lang, "gw_addr")["gw_addr.err.base_rfc1918"]
            for w in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "Docker", "MikroTik"):
                self.assertIn(w, part, lang)
            self.assertNotIn("enable --base", part, lang)          # З-1 (REV-3315-g1): the way is the panel, no command
            for w in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
                self.assertIn(w, rfc)
            moved = _cat(lang, "gw_net")["gw_net.base.enable_moved"]                # «… меняется в панели: Настройки › …»
            self.assertIn("%s › %s › %s" % (core["nav.settings"], core["st.gw"], core["st.net"]), moved)

    def test_the_server_addresses(self):
        for lang, q in LANGS:
            core, adr, _ = self.words(lang)
            part = _item(self.raw[lang], q % core["adr.title"])
            for w in ("%s › %s › %s" % (core["nav.settings"], core["st.gServer"], core["adr.short"]), core["adr.title"],
                      adr["adr.tag.dynamic"], adr["adr.tag.deprecated"], adr["adr.warn.noRoute6"],
                      adr["adr.tag.echo_other"].format(ip="…"), adr["adr.tag.lock_aaaa"], adr["adr.check"],
                      adr["adr.fIn"], adr["adr.seg.accept"], adr["adr.seg.off"], adr["adr.seg.accept_give"],
                      adr["adr.seg.reserve"], adr["adr.impact"], adr["adr.pfxT"].format(old="…", new="…").rstrip("."),
                      adr["adr.moveRole"].format(ip="…")):
                self.assertIn(q % w, part, lang)
            feed = _cat(lang, "gw_live")["gw_live.event.ip6_prefix_changed"]   # the feed and the bot: the same words
            self.assertTrue(feed.startswith(adr["adr.pfxT"].split(":")[0]), lang)
            former = _sh(lang, "gwstat", "GWS_HOST_IN_ALL").split(" — ")[0]          # «Приём подключений»
            self.assertIn(q % former, part, lang)

    def test_the_exit_block(self):
        for lang, q in LANGS:
            core, adr, _ = self.words(lang)
            part = _item(self.raw[lang], q % adr["adr.oSpread"])
            for w in (adr["adr.bOut"], adr["adr.oSees"], adr["adr.direct"].strip("«»“”"), adr["adr.oDirect"],
                      adr["adr.fOut"], adr["adr.oSpread"], adr["adr.oByRoute"], adr["adr.reset"]):
                self.assertIn(q % w, part, lang)
            self.assertIn("%s (%s)" % (adr["adr.oOwn"], adr["adr.oOwnS"]), part, lang)
        self.assertIn(_js("ru", "pages/addresses-i18n-%s.js")["adr.resetH"].split(";")[0],
                      _item(self.ru_raw, "«Разнести выходы по адресам»"))

    def test_the_ipv6_line_in_the_subscription(self):
        mark = re.search(r'(?m)^SUB6_MARK = "([^"]+)"', text("lib/kaskad/panel/userpage.py")).group(1)
        for lang, q in LANGS:
            core, adr, _ = self.words(lang)
            part = _item(self.raw[lang], q % mark)
            self.assertIn(q % adr["adr.seg.accept_give"], part, lang)
            self.assertIn(adr["adr.gXray"], part, lang)
            for w in ("VLESS Reality", "Hysteria2", "`amneziawg://`", "QR"):
                self.assertIn(w, part, lang)
        self.assertIn("for line in _lines6(runner, st, devices):", text("lib/kaskad/panel/userpage.py"))

    def test_regroup(self):
        for lang, q in LANGS:
            core, _, eg = self.words(lang)
            pol, live = _cat(lang, "gw_policy_ops"), _cat(lang, "gw_live")
            part = _item(self.raw[lang], q % eg["ep.rg.btn"])
            for w in (eg["ep.rg.btn"], eg["ep.tiers"], eg["ep.rg.now"], eg["ep.rg.after"],
                      "%s › %s" % (core["nav.exits"], core["ex.tabGroups"]), "%s › %s" % (core["nav.exits"], core["n2.exRu"]),
                      pol["gw_policy_ops.regroup_note"].rstrip("."),
                      live["gw_live.event.group_regrouped"].split(": ", 1)[1].format(devices="…"),
                      pol["gw_policy_ops.regroup_frozen"].split(":")[0] + "…"):
                self.assertIn(q % w, part, lang)
            self.assertIn({"ru": "по соединениям", "en": "per connection"}[lang], pol["gw_policy_ops.regroup_nothing"])
            self.assertIn({"ru": "по соединениям", "en": "per connection"}[lang], part, lang)
        night = _cat("en", "gw_cli")["gw_cli.pol.night"].split(":")[0]
        self.assertIn('"%s"' % night, _item(self.en_raw, '"Regroup"'))
        self.assertIn("перебалансировк", _cat("ru", "gw_cli")["gw_cli.pol.night"])
        self.assertIn("«Ночной перебалансировке»", _item(self.ru_raw, "«Разложить заново»"))

    def test_the_ssh_menu_the_bot_and_mcp(self):
        for lang, q in LANGS:
            core = _js(lang)
            sh = lambda key: _sh(lang, "gwproto", key)                                  # noqa: E731
            cancel = re.search(r"(?m)^M_%s_C_CANCEL='([^']*)'" % lang, text("lib/kaskad/i18n/sh/core.%s.sh" % lang)).group(1)
            part = _item(self.raw[lang], q % sh("GWP_PORT"))
            for w in (sh("GWP_PORT"), core["ps.chg"], "1) %s 2) %s" % (sh("GWP_SET_APPLY"), cancel), sh("GWP_SET_SAFE"),
                      sh("GWP_UNDO_ITEM"), "1) %s 2) %s" % (sh("GWP_PEND_KEEP"), sh("GWP_PEND_BACK")),
                      (sh("GWP_PEND_PROMPT") % "1:59").strip()):
                self.assertIn(q % w, part, lang)
            for w in ("17 → 1", "`inbound_port`", "`gateway_addresses`", "`gateway_addresses_in`", "--yes`"):
                self.assertIn(w, part, lang)
            self.assertIn("--yes", _cat(lang, "gw_mip")["gw_mip.confirm.cli"])
        # the menu asks with a rollback where the server can't see the admin (VLESS, SOCKS5) — the same branch
        port = re.search(r"(?s)\nk3_gw_proto_port\(\) \{.*?\n\}\n", text("install.sh")).group(0)
        self.assertIn('"$(t GWP_SET_APPLY)" "$(t GWP_SET_SAFE)"', port)
        # the bot tells by itself: settings back / not back, the IPv6 prefix — kinds it pushes, not the feed only
        for kind in ("settings_reverted", "settings_revert_failed", "ip6_prefix_changed"):
            self.assertIn(kind, monitor.GW_KINDS)
            self.assertNotIn(kind, monitor.NO_PUSH)
        # no new MCP tools; the new operational CLI commands are hidden from MCP, the former tools stay
        mcp = json.loads(text("lib/kaskad/mcp/actions.json"))
        for name in ("inbound_port", "gateway_addresses", "gateway_addresses_in"):
            self.assertIn(name, mcp["actions"])
        for name in mcp["actions"]:
            self.assertIsNone(re.search(r"pending|regroup|inbound_settings|gateway_reissue|gateway_net_|addresses_(in6|give|"
                                        r"direct|out|spread|reset)", name), name)
        for cmd in ("gw pending confirm", "gw addresses in6", "gw inbound set"):
            self.assertIn(cmd, mcp["hidden"])

    def test_ipsec(self):
        self.assertIn("meta ipsec exists drop", text("lib/kaskad/gw/net.py"))
        for lang in ("ru", "en"):
            part = _item(self.raw[lang], "IPsec")
            for w in ("IKEv2", "IPsec", "`kaskadctl gw check`"):
                self.assertIn(w, part, lang)


if __name__ == "__main__":
    unittest.main()
