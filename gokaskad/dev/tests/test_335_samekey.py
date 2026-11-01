"""3.3.5 №4: one notion of «the same key» — ``exits.peer_key`` (= ``exits.identity``). An AmneziaWG key is the server key
and the client key (its public key, from the private one), no address: the same client by IP and by domain is one key
(«этот ключ уже есть как e-…, по другому адресу»), two people of one server — two exits. A pool of 3.3.4 with both legs
of one client: told in ``gw exits``, the panel and «Проверка сервера», never removed by itself; the ids stay.
Stand T-335 §1б, §4 (BACKLOG-3.3.5 №4)."""

import contextlib
import io
import json

from kaskad import cli, util
from kaskad.errors import ConflictError
from kaskad.gw import exits, exitsubs, ops
from kaskad.i18n import use_lang
from kaskad.vendor import awgkeys
from tests.fakes_gw import _key as fake_key
from tests.fakes_gw import write_pool
from tests.test_gw_exits import conf_of, vpn_of
from tests.test_gw_exitsubs import GA_URL, IP_URL, SubsCase

BY_IP = "198.51.100.31:51820"
BY_DOMAIN = "vpn.example.com:51820"


def other_client(conf, n=9):
    """The same server (its public key and endpoint), another person: another private key."""
    return "\n".join("PrivateKey = %s" % fake_key(n) if ln.startswith("PrivateKey") else ln
                     for ln in conf.splitlines()) + "\n"


