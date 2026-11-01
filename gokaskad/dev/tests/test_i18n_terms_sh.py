"""Термины глоссария в английских текстах SSH-меню и установщика (lib/kaskad/i18n/sh/*.en.sh), критика 3.3.2.

docs/k3/GLOSSARY-en.md и «Решения по терминам после критики» (k3/handoff/I18N-3.3.2.md): сервер — server (не node);
пробросы — Forwards, правила маршрутизации — Routing rules, «Rules» без уточнения — нет; режим без модуля — userspace
(не program); нет бинарей — «no AmneziaWG core» (не engine); Access from the internet; Client address; Settings backup;
GitHub key (не PAT); en-US; многоточие «…» и типографский апостроф; выход из меню — Quit/Back, «Exit» — только VPN-выход.
Плюс перенос строк меню (находка 28): путь «17 → 1 → 5» не рвётся у стрелки ни в k3_gw_fold, ни в k3_wrap,
и runner tools/i18n_h/run_section.sh гоняет раздел 49 отдельно (находка 35: HEAD_BASE)."""
import glob
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH = os.path.join(ROOT, "lib", "kaskad", "i18n", "sh")
INSTALL = os.path.join(ROOT, "install.sh")


def catalog(lang="en"):
    """{KEY: text} всех областей; '\\'' — одиночная кавычка кода."""
    out = {}
    for p in sorted(glob.glob(os.path.join(SH, "*.%s.sh" % lang))):
        with open(p, encoding="utf-8") as f:
            for ln in f:
                m = re.match(r"^M_%s_([A-Za-z0-9_]+)='(.*)'$" % lang, ln.rstrip("\n"))
                if m:
                    out[m.group(1)] = m.group(2).replace("'\\''", "'")
    return out


EN = catalog()


def hits(rx, allow=()):
    """Ключи, где встречается rx, кроме белого списка allow."""
    return sorted(k for k, v in EN.items() if re.search(rx, v) and k not in allow)


