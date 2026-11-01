#!/usr/bin/env python3
"""Обезличивание сцены заглушки панели (UI-2, REV-ui2 С2): ответы GET живого узла → lib/kaskad/panel/static/dev-mock-scene-<имя>.js.

    python3 tools/scene_scrub.py make <raw.json> <имя> <out.js>   — сцена из записи (запись — k3/stand/t-ui2-tools/rec.mjs,
                                                                     в репозиторий не кладётся)
    python3 tools/scene_scrub.py check <сцена.js> [...]          — проверка готовой сцены (её же зовёт tests/test_ui2_layout.py)

Правила (решение координатора, BRIEF-UI-2-r2 п. 2):
- IP: остаются только адреса из разрешённых диапазонов (ALLOWED_V4/ALLOWED_V6) — частные, петля, 100.64/10, 198.18/15 и
  документальные; всё остальное → 203.0.113.x / 2001:db8::x. Строка вида адреса — тоже адрес, кроме версии под ключом
  *version* («3.3.7.3» → «3.3.7»);
- адрес ловится с любыми соседними знаками (точка в конце фразы, запятая, скобка, кавычка, «:порт», «слово:2a01:…»);
- домены (в том числе IDN «мойузел.рф», punycode xn--… и после «_»): остаются только example.com / example.net / example.org и
  их поддомены; прочие → узел — node.example.com, другие — example.com, example.net, …; имена файлов (.dat, .timer, .sock, …)
  и ключи текстов из каталога lib/kaskad/i18n/catalog (gw_host.why.anchor) — не домены;
- UUID под любым ключом → 00000000-0000-4000-8000-00000000000N (видно, что подделка);
- значения ключей-секретов (SECRET_KEYS и всё с pass/secret/token/private/psk/short_id в имени: short_id(s), path, pass,
  password, obfs_password, pbk, sid, pub, отпечатки, …) и длинные случайные строки → подделка той же длины и того же набора
  знаков; параметры маскировки AWG (H1–H4, S1–S4, I1–I5, Jc, Jmin, Jmax) — тоже, в допустимых пределах (Hn — в своей
  четверти 0…2^32−1, начало ≤ конца; Jmin ≤ Jmax);
- сертификат: not_after → 2030-01-01T00:00:00Z, issuer → Example CA; прочие ISO-времена — без секунд;
- почта → admin@example.com, chat_id → 123456789.
Скрипт падает (код 1), если после чистки осталась хоть одна исходная заменённая строка или audit() нашёл адрес, домен или
UUID вне правил. Сами значения в выводе не печатаются."""

import hashlib
import ipaddress
import json
import os
import re
import sys

ALLOWED_V4 = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "127.0.0.0/8", "198.18.0.0/15",
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
# не адрес узла: «слушать на всех» и маски сети (0.0.0.0, 255.255.255.0) — служебные значения, их не подменяем
SPECIAL_V4 = [ipaddress.ip_network("0.0.0.0/32")]
ALLOWED_V6 = [ipaddress.ip_network(n) for n in ("::/128", "::1/128", "fe80::/10", "fc00::/7", "2001:db8::/32")]
ALLOWED_DOMAINS = ("example.com", "example.net", "example.org")
# имена файлов и служб, а не домены (только окончания, которых нет среди доменов верхнего уровня)
NOT_DOMAIN_SUFFIX = ("dat", "timer", "sock", "service", "socket", "json", "conf", "log", "wait")
SECRET_KEYS = {"pub", "pbk", "public_key", "publickey", "private_key", "privatekey", "short_id", "short_ids", "shortid", "sid",
               "sha256", "fingerprint", "fingerprints", "headerprotectionkey", "token", "password", "pass", "obfs_password",
               "psk", "uuid", "secret", "path", "serial", "spx"}
