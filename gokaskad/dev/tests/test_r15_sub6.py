"""3.3.7.15 F13 — IPv6 строкой в подписке (k3/handoff/SPEC-R1-3315-2026-10-30.md §1 решение 6, §3 F13; PLAN-multi-ip-
roles §6.1, §6.2, §9.2).

What is checked (the «Тесты» of F13; the rollback on the trees of 3.3.7.13/14 — tests/test_r15_roles_rollback.py):
* ``give = null`` — the body of the subscription is the one of ``build_sub`` of 3.3.7.14 (fc5ef683, taken by ``git
  show``) byte for byte, VLESS Reality listens on ``0.0.0.0``;
* ``give6 = X`` — after every IPv4 line: ``vless://…@[X]:<port>`` (Reality), ``hysteria2://…@[X]:…``, ``amneziawg://``
  whose .conf says ``Endpoint = [X]:<port>``; names + « · IPv6»; the IPv4 lines the same and at the same places;
* VLESS TLS — no IPv6 line; Hysteria2 on the certificate of a domain — none; Hysteria2 with the mask and AmneziaWG
  «программа» — only when X is the source of the IPv6 route;
* X deprecated / gone / without a way out / seen outside as another / not taken by ``in6`` — no IPv6 line, IPv4 as is;
* ``in6`` a list without X — ``give6`` adds X to it;
* С-5: ``listen "::"`` by the record, never by the cache; ``nginx.has_ipv6()`` false — ``0.0.0.0``; a change of the
  cache without a write — the same config of xray;
* a file (``mark=True``) — never on another address; an IPv4 element: written — refused, read — skipped (and kept by
  a write of IPv6);
* the confirmation of the restart of xray; the refusals of the write; the panel (``ver``, the body, the page code
  ``accept_give``); a copy of another server keeps ``give`` of this machine."""

import ast
import base64
import json
import os
import re
import subprocess
import unittest
import urllib.parse
from unittest import mock

from kaskad import errors, state
from kaskad.errors import KaskadError
from kaskad.gw import addresses as A
from kaskad.gw import mip, model, ops, registry
from kaskad.gw.inbounds import tunnel
from kaskad.gw.inbounds.base import DummyInbound
from kaskad.panel import userpage
from tests.panel.panelcase import PanelCase
from tests.test_gw_hy2 import Hy2Case
from tests.test_r15_addr6 import REPO, REV14, doc6, with_ipv6

X6 = "2001:db8:10::5"           # SLAAC on eth0 — the source of the IPv6 route (ROUTE6 of tests/test_r15_addr6)
S6 = "2001:db8:10::7"           # static on eth0, not the source of the route
D6 = "2001:db8:20::5"           # deprecated
IF_INET6 = "/proc/net/if_inet6"
INET6 = "00000000000000000000000000000001 01 80 10 80       lo\n"


class EpInbound(DummyInbound):
    """AmneziaWG 2.0 as a dummy whose .conf has the form of the real one with ``Endpoint = <gateway.host>:<port>``
    (``tunnel.hostport``: brackets around an IPv6) — what the adapters take of the copy of the state."""

    def line(self, st):
        return "2.0"

    def issue(self, st, sec, device, os=None):
        port = (model.inbound(st, self.kind) or {}).get("port") or 41023
        body = "[Interface]\nPrivateKey = k-%s\n\n[Peer]\nPublicKey = SERVER-%s\nEndpoint = %s\n" % (
            device["id"], self.kind, tunnel.hostport(tunnel.host(st), port))
        rec = {"format": "conf", "title": self.title, "body": body, "filename": "%s.conf" % self.kind, "notes": []}
        return dict(rec, inbound=self.kind, items=[rec])


def conf_of(line):
    tok, _, frag = line[len("amneziawg://"):].partition("#")
    return base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4)).decode("utf-8"), urllib.parse.unquote(frag)


def name_of(link):
    return urllib.parse.unquote(link.rpartition("#")[2])


