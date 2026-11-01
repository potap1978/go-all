#!/usr/bin/env python3
"""Память процесса веб-панели в покое и под нагрузкой — обоснование MemoryMax юнита (T-M6-qs330b №3; офлайн, без root).

    python3 tools/panel_mem.py        одна строка JSON, МБ: *_now — RSS сейчас, *_peak — пик (ru_maxrss)

Настоящий HTTP-сервер панели в этом процессе поверх поддельного узла тестов (tests/panel/panelcase.py): шлюз,
40 человек × 3 устройства, настоящий каталог шаблонов (33 467) с индексом (собран отдельным процессом, как на узле).
«idle_full» — открыты все страницы; «load» — 4 вкладки опрашивают страницы и ищут шаблоны, тем временем 15 раз
добавляется и удаляется устройство. Тяжёлые дочерние процессы на узле идут вне cgroup панели (kaskad.sysrun.heavy).
"""
import urllib.parse, gzip, hashlib, json, os, resource, subprocess, sys, threading, time, unittest

REPO = os.environ.get("K_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(REPO, "lib"), REPO]

def rss_now():
    return int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], stdout=subprocess.PIPE,
                              universal_newlines=True).stdout.strip()) * 1024
def rss_peak():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r if sys.platform == "darwin" else r * 1024
MB = lambda v: round(v / 1048576.0, 1)
OUT = {"t0_now": MB(rss_now())}

from tests.panel.panelcase import PanelCase   # noqa: E402
from kaskad.gw import ops                       # noqa: E402
from kaskad.site import templates as T          # noqa: E402
from kaskad import paths                        # noqa: E402

class M(PanelCase):
    def test_measure(self):
        OUT["panel_empty_now"] = MB(rss_now())
        self.gateway(inbounds=("wg",))
        for i in range(40):
            uid = ops.user_add(self.w.runner, "Человек %d" % i)["id"]
            for j in range(3):
                ops.device_add(self.w.runner, uid, "dev-%d-%d" % (i, j), access=["wg"])
        self.collect()
        # the real catalog + its index (built apart, as on the node)
        raw = gzip.decompress(open(os.path.join(REPO, "assets", "templates_catalog.json.gz"), "rb").read())
        os.makedirs(paths.assets_dir(), exist_ok=True)
        with open(os.path.join(paths.assets_dir(), "templates_catalog.json"), "wb") as f:
            f.write(raw)
        sha = hashlib.sha256(raw).hexdigest()
        T.CATALOG_SHA256 = sha
        env = dict(os.environ); env.pop("INVOCATION_ID", None)
        subprocess.run([sys.executable, "-c", "import sys; sys.path[:0]=[%r]; from kaskad.site import templates as T; "
                        "T.CATALOG_SHA256=%r; T.build_index()" % (os.path.join(REPO, "lib"), sha)], env=env, check=True)
        if hasattr(T, "BUILD_APART"):
            T.BUILD_APART = False
        pages = ["/api/overview", "/api/gw", "/api/gw/inbounds", "/api/gw/people", "/api/gw/exits", "/api/scheme",
                 "/api/forwards", "/api/health", "/api/appearance", "/api/panel", "/api/site", "/api/site/categories",
                 "/api/backup/summary"]
        for p in pages:
            self.call("GET", p)
        cats = self.call("GET", "/api/site/categories").json()
        cat_ids = [c["id"] for c in (cats.get("categories") or [])][:6]
        for p in pages:
            self.call("GET", p)
        time.sleep(1)
        OUT["idle_full_now"] = MB(rss_now()); OUT["idle_full_peak"] = MB(rss_peak())
        stop = threading.Event(); errs = []
        def tab(k):
            n = 0
            while not stop.is_set():
                try:
                    for p in pages:
                        self.call("GET", p)
                    c = cat_ids[n % len(cat_ids)] if cat_ids else ""
                    self.call("GET", "/api/site/templates?category=%s&page=%d" % (c, 1 + n % 5))
                    self.call("GET", "/api/site/templates?query=%s&page=1" % urllib.parse.quote("ресторан" if n % 2 else "shop"))
                except Exception as e:      # noqa
                    errs.append(str(e))
                n += 1
        ts = [threading.Thread(target=tab, args=(k,), daemon=True) for k in range(4)]
        for t in ts: t.start()
        uid = self.uid
        for i in range(15):                  # changes meanwhile (device add / delete through the API)
            d = self.ok("POST", "/api/gw/devices", {"user": uid, "name": "load-%d" % i, "kind": "phone",
                                                    "platform": "android", "access": ["wg"]})["device"]
            self.ok("DELETE", "/api/gw/devices/%s" % d["id"])
            OUT.setdefault("load_now_samples", []).append(MB(rss_now()))
        time.sleep(20)
        stop.set()
        for t in ts: t.join()
        OUT["load_peak"] = MB(rss_peak()); OUT["after_load_now"] = MB(rss_now()); OUT["errors"] = len(errs); OUT["err_sample"] = list(set(errs))[:3]

if __name__ == "__main__":
    r = unittest.TextTestRunner(stream=open(os.devnull, "w")).run(unittest.defaultTestLoader.loadTestsFromTestCase(M))
    OUT["ok"] = r.wasSuccessful()
    if not r.wasSuccessful():
        OUT["fail"] = [str(x[1])[-1500:] for x in r.errors + r.failures]
    s = OUT.get("load_now_samples") or []
    OUT["load_now_max"] = max(s) if s else None
    OUT.pop("load_now_samples", None)
    print(json.dumps(OUT, ensure_ascii=False))
