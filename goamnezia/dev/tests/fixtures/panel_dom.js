/* goAmnezia pro — панель (admin-web/static) в игрушечном DOM под node: без браузера, jsdom и сети.

   Зачем: проверять собранный интерфейс (клики, переходы, слои) там, где браузера нет, — в tests/run_static.sh на
   машине разработчика и в CI. Грузится настоящий index.html (вместе с его первым скриптом, который ставит вид на
   <html>) и настоящий app.js. Сеть — заглушка /api/… с данными в форме server.py; таймеры свои, время двигает тест
   (tick). Без node проверки пропускаются — как отрисовка страницы подключения в tests/test_pages_dryrun.sh.

   DOM здесь — то, чем пользуется app.js: дерево узлов, атрибуты, classList/dataset/style, селекторы (тег, *, #id,
   .класс, [атрибут], [атрибут=значение], :not(), потомок, ребёнок, соседи), всплытие событий с перехватом, фокус.
   Вёрстки нет: размеры нулевые, getComputedStyle пустой; раскладку телефона задаёт ширина окна в matchMedia.

   Использование — tests/test_panel_dom.py:
     const { boot } = require("./panel_dom.js");
     const P = await boot({ hash: "#profiles", width: 1280, tg: false, look: { nav: "full" } });
     P.click(P.$(".gx-viewbtn")); await P.settle(); P.page(); P.errors;
*/
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..", "..");

