"""3.3.7.2, безопасность (REV-3371-2 Б-1, Б-2, П-1): файлы 2.x из копии .kbk.

Б-1: файлы мониторов (/etc/kaskad/monitors/<ip>.conf) восстанавливались как есть, а bash 2.x читал их через ``source``
(меню мониторинга, бот на bash, служба kaskad-monitor) — строка «touch …», «$(…)», «;» в чужой копии шла бы от root.
Теперь: восстановление разбирает файл (адрес, числа) и пишет его своим форматом; bash читает разбором (k3_mon_load),
не ``source``; после обновления лежащие файлы переписываются проверенными (``kaskadctl update files``).
Б-2: суррогат в JSON копии не обрывает восстановление на полпути (вся копия — до применения); файл имён серверов,
привезённый в 3.3.5–3.3.7, очищается при обновлении. П-1: поля файла имён не обрезаются до 64 (предел 255).

Проверки «файл-метка не создан» на коде до правки падают: метка появляется."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from kaskad import backup, paths, util
from kaskad.bot import aliases, monitors
from kaskad.errors import KaskadError
from tests.fakes_gw import INSTALL_SH
from tests.helpers import RootCase
from tests.test_3351_cmdinj import function
from tests.test_backup import PW, BackupCase

GOOD = 'MON_IP="203.0.113.5"\nMON_INTERVAL=60\nMON_THRESHOLD=100\nMON_COOLDOWN=300\n'


def evil(mark):
    """Файлы мониторов «чужой копии»: команда строкой, в значении, после «;», после перевода строки в кавычках."""
    return {
        "198.51.100.1.conf": 'MON_IP="198.51.100.1"\nMON_INTERVAL=60\nMON_THRESHOLD=100\ntouch "%s-1"\n' % mark,
        "198.51.100.2.conf": 'MON_IP="198.51.100.2"\nMON_INTERVAL=$(touch "%s-2")\nMON_THRESHOLD=100\n' % mark,
        "198.51.100.3.conf": 'MON_IP="198.51.100.3"; touch "%s-3"\nMON_INTERVAL=60\nMON_THRESHOLD=100\n' % mark,
        "198.51.100.4.conf": 'MON_IP="198.51.100.4\ntouch %s-4\n"\nMON_INTERVAL=60\nMON_THRESHOLD=100\n' % mark,
        "198.51.100.5.conf": 'MON_IP="198.51.100.5"\nMON_INTERVAL="a[$(touch %s-5)]"\nMON_THRESHOLD=100\n' % mark,
        "198.51.100.6.conf": 'MON_IP=`touch %s-6`\nMON_INTERVAL=60\nMON_THRESHOLD=100\n' % mark,
    }


def bash_load(path):
    """k3_mon_load из install.sh над файлом -> (rc, «IP INTERVAL THRESHOLD COOLDOWN»)."""
    src = function("validate_ip") + function("k3_mon_load")
    r = subprocess.run(["bash", "-c", src + '\nk3_mon_load "$1"; rc=$?; echo "$rc|$MON_IP $MON_INTERVAL $MON_THRESHOLD '
                        '$MON_COOLDOWN"', "_", path], capture_output=True, text=True, timeout=30)
    rc, _, vals = r.stdout.strip().rpartition("\n")[2].partition("|")
    return int(rc), vals


def marks(d):
    return sorted(n for n in os.listdir(d) if n.startswith("PWNED"))


class KbkMonitorsTest(BackupCase):
    def setUp(self):
        super().setUp()
        self.scratch = tempfile.mkdtemp(prefix="k3-3372-")
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.mark = os.path.join(self.scratch, "PWNED")

    def _copy_with(self, files):
        self.old_node()
        util.ensure_dir(paths.monitors(), 0o700)
        for n, t in files.items():
            util.atomic_write(os.path.join(paths.monitors(), n), t, 0o600)
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        shutil.rmtree(paths.monitors(), True)
        return text

    def test_monitors_of_a_copy_come_parsed_never_as_they_were(self):
        files = dict(evil(self.mark), **{"203.0.113.5.conf": GOOD})
        text = self._copy_with(files)
        res = backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        got = sorted(os.listdir(paths.monitors()))
        # годный — как был; где адрес и числа читаются (лишняя строка, «; …» после кавычек) — только они, нашим
        # форматом; где команда в самом значении — не перенесён, сказано
        self.assertEqual(got, ["198.51.100.1.conf", "198.51.100.3.conf", "198.51.100.4.conf", "203.0.113.5.conf"])
        self.assertEqual(util.read_text(os.path.join(paths.monitors(), "203.0.113.5.conf")), GOOD)
        for ip in ("198.51.100.1", "198.51.100.3", "198.51.100.4"):
            self.assertEqual(util.read_text(os.path.join(paths.monitors(), ip + ".conf")),
                             'MON_IP="%s"\nMON_INTERVAL=60\nMON_THRESHOLD=100\nMON_COOLDOWN=300\n' % ip)
        bad = [w for w in res["warnings"] if "не файл монитора" in w]
        self.assertEqual(len(bad), 3, res["warnings"])
        self.assertIn("monitor file 198.51.100.3.conf rewritten checked", self.log())
        # и даже прежнее чтение 2.x (source) над тем, что записано, ничего не исполняет
        for n in got:
            subprocess.run(["bash", "-c", 'source "$1"', "_", os.path.join(paths.monitors(), n)],
                           capture_output=True, timeout=30)
        self.assertEqual(marks(self.scratch), [])
        self.assertEqual([m["ip"] for m in monitors.read_all()], ["198.51.100.1", "198.51.100.3", "198.51.100.4",
                                                                   "203.0.113.5"])

    def test_a_surrogate_in_the_copy_does_not_stop_it_half_way(self):
        """REV-3371-2 Б-2: «\\udc9b» в JSON копии (файл имён) — вся копия проверяется до применения: импорт проходит
        целиком, вместо суррогата — ничего (поле очищено); пробросы и имена — на месте."""
        self.old_node()
        doc = backup.payload(self.w.runner, backup.PARTS)
        doc["files"][backup.F_ALIASES] = "203.0.113.5=Вена\udc9b|||\n198.51.100.7=Офис\udcff|||\n"
        doc["manifest"]["files"][backup.F_ALIASES]["sha256"] = backup._sha(doc["files"][backup.F_ALIASES])
        text = backup.seal(self.w.runner, doc, PW, backup.PARTS)
        # суррогат вне «surrogateescape» (\ud800): копия не сходится — отказ до применения, ничего не тронуто
        doc["files"][backup.F_ALIASES] = "203.0.113.5=Вена\ud800|||\n"
        doc["manifest"]["files"][backup.F_ALIASES]["sha256"] = hashlib_sha(doc["files"][backup.F_ALIASES])
        bad = backup.seal(self.w.runner, doc, PW, backup.PARTS)
        self.new_node()
        with self.assertRaises(KaskadError):
            backup.unseal(self.w.runner, bad, PW)
        res = backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertTrue(res["ok"])
        got = util.read_text(paths.aliases())
        self.assertNotRegex(got, "[\ud800-\udfff]")
        self.assertIn("203.0.113.5=Вена", got)
        self.assertIn("198.51.100.7=Офис", got)
        self.assertIn("forwards", res["parts"])


def hashlib_sha(t):
    """sha256 копии для текста с одиночным суррогатом (вне surrogateescape — через surrogatepass, как подделал бы
    злоумышленник: JSON держит любой «\\udXXX»)."""
    import hashlib
    try:
        return backup._sha(t)
    except UnicodeEncodeError:
        return hashlib.sha256(t.encode("utf-8", "surrogatepass")).hexdigest()


class BashReaderTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="k3-3372-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.mark = os.path.join(self.d, "PWNED")

    def put(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_the_reader_runs_nothing(self):
        for n, t in evil(self.mark).items():
            rc, vals = bash_load(self.put(n, t))
            if n == "198.51.100.1.conf":                      # лишняя строка не читается — монитор годный
                self.assertEqual((rc, vals), (0, "198.51.100.1 60 100 300"))
            else:
                self.assertEqual(rc, 1, (n, vals))
        self.assertEqual(marks(self.d), [])

    def test_good_files_read_as_before(self):
        self.assertEqual(bash_load(self.put("a.conf", GOOD)), (0, "203.0.113.5 60 100 300"))
        self.assertEqual(bash_load(self.put("b.conf", "MON_IP=203.0.113.6\nMON_INTERVAL='060'\nMON_THRESHOLD=7\n")),
                         (0, "203.0.113.6 60 7 300"))           # «060» — десятичное, как в ядре
        self.assertEqual(bash_load(self.put("c.conf", GOOD.replace("300", "999999999")))[1],
                         "203.0.113.5 60 100 300")              # cooldown вне 0…604800 — по умолчанию
        for bad in ("MON_IP=203.0.113.5\nMON_INTERVAL=0\nMON_THRESHOLD=1\n",
                    "MON_IP=203.0.113.5\nMON_INTERVAL=86401\nMON_THRESHOLD=1\n",
                    "MON_IP=203.0.113.5\nMON_INTERVAL=60\nMON_THRESHOLD=65536\n",
                    "MON_IP=203.0.113.05\nMON_INTERVAL=60\nMON_THRESHOLD=1\n",
                    "MON_IP=example.com\nMON_INTERVAL=60\nMON_THRESHOLD=1\n", ""):
            self.assertEqual(bash_load(self.put("d.conf", bad))[0], 1, bad)
        self.assertEqual(bash_load(os.path.join(self.d, "none.conf"))[0], 1)

    def test_the_same_answer_as_the_core(self):
        """Разбор bash и ядра (bot/monitors.parse) согласны на годных и негодных файлах."""
        cases = list(evil(self.mark).values()) + [GOOD, "MON_IP='203.0.113.9'\nMON_INTERVAL=5\nMON_THRESHOLD=9\n"
                                                  "MON_COOLDOWN=0\n"]
        for t in cases:
            m = monitors.parse(t)
            rc, vals = bash_load(self.put("x.conf", t))
            if rc == 0:
                self.assertIsNotNone(m, t)
                self.assertEqual(vals, "%(ip)s %(interval)d %(threshold)d %(cooldown)d" % m, t)
        self.assertEqual(marks(self.d), [])

    def test_no_monitor_file_is_sourced(self):
        """Ни одно чтение файлов мониторов в install.sh не идёт через source/«.»/eval (класс Б-1)."""
        with open(INSTALL_SH, encoding="utf-8") as f:
            src = f.read()
        for line in src.splitlines():
            if re.search(r"MONITOR_DIR|MON_IP=", line) or re.search(r'(source|\.)\s+"\$(c|cf|conf)"', line):
                self.assertNotRegex(line, r'(^|[;&|{(\s])(source|eval|\.)\s+"?\$(c|cf|conf)\b', line)
        self.assertGreaterEqual(src.count("k3_mon_load \""), 5)

    def test_timestamps_are_numbers_only(self):
        """.last_alert_/.last_check_ идут в (( )) — там «a[$(…)]» исполнился бы: читается только число."""
        with open(INSTALL_SH, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('[[ "$last_alert" =~ ^[0-9]{1,12}$ ]] || last_alert=0', src)
        self.assertIn('[[ "$lc" =~ ^[0-9]{1,12}$ ]] || lc=0', src)


class UpdateCleanTest(RootCase):
    """После обновления (kaskadctl update files, --k3-post-update): лежащие файлы — проверенными."""

    def setUp(self):
        super().setUp()
        self.mark = os.path.join(self.root, "PWNED")
        os.makedirs(paths.monitors(), 0o700)

    def put(self, name, text):
        with open(os.path.join(paths.monitors(), name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_monitor_files_rewritten_bad_ones_dropped(self):
        for n, t in evil(self.mark).items():
            self.put(n, t)
        self.put("203.0.113.5.conf", GOOD)
        self.put("odd-name.conf", GOOD.replace("203.0.113.5", "203.0.113.8"))
        self.put("zz.conf", GOOD)                                  # то же место — второй раз
        self.put(".last_alert_203.0.113.5", "1700000000\n")
        res = monitors.clean_dir()
        self.assertEqual(sorted(os.listdir(paths.monitors())),
                         [".last_alert_203.0.113.5", "198.51.100.1.conf", "198.51.100.3.conf", "198.51.100.4.conf",
                          "203.0.113.5.conf", "203.0.113.8.conf"])
        self.assertEqual(sorted(res["rewritten"]), ["198.51.100.1.conf", "198.51.100.3.conf", "198.51.100.4.conf",
                                                    "odd-name.conf"])
        self.assertEqual(sorted(res["dropped"]), ["198.51.100.2.conf", "198.51.100.5.conf", "198.51.100.6.conf",
                                                  "zz.conf"])
        for n in os.listdir(paths.monitors()):
            if n.endswith(".conf"):
                t = util.read_text(os.path.join(paths.monitors(), n))
                self.assertRegex(t, r'^MON_IP="[0-9.]+"\nMON_INTERVAL=\d+\nMON_THRESHOLD=\d+\nMON_COOLDOWN=\d+\n$')
        self.assertEqual(monitors.clean_dir(), {"rewritten": [], "dropped": []})     # второй раз — ничего
        self.assertEqual(marks(self.root), [])

    def test_names_file_cleaned_once(self):
        util.atomic_write(paths.aliases(), "203.0.113.5=Вена\n", 0o600)
        self.assertFalse(aliases.clean_existing())                 # чистый (2.1 «IP=имя») — не переписывается
        self.assertEqual(util.read_text(paths.aliases()), "203.0.113.5=Вена\n")
        with open(paths.aliases(), "wb") as f:
            f.write("203.0.113.5=Вена\x1b]52;c;SGk=\x07|за\\eметка|||\n198.51.100.7=Офис \x9b2J|||\nмусор\n"
                    .encode("utf-8").replace("\x9b".encode("utf-8"), b"\x9b"))
        self.assertTrue(aliases.clean_existing())
        got = util.read_text(paths.aliases())
        self.assertNotRegex(got, "[\x00-\x09\x0b-\x1f\x7f-\x9f\\\\]")
        self.assertTrue(got.startswith("203.0.113.5=Вена]52;c;SGk=|заeметка||\n198.51.100.7=Офис "), got)
        self.assertFalse(aliases.clean_existing())

    def test_cli_update_files(self):
        from kaskad import cli
        self.put("198.51.100.3.conf", evil(self.mark)["198.51.100.3.conf"])
        self.put("198.51.100.2.conf", evil(self.mark)["198.51.100.2.conf"])
        self.assertEqual(cli.main(["update", "files", "--quiet"]), 0)
        self.assertEqual(os.listdir(paths.monitors()), ["198.51.100.3.conf"])
        self.assertEqual(util.read_text(os.path.join(paths.monitors(), "198.51.100.3.conf")),
                         'MON_IP="198.51.100.3"\nMON_INTERVAL=60\nMON_THRESHOLD=100\nMON_COOLDOWN=300\n')
        self.assertEqual(marks(self.root), [])


class NamesKeepLengthTest(RootCase):
    """П-1: поля файла имён из копии и прежних версий не обрезаются до 64 (2.x длину не ограничивал), предел — 255."""

    def test_clean_file_keeps_long_fields(self):
        note, isp = "заметка " * 12, "Очень длинный провайдер " * 4
        got = aliases.clean_file("203.0.113.5=Офис|%s|RU|%s\n" % (note, isp))
        self.assertEqual(got, "203.0.113.5=Офис|%s|RU|%s\n" % (note.strip(), isp.strip()))
        self.assertEqual(len(aliases.clean_file("203.0.113.5=%s\n" % ("я" * 400)).split("=")[1].split("|")[0]), 255)

    def test_a_new_name_keeps_the_long_note(self):
        note = "н" * 100
        util.atomic_write(paths.aliases(), "203.0.113.5=Офис|%s||\n" % note, 0o600)
        aliases.update("203.0.113.5", name="Дом")
        self.assertEqual(aliases.get("203.0.113.5")["note"], note)
        self.assertEqual(len(aliases.update("203.0.113.5", note="x" * 100)["note"]), 64)   # новое поле — как в 2.x меню


if __name__ == "__main__":
    unittest.main()