def old_build_sub():
    """``build_sub`` and ``_conf_lines`` of 3.3.7.14 (``git show``) over this module's adapters and helpers — the
    reference of «give = null — the body as in 3.3.7.14». SkipTest without git or the commit."""
    try:
        src = subprocess.run(["git", "-C", REPO, "show", REV14 + ":lib/kaskad/panel/userpage.py"], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("no git")
    if src.returncode != 0:
        raise unittest.SkipTest("no commit %s" % REV14)
    fns = [n for n in ast.parse(src.stdout).body if isinstance(n, ast.FunctionDef)
           and n.name in ("build_sub", "_conf_lines")]
    ns = dict(userpage.__dict__)
    exec(compile(ast.Module(body=fns, type_ignores=[]), "<3.3.7.14 userpage>", "exec"), ns)
    return ns["build_sub"]


class Sub6Case(Hy2Case):
    """VLESS Reality, Hysteria2 (self-signed, the mask on), WireGuard (the real adapter) and AmneziaWG 2.0 (``EpInbound``,
    the engine «программа» of a test node) for the device «Телефон» of «Мама»; the IPv6 of tests/test_r15_addr6 on
    eth0, the echo of IPv6 sees each address as itself."""

    def dummies(self):
        ad = EpInbound(kind="awg2", title="AmneziaWG 2.0", default_port=41023, ifaces=["kgw-awg2"])
        registry.register(ad)
        return {"awg2": ad}

    def setUp(self):
        super().setUp()
        doc6(self)
        self.echo = {}
        p = mock.patch.object(A, "_echo6", lambda runner, local: self.echo.get(local, local))
        p.start()
        self.addCleanup(p.stop)
        self.install()
        for kind in ("vless", "wg", "awg2"):
            rc, _, err = self.run_json("gw", "inbound", "install", kind)
            self.assertEqual(rc, 0, err)
        with_ipv6(self)
        self.write(IF_INET6, INET6)                     # nginx.has_ipv6(): the kernel has IPv6
        self.dev = self.add_device(access="vless,hy2,wg,awg2")["id"]
        self.ok("gw", "apply")

    def sub(self):
        st = state.load()
        return userpage.build_sub(model.find_user(st, model.get_device(st, self.dev)["user"]), self.w.runner)[1]

    def give6(self, *args):
        return self.run_json("gw", "addresses", "give6", *args)

    def reality(self):
        return [i for i in self.cfg()["inbounds"] if i["tag"] == "gw-vless"][0]

    def edit_cache(self, fn):
        m = A.load_cache()
        m["addresses6"] = [r for r in (fn(dict(r)) for r in m["addresses6"]) if r]
        A.save_cache(m)

    def stale_echo(self):
        """The echo of IPv6 older than FRESH: the next look asks it again."""
        m = A.load_cache()
        m["echo6_at"] = None
        A.save_cache(m)

    def edit_state(self, fn):
        with state.locked():
            st = state.load()
            fn(st)
            state.save(st)


class NullTest(Sub6Case):
    def test_give_null_is_3_3_7_14_byte_for_byte(self):
        old = old_build_sub()
        st = state.load()
        user = model.find_user(st, model.get_device(st, self.dev)["user"])
        seen = []
        real = ops.issue
        with mock.patch.object(ops, "issue", lambda *a, **kw: seen.append(kw.get("host")) or real(*a, **kw)):
            now = userpage.build_sub(user, self.w.runner)
        self.assertEqual(now, old(user, self.w.runner))
        self.assertEqual([l.split("://")[0] for l in now[1]], ["vless", "hysteria2", "amneziawg", "amneziawg"])
        self.assertEqual(set(seen), {None})                                    # no issue on another address
        self.assertEqual(self.reality()["listen"], "0.0.0.0")                  # the config of xray as 3.3.7.14


class LinesTest(Sub6Case):
    def test_the_ipv6_lines_go_after_every_ipv4_one(self):
        ipv4 = self.sub()
        rc, doc, err = self.give6(X6, "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(state.load()["gateway"]["addresses"]["give"], {"items": [{"ip": X6, "n": 1}]})
        links = self.sub()
        self.assertEqual(links[:len(ipv4)], ipv4)                              # the same lines at the same places
        six = links[len(ipv4):]
        self.assertEqual([l.split("://")[0] for l in six], ["vless", "hysteria2", "amneziawg", "amneziawg"])
        port = model.inbound(state.load(), "vless")["port"]
        self.assertIn("@[%s]:%d?" % (X6, port), six[0])
        self.assertIn("security=reality", six[0])
        self.assertIn("@[%s]:443/?" % X6, six[1])
        self.assertIn("obfs=salamander", six[1])                               # X is the source of the IPv6 route
        for line, ref in zip(six, ipv4):
            self.assertEqual(name_of(line) if "#" in line else None, name_of(ref) + " · IPv6")
        for line, kind_port in zip(six[2:], ((41023,), (51820,))):
            conf, name = conf_of(line)
            self.assertIn("\nEndpoint = [%s]:%d\n" % (X6, kind_port[0]), conf)
            self.assertTrue(name.endswith(" · IPv6"), name)
        c4 = conf_of(ipv4[3])[0]                                               # the real WireGuard: only Endpoint
        ep4 = re.search(r"(?m)^Endpoint = (\S+)$", c4).group(1)
        self.assertEqual(conf_of(six[3])[0], c4.replace("Endpoint = " + ep4, "Endpoint = [%s]:51820" % X6))
        self.assertEqual(self.reality()["listen"], "::")
        for l in links:
            self.assertEqual(l.split(), [l])

    def test_vless_tls_and_hysteria2_on_a_domain_give_no_ipv6_line(self):
        self.give6(X6, "--yes")
        hy2 = registry.get("hy2")
        with mock.patch.object(type(registry.get("vless")), "security", lambda self_, st: "tls"):
            seen = []
            real = ops.issue
            with mock.patch.object(ops, "issue", lambda *a, **kw: seen.append((kw.get("inbound"), kw.get("host")))
                                   or real(*a, **kw)):
                userpage._lines6(self.w.runner, state.load(), [model.get_device(state.load(), self.dev)])
            self.assertNotIn(("vless", X6), seen)                              # VLESS TLS: the domain, by AAAA
        with mock.patch.object(type(hy2), "cert_mode", lambda self_, st: "domain"), \
                mock.patch("kaskad.site.model.domain_of", lambda st: "example.com"):
            six = self.sub()[4:]
            self.assertEqual([l.split("://")[0] for l in six], ["vless", "amneziawg", "amneziawg"])
            self.assertFalse([l for l in self.sub()[:4] if "[" in l])

    def test_the_mask_and_the_program_engine_only_on_the_source_of_the_ipv6_route(self):
        self.give6(S6, "--yes")
        six = self.sub()[4:]
        self.assertEqual([l.split("://")[0] for l in six], ["vless", "amneziawg"])     # no Hy2 (mask), no AWG (program)
        self.assertIn("Endpoint = [%s]:51820" % S6, conf_of(six[1])[0])                # WireGuard of the kernel: yes
        self.edit_state(lambda st: st["gateway"]["inbounds"]["hy2"].update(obfs=False))
        six = self.sub()[4:]
        self.assertEqual([l.split("://")[0] for l in six], ["vless", "hysteria2", "amneziawg"])
        self.assertIn("@[%s]:443/?" % S6, six[1])

    def test_no_ipv6_line_while_the_given_ipv6_is_not_usable(self):
        ipv4 = self.sub()
        self.give6(X6, "--yes")
        self.assertEqual(len(self.sub()), 8)
        cases = (("deprecated", lambda r: dict(r, deprecated=True) if r["ip"] == X6 else r),
                 ("gone", lambda r: None if r["ip"] == X6 else r),
                 ("no_route6", lambda r: dict(r, route_ok6=False) if r["ip"] == X6 else r),
                 ("echo_other", lambda r: dict(r, echo6=S6) if r["ip"] == X6 else r))
        good = A.load_cache()
        for why, fn in cases:
            with self.subTest(why):
                A.save_cache(good)
                self.edit_cache(fn)
                self.assertEqual(self.sub(), ipv4)
                self.assertEqual(A.give6_state(state.load()), {"ip": X6, "on": False, "why": why})
                st = mip.status(state.load())
                self.assertIn("сейчас не выдаётся", "\n".join(mip.in_lines(st)))
        A.save_cache(good)
        self.edit_state(lambda st: st["gateway"]["addresses"].update({"in6": {"*": [S6]}}))
        self.assertEqual(self.sub(), ipv4)                                     # not taken by in6: no line
        self.assertEqual(A.give6_state(state.load())["why"], "off")


class WriteTest(Sub6Case):
    def test_in6_without_the_ipv6_takes_it(self):
        rc, doc, err = self.run_json("gw", "addresses", "in6", S6)
        self.assertEqual(rc, 0, err)
        rc, doc, err = self.give6(X6, "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(state.load()["gateway"]["addresses"]["in6"], {"*": [X6, S6]})
        self.assertTrue([w for w in doc["warnings"] if "теперь принимает" in w], doc["warnings"])
        rc, doc, err = self.run_json("gw", "addresses", "in6", S6, "--yes")           # closing it: said
        self.assertEqual(rc, 0, err)
        self.assertTrue([w for w in doc["warnings"] if "строк IPv6 в подписке нет" in w], doc["warnings"])

    def test_the_restart_of_xray_is_confirmed(self):
        rc, doc, _ = self.give6(X6)
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "confirm"))
        self.assertIn("xray перезапустится", doc["error"])
        self.assertIn("give6 %s --yes" % X6, doc["error"])
        self.assertNotIn("addresses", state.load()["gateway"])
        self.assertEqual(self.give6(X6, "--yes")[0], 0)
        self.assertEqual(self.give6(S6)[0], 0)                                # another IPv6: listen stays — no question
        rc, doc, _ = self.give6("off")
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "confirm"))
        self.assertIn("перестанет слушать IPv6", doc["error"])
        self.assertEqual(self.give6("off", "--yes")[0], 0)
        self.assertNotIn("addresses", state.load()["gateway"])
        self.assertEqual(self.reality()["listen"], "0.0.0.0")
        os.unlink(self.path(IF_INET6))                                         # no IPv6 in the kernel: no restart
        rc, doc, err = self.give6(X6)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.reality()["listen"], "0.0.0.0")

    def test_the_refusals(self):
        for arg, code, reason in (("2001:db8:44::1", errors.CONFLICT, "address_not_here"),
                                  (D6, errors.CONFLICT, "deprecated"), ("fe80::1", errors.USAGE, None),
                                  ("85.143.0.20", errors.USAGE, None), ("example.com", errors.USAGE, None)):
            with self.subTest(arg):
                rc, doc, _ = self.give6(arg, "--yes")
                self.assertEqual(rc, code, doc)
                if reason:
                    self.assertEqual(doc["data"]["reason"], reason)
        self.assertIn("в следующем выпуске", self.give6("85.143.0.20")[1]["error"])
        self.echo[X6] = S6
        self.stale_echo()
        rc, doc, _ = self.give6(X6, "--yes")
        self.assertEqual((rc, doc["data"]["reason"], doc["data"]["seen"]), (errors.CONFLICT, "echo_other", S6))
        self.echo[X6] = None                                                   # no answer, never one before
        self.edit_cache(lambda r: dict(r, echo6=None))
        self.stale_echo()
        rc, doc, err = self.give6(X6, "--yes")                                 # no answer of the echo: a warning
        self.assertEqual(rc, 0, err)
        self.assertTrue([w for w in doc["warnings"] if "не проверен" in w], doc["warnings"])
        self.assertTrue([w for w in doc["warnings"] if "SLAAC" in w], doc["warnings"])
        with_ipv6(self, route6=None)                                           # no default IPv6 route
        rc, doc, _ = self.give6(S6, "--yes")
        self.assertEqual((rc, doc["data"]["reason"]), (errors.CONFLICT, "no_route6"))

    def test_a_file_is_never_on_another_address(self):
        for host, mark in ((X6, True), ("example.com", False), ("2001:DB8:10::5", False)):
            with self.subTest(host):
                with self.assertRaises(KaskadError):
                    ops.issue(self.w.runner, self.dev, inbound="wg", host=host, mark=mark)
        got = ops.issue(self.w.runner, self.dev, inbound="wg", host=X6, mark=False)
        self.assertIn("Endpoint = [%s]:51820" % X6, [i for i in got["items"] if i["format"] == "conf"][0]["body"])
        self.assertNotIn(X6, json.dumps(ops.issue(self.w.runner, self.dev, inbound="wg")))
        self.assertNotIn(X6, json.dumps(state.load()))                         # the copy, never the state

    def test_an_ipv4_element_is_skipped_and_kept(self):
        give = {"mode": "all", "items": [{"ip": "85.143.0.10", "n": 1}, {"ip": X6, "n": 2}]}     # a later version
        self.edit_state(lambda st: st["gateway"].setdefault("addresses", {}).update(give=give))
        A.collect(self.w.runner)                                               # the collector keeps the cache
        self.assertEqual(A.give6_of(state.load()), X6)
        self.assertEqual(len(self.sub()), 8)
        self.assertEqual(self.give6(S6, "--yes")[0], 0)
        self.assertEqual(state.load()["gateway"]["addresses"]["give"],
                         {"mode": "all", "items": [{"ip": "85.143.0.10", "n": 1}, {"ip": S6, "n": 2}]})
        self.assertEqual(self.give6("off", "--yes")[0], 0)
        self.assertEqual(state.load()["gateway"]["addresses"]["give"],
                         {"mode": "all", "items": [{"ip": "85.143.0.10", "n": 1}]})
        self.assertIsNone(A.give6_of(state.load()))
        self.assertEqual(len(self.sub()), 4)
        self.assertEqual(self.reality()["listen"], "0.0.0.0")


