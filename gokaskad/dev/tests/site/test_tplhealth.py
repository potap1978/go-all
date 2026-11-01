"""kaskad.site.tplhealth — «жив ли шаблон» (3.2.21): the repository by an anonymous ``git ls-remote`` (FakeRunner),
the preview by HEAD (a fake ``_open``), the cache with its terms, the marks of the deployment, «Случайный» that walks
around removed templates and an explicit removed one refused without a clone. No network: git and HTTP are fakes,
the fixture repositories are local (tests/site/fixtures_templates.py)."""

import hashlib
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from kaskad import paths
from kaskad.site import templates as T
from kaskad.site import tplhealth as H
from kaskad.sysrun import FakeRunner
from tests.site import fixtures_templates as fx
from tests.site.test_templates import HAVE_GIT, Base, DeployBase

DAY = 86400
REAL_OPEN = H._open                     # before any test replaces it
PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 0))]
LS = ["env", "GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS="]
_REPOS = []


def my_repos():
    """The fixture repositories of this module (test_templates removes its own at its end)."""
    if not _REPOS:
        d = tempfile.mkdtemp(prefix="kaskad-tplh-repos-")
        with mock.patch.dict(os.environ, {"KASKAD_TEST_GIT_HOME": d}):
            fx.build_repos(d)
        _REPOS.append(d)
    return _REPOS[0]


def tearDownModule():
    for d in _REPOS:
        shutil.rmtree(d, ignore_errors=True)


class Resp(io.BytesIO):
    def __init__(self, status, body=b""):
        super().__init__(body)
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def http_error(url, code):
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))


class Web(object):
    """A fake ``tplhealth._open``: answers[url] = status | (status, body) | an exception; ``seen`` — (method, url).
    A status of 400+ is raised as HTTPError, as urllib does."""

    def __init__(self, answers=None, default=200):
        self.answers = dict(answers or {})
        self.default = default
        self.seen = []
        self.headers = []

    def __call__(self, req, timeout, schemes):
        self.seen.append((req.get_method(), req.full_url))
        self.headers.append(dict(req.header_items()))
        a = self.answers.get((req.get_method(), req.full_url), self.answers.get(req.full_url, self.default))
        if isinstance(a, BaseException):
            raise a
        status, body = a if isinstance(a, tuple) else (a, b"")
        if status >= 400:
            raise http_error(req.full_url, status)
        return Resp(status, body)


class HealthBase(Base):
    def setUp(self):
        super().setUp()
        self.put_catalog()
        self.now = [1_800_000_000.0]
        p = mock.patch.object(H, "_clock", lambda: self.now[0])
        p.start()
        self.addCleanup(p.stop)
        self.web = Web()
        p = mock.patch.object(H, "_open", self.web)
        p.start()
        self.addCleanup(p.stop)
        self.dns = {}                                   # name -> [ip]; the rest — a public address; no real DNS
        p = mock.patch.object(H, "_resolve", lambda host, *a: [(2, 1, 6, "", (ip, 0)) for ip in self.dns[host]]
                              if host in self.dns else PUBLIC)
        p.start()
        self.addCleanup(p.stop)

    def git(self, rc=0, out="abc\trefs/heads/main\n", err="", binaries=("git", "timeout")):
        r = FakeRunner(binaries=list(binaries))
        r.on(["timeout"], (rc, out, err))
        r.on(LS, (rc, out, err))
        return r


# ── the repository ──────────────────────────────────────────────────────────