class TermsTest(unittest.TestCase):
    def test_catalog_is_read(self):
        self.assertGreater(len(EN), 1500)

    def test_server_not_node(self):
        self.assertEqual(hits(r"\b[Nn]odes?\b"), [])

    def test_rules_only_qualified(self):
        # «rule» допустимо: «routing rule(s)», «rules profile», и технические правила файрвола в строке, где назван
        # iptables / nftables / UFW (это правила netfilter, не пункт меню). Остальное — пробросы (Forwards).
        left = []
        for k, v in EN.items():
            if re.search(r"iptables|nftables|\bUFW\b|\bufw\b", v):
                continue
            for m in re.finditer(r"\b[Rr]ules?\b", v):
                before, after = v[:m.start()], v[m.end():]
                if re.search(r"[Rr]outing $", before) or after.startswith(" profile"):
                    continue
                left.append(k)
        self.assertEqual(sorted(set(left)), [])

    def test_forward_menu_items(self):
        self.assertEqual((EN["MM_I4"], EN["MM_I5"], EN["MM_I9"]), ("Custom forward", "Forwards", "Delete forward"))
        self.assertEqual(EN["RULES_DELETED"], "[OK] Forward deleted.")
        self.assertTrue(EN["GW_M_RULES"].startswith("Routing rules “Russian sites direct”"))
        self.assertNotIn("Cascades", EN["BOT_HELP"])

    def test_forward_not_rule_in_code_and_journal(self):
        # T-M6-332 №5: «[SUCCESS] Custom Rule is set up.», журнал «ADD rule:/DELETE rule:» на английском сервере — имя
        # проброса и строки журнала берутся из каталога (RU — как в 2.x/3.3.1, EN — «forward»)
        self.assertEqual(EN["FWD_CUSTOM_NAME"], "Custom forward")
        self.assertEqual(catalog("ru")["FWD_CUSTOM_NAME"], "Custom Rule")
        for k in ("LOG_FWD_ADD", "LOG_FWD_ADD_FAIL", "LOG_FWD_DEL", "LOG_FWD_FLUSH", "LOG_FWD_ADOPT"):
            self.assertRegex(EN[k], r"\bforwards?\b", k)
            self.assertNotRegex(EN[k], r"\brules?\b", k)
            self.assertRegex(catalog("ru")["%s" % k], r"\brules?\b", k)          # RU — байт в байт 3.3.1
        with open(INSTALL, encoding="utf-8") as f:
            code = [ln for ln in f if not ln.startswith("M_")]
        left = [ln.strip() for ln in code if re.search(r"Custom Rule|log_action \"(ADD|DELETE|FLUSH|ADOPT)[^$]*rule|"
                                                       r"log_action \"Self-update", ln)]
        self.assertEqual(left, [])

    def test_owner_is_the_bot_or_server_owner(self):
        # T-M6-332 №15: «owner» — только владелец бота/сервера; пользователь устройства — user (24.10, было person), у пользователя — default
        self.assertEqual(hits(r"\b[Oo]wner", allow=("BOT_NOT_BOUND", "GWD_OWNER")), [])
        self.assertEqual(EN["GWR_T_1_DEVICE"], "Same as user (or default)")
        self.assertEqual(EN["GWR_T_1_USER"], "Same as default")
        self.assertEqual((EN["GWR_P_INHERIT"], EN["GWR_P_INHERIT_USER"]), ("same as the user", "same as default"))
        self.assertEqual(catalog("ru")["GWR_P_INHERIT_USER"], catalog("ru")["GWR_P_INHERIT"])     # RU — как в 3.3.1

    def test_self_update_journal(self):
        # T-M6-332 №22: «Self-update -> v3.3.2 (…)» — на языке сервера; RU — по-русски
        ru = catalog("ru")
        self.assertEqual(tuple(re.findall(r"%s", ru["LOG_SELF_UPDATE"])), ("%s",) * 3)
        self.assertNotRegex(ru["LOG_SELF_UPDATE"] + ru["LOG_SELF_UPDATE_RB"] + ru["LOG_SELF_UPDATE_NONE"], r"[A-Za-z]{4,}")
        self.assertTrue(EN["LOG_SELF_UPDATE"].startswith("Self-update -> v"))

    def test_userspace_not_program(self):
        # «program» — сам Kaskad (обновить программу, программа 2.x); рядом с amneziawg-go / модулем ядра — userspace
        self.assertEqual(hits(r"(?i)\bprogram\b.*(amneziawg-go|userspace|kernel module)|"
                              r"(amneziawg-go|userspace|kernel module).*\bprogram\b"), [])

    def test_core_not_engine(self):
        # engine — только выбор режима (module/userspace) и движок пробросов; нет бинарей — «no AmneziaWG core»
        self.assertEqual(hits(r"\bno (AmneziaWG )?engine\b"), [])
        self.assertIn("no AmneziaWG core", EN["GWX_ST_NOCORE"])

    def test_kaskad_core_wording(self):
        # калька «на нём работает панель» → «… — the panel needs it»
        self.assertEqual(hits(r"it runs (the|Quick)|runs on it"), [])
        for k in ("PNL_NO_CORE", "SITE_NO_CORE", "GW_NO_CORE", "QS_NOCORE", "BOT_SSH_SW_NO_LIB"):
            self.assertRegex(EN[k], r"The Kaskad 3 core is unavailable \(%s\) — .* needs? it\.", k)

    def test_panel_and_address_terms(self):
        self.assertEqual(hits(r"(?i)internet access"), [])
        self.assertEqual(hits(r"(?i)address(es)? for (devices|clients)|server address for"), [])
        self.assertEqual(EN["MM_H_HOST"], "Client address:")
        self.assertTrue(EN["PNL_M_PUB_OFF"].startswith("Access from the internet:"))
        self.assertEqual(hits(r"Domain & site"), [])

    def test_settings_backup_name(self):
        self.assertEqual(EN["BAK_TITLE"], "Settings backup")
        self.assertTrue(EN["MIG_I6"].startswith("6) Settings backup — "))
        self.assertEqual(hits(r"(?i)backup and (move|transfer)"), [])

    def test_network_interface(self):
        self.assertEqual(hits(r"(?i)network card"), [])

    def test_github_key_not_pat(self):
        self.assertEqual(hits(r"\bPAT\b"), [])
        for k in ("UP_PAT_ASK", "EGRESS_AWG_ASK_PAT", "MIG_PAT_ASK"):
            self.assertIn("GitHub key (personal access token", EN[k], k)

    def test_users_not_people(self):
        # владелец 24.10: «заменим везде человек на пользователя» — было test_people_not_users (глоссарий 3.3.2)
        self.assertEqual(hits(r"\b[Pp]ersons?\b|\b[Pp]eople\b"), [])

    def test_delete_key(self):
        self.assertEqual(EN["EGRESS_F_DEL"], "Delete key")
        self.assertEqual(hits(r"\bRemove key"), [])

    def test_exit_only_for_vpn(self):
        # «Exit» как «выйти из экрана» путают с VPN-выходом: главное меню — Quit, подэкраны — Back/Quit
        self.assertEqual(EN["MM_I0"], "Quit")
        self.assertEqual(EN["EGRESS_F_EXIT"], "Back")
        self.assertEqual(EN["GUIDE_EXIT"], "Quit")
        self.assertEqual(hits(r"^Exit$|0 — exit\b"), [])

    def test_us_spelling(self):
        self.assertEqual(hits(r"(?i)\b(neighbour\w*|licence\w*|grey|colour\w*|cancell(ed|ing)|behaviour|favour\w*|"
                              r"catalogue|organis\w+|recognis\w+)\b"), [])

    def test_platforms(self):
        self.assertEqual(EN["GW_PLAT_IOS"], "iPhone / iPad")
        self.assertEqual(hits(r"iPhone or iPad|\bMac OS\b|\bMacOS\b"), [])

    def test_typography(self):
        # многоточие — «…», апостроф в словах — ’ (одиночная кавычка — только код: journalctl -u 'kaskad-gw-*')
        self.assertEqual(hits(r"\.\.\."), [])
        self.assertEqual(hits(r"[A-Za-z]'[A-Za-z]"), [])

    def test_placeholder_page_one_word(self):
        self.assertEqual(EN["SITE_SIMPLE_PAGE"], "placeholder page")
        self.assertEqual(EN["SITET_SIMPLE_PAGE"], "placeholder page")
        self.assertEqual(hits(r"(?i)\b(simple|stub) page"), [])

    def test_grammar_fixes(self):
        self.assertIn("%s will go back", EN["SITE_CLR_USE"])
        self.assertIn("rather than going direct", EN["GWX_STEP_VPN_REC2"])
        self.assertEqual(EN["GWV_LIST_INT"], "international sites")

    def test_guide_pages_fit_78_columns(self):
        for k, v in EN.items():
            if re.fullmatch(r"GUIDE_P\d+", k):
                for ln in v.replace("%s", "").split("\\n"):
                    self.assertLessEqual(len(ln), 78, (k, ln))


