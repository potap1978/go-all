"""The time zone of the node — kaskad.tz (docs/k3/SPEC-stage1-exits.md §3.7, §11.1 «tz.py»; stage 1, T3)."""

import calendar
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from kaskad import paths, tz
from tests.helpers import RootCase

HAVE_DB = os.path.isfile(os.path.join(tz.ZONEINFO, "Europe/Berlin"))


def utc(y, mo, d, h=0, mi=0):
    return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))


def date_offset(name, ts):
    """The wall clock of ``date`` with TZ=<name> at ``ts`` minus ``ts`` -> seconds east of UTC (GNU date or BSD date;
    not ``%z``: the date of macOS prints the offset of today for a day of 2014)."""
    env = dict(os.environ, TZ=name)
    fmt = "+%Y %m %d %H %M %S"
    args = (["date", "-r", str(ts), fmt] if sys.platform == "darwin" else ["date", "-d", "@%d" % ts, fmt])
    wall = subprocess.check_output(args, env=env, stdin=subprocess.DEVNULL).decode().split()
    return calendar.timegm(tuple(int(x) for x in wall) + (0, 0, 0)) - ts


def tzif(trans=(), idx=(), types=((0, 0, 0),), abbr=b"UTC\0", footer=b"UTC0", version=b"2"):
    """A TZif file of our own: v1 block empty of transitions, v2 block with ``trans`` (64-bit), the footer."""
    def block(tsize, tr, ix):
        h = b"TZif" + version + b"\0" * 15 + struct.pack(">6l", 0, 0, 0, len(tr), len(types), len(abbr))
        body = struct.pack(">%d%s" % (len(tr), "q" if tsize == 8 else "l"), *tr) + bytes(bytearray(ix))
        for off, dst, ai in types:
            body += struct.pack(">lBB", off, dst, ai)
        return h + body + abbr
    data = block(4, (), ())
    if version >= b"2":
        data += block(8, trans, idx) + b"\n" + footer + b"\n"
    return data


