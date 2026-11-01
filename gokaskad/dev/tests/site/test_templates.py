"""kaskad.site.templates — the catalog of masquerade sites and their deployment (docs/k3/SPEC-wave4.md §5, §5.1).

Offline: the catalog is a small fixture (its sha256 patched in), GitHub is a fake opener, the templates are local
git repositories cloned by the real git through a fake runner (tests/site/fixtures_templates.py).
"""

import contextlib
import gzip
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from unittest import mock

from kaskad import cli, errors, ghkey, paths
from kaskad.errors import KaskadError
from kaskad.site import templates as T
from tests.helpers import RootCase
from tests.site import fixtures_templates as fx

HAVE_GIT = shutil.which("git") is not None
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Base(RootCase):
    """KASKAD_ROOT in a temp dir, no checkout with assets, no GitHub key, the fixture catalog's sha256."""

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {"KASKAD_ASSETS_ROOTS": "", "GH_TOKEN": "", "GITHUB_TOKEN": "",
                                                "GOINST_TOKEN_FILE": self.path("/nonexistent")})
        self.env.start()
        self.addCleanup(self.env.stop)
        p = mock.patch.object(T, "CATALOG_SHA256", fx.catalog_sha())
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(T, "MIN_FREE", 0)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(ghkey, "open_github", side_effect=AssertionError("no network in tests"))
        self.opener = p.start()
        self.addCleanup(p.stop)

    def put_catalog(self, data=None):
        p = T.catalog_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(fx.catalog_bytes() if data is None else data)
        return p


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def http_error(url, code):
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))


# ── the catalog: fetch_catalog ──────────────────────────────────────────────