class TestRepo(HealthBase):
    URL = "https://github.com/fx/good"

    def test_ok_and_the_call_is_anonymous(self):
        os.environ["GH_TOKEN"] = "ghp_" + "s" * 36
        r = self.git()
        self.assertEqual(H.repo_state(r, self.URL), "ok")
        argv = r.calls[0][0]
        self.assertEqual(argv[:4], ["timeout", "-k", "2", "8"])
        for need in ("GIT_TERMINAL_PROMPT=0", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null",
                     "credential.helper=", "protocol.allow=never", "protocol.https.allow=always",
                     "protocol.file.allow=never", "protocol.ext.allow=never"):
            self.assertIn(need, argv)
        self.assertEqual(argv[-4:], ["ls-remote", "--exit-code", "-h", self.URL])
        self.assertNotIn("ghp_", " ".join(argv))
        self.assertFalse(any("Authorization" in a or "extraheader" in a for a in argv))

    def test_removed_repository(self):
        for err in ("remote: Repository not found.\nfatal: repository 'https://github.com/fx/x/' not found\n",
                    "fatal: could not read Username for 'https://github.com': terminal prompts disabled\n"):
            self.assertEqual(H.repo_state(self.git(128, "", err), self.URL), "gone", err)

    def test_empty_repository_is_removed(self):
        self.assertEqual(H.repo_state(self.git(2, "", ""), self.URL), "gone")

    def test_timeout_and_network_are_unknown(self):
        self.assertEqual(H.repo_state(self.git(124, "", ""), self.URL), "unknown")
        self.assertEqual(H.repo_state(self.git(137, "", ""), self.URL), "unknown")
        for err in ("fatal: unable to access 'https://github.com/fx/good/': Could not resolve host: github.com",
                    "fatal: unable to access 'https://github.com/fx/good/': Failed to connect to github.com port 443",
                    "fatal: unable to access 'https://github.com/fx/good/': gnutls_handshake() failed",
                    "fatal: unable to access 'https://github.com/fx/good/': The requested URL returned error: 403"):
            self.assertEqual(H.repo_state(self.git(128, "", err), self.URL), "unknown", err)

    def test_without_timeout_binary_the_runner_limits(self):
        r = self.git(binaries=("git",))
        self.assertEqual(H.repo_state(r, self.URL), "ok")
        self.assertEqual(r.calls[0][0][:3], LS)

    def test_bad_url_is_not_asked(self):
        r = self.git()
        for url in ("http://github.com/fx/good", "file:///etc", "https://github.com/fx/a;b", ""):
            self.assertEqual(H.repo_state(r, url), "unknown")
        self.assertEqual(r.calls, [])

    def test_without_git_the_same_question_over_https(self):
        r = self.git(binaries=("timeout",))
        refs = self.URL + "/info/refs?service=git-upload-pack"
        self.web.answers[refs] = (200, b"001e# service=git-upload-pack\n0000015547b3 HEAD\x00multi_ack\n")
        self.assertEqual(H.repo_state(r, self.URL), "ok")
        self.assertEqual(self.web.seen, [("GET", refs)])
        self.assertNotIn("Authorization", self.web.headers[0])
        self.web.answers[refs] = 401                # GitHub: a missing repository looks private
        self.assertEqual(H.repo_state(r, self.URL), "gone")
        self.web.answers[refs] = (200, b"001e# service=git-upload-pack\n0000003f0000 capabilities^{}\x00x\n0000")
        self.assertEqual(H.repo_state(r, self.URL), "gone")
        self.web.answers[refs] = urllib.error.URLError("down")
        self.assertEqual(H.repo_state(r, self.URL), "unknown")
        self.web.answers[refs] = 503
        self.assertEqual(H.repo_state(r, self.URL), "unknown")
        self.assertEqual(r.calls, [])

    def test_git_gone_texts(self):
        self.assertTrue(H.git_gone("remote: Repository not found. | fatal: repository 'x' not found"))
        # «ssl» and «tls» in the address are not the network: 17 templates of the catalog are …/classless-…
        for url in ("https://github.com/fx/classless-site", "https://github.com/fx/atlas-tls-landing"):
            err = "remote: Repository not found.\nfatal: repository '%s/' not found\n" % url
            self.assertTrue(H.git_gone(err), url)
            self.assertEqual(H.repo_state(self.git(128, "", err), url), "gone")
        self.assertFalse(H.git_gone("fatal: unable to access 'https://github.com/fx/x/': SSL certificate problem"))
        self.assertFalse(H.git_gone("fatal: unable to access 'x': Could not resolve host: github.com"))
        self.assertFalse(H.git_gone(""))


# ── the preview ─────────────────────────────────────────────────────────────

