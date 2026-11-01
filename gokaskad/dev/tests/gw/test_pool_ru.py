"""T15 Q4: the file of the Russian keys ``upstream-ru.txt`` in the pool of the gateway (SPEC-groups §2.2, §7, §13.1
«exits.pool (Q4)»): numbers 201+, marks and ports by the same formulas, the tag «gr-», the ids through ``pool_id``,
no file — nothing, the 33rd line refused in words; ``pool_fallback``, ``line_ids``, ``renumbered``/``line_marks`` by
file; the same key in both files told, never a twin of the other role."""

import os
import unittest

from kaskad import paths
from kaskad.gw import exits, rev
from kaskad.i18n import tr
from tests.fakes_gw import VLESS_A, VLESS_B, awg_line, install_parser, write_pool
from tests.helpers import RootCase

RU_V = ("vless://aaaaaaaa-2222-3333-4444-555555555555@203.0.113.7:443?type=tcp&security=reality"
        "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#Home")


def write_ru(lines):
    p = paths.egress_up_ru()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(l + "\n" for l in lines))
    os.chmod(p, 0o600)


class PoolRuCase(RootCase):
    def setUp(self):
        super().setUp()
        install_parser()
        self.awg_ru = awg_line(7, name="Router", endpoint="203.0.113.50:51820", address="10.8.7.2/32")
        write_pool([VLESS_A, awg_line(2)])
        write_ru([self.awg_ru, RU_V])

    def rows(self, roles=exits.ROLES):
        return dict((e["id"], e) for e in exits.pool(None, roles=roles))


