"""Живая проверка 3.3.2 на e7e67ff, SSH-меню (находки №6–12).

№6  одна фраза ожидания Enter: EN «Press Enter to continue», RU «Нажмите Enter» (ключ C_ENTER), других нет;
№7  SSH 8: «GitHub key (personal access token)» / «Ключ GitHub», не «GitHub PAT», ключ вводится скрытно;
№8  сторож (минутный таймер kaskad-net-watch) — watchdog, сборщик (kaskad-gw-collect, «сторож выходов») — collector;
№9  длинные английские строки экранов SSH не печатаются голым echo: по словам в ширину окна (k3_gw_fold, k3_fold_tty);
№10 главное меню перечитывает язык из настроек при каждой перерисовке (k3_lang_sync);
№11 рамка «Uninstall Kaskad PRO» — по ширине надписи на экране (k3_box_title), правая граница на месте;
№12 17 → 3 → 2: «блок» → «напрямую» — с вопросом цифрами, после сохранения — какой режим теперь.
Экраны целиком (ширина 80, рамка, язык меню, вопрос) — tools/k3_install_test.sh раздел 53 и tools/i18n_h/gwexit_test.sh.
Каталоги берутся из lib/kaskad/i18n/sh (встроенная копия в install.sh пересобирается tools/build_payload.py)."""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import unittest

from tests.test_i18n_terms_sh import bash4, catalog, shquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH = os.path.join(ROOT, "lib", "kaskad", "i18n", "sh")
INSTALL = os.path.join(ROOT, "install.sh")
EN, RU = catalog("en"), catalog("ru")


def code_lines():
    """[(номер, строка)] install.sh без встроенных каталогов (K3_I18N) и без комментариев."""
    out, inside = [], False
    with open(INSTALL, encoding="utf-8") as f:
        for n, ln in enumerate(f, 1):
            if ln.startswith("# >>> K3_I18N"):
                inside = True
            elif ln.startswith("# <<< K3_I18N"):
                inside = False
            elif not inside and not ln.lstrip().startswith("#"):
                out.append((n, ln.rstrip("\n")))
    return out


CODE = code_lines()


def function(name):
    """Тело функции install.sh (от «name() {» до «}» в начале строки)."""
    with open(INSTALL, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"^%s\(\) \{\n(.*?)^\}" % re.escape(name), src, re.S | re.M)
    assert m, name
    return m.group(1)


def width(s):
    return sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in "WF" else 1) for c in s)


class EnterPauseTest(unittest.TestCase):
    """№6: «Press Enter…», «Enter...», «Press Enter to return to the menu…» — было три фразы, стала одна."""

    def test_one_phrase(self):
        self.assertEqual(EN["C_ENTER"], "Press Enter to continue")
        self.assertEqual(RU["C_ENTER"], "Нажмите Enter")
        pause = re.compile(r"^(Press Enter|Нажмите Enter)|^Enter(\.\.\.|…)$|^Enter — (back|назад)$")
        for lang, cat in (("en", EN), ("ru", RU)):
            self.assertEqual(sorted(k for k, v in cat.items() if pause.search(v)), ["C_ENTER"], lang)
        for k in ("FWD_ENTER_MENU", "PING_ENTER_BACK", "EGRESS_ENTER_BACK"):
            self.assertNotIn(k, EN)
            self.assertNotIn(k, RU)

    def test_no_raw_pause_in_code(self):
        bad = [(n, ln.strip()) for n, ln in CODE
               if re.search(r"""read [^;|]*-p ["'](Enter|Press|Нажмите)""", ln)
               or re.search(r"\$\(t (FWD_ENTER_MENU|PING_ENTER_BACK|EGRESS_ENTER_BACK)\)", ln)]
        self.assertEqual(bad, [])


class GitHubKeyTest(unittest.TestCase):
    """№7: SSH 8 — «GitHub key», один раз пояснение «(personal access token)»; по-русски — «Ключ GitHub»."""

    def test_bot_menu(self):
        body = function("bot_menu")
        self.assertNotRegex(body, r"\bPAT\b")
        self.assertNotIn("Update:", body)
        self.assertIn("6) $(t BOT_SSH_I_GHKEY)", body)
        self.assertIn("$(t BOT_SSH_L_GHKEY): ", body)
        self.assertRegex(body, r'6\) echo "\$\(t BOT_SSH_ASK_GHKEY\)"; read -rs -p "> " u; echo;')   # ключ не на экране
        self.assertEqual((EN["BOT_SSH_I_GHKEY"], EN["BOT_SSH_L_GHKEY"]), ("GitHub key (personal access token)", "GitHub key"))
        self.assertEqual((RU["BOT_SSH_I_GHKEY"], RU["BOT_SSH_L_GHKEY"]), ("Ключ GitHub", "Ключ GitHub"))
        self.assertTrue(EN["BOT_SSH_ASK_GHKEY"].startswith("GitHub key"))
        self.assertTrue(RU["BOT_SSH_ASK_GHKEY"].startswith("Ключ GitHub"))

    def test_no_pat_word(self):
        for lang, cat in (("en", EN), ("ru", RU)):
            self.assertEqual(sorted(k for k, v in cat.items() if re.search(r"\bPAT\b", v)), [], lang)
        for k in ("UP_PAT_ASK", "EGRESS_AWG_ASK_PAT", "MIG_PAT_ASK"):
            self.assertIn("ключ GitHub", RU[k], k)


