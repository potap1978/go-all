"""The quick setup without the panel: setup.json, the rail, the link (secret, one-time token, 24 h, revocation after
the password, the bound address), the jobs, the steps on stubs, secrets kept out of files, the journal and argv."""

import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from kaskad import cli, paths, sysrun
from kaskad.errors import ConflictError, KaskadError
from kaskad.panel import auth, conf
from kaskad.setup import features, jobs, link, model, product, steps
from tests.helpers import RootCase


def slurp(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


IP = "203.0.113.67"


def open_panel():
    conf.save({"schema": 1, "public": {"on": True, "port": 41234, "bind": IP, "host": IP, "cert_host": IP}})


def new_link(**kw):
    return model.update(lambda d: link.issue(d, **kw))


class StateTest(RootCase):
    def test_defaults_and_mode_0600(self):
        doc = model.load()
        self.assertEqual(doc["status"], "off")
        self.assertFalse(model.active(doc))
        model.update(lambda d: d.update(mode="node"))
        self.assertEqual(os.stat(model.path()).st_mode & 0o777, 0o600)
        self.assertEqual(model.load()["mode"], "node")

    def test_broken_file_is_defaults(self):
        os.makedirs(paths.etc(), exist_ok=True)
        with open(model.path(), "w") as f:
            f.write("{не json")
        self.assertEqual(model.load()["status"], "off")

    def test_rail_states_and_minutes_left(self):
        doc = model.load()
        doc.update(password_set=True, current="exits", stages={"import": "done", "protocols": "skipped"})
        rl = model.rail(doc)
        self.assertEqual([r["state"] for r in rl["stages"]], ["done", "skipped", "current", "ahead", "ahead", "ahead"])
        self.assertEqual(rl["position"], 3)
        self.assertEqual(rl["total"], 6)
        self.assertEqual(rl["left_min"], sum(product.STAGES[k][2] for k in ("exits", "domain", "extras", "done")))
        self.assertEqual(rl["welcome"]["state"], "done")

    def test_classic_mode_has_its_own_rail(self):
        doc = model.load()
        doc["mode"] = "classic"
        kinds = [r["kind"] for r in model.rail(doc)["stages"]]
        # owner 2026-10-09 §12: Вход → Импорт → Пробросы → Домен → Готово
        self.assertEqual(kinds, ["import", "forward", "domain", "done"])
        self.assertEqual(model.rail(doc)["stages"][1]["title"], "Пробросы")

    def test_rail_ranges_of_the_design(self):
        doc = model.load()
        doc.update(password_set=True, current="import")
        rl = model.rail(doc)
        self.assertEqual([r["range"] for r in rl["stages"]], [[1, 2], [2, 5], [2, 3], [5, 20], [1, 2], [1, 3]])
        self.assertEqual(rl["left"], [12, 35])                   # «≈ 12–35 минут осталось» of the design
        doc.update(current="exits", stages={"import": "done", "protocols": "done"})
        self.assertEqual(model.rail(doc)["left"], [9, 28])
        for r in rl["stages"]:
            self.assertEqual(r["min"], r["range"][1])

    def test_stage_moves(self):
        doc = model.load()
        with self.assertRaises(ValueError):
            model.set_stage(doc, "import", "open")              # the password first
        doc["password_set"] = True
        doc["current"] = "import"
        with self.assertRaises(ValueError):
            model.set_stage(doc, "domain", "open")              # ahead — not yet
        self.assertEqual(model.set_stage(doc, "import", "skip"), "protocols")
        self.assertEqual(model.set_stage(doc, "import", "open"), "import")    # a skipped one can be opened again
        self.assertEqual(model.set_stage(doc, "import", "done"), "protocols")
        with self.assertRaises(ValueError):
            model.set_stage(doc, "done", "skip")


class LinkTest(RootCase):
    def setUp(self):
        RootCase.setUp(self)
        open_panel()

    def test_secret_url_and_expiry(self):
        tok = new_link()
        doc = model.load()
        secret = doc["link"]["secret"]
        self.assertRegex(secret, r"^[A-Za-z0-9_-]{32}$")
        self.assertTrue(model.active(doc))
        self.assertAlmostEqual(doc["link"]["expires"], time.time() + 24 * 3600, delta=5)
        self.assertEqual(link.url(doc, tok), "https://%s:41234/setup-%s/?t=%s" % (IP, secret, tok))
        self.assertNotIn(tok, json.dumps(doc))                                  # only its hash is kept
        self.assertFalse(model.active(doc, now=time.time() + 24 * 3600 + 1))

    def test_match_only_the_secret(self):
        new_link()
        doc = model.load()
        s = doc["link"]["secret"]
        self.assertEqual(link.match(doc, "/setup-%s/api/setup/state" % s), ("/setup-" + s, "/api/setup/state"))
        self.assertEqual(link.match(doc, "/setup-%s" % s), ("/setup-" + s, None))
        for bad in ("/setup-%s/" % ("A" * 32), "/setup-%sX/" % s, "/setup-%s" % s[:-1], "/setup-/", "/api/setup/x"):
            self.assertIsNone(link.match(doc, bad), bad)
        model.update(lambda d: d.update(status="finished"))
        self.assertIsNone(link.match(model.load(), "/setup-%s/" % s))      # after «Готово» — 404

    def test_token_is_one_time(self):
        tok = new_link()
        self.assertEqual(model.update(lambda d: link.redeem(d, tok, "198.51.100.1")), "ok")
        self.assertEqual(model.update(lambda d: link.redeem(d, tok, "198.51.100.1")), "used")
        self.assertEqual(model.update(lambda d: link.redeem(d, "x" * 43, "198.51.100.1")), "unknown")

    def test_expired_token(self):
        tok = new_link()
        later = time.time() + 25 * 3600
        self.assertEqual(model.update(lambda d: link.redeem(d, tok, "1.1.1.1", now=later)), "expired")

    def test_new_link_keeps_path_new_path_changes_it(self):
        new_link()
        s1 = model.load()["link"]["secret"]
        new_link()
        self.assertEqual(model.load()["link"]["secret"], s1)
        new_link(new_path=True)
        self.assertNotEqual(model.load()["link"]["secret"], s1)

    def test_password_revokes_tokens_and_new_links_carry_none(self):
        tok = new_link()
        model.update(lambda d: (d.update(password_set=True), link.revoke_all(d)))
        self.assertEqual(model.update(lambda d: link.redeem(d, tok, "1.1.1.1")), "revoked")
        self.assertIsNone(new_link())
        self.assertTrue(link.url(model.load()).endswith("/"))

    def test_bind_to_first_address(self):
        tok = new_link(bind=True)
        model.update(lambda d: link.redeem(d, tok, "198.51.100.1"))
        doc = model.load()
        self.assertTrue(link.allowed_ip(doc, "198.51.100.1"))
        self.assertFalse(link.allowed_ip(doc, "198.51.100.2"))
        new_link(bind=False)
        self.assertTrue(link.allowed_ip(model.load(), "198.51.100.2"))

    def test_no_url_without_open_panel(self):
        conf.save({"schema": 1, "public": {"on": False}})
        tok = new_link()
        self.assertIsNone(link.url(model.load(), tok))


class JobsTest(RootCase):
    def test_job_runs_and_reports(self):
        ran = []

        def launch(job_id, step):
            body = jobs.take_input(job_id)
            ran.append(body)
            p = jobs.Progress(job_id, step)
            p.item("a", "wait", "делаю", "Шаг A")
            p.item("a", "ok", "готово")
            p.finish({"ok": True, "state": "ok", "text": "всё"})
        with mock.patch.object(jobs, "LAUNCH", launch):
            doc = jobs.start("protocols", {"kinds": ["wg"], "secret": "s3cr3t"})
        self.assertEqual(ran, [{"kinds": ["wg"], "secret": "s3cr3t"}])
        self.assertFalse(os.path.exists(jobs.input_path(doc["id"])))         # the body file is gone at once
        st = jobs.status()
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["items"][0]["state"], "ok")
        self.assertEqual(st["items"][0]["title"], "Шаг A")
        self.assertNotIn("s3cr3t", slurp(jobs.job_path()))

    def test_one_job_at_a_time_and_dead_job_fails(self):
        with mock.patch.object(jobs, "LAUNCH", lambda j, s: None):
            jobs.start("protocols", {})
            with self.assertRaises(ConflictError):
                jobs.start("cert", {})
        doc = jobs.read()
        doc.update(state="running", pid=999999)
        jobs.write(doc)
        self.assertEqual(jobs.status()["state"], "failed")
        self.assertEqual(jobs.status()["result"]["error"]["code"], "interrupted")


