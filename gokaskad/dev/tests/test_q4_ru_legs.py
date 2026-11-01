"""T15 Q4 (SPEC-groups §7, §6.5, §13.1 «egress (Q4, bash-тест)»): the legs of the Russian keys (upstream-ru.txt) with
the real functions of install.sh — numbers 201+, ``--egress-awg-routes kawg201``, ``egress_disable`` with the gateway
on, ``heal`` of a killed kawg201, the core 3.1 for a Russian 3.x key, ``egress_ru_apply`` (no bot, no rebuild),
``egress_reresolve`` (a new address by ``awg set … endpoint``, the system resolver silent — 77.88.8.8, bounded), and
the bot's places never read the Russian file. Every check fails on the code before Q4: there the legs of the second
file are «extra» and go, ``egress_up_file``/``egress_awg_lists``/``egress_ru_apply``/``egress_reresolve`` are not
there."""

import json
import os
import re
import subprocess
import time
import unittest

from tests.fakes_gw import INSTALL_SH, awg_conf
from tests.test_336_awgaddr import FAKE_DNS, LEG_FUNCS, LegConfBase, fn, read
from tests.test_336_heal import REVIVE_FUNCS, UNITS

RU_GLOBALS = 'EGRESS_UP_RU="$W/up-ru.txt"; EGRESS_RU_BASE=200; EGRESS_RU_MAX=32\n'

# the fallback of egress_reresolve: a DNS answer of «77.88.8.8» — the fake socket answers the A query from udp.json
FAKE_UDP = r'''
import json as _j, os as _o, socket as _s, struct as _st
_real = _s.socket
class _Fake(_real):
    def sendto(self, data, addr):
        if addr == ("77.88.8.8", 53):
            with open(_o.environ["K3_FAKE_DNS"] + ".udp", "a") as f:
                f.write("asked\n")
            self._q = data
            return len(data)
        return super().sendto(data, addr)
    def connect(self, addr):
        if addr == ("77.88.8.8", 53):
            self._to = addr
            return None
        return super().connect(addr)
    def send(self, data):
        if getattr(self, "_to", None):
            return self.sendto(data, self._to)
        return super().send(data)
    def recv(self, n):
        q = getattr(self, "_q", None)
        if q is None:
            return super().recv(n)
        udp = _o.environ["K3_FAKE_DNS"].replace("dns.json", "udp.json")
        with open(udp) as f:
            ips = _j.load(f)
        ans = b"".join(b"\xc0\x0c" + _st.pack(">HHIH", 1, 1, 60, 4) + _s.inet_aton(ip) for ip in ips)
        qid, flags = q[:2], b"\x81\x80"
        bad = udp + ".bad"                     # a forged answer: another ID, not an answer (QR 0), or an error RCODE
        if _o.path.exists(bad):
            with open(bad) as f:
                how = f.read().strip()
            if how == "id":
                qid = bytes([q[0] ^ 0xFF, q[1]])
            elif how == "qr":
                flags = b"\x01\x80"
            elif how == "rcode":
                flags = b"\x81\x83"
        return qid + flags + _st.pack(">HHHH", 1, len(ips), 0, 0) + q[12:] + ans
_s.socket = _Fake
'''


class RuLegCase(LegConfBase):
    FUNCS = LEG_FUNCS + ("egress_awg_routes", "egress_awg_info", "egress_pool_needs_awg3", "egress_awg_line_of")

    def setUp(self):
        super().setUp()
        self.write("site/sitecustomize.py", FAKE_DNS + FAKE_UDP)
        self.write("udp.json", "[]")
        self.write("links", "")

    def line(self, n, ep, line="1.0"):
        return "awg://" + self.p._b64e(json.dumps(self.p.awg_from_conf(awg_conf(n=n, endpoint=ep, line=line), "k%d" % n)))

    def files(self, abroad=(), ru=()):
        self.write("up.txt", "".join(l + "\n" for l in abroad))
        self.write("up-ru.txt", "".join(l + "\n" for l in ru))

    def run_sh(self, entry, extra=""):
        return super().run_sh(entry, RU_GLOBALS + extra)


class UpFileTest(RuLegCase):
    def test_numbers_to_files(self):
        rc, _log = self.run_sh('for n in 1 200 201 232 233 0 x 0201; do egress_up_file "$n" && echo "$n ${EGRESS_UPF##*/} '
                               '$EGRESS_UPN" >> "$W/out" || echo "$n -" >> "$W/out"; done')
        self.assertEqual(rc, 0)
        self.assertEqual(read(os.path.join(self.w, "out")).split("\n")[:-1],
                         ["1 up.txt 1", "200 up.txt 200", "201 up-ru.txt 1", "232 up-ru.txt 32", "233 -", "0 -", "x -",
                          "0201 up-ru.txt 1"])

    def test_lists_of_both_files(self):
        self.files([self.line(1, "198.51.100.31:51820")],
                   [self.line(5, "203.0.113.5:51820"), self.line(6, "203.0.113.6:51820")])
        rc, _log = self.run_sh('egress_awg_lists > "$W/out"')
        rows = [r.split("\t")[:4] for r in read(os.path.join(self.w, "out")).split("\n") if r]
        self.assertEqual(rows, [["1", "kawg1", "257", "257"], ["201", "kawg201", "457", "457"],
                                ["202", "kawg202", "458", "458"]])

    def test_only_32_russian_legs(self):
        self.files([], [self.line(10 + i, "203.0.113.%d:51820" % (10 + i)) for i in range(33)])
        rc, _log = self.run_sh('egress_awg_lists > "$W/out"')
        rows = [r.split("\t")[1] for r in read(os.path.join(self.w, "out")).split("\n") if r]
        self.assertEqual((len(rows), rows[0], rows[-1]), (32, "kawg201", "kawg232"))


