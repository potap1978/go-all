"""AmneziaWG and WireGuard in the subscription of /p/<токен>/sub (owner 24.10, docs/k3/SPEC-vless-inbound.md §8):
the .conf of a device as a line «amneziawg://<base64url .conf, no =>#<name>» — the line of Incy and of goAmnezia Pro
all.txt; after the links of VLESS and Hysteria2, which stay as they were (same text, same places); only the user's
own enabled devices; the mark «ждут новых настроек» stays; old data (3.3.7.9) — the same token, the same address,
nothing written by a fetch (3.3.7.9 reads the data back)."""

import base64
import json
import os
import urllib.parse
from unittest import mock

from kaskad import paths, secrets, state
from kaskad.errors import KaskadError
from kaskad.gw import exits, model, ops, registry
from kaskad.gw import mtu as gwmtu
from kaskad.gw.inbounds.base import DummyInbound
from kaskad.panel import userpage
from tests.fakes_gw import awg_conf, install_parser
from tests.test_gw_hy2 import Hy2Case


def _conf_of(line):
    """The .conf inside a line amneziawg://, and its name."""
    assert line.startswith("amneziawg://"), line
    tok, _, frag = line[len("amneziawg://"):].partition("#")
    raw = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4)).decode("utf-8")
    return raw, urllib.parse.unquote(frag)


def _scheme(links):
    return [l.split("://", 1)[0] for l in links]


def _old_links(user, runner):
    """The links of the subscription as 3.3.7.9 built them: only SUB_KINDS, device by device (the reference)."""
    st = state.load()
    out = []
    for d in model.devices(st, user=user["id"]):
        if not model.device_active(st, d):
            continue
        for kind in [k for k in d.get("access") or [] if k in model.SUB_KINDS and k in model.enabled_kinds(st)]:
            try:
                got = ops.issue(runner, d["id"], inbound=kind, where="page")
            except KaskadError:
                continue
            for it in got["items"]:
                if it["format"] == "link" and it["body"] not in out:
                    out.append(it["body"])
    return out


class ConfInbound(DummyInbound):
    """A dummy of WireGuard / AmneziaWG whose .conf has the form of the real one ([Interface] … [Peer]); WireGuard on
    MikroTik gives the script of RouterOS first (format «conf» too) — as the real adapter does."""

    def __init__(self, line=None, **kw):
        super().__init__(**kw)
        self._line = line

    def line(self, st):
        return self._line

    def issue(self, st, sec, device, os=None):
        rec = dict(super().issue(st, sec, device, os)["items"][0])
        rec["body"] = "[Interface]\n%s%s\n[Peer]\nPublicKey = SERVER-%s\n" % (
            rec["body"], "Jc = 4\n" if self._line else "", self.kind)
        recs = [rec]
        if os == "mikrotik" and self.kind == "wg":
            recs = [dict(rec, body="/interface wireguard add name=kaskad-wg\n", filename="kaskad-wg.rsc"), rec]
        return dict(recs[0], inbound=self.kind, items=recs)


class SubAwgCase(Hy2Case):
    def dummies(self):
        out = {}
        for kind, kw in (("wg", {"title": "WireGuard", "ifaces": ["kgw-wg"]}),
                         ("awg2", {"title": "AmneziaWG 2.0", "line": "2.0", "default_port": 41023, "ifaces": ["kgw-awg2"]}),
                         ("awg3", {"title": "AmneziaWG 3.1", "line": "3.1", "default_port": 41033, "ifaces": ["kgw-awg3"]})):
            out[kind] = ConfInbound(kind=kind, **kw)
            registry.register(out[kind])
        return out

    def world(self):
        self.install()                                              # the gateway, hy2, the user «Мама»
        for kind in ("vless", "awg2", "wg", "awg3"):
            rc, _, err = self.run_json("gw", "inbound", "install", kind)
            self.assertEqual(rc, 0, err)

    def conf(self, did, kind):
        """The .conf the issue window gives (the static key)."""
        rc, doc, err = self.run_json("gw", "issue", did, "--inbound", kind)
        self.assertEqual(rc, 0, err)
        return [i for i in doc["items"] if i["format"] == "conf" and i["body"].startswith("[Interface]")][0]["body"]

    def sub(self, did):
        st = state.load()
        user = model.find_user(st, model.get_device(st, did)["user"])
        return userpage.build_sub(user, self.w.runner)[1]