class ListenTest(Sub6Case):
    def test_listen_by_the_record_never_by_the_cache(self):
        """С-5: written X, the cache without X (or X deprecated) — ``::`` and no IPv6 line; the cache changes without a
        write — the config of xray the same; no IPv6 in the kernel — ``0.0.0.0``."""
        self.give6(X6, "--yes")
        self.assertEqual(self.reality()["listen"], "::")
        text = self.read("/etc/kaskad/gw/xray.json")
        for fn in (lambda r: None if r["ip"] == X6 else r, lambda r: dict(r, deprecated=True)):
            self.edit_cache(fn)
            self.ok("gw", "apply")
            self.assertEqual(self.read("/etc/kaskad/gw/xray.json"), text)
            self.assertEqual(len(self.sub()), 4)                               # no IPv6 line
        os.unlink(self.path(IF_INET6))
        self.ok("gw", "apply")
        self.assertEqual(self.reality()["listen"], "0.0.0.0")

    def test_reset_of_the_roles_asks_as_give6_off(self):
        """F12а «Сбросить выбор» drops ``give`` too: while Reality listens on the IPv6 given (xray restarts) — the same
        question as ``give6 off`` (SPEC F13: ``listen`` changes only by a write of ``give`` with the line in «Что
        изменится»); nothing given — never asked."""
        self.give6(X6, "--yes")
        with self.assertRaises(errors.ConflictError) as cm:
            mip.reset_roles(self.w.runner)
        self.assertEqual(cm.exception.data.get("need"), "confirm")
        self.assertIn("перестанет слушать IPv6", str(cm.exception))
        self.assertEqual((A.give6_of(state.load()), self.reality()["listen"]), (X6, "::"))
        res = mip.reset_roles(self.w.runner, yes=True)
        self.assertTrue([w for w in res["warnings"] if "перестанет слушать IPv6" in w], res["warnings"])
        self.assertEqual((A.give6_of(state.load()), self.reality()["listen"]), (None, "0.0.0.0"))
        self.assertTrue(mip.reset_roles(self.w.runner)["ok"])                  # nothing given: no question