class SyncTest(RuLegCase):
    def test_russian_legs_201_and_not_extra(self):
        """Legs of both files come up; the Russian leg kawg201 has the config of ITS key; a second sync does not take
        it away as «extra»; its mark 457 is closed before (rule 457 → table 457)."""
        self.files([self.line(1, "198.51.100.31:51820")], [self.line(7, "203.0.113.7:51820")])
        rc, log = self.run_sh("egress_awg_sync")
        self.assertEqual(rc, 0)
        self.assertIn(self.peer(1), self.conf(1))
        self.assertIn(self.peer(7), self.conf(201))
        self.assertIn("Endpoint = 203.0.113.7:51820", self.conf(201))
        self.assertIn("457 457", self.rules())
        self.write("links", "kawg1\nkawg201\n")
        self.write("known", "kawg1\nkawg201\n")
        rc, log = self.run_sh("egress_awg_sync")
        self.assertNotIn("systemctl disable --no-reload --now kaskad-leg@kawg201.service", self.acts(log))
        self.assertIsNotNone(self.conf(201))

    def test_routes_kawg201(self):
        """``--egress-awg-routes kawg201`` (ExecStartPre of its unit after a reboot): the rule of the Russian leg."""
        self.files([], [self.line(7, "203.0.113.7:51820")])
        rc, log = self.run_sh("egress_awg_routes kawg201")
        self.assertEqual(rc, 0)
        self.assertIn("457 457", self.rules())

    def test_unit_start_of_201(self):
        """``--egress-awg-conf kawg201``: the config of the Russian key 1 (not of the foreign line 1)."""
        self.files([self.line(1, "198.51.100.31:51820")], [self.line(7, "203.0.113.7:51820")])
        rc, _log = self.run_sh("egress_awg_conf_unit kawg201")
        self.assertEqual(rc, 0)
        self.assertIn(self.peer(7), self.conf(201))
        self.assertNotIn(self.peer(1), self.conf(201))

    def test_core_31_for_a_russian_3x_key(self):
        """A node with only a Russian 3.x key: the core 3.1 is asked (egress_pool_needs_awg3)."""
        self.files([], [self.line(8, "203.0.113.8:51820", line="3.1")])
        rc, _log = self.run_sh('egress_pool_has_awg && echo has >> "$W/out"; egress_pool_needs_awg3 && echo 31 >> "$W/out"; '
                               'egress_awg_info >> "$W/out"')
        out = read(os.path.join(self.w, "out"))
        self.assertIn("has\n31\n", out)
        self.assertRegex(out, r"(?m)^201\t3\.1\t")


class HealTest(RuLegCase):
    FUNCS = REVIVE_FUNCS + ("egress_awg_routes", "egress_awg_info", "egress_pool_needs_awg3")

    def test_heal_raises_a_killed_kawg201(self):
        self.files([self.line(1, "198.51.100.31:51820")], [self.line(7, "203.0.113.7:51820")])
        self.write("links", "kawg1\n")                       # kawg201 is gone (killed)
        rc, log = self.run_sh("egress_awg_heal", UNITS)
        acts = self.acts(log)
        self.assertIn("systemctl restart kaskad-leg@kawg201.service", acts)
        self.assertNotIn("systemctl restart kaskad-leg@kawg1.service", acts)
        self.assertIn("kawg201", read(os.path.join(self.w, "links")))


class DisableTest(RuLegCase):
    FUNCS = RuLegCase.FUNCS + ("egress_disable", "egress_awg_teardown")
    STUB = r'''
k3_units_off() { echo "units_off $*" >> "$LOG"; }
egress_set_bot_proxy() { echo "bot_proxy '$1'" >> "$LOG"; }
egress_probe_remove() { :; }
t() { echo "$1"; }
k3_gw_enabled() { [ -e "$W/gw_on" ]; }
'''

    def test_gateway_on_keeps_the_russian_legs(self):
        self.files([], [self.line(7, "203.0.113.7:51820")])
        self.run_sh("egress_awg_sync")
        self.write("links", "kawg1\nkawg201\n")
        self.write("known", "kawg1\nkawg201\n")
        self.write("gw_on", "")
        rc, log = self.run_sh("egress_disable", self.STUB)
        acts = self.acts(log)
        self.assertNotIn("systemctl disable --no-reload --now kaskad-leg@kawg201.service", acts)
        self.assertIn("systemctl disable --no-reload --now kaskad-leg@kawg1.service", acts)   # no key — goes
        self.assertIn("gw apply", log)

    def test_gateway_off_as_before(self):
        self.files([], [self.line(7, "203.0.113.7:51820")])
        self.write("links", "kawg201\n")
        self.write("known", "kawg201\n")
        rc, log = self.run_sh("egress_disable", self.STUB)
        self.assertIn("systemctl disable --no-reload --now kaskad-leg@kawg201.service", self.acts(log))