class TestPreview(HealthBase):
    URL = "https://someone.github.io/site/"

    def test_states(self):
        self.web.answers[self.URL] = 200
        self.assertEqual(H.preview_state(self.URL), "ok")
        self.assertEqual(self.web.seen[-1], ("HEAD", self.URL))
        for code in (404, 410):
            self.web.answers[self.URL] = code
            self.assertEqual(H.preview_state(self.URL), "gone")
        for code in (403, 500, 503):
            self.web.answers[self.URL] = code
            self.assertEqual(H.preview_state(self.URL), "unknown")
        self.web.answers[self.URL] = urllib.error.URLError("timed out")
        self.assertEqual(H.preview_state(self.URL), "unknown")
        self.web.answers[self.URL] = OSError("reset")
        self.assertEqual(H.preview_state(self.URL), "unknown")

    def test_head_not_allowed_then_get(self):
        for head in (405, 501):
            for got, want in ((200, "ok"), (404, "gone"), (500, "unknown")):
                self.web.seen = []
                self.web.answers = {("HEAD", self.URL): head, ("GET", self.URL): (got, b"<html>" + b"x" * 9000)}
                self.assertEqual(H.preview_state(self.URL), want)
                self.assertEqual([m for m, _ in self.web.seen], ["HEAD", "GET"])

    def test_only_public_addresses(self):
        for url in ("http://127.0.0.1/", "http://10.1.2.3/x", "http://169.254.169.254/latest/meta-data/",
                    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://192.168.1.1/", "http://0.0.0.0/",
                    "http://[fe80::1]/"):
            with self.assertRaises(urllib.error.URLError, msg=url):
                H.check_target(url)
        self.dns["intranet.example"] = ["10.0.0.7"]
        self.dns["mixed.example"] = ["93.184.216.34", "127.0.0.1"]
        for url in ("https://intranet.example/", "https://mixed.example/"):
            with self.assertRaises(urllib.error.URLError, msg=url):
                H.check_target(url)
        H.check_target("https://someone.github.io/site/")              # a public name
        H.check_target("http://93.184.216.34/")
        with mock.patch.object(H, "_resolve", side_effect=socket.gaierror("no name")):
            with self.assertRaises(urllib.error.URLError):
                H.check_target("https://nowhere.example/")
        # the real _open refuses before any connection; a redirect to such an address is refused too
        with mock.patch.object(H.urllib.request, "build_opener", side_effect=AssertionError("connected")):
            with mock.patch.object(H, "_open", REAL_OPEN):
                self.assertEqual(H.preview_state("http://169.254.169.254/"), "unknown")
        req = urllib.request.Request("https://a.example/x", method="HEAD")
        for bad in ("http://169.254.169.254/", "https://intranet.example/x"):
            with self.assertRaises(urllib.error.URLError):
                H._Redirect(("http", "https")).redirect_request(req, io.BytesIO(b""), 302, "Found", {}, bad)

    def test_only_http(self):
        for url in ("ftp://example.org/x", "file:///etc/passwd", "javascript:alert(1)", ""):
            self.assertEqual(H.preview_state(url), "unknown")
        self.assertEqual(self.web.seen, [])

    def test_redirects_only_to_http_and_head_stays_head(self):
        h = H._Redirect(("http", "https"))
        req = urllib.request.Request("https://a.example/x", method="HEAD")
        for bad in ("ftp://a.example/y", "file:///etc/passwd"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                h.redirect_request(req, io.BytesIO(b""), 302, "Found", {}, bad)
            cm.exception.close()
        new = h.redirect_request(req, io.BytesIO(b""), 301, "Moved", {}, "https://b.example/y")
        self.assertEqual((new.get_method(), new.full_url), ("HEAD", "https://b.example/y"))
        with self.assertRaises(urllib.error.HTTPError) as cm:   # the repository over https: https only
            H._Redirect(("https",)).redirect_request(req, io.BytesIO(b""), 302, "Found", {}, "http://b.example/")
        cm.exception.close()



# ── a page of templates and the cache ───────────────────────────────────────

class TestCheck(HealthBase):
    """The fixture catalog: gh_good, gh_dist, gh_cafe … with previews https://example.org/<id>/."""

    def runner(self, gone=(), unknown=(), slow=None):
        """git ls-remote: urls of ``gone`` — not found, of ``unknown`` — no network, the rest — ok."""
        r = FakeRunner(binaries=["git", "timeout"])

        def ls(argv, inp):
            url = argv[-1]
            if slow is not None and url in slow[0]:
                slow[1].wait(10)
            if url in gone:
                return 128, "", "remote: Repository not found.\n"
            if url in unknown:
                return 128, "", "fatal: unable to access '%s': Could not resolve host: github.com" % url
            return 0, "abc\trefs/heads/main\n", ""
        r.on(["timeout"], ls)
        return r

    def test_the_four_states(self):
        self.web.answers["https://example.org/gh_cafe/"] = 404
        r = self.runner(gone=(fx.GH + "dist",), unknown=(fx.GH + "home",))
        got = H.check(r, ["gh_good", "gh_dist", "gh_cafe", "gh_home"])
        self.assertEqual(dict((k, v["state"]) for k, v in got.items()),
                         {"gh_good": "alive", "gh_dist": "gone", "gh_cafe": "no_preview", "gh_home": "unknown"})
        self.assertEqual(got["gh_dist"]["text"], "Шаблон удалён автором — выбрать нельзя")
        self.assertEqual(got["gh_cafe"]["text"], "Образец снят автором, сам шаблон ставится")
        self.assertEqual(got["gh_home"]["text"], "Не удалось проверить")
        previews = [u for _, u in self.web.seen]
        self.assertNotIn("https://example.org/gh_dist/", previews)   # removed — the preview is not asked
        self.assertNotIn("https://example.org/gh_home/", previews)   # the repository did not answer
        self.assertEqual(H.cached(["gh_good", "gh_dist", "gh_cafe", "gh_home", "nope"]),
                         {"gh_good": H.doc("alive"), "gh_dist": H.doc("gone"), "gh_cafe": H.doc("no_preview"),
                          "gh_home": None, "nope": None})

    def test_no_preview_in_the_catalog_is_alive(self):
        self.put_catalog(json.dumps({"categories": [{"id": "business", "name": "B", "templates": [
            dict(fx._t("gh_good", "Good", fx.GH + "good"), preview_url="")]}]}).encode())
        with open(T.catalog_path(), "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        with mock.patch.object(T, "CATALOG_SHA256", sha):
            self.assertEqual(H.check(self.runner(), ["gh_good"])["gh_good"]["state"], "alive")
        self.assertEqual(self.web.seen, [])

    def test_fresh_answers_are_not_asked_again_unknown_is_not_kept(self):
        r = self.runner(unknown=(fx.GH + "home",))
        H.check(r, ["gh_good", "gh_home"])
        n = len(r.calls)
        H.check(r, ["gh_good", "gh_home"])
        self.assertEqual([c[0][-1] for c in r.calls[n:]], [fx.GH + "home"])     # only the unknown one again

    def test_terms_of_the_cache(self):
        self.web.answers["https://example.org/gh_cafe/"] = 404
        r = self.runner(gone=(fx.GH + "dist",))
        H.check(r, ["gh_good", "gh_dist", "gh_cafe"])
        self.now[0] += 7 * DAY - 60
        self.assertEqual([v and v["state"] for v in H.cached(["gh_good", "gh_dist", "gh_cafe"]).values()],
                         ["alive", "gone", "no_preview"])
        self.now[0] += 120                                           # past 7 days: the living ones are old
        self.assertEqual(H.cached(["gh_good", "gh_cafe", "gh_dist"]), {"gh_good": None, "gh_cafe": None,
                                                                         "gh_dist": H.doc("gone")})
        self.assertTrue(H.is_gone("gh_dist"))
        self.now[0] += 23 * DAY                                      # past 30 days: the removed one too
        self.assertIsNone(H.cached(["gh_dist"])["gh_dist"])
        self.assertFalse(H.is_gone("gh_dist"))
        H.mark("gh_good", "alive")                                   # a write drops the old entries
        with open(paths.site_tpl_health(), encoding="utf-8") as f:
            self.assertEqual(sorted(json.load(f)["items"]), ["gh_good"])

    def test_file_rights(self):
        H.check(self.runner(), ["gh_good"])
        p = paths.site_tpl_health()
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(p)).st_mode & 0o777, 0o700)
        self.assertEqual([n for n in os.listdir(os.path.dirname(p)) if n.startswith(".tmp")], [])

    def test_damaged_cache_is_empty(self):
        os.makedirs(os.path.dirname(paths.site_tpl_health()), exist_ok=True)
        with open(paths.site_tpl_health(), "w") as f:
            f.write("{not json")
        self.assertEqual(H.cached(["gh_good"]), {"gh_good": None})
        H.check(self.runner(), ["gh_good"])
        self.assertEqual(H.cached(["gh_good"])["gh_good"]["state"], "alive")

    def test_unknown_ids_and_the_limit(self):
        r = self.runner()
        got = H.check(r, ["nope", 7, "gh_good"] + ["x%d" % i for i in range(40)])
        self.assertEqual(got["nope"]["state"], "unknown")
        self.assertNotIn(7, got)
        self.assertEqual(len(got), H.MAX_IDS - 1)                    # 25 looked at, one of them not a string
        self.assertEqual(len(r.calls), 1)

    def test_eight_at_once_and_the_deadline(self):
        gate = threading.Event()
        state = {"now": 0, "max": 0}
        lock = threading.Lock()
        slow_urls = set(fx.GH + n for n in ("noindex", "appshell", "emptyshell", "broken", "big", "hang", "fail",
                                            "home", "nolic", "port", "trattoria"))
        r = FakeRunner(binaries=["git", "timeout"])

        def ls(argv, inp):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            try:
                if argv[-1] in slow_urls:
                    gate.wait(10)
                    return 124, "", ""          # late anyway: no preview asked after the test ends
                return 0, "abc\trefs/heads/main\n", ""
            finally:
                with lock:
                    state["now"] -= 1
        r.on(["timeout"], ls)
        ids = ["gh_good", "gh_dist", "gh_cafe", "gh_noindex", "gh_appshell", "gh_emptyshell", "gh_broken", "gh_big",
               "gh_hang", "gh_fail", "gh_home", "gh_nolic", "gh_port", "gh_trattoria"]
        t0 = time.monotonic()
        with mock.patch.object(H, "LS_TIMEOUT", 8):
            got = H.check(r, ids, deadline=1.5)
        spent = time.monotonic() - t0
        late = [t for t in threading.enumerate() if t.name == "kaskad-tplcheck" and t.is_alive()]
        gate.set()
        self.assertLess(spent, 2.5)                                  # a hard term: the late git is not waited for
        self.assertTrue(late and all(t.daemon for t in late))        # …and does not hold kaskadctl at its exit
        self.assertLessEqual(state["max"], H.WORKERS)
        self.assertEqual(state["max"], H.WORKERS)
        self.assertEqual([got[i]["state"] for i in ("gh_good", "gh_dist", "gh_cafe")], ["alive"] * 3)
        self.assertTrue(all(got[i]["state"] == "unknown" for i in ids[3:]))
        self.assertEqual(sorted(k for k, v in H.cached(ids).items() if v), ["gh_cafe", "gh_dist", "gh_good"])

    def test_one_ls_remote_per_repository(self):
        """dawidolko and html5up keep many templates in one repository: one question for the page."""
        mono = "https://github.com/fx/html5up"
        doc = {"categories": [{"id": "education", "name": "E", "templates": [
            fx._t("h5_%d" % i, "T%d" % i, mono, source="html5up", sparse="t%d" % i) for i in range(6)]}]}
        self.put_catalog(json.dumps(doc).encode())
        with open(T.catalog_path(), "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        r = self.runner()
        with mock.patch.object(T, "CATALOG_SHA256", sha):
            got = H.check(r, ["h5_%d" % i for i in range(6)])
        self.assertEqual(set(v["state"] for v in got.values()), {"alive"})
        self.assertEqual([c[0][-1] for c in r.calls], [mono])
        self.assertEqual(len(self.web.seen), 6)                       # the previews are their own

    def test_one_pool_for_the_whole_process(self):
        """Pages flipped fast: every batch waits for a place — never more than WORKERS git at once."""
        gate = threading.Event()
        state = {"now": 0, "max": 0}
        lock = threading.Lock()
        r = FakeRunner(binaries=["git", "timeout"])

        def ls(argv, inp):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            gate.wait(10)
            with lock:
                state["now"] -= 1
            return 124, "", ""
        r.on(["timeout"], ls)
        ids = ["gh_good", "gh_dist", "gh_cafe", "gh_noindex", "gh_appshell", "gh_emptyshell", "gh_broken", "gh_big",
               "gh_hang", "gh_fail", "gh_home", "gh_trattoria"]
        batches = [threading.Thread(target=H.check, args=(r, ids[k:] + ids[:k]), kwargs={"deadline": 1.5})
                   for k in (0, 4, 8)]
        for b in batches:
            b.start()
        for b in batches:
            b.join(5)
        gate.set()
        self.assertEqual(state["max"], H.WORKERS)
        deadline = time.monotonic() + 5
        while state["now"] and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_force_asks_again(self):
        r = self.runner()
        H.check(r, ["gh_good"])
        H.check(r, ["gh_good"], force=True)
        self.assertEqual(len(r.calls), 2)

    def test_mark(self):
        H.mark("gh_good", "gone")
        self.assertTrue(H.is_gone("gh_good"))
        H.mark("gh_good", "alive")
        self.assertEqual(H.cached(["gh_good"])["gh_good"]["state"], "alive")
        r = self.runner()
        H.check(r, ["gh_good"])                       # alive from a deployment: the preview is still asked
        self.assertEqual(self.web.seen, [("HEAD", "https://example.org/gh_good/")])
        self.web.answers["https://example.org/gh_cafe/"] = 404
        H.check(r, ["gh_cafe"])
        H.mark("gh_cafe", "alive")                    # a deployment does not hide «образец снят»
        self.assertEqual(H.cached(["gh_cafe"])["gh_cafe"]["state"], "no_preview")
        H.mark("gh_cafe", "unknown")                  # nothing else is kept
        H.mark(None, "gone")
        self.assertEqual(H.cached(["gh_cafe"])["gh_cafe"]["state"], "no_preview")


# ── the deployment: marks, «Случайный», an explicit removed one ─────────────

class HealthDeployBase(DeployBase):
    """DeployBase on the repositories of this module."""

    def setUp(self):
        super().setUp()
        self.repos = my_repos()
        os.environ["KASKAD_TEST_GIT_HOME"] = self.repos
        self.world = fx.GitWorld(self.repos, self.clock)
        self.runner = self.world.runner


@unittest.skipUnless(HAVE_GIT, "нужен git для локальных репозиториев-фикстур")
class TestDeployHealth(HealthDeployBase):

    def test_failed_clone_marks_removed_success_marks_alive(self):
        res = T.deploy(self.runner, "gh_fail")
        self.assertEqual((res["ok"], res["reason"]), (False, "gone"))
        self.assertEqual(res["text"], "Шаблон «gh_fail» удалён автором — выберите другой.")
        self.assertTrue(H.is_gone("gh_fail"))
        self.assertTrue(T.deploy(self.runner, "gh_good")["ok"])
        self.assertEqual(H.cached(["gh_good"])["gh_good"]["state"], "alive")
        self.assertEqual(self.world.probes, [])               # an explicit id is not probed: the clone tells

    def test_network_failure_marks_nothing(self):
        url = fx.GH + "good"
        self.world.clone_fail[url] = (128, "", "fatal: unable to access '%s/': Could not resolve host: github.com\n"
                                      % url)
        res = T.deploy(self.runner, "gh_good")
        self.assertEqual(res["reason"], "download_failed")
        self.assertEqual(H.cached(["gh_good"]), {"gh_good": None})

    def test_explicit_removed_is_refused_without_a_clone(self):
        H.mark("gh_good", "gone")
        self.put_old_site()
        res = T.deploy(self.runner, "gh_good")
        self.assertEqual((res["ok"], res["reason"], res["tries"]), (False, "gone", 0))
        self.assertIn("удалён автором", res["text"])
        self.assertEqual(self.runner.calls, [])               # no clone, no ls-remote, no apt
        self.assertEqual(self.site_read("index.html"), "OLD SITE")

    def test_random_skips_the_known_removed(self):
        H.mark("gh_cafe", "gone")
        self.seq("gh_cafe", "gh_good")
        for _ in range(5):
            self.assertNotEqual(T.pick_random(), "gh_cafe")
        res = T.deploy(self.runner)
        self.assertEqual((res["ok"], res["id"], res["tries"]), (True, "gh_good", 1))
        self.assertEqual(self.world.probes, [fx.GH + "good"])

    def test_random_probes_the_candidate_and_takes_another(self):
        self.world.ls[fx.GH + "cafe"] = (128, "", "remote: Repository not found.\n")
        self.world.ls[fx.GH + "trattoria"] = (2, "", "")                   # empty: nothing to install
        self.seq("gh_cafe", "gh_trattoria", "gh_good")
        res = T.deploy(self.runner)
        self.assertEqual((res["ok"], res["id"], res["tries"]), (True, "gh_good", 1))
        self.assertEqual(self.world.probes, [fx.GH + "cafe", fx.GH + "trattoria", fx.GH + "good"])
        self.assertEqual([u for u, _ in self.world.clones], [fx.GH + "good"])
        self.assertTrue(H.is_gone("gh_cafe") and H.is_gone("gh_trattoria"))

    def test_random_unknown_network_still_tries_the_clone(self):
        self.world.ls[fx.GH + "good"] = (124, "", "")
        self.seq("gh_good")
        res = T.deploy(self.runner)
        self.assertEqual((res["ok"], res["id"]), (True, "gh_good"))

    def test_random_probes_are_limited(self):
        """Every candidate removed: at most MAX_TRIES * 3 probes, then at most MAX_TRIES clones — then the stub."""
        def gone(argv, inp):
            self.world.probes.append(argv[-1])
            return 128, "", "remote: Repository not found.\n"
        self.world.runner._handlers.insert(0, (["timeout", "-k", "2"], gone))
        res = T.deploy(self.runner)
        self.assertEqual(len(self.world.probes), T.MAX_TRIES * 3)
        self.assertLessEqual(res["tries"], T.MAX_TRIES)
        self.assertTrue(res["ok"])                     # a template not probed, or the stub


@unittest.skipUnless(HAVE_GIT, "нужен git для локальных репозиториев-фикстур")
class TestCliCheck(HealthDeployBase):
    """kaskadctl site templates --check (SSH-меню 19): health у каждой строки страницы; без --check — только кэш."""

    def run_cli(self, *argv):
        import contextlib
        from kaskad import cli
        from tests.site.test_templates import site_parser
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "build_parser", site_parser), mock.patch.object(T, "_runner", lambda: self.runner), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue()

    def test_check_flag(self):
        web = Web({"https://example.org/gh_cafe/": 404})
        self.world.ls[fx.GH + "dist"] = (128, "", "remote: Repository not found.\n")
        with mock.patch.object(H, "_open", web):
            rc, out = self.run_cli("site", "templates", "--category", "business", "--json")
            self.assertEqual(self.world.probes, [])                      # без --check — без сети
            self.assertEqual([i["health"] for i in json.loads(out)["items"]], [None, None, None])
            rc, out = self.run_cli("site", "templates", "--category", "business", "--check", "--json")
            doc = json.loads(out)
            self.assertEqual((rc, [i["health"]["state"] for i in doc["items"]]), (0, ["alive", "gone", "no_preview"]))
            rc, out = self.run_cli("site", "templates", "--category", "business")       # кэш: те же пометки
        self.assertIn("удалён автором — выбрать нельзя", out)
        self.assertIn("образец снят, шаблон ставится", out)
        self.assertIn("превью: https://example.org/gh_good/", out)
        self.assertNotIn("https://example.org/gh_dist/", out)
        self.assertNotIn("https://example.org/gh_cafe/", out)
        self.assertEqual(self.world.clones, [])


if __name__ == "__main__":
    unittest.main()
