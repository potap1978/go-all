# -*- coding: utf-8 -*-
"""Порядок ключей на странице подключения (находка NC-27 критика-новичка, 28.09.2026).

Совет в SSH говорит «берите AmneziaWG 3.1», а страница, мастер и списки первым показывали 2.0 — по алфавиту
имён профилей. Рекомендуемое на телефоне приложение AmneziaWG линию 3.1 читает (lib/clients.json), поэтому
первым везде идёт 3.1, затем 2.0 и 1.x. Здесь — страница: в lib/pages_server.py меняется только порядок.

Стенд — тот же, что у tests/test_pages_server.py (каталог продукта и подставной мост), но у человека ключи
в двух профилях.

Запуск: python3 -m unittest tests.test_pages_order
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_pages_server as tps  # noqa: E402

ps = tps.ps

ONE = '"profiles": ["awg2"], "peers": {"awg2": {"active": True, "has_key": True}}'
TWO = ('"profiles": ["awg2", "awg3"], "peers": {"awg2": {"active": True, "has_key": True}, '
       '"awg3": {"active": True, "has_key": True}}')


class PageKeyOrder(tps.AppTestBase):
    def setUp(self):
        super().setUp()
        src = tps.FAKE_BRIDGE.replace(ONE, TWO)
        self.assertNotEqual(src, tps.FAKE_BRIDGE, 'карточка подставного моста сменилась — поправьте ONE')
        with open(self.bin, 'w') as fh:
            fh.write(src)

    def test_order_is_31_then_20_then_1x(self):
        self.assertEqual(ps.ordered_profiles(['awg1', 'awg2', 'awg3']), ['awg3', 'awg2', 'awg1'])
        self.assertEqual(ps.ordered_profiles(['awg2', 'awg9', 'awg3']), ['awg3', 'awg2', 'awg9'])

    def test_page_lists_the_31_key_first(self):
        app = self.app()
        # Метаданные старой страницы — в алфавитном порядке, как их писало ядро до NC-27
        page = tps.FakePage({'id': 'p-1a2b', 'user': 'anna', 'created': '2026-09-21T10:00:00Z',
                             'profiles': ['awg2', 'awg3']})
        kind, view = app.page_view(page)
        self.assertEqual(kind, 'ok')
        label, profiles, user = view
        data = app.page_data(page, label, profiles, 'android', user)
        self.assertEqual([p['id'] for p in data['profiles']], ['awg3', 'awg2'])


class PageKeyOrderByLine(tps.AppTestBase):
    """AWG31-01: место профиля — не линия. Подхваченный из приложения amnezia-awg2 держит 3.1, и 2.0 встала в awg3:
    первым на странице — ключ 3.1 (awg2), как и советует строка над ключами, а не awg3 по месту."""

    LINE = '"line": "2.0"'

    def setUp(self):
        super().setUp()
        src = tps.FAKE_BRIDGE.replace(ONE, TWO).replace(self.LINE, '"line": {"awg2": "3.1", "awg3": "2.0"}.get(profile, "2.0")')
        self.assertEqual(tps.FAKE_BRIDGE.count(self.LINE), 1, 'ответ user-key подставного моста сменился — поправьте LINE')
        with open(self.bin, 'w') as fh:
            fh.write(src)

    def test_page_lists_the_31_key_first_by_its_line(self):
        app = self.app()
        page = tps.FakePage({'id': 'p-1a2b', 'user': 'anna', 'created': '2026-09-21T10:00:00Z',
                             'profiles': ['awg3', 'awg2']})
        kind, view = app.page_view(page)
        self.assertEqual(kind, 'ok')
        label, profiles, user = view
        data = app.page_data(page, label, profiles, 'android', user)
        self.assertEqual([(p['id'], p['line']) for p in data['profiles']], [('awg2', '3.1'), ('awg3', '2.0')])


class PageKeyTitleWithPort(tps.AppTestBase):
    """DUP-LINE-01: на сервере две 3.1 — подхваченная у приложения в awg2 (46567) и своя в awg3 (6714). Ключ на странице
    называется с портом — «Профиль 3.1 · порт 6714» (решение владельца 15.10: без имени места, порт всегда)."""

    def setUp(self):
        super().setUp()
        src = tps.FAKE_BRIDGE.replace(ONE, TWO).replace(PageKeyOrderByLine.LINE, '"line": "3.1"')
        with open(self.bin, 'w') as fh:
            fh.write(src)

    def config(self, awg2_line):
        import json
        with open(os.path.join(self.gdir, 'config.json'), 'w') as fh:
            json.dump({'profiles': {
                'awg2': {'line': awg2_line, 'port': 46567, 'installed_at': '2026-10-01T00:00:00Z', 'imported': True},
                'awg3': {'line': '3.1', 'port': 6714, 'installed_at': '2026-10-02T00:00:00Z'}}}, fh)

    def test_two_31_keys_carry_the_port(self):
        self.config('3.1')
        app = self.app()
        page = tps.FakePage({'id': 'p-1a2b', 'user': 'anna', 'created': '2026-09-21T10:00:00Z', 'profiles': ['awg3', 'awg2']})
        kind, view = app.page_view(page)
        self.assertEqual(kind, 'ok')
        label, profiles, user = view
        data = app.page_data(page, label, profiles, 'android', user)
        self.assertEqual(sorted((p['id'], p['port']) for p in data['profiles']), [('awg2', 46567), ('awg3', 6714)])
        with open(tps.ps.__file__, encoding='utf-8') as fh:
            js = fh.read()
        self.assertIn("if (pr.port) { return t('profile', pr.line) + ' · ' + t('port_n', pr.port); }", js)
        self.assertIn('"port_n": "порт %s"', js)

    def test_port_always(self):
        self.config('2.0')
        app = self.app()
        page = tps.FakePage({'id': 'p-1a2b', 'user': 'anna', 'created': '2026-09-21T10:00:00Z', 'profiles': ['awg3']})
        label, profiles, user = app.page_view(page)[1]
        data = app.page_data(page, label, profiles, 'android', user)
        self.assertEqual([p['port'] for p in data['profiles']], [6714])


if __name__ == '__main__':
    unittest.main()