SECRET_PART = ("pass", "secret", "token", "private", "psk", "short_id")
# поля, где домен — сам узел (его метки не должны встречаться в сцене и отдельным словом)
OWN_KEYS = {"domain", "domain_unicode", "host", "cert_host", "names", "panel_url_masked", "sni", "dest"}
AWG_KEYS = re.compile(r"^(H[1-4]|S[1-4]|I[1-5]|Jc|Jmin|Jmax)$")
AWG_INT_KEYS = re.compile(r"^(?i:h[1-4]|s[1-4]|jc|jmin|jmax)$")
FAKE_UUID = "00000000-0000-4000-8000-%012x"
FAKE_UUID_RE = re.compile(r"^00000000-0000-4000-8000-[0-9a-f]{12}$")

# REV-ui2-2 С1: адрес ловится с любыми соседними знаками — точка в конце фразы («…не на 8.210.11.4.»), запятая, скобка,
# кавычка, «:порт»; не ловится только часть длинной цепочки цифр с точками (1.2.3.4.5 — версия, а не адрес)
IP4_RE = re.compile(r"(?<!\d)(?<!\d\.)\d{1,3}(?:\.\d{1,3}){3}(?!\.?\d)")
# IPv6: сплошные куски из шестнадцатеричных знаков, «:» и «.» (с двумя «:» и больше) — разбирает v6_spans(): обрезает точку
# и одиночное «:» по краям («адрес:2a01:…», «…::1.»), [адрес]:порт — скобка уже граница
IP6_RUN_RE = re.compile(r"[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*")
# домены: метки из любых букв и цифр (IDN — «мойузел.рф») и punycode (xn--…); начало — после любого знака, кроме буквы, цифры,
# «.» и «-» (после «_»: «_acme-challenge.узел…», «_x.example-real.net»); конец — перед любым знаком, кроме буквы, цифры, «_», «-»
# и «.буква» (точка в конце фразы — граница). Ключи текстов (gw_host.why.anchor) отсекает i18n_key() по каталогу текстов.
_LAB = r"[^\W_](?:[^\W_]|-)*"
DOM_RE = re.compile(r"(?<![^\W_])(?<![.-])(?:%s\.)+[^\W\d_](?:[^\W_]|-)*[^\W\d_](?![\w-])(?!\.[^\W_])" % _LAB)
UUID_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
ISO_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d):\d\d(\.\d+)?(Z|[+-]\d\d:?\d\d)?$")
TOKEN_RE = re.compile(r"[A-Za-z0-9+/_=-]{16,}")
VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+)\.\d+(-[0-9a-f]{6,40})?$")


def secret_key(key):
    k = (key or "").lower()
    return k in SECRET_KEYS or any(p in k for p in SECRET_PART)


def v4_kind(ip):
    """'ok' — разрешён, 'bad' — не адрес (число > 255), 'pub' — остальное."""
    try:
        a = ipaddress.IPv4Address(ip)
    except ValueError:
        return "bad"
    if any(a in n for n in ALLOWED_V4 + SPECIAL_V4) or ip.startswith("255."):
        return "ok"
    return "pub"


def v6_kind(s):
    try:
        a = ipaddress.IPv6Address(s)
    except ValueError:
        return "bad"
    return "ok" if any(a in n for n in ALLOWED_V6) else "pub"


def domain_ok(d):
    low = d.lower()
    return any(low == a or low.endswith("." + a) for a in ALLOWED_DOMAINS)


_I18N_KEYS = None


def i18n_keys():
    """Все ключи текстов Kaskad (lib/kaskad/i18n/catalog/*/*.json). Нет каталога — пустое множество: ключ считается доменом
    (лучше лишняя замена, чем пропуск)."""
    global _I18N_KEYS
    if _I18N_KEYS is None:
        keys = set()
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "i18n", "catalog")
        for lang in ("ru", "en"):
            d = os.path.join(base, lang)
            for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                if f.endswith(".json"):
                    try:
                        with open(os.path.join(d, f), encoding="utf-8") as fh:
                            keys.update(k for k in json.load(fh) if isinstance(k, str))
                    except (OSError, ValueError):
                        pass
        _I18N_KEYS = keys
    return _I18N_KEYS


