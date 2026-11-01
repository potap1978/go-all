"""What the menu and the bot can do, the panel does too (the critic of the panel ↔ server contract, C5 and the GitHub
key): the GitHub key — its format, the check on GitHub, where it is written (as the menu writes it), never in an answer
or the log; the AmneziaWG cards that need it; the person's page; the panel's own access. A real panel on the fake
node of GwCase; every key here is made up."""

import io
import json
import os
import re
import shutil
import stat
import subprocess
import unittest
import urllib.error
import urllib.request
from unittest import mock

from kaskad import ghkey, paths
from kaskad.gw.inbounds import awg
from kaskad.panel import auth, userpage
from tests.panel.panelcase import PanelCase

KEY = "ghp_" + "A1b2C3d4E5" * 4                              # made up: the form of a classic key
KEY2 = "github_pat_" + "11ABCDEFG0" * 6 + "_Xyz9"           # made up: the form of a fine-grained key
CONFIG = 'BOT_TOKEN=""\nBOT_CHAT_ID="42"\nMENU_STYLE="inline"\nGITHUB_PAT=""\nBOT_PROXY=""\n'


class FakeGithub(object):
    """``ghkey.open_github`` for the requests to raw.githubusercontent.com: the answer by branch (an HTTP code,
    "net" — no connection, bytes — the file); every request is recorded with its Authorization header."""

    def __init__(self, answers):
        self.answers = answers
        self.seen = []

    def __call__(self, req, timeout=None):
        self.seen.append((req.full_url, req.get_header("Authorization"), timeout))
        ans = self.answers.get(req.full_url.split("/")[5], 404)        # https://raw…/<owner>/<repo>/<ref>/bin/…
        if ans == "net":
            raise urllib.error.URLError("no route to host")
        if isinstance(ans, int):
            raise urllib.error.HTTPError(req.full_url, ans, "error", {}, io.BytesIO(b""))
        return io.BytesIO(ans)


class KeyCase(PanelCase):
    """No GitHub key anywhere and no AmneziaWG core on the disk (no checkout with bin/), a 2.x config."""

    def setUp(self):
        super().setUp()
        self._env = dict((k, os.environ.pop(k, None)) for k in ("GH_TOKEN", "GITHUB_TOKEN", "GOINST_TOKEN_FILE",
                                                                 "KASKAD_AWG_ROOTS"))
        os.environ["GOINST_TOKEN_FILE"] = self.path("/opt/go-installer/token")
        os.environ["KASKAD_AWG_ROOTS"] = self.path("/srv/no-checkout")
        self.write("/etc/kaskad/config", CONFIG)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def github(self, **answers):
        fake = FakeGithub(answers)
        # the check of the key and the download of the core (awg.fetch_core) both go through ghkey.open_github —
        # answered here, nothing goes to the network
        for p in (mock.patch.object(ghkey, "open_github", fake),):
            p.start()
            self.addCleanup(p.stop)
        return fake

    def no_key_in(self, *texts):
        for text in texts:
            for key in (KEY, KEY2):
                self.assertNotIn(key, text)