class TestFetchCatalog(Base):

    def test_present_and_right_is_not_downloaded(self):
        self.put_catalog()
        res = T.fetch_catalog(None)
        self.assertEqual((res["ok"], res["downloaded"], res["from"]), (True, False, "disk"))
        self.opener.assert_not_called()

    def test_no_key_is_a_clear_error(self):
        with self.assertRaises(KaskadError) as cm:
            T.fetch_catalog(None)
        self.assertEqual(cm.exception.data["reason"], "nokey")
        self.assertIn("ключ", str(cm.exception))
        self.assertIn("gokaskad → 8 → 6", str(cm.exception))
        self.assertFalse(os.path.exists(T.catalog_path()))

    def test_from_github_with_the_key_checked_and_atomic(self):
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        seen = []

        def opener(req, timeout):
            seen.append((req.full_url, req.get_header("Authorization")))
            return FakeResponse(fx.gz(fx.catalog_bytes()))
        self.opener.side_effect = opener
        res = T.fetch_catalog(None)
        self.assertEqual((res["downloaded"], res["from"], res["ref"]), (True, "github", "main"))
        self.assertEqual(seen, [("https://raw.githubusercontent.com/%s/main/assets/templates_catalog.json.gz"
                                 % ghkey.REPO, "token ghp_" + "a" * 36)])
        with open(T.catalog_path(), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), fx.catalog_sha())
        self.assertEqual(os.stat(T.catalog_path()).st_mode & 0o777, 0o644)
        self.assertEqual([n for n in os.listdir(paths.assets_dir()) if n.startswith(".tmp")], [])

    def test_branch_of_the_self_update_first_then_main_test(self):
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        self.write("/etc/kaskad/config", 'KASKAD_BRANCH="dev3"\n')
        urls = []

        def opener(req, timeout):
            urls.append(req.full_url)
            if "/test/" not in req.full_url:
                raise http_error(req.full_url, 404)
            return FakeResponse(fx.gz(fx.catalog_bytes()))
        self.opener.side_effect = opener
        res = T.fetch_catalog(None)
        self.assertEqual(res["ref"], "test")
        self.assertEqual([u.split("/")[5] for u in urls], ["dev3", "main", "test"])

    def test_without_the_branch_the_catalog_comes_from_dev(self):
        """O, 2026-10-08: installed from dev without KASKAD_BRANCH — main and test have no catalog (404), dev has it (k3 before 3.3.4)."""
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        urls = []

        def opener(req, timeout):
            urls.append(req.full_url)
            if "/dev/" not in req.full_url:
                raise http_error(req.full_url, 404)
            return FakeResponse(fx.gz(fx.catalog_bytes()))
        self.opener.side_effect = opener
        res = T.fetch_catalog(None)
        self.assertEqual((res["from"], res["ref"]), ("github", "dev"))
        self.assertEqual([u.split("/")[5] for u in urls], ["main", "test", "dev"])
        self.assertEqual(T.catalog()["total"], 17)

    def test_the_branch_of_the_node_is_tried_first_and_once(self):
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        self.write("/etc/kaskad/config", 'KASKAD_BRANCH="k3"\n')
        urls = []

        def opener(req, timeout):
            urls.append(req.full_url)
            return FakeResponse(fx.gz(fx.catalog_bytes()))
        self.opener.side_effect = opener
        self.assertEqual(T.fetch_catalog(None)["ref"], "k3")
        self.assertEqual([u.split("/")[5] for u in urls], ["k3"])
        self.assertEqual(T._refs(), ["k3", "main", "test", "dev"])    # 3.3.4: узел ещё на k3 (до перехода) — k3 первой

    def test_damaged_download_is_not_written(self):
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        bad = fx.catalog_bytes().replace(b"Good Business", b"Evil Business")
        self.opener.side_effect = lambda req, timeout: FakeResponse(fx.gz(bad))
        with self.assertRaises(KaskadError) as cm:
            T.fetch_catalog(None)
        self.assertEqual(cm.exception.data["reason"], "sha")
        self.assertFalse(os.path.exists(T.catalog_path()))

    def test_not_a_gzip_is_damaged(self):
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        self.opener.side_effect = lambda req, timeout: FakeResponse(b"<html>404</html>")
        with self.assertRaises(KaskadError) as cm:
            T.fetch_catalog(None)
        self.assertEqual(cm.exception.data["reason"], "sha")

    def test_damaged_file_on_disk_is_replaced(self):
        self.put_catalog(b'{"categories": []}')
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        self.opener.side_effect = lambda req, timeout: FakeResponse(fx.gz(fx.catalog_bytes()))
        with self.assertRaises(T.CatalogMissing) as cm:
            T.catalog()
        self.assertEqual(cm.exception.data["reason"], "catalog_damaged")
        self.assertTrue(T.fetch_catalog(None)["downloaded"])
        self.assertEqual(T.catalog()["total"], 17)

    def test_no_network(self):
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        self.opener.side_effect = urllib.error.URLError("no route")
        with self.assertRaises(KaskadError) as cm:
            T.fetch_catalog(None)
        self.assertEqual(cm.exception.data["reason"], "net")
        self.assertIn("Нет связи с GitHub", str(cm.exception))

    def test_refused_key(self):
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36

        def opener(req, timeout):
            raise http_error(req.full_url, 401)
        self.opener.side_effect = opener
        with self.assertRaises(KaskadError) as cm:
            T.fetch_catalog(None)
        self.assertEqual(cm.exception.data["reason"], "bad")

    def test_not_in_the_repository(self):
        """No branch has the catalog: the text names the branches tried and what to do — not the GitHub key (it was
        accepted: 404, not 401/403)."""
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        urls = []

        def opener(req, timeout):
            urls.append(req.full_url)
            raise http_error(req.full_url, 404)
        self.opener.side_effect = opener
        with self.assertRaises(KaskadError) as cm:
            T.fetch_catalog(None)
        e = cm.exception
        self.assertEqual(e.data["reason"], "missing")
        self.assertEqual(e.data["refs"], ["main", "test", "dev"])
        self.assertEqual([u.split("/")[5] for u in urls], ["main", "test", "dev"])
        text = str(e)
        self.assertIn("(ветки main, test, dev)", text)
        self.assertIn("KASKAD_BRANCH=", text)
        self.assertIn("/etc/kaskad/config", text)
        self.assertIn("gokaskad → 11", text)
        self.assertNotIn("ключ", text)
        self.assertFalse(e.data.get("needs_key"))
        self.assertFalse(os.path.exists(T.catalog_path()))

    def test_not_in_the_repository_names_the_branch_of_the_node(self):
        os.environ["GH_TOKEN"] = "ghp_" + "a" * 36
        self.write("/etc/kaskad/config", 'KASKAD_BRANCH="dev3"\n')
        self.opener.side_effect = lambda req, timeout: (_ for _ in ()).throw(http_error(req.full_url, 404))
        with self.assertRaises(KaskadError) as cm:
            T.fetch_catalog(None)
        self.assertIn("(ветки dev3, main, test, dev)", str(cm.exception))

    def test_from_a_checkout_on_disk_without_network(self):
        src = tempfile.mkdtemp(dir=self.root)
        os.makedirs(os.path.join(src, "assets"))
        with open(os.path.join(src, T.CATALOG_ASSET), "wb") as f:
            f.write(fx.gz(fx.catalog_bytes()))
        os.environ["KASKAD_ASSETS_ROOTS"] = src
        res = T.fetch_catalog(None)
        self.assertEqual(res["from"], "repo")
        self.opener.assert_not_called()


class TestRepositoryAsset(Base):
    """assets/templates_catalog.json.gz of kaskad-pro: the clean catalog of goAmnezia, packed deterministically."""

    ASSET = os.path.join(REPO_ROOT, T.CATALOG_ASSET)

    def test_asset_is_the_clean_catalog(self):
        with open(self.ASSET, "rb") as f:
            packed = f.read()
        self.assertEqual(packed[:3], b"\x1f\x8b\x08")
        self.assertEqual(packed[3] & 0x08, 0, "no file name in the gzip header")
        self.assertEqual(packed[4:8], b"\0\0\0\0", "mtime 0")
        raw = gzip.decompress(packed)
        self.assertEqual(len(raw), 14266581)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "ca31e8d0d6a58687a73cef43fc3c13efe0b2d9f9afa1d257dbe85951a03028e1")

    def test_real_catalog_through_the_module(self):
        os.environ["KASKAD_ASSETS_ROOTS"] = REPO_ROOT
        with mock.patch.object(T, "CATALOG_SHA256", "ca31e8d0d6a58687a73cef43fc3c13efe0b2d9f9afa1d257dbe85951a03028e1"):
            self.assertEqual(T.fetch_catalog(None)["from"], "repo")
            cat = T.catalog()
            self.assertEqual(cat["total"], 33467)
            self.assertEqual(len(cat["categories"]), 18)
            pool = [r for r in T.iter_records() if T.in_pool(r)]
            self.assertEqual(len(pool), 20491)
            self.assertFalse(any(r[T._I_CAT] == "portfolio" or not r[T._I_LIC] for r in pool))
            # the 5 ids the copy in the skill lost (docs-research-wave4 §9) are here
            for tid in ("gh_vitaliitymashkov_homepage", "gh_loiko_vitalii_homepage", "gh_vitaliistf_homepage",
                        "gh_vitaliinazaruk_dart", "gh_vitaliinazaruk_s3page"):
                self.assertIsNotNone(T.get(tid), tid)
            self.assertGreater(T.search(query="ресторан")["total"], 100)


