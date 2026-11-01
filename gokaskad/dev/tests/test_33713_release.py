"""Kaskad 3.3.7.13 — what it adds to 3.3.7.12: the number fields of a group condition and of an exit's quota are on
the screen again (3.3.7.12 hid them with ``display: contents``), the condition form in even columns with the phrase
told once and «×» at every check, the score window says «но не выше 999» when the ceiling cuts the limit and has no
list of reference servers, ``kaskadctl gw policy`` shows the tier of new exits and the night rebalancing, a form
after 409 lists the edits at once, no favicon request on /p/, no error when two checks make the bench dir at once:
one version (3.3.7.13 newer than 3.3.7.12 for the update), and «Что нового» of 3.3.7.13 for the admin, tied to what
the code says and does."""
import json
import os
import re
import subprocess
import unittest

import kaskad
from tests.test_3371_release import entry, text

ST = "lib/kaskad/panel/static/"


def _js(lang, name="i18n-%s.js"):
    return dict(re.findall(r'(?m)^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', text(ST + name % lang)))


def _cat(lang, name):
    return json.loads(text("lib/kaskad/i18n/catalog/%s/%s.json" % (lang, name)))


def _flat(s):
    return re.sub(r"\s+", " ", s)


def _bullets(raw):
    """The «- …» items of a part, each on one line."""
    return [_flat(b) for b in re.split(r"(?m)^- ", raw)[1:]]


