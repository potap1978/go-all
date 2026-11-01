"""3.3.7.13: значок внутренней группы — роутер вместо метки «РФ» / RU (решение координатора 29.10, макет
kaskad-order r1 В1/В2, `kk-flag`).

Один помощник ядра `domMark` (K.domMark): у группы, её выхода и ключа — карточки и списки групп, «Выходы · час», фишки
окон; в схеме — значок роутера впереди (`ruIco`). Диктору — «Внутренняя группа» / «Domestic group». «⚠РФ» у ресурсов,
запрещённых в РФ (rf.*), — другое, его не трогаем.
"""
import json
import os
import re
import subprocess
import tempfile
import unittest

from tests import jsparts
from tests.test_panel_rf_js import DOM, read

ST = os.path.join(os.path.dirname(__file__), "..", "lib", "kaskad", "panel", "static")


def src(name):
    with open(os.path.join(ST, name), encoding="utf-8") as f:
        return f.read()


def words(name):
    """Значения словаря панели: "ключ": "текст" и 'ключ': 'текст' (graph-i18n)."""
    s = src(name)
    return dict(re.findall(r'(?m)^\s*"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', s) +
                re.findall(r"(?m)^\s*'([^']+)':\s*'((?:[^'\\]|\\.)*)'", s))


def dictionaries():
    out = []
    for root, _dirs, files in os.walk(ST):
        for f in files:
            if re.search(r"i18n-(ru|en)\.js$", f):
                out.append(os.path.relpath(os.path.join(root, f), ST))
    return sorted(out)


def run(script):
    js = read("app.js")
    code = DOM + "\n".join([jsparts.function(js, "el"), jsparts.function(js, "append"), jsparts.function(js, "icon"),
                            jsparts.const(js, "txt"), jsparts.function(js, "domMark")]) + r"""
const t = (key) => key;
window.KaskadIcons = { svg: (name, px) => { const s = document.createElement("svg"); s.setAttribute("data-i", name); s.setAttribute("width", String(px)); return s; } };
const show = (n) => ({ cls: n.className, role: n.getAttribute("role"), aria: n.getAttribute("aria-label"), hid: n.getAttribute("aria-hidden"),
  title: n.getAttribute("title"), text: n.textContent, ico: n.querySelector("svg") ? [n.querySelector("svg").getAttribute("data-i"), n.querySelector("svg").getAttribute("width")] : null });
""" + script
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(code)
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    out = res.stdout.decode("utf-8").strip().splitlines()
    if res.returncode != 0 or not out:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out[-1])


class DomMarkTest(unittest.TestCase):
    def test_helper(self):
        """Роутер без текста; диктору — «Внутренняя группа» (role img); подсказка места; deco — только рисунок."""
        out = run(r"""console.log(JSON.stringify([show(domMark(18, "kk-flag")), show(domMark(12, "kb-xn__rf", { tip: "ovb.xnRfT" })),
  show(domMark(18, "kk-flag", { deco: true }))]));""")
        self.assertEqual(out[0], {"cls": "kk-dom kk-flag", "role": "img", "aria": "ex.domMark", "hid": None, "title": "ex.domMark",
                                  "text": "", "ico": ["router", "18"]})
        self.assertEqual((out[1]["aria"], out[1]["title"], out[1]["ico"]), ("ex.domMark", "ovb.xnRfT", ["router", "12"]))
        self.assertEqual((out[2]["role"], out[2]["aria"], out[2]["hid"], out[2]["title"]), (None, None, "true", None))

    def test_words(self):
        self.assertEqual(words("i18n-ru.js")["ex.domMark"], "Внутренняя группа")
        self.assertEqual(words("i18n-en.js")["ex.domMark"], "Domestic group")
        # «⚠РФ» — пометка ресурсов, запрещённых в РФ: не роль группы, остаётся
        self.assertEqual((words("i18n-ru.js")["rf.mark"], words("i18n-en.js")["rf.mark"]), ("⚠РФ", "⚠RU"))

    def test_no_rf_label_of_the_role(self):
        """Ни одного слова «РФ» / «RU» меткой в словарях панели; старые ключи метки убраны."""
        gone = {"ep.ru.rf", "ovb.xnRf", "graph.ru.tag", "ep.ru.flagT"}
        for name in dictionaries():
            w = words(name)
            self.assertEqual([k for k, v in w.items() if v.strip() in ("РФ", "RU")], [], name)
            self.assertEqual(gone & set(w), set(), name)

    def test_places(self):
        """Где была метка — значок: шапка группы, фишки окон «Новая группа» и «Перенести», ссылка «Внутренние группы»
        (рисунком: рядом сказано словами), окно выхода, «Выходы · час»; в схеме — роутер впереди, без фишки."""
        eg = src("pages/exits-groups.js")
        for frag in ('lead: ru ? K.domMark(18, "kk-eg-head__ic kk-eg-head__ic--rf") : null',
                     'chips: [K.domMark(12, "gx-chip gx-chip--tag")] }], () => st.role',
                     'chips: role === "ru" ? [K.domMark(12, "gx-chip gx-chip--tag")] : []',
                     'K.domMark(18, "kk-flag", { deco: true })',
                     'isRu ? K.domMark(12, "gx-chip gx-chip--tag") : null'):
            self.assertIn(frag, eg)
        self.assertIn('x.ru ? K.domMark(12, "kb-xn__rf", { tip: t("ovb.xnRfT") }) : null', src("pages/ovb.js"))
        g = src("graph.js")
        self.assertNotIn("kg-chip--tag", g)
        self.assertEqual(len(re.findall(r"\bruIco\(", g)), 3)   # объявление, узел, строка списка
        self.assertRegex(src("app.js"), r"window\.KaskadPanel = Object\.freeze\(\{ el, t, tn, icon, domMark,")


if __name__ == "__main__":
    unittest.main()