# ── catalog() / search() / pick_random() ────────────────────────────────────

class TestCatalogRead(Base):

    def setUp(self):
        super().setUp()
        self.put_catalog()

    def test_missing_catalog(self):
        os.unlink(T.catalog_path())
        with self.assertRaises(T.CatalogMissing) as cm:
            T.catalog()
        self.assertEqual(cm.exception.data["reason"], "catalog_missing")

    def test_categories(self):
        cat = T.catalog()
        self.assertEqual(cat["total"], 17)
        ids = [c["id"] for c in cat["categories"]]
        self.assertEqual(ids, ["business", "landing", "portfolio", "other", "education", "restaurant"])  # no empty
        self.assertEqual(cat["categories"][0], {"id": "business", "title": "Бизнес и корпоративные",
                                                "icon": "briefcase", "count": 3})

    def test_search_case_insensitive_and_cyrillic(self):
        self.assertEqual([i["id"] for i in T.search(query="GOOD")["items"]], ["gh_good"])
        self.assertEqual([i["id"] for i in T.search(query="кафе")["items"]], ["gh_cafe"])
        self.assertEqual([i["id"] for i in T.search(query="  РОМАШКА ")["items"]], ["gh_cafe"])
        # descriptions are Russian: after the names
        self.assertEqual([i["id"] for i in T.search(query="ресторан")["items"]], ["gh_trattoria"])
        self.assertEqual([i["id"] for i in T.search(query="trattoria")["items"]], ["gh_trattoria"])
        self.assertEqual(T.search(query="нет такого")["total"], 0)

    def test_search_item_fields(self):
        it = T.search(category="business", query="good")["items"][0]
        self.assertEqual(it, {"id": "gh_good", "name": "Good Business", "category": "business", "source": "github",
                              "license": "MIT", "preview": "https://example.org/gh_good/",
                              "description": "Корпоративный сайт", "repo": "https://github.com/fx/good",
                              "author": "fx"})
        alpha = T.get("h5up_alpha")
        self.assertEqual((alpha["author"], alpha["license"]), ("html5up", "CC-BY-3.0"))
        self.assertIsNone(T.search(query="No Licence")["items"][0]["license"])

    def test_pages(self):
        res = T.search(category="landing", per_page=4)
        self.assertEqual((res["page"], res["pages"], res["total"], len(res["items"])), (1, 3, 9, 4))
        last = T.search(category="landing", page=3, per_page=4)
        self.assertEqual(len(last["items"]), 1)
        self.assertEqual(T.search(category="landing", page=99, per_page=4)["page"], 3)
        self.assertEqual(T.search(category="landing", page=-5, per_page=4)["page"], 1)
        self.assertEqual(T.search()["per_page"], 25)
        self.assertEqual(T.search(category="legal"), {"items": [], "page": 1, "pages": 1, "per_page": 25,
                                                       "total": 0, "category": "legal", "query": None})

    def test_unknown_category(self):
        with self.assertRaises(KaskadError) as cm:
            T.search(category="nope")
        self.assertEqual(cm.exception.code, errors.USAGE)

    def test_short_key(self):
        k = T.short_key("gh_good")
        self.assertEqual(k, hashlib.sha1(b"gh_good").hexdigest()[:12])
        self.assertEqual(T.by_short_key(k), "gh_good")
        self.assertIsNone(T.by_short_key("000000000000"))

    def test_pool_rules(self):
        pool = {r[T._I_ID] for r in T.iter_records() if T.in_pool(r)}
        for tid in ("gh_port", "gh_u_u_github_io", "gh_x_site_github_io", "gh_nolic"):
            self.assertNotIn(tid, pool)
        self.assertIn("gh_good", pool)
        self.assertIn("h5up_alpha", pool)
        seen = set()
        for seed in range(300):
            T._rng.seed(seed)
            seen.add(T.pick_random())
        self.assertEqual(seen, pool)

    def test_pick_random_exclude(self):
        pool = [r[T._I_ID] for r in T.iter_records() if T.in_pool(r)]
        for seed in range(50):
            T._rng.seed(seed)
            self.assertNotIn(T.pick_random(exclude=pool[:-1]), pool[:-1])
        self.assertEqual(T.pick_random(exclude=pool[:-1]), pool[-1])
        self.assertIsNone(T.pick_random(exclude=pool))


# ── the page rules (templates_lib.sh) ───────────────────────────────────────

