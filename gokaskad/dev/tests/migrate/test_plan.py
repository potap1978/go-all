"""migrate plan: what is removed (SPEC §4.4), the persisted file edit, blockers, preview (§4.11)."""

from kaskad import errors, ipt, state
from kaskad.migrate import apply as apply_mod
from kaskad.migrate import plan, scan
from tests.helpers import RootCase
from tests.migrate import scripts
from tests.migrate.env import Env, fixture

STALE = (
    '-A PREROUTING -p tcp -m tcp --dport 9000 -m comment --comment "kaskad:9000:tcp" -j DNAT --to-destination 192.0.2.90:9000\n',
    '-A INPUT -p tcp -m tcp --dport 9000 -m comment --comment "kaskad:9000:tcp" -j ACCEPT\n',
    '-A FORWARD -d 192.0.2.90/32 -p tcp -m tcp --dport 9000 -m state --state NEW,RELATED,ESTABLISHED -m comment --comment "kaskad:9000:tcp" -j ACCEPT\n',
    '-A INPUT -p tcp -m tcp --dport 9999 -j ACCEPT\n',
)


def _insert_after(text, anchor, line):
    i = text.index(anchor) + len(anchor)
    return text[:i] + line + text[i:]


class RemovalTest(RootCase):
    def build(self, scenario, force=False, **kw):
        env = Env(self, scenario, **kw).start()
        inv = scan.collect(env.runner)
        return env, inv, plan.build(inv, force=force)

    def test_pro_213_everything_goes(self):
        _env, _inv, pl = self.build("pro_213")
        self.assertEqual(len(pl.remove["nat"]), 3)          # 3 DNAT; kaskad:masq stays
        self.assertEqual(len(pl.remove["filter"]), 9)
        self.assertTrue(all("kaskad:" in r.text for r in pl.removed_rules))
        self.assertFalse(any("kaskad:masq" in r.text for r in pl.removed_rules))
        self.assertEqual([r.text for r in pl.masq_kept],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])
        self.assertIn("Останется как было: правило Kaskad для выхода в интернет через eth0 в iptables — на него может опираться другой VPN на сервере.",
                      pl.preview())
        self.assertEqual([f["port"] for f in pl.forwards], [443, 8443, 51820])
        self.assertEqual(pl.forwards[0]["origin"], "pro")
        self.assertEqual(pl.forwards[0]["name"], "Вена")
        self.assertEqual(pl.summary()["remove_rules"], 12)
        self.assertFalse(pl.blocked)

    def test_unlabeled_masquerade_and_foreign_stay(self):
        _env, inv, pl = self.build("foreign")
        texts = [r.text for r in pl.removed_rules]
        self.assertFalse(any("MASQUERADE" in t for t in texts))           # kaskad:masq kept: DNATs remain
        self.assertEqual([r.text for r in pl.masq_kept],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])
        # the :443 FORWARD pair stays: the user's own DNAT :9443 -> 203.0.113.5:443 relies on it
        self.assertEqual(len(pl.kept_ref), 2)
        self.assertTrue(all("203.0.113.5" in r.text for r, _ref in pl.kept_ref))
        self.assertIn("--dport 9443", pl.kept_ref[0][1])
        # shadowed :465 and skipped :1194 untouched, with their INPUT/FORWARD
        self.assertFalse(any("465" in t or "1194" in t for t in texts))
        # foreign DNATs untouched
        for r in inv.foreign_dnat():
            self.assertNotIn(r.text, texts)
        # free :51820 INPUT and pair go
        self.assertIn("-A INPUT -p udp -m udp --dport 51820 -j ACCEPT", texts)

    def test_free_pair_kept_when_a_shadowed_rule_points_to_the_same_target(self):
        env = Env(self, "empty").start()
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "8443", "-j", "ACCEPT")
        scripts.free_add(env.ipt, "tcp", 8443, 443, "203.0.113.5")   # shadowed by the ACCEPT above
        scripts.free_add(env.ipt, "tcp", 443, 443, "203.0.113.5")    # imported, same target/pair
        inv = scan.collect(env.runner)
        pl = plan.build(inv)
        self.assertEqual([c.status for c in inv.cands], ["shadowed", "import"])
        texts = [r.text for r in pl.removed_rules]
        self.assertFalse(any(r.chain == "FORWARD" for r in pl.removed_rules), texts)
        self.assertEqual(len(pl.kept_ref), 2)
        self.assertIn("-A INPUT -p tcp -m tcp --dport 443 -j ACCEPT", texts)

    def test_masq_stays_even_when_unlabeled_covers_it(self):
        _env, _inv, pl = self.build("pro_211_over_free")
        self.assertFalse(any("MASQUERADE" in r.text for r in pl.remove["nat"]))
        self.assertEqual([r.text for r in pl.masq_kept],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])

    def test_removal_lines_are_minus_D(self):
        _env, _inv, pl = self.build("free_v10")
        self.assertEqual(pl.removal_lines("nat"), [
            "-D PREROUTING -p udp -m udp --dport 51820 -j DNAT --to-destination 198.51.100.7:51820",
            "-D PREROUTING -p tcp -m tcp --dport 443 -j DNAT --to-destination 203.0.113.5:443"])