def en_web_and_catalogs():
    """{источник: текст} английских текстов: каталоги bash и Python, словари веба."""
    out = {"sh:" + k: v for k, v in EN.items()}
    for p in glob.glob(os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog", "en", "*.json")):
        with open(p, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                out["json:" + k] = v
    for p in glob.glob(os.path.join(ROOT, "lib", "kaskad", "panel", "static", "**", "*-en.js"), recursive=True):
        with open(p, encoding="utf-8") as f:
            out["js:" + os.path.basename(p)] = f.read()
    return out


class WatchdogCollectorTest(unittest.TestCase):
    """№8: одно слово на понятие. watchdog — минутный сторож (kaskad-net-watch: net check --repair и gw check: пробросы,
    шлюз, перезапуск протоколов); collector — сборщик kaskad-gw-collect (проверка выходов, сессии, ноги AmneziaWG)."""

    def test_glossary(self):
        with open(os.path.join(ROOT, "docs", "k3", "GLOSSARY-en.md"), encoding="utf-8") as f:
            g = f.read()
        self.assertRegex(g, r"\| сборщик \(на узле\) \| \*\*collector\*\* \| служба `kaskad-gw-collect`")
        self.assertRegex(g, r"\| сторож \(минутный\) \| \*\*watchdog\*\* \| таймер `kaskad-net-watch`")

    def test_collector_is_never_a_watchdog(self):
        texts = en_web_and_catalogs()
        self.assertEqual(sorted(k for k, v in texts.items() if re.search(r"(?i)\b(exits?|gateway) watchdog", v)), [])
        self.assertEqual(EN["GWS_RU_COLLECT"], "Collector (exit checks): %s")
        with open(os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog", "en", "gw_ops.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["gw_ops.unit.collect"], "collector")

    def test_ru_watch_of_exits_is_collector_in_en(self):
        # «сторож выходов» по-русски — это сборщик: по-английски collector
        for lang_dir in ("catalog",):
            ru = {}
            for p in glob.glob(os.path.join(ROOT, "lib", "kaskad", "i18n", lang_dir, "ru", "*.json")):
                with open(p, encoding="utf-8") as f:
                    ru.update(json.load(f))
            en = {}
            for p in glob.glob(os.path.join(ROOT, "lib", "kaskad", "i18n", lang_dir, "en", "*.json")):
                with open(p, encoding="utf-8") as f:
                    en.update(json.load(f))
            for k, v in ru.items():
                if re.search(r"(?i)сторож выходов|сборщик", v):
                    self.assertIn("collector", en[k].lower(), k)
        for k, v in RU.items():
            if re.search(r"(?i)сторож выходов|сборщик", v):
                self.assertIn("collector", EN[k].lower(), k)


def raw_long_prints():
    """Места install.sh, где английская строка каталога шире 79 столбцов печатается голым echo/printf (без переноса)."""
    def vis(v):
        v = v.replace("%%", "%")
        v = re.sub(r"%[sd]", "", v)
        return max(width(p) for p in v.split("\\n"))
    long_keys = [k for k, v in EN.items() if vis(v) > 79]
    out = []
    for n, ln in CODE:
        if not re.search(r"\becho -n?e\b|\bprintf\b", ln) or re.search(r"k3_gw_fold|k3_fold_tty|k3_wrap|log_action", ln):
            continue
        for k in long_keys:
            if re.search(r"\$\(t[fn]? %s\b" % k, ln):
                out.append((n, k))
    return out


class WidthTest(unittest.TestCase):
    """№9: в окне 80×24 строки не рвутся посреди слова — длинные строки каталога идут через перенос по словам."""

    # Не экран SSH-меню: тексты для Telegram (бот шлёт их как сообщения).
    NOT_SCREEN = {"BOT_REFUSED_HINT"}

    def test_no_raw_long_lines(self):
        self.assertEqual([x for x in raw_long_prints() if x[1] not in self.NOT_SCREEN], [])

    def test_screens_use_fold(self):
        # экраны из находки: 16 → 4, 17 → 3 (таблица выходов), 17 → 2 → 6 (маршрут), 18 → 6 (MCP)
        self.assertIn("k3_gw_fold_run k3_gw_exits_table", function("k3_gw_exits_menu"))
        self.assertIn('k3_gw_fold "     " "$(printf', function("k3_gw_pick_route"))
        mcp = function("k3_mcp_keys_menu")
        self.assertIn('k3_gw_fold "  " "$(t PNL_MCP_HOW_BASHRC)"', mcp)
        self.assertIn('k3_gw_fold "  " "$(tf PNL_MCP_INTRO', mcp)
        ret = function("k3_return_to_2x")
        for k in ("MIG_RET_GW1", "MIG_RET_FWD", "MIG_RET_CMD", "MIG_RET_UFW"):
            self.assertRegex(ret, r'k3_gw_fold "[ ]*" "[^"]*\$\(tf? %s\b' % k, k)
        # команда ssh-keygen — отдельной строкой целиком (её копируют), на обоих языках
        self.assertEqual(EN["PNL_MCP_ADD1"], "Create a key on your computer:\\n  %s")
        self.assertEqual(RU["PNL_MCP_ADD1"], "Сделайте ключ на своём компьютере:\\n  %s")


def run_lib(script, env_add=None, stdin=b""):
    """bash 4: install.sh --lib, поверх — каталоги из lib/kaskad/i18n/sh, затем script."""
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, "root", "etc", "kaskad"))
        env = dict(os.environ, KASKAD_ROOT=os.path.join(d, "root"), LANG="en_US.UTF-8")
        for k in ("KASKAD_LANG", "LC_ALL", "K3_TERM_COLS"):
            env.pop(k, None)
        env.update(env_add or {})
        head = ('source "$1" --lib >/dev/null 2>&1 || exit 97\n'
                'for f in "$2"/*.sh; do . "$f"; done\n')
        r = subprocess.run([bash4(), "-c", head + script, "x", INSTALL, SH], env=env, input=stdin,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        return r.returncode, r.stdout.decode("utf-8"), d
    finally:
        shutil.rmtree(d, ignore_errors=True)


LONG = ["MIG_RET_GW1", "MIG_RET_FWD", "MIG_RET_CMD", "MIG_RET_UFW", "MIG_RET_UNSAVED", "GWX_EGR_FAIL1", "GWX_EGR_OK_POOL",
        "PNL_MCP_INTRO", "PNL_MCP_HOW_BASHRC", "PNL_NO_CORE", "SITE_NO_CORE", "BOT_SSH_NO_CHAT_ID", "FWD_SSH_CUT",
        "GWX_ST_NOCORE", "GWU_FAIL_BLOCK", "EGRESS_SUB_CERT", "UP_BR_DEF", "I_LOST"]


@unittest.skipUnless(bash4(), "нужен bash 4+")
class FoldScreensTest(unittest.TestCase):
    """№9: строки экранов по-английски через k3_gw_fold / k3_fold_tty в окне 80 — не шире 80, слова не рвутся."""

    def sample(self, k):
        return EN[k].replace("%%", "%").replace("%d", "7").replace("%s", "X", 1).replace("%s", "")

    def test_fold_80(self):
        script = ""
        for i, k in enumerate(LONG):
            script += "echo '@@ %d'; K3_TERM_COLS=80 k3_fold_tty '  ' %s\n" % (i, shquote(self.sample(k)))
        rc, out, _d = run_lib(script)
        self.assertEqual(rc, 0, out[-2000:])
        blocks = re.split(r"^@@ (\d+)\n", out, flags=re.M)[1:]
        self.assertEqual(len(blocks), 2 * len(LONG), out[-2000:])
        for j in range(0, len(blocks), 2):
            src, got = self.sample(LONG[int(blocks[j])]), blocks[j + 1]
            lines = [ln for ln in got.split("\n") if ln.strip()]
            if width(src) > 79:
                self.assertGreater(len(lines), 1, (src, got))       # длинная — перенесена
            for ln in lines:
                self.assertLessEqual(width(ln), 80, ln)
            self.assertEqual(" ".join(got.split()), " ".join(src.split()))   # ни одно слово не разорвано и не потеряно

    def test_fold_tty_without_terminal_is_as_is(self):
        # без терминала и без K3_TERM_COLS (ядро зовёт gokaskad: панель, бот, мастер) — строка как есть, одной строкой
        s = self.sample("EGRESS_SUB_CERT")
        rc, out, _d = run_lib("k3_fold_tty '  ' %s | cat" % shquote(s))
        self.assertEqual((rc, out), (0, s + "\n"))


@unittest.skipUnless(bash4(), "нужен bash 4+")
class BoxTitleTest(unittest.TestCase):
    """№11: рамка удаления — строка с ⚠ той же ширины, что верх и низ (64), на обоих языках."""

    def test_uninstall_frame(self):
        self.assertIn('k3_box_title "$RED" "$(t UNINST_FRAME)"', function("full_uninstall"))
        for lang in ("ru", "en"):
            self.assertEqual(EN["UNINST_FRAME"] if lang == "en" else RU["UNINST_FRAME"],
                             (EN if lang == "en" else RU)["UNINST_FRAME"].strip())   # без отступов-подгонок в каталоге
            for loc in ("en_US.UTF-8", "C"):
                rc, out, _d = run_lib('k3_lang_set %s; RED=""; NC=""; k3_box_title "" "$(t UNINST_FRAME)"' % lang,
                                      {"LANG": loc})
                self.assertEqual(rc, 0, out)
                lines = out.rstrip("\n").split("\n")
                self.assertEqual(len(lines), 3, out)
                self.assertEqual([width(ln) for ln in lines], [64, 64, 64], (lang, loc, out))
                inner = lines[1][1:-1]
                lp, rp = len(inner) - len(inner.lstrip(" ")), len(inner) - len(inner.rstrip(" "))
                self.assertLessEqual(abs(lp - rp), 1, (lang, loc, out))


@unittest.skipUnless(bash4(), "нужен bash 4+")
class LangSyncTest(unittest.TestCase):
    """№10: открытое меню подхватывает язык, сменённый в панели/боте (строка KASKAD_LANG в настройках)."""

    SCRIPT = r'''
conf() { printf 'KASKAD_LANG="%s"\n' "$1" > "$KASKAD_CONF"; }
conf ru; K3_ENV_LANG="${E:-}"; k3_lang_load
echo "start $K3_LANG"
k3_lang_sync; echo "same rc=$? $K3_LANG"
conf en; k3_lang_sync; echo "en rc=$? $K3_LANG $KASKAD_LANG $(t MM_I0)"
k3_lang_sync; echo "again rc=$? $K3_LANG"
conf ru; k3_lang_sync; echo "ru rc=$? $K3_LANG $(t MM_I0)"
'''

    def test_sync_from_settings(self):
        rc, out, _d = run_lib(self.SCRIPT)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.split("\n")[:5], ["start ru", "same rc=1 ru", "en rc=0 en en Quit", "again rc=1 en",
                                               "ru rc=0 ru Выход"])

    def test_env_wins_until_settings_change(self):
        rc, out, _d = run_lib(self.SCRIPT, {"E": "en"})
        self.assertEqual(rc, 0, out)
        # окружение en, в настройках ru: язык — en; строка в настройках стала en (тот же) — en; стала ru — ru
        self.assertEqual(out.split("\n")[:5], ["start en", "same rc=1 en", "en rc=1 en en Quit", "again rc=1 en",
                                               "ru rc=0 ru Выход"])

    def test_main_menu_syncs_before_each_draw(self):
        body = function("show_menu")
        self.assertLess(body.index("k3_lang_sync && K3_HEAD_TS=0"), body.index("k3_menu_draw"))
        self.assertIn('K3_LANG_CONF_SEEN="$l"', function("k3_lang_menu"))


