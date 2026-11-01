"""Kaskad 3.3.6, the stand 3.3.5 П7: the panel lists the people and devices whose route is this exit only, names of one
kind in one go — «устройств «sub1», «v-vless», «a-vless»», not «устройства «sub1», устройства «v-vless», …»; in
English «devices “a”, “b”». One name — as before. The form of the word is «one name / several» (REV-336 З-2),
in Russian «людей …», «устройств …» (REV-336-nits №2). The question of deleting a subscription (its keys — many exits)
lists each name once."""
# k3-select: files=lib/kaskad/panel/static/i18n-*.js
import json
import os
import subprocess
import tempfile
import unittest

from tests import jsparts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def pinned(lang, ids, people, default=None):
    js = read("app.js")
    script = """
global.window = {};
require(%s);
require(%s);
const DICT = Object.assign({}, window.KASKAD_I18N, %s ? window.KASKAD_I18N_EN : {});
%s
%s
const R = { exits: { data: { defaultRoute: %s } }, people: { loaded: true, data: %s }, gw: { data: null } };
%s
console.log(JSON.stringify(exitPinned(%s)));
""" % (json.dumps(os.path.join(STATIC, "i18n-ru.js")), json.dumps(os.path.join(STATIC, "i18n-en.js")),
       "true" if lang == "en" else "false", jsparts.const(js, "fill"), "const missing = new Set();\n" + jsparts.const(js, "raw"), json.dumps(default), json.dumps(people, ensure_ascii=False),
        "const t = (key, vars) => fill(raw(key), vars);\nconst LANG = %s, fmtInt = String;\n" % json.dumps(lang)
        + jsparts.const(js, "pluralIdx") + "\n" + jsparts.const(js, "tn") + "\n" + jsparts.function(js, "exitPinned"), json.dumps(ids))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
    try:
        r = subprocess.run(["node", f.name], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(f.name)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


def via(eid):
    return {"via": "exit:" + eid, "fail": "block"}


PEOPLE = {"users": [{"name": "Аня", "route": via("e-1")}, {"name": "Петя", "route": via("e-1")},
                    {"name": "Оля", "route": via("e-2")}],
          "devices": [{"name": "sub1", "route": via("e-1")}, {"name": "v-vless", "route": via("e-1")},
                      {"name": "a-vless", "route": via("e-1")}, {"name": "tv", "route": None},
                      {"name": "fa", "route": via("e-2")}]}


class PinnedWordsTest(unittest.TestCase):
    def test_names_of_one_kind_in_one_go(self):
        self.assertEqual(pinned("ru", "e-1", PEOPLE, via("e-1")),
                         ["по умолчанию — для всех, у кого маршрут не задан", "пользователей «Аня», «Петя»",
                          "устройств «sub1», «v-vless», «a-vless»"])
        self.assertEqual(pinned("en", "e-1", PEOPLE)[-2:], ["users “Аня”, “Петя”", "devices “sub1”, “v-vless”, “a-vless”"])

    def test_one_name_as_before(self):
        self.assertEqual(pinned("ru", "e-2", PEOPLE), ["пользователя «Оля»", "устройства «fa»"])
        self.assertEqual(pinned("en", "e-2", PEOPLE), ["user “Оля”", "device “fa”"])
        self.assertEqual(pinned("ru", "e-3", PEOPLE), [])

    def test_the_keys_of_a_subscription(self):
        self.assertEqual(pinned("ru", ["e-1", "e-2"], PEOPLE),
                         ["пользователей «Аня», «Петя», «Оля»", "устройств «sub1», «v-vless», «a-vless», «fa»"])
        self.assertIn("const pinned = exitPinned(x.exits);", read("app.js"))


if __name__ == "__main__":
    unittest.main()