class StepsTest(RootCase):
    def setUp(self):
        RootCase.setUp(self)
        open_panel()
        new_link()
        self.runner = sysrun.FakeRunner()
        self.patches = [mock.patch.object(auth, "SCRYPT_N", 1 << 10), mock.patch.object(steps, "online",
                                                                                        lambda force=False: True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        RootCase.tearDown(self)

    def password(self, pw="Correct-Horse-9"):
        return steps.run("password", {"password": pw}, self.runner)

    def test_password_first(self):
        out = steps.run("mode", {"mode": "node"}, self.runner)
        self.assertEqual(out["error"]["code"], "password_first")

    def test_password_rules_of_the_server(self):
        out = self.password("short")
        self.assertEqual((out["state"], out["error"]["code"], out["error"]["field"]), ("error", "weak", "password"))
        self.assertIn("10", out["text"])
        out = self.password()
        self.assertEqual(out["state"], "ok")
        self.assertTrue(auth.verify("admin", "Correct-Horse-9"))
        doc = model.load()
        self.assertTrue(doc["password_set"])
        self.assertEqual(doc["current"], "import")
        self.assertNotIn("Correct-Horse-9", slurp(model.path()))
        self.assertNotIn("Correct-Horse-9", slurp(paths.log()))

    def test_mode_switches_rail(self):
        self.password()
        model.update(lambda d: model.set_stage(d, "import", "done"))
        self.assertEqual(model.load()["current"], "protocols")
        out = steps.run("mode", {"mode": "classic"}, self.runner)
        self.assertEqual(out["state"], "ok")
        self.assertIn("forward", [r["kind"] for r in out["data"]["rail"]["stages"]])
        # the mode cards stand on the second stage of both rails: the page stays on them
        self.assertEqual((model.load()["current"], out["data"]["rail"]["current"]), ("forward", "forward"))
        self.assertEqual(model.load()["stages"], {"import": "done"})
        steps.run("mode", {"mode": "node"}, self.runner)
        self.assertEqual(model.load()["current"], "protocols")
        model.update(lambda d: d.update(current="domain"))
        steps.run("mode", {"mode": "classic"}, self.runner)
        self.assertEqual(model.load()["current"], "domain")       # a stage of both rails is left as it is
        self.assertEqual(steps.run("mode", {"mode": "x"}, self.runner)["error"]["field"], "mode")

    def test_forward_keeps_where_it_goes_for_the_summary(self):
        self.password()
        f = {"proto": "tcp", "port": 8443, "to": "192.0.2.4", "to_port": 443}
        with mock.patch("kaskad.forwards.add_forward", lambda *a, **kw: {"forward": f}):
            out = steps.run("forward", {"proto": "tcp", "port": "8443", "to": "192.0.2.4", "to_port": "443"},
                            self.runner)
        self.assertEqual(out["state"], "ok")
        self.assertEqual(model.load()["answers"]["forward"], f)

    def test_templates_give_the_categories_of_the_catalog(self):
        self.password()
        cats = [{"id": "food", "title": "Еда", "icon": "x", "count": 3}, {"id": "photo", "title": "Фото", "icon": "y", "count": 1}]
        with mock.patch("kaskad.site.templates.search", lambda c, q, page=1: {"items": [], "page": 1, "pages": 1,
                                                                               "total": 0, "category": c, "query": q}), \
                mock.patch("kaskad.site.templates.catalog", lambda: {"categories": cats, "total": 4}), \
                mock.patch("kaskad.site.templates.prepare", lambda runner=None: None):
            out = steps.run("templates", {"category": "food"}, self.runner)
        self.assertEqual(out["data"]["categories"], [{"id": "food", "title": "Еда", "count": 3},
                                                     {"id": "photo", "title": "Фото", "count": 1}])
        self.assertEqual(out["data"]["category"], "food")

    def test_extras_list_names_the_agent_keys_without_the_keys(self):
        self.password()
        keys = [{"fingerprint": "SHA256:abc", "type": "ssh-ed25519", "label": "laptop", "line": "ssh-ed25519 AAAA secret"}]
        with mock.patch("kaskad.mcp.keys.listing", lambda: {"keys": keys}):
            out = steps.run("extras-list", {}, self.runner)
        # 3.3.3: the keys are listed by the panel's GET /api/mcp (AgentKey behind the wizard's session) — the list of
        # «Дополнительно» names the item only
        mcp = next(x for x in out["data"]["items"] if x["id"] == "mcp")
        self.assertNotIn("keys", mcp)
        self.assertNotIn("AAAA", json.dumps(out))

    def test_domain_name_kept_for_the_page(self):
        self.password()
        dns = {"state": "none", "ready": False, "a": [], "own": [IP], "advice": "нет A-записи", "checked_at": None}
        with mock.patch("kaskad.site.ops.set_domain", lambda r, name: {"domain": "vpn.example.ru", "dns": dns, "text": ""}):
            out = steps.run("domain", {"name": "VPN.example.ru"}, self.runner)
        self.assertEqual(out["data"]["dns"], "none")
        a = model.load()["answers"]
        self.assertEqual((a["domain_plan"], a["domain"]), ("have", "vpn.example.ru"))

    def test_exits_later_is_direct_with_a_note(self):
        """DECISIONS-3.3.0 D1: «Позже» on «Выходы» — the route «напрямую» with an honest note, the stage skipped."""
        self.password()
        model.update(lambda d: d.update(current="exits"))
        got = []
        with mock.patch("kaskad.gw.ops.exits_view", lambda r: []), \
                mock.patch("kaskad.gw.ops.default_route", lambda r, via, fail=None: got.append(via) or {}):
            out = steps.run("exits-choice", {"choice": "later"}, self.runner)
        self.assertEqual(got, ["direct"])
        self.assertIn("напрямую", out["text"])
        doc = model.load()
        self.assertEqual((doc["stages"].get("exits"), doc["current"], doc["answers"]["exits"]),
                         ("skipped", "domain", "later"))
        got[:] = []
        model.update(lambda d: d.update(current="exits"))
        with mock.patch("kaskad.gw.ops.exits_view", lambda r: [{"id": "e-1", "usable": True}]), \
                mock.patch("kaskad.gw.ops.default_route", lambda r, via, fail=None: got.append(via) or {}):
            out = steps.run("exits-choice", {"choice": "later"}, self.runner)
        self.assertEqual(got, [])                                 # exits are there: the route is not touched
        self.assertIn("не меняли", out["text"])

    def test_route_is_fail_closed_by_default(self):
        self.password()
        got = []
        with mock.patch("kaskad.gw.ops.default_route", lambda r, via, fail=None: got.append((via, fail)) or
                        {"route": {"via": via, "fail": fail}}):
            out = steps.run("route", {"via": "pool"}, self.runner)
        self.assertEqual(got, [("pool", "block")])
        self.assertEqual(out["data"]["route"]["fail"], "block")

    def test_protocols_rows_partial_and_module(self):
        self.password()
        plan = [{"kind": k, "title": t} for k, t in (("wg", "WireGuard"), ("awg2", "AmneziaWG 2.0"),
                                                     ("l2tp", "L2TP/IPsec"))]

        def install(runner, kind, port=None, force=False, awg_engine=None, progress=None):
            if kind == "l2tp":
                raise ConflictError("Порт 1701/udp занят: xl2tpd другого владельца")
            out = {"ok": True, "record": {"port": 51820}}
            if kind == "awg2":
                progress("собираю модуль…")
                out.update(awg_engine="program", warnings=["модуль нельзя: контейнер LXC"])
            return out
        rows = []

        class P(jobs.Progress):
            def _flush(self):
                rows.append([dict(r) for r in self.rows])
        with mock.patch("kaskad.gw.ops.inbound_plan", lambda r, kinds, st=None: [p for p in plan if p["kind"] in kinds]), \
                mock.patch("kaskad.gw.ops.inbound_install", install), mock.patch("kaskad.gw.ops.enable",
                                                                                lambda r: {"ok": True}):
            prog = P(None, "protocols")
            prog.job_id = "x"
            out = steps.run("protocols", {"kinds": ["l2tp", "wg", "awg2"]}, self.runner, prog)
        self.assertEqual(out["state"], "partial")
        last = dict((r["item"], r) for r in rows[-1])
        self.assertEqual(last["wg"]["state"], "ok")
        self.assertEqual(last["l2tp"]["state"], "err")
        self.assertIn("занят", last["l2tp"]["detail"])
        self.assertEqual(last["module"]["state"], "warn")                 # «модуль нельзя — на программе»
        self.assertIn("LXC", last["module"]["detail"])
        self.assertEqual(out["data"]["failed"][0]["code"], "port_busy")
        self.assertEqual(model.load()["answers"]["protocols"], ["wg", "awg2"])

    def test_protocol_list_from_registry_with_owner_defaults(self):
        self.password()
        from kaskad.gw import model as gmodel
        with mock.patch("kaskad.gw.ops.probe", lambda r: {"supported": True, "reason": "", "packages": []}), \
                mock.patch("kaskad.gw.ops.inbound_plan", lambda r, kinds, st=None: [
                    {"kind": k, "title": k.upper(), "supported": True, "needs_key": False, "needs_core": False}
                    for k in kinds]), \
                mock.patch("kaskad.gw.awgengine.check", lambda r, st=None: {"can": True, "blockers": [], "codes": []}):
            out = steps.run("protocols-plan", {}, self.runner)
        kinds = [c["kind"] for c in out["data"]["protocols"]]
        self.assertIn("vless", kinds)                                   # VLESS Reality (3.3): from the registry
        vl = [c for c in out["data"]["protocols"] if c["kind"] == "vless"][0]
        self.assertFalse(vl["on"])                                      # not ticked by default
        self.assertIn("v2RayTun", vl["why"])                            # one short line (3.3.2 №16)
        self.assertEqual(kinds, list(gmodel.INBOUND_KINDS))              # the order of the registry
        self.assertIn("pptp", kinds)
        self.assertEqual(sorted(out["data"]["defaults"]), ["awg2", "awg3", "l2tp", "wg"])
        self.assertEqual(out["data"]["github"]["ask"], False if out["data"]["github"]["state"] != "none" else True)

    def test_github_key_silent_when_it_works_field_when_missing(self):
        self.password()
        from kaskad import ghkey
        with mock.patch.object(ghkey, "current", return_value=("", None)):
            gh = steps.github_state(self.runner)
        self.assertTrue(gh["ask"])
        with mock.patch.object(ghkey, "current", return_value=("ghp_" + "a" * 36, "config")), \
                mock.patch.object(ghkey, "check", return_value={"ok": True, "ref": "main"}) as chk:
            gh = steps.github_state(self.runner)
        self.assertEqual((gh["state"], gh["ask"]), ("ok", False))
        self.assertIn("из команды установки", gh["text"])
        chk.assert_called_once()
        with mock.patch.object(ghkey, "current", return_value=("ghp_" + "b" * 36, "config")), \
                mock.patch.object(ghkey, "check", return_value={"ok": False, "reason": "bad", "error": "401"}):
            gh = steps.github_state(self.runner)
        self.assertEqual((gh["state"], gh["ask"]), ("bad", True))

    def test_github_key_step_net_then_force(self):
        self.password()
        from kaskad import ghkey
        key = "ghp_" + "c" * 36
        with mock.patch.object(ghkey, "check", return_value={"ok": False, "reason": "net", "error": "нет связи"}):
            out = steps.run("github-key", {"key": key}, self.runner)
            self.assertEqual(out["error"]["code"], "net")
            self.assertEqual(out["error"]["action"]["force"], True)
            out = steps.run("github-key", {"key": key, "force": True}, self.runner)
        self.assertEqual(out["state"], "partial")
        self.assertEqual(ghkey.current()[0], key)
        self.assertNotIn(key, slurp(model.path()))
        self.assertNotIn(key, slurp(paths.log()))

    def test_extras_soon_without_other_branches(self):
        self.password()
        with mock.patch.object(features, "has", lambda *w: False):
            out = steps.run("extras-list", {}, self.runner)
            items = dict((x["id"], x) for x in out["data"]["items"])
            self.assertEqual(sorted(items), ["backup", "bot", "mcp"])           # 3.3.3: no «Автообновление подписки»
            self.assertFalse(items["backup"]["available"])
            self.assertEqual(items["backup"]["text"], features.soon())
            self.assertTrue(items["bot"]["available"])
            out = steps.run("exits-add", {"text": "https://sub.example.net/u/abc"}, self.runner)
        self.assertEqual((out["state"], out["error"]["code"]), ("unavailable", "soon"))
        self.assertNotIn("sub.example.net", slurp(model.path()))

    def test_offline_state(self):
        self.password()
        with mock.patch.object(steps, "online", lambda force=False: False):
            out = steps.run("github-key", {"key": "ghp_" + "d" * 36}, self.runner)
        self.assertEqual((out["state"], out["ok"], out["error"]["code"]), ("offline", False, "offline"))

    def test_connection_states(self):
        self.password()
        model.update(lambda d: d["answers"].update(device="d-1", issued_at=int(time.time()) - 10, issued_kind="awg3"))
        with mock.patch.object(steps, "_live_device", return_value={"online": False}):
            self.assertEqual(steps.run("connection", {}, self.runner)["data"]["conn"], "wait")
        with mock.patch.object(steps, "_live_device", return_value={"online": True, "rx": 100, "tx": 10,
                                                                    "since": 1}):
            self.assertEqual(steps.run("connection", {}, self.runner)["data"]["conn"], "connected")
        with mock.patch.object(steps, "_live_device", return_value={"online": True, "rx": 900000, "tx": 10}):
            self.assertEqual(steps.run("connection", {}, self.runner)["data"]["conn"], "traffic")
        model.update(lambda d: d["answers"].update(issued_at=int(time.time()) - 200))
        with mock.patch.object(steps, "_live_device", return_value={"online": False}), \
                mock.patch("kaskad.gw.model.get_device", lambda st, ref: {"access": ["awg3", "sstp"]}), \
                mock.patch("kaskad.gw.model.enabled_kinds", lambda st: ["awg3", "awg2", "sstp"]), \
                mock.patch("kaskad.state.load", lambda: {}):
            out = steps.run("connection", {}, self.runner)
        self.assertEqual(out["data"]["conn"], "stuck")
        codes = [h["code"] for h in out["data"]["hints"]]
        self.assertEqual(codes, ["port", "line31", "udp"])
        self.assertEqual(out["data"]["hints"][1]["action"]["inbound"], "awg2")
        self.assertEqual(out["data"]["hints"][2]["action"]["inbound"], "sstp")

    def test_person_and_issue_by_platform(self):
        self.password()
        made = {}
        offers = {"platform": "keenetic", "offers": [
            {"inbound": "awg2", "format": "vpn", "enabled": True}, {"inbound": "l2tp", "format": "fields", "enabled": True},
            {"inbound": "wg", "format": "qr", "enabled": False}], "notes": []}

        def device_add(runner, uid, name, kind="other", platform="other", access=(), via=None, fail=None):
            made.update(uid=uid, name=name, platform=platform, access=list(access))
            return {"device": {"id": "d-12345678", "name": name, "access": list(access)}}
        with mock.patch("kaskad.gw.model.users", lambda st: []), \
                mock.patch("kaskad.gw.ops.user_add", lambda r, name, via=None, fail=None: {"id": "u-1", "name": name}), \
                mock.patch("kaskad.gw.ops.offers", lambda p, st=None: offers), \
                mock.patch("kaskad.gw.ops.device_add", device_add), mock.patch("kaskad.state.load", lambda: {}):
            out = steps.run("person", {"user": "Мама", "device": "Роутер дома", "platform": "keenetic"}, self.runner)
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual(made["access"], ["awg2", "l2tp"])                 # only what is installed, best first
        self.assertEqual(made["platform"], "keenetic")
        a = model.load()["answers"]                                          # the page restores the device after a reload
        self.assertEqual((a["device"], a["device_user"], a["device_platform"]), ("d-12345678", "Мама", "keenetic"))
        issued = {"ok": True, "device": "d-12345678", "kind": "l2tp", "os": "keenetic", "available": ["awg2", "l2tp"],
                  "items": [{"format": "fields", "title": "L2TP/IPsec", "body": [["Пароль", "SeCrEt-PaSs-1"]],
                             "filename": "", "notes": []}], "howto": ["…"]}
        with mock.patch("kaskad.gw.ops.issue", lambda r, ref, inbound=None, os_name=None, where="menu": issued):
            out = steps.run("issue", {"inbound": "l2tp"}, self.runner)
        self.assertEqual(out["data"]["items"][0]["format"], "fields")
        self.assertNotIn("SeCrEt-PaSs-1", slurp(model.path()))             # settings are never stored
        self.assertEqual(model.load()["answers"]["issued_kind"], "l2tp")

    def test_finish_closes_the_path(self):
        self.password()
        s = model.load()["link"]["secret"]
        steps.run("finish", {}, self.runner)
        self.assertIsNone(link.match(model.load(), "/setup-%s/" % s))


class CliTest(RootCase):
    def setUp(self):
        RootCase.setUp(self)
        open_panel()
        self.p = mock.patch.object(auth, "SCRYPT_N", 1 << 10)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        RootCase.tearDown(self)

    def run_cli(self, argv, stdin=""):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), redirect_stdout(out):
            rc = cli.main(argv)
        return rc, out.getvalue()

    def test_link_json_and_password_step_by_stdin(self):
        rc, out = self.run_cli(["setup", "link", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertIn("/setup-", doc["url"])
        self.assertIn("?t=", doc["url"])
        rc, out = self.run_cli(["setup", "step", "password", "--stdin", "--json"], '{"password": "Correct-Horse-9"}')
        self.assertEqual(rc, 0, out)
        self.assertTrue(json.loads(out)["ok"])
        rc, out = self.run_cli(["setup", "link", "--json"])
        self.assertNotIn("?t=", json.loads(out)["url"])                   # after the password — no token

    def test_no_secret_takes_argv(self):
        """Keys, tokens and passwords only through stdin: no option of «setup» names a secret."""
        p = cli.build_parser()
        text = p.parse_args(["setup", "step", "bot"]).__dict__
        self.assertNotIn("token", text)
        sub = [a for a in p._actions if getattr(a, "choices", None)][0].choices["setup"]
        flags = []
        for a in sub._actions:
            for name, sp in (getattr(a, "choices", None) or {}).items():
                for b in sp._actions:
                    flags.extend(b.option_strings)
        for bad in ("--password", "--token", "--key", "--url"):
            self.assertNotIn(bad, flags)

    def test_status_has_no_secret(self):
        self.run_cli(["setup", "link", "--json"])
        rc, out = self.run_cli(["setup", "status", "--json"])
        secret = model.load()["link"]["secret"]
        doc = json.loads(out)
        self.assertNotIn(secret, json.dumps({k: v for k, v in doc.items() if k != "url"}))
        self.assertIn("••••", doc["path"])

    def test_unknown_step(self):
        rc, out = self.run_cli(["setup", "step", "nope", "--json"])
        self.assertEqual(rc, 2)

    def start(self, domain):
        """``setup start --json`` with the IP certificate kept (valid) and the domain's certificate ``domain`` or none."""
        from kaskad.ipcert import ops as ipops
        from kaskad.panel import conf
        from kaskad.setup import cli as scli
        kept = {"ok": True, "issued": False, "kept": True, "cert": {"not_after": "2026-10-16T08:00:00Z"},
                "text": "Сертификат на 203.0.113.10 действует ещё 150 ч — не перевыпускаю."}
        site = (domain, "CRT", "KEY") if domain else None
        with mock.patch.object(scli, "ensure_panel", lambda runner: conf.load()), \
                mock.patch.object(conf, "reload_service", lambda runner: None), \
                mock.patch.object(ipops, "issue", lambda *a, **kw: kept), \
                mock.patch.object(conf, "site_cert", lambda: site), \
                mock.patch.object(conf, "fingerprint", lambda sec=None: "AB:CD"):
            rc, out = self.run_cli(["setup", "start", "--json"])
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def test_start_says_which_certificate_covers_the_link(self):
        """T-M6-qs330 №14: the link on the domain is covered by the domain's certificate — not «на IP»."""
        doc = self.start("vpn.example.com")
        self.assertTrue(doc["url"].startswith("https://vpn.example.com:"), doc["url"])
        self.assertEqual((doc["cert"]["state"], doc["cert"]["host"], doc["cert"]["ip_state"]), ("domain", "vpn.example.com", "ok"))
        self.assertIn("Ссылка на домен vpn.example.com: сертификат Let's Encrypt домена", doc["cert"]["text"])
        self.assertNotIn("на IP", doc["cert"]["text"])
        self.assertEqual(doc["fingerprint"], "")
        doc = self.start(None)                                             # the link on the IP: the IP certificate
        self.assertEqual(doc["cert"]["state"], "ok")
        self.assertNotIn("vpn.example.com", doc["url"])

    def _job(self, step, body):
        with mock.patch.object(jobs, "LAUNCH", lambda j, s: None):
            return jobs.start(step, body)["id"]

    def test_job_refusal_exits_zero_and_the_wizard_still_sees_it(self):
        """T-M6 №9: an expected refusal of a step in a job (unit kaskad-setup-<id>) — code 0 (no «Failed with result
        'exit-code'» in the journal); the refusal is in job.json as before. In SSH (no --job) a refusal stays code 1."""
        job = self._job("password", {"password": "1"})
        rc, out = self.run_cli(["setup", "step", "password", "--job", job])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        st = jobs.status()
        self.assertEqual(st["state"], "failed")
        self.assertFalse(st["result"]["ok"])
        self.assertEqual(st["result"]["error"]["code"], "weak")
        rc, out = self.run_cli(["setup", "step", "password", "--stdin", "--json"], '{"password": "1"}')
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["error"]["code"], "weak")

    def test_job_crash_exits_non_zero(self):
        job = self._job("password", {"password": "Correct-Horse-9"})
        with mock.patch.object(auth, "set_password", side_effect=OSError(28, "No space left on device")):
            rc, out = self.run_cli(["setup", "step", "password", "--job", job])
        self.assertEqual(rc, 1)
        self.assertEqual(jobs.status()["result"]["error"]["code"], "internal")


if __name__ == "__main__":
    unittest.main()
