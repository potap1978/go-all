"""Kaskad 3.3.7.5, круг 3 — ссылки людей не ломаются молча, работающий узел молча не меняется:

1. Вход по порту выключается сам только на новой установке: (a) первое завершение мастера (повторное — нет),
   (b) первый вход администратора в панель через домен (слушатель ``proxy``), (c) ни одной ссылки людей с портом ещё не
   выдавалось (ссылка, открытая страница на порту, ссылки до 3.3.7.5). Мастер сам больше не выключает.
2. «Убрать домен» при выключенном порте сначала включает его; смена и снятие домена честно говорят про ссылки людей.
П-3: уже выключенный порт не трогается.

Каждая проверка держит порчу (класс ``Porcha3375R3Test``)."""
import os
import types
import unittest
from unittest import mock

from kaskad import util
from kaskad.errors import KaskadError
from kaskad.panel import api, conf, portoff, userpage
from kaskad.setup import model as smodel
from kaskad.setup import steps
from kaskad.site import ops as sops
from tests.panel.panelcase import PanelCase
from tests.site.sitecase import DOMAIN, SiteCase
from tests.test_3375_hf_r2 import _Port, _cfg, _site_state
from tests.userpage_case import UID1, PageCase


class _Off(PanelCase):
    def setUp(self):
        super().setUp()
        _site_state("site")
        _cfg(public_on=True)
        self.calls = []
        p = mock.patch.object(conf, "set_public", side_effect=lambda r, on: self.calls.append(on))
        p.start()
        self.addCleanup(p.stop)


class AutoOffTest(_Off):
    def test_new_install_first_domain_sign_in_turns_the_port_off_once(self):
        portoff.mark_pending()
        self.assertEqual(portoff.on_domain_login(), "off")
        self.assertEqual(self.calls, [False])
        self.assertFalse(portoff.pending())
        self.assertEqual(portoff.on_domain_login(), "")          # decided once
        self.assertEqual(self.calls, [False])
        self.assertIn("PANEL:", self.read("/var/log/kaskad.log"))
        self.assertNotIn("app-", self.read("/var/log/kaskad.log"))     # no secret in the journal

    def test_without_the_flag_nothing(self):
        self.assertEqual(portoff.on_domain_login(), "")             # an updated node: never set
        self.assertEqual(self.calls, [])

    def test_a_port_link_given_keeps_the_port(self):
        portoff.mark_pending()
        portoff.mark_port_link()
        self.assertEqual(portoff.on_domain_login(), "kept")
        self.assertEqual(self.calls, [])
        self.assertFalse(portoff.pending())

    def test_links_not_on_the_domain_yet_wait(self):
        _site_state("domain")
        portoff.mark_pending()
        self.assertEqual(portoff.on_domain_login(), "")
        self.assertTrue(portoff.pending())
        self.assertEqual(self.calls, [])

    def test_port_already_off_is_not_touched(self):          # П-3
        _cfg(public_on=False)
        portoff.mark_pending()
        self.assertEqual(portoff.on_domain_login(), "")
        self.assertEqual(self.calls, [])
        self.assertFalse(portoff.pending())                     # З-7: decided — the flag is taken

    def test_port_turned_on_later_stays_on(self):            # REV-3375 З-7
        _cfg(public_on=False)
        portoff.mark_pending()
        portoff.on_domain_login()                               # the port was already off: the flag goes
        _cfg(public_on=True)                                    # the admin turns the port on by hand
        self.assertEqual(portoff.on_domain_login(), "")
        self.assertEqual(self.calls, [])

    def test_old_links_are_port_links_once(self):
        self.assertTrue(os.path.exists(portoff._path(portoff.CHECKED)))     # the panel's start looked (no people)
        self.assertFalse(portoff.port_link_given())
        os.unlink(portoff._path(portoff.CHECKED))
        portoff.check_old_links(lambda: True)
        self.assertTrue(portoff.port_link_given())
        os.unlink(portoff._path(portoff.LINKS))
        portoff.check_old_links(lambda: True)                     # done once per node
        self.assertFalse(portoff.port_link_given())


class FirstFinishTest(PanelCase):
    """(a): the first end of the wizard sets the flag; the wizard run again — never."""

    def finish(self):
        steps.s_finish(None)

    def test_first_finish_only(self):
        self.finish()
        self.assertTrue(portoff.pending())
        os.unlink(portoff._path(portoff.PENDING))
        smodel.update(lambda d: d.update(finished_at=None, status="active"))       # «setup link» again
        smodel.update(lambda d: smodel.record_step(d, "finish", {"state": "ok"}))
        self.finish()
        self.assertFalse(portoff.pending())

    def test_wizard_never_turns_the_port_off(self):
        from kaskad.setup import web
        self.assertFalse(hasattr(web, "_public_off_after_finish"))


