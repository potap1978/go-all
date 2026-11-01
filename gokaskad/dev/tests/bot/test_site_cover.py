"""«🌐 Домен» in the bot, the cover site (docs/k3/SPEC-wave4.md §5, §7.1, C-лицо): on and off with the consequences and
a confirmation, every refusal of the core in words, templates — categories by 8, templates by 8 with «Далее/Назад»,
the previews as links, search, «🎲 Наугад», the card and «Поставить» (callback_data through short_key, ≤ 64 bytes),
the catalog brought by a job (no GitHub key — the way 8 → 6), the panel's address masked / shown in a message that
deletes itself in 5 minutes / replaced after «старый перестанет открываться сразу», the check of the site. Long steps
answer «⏳ …» first, then the result. nginx and git are fakes (as tests/site/test_nginx.py)."""

import contextlib
import gzip
import hashlib
import json
import os
import re
from unittest import mock

from kaskad import state, util
from kaskad.bot import site as bsite
from kaskad.bot import store
from kaskad.errors import CONFLICT, KaskadError
from kaskad.panel import conf
from kaskad.site import consumers, nginx, ops
from kaskad.site import templates as T
from kaskad.site import tplhealth as H
from tests.bot.fake_tg import OWNER
from tests.bot.test_site import SiteBotCase
from tests.site.test_tplhealth import Web

CATS = [("business", "Бизнес и корпоративные"), ("landing", "Лендинги"), ("restaurant", "Рестораны и еда"),
        ("medical", "Медицина и фитнес"), ("education", "Образование"), ("travel", "Путешествия"),
        ("blog", "Блоги и журналы"), ("legal", "Юридические"), ("photography", "Фотография"), ("other", "Разное")]


def catalog_bytes():
    cats = []
    for cid, name in CATS:
        n = 20 if cid == "business" else 2
        ts = [{"id": "gh_%s_%02d_%s" % (cid, i, "x" * 90), "name": "%s шаблон %d" % (name, i), "source": "github",
               "license": "MIT",
               "description": "Сайт ресторана у моря" if cid == "restaurant" else "Корпоративный сайт",
               "repo_url": "https://github.com/fx/%s%d" % (cid, i), "sparse_path": "",
               "preview_url": "https://example.org/%s/%d/?a=1&b=2" % (cid, i)} for i in range(1, n + 1)]
        cats.append({"id": cid, "name": name, "icon": "x", "templates": ts})
    return json.dumps({"categories": cats}, ensure_ascii=False).encode("utf-8")


RAW = catalog_bytes()


class CoverCase(SiteBotCase):
    def setUp(self):
        super(CoverCase, self).setUp()
        p = mock.patch.object(T, "CATALOG_SHA256", hashlib.sha256(RAW).hexdigest())
        p.start()
        self.addCleanup(p.stop)
        # «жив ли шаблон» (3.2.21): the check runs in place (not a thread), no network — repositories by self.repo
        # (url -> ok | gone | unknown; the rest ok), previews by self.web (200 unless said)
        self.repo, self.web = {}, Web()
        for p in (mock.patch.object(bsite, "_spawn", lambda fn: fn()),
                  mock.patch.object(H, "repo_state", side_effect=lambda r, url, timeout=8: self.repo.get(url, "ok")),
                  mock.patch.object(H, "_open", self.web)):
            p.start()
            self.addCleanup(p.stop)
        self.put_catalog()
        self.with_domain(issued="le")

    def put_catalog(self):
        util.ensure_dir(os.path.dirname(T.catalog_path()), 0o755)
        with open(T.catalog_path(), "wb") as f:
            f.write(RAW)

    @contextlib.contextmanager
    def fake_site(self, deployed=None):
        """nginx, the template and the panel's reload are fakes: ops.mode / rotate run for real otherwise."""
        deployed = deployed or {"ok": True, "id": "demo", "name": "Демо", "source": "repo", "fallback": False}
        with mock.patch.object(nginx, "ensure", return_value=[]), \
                mock.patch.object(nginx, "write", return_value="start"), \
                mock.patch.object(nginx, "can_reject_handshake", return_value=True), \
                mock.patch.object(nginx, "ufw_allow443", return_value=False), \
                mock.patch.object(nginx, "ours", return_value=True), \
                mock.patch.object(nginx, "remove", return_value="reload"), \
                mock.patch.object(nginx, "ufw_delete443", return_value=False), \
                mock.patch.object(T, "deploy", return_value=deployed) as dep, \
                mock.patch.object(consumers, "_reload_panel", return_value=False):
            yield dep

    def site_on(self):
        with self.fake_site():
            ops.mode(self.runner, "site")

    def all_data(self):
        out = []
        for c in self.tg.sent("sendMessage", "editMessageText"):
            out += [d for _, d in c.buttons()]
        return out

    def key(self, cid, i):
        return T.short_key("gh_%s_%02d_%s" % (cid, i, "x" * 90))


