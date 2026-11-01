#!/usr/bin/env python3
"""Пик памяти процесса панели при работе с каталогом шаблонов сайта (находка №3 T-M6-qs330: OOM панели, MemoryMax=200M).

    python3 tools/tpl_index_mem.py                    синтетический каталог масштаба настоящего (33 467 шаблонов, ~14 МБ)
    python3 tools/tpl_index_mem.py --catalog FILE     настоящий каталог (json или json.gz)
    python3 tools/tpl_index_mem.py --json             итог одной строкой JSON (для тестов)

Каждый замер — в отдельном подпроцессе: KASKAD_ROOT во временной папке, каталог на месте, затем то, что делает панель
при «Выбрать вручную»: categories → страницы категории (1–3) → «Все категории» → поиск по слову → get / by_short_key /
pick_random. Сборка индекса (если она есть в этой версии кода) — до замера в своём процессе, как на узле
(``kaskadctl site templates-index`` в systemd-run --scope), — её пик меряется отдельно.

Пики: ``rss`` — ru_maxrss процесса (сколько видит cgroup панели), ``py`` — tracemalloc (только объекты Python).
Офлайн, без root и сети.
"""

import argparse
import gzip
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

CATS = [("business", "Бизнес и корпоративные", "briefcase"), ("ecommerce", "Магазины", "cart"),
        ("medical", "Медицина", "heart"), ("education", "Образование", "book"), ("portfolio", "Портфолио", "user"),
        ("realestate", "Недвижимость", "home"), ("restaurant", "Рестораны и кафе", "utensils"),
        ("landing", "Лендинги", "rocket"), ("technology", "IT и стартапы", "chip"), ("blog", "Блоги", "pen"),
        ("travel", "Путешествия", "plane"), ("beauty", "Красота", "star"), ("automotive", "Авто", "car"),
        ("entertainment", "Развлечения", "music"), ("nonprofit", "Некоммерческие", "hand"),
        ("photography", "Фото", "camera"), ("legal", "Юристы", "scale"), ("other", "Разное", "dots")]
WORDS = ("корпоративный сайт с минималистичным дизайном адаптивная вёрстка одностраничный лендинг для кафе ресторана "
         "агентства фотографа студии стартапа магазина портфолио блога события конференции свадьбы клиники").split()


def synth_catalog(n=33467, seed=7):
    """Bytes of a catalog of goAmnezia's shape: ``n`` templates in 18 categories, ~14 MB as the real one."""
    rnd = random.Random(seed)
    cats = [{"id": c, "name": t, "icon": i, "templates": []} for c, t, i in CATS]
    for k in range(n):
        user = "user%05d" % rnd.randrange(30000)
        repo = "site-%s-%d" % (rnd.choice(WORDS), k)
        cats[(k * 7 + rnd.randrange(3)) % len(cats)]["templates"].append({
            "id": "gh_%s_%s" % (user, repo.replace("-", "_")),
            "name": " ".join(w.capitalize() for w in rnd.sample(WORDS, 2)),
            "source": "github",
            "description": " ".join(rnd.sample(WORDS, 3)).capitalize(),
            "repo_url": "https://github.com/%s/%s" % (user, repo),
            "sparse_path": "",
            "preview_url": "https://%s.github.io/%s/" % (user, repo),
            "license": rnd.choice(("MIT", "CC-BY-3.0", "Apache-2.0", "")),
        })
    return json.dumps({"categories": cats}, ensure_ascii=False, indent=2).encode("utf-8")


_CHILD = r"""
import json, os, resource, sys, time, tracemalloc
sys.path[:0] = [os.environ["K_LIB"]]
from kaskad.site import templates as T
T.CATALOG_SHA256 = os.environ["K_SHA"]
def rss():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r if sys.platform == "darwin" else r * 1024
mode = os.environ["K_MODE"]
if mode == "build":                       # the separate process of the node: kaskadctl site templates-index
    base = rss(); tracemalloc.start(); t0 = time.time()
    T.build_index()
    print(json.dumps({"rss": rss(), "rss_base": base, "py": tracemalloc.get_traced_memory()[1], "s": time.time() - t0}))
    sys.exit(0)
if hasattr(T, "BUILD_APART"):
    T.BUILD_APART = False                 # the index is built before; a stale one would be built in this process
base = rss(); tracemalloc.start(); t0 = time.time()
cat = T.catalog()
first = cat["categories"][0]["id"]
for p in (1, 2, 3):
    T.search(first, None, p)
T.search(None, None, 1)
T.search(None, None, 2)
T.search(None, "ресторан", 1)
T.search(first, "кафе", 2)
it = T.search(None, None, 1)["items"][0]
T.get(it["id"]); T.by_short_key(T.short_key(it["id"]))
T.pick_random()
print(json.dumps({"rss": rss(), "rss_base": base, "py": tracemalloc.get_traced_memory()[1], "s": time.time() - t0,
                  "total": cat["total"]}))
"""


def _child(root, sha, mode, lib):
    env = dict(os.environ, KASKAD_ROOT=root, KASKAD_ASSETS_ROOTS="", K_SHA=sha, K_MODE=mode, K_LIB=lib)
    env.pop("INVOCATION_ID", None)
    p = subprocess.run([sys.executable, "-c", _CHILD], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=600)
    if p.returncode != 0:
        raise SystemExit("замер %s не прошёл:\n%s" % (mode, p.stderr[-2000:]))
    return json.loads(p.stdout.strip().splitlines()[-1])


def measure(raw, lib=None):
    import hashlib
    lib = lib or os.path.join(REPO, "lib")
    sha = hashlib.sha256(raw).hexdigest()
    root = tempfile.mkdtemp(prefix="kaskad-tplmem-")
    try:
        assets = os.path.join(root, "opt", "kaskad", "assets")
        os.makedirs(assets)
        with open(os.path.join(assets, "templates_catalog.json"), "wb") as f:
            f.write(raw)
        sys.path[:0] = [lib]
        out = {"catalog_bytes": len(raw)}
        has_index = "def build_index" in open(os.path.join(lib, "kaskad", "site", "templates.py"), encoding="utf-8").read()
        if has_index:
            out["build"] = _child(root, sha, "build", lib)
        out["panel"] = _child(root, sha, "panel", lib)
        return out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", help="каталог (json или json.gz); без него — синтетический")
    ap.add_argument("--lib", help="папка lib/ проверяемой версии кода (по умолчанию — эта рабочая копия)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.catalog:
        with open(a.catalog, "rb") as f:
            raw = f.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    else:
        raw = synth_catalog()
    out = measure(raw, a.lib)
    if a.json:
        print(json.dumps(out))
        return 0
    mb = lambda v: "%.1f МБ" % (v / 1048576.0)        # noqa: E731
    print("Каталог: %s" % mb(out["catalog_bytes"]))
    for k, title in (("build", "Сборка индекса (отдельный процесс)"), ("panel", "Процесс панели (каталог, листание, поиск)")):
        if k in out:
            r = out[k]
            print("%s: пик RSS %s (база %s, прирост %s), пик Python %s, %.2f с" % (
                title, mb(r["rss"]), mb(r["rss_base"]), mb(r["rss"] - r["rss_base"]), mb(r["py"]), r["s"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