class SignInListenerTest(_Port):
    """(b): only a session on the listener for nginx (the domain) decides; the port and the tunnel never."""

    def test_only_the_domain_listener(self):
        seen = []
        with mock.patch.object(api, "_domain_sign_in", side_effect=lambda: seen.append(1)):
            self.assertEqual(self.pcall("GET", "/api/session").status, 200)
            self.ok("GET", "/api/session")
            self.assertEqual(seen, [])
            api.session_info(types.SimpleNamespace(session={"via": "password"}, listener="proxy", scheme="https"))
            self.assertEqual(seen, [1])
            api.session_info(types.SimpleNamespace(session=None, listener="proxy", scheme="https"))
            self.assertEqual(seen, [1])                           # no session: not an admin


class PortLinkMarkTest(PageCase):
    """(c): a link of people built on the port, or a page opened there, marks; a link on the domain does not."""

    def test_marks(self):
        _site_state("site")
        cfg = _cfg(public_on=True)
        conf.write_access(cfg)
        self.token()
        self.assertTrue(userpage.url(UID1).startswith("https://%s/p/" % DOMAIN))
        self.assertFalse(portoff.port_link_given())
        _site_state("domain")
        conf.write_access(cfg)
        self.assertIn(":41234/p/", userpage.url(UID1))
        self.assertTrue(portoff.port_link_given())

    def test_page_opened_on_the_port(self):
        tok = self.token()
        req = types.SimpleNamespace(client_ip="198.51.100.7", listener="public",
                                    match=types.SimpleNamespace(group=lambda k: tok))
        self.assertIsNotNone(userpage.authorize(req))
        self.assertTrue(portoff.port_link_given())


class ClearKeepsAWayInTest(SiteCase):
    """2: «Убрать домен» with the port off switches it on first; the answer says what happens to people's links."""

    def setUp(self):
        super().setUp()
        self.with_domain(issued="le")
        self.calls = []
        for p in (mock.patch.object(sops.nginx, "remove", return_value="reload"),
                  mock.patch.object(sops.acme, "set_autorenew"), mock.patch.object(sops.acme, "drop_hook"),
                  mock.patch.object(sops.acme, "remove_units"), mock.patch.object(sops.consumers, "drop_copy"),
                  mock.patch.object(sops.consumers, "reload", return_value=[]),
                  mock.patch.object(sops.consumers, "_reload_panel", return_value=False),
                  mock.patch.object(conf, "set_public", side_effect=self.set_public)):
            p.start()
            self.addCleanup(p.stop)

    def set_public(self, runner, on):
        self.calls.append(on)

    def people(self, port):
        return mock.patch.object(sops, "people_links", return_value={"domain": DOMAIN, "count": 2, "port": port})

    def test_port_off_comes_on_first(self):
        with self.people(False):
            out = sops.clear(self.w.runner, way_in=True)
        self.assertEqual(self.calls, [True])
        text = " ".join(out["warnings"])
        self.assertIn("пользователей со ссылками: 2", text)
        self.assertIn("включил его", text)

    def test_port_on_stays(self):
        with self.people(True):
            out = sops.clear(self.w.runner, way_in=True)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(out["warnings"]), 1)

    def test_refused_way_in_keeps_the_domain(self):
        with self.people(False), mock.patch.object(conf, "set_public", side_effect=KaskadError("ufw")):
            with self.assertRaises(KaskadError):
                sops.clear(self.w.runner, way_in=True)
        self.assertEqual(sops.status(self.w.runner)["domain"], DOMAIN)

    def test_tunnel_only_node_stays_so(self):
        with mock.patch.object(sops, "people_links", return_value={"domain": "", "count": 0, "port": False}):
            out = sops.clear(self.w.runner, way_in=True)            # the panel was not on the domain: no address lost
        self.assertEqual(self.calls, [])
        self.assertNotIn("warnings", out)

    def test_internal_clear_never_switches(self):
        with self.people(False):
            out = sops.clear(self.w.runner)                       # a change of domain, a restore, the removal
        self.assertEqual(self.calls, [])
        self.assertNotIn("warnings", out)

    def test_windows_say_it(self):
        with self.people(False):
            w = sops.status(self.w.runner)["people_warn"]
        self.assertIn("пользователей со ссылками: 2", " ".join(w["clear"]))
        self.assertIn("сначала включу", " ".join(w["clear"]))
        self.assertIn("только по новому домену", " ".join(w["change"]))


