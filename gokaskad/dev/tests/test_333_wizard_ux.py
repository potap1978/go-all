"""Kaskad 3.3.3 — мастер по UX-333 (FIX-333-wizard-ux): PortsSummary из настоящих портов сервера, DomainSteps
(имя из двух полей, вид имени, деление имени узла на поддомен и домен), выходы двумя вариантами, «Пропустить» только
там, где она не «Дальше», адрес узла в схемах этапа 2, проверка R-333 П3 (Chrome под root — --no-sandbox)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad.i18n import strict_env as tr_strict_env
from kaskad.setup import product, steps
from tests import jsparts
from tests.fakes_nft import World, save_state
from tests.helpers import RootCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read(rel):
    with open(os.path.join(STATIC, rel), encoding="utf-8") as f:
        return f.read()


def node(src):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    if r.returncode:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


class HostPortsTest(unittest.TestCase):
    """steps._host_ports: what listens for the outside — installed and switched on inbounds with their real ports,
    the forwards, the domain of the site, the panel's own port."""

    def test_real_ports(self):
        rows = [{"kind": "wg", "title": "WireGuard", "installed": True, "enabled": True, "ports": ["51820/udp"]},
                {"kind": "l2tp", "title": "L2TP/IPsec", "installed": True, "enabled": True,
                 "ports": ["1701/udp", "500/udp", "4500/udp"]},
                {"kind": "pptp", "title": "PPTP", "installed": True, "enabled": False, "ports": ["1723/tcp"]},
                {"kind": "sstp", "title": "SSTP", "installed": False, "enabled": False, "ports": []}]
        st = {"forwards": [{"proto": "tcp", "port": 8443, "to": "203.0.113.5", "to_port": 443},
                           {"proto": "bogus", "port": 1}],
              "site": {"domain": "k3.example.com", "mode": "site"}}
        cfg = {"public": {"on": True, "port": 41234}}
        with mock.patch("kaskad.gw.ops.inbound_list", return_value=rows):
            got = steps._host_ports(None, st, cfg)
        self.assertEqual([x["kind"] for x in got["inbounds"]], ["wg", "l2tp"])        # off and absent — no ports
        rows[2]["enabled"] = True
        with mock.patch("kaskad.gw.ops.inbound_list", return_value=rows):
            pptp = steps._host_ports(None, st, cfg)["inbounds"][2]
        self.assertEqual(pptp["ip_protos"], [{"name": "gre", "num": 47}])            # R-333 З6: PPTP needs GRE
        self.assertEqual(got["inbounds"][1]["ports"], ["1701/udp", "500/udp", "4500/udp"])
        self.assertEqual(got["forwards"], [{"proto": "tcp", "port": 8443}])
        self.assertEqual((got["domain"], got["panel"]), ("k3.example.com", 41234))

    def test_nothing_known(self):
        with mock.patch("kaskad.gw.ops.inbound_list", side_effect=OSError("x")):
            got = steps._host_ports(None, {}, {"public": {"on": False, "port": 41234}})
        self.assertEqual(got, {"inbounds": [], "forwards": [], "domain": None, "panel": None})

    def test_product_links_are_real_only(self):
        doc = product.doc()
        # «Открыть AdminVPS» — the partner address of AdminVPS from product.HOSTING (OWNER-FEEDBACK №47), no copy
        self.assertEqual(doc["links"]["domain_buy"], product.hosting_url("adminvps"))
        self.assertNotIn("domain_buy", product.LINKS)
        self.assertIsNone(doc["links"]["exit_guide"])       # no published guide yet: the line goes without a link
        self.assertNotIn("promo", doc)                      # no promo code (OWNER-FEEDBACK №44)


