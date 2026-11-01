"""3.3.2, живая проверка (e7e67ff): внутренние коды протоколов (awg2, wg, l2tp…) человеку не показываются — только
названия (AmneziaWG 2.0, WireGuard, L2TP/IPsec); «amneziaWG engine» в kaskadctl gw status — первая буква строки
каталога не опускается у имени. Решения: k3/handoff/I18N-3.3.2.md («Внутренние коды … человеку не показывать»).

Места: панель «Протоколы» (подзаголовок карточки), SSH 16 → 6 → 1 «Копия настроек» (протоколы в составе копии), порт
занят протоколом шлюза (проброс и порт протокола), ошибки и предупреждения ядра, что видят панель/SSH/бот (протокол не
установлен, не выдан, без адаптера, модуль ядра не поднял протокол, импорт AmneziaWG, UFW закрывает порт протокола).
Консоль kaskadctl и журнал — не здесь (там код законен)."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad import backup, conflicts, i18n
from kaskad.errors import KaskadError, UnsupportedError
from kaskad.gw import awgengine, awgkmod, issue_os, model, ops, registry
from kaskad.gw.inbounds import tunnel
from kaskad.i18n import lower_first, tr, use_lang
from tests import jsparts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = re.compile(r"\b(awg[123]|wg|l2tp|ikev2|sstp|ocserv|socks5|pptp|openvpn|vless)\b")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class TitlesTest(unittest.TestCase):
    def test_every_kind_has_a_name(self):
        for kind in model.INBOUND_KINDS:
            t = registry.title(kind)
            self.assertNotEqual(t, kind, kind)
            self.assertIsNone(CODE.fullmatch(t), kind)
            self.assertEqual(t, issue_os.TITLES[kind], kind)
        self.assertEqual(registry.titles(["awg2", "wg", "l2tp"]), ["AmneziaWG 2.0", "WireGuard", "L2TP/IPsec"])

    def test_without_an_adapter_the_table_then_the_code(self):
        with mock.patch.object(registry, "get", return_value=None):
            self.assertEqual(registry.title("sstp"), "SSTP")
            self.assertEqual(registry.title("nope"), "nope")


class LowerFirstTest(unittest.TestCase):
    def test_names_stay(self):
        for s in ("AmneziaWG engine: kernel module", "VLESS is off", "L2TP/IPsec", "IKEv2 x", "WireGuard isn’t",
                  "Kaskad updates", "Telegram bot", ""):
            self.assertEqual(lower_first(s), s)

    def test_words_go_small(self):
        self.assertEqual(lower_first("Движок AmneziaWG: модуль ядра"), "движок AmneziaWG: модуль ядра")
        self.assertEqual(lower_first("The kernel module"), "the kernel module")
        self.assertEqual(lower_first("Клиенты AmneziaWG переподключатся"), "клиенты AmneziaWG переподключатся")

    def test_engine_line_of_gw_status_in_both_languages(self):
        keys = [k for k in i18n.catalog("en") if k.startswith("gw_awgengine.text.")]
        self.assertGreater(len(keys), 5)
        for k in keys:
            self.assertTrue(lower_first(i18n.lookup(k, "en")).startswith("AmneziaWG engine:"), k)
            self.assertTrue(lower_first(i18n.lookup(k, "ru")).startswith("движок AmneziaWG:"), k)   # RU — как в 3.3.1
        self.assertTrue(lower_first(tr("gw_awgengine.reconnect", "en")).startswith("AmneziaWG clients"))

    def test_no_hand_made_lowering_left(self):
        """«text[0].lower() + text[1:]» по строке каталога — причина «amneziaWG engine»: только lower_first."""
        bad = re.compile(r"\[0\]\.lower\(\)\s*\+|\[:1\]\.lower\(\)\s*\+")
        for d, _, files in os.walk(os.path.join(ROOT, "lib", "kaskad")):
            if "vendor" in d:
                continue
            for f in files:
                if f.endswith(".py") and not (d.endswith("i18n") and f == "__init__.py"):
                    self.assertIsNone(bad.search(read(os.path.join(d, f))), os.path.join(d, f))


class PortTakenByProtocolTest(unittest.TestCase):
    """SSH «порт занят»: «the gateway protocol VLESS uses it», без «(vless)»."""

    class Ad(object):
        title = "VLESS"

        def ports(self, st):
            return [("tcp", 443)]

    def test_gateway_ports_name_the_protocol(self):
        with mock.patch.object(model, "enabled_kinds", return_value=["vless"]), \
                mock.patch.object(registry, "get", return_value=self.Ad()):
            ports = conflicts.gateway_ports({})
            self.assertEqual(ports, {("tcp", 443): "VLESS"})
            with use_lang("en"):
                runner = mock.Mock()
                runner.run.return_value = mock.Mock(ok=True, rc=0, out="", err="")
                runner.which.return_value = None
                found = conflicts.check("tcp", 443, runner, env={}, st={})
        g = [c["detail"] for c in found if c["kind"] == "gateway"]
        self.assertTrue(g and g[0].startswith("the gateway protocol VLESS uses it"), found)
        self.assertNotIn("(vless)", g[0])
        self.assertTrue(tr("conflicts.cause.gateway", "ru", title="VLESS").startswith("его занимает протокол шлюза VLESS"))


class CoreErrorsNameProtocolsTest(unittest.TestCase):
    def test_no_adapter(self):
        with mock.patch.object(registry, "get", return_value=None), use_lang("en"):
            with self.assertRaises(UnsupportedError) as c:
                ops.adapter("sstp")
        self.assertIn("SSTP", str(c.exception))
        self.assertIsNone(CODE.search(str(c.exception)))

    def test_texts_with_the_protocol(self):
        for key in ("gw_ops.err.not_installed", "gw_ops.err.not_granted", "gw_ops.err.not_usable",
                    "gw_ops.err.kind_unsupported", "gw_ops.err.pki_unsupported", "gw_ops.warn.no_adapter",
                    "gw_ops.purge.no_adapter"):
            src = read("lib/kaskad/gw/ops.py")
            for m in re.finditer(r'tr\("%s", kind=([^,)]+)' % re.escape(key), src):
                self.assertTrue(m.group(1).startswith("registry.title("), "%s: %s" % (key, m.group(1)))

    def test_kernel_module_warning(self):
        st = {"enabled": True, "loaded": True, "kernel": "6.1.0", "failed": {"awg2": {"kernel": "6.1.0", "reason": "x"}}}
        for lang in ("ru", "en"):
            with use_lang(lang):
                w = awgkmod.warnings(st)
            self.assertEqual(len(w), 1)
            self.assertIn("AmneziaWG 2.0", w[0])
            self.assertIsNone(CODE.search(w[0].replace("amneziawg-go", "")), w[0])

    def test_awg_import_of_a_foreign_file(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, "x.json")
        with open(p, "w") as f:
            json.dump({"kind": awgengine.EXPORT_KIND, "v": 1, "inbounds": {"wg": {}, "awg2": {}}, "secrets": {},
                       "users": [], "devices": []}, f)
        with use_lang("en"), self.assertRaises(KaskadError) as c:
            awgengine.read_export(p, "File")
        self.assertIn("WireGuard", str(c.exception))
        self.assertIsNone(CODE.search(str(c.exception)), str(c.exception))

    def test_ufw_problem_names_the_protocol(self):
        runner = mock.Mock()
        runner.which.return_value = "/usr/sbin/ufw"
        rec = {"kind": "wg", "want": [(51820, "udp"), (8443, "tcp")], "open": [], "sig": None}
        with mock.patch.object(tunnel, "ufw_signature", return_value="s"), \
                mock.patch.object(tunnel, "read_json", return_value={}), \
                mock.patch.object(tunnel, "ufw_live", return_value={"deny": {"51820/udp"}, "allow": set()}), \
                mock.patch.object(tunnel, "ufw_records", return_value=[("/x", rec)]), \
                mock.patch.object(tunnel, "_spec", side_effect=lambda it: "%s/%s" % it), \
                mock.patch.object(tunnel.util, "atomic_write"), use_lang("en"):
            probs = tunnel.ufw_check(runner, repair=False, now=10 ** 9)
        text = " | ".join(p["detail"] for p in probs)
        self.assertIn("51820/udp (WireGuard)", text)
        self.assertIn("8443/tcp (WireGuard)", text)
        self.assertNotIn("(wg)", text)


class BackupSummaryNamesTest(unittest.TestCase):
    """SSH 16 → 6 → 1: «protocols: AmneziaWG 2.0, …» — названия из ядра, коды остаются в summary (консоль, файл)."""

    def test_summary_doc_has_the_names(self):
        st = {"gateway": {"users": [{}], "devices": [], "inbounds": {"awg2": {}, "wg": {}, "l2tp": {}, "vless": {}}}}
        with mock.patch.object(backup, "collect", return_value={backup.F_STATE: json.dumps(st)}), \
                mock.patch.object(backup, "last_snapshot", return_value=None):
            doc = backup.summary_doc(mock.Mock(), ["gateway"])
        self.assertEqual(doc["summary"]["gateway"]["inbounds"], ["awg2", "l2tp", "vless", "wg"])
        self.assertEqual(doc["inbound_titles"], ["AmneziaWG 2.0", "L2TP/IPsec", "VLESS", "WireGuard"])

    @unittest.skipUnless(shutil.which("jq"), "нет jq")
    def test_the_menu_prints_the_names(self):
        sh = read("install.sh")
        body = sh[sh.index("k3_backup_save() {"):]
        body = body[:body.index("\n}\n")]
        self.assertNotIn("$s.gateway.inbounds | join", body)
        prog = re.search(r"--arg panel \"\$\(t BAK_S_PANEL\)\" '(.*?)'\)", body, re.S).group(1)
        doc = {"summary": {"gateway": {"present": True, "users": 1, "devices": 2, "inbounds": ["awg2", "awg3", "l2tp",
                                                                                               "vless", "wg"]},
                           "exits": {"keys": 0, "subs": 0}, "forwards": {"forwards": 0, "names": 0},
                           "site": {"domain": ""}, "bot": {"token": False}, "mcp": {"keys": 0}},
               "inbound_titles": ["AmneziaWG 2.0", "AmneziaWG 3.1", "L2TP/IPsec", "VLESS", "WireGuard"]}
        args = ["jq", "-r"]
        for k, v in (("gw", "gateway: people %s, devices %s, protocols: %s"), ("none", "none"), ("exits", "x %s %s"),
                     ("fwd", "f %s %s"), ("dom", "d %s"), ("bot", "b"), ("mcp", "m %s"), ("panel", "p")):
            args += ["--arg", k, v]
        out = subprocess.run(args + [prog], input=json.dumps(doc), stdout=subprocess.PIPE, universal_newlines=True,
                             check=True).stdout
        line = out.splitlines()[0]
        self.assertEqual(line, "gateway: people 1, devices 2, protocols: AmneziaWG 2.0, AmneziaWG 3.1, L2TP/IPsec, "
                               "VLESS, WireGuard")
        self.assertIsNone(CODE.search(line))


def sh_function(src, name):
    i = src.index("\n%s() {" % name) + 1
    return src[i:src.index("\n}\n", i) + 3]


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "нет jq/bash")
class MtuNoteNamesTest(unittest.TestCase):
    """Меню 17: «MTU: протоколу AmneziaWG 2.0, AmneziaWG 3.1 нужно решение» — не «awg2 awg3»."""

    def test_titles(self):
        sh = read("install.sh")
        self.assertIn('tf GW_MTU_NOTE "$(k3_mtu_narrow_titles)"', sh)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with open(os.path.join(d, "state.json"), "w") as f:
            json.dump({"gateway": {"mtu": {"inbounds": {"awg2": {"offer": "client"}, "wg": {"offer": None},
                                                        "awg3": {"offer": "s4"}}}}}, f)
        src = "".join(sh_function(sh, n) for n in ("k3_gw_title", "k3_mtu_narrow", "k3_mtu_narrow_titles"))
        out = subprocess.run(["bash", "-c", src + 'KASKAD_DIR="$1"; k3_mtu_narrow_titles', "x", d],
                             stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout
        self.assertEqual(out, "AmneziaWG 2.0, AmneziaWG 3.1")


@unittest.skipUnless(shutil.which("node"), "нет node")
class PanelProtocolCardTest(unittest.TestCase):
    """Панель «Протоколы»: подзаголовок карточки — реализация словами, без кода (было «awg2 · kernel module»)."""

    def test_card_subtitle(self):
        js = read("lib/kaskad/panel/static/app.js")
        card = jsparts.function(js, "ibCard")
        self.assertNotIn("r.kind +", card)
        src = """
