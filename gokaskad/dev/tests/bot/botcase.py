"""Test base of the bot: a temporary KASKAD_ROOT, a fake Telegram, /etc/kaskad/config of a bound bot and an App
wired to them — the clock and sleeps are the test's, pings and TCP connects are fakes, GeoIP is the fake's
``/json/<ip>``. ``say``/``press`` queue an update and run one receiver step (poll + the lanes + jobs)."""

import os

from kaskad.bot import app as bapp
from kaskad.bot import probe
from kaskad.sysrun import FakeRunner
from tests.bot.fake_tg import OWNER, TOKEN, FakeTelegram
from tests.helpers import RootCase

T0 = 1791190000.0


class BotMixin(object):
    MENU = "inline"

    def bot_setup(self):
        self.now = T0
        self.tg = FakeTelegram().start()
        self.addCleanup(self.tg.stop)
        self.sleeps = []
        self.pings = {}                 # ip -> ms; absent -> no answer
        self.tcp = {}                   # (ip, port) -> ms; absent -> refused
        self._ssh = os.environ.pop("SSH_CONNECTION", None)
        self.addCleanup(self._restore_ssh)
        self.write_config()
        self.app = None

    def _restore_ssh(self):
        if self._ssh is not None:
            os.environ["SSH_CONNECTION"] = self._ssh

    def write_config(self, **kw):
        vals = {"BOT_TOKEN": TOKEN, "BOT_CHAT_ID": str(OWNER), "MENU_STYLE": self.MENU, "BOT_PROXY": "",
                "GITHUB_PAT": ""}
        vals.update(kw)
        self.write("/etc/kaskad/config", "".join('%s="%s"\n' % kv for kv in vals.items()))

    def ping_handler(self, argv, inp):
        ip = argv[-1]
        ms = self.pings.get(ip)
        if ms is None:
            return 1, "PING %s 56(84) bytes of data.\n\n--- %s ping statistics ---\n1 packets transmitted, " \
                      "0 received, 100%% packet loss, time 0ms\n" % (ip, ip), ""
        return 0, "64 bytes from %s: icmp_seq=1 ttl=57 time=%s ms\n" % (ip, ms), ""

    def tcp_connect(self, ip, port, timeout):
        ms = self.tcp.get((ip, port))
        if ms is None:
            raise OSError("Connection refused")
        return ms

    def make_app(self, runner=None, monitor=False, **kw):
        runner = runner if runner is not None else self.runner
        prober = probe.Prober(runner, tcp_connect=self.tcp_connect,
                              geoip_url=self.tg.base + "/json/%s?fields=status,country,city,isp,org",
                              sleep=lambda s: None)
        args = dict(runner=runner, clock=lambda: self.now, sleep=self.sleeps.append, api_base=self.tg.base,
                    my_ip="203.0.113.10", poll_timeout=1, prober=prober, monitor=False)
        args.update(kw)
        app = bapp.App(**args)
        app.scheduler.start()
        if monitor:
            from kaskad.bot.monitor import Monitor
            app.monitor = Monitor(app, sync=True)
        app.last_poll = self.now - 5            # a bot that has been running: presses are fresh
        self.app = app
        self.addCleanup(self._stop_app, app)
        return app

    @staticmethod
    def _stop_app(app):
        app.stop_event.set()
        app.scheduler.stop(2)

    # ── driving ──
    def poll(self):
        n = self.app.poll_once()
        self.assertTrue(self.app.scheduler.wait_idle(15), "the bot did not finish its work")
        return n

    def say(self, text, chat=OWNER, date=None):
        self.tg.message(chat, text, date=self.now if date is None else date)
        return self.poll()

    def press(self, data, chat=OWNER, message_id=77):
        self.tg.press(chat, data, message_id)
        return self.poll()

    def last(self, chat=OWNER):
        return self.tg.last(chat)

    def last_text(self, chat=OWNER):
        c = self.tg.last(chat)
        return c.text if c is not None else ""

    def buttons(self, chat=OWNER):
        """Buttons of the last message (or edit) that had an inline keyboard."""
        for c in reversed(self.tg.sent("sendMessage", "editMessageText", chat=chat)):
            b = c.buttons()
            if b:
                return b
        return []

    def data_of(self, prefix, chat=OWNER):
        return [d for _, d in self.buttons(chat) if d and d.startswith(prefix)]


class BotCase(BotMixin, RootCase):
    def setUp(self):
        super(BotCase, self).setUp()
        self.runner = self.make_runner()
        self.bot_setup()

    def make_runner(self):
        r = FakeRunner()
        r.on(["ping"], self.ping_handler)
        return r

    def tearDown(self):
        if getattr(self.runner, "unmatched", None):
            self.fail("unmatched commands: %r" % self.runner.unmatched)
        super(BotCase, self).tearDown()