class SameKeyCase(SubsCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def line(self, conf, name="Аня"):
        return exits.load_parser().extract(vpn_of(conf, name))[0]


class IdentityTest(SameKeyCase):
    def test_the_same_client_by_ip_and_by_domain_is_one_key(self):
        a, b = self.line(conf_of("2.0", 3, endpoint=BY_IP)), self.line(conf_of("2.0", 3, endpoint=BY_DOMAIN))
        self.assertNotEqual(exits.exit_id(a), exits.exit_id(b))
        self.assertEqual(exits.peer_key(a), exits.peer_key(b))
        self.assertEqual(exits.identity(a), exits.peer_key(a))                  # one notion, no second key
        self.assertEqual(exits.identity_hash(a), exits.identity_hash(b))
        self.assertEqual(exits.same_how(a, b), "ip_domain")                     # REV-335-2: «по IP и по домену»
        pub = awgkeys.pubkey(fake_key(3))
        self.assertEqual(exits.peer_key(a), "awg|%s|%s" % (fake_key(103), pub))  # no private key, no address

    def test_two_people_of_one_server_are_two_keys(self):
        conf = conf_of("2.0", 3)
        a, b = self.line(conf), self.line(other_client(conf))
        self.assertNotEqual(exits.peer_key(a), exits.peer_key(b))

    def test_the_parser_of_the_menu_says_the_same(self):
        """install.sh adds by «egress_parse.py peers» — the same key as the core, AmneziaWG too."""
        parser = exits.load_parser()
        self.assertGreaterEqual(parser.PARSER_VERSION, 19)
        lines = [self.line(conf_of("2.0", 3, endpoint=BY_IP)), self.line(conf_of("2.0", 3, endpoint=BY_DOMAIN)),
                 self.line(other_client(conf_of("2.0", 3))), self.line(conf_of("3.1", 4))]
        for l in lines:
            self.assertEqual(parser.peer_key(l), exits.peer_key(l))
        self.assertEqual(parser.client_key(fake_key(5)), awgkeys.pubkey(fake_key(5)))
        for bad in ("not a key", "QUJD"):                                      # not base64 / not 32 bytes
            self.assertTrue(parser.client_key(bad).startswith("k:"))
            self.assertEqual(parser.client_key(bad), exits.client_key(bad))
        self.assertEqual(exits.client_key(""), "")


class AddByHandTest(SameKeyCase):
    def test_the_same_key_by_domain_is_already_there(self):
        ip = self.line(conf_of("2.0", 3, endpoint=BY_IP))
        write_pool([ip])
        eid = exits.exit_id(ip)
        text = vpn_of(conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня")
        with use_lang("ru"):
            res = ops.exit_preview(self.w.runner, text)
        row = res["items"][0]
        self.assertEqual((row["in_pool"], row["same_peer"], row["same_how"]), (True, eid, "ip_domain"))
        self.assertEqual(row["same_text"], "этот ключ уже есть как %s («Аня»), по другому адресу" % eid)
        self.w.script.add_result = (4, "Новых ключей не добавлено (дубликаты или пусто).\n", "")
        with use_lang("ru"), self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, text)
        self.assertTrue(str(c.exception).startswith(
            "Второй раз не добавляю: этот ключ уже есть как %s («Аня»), по другому адресу." % eid), str(c.exception))
        with use_lang("en"), self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, text)
        self.assertIn("this key is already there as %s (“Аня”), at another address" % eid, str(c.exception))

    def test_the_result_of_each_key_says_why(self):
        """Several keys at once: the one the pool has by another address is «in_pool» with its reason (panel, wizard)."""
        from tests.fakes_gw import VLESS_B
        ip = self.line(conf_of("2.0", 3, endpoint=BY_IP))
        write_pool([ip])
        self.w.script.add_result = (4, "Новых ключей не добавлено (дубликаты или пусто).\n", "")
        with use_lang("ru"), self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, vpn_of(conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня") + "\n" + VLESS_B)
        res = dict((r["kind"], r) for r in c.exception.data["results"])
        self.assertEqual(res["awg"]["result"], "in_pool")
        self.assertEqual(res["awg"]["why"], "этот ключ уже есть как %s («Аня»), по другому адресу" % exits.exit_id(ip))

    def test_another_person_of_the_same_server_is_a_new_exit(self):
        conf = conf_of("2.0", 3)
        write_pool([self.line(conf)])
        res = ops.exit_preview(self.w.runner, vpn_of(other_client(conf), "Борис"))
        self.assertEqual([(x["in_pool"], x.get("same_peer")) for x in res["items"]], [(False, None)])


class SubscriptionTest(SameKeyCase):
    def test_by_ip_and_by_domain_with_the_same_keys(self):
        """Two subscriptions of one goAmnezia server — by IP and by domain, the same keys: the second adds nothing and
        says why."""
        a, b = conf_of("2.0", 3), conf_of("3.1", 4)
        res = self.add(IP_URL, "\n".join([vpn_of(a, "Аня"), vpn_of(b, "Борис")]))
        self.assertEqual(res["result"]["added"], 2)
        ids = [i for i, _ in exits.line_ids()]
        dom = [conf_of("2.0", 3, endpoint=BY_DOMAIN), conf_of("3.1", 4, endpoint=BY_DOMAIN)]
        with use_lang("ru"):
            r = self.add(GA_URL, "\n".join([vpn_of(dom[0], "Аня"), vpn_of(dom[1], "Борис")]))["result"]
        self.assertEqual((r["added"], r["dups"]), (0, 2))
        self.assertIn("Пропущено дублей: 2 — эти ключи уже есть в пуле: %s («Аня», по другому адресу), %s («Борис», по "
                      "другому адресу)." % tuple(ids), r["text"])                   # REV-335-2 Н4: with the names
        self.assertEqual([i for i, _ in exits.line_ids()], ids)                  # the pool as it was
        with use_lang("ru"):
            rows = exitsubs.listing()
        self.assertEqual([x["keys"] for x in rows], [2, 2])                     # its keys are in the pool, by IP
        self.assertIn("; пропущено дублей: 2 (ключ уже в пуле)", rows[1]["text"])

    def test_two_people_of_one_server_in_one_subscription(self):
        conf = conf_of("2.0", 3)
        res = self.add(GA_URL, "\n".join([vpn_of(conf, "Аня"), vpn_of(other_client(conf), "Борис")]))
        self.assertEqual((res["result"]["added"], res["result"]["dups"]), (2, 0))
        self.assertEqual(len(exits.pool_lines()), 2)

    def test_one_key_by_ip_and_by_domain_in_one_answer_is_one_exit(self):
        res = self.add(GA_URL, "\n".join([vpn_of(conf_of("2.0", 3, endpoint=BY_IP), "Аня"),
                                          vpn_of(conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня")]))
        self.assertEqual(res["result"]["added"], 1)
        self.assertEqual(len(exits.pool_lines()), 1)


class UpgradeTest(SameKeyCase):
    """A pool of 3.3.4 with both legs of one client (by IP and by domain — 3.3.4 took them for two servers)."""

    def setUp(self):
        super().setUp()
        self.ip = self.line(conf_of("2.0", 3, endpoint=BY_IP), "Аня IP")
        self.dom = self.line(conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня домен")
        self.lines = [self.ip, self.dom]
        write_pool(self.lines)
        self.old = [exits.exit_id(l) for l in self.lines]
        doc = {"schema": 1, "next": 2, "subs": [{"id": "s-abcdef", "n": 1, "url": GA_URL, "auto": True, "pin": "",
                                                 "keys": self.old, "seen": self.old, "present": self.old,
                                                 "excluded": [], "added": "2026-10-14T00:00:00Z", "last_try": 1,
                                                 "last_ok": 1, "error": "", "count": 2, "full": 0}]}
        util.atomic_write(exitsubs.path(), json.dumps(doc), 0o600)

    def test_ids_stay_and_the_repeat_is_told(self):
        pool = exits.pool(self.w.runner)
        self.assertEqual([e["id"] for e in pool], self.old)
        self.assertEqual([(e.get("same_peer"), e.get("same_how")) for e in pool],
                         [(None, None), (self.old[0], "ip_domain")])
        with use_lang("ru"):
            rc, out, _ = self.run_cli("gw", "exits")
        self.assertEqual(rc, 0)
        self.assertIn("Ключ «Аня IP» добавлен дважды (и как «Аня домен»): по IP и по домену. Работать будет только "
                      "один — удалите лишний, маршруты перейдут на оставшийся.", out)       # REV-335-2: no «ноги»
        rows = ops.exits_view(self.w.runner)
        self.assertEqual([r.get("same_peer") for r in rows], [None, self.old[0]])   # the panel: ex.samePeer
        self.assertNotIn("PrivateKey", json.dumps(rows))

    def test_a_refresh_removes_neither(self):
        self.feed.bodies[GA_URL] = "\n".join(vpn_of(c, n) for c, n in (
            (conf_of("2.0", 3, endpoint=BY_IP), "Аня IP"), (conf_of("2.0", 3, endpoint=BY_DOMAIN), "Аня домен")))
        r = self.refresh()["results"][0]
        self.assertEqual((r["added"], r["removed"]), (0, 0), r["text"])
        self.assertEqual([i for i, _ in exits.line_ids()], self.old)