class TestPageRules(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="kaskad-tpl-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def put(self, rel, text="x"):
        p = os.path.join(self.d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def test_visible_text_like_sed_tr_wc(self):
        # one-line <script>/<style> are cut; tags become blanks; blanks squeezed; bytes counted
        self.assertEqual(T.visible_text_len(b"<p>ab</p>\n<script>xx</script>cd\n"), len(b" ab cd "))
        # a script over several lines is not cut by sed (per line): its text counts
        self.assertEqual(T.visible_text_len(b"<script>\nvar a;\n</script>"), len(b" var a; "))
        self.assertEqual(T.visible_text_len("<b>ё</b>".encode("utf-8")), len(" ё ".encode("utf-8")))

    def test_app_shell(self):
        self.put("index.html", "<html><body>%PUBLIC_URL%" + fx.TEXT + "</body></html>")
        self.assertEqual(T.page_problem(self.d), "app_shell")
        self.put("index.html", '<div id="root"></div><script src="./src/main.tsx"></script>' + fx.TEXT)
        self.assertEqual(T.page_problem(self.d), "app_shell")
        self.put("index.html", "<html><body><p>short</p></body></html>")
        self.assertEqual(T.page_problem(self.d), "app_shell")
        self.put("index.html", "<html><body><p>short</p><img src=a.png></body></html>")
        self.assertIsNone(T.page_problem(self.d))
        self.put("index.html", "<html><body style=\"BACKGROUND-IMAGE: url(a.jpg)\">short</body></html>")
        self.assertIsNone(T.page_problem(self.d))
        self.put("index.html", "<html><body style=\"background-color: red\">short</body></html>")
        self.assertEqual(T.page_problem(self.d), "app_shell")

    def test_broken_assets(self):
        page = '<link href="/css/a.css?v=2"><script src="js/b.js"></script><link href="//cdn.x/c.css">' + fx.TEXT
        self.put("index.html", page)
        self.assertEqual(T.page_problem(self.d), "broken_assets")
        self.put("css/a.css")
        self.assertIsNone(T.page_problem(self.d))
        shutil.rmtree(os.path.join(self.d, "css"))
        self.put("index.html", '<link href="css/a.css">' + fx.TEXT)        # one missing of one: not «broken»
        self.assertIsNone(T.page_problem(self.d))
        self.assertEqual(T.local_refs(b'<LINK HREF="A.CSS"><a href="x.html"><script src="//h/x.js">'), [b"A.CSS"])

    def test_no_index(self):
        self.assertEqual(T.page_problem(self.d), "no_index")

    def test_pick_index_dir_order(self):
        self.put("docs/index.html")
        self.put("examples/one/index.html")
        self.assertEqual(T.pick_index_dir(self.d), os.path.join(self.d, "docs"))          # second pass
        self.put("theme/sub/index.html")
        self.assertEqual(T.pick_index_dir(self.d), os.path.join(self.d, "theme", "sub"))  # first pass
        self.put("b/index.html")
        self.put("a/index.html")
        self.assertEqual(T.pick_index_dir(self.d), os.path.join(self.d, "a"))             # shallowest, then name
        self.put("public_html/index.html")
        self.assertEqual(T.pick_index_dir(self.d), os.path.join(self.d, "public_html"))   # a known folder
        self.put("dist/index.html")
        self.assertEqual(T.pick_index_dir(self.d), os.path.join(self.d, "dist"))
        self.put("index.html")
        self.assertEqual(T.pick_index_dir(self.d), os.path.join(self.d, "dist"))          # dist before «.»

    def test_pick_index_dir_skips_sources_and_too_deep(self):
        self.put("src/index.html")
        self.put("node_modules/x/index.html")
        self.put("a/b/c/d/e/index.html")                                                   # depth 6
        self.assertIsNone(T.pick_index_dir(self.d))
        self.put("main.html", "<p>main</p>")
        self.assertEqual(T.pick_index_dir(self.d), self.d)
        with open(os.path.join(self.d, "index.html")) as f:
            self.assertEqual(f.read(), "<p>main</p>")

    def test_repo_url_rule(self):
        self.assertTrue(T.repo_url_ok("https://github.com/a/b"))
        for bad in ("http://github.com/a/b", "git://x/y", "https://x/a b", "https://x/$(id)", "https://x/a;b",
                    "https://x/" + "a" * 600, "", None, "ext::sh -c id"):
            self.assertFalse(T.repo_url_ok(bad), bad)

    def test_strip_service_files_exact_names(self):
        for rel in ("README.md", "readme", "LICENSE", "License.txt", "CHANGELOG.md", "package.json", ".gitignore",
                    ".eslintrc.json", "sub/README.md", "sub/notes.md", "sub/deep/keep.md", "license.html",
                    "security.html", "Changelog.html", "readme.html", "index.html", "licensed.css",
                    "node_modules/a.js", ".github/x.yml", "sub/node_modules/keep.js"):
            self.put(rel)
        T.strip_service_files(self.d)
        left = sorted(os.path.relpath(os.path.join(d, f), self.d) for d, _, fs in os.walk(self.d) for f in fs)
        self.assertEqual(left, ["Changelog.html", "index.html", "license.html", "licensed.css", "readme.html",
                                "security.html", "sub/deep/keep.md", "sub/node_modules/keep.js"])

    def test_trackers_cut_credits_kept(self):
        self.put("index.html", fx.GOOD_INDEX)
        self.put("sub/page.htm", '<head><script src="https://static.hotjar.com/c/x.js"></script></head><p>x</p>')
        self.put("sitemap.xml", "<urlset/>")
        self.put("js/app.js", "gtag('x')")
        T.strip_trackers(self.d)
        with open(os.path.join(self.d, "index.html"), encoding="utf-8") as f:
            page = f.read()
        for gone in ("googletagmanager", "gtag(", "mc.yandex.ru", "ym(", "canonical", "og:url"):
            self.assertNotIn(gone, page)
        self.assertIn(fx.FOOTER, page)
        self.assertIn('<script src="js/main.js"></script>', page)
        with open(os.path.join(self.d, "sub/page.htm")) as f:
            self.assertNotIn("hotjar", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.d, "sitemap.xml")))
        with open(os.path.join(self.d, "js/app.js")) as f:
            self.assertEqual(f.read(), "gtag('x')")                                         # only pages


