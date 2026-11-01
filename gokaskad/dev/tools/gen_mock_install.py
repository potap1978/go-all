#!/usr/bin/env python3
"""dev-mock-install.js — «Установите приложение» for the dev mocks of the panel and the wizard (``?mock=1``), made from
the real catalogs (gw/install_apps.json, vendor/clients.json) by ``kaskad.gw.install_apps.block``: the mocks show the
same apps and links as the server, no copy to drift. ``--check`` — exit 1 when the file is stale (tests).

    python3 tools/gen_mock_install.py [--check]
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
OUT = os.path.join(ROOT, "lib", "kaskad", "panel", "static", "dev-mock-install.js")


def build():
    from kaskad.gw import install_apps, issue_os, model
    from kaskad.i18n import tr, use_lang
    doc = {}
    for lang in ("ru", "en"):
        with use_lang(lang):
            blocks = {}
            for kind in sorted(issue_os.TITLES):
                for p in model.PLATFORMS:
                    b = install_apps.block(kind, p)
                    if b["supported"]:
                        blocks["%s:%s" % (kind, p)] = b
            # the support table «платформа × протокол» (SPEC-issue-fallbacks.md): support of every pair as the server
            # gives it, the order of the platform and the words of the alternatives — KaskadMockSupport builds
            # support + alternatives for the mock's own server and device
            rows = {}
            for p in model.PLATFORMS:
                rows[p] = []
                for k in install_apps.order(p):
                    sp = install_apps.support(k, p, [], where="panel")
                    rows[p].append([k, sp["level"], sp["min"], sp["guide"], sp["note"], sp["links"], sp["unverified"]])
            words = dict((k, tr("gw_issue.support." + k, os="{os}", proto="{proto}"))
                         for k in ("workaround", "none", "nothing", "alt_ready", "alt_device", "alt_server"))
            doc[lang] = {"blocks": blocks, "none": tr("gw_issue.install.none", proto="{proto}", os="{os}"),
                         "titles": dict(issue_os.TITLES),
                         "os": dict((p, install_apps.platform_title(p)) for p in model.PLATFORMS),
                         "rows": rows, "words": words,
                         # приложения подписки по платформам — как userpage.sub_status «apps» (вкладка «Подписка»)
                         "subs": install_apps.sub_blocks()}
    body = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ("/* Сгенерировано tools/gen_mock_install.py из gw/install_apps.json и vendor/clients.json — не править руками.\n"
            "   «Установите приложение» и таблица поддержки для макетов панели и мастера (?mock=1): KaskadMockInstall(kind, os, lang),\n"
            "   KaskadMockSupport(kind, os, lang, server, device), KaskadMockSubApps(lang). */\n"
            "(function (g) {\n  'use strict';\n  var D = %s;\n"
            "  g.KaskadMockInstall = function (kind, os, lang) {\n"
            "    var L = D[lang === 'en' ? 'en' : 'ru'], b = L.blocks[kind + ':' + os];\n"
            "    if (b) { return JSON.parse(JSON.stringify(b)); }\n"
            "    return { supported: false, builtin: '', apps: [], text: L.none.replace('{proto}', L.titles[kind] || kind)"
            ".replace('{os}', L.os[os] || os) };\n"
            "  };\n"
            "  g.KaskadMockSupport = function (kind, os, lang, server, device) {\n"
            "    var L = D[lang === 'en' ? 'en' : 'ru'], W = L.words, rows = L.rows[os] || [], T = function (k) { return L.titles[k] || k; };\n"
            "    var w = function (key, k) { return W[key].replace('{os}', L.os[os] || os).replace('{proto}', T(k)); };\n"
            "    var row = rows.filter(function (r) { return r[0] === kind; })[0] || [kind, 'none', '', '', '', [], ''];\n"
            "    var s = { level: row[1], min: row[2], guide: row[3], note: row[4], links: row[5], unverified: row[6], levels: {}, text: '' };\n"
            "    Object.keys(L.titles).forEach(function (k) { s.levels[k] = 'none'; });\n"
            "    rows.forEach(function (r) { s.levels[r[0]] = r[1]; });\n"
            "    var alts = rows.filter(function (r) { return r[0] !== kind && r[1] !== 'none'; }).map(function (r) {\n"
            "      var k = r[0], st = server.indexOf(k) >= 0 ? (device.indexOf(k) >= 0 ? 'ready' : 'device_off') : 'server_off';\n"
            "      return { proto: k, title: T(k), level: r[1], state: st, note: r[4],\n"
            "        text: w(st === 'ready' ? 'alt_ready' : st === 'device_off' ? 'alt_device' : 'alt_server', k) };\n"
            "    });\n"
            "    if (s.level === 'workaround') { s.text = w('workaround', kind); }\n"
            "    if (s.level === 'none') { s.text = alts.length ? w('none', kind) : w('nothing', kind); }\n"
            "    return { support: s, alternatives: alts };\n"
            "  };\n"
            "  g.KaskadMockSubApps = function (lang) { return JSON.parse(JSON.stringify(D[lang === 'en' ? 'en' : 'ru'].subs || {})); };\n"
            "})(window);\n") % body


def main():
    text = build()
    old = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else None
    if "--check" in sys.argv[1:]:
        if old != text:
            print("dev-mock-install.js устарел — python3 tools/gen_mock_install.py")
            return 1
        return 0
    if old != text:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(text)
    print("dev-mock-install.js: %d байт" % len(text.encode("utf-8")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
