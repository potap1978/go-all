# -*- coding: utf-8 -*-
"""Каталог шаблонов сайта-маскировки и lib/catalog_tool.py (python 3.9, unittest).

Каталог взят из навыка site-templates-library как есть (решение владельца:
база проверена, повторная проверка страниц не нужна). Здесь проверяем не
шаблоны, а ЦЕЛОСТНОСТЬ переноса и помощника, которым пользуются бот и панель:
  • catalog_tool.py validate на нашем файле — без проблем;
  • 18 категорий, 33 467 записей — ровно столько, сколько в навыке;
  • page() режет списки на страницы (портфолио — 8 793 записи, в бот целиком нельзя);
  • short_key() укладывается в 64 байта callback_data Telegram;
  • search() находит, random() возвращает существующий id, get() — запись.

Запуск: python3 -m unittest tests/test_templates_catalog.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = ROOT / 'lib'
CATALOG = ROOT / 'templates_catalog.json'
sys.path.insert(0, str(LIB))

from catalog_tool import Catalog, ID_RE  # noqa: E402

EXPECTED_CATEGORIES = 18
EXPECTED_TEMPLATES = 33467
CATEGORY_IDS = {'business', 'ecommerce', 'medical', 'education', 'portfolio', 'realestate',
                'restaurant', 'landing', 'technology', 'blog', 'travel', 'beauty', 'automotive',
                'entertainment', 'nonprofit', 'photography', 'legal', 'other'}


class CatalogFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assertTrue(CATALOG.is_file(), "нет %s" % CATALOG)
        with open(CATALOG, encoding='utf-8') as f:
            cls.data = json.load(f)
        cls.cat = Catalog(str(CATALOG))

    def test_validate_cli(self):
        """`catalog_tool.py validate <файл>` — exit 0, «проблем: 0»."""
        p = subprocess.run([sys.executable, str(LIB / 'catalog_tool.py'), 'validate', str(CATALOG)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('проблем: 0', p.stdout)

    def test_validate_library(self):
        self.assertEqual(Catalog.validate(self.data), [])

    def test_default_path_points_next_to_install(self):
        """Без GOAMNEZIA_CATALOG помощник ищет каталог рядом с install.sh (../templates_catalog.json)."""
        env = dict(os.environ)
        env.pop('GOAMNEZIA_CATALOG', None)
        p = subprocess.run([sys.executable, str(LIB / 'catalog_tool.py'), 'stats'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('всего: %d' % EXPECTED_TEMPLATES, p.stdout)

    def test_counts(self):
        cats = self.cat.categories()
        self.assertEqual(len(cats), EXPECTED_CATEGORIES)
        self.assertEqual({c[0] for c in cats}, CATEGORY_IDS)
        self.assertEqual(sum(n for _, _, n in cats), EXPECTED_TEMPLATES)

    def test_ids_unique_and_well_formed(self):
        ids = [t['id'] for c in self.data['categories'] for t in c['templates']]
        self.assertEqual(len(ids), len(set(ids)))
        bad = [i for i in ids if not ID_RE.match(i)]
        self.assertEqual(bad, [])
        # website_deploy_template принимает ^[A-Za-z0-9_.-]{1,120}$ — все id должны проходить
        re_site = re.compile(r'^[A-Za-z0-9_.-]{1,120}$')
        self.assertEqual([i for i in ids if not re_site.match(i)], [])

    def test_every_template_has_https_repo(self):
        for c in self.data['categories']:
            for t in c['templates']:
                self.assertTrue(t['repo_url'].startswith('https://'), t['id'])

    def test_page(self):
        items, pages = self.cat.page('portfolio', page=0, per=24)
        self.assertEqual(len(items), 24)
        self.assertEqual(pages, (8793 + 23) // 24)
        last, pages2 = self.cat.page('portfolio', page=pages - 1, per=24)
        self.assertEqual(pages2, pages)
        self.assertTrue(0 < len(last) <= 24)
        # страница за пределами — прижимается к последней, а не падает
        over, _ = self.cat.page('portfolio', page=10 ** 6, per=24)
        self.assertEqual(over, last)
        self.assertEqual(self.cat.page('no_such_category'), ([], 1))

    def test_short_key_fits_callback_data(self):
        seen = {}
        for c in self.data['categories']:
            for t in c['templates']:
                key = Catalog.short_key(t['id'])
                self.assertLessEqual(len(key.encode('utf-8')), 64)
                self.assertRegex(key, r'^[0-9a-f]{12}$')
                seen.setdefault(key, t['id'])
        # 12 hex-символов sha1 на 33 467 записей: коллизий нет
        self.assertEqual(len(seen), EXPECTED_TEMPLATES)

    def test_search(self):
        hits = self.cat.search('aerial')
        self.assertTrue(any(h['id'] == 'h5up_aerial' for h in hits))
        for h in hits:
            self.assertTrue('aerial' in h['name'].lower() or 'aerial' in h['id'].lower())
            self.assertIn('category', h)
        self.assertTrue(len(self.cat.search('cafe', category='restaurant')) >= 1)
        self.assertEqual(self.cat.search('zzz-no-such-template-zzz'), [])

    def test_random_returns_existing(self):
        for _ in range(20):
            t = self.cat.random()
            self.assertIsNotNone(t)
            self.assertIsNotNone(self.cat.get(t['id']))
        t = self.cat.random(category='legal')
        self.assertEqual(self.cat.get(t['id'])['category'], 'legal')

    def test_get(self):
        t = self.cat.get('h5up_aerial')
        self.assertEqual(t['name'], 'Aerial')
        self.assertEqual(t['category'], 'business')
        self.assertEqual(t['sparse_path'], 'aerial')
        self.assertIsNone(self.cat.get('gh_no_such_template'))


class FixtureTest(unittest.TestCase):
    """Фикстура tests/fixtures/site_min — то, чем tests/test_website_dryrun.sh проверяет чистку."""

    def test_fixture_shape(self):
        d = ROOT / 'tests' / 'fixtures' / 'site_min'
        self.assertTrue((d / 'index.html').is_file())
        self.assertTrue((d / 'README.md').is_file())
        self.assertTrue((d / 'package.json').is_file())
        html = (d / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<style>', html)
        self.assertNotIn('%PUBLIC_URL%', html)


if __name__ == '__main__':
    unittest.main()
