"""Kaskad 3.3.0, the live check №2 (k3/stand/logs/T-M6-qs330b.md), the panel:

    №11  a person with devices is deleted only after the name typed (DECISIONS-3.3.0, «Пакет правок k3-fix330»): the
         trash opens the window with the name at once; without devices — the confirmation in place;
    №12  deleting a device with VLESS (or a person with one) says «VLESS переподключится у всех на пару секунд» — as
         ``gw device del``;
    №13  the tile «Порт» of a protocol never tears «48445/udp»: each port unbroken, the column no narrower than it;
    №17  a key without a name of its own (its name is «вид · сервер»): the second line does not repeat it — the panel
         and the wizard.

The real functions of app.js (tests/jsparts.py) in node; the rows of the exits are the server's own
(kaskad.gw.exits.public). Node-less: skipped."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from kaskad.gw import exits
from tests import jsparts

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
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


@unittest.skipUnless(shutil.which("node"), "нет node")
class DeleteConfirmTest(unittest.TestCase):
    def run_js(self, script):
        js = read("app.js")
        parts = [jsparts.const(js, n) for n in ("arr", "hasVless")]
        parts += [jsparts.function(js, n) for n in ("delUser", "delDevice")]
        src = """
const t = (k, v) => k + (v ? " " + JSON.stringify(v) : "");
const asked = [], acts = [];
let ANSWER = false;
const typedConfirm = async (o) => { asked.push(o); return ANSWER; };
const act = async (fn, o) => { acts.push(o.ok); return true; };
const API = { users: { del: () => null }, devices: { del: () => null } };
const ALL_GW = [];
const USERS = {};
const findUser = (id) => USERS[id] || null;
%s
(async () => { %s })().catch((e) => { console.error(e); process.exit(1); });
""" % ("\n".join(parts), script)
        return node(src)

    def test_person_with_devices_types_the_name_and_hears_about_vless(self):
        doc = self.run_js("""
USERS.u1 = { id: "u1", name: "Мама", devices: [{ id: "d1", access: ["awg2", "vless"] }, { id: "d2", access: ["wg"] }] };
USERS.u2 = { id: "u2", name: "Папа", devices: [{ id: "d3", access: ["awg2"] }] };
USERS.u3 = { id: "u3", name: "Гости", devices: [] };
const r1 = await delUser("u1");                 // passed on the window: nothing deleted
ANSWER = true;
await delUser("u2");
await delUser("u3");                             // no devices: no window (the confirmation in place was before)
console.log(JSON.stringify({ r1, asked: asked.map((o) => [o.expected, o.warn]), acts }));
""")
        self.assertFalse(doc["r1"])
        self.assertEqual(doc["asked"], [["Мама", "pp.delVless"], ["Папа", ""]])
        self.assertEqual(doc["acts"], ["pp.userDeleted", "pp.userDeleted"])

    def test_device_with_vless_says_it_reconnects_everybody(self):
        doc = self.run_js("""
ANSWER = false;
delDevice({ id: "d1", name: "Ноутбук", access: ["vless", "awg2"] });
delDevice({ id: "d2", name: "Телефон", access: ["awg2"] });
await new Promise((ok) => setTimeout(ok, 0));
console.log(JSON.stringify(asked.map((o) => [o.expected, o.warn])));
""")
        self.assertEqual(doc, [["Ноутбук", "pp.delVless"], ["Телефон", ""]])

    def test_the_trash_of_a_person_with_devices_opens_the_name_window_at_once(self):
        js = read("app.js")
        # 3.3.3 (UX-333 §5): «Удалить человека» — пункт «⋯»; с устройствами — окно с именем сразу. UI-B 23.10: «⋯»
        # человека одно на всех ширинах (ppMenu; прежняя деталь телефона М3 с userMore заменена листом человека)
        self.assertIn('label: t("pp.k1.delete"), icon: "trash-bin", danger: true, on: async () => { if (u.devices.length) delUser(u.id);', js)
        self.assertIn('U.menu(ppMenu(all), { label: t("c.moreOf", { title: u.name })', js)      # строка телефона
        self.assertIn('"pp.delVless": "VLESS переподключится у всех на пару секунд: xray шлюза перезапустится, чтобы '
                      'устройство отключилось сразу."', read("i18n-ru.js"))
        # the same words as the CLI (kaskad.gw.xray.CUT_NOTE)
        from kaskad.gw import xray
        self.assertTrue(xray.CUT_NOTE.startswith("VLESS переподключится у всех на пару секунд: xray шлюза"))


@unittest.skipUnless(shutil.which("node"), "нет node")
class PortTileTest(unittest.TestCase):
    def test_ports_never_torn_inside(self):
        js = read("app.js")
        src = """
const txt = (s) => ({ text: s });
const el = (tag, a, kids) => ({ tag, cls: a.class, text: a.text, kids: kids || [] });
%s
const one = (s) => portsNode(s).kids.map((k) => (k.cls ? "[" + k.text + "]" : k.text)).join("");
console.log(JSON.stringify([one("48445/udp"), one("1701, 500, 4500/udp"), one("443/tcp; 443/udp"), one("—")]));
""" % jsparts.const(js, "portsNode")
        self.assertEqual(node(src), ["[48445/udp]", "[1701], [500], [4500/udp]", "[443/tcp]; [443/udp]", "[—]"])
        self.assertIn('kvTile(t("ib.port"), "", portsNode(ibPorts(r) || DASH))', js)

    def test_the_port_column_is_no_narrower_than_a_port(self):
        css = read("styles.css")
        self.assertIn(".gx-nowrap { white-space: nowrap; }", css)
        cols = re.findall(r"\.gx-pkv \{[^}]*grid-template-columns: ([^;]+);", css)
        self.assertEqual(cols, ["minmax(min-content, 1fr) minmax(0, 1fr)", "minmax(min-content, 1fr) repeat(2, minmax(0, 1fr))"])


@unittest.skipUnless(shutil.which("node"), "нет node")
class UnnamedKeyTest(unittest.TestCase):
    def rows(self):
        unnamed = {"id": "e-0123456789", "index": 1, "kind": "awg", "version": "1.5", "host": "x6.example.org:8635"}
        named = {"id": "e-abcdefabcd", "index": 2, "kind": "awg", "version": "2.0", "host": "x6.example.org:7870",
                 "name": "kaskad-n2 · AmneziaWG 2.0"}
        exits.set_where([unnamed, named])
        return [exits.public(unnamed), exits.public(named)]

    def test_server_rows(self):
        a, b = self.rows()
        self.assertEqual((a["name"], a["where"], a["named"]), ("AmneziaWG 1.5 · x6.example.org:8635",) * 2 + (False,))
        self.assertTrue(b["named"])

    def test_panel_drops_the_repeated_line(self):
        from tests.test_panel_js_contract import node as app_node
        doc = app_node("console.log(JSON.stringify(app.normExits({ exits: DATA }).rows.map((r) => r.unnamed)));",
                       self.rows())
        self.assertEqual(doc, [True, False])
        js = read("app.js")
        self.assertIn("subNode: r.unnamed ? null : [exitKindText(r)", js)

    def test_wizard_drops_the_repeated_line(self):
        js = read("setup/setup.js")
        # 3.3.3: a row of ExitsInput — the second line (host) only when it is not the name itself
        self.assertIn('host: e.where && e.where !== e.name ? e.where : ""', js)


if __name__ == "__main__":
    unittest.main()