# ── deploy ──────────────────────────────────────────────────────────────────

_REPOS = []


def shared_repos():
    """The fixture repositories, built once per run (the clones only read them)."""
    if not _REPOS:
        d = tempfile.mkdtemp(prefix="kaskad-tpl-repos-")
        with mock.patch.dict(os.environ, {"KASKAD_TEST_GIT_HOME": d}):
            fx.build_repos(d)
        _REPOS.append(d)
    return _REPOS[0]


def tearDownModule():
    for d in _REPOS:
        shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(HAVE_GIT, "нужен git для локальных репозиториев-фикстур")
class DeployBase(Base):

    def setUp(self):
        super().setUp()
        self.put_catalog()
        self.repos = shared_repos()
        os.environ["KASKAD_TEST_GIT_HOME"] = self.repos
        self.clock = [1000.0]
        p = mock.patch.object(T, "_now", lambda: self.clock[0])
        p.start()
        self.addCleanup(p.stop)
        self.world = fx.GitWorld(self.repos, self.clock)
        self.runner = self.world.runner
        self.site = paths.site_root()

    def put_old_site(self, text="OLD SITE"):
        os.makedirs(self.site, exist_ok=True)
        with open(os.path.join(self.site, "index.html"), "w") as f:
            f.write(text)
        with open(os.path.join(self.site, "old.css"), "w") as f:
            f.write("old")

    def files(self, top=None):
        top = top or self.site
        return sorted(os.path.relpath(os.path.join(d, f), top) for d, ds, fs in os.walk(top) for f in fs + [
            x for x in ds if os.path.islink(os.path.join(d, x))])

    def leftovers(self):
        parent = os.path.dirname(self.site)
        return [n for n in os.listdir(parent) if n.startswith(".kaskad-site-work.")]

    def site_read(self, rel):
        with open(os.path.join(self.site, rel), encoding="utf-8") as f:
            return f.read()

    def seq(self, *ids):
        """_rng.choice picks these ids in order (the first one still in the pool)."""
        order = list(ids)

        class Rng(object):
            def choice(self, pool):
                for i in order:
                    if i in pool:
                        return i
                return pool[0]
        p = mock.patch.object(T, "_rng", Rng())
        p.start()
        self.addCleanup(p.stop)


