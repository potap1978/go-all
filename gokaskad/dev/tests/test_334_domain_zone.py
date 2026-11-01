"""Kaskad 3.3.4, REV-334 №4: the wizard keeps the domain as typed in its field «Ваш домен» (``answers.domain_zone``,
``domain_try.zone``) next to the full name, so after a reload the fields come back as they were: a bare
``example.co.nz`` is not split into the subdomain «example» of «co.nz» (the A record «example» instead of «@»)."""

import unittest

from kaskad import util
from kaskad.setup import model as smodel_setup
from kaskad.setup import steps
from kaskad.site import dns
from tests.site.sitecase import DOMAIN
from tests.test_qs330d_domain import SwitchCase


class ZoneOfTest(unittest.TestCase):
    def test_zone_of(self):
        z = steps._zone_of
        self.assertEqual(z("example.co.nz", "example.co.nz"), "example.co.nz")       # no subdomain
        self.assertEqual(z("vpn.example.co.nz", "example.co.nz"), "example.co.nz")   # one label before it
        self.assertEqual(z("VPN.Example.com ", " example.COM"), "example.com")
        self.assertEqual(z("a.b.example.com", "example.com"), "")                    # two labels — not a subdomain field
        self.assertEqual(z("vpn.example.com", "other.com"), "")                      # not of this name
        self.assertEqual(z("vpnexample.com", "example.com"), "")                     # a suffix, not a label
        self.assertEqual(z("example.com", "com"), "")                                # a zone without a dot is no domain
        self.assertEqual(z("example.com", None), "")
        self.assertEqual(z("example.com", ""), "")


class DomainZoneTest(SwitchCase):
    def ans(self):
        return smodel_setup.load()["answers"]

    def test_answer_keeps_the_zone(self):
        steps._domain_answer("example.co.nz", None, "example.co.nz")
        self.assertEqual((self.ans()["domain"], self.ans()["domain_zone"]), ("example.co.nz", "example.co.nz"))
        steps._domain_answer("vpn.example.co.nz", None, "example.co.nz")
        self.assertEqual((self.ans()["domain"], self.ans()["domain_zone"]), ("vpn.example.co.nz", "example.co.nz"))
        # the same name, the field split another way: written (the early return compares the zone too)
        steps._domain_answer("vpn.example.co.nz", None, "vpn.example.co.nz")
        self.assertEqual(self.ans()["domain_zone"], "vpn.example.co.nz")
        before = util.read_text(smodel_setup.path())
        steps._domain_answer("vpn.example.co.nz", None, "vpn.example.co.nz")      # the poll: nothing new
        self.assertEqual(util.read_text(smodel_setup.path()), before)
        # a name without a zone (the panel, an old page) or with a zone of another name — no zone kept
        steps._domain_answer("vpn.example.com", None, "other.com")
        self.assertNotIn("domain_zone", self.ans())

    def test_try_keeps_its_zone_and_the_domain_its_own(self):
        new = "vpn.example.co.nz"
        steps._domain_answer(DOMAIN, None, "example.com")
        self.no_record()
        steps.run("dns", {"name": new, "zone": "example.co.nz"}, self.w.runner)
        a = self.ans()
        self.assertEqual((a["domain"], a["domain_zone"]), (DOMAIN, "example.com"))   # the working domain — as it was
        self.assertEqual((a["domain_try"]["name"], a["domain_try"]["zone"]), (new, "example.co.nz"))
        self.answers_for[new] = {dns.A: ("ok", ["203.0.113.10"]), dns.AAAA: ("ok", [])}
        steps.run("dns", {"name": new}, self.w.runner)                             # an old page: no zone
        self.assertEqual(self.ans()["domain_try"]["zone"], "")

    def test_check_of_the_working_domain_follows_the_field(self):
        """REV-334: «vpn.example.com» целиком в «Ваш домен» (зона = имя), DNS в порядке; «Перенести» делит поля —
        проверка того же имени с зоной example.com обновляет зону, чтобы после перезагрузки поля были как сейчас."""
        steps._domain_answer(DOMAIN, None, DOMAIN)
        self.assertEqual(self.ans()["domain_zone"], DOMAIN)
        steps.run("dns", {"name": DOMAIN, "zone": "example.com"}, self.w.runner)
        a = self.ans()
        self.assertEqual((a["domain"], a["domain_zone"]), (DOMAIN, "example.com"))
        before = util.read_text(smodel_setup.path())
        steps.run("dns", {"name": DOMAIN, "zone": "example.com"}, self.w.runner)  # the poll: nothing new
        self.assertEqual(util.read_text(smodel_setup.path()), before)
        for zone in ("other.com", "", "com"):                                     # not of this name / an old page
            steps.run("dns", {"name": DOMAIN, "zone": zone}, self.w.runner)
            self.assertEqual(self.ans()["domain_zone"], "example.com", zone)
        steps.run("dns", {}, self.w.runner)                                        # no name — nothing
        self.assertEqual(self.ans()["domain_zone"], "example.com")
        steps.run("dns", {"name": DOMAIN, "zone": DOMAIN}, self.w.runner)          # joined back — follows too
        self.assertEqual(self.ans()["domain_zone"], DOMAIN)

    def test_check_of_the_working_domain_without_its_answer_writes_no_zone(self):
        """The domain set in the panel (no ``answers.domain``): the zone of nothing is not kept."""
        smodel_setup.update(lambda d: d["answers"].pop("domain", None))
        steps.run("dns", {"name": DOMAIN, "zone": "example.com"}, self.w.runner)
        self.assertNotIn("domain_zone", self.ans())

    def test_bad_zone_type_is_refused(self):
        res = steps.run("dns", {"name": "vpn.example.com", "zone": True}, self.w.runner)
        self.assertFalse(res["ok"], res)


if __name__ == "__main__":
    unittest.main()