class AwgInTheSubscriptionTest(SubAwgCase):
    def test_amneziawg_and_wireguard_go_after_vless_and_hysteria2(self):
        self.world()
        d = self.add_device(access="vless,wg,hy2,awg2,awg3")
        links = self.sub(d["id"])
        self.assertEqual(_scheme(links), ["vless", "hysteria2", "amneziawg", "amneziawg", "amneziawg"])
        # 3.1 → 2.0 → WireGuard whatever the order of the access; each line — the .conf of the window byte for byte
        for line, kind, title in zip(links[2:], ("awg3", "awg2", "wg"), ("AmneziaWG 3.1", "AmneziaWG 2.0", "WireGuard")):
            conf, name = _conf_of(line)
            self.assertEqual(conf, self.conf(d["id"], kind))
            self.assertEqual(name, "goKaskad PRO · %s · Телефон" % title)
            self.assertNotIn("=", line.partition("#")[0][len("amneziawg://"):])   # base64url, no padding
        for l in links:
            self.assertEqual(l.split(), [l])                         # one line, no spaces: the apps split by lines

    def test_vless_and_hysteria2_links_stay_as_in_3379(self):
        # «отпечатки ссылок у старых устройств те же»: the same text, the same places; AmneziaWG only after them
        self.world()
        a = self.add_device(name="Телефон", access="vless,awg2,hy2")
        self.add_device(name="Ноутбук", platform="windows", access="hy2,wg,vless")
        user = model.find_user(state.load(), a["user"])
        old = _old_links(user, self.w.runner)
        new = userpage.build_sub(user, self.w.runner)[1]
        self.assertEqual(len(old), 4)
        self.assertEqual(new[:len(old)], old)
        self.assertEqual([_conf_of(l)[1] for l in new[len(old):]],
                         ["goKaskad PRO · AmneziaWG 2.0 · Телефон", "goKaskad PRO · WireGuard · Ноутбук"])

    def test_the_script_of_routeros_is_not_the_conf(self):
        self.world()
        d = self.add_device(name="Роутер", platform="mikrotik", access="wg")
        links = self.sub(d["id"])
        self.assertEqual(len(links), 1)
        self.assertTrue(_conf_of(links[0])[0].startswith("[Interface]\n"))

    def test_only_the_own_enabled_devices_and_not_a_paused_user(self):
        self.world()
        mine = self.add_device(name="Телефон", access="awg2")
        off = self.add_device(name="Планшет", access="awg2,wg")
        self.ok("gw", "device", "disable", off["id"])
        self.ok("gw", "user", "add", "Папа")
        rc, doc, err = self.run_json("gw", "device", "add", "Папа", "Телефон", "--platform", "ios", "--access", "awg2")
        self.assertEqual(rc, 0, err)
        other = doc["device"]
        links = self.sub(mine["id"])
        self.assertEqual(len(links), 1)
        body = "\n".join(_conf_of(l)[0] for l in links)
        sec = secrets.load()
        self.assertIn(sec["dev.%s.awg2.private" % mine["id"]], body)
        for did in (off["id"], other["id"]):                         # a disabled device, another user: no keys
            keys = [v for k, v in sec.items() if k.startswith("dev.%s." % did) and isinstance(v, str) and len(v) >= 8]
            self.assertTrue(keys)
            for v in keys:
                self.assertNotIn(v, body)
        self.assertEqual(userpage.sub_status("Мама")["devices"], 1)  # the counter of the admin counts AmneziaWG too
        self.ok("gw", "user", "disable", "Мама")
        self.assertEqual(self.sub(mine["id"]), [])                    # paused: an empty subscription

    def test_a_device_with_only_amneziawg_is_not_empty_any_more(self):
        self.world()
        d = self.add_device(access="wg")
        userpage.sub_enable("Мама")
        st = userpage.sub_status("Мама")
        self.assertEqual((st["devices"], st["empty_note"]), (1, ""))
        self.assertEqual(len(self.sub(d["id"])), 1)

    def test_an_inbound_switched_off_is_left_out(self):
        self.world()
        d = self.add_device(access="awg2,wg")
        self.ok("gw", "inbound", "disable", "wg")
        self.assertEqual([_conf_of(l)[1] for l in self.sub(d["id"])], ["goKaskad PRO · AmneziaWG 2.0 · Телефон"])

    def test_a_fetch_is_not_an_issue(self):
        # a fetch of the subscription is not «выдали»: «ждут новых настроек» stays (the device may still hold its
        # static key, SPEC-awg-engine §6.1) and «порт закрыт?» does not start — an app refreshes it every 12 h with
        # every protocol of the user, he uses one of them
        from kaskad.gw import inlog
        self.world()
        d = self.add_device(access="vless,hy2,awg2,wg")
        with mock.patch.object(gwmtu, "issued") as issued, mock.patch.object(inlog, "mark_issued") as seen:
            self.assertEqual(len(self.sub(d["id"])), 4)
        self.assertEqual((issued.call_args_list, seen.call_args_list), ([], []))
        with mock.patch.object(gwmtu, "issued") as issued, mock.patch.object(inlog, "mark_issued") as seen:
            self.conf(d["id"], "awg2")                               # the issue window still does both
        self.assertEqual([c.args for c in issued.call_args_list], [("awg2", d["id"])])
        self.assertEqual([c.args for c in seen.call_args_list], [("awg2",)])

    def test_a_key_not_ready_leaves_out_that_line_only(self):
        self.world()
        d = self.add_device(access="vless,awg2,wg,awg3")
        real = ops.issue

        def flaky(runner, ref, inbound=None, **kw):
            if inbound == "wg":
                raise KaskadError("нет ключа")
            if inbound == "awg3":
                raise RuntimeError("сломалось")
            return real(runner, ref, inbound=inbound, **kw)
        with mock.patch.object(ops, "issue", flaky):
            links = self.sub(d["id"])
        self.assertEqual(_scheme(links), ["vless", "amneziawg"])
        self.assertEqual(_conf_of(links[1])[1], "goKaskad PRO · AmneziaWG 2.0 · Телефон")
        self.assertIn("RuntimeError", self.log())