class RuApplyTest(RuLegCase):
    FUNCS = RuLegCase.FUNCS + ("egress_ru_apply", "egress_ru_apply_now", "egress_pool_hash", "egress_ru_shifted",
                               "egress_ru_hold")
    # k3_gw_pool_changed — as the core does it (REV-s1-q4-2 С-1): gw_fail — gw apply failed (code 1); xray_old — code 0,
    # but xray stayed on the old config; else the table and xray by the new file — the mark of the held legs goes.
    # flock — legs.lock: legs_busy — not taken in 30 s; every call and whether gw apply ran under it — in the log.
    STUB = r'''
EGRESS_DIR="$W"
t() { echo "$1"; }
_egr_mark() { echo "mark $*" >> "$LOG"; }
egress_xray_bin() { return 1; }
egress_skip_report() { :; }
egress_ensure_awg() { echo "ensure_awg '$1'" >> "$LOG"; }
k3_engine() { echo nft; }
k3_gw_enabled() { [ ! -e "$W/gw_off" ]; }
k3_fold_tty() { echo "$2"; }
k3_log_lang() { echo ru; }
flock() { echo "flock $*" >> "$LOG"; [ ! -e "$W/legs_busy" ] && echo held > "$W/legs_locked"; }
k3_gw_pool_changed() {
    echo "gw apply$([ -e "$W/legs_locked" ] && echo " under legs.lock")" >> "$LOG"
    [ ! -e "$W/gw_fail" ] || return 1
    [ -e "$W/xray_old" ] || rm -f "$AWG_DIR/.ru-hold"
    return 0
}
'''

    def hash_of(self, lines):
        import hashlib
        return hashlib.sha1("\n".join(lines).encode()).hexdigest()[:16]

    def test_writes_the_file_legs_and_gw_apply_no_bot(self):
        new = [self.line(7, "203.0.113.7:51820"), self.line(8, "203.0.113.8:51820", line="3.1")]
        self.files([self.line(1, "198.51.100.31:51820")], [])
        self.write("in", "".join(l + "\n" for l in new))
        rc, log = self.run_sh('egress_ru_apply %s < "$W/in"' % self.hash_of([]), self.STUB)
        self.assertEqual(rc, 0)
        self.assertEqual(read(os.path.join(self.w, "up-ru.txt")).split("\n")[:-1], new)
        self.assertEqual(oct(os.stat(os.path.join(self.w, "up-ru.txt")).st_mode & 0o777), "0o600")
        self.assertIn("ensure_awg '3'", log)
        self.assertIn("gw apply", log)
        self.assertNotIn("rebuild", log)
        self.assertFalse([a for a in log if "kaskad-bot" in a])
        self.assertIn(self.peer(7), self.conf(201))

    def test_a_removed_key_in_the_middle_never_lends_its_leg(self):
        """Д-1 (SEC-s1-q4, решение координатора): the key of place 202 removed — the key of 203 moves to 202. The legs
        whose place changes owner (202, 203) are closed BEFORE the file is written, gw apply runs with them closed
        (marks by the new numbers), only then the legs come up; kawg201 (the same key) is not touched. Before: sync
        first — kawg202 carried the key of another router while the marks of the removed exit still led there."""
        keys = [self.line(7, "203.0.113.7:51820"), self.line(8, "203.0.113.8:51820"), self.line(9, "203.0.113.9:51820")]
        self.files([], keys)
        rc, log = self.run_sh("egress_awg_sync", self.STUB)
        self.write("links", "kawg201\nkawg202\nkawg203\n")
        self.write("known", "kawg201\nkawg202\nkawg203\n")
        new = [keys[0], keys[2]]
        self.write("in", "".join(l + "\n" for l in new))
        open(os.path.join(self.w, "log"), "w").close()
        rc, log = self.run_sh('egress_ru_apply %s < "$W/in"' % self.hash_of(keys), self.STUB)
        self.assertEqual(rc, 0)
        acts = self.acts(log)
        off = [i for i, a in enumerate(acts) if a.startswith("systemctl disable --no-reload --now kaskad-leg@kawg20")]
        apply_at = acts.index("gw apply")
        up202 = [i for i, a in enumerate(acts) if "restart kaskad-leg@kawg202" in a]
        self.assertEqual(len(off) >= 2, True, acts)
        self.assertTrue(all(i < apply_at for i in off[:2]), acts)
        self.assertTrue(up202 and up202[0] > apply_at, acts)
        self.assertFalse([a for a in acts[:apply_at] if "kaskad-leg@kawg201" in a and "disable" in a], acts)
        self.assertIn(self.peer(9), self.conf(202))

    def test_appended_key_as_before(self):
        """Nothing changes owner (a key appended): the legs first, then gw apply — as before."""
        keys = [self.line(7, "203.0.113.7:51820")]
        self.files([], keys)
        self.run_sh("egress_awg_sync", self.STUB)
        new = keys + [self.line(8, "203.0.113.8:51820")]
        self.write("in", "".join(l + "\n" for l in new))
        open(os.path.join(self.w, "log"), "w").close()
        rc, log = self.run_sh('egress_ru_apply %s < "$W/in"' % self.hash_of(keys), self.STUB)
        acts = self.acts(log)
        self.assertFalse([a for a in acts if "disable" in a and "kawg201" in a], acts)
        up = [i for i, a in enumerate(acts) if "restart kaskad-leg@kawg202" in a]
        self.assertTrue(up and up[0] < acts.index("gw apply"), acts)

    def test_changed_meanwhile_and_limits(self):
        self.files([], [self.line(7, "203.0.113.7:51820")])
        self.write("in", self.line(8, "203.0.113.8:51820") + "\n")
        rc, log = self.run_sh('egress_ru_apply %s < "$W/in"; echo "rc=$?" >> "$LOG"' % self.hash_of([]), self.STUB)
        self.assertIn("rc=3", log)
        self.assertIn(self.line(7, "203.0.113.7:51820"), read(os.path.join(self.w, "up-ru.txt")))
        cur = self.hash_of([self.line(7, "203.0.113.7:51820")])
        self.write("in", "".join(self.line(20 + i, "203.0.113.%d:51820" % (20 + i)) + "\n" for i in range(33)))
        rc, log = self.run_sh('egress_ru_apply %s < "$W/in"; echo "rc=$?" >> "$LOG"' % cur, self.STUB)
        self.assertIn("rc=5", log)
        self.write("in", "http://example.com/sub\n")
        rc, log = self.run_sh('egress_ru_apply %s < "$W/in"; echo "rc=$?" >> "$LOG"' % cur, self.STUB)
        self.assertIn("rc=1", log)
        self.assertEqual(read(os.path.join(self.w, "up-ru.txt")).split("\n")[:-1], [self.line(7, "203.0.113.7:51820")])
        for sep in ("\u2028", "\u2029", "\u0085", "\x0b", "\x0c", "\x1c", "\r"):    # SEC П-3: one line for bash, two for the core
            with self.subTest(sep=repr(sep)):
                self.write("in", self.line(8, "203.0.113.8:51820") + "#a" + sep + "vless://x@203.0.113.9:443\n")
                open(os.path.join(self.w, "log"), "w").close()
                rc, log = self.run_sh('egress_ru_apply %s < "$W/in"; echo "rc=$?" >> "$LOG"' % cur, self.STUB)
                self.assertIn("rc=1", log)
                self.assertEqual(read(os.path.join(self.w, "up-ru.txt")).split("\n")[:-1],
                                 [self.line(7, "203.0.113.7:51820")])