def _css_rules(css):
    """(selector, body) of the innermost rules — an @media/@container head never gets inside ``[^{}]``."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.findall(r"([^{}]+)\{([^{}]*)\}", css)


class Release33713Test(unittest.TestCase):
    def setUp(self):
        self.readme = text("README.md")
        self.e = entry(self.readme, "3.3.7.13")
        self.ru_raw, sep, self.en_raw = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.7.13")
        self.ru, self.en = _flat(self.ru_raw), _flat(self.en_raw)

    def test_one_version(self):
        # 3.3.7.14 is above it now (tests/test_33714_release.py): the version is its, the heading names 3.3.7.13 too
        self.assertTrue(kaskad.__version__.startswith("3.3.7."), kaskad.__version__)
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertEqual(m.group(1), kaskad.__version__)
        self.assertRegex(self.readme, r"(?m)^## Kaskad 3 \(версии [^)]*, 3\.3\.7\.11, 3\.3\.7\.12, 3\.3\.7\.13[ ,]")
        self.assertLess(self.readme.index("**3.3.7.14 — "), self.readme.index("**3.3.7.13 — "))
        self.assertLess(self.readme.index("**3.3.7.13 — "), self.readme.index("**3.3.7.12 — "))

    def test_the_update_sees_it_newer(self):
        """``k3_ver_lt`` of install.sh (sort -V): 3.3.7.13 is newer than 3.3.7.12 and 3.3.7.9, older than 3.3.8."""
        fn = re.search(r"(?m)^k3_ver_lt\(\) \{.*\}$", text("install.sh")).group(0)
        for a, b, lt in (("3.3.7.12", "3.3.7.13", 0), ("3.3.7.13", "3.3.7.12", 1), ("3.3.7.13", "3.3.7.13", 1),
                         ("3.3.7.9", "3.3.7.13", 0), ("3.3.7.13", "3.3.8", 0), ("3.3.3", "3.3.7.13", 0)):
            rc = subprocess.run(["bash", "-c", fn + '\nk3_ver_lt "$1" "$2"', "_", a, b], capture_output=True).returncode
            self.assertEqual(rc, lt, (a, b))

    def test_the_number_fields_first_and_on_the_screen(self):
        """The first item: the threshold, «держится» and the three quota fields — and no CSS rule hides them."""
        for lang, raw, q in (("ru", self.ru_raw, "«%s»"), ("en", self.en_raw, '"%s"')):
            core, eg = _js(lang), _js(lang, "pages/exits-groups-i18n-%s.js")
            first = _bullets(raw)[0]
            path = lambda *ks: " › ".join(core[k] for k in ks)
            for w in (path("nav.exits", "n2.gGroups"), path("nav.exits", "n2.exList"), eg["ep.holds"],
                      eg["ep.xTitle"], eg["ep.qLimit"], eg["ep.qReset"], eg["ep.qWarn"]):
                self.assertIn(q % w, first, (lang, w))
            for w in (eg["ep.holdSamples"], eg["ep.holdMinutes"]):
                self.assertIn(w, first, (lang, w))
            self.assertIn("3.3.7.12", first)
        js = text(ST + "pages/exits-groups.js")
        self.assertGreaterEqual(js.count('classList.add("kk-eg-num")'), 3)    # threshold, «держится», quota
        self.assertIn('class: "kk-eg-nums"', js)                              # the exit row's wrapper, not the fields
        for name in sorted(os.listdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ST))):
            if not name.endswith(".css"):
                continue
            for sel, body in _css_rules(text(ST + name)):
                if re.search(r"\.kk-eg-num(?![\w-])", sel):
                    self.assertNotRegex(body, r"display\s*:\s*(contents|none)", (name, sel.strip()))

    def test_what_3_3_7_12_could_save_unseen(self):
        """With the fields hidden, a new check («+ условие») and a change of «Что проверять» put 0 into the threshold,
        a quota turned on kept QUOTA_NEW: the first item asks to check them, with the numbers of the code."""
        js = text(ST + "pages/exits-groups.js")
        self.assertRegex(js, r"sd\.conds\.push\(\{ metric: m, op: u\.ops\[0\], value: 0,")         # «+ условие»
        self.assertRegex(js, r'v === "service" \? false : 0 \}')                                  # «Что проверять»
        q = re.search(r"const QUOTA_NEW = \{[^}]*limit_gb: (\d+),[^}]*warn_pct: (\d+),", js)
        self.assertTrue(q)
        for lang, raw, q_, gb, ask in (
                ("ru", self.ru_raw, "«%s»", "ГБ", "Проверьте условия и квоты, заданные в 3.3.7.12."),
                ("en", self.en_raw, '"%s"', "GB", "Check the conditions and quotas set in 3.3.7.12.")):
            eg = _js(lang, "pages/exits-groups-i18n-%s.js")
            first = _bullets(raw)[0]
            self.assertIn(q_ % ("+ " + eg["ep.addCond"]), first, lang)
            self.assertIn(q_ % eg["ep.metricL"], first, lang)
            self.assertIn("%s %s" % (q.group(1), gb), first, lang)
            self.assertIn("%s%%" % q.group(2), first, lang)
            self.assertTrue(first.strip().endswith(ask), (lang, first[-80:]))
            self.assertRegex(first, r"нулём" if lang == "ru" else r"as 0\b")

    def test_the_condition_form(self):
        """Labels in a column, the phrase above and only the times below, «×» at every check (inactive alone)."""
        for lang, part, q in (("ru", self.ru, "«%s»"), ("en", self.en, '"%s"')):
            eg = _js(lang, "pages/exits-groups-i18n-%s.js")
            for k in ("ep.if", "ep.holds", "ep.then", "ep.backWhen", "ep.exL", "ep.backManualL", "ep.pushL",
                      "ep.condDel"):
                self.assertIn(q % eg[k], part, (lang, k))
            for k in ("ep.leaveIn", "ep.backIn"):
                self.assertIn(q % eg[k].replace("{d}", "…"), part, (lang, k))
            self.assertIn(eg["ep.err.conds"].lower(), part.lower())
            self.assertIn(q % "×", part)
        js = text(ST + "pages/exits-groups.js")
        self.assertRegex(js, r'tip: t\(one \? "ep\.err\.conds" : "ep\.condDel"\).*dis: one')
        self.assertRegex(js, r"const says = \(\) => \{ const s = ruleTimes\(r, g\); summary\.textContent = s;")

    def test_the_score_window(self):
        """D-143 (владелец 29.10): the weak link — more than 30 % below the mean of the links (``score_abs.WEAK_GAP``),
        «Почему итог не выше» — the one line ``ovb.why.lim`` the notes quote; the word «слабое звено» on the radar is
        ``ovb.weak``; no refs."""
        import kaskad.score_abs as SA
        self.assertEqual((SA.MARGIN, SA.WEAK_GAP), (350, 30))
        self.assertEqual(SA._shown(1234.5), 999)                              # the ceiling on the screen
        self.assertIn('"weak_gap": SA.WEAK_GAP', text("lib/kaskad/bench/hostscore.py"))
        cap = SA._shown(77 + SA.MARGIN * min(1.0, 77 / 100.0))                 # З1 REV-score-rename: by the formula
        self.assertEqual(cap, 346)
        for lang, part, said, word, name, q, again in (
                ("ru", self.ru, "«Слабое звено — отдача 77: итог не выше %d, без предела было бы 790»" % cap,
                 "«слабое звено»", "отдача", "«%s»", "Итог прошлых проверок пересчитан по новому"),
                ("en", self.en, '"Weak link — upload 77: the total is at most %d, without the limit 790"' % cap,
                 '"weak link"', "upload", '"%s"', "The total of past checks is recalculated by the new rule")):
            ovb = _js(lang, "pages/ovb-i18n-%s.js")
            self.assertIn(said[1:-1], ovb["ovb.why.lim"].format(name=name, v=77, cap=cap, raw=790), lang)
            self.assertIn(said, part, lang)
            self.assertIn(again, part, lang)                                  # З3: the old checks — recalculated
            self.assertNotRegex(ovb["ovb.dr.howX"], r"\), [А-ЯA-Z]", lang)     # З2: «диск 5). Слабое»
            self.assertIn(word, part, lang)
            self.assertEqual(word[1:-1], ovb["ovb.weak"], lang)
            self.assertIn("{gap}", ovb["ovb.dr.howX"], lang)                 # the 30 % comes from the server
            self.assertIn("30%", part, lang)
            self.assertFalse([k for k in ovb if k.startswith(("ovb.why.free", "ovb.why.limTop", "ovb.why.usual"))], lang)
            for k in ("ovb.score", "ovb.why", "ovb.dr.how"):
                self.assertIn(q % ovb[k], part, (lang, k))
            self.assertIn("ovb.calib", ovb)                                    # «which links are checked» stays
            self.assertFalse([k for k in ovb if k.startswith(("ovb.ref", "ovb.cityMsk"))], lang)
        self.assertNotRegex(text(ST + "pages/ovb.js"), r"\.refs\b|refsT")
        self.assertNotRegex(text("lib/kaskad/bench/hostscore.py"), r'"refs"\s*:')
        for r in SA.REFS:                                                      # no hoster, tariff, cpu in the text
            for k in ("hoster", "tariff", "cpu"):
                self.assertNotIn(str(r[k]), self.e)

    def test_policy_view_and_the_forms_after_409(self):
        """The CLI says the panel's words; the default route and the exit form rebase at once after 409."""
        for lang, part, q in (("ru", self.ru, "«%s»"), ("en", self.en, '"%s"')):
            core, eg, kit = _js(lang), _js(lang, "pages/exits-groups-i18n-%s.js"), _js(lang, "kit-i18n-%s.js")
            cli = _cat(lang, "gw_cli")
            self.assertEqual(cli["gw_cli.pol.new_tier"].split(":")[0], eg["ep.newTier"])
            self.assertEqual(cli["gw_cli.pol.night"].split(":")[0], eg["ep.night"])
            for w in (eg["ep.newTier"], eg["ep.night"], " › ".join((core["nav.exits"], core["n2.exDefault"])),
                      kit["kit.applyAgain"]):
                self.assertIn(q % w, part, (lang, w))
            ru_keys = cli["gw_cli.pol.new_tier_ru"].split(":")[0].split()[-1]  # «ключи» / "keys"
            self.assertIn(q % ru_keys, part)
            gone = "изменили, пока вы правили" if lang == "ru" else "changed while you were editing"
            self.assertIn(gone, core["ex.routeChangedList"])
            self.assertIn(gone, eg["ep.exChanged"])
            self.assertIn(q % ("… " + gone), part)
            self.assertIn("`kaskadctl gw policy`", part)
        pol = text("lib/kaskad/gw/cli_policy.py")
        for k in ("gw_cli.pol.new_tier_ru", "gw_cli.pol.new_tier", "gw_cli.pol.night"):
            self.assertIn('"%s"' % k, pol)
        self.assertRegex(text(ST + "app.js"), r'onConflict: [^\n]*defSync\(true\)[^\n]*ex\.routeChangedList')
        self.assertRegex(text(ST + "pages/exits-groups.js"), r"onConflict: [^\n]*exSync\(id, true\)")

    def test_the_user_page_and_the_bench_dir(self):
        page = text(ST + "user.html")
        self.assertEqual(re.findall(r'<link rel="icon" href="([^"]*)">', page), ["data:,"])
        self.assertIn("img-src 'self' data:", text("lib/kaskad/panel/userpage.py"))
        self.assertIn("`/p/…`", self.ru)
        self.assertIn("`/p/…`", self.en)
        self.assertIn("404", self.ru)
        self.assertRegex(text("lib/kaskad/util.py"), r"os\.makedirs\(path, mode, exist_ok=True\)")

    def test_several_ips_and_socks5_udp(self):
        """dev-mip in 3.3.7.13: the items name what the code has — the commands, the MCP tools, the menu words, the
        refusals and the honest lines (the main address of the check, the Telegram API proxy, SOCKS5 UDP)."""
        cli = text("lib/kaskad/gw/cli.py")
        for sub in ("add", "in", "exit"):
            self.assertIn('asub_a.add_parser("%s"' % sub, cli)
        self.assertIn('p1.add_argument("--all"', cli)
        self.assertIn('p1.add_argument("--group"', cli)
        tools = json.loads(text("lib/kaskad/mcp/actions.json"))["actions"]
        for t in ("gateway_addresses", "gateway_addresses_in", "gateway_exit_src"):
            self.assertIn(t, tools)
            for part in (self.ru, self.en):
                self.assertIn("`%s`" % t, part)
        for c in ("`kaskadctl gw addresses`", "`kaskadctl gw addresses add`", "`kaskadctl gw addresses in A,B`",
                  "`--all`", "`--group`", "`main`"):
            self.assertIn(c, self.ru)
            self.assertIn(c, self.en)

        def sh(area, lang, key):
            m = re.search(r"(?m)^M_%s_%s='([^']*)'" % (lang, key), text("lib/kaskad/i18n/sh/%s.%s.sh" % (area, lang)))
            return m.group(1)

        for lang, part, q in (("ru", self.ru, "«%s»"), ("en", self.en, '"%s"')):
            mip, s5 = _cat(lang, "gw_mip"), _cat(lang, "gw_in_socks5")
            self.assertIn(q % (sh("gw", lang, "GW_TITLE") + " › " + sh("gw", lang, "GW_M_HOST")), part)
            self.assertIn(q % sh("gwstat", lang, "GWS_HOST_IN_ALL").split(" — ")[0], part)
            self.assertIn(q % sh("gwstat", lang, "GWS_HOST_SRC").split(" — ")[0], part)
            for k in ("client_out", "panel_out", "via_out", "host_not_in", "not_here", "route_other", "same_endpoint"):
                self.assertIn("gw_mip.err." + k, mip)
            self.assertIn(mip["gw_mip.src.bench"], part)                      # the check measures the main address
            self.assertIn(mip["gw_mip.limit.hy2"], part)
            engine = "на движке «программа»" if lang == "ru" else "on the userspace"   # AmneziaWG «программа»
            self.assertIn(engine, mip["gw_mip.limit.awg"])
            self.assertIn(engine, part)
            self.assertIn("VLESS, Hysteria2, Shadowsocks, Trojan " + ("и" if lang == "ru" else "and") + " VMess",
                          mip["gw_mip.src.tg"])
            rule = "Россия напрямую" if lang == "ru" else "Russian sites direct"    # open UDP skips it — said
            self.assertIn(rule, s5["gw_in_socks5.udp_lists"])
            self.assertIn(q % rule, part)
            self.assertIn("gw_in_socks5.udp_mixed", s5)
            self.assertIn("Kaskad", sh("egress", lang, "EGRESS_LEG_TABLE_FOREIGN"))
        self.assertIn('UDP_LOGIN = "_udp"', text("lib/kaskad/gw/inbounds/socks5.py"))

    def test_stream_mode(self):
        """dev-stream in 3.3.7.13: the item names the panel's own words — where it is switched on, the bar, «Показать QR»,
        the tick of the names and its labels, «Скопировать команду», «Чего режим не закрывает» — and the 10 s of a
        reveal; the flag lives in the browser only."""
        stream = text(ST + "stream.js")
        self.assertIn("_ttl: 10000", stream)
        self.assertIn('KEY = "kaskad.stream"', stream)
        for lang, part, q, sec in (("ru", self.ru, "«%s»", "10 секунд"), ("en", self.en, '"%s"', "10 seconds")):
            i18n = _js(lang)
            self.assertIn(q % i18n["stm.title"], part)
            self.assertIn(q % (i18n["nav.settings"] + " › " + i18n["st.look"]), part)
            for k in ("stm.qrShow", "stm.bar", "stm.barHide", "stm.barOff", "stm.names", "stm.cmd", "stm.nc"):
                self.assertIn(q % i18n[k], part, k)
            for k in ("stm.user", "stm.device"):
                self.assertIn(q % i18n[k].replace("{n}", "1" if k == "stm.user" else "3"), part, k)
            self.assertIn(sec, part)
            self.assertIn(i18n["stm.local"].split(" — ")[1].rstrip("."), part)    # this browser only

    def test_order_in_groups_and_the_router_icon(self):
        """dev-order in 3.3.7.13: the items name the panel's own words of the order in a tier and of the steps of a
        domestic group, the CLI and the MCP fields; the router icon replaced the «РФ» mark and the screen reader says
        «Внутренняя группа»; ⚠РФ of banned resources stays."""
        mcp = json.loads(text("lib/kaskad/mcp/actions.json"))["actions"]["exit_groups_list"]["description"]
        for lang, part, q, mark in (("ru", self.ru, "«%s»", "«РФ»"), ("en", self.en, '"%s"', '"RU"')):
            core, eg, kit = _js(lang), _js(lang, "pages/exits-groups-i18n-%s.js"), _js(lang, "kit-i18n-%s.js")
            ovb = _js(lang, "pages/ovb-i18n-%s.js")
            for w in (core["nav.exits"] + " › " + core["n2.gGroups"], core["nav.exits"] + " › " + core["n2.exRu"],
                      eg["ep.method.manual"], eg["ep.ord.back"], eg["ep.ord.col.str"], eg["ep.ord.out"].split(" · ")[0],
                      kit["kit.ord.no"], kit["kit.what"], eg["ep.ord.was"].replace("{x}", "…"),
                      eg["ep.ord.oneStep"].replace("{name}", "…"), ovb["ovb.xnT"], core["c.save"], core["ex.domMark"]):
                self.assertTrue(q % w in part, (lang, w))
            for w in (core["nav.overview"][:-1], core["nav.scheme"][:-1], mark, core["rf.mark"],   # «Обзоре», ⚠РФ — as before
                      "`kaskadctl gw policy`", "`exit_groups_list`", "`method`", "`order`"):
                self.assertTrue(w in part, (lang, w))
            self.assertIn("order=manual", mcp[lang])

    def test_russian_and_english(self):
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", self.en))
        for part in (self.ru, self.en):
            self.assertNotRegex(part, r"\.ru\b")
            self.assertIsNone(re.search(r"\b(REV|SEC|DQA|D-\d+|Н-\d|З-\d|М-\d|С-\d|П-\d|Р-\d|Ш\d|В\d|А\d|Q\d|AC\d|T-\d+|"
                                        r"it1|it2|bl1|Т\d|Л\d)\b", part))
            self.assertNotRegex(part, r"№\s?\d")                                          # no numbers of remarks
            self.assertNotRegex(part, r"\.(js|py|css|json|md|html)\b")                    # no file names
            self.assertNotRegex(part, r"(?i)kk-|ensure_dir|refs\b|409")                   # no code words
        self.assertIsNone(re.search(r"(?i)\bnodes?\b", self.en))
        self.assertNotRegex(self.ru, r"(?i)человек|\bлюд")
        self.assertNotRegex(self.en, r"(?i)\b(person|people)\b")
        # one item per change, the same count in both languages
        self.assertEqual(len(_bullets(self.ru_raw)), len(_bullets(self.en_raw)))


if __name__ == "__main__":
    unittest.main()
