"""Common ground of the tests of the domain (kaskad.site, docs/k3/SPEC-wave4.md §4.1): the fake node of GwCase, a
fake certbot (no network, no Let's Encrypt), answers of DNS without a resolver, a fixed clock.

Certificates: tests/fixtures/site/*.pem|key (make.sh): «le» — vpn.example.com 2026-10-01 … 2026-12-30 by «R11»,
«le2» — the renewed one (… 2027-03-01), «staging» — the same name by «(STAGING) Ersatz Edamame E1», «other» —
other.example.org, «idn» — впн.пример.рф. The clock is 2026-10-07 12:00 UTC: «le» has 83 days left.
"""

import calendar
import os
import shutil
import time
from unittest import mock

from kaskad import paths, state
from kaskad.site import acme, certinfo, consumers, dns, model, ops
from tests.gwcase import GwCase

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "site")
NOW = calendar.timegm(time.strptime("2026-10-07T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
DAY = 86400
DOMAIN = "vpn.example.com"
NODE = "203.0.113.10"

ERRORS = {
    "limit": ("An unexpected error occurred:\nError creating new order :: too many certificates (5) already issued for "
              "this exact set of identifiers in the last 168h0m0s, retry after 2026-10-08 12:00:00 UTC: see "
              "https://letsencrypt.org/docs/rate-limits/#new-certificates-per-exact-set-of-hostnames\n"),
    "net": ("An unexpected error occurred:\nrequests.exceptions.ConnectionError: HTTPSConnectionPool("
            "host='acme-v02.api.letsencrypt.org', port=443): Max retries exceeded with url: /directory\n"),
    "dns": ("Certbot failed to authenticate some domains (authenticator: standalone).\n  Domain: vpn.example.com\n"
            "  Type:   connection\n  Detail: 198.51.100.99: Fetching http://vpn.example.com/.well-known/acme-challenge/x:"
            " Timeout during connect (likely firewall problem)\n"),
}


def fixture(name):
    with open(os.path.join(FIX, name), encoding="ascii") as f:
        return f.read()


def put_copy_pair(name):
    """A test-only pair at the same stable paths as a real ``cert-installed`` switch."""
    tls = model.tls_dir()
    pair = os.path.join(tls, ".pair-test")
    os.makedirs(tls, exist_ok=True)
    shutil.rmtree(pair, ignore_errors=True)
    os.makedirs(pair, 0o755)
    shutil.copy(os.path.join(FIX, name + ".pem"), os.path.join(pair, "fullchain.pem"))
    shutil.copy(os.path.join(FIX, name + ".key"), os.path.join(pair, "privkey.pem"))
    os.chmod(os.path.join(pair, "fullchain.pem"), 0o644)
    os.chmod(os.path.join(pair, "privkey.pem"), 0o600)
    try:
        os.unlink(model.current_path())
    except OSError:
        pass
    os.symlink(os.path.basename(pair), model.current_path())


class FakeCertbot(object):
    """``certbot certonly|renew|update_account``. ``mode``: "ok" or a key of ERRORS (the run fails with that text —
    ``in_log`` puts it into letsencrypt.log instead of stderr); ``pem`` — the fixture certonly writes; ``renew_pem`` —
    what renew writes (None: not due, nothing changes); ``hook`` — run our deploy hook in-process after a new
    certificate (as certbot does), with RENEWED_LINEAGE."""

    def __init__(self, case):
        self.case = case
        self.mode = "ok"
        self.in_log = False
        self.pem = "le"
        self.renew_pem = None
        self.hook = True
        self.foreign_hook = None
        self.calls = []
        self.hooked = []

    def live(self, d):
        return model.live_dir(d)

    def write(self, d, name, hook, webroot=None):
        live = self.live(d)
        os.makedirs(live, exist_ok=True)
        with open(os.path.join(live, "fullchain.pem"), "w") as f:
            f.write(fixture(name + ".pem"))
        with open(os.path.join(live, "privkey.pem"), "w") as f:
            f.write(fixture(name + ".key"))
        conf = model.renewal_conf(d)
        if not os.path.exists(conf):
            os.makedirs(os.path.dirname(conf), exist_ok=True)
            lines = ["# renew_before_expiry = 30 days", "version = 2.9.0", "archive_dir = /etc/letsencrypt/archive/%s" % d,
                     "cert = /etc/letsencrypt/live/%s/cert.pem" % d, "", "# Options used in the renewal process",
                     "[renewalparams]", "account = 0123456789abcdef",
                     "authenticator = %s" % ("webroot" if webroot else "standalone"),
                     "server = https://acme-v02.api.letsencrypt.org/directory"]
            if self.foreign_hook:
                lines.append("renew_hook = %s" % self.foreign_hook)
            elif hook:
                lines.append("renew_hook = %s" % hook)
            if webroot:                                    # as certbot saves ``--webroot -w``
                lines += ["[[webroot_map]]", "%s = %s" % (d, webroot)]
            with open(conf, "w") as f:
                f.write("\n".join(lines) + "\n")
        if self.hook:
            self.hooked.append(live)
            consumers.cert_installed(self.case.w.runner, lineage=live)

    def fail(self):
        text = ERRORS[self.mode]
        if self.in_log:
            p = model.le_log()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a") as f:
                f.write("2026-10-07 12:00:00,000:DEBUG:certbot._internal.main:Exiting abnormally:\n" + text)
            return 1, "", "Ask for help or search for solutions at https://community.letsencrypt.org.\n"
        return 1, "", text

    def __call__(self, argv, input):
        self.calls.append(list(argv))
        verb = argv[1]
        if verb == "certonly":
            if self.mode != "ok":
                return self.fail()
            d = argv[argv.index("--cert-name") + 1]
            hook = argv[argv.index("--deploy-hook") + 1] if "--deploy-hook" in argv else None
            cur = certinfo.read(os.path.join(self.live(d), "fullchain.pem"))
            if cur is not None and "--force-renewal" not in argv and certinfo.days_left(cur) >= 30 \
                    and cur["staging"] == ("--staging" in argv):
                return 0, "Certificate not yet due for renewal; no action taken.\n", ""
            webroot = argv[argv.index("-w") + 1] if "--webroot" in argv else None
            self.write(d, self.pem, hook, webroot)
            return 0, "Successfully received certificate.\n", ""
        if verb == "renew":
            if self.mode != "ok":
                return self.fail()
            d = argv[argv.index("--cert-name") + 1]
            if self.renew_pem:
                self.write(d, self.renew_pem, None)
            return 0, "", ""
        if verb == "update_account":
            return 0, "Your e-mail address was updated.\n", ""
        return 1, "", "fake certbot: %r\n" % (argv,)


class _Bins(set):
    """Binaries of the fake runner: certbot appears once the fake apt installed it."""

    def __init__(self, items, pkgs):
        set.__init__(self, items)
        self.pkgs = pkgs

    def __contains__(self, name):
        return set.__contains__(self, name) or (name == "certbot" and "certbot" in self.pkgs.installed)


class SiteFakes(object):
    """The fakes of the domain on any GwCase (SiteCase, the panel's PanelCase): ``site_fakes()`` in setUp."""

    def site_fakes(self):
        self.w.runner.binaries = _Bins(self.w.runner.binaries, self.w.pkgs)
        self.certbot = FakeCertbot(self)
        self.w.runner.on(["certbot"], self.certbot)
        self.answers = {dns.A: ("ok", [NODE]), dns.AAAA: ("ok", [])}
        self.answers_for = {}                           # name -> its own answers (a change of the domain: two names)
        self.dns_asked = []

        def query(name, qtype, servers=None, timeout=None, tries=None):
            self.dns_asked.append((name, qtype))
            return self.answers_for.get(name, self.answers)[qtype]
        self.notified = []
        self.clock = NOW
        self.mono = 1000.0
        self.slept = []
        self._site_patches = [mock.patch.object(certinfo, "_now", lambda: self.clock),
                              mock.patch.object(acme, "_now", lambda: self.clock),
                              mock.patch.object(dns, "query", query),
                              mock.patch.object(ops, "_sleep", self._sleep),
                              mock.patch.object(ops, "_mono", lambda: self.mono),
                              mock.patch.object(ops, "_notify", lambda text: self.notified.append(text) or True)]
        for p in self._site_patches:
            p.start()
        self.addCleanup(self._site_stop)

    def _site_stop(self):
        for p in reversed(self._site_patches):
            p.stop()

    def _sleep(self, s):
        self.slept.append(s)
        self.mono += s

    def certbot_present(self):
        self.w.pkgs.installed.add("certbot")

    def site(self):
        return model.peek(state.load())

    def set_site(self, **kw):
        st = state.load()
        s = model.site(st)
        s.update(kw)
        state.save(st)

    def put_live(self, name, domain=DOMAIN, hook=True):
        """A certificate of certbot in /etc/letsencrypt/live/<domain> (and its renewal file) without a run."""
        was, self.certbot.hook = self.certbot.hook, False
        try:
            self.certbot.write(domain, name, acme.hook_command() if hook else None)
        finally:
            self.certbot.hook = was

    def put_copy(self, name):
        """The copy for the consumers as cert-installed leaves it."""
        put_copy_pair(name)

    def with_domain(self, issued=None):
        """The domain set (DNS ok); ``issued`` — a fixture issued and handed out."""
        ops.set_domain(self.w.runner, DOMAIN)
        if issued:
            self.certbot.pem = issued                   # certbot comes from the fake apt
            ops.issue(self.w.runner)

    def log_text(self):
        try:
            with open(paths.log(), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""


class SiteCase(SiteFakes, GwCase):
    """GwCase + a fake certbot + DNS answers (``self.answers[qtype] = (status, [addresses])``) + the clock."""

    def setUp(self):
        super().setUp()
        self.site_fakes()
        state.save(state.empty())
