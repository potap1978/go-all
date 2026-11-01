"""3.3.2: the findings of the live check on .67 (k3/stand/logs/T-67-332.md, №2–№5, №7).

№2 «Появится как …» names a .conf by its file, №3 /p/ tells «on the server, not for this device» from «not on the
server», №4 SSH 17 → 2 names the exit, not its id, №5 the menu 18 line tells how the tunnel really asks, №7 the journal
of the panel speaks the server's language and the drop-ins of the inbounds are neutral English, refreshed on update."""

import json
import os
import io
import re
import shutil
import unittest
from unittest import mock

from kaskad import paths, unit_refresh, util
from kaskad.gw import gate, registry
from kaskad.i18n import set_lang
from kaskad.sysrun import FakeRunner
from tests.helpers import RootCase
from tests.test_qs330b_reboot import gw_state
from tests.test_332f_give import HAS_NODE, issuance, prelude, read, run_node
from tests.test_i18n_terms_sh import bash4, shquote
from tests.test_ssh_live332 import run_lib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def model(r, **o):
    o.setdefault("lang", "ru")
    js = prelude(o["lang"]) + "console.log(JSON.stringify(G.model(%s, %s)));" % (json.dumps(r), json.dumps(o))
    return run_node(js)


@unittest.skipUnless(HAS_NODE, "нет node")
class ShowsAsTest(unittest.TestCase):
    """№2: a vpn:// / vless:// link is named by its description, a .conf — by the file's name without «.conf»."""

    def test_names(self):
        dev = {"name": "qa-iPhone"}
        r = issuance("awg3", "ios")
        self.assertEqual(model(r, device=dev)["keyName"], "goKaskad PRO · AmneziaWG 3.1 · qa-iPhone")   # DefaultVPN: vpn://
        self.assertEqual(model(r, device=dev, app="amneziavpn")["keyName"], "goKaskad PRO · AmneziaWG 3.1 · qa-iPhone")
        self.assertEqual(model(r, device=dev, app="amneziawg")["keyName"], "goKaskadPro-mama-awg31")            # .conf
        self.assertEqual(model(issuance("wg", "ios"), device=dev)["keyName"], "goKaskadPro-mama-wg")
        self.assertEqual(model(issuance("vless", "ios"), device=dev)["keyName"], "goKaskad PRO · VLESS · qa-iPhone")
        en = model(issuance("awg3", "android", lang="en"), device=dev, app="amneziawg", lang="en")
        self.assertEqual(en["keyName"], "goKaskadPro-mama-awg31")

    def test_window_line(self):
        js = prelude() + r"""
const R = %s;
const out = {};
const g = G.mount({ viewer: "admin", lang: "ru", device: { name: "qa-iPhone" }, onProto: () => {},
  drawQr: () => document.createElement("i"), drawLinkQr: () => document.createElement("b") });
document.body.appendChild(g.el); g.set(R);
for (const app of ["amneziawg", "amneziavpn"]) {
  fire(gk(g.el, "app:" + app), "click");
  out[app] = g.el.all((n) => n.className === "gs-step__as").map((n) => n.textContent);
}
console.log(JSON.stringify(out));
""" % json.dumps(issuance("awg3", "ios"))
        got = run_node(js)
        self.assertEqual(got["amneziawg"], ["Появится как «goKaskadPro-mama-awg31»"])
        self.assertEqual(got["amneziavpn"], ["Появится как «goKaskad PRO · AmneziaWG 3.1 · qa-iPhone»"])