class TestDeploy(DeployBase):

    def test_good_template(self):
        res = T.deploy(self.runner, "gh_good")
        self.assertEqual((res["ok"], res["id"], res["fallback"], res["tries"], res["backup"]),
                         (True, "gh_good", False, 1, None))
        self.assertEqual(res["source"], "https://github.com/fx/good")
        self.assertEqual(self.files(), [".kaskad_template_id", ".kaskad_template_source", "about/changelog.html",
                                        "css/style.css", "docs/guide/deep.md", "index.html", "js/main.js",
                                        "license.html", "security.html"])
        page = self.site_read("index.html")
        self.assertNotIn("googletagmanager", page)
        self.assertNotIn("canonical", page)
        self.assertIn(fx.FOOTER, page)
        self.assertEqual(self.site_read(".kaskad_template_id"), "gh_good\n")
        self.assertEqual(self.site_read(".kaskad_template_source"), "https://github.com/fx/good\n")
        self.assertEqual(os.stat(self.site).st_mode & 0o777, 0o755)
        self.assertEqual(os.stat(os.path.join(self.site, "index.html")).st_mode & 0o777, 0o644)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.world.timeouts, [90])
        self.assertEqual(self.runner.unmatched, [])
        st = T.status()
        self.assertEqual((st["id"], st["source"], st["root"], st["stub"], st["site"]),
                         ("gh_good", "https://github.com/fx/good", self.site, False, True))
        self.assertRegex(st["deployed_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_symlinks_never_reach_the_root(self):
        T.deploy(self.runner, "gh_good")
        for d, ds, fs in os.walk(self.site):
            for n in ds + fs:
                self.assertFalse(os.path.islink(os.path.join(d, n)), n)
        self.assertFalse(os.path.lexists(os.path.join(self.site, "leak.dat")))

    def test_page_in_dist(self):
        res = T.deploy(self.runner, "gh_dist")
        self.assertTrue(res["ok"])
        self.assertEqual(self.files(), [".kaskad_template_id", ".kaskad_template_source", "css/site.css",
                                        "index.html"])

    def test_home_html_becomes_index(self):
        self.assertTrue(T.deploy(self.runner, "gh_home")["ok"])
        self.assertIn("index.html", self.files())

    def test_sparse_monorepo(self):
        res = T.deploy(self.runner, "h5up_alpha")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.files(), [".kaskad_template_id", ".kaskad_template_source", "css/site.css",
                                        "index.html"])
        self.assertIn("--sparse", self.world.clones[0][1])

    def test_template_errors_leave_the_site(self):
        cases = {"gh_noindex": "no_index", "gh_appshell": "app_shell", "gh_emptyshell": "app_shell",
                 "gh_broken": "broken_assets", "gh_fail": "gone"}       # 3.2.21: «Repository not found» — удалён
        self.put_old_site()
        for tid, code in cases.items():
            res = T.deploy(self.runner, tid)
            self.assertFalse(res["ok"], tid)
            self.assertEqual((res["reason"], res["tries"], res["fallback"], res["backup"]), (code, 1, False, None))
            self.assertIn("«%s»" % tid, res["text"])
            self.assertEqual(self.site_read("index.html"), "OLD SITE")
            self.assertEqual(self.files(), ["index.html", "old.css"])
            self.assertEqual(self.leftovers(), [])
            if tid == "gh_fail":
                self.assertIn("not found", res["errors"][0]["detail"])

    def test_too_big(self):
        with mock.patch.object(T, "MAX_BYTES", 100 * 1024):
            res = T.deploy(self.runner, "gh_big")
        self.assertEqual((res["ok"], res["reason"]), (False, "too_big"))
        self.assertTrue(T.deploy(self.runner, "gh_big")["ok"])               # 300 KB < 100 MB

    def test_hanging_clone_hits_the_deadline(self):
        self.world.delays["https://github.com/fx/hang"] = 500
        self.put_old_site()
        res = T.deploy(self.runner, "gh_hang")
        self.assertEqual((res["ok"], res["reason"]), (False, "timeout"))
        self.assertIn("90 секунд", res["text"])
        self.assertEqual(self.world.timeouts, [90])
        self.assertEqual(self.site_read("index.html"), "OLD SITE")

    def test_deadline_is_shared_by_the_git_calls_of_one_template(self):
        url = "https://github.com/fx/html5up"
        self.world.fail_fast.add(url)
        self.world.delays[url] = 80                 # the failed sparse clone ate 80 s of 90
        res = T.deploy(self.runner, "h5up_alpha")
        self.assertEqual((res["ok"], res["reason"]), (False, "timeout"))
        self.assertEqual(self.world.timeouts, [90, 10])

    def test_without_timeout_binary_the_runner_limits(self):
        world = fx.GitWorld(self.repos, self.clock, binaries=("git",))
        calls = []
        orig = world.runner.run

        def run(argv, input=None, check=False, timeout=60):
            calls.append((argv[0], timeout))
            return orig(argv, input, check, timeout)
        world.runner.run = run
        self.assertTrue(T.deploy(world.runner, "gh_good")["ok"])
        self.assertEqual(calls, [("env", 105)])

    def test_unknown_and_malformed_ids(self):
        with self.assertRaises(KaskadError) as cm:
            T.deploy(self.runner, "gh_nosuch")
        self.assertEqual(cm.exception.code, errors.USAGE)
        for bad in ("..", ".", "../x", "a b", "x" * 121, ""):
            with self.assertRaises(KaskadError) as cm:
                T.deploy(self.runner, bad)
            self.assertEqual(cm.exception.code, errors.USAGE, bad)

    def test_backup_is_one_copy(self):
        self.put_old_site()
        r1 = T.deploy(self.runner, "gh_good")
        bdir = paths.site_backup_dir()
        self.assertEqual(r1["backup"], os.path.join(bdir, "previous"))
        self.assertEqual(self.files(r1["backup"]), ["index.html", "old.css"])
        r2 = T.deploy(self.runner, "gh_dist")
        self.assertEqual(os.listdir(bdir), ["previous"])
        with open(os.path.join(bdir, "previous", ".kaskad_template_id")) as f:
            self.assertEqual(f.read(), "gh_good\n")
        self.assertEqual(r2["backup"], r1["backup"])
        self.assertEqual(T.status()["backup"], r1["backup"])
        self.assertEqual(os.stat(bdir).st_mode & 0o777, 0o700)

    def test_empty_root_is_not_backed_up(self):
        os.makedirs(self.site)
        res = T.deploy(self.runner, "gh_good")
        self.assertIsNone(res["backup"])
        self.assertFalse(os.path.exists(os.path.join(paths.site_backup_dir(), "previous")))

    def test_failure_while_building_leaves_the_site(self):
        self.put_old_site()
        with mock.patch.object(T, "_finish_tree", side_effect=OSError(13, "Permission denied")):
            with self.assertRaises(KaskadError) as cm:
                T.deploy(self.runner, "gh_good")
        self.assertIn("Прежний сайт не тронут", str(cm.exception))
        self.assertEqual(self.site_read("index.html"), "OLD SITE")
        self.assertEqual(self.leftovers(), [])

    def test_failed_rename_rolls_back(self):
        self.put_old_site()
        real = os.rename

        def rename(a, b):
            if os.path.basename(a) == "new" and b == self.site:
                raise OSError(18, "Invalid cross-device link")
            return real(a, b)
        with mock.patch.object(T, "_exchange", return_value=False), mock.patch.object(T.os, "rename", rename):
            with self.assertRaises(KaskadError):
                T.deploy(self.runner, "gh_good")
        self.assertEqual(self.site_read("index.html"), "OLD SITE")
        self.assertEqual(self.files(), ["index.html", "old.css"])
        self.assertEqual(self.leftovers(), [])

    def test_swap_by_two_renames(self):
        self.put_old_site()
        with mock.patch.object(T, "_exchange", return_value=False):
            res = T.deploy(self.runner, "gh_good")
        self.assertTrue(res["ok"])
        self.assertEqual(self.files(res["backup"]), ["index.html", "old.css"])

    def test_bad_roots(self):
        for root in ("/", self.path("/var/www"), self.path("/tmp"), "relative/site"):
            with self.assertRaises(KaskadError):
                T.deploy(self.runner, "gh_good", root=root)
        link = self.path("/var/www/link-site")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(self.repos, link)
        with self.assertRaises(KaskadError):
            T.deploy(self.runner, "gh_good", root=link)

    def test_other_root(self):
        root = self.path("/srv/www/other")
        res = T.deploy(self.runner, "gh_good", root=root)
        self.assertEqual(res["root"], root)
        self.assertEqual(T.status(root)["id"], "gh_good")

    def test_low_disk(self):
        with mock.patch.object(T, "MIN_FREE", 1 << 62):
            with self.assertRaises(KaskadError) as cm:
                T.deploy(self.runner, "gh_good")
        self.assertIn("Мало места", str(cm.exception))

    def test_one_change_at_a_time(self):
        with T._RootLock():
            with self.assertRaises(errors.LockedError):
                T.deploy(self.runner, "gh_good")


class TestDeployRandom(DeployBase):

    def test_random_from_the_pool(self):
        self.seq("gh_port", "gh_nolic", "gh_u_u_github_io", "gh_good")      # the first three are not in the pool
        res = T.deploy(self.runner)
        self.assertEqual((res["ok"], res["id"], res["tries"], res["fallback"]), (True, "gh_good", 1, False))

    def test_retries_then_success(self):
        self.seq("gh_noindex", "gh_broken", "gh_cafe")
        res = T.deploy(self.runner)
        self.assertEqual((res["ok"], res["id"], res["tries"]), (True, "gh_cafe", 3))
        self.assertEqual([e["reason"] for e in res["errors"]], ["no_index", "broken_assets"])

    def test_three_failures_then_the_stub(self):
        self.seq("gh_noindex", "gh_appshell", "gh_broken", "gh_good")
        res = T.deploy(self.runner)
        self.assertEqual((res["ok"], res["id"], res["fallback"], res["tries"]), (True, T.STUB_ID, True, 3))
        self.assertEqual(len(self.world.clones), 3)
        page = self.site_read("index.html").lower()
        for word in ("vpn", "kaskad", "anten-ka", "antenka"):
            self.assertNotIn(word, page)
        self.assertIn("<title>welcome</title>", page)
        self.assertEqual(self.site_read(".kaskad_template_id"), T.STUB_ID + "\n")
        self.assertFalse(os.path.exists(os.path.join(self.site, ".kaskad_template_source")))
        self.assertTrue(T.status()["stub"])

    def test_stub_takes_the_domain(self):
        self.write("/etc/kaskad/state.json", json.dumps({"schema": 1, "site": {"domain": "shop.example.com"}}))
        self.seq("gh_noindex", "gh_appshell", "gh_broken")
        T.deploy(self.runner)
        self.assertIn("<h1>shop.example.com</h1>", self.site_read("index.html"))
        self.write("/etc/kaskad/state.json", json.dumps({"site": {"domain": "<script>x</script>"}}))
        shutil.rmtree(self.site)
        T.deploy(self.runner)
        self.assertIn("<h1>Welcome</h1>", self.site_read("index.html"))

    def test_random_never_replaces_a_working_site_by_the_stub(self):
        self.put_old_site()
        self.seq("gh_noindex", "gh_appshell", "gh_broken")
        res = T.deploy(self.runner)
        self.assertEqual((res["ok"], res["fallback"], res["tries"], res["reason"]), (False, False, 3, "broken_assets"))
        self.assertEqual(self.site_read("index.html"), "OLD SITE")

    def test_no_catalog_no_key_gives_the_stub(self):
        os.unlink(T.catalog_path())
        res = T.deploy(self.runner)
        self.assertEqual((res["ok"], res["id"], res["fallback"], res["tries"]), (True, T.STUB_ID, True, 0))
        self.assertTrue(any("ключ" in w for w in res["warnings"]))
        self.assertEqual(self.runner.calls, [])

    def test_no_catalog_for_an_explicit_id_is_an_error(self):
        os.unlink(T.catalog_path())
        with self.assertRaises(KaskadError) as cm:
            T.deploy(self.runner, "gh_good")
        self.assertEqual(cm.exception.data["reason"], "nokey")
        self.assertFalse(os.path.exists(self.site))

    def test_hanging_clones_then_the_stub(self):
        for name in ("noindex", "appshell", "broken"):
            self.world.delays["https://github.com/fx/" + name] = 1000
        self.seq("gh_noindex", "gh_appshell", "gh_broken")
        res = T.deploy(self.runner)
        self.assertEqual([e["reason"] for e in res["errors"]], ["timeout"] * 3)
        self.assertTrue(res["fallback"])


class TestGitInstall(DeployBase):

    def setUp(self):
        super().setUp()
        self.world = fx.GitWorld(self.repos, self.clock, binaries=("timeout",))
        self.runner = self.world.runner
        self.apt = []

        def apt(argv, inp):
            self.apt.append(argv)
            self.runner.binaries.add("git")
            return 0, "", ""
        self.runner.on(["dpkg-query"], (1, "", "dpkg-query: no packages found matching git"))
        self.runner.on(["env", "DEBIAN_FRONTEND=noninteractive", "NEEDRESTART_SUSPEND=1", "apt-get", "install"], apt)

    def test_git_installed_and_recorded_once(self):
        self.write("/etc/kaskad/site/pkgs", "certbot\n", 0o644)
        self.assertTrue(T.deploy(self.runner, "gh_good")["ok"])
        self.assertEqual(len(self.apt), 1)
        self.assertEqual(self.apt[0][-1], "git")
        self.assertEqual(self.read("/etc/kaskad/site/pkgs"), "certbot\ngit\n")
        self.assertTrue(T.deploy(self.runner, "gh_dist")["ok"])
        self.assertEqual(len(self.apt), 1)
        self.assertFalse(T.record_pkg("git"))
        self.assertEqual(self.read("/etc/kaskad/site/pkgs"), "certbot\ngit\n")

    def test_git_already_there_is_not_ours(self):
        self.runner.binaries.add("git")
        self.assertTrue(T.deploy(self.runner, "gh_good")["ok"])
        self.assertEqual(self.apt, [])
        self.assertFalse(os.path.exists(T.pkgs_file()))

    def test_catalog_only_reads_never_install(self):
        T.catalog()
        T.search(query="good")
        T.pick_random()
        T.status()
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(os.path.exists(T.pkgs_file()))


# ── CLI ─────────────────────────────────────────────────────────────────────

def site_parser():
    """kaskadctl with only the group «site» (its subparsers come from site/cli.py of 4.0-B; here — a minimal one)."""
    import argparse
    p = argparse.ArgumentParser(prog="kaskadctl")
    sub = p.add_subparsers(dest="group")
    site = sub.add_parser("site")
    ssub = site.add_subparsers(dest="site_cmd", metavar="КОМАНДА")
    ssub.required = True
    T.register_cli(ssub)
    return p


class TestCli(DeployBase):

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "build_parser", site_parser), mock.patch.object(T, "_runner",
                                                                                  lambda: self.runner), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_templates_json(self):
        rc, out, _ = self.run_cli("site", "templates", "--json")
        doc = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["catalog_total"], 17)
        self.assertEqual(len(doc["categories"]), 6)
        self.assertEqual((doc["page"], doc["pages"], doc["total"]), (1, 1, 17))
        rc, out, _ = self.run_cli("site", "templates", "--category", "landing", "--query", "SHELL", "--json")
        self.assertEqual([i["id"] for i in json.loads(out)["items"]], ["gh_appshell", "gh_emptyshell"])
        rc, out, _ = self.run_cli("site", "templates", "--query", "кафе", "--page", "2", "--json")
        doc = json.loads(out)
        self.assertEqual((doc["page"], doc["total"], doc["items"][0]["id"]), (1, 1, "gh_cafe"))

    def test_templates_text(self):
        rc, out, _ = self.run_cli("site", "templates")
        self.assertIn("Лендинги", out)
        rc, out, _ = self.run_cli("site", "templates", "--category", "business")
        self.assertIn("gh_good", out)
        self.assertIn("превью: https://example.org/gh_good/", out)

    def test_templates_errors(self):
        rc, out, _ = self.run_cli("site", "templates", "--category", "nope", "--json")
        doc = json.loads(out)
        self.assertEqual((rc, doc["ok"], doc["code"]), (errors.USAGE, False, errors.USAGE))
        os.unlink(T.catalog_path())
        rc, out, _ = self.run_cli("site", "templates", "--json")
        doc = json.loads(out)
        self.assertEqual((rc, doc["ok"], doc["data"]["reason"]), (errors.ERROR, False, "nokey"))

    def test_template_set_and_status(self):
        rc, out, _ = self.run_cli("site", "template", "set", "gh_good", "--json")
        doc = json.loads(out)
        self.assertEqual((rc, doc["ok"], doc["id"], doc["fallback"]), (0, True, "gh_good", False))
        rc, out, _ = self.run_cli("site", "template", "set", "gh_broken")
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("Прежний сайт не тронут", out)
        rc, out, _ = self.run_cli("site", "template", "status", "--json")
        doc = json.loads(out)
        self.assertEqual((rc, doc["ok"], doc["id"]), (0, True, "gh_good"))
        rc, out, _ = self.run_cli("site", "template", "status")
        self.assertIn("gh_good", out)

    def test_template_set_random_json(self):
        self.seq("gh_good")
        rc, out, _ = self.run_cli("site", "template", "set", "--random", "--json")
        doc = json.loads(out)
        self.assertEqual((rc, doc["id"]), (0, "gh_good"))
        rc, out, _ = self.run_cli("site", "template", "set", "gh_dist")
        self.assertIn("Спасибо авторам fx", out)
        self.assertIn("Прежний сайт сохранён", out)

    def test_template_set_needs_one_of(self):
        for argv in (("site", "template", "set"), ("site", "template", "set", "gh_good", "--random")):
            with self.assertRaises(SystemExit) as cm:
                self.run_cli(*argv)
            self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