class ModeTest(CoverCase):
    def test_enable_asks_then_the_job_reports(self):
        self.make_app()
        self.press("sdm")
        text = self.last_text()
        self.assertIn("🌐 <b>Включить сайт-маскировку</b>", text)
        self.assertIn("займёт порты 80 и 443/tcp", text)
        self.assertEqual([d for _, d in self.buttons()], ["sdmy:onY0", "sd"])     # T-3378 Н-13: no devices priced
        self.assertNotEqual(self.site()["mode"], "site")              # the question changes nothing
        with self.fake_site() as dep:
            self.press("sdmy:onY0")
        dep.assert_called_once_with(self.runner, None)                # a random template
        texts = self.texts()
        self.assertIn("⏳ Включаю сайт-маскировку", texts[-2])
        self.assertIn("✅ Сайт-маскировка включена: <code>https://vpn.example.com/</code> — шаблон «demo»", texts[-1])
        self.assertIn("Веб-панель на домене: <code>https://vpn.example.com/app-", texts[-1])
        self.assertIn("••••", texts[-1])
        self.assertNotIn(conf.load()["proxy"]["path"], texts[-1])
        self.assertEqual(self.site()["mode"], "site")

    def test_a_price_not_counted_passes_no_yes(self):
        """SEC-vless-sh12 придирка 4: the price of VLESS could not be counted for the question — its «yes» is not passed,
        the core asks the price itself."""
        from kaskad.site import ops as site_ops
        self.make_app()
        with mock.patch.object(site_ops, "vless_price", side_effect=RuntimeError("boom")):
            self.press("sdm")
        self.assertEqual([d for _, d in self.buttons()], ["sdmy:on0", "sd"])
        with mock.patch.object(site_ops, "mode", return_value={"ok": True, "site": {}, "template": {}}) as m:
            self.press("sdmy:on0")
        self.assertIs(m.call_args.kwargs["yes"], False)
        with mock.patch.object(site_ops, "mode", return_value={"ok": True, "site": {}, "template": {}}) as m:
            self.press("sdmy:onY0")
        self.assertIs(m.call_args.kwargs["yes"], True)
        with mock.patch.object(site_ops, "mode", return_value={"ok": True, "site": {}, "template": {}}) as m:
            self.press("sdmy:onY")                          # T-3378 Н-13: no count of the consent — the question
        m.assert_not_called()
        self.assertEqual([d for _, d in self.buttons()], ["sdmy:onY0", "sd"])
        for old in ("sdmy:on", "sdmy:off"):                 # REV-vless-sh2 придирка 3: a button sent before the update
            with mock.patch.object(site_ops, "mode", return_value={"ok": True, "site": {}, "template": {}}) as m:
                self.press(old)
            self.assertIs(m.call_args.kwargs["yes"], False, old)

    def test_fallback_says_the_stub(self):
        self.make_app()
        stub = {"ok": True, "id": "builtin_stub", "name": None, "source": None, "fallback": True,
                "warnings": ["нет связи с GitHub"]}
        with self.fake_site(deployed=stub):
            self.press("sdmy:on")
        text = self.last_text()
        self.assertIn("✅ Сайт-маскировка включена", text)
        self.assertIn("поставлена страница-заглушка. Шаблон можно сменить", text)
        self.assertIn("⚠️ нет связи с GitHub", text)

    def test_the_screen_with_the_site(self):
        self.site_on()
        self.make_app()
        self.press("sd")
        text = self.last_text()
        self.assertIn("Сайт-маскировка: ✅ включена, шаблон «demo»", text)
        self.assertIn("Панель на домене: <code>https://vpn.example.com/app-", text)
        self.assertNotIn(conf.load()["proxy"]["path"], text)
        self.assertEqual([d for _, d in self.buttons()], ["sdd", "sdc", "sdu", "sdt:1", "sdp", "sdq", "sdm", "sdn",
                                                          "sdx", "sd", "m"])

    def test_disable_names_the_consequences(self):
        self.site_on()
        self.make_app()
        self.press("sdm")
        text = self.last_text()
        self.assertIn("🛑 <b>Выключить сайт-маскировку</b>", text)
        self.assertIn("сайт на домене перестанет открываться", text)
        self.assertIn("веб-панель и страницы пользователей по адресам на домене перестанут открываться", text)
        self.assertIn("файлы сайта останутся в <code>", text)
        self.assertEqual([d for _, d in self.buttons()], ["sdmy:offY0", "sd"])
        self.assertEqual(self.site()["mode"], "site")
        with self.fake_site():
            self.press("sdmy:offY0")
        self.assertIn("⏳ Выключаю", self.texts()[-2])
        self.assertIn("✅ Сайт-маскировка выключена", self.last_text())
        self.assertEqual(self.site()["mode"], "domain")

    def test_without_a_certificate_of_lets_encrypt(self):
        ops.clear(self.runner)
        ops.set_domain(self.runner, "other.example.org")
        self.make_app()
        self.press("sdm")
        self.assertIn("❌ Для сайта нужен боевой сертификат Let's Encrypt", self.last_text())
        self.assertEqual([d for _, d in self.buttons()], ["sdc", "sd"])

    def test_every_refusal_in_words(self):
        cases = [
            ("no_domain", "Для сайта сначала задайте домен.", "домен не задан", "Задайте домен"),
            ("no_cert", "Нужен боевой сертификат.", "нет боевого сертификата Let's Encrypt", "Выпустите его"),
            ("port443", "Порт 443/tcp занят: проброс tcp/443.", "порт 443/tcp занят", "сертификат без сайта"),
            ("port80", "Порт 80/tcp занят: nginx (pid 812).", "порт 80/tcp занят", "сертификат без сайта"),
            ("port443", "Порт 80 в тексте, но reason — port443.", "порт 443/tcp занят", "сертификат без сайта"),
            ("neighbour", "На 443 уже настроен nginx соседа (goVLESS).", "на сервере уже есть сайт другой программы",
             "сертификат без сайта"),
            ("apt", "Не удалось поставить nginx: E: x", "не удалось поставить nginx или git", "через несколько минут"),
            ("nginx_test", "nginx не принял настройки сайта: x.", "nginx не принял настройки", "Прежние настройки"),
        ]
        self.make_app()
        for reason, text, why, todo in cases:
            with self.subTest(reason=reason, text=text):
                with mock.patch.object(ops, "mode", side_effect=ops.Refusal(reason, text, code=CONFLICT)):
                    self.press("sdmy:on")
                last = self.last_text()
                self.assertIn("❌ Сайт не включён: %s" % why, last)
                self.assertIn(text.replace("<", "&lt;"), last)
                self.assertIn(todo, last)
                self.assertEqual([d for _, d in self.buttons()], ["sdm", "sd"])
        tpl = [("nokey", "нет ключа GitHub для каталога шаблонов", "SSH-меню <b>8 → 6</b>"),
               ("net", "нет связи с GitHub", "Проверьте сеть сервера"),
               ("no_index", "в нём нет готовой страницы index.html", "Выберите другой шаблон"),
               ("too_big", "он слишком большой", "Выберите другой шаблон"),
               ("bad_repo", "у него неверный адрес репозитория", "Выберите другой шаблон")]
        for tr, why, todo in tpl:
            with self.subTest(template_reason=tr):
                err = ops.Refusal("template", "Шаблон сайта не развёрнут: x. Сайт не включён.", code=CONFLICT,
                                  extra={"template_reason": tr, "template_id": None})
                with mock.patch.object(ops, "mode", side_effect=err):
                    self.press("sdmy:on")
                last = self.last_text()
                self.assertIn("❌ Сайт не включён: шаблон не поставлен — %s." % why, last)
                self.assertIn(todo, last)
                self.assertIn("Всё осталось как было", last)
        with mock.patch.object(ops, "mode", side_effect=KaskadError("Шаблон сайта не развёрнут: нет связи")):
            self.press("sdmy:on")
        self.assertIn("❌ Сайт не включён: Шаблон сайта не развёрнут: нет связи", self.last_text())

    def test_old_buttons(self):
        self.make_app()
        for data in ("sdmy:maybe", "sdtc:x:1", "sdtc:99:1", "sdt:x", "sdts:x", "sdti:000000000000"):
            with self.subTest(data=data):
                self.press(data)
                self.assertEqual(self.last_text(), "⌛ Эта кнопка из прежнего меню — откройте меню заново: /menu")