class HeldLegsBase(RuLegCase):
    """REV-s1-q4-2 С-1: Д-1 holds when the apply fails. Keys 7, 8, 9 in upstream-ru.txt, key 8 removed: the legs of
    places 202 and 203 change owner. gw apply failed (or xray stayed on its old config) — the old marks of the removed
    exit still lead to 202: the legs stay closed with the mark «ждут применения» (.ru-hold), which neither the build of
    the legs, nor their unit's start, nor the watchdog (egress_awg_leg_up) gets past until the core applies the table
    and xray by the new file. Before: the legs came up under the new numbers right after the failed gw apply."""

    FUNCS, STUB, hash_of = RuApplyTest.FUNCS, RuApplyTest.STUB, RuApplyTest.hash_of

    def setUp(self):
        super().setUp()
        self.keys3 = [self.line(7, "203.0.113.7:51820"), self.line(8, "203.0.113.8:51820"),
                      self.line(9, "203.0.113.9:51820")]
        self.files([], self.keys3)
        self.run_sh("egress_awg_sync", self.STUB)
        self.write("links", "kawg201\nkawg202\nkawg203\n")
        self.write("known", "kawg201\nkawg202\nkawg203\n")
        os.makedirs(os.path.join(self.w, "run", "kaskad", "gw"), exist_ok=True)    # legs.lock is taken
        self.new = [self.keys3[0], self.keys3[2]]
        self.write("in", "".join(l + "\n" for l in self.new))
        open(os.path.join(self.w, "log"), "w").close()

    def hold(self):
        return read(os.path.join(self.w, "awg", ".ru-hold"))

    def apply(self, *flags):
        for f in flags:
            open(os.path.join(self.w, f), "w").close()
        return self.run_sh('egress_ru_apply %s < "$W/in"; echo "rc=$?" >> "$LOG"' % self.hash_of(self.keys3), self.STUB)

    def assert_held(self, log):
        acts = self.acts(log)
        self.assertIn("rc=0", log)
        self.assertEqual(read(os.path.join(self.w, "up-ru.txt")).split("\n")[:-1], self.new)
        self.assertFalse([a for a in acts if re.search(r"restart kaskad-leg@kawg20[23]", a)], acts)
        self.assertNotIn(self.peer(9), self.conf(202) or "")
        self.assertIsNone(self.conf(203))
        self.assertEqual(self.hold().split("\n")[:3], [self.hash_of(self.new), "kawg202", "kawg203"])
        self.assertIn("mark egress_warn:ru_held", log)
        self.assertIn("458 458", self.rules())                 # the mark of place 202 — to unreachable, not dropped
        first_off = min(i for i, a in enumerate(acts) if "disable --no-reload --now kaskad-leg@kawg20" in a)
        self.assertLess(next(i for i, a in enumerate(acts) if a.startswith("flock -w 30 ")), first_off)
        self.assertIn("gw apply under legs.lock", acts)
        # the build of the legs again, the start of the unit (systemd, boot) — the legs stay closed
        open(os.path.join(self.w, "log"), "w").close()
        rc, log = self.run_sh('egress_awg_sync; egress_awg_conf_unit kawg202; echo "conf=$?" >> "$LOG"', self.STUB)
        self.assertFalse([a for a in self.acts(log) if re.search(r"restart kaskad-leg@kawg20[23]", a)], log)
        self.assertIn("conf=1", log)
        self.assertIsNone(self.conf(202))