class PosixFooterTest(unittest.TestCase):
    """The POSIX-TZ string after the last transition: forms Mm.w.d, Jn, n, a signed time of a change."""

    def off(self, text, ts):
        return tz.Posix(text).offset(ts)[0]

    def test_berlin_rule_2030(self):
        p = "CET-1CEST,M3.5.0,M10.5.0/3"
        self.assertEqual(self.off(p, utc(2030, 1, 15, 12)), 3600)
        self.assertEqual(self.off(p, utc(2030, 7, 15, 12)), 7200)
        # the last Sunday of March 2030 is the 31st: 02:00 CET = 01:00 UTC
        self.assertEqual(self.off(p, utc(2030, 3, 31, 0, 59)), 3600)
        self.assertEqual(self.off(p, utc(2030, 3, 31, 1, 0)), 7200)
        # the last Sunday of October 2030 is the 27th: 03:00 CEST = 01:00 UTC
        self.assertEqual(self.off(p, utc(2030, 10, 27, 0, 59)), 7200)
        self.assertEqual(self.off(p, utc(2030, 10, 27, 1, 0)), 3600)

    def test_south(self):
        p = "AEST-10AEDT,M10.1.0,M4.1.0/3"           # summer across the new year
        self.assertEqual(self.off(p, utc(2031, 1, 10)), 11 * 3600)
        self.assertEqual(self.off(p, utc(2031, 7, 10)), 10 * 3600)
        self.assertEqual(self.off(p, utc(2031, 12, 31, 23)), 11 * 3600)

    def test_julian_without_leap_day(self):
        p = "XXX-1YYY,J60/2,J305/2"                   # J60 is the 1st of March, also in a leap year
        self.assertEqual(self.off(p, utc(2028, 2, 29, 12)), 3600)
        self.assertEqual(self.off(p, utc(2028, 3, 1, 1, 30)), 7200)   # 02:00 local = 01:00 UTC
        self.assertEqual(self.off(p, utc(2027, 3, 1, 1, 30)), 7200)

    def test_zero_based_day(self):
        p = "XXX-1YYY,59/2,305/2"                     # day 59 (from 0) of a leap year is the 29th of February
        self.assertEqual(self.off(p, utc(2028, 2, 29, 0, 30)), 3600)
        self.assertEqual(self.off(p, utc(2028, 2, 29, 1, 30)), 7200)
        self.assertEqual(self.off(p, utc(2027, 2, 28, 12)), 3600)     # 2027: day 59 is the 1st of March
        self.assertEqual(self.off(p, utc(2027, 3, 1, 1, 30)), 7200)

    def test_signed_time_of_change(self):
        p = "<-02>2<-01>,M3.5.0/-1,M10.5.0/0"         # America/Nuuk: the change at −1:00 local, a day before
        # the last Sunday of March 2030 is the 31st: −1:00 of it = 30th 23:00 local (−02) = 31st 01:00 UTC
        self.assertEqual(self.off(p, utc(2030, 3, 31, 0, 59)), -7200)
        self.assertEqual(self.off(p, utc(2030, 3, 31, 1, 0)), -3600)
        self.assertEqual(self.off(p, utc(2030, 8, 1)), -3600)

    def test_all_year_summer(self):
        p = "EST5EDT,0/0,J365/25"                     # RFC 8536 §3.3.1: summer time all year
        for ts in (utc(2030, 1, 1, 6), utc(2030, 6, 1), utc(2030, 12, 31, 23)):
            self.assertEqual(self.off(p, ts), -4 * 3600)

    def test_hours_up_to_167(self):
        tz.Posix("XXX3YYY,M3.5.0/167,M10.5.0/-167")
        with self.assertRaises(tz.TzError):
            tz.Posix("XXX3YYY,M3.5.0/168,M10.5.0/3")
        for bad in ("", "1", "XX3", "XXX3YYY,M13.1.0,M10.5.0", "XXX3YYY,J0,J10", "XXX3YYY,M3.5.0"):
            with self.assertRaises(tz.TzError):
                tz.Posix(bad)

    def test_no_summer(self):
        self.assertEqual(self.off("<+0530>-5:30", utc(2030, 7, 1)), 5 * 3600 + 1800)
        self.assertEqual(self.off("MSK-3", utc(2030, 7, 1)), 3 * 3600)