class TemplatesTest(CoverCase):
    def test_categories_by_8_templates_by_8_previews_as_links(self):
        self.make_app()
        self.press("sdt:1")
        self.assertIn("🎨 <b>Шаблоны сайта</b> — 38 шаблонов", self.last_text())
        data = [d for _, d in self.buttons()]
        self.assertEqual(data, ["sdtc:%d:1" % i for i in range(8)] + ["sdt:2", "sdtr", "sdtq", "sd"])
        self.assertIn("Бизнес и корпоративные (20)", [t for t, _ in self.buttons()])
        self.press("sdt:2")
        self.assertEqual([d for _, d in self.buttons()], ["sdtc:8:1", "sdtc:9:1", "sdt:1", "sdtr", "sdtq", "sd"])
        self.press("sdtc:0:1")
        text = self.last_text()
        self.assertIn("🎨 <b>Бизнес и корпоративные</b> — 20, страница 1 из 3", text)
        self.assertIn('1. <a href="https://example.org/business/1/?a=1&amp;b=2">Бизнес и корпоративные шаблон 1</a> '
                      '— Корпоративный сайт', text)
        data = [d for _, d in self.buttons()]
        self.assertEqual(data[:8], ["sdti:" + self.key("business", i) for i in range(1, 9)])
        self.assertEqual(data[8:], ["sdtc:0:2", "sdtr", "sdtq:0", "sdt:1", "sd"])
        self.assertEqual(self.tg.sent("editMessageText")[-1].params.get("link_preview_options"), {"is_disabled": True})
        self.press("sdtc:0:2")
        self.assertEqual([d for _, d in self.buttons()][8:10], ["sdtc:0:1", "sdtc:0:3"])
        self.press("sdtc:0:3")
        self.assertIn("страница 3 из 3", self.last_text())
        self.assertEqual(len(self.data_of("sdti:")), 4)
        for d in self.all_data():
            self.assertLessEqual(len(d.encode("utf-8")), 64, d)

    def test_card_and_replace_in_site_mode(self):
        self.site_on()
        self.make_app()
        key = self.key("landing", 2)
        self.press("sdti:" + key)
        text = self.last_text()
        self.assertIn("🎨 <b>Лендинги шаблон 2</b>", text)
        self.assertIn("Категория: Лендинги", text)
        self.assertIn("Лицензия: MIT · авторы: fx", text)
        self.assertIn('🔗 <a href="https://example.org/landing/2/?a=1&amp;b=2">Превью шаблона</a>', text)
        self.assertIn("⚠️ Текущий сайт «demo» заменится этим шаблоном; прежний сохранится в резервной копии.", text)
        self.assertEqual([d for _, d in self.buttons()], ["sdty:" + key, "sdt:1", "sd"])
        tid = T.by_short_key(key)
        got = {"ok": True, "id": tid, "name": "Лендинги шаблон 2", "fallback": False,
               "backup": "/var/backups/kaskad/site/previous", "warnings": []}
        with mock.patch.object(ops, "template_set", return_value=got) as ts:
            self.press("sdty:" + key)
        ts.assert_called_once_with(self.runner, tid)
        texts = self.texts()
        self.assertIn("⏳ Ставлю шаблон «Лендинги шаблон 2»", texts[-2])
        self.assertIn("✅ Шаблон «Лендинги шаблон 2» на сайте.", texts[-1])
        self.assertIn("резервной копии: <code>/var/backups/kaskad/site/previous</code>", texts[-1])
        bad = {"ok": False, "id": tid, "reason": "no_index", "text": "В шаблоне нет index.html."}
        with mock.patch.object(ops, "template_set", return_value=bad):
            self.press("sdty:" + key)
        self.assertIn("❌ Шаблон не поставлен: В шаблоне нет index.html. Прежний сайт не тронут.", self.last_text())
        with mock.patch.object(ops, "template_set", side_effect=KaskadError(
                "Каталог в закрытом репозитории", data={"reason": "nokey"})):
            self.press("sdty:" + key)
        self.assertIn("нет ключа GitHub", self.last_text())
        self.assertIn("<b>8 → 6</b>", self.last_text())

    def test_without_the_site_the_template_switches_it_on(self):
        self.make_app()
        key = self.key("blog", 1)
        self.press("sdti:" + key)
        self.assertIn("Сайт-маскировка включится с этим шаблоном", self.last_text())
        with self.fake_site() as dep:
            self.press("sdty:" + key)
        dep.assert_called_once_with(self.runner, T.by_short_key(key))
        self.assertIn("⏳ Включаю сайт-маскировку", self.texts()[-2])
        self.assertIn("✅ Сайт-маскировка включена", self.last_text())

    def test_random(self):
        self.make_app()
        with mock.patch.object(T, "pick_random", return_value=T.by_short_key(self.key("travel", 1))):
            self.press("sdtr")
        self.assertIn("🎨 <b>Путешествия шаблон 1</b>", self.last_text())
        self.assertEqual([d for _, d in self.buttons()], ["sdty:" + self.key("travel", 1), "sdtr", "sdt:1", "sd"])

    def test_search_by_text_with_pages(self):
        self.make_app()
        self.press("sdtq")
        self.assertIn("🔍 Пришлите слово для поиска по всему каталогу", self.last_text())
        self.say("шаблон")                                    # every name has it: 38 → 5 pages of 8
        text = self.last_text()
        self.assertIn("🔍 «шаблон» — найдено 38, страница 1 из 5", text)
        self.assertEqual(len(self.data_of("sdti:")), 8)
        self.press("sdts:2")
        self.assertIn("страница 2 из 5", self.last_text())
        self.say("у моря")                                    # another word — a new search (the description)
        self.assertIn("🔍 «у моря» — найдено 2", self.last_text())
        self.press("sdti:" + self.key("restaurant", 1))       # a card ends the search
        self.press("sdts:2")
        self.assertIn("⌛ Поиск устарел", self.last_text())

    def test_search_in_a_category(self):
        self.make_app()
        self.press("sdtq:2")
        self.assertIn("в категории «Рестораны и еда»", self.last_text())
        self.say("моря")
        self.assertIn("🔍 «моря» в «Рестораны и еда» — найдено 2", self.last_text())

    def test_the_catalog_is_brought_by_a_job(self):
        os.unlink(T.catalog_path())
        self.make_app()
        with mock.patch.object(T, "fetch_catalog", side_effect=lambda runner=None: self.put_catalog()) as fc:
            self.press("sdt:1")
        fc.assert_called_once()
        texts = self.texts()
        self.assertIn("⏳ Загружаю каталог шаблонов сайта", texts[-2])
        self.assertIn("🎨 <b>Шаблоны сайта</b>", texts[-1])

    def test_no_github_key_names_the_way(self):
        os.unlink(T.catalog_path())
        self.make_app()
        err = KaskadError("Каталог шаблонов сайта лежит в закрытом репозитории Kaskad.", data={"reason": "nokey"})
        with mock.patch.object(T, "fetch_catalog", side_effect=err):
            self.press("sdt:1")
        self.assertIn("❌ Каталог шаблонов не скачать: нет ключа GitHub", self.last_text())
        self.assertIn("SSH-меню: <b>8 → 6</b>", self.last_text())