class HeldLegsTest(HeldLegsBase):
    def test_gw_apply_fails(self):
        rc, log = self.apply("gw_fail")
        self.assert_held(log)
        # the next apply passes (the core takes the mark away) — only then the legs, with the keys of their places
        os.unlink(os.path.join(self.w, "gw_fail"))
        open(os.path.join(self.w, "log"), "w").close()
        rc, log = self.run_sh('egress_ru_apply', self.STUB)
        acts = self.acts(log)
        up = [i for i, a in enumerate(acts) if "restart kaskad-leg@kawg202" in a]
        self.assertTrue(up and up[0] > next(i for i, a in enumerate(acts) if a.startswith("gw apply")), acts)
        self.assertIn(self.peer(9), self.conf(202))
        self.assertFalse(os.path.exists(os.path.join(self.w, "awg", ".ru-hold")))

    def test_xray_stays_on_the_old_config(self):
        """gw apply answered 0, but xray of the gateway runs its old config (ops._apply: a warning) — the same."""
        rc, log = self.apply("xray_old")
        self.assert_held(log)

    def test_another_change_while_held_keeps_the_legs_and_moves_the_fingerprint(self):
        """A key appended while the legs wait: they stay held, the mark waits for the new file (else nobody releases)."""
        self.apply("gw_fail")
        more = self.new + [self.line(10, "203.0.113.10:51820")]
        self.write("in", "".join(l + "\n" for l in more))
        open(os.path.join(self.w, "log"), "w").close()
        rc, log = self.run_sh('egress_ru_apply %s < "$W/in"' % self.hash_of(self.new), self.STUB)
        self.assertEqual(self.hold().split("\n")[:3], [self.hash_of(more), "kawg202", "kawg203"])
        self.assertFalse([a for a in self.acts(log) if re.search(r"restart kaskad-leg@kawg20[23]", a)], log)

    def test_legs_lock_busy_refuses_with_6(self):
        """The watchdog holds legs.lock longer than 30 s: code 6, nothing touched (was: on without the lock)."""
        rc, log = self.apply("legs_busy")
        self.assertIn("rc=6", log)
        self.assertIn("mark egress_fail:pool_busy", log)
        self.assertEqual(read(os.path.join(self.w, "up-ru.txt")).split("\n")[:-1], self.keys3)
        self.assertFalse(os.path.exists(os.path.join(self.w, "awg", ".ru-hold")))
        self.assertFalse([a for a in self.acts(log) if "disable" in a or "gw apply" in a], log)
        self.assertEqual([f for f in os.listdir(self.w) if f.startswith(".poolru.")], [])

    def test_gateway_off_nothing_to_wait_for(self):
        """The gateway off: no marks of devices — the mark goes at once, the legs come up as before."""
        rc, log = self.apply("gw_off", "xray_old")
        self.assertFalse(os.path.exists(os.path.join(self.w, "awg", ".ru-hold")))
        self.assertIn(self.peer(9), self.conf(202))
        self.assertNotIn("mark egress_warn:ru_held", log)


