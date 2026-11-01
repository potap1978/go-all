"""Fixtures of tests/site/test_templates.py: a small catalog in the format of goAmnezia, local git repositories of
templates built in a temp dir (real ``git init``/``commit``) and a fake runner whose git clones them.

The repositories are written by code, not kept as files in kaskad-pro: their .gitignore, symlinks and README must
not become files of our own repository.
"""

import gzip
import hashlib
import io
import json
import os
import subprocess

from kaskad.sysrun import FakeRunner

TEXT = ("Добро пожаловать в нашу компанию. Мы помогаем бизнесу расти: консультации, проекты под ключ, поддержка "
        "клиентов по всей стране. Наши специалисты работают с 2009 года и знают, как сделать работу удобной. "
        "Свяжитесь с нами, чтобы обсудить вашу задачу. ") * 2

GOOD_INDEX = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Good Business</title>
<link rel="stylesheet" href="css/style.css">
<link rel="canonical" href="https://someone.github.io/good/">
<meta property="og:url" content="https://someone.github.io/good/">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-TEST123"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
</script>
<script>(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){};})(window, document, "script", "https://mc.yandex.ru/metrika/tag.js", "ym");
ym(12345678, "init", {});</script>
<noscript><div><img src="https://mc.yandex.ru/watch/12345678" alt=""></div></noscript>
</head>
<body>
<h1>Good Business</h1>
<p>%s</p>
<a href="license.html">Лицензия</a> · <a href="security.html">Безопасность</a>
<footer>Designed by <a href="https://colorlib.com">Colorlib</a> · Distributed by <a href="https://themewagon.com">ThemeWagon</a></footer>
<script src="js/main.js"></script>
</body>
</html>
""" % TEXT

PLAIN_INDEX = ("<!DOCTYPE html><html><head><title>Page</title><link rel=\"stylesheet\" href=\"css/site.css\"></head>"
               "<body><h1>Page</h1><p>%s</p></body></html>\n" % TEXT)

FOOTER = 'Designed by <a href="https://colorlib.com">Colorlib</a>'

# name -> {path: content}; symlinks: "@path" -> target
REPOS = {
    "good": {
        "index.html": GOOD_INDEX,
        "css/style.css": "body{color:#222}\n",
        "js/main.js": "console.log('ok');\n",
        "license.html": "<html><body><p>Licence of the site</p></body></html>\n",
        "security.html": "<html><body><p>Security policy</p></body></html>\n",
        "about/changelog.html": "<html><body><p>News</p></body></html>\n",
        "README.md": "# Good\n",
        "LICENSE": "MIT\n",
        "LICENSE.txt": "MIT\n",
        "CHANGELOG.md": "1.0\n",
        "package.json": "{}\n",
        "package-lock.json": "{}\n",
        ".gitignore": "node_modules\n",
        ".eslintrc.json": "{}\n",
        "gulpfile.js": "// build\n",
        "sitemap.xml": "<urlset></urlset>\n",
        "docs/notes.md": "notes\n",
        "docs/guide/deep.md": "deep notes stay (depth 3)\n",
        "node_modules/x/index.js": "x\n",
        ".github/workflows/ci.yml": "on: push\n",
        "@leak.dat": "/etc/passwd",
        "@assets-link": "/etc",
    },
    "dist": {
        "README.md": "# build me\n",
        "src/index.html": "<html>source</html>\n",
        "dist/index.html": PLAIN_INDEX,
        "dist/css/site.css": "body{}\n",
    },
    "cafe": {"index.html": PLAIN_INDEX, "css/site.css": "body{}\n"},
    "trattoria": {"index.html": PLAIN_INDEX, "css/site.css": "body{}\n"},
    "nolic": {"index.html": PLAIN_INDEX, "css/site.css": "body{}\n"},
    "port": {"index.html": PLAIN_INDEX, "css/site.css": "body{}\n"},
    "uio": {"index.html": PLAIN_INDEX, "css/site.css": "body{}\n"},
    "noindex": {
        "package.json": "{\"scripts\": {\"build\": \"vite build\"}}\n",
        "src/index.html": "<html><body><div id=\"app\"></div></body></html>\n",
        "src/App.jsx": "export default () => null;\n",
        "README.md": "# app\n",
    },
    "appshell": {
        "index.html": "<!doctype html><html><head><title>App</title></head><body><div id=\"root\"></div>"
                      "<script type=\"module\" src=\"/src/main.jsx\"></script></body></html>\n",
        "src/main.jsx": "x\n",
    },
    "emptyshell": {"index.html": "<html><body><div id=\"root\"></div><p>Loading…</p></body></html>\n"},
    "broken": {
        "index.html": "<html><head><link rel=\"stylesheet\" href=\"css/a.css\"><script src=\"js/b.js\"></script>"
                      "</head><body><p>%s</p></body></html>\n" % TEXT,
    },
    "big": {"index.html": PLAIN_INDEX, "css/site.css": "body{}\n", "media/blob.bin": b"\0" * (300 * 1024)},
    "home": {"home.html": PLAIN_INDEX, "css/site.css": "body{}\n"},
    "html5up": {
        "README.md": "# html5up\n",
        "alpha/index.html": PLAIN_INDEX,
        "alpha/css/site.css": "body{}\n",
        "beta/index.html": PLAIN_INDEX,
        "beta/css/site.css": "body{}\n",
    },
}


def _t(tid, name, repo, license="MIT", source="github", description="Одностраничный лендинг", sparse="",
       preview=None):
    d = {"id": tid, "name": name, "source": source, "description": description, "repo_url": repo,
         "sparse_path": sparse, "preview_url": preview if preview is not None else "https://example.org/%s/" % tid}
    if license:
        d["license"] = license
    return d


GH = "https://github.com/fx/"


def catalog_doc():
    cats = [
        ("business", "Бизнес и корпоративные", "briefcase", [
            _t("gh_good", "Good Business", GH + "good", description="Корпоративный сайт"),
            _t("gh_dist", "Dist Layout", GH + "dist"),
            _t("gh_cafe", "Кафе Ромашка", GH + "cafe", license="CC-BY-4.0"),
        ]),
        ("landing", "Лендинги", "rocket", [
            _t("gh_noindex", "Vite Starter", GH + "noindex"),
            _t("gh_appshell", "React Shell", GH + "appshell"),
            _t("gh_emptyshell", "Empty Shell", GH + "emptyshell"),
            _t("gh_broken", "Broken Assets", GH + "broken"),
            _t("gh_big", "Big Repo", GH + "big"),
            _t("gh_hang", "Hanging Repo", GH + "hang"),
            _t("gh_fail", "Missing Repo", GH + "fail"),
            _t("gh_home", "Home Page", GH + "home"),
            _t("gh_nolic", "No Licence", GH + "nolic", license=None, source="themewagon"),
        ]),
        ("portfolio", "Портфолио и креатив", "palette", [
            _t("gh_port", "Someone CV", GH + "port", description="Портфолио"),
        ]),
        ("other", "Разное", "chart-bar", [
            _t("gh_u_u_github_io", "U Personal", "https://github.com/u/u.github.io"),
            _t("gh_x_site_github_io", "X Site", "https://github.com/x/site.github.io"),
        ]),
        ("education", "Образование", "book", [
            _t("h5up_alpha", "Alpha", GH + "html5up", license="CC-BY-3.0", source="html5up", sparse="alpha",
               description="Сайт академии"),
        ]),
        ("restaurant", "Рестораны и еда", "utensils", [
            _t("gh_trattoria", "Trattoria", GH + "trattoria", description="Сайт ресторана"),
        ]),
        ("legal", "Юридические", "briefcase", []),
    ]
    return {"categories": [{"id": c, "name": n, "icon": i, "templates": ts} for c, n, i, ts in cats]}


def catalog_bytes():
    return json.dumps(catalog_doc(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def catalog_sha():
    return hashlib.sha256(catalog_bytes()).hexdigest()


def gz(data):
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as g:
        g.write(data)
    return buf.getvalue()


# repository url -> the fixture repository (the rest: no such repository)
URLS = {GH + name: name for name in REPOS}
URLS["https://github.com/u/u.github.io"] = "uio"
URLS["https://github.com/x/site.github.io"] = "uio"

_GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C"}


def _env():
    env = dict(os.environ)
    env.update(_GIT_ENV)
    env["HOME"] = env.get("KASKAD_TEST_GIT_HOME") or env.get("TMPDIR") or "/tmp"
    return env


def _git(args, cwd=None):
    return subprocess.run(["git"] + args, cwd=cwd, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def build_repos(base):
    """Every REPOS entry as a git repository under ``base`` (one commit)."""
    for name, files in REPOS.items():
        top = os.path.join(base, name)
        os.makedirs(top)
        for rel, content in files.items():
            if rel.startswith("@"):
                os.symlink(content, os.path.join(top, rel[1:]))
                continue
            p = os.path.join(top, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        for args in (["init", "-q", "-b", "main"], ["config", "uploadpack.allowFilter", "true"],
                     ["config", "uploadpack.allowAnySHA1InWant", "true"], ["add", "-A", "-f"],
                     ["commit", "-q", "-m", "fixture"]):
            r = _git(args, cwd=top)
            if r.returncode != 0:
                raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))


class GitWorld(object):
    """A FakeRunner whose ``git`` clones the fixture repositories (by their https url) with the real git; the
    protocol rules of the module (only https) are swapped for file:// here. ``delays[url]`` — seconds the clone
    «takes» on the fake clock; ``hang`` — urls whose clone the deadline kills (rc 124).

    ``git ls-remote`` (site.tplhealth, 3.2.21) answers by the same table: a fixture repository — its branch, else
    «Repository not found» (rc 128); ``ls[url] = (rc, out, err)`` overrides. ``probes`` — the urls asked."""

    def __init__(self, base, clock, binaries=("git", "timeout")):
        self.base = base
        self.clock = clock                      # [seconds] — the module's _now reads it
        self.delays = {}
        self.fail_fast = set()                  # urls whose first clone (the sparse one) fails at once
        self.timeouts = []                      # the N of «timeout -k 5 N»
        self.clones = []                        # (url, extra args)
        self.ls = {}                            # url -> (rc, out, err) of git ls-remote
        self.probes = []                        # urls of git ls-remote
        self.clone_fail = {}                    # url -> (rc, out, err) of git clone (the network, say)
        self.runner = FakeRunner(binaries=list(binaries))
        self.runner.on(["timeout"], self._timeout)
        self.runner.on(["env", "GIT_TERMINAL_PROMPT=0", "git"], self._env_git)
        self.runner.on(["env", "GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS="], self._ls_remote)

    def _ls_remote(self, argv, inp):
        """Anonymous: no credential helper, no config of the node, only https — then the table."""
        for need in ("GIT_TERMINAL_PROMPT=0", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null",
                     "credential.helper=", "protocol.file.allow=never", "protocol.https.allow=always"):
            assert need in argv, (need, argv)
        i = argv.index("ls-remote")
        assert argv[i + 1:i + 3] == ["--exit-code", "-h"], argv
        url = argv[-1]
        self.probes.append(url)
        if url in self.ls:
            return self.ls[url]
        if url in URLS:
            return 0, "0123456789abcdef0123456789abcdef01234567\trefs/heads/main\n", ""
        return 128, "", "remote: Repository not found.\nfatal: repository '%s/' not found\n" % url

    def _timeout(self, argv, inp):
        if "ls-remote" in argv:
            assert argv[1:4] == ["-k", "2", "8"], argv
            return self._ls_remote(argv[4:], inp)
        assert argv[1:3] == ["-k", "5"], argv
        self.timeouts.append(int(argv[3]))
        return self._env_git(argv[4:], inp, limit=int(argv[3]))

    def _env_git(self, argv, inp, limit=None):
        assert argv[:3] == ["env", "GIT_TERMINAL_PROMPT=0", "git"], argv
        args = argv[3:]
        rules = []
        while args and args[0] == "-c":
            rules.append(args[1])
            args = args[2:]
        assert "protocol.file.allow=never" in rules and "protocol.https.allow=always" in rules, rules
        if args and args[0] == "clone":
            return self._clone(args, limit)
        if args[:1] == ["-C"] and args[2:4] == ["sparse-checkout", "set"]:
            r = _git(["-c", "protocol.file.allow=always", "-C", args[1], "sparse-checkout", "set"] + args[4:])
            return r.returncode, r.stdout, r.stderr
        raise AssertionError("unexpected git call %r" % (argv,))

    def _clone(self, args, limit):
        i = args.index("--")
        url, dest = args[i + 1], args[i + 2]
        extra = args[1:i]
        self.clones.append((url, extra))
        if url in self.delays:
            spent = self.delays[url]
            if limit is not None and spent >= limit:
                self.clock[0] += limit
                return 124, "", ""
            self.clock[0] += spent
        if url in self.clone_fail:
            return self.clone_fail[url]
        if url in self.fail_fast and "--sparse" in extra:
            return 128, "", "fatal: the remote end hung up unexpectedly\n"
        name = URLS.get(url)
        if name is None:
            return 128, "", "Cloning into '%s'...\nremote: Repository not found.\nfatal: repository '%s' not found\n" \
                % (dest, url)
        r = _git(["-c", "protocol.file.allow=always", "clone"] + extra
                 + ["--", "file://" + os.path.join(self.base, name), dest])
        return r.returncode, r.stdout, r.stderr