class PersistedTest(RootCase):
    def build(self, rules_v4=None, scenario="pro_213"):
        env = Env(self, scenario).start()
        if rules_v4 is not None:
            self.write("/etc/iptables/rules.v4", rules_v4, 0o640)
        inv = scan.collect(env.runner)
        return env, inv, plan.build(inv)

    def test_equal_file_loses_exactly_the_removed_lines(self):
        _env, inv, pl = self.build()
        self.assertEqual(len(pl.persisted_lines), 12)
        apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        got = self.read("/etc/iptables/rules.v4")
        orig = fixture("pro_213", "rules.v4").splitlines(True)
        self.assertEqual(got, "".join(l for i, l in enumerate(orig) if i not in set(pl.persisted_lines)))
        self.assertEqual([l for l in got.splitlines() if "kaskad:" in l],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])
        self.assertIn(":FORWARD ACCEPT [0:0]\n", got)

    def test_kaskad_masq_only_in_the_file_stays(self):
        text = fixture("free_v10", "rules.v4")
        masq = '-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE\n'
        text = _insert_after(text, ":POSTROUTING ACCEPT [0:0]\n", masq)
        _env, inv, pl = self.build(text, "free_v10")
        self.assertEqual(pl.persisted_stale, [])
        apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        self.assertIn(masq, self.read("/etc/iptables/rules.v4"))

    def test_file_only_kaskad_forward_is_imported_on_switch(self):
        # 2.x would bring kaskad:9000 back at boot: it is a configured cascade, not a leftover
        text = fixture("pro_213", "rules.v4")
        text = _insert_after(text, ":POSTROUTING ACCEPT [0:0]\n", STALE[0])
        text = _insert_after(text, ":OUTPUT ACCEPT [0:0]\n", STALE[1] + STALE[2] + STALE[3])
        _env, inv, pl = self.build(text)
        self.assertEqual([(c.key_text, c.to, c.cls, c.source) for c in pl.file_imports],
                         [("tcp:9000", "192.0.2.90", "pro", "file")])
        self.assertIn(("tcp", 9000, "192.0.2.90", 9000), [(f["proto"], f["port"], f["to"], f["to_port"])
                                                          for f in pl.forwards])
        self.assertEqual(pl.persisted_stale, [])
        self.assertIn("Сейчас не работают: tcp :9000 — они есть только в /etc/iptables/rules.v4 (2.x вернул бы их "
                      "после перезагрузки); их сразу поднимет nftables.", pl.preview())
        self.assertEqual(pl.summary()["persisted_import"], 1)
        apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        got = self.read("/etc/iptables/rules.v4")
        self.assertNotIn("9000", got)
        self.assertIn(STALE[3], got)

    def test_file_only_free_forward_is_imported_on_switch(self):
        text = fixture("free_v10", "rules.v4")
        text = _insert_after(text, ":POSTROUTING ACCEPT [0:0]\n",
                             "-A PREROUTING -p tcp -m tcp --dport 8080 -j DNAT --to-destination 192.0.2.80:80\n")
        text = _insert_after(text, ":OUTPUT ACCEPT [0:0]\n",
                             "-A FORWARD -d 192.0.2.80/32 -p tcp -m tcp --dport 80 -m state --state "
                             "NEW,RELATED,ESTABLISHED -j ACCEPT\n")
        _env, inv, pl = self.build(text, "free_v10")
        self.assertEqual([(c.key_text, c.cls) for c in pl.file_imports], [("tcp:8080", "free")])
        self.assertEqual(pl.persisted_stale, [])
        apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        self.assertNotIn("192.0.2.80", self.read("/etc/iptables/rules.v4"))

    def test_engine_nft_file_only_rule_is_a_stale_line(self):
        # adopt mode: Kaskad 3 already carries the forwards; a Kaskad line only in the file is a leftover
        st = state.empty()
        st["engine"] = "nft"
        state.upsert_forward(st, {"proto": "tcp", "port": 443, "to": "203.0.113.5", "to_port": 443})
        state.save(st)
        text = fixture("empty", "save.txt")
        text = _insert_after(text, ":POSTROUTING ACCEPT [0:0]\n", STALE[0])
        text = _insert_after(text, ":OUTPUT ACCEPT [0:0]\n", STALE[1] + STALE[2] + STALE[3])
        _env, inv, pl = self.build(text, "empty")
        self.assertEqual(pl.mode, "adopt")
        self.assertEqual(pl.file_imports, [])
        self.assertEqual(sorted(r.text for r in pl.persisted_stale), sorted(s.strip() for s in STALE[:3]))
        self.assertTrue(any("уберу" in w for w in pl.warnings))
        apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        got = self.read("/etc/iptables/rules.v4")
        self.assertNotIn("9000", got)
        self.assertIn(STALE[3], got)

    def test_file_only_second_rule_on_a_port_is_a_stale_line(self):
        text = fixture("empty", "save.txt")
        text = _insert_after(text, ":POSTROUTING ACCEPT [0:0]\n",
                             "-A PREROUTING -p tcp -m tcp --dport 8080 -j DNAT --to-destination 192.0.2.80:80\n"
                             "-A PREROUTING -p tcp -m tcp --dport 8080 -j DNAT --to-destination 192.0.2.81:80\n")
        text = _insert_after(text, ":OUTPUT ACCEPT [0:0]\n",
                             "-A FORWARD -d 192.0.2.80/32 -p tcp -m tcp --dport 80 -m state --state "
                             "NEW,RELATED,ESTABLISHED -j ACCEPT\n"
                             "-A FORWARD -d 192.0.2.81/32 -p tcp -m tcp --dport 80 -m state --state "
                             "NEW,RELATED,ESTABLISHED -j ACCEPT\n")
        _env, inv, pl = self.build(text, "empty")
        self.assertEqual([(c.key_text, c.to) for c in pl.file_imports], [("tcp:8080", "192.0.2.80")])
        self.assertEqual(sorted(r.text for r in pl.persisted_stale),
                         ["-A FORWARD -d 192.0.2.81/32 -p tcp -m tcp --dport 80 -m state --state "
                          "NEW,RELATED,ESTABLISHED -j ACCEPT",
                          "-A PREROUTING -p tcp -m tcp --dport 8080 -j DNAT --to-destination 192.0.2.81:80"])
        apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        self.assertNotIn("192.0.2.8", self.read("/etc/iptables/rules.v4"))

    def test_file_only_forward_on_a_port_a_live_rule_takes_stays(self):
        line = "-A PREROUTING -p tcp -m tcp --dport 8080 -j DNAT --to-destination 192.0.2.80:80\n"
        fwd = "-A FORWARD -d 192.0.2.80/32 -p tcp -m tcp --dport 80 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT\n"
        text = _insert_after(fixture("empty", "save.txt"), ":POSTROUTING ACCEPT [0:0]\n", line)
        text = _insert_after(text, ":OUTPUT ACCEPT [0:0]\n", fwd)
        env = Env(self, "empty").start()
        self.write("/etc/iptables/rules.v4", text, 0o640)
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "8080", "-j", "DNAT",
                    "--to-destination", "10.0.0.8:8080")          # the port works for someone else now
        inv = scan.collect(env.runner)
        pl = plan.build(inv)
        self.assertEqual(pl.file_imports, [])
        self.assertEqual([(c.key_text, c.reason) for c in pl.file_kept], [("tcp:8080", "file_taken")])
        self.assertEqual(pl.persisted_lines, [])
        self.assertIn("tcp :8080 → 192.0.2.80:80 — есть только в /etc/iptables/rules.v4, порт сейчас забирает другое "
                      "правило (-p tcp -m tcp --dport 8080 -j DNAT --to-destination 10.0.0.8:8080). Оставлю как есть.",
                      pl.preview())

    def test_duplicate_lines_counted_with_multiplicity(self):
        text = fixture("free_v10", "rules.v4")
        kaskad_dup = "-A INPUT -p tcp -m tcp --dport 443 -j ACCEPT\n"      # Kaskad's: twice in the file, once live
        own_dup = "-A INPUT -i lo -j ACCEPT\n"                              # not Kaskad's: twice in the file, never live
        text = _insert_after(text, ":OUTPUT ACCEPT [0:0]\n", kaskad_dup + own_dup + own_dup)
        _env, inv, pl = self.build(text, "free_v10")
        self.assertEqual(len(pl.persisted_stale), 1)
        apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        got = self.read("/etc/iptables/rules.v4")
        self.assertEqual(got.count(kaskad_dup), 0)   # one with the live rule, the extra copy as stale
        self.assertEqual(got.count(own_dup), 2)      # foreign lines are never touched

    def test_missing_file(self):
        env = Env(self, "pro_213").start()
        import os
        os.unlink(self.path("/etc/iptables/rules.v4"))
        inv = scan.collect(env.runner)
        pl = plan.build(inv)
        self.assertIsNone(inv.persisted)
        self.assertEqual(pl.persisted_lines, [])
        self.assertFalse(any(a["step"] == "persisted_edit" for a in pl.actions()))

    def test_file_with_garbage_is_not_touched(self):
        _env, inv, pl = self.build(fixture("pro_213", "rules.v4") + "this is not iptables\n")
        self.assertEqual(pl.persisted_lines, [])
        self.assertTrue(any("не трогаю" in w for w in pl.warnings))

    def test_crlf_and_no_final_newline_survive(self):
        text = fixture("free_v10", "rules.v4").replace("\n", "\r\n").rstrip("\r\n")
        _env, inv, pl = self.build(text, "free_v10")
        apply_mod.write_persisted(inv.persisted, pl.persisted_lines)
        with open(self.path("/etc/iptables/rules.v4"), "rb") as f:
            got = f.read()
        self.assertTrue(got.endswith(b"# Completed on Sat Oct  4 12:00:00 2026"))
        self.assertIn(b"\r\n", got)
        self.assertNotIn(b"DNAT", got)