class TemplateHealthTest(CoverCase):
    """«Жив ли шаблон» (3.2.21): the list and the card at once by the cache («⏳ Проверяю…»), the check of the page,
    the same message drawn again; removed — «❌ удалён», no link, no «Поставить»; the preview taken down — no link."""

    def url(self, cid, i):
        return "https://github.com/fx/%s%d" % (cid, i)

    def test_list_marks_removed_and_without_preview(self):
        self.repo[self.url("blog", 1)] = "gone"
        self.web.answers["https://example.org/blog/2/?a=1&b=2"] = 404
        self.make_app()
        self.press("sdtc:6:1")                                        # «Блоги и журналы»: 2 шаблона
        edits = [c.text for c in self.tg.sent("editMessageText")]
        self.assertIn("⏳ Проверяю, живы ли шаблоны…", edits[-2])     # at once — by the cache
        self.assertIn('1. <a href="https://example.org/blog/1/?a=1&amp;b=2">', edits[-2])
        text = self.last_text()                                       # then — the same message drawn again
        self.assertEqual(self.tg.sent("editMessageText")[-1].params.get("message_id"), 77)
        self.assertIn("1. ❌ <s>Блоги и журналы шаблон 1</s> — удалён автором, выбрать нельзя", text)
        self.assertIn("2. Блоги и журналы шаблон 2 — образец снят автором, шаблон ставится", text)
        self.assertNotIn("example.org/blog/", text)
        self.assertNotIn("⏳", text)
        self.assertEqual([t for t, _ in self.buttons()][:2], ["1 · ❌ Блоги и журналы шаблон 1", "2 · Блоги и журналы шаблон 2"])
        n = len(self.tg.sent("editMessageText"))
        self.press("sdtc:6:1")                                        # known now: one drawing, no check
        self.assertEqual(len(self.tg.sent("editMessageText")), n + 1)
        self.assertNotIn("⏳", self.last_text())

    def test_card_of_a_removed_template(self):
        self.repo[self.url("travel", 1)] = "gone"
        self.make_app()
        key = self.key("travel", 1)
        self.press("sdti:" + key)
        text = self.last_text()
        self.assertIn("❌ Шаблон удалён автором — выбрать нельзя.", text)
        self.assertNotIn("Превью", text)
        self.assertEqual([d for _, d in self.buttons()], ["sdt:1", "sd"])          # no «Поставить»
        with self.fake_site() as dep:
            self.press("sdty:" + key)                                 # an old button: refused, no job
        dep.assert_not_called()
        self.assertIn("удалён автором — выбрать нельзя", self.last_text())

    def test_card_without_preview_still_installs(self):
        self.web.answers["https://example.org/travel/2/?a=1&b=2"] = 404
        self.make_app()
        key = self.key("travel", 2)
        self.press("sdti:" + key)
        text = self.last_text()
        self.assertIn("Образец снят автором, сам шаблон ставится.", text)
        self.assertNotIn("example.org", text)
        self.assertEqual([d for _, d in self.buttons()][0], "sdty:" + key)

    def test_a_late_answer_clears_the_hourglass_when_the_person_went_to_a_new_message(self):
        """A press that answers with a new message (the reply keyboard, «_new») leaves the list where it was: the late
        answer draws it without «⏳»."""
        later = []
        self.make_app()
        with mock.patch.object(bsite, "_spawn", later.append):
            self.press("sdtc:6:1")
            self.press("sd_new")                                      # a new message: the list stays above it
        self.assertNotIn("⏳", self.last_text())
        later[0]()
        self.assertTrue(self.app.scheduler.wait_idle(15))
        last = self.tg.sent("editMessageText")[-1]
        self.assertEqual(last.params.get("message_id"), 77)
        self.assertNotIn("⏳", last.text)
        self.assertIn("Название — ссылка на превью", last.text)

    def test_edits_are_counted_for_the_last_messages_only(self):
        self.make_app()
        from kaskad.bot import app as bapp
        for mid in range(bapp.EDITS_KEPT + 10):
            self.app.note_edit(1, mid)
        self.assertLessEqual(len(self.app.edits), bapp.EDITS_KEPT)
        self.assertEqual(self.app.edit_mark(1, bapp.EDITS_KEPT + 9), 1)

    def test_a_late_answer_does_not_cover_another_screen(self):
        later = []
        self.make_app()
        with mock.patch.object(bsite, "_spawn", later.append):
            self.press("sdtc:6:1")
            self.assertIn("⏳ Проверяю", self.last_text())
            self.press("sd")                                          # the person went on
        screen = self.last_text()
        later[0]()                                                    # the check ends now
        self.assertTrue(self.app.scheduler.wait_idle(15))
        self.assertEqual(self.last_text(), screen)