class PageTest(unittest.TestCase):
    def test_accept_give_on_the_page_and_the_line_for_people(self):
        st = {"gateway": {"addresses": {"give": {"items": [{"ip": X6, "n": 1}]}}}}
        doc = {"addresses": [], "addresses6": [{"ip": X6, "iface": "eth0", "route_ok6": True},
                                               {"ip": S6, "iface": "eth0", "route_ok6": True}]}
        with mock.patch.object(A, "is_public6", lambda ip: A._v6(ip) is not None):
            rows = A.page(st, doc, locks={})["v6"]
            self.assertEqual([r["codes"][0] for r in rows], ["accept_give", "reserve"])
            self.assertEqual(A.page(st, {"addresses6": []}, locks={})["v6"],
                             [{"ip": X6, "iface": "", "codes": ["accept_give", "gone"]}])
            st["gateway"]["addresses"]["in6"] = {"*": [S6]}
            self.assertEqual([r["codes"][0] for r in A.page(st, doc, locks={})["v6"]], ["off", "reserve"])
            self.assertIsNone(A.give6_of({"gateway": {"addresses": {"give": {"items": [{"ip": "85.143.0.10"}]}}}}))
            self.assertIsNone(A.give6_of({"gateway": {"addresses": {"give": ["x"]}}}))
            self.assertEqual(mip._give_items({}, X6), {"items": [{"ip": X6, "n": 1}]})
            self.assertEqual(mip._give_items({"give": {"items": [{"ip": X6, "n": 4}]}}, X6), {"items": [{"ip": X6, "n": 4}]})
            self.assertIsNone(mip._give_items({"give": {"items": [{"ip": X6, "n": 1}]}}, None))
        line = mip.in_lines({"in": {"all": True, "list": [], "gone": [], "active": False}, "back": mip.BACK,
                             "give6": {"ip": X6, "on": True, "why": None}})
        self.assertIn("IPv6 — дополнительной строкой подписки: %s." % X6, line)

    def test_a_copy_of_another_server_keeps_give_of_this_machine(self):
        from kaskad import backup
        g_file = {"addresses": {"give": {"items": [{"ip": X6, "n": 1}]}}}
        now = {"gateway": {"addresses": {"give": {"items": [{"ip": S6, "n": 1}]}}}}
        other = json.loads(json.dumps(g_file))
        backup._addresses_of_this_machine(other, now, False)
        self.assertEqual(other["addresses"], now["gateway"]["addresses"])
        same = json.loads(json.dumps(g_file))
        backup._addresses_of_this_machine(same, now, True)
        self.assertEqual(same, g_file)