def i18n_key(text, span):
    """Совпадение DOM_RE — часть ключа текста («host.why.anchor» в «gw_host.why.anchor»): весь сплошной кусок из
    [a-z0-9_.] вокруг совпадения — ключ из каталога. Иначе домен (в том числе «bot.realnode.uk» — каталог «bot» есть, ключа нет)."""
    if text is None or span is None:
        return False
    a, b = span
    while a > 0 and re.match(r"[A-Za-z0-9_.]", text[a - 1]):
        a -= 1
    while b < len(text) and re.match(r"[A-Za-z0-9_.]", text[b]):
        b += 1
    tok = text[a:b].rstrip(".")
    return tok in i18n_keys()


def not_domain(d, key=None, text=None, span=None):
    return d.lower().rsplit(".", 1)[-1] in NOT_DOMAIN_SUFFIX or i18n_key(text, span)


def _v6_ok(c):
    if c.count(":") < 2:
        return False
    try:
        ipaddress.IPv6Address(c)
        return True
    except ValueError:
        return False


def v6_spans(text):
    """[(начало, конец)] адресов IPv6 в строке. Кусок из [0-9A-Fa-f:.] с двумя «:» и больше: срезать точки в конце и одиночное
    «:» по краям; если кусок со «::» всё ещё не адрес — отбросить одну первую часть до «:» или «.» («cafe.2a01:…::1»).
    Длинные цепочки без «::» (отпечатки AB:CD:…) не режутся на куски — иначе любой хвост из 8 групп сойдёт за адрес."""
    out = []
    for m in IP6_RUN_RE.finditer(text):
        r, base = m.group(0), m.start()
        lo, hi = 0, len(r)
        while hi > lo and r[hi - 1] == ".":
            hi -= 1
        if hi - lo >= 2 and r[hi - 1] == ":" and r[hi - 2] != ":":
            hi -= 1
        if hi - lo >= 2 and r[lo] in ":." and r[lo + 1] != ":":
            lo += 1
        while lo < hi and r[lo] == ".":
            lo += 1
        if _v6_ok(r[lo:hi]):
            out.append((base + lo, base + hi))
            continue
        if "::" in r[lo:hi]:
            cut = [i for i in range(lo, hi - 1) if r[i] in ":." and r[i + 1] != ":" and (i == lo or r[i - 1] != ":")]
            if cut and _v6_ok(r[cut[0] + 1:hi]):
                out.append((base + cut[0] + 1, base + hi))
    return out


def v6_sub(fn, text):
    """Замена адресов IPv6 (как re.sub с функцией от строки адреса)."""
    parts, last = [], 0
    for a, b in v6_spans(text):
        parts += [text[last:a], fn(text[a:b])]
        last = b
    return "".join(parts) + text[last:]


def audit(obj):
    """Нарушения правил в разобранной сцене: [(вид, ключ)] — без самих значений."""
    bad = []

    def s(x, key):
        for m in IP4_RE.finditer(x):
            if v4_kind(m.group(0)) == "pub":
                bad.append(("адрес IPv4", key))
        for a, b in v6_spans(x):
            if v6_kind(x[a:b]) == "pub":
                bad.append(("адрес IPv6", key))
        for m in DOM_RE.finditer(x):
            if not domain_ok(m.group(0)) and not not_domain(m.group(0), key, x, m.span()):
                bad.append(("домен", key))
        for m in UUID_RE.finditer(x):
            if not FAKE_UUID_RE.match(m.group(0)):
                bad.append(("UUID", key))
        if key == "not_after" and x and not x.startswith("2030-01-01T00:00:00"):
            bad.append(("сертификат", key))
        if key == "issuer" and x and x != "Example CA":
            bad.append(("сертификат", key))

    def walk(x, key=None):
        if isinstance(x, dict):
            for k, v in x.items():
                s(k, None)
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str):
            s(x, key)
    walk(obj)
    return bad


U32 = 2 ** 32 - 1


