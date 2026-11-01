"""kaskad.rules.dat (the protobuf lists of Xray) and kaskad.rules.lists (groups, ranges) on synthetic bytes."""

import io
import ipaddress
import unittest

from kaskad.rules import dat, lists
from tests.rules_helpers import (UNKNOWN, cidr, f_len, f_varint, geoip_dat, geoip_entry, geosite_dat, key,
                                 ru_nets)


def net4(n, p):
    return str(ipaddress.IPv4Network((n, p)))


class GeoIPTest(unittest.TestCase):
    def test_codes_cidrs_v6_reverse_and_unknown_fields_everywhere(self):
        raw = geoip_dat([geoip_entry("direct", ["5.8.0.0/16", "2.26.8.77/24", "2a02:6b8::/32"], unknown=True),
                         geoip_entry("WhiteList", ["77.88.8.0/24"], reverse=True)], unknown_top=True)
        g = dat.geoip(raw)
        self.assertEqual(sorted(g), ["DIRECT", "WHITELIST"])                  # codes upper-case
        d = g["DIRECT"]
        self.assertEqual([net4(n, p) for n, p in d["v4"]], ["5.8.0.0/16", "2.26.8.0/24"])   # host bits cleared
        self.assertEqual([str(ipaddress.IPv6Network((n, p))) for n, p in d["v6"]], ["2a02:6b8::/32"])
        self.assertEqual((d["bad"], d["reverse"]), (0, False))
        self.assertTrue(g["WHITELIST"]["reverse"])
        self.assertEqual(dat.codes(raw), ["DIRECT", "WHITELIST"])
        # asked by code, case-insensitively: the other entries are not decoded at all
        self.assertEqual(list(dat.geoip(raw, ["Direct"])), ["DIRECT"])

    def test_prefix_absent_or_zero_is_slash_zero_and_broken_cidrs_are_counted(self):
        no_prefix = cidr("10.0.0.0/8", with_prefix=False)                    # proto3 wrote nothing: /0
        bad_len = f_len(1, b"\x01\x02\x03")                                     # an ip of 3 bytes
        too_long = f_len(1, b"\x0a\x00\x00\x00") + f_varint(2, 33)              # /33
        raw = geoip_dat([geoip_entry("DIRECT", [], raw_cidrs=[no_prefix, bad_len, too_long])])
        d = dat.geoip(raw)["DIRECT"]
        self.assertEqual(d["v4"], [(0, 0)])
        self.assertEqual(d["bad"], 2)
        # the kernel never takes a /0 (or anything wider than /8) for «Russia»
        ranges, wide = lists.merge(d["v4"])
        self.assertEqual((ranges, wide), ([], 1))

    def test_streaming_from_a_file_object_and_merging_entries_with_one_code(self):
        raw = geoip_dat([("DIRECT", ["5.8.0.0/24"]), ("OTHER", ["1.1.1.0/24"]), ("direct", ["5.8.1.0/24"])])
        g = dat.geoip(io.BytesIO(raw))
        self.assertEqual(len(g["DIRECT"]["v4"]), 2)
        self.assertEqual(len(list(dat.entries(io.BytesIO(raw)))), 3)

    def test_broken_files(self):
        good = geoip_dat([("DIRECT", ["5.8.0.0/24"])])
        for broken in (good[:-3],                                            # cut short
                       key(1, 2) + b"\xff\xff\xff\xff\x0f",                   # a length far past the end
                       key(1, 3) + b"\x00",                                   # a group (wire type 3)
                       b"\xff" * 11):                                         # a varint longer than 10 bytes
            with self.assertRaises(dat.DatError):
                dat.geoip(broken)
        self.assertEqual(dat.geoip(b""), {})                                   # empty: no entries, no error


class GeoSiteTest(unittest.TestCase):
    def test_types_values_attributes_and_unknown_fields(self):
        entry = f_len(1, "youtube") + UNKNOWN
        for d in (f_len(2, "youtube.com"),                                   # no type written: Plain (keyword)
                  f_varint(1, dat.DOMAIN) + f_len(2, "googlevideo.com") + f_len(3, f_len(1, "cn") + f_varint(2, 1)),
                  f_varint(1, dat.FULL) + f_len(2, "www.youtube.com") + UNKNOWN,
                  f_varint(1, dat.REGEX) + f_len(2, r"^yt\d+\.example$"),
                  f_varint(1, 9) + f_len(2, "future.example")):              # a type of a newer format
            entry += f_len(2, d)
        raw = f_len(1, entry) + f_varint(4, 1)
        g = dat.geosite(raw)
        self.assertEqual(g["YOUTUBE"], [(dat.PLAIN, "youtube.com"), (dat.DOMAIN, "googlevideo.com"),
                                        (dat.FULL, "www.youtube.com"), (dat.REGEX, r"^yt\d+\.example$"),
                                        (9, "future.example")])
        rec = lists.category(g["YOUTUBE"])
        self.assertEqual(rec["domains"], ["googlevideo.com", "www.youtube.com"])
        self.assertEqual((rec["keyword"], rec["regexp"], rec["other"], rec["full"]), (1, 1, 1, 1))

    def test_codes_asked_for(self):
        raw = geosite_dat({"CATEGORY-RU": ["yandex.ru"], "YOUTUBE": ["youtube.com"]})
        self.assertEqual(list(dat.geosite(raw, ["category-ru"])), ["CATEGORY-RU"])


