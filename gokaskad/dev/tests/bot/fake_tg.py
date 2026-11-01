"""A fake Telegram Bot API on 127.0.0.1 for the bot's tests (plain HTTP, or HTTPS with a test certificate).

Records every call (method, params, uploaded files), serves queued updates to getUpdates (long polling waits
at most ``max_wait`` s), answers like Telegram (message ids, ``ok``), and fails on request: ``fail(method,
status, description, retry_after, times, when)``; ``delay[method] = s`` makes it answer late (timeouts).
Also ``GET /json/<ip>`` — a fake ip-api.com (``geo[ip]``, else «fail»).
"""

import email.parser
import email.policy
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

TOKEN = "123456789:TEST-fake-token-of-kaskad-tests-000"
OWNER = 555000111


class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Call(object):
    def __init__(self, method, params, files):
        self.method = method
        self.params = params
        self.files = files
        self.at = time.time()
        self.ok = None                  # True / False once answered (False: the fake answered an error)

    @property
    def chat(self):
        c = self.params.get("chat_id")
        try:
            return int(c)
        except (TypeError, ValueError):
            return c

    @property
    def text(self):
        return self.params.get("text") or self.params.get("caption") or ""

    def markup(self):
        m = self.params.get("reply_markup")
        if isinstance(m, str):
            m = json.loads(m)
        return m or {}

    def buttons(self):
        """[(text, callback_data)] of an inline keyboard, row by row flattened."""
        return [(b.get("text"), b.get("callback_data")) for row in self.markup().get("inline_keyboard") or []
                for b in row]

    def __repr__(self):
        return "Call(%s %r)" % (self.method, self.text[:60])


def parse_multipart(body, ctype):
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + ctype.encode("ascii") + b"\r\n\r\n" + body)
    fields, files = {}, {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True)
        filename = part.get_filename()
        if filename is not None:
            files[name] = (filename, data, part.get_content_type())
        else:
            fields[name] = data.decode("utf-8")
    return fields, files