@unittest.skipUnless(HAS_NODE, "нет node")
class PersonAltsTest(unittest.TestCase):
    """№3: on /p/ the other protocols tell what the admin must do: add to this device, or turn on on the server."""

    def test_states(self):
        r = issuance("awg3", "huawei", server=("awg3", "l2tp"), device=("awg3",), where="page")
        p = model(r, viewer="person")
        got = [(x["proto"], x["state"], x["act"], x["status"], x["ask"]) for x in p["alts"]]
        self.assertEqual(got, [
            ("l2tp", "device_off", "ask", "Включён на сервере, но не для этого устройства",
             "Попросите администратора добавить L2TP/IPsec этому устройству"),
            ("pptp", "server_off", "ask", "Не включён на сервере", "Попросите администратора включить PPTP")])
        en = model(issuance("awg3", "huawei", lang="en", server=("awg3", "l2tp"), device=("awg3",), where="page"),
                   viewer="person", lang="en")
        self.assertEqual([(x["status"], x["ask"]) for x in en["alts"]], [
            ("Enabled on the server, but not for this device", "Ask your admin to add L2TP/IPsec to this device"),
            ("Not enabled on the server", "Ask your admin to enable PPTP")])
        # the panel: the same states, the admin's words and buttons
        a = model(issuance("awg3", "huawei", server=("awg3", "l2tp"), device=("awg3",)), actions={"grant": True, "enable": True})
        self.assertEqual([(x["state"], x["act"], x["status"]) for x in a["alts"]],
                         [("device_off", "grant", "Включён на сервере, не выдан этому устройству"),
                          ("server_off", "enable", "Не включён на сервере")])

    def test_dictionaries_agree(self):
        ru, en = read("give-i18n-ru.js"), read("give-i18n-en.js")
        for k in ("pDevice:", "pServer:", "askAdd:"):
            self.assertIn(k, ru)
            self.assertIn(k, en)
        self.assertNotIn("pOff", ru + en)


PEOPLE = {"ok": True, "users": [{"id": "u1", "name": "Анна", "route": None,
                                 "route_effective": {"via": "exit:e-a1da8a655a", "fail": "block"}}]}
DEVICES = {"ok": True, "devices": [
    {"id": "d1", "user": "u1", "login": "l1", "name": "qa-iPhone", "platform": "ios", "access": ["awg3"],
     "route": {"via": "exit:e-a1da8a655a", "fail": "direct"}},
    {"id": "d2", "user": "u1", "login": "l2", "name": "qa-Pixel", "platform": "android", "access": ["awg3"],
     "route": {"via": "exit:e-gone", "fail": "block"}},
    {"id": "d3", "user": "u1", "login": "l3", "name": "qa-Mac", "platform": "macos", "access": ["awg3"], "route": None,
     "route_effective": {"via": "pool", "fail": "block"}}]}
EXITS = {"ok": True, "exits": [{"id": "e-a1da8a655a", "name": "kaskad-t3 · AmneziaWG 3.1"}]}


@unittest.skipUnless(bash4() and shutil.which("jq"), "нужны bash 4+ и jq")
class PeopleRouteNameTest(unittest.TestCase):
    """№4: SSH 17 → 2 — «маршрут: выход kaskad-t3 · AmneziaWG 3.1», не «выход e-a1da8a655a»; выхода нет — id."""

    def run_menu(self, exits_rc=0, devices=DEVICES, people=PEOPLE):
        script = r"""
k3_lang_set ru
k3_gw() {
  K3_GW_RC=0; K3_GW_WARN=""
  case "$1 $2" in
    "user list") K3_GW_OUT=%s ;;
    "device list") K3_GW_OUT=%s; K3_GW_WARN="w-devices" ;;
    "exits ") echo "exits-called" >&2; K3_GW_OUT=%s; K3_GW_RC=%d ;;
  esac
  return $K3_GW_RC
}
k3_gw_people_load 2>&1; echo "rc=$?"
for r in "${K3_GW_DEV_ROWS[@]}"; do IFS=$'' read -r id user login name plat acc rt en <<< "$r"; echo "$name|$rt"; done
for r in "${K3_GW_USER_ROWS[@]}"; do IFS=$'' read -r uid name rt en <<< "$r"; echo "U $name|$rt"; done
echo "warn=$K3_GW_WARN xname=${K3_GW_XNAME[e-a1da8a655a]:-}"
""" % (shquote(json.dumps(people)), shquote(json.dumps(devices)), shquote(json.dumps(EXITS)), exits_rc)
        rc, out, _d = run_lib(script)
        self.assertEqual(rc, 0, out)
        return out

    def test_name_not_id(self):
        out = self.run_menu()
        self.assertIn("rc=0", out)
        self.assertIn("qa-iPhone|выход kaskad-t3 · AmneziaWG 3.1, при отказе — напрямую\n", out + "\n")
        self.assertIn("qa-Pixel|выход e-gone\n", out)                    # no such exit — its id, as before
        self.assertIn("qa-Mac|как у пользователя — пул выходов\n", out)
        self.assertIn("U Анна|как по умолчанию — сейчас: выход kaskad-t3 · AmneziaWG 3.1\n", out)
        self.assertNotIn("e-a1da8a655a", out.replace("xname=", "").split("warn=")[0])
        self.assertIn("warn=w-devices xname=kaskad-t3 · AmneziaWG 3.1", out)   # the list's warning kept

    def test_core_fails_ids_stay(self):
        out = self.run_menu(exits_rc=3)
        self.assertIn("rc=0", out)
        self.assertIn("qa-iPhone|выход e-a1da8a655a, при отказе — напрямую\n", out + "\n")

    def test_no_exit_route_no_exits_call(self):
        people = {"ok": True, "users": [dict(PEOPLE["users"][0], route_effective={"via": "pool", "fail": "block"})]}
        out = self.run_menu(devices={"ok": True, "devices": [DEVICES["devices"][2]]}, people=people)
        self.assertNotIn("exits-called", out)
        self.assertIn("qa-Mac|как у пользователя — пул выходов\n", out)

