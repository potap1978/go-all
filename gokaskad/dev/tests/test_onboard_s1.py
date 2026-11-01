"""Stage 1, T11 (SPEC-stage1-exits §2.9): connecting the bot through its proxy when bot-bal goes round the keys of its
group (no probes) — a network error is tried again with a new connection, min(the keys of the group, 5) times, so a dead
key does not stop the setup; the first token rebuilds the bot's proxy (its own probes with the gateway off)."""

from kaskad import i18n
from kaskad.bot import onboard, telegram
from tests.test_333d_backend import TOKEN, BotCase, FakeTg


class Flaky(FakeTg):
    """getMe fails through the first ``dead`` connections (dead keys of the group), then answers."""
    dead = 0

    def get_me(self):
        if len(FakeTg.made) <= Flaky.dead:
            raise telegram.NetworkError("getMe", "timed out")
        return FakeTg.me


class RetryTest(BotCase):
    def setUp(self):
        super().setUp()
        self.config('BOT_PROXY="socks5://127.0.0.1:10808"\n')
        from unittest import mock
        p = mock.patch.object(telegram, "Api", Flaky)
        p.start()
        self.addCleanup(p.stop)

    def pool(self, n, idx=None):
        self.write("/etc/kaskad/egress/upstream.txt", "".join("trojan://p%d@t.example.com:443#k\n" % i for i in range(n)))
        if idx is not None:
            self.write("/etc/kaskad/egress/.tg_indexes", idx + "\n")

    def test_a_dead_key_does_not_stop_the_setup(self):
        self.pool(3)
        Flaky.dead = 2
        self.assertEqual(onboard.check_token(TOKEN)["username"], "kaskad_test_bot")
        self.assertEqual(len(FakeTg.made), 3)                     # a new connection each time

    def test_tries_are_the_keys_of_the_group_at_most_5(self):
        self.pool(9, "2 4")
        self.assertEqual(onboard.proxy_tries(), 2)
        self.pool(9)
        self.write("/etc/kaskad/egress/.tg_indexes", "1 2 3 4 5 6 7 8\n")
        self.assertEqual(onboard.proxy_tries(), 5)
        Flaky.dead = 10
        with i18n.use_lang("en"):
            reason, text = self.reason(onboard.check_token, TOKEN)
        self.assertEqual(reason, "net_proxy")
        self.assertEqual(len(FakeTg.made), 5)

    def test_no_proxy_one_try(self):
        self.config('BOT_PROXY=""\n')
        self.pool(3)
        Flaky.dead = 1
        self.assertEqual(self.reason(onboard.check_token, TOKEN)[0], "net")
        self.assertEqual(len(FakeTg.made), 1)

    def test_first_token_rebuilds_the_bots_proxy(self):
        self.pool(2)
        Flaky.dead = 0
        calls = []
        self.runner.on(["systemd-run"], lambda argv, inp: (calls.append(argv) or 0, "", ""))
        onboard.apply(self.runner, TOKEN, "555", test=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], "--egress-tg-rebuild")
        onboard.apply(self.runner, TOKEN, "555", test=False)    # the token was there: nothing
        self.assertEqual(len(calls), 1)