class HostingOnePlaceTest(unittest.TestCase):
    """OWNER-FEEDBACK №45: the partner hosting — names, regions, addresses — lives in product.HOSTING only; the wizard
    and the panel get it from the product object, the bot and the SSH menu by substitution."""

    def test_product(self):
        from kaskad.i18n import use_lang
        got = product.doc()["links"]["hosting"]
        self.assertEqual([(x["id"], x["name"], x["url"], x["region"]) for x in got],
                         [("adminvps", "AdminVPS", "https://vk.cc/ct29NQ", "РФ и Европа"),
                          ("cloudvps", "Cloud VPS", "https://vk.cc/cUxAhj", "Беларусь")])
        with use_lang("en"):
            self.assertEqual([x["region"] for x in product.doc()["links"]["hosting"]], ["Russia and Europe", "Belarus"])

    def test_bot_takes_them(self):
        from kaskad.bot import menus
        from kaskad.i18n import use_lang
        for lang in ("ru", "en"):
            with open(os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog", lang, "bot.json"), encoding="utf-8") as f:
                words = json.load(f)["bot.menu.promo"]
            self.assertIn("{eu_url}", words)
            self.assertIn("{by_url}", words)
            with use_lang(lang):
                text = menus.promo()
            self.assertIn("https://vk.cc/ct29NQ", text)
            self.assertIn("https://vk.cc/cUxAhj", text)

    # k3-select: dirs=.,!tests,!*.md — git grep over the whole tree but tests/ and *.md: any file may get a copy
    def test_no_second_copy(self):
        urls = [x["url"] for x in product.HOSTING]
        out = subprocess.run(["git", "grep", "-n", "-F"] + sum([["-e", u] for u in urls], []) +
                             ["--", ".", ":!*.md", ":!tests/"], cwd=ROOT, stdout=subprocess.PIPE,
                             universal_newlines=True).stdout.splitlines()
        if not out:
            self.skipTest("нет git")
        bad = [x for x in out if not x.startswith("lib/kaskad/setup/product.py:")
               and not re.match(r"^install\.sh:\d+:K3_HOST_URL_[A-Z0-9]+='https://", x)]
        self.assertEqual(bad, [])
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            sh = f.read()
        for x in product.HOSTING:                       # generated by build_payload from product.HOSTING
            self.assertIn("K3_HOST_URL_%s='%s'" % (x["id"].upper(), x["url"]), sh)
        self.assertIn('tf BOT_PROMO "$K3_HOST_URL_ADMINVPS" "$K3_HOST_URL_CLOUDVPS"', sh)

    @unittest.skipUnless(shutil.which("bash"), "нет bash")
    def test_ssh_texts(self):
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            sh = f.read()
        blk = re.search(r"^# >>> K3_I18N.*?^# <<< K3_I18N$", sh, re.S | re.M).group(0)
        tf = re.search(r"^tf\(\) \{.*\}$", sh, re.M).group(0)
        for lang in ("ru", "en"):
            out = subprocess.run(["bash", "-c", blk + "\n" + tf + "\nK3_LANG=" + lang +
                                  '\ntf BOT_PROMO "$K3_HOST_URL_ADMINVPS" "$K3_HOST_URL_CLOUDVPS"'],
                                 stdout=subprocess.PIPE, universal_newlines=True, env=dict(tr_strict_env(), LANG="C.UTF-8")).stdout
            self.assertIn("https://vk.cc/ct29NQ", out)
            self.assertIn("https://vk.cc/cUxAhj", out)
            self.assertNotIn("%s", out)

    def test_page(self):
        js = read("setup/setup.js")
        self.assertIn("arr(links.hosting)", js)
        self.assertNotIn("qs-promo", js + read("setup/qs.css"))
        for lang in ("ru", "en"):
            src = read("setup/setup-i18n-%s.js" % lang)
            self.assertNotIn('"dm.promo', src)
            self.assertIn('"ns.ru":', read("ux-i18n-%s.js" % lang))       # the line lives in ux.js (one with the panel)


class PortBusyWordsTest(unittest.TestCase):
    """R-333 П2: the refusal names the protocol, the reason, the action (a command for SSH when a service holds the
    port) — the page adds «Установить снова» and «Не ставить …»."""

    def err(self, detail, kind="listener"):
        from kaskad.errors import ConflictError
        from kaskad.i18n import keeping, tr
        with keeping():                                  # the job of the wizard keeps what tr() said (i18n.Said)
            return self._err(detail, kind, ConflictError, tr)

    def _err(self, detail, kind, ConflictError, tr):
        return ConflictError(tr("gw_ops.err.port_busy", port=1701, protos="udp", why=detail),
                             data={"kind": kind, "conflicts": [{"kind": kind, "detail": detail}]})

    def test_service(self):
        from kaskad.i18n import use_lang
        with use_lang("ru"):
            got = steps._port_busy(self.err("xl2tpd"), "L2TP/IPsec")
        self.assertEqual(got["text"], "L2TP/IPsec не установлен: порт 1701/udp занят другой программой — xl2tpd.")
        # no data of the holder (procs, pids) — no command made up from the words (R-333 С1)
        self.assertEqual((got["port"], got["protos"], got["who"], got["cmd"]), (1701, "udp", "xl2tpd", "ss -lntup 'sport = :1701'"))
        with use_lang("en"):
            self.assertIn("L2TP/IPsec is not installed", steps._port_busy(self.err("xl2tpd"), "L2TP/IPsec")["text"])

    def test_no_command_for_what_is_not_a_plain_program(self):
        self.assertEqual(steps._port_busy(self.err("проброс 1701/udp → 10.0.0.2", kind="forward"), "L2TP/IPsec")["cmd"], "")
        self.assertEqual(steps._port_busy(self.err("x; rm -rf /"), "L2TP/IPsec")["cmd"], "ss -lntup 'sport = :1701'")
        from kaskad.errors import KaskadError
        self.assertIsNone(steps._port_busy(KaskadError("other"), "L2TP/IPsec"))

    def test_page(self):
        js = read("setup/setup.js")
        self.assertIn('t(f.do === "look" ? "pr.busyLook" : "pr.busyDo")', js)
        self.assertIn("...busyNotes(done)", js)
        self.assertIn('btn(t("pr.again"), "inverse", () => installProtocols()', js)


class PortBusyRealChainTest(RootCase):
    """R-333 С1: the command for SSH comes from the data of the conflict (the process, its pid -> its systemd unit), not
    from the words — the real chain of the server's texts: conflicts.check (ss) -> gw.ops.port_conflicts (the words of
    an inbound) -> gw_ops.err.port_busy -> steps._port_busy, in Russian and in English."""

    def busy(self, ss_users, cgroup=None, units=(), lang="ru"):
        from kaskad import paths
        from kaskad.errors import ConflictError
        from kaskad.gw import ops as gops
        from kaskad.i18n import keeping, tr, use_lang
        w = World()
        w.ss = "udp   UNCONN 0      0            0.0.0.0:1701      0.0.0.0:*    %s\n" % ss_users
        for pid, text in (cgroup or {}).items():
            path = paths.P("/proc/%d/cgroup" % pid)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        for u in units:
            path = paths.P("/lib/systemd/system/%s.service" % u)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
        st = save_state()
        with use_lang(lang):
            found = gops.port_conflicts(st, w.runner, "l2tp", "udp", 1701)
            with keeping():
                e = ConflictError(tr("gw_ops.err.port_busy", port=1701, protos="udp", why=gops._busy_text(found)),
                                  data={"kind": found[0]["kind"], "conflicts": found})
            return steps._port_busy(e, "L2TP/IPsec")

    def test_service_by_cgroup_both_languages(self):
        cg = {4242: "0::/system.slice/xl2tpd.service\n"}
        ru = self.busy('users:(("xl2tpd",pid=4242,fd=3))', cg)
        en = self.busy('users:(("xl2tpd",pid=4242,fd=3))', cg, lang="en")
        for got in (ru, en):
            self.assertEqual((got["do"], got["cmd"]), ("stop", "systemctl disable --now xl2tpd"))
        self.assertEqual(ru["text"], "L2TP/IPsec не установлен: порт 1701/udp занят другой программой — его слушает xl2tpd.")
        self.assertNotIn("перестанут", ru["text"])        # the tail of a forward is no word of a protocol
        self.assertIsNone(re.search("[А-Яа-яЁё]", en["text"] + en["cmd"]), en)

    def test_process_is_not_the_unit(self):
        # xray of x-ui: the unit is x-ui (cgroup v1 too); charon without cgroup: its known unit that is on the server
        got = self.busy('users:(("xray-linux-amd64",pid=900,fd=7))', {900: "1:name=systemd:/system.slice/x-ui.service\n"})
        self.assertEqual(got["cmd"], "systemctl disable --now x-ui")
        got = self.busy('users:(("charon",pid=77,fd=12))', units=("strongswan-starter",))
        self.assertEqual(got["cmd"], "systemctl disable --now strongswan-starter")

    def test_unknown_or_shared_shows_the_holder(self):
        look = ("look", "ss -lntup 'sport = :1701'")
        for users, cg in (('users:(("charon",pid=77,fd=12))', None),                      # no cgroup, no unit file
                          ('users:(("nginx",pid=5,fd=6))', {5: "0::/system.slice/nginx.service\n"}),   # a site
                          ('users:(("nc",pid=8,fd=3))', {8: "0::/user.slice/user-0.slice/session-3.scope\n"}),
                          ('users:(("a",pid=1,fd=3),("b",pid=2,fd=3))', None)):           # two processes
            for lang in ("ru", "en"):
                got = self.busy(users, cg, lang=lang)
                self.assertEqual((got["do"], got["cmd"]), look, (users, lang))
                self.assertNotIn("disable --now a", got["cmd"])

    def test_no_process_no_command(self):
        """R-333 П-11: no process holds the port (WireGuard in the kernel) — no «ss» that sends people looking for a
        program; every systemd-* unit is the system."""
        for lang in ("ru", "en"):
            got = self.busy("", lang=lang)
            self.assertEqual((got["do"], got["cmd"]), ("", ""), lang)
        got = self.busy('users:(("systemd-timesyncd",pid=31,fd=3))', {31: "0::/system.slice/systemd-timesyncd.service\n"})
        self.assertEqual((got["do"], got["cmd"]), ("look", "ss -lntup 'sport = :1701'"))


def page_scope():
    js = read("setup/setup.js")
    parts = [jsparts.const(js, n) for n in ("domName", "SLD", "LBL", "subOk", "domFull", "domReady")]
    parts += [jsparts.function(js, n) for n in ("domSplit", "domOk", "portRows", "footKids", "exitsByData")]
    return """
const RU = %s;
const t = (k, v) => { let s = RU[k] || k; Object.keys(v || {}).forEach((x) => { s = s.split("{" + x + "}").join(v[x]); }); return s; };
const arr = (x) => (Array.isArray(x) ? x : []);
const inboundTitle = (k) => k;
const S = { busy: {}, rail: { stages: [] } };
let JOB = false, CUR = "import";
const jobRunning = () => JOB;
const cur = () => CUR;
const h = (tag, a, ...kids) => ({ tag, a: a || {} });
const icon = () => null;
const btn = (label, kind, act, o) => ({ btn: label, k: (o || {}).k, disabled: !!(o || {}).disabled });
const skip = () => {}, next = () => {}, open = () => {}, prevKind = () => "welcome";
%s
""" % (json.dumps(i18n_ru()), "\n".join(parts))


def i18n_ru():
    src = read("setup/setup-i18n-ru.js")
    out = node("const window = {};\n" + src + "\nconsole.log(JSON.stringify(window.KASKAD_SETUP_I18N));")
    return out


@unittest.skipUnless(shutil.which("node"), "нет node")
class PageLogicTest(unittest.TestCase):
    def test_port_rows_and_the_list(self):
        got = node(page_scope() + """
const a = portRows({ inbounds: [{ kind: "wg", title: "WireGuard", ports: ["51820/udp"] },
  { kind: "l2tp", title: "L2TP/IPsec", ports: ["1701/udp", "500/udp", "4500/udp"] },
  { kind: "vless", title: "VLESS", ports: ["443/tcp"] }, { kind: "pptp", title: "PPTP", ports: ["1723/tcp"], ip_protos: [{ name: "gre", num: 47 }] }],
  forwards: [{ proto: "tcp", port: 8443 }], domain: "k3.example.com", panel: 41234 });
const b = portRows({ inbounds: [], forwards: [], domain: null, panel: 443 });
console.log(JSON.stringify({ a, b }));
""")
        rows = dict(got["a"]["rows"])
        self.assertEqual(rows["L2TP/IPsec"], "1701, 500, 4500/udp")
        self.assertEqual(rows["Проброс"], "8443/tcp")
        self.assertNotIn("Сайт и панель на домене", rows)               # 443/tcp is there already (VLESS)
        self.assertEqual(rows["Сертификат Let's Encrypt"], "80/tcp")
        self.assertEqual(rows["Панель управления"], "41234/tcp")
        self.assertEqual(rows["Протокол GRE для PPTP"], "GRE (47)")
        self.assertEqual(got["a"]["list"], ["51820/udp", "1701/udp", "500/udp", "4500/udp", "443/tcp", "1723/tcp", "gre",
                                            "8443/tcp", "80/tcp", "41234/tcp"])          # each port once
        self.assertEqual(got["b"]["list"], ["443/tcp"])

    def test_domain_fields(self):
        got = node(page_scope() + """
console.log(JSON.stringify({
  split: ["vpn.example.com", "example.com", "a.b.example.com", "example.co.uk", "vpn.example.co.uk", "впн.пример.рф"].map(domSplit),
  ok: ["example.com", "пример.рф", "xn--e1afmkfd.xn--p1ai", "example", "-a.com", "a..com", "example.c", "1.2.3.4"].map(domOk),
  sub: ["", "vpn", "VPN-1", "vpn.eu", "-x", "впн"].map(subOk),
  full: [domFull({ sub: " VPN ", dom: "Example.com " }), domFull({ sub: "", dom: "example.com" }), domFull({ sub: "x", dom: "" })] }));
""")
        self.assertEqual(got["split"], [{"sub": "vpn", "dom": "example.com"}, {"sub": "", "dom": "example.com"},
                                        {"sub": "a", "dom": "b.example.com"}, {"sub": "", "dom": "example.co.uk"},
                                        {"sub": "vpn", "dom": "example.co.uk"}, {"sub": "впн", "dom": "пример.рф"}])
        self.assertEqual(got["ok"], [True, True, True, False, False, False, False, False])
        self.assertEqual(got["sub"], [True, True, True, False, False, True])        # кириллица — да (R-333 З2)
        self.assertEqual(got["full"], ["vpn.example.com", "example.com", ""])

    def test_domain_fields_by_the_saved_zone(self):
        """REV-334 №4: после перезагрузки поля делятся по домену, как его вписали (answers.domain_zone); без него —
        по списку зон второго уровня (имена до 3.3.4)."""
        got = node(page_scope() + """
console.log(JSON.stringify({
  zone: [["example.co.nz", "example.co.nz"], ["vpn.example.co.nz", "example.co.nz"], ["vpn.example.com", "vpn.example.com"],
         ["a.b.example.com", "example.com"], ["vpn.example.com", "other.com"], ["vpn.example.com", "com"]].map((x) => domSplit(x[0], x[1])),
  old: ["example.co.nz", "vpn.example.co.nz", "example.com.au", "example.in.ua", "example.co.uk"].map((x) => domSplit(x)) }));
""")
        self.assertEqual(got["zone"], [{"sub": "", "dom": "example.co.nz"}, {"sub": "vpn", "dom": "example.co.nz"},
                                       {"sub": "", "dom": "vpn.example.com"},
                                       {"sub": "a", "dom": "b.example.com"},          # две части — не поддомен: по-старому
                                       {"sub": "vpn", "dom": "example.com"},          # зона не этого имени — по-старому
                                       {"sub": "vpn", "dom": "example.com"}])         # «com» — не домен
        self.assertEqual(got["old"], [{"sub": "", "dom": "example.co.nz"}, {"sub": "vpn", "dom": "example.co.nz"},
                                      {"sub": "", "dom": "example.com.au"}, {"sub": "", "dom": "example.in.ua"},
                                      {"sub": "", "dom": "example.co.uk"}])

    def test_skip_only_where_it_is_not_next(self):
        got = node(page_scope() + """
const has = (f) => footKids(f).some((x) => x && x.k === "foot-skip");
const out = { plain: has({}), asked: has({ skip: true }) };
JOB = true; out.job = has({ skip: true }); JOB = false;
S.busy.stage = true; const b = footKids({ skip: true }).find((x) => x && x.k === "foot-skip"); out.busy = !!b && !b.disabled; S.busy.stage = false;
CUR = "done"; out.done = has({ skip: true });
console.log(JSON.stringify(out));
""")
        # off by default; on where a stage asks; gone while a job runs; not dimmed by a short request (R-333 П5)
        self.assertEqual(got, {"plain": False, "asked": True, "job": False, "busy": True, "done": False})

    def test_wired(self):
        js = read("setup/setup.js")
        self.assertIn('opt("direct", t("ex.direct"), t("ex.directD")), opt("exits", t("ex.exits"), t("ex.exitsD"), true))', js)
        self.assertIn('class: "qs-grid-2 qs-grid-pick"', js)                    # two choices in a row, not three columns
        self.assertNotIn('opt("later"', js)
        self.assertIn("if (d && !arr(d.exits).length && !arr(d.sub_rows).length) main.push(noServerLine());", js)
        self.assertIn('diaP(t("mode.picNode"), true, nodeAddr())', js)
        self.assertIn('diaP(t("mode.picServer"), true, nodeAddr())', js)
        self.assertIn("const nodeAddr = () => ip() || location.hostname", js)
        self.assertIn("[portsSummary(d), h(\"section\"", js)
        self.assertIn('label = phone ? t("dm.noDomShort") : t("dm.noDom")', js)
        self.assertIn('if (r.ok) skip();', js)
        # skip asked: import (something to move), protocols (Next = install), forward (Next = create)
        asks = re.findall(r"\bskip: (true|movable && !fileJob|!!todo.length \|\| !chosen.length|!fwDone)\b", js)
        self.assertEqual(sorted(asks), sorted(["true", "movable && !fileJob", "!!todo.length || !chosen.length", "!fwDone"]))
        ru = read("setup/setup-i18n-ru.js")
        for k in ("dm.s1", "dm.s2", "dm.s3", "dm.s4", "dm.cfTip", "ps.title", "ps.copy", "dm.s1Where", "vid.soon"):
            self.assertIn('"%s":' % k, ru)
        self.assertIn('"pr.fw": "Порты покажем в конце — со списком для хостера."', ru)

    def test_domain_explicit_and_locked(self):
        js = read("setup/setup.js")
        # R-333 З1: by itself only the check of the domain that works already; a new name — by «Выпустить сертификат»
        f = jsparts.function(js, "autoCert")
        self.assertIn("if (!isWorkDomain(u.checked) || u.certAuto", f)
        self.assertIn('return { main, foot: { next: { label: t("dm.certGo"), act: certNow, busy: !!(S.busy.cert || S.busy.certPrep) } } };', js)
        self.assertIn('acts[0] = btn(t("dm.switchGo"), "primary", certNow', js)
        # R-333 С3: the fields are locked while the certificate or the site is being set up; the progress stays seen
        self.assertIn('const locked = !!running(["cert", "site", "template"]) || !!S.busy.certPrep;', js)
        self.assertEqual(js.count("disabled: locked"), 2)
        self.assertIn('if (ok || running(["cert"])) {', js)
        self.assertIn("disabled: !!o.disabled,", jsparts.function(js, "input"))
        # R-333 З3: a working domain — plain «Дальше»; during the first check «без домена» is closed (same label)
        self.assertIn("if ((ok && !certFailed) || workDomain().length) return { main, foot: { next: {} } };", js)
        self.assertIn("if (checking) return { main, foot: { next: { label, disabled: true, busy: true } } };", js)
        got = node(page_scope() + jsparts.function(js, "workDomain") + """
const isWorkDomain = (name) => !!name && workDomain().indexOf(domName(name)) >= 0;
S.st = { site: { domain: "xn--b1an2a.xn--e1afmkfd.xn--p1ai", domain_u: "впн.пример.рф", cert: true } };
const a = [isWorkDomain("впн.пример.рф"), isWorkDomain("XN--B1AN2A.xn--e1afmkfd.xn--p1ai"), isWorkDomain("пример.рф")];
S.st.site.cert = false; a.push(isWorkDomain("впн.пример.рф")); S.st = {}; a.push(workDomain().length);
console.log(JSON.stringify(a));
""")
        self.assertEqual(got, [True, True, False, False, 0])

    def test_old_later_keeps_working_exits(self):
        # R-333 С2: the old answer «Позже»/«без выхода» is decided by the data — exits there and the route through
        # them: «Через выходы» (Next leaves the route); no exits or the route direct/block: «без выхода»
        got = node(page_scope() + """
console.log(JSON.stringify([
  exitsByData({ exits: [{ id: "a" }], route: { via: "pool" } }),
  exitsByData({ exits: [{ id: "a" }], route: { via: "exit:a" } }),
  exitsByData({ exits: [{ id: "a" }] }),
  exitsByData({ exits: [{ id: "a" }], route: { via: "direct" } }),
  exitsByData({ exits: [{ id: "a" }], route: { via: "block" } }),
  exitsByData({ exits: [], route: { via: "pool" } }),
  exitsByData(null)]));
""")
        self.assertEqual(got, ["exits", "exits", "exits", "direct", "direct", "direct", "direct"])
        js = read("setup/setup.js")
        self.assertIn('if (a === "direct" || a === "later") { u.choice = ""; u.fromData = true; } else u.choice = "exits";', js)
        self.assertIn("if (u.fromData && !u.choice) { u.fromData = false; u.choice = exitsByData(r.body.data);", js)
        self.assertIn('} else nextBtn = { disabled: true };', js)          # not decided yet: nothing is sent
        self.assertNotIn('u.choice = a === "direct" || a === "later" ? "direct"', js)

    def test_plan_failed_is_no_dead_end(self):
        js = read("setup/setup.js")                    # R-333 С4: «Повторить» in the note and «Пропустить» below
        self.assertIn('foot: { skip: !!res && res.state !== "ok", next: { label: t("pr.install"), disabled: true, busy: !res } }', js)
        # «Пропустить» between «Установить» and the start of the job does nothing (R-333 П-5)
        self.assertIn('if (!Object.keys(S.busy).some((x) => S.busy[x])) skip();', js)

    def test_layout_scan_chrome_as_root(self):
        with open(os.path.join(ROOT, "tests", "setup", "layout_scan.mjs"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('process.getuid && process.getuid() === 0 ? ["--no-sandbox"] : []', src)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("node"), "нет node")
class DomainFootTest(unittest.TestCase):
    """R-333 Н1: the buttons at the bottom of «Домен» by what happened — the domain looks at the server: «Выпустить
    сертификат»; the issue failed (the limit of Let's Encrypt, a closed 80): «Дальше без домена» that skips the stage,
    not a dead end; П-9: from the first press the fields are locked and a second press issues nothing."""

    def run_view(self, body):
        from tests.test_qs330c_live3 import wizard_scope
        js = read("setup/setup.js")
        view = re.search(r"\n  VIEW\.domain = \(\) => \{\n.*?\n  \};\n", js, re.S).group(0)
        src = wizard_scope() + """
const VIEW = {};
const domainSteps = () => ({ steps: true });
const moveNote = () => null;
const matchMedia = () => ({ matches: false });
let SKIPPED = 0;
const skip = () => { SKIPPED++; };
""" + view + "\n(async () => {\n" + body + "\n})().catch((e) => { console.error(e.stack); process.exit(1); });"
        return node(src)

    def test_cert_error_is_no_dead_end(self):
        got = self.run_view("""
const u = ui("domain");
setDomFields(u, "vpn.example.org"); await checkDomain();
const ready = VIEW.domain().foot.next;
S.res.cert = { ok: false, state: "error", error: { code: "limit", text: "too many certificates" } };
const failed = VIEW.domain().foot.next;
out.steps = [];
await failed.act();
console.log(JSON.stringify({ ready: ready.label, failed: failed.label, disabled: !!failed.disabled, steps: out.steps, skipped: SKIPPED,
  again: certCard(u).kids.find((x) => x && x.live).acts[0].k }));
""")
        self.assertEqual(got["ready"], "Выпустить сертификат")
        self.assertEqual(got["failed"], "Дальше без домена")
        self.assertFalse(got["disabled"])
        self.assertEqual(got["steps"], [["domain", {"plan": "none"}]])
        self.assertEqual(got["skipped"], 1)
        self.assertEqual(got["again"], "cert-again")                       # the retry — in the card

    def test_busy_from_the_first_press(self):
        js = read("setup/setup.js")
        f = jsparts.function(js, "startCert")
        self.assertIn("S.busy.certPrep = true; render();", f)
        self.assertLess(f.index("S.busy.certPrep = true"), f.index("await prepMove()"))
        self.assertIn("S.busy.certPrep", jsparts.function(js, "certNow"))


class SummaryExitsTest(unittest.TestCase):
    """UX-333 §8: «Выходы» of the summary — the keys added by hand by their names, a subscription as one entry with the
    number of its keys (the page says «{name} ({n} ключа)»), not every key of it."""

    def test_by_subscription(self):
        ex = [{"name": "Вена"}, {"name": "a", "source": {"sub": "s-1", "name": "sub.example.net"}},
              {"name": "b", "source": {"sub": "s-1", "name": "sub.example.net"}}, {"name": "c", "source": {"sub": "s-2", "name": "ga"}}]
        self.assertEqual(steps._summary_subs(ex), [{"name": "sub.example.net", "keys": 2}, {"name": "ga", "keys": 1}])
        js = read("setup/setup.js")
        self.assertIn('arr(ex.names).concat(arr(ex.subs).map((x) => t("d.sExitsSub"', js)


@unittest.skipUnless(shutil.which("node"), "нет node")
class R333iExitsExtrasTest(unittest.TestCase):
    """R-333i: × подписки — вопрос панели (с ключами / оставить ключи / отмена), «Удаляю…» на строке, отказ — тостом (З1);
    состояние подписки как в панели (П2); подсказка внизу этапа 5 — по кнопкам компонентов (З2)."""

    def run_js(self, body, lang="ru"):
        js = read("setup/setup.js")
        parts = [jsparts.function(js, n) for n in ("exitUxRows", "askSubDel", "delExitRow", "unsentHint")]
        i18n = read("setup/setup-i18n-ru.js") + ("\n" + read("setup/setup-i18n-en.js") if lang == "en" else "")
        uxi = read("ux-i18n-ru.js") + ("\n" + read("ux-i18n-en.js") if lang == "en" else "")
        src = """
const window = {};
%s
%s
const L = Object.assign({}, window.KASKAD_SETUP_I18N, window.KASKAD_SETUP_I18N_EN || {});
const LANG = %s;
const UXD = Object.assign({}, window.KASKAD_UX_I18N, LANG === "en" ? window.KASKAD_UX_I18N_EN : {});
const t = (k, v) => { let s = L[k] || k; Object.keys(v || {}).forEach((x) => { s = s.split("{" + x + "}").join(v[x]); }); return s; };
const tn = (k, n) => { const f = t(k).split("|"); return LANG === "en" ? f[n === 1 ? 0 : 1] : f[n %% 10 === 1 && n %% 100 !== 11 ? 0 : n %% 10 >= 2 && n %% 10 <= 4 && (n %% 100 < 10 || n %% 100 >= 20) ? 1 : 2]; };
const UX = () => ({ t: (lang, k) => UXD[k] || k });
const S = { modal: null, res: {} }, U = {}, out = { steps: [], toasts: [], reloads: [], renders: 0 };
const ui = (k) => (U[k] = U[k] || {});
const arr = (x) => (Array.isArray(x) ? x : []);
const render = () => { out.renders++; };
const toast = (x) => out.toasts.push(x);
const reload = (n) => out.reloads.push(n);
let NEXT = { ok: true };
const step = async (name, body) => { out.steps.push([name, body]); return NEXT; };
const h = (tag, a, ...kids) => ({ tag, a: a || {}, kids: [].concat(...kids).filter((x) => x !== null && x !== undefined) });
const note = (tone, title, text) => ({ note: tone, title, text });
const btn = (label, kind, act, o) => ({ btn: label, kind, act, k: (o || {}).k });
const FIELDS = {};
const document = { querySelector: (sel) => (sel in FIELDS ? { value: FIELDS[sel] } : null) };
%s
(async () => {
%s
})().catch((e) => { console.error(e.stack); process.exit(1); });
""" % (i18n, uxi, json.dumps(lang), "\n".join(parts), body)
        return node(src)

    D = """const D = { route: { via: "exit:e-2" }, exits: [{ id: "e-1", name: "Вена", source: { sub: "s-1" } }, { id: "e-2", name: "Варшава", source: { sub: "s-1" } },
  { id: "e-9", name: "Мой", state: "ok", usable: true }],
  sub_rows: [{ id: "s-1", name: "sub.example.net", keys: 2, auto: true, stale: false, error: false, exits: ["e-1", "e-2"] },
    { id: "s-2", name: "ga", keys: 0, auto: true, stale: true, error: true, text: "не обновилась: 404", exits: [] }] };"""

    def test_sub_delete_asks_as_the_panel(self):
        got = self.run_js(self.D + """
const rows = exitUxRows(D);
const r = { status: rows.map((x) => [x.id, x.status, x.statusText || ""]) };
rows[0].onDel();
const m = S.modal, foot = m.foot(() => { S.modal = null; });
r.title = m.title; r.body = m.body().map((x) => x && (x.a ? x.a.text : x.title + " " + x.text));
r.btns = foot.filter(Boolean).map((b) => [b.btn, b.k]);
foot.find((b) => b && b.k === "sd-keep").act();
await new Promise((ok) => setTimeout(ok, 0));
r.keep = out.steps.slice();
r.modal = S.modal;
NEXT = { ok: false, state: "error", error: { code: "bad_input", text: "Такой подписки нет — обновите страницу." } };
exitUxRows(D)[0].onDel(); S.modal.foot(() => {}).find((b) => b && b.k === "sd-yes").act();
r.busy = exitUxRows(D).filter((x) => x.busy).map((x) => [x.id, x.busy]);
await new Promise((ok) => setTimeout(ok, 0));
r.with = out.steps[1]; r.toasts = out.toasts; r.left = ui("exits").deleting;
exitUxRows(D)[1].onDel();
r.none = S.modal.foot(() => {}).filter(Boolean).map((b) => b.k);
console.log(JSON.stringify(r));
""")
        self.assertEqual(got["status"], [["s-1", "ok", ""], ["s-2", "err", "не обновилась: 404"], ["e-9", "ok", ""]])
        self.assertEqual(got["title"], "Удалить подписку «sub.example.net»?")
        self.assertIn("В пуле 2 ключа этой подписки", got["body"][0])
        self.assertIn("Варшава", got["body"][1])                         # the key chosen as the route — warned
        self.assertEqual([k for _, k in got["btns"]], ["sd-no", "sd-keep", "sd-yes"])
        self.assertEqual(got["btns"][2][0], "Удалить с её ключами")
        self.assertEqual(got["keep"], [["exit-sub-del", {"id": "s-1", "keep": True}]])
        self.assertIsNone(got["modal"])
        self.assertEqual(got["busy"], [["s-1", "Удаляю…"]])
        self.assertEqual(got["with"], ["exit-sub-del", {"id": "s-1"}])
        self.assertEqual(got["toasts"], ["Удалено: sub.example.net", "Такой подписки нет — обновите страницу."])
        self.assertEqual(got["left"], "")
        self.assertEqual(got["none"], ["sd-no", "sd-yes"])                 # no keys — no «оставить ключи»

    def test_unsent_hint_names_the_buttons_of_the_components(self):
        for lang, bot, key, both in (("ru", "«Отправить тест»", "«Добавить»", "«Отправить тест» и «Добавить»"),
                                     ("en", "“Send test”", "“Add”", "“Send test” and “Add”")):
            got = self.run_js("""
const r = { none: unsentHint() };
FIELDS['[data-ux="bot"] .ux-in--token'] = "7012345678:AAH"; r.bot = unsentHint();
FIELDS['[data-ux="agent"] textarea'] = "ssh-ed25519 AAAA"; r.both = unsentHint();
FIELDS['[data-ux="bot"] .ux-in--token'] = "  "; r.key = unsentHint();
console.log(JSON.stringify(r));
""", lang)
            self.assertEqual(got["none"], "")
            self.assertIn(bot, got["bot"])
            self.assertIn(key, got["key"])
            self.assertIn(both, got["both"])
            if lang == "en":
                self.assertNotRegex(json.dumps(got, ensure_ascii=False), "[А-Яа-яЁё]")
        js = read("setup/setup.js")
        self.assertIn("on: { input: unsentSoon }", jsparts.function(js, "extraUx"))
        self.assertIn("MutationObserver(unsentSoon)", jsparts.function(js, "extraUx"))
        self.assertNotIn('"x.unsent"', read("setup/setup-i18n-ru.js"))

    def test_one_open_at_a_time(self):
        f = jsparts.function(read("setup/setup.js"), "extraUx")
        self.assertIn("else if (u.ux[id].close) u.ux[id].close();", f)
        self.assertIn("if (on && was && was !== id) render();", f)

    def test_held_qr_is_not_readable(self):
        # R-333i П1: «Выдать заново» — прежний QR не только приглушён: рисунок размыт, камера его не прочтёт
        css = read("setup/qs.css")
        self.assertRegex(css, r"\.qs-card\[data-held\] \.gx-qrart[^{]*\{ filter: blur\(\d+px\); \}")


def i18n(lang):
    src = read("setup/setup-i18n-%s.js" % lang)
    name = "KASKAD_SETUP_I18N" + ("_EN" if lang == "en" else "")
    return node("const window = {};\n" + src + "\nconsole.log(JSON.stringify(window.%s));" % name)


@unittest.skipUnless(shutil.which("node"), "нет node")
class OptionalSubdomainTest(unittest.TestCase):
    """Поддомен необязателен (решение владельца 2026-10-13): подпись, подсказка под полями, предупреждение при пустом
    поддомене (обычный текст, не ошибка), A-запись «@»."""

    TEXTS = {
        "ru": ("Поддомен (необязательно)", "Оставьте пустым — будет использован сам домен."),
        "en": ("Subdomain (optional)", "Leave it empty to use the domain itself."),
    }

    def steps(self, lang, sub, dom, extra=None):
        js = read("setup/setup.js")
        parts = [jsparts.const(js, n) for n in ("domName", "SLD", "LBL", "subOk", "domFull", "domReady")]
        parts += [jsparts.function(js, n) for n in ("domSplit", "domOk", "domGuess", "domainSteps")]
        return node("""
const I = %s;
const t = (k, v) => { let s = I[k] || k; Object.keys(v || {}).forEach((x) => { s = s.split("{" + x + "}").join(v[x]); }); return s; };
const flat = (k) => [].concat(...k.map((x) => Array.isArray(x) ? flat(x) : [x])).filter((x) => x != null && x !== false);
const h = (tag, a, ...kids) => ({ tag, a: a || {}, kids: flat(kids) });
const S = { busy: {}, product: { links: {} } };
const running = () => null, ip = () => "203.0.113.5", copyBtn = () => null, warnTip = () => null;
const lastTry = () => null, dnsBlock = () => null, domainEdited = () => {}, checkDomain = () => {};
const render = () => {}, document = { querySelector: () => null };
const input = (k, v, on, o) => ({ tag: "input", a: { "data-k": k, aria: o.aria, bad: !!o.bad }, kids: [] });
const dStep = (n, done, title, why, kids) => ({ tag: "step", a: { n, title, why }, kids: flat(kids) });
const card = (a, ...k) => ({ tag: "card", a, kids: flat(k) });
const btn = (label, kind, act, o) => ({ tag: "button", a: { "data-k": o.k, text: label }, kids: [], act });
%s
const u = Object.assign({ sub: %s, dom: %s }, %s), tree = domainSteps(u);
const press = (n) => { if (!n || typeof n !== "object") return; if (n.a && n.a["data-k"] === "dm-sub-move") n.act(); (n.kids || []).forEach(press); };
const out = { k: {}, texts: [], rec: [], steps: {}, dot: null };
const walk = (n) => { if (!n || typeof n !== "object") { if (n != null) out.texts.push(String(n)); return; }
  if (n.tag === "step") out.steps[n.a.n] = { title: n.a.title, why: n.a.why };
  if (n.a && n.a.class === "qs-dnames__dot") out.dot = { text: n.a.text, off: n.a["data-off"] == null ? null : n.a["data-off"] };
  if (n.a && n.a["data-k"]) out.k[n.a["data-k"]] = { text: n.a.text || null, cls: n.a.class || "", role: n.a.role || null, aria: n.a.aria || null };
  if (n.a && n.a.text) out.texts.push(n.a.text);
  if (n.a && n.a.class === "qs-arec__v" || (n.a && /qs-arec__v/.test(n.a.class || ""))) out.rec.push(n.a.text);
  (n.kids || []).forEach(walk); };
walk(tree);
press(tree); out.after = { sub: u.sub, dom: u.dom };
console.log(JSON.stringify(out));
""" % (json.dumps(i18n(lang)), "\n".join(parts), json.dumps(sub), json.dumps(dom), json.dumps(extra or {})))

    def test_label_and_hint(self):
        for lang, (label, hint) in self.TEXTS.items():
            got = self.steps(lang, "vpn", "example.com")
            self.assertIn(label, got["texts"], lang)                           # подпись над полем
            self.assertEqual(got["k"]["dm-sub"]["aria"], label, lang)         # и имя поля для чтеца
            self.assertEqual(got["k"]["dm-sub-hint"]["text"], hint, lang)     # подсказка под полями (без вопроса «Ваш домен — …?»)
            self.assertNotIn("dm-sub-none", got["k"], lang)                   # с поддоменом предупреждения нет
            self.assertEqual(got["rec"][1], "vpn", lang)                      # A-запись — на поддомен

    def test_empty_subdomain_warns_and_points_at_at(self):
        for lang in ("ru", "en"):
            got = self.steps(lang, "", "example.com")
            warn = got["k"]["dm-sub-none"]
            self.assertIn("«@»" if lang == "ru" else "“@”", warn["text"], lang)
            self.assertEqual(warn["cls"], "qs-small", lang)                   # обычный текст, не ошибка
            self.assertIsNone(warn["role"], lang)
            self.assertFalse(any("qs-err" in v["cls"] for v in got["k"].values()), lang)
            self.assertEqual(got["rec"][:2], ["A", "@"], lang)                # имя A-записи — «@», сам домен
            self.assertIn("example.com", got["texts"], lang)                  # адрес сервера — сам домен

    def test_no_warning_before_the_domain(self):
        got = self.steps("ru", "", "")
        self.assertNotIn("dm-sub-none", got["k"])                             # домена ещё нет — говорить не о чем
        self.assertIn("dm-sub-hint", got["k"])

    def test_full_address_in_the_domain_field_asks(self):
        """REV-334 П2: «vpn.example.com» целиком в «Ваш домен», поддомен пуст — подсказка с частями из ввода и
        «Перенести»; молча не делим (зона может быть делегирована): A-запись пока «@», предупреждение «@» на месте."""
        cases = {"vpn.example.com": ("vpn", "example.com"), "VPN.Example.com": ("vpn", "example.com"),
                 "vpn.example.co.uk": ("vpn", "example.co.uk"), "впн.пример.рф": ("впн", "пример.рф")}
        for lang in ("ru", "en"):
            for dom, (s, d) in cases.items():
                got = self.steps(lang, "", dom)
                g = got["k"]["dm-sub-guess"]
                self.assertEqual(g["cls"], "qs-small qs-dguess", (lang, dom))       # подсказка, не ошибка
                self.assertIsNone(g["role"], (lang, dom))
                want = i18n(lang)["dm.subGuess"].replace("{d}", d).replace("{s}", s)
                self.assertIn(want, got["texts"], (lang, dom))
                self.assertIn("dm-sub-move", got["k"], (lang, dom))
                self.assertEqual(got["rec"][:2], ["A", "@"], (lang, dom))          # сами не делим
                self.assertIn("dm-sub-none", got["k"], (lang, dom))
                self.assertNotIn("dm-sub-hint", got["k"], (lang, dom))              # «Оставьте пустым» спрятано
                self.assertEqual(got["after"], {"sub": s, "dom": d}, (lang, dom))  # «Перенести» — части по полям
        self.assertIn("Ваш домен — example.com? Тогда «vpn» впишите в «Поддомен».", self.steps("ru", "", "vpn.example.com")["texts"])

    def test_no_guess_for_a_plain_domain_or_with_a_subdomain(self):
        for sub, dom in (("", "example.com"), ("", "example.co.uk"), ("", "example.com.au"), ("vpn", "a.example.com"),
                         ("", "vpn..example.com"), ("", ""), ("bad sub", "vpn.example.com"),
                         # зоны второго уровня и динамический DNS: example.pp.ua — сам домен, не «pp.ua?»
                         ("", "example.pp.ua"), ("", "example.kh.ua"), ("", "example.biz.ua"), ("", "example.org.il"),
                         ("", "example.ne.jp"), ("", "example.or.jp"), ("", "example.ac.jp"), ("", "example.gov.uk"),
                         ("", "example.edu.au"), ("", "example.co.ke"), ("", "example.gen.nz"),
                         ("", "example.duckdns.org"), ("", "example.eu.org"), ("", "example.ddns.net")):
            got = self.steps("ru", sub, dom)
            self.assertNotIn("dm-sub-guess", got["k"], (sub, dom))
            self.assertNotIn("dm-sub-move", got["k"], (sub, dom))
            if not sub:
                self.assertIn("dm-sub-hint", got["k"], (sub, dom))             # без вопроса подсказка на месте
        # а адрес целиком в таких зонах — по-прежнему вопрос
        for dom, (s, d) in (("vpn.example.pp.ua", ("vpn", "example.pp.ua")), ("vpn.example.duckdns.org", ("vpn", "example.duckdns.org"))):
            got = self.steps("ru", "", dom)
            self.assertIn("Ваш домен — %s? Тогда «%s» впишите в «Поддомен»." % (d, s), got["texts"], dom)

    def test_no_guess_when_the_name_already_points_here(self):
        """vpn.example.com целиком в «Ваш домен», DNS этого имени в порядке (сертификат выпущен) — домен работает как
        есть, вопрос «Ваш домен — example.com?» не нужен. DNS другого имени (поле поменяли) — вопрос есть."""
        for lang in ("ru", "en"):
            got = self.steps(lang, "", "vpn.example.com", {"checked": "vpn.example.com", "dns": {"dns": "ok"}})
            self.assertNotIn("dm-sub-guess", got["k"], lang)
            self.assertIn("dm-sub-hint", got["k"], lang)
            for extra in ({"checked": "vpn.example.com", "dns": {"dns": "wait"}}, {"checked": "old.example.com", "dns": {"dns": "ok"}}):
                got = self.steps(lang, "", "vpn.example.com", extra)
                self.assertIn("dm-sub-guess", got["k"], (lang, extra))

    def test_free_domain_and_the_dot_only_with_a_subdomain(self):
        """REV-334 №2: «Сам домен останется свободным» и точка между полями — только с поддоменом."""
        for lang in ("ru", "en"):
            free = i18n(lang)["dm.s2Free"]
            got = self.steps(lang, "vpn", "example.com")
            self.assertTrue(got["steps"]["2"]["why"].endswith(" " + free), lang)
            self.assertEqual(got["dot"], {"text": ".", "off": None}, lang)
            got = self.steps(lang, "", "example.com")
            self.assertNotIn(free, got["steps"]["2"]["why"], lang)
            self.assertEqual(got["dot"]["off"], "", lang)                    # точка скрыта (место держит)

    def test_texts(self):
        ru, en = i18n("ru"), i18n("en")
        # REV-334 №2–3: поддомен — по желанию; без «почта перестанет»; парковка — не данность; добавить или заменить
        self.assertEqual(ru["dm.subNone"], "Без поддомена A-запись — «@», для самого домена. Если на нём уже работает "
                         "сайт, он перестанет открываться — тогда лучше впишите поддомен.")
        self.assertEqual(ru["dm.s2"], "Впишите домен (поддомен — по желанию)")
        self.assertEqual(en["dm.s2"], "Enter your domain (subdomain optional)")
        # REV-334 П1: менять только A с тем же именем, CNAME — удалить и добавить A (A рядом с CNAME не бывает);
        # у «@» обычно ещё MX и TXT
        self.assertEqual(ru["dm.s3Why"], "У регистратора в разделе «DNS» добавьте запись. Если A-запись с таким именем уже "
                         "есть — измените её; если CNAME — удалите его и добавьте A-запись; MX и TXT не трогайте. Заполните поля так:")
        self.assertEqual(en["dm.s3Why"], "At your registrar, in the “DNS” section, add a record. If an A record with this name "
                         "already exists, change it; if it is a CNAME, delete it and add an A record; leave MX and TXT records "
                         "as they are. Fill in:")
        self.assertNotIn("(или CNAME)", ru["dm.s3Why"])
        self.assertNotIn("(or CNAME)", en["dm.s3Why"])
        for x in (ru["dm.s3Why"], en["dm.s3Why"]):
            self.assertNotRegex(x, r"замените|replace")
        # REV-334 П4: «it» было двусмысленно (сайт или домен)
        self.assertIn("If a website already runs on it, that site will stop loading", en["dm.subNone"])
        self.assertNotIn("stop opening", en["dm.subNone"])
        self.assertEqual(ru["dm.subGuess"], "Ваш домен — {d}? Тогда «{s}» впишите в «Поддомен».")
        self.assertEqual(en["dm.subGuess"], "Is your domain {d}? Then enter “{s}” in “Subdomain”.")
        for x in (ru["dm.subNone"], en["dm.subNone"], ru["dm.s3Why"], en["dm.s3Why"]):
            self.assertNotRegex(x.lower(), r"почт|mail|парков|parking")
        self.assertNotIn("свободн", ru["dm.s2Why"])
        self.assertNotIn("free", en["dm.s2Why"])
        for k in ("dm.sub", "dm.subHint", "dm.subNone", "dm.subGuess", "dm.subGuessMove", "dm.s2", "dm.s2Why", "dm.s2Free", "dm.s3Why"):
            self.assertIsNone(re.search(r"[А-Яа-яЁё]", en[k]), k)            # EN без кириллицы
            self.assertNotRegex(ru[k] + en[k], r"\.ru\b")                     # нейтрально, без зоны .ru
        self.assertIn("“@”", en["dm.subNone"])