def awg_h_range(s, n):
    """H1–H4 AWG — число или диапазон «a-b»: подделка в своей четверти 5…2^32−1 (Hn — n-я четверть), a ≤ b, диапазоны H1–H4
    не пересекаются (REV-ui2-2 П2: подделка не должна выглядеть ошибкой, которой на узле нет)."""
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", s.strip())
    if not m:
        return s
    return "-".join(str(v) for v in sorted(awg_h_num(int(g), n) for g in m.groups() if g is not None))


def awg_h_num(v, n):
    q = (U32 + 1) // 4
    return max(5, (n - 1) * q + v % q)


def load_scene(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"\[(\"[a-z0-9]{1,32}\")\] = (\{.*\});\s*$", src, re.S)
    if not m:
        raise ValueError("сцена не разобрана: " + os.path.basename(path))
    return json.loads(m.group(1)), json.loads(m.group(2)), src


class Scrub:
    def __init__(self):
        self.ipmap, self.dommap, self.uuidmap, self.orig, self.own = {}, {}, {}, set(), set()

    def fake(self, s):
        """Подделка той же длины и того же набора знаков (шестнадцатеричное — шестнадцатеричным)."""
        h = hashlib.sha256(("ui2-scene:" + s).encode()).hexdigest() * 8
        hexy = re.fullmatch(r"[0-9a-fA-F:-]+", s) is not None
        out = []
        for i, c in enumerate(s):
            n = int(h[i % len(h)], 16)
            if c.isdigit():
                out.append(str(n % 10))
            elif hexy and c in "abcdef":
                out.append("abcdef"[n % 6])
            elif hexy and c in "ABCDEF":
                out.append("ABCDEF"[n % 6])
            elif c.islower() and c.isascii():
                out.append(chr(97 + n % 26))
            elif c.isupper() and c.isascii():
                out.append(chr(65 + n % 26))
            else:
                out.append(c)
        r = "".join(out)
        if r == s and any(c.isalnum() for c in s):
            r = "".join("0" if c.isdigit() else ("x" if c.isalpha() else c) for c in s)
        self.orig.add(s)
        return r

    def ip4(self, m):
        ip = m.group(0)
        if v4_kind(ip) != "pub":
            return ip
        self.orig.add(ip)
        if ip not in self.ipmap:
            n = len(self.ipmap)
            self.ipmap[ip] = "203.0.113.%d" % (10 + n) if n < 240 else "198.51.100.%d" % (10 + n - 240)
        return self.ipmap[ip]

    def ip6(self, s):
        if v6_kind(s) != "pub":
            return s
        self.orig.add(s)
        return self.ipmap.setdefault(s, "2001:db8::%x" % (0x10 + len(self.ipmap)))

    def dom(self, m, key):
        d = m.group(0)
        if domain_ok(d) or not_domain(d, key, m.string, m.span()):
            return d
        low = d.lower()
        self.orig.add(d)
        if (key or "").lower() in OWN_KEYS:
            self.own.add(low)
        if low not in self.dommap:
            used = set(self.dommap.values())
            if low.count(".") >= 2:
                cand = ["node.example.com"] + ["node%d.example.com" % i for i in range(2, 999)]
            else:
                cand = list(ALLOWED_DOMAINS) + ["site%d.example.org" % i for i in range(4, 999)]
            self.dommap[low] = next(c for c in cand if c not in used)
        return self.dommap[low]

    def uuid(self, m):
        u = m.group(0).lower()
        if FAKE_UUID_RE.match(u):
            return u
        self.orig.add(m.group(0))
        return self.uuidmap.setdefault(u, FAKE_UUID % (len(self.uuidmap) + 1))

    def tok(self, m):
        s = m.group(0)
        mixed = re.search(r"[A-Z]", s) and re.search(r"[a-z]", s) and re.search(r"[0-9]", s)
        hexy = re.fullmatch(r"[0-9a-f]{16,}", s) and re.search(r"[a-f]", s) and re.search(r"[0-9]", s)
        return self.fake(s) if (mixed or hexy) and not FAKE_UUID_RE.match(s) else s

    def string(self, s, key=None):
        k = key or ""
        if not s:
            return s
        if k == "not_after":
            self.orig.add(s)
            return "2030-01-01T00:00:00Z"
        if k == "issuer":
            self.orig.add(s)
            return "Example CA"
        if k == "email":
            self.orig.add(s)
            return "admin@example.com"
        if k == "chat_id":
            self.orig.add(s)
            return "123456789"
        if "version" in k.lower():
            v = VERSION_RE.match(s)
            if v:
                return v.group(1) + (v.group(2) or "")
        iso = ISO_RE.match(s)
        if iso:
            return iso.group(1) + ":00" + (iso.group(3) or "")
        if re.match(r"^I[1-5]$", k):          # AWG I1–I5: «<b 0x…>» — подделываем только шестнадцатеричное после 0x
            return re.sub(r"0x([0-9a-fA-F]+)", lambda m: "0x" + self.fake(m.group(1)), s)
        if AWG_KEYS.match(k):                  # H1–H4, S1–S4, Jc, Jmin, Jmax: числа без ведущего нуля
            s = re.sub(r"\d+", lambda m: (lambda d: "1" + d[1:] if len(d) > 1 and d[0] == "0" else d)(self.fake(m.group(0))), s)
            return awg_h_range(s, int(k[1])) if re.match(r"^[Hh][1-4]$", k) else s
        if secret_key(k):
            s = UUID_RE.sub(self.uuid, s)
            return s if FAKE_UUID_RE.match(s) else self.fake(s)
        s = EMAIL_RE.sub(lambda m: (self.orig.add(m.group(0)), "admin@example.com")[1], s)
        s = UUID_RE.sub(self.uuid, s)
        s = IP4_RE.sub(self.ip4, s)
        s = v6_sub(self.ip6, s)
        s = DOM_RE.sub(lambda m: self.dom(m, key), s)
        s = TOKEN_RE.sub(self.tok, s)
        # маскированный секрет (видны первые и последние знаки: app-AbCd••••WxYz) — видимые части тоже подделка
        s = re.sub(r"[A-Za-z0-9]+(?:•+[A-Za-z0-9]+)+", lambda m: "•".join(self.fake(p) if p else p for p in m.group(0).split("•")), s)
        return s

    def walk(self, x, key=None):
        if isinstance(x, dict):
            out = {self.string(k): self.walk(v, k) for k, v in x.items()}
            for lo, hi in (("Jmin", "Jmax"), ("jmin", "jmax")):   # подделка AWG: Jmin ≤ Jmax, как на узле
                if lo in out and hi in out and str(out[lo]).isdigit() and str(out[hi]).isdigit() and int(out[lo]) > int(out[hi]):
                    out[lo], out[hi] = out[hi], out[lo]
            return out
        if isinstance(x, list):
            return [self.walk(v, key) for v in x]
        if isinstance(x, str):
            return self.string(x, key)
        if isinstance(x, bool) or x is None:
            return x
        if isinstance(x, int):
            if key == "chat_id":
                return 123456789
            if AWG_INT_KEYS.match(key or "") or (secret_key(key) and x > 99):
                self.orig.add(str(x))
                v = int(self.fake(str(x)).lstrip("0") or "1") if x > 9 else (x % 7) + 1
                return awg_h_num(v, int(key[1])) if re.match(r"^[Hh][1-4]$", key or "") else v
        return x

    def leaks(self, text):
        """Исходные заменённые строки (≥ 4 знаков), оставшиеся в выводе, и метки исходных доменов узла отдельным словом
        («mynode7», «realnode»: метки доменов узла (OWN_KEYS) и метки с цифрами; слова вроде «youtube», «meta» в названиях списков — не утечка)."""
        labels = {lab for d in self.dommap for lab in d.split(".")[:-1]
                  if len(lab) >= 4 and lab != "www" and (d in self.own or re.search(r"\d", lab))}
        skip = set(self.ipmap.values()) | set(self.dommap.values()) | {"admin@example.com", "123456789", "Example CA", "2030-01-01T00:00:00Z"}   # подстановки (повторная чистка сцены)
        out = [o for o in self.orig if len(o) >= 4 and o not in skip and not o.isdigit() and o in text]
        out += [o for o in self.orig if o.isdigit() and len(o) >= 6 and re.search(r"(?<!\d)%s(?!\d)" % o, text)]
        out += [lab for lab in labels if re.search(r"(?i)(?<![^\W_])%s(?![^\W_])" % re.escape(lab), text)]
        return out