class HeldLegsQ2Test(HeldLegsBase):
    """REV-s1-q4-3 (T15 Q2). З-1: from the mark to the file nothing cuts in (Ctrl+C, the SSH hangup, TERM of a timeout);
    a write cut off by kill -9 leaves no keys behind for long — the next write takes them away. З-2: gw apply gets the
    legs.lock install.sh holds. З-3: the mark is written while the OLD file is on disk (B4); a held leg found alive
    goes (B3)."""

    WRAP = ('eval "$(declare -f %s | sed \'1s/^%s /_real_%s /\')"\n')

    def wrap(self, name, body):
        return self.WRAP % (name, name, name) + "%s() { %s; }\n" % (name, body)

    def call(self, extra=""):
        return self.run_sh(extra + 'egress_ru_apply %s < "$W/in"; echo "rc=$?" >> "$LOG"' % self.hash_of(self.keys3),
                           self.STUB)

    def test_signals_in_the_window_wait_for_the_file(self):
        """Ctrl+C, the hangup of SSH and TERM arrive while the legs are closing — the file is written all the same, the
        traps of the caller are back afterwards. Before: TERM killed the write — a mark of a file never written."""
        open(os.path.join(self.w, "gw_fail"), "w").close()
        rc, log = self.call('trap "echo caller-trap >> \\"\\$LOG\\"" INT\n'
                            + self.wrap("egress_awg_leg_remove", '[ -e "$W/sig" ] || { : > "$W/sig"; kill -TERM $$; '
                                        'kill -INT $$; kill -HUP $$; }; _real_egress_awg_leg_remove "$@"'))
        self.assertEqual(rc, 0, log)
        self.assert_held(log)
        rc, log = self.run_sh('trap "echo caller-trap" INT\n' + self.wrap("egress_awg_leg_remove", ":")
                              + 'egress_ru_apply %s < "$W/in"; trap -p INT >> "$LOG"' % self.hash_of(self.new),
                              self.STUB)
        self.assertIn("trap -- 'echo caller-trap' SIGINT", log)

    def test_a_failed_mark_gives_the_traps_back(self):
        """REV-s1-q2 З-2 (порча b_fail_restore): the mark could not be written (mktemp, a full disk) — the change is
        refused, nothing written, and the traps of the caller are back: the menu hears Ctrl+C again."""
        rc, log = self.run_sh('trap "echo caller-trap" INT\n' + self.wrap("egress_ru_hold", "return 1")
                              + 'egress_ru_apply %s < "$W/in"; echo "rc=$?" >> "$LOG"; trap -p INT >> "$LOG"; '
                                'trap -p TERM >> "$LOG"' % self.hash_of(self.keys3), self.STUB)
        self.assertIn("rc=1", log)
        self.assertIn("trap -- 'echo caller-trap' SIGINT", log)
        self.assertNotIn("SIGTERM", log)                                  # no «ignore» left behind
        self.assertEqual(read(os.path.join(self.w, "up-ru.txt")).split("\n")[:-1], self.keys3)

    def test_kill_9_in_the_window_leaves_no_keys_for_the_next_write(self):
        """kill -9 between the mark and the file (the core then releases by the file there is, test_s1_q2_hold): the
        temporary file with the keys and a temporary mark go with the next write, under the lock of the pool."""
        open(os.path.join(self.w, "gw_fail"), "w").close()
        self.write("awg/.ru-hold.AbC123", "stale")
        rc, log = self.call(self.wrap("egress_awg_leg_remove", "kill -KILL $$"))
        self.assertNotEqual(rc, 0)
        self.assertEqual(read(os.path.join(self.w, "up-ru.txt")).split("\n")[:-1], self.keys3)     # the file is old
        self.assertEqual(self.hold().split("\n")[0], self.hash_of(self.new))                      # the mark of the new
        left = [f for f in os.listdir(self.w) if f.startswith(".poolru.")]
        self.assertEqual(len(left), 1)
        rc, log = self.call()
        self.assertIn("rc=0", log)
        self.assertEqual(read(os.path.join(self.w, "up-ru.txt")).split("\n")[:-1], self.new)
        self.assertEqual([f for f in os.listdir(self.w) if f.startswith(".poolru.")], [])
        self.assertEqual(sorted(f for f in os.listdir(os.path.join(self.w, "awg")) if f.startswith(".ru-hold")),
                         [".ru-hold"])

    def test_the_mark_is_written_while_the_old_file_is_there(self):
        """B4: the mark BEFORE the file — a restart of the leg's unit in between (ExecStartPre) finds it."""
        open(os.path.join(self.w, "gw_fail"), "w").close()
        rc, log = self.call(self.wrap("egress_ru_hold", '_real_egress_ru_hold "$@" && '
                                      'echo "hold at $(egress_pool_hash "$EGRESS_UP_RU")" >> "$LOG"'))
        self.assertIn("hold at %s" % self.hash_of(self.keys3), log)
        self.assertEqual(read(os.path.join(self.w, "up-ru.txt")).split("\n")[:-1], self.new)

    def test_a_held_leg_found_alive_goes(self):
        """B3: the build of the legs finds a held leg up (started by hand, a unit restarted meanwhile) — it is closed."""
        self.write("awg/.ru-hold", "0123456789abcdef\nkawg202\n")
        rc, log = self.run_sh("egress_awg_sync", self.STUB)
        acts = self.acts(log)
        self.assertTrue([a for a in acts if "disable --no-reload --now kaskad-leg@kawg202" in a], acts)
        self.assertFalse([a for a in acts if "restart kaskad-leg@kawg202" in a], acts)

    def test_gw_apply_gets_the_legs_lock(self):
        """З-2: the core releases the mark under legs.lock; gw apply runs under the one install.sh holds — handed down
        (KASKAD_LEGS_LOCK_FD, open in the child), else the core would wait for its own caller."""
        open(os.path.join(self.w, "gw_fail"), "w").close()
        rc, log = self.call(self.wrap("k3_gw_pool_changed",
                                      'bash -c \'[ -n "$KASKAD_LEGS_LOCK_FD" ] && true >&"$KASKAD_LEGS_LOCK_FD"\' '
                                      '&& echo "child has legs fd" >> "$LOG"; _real_k3_gw_pool_changed'))
        self.assertIn("child has legs fd", log)