class GithubKeyTest(KeyCase):
    def test_state_without_a_key(self):
        doc = self.ok("GET", "/api/github")
        self.assertEqual((doc["set"], doc["state"], doc["source"], doc["ours"], doc["checked"]),
                         (False, "none", None, False, None))
        self.assertEqual(doc["repo"], awg.CORE_REPO)

    def test_admin_only_with_the_csrf_header(self):
        self.err(401, "GET", "/api/github", tunnel=False)
        self.err(401, "PUT", "/api/github", {"token": KEY}, tunnel=False)
        self.err(403, "PUT", "/api/github", {"token": KEY}, csrf=False)
        self.err(403, "DELETE", "/api/github", csrf=False)
        self.err(403, "POST", "/api/github/check", {}, csrf=False)
        self.assertEqual(self.call("GET", "/api/github/check").status, 405)
        self.assertEqual(self.read("/etc/kaskad/config"), CONFIG)

    def test_the_format_is_checked_before_github(self):
        gh = self.github(main=b"\x7f")
        for bad in ("hello", "ghp_short", "gho_" + "a" * 36, "ghp_" + "a" * 30 + "$(reboot)",
                    "ghp_" + "a" * 30 + '"; rm -rf /', "ghp_" + "a" * 20 + " " + "b" * 20, KEY + "\nX=1",
                    KEY + "`id`", "ghp_" + "я" * 30):
            doc = self.err(400, "PUT", "/api/github", {"token": bad})
            self.assertEqual(doc["data"], {"reason": "format"}, bad)
        for body in ({}, {"token": 12345}, {"token": None}, {"token": KEY * 10}):
            self.err(400, "PUT", "/api/github", body)
        self.assertEqual(gh.seen, [])                                       # nothing went to GitHub
        self.assertEqual(self.read("/etc/kaskad/config"), CONFIG)           # nothing written
        self.assertFalse(os.path.exists(ghkey.bot_file()))

    def test_a_working_key_is_written_where_the_menu_writes_it(self):
        gh = self.github(main=b"\x7f")
        r = self.call("PUT", "/api/github", {"token": "  %s\n" % KEY})      # pasted with blanks around
        self.assertEqual(r.status, 200, r)
        doc = r.json()
        self.assertEqual((doc["set"], doc["state"], doc["source"], doc["ours"]), (True, "ok", "bot", True))
        self.assertTrue(doc["checked"])
        # bot_state/system UPDATE_TOKEN (found first) and GITHUB_PAT of /etc/kaskad/config, both 0600; other lines stay
        text = self.read("/etc/kaskad/config")
        self.assertEqual(text.count("GITHUB_PAT="), 1)
        self.assertIn('GITHUB_PAT="%s"' % KEY, text)
        for line in ('BOT_CHAT_ID="42"', 'MENU_STYLE="inline"', 'BOT_PROXY=""'):
            self.assertIn(line, text)
        self.assertEqual(self.read("/etc/kaskad/bot_state/system"), "UPDATE_TOKEN=%s\n" % KEY)
        for p in (paths.config(), ghkey.bot_file()):
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)
        # the one request: the key only in the Authorization header, the core's own file, a short timeout
        ((url, authz, timeout),) = gh.seen
        self.assertEqual(url, "https://raw.githubusercontent.com/%s/main/bin/awg-linux-amd64" % ghkey.REPO)
        self.assertEqual(authz, "token " + KEY)
        self.assertLessEqual(timeout, 15)
        self.assertEqual(awg.gh_token(), KEY)                               # the AmneziaWG core takes it from here
        # never in an answer, the log or the record of the check
        self.no_key_in(r.text, self.read("/var/log/kaskad.log"), self.read("/etc/kaskad/panel/github.json"))
        for path in ("/api/github", "/api/gw/inbounds", "/api/gw", "/api/panel", "/api/overview", "/api/session"):
            self.no_key_in(self.call("GET", path).text)
        self.assertIn("ключ GitHub сохранён в панели", self.read("/var/log/kaskad.log"))

    @unittest.skipUnless(shutil.which("bash"), "нет bash")
    def test_bash_sources_the_config_and_gets_the_key_back(self):
        self.github(main=b"\x7f")
        self.ok("PUT", "/api/github", {"token": KEY2})
        out = subprocess.run(["bash", "-c", 'source "$1"; printf %s "$GITHUB_PAT|$MENU_STYLE"', "k", paths.config()],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(out.stdout.decode(), KEY2 + "|inline")
        self.assertEqual(out.stderr, b"")

    def test_a_key_github_refuses_is_not_saved(self):
        self.github(main=401)
        doc = self.err(400, "PUT", "/api/github", {"token": KEY})
        self.assertEqual(doc["data"], {"reason": "bad"})
        self.assertIn("не принял ключ", doc["error"])
        self.github(main=404, test=404)                                     # no access to the repository
        doc = self.err(400, "PUT", "/api/github", {"token": KEY})
        self.assertIn("нет доступа к этому репозиторию", doc["error"])
        self.no_key_in(json.dumps(doc), self.read("/var/log/kaskad.log") if os.path.exists(paths.log()) else "")
        self.assertEqual(self.read("/etc/kaskad/config"), CONFIG)
        self.assertFalse(os.path.exists(ghkey.bot_file()))
        self.assertEqual(self.ok("GET", "/api/github")["state"], "none")

    def test_the_test_branch_is_enough(self):
        gh = self.github(main=404, test=b"\x7f")
        self.assertEqual(self.ok("PUT", "/api/github", {"token": KEY2})["state"], "ok")
        self.assertEqual([u.split("/")[5] for u, _, _ in gh.seen], ["main", "test"])

    def test_no_answer_from_github_saves_only_when_asked(self):
        self.github(main="net")
        doc = self.err(409, "PUT", "/api/github", {"token": KEY})
        self.assertEqual(doc["data"], {"reason": "net"})
        self.assertEqual(self.ok("GET", "/api/github")["state"], "none")
        doc = self.ok("PUT", "/api/github", {"token": KEY, "force": True})
        self.assertEqual((doc["set"], doc["state"], doc["checked"]), (True, "unchecked", None))   # not checked: no time
        self.assertIn("Нет связи с GitHub", doc["error"])
        self.assertIn("без проверки", self.read("/var/log/kaskad.log"))
        self.github(main=401)
        doc = self.ok("POST", "/api/github/check", {})
        self.assertEqual(doc["state"], "bad")
        self.assertTrue(doc["checked"])
        self.github(main=b"\x7f")
        doc = self.ok("POST", "/api/github/check", {})
        self.assertEqual((doc["state"], doc["error"]), ("ok", ""))
        self.github(main=401)
        self.err(400, "PUT", "/api/github", {"token": KEY, "force": True})   # force is for «no answer» only
        self.no_key_in(json.dumps(doc), self.read("/var/log/kaskad.log"))

    def test_delete_and_a_key_that_is_not_ours(self):
        self.github(main=b"\x7f")
        self.ok("PUT", "/api/github", {"token": KEY})
        self.write("/opt/go-installer/token", KEY2 + "\n")
        doc = self.ok("DELETE", "/api/github")
        self.assertTrue(doc["removed"])
        self.assertIn('GITHUB_PAT=""', self.read("/etc/kaskad/config"))
        self.assertFalse(os.path.exists(ghkey.bot_file()))
        # goInstaller's own key: still in use, not ours to delete
        self.assertEqual((doc["set"], doc["source"], doc["ours"], doc["state"]), (True, "goinstaller", False, "unchecked"))
        self.assertFalse(self.ok("DELETE", "/api/github")["removed"])
        self.assertEqual(self.read("/opt/go-installer/token"), KEY2 + "\n")
        self.no_key_in(self.call("GET", "/api/github").text)
        self.assertIn("ключ GitHub удалён", self.read("/var/log/kaskad.log"))

    def test_the_menus_key_comes_first_and_other_lines_of_its_file_stay(self):
        self.write("/etc/kaskad/bot_state/system", "UPDATE_TOKEN=%s\nOTHER=1\n" % KEY2)
        doc = self.ok("GET", "/api/github")
        self.assertEqual((doc["set"], doc["source"], doc["state"]), (True, "bot", "unchecked"))
        self.github(main=b"\x7f")
        self.ok("PUT", "/api/github", {"token": KEY})
        self.assertEqual(self.read("/etc/kaskad/bot_state/system"), "UPDATE_TOKEN=%s\nOTHER=1\n" % KEY)
        self.ok("DELETE", "/api/github")
        self.assertEqual(self.read("/etc/kaskad/bot_state/system"), "OTHER=1\n")


class AwgNeedsKeyTest(KeyCase):
    """«Ввести ключ GitHub» right on the card of AmneziaWG: a field of the inbounds, files only; the refusal of an
    install says the same in ``data``."""

    def setUp(self):
        super().setUp()
        self.gateway()                                   # awg2 is a dummy adapter here, awg1 and awg3 — the real ones
        self.w.runner.on(["uname"], lambda argv, inp: (0, "x86_64\n", ""))

    def rows(self, path="/api/gw/inbounds"):
        return dict((r["kind"], r) for r in self.ok("GET", path)["inbounds"])

    def test_the_cards_say_a_key_is_needed_and_reads_run_nothing(self):
        before = len(self.w.runner.calls)
        rows = self.rows()
        self.assertEqual((rows["awg1"]["needs_key"], rows["awg3"]["needs_key"]), (True, True))
        for kind in ("wg", "l2tp", "openvpn", "awg2"):                   # installed / not AmneziaWG / the dummy
            self.assertIs(rows[kind]["needs_key"], False, kind)
        self.assertTrue(self.rows("/api/gw")["awg3"]["needs_key"])
        self.assertEqual(self.w.runner.calls[before:], [])               # no awg --version, no uname
        self.github(main=b"\x7f")
        self.ok("PUT", "/api/github", {"token": KEY})
        self.assertFalse(self.rows()["awg3"]["needs_key"])
        self.ok("DELETE", "/api/github")
        self.assertTrue(self.rows()["awg3"]["needs_key"])
        # a key the menu saved (not this panel): the view follows the file
        self.write("/etc/kaskad/bot_state/system", "UPDATE_TOKEN=%s\n" % KEY2)
        self.assertFalse(self.rows()["awg3"]["needs_key"])
        os.unlink(ghkey.bot_file())
        self.assertTrue(self.rows()["awg3"]["needs_key"])
        # goInstaller's key — files nobody watches: computed per request, not kept in the cached view
        self.write("/opt/go-installer/token", KEY2 + "\n")
        self.assertFalse(self.rows()["awg3"]["needs_key"])
        os.unlink(self.path("/opt/go-installer/token"))
        # a core on the disk (the copy of «Прокси для API Telegram»): no key needed
        d = os.path.join(paths.egress_dir(), "awg")
        os.makedirs(d, exist_ok=True)
        for name, marker in awg.MARKERS.items():
            with open(os.path.join(d, name), "wb") as f:
                f.write(b"\x7fELF fake " + marker)
        self.assertFalse(self.rows()["awg3"]["needs_key"])
        self.assertEqual(self.w.runner.calls[before:], [])

    def test_an_install_without_the_key_says_so_in_data(self):
        doc = self.err(409, "POST", "/api/gw/inbounds/awg3/install", {})
        self.assertEqual(doc["data"], {"needs_key": True, "key": "none"})
        self.assertIn("ключа GitHub", doc["error"])
        # a key GitHub refuses at the download: the same flag, «bad»
        self.github(main=b"\x7f")
        self.ok("PUT", "/api/github", {"token": KEY})
        self.github(main=401, test=401)
        doc = self.err(400, "POST", "/api/gw/inbounds/awg3/install", {})
        self.assertEqual(doc["data"], {"needs_key": True, "key": "bad"})
        self.no_key_in(json.dumps(doc), self.read("/var/log/kaskad.log"))
        # no connection: not the key's fault — no flag (a new key would not help)
        self.github(main="net", test="net")
        doc = self.err(400, "POST", "/api/gw/inbounds/awg3/install", {})
        self.assertNotIn("data", doc)


class PersonsPageTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        self.base = "/api/gw/users/%s/page" % self.uid

    def flag(self, cookie=None):
        doc = self.call("GET", "/api/gw/people", cookie=cookie)
        return [u for u in doc.json()["users"] if u["id"] == self.uid][0]["page"], doc.text

    def test_on_show_rotate_off_from_the_panel(self):
        doc = self.ok("GET", self.base)
        self.assertEqual((doc["enabled"], doc["url"], doc["path"]), (False, None, None))
        self.err(401, "GET", self.base, tunnel=False)
        self.err(403, "POST", self.base, {"action": "enable"}, csrf=False)
        self.err(401, "POST", self.base, {"action": "enable"}, tunnel=False)
        self.err(400, "POST", self.base, {"action": "explode"})
        self.assertEqual(self.flag()[0], False)
        doc = self.ok("POST", self.base, {"action": "enable"})
        path = doc["path"]
        self.assertRegex(path, r"^/p/[A-Za-z0-9_-]{43}$")
        # the panel is closed for the internet: no whole link — and the answer says why
        self.assertEqual((doc["enabled"], doc["url"], doc["public"], doc["note"]), (True, None, False, userpage.NOTE_CLOSED))
        page, text = self.flag()
        self.assertTrue(page)
        self.assertNotIn(path[3:], text)                                   # the list of people has the flag only
        self.assertEqual(self.ok("POST", self.base, {"action": "enable"})["path"], path)    # on again — the same link
        # the open mode: the link is whole (the tunnel then needs the password too)
        auth.set_password("правильный-пароль")
        tok = self.login("правильный-пароль", tunnel=True)
        port = self.public_on()
        doc = self.ok("GET", self.base, cookie=tok)
        self.assertEqual((doc["url"], doc["public"], doc["note"]), ("https://127.0.0.1:%d%s" % (port, path), True, ""))
        new = self.ok("POST", self.base, {"action": "rotate"}, cookie=tok)
        self.assertNotEqual(new["path"], path)
        self.assertIsNone(userpage.find_token(path[3:]))                   # the old link is dead at once
        doc = self.ok("POST", self.base, {"action": "disable"}, cookie=tok)
        self.assertEqual((doc["enabled"], doc["path"]), (False, None))
        self.assertFalse(self.flag(cookie=tok)[0])
        log = self.read("/var/log/kaskad.log")
        self.assertIn("Страница пользователя включена", log)
        self.assertNotIn(new["path"][3:], log)


class PanelAccessTest(PanelCase):
    def test_the_login_link_goes_where_people_can_open_it(self):
        doc = self.ok("POST", "/api/panel/link", {"ttl": 900})
        self.assertTrue(doc["url"].startswith("http://127.0.0.1:%d/login?t=" % self.port))
        self.assertEqual((doc["public"], doc["expires_in"]), (False, 900))
        self.err(403, "POST", "/api/panel/link", {}, csrf=False)
        auth.set_password("правильный-пароль")
        tok = self.login("правильный-пароль", tunnel=True)
        port = self.public_on()
        # asked over the tunnel, in the open mode: the address of the open mode (as the links of the bot and the menu)
        doc = self.ok("POST", "/api/panel/link", {}, cookie=tok)
        self.assertTrue(doc["url"].startswith("https://127.0.0.1:%d%s/login?t=" % (port, self.pb)))
        self.assertTrue(doc["public"])
        token = re.search(r"t=([A-Za-z0-9_-]+)", doc["url"]).group(1)
        self.assertNotIn(token, self.read("/var/log/kaskad.log"))

    def test_session_tells_the_way_in(self):
        doc = self.ok("GET", "/api/session")
        self.assertEqual((doc["via"], doc["scheme"]), ("tunnel", "http"))
        auth.set_password("правильный-пароль")
        tok = self.login("правильный-пароль", tunnel=True)
        doc = self.ok("GET", "/api/session", cookie=tok)
        self.assertEqual((doc["via"], doc["login"]), ("password", "admin"))


class KeyRedirectTest(unittest.TestCase):
    """The key goes only to GitHub: a redirect to another host goes without «Authorization» (urllib keeps headers)."""

    def test_authorization_dropped_on_another_host(self):
        h = ghkey._KeyStaysHome()
        req = urllib.request.Request("https://raw.githubusercontent.com/o/r/main/bin/x",
                                     headers={"Authorization": "token ghp_" + "a" * 36})
        same = h.redirect_request(req, None, 302, "Found", {}, "https://raw.githubusercontent.com/o/r/test/bin/x")
        other = h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/collect")
        self.assertIsNotNone(same.get_header("Authorization"))
        self.assertIsNone(other.get_header("Authorization"))