class PanelAddressTest(CoverCase):
    def test_masked_shown_and_deleted_after_5_minutes(self):
        self.site_on()
        path = conf.load()["proxy"]["path"]
        self.make_app()
        self.press("sdp")
        self.assertIn("🔗 <b>Адрес панели на домене</b>", self.last_text())
        self.assertIn("••••", self.last_text())
        self.assertNotIn(path, self.last_text())
        self.assertEqual([d for _, d in self.buttons()], ["sdps", "sdpr", "sd"])
        self.press("sdps")
        shown = self.tg.sent("sendMessage")[-1]
        self.assertIn("https://vpn.example.com/%s/" % path, shown.text)
        self.assertIn("Адрес панели — секрет", shown.text)
        self.assertIn("удалится само через 5 минут", shown.text)
        self.assertEqual(len(store.Expiring()._load()), 1)
        self.now += 299
        self.app.expire_due()
        self.assertEqual(self.tg.sent("deleteMessage"), [])
        self.now += 2
        self.poll()                                           # after every poll: what is due is deleted
        dels = self.tg.sent("deleteMessage")
        self.assertEqual(len(dels), 1)
        self.assertEqual(dels[0].params["chat_id"], OWNER)
        self.assertEqual(store.Expiring()._load(), [])

    def test_shutdown_deletes_the_secret_at_once(self):
        self.site_on()
        self.make_app()
        self.press("sdps")
        self.app.shutdown()
        self.assertEqual(len(self.tg.sent("deleteMessage")), 1)

    def test_rotate_after_the_warning(self):
        self.site_on()
        old = conf.load()["proxy"]["path"]
        self.make_app()
        self.press("sdpr")
        self.assertIn("<b>старый перестанет открываться сразу</b>", self.last_text())
        self.assertEqual([d for _, d in self.buttons()], ["sdpry", "sdp"])
        self.assertEqual(conf.load()["proxy"]["path"], old)
        with self.fake_site():
            self.press("sdpry")
        self.assertIn("⏳ Меняю адрес панели", self.texts()[-2])
        self.assertIn("✅ Новый адрес панели: <code>https://vpn.example.com/app-", self.last_text())
        new = conf.load()["proxy"]["path"]
        self.assertNotEqual(new, old)
        self.assertNotIn(new, self.last_text())
        with mock.patch.object(ops, "rotate_panel_path", side_effect=ops.Refusal(
                "nginx_test", "nginx не принял новый путь панели: x. Старый путь оставлен.", code=CONFLICT)):
            self.press("sdpry")
        self.assertIn("❌ Адрес не сменён: nginx не принял настройки.", self.last_text())
        self.assertIn("Старый путь оставлен", self.last_text())

    def test_without_the_site(self):
        self.make_app()
        self.press("sdp")
        self.assertIn("❌ Панель не открыта из интернета: нет ни сайта на домене, ни доступа по порту.", self.last_text())