// ── Разбор и печать HTML ─────────────────────────────────────────────────────
const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
const RAWTEXT = new Set(["script", "style"]);
const RCDATA = new Set(["textarea", "title"]);
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0" };
const decode = (s) => s.replace(/&(#[xX][0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);/g, (m, e) => {
  if (e[0] === "#") return String.fromCodePoint(e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
  return Object.prototype.hasOwnProperty.call(ENTITIES, e) ? ENTITIES[e] : m;
});
const TAG_RE = /^<([a-zA-Z][\w:-]*)((?:\s+[^\s"'>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*(\/?)>/;
const ATTR_RE = /([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;

function parseHTML(doc, parent, html) {
  const stack = [parent];
  const top = () => stack[stack.length - 1];
  const text = (s) => { if (s) top().appendChild(doc.createTextNode(decode(s))); };
  let i = 0;
  while (i < html.length) {
    const lt = html.indexOf("<", i);
    if (lt < 0) { text(html.slice(i)); break; }
    if (lt > i) text(html.slice(i, lt));
    i = lt;
    if (html.startsWith("<!--", i)) {
      const end = html.indexOf("-->", i + 4);
      top().appendChild(doc.createComment(html.slice(i + 4, end < 0 ? html.length : end)));
      i = end < 0 ? html.length : end + 3;
      continue;
    }
    if (html.startsWith("<!", i) || html.startsWith("<?", i)) {
      const end = html.indexOf(">", i);
      i = end < 0 ? html.length : end + 1;
      continue;
    }
    const rest = html.slice(i);
    if (rest[1] === "/") {
      const m = /^<\/([a-zA-Z][\w:-]*)\s*>/.exec(rest);
      if (!m) { text("<"); i += 1; continue; }
      const name = m[1].toLowerCase();
      for (let k = stack.length - 1; k > 0; k -= 1) if (stack[k].localName === name) { stack.length = k; break; }
      i += m[0].length;
      continue;
    }
    const m = TAG_RE.exec(rest);
    if (!m) { text("<"); i += 1; continue; }
    i += m[0].length;
    const node = doc.createElement(m[1]);
    ATTR_RE.lastIndex = 0;
    for (let a = ATTR_RE.exec(m[2]); a; a = ATTR_RE.exec(m[2])) node.setAttribute(a[1], decode(a[2] ?? a[3] ?? a[4] ?? ""));
    top().appendChild(node);
    const name = node.localName;
    if (RAWTEXT.has(name) || RCDATA.has(name)) {
      const close = html.toLowerCase().indexOf(`</${name}`, i);
      const body = html.slice(i, close < 0 ? html.length : close);
      if (body) node.appendChild(doc.createTextNode(RCDATA.has(name) ? decode(body) : body));
      const gt = close < 0 ? -1 : html.indexOf(">", close);
      i = gt < 0 ? html.length : gt + 1;
      continue;
    }
    if (!m[3] && !VOID.has(name)) stack.push(node);
  }
}
const escText = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escAttr = (s) => String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
function serialize(node) {
  if (node.nodeType === 3) return escText(node.data);
  if (node.nodeType === 8) return `<!--${node.data}-->`;
  if (node.nodeType !== 1) return node.childNodes.map(serialize).join("");
  const attrs = node.getAttributeNames().map((k) => ` ${k}="${escAttr(node.getAttribute(k))}"`).join("");
  if (VOID.has(node.localName)) return `<${node.localName}${attrs}>`;
  return `<${node.localName}${attrs}>${node.childNodes.map(serialize).join("")}</${node.localName}>`;
}

// ── Селекторы ────────────────────────────────────────────────────────────────
const selCache = new Map();
function parseSelectorList(src) {
  if (selCache.has(src)) return selCache.get(src);
  const s = String(src);
  let i = 0;
  const groups = [];
  let parts = [];
  let comp = null;
  let comb = null;
  const ident = () => { const m = /^(?:\\.|[\w\u00a0-\uffff-])+/.exec(s.slice(i)); if (!m) throw new SyntaxError(`selector: ${src}`); i += m[0].length; return m[0].replace(/\\(.)/g, "$1"); };
  const need = () => { if (!comp) { comp = { tag: null, ids: [], classes: [], attrs: [], pseudos: [] }; parts.push({ comb, comp }); comb = null; } return comp; };
  const endGroup = () => { if (!parts.length || comb) throw new SyntaxError(`selector: ${src}`); groups.push(parts); parts = []; comp = null; comb = null; };
  while (i < s.length) {
    const ch = s[i];
    if (/\s/.test(ch)) {
      while (i < s.length && /\s/.test(s[i])) i += 1;
      if (i < s.length && !">+~,".includes(s[i]) && comp) { comb = " "; comp = null; }
      continue;
    }
    if (ch === ",") { i += 1; endGroup(); continue; }
    if (">+~".includes(ch)) { i += 1; comb = ch; comp = null; continue; }
    if (ch === "*") { i += 1; need().tag = "*"; continue; }
    if (ch === "#") { i += 1; need().ids.push(ident()); continue; }
    if (ch === ".") { i += 1; need().classes.push(ident()); continue; }
    if (ch === "[") {
      const end = s.indexOf("]", i);
      const m = /^\[\s*([^\s~|^$*=\]]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\s\]]+))\s*(i)?)?\s*\]$/.exec(s.slice(i, end + 1));
      if (!m) throw new SyntaxError(`selector: ${src}`);
      need().attrs.push({ name: m[1], op: m[2] || "", value: m[3] ?? m[4] ?? m[5] ?? "", ci: !!m[6] });
      i = end + 1;
      continue;
    }
    if (ch === ":") {
      i += 1;
      if (s[i] === ":") throw new SyntaxError(`pseudo-element in selector: ${src}`);
      const name = ident().toLowerCase();
      let arg = null;
      if (s[i] === "(") {
        let depth = 0;
        const start = i + 1;
        for (; i < s.length; i += 1) {
          if (s[i] === "(") depth += 1;
          else if (s[i] === ")") { depth -= 1; if (!depth) break; }
        }
        arg = s.slice(start, i);
        i += 1;
      }
      need().pseudos.push({ name, arg, list: name === "not" || name === "is" || name === "where" ? parseSelectorList(arg) : null });
      continue;
    }
    need().tag = ident().toLowerCase();
  }
  endGroup();
  selCache.set(src, groups);
  return groups;
}
function attrMatch(el, a) {
  const v = el.getAttribute(a.name);
  if (v === null) return false;
  if (!a.op) return true;
  const x = a.ci ? v.toLowerCase() : v;
  const y = a.ci ? a.value.toLowerCase() : a.value;
  switch (a.op) {
    case "=": return x === y;
    case "~=": return x.split(/\s+/).includes(y);
    case "|=": return x === y || x.startsWith(`${y}-`);
    case "^=": return !!y && x.startsWith(y);
    case "$=": return !!y && x.endsWith(y);
    case "*=": return !!y && x.includes(y);
    default: return false;
  }
}
function compoundMatch(el, c, scope) {
  if (!el || el.nodeType !== 1) return false;
  if (c.tag && c.tag !== "*" && el.localName !== c.tag) return false;
  for (const id of c.ids) if (el.getAttribute("id") !== id) return false;
  for (const cl of c.classes) if (!el.classList.contains(cl)) return false;
  for (const a of c.attrs) if (!attrMatch(el, a)) return false;
  for (const p of c.pseudos) {
    switch (p.name) {
      case "not": if (listMatch(el, p.list, scope)) return false; break;
      case "is": case "where": if (!listMatch(el, p.list, scope)) return false; break;
      case "scope": if (el !== scope) return false; break;
      case "first-child": if (el.previousElementSibling) return false; break;
      case "last-child": if (el.nextElementSibling) return false; break;
      case "disabled": if (!el.disabled) return false; break;
      case "enabled": if (el.disabled) return false; break;
      case "checked": if (!el.checked) return false; break;
      case "focus": if (el.ownerDocument.activeElement !== el) return false; break;
      default: throw new SyntaxError(`selector pseudo-class :${p.name} is not supported by the toy DOM`);
    }
  }
  return true;
}
function complexMatch(el, parts, idx, scope) {
  if (!compoundMatch(el, parts[idx].comp, scope)) return false;
  if (idx === 0) return true;
  const comb = parts[idx].comb;
  if (comb === ">") return !!el.parentElement && complexMatch(el.parentElement, parts, idx - 1, scope);
  if (comb === "+") return !!el.previousElementSibling && complexMatch(el.previousElementSibling, parts, idx - 1, scope);
  if (comb === "~") { for (let n = el.previousElementSibling; n; n = n.previousElementSibling) if (complexMatch(n, parts, idx - 1, scope)) return true; return false; }
  for (let n = el.parentElement; n; n = n.parentElement) if (complexMatch(n, parts, idx - 1, scope)) return true;
  return false;
}
const listMatch = (el, groups, scope) => groups.some((parts) => complexMatch(el, parts, parts.length - 1, scope));
function* descendants(node) {
  for (const k of node.childNodes) {
    if (k.nodeType === 1) { yield k; yield* descendants(k); } else if (k.nodeType === 11) yield* descendants(k);
  }
}

// ── Узлы ─────────────────────────────────────────────────────────────────────
function makeDom(win, report) {
  class Node {
    constructor(doc, type) { this._doc = doc; this.nodeType = type; this.parentNode = null; this.childNodes = []; this._ls = null; }
    get ownerDocument() { return this._doc; }
    get firstChild() { return this.childNodes[0] || null; }
    get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
    get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
    get nextSibling() { const p = this.parentNode; return p ? p.childNodes[p.childNodes.indexOf(this) + 1] || null : null; }
    get previousSibling() { const p = this.parentNode; return p ? p.childNodes[p.childNodes.indexOf(this) - 1] || null : null; }
    get isConnected() { for (let n = this; n; n = n.parentNode) if (n.nodeType === 9) return true; return false; }
    hasChildNodes() { return this.childNodes.length > 0; }
    contains(n) { for (; n; n = n.parentNode) if (n === this) return true; return false; }
    getRootNode() { let n = this; while (n.parentNode) n = n.parentNode; return n; }
    _adopt(c) {
      if (!(c instanceof Node)) throw new TypeError("Failed to execute 'appendChild': parameter 1 is not of type 'Node'.");
      if (c.contains(this)) throw new Error("HierarchyRequestError: the new child contains the parent");
      if (c.parentNode) c.parentNode.removeChild(c);
      return c;
    }
    // append/prepend/before/after/replaceWith, как в браузере: не узел — это строка, и null становится текстом «null».
    _nodes(list) { return list.map((v) => (v instanceof Node ? v : this._doc.createTextNode(String(v)))); }
    insertBefore(c, ref) {
      if (c && c.nodeType === 11) { [...c.childNodes].forEach((k) => this.insertBefore(k, ref)); return c; }
      c = this._adopt(c);
      const i = ref ? this.childNodes.indexOf(ref) : -1;
      if (ref && i < 0) throw new Error("NotFoundError: reference node is not a child of this node");
      if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
      c.parentNode = this;
      return c;
    }
    appendChild(c) { return this.insertBefore(c, null); }
    removeChild(c) {
      const i = this.childNodes.indexOf(c);
      if (i < 0) throw new Error("NotFoundError: the node to be removed is not a child of this node");
      this.childNodes.splice(i, 1);
      c.parentNode = null;
      const doc = this._doc;
      if (doc && doc._active && c.contains(doc._active)) doc._active = null;
      return c;
    }
    replaceChild(n, old) { this.insertBefore(n, old); return this.removeChild(old); }
    append(...nodes) { this._nodes(nodes).forEach((n) => this.appendChild(n)); }
    prepend(...nodes) { const ref = this.firstChild; this._nodes(nodes).forEach((n) => this.insertBefore(n, ref)); }
    replaceChildren(...nodes) { const list = this._nodes(nodes); [...this.childNodes].forEach((k) => this.removeChild(k)); list.forEach((n) => this.appendChild(n)); }
    get textContent() { return this.childNodes.map((k) => (k.nodeType === 8 ? "" : k.textContent)).join(""); }
    set textContent(v) { [...this.childNodes].forEach((k) => this.removeChild(k)); if (v !== null && v !== undefined && String(v) !== "") this.appendChild(this._doc.createTextNode(String(v))); }
    // ── События ──
    addEventListener(type, fn, opts) {
      if (!fn) return;
      const capture = typeof opts === "boolean" ? opts : !!(opts && opts.capture);
      const once = !!(opts && typeof opts === "object" && opts.once);
      this._ls = this._ls || {};
      const list = (this._ls[type] = this._ls[type] || []);
      if (!list.some((l) => l.fn === fn && l.capture === capture)) list.push({ fn, capture, once });
    }
    removeEventListener(type, fn, opts) {
      const capture = typeof opts === "boolean" ? opts : !!(opts && opts.capture);
      if (this._ls && this._ls[type]) this._ls[type] = this._ls[type].filter((l) => !(l.fn === fn && l.capture === capture));
    }
    dispatchEvent(ev) { return dispatch(this, ev); }
  }
  // Слушатели окна живут в том же формате, что у узлов: окно — последняя ступень всплытия.
  const winTarget = { _ls: null };
  ["addEventListener", "removeEventListener"].forEach((k) => { winTarget[k] = Node.prototype[k]; });
  function invoke(node, ev, capturePhase, atTarget) {
    const list = node._ls && node._ls[ev.type];
    if (!list || !list.length) return;
    for (const l of [...list]) {
      if (!atTarget && l.capture !== capturePhase) continue;
      if (ev._stopImm) break;
      if (l.once) node.removeEventListener(ev.type, l.fn, l.capture);
      ev.currentTarget = node === winTarget ? win : node;
      try {
        if (typeof l.fn === "function") l.fn.call(ev.currentTarget, ev); else l.fn.handleEvent(ev);
      } catch (err) { report(`uncaught in ${ev.type} listener: ${err && err.stack ? err.stack : err}`); }
    }
  }
  function dispatch(target, ev) {
    const t0 = target === win ? winTarget : target;
    ev.target = target;
    const chain = [];
    if (t0 !== winTarget) {
      for (let n = t0.parentNode; n; n = n.parentNode) chain.push(n);
      if (chain.length && chain[chain.length - 1].nodeType === 9) chain.push(winTarget);
    }
    ev._path = [t0, ...chain].map((n) => (n === winTarget ? win : n));
    for (let k = chain.length - 1; k >= 0 && !ev._stop; k -= 1) { ev.eventPhase = 1; invoke(chain[k], ev, true, false); }
    if (!ev._stop) { ev.eventPhase = 2; invoke(t0, ev, true, true); }
    if (ev.bubbles) for (let k = 0; k < chain.length && !ev._stop; k += 1) { ev.eventPhase = 3; invoke(chain[k], ev, false, false); }
    ev.eventPhase = 0;
    ev.currentTarget = null;
    return !ev.defaultPrevented;
  }
  class Event {
    constructor(type, init = {}) {
      this.type = String(type);
      this.bubbles = !!init.bubbles; this.cancelable = !!init.cancelable; this.composed = !!init.composed;
      this.defaultPrevented = false; this.isTrusted = true; this.timeStamp = Date.now();
      this.target = null; this.currentTarget = null; this.eventPhase = 0;
      this._stop = false; this._stopImm = false; this._path = [];
      Object.keys(init).forEach((k) => { if (!(k in this)) this[k] = init[k]; });
    }
    get srcElement() { return this.target; }
    preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
    stopPropagation() { this._stop = true; }
    stopImmediatePropagation() { this._stop = true; this._stopImm = true; }
    composedPath() { return [...this._path]; }
  }
  class UIEvent extends Event {}
  class MouseEvent extends UIEvent {
    constructor(type, init = {}) {
      super(type, init);
      ["button", "buttons", "detail", "clientX", "clientY", "screenX", "screenY", "offsetX", "offsetY"].forEach((k) => { if (this[k] === undefined) this[k] = 0; });
      ["altKey", "ctrlKey", "metaKey", "shiftKey"].forEach((k) => { this[k] = !!init[k]; });
    }
  }
  class KeyboardEvent extends UIEvent {
    constructor(type, init = {}) {
      super(type, init);
      this.key = init.key === undefined ? "" : String(init.key);
      this.code = init.code || ""; this.repeat = !!init.repeat; this.isComposing = !!init.isComposing;
      ["altKey", "ctrlKey", "metaKey", "shiftKey"].forEach((k) => { this[k] = !!init[k]; });
    }
  }
  class FocusEvent extends UIEvent {}
  class InputEvent extends UIEvent {}
  class CustomEvent extends Event { constructor(type, init = {}) { super(type, init); this.detail = init.detail === undefined ? null : init.detail; } }

  class CharacterData extends Node {
    constructor(doc, type, data) { super(doc, type); this.data = String(data); }
    get nodeValue() { return this.data; }
    set nodeValue(v) { this.data = String(v); }
    get textContent() { return this.data; }
    set textContent(v) { this.data = String(v); }
    get length() { return this.data.length; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    cloneNode() { return this.nodeType === 3 ? this._doc.createTextNode(this.data) : this._doc.createComment(this.data); }
  }
  class Text extends CharacterData { get nodeName() { return "#text"; } }
  class Comment extends CharacterData { get nodeName() { return "#comment"; } }

  class ParentNode extends Node {
    get children() { return this.childNodes.filter((k) => k.nodeType === 1); }
    get childElementCount() { return this.children.length; }
    get firstElementChild() { return this.children[0] || null; }
    get lastElementChild() { const c = this.children; return c[c.length - 1] || null; }
    querySelectorAll(sel) { const g = parseSelectorList(sel); const out = []; for (const n of descendants(this)) if (listMatch(n, g, this)) out.push(n); return out; }
    querySelector(sel) { const g = parseSelectorList(sel); for (const n of descendants(this)) if (listMatch(n, g, this)) return n; return null; }
    getElementsByTagName(tag) { return this.querySelectorAll(tag); }
    getElementsByClassName(cls) { return this.querySelectorAll(String(cls).trim().split(/\s+/).map((c) => `.${c}`).join("")); }
    getElementById(id) { for (const n of descendants(this)) if (n.getAttribute("id") === String(id)) return n; return null; }
  }
  class DocumentFragment extends ParentNode {
    constructor(doc) { super(doc, 11); }
    get nodeName() { return "#document-fragment"; }
    cloneNode(deep) { const f = new DocumentFragment(this._doc); if (deep) this.childNodes.forEach((k) => f.appendChild(k.cloneNode(true))); return f; }
  }

  class TokenList {
    constructor(el) { this._el = el; }
    _get() { return (this._el.getAttribute("class") || "").split(/\s+/).filter(Boolean); }
    _set(l) { this._el.setAttribute("class", l.join(" ")); }
    add(...t) { const l = this._get(); t.forEach((x) => { if (!l.includes(x)) l.push(x); }); this._set(l); }
    remove(...t) { this._set(this._get().filter((x) => !t.includes(x))); }
    toggle(x, force) { const want = force === undefined ? !this.contains(x) : !!force; if (want) this.add(x); else this.remove(x); return want; }
    replace(a, b) { const l = this._get(); const i = l.indexOf(a); if (i < 0) return false; l[i] = b; this._set(l); return true; }
    contains(x) { return this._get().includes(x); }
    item(i) { return this._get()[i] ?? null; }
    get length() { return this._get().length; }
    get value() { return this._el.getAttribute("class") || ""; }
    toString() { return this.value; }
    forEach(fn) { this._get().forEach(fn); }
    [Symbol.iterator]() { return this._get()[Symbol.iterator](); }
  }
  const kebab = (p) => (p.startsWith("--") ? p : p.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`));
  function makeStyle() {
    const props = new Map();
    const api = {
      setProperty(p, v) { if (v === null || v === undefined || String(v) === "") props.delete(kebab(String(p))); else props.set(kebab(String(p)), String(v)); },
      removeProperty(p) { const k = kebab(String(p)); const old = props.get(k) || ""; props.delete(k); return old; },
      getPropertyValue(p) { return props.get(kebab(String(p))) || ""; },
      get cssText() { return [...props].map(([k, v]) => `${k}: ${v};`).join(" "); },
      set cssText(s) {
        props.clear();
        String(s || "").split(";").forEach((d) => { const j = d.indexOf(":"); if (j > 0) props.set(d.slice(0, j).trim().toLowerCase(), d.slice(j + 1).trim()); });
      },
      get length() { return props.size; },
      item(i) { return [...props.keys()][i] || ""; },
    };
    return new Proxy(api, {
      get(t, k) { if (typeof k === "symbol" || k in t) return Reflect.get(t, k); return props.get(kebab(k)) || ""; },
      set(t, k, v) { if (typeof k === "symbol" || k in t) return Reflect.set(t, k, v); api.setProperty(k, v); return true; },
    });
  }
  const dataAttr = (k) => `data-${k.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;
  const dataKey = (a) => a.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  const BOOL_ATTRS = { hidden: "hidden", disabled: "disabled", readOnly: "readonly", required: "required", multiple: "multiple", autofocus: "autofocus", open: "open", noValidate: "novalidate" };
  const STR_ATTRS = { id: "id", className: "class", title: "title", lang: "lang", dir: "dir", name: "name", placeholder: "placeholder", href: "href", src: "src",
    alt: "alt", htmlFor: "for", autocomplete: "autocomplete", download: "download", target: "target", rel: "rel", accept: "accept", inputMode: "inputmode",
    min: "min", max: "max", step: "step", pattern: "pattern", role: "role", ariaLabel: "aria-label" };
  const FORM = new Set(["input", "textarea", "select", "button"]);

  class Element extends ParentNode {
    constructor(doc, name, ns) {
      super(doc, 1);
      this.localName = String(name).toLowerCase();
      this.namespaceURI = ns || "http://www.w3.org/1999/xhtml";
      this._attrs = new Map();
      this._style = null; this._value = undefined; this._checked = undefined;
      this.scrollTop = 0; this.scrollLeft = 0;
      if (this.localName === "template") this.content = new DocumentFragment(doc);
    }
    get tagName() { return this.namespaceURI === "http://www.w3.org/1999/xhtml" ? this.localName.toUpperCase() : this.localName; }
    get nodeName() { return this.tagName; }
    // ── Атрибуты ──
    getAttributeNames() { return [...this._attrs.keys()].concat(this._style && this._style.length && !this._attrs.has("style") ? ["style"] : []); }
    getAttribute(k) {
      k = String(k);
      if (k === "style") return this._style && this._style.length ? this._style.cssText : (this._attrs.has("style") ? this._attrs.get("style") : null);
      if (this._attrs.has(k)) return this._attrs.get(k);
      const lk = k.toLowerCase();
      return this._attrs.has(lk) ? this._attrs.get(lk) : null;
    }
    setAttribute(k, v) {
      k = String(k);
      if (!/^[^\s"'>/=]+$/.test(k)) throw new Error(`InvalidCharacterError: '${k}' is not a valid attribute name`);
      if (k === "style") { this.style.cssText = String(v); this._attrs.delete("style"); return; }
      this._attrs.set(k, String(v));
    }
    removeAttribute(k) { k = String(k); if (k === "style" && this._style) this._style.cssText = ""; this._attrs.delete(k); this._attrs.delete(k.toLowerCase()); }
    hasAttribute(k) { return this.getAttribute(k) !== null; }
    toggleAttribute(k, force) { const want = force === undefined ? !this.hasAttribute(k) : !!force; if (want) { if (!this.hasAttribute(k)) this.setAttribute(k, ""); } else this.removeAttribute(k); return want; }
    get attributes() { return this.getAttributeNames().map((name) => ({ name, value: this.getAttribute(name) })); }
    get classList() { return new TokenList(this); }
    get dataset() {
      const el = this;
      return new Proxy({}, {
        get(_, k) { if (typeof k !== "string") return undefined; const v = el.getAttribute(dataAttr(k)); return v === null ? undefined : v; },
        set(_, k, v) { el.setAttribute(dataAttr(String(k)), String(v)); return true; },
        deleteProperty(_, k) { el.removeAttribute(dataAttr(String(k))); return true; },
        has(_, k) { return typeof k === "string" && el.hasAttribute(dataAttr(k)); },
        ownKeys() { return el.getAttributeNames().filter((a) => a.startsWith("data-")).map(dataKey); },
        getOwnPropertyDescriptor(_, k) { const v = typeof k === "string" ? el.getAttribute(dataAttr(k)) : null; return v === null ? undefined : { value: v, writable: true, enumerable: true, configurable: true }; },
      });
    }
    get style() { if (!this._style) { this._style = makeStyle(); if (this._attrs.has("style")) { this._style.cssText = this._attrs.get("style"); this._attrs.delete("style"); } } return this._style; }
    set style(v) { this.style.cssText = String(v); }
    // ── Свойства форм ──
    get type() { const v = this.getAttribute("type"); if (this.localName === "input") return (v || "text").toLowerCase(); if (this.localName === "button") return (v || "submit").toLowerCase(); return v || ""; }
    set type(v) { this.setAttribute("type", v); }
    get value() {
      if (this.localName === "select") { const o = this.selectedOptions[0]; return o ? o.value : ""; }
      if (this.localName === "option") { const v = this.getAttribute("value"); return v === null ? this.textContent.trim() : v; }
      if (this._value !== undefined) return this._value;
      if (this.localName === "textarea") return this.textContent;
      return this.getAttribute("value") ?? "";
    }
    set value(v) {
      if (this.localName === "select") { this.querySelectorAll("option").forEach((o) => { o._selected = o.value === String(v); }); return; }
      if (this.localName === "option") { this.setAttribute("value", v); return; }
      this._value = String(v ?? "");
    }
    get options() { return this.querySelectorAll("option"); }
    get selectedOptions() { const all = this.options; const on = all.filter((o) => o.selected); return on.length ? on.slice(0, 1) : all.slice(0, 1); }
    get selectedIndex() { return this.options.indexOf(this.selectedOptions[0]); }
    set selectedIndex(i) { this.options.forEach((o, k) => { o._selected = k === i; }); }
    get selected() { return this._selected !== undefined ? this._selected : this.hasAttribute("selected"); }
    set selected(v) { this._selected = !!v; }
    get checked() { return this._checked !== undefined ? this._checked : this.hasAttribute("checked"); }
    set checked(v) { this._checked = !!v; }
    get tabIndex() { const v = this.getAttribute("tabindex"); return v === null ? (FORM.has(this.localName) || this.localName === "a" ? 0 : -1) : Number(v); }
    set tabIndex(v) { this.setAttribute("tabindex", String(v)); }
    // ── Разметка и текст ──
    get innerHTML() { return (this.content || this).childNodes.map(serialize).join(""); }
    set innerHTML(v) {
      const host = this.localName === "template" ? this.content : this;
      [...host.childNodes].forEach((k) => host.removeChild(k));
      if (v !== null && v !== undefined && String(v) !== "") parseHTML(this._doc, host, String(v));
    }
    get outerHTML() { return serialize(this); }
    get innerText() { return this.textContent; }
    set innerText(v) { this.textContent = v; }
    // ── Дерево ──
    get nextElementSibling() { for (let n = this.nextSibling; n; n = n.nextSibling) if (n.nodeType === 1) return n; return null; }
    get previousElementSibling() { for (let n = this.previousSibling; n; n = n.previousSibling) if (n.nodeType === 1) return n; return null; }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    before(...nodes) { const p = this.parentNode; if (p) this._nodes(nodes).forEach((n) => p.insertBefore(n, this)); }
    after(...nodes) { const p = this.parentNode; if (!p) return; const ref = this.nextSibling; this._nodes(nodes).forEach((n) => p.insertBefore(n, ref)); }
    replaceWith(...nodes) {
      const p = this.parentNode;
      if (!p) return;
      const list = this._nodes(nodes).filter((n) => n !== this);
      const ref = this.nextSibling;
      p.removeChild(this);
      list.forEach((n) => p.insertBefore(n, ref && ref.parentNode === p ? ref : null));
    }
    matches(sel) { return listMatch(this, parseSelectorList(sel), this); }
    closest(sel) { const g = parseSelectorList(sel); for (let n = this; n && n.nodeType === 1; n = n.parentNode) if (listMatch(n, g, this)) return n; return null; }
    cloneNode(deep) {
      const c = this._doc.createElementNS(this.namespaceURI, this.localName);
      this.getAttributeNames().forEach((k) => c.setAttribute(k, this.getAttribute(k)));
      if (deep) this.childNodes.forEach((k) => c.appendChild(k.cloneNode(true)));
      return c;
    }
    insertAdjacentElement(where, n) {
      const w = String(where).toLowerCase();
      if (w === "beforebegin") this.before(n); else if (w === "afterbegin") this.prepend(n); else if (w === "beforeend") this.append(n); else this.after(n);
      return n;
    }
    // ── Поведение ──
    focus() {
      const doc = this._doc;
      if (!this.isConnected || doc._active === this) return;
      const prev = doc._active;
      doc._active = this;
      if (prev) { dispatch(prev, new FocusEvent("blur", { relatedTarget: this })); dispatch(prev, new FocusEvent("focusout", { bubbles: true, relatedTarget: this })); }
      dispatch(this, new FocusEvent("focus", { relatedTarget: prev || null }));
      dispatch(this, new FocusEvent("focusin", { bubbles: true, relatedTarget: prev || null }));
    }
    blur() {
      const doc = this._doc;
      if (doc._active !== this) return;
      doc._active = null;
      dispatch(this, new FocusEvent("blur", {}));
      dispatch(this, new FocusEvent("focusout", { bubbles: true }));
    }
    click() {
      if (FORM.has(this.localName) && this.disabled) return;
      const box = this.localName === "input" && (this.type === "checkbox" || this.type === "radio");
      if (box) this.checked = this.type === "radio" ? true : !this.checked;
      const ok = dispatch(this, new MouseEvent("click", { bubbles: true, cancelable: true, composed: true, detail: 1, view: win }));
      if (box && ok) { dispatch(this, new InputEvent("input", { bubbles: true })); dispatch(this, new Event("change", { bubbles: true })); }
    }
    select() {}
    setSelectionRange() {}
    scrollIntoView() {}
    scrollTo() {}
    scrollBy() {}
    getBoundingClientRect() { return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON() { return this; } }; }
    getClientRects() { return []; }
    get offsetWidth() { return 0; } get offsetHeight() { return 0; } get offsetTop() { return 0; } get offsetLeft() { return 0; } get offsetParent() { return null; }
    get clientWidth() { return 0; } get clientHeight() { return 0; } get scrollWidth() { return 0; } get scrollHeight() { return 0; }
    animate() { return { finished: Promise.resolve(), cancel() {}, finish() {}, onfinish: null }; }
    getAnimations() { return []; }
  }
  Object.entries(BOOL_ATTRS).forEach(([prop, attr]) => Object.defineProperty(Element.prototype, prop, {
    get() { return this.hasAttribute(attr); }, set(v) { this.toggleAttribute(attr, !!v); }, configurable: true }));
  Object.entries(STR_ATTRS).forEach(([prop, attr]) => Object.defineProperty(Element.prototype, prop, {
    get() { return this.getAttribute(attr) ?? ""; }, set(v) { this.setAttribute(attr, v); }, configurable: true }));

  class Document extends ParentNode {
    constructor() { super(null, 9); this._doc = this; this._active = null; this.hidden = false; this.visibilityState = "visible"; this.readyState = "complete"; this.cookie = ""; }
    get nodeName() { return "#document"; }
    get documentElement() { return this.children[0] || null; }
    get head() { const h = this.documentElement; return h ? h.children.find((k) => k.localName === "head") || null : null; }
    get body() { const h = this.documentElement; return h ? h.children.find((k) => k.localName === "body") || null : null; }
    get activeElement() { return this._active && this._active.isConnected ? this._active : this.body; }
    get title() { const t = this.querySelector("title"); return t ? t.textContent.trim() : ""; }
    set title(v) { let t = this.querySelector("title"); if (!t && this.head) { t = this.createElement("title"); this.head.appendChild(t); } if (t) t.textContent = v; }
    get defaultView() { return win; }
    get location() { return win.location; }
    createElement(tag) { return new Element(this, tag); }
    createElementNS(ns, tag) { return new Element(this, tag, ns); }
    createTextNode(s) { return new Text(this, 3, s); }
    createComment(s) { return new Comment(this, 8, s); }
    createDocumentFragment() { return new DocumentFragment(this); }
    hasFocus() { return true; }
    execCommand() { return false; }
    elementFromPoint() { return null; }
  }
  return { Node, Element, Text, Comment, DocumentFragment, Document, Event, UIEvent, MouseEvent, KeyboardEvent, FocusEvent, InputEvent, CustomEvent, dispatch, winTarget };
}

// ── Заглушка /api/… в форме server.py ────────────────────────────────────────
// Каталог действий — из настоящего lib/actions.json (как actions_payload): кнопки вроде «🎲 Случайное имя» панель
// рисует только для действий, которые есть в каталоге.
function actionsPayload(root) {
  const spec = JSON.parse(fs.readFileSync(path.join(root, "lib", "actions.json"), "utf8"));
  const actions = {};
  Object.entries(spec.actions || {}).forEach(([name, a]) => {
    if (!a || typeof a !== "object") return;
    actions[name] = { readonly: !!a.readonly, destructive: !!a.destructive, secret: !!a.secret, params: a.params || {}, description: a.description || {} };
  });
  const types = {};
  Object.entries(spec.param_types || {}).forEach(([name, p]) => {
    if (!p || typeof p !== "object") return;
    types[name] = {};
    ["type", "pattern", "enum", "minimum", "maximum", "maxLength", "description"].forEach((k) => { if (k in p) types[name][k] = p[k]; });
  });
  return { actions, param_types: types };
}
function fixtureApi(root, o) {
  const now = Math.floor(Date.now() / 1000);
  const iso = (sec) => new Date(sec * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const profiles = [
    { profile: "awg3", container: "amnezia-awg3", state: "running", port: 443, line: "3.1", peers: 2, image_tag: "3.1.20260828", image_digest: "sha256:0", enabled: true, subnet: "10.8.3.0/24", mtu: 1280, preset: "default", installed: true },
    { profile: "awg2", container: "amnezia-awg2", state: "running", port: 39001, line: "2.0", peers: 1, image_tag: "3.1.20260828", image_digest: "sha256:0", enabled: true, subnet: "10.8.2.0/24", mtu: 1280, preset: "default", installed: true },
  ];
  const peer = (ip, pubkey, ts) => ({ ip, pubkey, created: "2026-09-01T10:00:00Z", active: true, last_handshake: ts, rx: 0, tx: 0, has_key: true, handshake_ts: ts, online: !!ts && now - ts < 180 });
  const users = [
    { name: "admin", label: "", created: "2026-09-01T10:00:00Z", enabled: true, quota_bytes: 0, expires: "", traffic: { rx: 1048576, tx: 2097152, last_rx: 0, last_tx: 0, updated: "" },
      profiles: ["awg3", "awg2"], peers: { awg3: peer("10.8.3.2", "PUBADMIN3", now - 30), awg2: peer("10.8.2.2", "PUBADMIN2", 0) }, state: "on", mark: "●", last_handshake: now - 30, online: true },
    { name: "anna", label: "Анна", created: "2026-09-02T10:00:00Z", enabled: true, quota_bytes: 0, expires: "", traffic: { rx: 524288, tx: 1048576, last_rx: 0, last_tx: 0, updated: "" },
      profiles: ["awg3"], peers: { awg3: peer("10.8.3.3", "PUBANNA3", 0) }, state: "on", mark: "●", last_handshake: 0, online: false },
  ];
  const cfgProfiles = {};
  profiles.forEach((p) => { cfgProfiles[p.profile] = { line: p.line, port: p.port, subnet: p.subnet, mtu: p.mtu, preset: p.preset, container: p.container }; });
  const config = { schema: 1, version: "0.5.0", installed: true, mode: "lite", lang: "ru", domain: "", server_ip: "203.0.113.10", endpoint_host: "", endpoint_auto: "203.0.113.10",
    endpoint_effective: "203.0.113.10", client_dns: ["1.1.1.1"], profiles: cfgProfiles, panel: { port: 1985, miniapp_path: "", trust_tunnel: true },
    site: { template_id: "", template_source: "", listen: "public", mcp_location: false, panel_path: "" }, options: { drop_icmp: false, alt_svc: false }, mcp: { http_enabled: false, http_port: 1986 } };
  const services = { docker: "active", "goamnezia-bot": "active", "goamnezia-admin": "active" };
  const get = {
    "/api/session": { state: "ok", seconds: 900, ttl: 900, grace: 60, tfa: true, lang: "ru", version: "0.5.0", password: true, who: o.tg ? "tg:424242" : "tunnel" },
    "/api/actions": actionsPayload(root),
    "/api/appearance": { appearance: o.serverLook || null, defaults: { theme: "system", accent: "ga", hue: 200, tint: "neutral", density: "detailed", radius: 8, btn: "pill", cards: "shadow", nav: "full", fs: "m", vibrant: false, motion: true } },
    "/api/overview": { ok_status: true, status_error: null, version: "0.5.0", installed: true, mode: "lite", domain: "", server_ip: "203.0.113.10", endpoint_host: "",
      endpoint_host_gone: false, server_ip_change: null, upstream: null, lang: "ru", users_total: users.length, users_online: 1,
      profiles: profiles.map((p) => ({ profile: p.profile, container: p.container, state: p.state, port: p.port, line: p.line, peers: p.peers, image_tag: p.image_tag, enabled: true, subnet: p.subnet })),
      services, panel_url: "", traffic_total: { rx: 1572864, tx: 3145728 }, generated_at: iso(now), tunnel_host: "203.0.113.10" },
    "/api/traffic": { profiles: profiles.map((p) => ({ profile: p.profile, peers: p.peers, online: p.profile === "awg3" ? 1 : 0, rx: 1048576, tx: 2097152, period: "live" })),
      users, online: 1, hours: [], days: [], month: null, today: null, online_window: 180, generated_at: iso(now) },
    "/api/users": { users, online: 1, generated_at: iso(now) },
    "/api/share": { available: true, shares: [], count: 0, error: null },
    "/api/profiles": { installed: profiles, available: ["awg2", "awg3", "awg1"], not_installed: ["awg1"], udp443: "awg3", mode: "lite", presets: ["default", "quic", "router"], error: null },
    // Ускорение AmneziaWG (kmod-status, KMOD_PLAN этап Б): модуль не ставился, профили на программе — как по умолчанию.
    "/api/kmod": { kmod: { loaded: false, version: "", patched: false, warn: false, state: "off", code: "", reason: "", kernel: "6.8.0-36-generic", want: "",
      installed: false, installed_version: "", owner: "", label: "3.1.20260906+kaskad-k31.1", profiles: { awg2: { engine: "program", want: "" }, awg3: { engine: "program", want: "" } },
      progress: null, selftest: null, failed: null, packages: [] }, error: null, job: null },
    "/api/logs/status": { available: true, areas: [{ area: "goamnezia", used_mb: 12, budget_mb: 200, percent: 6 }], free_mb: 12345, error: null },
    "/api/backups": { backups: [] },
    "/api/agents": { stub: false, agents: [], transport: null },
    "/api/clients": { available: true, checked: iso(now), error: null, apps: [
      { id: "amneziavpn", name: "AmneziaVPN", official: true, platforms: ["ios", "android", "windows", "macos"], mirror_platforms: ["android", "windows"] }] },
    "/api/mirror": { available: true, items: [], max_total_mb: 2048, used_mb: 0, error: null },
    "/api/settings": { config, endpoint_host_gone: false, server_ip_change: null, tunnel_host: "203.0.113.10", bot_state: "active", panel_url: "", panel_path_saved: false, tfa: true,
      bot_admins: { admins: [], owner: "" }, password: { set: true, login: "admin", updated: "", file: "/opt/goamnezia/admin-web/password.json", min_len: 10, reset_cmd: "/opt/goamnezia/admin-web/install_admin.sh --reset-password" },
      trust_tunnel: true, bind: "127.0.0.1:1985", token_file: "/opt/goamnezia/admin-web/token", lock_file: "/run/goamnezia/config.lock", action_bin: "/opt/goamnezia/install.sh",
      components: ["system", "bot", "admin"], session: { ttl: 900, grace: 60 }, who: o.tg ? "tg:424242" : "tunnel" },
  };
  const post = {
    "/api/appearance": (body) => ({ appearance: body && body.appearance }),
    "/api/action/backup-last": () => ({ status: "success", message: "ok", tick_ok: true }),
    "/api/action/upstream-check": () => ({ status: "success", message: "ok", summary: null }),
    "/api/action/name-suggest": () => ({ status: "success", message: "ok", id: "misty-london", label: "Туманный Лондон" }),
  };
  // o.api["GET /api/…"] — ответ целиком ({status, body} или функция тела запроса); o.data["/api/…"] — правка данных
  // готового ответа на GET (получает копию, возвращает новые данные).
  return (method, p, body) => {
    const custom = o.api && o.api[`${method} ${p}`];
    if (custom !== undefined) return typeof custom === "function" ? custom(body) : custom;
    if (method === "GET" && Object.prototype.hasOwnProperty.call(get, p)) {
      const patch = o.data && o.data[p];
      return { status: 200, body: { ok: true, data: patch ? patch(JSON.parse(JSON.stringify(get[p]))) : get[p] } };
    }
    if (method === "POST" && post[p]) return { status: 200, body: { ok: true, data: post[p](body) } };
    return { status: 404, body: { ok: false, error: `no fixture for ${method} ${p}`, code: "not_found" } };
  };
}

// ── Окно ─────────────────────────────────────────────────────────────────────
class Storage {
  constructor(init) { this._m = new Map(Object.entries(init || {}).map(([k, v]) => [k, String(v)])); }
  getItem(k) { return this._m.has(String(k)) ? this._m.get(String(k)) : null; }
  setItem(k, v) { this._m.set(String(k), String(v)); }
  removeItem(k) { this._m.delete(String(k)); }
  clear() { this._m.clear(); }
  key(i) { return [...this._m.keys()][i] ?? null; }
  get length() { return this._m.size; }
}

// Подпись и цвета мини-аппа — как их кладёт в адрес клиент Telegram (#tgWebAppData=…&tgWebAppThemeParams=…).
const TG_INIT = "query_id=AAHdF6IQAAAAAN0XohDhrOrc&user=%7B%22id%22%3A424242%2C%22first_name%22%3A%22Test%22%7D&auth_date=1790000000&hash=0000";
const TG_THEME = { bg_color: "#ffffff", secondary_bg_color: "#efeff3", text_color: "#000000", hint_color: "#999999", link_color: "#2481cc", button_color: "#2481cc", button_text_color: "#ffffff" };

// Необработанный отказ обещания — ошибка той панели, что сейчас загружена (проверки идут по одной).
let activeReport = null;
process.on("unhandledRejection", (reason) => { if (activeReport) activeReport(`unhandled rejection: ${(reason && reason.stack) || reason}`); });

async function boot(o = {}) {
  const root = o.root || ROOT;
  const staticDir = path.join(root, "admin-web", "static");
  const errors = [];
  const report = (msg) => { errors.push(String(msg)); };
  activeReport = report;
  const win = {};
  const dom = makeDom(win, report);
  const doc = new dom.Document();

  // Таймеры под рукой теста: время идёт только в tick(), обещания доходят в settle().
  let clock = 0, seq = 0;
  const timers = new Map();
  const addTimer = (fn, ms, every, args) => { seq += 1; timers.set(seq, { id: seq, fn, args, at: clock + Math.max(0, Number(ms) || 0), every }); return seq; };
  const settle = async (turns = 12) => { for (let k = 0; k < turns; k += 1) await new Promise((r) => setImmediate(r)); };
  async function tick(ms = 0) {
    const end = clock + Math.max(0, ms);
    for (let guard = 0; ; guard += 1) {
      if (guard > 10000) { report("timers: more than 10000 callbacks in one tick — a timer re-arms itself without delay"); break; }
      let next = null;
      for (const t of timers.values()) if (t.at <= end && (!next || t.at < next.at || (t.at === next.at && t.id < next.id))) next = t;
      if (!next) break;
      clock = next.at;
      if (next.every) next.at += next.every; else timers.delete(next.id);
      try { if (typeof next.fn === "function") next.fn(...(next.args || [])); } catch (err) { report(`uncaught in timer: ${err && err.stack ? err.stack : err}`); }
      await settle(4);
    }
    clock = end;
    await settle();
  }

  // Адрес и история: hashchange — только при переходе (location.hash = …, назад/вперёд), не от replaceState.
  const width = Number(o.width) || 1280;
  let url = new URL(`https://203.0.113.10:1985/${o.hash || ""}`);
  if (o.tg) {
    const theme = encodeURIComponent(JSON.stringify(o.tgTheme || TG_THEME));
    url = new URL(`https://203.0.113.10:1985/#tgWebAppData=${encodeURIComponent(TG_INIT)}&tgWebAppVersion=8.0&tgWebAppPlatform=ios&tgWebAppThemeParams=${theme}`);
  }
  const fireHash = (oldURL) => { addTimer(() => dom.dispatch(win, new dom.Event("hashchange", { oldURL, newURL: url.href })), 0, 0, []); };
  const location = {
    get href() { return url.href; }, set href(v) { const old = url.href; const oldHash = url.hash; url = new URL(v, url); if (url.hash !== oldHash) fireHash(old); },
    get protocol() { return url.protocol; }, get host() { return url.host; }, get hostname() { return url.hostname; }, get port() { return url.port; },
    get pathname() { return url.pathname; }, get search() { return url.search; }, get origin() { return url.origin; },
    get hash() { return url.hash; }, set hash(v) { const old = url.href; const oldHash = url.hash; url.hash = v; if (url.hash !== oldHash) fireHash(old); },
    reloads: 0, reload() { this.reloads += 1; }, assign(v) { this.href = v; }, replace(v) { this.href = v; }, toString() { return url.href; },
  };
  const history = {
    length: 1, state: null,
    replaceState(state, _t, u) { this.state = state; if (u !== undefined && u !== null) url = new URL(String(u), url); },
    pushState(state, _t, u) { this.length += 1; this.replaceState(state, _t, u); },
    back() {}, forward() {}, go() {},
  };
  const mediaMatches = (q) => {
    let ok = true;
    String(q).replace(/\(\s*([a-z-]+)\s*:\s*([^)]+?)\s*\)/g, (_, k, v) => {
      if (k === "max-width") ok = ok && width <= parseFloat(v);
      else if (k === "min-width") ok = ok && width >= parseFloat(v);
      else if (k === "prefers-color-scheme") ok = ok && (v === "dark") === !!o.dark;
      else if (k === "prefers-reduced-motion") ok = ok && v === "no-preference";
      else if (k === "hover") ok = ok && (v === "hover") === width >= 700;
      else if (k === "pointer") ok = ok && (v === "coarse") === width < 700;
      else ok = false;
      return "";
    });
    return ok;
  };
  const tgEvents = [];
  const fetchLog = [];
  const route = fixtureApi(root, o);
  const localStorage = new Storage(o.localStorage || {});
  if (o.look) localStorage.setItem("goamnezia-appearance-v1", JSON.stringify(o.look));
  if (o.lang) localStorage.setItem("goamnezia-lang", o.lang);
  const consoleSink = { log() {}, info() {}, debug() {}, warn() {}, error: (...a) => report(`console.error: ${a.map((x) => (x && x.stack) || String(x)).join(" ")}`) };

  Object.assign(win, {
    document: doc, location, history, console: consoleSink, navigator: { platform: "MacIntel", userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) toy-dom", language: "ru-RU",
      languages: ["ru-RU", "ru", "en"], maxTouchPoints: width < 700 ? 5 : 0, clipboard: { writeText: async () => {} }, onLine: true },
    localStorage, sessionStorage: new Storage(o.sessionStorage || {}),
    innerWidth: width, innerHeight: width < 700 ? 844 : 900, devicePixelRatio: 2, isSecureContext: true, name: "",
    matchMedia: (q) => ({ media: String(q), matches: mediaMatches(q), onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
    getComputedStyle: () => new Proxy({ getPropertyValue: () => "" }, { get: (t, k) => (k in t ? t[k] : "") }),
    setTimeout: (fn, ms, ...args) => addTimer(fn, ms, 0, args), clearTimeout: (id) => { timers.delete(id); },
    setInterval: (fn, ms, ...args) => addTimer(fn, ms, Math.max(1, Number(ms) || 0), args), clearInterval: (id) => { timers.delete(id); },
    requestAnimationFrame: (fn) => addTimer(() => fn(clock), 16, 0, []), cancelAnimationFrame: (id) => { timers.delete(id); },
    queueMicrotask, structuredClone: globalThis.structuredClone, performance: { now: () => clock }, crypto: globalThis.crypto,
    URL, URLSearchParams, AbortController, AbortSignal, Blob, TextEncoder, TextDecoder,
    btoa: (s) => Buffer.from(String(s), "binary").toString("base64"), atob: (s) => Buffer.from(String(s), "base64").toString("binary"),
    FileReader: class { readAsText() {} readAsDataURL() {} addEventListener() {} },
    CSS: { supports: () => false, escape: (s) => String(s).replace(/[^\w-]/g, (c) => `\\${c}`) },
    Node: dom.Node, Element: dom.Element, HTMLElement: dom.Element, Text: dom.Text, DocumentFragment: dom.DocumentFragment, Document: dom.Document,
    Event: dom.Event, UIEvent: dom.UIEvent, MouseEvent: dom.MouseEvent, KeyboardEvent: dom.KeyboardEvent, FocusEvent: dom.FocusEvent, InputEvent: dom.InputEvent, CustomEvent: dom.CustomEvent,
    addEventListener: (type, fn, opts) => dom.winTarget.addEventListener(type, fn, opts),
    removeEventListener: (type, fn, opts) => dom.winTarget.removeEventListener(type, fn, opts),
    dispatchEvent: (ev) => dom.dispatch(win, ev),
    postMessage() {},
    fetch: (u, opts = {}) => {
      const target = new URL(String(u), url);
      const method = String(opts.method || "GET").toUpperCase();
      let body = null;
      try { body = opts.body ? JSON.parse(opts.body) : null; } catch (e) { body = opts.body; }
      fetchLog.push({ method, path: target.pathname, query: target.search, body, headers: { ...(opts.headers || {}) } });
      const build = (r) => {
        const status = r.status || 200;
        const text = JSON.stringify(r.body === undefined ? {} : r.body);
        const res = { status, ok: status >= 200 && status < 300, statusText: String(status), url: target.href,
          headers: { get: (k) => (String(k).toLowerCase() === "content-type" ? "application/json" : null), has: (k) => String(k).toLowerCase() === "content-type" },
          // Разбор — JSON самой панели: объекты ответа должны быть из её мира, как в браузере.
          json: async () => ctxJSON.parse(text), text: async () => text, clone: () => res };
        return res;
      };
      // Ответ может прийти позже: заглушка долгого действия отдаёт обещание, и тест видит экран «выполняется».
      const answer = () => {
        const r = route(method, target.pathname, body);
        return r && typeof r.then === "function" ? r.then(build) : Promise.resolve(build(r));
      };
      // o.hold(method, path, headers) → обещание: ответ ждёт его (медленный сервер — тест сам решает, когда ответить).
      const held = o.hold ? o.hold(method, target.pathname, { ...(opts.headers || {}) }, clock) : null;
      return held && typeof held.then === "function" ? held.then(answer) : answer();
    },
  });
  win.window = win; win.self = win; win.top = win; win.parent = win; win.frames = win;
  if (o.tg) win.TelegramWebviewProxy = { postEvent: (type, data) => { tgEvents.push({ type, data }); } };
  vm.createContext(win);
  const ctxJSON = vm.runInContext("JSON", win);

  // Разметка — настоящий index.html; его первый скрипт ставит вид на <html> (data-theme, data-nav…) до app.js.
  const html = fs.readFileSync(path.join(staticDir, "index.html"), "utf8");
  const tmp = doc.createDocumentFragment();
  parseHTML(doc, tmp, html);
  const htmlEl = tmp.children.find((k) => k.localName === "html");
  if (!htmlEl) throw new Error("index.html: no <html>");
  doc.appendChild(htmlEl);
  for (const s of doc.querySelectorAll("script:not([src])")) vm.runInContext(s.textContent, win, { filename: "index.html (inline script)" });
  const appSrc = fs.readFileSync(path.join(staticDir, "app.js"), "utf8");
  vm.runInContext(appSrc, win, { filename: path.join(staticDir, "app.js") });
  await settle();
  await tick(0);

  const $ = (sel) => doc.querySelector(sel);
  const $$ = (sel) => doc.querySelectorAll(sel);
  const need = (x) => { const n = typeof x === "string" ? $(x) : x; if (!n) throw new Error(`no element: ${x}`); return n; };
  const P = {
    win, doc, errors, fetchLog, tgEvents, settle, tick, $, $$,
    // Видимый раздел — секция без hidden (setPage прячет остальные).
    page() { const s = doc.querySelectorAll("section[data-page]").filter((n) => !n.hidden); return s.length === 1 ? s[0].getAttribute("data-page") : `?${s.map((n) => n.getAttribute("data-page")).join(",")}`; },
    hash() { return url.hash; },
    // Клик как у мыши: по элементу или селектору, потом — обещания и короткие таймеры (фокус полей, 20–30 мс).
    async click(x) { need(x).click(); await settle(); await tick(50); },
    async key(key, mods = {}, target) {
      const t = target ? need(target) : doc.activeElement || doc.body;
      dom.dispatch(t, new dom.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...mods }));
      await settle(); await tick(50);
    },
    async type(x, text) { const n = need(x); n.value = text; dom.dispatch(n, new dom.InputEvent("input", { bubbles: true })); await settle(); },
    // Переход по адресу — как «назад» или правка хэша руками: событие hashchange.
    async go(hash) { location.hash = hash; await tick(0); },
    // Первый элемент (по селектору), чей текст без пробелов по краям равен text.
    byText(text, sel = "*") { return doc.querySelectorAll(sel).find((n) => n.textContent.trim() === text) || null; },
    visible(x) { for (let n = need(x); n && n.nodeType === 1; n = n.parentNode) if (n.hidden) return false; return true; },
    calls(method, p) { return fetchLog.filter((c) => c.method === method && c.path === p); },
    close() { timers.clear(); if (activeReport === report) activeReport = null; },
  };
  return P;
}

module.exports = { boot, parseHTML, parseSelectorList };