const WORDS = { ru: { "awg.kernel": "модуль ядра" }, en: { "awg.kernel": "kernel module" } };
let LNG = "en";
const t = (k) => WORDS[LNG][k] || k;
%s
const rows = [
  { kind: "awg2", title: "AmneziaWG 2.0", engine: "amneziawg-go", core: "kernel" },
  { kind: "awg1", title: "AmneziaWG 1.5", engine: "amneziawg-go", core: "userspace" },
  { kind: "wg", title: "WireGuard", engine: "wireguard", core: "" },
  { kind: "l2tp", title: "L2TP/IPsec", engine: "accel-ppp + strongSwan", core: "" },
  { kind: "pptp", title: "PPTP", engine: "accel-ppp", core: "" },
  { kind: "vless", title: "VLESS", engine: "xray", core: "" },
  { kind: "openvpn", title: "OpenVPN", engine: "openvpn", core: "" }];
const en = rows.map(ibEngine); LNG = "ru"; const ru = rows.map(ibEngine);
console.log(JSON.stringify({ en, ru }));
""" % jsparts.const(js, "ibEngine")
        r = subprocess.run(["node", "-e", src], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        got = json.loads(r.stdout.decode())
        # под именем протокола движок — только у AmneziaWG (UI-A2, R-ui6 «Передача» п. 5)
        self.assertEqual(got["en"], ["kernel module", "amneziawg-go", "", "", "", "", ""])
        self.assertEqual(got["ru"][0], "модуль ядра")
        for s in got["en"] + got["ru"]:
            self.assertIsNone(CODE.search(s.replace("amneziawg-go", "")), s)


if __name__ == "__main__":
    unittest.main()