class CheckTest(CoverCase):
    def test_verdicts_in_words(self):
        cases = [({"ok": True, "verdict": "ok"}, "✅ Сайт в порядке"),
                 ({"ok": False, "verdict": "dns_mismatch"}, "DNS домена не ведёт на этот сервер"),
                 ({"ok": False, "verdict": "no_cert"}, "Нет действующего сертификата"),
                 ({"ok": False, "verdict": "http_error", "http": {"root": 502, "redirect": 200}},
                  "(главная — код 502, http — код 200)"),
                 ({"ok": False, "verdict": "no_site"}, "Сайт-маскировка выключена"),
                 ({"ok": False, "verdict": "cert_expiring", "days_left": 9}, "осталось 9 дн."),
                 ({"ok": False, "verdict": "panel_unreachable"}, "панель по секретному адресу не отвечает")]
        self.make_app()
        for doc, phrase in cases:
            with self.subTest(verdict=doc["verdict"]):
                with mock.patch.object(ops, "check", return_value=doc):
                    self.press("sdq")
                self.assertIn("⏳ Проверяю главную страницу", self.texts()[-2])
                self.assertIn("🔎 <b>Проверка сайта</b>", self.last_text())
                self.assertIn(phrase, self.last_text())
                self.assertEqual([d for _, d in self.buttons()], ["sdq", "sd"])

    def test_clear_names_the_site(self):
        self.site_on()
        self.make_app()
        self.press("sdx")
        self.assertIn("сайт-маскировка выключится", self.last_text())


