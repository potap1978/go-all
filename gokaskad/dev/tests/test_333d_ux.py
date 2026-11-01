"""3.3.3 UX-333: the shared components of ux.js — AgentKey, TelegramBot, ProtocolCard (+ the log drawer), ShareMenu,
ExitsInput (design k3/design/ux-333/export-2026-10-12/HANDOFF-ux-333.md, owner's №35–39) and how the panel uses them.

The pure functions and the behaviour run in node over the small DOM of test_332f_give (timers by hand, real Promises);
the texts — RU and EN with the same keys and not a Cyrillic letter in English; app.js — the routes it calls exist in the
panel and every new text key is in both dictionaries."""

import json
import os
import re
import shutil
import unittest

from tests.test_332f_give import DOM_JS, run_node

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CYR = re.compile("[А-Яа-яЁё]")
HAS_NODE = bool(shutil.which("node"))


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def dict_of(name, var):
    src = read(name)
    body = src[src.index("window.%s = " % var) + len("window.%s = " % var):].strip().rstrip(";")
    return json.loads(body)


# timers by hand: the poll of AgentKey and the debounce of the fields wait for tick()
PRE = DOM_JS + r"""
Node_.prototype.nodeType = 1; Text_.prototype.nodeType = 3;
Node_.prototype.focus = function () { document.activeElement = this; };
{ const cl = Object.getOwnPropertyDescriptor(Node_.prototype, "classList").get;
  Object.defineProperty(Node_.prototype, "classList", { get() { const c = cl.call(this), n = this;
    c.remove = (x) => { n.className = (" " + n.className + " ").replace(" " + x + " ", " ").trim(); }; return c; } }); }
PHONE = true;
let TQ = [];
global.setTimeout = (fn) => { TQ.push(fn); return TQ.length; };
global.clearTimeout = () => {};
global.setInterval = () => 1; global.clearInterval = () => {};
const tick = async () => { const q = TQ; TQ = []; q.forEach((f) => f()); await flush(); };
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const txtOf = (n) => n.textContent;
const byCls = (root, c) => root.all((n) => n.classList && n.classList.contains(c));
const btnText = (root, s) => root.all((n) => n.tagName === "BUTTON" && n.textContent.indexOf(s) >= 0)[0];
const input = (n, v) => { n.value = v; fire(n, "input"); };
"""


def prelude(lang="ru"):
    i18n = read("ux-i18n-ru.js") + (("\n" + read("ux-i18n-en.js")) if lang == "en" else "")
    return PRE + i18n + "\n" + read("ux.js") + "\nconst X = window.KaskadUX;\n"


@unittest.skipUnless(HAS_NODE, "no node")
class PureTest(unittest.TestCase):
    def test_models(self):
        out = run_node(prelude() + r"""
const r = {};
r.pub = ["", "ssh-ed25519 AAAAC3Nza kaskad-agent@MacBook-Anna", "-----BEGIN OPENSSH PRIVATE KEY-----\nb3Bl", "hello", "ssh-rsa AAAAB3 x@y\nssh-rsa AAAAB3 z@w",
  "ecdsa-sha2-nistp256 AAAAE2 kaskad-agent@pc"].map((s) => X.pubCheck(s));
r.st = [X.agentState({ keys: [] }), X.agentState({ keys: [{ seen: null }] }), X.agentState({ keys: [{ seen: null }, { seen: { at: 5 } }] })];
r.cl = ["claude-code/2.1", "claude-ai/0.1", "Cursor/1.0", "?/?", "my-agent/3"].map(X.clientName);
const c = { host: "vpn.example.org", claude_code: "claude mcp add kaskad -- ssh root@vpn.example.org kaskad-mcp" };
r.code = X.agentCommands("ru", "win", "code", c);
r.codeMac = X.agentCommands("ru", "mac", "code", c);
r.desk = [X.agentCommands("ru", "mac", "desktop", c), X.agentCommands("ru", "win", "desktop", c), X.agentCommands("ru", "mac", "cursor", c), X.agentCommands("ru", "win", "cursor", c)].map((x) => x.caption);
r.json = JSON.parse(X.agentCommands("en", "mac", "cursor", c).text);
r.keygen = X.keygen;
r.tok = ["123456789:AAH" + "x".repeat(30), "12345:AA" + "x".repeat(30), "123456789:short", " 123456789:AAH" + "y".repeat(31) + " "].map(X.tokenOk);
r.chip = [X.botChip({}, {}), X.botChip({ token: true }, {}), X.botChip({ token: true, chat: true, running: true }, {}),
  X.botChip({ token: true, chat: true, running: true, need_exit: true }, {}), X.botChip({}, { tok: "bad" }), X.botChip({}, { tok: "ok" }),
  X.botChip({ need_exit: true }, { send: "ok" }), X.botChip({}, { tok: "noexit" }),
  X.botChip({ token: true, chat: true, running: true }, { send: "nostart" }), X.botChip({ token: true, chat: true, running: true }, { send: "err" }),
  X.botChip({ token: true }, { tok: "net" }), X.botChip({ token: true }, { tok: "fail" }), X.botChip({}, { tok: "fail" })];
r.tokState = ["bad_token", "bad_format", "net", "net_proxy", "telegram", "", "http"].map(X.tokState);
r.ty = ["", "vpn://AAA", "VLESS://x@y", "[Interface]\nPrivateKey = a", "# x\n[Interface]\n", "https://sub.example.net/s-1/all", "http://1.2.3.4/x", "hello world"].map(X.exitType);
console.log(JSON.stringify(r));
""")
        self.assertEqual([x["state"] for x in out["pub"]], ["empty", "ok", "private", "bad", "bad", "ok"])
        self.assertEqual(out["pub"][1]["name"], "MacBook-Anna")
        self.assertEqual(out["st"], ["none", "key", "ok"])
        self.assertEqual(out["cl"], ["Claude Code", "Claude Desktop", "Cursor", "", "my-agent"])
        self.assertEqual(out["code"]["caption"], "PowerShell")
        # R-333 З6: PowerShell eats a bare «--» of claude.ps1 (npm) — on the Windows tab it is quoted
        self.assertEqual(out["code"]["text"], "claude mcp add kaskad '--' ssh root@vpn.example.org kaskad-mcp")
        self.assertEqual(out["codeMac"]["text"], "claude mcp add kaskad -- ssh root@vpn.example.org kaskad-mcp")   # the server's string
        self.assertEqual(out["desk"], ["~/Library/Application Support/Claude/claude_desktop_config.json", "%APPDATA%\\Claude\\claude_desktop_config.json",
                                       "~/.cursor/mcp.json", "%USERPROFILE%\\.cursor\\mcp.json"])
        args = out["json"]["mcpServers"]["kaskad"]["args"]
        self.assertEqual(out["json"]["mcpServers"]["kaskad"]["command"], "ssh")
        self.assertIn("root@vpn.example.org", args)
        self.assertEqual(args[:2], ["-i", "~/.ssh/kaskad_agent"])
        # the key is made once, without a passphrase; Windows — through cmd /c so that -N "" reaches ssh-keygen from
        # PowerShell 5.1 and 7.3+ alike (cmd.exe gets the legacy argument passing in both)
        self.assertIn('[ -f ~/.ssh/kaskad_agent ] || ssh-keygen -q -t ed25519 -N ""', out["keygen"]["mac"])
        self.assertTrue(out["keygen"]["win"].startswith("cmd /c '"))
        self.assertIn('if not exist "%USERPROFILE%\\.ssh\\kaskad_agent" ssh-keygen -q -t ed25519 -N ""', out["keygen"]["win"])
        self.assertIn('type "%USERPROFILE%\\.ssh\\kaskad_agent.pub"', out["keygen"]["win"])
        # a key without its .pub (R-333 П7): the public half is made again from the private one
        # through a temporary file: a failed ssh-keygen -y leaves no empty .pub, and an empty one is made again (П5′)
        self.assertIn("{ [ -s ~/.ssh/kaskad_agent.pub ] || { ssh-keygen -y -f ~/.ssh/kaskad_agent > ~/.ssh/kaskad_agent.pub.new"
                      " && mv ~/.ssh/kaskad_agent.pub.new ~/.ssh/kaskad_agent.pub; } || { rm -f ~/.ssh/kaskad_agent.pub.new; false; }; }"
                      " && cat", out["keygen"]["mac"])
        self.assertIn('ssh-keygen -y -f "%USERPROFILE%\\.ssh\\kaskad_agent" > "%USERPROFILE%\\.ssh\\kaskad_agent.pub.new" && move /y', out["keygen"]["win"])
        self.assertIn('|| del "%USERPROFILE%\\.ssh\\kaskad_agent.pub.new"))', out["keygen"]["win"])
        self.assertEqual(out["tok"], [True, False, False, True])
        # a failed test is not «working», even with the service running (review №1)
        self.assertEqual(out["chip"], ["off", "wait", "ok", "warn", "err", "wait", "ok", "warn", "wait", "wait", "net",
                                       "wait", "off"])   # a failure of the panel itself is not «нет связи с Telegram»
        # R-333 С2: «токен не подошёл» — only when Telegram refused it
        self.assertEqual(out["tokState"], ["bad", "bad", "noexit", "net", "net", "fail", "fail"])
        self.assertEqual(out["ty"], ["empty", "awg", "vless", "conf", "conf", "sub", "sub", "bad"])

    def test_protocol_model(self):
        out = run_node(prelude("en") + r"""
const base = { kind: "awg3", title: "AmneziaWG 3.1", engine: "модуль ядра", desc: "d", enabled: true, devices: 2, sessions: 1, ports: ["43304/udp"] };
const m = (o, lang) => X.protoModel(Object.assign({}, base, o), lang || "ru");
const r = {
  run: m({ running: true }), off: m({ enabled: false, running: false }),
  busy: m({ running: false, fail: { why: "busy", port: 43304, proto: "udp", line: "Address already in use" } }),
  closed: m({ running: true, fail: { why: "closed", port: 43304, proto: "udp" } }),
  other: m({ running: false, fail: { why: "other", line: "modprobe: FATAL: Module amneziawg not found", human: "Модуль ядра AmneziaWG не загрузился" } }),
  raw: m({ running: false, fail: { why: "other", line: "weird failure", human: "" } }),
  none: m({ running: false, why: "служба завершилась с ошибкой (код 1)" }),
  l2tp: m({ kind: "l2tp", title: "L2TP/IPsec", ports: ["1701/udp", "500/udp", "4500/udp"], fixedPort: true, running: true,
    switches: [{ key: "plain", title: "L2TP без шифрования", on: true, warnOn: "w" }] }),
  l2tpEn: m({ kind: "l2tp", ports: ["1701/udp", "500/udp", "4500/udp"], fixedPort: true, running: true }, "en"),
  wait: m({ running: null, chip: { tone: "off", text: "ждёт шлюза" } }),
  fixedDown: m({ fixedPort: true, running: false, fail: { why: "busy", port: 1701, proto: "udp" } }),
  again: m({ running: false, fail: { why: "other", line: "x" }, againAt: 1760270000 }),
  l2tpClosed: m({ kind: "l2tp", ports: ["1701/udp", "500/udp", "4500/udp"], fixedPort: true, running: true, fail: { why: "closed", port: 1701, proto: "udp" } }),
  cert: m({ kind: "hy2", ports: ["443/udp"], running: true, fail: { why: "cert", port: 443, proto: "udp", line: "", cert: "unreadable",
    human: "Сертификат не подошёл или не найден — выпустите его заново." } }),
};
console.log(JSON.stringify(r));
""")
        self.assertEqual((out["run"]["chip"]["tone"], out["run"]["buttons"], out["run"]["menu"]), ("ok", ["toggle", "port"], ["delete"]))
        self.assertEqual(out["run"]["desc"], "d")
        self.assertEqual((out["off"]["chip"]["text"], out["off"]["buttons"]), ("выключен", ["toggle", "port"]))
        b = out["busy"]
        self.assertEqual((b["chip"]["tone"], b["chip"]["text"], b["buttons"], b["menu"]), ("err", "не работает", ["port", "log"], ["toggle", "delete"]))
        self.assertEqual(b["reason"]["l1"], "Порт 43304 занят другой программой на сервере — смените порт")
        c = out["closed"]
        self.assertEqual((c["chip"]["tone"], c["chip"]["text"], c["buttons"]), ("warn", "нет подключений", ["toggle", "port"]))
        self.assertEqual(c["reason"]["l1"], "Порт может быть закрыт у хостера — откройте 43304/udp")
        o = out["other"]
        self.assertEqual((o["buttons"], o["menu"]), (["restart", "log"], ["toggle", "port", "delete"]))
        self.assertEqual(o["reason"]["l1"], "Модуль ядра AmneziaWG не загрузился")
        self.assertEqual(o["reason"]["l2"], "Из журнала: «modprobe: FATAL: Module amneziawg not found»")
        # КТ3-10: no word for it — the words of «не запустилась», the raw line only under them
        self.assertEqual((out["raw"]["reason"]["l1"], out["raw"]["reason"]["l2"]),
                         ("Служба не запустилась. Подробности — в журнале.", "Из журнала: «weird failure»"))
        self.assertEqual(out["none"]["reason"]["l1"], "служба завершилась с ошибкой (код 1)")              # no fail yet — systemd's why
        l = out["l2tp"]
        self.assertEqual((l["ports"]["text"], l["buttons"], l["menu"]), ("3 порта", ["toggle"], ["sw:plain", "delete"]))
        self.assertIn("1701/udp — L2TP", l["ports"]["tip"])
        self.assertEqual(out["l2tpEn"]["ports"]["text"], "3 ports")
        self.assertEqual(out["wait"]["chip"], {"tone": "off", "text": "ждёт шлюза"})
        self.assertEqual(out["fixedDown"]["buttons"], ["restart", "log"])                               # L2TP has no port to change
        self.assertIn("снова остановилась", out["again"]["reason"]["l3"])                         # survives a redraw
        self.assertEqual(out["l2tpClosed"]["reason"]["l1"], "Порт может быть закрыт у хостера — откройте 1701/udp, 500/udp, 4500/udp")
        # стенд 3.3.7.2 Н-8.2: служба идёт, сертификат Hysteria2 негоден — «не работает» с причиной словами, без строки журнала
        ce = out["cert"]
        self.assertEqual((ce["state"], ce["chip"]["tone"], ce["chip"]["text"]), ("other", "err", "не работает"))
        self.assertEqual((ce["reason"]["l1"], ce["reason"]["l2"]), ("Сертификат не подошёл или не найден — выпустите его заново.", ""))