class ReresolveTest(RuLegCase):
    """§6.5. The addresses are global ones of no example range (203.0.114.0/24): only a global IPv4 is taken."""
    FUNCS = RuLegCase.FUNCS + ("egress_reresolve", "egress_reresolve_now", "egress_reresolve_py", "egress_pool_locked")

    def awg_tool(self):
        return 'awg_tool_bin() { [ -e "$W/core" ] && echo "$W/awgtool"; }\n'

    def setUp(self):
        super().setUp()
        self.write("awgtool", '#!/bin/sh\necho "awg $*" >> "$(dirname "$0")/log"\n')
        os.chmod(os.path.join(self.w, "awgtool"), 0o755)

    def built(self):
        self.files([], [self.line(7, "home.example.net:51820")])
        self.dns({"home.example.net": ["203.0.114.70"]})
        rc, _log = self.run_sh("egress_awg_sync")
        self.assertIn("Endpoint = 203.0.114.70:51820", self.conf(201))
        self.write("links", "kawg201\n")

    def test_new_address_set_without_recreating(self):
        self.built()
        self.dns({"home.example.net": ["203.0.114.71"]})
        rc, log = self.run_sh("egress_reresolve 201", self.awg_tool())
        self.assertEqual(rc, 0, self.out)
        sets = [a for a in log if a.startswith("awg set")]
        self.assertEqual(len(sets), 1)
        self.assertRegex(sets[0], r"^awg set kawg201 peer [A-Za-z0-9+/]{43}= endpoint 203\.0\.114\.71:51820$")
        self.assertIn("Endpoint = 203.0.114.71:51820", self.conf(201))
        self.assertFalse([a for a in self.acts(log) if a.startswith(("systemctl", "ip link del"))])
        self.assertTrue([a for a in log if "EGRESS_LOG_LEG_RERESOLVED kawg201" in a])
        self.assertFalse([a for a in log if "203.0.114.71" in a and "RERESOLVED" in a])   # SEC П-2: no address

    def test_same_address_nothing(self):
        self.built()
        rc, log = self.run_sh("egress_reresolve 201", self.awg_tool())
        self.assertEqual(rc, 0)
        self.assertFalse([a for a in log if a.startswith("awg set")])

    def test_silent_resolver_then_77_88_8_8_bounded(self):
        """The system resolver silent: after EGRESS_RERESOLVE_WAIT s — a query to 77.88.8.8; only a global IPv4."""
        self.built()
        self.dns({"home.example.net": "sleep"})
        self.write("udp.json", json.dumps(["10.0.0.5", "198.51.100.9", "203.0.114.72"]))   # private, documentation — out
        t0 = time.time()
        rc, log = self.run_sh("EGRESS_RERESOLVE_WAIT=1 egress_reresolve 201", self.awg_tool())
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(rc, 0, self.out)
        self.assertTrue(os.path.exists(os.path.join(self.w, "dns.json.udp")))
        self.assertIn("Endpoint = 203.0.114.72:51820", self.conf(201))

    def test_pool_busy_nothing(self):
        """SEC П-5: the pool is being changed (its lock held) — no awg set, no new config: the line N may be another
        key's by now; the next pass of the collector retries. Free — under that lock, without waiting."""
        self.built()
        self.dns({"home.example.net": ["203.0.114.71"]})
        lock = 'EGRESS_POOL_LOCK="$W/pool.lock"; k3_fold_tty() { echo "$2"; }; _egr_mark() { :; }\n'
        rc, log = self.run_sh("egress_reresolve 201", self.awg_tool() + lock
                              + 'flock() { echo "flock $*" >> "$LOG"; return 1; }\n')
        self.assertEqual(rc, 6)
        self.assertFalse([a for a in log if a.startswith("awg set")])
        self.assertIn("Endpoint = 203.0.114.70:51820", self.conf(201))
        self.assertTrue([a for a in log if a.startswith("flock -w 0 ")], log)
        rc, log = self.run_sh("egress_reresolve 201", self.awg_tool() + lock
                              + 'flock() { echo "flock $*" >> "$LOG"; return 0; }\n')
        self.assertEqual(rc, 0)
        self.assertIn("Endpoint = 203.0.114.71:51820", self.conf(201))

    def test_forged_77_88_8_8_answer_refused(self):
        """SEC П-1: an answer with another ID, without QR or with an error RCODE — no new address, the leg as it was."""
        for how in ("id", "qr", "rcode"):
            with self.subTest(how=how):
                self.built()
                self.dns({"home.example.net": "sleep"})
                self.write("udp.json", json.dumps(["203.0.114.72"]))
                self.write("udp.json.bad", how)
                rc, log = self.run_sh("EGRESS_RERESOLVE_WAIT=1 egress_reresolve 201", self.awg_tool())
                self.assertNotEqual(rc, 0)
                self.assertFalse([a for a in log if a.startswith("awg set")])
                self.assertIn("Endpoint = 203.0.114.70:51820", self.conf(201))
        with open(INSTALL_SH, encoding="utf-8") as f:
            self.assertIn("s.connect((\"77.88.8.8\", 53))", f.read())

    def test_no_answer_anywhere(self):
        self.built()
        self.dns({})
        rc, log = self.run_sh("EGRESS_RERESOLVE_WAIT=1 egress_reresolve 201", self.awg_tool())
        self.assertNotEqual(rc, 0)
        self.assertFalse([a for a in log if a.startswith("awg set")])
        self.assertIn("Endpoint = 203.0.114.70:51820", self.conf(201))

    def test_ip_key_and_no_leg(self):
        self.files([self.line(1, "198.51.100.31:51820")], [])
        self.write("links", "kawg1\n")
        rc, _log = self.run_sh("egress_reresolve 1", self.awg_tool())
        self.assertNotEqual(rc, 0)
        rc, _log = self.run_sh("egress_reresolve 201", self.awg_tool())
        self.assertNotEqual(rc, 0)