def panel_status(req, pub):
    return {"ok": True, "installed": True, "active": True, "public": {"on": pub}, "require_password": req,
            "password": True, "url": "https://vpn.example.com:37870/", "fingerprint": "", "tunnel": "ssh -L 1980:127.0.0.1:1980 root@x"}


@unittest.skipUnless(bash4() and shutil.which("jq"), "нужны bash 4+ и jq")
class TunnelPasswordLineTest(unittest.TestCase):
    """№5: «Пароль в SSH-туннеле» говорит, как работает панель (auth.tunnel: открытый доступ — пароль везде)."""

    def menu(self, req, pub, keys, lang="ru"):
        script = r"""
k3_lang_set %s
k3_lib_ok() { return 0; }
k3ctl() { if [ "$1 $2" = "panel status" ]; then printf '%%s' %s; else echo "k3ctl $*"; fi; }
clear() { :; }; k3_note_take() { :; }; k3_site_panel_line() { return 1; }; k3_gw_pause() { :; }
k3_panel_menu
echo "STATE: $(k3_panel_state_line req)"
""" % (lang, shquote(json.dumps(panel_status(req, pub))))
        rc, out, _d = run_lib(script, stdin=keys.encode())
        self.assertEqual(rc, 0, out)
        return out

    def test_menu_lines(self):
        out = self.menu(False, True, "0\n")
        self.assertIn("Пароль: задан · нужен и в туннеле, пока открыт доступ из интернета", out)
        self.assertIn(" 4) Пароль в SSH-туннеле: нужен из-за доступа из интернета → спрашивать всегда", out)
        self.assertIn("STATE: Пароль в SSH-туннеле: не нужен, но пока открыт доступ из интернета — пароль спрашивается везде.", out)
        self.assertNotIn("не нужен → спрашивать", out)
        off = self.menu(False, False, "0\n")
        self.assertIn(" 4) Пароль в SSH-туннеле: не нужен → спрашивать", off)
        self.assertIn("STATE: Пароль в SSH-туннеле: не нужен.", off)
        self.assertNotIn("пока открыт доступ", off)
        on = self.menu(True, True, "0\n")
        self.assertIn("Пароль: задан · нужен и в туннеле\n", on)
        self.assertIn(" 4) Пароль в SSH-туннеле: нужен → не спрашивать", on)
        self.assertIn("STATE: Пароль в SSH-туннеле: нужен.", on)
        en = self.menu(False, True, "0\n", lang="en")
        self.assertIn(" 4) Password in the SSH tunnel: required while open to the internet → always ask", en)
        self.assertIn("STATE: Password in the SSH tunnel: not required, but while access from the internet is on, it is asked everywhere.", en)

    def test_turning_off_while_open_tells_the_truth(self):
        out = self.menu(True, True, "4\n2\n0\n")
        self.assertIn("Пока открыт доступ из интернета, пароль спрашивается везде", out)
        self.assertLess(out.index("Пока открыт доступ из интернета"), out.index("Панель перестанет спрашивать"))
        closed = self.menu(True, False, "4\n2\n0\n")
        self.assertNotIn("Пока открыт доступ из интернета", closed)

    def test_the_rule_is_the_panels(self):
        from kaskad.panel import auth
        self.assertFalse(auth.tunnel({}, "127.0.0.1", True, public=True, require_password=False))
        self.assertTrue(auth.tunnel({}, "127.0.0.1", True, public=False, require_password=False))