class FlushedLiveRulesTest(RootCase):
    """Critic #2: the live rules were flushed (iptables -F, no restore after a reboot), rules.v4
    still has the cascades of Kaskad PRO 2.13. They are imported, not wiped."""

    def env(self):
        env = Env(self, "pro_213").start()
        env.ipt.load(fixture("empty", "save.txt"))            # live: nothing of Kaskad
        return env

    def test_plan(self):
        env = self.env()
        inv = scan.collect(env.runner)
        pl = plan.build(inv)
        self.assertEqual(inv.cands, [])
        sm = pl.summary()
        self.assertEqual((sm["kaskad_rules"], sm["import"], sm["persisted_import"], sm["persisted_lines"]), (0, 3, 3, 12))
        self.assertEqual([(f["proto"], f["port"], f["name"], f["origin"]) for f in pl.forwards],
                         [("tcp", 443, "Вена", "pro"), ("tcp", 8443, "Амстердам", "pro"),
                          ("udp", 51820, "Амстердам", "pro")])
        text = pl.preview()
        self.assertIn("Перенесу 3 проброса:", text)
        self.assertIn("Сейчас не работают: udp :51820, tcp :443, tcp :8443 — они есть только в /etc/iptables/rules.v4",
                      text)
        self.assertNotIn("Пробросов Kaskad в iptables нет", text)
        self.assertEqual(pl.removed_rules, [])

    def test_apply(self):
        env = self.env()
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertEqual(env.nft.table["forwards"], {("udp", 51820): ("198.51.100.7", 51820),
                                                     ("tcp", 443): ("203.0.113.5", 443),
                                                     ("tcp", 8443): ("198.51.100.7", 443)})
        self.assertEqual(env.restores("noflush"), [])                  # nothing live to take out
        got = self.read("/etc/iptables/rules.v4")
        self.assertEqual([l for l in got.splitlines() if "kaskad:" in l],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])
        st = state.load()
        self.assertEqual(len(st["forwards"]), 3)
        self.assertEqual((st["migrations"][-1]["imported"], st["migrations"][-1]["skipped"]), (3, 0))
        self.assertEqual(apply_mod.run(env.runner)["result"], "nothing")


