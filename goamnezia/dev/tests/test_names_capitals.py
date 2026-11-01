# -*- coding: utf-8 -*-
"""Набор случайных имён «прилагательное + столица» — lib/names_capitals.json (python 3.9, unittest).

Решение владельца 27.09 (docs/MODES_047.md, «Имена ключей»): 500 пар ru/en, по 2–3
прилагательных на столицу, id латиницей из английского варианта. Бот показывает людей
по двое в ряд, поэтому подпись короткая: до 20 символов где возможно, 24 — жёсткий потолок.

Здесь проверяем форму набора, а не вкус: число записей, ключи, уникальность, id, длину,
что подпись кончается своей столицей и что у каждой столицы 2–3 варианта.

Запуск: python3 -m unittest tests/test_names_capitals.py
"""
import collections
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAMES = ROOT / 'lib' / 'names_capitals.json'

EXPECTED_COUNT = 500
KEYS = {'id', 'ru', 'en', 'city_ru', 'city_en'}
ID_RE = re.compile(r'^[a-z0-9_]{1,32}$')
MAX_LEN = 24


class NamesCapitalsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NAMES, encoding='utf-8') as fh:
            cls.names = json.load(fh)

    def test_count(self):
        self.assertIsInstance(self.names, list)
        self.assertEqual(len(self.names), EXPECTED_COUNT)

    def test_required_keys(self):
        for i, entry in enumerate(self.names):
            with self.subTest(i=i):
                self.assertIsInstance(entry, dict)
                self.assertEqual(set(entry), KEYS)
                for key in KEYS:
                    self.assertIsInstance(entry[key], str, key)
                    self.assertTrue(entry[key].strip(), key)
                    self.assertEqual(entry[key], entry[key].strip(), key)

    def test_unique(self):
        for key in ('id', 'ru', 'en'):
            counts = collections.Counter(entry[key] for entry in self.names)
            dups = sorted(value for value, n in counts.items() if n > 1)
            self.assertEqual(dups, [], 'повторы в %s' % key)

    def test_id_format(self):
        bad = [entry['id'] for entry in self.names if not ID_RE.match(entry['id'])]
        self.assertEqual(bad, [])

    def test_length(self):
        for key in ('ru', 'en'):
            long_ = [entry[key] for entry in self.names if len(entry[key]) > MAX_LEN]
            self.assertEqual(long_, [], '%s длиннее %d символов' % (key, MAX_LEN))

    def test_ends_with_city(self):
        for entry in self.names:
            for full, city in (('ru', 'city_ru'), ('en', 'city_en')):
                with self.subTest(name=entry[full]):
                    suffix = ' ' + entry[city]
                    self.assertTrue(entry[full].endswith(suffix),
                                    '%r не кончается на %r' % (entry[full], entry[city]))
                    self.assertTrue(entry[full][:-len(suffix)].strip(), 'нет прилагательного')

    def test_two_or_three_per_city(self):
        per_city = collections.Counter((e['city_ru'], e['city_en']) for e in self.names)
        wrong = sorted((city, n) for city, n in per_city.items() if not 2 <= n <= 3)
        self.assertEqual(wrong, [])
        # Одна столица — одна пара названий: иначе «2–3 на столицу» можно обойти опечаткой.
        ru_of = collections.defaultdict(set)
        en_of = collections.defaultdict(set)
        for city_ru, city_en in per_city:
            ru_of[city_en].add(city_ru)
            en_of[city_ru].add(city_en)
        self.assertEqual({k: v for k, v in ru_of.items() if len(v) > 1}, {})
        self.assertEqual({k: v for k, v in en_of.items() if len(v) > 1}, {})


if __name__ == '__main__':
    unittest.main()