class PoolTest(PoolRuCase):
    def test_paths(self):
        self.assertEqual(paths.egress_up_ru(), os.path.join(os.path.dirname(paths.egress_up()), "upstream-ru.txt"))
        self.assertEqual(rev.ru_pool_path(), paths.egress_up_ru())
        self.assertEqual(exits.pool_path("ru"), paths.egress_up_ru())
        self.assertEqual(exits.pool_lines("ru"), [self.awg_ru, RU_V])

    def test_numbers_marks_tags(self):
        rows = self.rows()
        a = rows[exits.exit_id(self.awg_ru)]
        self.assertEqual((a["role"], a["index"], a["kind"], a["iface"], a["mark"], a["table"]),
                         ("ru", 201, "awg", "kawg201", 457, 457))
        self.assertEqual(a["tag"], "gr-" + a["id"])
        self.assertEqual(a["outbound"]["tag"], a["tag"])                 # the parser's outbound under the tag of the role
        v = rows[exits.exit_id(RU_V)]
        self.assertEqual((v["role"], v["index"], v["kind"], v["mark"]), ("ru", 202, exits.XRAY, 0x300 + 202))
        self.assertEqual(v["tag"], "gr-" + v["id"])
        self.assertEqual(exits.tproxy_port(v), 12601)     # SEC-s1-q2 С-2: by its id (the first one given), not 12400+202
        self.assertTrue(all(0x200 > r["mark"] for r in rows.values() if r["kind"] == "awg"))
        self.assertTrue(all(0x400 > r["mark"] for r in rows.values() if r["kind"] == exits.XRAY))
        foreign = [r for r in rows.values() if r["role"] == "abroad"]
        self.assertEqual(sorted(r["index"] for r in foreign), [1, 2])
        self.assertTrue(all(r["tag"].startswith("gx-") for r in foreign))

    def test_default_is_foreign_only(self):
        self.assertEqual(set(r["role"] for r in exits.pool(None)), {"abroad"})
        self.assertEqual(set(r["role"] for r in exits.pool(None, roles=("ru",))), {"ru"})
        self.assertEqual(len(exits.pool(None, roles=exits.ROLES)), 4)

    def test_no_file_no_rows(self):
        os.unlink(paths.egress_up_ru())
        self.assertEqual(exits.pool(None, roles=("ru",)), [])
        self.assertEqual(len(exits.pool(None, roles=exits.ROLES)), 2)
        self.assertEqual(exits.pool_fallback(roles=("ru",)), [])

    def test_only_russian_keys(self):
        """A node with no foreign key: the Russian ones are still exits of the gateway."""
        write_pool([])
        self.assertEqual(exits.pool(None), [])
        self.assertEqual(sorted(e["index"] for e in exits.pool(None, roles=exits.ROLES)), [201, 202])

    def test_33rd_line_refused_in_words(self):
        lines = [awg_line(10 + i, name="R%d" % i, endpoint="203.0.113.%d:51820" % (60 + i),
                          address="10.8.%d.2/32" % (10 + i)) for i in range(33)]
        write_ru(lines)
        rows = exits.pool(None, roles=("ru",))
        self.assertEqual(len(rows), 33)
        self.assertTrue(all(r["kind"] == "awg" for r in rows[:32]))
        last = rows[32]
        self.assertEqual((last["index"], last["kind"], last["mark"]), (233, "bad", None))
        self.assertEqual(last["problem_code"], "problem.ru_too_far")
        for lang in ("ru", "en"):
            text = tr("gw_exits.problem.ru_too_far", lang, max=32)
            self.assertIn("32", text)
        self.assertNotRegex(tr("gw_exits.problem.ru_too_far", "en", max=32), "[А-Яа-яЁё]")

    def test_foreign_line_past_200_never_takes_a_russian_mark(self):
        many = [awg_line(40 + i, name="A%d" % i, endpoint="198.51.100.%d:51820" % (i % 200 + 1),
                         address="10.9.%d.%d/32" % (i // 200, i % 200 + 2)) for i in range(202)]
        write_pool(many)
        write_ru([])
        rows = exits.pool(None)
        self.assertEqual(rows[199]["kind"], "awg")
        self.assertEqual((rows[200]["kind"], rows[200]["problem_code"]), ("bad", "problem.too_far"))
        fb = exits.pool_fallback()
        self.assertEqual(fb[200]["mark"], None)

    def test_ids_through_pool_id(self):
        """A Russian line renamed by a subscription keeps the id of its exit (the map ``ids`` of subs.json)."""
        kept = "e-00000000aa"
        with open(os.path.join(paths.egress_dir(), "subs.json"), "w") as f:
            f.write('{"schema": 1, "next": 1, "subs": [], "ids": {"%s": "%s"}}' % (exits.exit_id(RU_V), kept))
        rows = self.rows()
        self.assertIn(kept, rows)
        self.assertEqual(rows[kept]["tag"], "gr-" + kept)
        self.assertEqual([i for i, _ in exits.line_ids(role="ru")], [exits.exit_id(self.awg_ru), kept])
        self.assertEqual(rev.pool_ids()["ru"], [exits.exit_id(self.awg_ru), kept])
        self.assertIn(kept, [e["id"] for e in exits.pool_fallback(roles=exits.ROLES)])


class FallbackTest(PoolRuCase):
    def test_fallback_both_files(self):
        fb = exits.pool_fallback(roles=exits.ROLES)
        self.assertEqual([e["index"] for e in fb], [1, 2, 201, 202])
        ru = [e for e in fb if e["role"] == "ru"]
        self.assertEqual((ru[0]["kind"], ru[0]["iface"], ru[0]["mark"]), ("awg", "kawg201", 457))
        self.assertEqual((ru[1]["kind"], ru[1]["mark"]), (exits.XRAY, 0x300 + 202))
        self.assertTrue(all(e["tag"].startswith("gr-") for e in ru))
        self.assertEqual([e["role"] for e in exits.pool_fallback()], ["abroad", "abroad"])

    def test_the_same_text_in_both_files(self):
        write_ru([VLESS_A])
        rows = exits.pool(None, roles=exits.ROLES)
        ru = [e for e in rows if e["role"] == "ru"][0]
        self.assertEqual(ru["id"], exits.exit_id(VLESS_A) + "-201")      # one exit; the copy is told
        self.assertEqual(ru["problem_code"], "problem.dup")
        fb = [e for e in exits.pool_fallback(roles=exits.ROLES) if e["role"] == "ru"][0]
        self.assertEqual(fb["id"], ru["id"])
        self.assertEqual(rev.pool_ids()["ru"], [ru["id"]])               # the rev and the routes name it the same
        self.assertEqual([i for i, _n, r in exits.all_line_ids() if r == "ru"], [ru["id"]])


class SamePeerTest(PoolRuCase):
    def test_same_key_in_both_files_told_no_twin(self):
        """§2.2: the same server in both files is told (``same_peer_problems`` sees both files), but the routes of a
        foreign copy never go to the Russian one (no ``twin`` across roles)."""
        other = VLESS_A.replace("#Vienna", "#Vienna-RU")
        write_ru([other])
        rows = exits.pool(None, roles=exits.ROLES)
        ru = [e for e in rows if e["role"] == "ru"][0]
        self.assertEqual(ru["same_peer"], exits.exit_id(VLESS_A))
        self.assertNotIn("twin", ru)
        self.assertNotIn("twin", [e for e in rows if e["id"] == exits.exit_id(VLESS_A)][0])
        self.assertEqual(len(exits.same_peer_problems({}, rows)), 1)

    def test_twin_inside_one_file(self):
        write_ru([RU_V, RU_V.replace("#Home", "#Home-2")])
        rows = [e for e in exits.pool(None, roles=exits.ROLES) if e["role"] == "ru"]
        self.assertEqual(rows[1]["twin"], rows[0]["id"])


class RenumberTest(unittest.TestCase):
    def test_each_file_by_itself(self):
        A, B, C, R1, R2, R3 = ("e-%010x" % i for i in range(6))
        # a Russian line out of the middle: only the Russian lines after it
        self.assertEqual(exits.renumbered([A, B, R1, R2, R3], [A, B, R1, R3], 3, 2), [202, 203])
        # a foreign key added at the end: nothing of Russia moves
        self.assertEqual(exits.renumbered([A, B, R1], [A, B, C, R1], 1, 1), [])
        # a foreign line out of the middle: the foreign ones only
        self.assertEqual(exits.renumbered([A, B, C, R1], [A, C, R1], 1, 1), [2, 3])
        # a snapshot of before Q4 (no «pool_ru»): the whole list was upstream.txt
        self.assertEqual(exits.renumbered([A, B], [A, B, R1], None, 1), [])
        self.assertEqual(exits.renumbered([A, B], [A, B], 0, 0), [])

    def test_line_marks_of_russian_lines(self):
        self.assertEqual(exits.line_marks([201, 232]), [457, 0x300 + 201, 488, 0x300 + 232])
        self.assertTrue(all(m < 0x400 for m in exits.line_marks([232])))

    def test_merge_keeps_the_russian_part(self):
        new, _ch, dirty = exits.merge({"pool": ["e-0000000001"], "exits": {}}, {}, ["e-0000000001", "e-0000000002"],
                                      now=1000.0, ru=1)
        self.assertEqual(new["pool_ru"], 1)
        self.assertTrue(dirty)
        new2, _ch, dirty2 = exits.merge(new, {}, ["e-0000000001", "e-0000000002"], now=1001.0, ru=1)
        self.assertFalse(dirty2)
        _n, _c, dirty3 = exits.merge(new, {}, ["e-0000000001", "e-0000000002"], now=1002.0, ru=0)
        self.assertTrue(dirty3)
        self.assertEqual(exits.ru_count([{"role": "ru"}, {"role": "abroad"}, {}]), 1)


if __name__ == "__main__":
    unittest.main()