HEAD = ("/* Kaskad — сцена заглушки панели «%s» (?mock=1&scene=%s): ответы GET живого узла (стенд T-3373, Kaskad 3.3.7,\n"
        "   4 устройства, 6 протоколов, 1 выход) — для детектора вёрстки и tools/k3_layout_check.mjs. Обезличена\n"
        "   tools/scene_scrub.py: адреса — 203.0.113.x / 2001:db8::, домены — example.com, UUID и ключи — подделки, сертификат —\n"
        "   условный; tests/test_ui2_layout.py проверяет её тем же audit(). Подключает dev-mock-scene.js. Время в данных\n"
        "   сдвигается к «сейчас» при загрузке (at — момент записи). */\n")


def make(raw_path, name, out):
    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)
    if not re.fullmatch(r"[a-z0-9]{1,32}", name):
        sys.exit("имя сцены — [a-z0-9]{1,32}")
    docs = {k: json.loads(v) for k, v in raw["get"].items() if k != "/api/events"}
    sc = Scrub()
    clean = {k: sc.walk(v) for k, v in docs.items()}
    live = sc.walk(raw["live"])
    scene = {"at": int(os.path.getmtime(raw_path)), "get": clean, "live": live}
    body = json.dumps(scene, ensure_ascii=False, sort_keys=True, indent=0, separators=(",", ":"))
    js = HEAD % (name, name) + "(window.KASKAD_MOCK_SCENES = window.KASKAD_MOCK_SCENES || {})[%s] = %s;\n" % (json.dumps(name), body)
    left, bad = sc.leaks(js), audit(scene)
    if left or bad:
        print("УТЕЧКА: исходных строк осталось %d; нарушений правил %d: %s" % (len(left), len(bad), sorted(set(bad))[:10]))
        return 1
    with open(out, "w", encoding="utf-8") as f:
        f.write(js)
    print("сцена %s: %d ответов; замен: адресов %d, доменов %d, UUID %d, прочих строк %d; %d байт" % (
        name, len(clean), len(sc.ipmap), len(sc.dommap), len(sc.uuidmap), len(sc.orig), len(js.encode())))
    return 0