CYR = re.compile("[А-Яа-яЁё]")
OLD_RU = ("# Kaskad 3: вход шлюза не запускается без части шлюза в таблице nftables (fail-closed).\n# Файл пишет kaskadctl.\n"
          "[Service]\nExecStartPre=+/bin/sh -c 'exec /usr/sbin/nft list chain ip kaskad gw_pre >/dev/null'\n"
          "ExecStartPre=-+/bin/sh -c 'echo \"Kaskad: запуск входа kgw-wg №$$n с загрузки, через $$u с после неё\"'\n")


class JournalAndDropinsTest(RootCase):
    """№7: the panel's journal lines in the language of the server; the drop-ins kaskad-gate.conf neutral English and
    rewritten by ``kaskadctl update units`` (--k3-post-update) — those on disk only."""

    def setUp(self):
        super().setUp()
        registry.reset()
        os.makedirs(paths.systemd_dir(), exist_ok=True)
        self.runner = FakeRunner(binaries=("nft",))
        self.runner.on(["systemctl"], (0, "", ""))

    def tearDown(self):
        registry.reset()
        super().tearDown()

    def test_panel_journal_speaks_the_server_language(self):
        from kaskad.panel import server
        from kaskad.i18n import use_lang
        for lang, want in (("en", "kaskad-panel: settings reloaded\n"), ("ru", "kaskad-panel: настройки перечитаны\n")):
            set_lang(lang)
            buf = io.StringIO()
            with mock.patch("sys.stderr", buf), use_lang("ru" if lang == "en" else "en"):   # a request in the other one
                server.log_t("journal.srv.reloaded")
            self.assertEqual(buf.getvalue(), want)
        with open(os.path.join(ROOT, "lib/kaskad/panel/server.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(re.findall(r'\blog\("[^"]*[А-Яа-яЁё]', src), [])            # no Russian line by hand
        self.assertIn('log_t("journal.srv.reloaded")', src)

    def test_dropins_on_disk_rewritten_on_update(self):
        for u in ("kaskad-gw-wg.service", "kaskad-gw-accel.service"):
            util.atomic_write(gate.dropin_path(u), OLD_RU, 0o644)
        set_lang("ru")
        with mock.patch("kaskad.state.load", return_value=gw_state("wg", "l2tp")):
            res = unit_refresh.refresh(self.runner)
        self.assertEqual(sorted(res["gates"]), ["kaskad-gw-accel.service", "kaskad-gw-wg.service"])
        self.assertIn("kaskad-gw-wg.service.d/kaskad-gate.conf", res["written"])
        for u in res["gates"]:
            text = util.read_text(gate.dropin_path(u))
            self.assertIsNone(CYR.search(text), u)
            self.assertEqual(text, gate.text(self.runner, "kgw-wg" if u == "kaskad-gw-wg.service" else None, u))
        self.assertIn("Kaskad: inbound kgw-wg start #", util.read_text(gate.dropin_path("kaskad-gw-wg.service")))
        self.assertEqual([a[1:] for a in self.runner.commands(["systemctl"])], [["daemon-reload"]])   # no restart
        # the second time — nothing; a unit without a drop-in gets none here (gw apply creates them)
        with mock.patch("kaskad.state.load", return_value=gw_state("wg", "l2tp", "awg3")):
            self.assertEqual(unit_refresh.refresh(self.runner)["gates"], [])
        self.assertFalse(os.path.exists(gate.dropin_path("kaskad-gw-awg3.service")))

    def test_broken_state_leaves_dropins(self):
        util.atomic_write(gate.dropin_path("kaskad-gw-wg.service"), OLD_RU, 0o644)
        with mock.patch("kaskad.state.load", side_effect=ValueError("broken")):
            self.assertEqual(unit_refresh.refresh(self.runner)["gates"], [])
        self.assertEqual(util.read_text(gate.dropin_path("kaskad-gw-wg.service")), OLD_RU)


if __name__ == "__main__":
    unittest.main()