class FakeTelegram(object):
    def __init__(self, token=TOKEN, ssl_context=None, max_wait=0.3):
        self.token = token
        self.max_wait = max_wait
        self.calls = []
        self.cond = threading.Condition()
        self.updates = []
        self.next_update = 1
        self.next_message = 1000
        self.next_query = 1
        self.failures = []
        self.delay = {}
        self.geo = {}
        self.offsets = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _reply(self, status, doc):
                raw = json.dumps(doc).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                m = re.match(r"^/json/([0-9.]+)", self.path)
                if not m:
                    return self._reply(404, {})
                delay = fake.delay.get("geoip")
                if delay:
                    time.sleep(delay)
                g = fake.geo.get(m.group(1))
                self._reply(200, dict(g, status="success") if g else {"status": "fail"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                m = re.match(r"^/bot([^/]+)/(\w+)$", self.path)
                if not m:
                    return self._reply(404, {"ok": False, "error_code": 404, "description": "Not Found"})
                ctype = self.headers.get("Content-Type") or ""
                if ctype.startswith("multipart/form-data"):
                    params, files = parse_multipart(body, ctype)
                else:
                    params, files = (json.loads(body.decode("utf-8")) if body else {}), {}
                status, doc = fake.handle(m.group(1), m.group(2), params, files)
                if status is None:                       # drop the connection without an answer
                    self.close_connection = True
                    return
                self._reply(status, doc)

        self.server = _Server(("127.0.0.1", 0), Handler)
        if ssl_context is not None:
            self.server.socket = ssl_context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True

    @property
    def port(self):
        return self.server.server_address[1]

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        with self.cond:
            self.cond.notify_all()
        self.server.shutdown()
        self.server.server_close()

    # ── updates ──
    def _push(self, upd):
        with self.cond:
            upd["update_id"] = self.next_update
            self.next_update += 1
            self.updates.append(upd)
            self.cond.notify_all()
        return upd

    def message(self, chat, text, date=None, user=None):
        return self._push({"message": {"message_id": self._mid(), "date": int(date if date is not None else time.time()),
                                       "chat": {"id": chat, "type": "private"},
                                       "from": {"id": user or chat, "is_bot": False}, "text": text}})

    def press(self, chat, data, message_id=77, user=None):
        qid = "q%d" % self.next_query
        self.next_query += 1
        return self._push({"callback_query": {"id": qid, "from": {"id": user or chat, "is_bot": False},
                                              "message": {"message_id": message_id, "date": int(time.time()) - 3600,
                                                          "chat": {"id": chat, "type": "private"}},
                                              "data": data}})

    def _mid(self):
        with self.cond:
            self.next_message += 1
            return self.next_message

    # ── failures ──
    def fail(self, method, status=500, description="Internal Server Error", retry_after=None, times=1, when=None,
             drop=False):
        self.failures.append({"method": method, "status": status, "description": description,
                              "retry_after": retry_after, "times": times, "when": when, "drop": drop})

    def _failure(self, method, params):
        for f in self.failures:
            if f["method"] == method and f["times"] > 0 and (f["when"] is None or f["when"](params)):
                f["times"] -= 1
                if f["drop"]:
                    return None, None
                doc = {"ok": False, "error_code": f["status"], "description": f["description"]}
                if f["retry_after"] is not None:
                    doc["parameters"] = {"retry_after": f["retry_after"]}
                return f["status"], doc
        return False

    # ── the API ──
    def handle(self, token, method, params, files):
        if token != self.token:
            return 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
        call = Call(method, params, files)
        with self.cond:
            self.calls.append(call)
        status, doc = self._answer(method, params)
        call.ok = bool(doc and doc.get("ok"))
        return status, doc

    def _answer(self, method, params):
        failed = self._failure(method, params)
        if failed is not False:
            return failed
        if self.delay.get(method):
            time.sleep(self.delay[method])
        bad = self._check(method, params)
        if bad:
            return 400, {"ok": False, "error_code": 400, "description": "Bad Request: " + bad}
        if method == "getUpdates":
            offset = int(params.get("offset") or 0)
            timeout = float(params.get("timeout") or 0)
            with self.cond:
                self.offsets.append(offset)
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
                if not self.updates and timeout:
                    self.cond.wait(min(timeout, self.max_wait))
                res = [u for u in self.updates if u["update_id"] >= offset][:int(params.get("limit") or 100)]
            return 200, {"ok": True, "result": res}
        if method in ("sendMessage", "sendDocument", "sendPhoto"):
            return 200, {"ok": True, "result": {"message_id": self._mid(), "date": int(time.time()),
                                                "chat": {"id": params.get("chat_id")}, "text": params.get("text")}}
        if method == "editMessageText":
            return 200, {"ok": True, "result": {"message_id": params.get("message_id")}}
        if method == "getMe":
            return 200, {"ok": True, "result": {"id": 123456789, "is_bot": True, "username": "kaskad_test_bot"}}
        if method in ("answerCallbackQuery", "setMyCommands", "setChatMenuButton", "deleteMessage"):
            return 200, {"ok": True, "result": True}
        return 404, {"ok": False, "error_code": 404, "description": "Not Found: method"}

    @staticmethod
    def _check(method, params):
        """What real Telegram refuses: callback_data over 64 bytes, more than 100 buttons, texts over the
        limits, a text with unbalanced HTML tags."""
        m = params.get("reply_markup")
        if isinstance(m, str):
            m = json.loads(m)
        rows = (m or {}).get("inline_keyboard") or []
        if sum(len(r) for r in rows) > 100:
            return "too many buttons"
        for row in rows:
            for b in row:
                if len((b.get("callback_data") or "").encode("utf-8")) > 64:
                    return "BUTTON_DATA_INVALID"
                if not b.get("text"):
                    return "BUTTON_TEXT_EMPTY"
        text = params.get("text")
        if method in ("sendMessage", "editMessageText"):
            if not text:
                return "message text is empty"
            if len(text) > 4096:
                return "message is too long"
        cap = params.get("caption")
        if cap and len(cap) > 1024:
            return "message caption is too long"
        for t in (text, cap):
            if t and params.get("parse_mode") == "HTML":
                stack = []
                for close, tag in re.findall(r"<(/?)(b|i|u|s|code|pre|a)\b[^>]*>", t):
                    if close:
                        if not stack or stack.pop() != tag:
                            return "can't parse entities: unbalanced <%s>" % tag
                    else:
                        stack.append(tag)
                if stack:
                    return "can't parse entities: unclosed <%s>" % stack[-1]
                if re.search(r"<(?!/?(b|i|u|s|code|pre|a)\b)", t):
                    return "can't parse entities: unsupported start tag"
                if re.search(r"&(?!(amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)", t):
                    return "can't parse entities: unsupported entity"
        return ""

    # ── inspection ──
    def sent(self, *methods, **kw):
        """Calls Telegram accepted (``failed=True`` — also the ones the fake answered with an error)."""
        chat = kw.get("chat")
        failed = kw.get("failed", False)
        with self.cond:
            calls = list(self.calls)
        return [c for c in calls if (not methods or c.method in methods) and (chat is None or c.chat == chat)
                and (failed or c.ok is not False)]

    def texts(self, chat=None):
        return [c.text for c in self.sent("sendMessage", "editMessageText", chat=chat)]

    def last(self, chat=None):
        out = self.sent("sendMessage", "editMessageText", "sendDocument", "sendPhoto", chat=chat)
        return out[-1] if out else None

    def clear(self):
        with self.cond:
            self.calls = []