def bash4():
    b = shutil.which("bash")
    if not b:
        return None
    r = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], stdout=subprocess.PIPE, timeout=30)
    return b if r.stdout.strip().isdigit() and int(r.stdout) >= 4 else None


PATHS = [
    "AmneziaWG acceleration: not loaded — amneziawg-go (userspace) is running (17 → 1 → AmneziaWG engine).",
    "! The site responds, but the certificate expires soon (5 days left). Renew: 19 → 2.",
    "New panel address: https://vpn.example.com/app-AbCdEfGh/. In full: 19 → 8 → 1.",
    "Install later: “Gateway → Protocols → L2TP/IPsec → Install” (item 17), when the system finishes updating.",
]


WIDTHS = range(50, 90, 3)        # окна SSH-клиента; уже 50 путь в кавычках («“Gateway → … → Install”») длиннее строки


@unittest.skipUnless(bash4(), "нужен bash 4+")
class FoldTest(unittest.TestCase):
    """Находка 28: «(17 → 1 →⏎AmneziaWG engine)», «Renew: 19 →⏎2.» — перенос рвал путь меню у стрелки."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "root", "etc", "kaskad"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_all(self, fold):
        """Все строки PATHS во всех ширинах окна одним запуском bash: блоки «@@ <ширина> <номер>»."""
        script = ""
        for cols in WIDTHS:
            for i, s in enumerate(PATHS):
                script += "echo '@@ %d %d'; K3_TERM_COLS=%d; %s\n" % (cols, i, cols, fold % shquote(s))
        env = dict(os.environ, KASKAD_ROOT=os.path.join(self.dir, "root"), LANG="en_US.UTF-8")
        for k in ("KASKAD_LANG", "LC_ALL", "K3_TERM_COLS"):
            env.pop(k, None)
        head = 'source "$1" --lib >/dev/null 2>&1 || exit 97\n'
        r = subprocess.run([bash4(), "-c", head + script, "x", INSTALL], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=300)
        out = r.stdout.decode("utf-8")
        self.assertEqual(r.returncode, 0, out[-2000:])
        blocks = re.split(r"^@@ (\d+) (\d+)\n", out, flags=re.M)[1:]
        self.assertEqual(len(blocks), 3 * len(WIDTHS) * len(PATHS))
        for j in range(0, len(blocks), 3):
            yield int(blocks[j]), PATHS[int(blocks[j + 1])], blocks[j + 2]

    def check(self, cols, src, out):
        lines = [ln for ln in out.split("\n") if ln.strip()]
        for ln in lines:
            self.assertLessEqual(len(ln), cols, (cols, ln))
            self.assertFalse(ln.rstrip().endswith("→"), (cols, ln))
            self.assertFalse(ln.lstrip().startswith("→"), (cols, ln))
        for p in ("17 → 1 → AmneziaWG", "19 → 2.", "19 → 8 → 1."):
            if p in src:                         # путь — на одной строке
                self.assertTrue(any(p in ln for ln in lines), (cols, p, lines))
        self.assertEqual(" ".join(out.split()), " ".join(src.split()), cols)   # текст не потерян

    def test_gw_fold_keeps_menu_paths(self):
        for cols, src, out in self.run_all('k3_gw_fold "  " %s'):
            self.check(cols, src, out)

    def test_wrap_keeps_menu_paths(self):
        for cols, src, out in self.run_all("printf '%%s\\n' %s | k3_wrap"):
            self.check(cols, src, out)


def shquote(s):
    return "'" + s.replace("'", "'\\''") + "'"


@unittest.skipUnless(bash4() and shutil.which("jq") and os.environ.get("K3_SLOW_TESTS") != "0", "нужны bash 4+ и jq")
class RunSectionTest(unittest.TestCase):
    """Находка 35: run_section.sh 49 падал «HEAD_BASE: unbound variable» (многострочное присваивание раздела 44)."""

    def test_section_49_alone(self):
        env = dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
        r = subprocess.run(["bash", os.path.join(ROOT, "tools", "i18n_h", "run_section.sh"), "49"], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        out = r.stdout.decode("utf-8", "replace")
        self.assertNotIn("unbound variable", out)
        self.assertIn("ВСЁ ЗЕЛЕНО", out)
        self.assertEqual(r.returncode, 0, out[-2000:])


if __name__ == "__main__":
    unittest.main()