class BlockerTest(RootCase):
    def test_unsupported(self):
        env = Env(self, "pro_213", supported=False).start()
        env.nft.reason = "нет поддержки fib в ядре"
        pl = plan.build(scan.collect(env.runner))
        self.assertTrue(pl.blocked)
        self.assertEqual(pl.blocker_code(), errors.UNSUPPORTED)
        self.assertIn("fib", pl.preview())

    def test_missing_nft_is_installed_not_blocked(self):
        env = Env(self, "pro_213", nft_installed=False, supported=False).start()
        pl = plan.build(scan.collect(env.runner))
        self.assertFalse(pl.blocked)
        self.assertTrue(pl.install_nft)
        self.assertEqual(pl.actions()[1]["step"], "install_nft")

    def test_unsupported_os_is_not_fixed_by_installing(self):
        env = Env(self, "pro_213", nft_installed=False, supported=False).start()
        real = env.nft.probe_support
        env.nft.probe_support = lambda runner: dict(real(runner), code="os", reason="Ubuntu 22.04")
        for p in env.nft.patches()[:1]:
            p.start()
            self.addCleanup(p.stop)
        pl = plan.build(scan.collect(env.runner))
        self.assertFalse(pl.install_nft)
        self.assertEqual(pl.blocker_code(), errors.UNSUPPORTED)

    def test_missing_nft_without_apt(self):
        env = Env(self, "pro_213", nft_installed=False, supported=False, apt=False).start()
        pl = plan.build(scan.collect(env.runner))
        self.assertEqual(pl.blocker_code(), errors.UNSUPPORTED)

    def test_docker_conflict_needs_force(self):
        env = Env(self, "docker_after").start()
        inv = scan.collect(env.runner)
        pl = plan.build(inv)
        self.assertEqual(pl.blocker_code(), errors.CONFLICT)
        self.assertEqual(pl.actions(), [])
        forced = plan.build(inv, force=True)
        self.assertFalse(forced.blocked)
        self.assertTrue(any("--force" in w for w in forced.warnings))

    def test_broken_state(self):
        self.write("/etc/kaskad/state.json", "{broken")
        env = Env(self, "pro_213").start()
        pl = plan.build(scan.collect(env.runner))
        self.assertEqual(pl.blocker_code(), errors.UNSUPPORTED)

    def test_iptables_unreadable(self):
        env = Env(self, "pro_213").start()
        env.ipt.inject(lambda argv, inp: (4, "", "Another app is currently holding the xtables lock.\n")
                       if argv[-1] == "-S" else None)
        pl = plan.build(scan.collect(env.runner))
        self.assertEqual(pl.blocker_code(), errors.ERROR)

    def test_adopt_mode_nothing_to_do(self):
        st = state.empty()
        st["engine"] = "nft"
        state.upsert_forward(st, {"proto": "tcp", "port": 443, "to": "203.0.113.5", "to_port": 443})
        state.save(st)
        env = Env(self, "empty").start()
        pl = plan.build(scan.collect(env.runner))
        self.assertEqual(pl.mode, "adopt")
        self.assertTrue(pl.nothing_to_do)
        self.assertFalse(pl.summary()["has_work"])
        self.assertIn("Переносить нечего", pl.preview())


