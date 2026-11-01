"""REV-3377-stand-fix 1: a GET of the panel that runs commands on the node is marked ``runs`` and refused to a
foreign page as a change is (over the SSH tunnel an admin needs no cookie — any open page could make the node run
them). Every admin GET is asked here on the fake node with every program «installed»: the ones that ran a command
are exactly the marked ones; the marked ones answer 403 to a foreign page, the other reads — as before."""

import unittest

from kaskad.bench import sched_unit
from kaskad.panel import routes
from tests.panel.panelcase import PanelCase

# a path for each admin GET with parameters (a new one — add its path here)
SAMPLES = {
    r"/api/gw/users/(?P<uid>u-[0-9a-f]{8})/page": "/api/gw/users/u-00000001/page",
    r"/api/gw/users/(?P<uid>u-[0-9a-f]{8})/sub": "/api/gw/users/u-00000001/sub",
    r"/api/gw/exits/(?P<id>e-[0-9a-f]{10})(?P<dup>-[0-9]{1,4})?/history": "/api/gw/exits/e-0123456789/history",
    r"/api/gw/exits/(?P<id>e-[0-9a-f]{10})(?P<dup>-[0-9]{1,4})?/transitions": "/api/gw/exits/e-0123456789/transitions",
    r"/api/gw/inbounds/(?P<kind>[a-z0-9]{2,8})/log": "/api/gw/inbounds/vless/log",
    # круг 1 №14: порт окна «Установить …?» — ss и iptables (что держит порт)
    r"/api/gw/inbounds/(?P<kind>[a-z0-9]{2,8})/port": "/api/gw/inbounds/wg/port?port=40000",
    # 3.3.7.15 F3: окно «Настройки протокола» — ss, когда рекомендованный порт протокола не его нынешний
    r"/api/gw/inbounds/(?P<kind>[a-z0-9]{2,8})/settings": "/api/gw/inbounds/wg/settings",
    r"/api/host/day/(?P<day>[^/]{1,32})": "/api/host/day/2026-10-24",
    r"/api/host/bench/(?P<rid>[^/]{1,64})": "/api/host/bench/0123456789",
    r"/api/gw/vless/ask": "/api/gw/vless/ask?do=tls",
    # 3.3.7.9 (SPEC-activity §5.2): reads of metrics.db only — never a command
    r"/api/gw/devices/(?P<id>d-[0-9a-f]{8})/activity": "/api/gw/devices/d-00000001/activity?range=7d",
    r"/api/gw/users/(?P<id>u-[0-9a-f]{8})/activity": "/api/gw/users/u-00000001/activity?range=30d",
}
# marked, but run commands only on a node with the protocol (the fake node here has no VLESS): T-3378 Н-2;
# «Настройки протокола» (F3) — у установленного протокола (здесь его нет)
WITH_PROTOCOL = {r"/api/gw/vless/ask", r"/api/gw/inbounds/(?P<kind>[a-z0-9]{2,8})/settings"}
STREAMS = {"/api/events"}          # a stream (SSE): reads files of the node, no commands
# the GETs of the browser test (SPEC-browser-channel §4.2): without a lease of this session — 403 «lease», with the
# header of a foreign page or without it; they run nothing (the lease comes from POST start, refused to a foreign page)
LEASE_GETS = {r"/api/host/speed/ping", r"/api/host/speed/down"}


class _Every(set):
    """Every program is «installed» (FakeRunner.which): the paths behind ``which`` are asked too."""

    def __contains__(self, name):
        return True


def admin_gets():
    return [r for r in routes.ROUTES if r.method == "GET" and r.auth == "admin" and r.pattern not in STREAMS]


def path_of(r):
    if r.pattern in SAMPLES:
        return SAMPLES[r.pattern]
    return None if any(c in r.pattern for c in "([") else r.pattern.replace("\\.", ".")


class GetRunsTest(PanelCase):
    def setUp(self):
        super().setUp()
        sched_unit.RUNNER[0] = self.w.runner
        self.addCleanup(lambda: sched_unit.RUNNER.__setitem__(0, None))
        self.w.runner.binaries = _Every()

    def tearDown(self):
        self.w.runner.unmatched[:] = []           # every program «installed»: their commands are not mocked here
        super().tearDown()

    def test_a_get_that_runs_commands_is_marked(self):
        no_path = [r.pattern for r in admin_gets() if path_of(r) is None]
        self.assertEqual(no_path, [], "admin GET без пути в SAMPLES — добавьте путь")
        ran, marked = {}, set()
        for r in admin_gets():
            n = len(self.w.runner.calls)
            self.call("GET", path_of(r))
            argv = [a[:2] for a, _ in self.w.runner.calls[n:]]
            if argv:
                ran[r.pattern] = argv
            if r.runs:
                marked.add(r.pattern)
        self.assertEqual(set(ran) - marked, set(), "GET запускает команды без runs=True: %r" % (ran,))
        self.assertEqual(marked - set(ran) - WITH_PROTOCOL, set(), "runs=True, а команд нет — снять пометку")
        self.assertEqual(marked, {r"/api/site", r"/api/bot", r"/api/host/bench/price",
                                  r"/api/gw/inbounds/(?P<kind>[a-z0-9]{2,8})/log", r"/api/gw/vless/ask",
                                  r"/api/gw/inbounds/(?P<kind>[a-z0-9]{2,8})/port",
                                  r"/api/gw/inbounds/(?P<kind>[a-z0-9]{2,8})/settings",
                                  r"/api/gw/addresses",                      # 3.3.7.15 F10: ip, the echo (curl)
                                  r"/api/gw/net"})                           # 3.3.7.15 F6: busy networks of the node

    def test_a_foreign_page_gets_403_from_the_marked_ones_only(self):
        for r in admin_gets():
            n = len(self.w.runner.calls)
            resp = self.call("GET", path_of(r), headers={"Sec-Fetch-Site": "cross-site"})
            if r.runs:
                self.assertEqual(resp.status, 403, (r.pattern, resp))
                self.assertEqual(self.w.runner.calls[n:], [], r.pattern)      # refused before the handler
                self.assertEqual(self.call("GET", path_of(r), headers={"Origin": "https://evil.example"}).status, 403)
            elif r.pattern in LEASE_GETS:
                plain = self.call("GET", path_of(r))
                self.assertEqual((resp.status, resp.json().get("code")), (403, "lease"), r.pattern)
                self.assertEqual((plain.status, plain.json().get("code")), (403, "lease"), r.pattern)
            else:
                self.assertNotEqual(resp.status, 403, (r.pattern, resp))     # a read that runs nothing — as before

    def test_only_an_admin_get_is_marked(self):
        for method, auth in (("POST", "admin"), ("GET", "public"), ("GET", "page")):
            with self.assertRaises(ValueError):
                routes.route(method, r"/api/x-test", auth=auth, runs=True)


if __name__ == "__main__":
    unittest.main()