class PanelGiveTest(PanelCase):
    def setUp(self):
        super().setUp()
        doc6(self)
        self.gateway()
        with_ipv6(self)
        p = mock.patch.object(A, "_echo6", lambda runner, local: local)
        p.start()
        self.addCleanup(p.stop)

    def test_put_give(self):
        ver = self.ok("GET", "/api/gw/addresses")["ver"]
        self.assertEqual(self.err(400, "PUT", "/api/gw/addresses/give", {"ip6": X6})["code"], "ver_required")
        self.assertEqual(self.err(409, "PUT", "/api/gw/addresses/give", {"ip6": X6, "ver": "0" * 12})["code"],
                         "object_changed")
        self.assertEqual(self.err(400, "PUT", "/api/gw/addresses/give", {"ip6": X6, "ver": ver, "x": 1})["code"],
                         "field")
        for body in ({"ver": ver}, {"ip6": 5, "ver": ver}, {"ip6": "", "ver": ver}):
            self.err(400, "PUT", "/api/gw/addresses/give", body)
        self.assertNotIn("addresses", state.load()["gateway"])
        got = self.ok("PUT", "/api/gw/addresses/give", {"ip6": X6, "ver": ver})
        self.assertEqual(got["give6"], X6)
        self.assertEqual(got["ver"], self.ok("GET", "/api/gw/addresses")["ver"])
        page = self.ok("GET", "/api/gw/addresses")
        self.assertEqual([r["codes"][0] for r in page["page"]["v6"] if r["ip"] == X6], ["accept_give"])
        self.assertEqual(page["mip"]["give6"], {"ip": X6, "on": True, "why": None})
        got = self.ok("PUT", "/api/gw/addresses/give", {"ip6": None, "ver": got["ver"]})
        self.assertIsNone(got["give6"])
        self.assertNotIn("addresses", state.load()["gateway"])


class ReviewB2Sub6Test(Sub6Case):
    """REV-3315-b2: П-1 (a link without «#»), П-6 (no question of a restart while the gateway is off)."""

    def test_a_link_without_a_name_stays_whole(self):
        self.give6(X6, "--yes")
        link = "vless://u@[%s]:443?security=reality" % X6
        fake = lambda runner, d, kind, ip: [{"format": "link", "body": link}] if kind == "vless" else []    # noqa: E731
        with mock.patch.object(userpage, "_issue6", fake):
            got = userpage._lines6(self.w.runner, state.load(), [model.get_device(state.load(), self.dev)])
        self.assertEqual(got, [link + "#" + urllib.parse.quote("· IPv6", safe="")])

    def test_no_question_of_a_restart_while_the_gateway_is_off(self):
        self.give6(X6, "--yes")
        with state.locked():
            st = state.load()
            st["gateway"]["enabled"] = False
            state.save(st)
        self.assertTrue(mip.reset_roles(self.w.runner)["ok"])                  # no 409: no xray of the gateway runs
        self.assertIsNone(A.give6_of(state.load()))


if __name__ == "__main__":
    unittest.main()
