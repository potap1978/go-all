"""SSE of the panel (WP-P1, spec §3.3): the first live event carries everything, then merge patches; state after a
change; alerts from live.json; heartbeat; at most MAX_STREAMS; the beacon live.want."""

import http.client
import json
import time
from unittest import mock

from kaskad import paths, util
from kaskad.gw import collect, live
from kaskad.panel import sse
from tests.panel.panelcase import PanelCase


class SseClient(object):
    def __init__(self, port, tunnel=True, cookie=None):
        self.c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.c.putrequest("GET", "/api/events", skip_host=True)
        self.c.putheader("Host", "127.0.0.1:%d" % port)
        if not tunnel:
            self.c.putheader("X-Forwarded-For", "198.51.100.9")
        if cookie:
            self.c.putheader("Cookie", "kaskad_session=%s" % cookie)
        self.c.endheaders()
        self.sock = self.c.sock                     # http.client lets go of it (Connection: close)
        self.r = self.c.getresponse()
        self.status = self.r.status
        self.headers = dict((k.lower(), v) for k, v in self.r.getheaders())

    def next(self, timeout=5.0):
        """("live"|"state"|"alert", data) or ("comment", text)."""
        self.sock.settimeout(timeout)
        name, data = None, []
        while True:
            line = self.r.fp.readline()
            if not line:
                raise EOFError("stream closed")
            line = line.decode("utf-8").rstrip("\n")
            if line == "":
                if name or data:
                    return name or "message", json.loads("\n".join(data))
                continue
            if line.startswith(":"):
                return "comment", line[1:].strip()
            k, _, v = line.partition(":")
            v = v[1:] if v.startswith(" ") else v
            if k == "event":
                name = v
            elif k == "data":
                data.append(v)

    def until(self, kind, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            got = self.next(max(0.05, end - time.time()))
            if got[0] == kind:
                return got[1]
        raise AssertionError("no %s event" % kind)

    def close(self):
        self.r.close()
        self.c.close()
        try:
            self.sock.close()
        except OSError:
            pass


def write_live(doc):
    util.atomic_write(paths.live_json(), json.dumps(doc), 0o600)


class MergePatchTest(PanelCase):
    def test_merge_patch(self):
        old = {"a": 1, "b": {"x": 1, "y": [1, 2]}, "c": None, "d": {"k": 1}}
        new = {"a": 1, "b": {"x": 2, "y": [1, 2, 3]}, "c": None, "e": True}
        self.assertEqual(sse.merge_patch(old, new), {"b": {"x": 2, "y": [1, 2, 3]}, "d": None, "e": True})
        self.assertEqual(sse.merge_patch(new, new), {})
        self.assertEqual(sse.merge_patch({"a": 1}, {"a": 1.0}), {"a": 1.0})
        self.assertEqual(sse.merge_patch({"a": {"b": 1}}, {"a": {}}), {"a": {"b": None}})


class StreamTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        self.collect()

    def test_full_then_patches_then_heartbeat(self):
        s = SseClient(self.port)
        self.addCleanup(s.close)
        self.assertEqual(s.status, 200)
        self.assertEqual(s.headers["content-type"], "text/event-stream; charset=utf-8")
        self.assertEqual((s.headers["x-accel-buffering"], s.headers["cache-control"]), ("no", "no-store"))
        kind, first = s.next()
        self.assertEqual(kind, "live")
        self.assertIs(first["full"], True)
        self.assertIn("wg", first["inbounds"])
        self.assertNotIn("events", first)
        self.assertNotIn("seq", first)
        doc = json.loads(self.read("/run/kaskad/live.json"))
        doc["host"]["cpu"] = 42.5
        doc["ts"] = doc["ts"] + 2
        doc["seq"] += 1
        write_live(doc)
        patch = s.until("live")
        self.assertEqual(patch, {"host": {"cpu": 42.5}, "ts": doc["ts"]})
        self.assertEqual(s.next(timeout=3), ("comment", "hb"))                 # silence: a heartbeat

    def test_state_after_a_change_and_alerts(self):
        s = SseClient(self.port)
        self.addCleanup(s.close)
        s.until("live")
        self.ok("POST", "/api/gw/users", {"name": "Дача"})
        st = s.until("state")
        self.assertIn("state", st["parts"])
        self.assertIn("panel", st["parts"])
        doc = json.loads(self.read("/run/kaskad/live.json"))
        ev = {"id": 1791190000123, "ts": 1791190000, "kind": "exit_dead", "text": "Выход «Вена» не работает",
              "exit": self.vid}
        doc["events"] = list(doc.get("events") or []) + [ev]
        write_live(doc)
        self.assertEqual(s.until("alert"), ev)

    def test_alert_by_its_key_in_the_language_of_the_server(self):
        # живая проверка 3.3.3, C: событие, записанное по-русски до смены языка, в файле с lang=en — тост панели на EN
        from tests.serverlang import server
        with server("en"):
            s = SseClient(self.port)
            self.addCleanup(s.close)
            s.until("live")
            doc = json.loads(self.read("/run/kaskad/live.json"))
            ev = {"id": 1791190000124, "ts": 1791190000, "kind": "inbound_down", "key": "gw_live.inbound_down",
                  "args": {"title": "L2TP/IPsec"}, "text": "Протокол L2TP/IPsec не работает", "inbound": "l2tp"}
            doc["events"] = list(doc.get("events") or []) + [ev]
            doc["i18n"] = dict(doc.get("i18n") or {}, lang="en")
            doc["ts"] = doc["ts"] + 2
            write_live(doc)
            self.assertEqual(s.until("alert"), dict(ev, text="Protocol L2TP/IPsec is down"))
            got = self.ok("GET", "/api/overview")                      # the list «Overview» reads (header: none)
            self.assertIn("Protocol L2TP/IPsec is down", [e.get("text") for e in got["events"]])

    def test_auth_limit_and_the_beacon(self):
        nobody = SseClient(self.port, tunnel=False)
        self.assertEqual(nobody.status, 401)
        nobody.close()
        self.assertFalse(live.want_fresh())
        with mock.patch.object(sse, "MAX_STREAMS", 2):
            a, b = SseClient(self.port), SseClient(self.port)
            a.until("live")
            b.until("live")
            self.assertTrue(live.want_fresh())                                  # the collector goes to 2 s rounds
            c = SseClient(self.port)
            self.assertEqual(c.status, 503)
            self.assertIn("вкладок", json.loads(c.r.read().decode("utf-8"))["error"])
            c.close()
            a.close()
            d = self.wait(lambda: (lambda x: x if x.status == 200 else x.close())(SseClient(self.port)), timeout=5)
            self.assertIsNotNone(d)                                            # the slot came back
            d.close()
            b.close()

    def test_an_old_snapshot_is_no_data(self):
        """C2: live.json older than live.STALE (the collector is stopped or hangs) is unknown — not «running, 0
        sessions»: the stream says {"full": true} without data, also when the file goes stale while it is open, and
        a full picture again once the collector writes; events already in the file are never alerts."""
        doc = json.loads(self.read("/run/kaskad/live.json"))
        at = int(time.time()) - 600
        old = {"id": at * 1000, "ts": at, "kind": "exit_dead", "text": "Выход «Вена» не работает", "exit": self.vid}
        doc["events"] = [old]
        doc["ts"] = time.time() - 3600
        write_live(doc)
        s = SseClient(self.port)
        self.addCleanup(s.close)
        self.assertEqual(s.next(), ("live", {"full": True}))
        with mock.patch.object(collect, "LIVE", live.Live()):            # the collector starts again: keeps the event
            self.collect()
        self.assertEqual(json.loads(self.read("/run/kaskad/live.json"))["events"], [old])
        seen = []

        def until_live():
            while True:
                kind, data = s.next(timeout=5)
                seen.append(kind)
                if kind == "live":
                    return data
        full = until_live()
        self.assertIs(full["full"], True)
        self.assertIs(full["inbounds"]["wg"]["running"], True)
        with mock.patch.object(live, "STALE", 0.3):                      # and stops: the file ages without a change
            self.assertEqual(until_live(), {"full": True})
            end = time.time() + 1.0
            while time.time() < end:
                seen.append(s.next(timeout=2)[0])
        self.assertNotIn("alert", seen)                                  # the old event was never news

    def test_without_live_json_the_first_event_is_empty(self):
        import os
        os.unlink(paths.live_json())
        s = SseClient(self.port)
        self.addCleanup(s.close)
        self.assertEqual(s.next(), ("live", {"full": True}))
        self.collect()
        patch = s.until("live")
        self.assertIs(patch["full"], True)
        self.assertIn("inbounds", patch)