class PreviewTest(RootCase):
    def test_spec_example(self):
        """The example of SPEC §4.11, built from real rule-making functions."""
        env = Env(self, "empty").start()
        self.write("/etc/kaskad/aliases", "203.0.113.5=Вена|||\n198.51.100.7=Амстердам|||\n")
        e = env.ipt
        scripts.free_add(e, "tcp", 443, 443, "203.0.113.5")
        scripts.free_add(e, "tcp", 443, 443, "192.0.2.9")
        e.cmd("-t", "nat", "-N", "DOCKER")
        e.cmd("-t", "nat", "-A", "DOCKER", "-i", "docker0", "-j", "RETURN")
        e.cmd("-t", "nat", "-A", "DOCKER", "!", "-i", "docker0", "-p", "tcp", "--dport", "8080", "-j", "DNAT",
              "--to-destination", "172.17.0.2:80")
        e.cmd("-t", "nat", "-A", "PREROUTING", "-m", "addrtype", "--dst-type", "LOCAL", "-j", "DOCKER")
        scripts.pro213_add(e, "tcp", 8080, 80, "10.0.0.5")
        scripts.pro213_add(e, "udp", 51820, 51820, "198.51.100.7")
        scripts.pro213_add(e, "tcp", 8443, 443, "198.51.100.7")
        e.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "2222", "-j", "DNAT",
              "--to-destination", "10.0.0.7:22")
        pl = plan.build(scan.collect(env.runner))
        text = pl.preview(backup=self.path("/var/backups/kaskad/20261004T120000Z-pro-2.13"))
        self.assertEqual(text, "\n".join([
            "Перевод пробросов на nftables",
            "",
            "Перенесу 3 проброса:",
            "  tcp :443     → 203.0.113.5:443      Вена        из бесплатной версии",
            "  udp :51820   → 198.51.100.7:51820   Амстердам   из Kaskad PRO",
            "  tcp :8443    → 198.51.100.7:443     Амстердам   из Kaskad PRO",
            "Не перенесу:",
            "  tcp :443 → 192.0.2.9:443 — не работало: второе правило на тот же порт. Уберу.",
            "  tcp :8080 → 10.0.0.5:80 — не работало: порт раньше забирает Docker. Правило оставлю как есть.",
            "Останется как было: чужое правило выхода в интернет через eth0 в iptables, 1 чужое правило DNAT, "
            "правила Docker, правило Kaskad для выхода в интернет через eth0 в iptables — на него может опираться "
            "другой VPN на сервере.",
            "",
            "Что изменится:",
            "  • проброс срабатывает только для адресов этого сервера",
            "  • у каждого проброса появится счётчик трафика",
            "  • правила iptables Kaskad заменит таблица nftables «kaskad»",
            "Уберу из iptables: 15 правил.",
            "Резервная копия: /var/backups/kaskad/20261004T120000Z-pro-2.13",
            "Вернуть как было: меню «Движок и перенос» → «Вернуть iptables».",
        ]))

    def test_plural(self):
        self.assertEqual([plan.forwards_word(n) for n in (1, 2, 5, 11, 21, 22, 25)],
                         ["1 проброс", "2 проброса", "5 пробросов", "11 пробросов", "21 проброс", "22 проброса",
                          "25 пробросов"])

    def test_warnings_in_preview(self):
        env = Env(self, "foreign").start()
        text = plan.build(scan.collect(env.runner)).preview()
        self.assertIn("tcp :8443: правило правили вручную, условия (-i eth0) будут сняты.", text)
        self.assertIn("оно не срабатывало и после переезда тоже не будет", text)
        self.assertIn("такую цель Kaskad 3 не пробрасывает", text)

    def test_no_open_connections_warning_any_more(self):
        # the engine adopts pre-switch connections (fwd_count sets the K mark on DNATed connections of
        # forward ports), verified on real kernels — so no scenario warns about them
        for sc in ("docker_before", "real-pro23-ufw", "pro_213"):
            self.tearDown()
            self.setUp()
            env = Env(self, sc).start()
            text = plan.build(scan.collect(env.runner)).preview()
            self.assertNotIn("открытые до переезда", text, sc)

    def test_empty_server(self):
        env = Env(self, "empty").start()
        text = plan.build(scan.collect(env.runner)).preview()
        self.assertIn("Пробросов Kaskad в iptables нет — включу новый движок.", text)

    def test_no_secrets_anywhere(self):
        from tests.migrate.env import SECRETS
        env = Env(self, "pro_213").start()
        d = plan.build(scan.collect(env.runner)).to_dict()
        import json
        dump = json.dumps(d, ensure_ascii=False)
        for s in SECRETS:
            self.assertNotIn(s, dump)
        self.assertEqual(ipt.MASQ_LABEL, "kaskad:masq")