class FailDirectConfirmTest(unittest.TestCase):
    """№12: 17 → 3 → 2 — ослабление защиты (блок → напрямую) только после «1) Да», после сохранения — новый режим."""

    def test_code(self):
        body = function("k3_gw_exits_menu")
        i_q = body.index('k3_choose_yes "$(t GWX_FAIL_DIRECT_Q)" 2 "$(t GWX_FAIL_DIRECT_YES)" "$(t GWX_FAIL_DIRECT_NO)"')
        self.assertLess(i_q, body.index('k3_gw route "$via" --fail "$nf"'))
        self.assertIn("GWX_FAIL_SAVED", body)
        self.assertNotIn("GWX_SAVED)", body)

    def test_texts(self):
        self.assertEqual(EN["GWX_FAIL_SAVED"] % EN["GWX_W_DIRECT"], "[OK] Saved: on failure — direct.")
        self.assertEqual(EN["GWX_FAIL_SAVED"] % EN["GWX_W_BLOCK"], "[OK] Saved: on failure — block.")
        self.assertEqual(RU["GWX_FAIL_SAVED"] % RU["GWX_W_DIRECT"], "[OK] Сохранено: при отказе — напрямую.")
        self.assertEqual(EN["GWX_FAIL_DIRECT_YES"], "Yes, switch to “direct”")
        self.assertEqual(RU["GWX_FAIL_DIRECT_YES"], "Да, переключить на «напрямую»")
        self.assertIn("this server’s address", EN["GWX_FAIL_DIRECT_WARN"])
        self.assertIn("с адреса этого сервера", RU["GWX_FAIL_DIRECT_WARN"])


if __name__ == "__main__":
    unittest.main()
