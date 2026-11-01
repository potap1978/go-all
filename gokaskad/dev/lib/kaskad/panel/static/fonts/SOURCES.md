# Шрифты панели — откуда и как собраны

Все три — SIL Open Font License 1.1, лицензии лежат рядом (`OFL-*.txt`). Файлы едут в `install.sh` (K3_PAYLOAD),
отдаются сервером панели по `/fonts/<имя>.woff2` (CSP `default-src 'self'`, без CDN). Этот файл в доставку не входит.

| Файл | Шрифт | Веса (ось wght) | Размер | Источник |
|---|---|---|---|---|
| `onest.woff2` | Onest | 400–700 | 30 568 Б | `ofl/onest/Onest[wght].ttf` |
| `rubik.woff2` | Rubik | 500–700 | 40 056 Б | `ofl/rubik/Rubik[wght].ttf` |
| `jbmono.woff2` | JetBrains Mono | 400–600 | 18 304 Б | `ofl/jetbrainsmono/JetBrainsMono[wght].ttf` |

Скачано 2026-10-09 из репозитория Google Fonts: `https://raw.githubusercontent.com/google/fonts/main/ofl/…`
(ветка main, последний коммит каталога onest — `4c1db3a`, 2026-08-06). SHA-256 исходников:

```
966c5c29b4755da84b6854d5c21dd4eaa2420225d0e9874de602de176d4a9f31  Onest[wght].ttf
1b3a7437ba2af80e465e773ed60c5036d1ba6ace492d89046dbcf18fb31e4e88  Rubik[wght].ttf
48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda  JetBrainsMono[wght].ttf
```

## Как собраны

fontTools 4.66 (`pip install fonttools brotli`): подмножество символов → сужение оси `wght` → woff2, без хинтинга.

Символы: латиница U+0020–007E, U+00A0–00FF, U+0131, U+0152–0153, U+02C6, U+02DA, U+02DC; кириллица U+0400–045F,
U+0490–0491; знаки U+2010–2027 (тире, кавычки, «…», «•»), U+2030, U+2039–203A, U+2116 «№», U+20AC, U+20BD «₽»,
U+2122, стрелки U+2190–2193, U+21C4, U+2212 «−», U+2248 «≈», U+2260, U+2264–2265, U+2715, U+25CF.
Набор взят по всем символам, которые встречаются в текстах панели (`*.js`, `*.html`). Чего нет в шрифте (у Rubik —
«→»), браузер берёт из следующего шрифта цепочки (`--font-head: "Rubik", var(--font)`).

Возможности OpenType: у Onest и Rubik — kern, liga, calt, tnum, lnum, case; у JetBrains Mono — только kern
(лигатуры кода не нужны: в адресах и ключах `->`, `!=` должны остаться как есть).

```python
from fontTools.ttLib import TTFont
from fontTools import subset
from fontTools.varLib import instancer
f = TTFont("Onest[wght].ttf")
o = subset.Options(); o.flavor = "woff2"; o.layout_features = ["kern", "liga", "calt", "tnum", "lnum", "case"]
o.hinting = False; o.desubroutinize = True; o.notdef_outline = True
s = subset.Subsetter(o); s.populate(unicodes=subset.parse_unicodes(UNI)); s.subset(f)
f = instancer.instantiateVariableFont(f, {"wght": (400, 700)})
f.flavor = "woff2"; f.save("onest.woff2")
```

Нужен новый символ — добавьте его в `UNI` и пересоберите все три файла тем же способом; обновите таблицу размеров.