class StateRestoreTest(RootCase):
    """Critic #3b: after «Вернуть iptables» on a server without netfilter-persistent a reboot wipes
    the rules; the next switch offers the forwards state.json kept instead of an empty list."""

    def rolled_back_and_rebooted(self):
        from kaskad.migrate import rollback
        env = Env(self, "pro_213", netfilter_persistent=False).start()
        apply_mod.run(env.runner)
        rollback.run(env.runner)
        st = state.load()
        self.assertEqual((st["engine"], len(st["forwards"])), ("legacy", 3))
        env.ipt.load(fixture("empty", "save.txt"))           # the reboot: nothing restores iptables
        return env

    def test_offered_back(self):
        env = self.rolled_back_and_rebooted()
        pl = plan.build(scan.collect(env.runner))
        self.assertEqual([(c.key_text, c.source) for c in pl.state_restores],
                         [("tcp:443", "state"), ("tcp:8443", "state"), ("udp:51820", "state")])
        self.assertEqual(pl.summary()["import"], 3)
        self.assertEqual(pl.summary()["state_restore"], 3)
        text = pl.preview()
        self.assertIn("Верну 3 проброса из прошлой настройки Kaskad 3 — в iptables их сейчас нет (например, пропали "
                      "после перезагрузки):", text)
        self.assertIn("  tcp :443     → 203.0.113.5:443      Вена        из прошлой настройки Kaskad 3", text)
        self.assertNotIn("Пробросов Kaskad в iptables нет", text)

    def test_apply_brings_them_back_with_names(self):
        env = self.rolled_back_and_rebooted()
        before = [(f["proto"], f["port"], f["to"], f["to_port"], f["name"], f["added"], f["origin"])
                  for f in state.load()["forwards"]]
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertEqual([c["source"] for c in res["imported"]], ["state", "state", "state"])
        st = state.load()
        self.assertEqual(st["engine"], "nft")
        self.assertEqual([(f["proto"], f["port"], f["to"], f["to_port"], f["name"], f["added"], f["origin"])
                          for f in st["forwards"]], before)
        self.assertEqual(env.nft.table["forwards"], {("tcp", 443): ("203.0.113.5", 443),
                                                     ("tcp", 8443): ("198.51.100.7", 443),
                                                     ("udp", 51820): ("198.51.100.7", 51820)})

    def test_port_taken_by_a_live_rule_is_not_taken_back(self):
        env = self.rolled_back_and_rebooted()
        env.ipt.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "8443", "-j", "DNAT",
                    "--to-destination", "10.0.0.8:8443")
        pl = plan.build(scan.collect(env.runner))
        self.assertEqual([c.key_text for c in pl.state_restores], ["tcp:443", "udp:51820"])
        self.assertIn("tcp :8443 → 198.51.100.7:443 — из прошлой настройки Kaskad 3, но порт сейчас забирает другое "
                      "правило (-p tcp -m tcp --dport 8443 -j DNAT --to-destination 10.0.0.8:8443). Не верну.",
                      pl.preview())

    def test_live_kaskad_rules_are_the_newer_truth(self):
        env = self.rolled_back_and_rebooted()
        scripts.pro213_add(env.ipt, "tcp", 9443, 443, "192.0.2.44")       # added on the old engine later
        pl = plan.build(scan.collect(env.runner))
        self.assertEqual(pl.state_restores, [])
        self.assertEqual([(f["proto"], f["port"]) for f in pl.forwards], [("tcp", 9443)])

    def test_a_dead_kaskad_line_in_the_file_does_not_block_the_offer(self):
        env = self.rolled_back_and_rebooted()
        text = fixture("empty", "save.txt")
        text = _insert_after(text, ":POSTROUTING ACCEPT [0:0]\n",
                             "-A PREROUTING -p tcp -m tcp --dport 8080 -j ACCEPT\n"
                             "-A PREROUTING -p tcp -m tcp --dport 8080 -j DNAT --to-destination 192.0.2.80:80\n")
        text = _insert_after(text, ":OUTPUT ACCEPT [0:0]\n",
                             "-A FORWARD -d 192.0.2.80/32 -p tcp -m tcp --dport 80 -m state --state "
                             "NEW,RELATED,ESTABLISHED -j ACCEPT\n")
        self.write("/etc/iptables/rules.v4", text, 0o640)
        pl = plan.build(scan.collect(env.runner))
        self.assertEqual(len(pl.state_restores), 3)               # the 8080 rule never worked: a stale line
        self.assertEqual(len(pl.persisted_stale), 2)

    def test_rules_in_the_file_are_the_newer_truth(self):
        env = self.rolled_back_and_rebooted()
        self.write("/etc/iptables/rules.v4", fixture("pro_213", "rules.v4"), 0o640)
        pl = plan.build(scan.collect(env.runner))
        self.assertEqual(pl.state_restores, [])
        self.assertEqual(len(pl.file_imports), 3)


