# -*- coding: utf-8 -*-
"""Проверки вендоренных upstream-скриптов Amnezia (CONTRACT §1, §2, §3.3; PLAN §6.8).

Смысл: lib/upstream — это чужой код, который мы держим у себя дословно. Единственное
разрешённое отличие — идемпотентные правила iptables в awg/start.sh. Тест ловит две
беды: (1) файл поправили руками, а README/шасумма про это не знает; (2) обновили
upstream и незаметно потеряли или приобрели плейсхолдер, из-за чего генератор конфига
начнёт молча писать пустые значения.
"""

import difflib
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = ROOT / "lib" / "upstream"
README = UPSTREAM / "README.md"
IMAGES = UPSTREAM / "images.json"

# Плейсхолдер вида $NAME или ${NAME} — та же регулярка, что описана в README.
PLACEHOLDER_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

# Строка таблицы README: | `awg/start.sh` | `<64 hex>` | 2415 | … |
README_ROW_RE = re.compile(
    r"^\|\s*`([A-Za-z0-9_]+/[A-Za-z0-9_.]+)`\s*\|\s*`([0-9a-f]{64})`\s*\|", re.M
)

# Машиночитаемый список плейсхолдеров: <!-- placeholders:<файл> --> и следом ```text … ```
PLACEHOLDER_BLOCK_RE = re.compile(
    r"<!--\s*placeholders:(?P<file>\S+?)\s*-->\s*```text\n(?P<body>.*?)```", re.S
)

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unique_placeholders(text):
    """Плейсхолдеры в порядке первого появления, без повторов."""
    seen, out = set(), []
    for m in PLACEHOLDER_RE.finditer(text):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def read_text(path):
    return path.read_text(encoding="utf-8")


class ReadmeChecksums(unittest.TestCase):
    """Таблица sha256 в README должна сходиться с файлами на диске."""

    def setUp(self):
        self.assertTrue(README.is_file(), "нет %s" % README)
        self.rows = README_ROW_RE.findall(read_text(README))

    def test_table_is_not_empty(self):
        self.assertGreaterEqual(
            len(self.rows), 11, "в таблице README меньше строк, чем вендоренных файлов"
        )

    def test_checksums_match_disk(self):
        for rel, expected in self.rows:
            with self.subTest(file=rel):
                path = UPSTREAM / rel
                self.assertTrue(path.is_file(), "README упоминает %s, а файла нет" % rel)
                self.assertEqual(
                    expected,
                    sha256_of(path),
                    "sha256 файла %s разошёлся с README — файл правили руками "
                    "или обновили upstream, не пересчитав таблицу" % rel,
                )

    def test_every_vendored_file_is_listed(self):
        """Обратная сторона: файл лежит в lib/upstream/<awg|xray>, но в README его нет."""
        listed = {rel for rel, _ in self.rows}
        on_disk = set()
        for sub in ("awg", "xray"):
            d = UPSTREAM / sub
            self.assertTrue(d.is_dir(), "нет каталога %s" % d)
            for f in sorted(d.iterdir()):
                if f.is_file():
                    on_disk.add("%s/%s" % (sub, f.name))
        self.assertEqual(
            on_disk,
            listed,
            "README и каталог разошлись: только на диске %s, только в README %s"
            % (sorted(on_disk - listed), sorted(listed - on_disk)),
        )


class StartShDiff(unittest.TestCase):
    """awg/start.sh отличается от upstream только строками про iptables."""

    ours = UPSTREAM / "awg" / "start.sh"
    theirs = UPSTREAM / "awg" / "start.sh.upstream"

    def setUp(self):
        for p in (self.ours, self.theirs):
            self.assertTrue(p.is_file(), "нет %s" % p)

    def test_diff_has_only_iptables_lines(self):
        diff = difflib.unified_diff(
            read_text(self.theirs).splitlines(),
            read_text(self.ours).splitlines(),
            fromfile="start.sh.upstream",
            tofile="start.sh",
            lineterm="",
            n=0,
        )
        leftovers = []
        for line in diff:
            if line.startswith(("---", "+++", "@@")):
                continue
            if not line.startswith(("+", "-")):
                continue
            if "iptables" in line:
                continue
            leftovers.append(line)
        self.assertEqual(
            [],
            leftovers,
            "в start.sh есть правки, не связанные с iptables: %s" % leftovers,
        )

    def test_files_actually_differ(self):
        self.assertNotEqual(
            sha256_of(self.ours),
            sha256_of(self.theirs),
            "start.sh совпал с upstream — правка про идемпотентные iptables потеряна",
        )

    def test_every_append_rule_is_idempotent(self):
        """Ни одного голого `iptables … -A`: каждое правило прикрыто `-C … ||`."""
        bare = [
            ln
            for ln in read_text(self.ours).splitlines()
            if re.match(r"^\s*iptables(?: -t \w+)? -A ", ln)
        ]
        self.assertEqual([], bare, "правила добавляются без проверки -C: %s" % bare)

        upstream_rules = [
            ln
            for ln in read_text(self.theirs).splitlines()
            if re.match(r"^\s*iptables(?: -t \w+)? -A ", ln)
        ]
        guarded = [
            ln
            for ln in read_text(self.ours).splitlines()
            if re.match(r"^\s*iptables(?: -t \w+)? -C ", ln) and "||" in ln
        ]
        self.assertEqual(
            len(upstream_rules),
            len(guarded),
            "число правил разошлось: в upstream %d, у нас идемпотентных %d"
            % (len(upstream_rules), len(guarded)),
        )

    def test_nat_table_rules_kept_table_flag(self):
        """`-t nat` не должен потеряться ни в проверке, ни в добавлении."""
        for ln in read_text(self.ours).splitlines():
            if "POSTROUTING" not in ln or not ln.startswith("iptables"):
                continue
            with self.subTest(line=ln):
                self.assertTrue(ln.startswith("iptables -t nat -C "), ln)
                self.assertIn("|| iptables -t nat -A POSTROUTING", ln)