class RangesTest(unittest.TestCase):
    def test_merge_into_maximal_ranges(self):
        nets = [(int(ipaddress.IPv4Address(a)), p) for a, p in
                (("10.0.1.0", 24), ("10.0.0.0", 24), ("10.0.2.0", 23), ("10.0.0.128", 25), ("192.0.2.0", 24),
                 ("192.0.4.0", 24))]
        ranges, wide = lists.merge(nets)
        self.assertEqual(lists.ranges_text(ranges), ["10.0.0.0/22", "192.0.2.0/24", "192.0.4.0/24"])
        self.assertEqual(wide, 0)
        self.assertEqual(lists.range_text(int(ipaddress.IPv4Address("5.8.0.0")), int(ipaddress.IPv4Address("5.8.2.255"))),
                         "5.8.0.0-5.8.2.255")
        self.assertEqual(lists.parse_range("5.8.0.0-5.8.2.255"), (84410368, 84411135))
        self.assertEqual(lists.parse_range("10.0.0.0/22"), (167772160, 167773183))
        with self.assertRaises(ValueError):
            lists.parse_range("10.0.0.0/22 . 0-255")

    def test_text_list(self):
        nets, counts = lists.text_cidrs("# RU\n5.8.0.0/24\n5.8.1.0/24\n2a02:6b8::/32\nnot an ip\n1.2.3.4\n")
        self.assertEqual(len(nets), 3)
        self.assertEqual(counts, {"v6": 1, "bad": 1})
        ranges, _ = lists.merge(nets)
        self.assertEqual(lists.ranges_text(ranges), ["1.2.3.4/32", "5.8.0.0/23"])

    def test_synthetic_release_size(self):
        nets = [(int(ipaddress.ip_network(n).network_address), ipaddress.ip_network(n).prefixlen) for n in ru_nets()]
        ranges, _ = lists.merge(nets)
        self.assertEqual(len(ranges), 1101)


class GroupsTest(unittest.TestCase):
    def cats(self, d):
        return dict((c, lists.category([(dat.DOMAIN, x) for x in v])) for c, v in d.items())

    def test_order_block_force_direct_without_overlaps(self):
        cats = self.cats({"category-ads": ["ads.example.com"], "win-spy": ["telemetry.example"],
                          "torrent": ["tracker.example"],
                          "youtube": ["youtube.com", "googlevideo.com", "rr1.googlevideo.com"],
                          "twitch-ads": ["gql.twitch.tv"], "twitch": ["twitch.tv"],
                          "category-ru": ["example.com", "music.youtube.com", "yandex.ru", "telemetry.example"],
                          "whitelist": ["Yandex.RU.", "x.ads.example.com", "bad name!"]})
        g = lists.groups(cats)
        self.assertEqual(g["block"], ["ads.example.com", "telemetry.example"])        # torrent off by default
        self.assertEqual([n for n in g["force"] if n not in lists.FORCE_EXTRA],
                         ["googlevideo.com", "gql.twitch.tv", "youtube.com"])   # rr1.* is redundant
        self.assertTrue(set(lists.FORCE_EXTRA) <= set(g["force"]))           # IP checkers: always through the exit
        # music.youtube.com is under a «force» domain, telemetry.example blocked, x.ads.example.com under a
        # blocked one: dropped; a parent of a force domain (twitch.tv) stays — the longer suffix wins in dnsmasq
        self.assertEqual(g["direct"], ["example.com", "twitch.tv", "yandex.ru"])
        self.assertIn("faceit", g["missing"])
        g = lists.groups(cats, block_ads=False, block_torrent=True)
        self.assertEqual(g["block"], ["tracker.example"])
        self.assertIn("telemetry.example", g["direct"])                              # not blocked any more
        self.assertEqual(cats["whitelist"]["invalid"], 1)

    def test_domain_names_are_strict(self):
        for good in ("yandex.ru", "xn--80ak6aa92e.xn--p1ai", "_dmarc.example.com", "lan", "a-b.c_d.example"):
            self.assertEqual(lists.norm_domain(good), good)
        for bad in ("", "-a.example", "a..b", "a/b", "a b", "кириллица.рф", "a" * 64 + ".example", "x/4#ip#kaskad#gw_ru",
                    "ex\nample.com"):
            self.assertIsNone(lists.norm_domain(bad), bad)


if __name__ == "__main__":
    unittest.main()