class EnglishTest(CoverCase):
    """KASKAD_LANG=en (3.3.2): templates, the card, the late redraw of «жив ли шаблон» (a thread of its own: the
    language of the answer is carried into it), the cover site on and the check — in English; the Cyrillic left is
    the data of the catalog (names, categories, descriptions)."""

    DATA = re.compile("|".join(re.escape(x) for x in sorted(
        [n for _, n in CATS] + ["Сайт ресторана у моря", "Корпоративный сайт", "шаблон"], key=len, reverse=True)))

    def setUp(self):
        super(EnglishTest, self).setUp()
        self.write_config(KASKAD_LANG="en")

    def assertEnglish(self, *texts):
        for t in texts + tuple(t for t, _ in self.buttons()):
            self.assertIsNone(re.search("[А-Яа-яЁё]", self.DATA.sub("", t)), t)

    def test_categories_list_and_the_late_redraw(self):
        self.repo["https://github.com/fx/blog1"] = "gone"
        self.make_app()
        self.press("sdt:1")
        text = self.last_text()
        self.assertIn("🎨 <b>Site templates</b> — 38 templates with free licenses.\nChoose a category (1 of 2)", text)
        self.assertEqual([t for t, _ in self.buttons()][-4:], ["Next ➡️", "🎲 Random", "🔍 Search", "⬅️ Domain"])
        self.assertEnglish(text)
        # the check and the redraw run in threads with no language of their own (here: Russian) — the language of
        # the answer goes with them
        with mock.patch.object(bsite, "_spawn", lambda fn: _in_russian(self.app, fn)):
            self.press("sdtc:6:1")
        edits = [c.text for c in self.tg.sent("editMessageText")]
        self.assertIn("⏳ Checking whether the templates are still there…", edits[-2])
        self.assertTrue(self.app.scheduler.wait_idle(15))
        text = self.last_text()
        self.assertIn("🎨 <b>Блоги и журналы</b> — 2, page 1 of 1", text)
        self.assertIn("— removed by its author, can’t be chosen", text)
        self.assertIn("The name links to the preview; choose a template with a button.", text)
        self.assertEnglish(text)

    def test_card_install_and_the_check(self):
        self.make_app()
        key = self.key("landing", 2)
        self.press("sdti:" + key)
        text = self.last_text()
        self.assertIn("Category: Лендинги", text)
        self.assertIn("License: MIT · authors: fx", text)
        self.assertIn('🔗 <a href="https://example.org/landing/2/?a=1&amp;b=2">Template preview</a>', text)
        self.assertIn("The masking site will be turned on with this template", text)
        self.assertEqual([t for t, _ in self.buttons()], ["✅ Install", "⬅️ Templates", "⬅️ Domain"])
        self.assertEnglish(text)
        with self.fake_site():
            self.press("sdty:" + key)
        texts = self.texts()
        self.assertIn("⏳ Turning on the masking site", texts[-2])
        self.assertIn("✅ Masking site is on: <code>https://vpn.example.com/</code> — template “demo”.", texts[-1])
        self.assertIn("Web panel on the domain: <code>https://vpn.example.com/app-", texts[-1])
        self.assertEnglish(texts[-2], texts[-1])
        for doc, phrase in (({"verdict": "cert_expiring", "days_left": 1}, "expires soon (1 day left)"),
                            ({"verdict": "http_error", "http": {"root": 502, "redirect": 200}},
                             "(home page — code 502, http — code 200)"),
                            ({"verdict": "strange"}, "❌ The check ended with an unknown result: strange.")):
            with self.subTest(verdict=doc["verdict"]):
                with mock.patch.object(ops, "check", return_value=doc):
                    self.press("sdq")
                self.assertIn("⏳ Checking the home page, the panel", self.texts()[-2])
                self.assertIn("🔎 <b>Site check</b>\n", self.last_text())
                self.assertIn(phrase, self.last_text())
                self.assertEnglish(self.texts()[-2], self.last_text())

    def test_refusal_and_search(self):
        self.make_app()
        err = ops.Refusal("template", "x", code=CONFLICT, extra={"template_reason": "nokey", "template_id": None})
        with mock.patch.object(ops, "mode", side_effect=err):
            self.press("sdmy:on")
        self.assertIn("❌ Site not turned on: the template wasn’t installed — no GitHub key for the template catalog.",
                      self.last_text())
        self.assertIn("SSH menu <b>8 → 6</b>", self.last_text())
        self.assertEnglish(self.last_text())
        self.press("sdtq:2")
        self.assertIn("🔍 Send a word to search in the “Рестораны и еда” category", self.last_text())
        self.say("моря")
        self.assertIn("🔍 “моря” in “Рестораны и еда” — 2 found, page 1 of 1", self.last_text())
        self.assertIn("For another word, just send it.", self.last_text())


def _in_russian(app, fn):
    """Run ``fn`` (and what it hands to the chat's lane) where the language is Russian."""
    from kaskad.bot.lang import use_lang
    with use_lang("ru"), mock.patch.object(app.scheduler, "submit", lambda chat, f, batch=None: f()):
        fn()