class ImagesJson(unittest.TestCase):
    """images.json — CONTRACT §3.3 плюс наши amd64_digest и xray_release."""

    IMAGE_KEYS = ("repo", "tag", "digest", "checked")
    TOP_KEYS = ("amneziawg-go", "amnezia-wg", "client_release", "xray_release")

    def setUp(self):
        self.assertTrue(IMAGES.is_file(), "нет %s" % IMAGES)
        self.data = json.loads(read_text(IMAGES))

    def test_top_level_keys(self):
        for key in self.TOP_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, self.data, "в images.json нет ключа %s" % key)

    def test_image_entries(self):
        for name in ("amneziawg-go", "amnezia-wg"):
            entry = self.data[name]
            with self.subTest(image=name):
                self.assertIsInstance(entry, dict)
                for key in self.IMAGE_KEYS:
                    self.assertIn(key, entry, "%s: нет ключа %s" % (name, key))
                self.assertEqual(entry["repo"], "amneziavpn/%s" % name)
                self.assertRegex(entry["digest"], SHA256_RE)
                self.assertRegex(entry["checked"], DATE_RE)
                self.assertTrue(entry["tag"], "%s: пустой тег" % name)
                if "amd64_digest" in entry:
                    self.assertRegex(entry["amd64_digest"], SHA256_RE)
                    self.assertNotEqual(
                        entry["amd64_digest"],
                        entry["digest"],
                        "%s: amd64_digest пишем, только если он отличается от manifest"
                        % name,
                    )

    def test_client_release_matches_readme(self):
        m = re.search(r"\|\s*тег\s*\|\s*\*\*`([^`]+)`\*\*", read_text(README))
        self.assertIsNotNone(m, "в README не нашёлся тег клиента")
        self.assertEqual(
            m.group(1),
            self.data["client_release"],
            "тег в README и client_release в images.json разошлись",
        )

    def test_xray_release_matches_dockerfile(self):
        dockerfile = read_text(UPSTREAM / "xray" / "Dockerfile")
        m = re.search(r'^ARG\s+XRAY_RELEASE\s*=\s*"?([^"\s]+)"?', dockerfile, re.M)
        self.assertIsNotNone(m, "в xray/Dockerfile нет ARG XRAY_RELEASE")
        self.assertEqual(
            m.group(1),
            self.data["xray_release"],
            "xray_release в images.json не совпал с ARG XRAY_RELEASE в Dockerfile",
        )


class Placeholders(unittest.TestCase):
    """Списки $ПЛЕЙСХОЛДЕРОВ в README = то, что реально есть в файлах."""

    def setUp(self):
        self.blocks = {
            m.group("file"): [
                ln.strip().lstrip("$")
                for ln in m.group("body").splitlines()
                if ln.strip()
            ]
            for m in PLACEHOLDER_BLOCK_RE.finditer(read_text(README))
        }

    def test_blocks_present(self):
        self.assertIn("awg/template.conf", self.blocks)
        self.assertIn("awg/configure_container.sh", self.blocks)

    def test_template_conf_matches_readme(self):
        self._compare("awg/template.conf")

    def test_configure_container_matches_readme(self):
        self._compare("awg/configure_container.sh")

    def _compare(self, rel):
        actual = unique_placeholders(read_text(UPSTREAM / rel))
        listed = self.blocks[rel]
        self.assertEqual(
            set(actual),
            set(listed),
            "%s: в файле лишние %s, в README лишние %s — "
            "новый плейсхолдер значит новый параметр протокола, "
            "поддержку добавляем релизом (PLAN §6.8)"
            % (rel, sorted(set(actual) - set(listed)), sorted(set(listed) - set(actual))),
        )
        self.assertEqual(actual, listed, "%s: порядок плейсхолдеров разошёлся" % rel)
        self.assertEqual(
            len(listed), len(set(listed)), "%s: в README дубли плейсхолдеров" % rel
        )


if __name__ == "__main__":
    unittest.main()
