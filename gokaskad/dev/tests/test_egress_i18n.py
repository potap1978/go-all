"""«Прокси для API Telegram» (install.sh: egress_*, awg_*, живая панель, пункт 8 → 7) на двух языках, 3.3.2.

Каталог lib/kaskad/i18n/sh/egress.{ru,en}.sh: те же ключи и подстановки printf, каждый ключ вызывается и каждый
вызов есть в каталоге; в переведённых функциях нет русских строк для человека; при K3_LANG=en панель, подсказка
вставки и предупреждения AmneziaWG — без кириллицы, при ru — прежние строки; машинные строки @K3:egress_… — только в
режимах веб-панели (--egress-add/--egress-del/--egress-replace), не в меню."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(ROOT, "install.sh")
CAT = os.path.join(ROOT, "lib", "kaskad", "i18n", "sh", "egress.%s.sh")
CYR = re.compile("[А-Яа-яЁё]")
PH = re.compile(r"%[-0-9.]*[sdi]")
# Не переводились: вызовов нет (мёртвый код) — egress_import(_now), egress_list, egress_status.
DEAD = {"egress_import", "egress_import_now", "egress_list", "egress_status"}
# Кириллица, которая человеку на экран не идёт: словарь ru встроенного разборщика (язык — KASKAD_LANG), --why для
# ядра (запись движка AmneziaWG, gw), комментарий в созданном скрипте пробы.
KEEP = {"egress_ensure_parser", "k3_awg_first_leg", "egress_probe_script"}
VLESS = ("vless://11111111-2222-3333-4444-555555555555@198.51.100.1:443?security=reality&type=tcp"
         "&pbk=Y_XlU6E0f4Ua1nqO9nO7Xn8xTkVUPkiOJMeDHqhJmmw&sid=ab12&sni=a.example#Riga")


def catalog(lang):
    out = {}
    with open(CAT % lang, encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("#") or not ln.strip():
                continue
            m = re.match(r"^M_%s_(EGRESS_[A-Za-z0-9_]+)='([^']*)'$" % lang, ln.rstrip("\n"))
            assert m, ln
            assert m.group(1) not in out, m.group(1)
            out[m.group(1)] = m.group(2)
    return out


def source():
    with open(INSTALL, encoding="utf-8") as f:
        return f.read()


def bash4():
    b = shutil.which("bash")
    if not b:
        return None
    r = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], stdout=subprocess.PIPE, timeout=30)
    return b if r.stdout.strip().isdigit() and int(r.stdout) >= 4 else None


class CatalogTest(unittest.TestCase):
    def test_same_keys_and_placeholders(self):
        ru, en = catalog("ru"), catalog("en")
        self.assertEqual(sorted(ru), sorted(en))
        for k in ru:
            self.assertEqual(PH.findall(ru[k].replace("%%", "")), PH.findall(en[k].replace("%%", "")), k)
            self.assertIsNone(CYR.search(en[k]), k)

    def test_every_key_called_and_every_call_known(self):
        body = re.sub(r"# >>> K3_I18N.*?# <<< K3_I18N", "", source(), flags=re.S)
        calls = re.findall(r"\b(t|tf)\s+(EGRESS_[A-Za-z0-9_]+)", body)
        ru = catalog("ru")
        self.assertEqual(sorted(set(k for _, k in calls)), sorted(ru))
        for how, k in calls:       # tf — формат с подстановками, t — строка без них
            self.assertEqual(how == "tf", bool(PH.findall(ru[k].replace("%%", ""))), k)

    def test_no_russian_left_in_the_functions(self):
        s = source()
        lines = s.split("\n")
        left = []
        for m in re.finditer(r"^((?:egress|awg|_egr|k3_networkd|k3_gw_pool_changed|k3_awg_first_leg)[A-Za-z0-9_]*)"
                             r"\s*\(\)\s*\{", s, re.M):
            name = m.group(1)
            if name in DEAD or name in KEEP:
                continue
            i = s.count("\n", 0, m.start())
            depth, j, heredoc = 0, i, None
            while j < len(lines):
                ln = lines[j]
                j += 1
                if heredoc:
                    heredoc = None if ln.strip() == heredoc else heredoc
                    continue
                code = "" if ln.lstrip().startswith("#") else re.sub(r"(^|\s)#.*$", "", ln)
                if CYR.search(code):
                    left.append("%s:%d %s" % (name, j, ln.strip()[:90]))
                hd = re.search(r"<<-?\s*'?([A-Z]+)'?", code)
                heredoc = hd.group(1) if hd else None
                depth += code.count("{") - code.count("}")
                if depth <= 0:
                    break
        self.assertEqual(left, [])


@unittest.skipUnless(bash4(), "нужен bash 4+")
class ScreenTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "root", "etc", "kaskad", "egress"))
        os.makedirs(os.path.join(self.dir, "bin"))
        for name, text in (("systemctl", "exit 3"), ("tput", "exit 1"), ("curl", "exit 7")):
            p = os.path.join(self.dir, "bin", name)
            with open(p, "w") as f:
                f.write("#!/bin/sh\n%s\n" % text)
            os.chmod(p, 0o755)
        with open(os.path.join(self.dir, "root", "etc", "kaskad", "egress", "upstream.txt"), "w") as f:
            f.write(VLESS + "\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_sh(self, lang, script, stdin=""):
        env = dict(os.environ, KASKAD_ROOT=os.path.join(self.dir, "root"), LANG="en_US.UTF-8",
                   PATH=os.path.join(self.dir, "bin") + os.pathsep + os.environ.get("PATH", ""),
                   EGRESS_POOL_LOCK=os.path.join(self.dir, "root", "run", "egress-pool.lock"))
        for k in ("KASKAD_LANG", "NO_COLOR", "LC_ALL", "GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PAT"):
            env.pop(k, None)
        head = ('source "$1" --lib >/dev/null 2>&1 || exit 97\n'
                'KASKAD_LOG=/dev/null; log_action() { :; }; k3_gw_pool_changed() { :; }\n'
                + ('k3_lang_set en\n' if lang == "en" else ""))
        r = subprocess.run([bash4(), "-c", head + script, "x", INSTALL], input=stdin.encode(), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        return r.returncode, re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", r.stdout.decode("utf-8"))

    SCREEN = ('egress_ensure_parser\n'
              'EGR_ST=on; EGR_OPING=120; EGR_ACTIVE=1; EGR_L30="0.5%"; EGR_PING[1]=35; EGR_WARN=""\n'
              '_egr_paint 1 1; _egr_paint 1 0; egress_paste_prompt\n'
              'egress_awg_info() { printf "1\\t2.0\\tlost3x\\n2\\t3.1\\tok\\n3\\t3.1\\tloss226\\n"; }\n'
              'egress_awg_is3x() { return 1; }; egress_awg_warnings; egress_awg_is3x() { return 0; }; egress_awg_warnings\n'
              'printf "\\n" | egress_add_interactive\n')

    def test_english_screen_has_no_russian(self):
        rc, out = self.run_sh("en", self.SCREEN)
        self.assertEqual(rc, 0, out)
        self.assertIsNone(CYR.search(out), out)
        for want in ("  Telegram API proxy (bot)", "Status: ● working", "Traffic loss: 30s 0.5% · 5m",
                     " F2  Add key   F3  Delete key", "F10  Back", "Navigation: ↑/↓ · F4 Pause · F5 Refresh",
                     "Navigation: ↑/↓ · F4 Resume", "    subscription       — an http(s):// link",
                     "Paste the key/config and press Enter.", "Key #1: AmneziaWG 3.x key added by an old Kaskad",
                     "Key #2: AmneziaWG 3.1 won’t connect", "loses ≈7 % of packets", "  Empty — canceled."):
            self.assertIn(want, out)

    def test_russian_screen_as_before(self):
        rc, out = self.run_sh("ru", self.SCREEN)
        self.assertEqual(rc, 0, out)
        for want in ("  Прокси для API Telegram (бот)", "  Статус: ● работает   Telegram API: 120 ms",
                     "  Потери трафика: 30с 0.5% · 5м — · 15м — · 60м —",
                     "  Ключи в пуле: (пинг до узла)",
                     "   F2  Добавить ключ   F3  Удалить ключ   F6  Выбрать руками   F7  Автоматический выбор   F10  Выход",
                     "  Навигация: ↑/↓ · F4 Продолжить · F5 Обновить",
                     "    vless://…          — один ключ VPN: VLESS, Hysteria2, Shadowsocks, Trojan или VMess\n"
                     "    подписка           — ссылка http(s):// или base64-текст подписки\n",
                     "  Вставьте ключ/конфиг и нажмите Enter. Многострочный .conf — просто вставьте целиком.",
                     "Ключ #3: AmneziaWG 3.1 с RandomTrailers при разных S1–S4 и диапазонах H теряет ≈7 % пакетов",
                     "  Пусто — отменено."):
            self.assertIn(want, out)

    def test_marks_only_for_the_panel(self):
        # метки — только когда их просит вызывающий (K3_MARKS=1: gw/ops.py), и только в режимах веб-панели
        rc, out = self.run_sh("en", 'egress_panel_add; echo "rc=$?"', stdin="  \n")
        self.assertNotIn("@K3:", out)
        rc, out = self.run_sh("en", 'K3_MARKS=1; egress_panel_add; echo "rc=$?"', stdin="  \n")
        self.assertIn("Empty: paste a key.\n@K3:egress_fail:empty\nrc=1", out)
        rc, out = self.run_sh("en", 'K3_MARKS=1; egress_panel_del 1 zz; echo "rc=$?"; egress_panel_replace nope </dev/null; '
                                    'echo "rc=$?"')
        self.assertIn("@K3:egress_fail:bad_ref\nrc=1", out)
        self.assertIn("Wrong pool fingerprint.\n@K3:egress_fail:bad_hash\nrc=1", out)
        # откат после неудачной вставки и замены — с режимом (ядро: gw/ops.py, «Ключ не добавлен» / «не заменён»)
        two = VLESS.replace("#Riga", "#B").replace("198.51.100.1", "198.51.100.2")
        rc, out = self.run_sh("en", 'egress_ensure_parser; egress_rebuild() { return 1; }; K3_MARKS=1; '
                                    'egress_panel_add; echo "rc=$?"', stdin=two + "\n")
        self.assertIn("@K3:egress_fail:rolled_back:add\n", out)
        rc, out = self.run_sh("en", 'egress_ensure_parser; egress_rebuild() { return 1; }; K3_MARKS=1; '
                                    'egress_panel_replace "$(egress_pool_hash)"; echo "rc=$?"', stdin=two + "\n")
        self.assertIn("@K3:egress_fail:rolled_back:replace\n", out)
        # меню и шлюз (k3_gw_egr_words читает вывод добавления) — без машинных строк
        rc, out = self.run_sh("en", 'K3_MARKS=1; EGRESS_MAX=1 egress_add_key "%s"; egress_remove_key x; true'
                              % VLESS.replace("#Riga", "#B"))
        self.assertNotIn("@K3:", out)
        self.assertIn("Wrong key number.", out)


if __name__ == "__main__":
    unittest.main()