class SlimFileTest(unittest.TestCase):
    """A «slim» file (zic -b slim): no transitions after the last change of the rules — the footer gives the rest."""

    def test_own_slim_file_berlin_2030(self):
        # one transition in 1996 (the last change of the EU rules), then only the footer
        data = tzif(trans=(utc(1996, 10, 27, 1),), idx=(0,), types=((3600, 0, 0), (7200, 1, 4)),
                    abbr=b"CET\0CEST\0", footer=b"CET-1CEST,M3.5.0,M10.5.0/3")
        z = tz.parse_tzif(data, "Test/Berlin")
        self.assertEqual(z.offset(utc(2030, 7, 1)), 7200)            # summer: +2
        self.assertEqual(z.offset(utc(2030, 1, 15)), 3600)           # winter: +1
        self.assertEqual(z.offset(utc(1990, 1, 1)), 3600)            # before the first transition: the first type

    def test_zic_slim(self):
        zic = shutil.which("zic") or ("/usr/sbin/zic" if os.path.exists("/usr/sbin/zic") else None)
        if not zic:
            self.skipTest("no zic")
        d = tempfile.mkdtemp(prefix="kaskad-zic-")
        try:
            src = os.path.join(d, "src")
            with open(src, "w") as f:
                f.write("Rule EU 1981 max - Mar lastSun 1:00u 1:00 S\n"
                        "Rule EU 1996 max - Oct lastSun 1:00u 0 -\n"
                        "Zone Test/Berlin 1:00 EU CE%sT\n")
            r = subprocess.run([zic, "-b", "slim", "-d", os.path.join(d, "out"), src], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if r.returncode != 0:
                self.skipTest("zic has no -b slim: %s" % r.stderr.decode(errors="replace")[:100])
            with open(os.path.join(d, "out", "Test", "Berlin"), "rb") as f:
                z = tz.parse_tzif(f.read(), "Test/Berlin")
            self.assertIsNotNone(z.footer)
            self.assertTrue(not z.trans or z.trans[-1] < utc(2030, 1, 1))     # 2030 is only in the footer
            self.assertEqual(z.offset(utc(2030, 7, 1)), 7200)
            self.assertEqual(z.offset(utc(2030, 1, 15)), 3600)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_broken_files(self):
        good = tzif()
        for bad in (b"", b"TZi", b"XXXX" + good[4:], good[:50], good[:60]):
            with self.assertRaises((tz.TzError, struct.error)):
                tz.parse_tzif(bad)
        with self.assertRaises(tz.TzError):                            # a footer that does not parse
            tz.parse_tzif(tzif(footer=b"!!!"))

    def test_version1_file(self):
        z = tz.parse_tzif(tzif(types=((19800, 0, 0),), abbr=b"IST\0", version=b"\0"))
        self.assertEqual(z.offset(utc(2030, 1, 1)), 19800)


@unittest.skipUnless(HAVE_DB, "no /usr/share/zoneinfo")
class SystemZonesTest(unittest.TestCase):
    """The system database against ``date`` with TZ= (a subprocess). Until 2037: the libc of macOS stops there."""

    def test_against_date(self):
        rnd = random.Random(7)
        stamps = [utc(2000, 1, 1), utc(2014, 10, 26, 1), utc(2026, 3, 29, 1), utc(2026, 10, 25, 0, 59),
                  utc(2026, 10, 25, 1)] + [rnd.randint(utc(2000, 1, 1), utc(2036, 12, 1)) for _ in range(12)]
        for name in ("Europe/Moscow", "Europe/Berlin", "UTC", "Asia/Kolkata"):
            z = tz.load(name)
            self.assertTrue(z.ok, name)
            for ts in stamps:
                self.assertEqual(z.offset(ts), date_offset(name, ts), "%s at %d" % (name, ts))

    def test_berlin_summer_and_winter(self):
        z = tz.load("Europe/Berlin")
        self.assertEqual(z.offset(utc(2030, 7, 1)), 7200)
        self.assertEqual(z.offset(utc(2030, 1, 15)), 3600)
        self.assertEqual(tz.load("Europe/Moscow").offset(utc(2030, 7, 1)), 3 * 3600)
        self.assertEqual(tz.load("Asia/Kolkata").offset(utc(2030, 7, 1)), 5 * 3600 + 1800)
        self.assertEqual(tz.load("UTC").offset(utc(2030, 7, 1)), 0)


class NodeZoneTest(RootCase):
    """KASKAD_TZ in /etc/kaskad/config: reading, writing, the fallback UTC+03:00, the clock of the node."""

    def setUp(self):
        super().setUp()
        tz._conf_cache[0] = tz._conf_cache[1] = None
        self.db = tempfile.mkdtemp(prefix="kaskad-zi-")
        self.addCleanup(shutil.rmtree, self.db, True)
        os.makedirs(os.path.join(self.db, "Europe"))
        with open(os.path.join(self.db, "Europe", "Berlin"), "wb") as f:
            f.write(tzif(trans=(utc(1996, 10, 27, 1),), idx=(0,), types=((3600, 0, 0), (7200, 1, 4)),
                         abbr=b"CET\0CEST\0", footer=b"CET-1CEST,M3.5.0,M10.5.0/3"))
        with open(os.path.join(self.db, "Europe", "Moscow"), "wb") as f:
            f.write(tzif(types=((10800, 0, 0),), abbr=b"MSK\0", footer=b"MSK-3"))
        with open(os.path.join(self.db, "Europe", "Broken"), "wb") as f:
            f.write(b"TZif2 not really")
        p = mock.patch.object(tz, "ZONEINFO", self.db)
        p.start()
        self.addCleanup(p.stop)

    def test_default_is_moscow(self):
        self.assertEqual(tz.name(), "Europe/Moscow")
        self.assertIsNone(tz.warning())
        self.assertEqual(tz.offset(utc(2030, 7, 1)), 3 * 3600)

    def test_set_name_keeps_other_lines(self):
        self.write("/etc/kaskad/config", 'KASKAD_LANG="en"\nBOT_TOKEN="x"\nKASKAD_TZ="Europe/Moscow"\n')
        self.assertEqual(tz.set_name(" Europe/Berlin "), "Europe/Berlin")
        text = self.read("/etc/kaskad/config")
        self.assertIn('KASKAD_LANG="en"', text)
        self.assertIn('BOT_TOKEN="x"', text)
        self.assertEqual(text.count("KASKAD_TZ"), 1)
        self.assertEqual(tz.name(), "Europe/Berlin")
        self.assertEqual(oct(os.stat(paths.config()).st_mode & 0o777), "0o600")
        self.assertEqual(tz.offset(utc(2030, 7, 1)), 7200)

    def test_bad_names_refused(self):
        for bad in ("../etc/passwd", "Europe/../../x", "", None, "Europe/Ber lin", "/abs", "a/b/c/d", "Europe/Nowhere",
                    "Europe/Broken"):
            with self.assertRaises(ValueError):
                tz.set_name(bad)
        self.assertFalse(os.path.exists(paths.config()))

    def test_no_file_falls_back_to_plus3(self):
        self.write("/etc/kaskad/config", 'KASKAD_TZ="Europe/Nowhere"\n')
        self.assertEqual(tz.warning(), "no_zone")
        self.assertEqual(tz.offset(utc(2030, 1, 1)), 3 * 3600)
        self.write("/etc/kaskad/config", "KASKAD_TZ='Europe/Broken'\n")
        self.assertEqual(tz.warning(), "no_zone")
        self.assertEqual(tz.offset(utc(2030, 1, 1)), 3 * 3600)
        self.write("/etc/kaskad/config", "export KASKAD_TZ=../../etc/x\n")
        self.assertEqual(tz.warning(), "bad_name")
        self.assertEqual(tz.offset(utc(2030, 1, 1)), 3 * 3600)

    def test_day_at_midnight(self):
        self.write("/etc/kaskad/config", 'KASKAD_TZ="Europe/Moscow"\n')
        mid = utc(2025, 12, 31, 21)                   # 2026-01-01 00:00 MSK
        self.assertEqual(tz.day(mid - 1), "2025-12-31")
        self.assertEqual(tz.day(mid), "2026-01-01")
        lt = tz.local(mid)
        self.assertEqual((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour), (2026, 1, 1, 0))
        self.assertEqual(lt.tm_gmtoff, 3 * 3600)

    def test_next_at(self):
        self.write("/etc/kaskad/config", 'KASKAD_TZ="Europe/Moscow"\n')
        self.assertEqual(tz.next_at("04:00", utc(2030, 5, 10, 0)), utc(2030, 5, 10, 1))    # 03:00 MSK -> 04:00
        self.assertEqual(tz.next_at("04:00", utc(2030, 5, 10, 1)), utc(2030, 5, 11, 1))    # at 04:00 — tomorrow
        self.assertEqual(tz.next_at("04:00", utc(2030, 5, 10, 2)), utc(2030, 5, 11, 1))
        with self.assertRaises(ValueError):
            tz.next_at("25:00", 0)

    def test_next_at_over_summer_change(self):
        tz.set_name("Europe/Berlin")
        # 2030-03-31: 02:00 CET -> 03:00 CEST; 02:30 does not exist -> 03:30 CEST = 01:30 UTC
        self.assertEqual(tz.next_at("02:30", utc(2030, 3, 30, 23)), utc(2030, 3, 31, 1, 30))
        self.assertEqual(tz.next_at("04:00", utc(2030, 3, 30, 23)), utc(2030, 3, 31, 2))   # 04:00 CEST
        # 2030-10-27: 02:30 runs twice -> the first (CEST) = 00:30 UTC
        self.assertEqual(tz.next_at("02:30", utc(2030, 10, 26, 23)), utc(2030, 10, 27, 0, 30))

    def test_new_zone_seen_without_restart(self):
        self.assertEqual(tz.offset(utc(2030, 7, 1)), 3 * 3600)
        tz.set_name("Europe/Berlin")
        self.assertEqual(tz.offset(utc(2030, 7, 1)), 7200)


if __name__ == "__main__":
    unittest.main()
