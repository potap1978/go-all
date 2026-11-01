"""The card «Уведомления» of the panel (T8, SE §7.5) and the kinds the server knows (``gw.model.NOTIFY_PUSH``),
the junction with the health of the node (SPEC-node-score §3.5, A6/A7): every kind ``host_*`` is a switch of the card
with its own label ``nt.k.<kind>`` in RU and EN, and every kind of the card has a label.

The full rule came with T8m (REV-s1-t8-a Б-1: ``node_online`` is not a kind of the server): the card shows exactly the
kinds the server knows, every one of them has a label, and the mock answers every one of them (``nfKinds`` shows only
the kinds with a boolean in the answer) — ``test_server_kinds_all_labelled`` (the expected-failure marker was removed in
the release branch 3.3.7, step 4).
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CYR = re.compile("[А-Яа-яЁё]")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def card_kinds():
    """The kinds of ``NOTIFY_KEY`` of app.js -> {kind: key of the label}."""
    m = re.search(r"const NOTIFY_KEY = \{(.*?)\};", read("app.js"), flags=re.S)
    assert m, "NOTIFY_KEY не найден в app.js"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return dict(re.findall(r'([a-z_]+): "([^"]+)"', body))


def labels(lang):
    return dict(re.findall(r'^  "(nt\.k\.[a-z_]+)": "([^"]+)"', read("i18n-%s.js" % lang), flags=re.M))


class NotifyKindsTest(unittest.TestCase):
    def test_host_kinds_are_switches_of_the_card(self):
        from kaskad.gw import hostmon, model
        kinds = card_kinds()
        for kind in hostmon.HOST_KINDS:
            self.assertIn(kind, model.NOTIFY_PUSH, kind)                 # the server knows the switch
            self.assertEqual(kinds.get(kind), "nt.k." + kind, kind)       # the card shows it, by its own label

    def test_every_kind_of_the_card_has_a_label(self):
        kinds = card_kinds()
        for lang in ("ru", "en"):
            have = labels(lang)
            for kind, key in kinds.items():
                self.assertTrue(have.get(key), (lang, kind))
                self.assertEqual(bool(CYR.search(have[key])), lang == "ru", (lang, kind))
            self.assertEqual(len(set(have[k] for k in kinds.values())), len(kinds), lang)   # no two alike

    def test_server_kinds_all_labelled(self):
        """T8m: the card = the kinds of the server (no ``node_online``), each with a label (``coalesce`` — a switch of
        its own, not a kind)."""
        from kaskad.gw import model
        kinds = card_kinds()
        server = set(k for k in model.NOTIFY_PUSH if k != "coalesce")
        self.assertEqual(set(kinds), server)
        mock = read("dev-mock.js")
        block = re.search(r"db\.notify = db\.notify \|\| \{ push: \{(.*?)\}, flap_summary", mock, re.S)
        self.assertTrue(block, "the mock of /api/notify")
        self.assertEqual(set(re.findall(r"(\w+):", re.sub(r"//[^\n]*", "", block.group(1)))) - {"coalesce"}, server)   # the mock = the server
        for lang in ("ru", "en"):
            have = labels(lang)
            for kind in sorted(server):
                self.assertTrue(have.get("nt.k." + kind), (lang, kind))


if __name__ == "__main__":
    unittest.main()