class FlushedPreroutingOnlyTest(RootCase):
    """Stand, Debian 12: only nat PREROUTING of PRO 2.13 was flushed; its INPUT/FORWARD rules stayed live and
    rules.v4 kept everything. The forwards come from the file — and the live companions go too (they used
    to stay in iptables until a reboot)."""

    def env(self):
        env = Env(self, "pro_213").start()
        live = "\n".join(l for l in fixture("pro_213", "save.txt").splitlines()
                         if not (l.startswith("-A PREROUTING") and "-j DNAT" in l))
        env.ipt.load(live + "\n")
        return env

    def test_plan_takes_the_live_companions(self):
        env = self.env()
        inv = scan.collect(env.runner)
        pl = plan.build(inv)
        self.assertEqual(inv.cands, [])
        self.assertEqual(pl.summary()["persisted_import"], 3)
        gone = [r.text for r in pl.removed_rules]
        self.assertTrue(gone, "the live INPUT/FORWARD companions must be removed")
        self.assertTrue(all("kaskad:" in t and "kaskad:masq" not in t for t in gone), gone)
        self.assertTrue(all(r.table == "filter" for r in pl.removed_rules))
        self.assertIn("Уберу из iptables", pl.preview())

    def test_apply_leaves_no_kaskad_rule_but_masq(self):
        env = self.env()
        res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        live = [l for l in env.ipt.save_text().splitlines() if "kaskad:" in l]
        self.assertEqual([l for l in live if "kaskad:compat" not in l],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])
        got = self.read("/etc/iptables/rules.v4")
        self.assertEqual([l for l in got.splitlines() if "kaskad:" in l],
                         ['-A POSTROUTING -o eth0 -m comment --comment "kaskad:masq" -j MASQUERADE'])