def head_audit(head):
    """Шапка файла сцены: те же правила, что audit(), — IPv4, IPv6, UUID и домены (имена файлов .js/.mjs/.py — не домены)."""
    bad = [("адрес в шапке", None) for m in IP4_RE.finditer(head) if v4_kind(m.group(0)) == "pub"]
    bad += [("адрес IPv6 в шапке", None) for a, b in v6_spans(head) if v6_kind(head[a:b]) == "pub"]
    bad += [("UUID в шапке", None) for m in UUID_RE.finditer(head) if not FAKE_UUID_RE.match(m.group(0))]
    bad += [("домен в шапке", None) for m in DOM_RE.finditer(head)
            if not domain_ok(m.group(0)) and not not_domain(m.group(0)) and m.group(0).rsplit(".", 1)[-1] not in ("js", "mjs", "py")]
    return bad


def check(paths):
    rc = 0
    for p in paths:
        _, scene, src = load_scene(p)
        bad = audit(scene)
        bad += head_audit(src.split("(window.KASKAD_MOCK_SCENES", 1)[0])
        print("%s: %s" % (os.path.basename(p), "чисто" if not bad else "нарушений %d: %s" % (len(bad), sorted(set(bad))[:10])))
        rc |= bool(bad)
    return rc


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "make":
        sys.exit(make(sys.argv[2], sys.argv[3], sys.argv[4]))
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        sys.exit(check(sys.argv[2:]))
    sys.exit(__doc__)