class PeopleLinksCountTest(PageCase):
    def test_count_of_people_with_links_on_the_domain(self):
        _site_state("site")
        _cfg(public_on=False)
        self.token()
        pl = sops.people_links()
        self.assertEqual((pl["domain"], pl["count"], pl["port"]), (DOMAIN, 1, False))
        _site_state("domain")
        self.assertEqual(sops.people_links()["domain"], "")


class Porcha3375R3Test(unittest.TestCase):
    """Порча: each guard of this round taken away — its test turns red."""

    def red(self, case, name):
        t = case(name)
        t.setUp()
        try:
            with self.assertRaises(AssertionError):
                getattr(t, name)()
        finally:
            t.doCleanups()
            t.tearDown()

    def test_porcha_a_wizard_again(self):
        real = smodel.update

        def no_guard(fn):
            def wrap(doc):
                doc["steps"].pop("finish", None)
                doc["stages"].pop("done", None)
                return fn(doc)
            return real(wrap)
        with mock.patch.object(smodel, "update", side_effect=no_guard):
            self.red(FirstFinishTest, "test_first_finish_only")

    def test_porcha_b_any_listener(self):
        with mock.patch.object(api, "_admin_from_domain", side_effect=lambda req: req.session is not None):
            self.red(SignInListenerTest, "test_only_the_domain_listener")

    def test_porcha_c_links_ignored(self):
        with mock.patch.object(portoff, "port_link_given", return_value=False):
            self.red(AutoOffTest, "test_a_port_link_given_keeps_the_port")

    def test_porcha_c_link_not_marked(self):
        with mock.patch.object(portoff, "mark_port_link"):
            self.red(PortLinkMarkTest, "test_marks")

    def test_porcha_c_page_not_marked(self):
        with mock.patch.object(portoff, "mark_port_link"):
            self.red(PortLinkMarkTest, "test_page_opened_on_the_port")

    def test_porcha_p3_port_already_off(self):
        real = conf.load

        def on(*a):
            cfg = real(*a)
            cfg["public"]["on"] = True
            return cfg
        with mock.patch.object(conf, "load", side_effect=on):
            self.red(AutoOffTest, "test_port_already_off_is_not_touched")

    def test_porcha_clear_without_way_in(self):
        with mock.patch.object(sops, "_keep_way_in", side_effect=lambda r, d: sops.people_links()):
            self.red(ClearKeepsAWayInTest, "test_port_off_comes_on_first")


if __name__ == "__main__":
    unittest.main()


class ClearCallersKeepTheWayIn(unittest.TestCase):
    """REV-3375 П-5: every «Убрать домен» a person can press (panel, bot, SSH menu / CLI, the wizard) asks ``clear``
    to keep a way into the panel (``way_in=True``); only the internal clear of a backup import never switches the port.
    Without it «Убрать домен» from the bot on a node with the port off would leave only the SSH tunnel."""
    PEOPLE = (("site", "panel.py"), ("site", "cli.py"), ("bot", "site.py"), ("setup", "steps.py"))

    def _calls(self, *parts):
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(root, "lib", "kaskad", *parts), encoding="utf-8").read()
        return re.findall(r"(?:\bops|_ops\(\)|\bsops)\.clear\b[^\n]*", src)

    def test_people_keep_the_way_in(self):
        for parts in self.PEOPLE:
            calls = self._calls(*parts)
            self.assertTrue(calls, parts)
            for c in calls:
                self.assertIn("way_in=True", c, parts)

    def test_backup_import_is_internal(self):
        for c in self._calls("backup.py"):
            self.assertNotIn("way_in", c)


class ProtocolsGridWaitsForTheSite(unittest.TestCase):
    """T-3375 Н-7: «Протоколы», открытые сразу, рисуются до ответа /api/site — сетка карточек зависит от домена, иначе
    переключатель «на сертификате домена» у SSTP и OpenConnect не появляется до ухода со страницы и возврата."""
    def test_grid_depends_on_the_domain(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        js = open(os.path.join(root, "lib", "kaskad", "panel", "static", "app.js"), encoding="utf-8").read()
        page = js[js.index("PAGES.inbounds = {"):]
        grid = page[page.index("const grid = blk(() => ({"):][:600]
        self.assertIn('dom: R.site.loaded && !R.site.missing && (rd("site") || {}).domain', grid)