class LineFormatTest(SubAwgCase):
    def test_the_line_is_the_one_of_goamnezia_all_txt(self):
        # goAmnezia Pro pages_server.amneziawg_link: base64url of the .conf with one line feed, no «=», the name quoted
        conf = awg_conf(3, line="2.0")
        name = "goKaskad PRO · AmneziaWG 2.0 · iPhone Маши"
        ga = "amneziawg://%s#%s" % (base64.urlsafe_b64encode(conf.encode()).decode().rstrip("="),
                                    urllib.parse.quote(name, safe=""))
        self.assertEqual(userpage.amneziawg_link(conf, name), ga)
        self.assertEqual(userpage.amneziawg_link(conf + "\n\n", name), ga)          # one line feed at the end
        # the parser of the egress pool of Kaskad reads all.txt of goAmnezia Pro (the format of Incy) — reads ours too
        install_parser()
        got = exits.load_parser().extract(ga)
        self.assertEqual(len(got), 1)
        rec = json.loads(base64.b64decode(got[0][len("awg://"):] + "=" * (-len(got[0][len("awg://"):]) % 4)))
        self.assertIn(conf.split("PrivateKey = ")[1].split("\n")[0], json.dumps(rec))
        self.assertIn(name, json.dumps(rec, ensure_ascii=False))


class MigrationTest(SubAwgCase):
    """«Старые данные → новая» и откат (правило владельца 24.10): the subscription keeps no data of its own beyond
    the token of 3.3.7.9 (``user.<id>.sub`` in secrets.json) — the same address after the update, AmneziaWG comes
    with the next refresh of the app, and a fetch writes neither state.json nor secrets.json (3.3.7.9 reads them)."""

    def test_the_token_of_3379_keeps_its_address_and_gets_amneziawg(self):
        self.world()
        d = self.add_device(access="vless,awg2")
        uid = d["user"]
        tok = "T" * 43                                               # a token as 3.3.7.9 left it in secrets.json
        secrets.set(userpage.SUB_KEY % uid, tok)
        self.assertEqual(userpage.find_token(tok, sub=True), uid)
        self.assertIsNone(userpage.find_token(tok))                     # never the page's
        self.assertTrue(userpage.sub_url(uid).endswith("/p/%s/sub" % tok))
        from kaskad.gw import inlog
        files = [paths.state(), paths.secrets_file()]
        before = [open(f, "rb").read() for f in files]
        seen = inlog.seen_path()
        seen_before = open(seen, "rb").read() if os.path.exists(seen) else None
        links = self.sub(d["id"])
        self.assertEqual(_scheme(links), ["vless", "amneziawg"])
        self.assertEqual([open(f, "rb").read() for f in files], before)   # nothing new for 3.3.7.9 to read back
        self.assertEqual(open(seen, "rb").read() if os.path.exists(seen) else None, seen_before)
        self.assertTrue(userpage.sub_url(uid).endswith("/p/%s/sub" % tok))