class BotNeverReadsTheRussianFileTest(unittest.TestCase):
    """§2.2, §7: the config of the bot, its xray, the menu list, the test and the active key read upstream.txt only."""

    def test_bot_places(self):
        for name in ("egress_rebuild", "egress_bot_config", "egress_tg_rebuild", "egress_list", "egress_test",
                     "egress_active_index", "egress_replace_now", "egress_add_key_now", "egress_remove_key_now",
                     "egress_probe_sample"):
            try:
                body = fn(name)
            except AssertionError:
                continue
            self.assertNotRegex(body, r"EGRESS_UP_RU|upstream-ru|egress_awg_lists", name)

    def test_no_place_reads_one_file_for_legs(self):
        """Every reader of the legs goes through egress_awg_lists (§7, Б2): no «awglist < "$EGRESS_UP"» left outside it."""
        src = read(INSTALL_SH)
        for name in ("egress_awg_lists", "egress_probe_sample"):     # проба потерь — только активный ключ бота (≤ 200)
            src = src.replace(fn(name), "")
        self.assertEqual(re.findall(r'awglist < "\$EGRESS_UP"', src), [])


class ProbeScriptCompositionTest(unittest.TestCase):
    """REV-s1-q4 С1/П3: probe.sh — отдельный скрипт из «declare -f» и переменных (egress_probe_script). Каждая функция
    install.sh, которую зовут функции этого скрипта (и его цикл), собрана в него; каждая глобальная переменная
    install.sh, которую они читают, задана в нём. До правки: egress_probe_sample звал egress_awg_lists, которой в
    probe.sh нет, — «command not found» раз в 10 с в журнал и ни одного замера; egress_active_index читал EGRESS_CFG,
    которой в probe.sh не было, — при нескольких ключах мерился не активный, а первый."""

    WORD = re.compile(r"(?<![\\\w-])[A-Za-z_][A-Za-z0-9_]*")    # не «\t» и не «-timeout»
    VAR = re.compile(r"\$\{?([A-Z_][A-Z0-9_]*)")

    def _bash(self, script, *args):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        r = subprocess.run(["bash", "-c", script, "_"] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_every_called_function_and_global_is_in_probe_sh(self):
        out = self._bash('source "$1" --lib >/dev/null 2>&1; EGRESS_PROBE_EVERY=10; egress_probe_script; '
                         'echo "@@F"; declare -F | awk \'{print $3}\'; echo "@@V"; compgen -v', INSTALL_SH)
        probe, rest = out.split("@@F\n", 1)
        lib_funcs, lib_vars = rest.split("@@V\n", 1)
        lib_funcs = set(lib_funcs.split())
        base_vars = set(self._bash("compgen -v").split())
        lib_vars = set(lib_vars.split()) - base_vars
        body, loop = probe.rstrip("\n").rsplit("\n", 1)
        self.assertTrue(loop.startswith("n=0; while :;"), loop)
        out = self._bash(body + '\necho "@@F"; declare -F | awk \'{print $3}\'; echo "@@V"; compgen -v')
        mine_funcs, mine_vars = out.split("@@F\n", 1)[1].split("@@V\n", 1)
        mine_funcs, mine_vars = set(mine_funcs.split()), set(mine_vars.split())
        self.assertIn("egress_probe_sample", mine_funcs)
        missing = []
        for name in sorted(mine_funcs) + ["<loop>"]:
            code = loop if name == "<loop>" else self._bash(body + "\ndeclare -f " + name)
            locals_ = set(re.findall(r"\blocal\s+([^;\n]*)", code))
            locals_ = {w for line in locals_ for w in self.WORD.findall(line)}
            for w in set(self.WORD.findall(code)):
                if w in lib_funcs and w not in mine_funcs and w != name:
                    missing.append("%s: функция %s" % (name, w))
            for v in set(self.VAR.findall(code)):
                if v in lib_vars and v not in mine_vars and v not in locals_:
                    missing.append("%s: переменная %s" % (name, v))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
