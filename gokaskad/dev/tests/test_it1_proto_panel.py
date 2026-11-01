"""Круг 1 правок по видео владельца (27.10), пакет Л2 — окно «Установить …?» (№14) без DOM: настоящие куски app.js в
node (tests/jsparts.py). Сервер — tests/panel/test_it1_proto.py.

* поле порта сразу заполнено ответом сервера (GET gw/inbounds/<kind>/port), «необязательно» и «Если оставить
  пустым…» сняты (подсказка «пустым» — только если сервер не ответил);
* «↻» — случайный свободный порт (подпись для читалки экрана RU/EN);
* рекомендованные порты — кнопками, занятый — недоступен, с подписью «занят» и причиной;
* введённый порт проверяется на сервере (?port=N), ответ — строкой под полем.
Т3 (вариант Б r3, О1–О4): кнопки и поле стали выбором «Рекомендуемый / Прежний / Ближайший / Случайный / Свой порт»
(radio), номер — на «Установить на N»; варианты — ibPortOpts, тесты — tests/test_it1_t3_proto.py.
Node-less: skipped."""

import re
import shutil
import unittest

from tests import jsparts
from tests.test_it1_logic_panel import STUB, read, run


@unittest.skipUnless(shutil.which("node"), "нет node")
class PortRecsTest(unittest.TestCase):
    def test_recommended_buttons_and_notes(self):
        js = read("app.js")
        parts = ["const validPort = (v) => /^\\d{1,5}$/.test(String(v).trim()) && Number(v) >= 1 && Number(v) <= 65535;",
                 jsparts.function(js, "ibPortRecs")]
        out = run(STUB, parts, r"""
console.log(JSON.stringify({
  recs: ibPortRecs({ recommended: [{ port: 1194, free: true, why: "" }, { port: 8443, free: false, why: "nginx" },
                                   { port: 0, free: true }, { port: "x" }, null, { port: 70000, free: true }] }),
  none: ibPortRecs(null), bad: ibPortRecs({ recommended: "1194" }),
}));
""")
        self.assertEqual(out["recs"], [{"port": "1194", "free": True, "why": ""}, {"port": "8443", "free": False, "why": "nginx"}])
        self.assertEqual((out["none"], out["bad"]), ([], []))


class InstallDialogTest(unittest.TestCase):
    def setUp(self):
        self.fn = jsparts.function(read("app.js"), "openInstall")

    def test_the_field_is_filled_by_the_server(self):
        self.assertIn("API.inbounds.offer(r.kind, q)", self.fn)
        self.assertIn("API.inbounds.offer(r.kind, {}).catch(() => null).then(", self.fn)
        self.assertIn("const m = ibPortOpts(doc, rnd);", self.fn)     # подобранный сервером — выбран сразу (m.sel)
        self.assertNotIn("c.optional", self.fn)                       # без правки: «Порт · необязательно»
        self.assertNotIn("ib.portAuto", self.fn)                      # «Подобрать свободный» в пустом поле
        self.assertIn('ask({ port: v })', self.fn)                    # проверка введённого до установки
        self.assertIn('API.inbounds.offer(r.kind, { random: 1 })', self.fn)
        self.assertIn('icon: "arrow-rotate-right"', self.fn)
        self.assertIn('tip: t("ib.portAnother")', self.fn)            # title и aria-label (U.btn без подписи)
        self.assertIn('disabled: o.free === false && o.kind !== "own" ? true : null', self.fn)   # занятый недоступен
        # REV-it1-b П-1: и после ответа — «Случайный», который сервер не подобрал, недоступен (sync держит disabled)
        self.assertIn('x.r.disabled = o.free === false && o.kind !== "own";', self.fn)
        self.assertIn('t("ib.ppBusy", { why: o.why })', self.fn)                                  # …с причиной
        api = read("app.js")
        self.assertIn('offer: (k, q) => request("GET", "/api/gw/inbounds/" + enc(k) + "/port" + qs(q))', api)

    def test_texts_ru_en(self):
        keys = ("ib.portAnother", "ib.ppFree", "ib.ppBusy", "ib.portChecking", "ib.portWait", "ib.ppRec",
                "ib.keptBusy", "ib.portHint", "ib.portHintKept")
        for lang in ("ru", "en"):
            words = dict(re.findall(r'"(ib\.\w+)": "([^"]+)"', read("i18n-%s.js" % lang)))
            for k in keys:
                self.assertTrue(words.get(k), (lang, k))
            self.assertNotIn("ib.portAuto", words)
        self.assertIn("{why}", dict(re.findall(r'"(ib\.\w+)": "([^"]+)"', read("i18n-en.js")))["ib.ppBusy"])


if __name__ == "__main__":
    unittest.main()