@unittest.skipUnless(HAS_NODE, "no node")
class DomTest(unittest.TestCase):
    def test_agent_key_flow(self):
        out = run_node(prelude() + r"""
(async () => {
  const calls = [];
  let doc = { keys: [], connect: { host: "203.0.113.5", claude_code: "claude mcp add kaskad -- ssh root@203.0.113.5 kaskad-mcp" } };
  const api = { get: () => { calls.push("get"); return Promise.resolve(JSON.parse(JSON.stringify(doc))); },
    add: (b) => { calls.push(["add", b]); doc.keys.push({ fingerprint: "SHA256:x", type: "ssh-ed25519", label: b.label, seen: null }); return Promise.resolve({ ok: true }); },
    del: (fp) => { calls.push(["del", fp]); doc.keys = []; return Promise.resolve({ ok: true }); } };
  const a = X.agentKey({ lang: "ru", api, open: true, poll: 5000 });
  document.body.appendChild(a.el);
  await flush();
  const r = { chip0: txtOf(byCls(a.el, "ux-fold__chip")[0]) };
  const ta = a.el.all((n) => n.tagName === "TEXTAREA")[0];
  input(ta, "-----BEGIN OPENSSH PRIVATE KEY-----");
  r.priv = txtOf(byCls(a.el, "ux-msg")[0]);
  r.privDis = btnText(a.el, "Добавить").disabled;
  input(ta, "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI kaskad-agent@MacBook-Anna");
  r.name = a.el.all((n) => n.tagName === "INPUT")[0].value;
  btnText(a.el, "Добавить").click();
  await flush();
  r.added = txtOf(byCls(a.el, "ux-added")[0]);
  r.chip1 = txtOf(byCls(a.el, "ux-fold__chip")[0]);
  r.wait = txtOf(byCls(a.el, "ux-live")[0]);
  r.timers = TQ.length;                                   // the poll is set
  doc.keys[0].seen = { at: Math.floor(Date.now() / 1000), client: "claude-code/2.1.4" };
  await tick();
  r.chip2 = txtOf(byCls(a.el, "ux-fold__chip")[0]);
  r.ok = txtOf(byCls(a.el, "ux-live")[0]);
  r.timersAfter = TQ.length;                              // connected — no more polling
  // R-333 П6: a second key after the first connected — polled until it connects too
  doc.keys.push({ fingerprint: "SHA256:y", type: "ssh-ed25519", label: "PC", seen: null });
  await a.refresh();
  r.chip3 = txtOf(byCls(a.el, "ux-fold__chip")[0]); r.timers2 = TQ.length;
  doc.keys[1].seen = { at: Math.floor(Date.now() / 1000), client: "cursor/1.0" };
  await tick();
  r.timers3 = TQ.length;
  a.el.all((n) => n.getAttribute && n.getAttribute("role") === "tab" && n.textContent === "Claude Desktop")[0].click();
  r.desk = txtOf(byCls(a.el, "ux-code__pre")[1]);
  r.calls = calls.filter((c) => c !== "get");
  console.log(JSON.stringify(r));
})();
""")
        self.assertEqual(out["chip0"], "не настроено")
        self.assertTrue(out["priv"].startswith("Это закрытый ключ"))
        self.assertTrue(out["privDis"])
        self.assertEqual(out["name"], "MacBook-Anna")                          # the name comes from the comment by itself
        self.assertEqual(out["calls"][0], ["add", {"pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI kaskad-agent@MacBook-Anna", "label": "MacBook-Anna"}])
        self.assertIn("Ключ добавлен · MacBook-Anna", out["added"])
        self.assertEqual(out["chip1"], "ключ добавлен, ждём агента")
        self.assertIn("Ждём, когда агент подключится", out["wait"])
        self.assertGreaterEqual(out["timers"], 1)
        self.assertEqual(out["chip2"], "подключено")
        self.assertIn("Агент подключился · Claude Code · MacBook-Anna · сегодня в", out["ok"])
        self.assertEqual(out["timersAfter"], 0)
        self.assertEqual((out["chip3"], out["timers2"], out["timers3"]), ("подключено", 1, 0))
        self.assertIn('"root@203.0.113.5"', out["desk"])

    def test_agent_key_delete_asks_first(self):
        """R-333 З1″: deleting a key cuts the agent off (and, unsure, other agents) — so «×» asks first, as a protocol."""
        out = run_node(prelude() + r"""
(async () => {
  const calls = [], toasts = [];
  let doc = { keys: [{ fingerprint: "SHA256:x", type: "ssh-ed25519", label: "MacBook-Anna", seen: null }], connect: { host: "h" } };
  let release;
  const api = { get: () => Promise.resolve(JSON.parse(JSON.stringify(doc))),
    del: (fp) => { calls.push(["del", fp]); return new Promise((ok) => { release = () => { doc.keys = []; ok({ ok: true, ended: 2, unsure: true }); }; }); } };
  const a = X.agentKey({ lang: "ru", api, open: true, toast: (t, d, k) => toasts.push([t, d, k]) });
  document.body.appendChild(a.el);
  await flush();
  const x = () => a.el.all((n) => n.tagName === "BUTTON" && /^Удалить ключ/.test(n.getAttribute("aria-label") || ""))[0];
  x().click(); await flush();
  const r = { ask: txtOf(byCls(a.el, "ux-confirm")[0]), calls0: calls.length, focus: document.activeElement && document.activeElement.textContent,
    xGone: !x(), cls: byCls(a.el, "ux-added")[0].className };
  btnText(byCls(a.el, "ux-confirm")[0], "Отмена").click(); await flush();
  r.cancel = { ask: byCls(a.el, "ux-confirm").length, calls: calls.length, x: !!x() };
  x().click(); await flush();
  btnText(byCls(a.el, "ux-confirm")[0], "Удалить").click(); await flush();
  const yes = btnText(byCls(a.el, "ux-confirm")[0], "Удаляю");
  r.busy = { yes: !!yes, dis: !!yes && (yes.disabled === true || yes.getAttribute("disabled") !== null && yes.getAttribute("disabled") !== undefined) };
  release(); await flush();
  r.after = { calls: calls, ask: byCls(a.el, "ux-confirm").length, toasts: toasts };
  console.log(JSON.stringify(r));
})();
""")
        self.assertIn("Удалить ключ «MacBook-Anna»?", out["ask"])
        self.assertIn("Агент с этим ключом сразу отключится", out["ask"])
        self.assertIn("могут отключиться и другие агенты", out["ask"])
        self.assertEqual(out["calls0"], 0)                                   # nothing deleted on the first click
        self.assertEqual(out["focus"], "Отмена")
        self.assertTrue(out["xGone"])
        self.assertIn("is-ask", out["cls"])
        self.assertEqual(out["cancel"], {"ask": 0, "calls": 0, "x": True})
        self.assertEqual(out["busy"], {"yes": True, "dis": True})
        self.assertEqual(out["after"]["calls"], [["del", "SHA256:x"]])
        self.assertEqual(out["after"]["ask"], 0)
        t = out["after"]["toasts"][0]
        self.assertEqual(t[2], "warning")
        self.assertTrue(t[1].startswith("Могли отключиться и другие агенты"), t[1])

    def test_agent_key_delete_one_at_a_time_and_focus(self):
        """R-333 З1‴, П1‴: while a key is being deleted «Удаляю…» is in its own row only, «×» of the other keys is off
        and the question does not move; Esc closes the question; after «Отмена», Esc, the deletion, a failure and a
        redraw by the poll the focus is on a meaningful element (the «×», «Отмена», the key field), not document.body."""
        out = run_node(prelude() + r"""
// a browser: a disabled button is not clicked and not focused
Object.defineProperty(Node_.prototype, "disabled", { configurable: true,
  get() { return "disabled" in this.attrs; }, set(v) { if (v) this.attrs.disabled = ""; else delete this.attrs.disabled; } });
Node_.prototype.click = function () { if (!this.disabled) fire(this, "click"); };
Node_.prototype.focus = function () { if (!this.disabled) document.activeElement = this; };
(async () => {
  const calls = [], toasts = [];
  let doc = { keys: [{ fingerprint: "SHA256:a", type: "ssh-ed25519", label: "MacBook-Anna", seen: null },
                     { fingerprint: "SHA256:b", type: "ssh-ed25519", label: "Second-PC", seen: null }], connect: { host: "h" } };
  let release, mode = "slow";
  const api = { get: () => Promise.resolve(JSON.parse(JSON.stringify(doc))),
    del: (fp) => { calls.push(fp);
      if (mode === "fail") return Promise.reject(Object.assign(new Error("x"), { status: 500, body: { error: "Сбой." } }));
      if (mode === "now") { doc.keys = doc.keys.filter((k) => k.fingerprint !== fp); return Promise.resolve({ ok: true, ended: 0 }); }
      return new Promise((ok) => { release = () => { doc.keys = doc.keys.filter((k) => k.fingerprint !== fp); ok({ ok: true, ended: 1 }); }; }); } };
  const a = X.agentKey({ lang: "ru", api, open: true, poll: 5000, toast: (t, d, k) => toasts.push([t, d, k]) });
  document.body.appendChild(a.el);
  await flush();
  const x = (name) => a.el.all((n) => n.tagName === "BUTTON" && n.getAttribute("aria-label") === "Удалить ключ " + name)[0];
  const ask = () => byCls(a.el, "ux-confirm");
  const rowOf = (n) => { while (n && !(n.classList && n.classList.contains("ux-added"))) n = n.parentNode; return n; };
  const esc = () => fire(document.activeElement, "keydown", { key: "Escape" });
  const focus = () => { const f = document.activeElement; if (!f || f === document.body) return "body";
    return f.getAttribute("aria-label") || f.getAttribute("data-akf") + ":" + f.textContent; };
  const r = {};
  // Esc closes the question, the focus goes back to «×»
  x("MacBook-Anna").click(); await flush();
  r.f0 = focus();
  esc(); await flush();
  r.esc = { ask: ask().length, focus: focus() };
  // «Отмена» — the same
  x("MacBook-Anna").click(); await flush();
  btnText(ask()[0], "Отмена").click(); await flush();
  r.cancel = { ask: ask().length, focus: focus() };
  // deleting MacBook-Anna (DELETE answers later)
  x("MacBook-Anna").click(); await flush();
  btnText(ask()[0], "Удалить").click(); await flush();
  r.busy = { asks: ask().length, row: txtOf(rowOf(ask()[0])).indexOf("MacBook-Anna") >= 0, yes: !!btnText(ask()[0], "Удаляю"),
    xB: x("Second-PC").disabled, focus: focus() };
  // «×» of Second-PC meanwhile: off; a click (even forced) does not move the question
  x("Second-PC").click(); fire(x("Second-PC"), "click"); await flush();
  esc(); await flush();                                                     // Esc while deleting: the question stays
  r.during = { asks: ask().length, row: txtOf(rowOf(ask()[0])).indexOf("MacBook-Anna") >= 0, yes: !!btnText(ask()[0], "Удаляю"),
    xA: !!x("MacBook-Anna"), calls: calls.slice() };
  release(); await flush();
  r.after = { asks: ask().length, keys: byCls(a.el, "ux-added").length, xB: x("Second-PC").disabled, focus: focus(), toast: toasts[0] && toasts[0][0] };
  // the poll redraws the card (Second-PC connected) while the question is open — the focus stays on «Отмена»
  x("Second-PC").click(); await flush();
  doc.keys[0].seen = { at: Math.floor(Date.now() / 1000), client: "cursor/1.0" };
  await a.refresh();
  r.poll = { asks: ask().length, focus: focus() };
  // a failed DELETE: the question closes even with the same answer of GET, the focus — the «×» of the key
  mode = "fail";
  btnText(ask()[0], "Удалить").click(); await flush();
  r.fail = { asks: ask().length, focus: focus(), busy: !!btnText(a.el, "Удаляю"), toast: toasts[toasts.length - 1][2] };
  // the last key deleted — the focus goes to the key field
  mode = "now";
  x("Second-PC").click(); await flush();
  btnText(ask()[0], "Удалить").click(); await flush();
  r.last = { keys: byCls(a.el, "ux-added").length, focus: document.activeElement && document.activeElement.tagName };
  r.calls = calls;
  console.log(JSON.stringify(r));
})();
""")
        self.assertEqual(out["f0"], "no:Отмена")
        self.assertEqual(out["esc"], {"ask": 0, "focus": "Удалить ключ MacBook-Anna"})
        self.assertEqual(out["cancel"], {"ask": 0, "focus": "Удалить ключ MacBook-Anna"})
        self.assertEqual(out["busy"], {"asks": 1, "row": True, "yes": True, "xB": True, "focus": "Удалить ключ «MacBook-Anna»?"})
        self.assertEqual(out["during"], {"asks": 1, "row": True, "yes": True, "xA": False, "calls": ["SHA256:a"]})
        self.assertEqual((out["after"]["asks"], out["after"]["keys"], out["after"]["xB"], out["after"]["focus"]),
                         (0, 1, False, "Удалить ключ Second-PC"))
        self.assertIn("MacBook-Anna", out["after"]["toast"])
        self.assertEqual(out["poll"], {"asks": 1, "focus": "no:Отмена"})
        self.assertEqual(out["fail"], {"asks": 0, "focus": "Удалить ключ Second-PC", "busy": False, "toast": "danger"})
        self.assertEqual(out["last"], {"keys": 0, "focus": "TEXTAREA"})
        self.assertEqual(out["calls"], ["SHA256:a", "SHA256:b", "SHA256:b"])

    def test_protocol_delete_question_esc_and_focus(self):
        """R-333 П1‴: the protocol's delete question — Esc closes it; after «Отмена», Esc and the answer the focus is
        on «⋯», not document.body."""
        out = run_node(prelude() + r"""
(async () => {
  const calls = [];
  const row = { kind: "wg", title: "WireGuard", engine: "", desc: "d", enabled: true, devices: 0, sessions: 0, ports: ["51820/udp"], running: true };
  const c = X.protocolCard(row, { lang: "ru", onToggle: () => {}, onPort: () => {}, onLog: () => {}, onRestart: () => Promise.resolve({}),
    onDelete: () => { calls.push("delete"); return Promise.reject(new Error("x")); }, onOption: () => {} });
  document.body.appendChild(c);
  const more = c.all((n) => n.getAttribute && n.getAttribute("aria-haspopup") === "menu")[0];
  const open = () => { more.click(); btnText(c, "Удалить протокол").click(); };
  const r = {};
  open(); r.f0 = document.activeElement.textContent;
  fire(document.activeElement, "keydown", { key: "Escape" });
  r.esc = [byCls(c, "ux-confirm").length, document.activeElement === more];
  open(); btnText(byCls(c, "ux-confirm")[0], "Отмена").click();
  r.cancel = [byCls(c, "ux-confirm").length, document.activeElement === more];
  open(); c.all((n) => n.tagName === "BUTTON" && n.textContent === "Удалить")[0].click(); await flush();
  r.done = [byCls(c, "ux-confirm").length, document.activeElement === more];
  r.calls = calls;
  console.log(JSON.stringify(r));
})();
""")
        self.assertEqual(out["f0"], "Отмена")
        self.assertEqual(out["esc"], [0, True])
        self.assertEqual(out["cancel"], [0, True])
        self.assertEqual(out["done"], [0, True])
        self.assertEqual(out["calls"], ["delete"])

    def test_token_check_panel_failure_words(self):
        """R-333 П4″: the panel answered with an error — «панель не смогла»; no answer at all (a broken link or the
        browser's time limit) — «панель не ответила», not «сбой на сервере»."""
        out = run_node(prelude() + r"""
(async () => {
  const api = { get: () => Promise.resolve({ ok: true, token: false }),
    check: (b) => /e500/.test(b.token) ? Promise.reject(Object.assign(new Error("Внутренняя ошибка панели"), { status: 500 }))
      : /net/.test(b.token) ? Promise.reject(Object.assign(new Error("Нет связи с панелью"), { status: 0, code: "net" }))
      : Promise.reject(Object.assign(new Error("Панель не ответила вовремя"), { status: 0, code: "timeout" })) };
  const b = X.telegramBot({ lang: "ru", api, open: true });
  document.body.appendChild(b.el);
  await flush();
  const tok = b.el.all((n) => n.tagName === "INPUT")[0];
  const r = {};
  input(tok, "123456789:AAHe500" + "x".repeat(30)); await tick();
  r.e500 = txtOf(byCls(b.el, "ux-live")[0]);
  input(tok, "123456789:AAHslow" + "x".repeat(30)); await tick();
  r.slow = txtOf(byCls(b.el, "ux-live")[0]);
  // the browser lost the panel: its transport code «net» is not the server's reason «net» (no exit to Telegram)
  input(tok, "123456789:AAHnet" + "x".repeat(30)); await tick();
  r.net = txtOf(byCls(b.el, "ux-live")[0]); r.netBanner = byCls(b.el, "ux-warnbar").length;
  console.log(JSON.stringify(r));
})();
""")
        self.assertIn("Панель не смогла проверить токен — дело не в Telegram.", out["e500"])
        self.assertIn("Внутренняя ошибка панели", out["e500"])
        self.assertIn("Панель не ответила на проверку токена", out["slow"])
        self.assertIn("Панель не ответила на проверку токена", out["net"])
        self.assertIn("Нет связи с панелью", out["net"])
        self.assertNotIn("добавьте выход", out["net"])
        self.assertEqual(out["netBanner"], 0)
        for k in ("e500", "slow", "net"):
            self.assertNotIn("сервере", out[k])

    def test_demo_reason_matches_the_engine_on_the_card(self):
        """R-333 П3″: the demo (?mock=1&uxfail=awg3:other) — awg3 runs on amneziawg-go there, so its reason and its log
        are not «модуль ядра не загрузился»; a kernel-module node gets that one. EN — not a Cyrillic letter."""
        import subprocess
        import tempfile
        script = r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0&uxfail=awg3:other,vless:other" + process.argv[4]);
if (process.argv[4]) { globalThis.document = { documentElement: { lang: "en" } }; }
require(process.argv[2]); require(process.argv[3]);
(async () => {
  const get = async (p) => (await window.KASKAD_MOCK.fetch(p, { method: "GET", headers: {} })).json();
  const out = {};
  for (const core of [null, "kernel"]) {
    if (core) { window.KASKAD_MOCK.db().inbounds.awg3.core = core; }
    const a = (await get("/api/gw/inbounds")).inbounds.find((x) => x.kind === "awg3");
    out[core || "prog"] = { engine: a.engine, fail: a.fail, log: (await get("/api/gw/inbounds/awg3/log")).lines.map((x) => x.t).join("\\n") };
  }
  out.vlessLog = (await get("/api/gw/inbounds/vless/log")).lines.map((x) => x.t).join("\\n");
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "demo.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(script)
            res = {}
            for lang, arg in (("ru", ""), ("en", "&lang=en")):
                r = subprocess.run(["node", path, os.path.join(STATIC, "dev-mock.js"), os.path.join(STATIC, "dev-mock-ux.js"), arg],
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr[:500])
                res[lang] = json.loads(r.stdout)
        prog, kern = res["ru"]["prog"], res["ru"]["kernel"]
        self.assertEqual(prog["engine"], "amneziawg-go")
        self.assertNotIn("одуль ядра", prog["fail"]["human"])
        self.assertNotIn("modprobe", prog["fail"]["line"] + prog["log"])
        self.assertIn("Line unrecognized", prog["fail"]["line"])
        self.assertIn("Модуль ядра AmneziaWG не загрузился", kern["fail"]["human"])
        self.assertIn("modprobe", kern["log"])
        self.assertNotIn("awg3", res["ru"]["vlessLog"])                     # the log of VLESS is VLESS's
        self.assertNotIn("AmneziaWG", res["ru"]["vlessLog"])
        self.assertFalse(CYR.search(json.dumps(res["en"], ensure_ascii=False)), res["en"])

    def test_telegram_bot_flow(self):
        out = run_node(prelude() + r"""
(async () => {
  const calls = [];
  let st = { token: false, chat: false, running: false, need_exit: false };
  const api = { get: () => Promise.resolve(Object.assign({ ok: true }, st)),
    check: (b) => { calls.push(["check", b.token.slice(0, 9)]); return /bad/.test(b.token) ? Promise.reject(Object.assign(new Error("no"), { data: { reason: "bad_token" } }))
      : /net/.test(b.token) ? Promise.reject(Object.assign(new Error("no"), { data: { reason: "net" } }))
      : /prx/.test(b.token) ? Promise.reject(Object.assign(new Error("Нет связи с Telegram через прокси бота: timed out"), { data: { reason: "net_proxy" } }))
      : /tg5/.test(b.token) ? Promise.reject(Object.assign(new Error("Telegram ответил ошибкой: Too Many Requests"), { data: { reason: "telegram" } }))
      : Promise.resolve({ username: "kaskad_k3_bot" }); },
    test: (b) => { calls.push(["test", Object.keys(b).sort().join(","), b.chat_id]); st = { token: true, chat: true, running: true, username: "kaskad_k3_bot" };
      return Promise.resolve(b.chat_id === "111111" ? { sent: false, sent_why: "nostart", username: "kaskad_k3_bot" } : { sent: true, username: "kaskad_k3_bot" }); } };
  const b = X.telegramBot({ lang: "ru", api, open: true, onGoExits: () => calls.push("exits") });
  document.body.appendChild(b.el);
  await flush();
  const r = { chip0: txtOf(byCls(b.el, "ux-fold__chip")[0]), need0: txtOf(byCls(b.el, "ux-live")[1]) };
  const [tok, id] = b.el.all((n) => n.tagName === "INPUT");
  input(tok, "123456789:AAHbad" + "x".repeat(30)); await tick();
  r.bad = txtOf(byCls(b.el, "ux-live")[0]); r.chipBad = txtOf(byCls(b.el, "ux-fold__chip")[0]);
  input(tok, "123456789:AAHnet" + "x".repeat(30)); await tick();
  r.noexit = txtOf(byCls(b.el, "ux-live")[0]); r.banner = !!byCls(b.el, "ux-warnbar").length;
  r.bannerText = r.banner ? txtOf(byCls(b.el, "ux-warnbar")[0]) : "";
  btnText(b.el, "Открыть «Выходы»").click();
  // R-333 С2: an exit that does not carry, Telegram 429 — not «токен не подошёл»
  input(tok, "123456789:AAHprx" + "x".repeat(30)); await tick();
  r.prx = { line: txtOf(byCls(b.el, "ux-live")[0]), chip: txtOf(byCls(b.el, "ux-fold__chip")[0]), cls: tok.className,
    banner: byCls(b.el, "ux-warnbar").length, exits: !!btnText(byCls(b.el, "ux-live")[0], "Открыть «Выходы»") };
  input(tok, "123456789:AAHtg5" + "x".repeat(30)); await tick();
  r.tg = { line: txtOf(byCls(b.el, "ux-live")[0]), chip: txtOf(byCls(b.el, "ux-fold__chip")[0]), exits: !!btnText(byCls(b.el, "ux-live")[0], "Открыть «Выходы»") };
  const before = calls.length;
  btnText(b.el, "Проверить ещё раз").click(); await tick();
  r.again = calls.length - before;
  input(tok, "123456789:AAHgood" + "x".repeat(30)); await tick();
  r.found = txtOf(byCls(b.el, "ux-live")[0]); r.chipOk = txtOf(byCls(b.el, "ux-fold__chip")[0]);
  input(id, "-100123a4567"); r.group = id.value;                // a group: the minus stays (review №2)
  input(id, "12a3b45678");
  r.id = id.value;
  btnText(b.el, "Отправить тест").click(); await flush();
  r.sent = txtOf(byCls(b.el, "ux-live")[1]); r.chipWork = txtOf(byCls(b.el, "ux-fold__chip")[0]);
  r.tokCleared = tok.value;
  input(id, "111111"); btnText(b.el, "Отправить тест").click(); await flush();
  r.nostart = txtOf(byCls(b.el, "ux-live")[1]);
  r.calls = calls;
  console.log(JSON.stringify(r));
})();
""")
        self.assertEqual(out["chip0"], "не настроен")
        self.assertEqual(out["need0"], "Сначала вставьте токен — шаг 2.")
        self.assertTrue(out["bad"].startswith("Telegram не принял токен"))
        self.assertEqual(out["chipBad"], "токен не подошёл")
        self.assertEqual(out["noexit"], "Не достучались до Telegram — сначала добавьте выход.")
        self.assertTrue(out["banner"])
        self.assertIn("Telegram недоступен с сервера", out["bannerText"])                # not «из РФ»: unknown
        self.assertNotIn("РФ", out["bannerText"])
        p = out["prx"]
        self.assertEqual(p["chip"], "нет связи с Telegram")
        self.assertIn("Нет связи с Telegram через прокси бота: timed out", p["line"])
        self.assertIn("Токен тут ни при чём", p["line"])
        self.assertIn("is-warn", p["cls"])
        self.assertNotIn("is-bad", p["cls"])
        self.assertTrue(p["exits"])
        self.assertEqual(p["banner"], 0)
        self.assertEqual(out["tg"]["chip"], "нет связи с Telegram")
        self.assertIn("Too Many Requests", out["tg"]["line"])
        self.assertIn("попробуйте ещё раз", out["tg"]["line"])
        self.assertFalse(out["tg"]["exits"])
        self.assertEqual(out["again"], 1)                                        # «Проверить ещё раз» asks again
        self.assertEqual(out["found"], "Бот @kaskad_k3_bot найден")
        self.assertEqual(out["chipOk"], "токен проверен")
        self.assertEqual(out["id"], "12345678")                                  # only digits
        self.assertEqual(out["group"], "-1001234567")
        self.assertIn("Сообщение доставлено", out["sent"])
        self.assertEqual(out["chipWork"], "работает")
        self.assertEqual(out["tokCleared"], "")                                  # the token does not stay in the field
        self.assertIn("откройте @kaskad_k3_bot, нажмите «Старт»", out["nostart"])
        self.assertIn("exits", out["calls"])
        self.assertIn(["test", "chat_id,token", "12345678"], out["calls"])
        self.assertIn(["test", "chat_id", "111111"], out["calls"])              # the saved token: not sent again

    def test_protocol_card_dom(self):
        out = run_node(prelude() + r"""
(async () => {
  const calls = [];
  const row = { kind: "awg3", title: "AmneziaWG 3.1", engine: "модуль ядра", desc: "d", enabled: true, devices: 2, sessions: 0, ports: ["43304/udp"],
    running: false, fail: { why: "other", line: "modprobe: FATAL", human: "" } };
  const c = X.protocolCard(row, { lang: "ru", onToggle: (on) => calls.push(["toggle", on]), onPort: () => calls.push("port"), onLog: () => calls.push("log"),
    onRestart: () => { calls.push("restart"); return Promise.resolve({ running: false, at: 1760270000 }); },
    onDelete: () => { calls.push("delete"); return Promise.resolve(); }, onOption: () => {} });
  document.body.appendChild(c);
  const r = { state: c.getAttribute("data-state"), eng: txtOf(byCls(c, "ux-pc__eng")[0]), btns: byCls(c, "ux-pc__btns")[0].children.map(txtOf) };
  btnText(c, "Перезапустить").click(); await flush();
  r.again = txtOf(byCls(c, "ux-reason")[0]);
  btnText(c, "Журнал").click();
  const more = c.all((n) => n.getAttribute && n.getAttribute("aria-haspopup") === "menu")[0];
  more.click();
  r.menu = c.all((n) => n.getAttribute && /^menuitem/.test(n.getAttribute("role") || "")).map(txtOf);
  btnText(c, "Удалить протокол").click();
  r.confirm = txtOf(byCls(c, "ux-confirm")[0]);
  c.all((n) => n.tagName === "BUTTON" && n.textContent === "Удалить")[0].click(); await flush();
  r.calls = calls;
  console.log(JSON.stringify(r));
})();
""")
        self.assertEqual(out["state"], "other")
        self.assertEqual(out["eng"], "модуль ядра")
        self.assertEqual(out["btns"], ["Перезапустить", "Журнал"])
        self.assertIn("снова остановилась", out["again"])
        self.assertEqual(out["menu"], ["Выключить", "Сменить порт", "Удалить протокол"])
        self.assertIn("Удалить AmneziaWG 3.1? Устройства с ним перестанут подключаться.", out["confirm"])
        self.assertEqual(out["calls"], ["restart", "log", "delete"])

    def test_restart_of_a_shared_unit_asks_first(self):
        # R-333 З3: VLESS/SOCKS5 — the gateway xray (TPROXY to the exits, DNS); L2TP — accel-ppp and strongSwan
        out = run_node(prelude("en") + r"""
(async () => {
  const calls = [];
  const mk = (kind, title, shared, lang) => X.protocolCard({ kind, title, engine: "x", enabled: true, devices: 1, sessions: 0, ports: ["443/tcp"],
    running: false, fail: { why: "other", line: "x" }, shared }, { lang, onToggle() {}, onPort() {}, onLog() {}, onDelete() {}, onOption() {},
    onRestart: () => { calls.push(kind); return Promise.resolve({ running: true, at: 1 }); } });
  const r = {};
  const v = mk("vless", "VLESS", { gateway: true, with: ["SOCKS5"] }, "ru"); document.body.appendChild(v);
  btnText(v, "Перезапустить").click(); await flush();
  r.vQ = txtOf(byCls(v, "ux-confirm")[0]); r.vCalls = calls.slice();
  btnText(byCls(v, "ux-confirm")[0], "Отмена").click(); await flush();
  r.vCancel = calls.slice(); r.vBack = !byCls(v, "ux-confirm").length;
  btnText(v, "Перезапустить").click(); await flush();
  btnText(byCls(v, "ux-confirm")[0], "Перезапустить").click(); await flush();
  r.vYes = calls.slice();
  const l = mk("l2tp", "L2TP/IPsec", { gateway: false, with: ["PPTP", "IKEv2"] }, "en"); document.body.appendChild(l);
  btnText(l, "Restart").click(); await flush();
  r.lQ = txtOf(byCls(l, "ux-confirm")[0]);
  const w = mk("wg", "WireGuard", { gateway: false, with: [] }, "ru"); document.body.appendChild(w);
  btnText(w, "Перезапустить").click(); await flush();
  r.wNoAsk = !byCls(w, "ux-confirm").length; r.all = calls;
  console.log(JSON.stringify(r));
})();
""")
        self.assertIn("Перезапустить VLESS?", out["vQ"])
        self.assertIn("прервётся связь у всех, кто идёт через выходы", out["vQ"])
        self.assertIn("Затронет и: SOCKS5.", out["vQ"])
        self.assertEqual(out["vCalls"], [])                      # nothing restarted before «yes»
        self.assertEqual(out["vCancel"], [])
        self.assertTrue(out["vBack"])
        self.assertEqual(out["vYes"], ["vless"])
        self.assertIn("Restart L2TP/IPsec?", out["lQ"])
        self.assertIn("connections of PPTP, IKEv2 drop for a few seconds", out["lQ"])
        self.assertTrue(out["wNoAsk"])                           # its own unit: no question
        self.assertEqual(out["all"], ["vless", "wg"])

    def test_log_drawer(self):
        out = run_node(prelude() + r"""
(async () => {
  let closed = 0;
  const d = X.logDrawer({ lang: "ru", title: "WireGuard", loading: true, reason: "Порт занят", onClose: () => closed++ });
  const r = { loading: txtOf(byCls(d.el, "ux-log__body")[0]) };
  d.set({ lines: [{ t: "ok line", err: false }, { t: "Address already in use", err: true }], cmd: "journalctl -u kaskad-gw-wg.service -n 30" });
  r.errs = byCls(d.el, "is-err").map(txtOf);
  r.sub = txtOf(byCls(d.el, "ux-log__sub")[0]);
  btnText(d.el, "Скопировать").click(); await flush();
  r.copied = COPIED.slice(-1)[0];
  fire(document.body, "keydown", { key: "Escape" });
  r.closed = closed; r.gone = !d.el.parentNode;
  console.log(JSON.stringify(r));
})();
""")
        self.assertEqual(out["loading"], "Читаю журнал…")
        self.assertEqual(out["errs"], ["Address already in use"])
        self.assertEqual(out["sub"], "последние 30 строк · journalctl -u kaskad-gw-wg.service -n 30")
        self.assertEqual(out["copied"], "ok line\nAddress already in use")
        self.assertEqual((out["closed"], out["gone"]), (1, True))

    def test_share_menu(self):
        out = run_node(prelude() + r"""
(async () => {
  const calls = [];
  const m = X.shareMenu({ lang: "ru", name: "Мама", getPage: () => Promise.resolve({ enabled: true, url: "https://vpn.example.org/p/TOKEN" }),
    getSub: () => Promise.resolve({ enabled: false, empty_note: "Сейчас подписка пустая: нет устройств с VLESS." }),
    enable: (k) => { calls.push(["enable", k]); return Promise.resolve({ enabled: true, url: "https://vpn.example.org/p/T2/sub", empty_note: "Сейчас подписка пустая: нет устройств с VLESS." }); },
    more: (k) => calls.push(["more", k]), onGive: () => calls.push("give") });
  document.body.appendChild(m.el);
  const btn = m.el.children[0], menu = m.el.children[1];
  btn.click();
  const r = { open: !menu.hidden, items: byCls(menu, "ux-smi").map(txtOf) };
  menu.all((n) => n.getAttribute && n.getAttribute("data-item") === "page")[0].click(); await flush();
  r.page = txtOf(menu);
  btnText(menu, "Скопировать").click(); await flush();
  r.copied = COPIED.slice(-1)[0];
  btnText(menu, "").click; menu.all((n) => n.getAttribute && n.getAttribute("aria-label") === "Назад")[0].click();
  menu.all((n) => n.getAttribute && n.getAttribute("data-item") === "sub")[0].click(); await flush();
  r.sub0 = txtOf(menu);
  btnText(menu, "Создать ссылку").click(); await flush();
  r.sub1 = txtOf(menu);
  menu.all((n) => n.getAttribute && n.getAttribute("aria-label") === "Назад")[0].click();
  menu.all((n) => n.getAttribute && n.getAttribute("data-item") === "give")[0].click();
  r.closedAfterGive = menu.hidden;
  r.calls = calls;
  console.log(JSON.stringify(r));
})();
""")
        self.assertTrue(out["open"])
        self.assertEqual(len(out["items"]), 3)
        self.assertTrue(out["items"][1].startswith("Ссылка для приложений (подписка)"))
        self.assertIn("https://vpn.example.org/p/TOKEN", out["page"])
        self.assertEqual(out["copied"], "https://vpn.example.org/p/TOKEN")
        self.assertIn("Ссылки для приложений ещё нет.", out["sub0"])
        self.assertIn("https://vpn.example.org/p/T2/sub", out["sub1"])
        # R-333 З4: an empty subscription says so, before and after the link is made
        self.assertIn("Сейчас подписка пустая", out["sub0"])
        self.assertIn("Сейчас подписка пустая", out["sub1"])
        self.assertNotIn("подписка пустая", out["page"])
        self.assertTrue(out["closedAfterGive"])                                 # «Выдать настройки» opens the window
        self.assertEqual(out["calls"], [["enable", "sub"], "give"])

    def test_exits_input_r333i(self):
        """R-333i: a subscription already added — «Добавить» closed with the words of the check (П3); a row being
        deleted — «Удаляю…» instead of its state and no «×» (З1); the bot and the agent key fold by close() (П4)."""
        out = run_node(prelude() + r"""
(async () => {
  let ALREADY = true;
  const api = { preview: () => Promise.resolve({ items: [] }), addKey: () => Promise.resolve({}), addSub: () => Promise.resolve({}),
    checkSub: (b) => Promise.resolve({ already: ALREADY, text: ALREADY ? "Эта подписка уже добавлена (sub.example.net)." : "" }) };
  const e = X.exitsInput({ lang: "ru", api, rows: [] });
  document.body.appendChild(e.el);
  const ta = e.el.all((n) => n.tagName === "TEXTAREA")[0], add = btnText(e.el, "Добавить");
  input(ta, "https://sub.example.net/s-abc/all"); await tick();
  const r = { dupe: txtOf(byCls(e.el, "ux-ei__state")[0]), dupeDis: add.disabled };
  ALREADY = false; input(ta, "https://sub.example.net/s-new/all"); await tick();
  r.newDis = add.disabled;
  e.set([{ id: "s-1", kind: "sub", name: "sub.example.net", keys: 3, auto: true, status: "ok", busy: "Удаляю…", onDel: () => {} },
    { id: "s-2", kind: "sub", name: "ga", keys: 1, auto: true, status: "old", statusText: "не обновлялась 3 дня" }]);
  const items = byCls(e.el, "ux-ei__item");
  r.busyRow = txtOf(items[0]); r.busyX = byCls(items[0], "ux-x").length; r.busyCls = items[0].className;
  r.oldRow = txtOf(items[1]);
  r.pure = X.exitRowText({ kind: "key", name: "Вена", status: "ok", busy: "Deleting…" }, "en").status;
  const b = X.telegramBot({ lang: "ru", api: { get: () => Promise.resolve({}) }, open: true });
  const k = X.agentKey({ lang: "ru", api: { get: () => Promise.resolve({ keys: [] }) }, open: true });
  b.close(); k.close();
  r.closed = [b.el.className, k.el.className];
  console.log(JSON.stringify(r));
})();
""")
        self.assertIn("уже добавлена", out["dupe"])
        self.assertTrue(out["dupeDis"])
        self.assertFalse(out["newDis"])
        self.assertIn("Удаляю…", out["busyRow"])
        self.assertNotIn("обновляется сама", out["busyRow"])
        self.assertEqual(out["busyX"], 0)
        self.assertIn("is-busy", out["busyCls"])
        self.assertIn("не обновлялась 3 дня", out["oldRow"])
        self.assertEqual(out["pure"], {"tone": "wait", "text": "Deleting…"})
        self.assertTrue(all("is-open" not in c for c in out["closed"]), out["closed"])

    def test_exits_input(self):
        out = run_node(prelude() + r"""
(async () => {
  const calls = [];
  const api = { preview: (b) => { calls.push(["preview", b.key.slice(0, 6)]); return Promise.resolve({ items: [{ name: "Vienna", title: "VLESS" }], module_build: false }); },
    addKey: (b) => { calls.push(["addKey", b.key.slice(0, 8)]); return Promise.resolve({ added: [{ name: "Vienna" }] }); },
    addSub: (b) => { calls.push(["addSub", b.url, b.auto]); return Promise.reject(Object.assign(new Error("Сервер подписки ответил 404"), { data: { reason: "http" } })); } };
  const e = X.exitsInput({ lang: "ru", api, rows: [], onAdded: (k) => calls.push(["added", k]), onError: () => calls.push("error") });
  document.body.appendChild(e.el);
  const ta = e.el.all((n) => n.tagName === "TEXTAREA")[0];
  const add = btnText(e.el, "Добавить");
  const r = { explain0: txtOf(byCls(e.el, "ux-ei__explain")[0]), dis0: add.disabled };
  input(ta, "hello"); r.bad = txtOf(byCls(e.el, "ux-ei__state")[0]); r.disBad = add.disabled;
  input(ta, "vless://uuid@203.0.113.10:443#Vienna"); await tick();
  r.known = txtOf(byCls(e.el, "ux-ei__state")[0]);
  add.click(); await flush();
  r.cleared = ta.value;
  input(ta, "vless://uuid@203.0.113.10:443#Late");                 // its preview answers after the text changed
  input(ta, "https://ga.example.net/s-" + "A".repeat(43) + "/all.vpn"); await tick(); r.subGa = txtOf(byCls(e.el, "ux-ei__state")[0]);
  input(ta, "https://sub.example.net/s-abc/all"); await tick(); r.sub = txtOf(byCls(e.el, "ux-ei__state")[0]);
  add.click(); await flush();
  r.err = txtOf(byCls(e.el, "ux-ei__state")[0]); r.kept = ta.value;
  let deleted = 0;
  e.set([{ id: "s-1", kind: "sub", name: "sub.example.net", keys: 3, auto: true, status: "ok", onDel: () => deleted++ }]);
  r.row = txtOf(byCls(e.el, "ux-ei__item")[0]);
  r.explain1 = txtOf(byCls(e.el, "ux-ei__explain")[0]);
  e.el.all((n) => n.getAttribute && n.getAttribute("aria-label") === "Удалить sub.example.net")[0].click();
  r.deleted = deleted;
  // R-333 З5: no subscriptions, but the host has keys (cards below) — the explanation is a link too
  e.set([], 2); r.explainKeys = txtOf(byCls(e.el, "ux-ei__explain")[0]);
  e.set([], 0); r.explainNone = txtOf(byCls(e.el, "ux-ei__explain")[0]);
  const e2 = X.exitsInput({ lang: "ru", api, rows: [], others: 1 }); r.explainInit = txtOf(byCls(e2.el, "ux-ei__explain")[0]);
  r.calls = calls;
  console.log(JSON.stringify(r));
})();
""")
        self.assertIn("Что такое подписка:", out["explain0"])              # no rows — the explanation is shown
        self.assertIn("goAmnezia Pro", out["explain0"])
        self.assertTrue(out["dis0"])
        self.assertEqual(out["bad"], "Не узнали формат — подойдёт один из вариантов над полем.")
        self.assertTrue(out["disBad"])
        self.assertEqual(out["known"], "Узнали: ключ VLESS · Vienna")
        self.assertEqual(out["cleared"], "")
        self.assertEqual(out["subGa"], "Узнали: подписка goAmnezia Pro")        # its address: /s-<43>/…
        self.assertEqual(out["sub"], "Узнали: ссылка подписки")                  # R-333 П1: any other one — neutral
        self.assertEqual(out["err"], "Сервер подписки ответил 404")
        self.assertEqual(out["kept"], "https://sub.example.net/s-abc/all")    # a failed add keeps the text in the field
        self.assertIn("sub.example.net · обновляется сама", out["row"])
        self.assertIn("Подписка · 3 ключа", out["row"])
        self.assertEqual(out["explain1"], "Что такое подписка?")                 # rows — the explanation folds into a link
        self.assertEqual(out["deleted"], 1)
        self.assertEqual(out["explainKeys"], "Что такое подписка?")
        self.assertIn("Что такое подписка:", out["explainNone"])
        self.assertEqual(out["explainInit"], "Что такое подписка?")              # known from the start
        self.assertEqual(out["calls"], [["preview", "vless:"], ["addKey", "vless://"], ["added", "key"], ["preview", "vless:"],
                                        ["addSub", "https://sub.example.net/s-abc/all", True], "error"])


    def test_exits_input_by_domain_and_nothing_to_add(self):
        """https://<IP> with a certificate of a domain that leads to the same server — «Добавить по домену» (the
        address from the field, the host replaced; T-M6-qs330b №10, now in the shared component for the panel and the
        wizard); every key of the text already in the pool — said at once, «Добавить» closed."""
        out = run_node(prelude() + r"""
(async () => {
  const calls = [];
  let pre = { items: [{ name: "Vienna", title: "VLESS", in_pool: true }] };
  const api = { preview: () => Promise.resolve(pre), addKey: () => Promise.resolve({}),
    addSub: (b) => { calls.push(b.url); return calls.length > 1 ? Promise.resolve({ text: "ok" })
      : Promise.reject(Object.assign(new Error("Сертификат выдан на домен vpn.example.org"), { data: { reason: "cert_name", cert_domain: "vpn.example.org", same_server: true } })); } };
  const e = X.exitsInput({ lang: "ru", api, rows: [], onAdded: (k) => calls.push("added:" + k) });
  document.body.appendChild(e.el);
  const ta = e.el.all((n) => n.tagName === "TEXTAREA")[0], add = btnText(e.el, "Добавить");
  const r = {};
  input(ta, "vless://uuid@203.0.113.10:443#Vienna"); await tick();
  r.dupe = txtOf(byCls(e.el, "ux-ei__state")[0]); r.dupeDis = add.disabled;
  pre = { items: [{ name: "Vienna", title: "VLESS", problem: "сервер выхода — этот же узел" }] };
  input(ta, "vless://uuid@203.0.113.10:443#Self"); await tick(); r.problem = txtOf(byCls(e.el, "ux-ei__state")[0]);
  input(ta, "https://203.0.113.5:8443/s-x/all.vpn"); await tick();
  r.subDis = add.disabled;
  add.click(); await flush();
  const by = btnText(e.el, "Добавить по домену");
  r.by = by ? by.textContent : null;
  by.click(); await flush();
  r.calls = calls;
  console.log(JSON.stringify(r));
})();
""")
        self.assertEqual(out["dupe"], "Этот ключ уже в списке выходов — добавлять не нужно.")
        self.assertTrue(out["dupeDis"])
        self.assertEqual(out["problem"], "сервер выхода — этот же узел")
        self.assertFalse(out["subDis"])
        self.assertEqual(out["by"], "Добавить по домену vpn.example.org")
        self.assertEqual(out["calls"], ["https://203.0.113.5:8443/s-x/all.vpn", "https://vpn.example.org:8443/s-x/all.vpn", "added:sub"])

    def test_no_server_line(self):
        """OWNER-FEEDBACK №45: «Нет сервера за рубежом?» — the partner hosting as buttons (name · region ↗, https only,
        rel without noreferrer), the line about the country; the same node in the wizard and in the panel's ExitsInput."""
        hosting = [{"id": "adminvps", "name": "AdminVPS", "region": "РФ и Европа", "url": "https://vk.example/a"},
                   {"id": "cloudvps", "name": "Cloud VPS", "region": "Беларусь", "url": "https://vk.example/b"},
                   {"id": "bad", "name": "Bad", "region": "", "url": "http://insecure.example/"}]
        out = run_node(prelude() + r"""
const H = %s;
const n = X.noServer({ lang: "ru", hosting: H });
const links = n.all((x) => x.tagName === "A");
const e = X.exitsInput({ lang: "ru", api: {}, rows: [], hosting: H });
const e2 = X.exitsInput({ lang: "ru", api: {}, rows: [] });
console.log(JSON.stringify({ text: n.textContent, hrefs: links.map((a) => [a.getAttribute("href"), a.getAttribute("rel"), a.getAttribute("target")]),
  none: X.noServer({ lang: "ru", hosting: [] }), inInput: txtOf(byCls(e.el, "ux-ei__explain")[0]).indexOf("Нет сервера за рубежом?") >= 0,
  notInInput: txtOf(byCls(e2.el, "ux-ei__explain")[0]).indexOf("Нет сервера") >= 0 }));
""" % json.dumps(hosting, ensure_ascii=False))
        self.assertIn("Нет сервера за рубежом?", out["text"])
        self.assertIn("AdminVPS· РФ и Европа ↗", out["text"].replace(" ·", "·"))
        self.assertIn("при заказе выберите другую страну", out["text"])
        self.assertEqual(out["hrefs"], [["https://vk.example/a", "noopener", "_blank"], ["https://vk.example/b", "noopener", "_blank"]])
        self.assertIsNone(out["none"])
        self.assertTrue(out["inInput"])
        self.assertFalse(out["notInInput"])
        en = run_node(prelude("en") + r"""
const n = X.noServer({ lang: "en", hosting: %s });
console.log(JSON.stringify(n.textContent));
""" % json.dumps([{"id": "a", "name": "AdminVPS", "region": "Russia and Europe", "url": "https://vk.example/a"}]))
        self.assertIsNone(CYR.search(en), en)

class TextsTest(unittest.TestCase):
    def test_same_keys_and_english(self):
        ru = dict_of("ux-i18n-ru.js", "KASKAD_UX_I18N")
        en = dict_of("ux-i18n-en.js", "KASKAD_UX_I18N_EN")
        self.assertEqual(sorted(ru), sorted(en))
        bad = dict((k, v) for k, v in en.items() if CYR.search(v))
        self.assertEqual(bad, {})
        src = read("ux.js")
        used = set(re.findall(r"t\(lang, '([a-zA-Z0-9.]+)'", src))
        self.assertEqual(sorted(k for k in used if k not in ru), [])
        # the person's link for apps — «подписка» only in brackets after it (HANDOFF §5)
        self.assertEqual(ru["sm.sub"], "Ссылка для приложений (подписка)")
        self.assertEqual(en["sm.sub"], "Link for apps (subscription)")

    def test_panel_texts_and_routes(self):
        app = read("app.js")
        ru, en = read("i18n-ru.js"), read("i18n-en.js")
        for key in ("ib.secInstalledN", "ib.restartedOk", "st.botAgent", "pp.giveWhich", "xs.autoOn", "xs.autoOff", "xs.listN"):
            self.assertIn('t("%s"' % key, app.replace("tn(", "t("))
            self.assertIn('"%s":' % key, ru)
            self.assertIn('"%s":' % key, en)
        # №39: AmneziaWG 2.0 on MikroTik — a container, not awg-proxy (it knows only 1.x parameters)
        self.assertNotIn("awg-proxy", ru + en)
        self.assertIn("MikroTik — через контейнер", ru)
        # R-333 З3: «shared» of the inbound rows reaches the card (normInbounds keeps it, ibUxCard passes it)
        norm = app[app.index("function normInbounds"):app.index("function normInbounds") + 2500]
        self.assertIn("shared: isObj(r.shared)", norm)
        card = app[app.index("function ibUxCard"):app.index("function ibUxCard") + 2000]
        self.assertIn("shared: r.shared", card)
        # R-333 З5: the keys below make «Что такое подписка» a link
        self.assertIn("others: exAbroad().length", app)                  # круг 1 №31г: российские — не «другие ключи»
        self.assertIn("exitsUx.set(subRows(v.subs), v.n)", app)
        for path in ('"/api/bot"', '"/api/bot/check"', '"/api/bot/test"', '"/api/mcp"', '"/api/mcp/keys"', '"/log"', '"/restart"'):
            self.assertIn(path, app)
        idx = read("index.html")
        self.assertIn('href="ux.css?v=__KASKAD_V__"', idx)
        self.assertLess(idx.index('src="ux.js'), idx.index('src="app.js'))
        self.assertIn('loadScript("ux-i18n-en.js")', app)

    @unittest.skipUnless(HAS_NODE, "no node")
    def test_undo_warns_about_agents_before_and_after(self):
        """R-333 П3‴: «Вернуть как было» — the question names the agent keys that go (their agents disconnect); the
        result with agents cut off is a warning, not green. backupDone/backupUndo of app.js run over stubs."""
        app = read("app.js")
        src = app[app.index("  function backupDone("):app.index("  function openBackupExport(")] + \
            app[app.index("  function backupUndo("):app.index("  function backupCard(")]
        out = run_node(r"""
globalThis.window = globalThis;
require(%s); require(%s);
const RU = window.KASKAD_I18N, EN = window.KASKAD_I18N_EN;
let L = RU, SUM = null, UNDO = null;
const t = (k, p) => String(L[k] || k).replace(/\{(\w+)\}/g, (m, x) => (p && x in p ? p[x] : m));
const arr = (x) => (Array.isArray(x) ? x : []);
const fmtInt = (n) => String(n);
const asked = [], told = [];
const confirmDialog = (o) => { asked.push(o); return Promise.resolve(true); };
const noticeDialog = (o) => { told.push(o); return Promise.resolve(true); };
const oneAtATime = (k, fn) => fn();
const withBusy = (label, fn) => fn();
const reloadEverything = () => {}, showError = (e) => { told.push({ error: String(e) }); };
const API = { backup: { summary: () => (SUM instanceof Error ? Promise.reject(SUM) : Promise.resolve(SUM)), undo: () => Promise.resolve(UNDO) } };
%s
(async () => {
  const run = async (sum, undo) => { SUM = sum; UNDO = undo; asked.length = 0; told.length = 0; await backupUndo();
    return { warn: asked[0].warn, tone: told[0].tone }; };
  const r = {};
  r.two = await run({ undo: { agent_keys_gone: 2 } }, { text: ["x"], warnings: [], notes: ["n"], agents_cut: 3 });
  r.none = await run({ undo: { agent_keys_gone: 0 } }, { text: ["x"], warnings: [], notes: [], agents_cut: 0 });
  r.fail = await run(new Error("net"), { text: ["x"], warnings: ["w"], agents_cut: 0 });
  L = EN;
  r.en = await run({ undo: { agent_keys_gone: 1 } }, { text: ["x"], warnings: [], agents_cut: 1 });
  r.cyr = /[А-Яа-яЁё]/.test(EN["bk.undoAgents"] + EN["bk.undoAgentsMaybe"]);
  console.log(JSON.stringify(r));
})();
""" % (json.dumps(os.path.join(STATIC, "i18n-ru.js")), json.dumps(os.path.join(STATIC, "i18n-en.js")), src))
        self.assertTrue(out["two"]["warn"].startswith("Удалятся ключи агентов, которых не было до загрузки копии: 2."), out["two"])
        self.assertIn("сразу отключатся", out["two"]["warn"])
        self.assertEqual(out["two"]["tone"], "warning")
        self.assertEqual(out["none"], {"warn": "", "tone": "success"})
        self.assertTrue(out["fail"]["warn"].startswith("Если после загрузки копии появились ключи агентов"), out["fail"])
        self.assertEqual(out["fail"]["tone"], "warning")
        self.assertTrue(out["en"]["warn"].startswith("Agent keys that weren’t here before the upload will be deleted: 1."))
        self.assertEqual(out["en"]["tone"], "warning")
        self.assertFalse(out["cyr"])


if __name__ == "__main__":
    unittest.main()
