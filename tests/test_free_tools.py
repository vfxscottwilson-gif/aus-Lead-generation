"""v5.1 free-tools modules (FREE_TOOLS_RESEARCH.md): link-in-bio own-site, Disify, Reacher, NZBN, Snov.io, Prospeo.
Network mocked. Every module: no key = no request, bad/odd reply or 429 = None, never raises."""
import os
import sys
import unittest
from unittest import mock

os.environ["KEY_CHECK"] = "warn"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import enrich as E        # noqa: E402
import optional as O      # noqa: E402


def resp(code=200, data=None):
    r = mock.Mock(status_code=code)
    r.json.return_value = data
    return r


class Base(unittest.TestCase):
    def setUp(self):
        isolated = mock.patch.dict(os.environ, {"KEY_CHECK": "warn"}, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        O._OFF.clear()
        O._FAILS.clear()
        O._SNOV_TOKEN.update(t="", exp=0)
        O.FLAGS.update(disify=False, reacher=True)


class TestLinkInBio(Base):
    def test_own_site_from_link_page(self):
        html = ('<script id="__NEXT_DATA__">{"links":[{"url":"https:\\/\\/ugc.production.linktr.ee\\/a.png"},'
                '{"url":"https://www.amazon.com/shop/jane"},{"url":"https://www.instagram.com/janefit"},'
                '{"url":"https://patreon.com/janefit"},{"url":"https://janefitcoaching.com/apply"}]}</script>')
        self.assertEqual(E.bio_site(html, "Jane Fit Coaching"), "https://janefitcoaching.com")
        self.assertEqual(E.bio_site(html, "Totally Different"), "")          # no name word in the domain: not taken
        self.assertEqual(E.bio_site("", "Jane"), "")

    def test_enrich_site_follows_own_site(self):
        pages = {"https://linktr.ee/janefit": '<a href="https://janefitcoaching.com">site</a>',
                 "https://janefitcoaching.com": '<p>Contact: hello@janefitcoaching.com</p>'}
        with mock.patch.object(E, "fetch", side_effect=lambda u: pages.get(u.rstrip("/"))), \
                mock.patch.object(E, "safe_allowed", return_value=True), mock.patch.object(E.time, "sleep"):
            info = E.enrich_site("https://linktr.ee/janefit", 5, True, "Jane Fit Coaching")
        self.assertEqual(info["domain"], "janefitcoaching.com")
        self.assertIn("hello@janefitcoaching.com", info["emails"])


class TestVerifiers(Base):
    def test_disify_off_by_default_then_flags_disposable(self):
        with mock.patch("requests.request") as rq, mock.patch.dict(os.environ, {"REACHER_URL": "", "USE_MX_CHECK": "false"}):
            O.verify_email("a@mailinator.com")
            self.assertFalse(rq.called)                                     # default Off: no request
            O.FLAGS["disify"] = True
            rq.return_value = resp(200, {"format": True, "disposable": True, "dns": True})
            self.assertEqual(O.verify_email("a@mailinator.com"), "invalid")
            rq.return_value = resp(200, {"format": True, "disposable": False, "dns": True})
            self.assertIsNone(O.verify_email("ann@realbiz.com"))            # unknown -> next check (MX off here)

    def test_reacher(self):
        with mock.patch.dict(os.environ, {"REACHER_URL": "https://reacher.example.org/", "REACHER_SECRET": "s3cret",
                                          "USE_MX_CHECK": "false"}), mock.patch("requests.request") as rq:
            rq.return_value = resp(200, {"is_reachable": "safe"})
            self.assertEqual(O.verify_email("ann@realbiz.com"), "valid")
            args, kw = rq.call_args
            self.assertEqual(args[1], "https://reacher.example.org/v0/check_email")
            self.assertEqual(kw["headers"]["x-reacher-secret"], "s3cret")
            rq.return_value = resp(200, {"is_reachable": "unknown"})
            self.assertIsNone(O.reacher_check("x@realbiz.com"))
            rq.side_effect = ConnectionError("down")
            self.assertIsNone(O.reacher_check("x@realbiz.com"))              # never raises


class TestNZBN(Base):
    SEARCH = {"items": [{"nzbn": "9429000000001", "entityName": "KIWI SMILES LIMITED", "entityStatusDescription": "Registered"},
                        {"nzbn": "9429000000002", "entityName": "KIWI SMILES HOLDINGS LIMITED"}]}
    DETAIL = {"entityStatusDescription": "Registered",
              "emailAddresses": [{"emailAddress": "Office@KiwiSmiles.co.nz"}],
              "phoneNumbers": [{"phoneCountryCode": "64", "phoneAreaCode": "9", "phoneNumber": "5550100"}],
              "roles": [{"roleType": "Director", "roleStatus": "Active", "rolePerson": {"firstName": "ARIA", "lastName": "NGATA"}},
                        {"roleType": "Shareholder", "rolePerson": {"firstName": "Bob", "lastName": "Jones"}}]}

    def test_exact_match_directors_and_contacts(self):
        with mock.patch.dict(os.environ, {"NZBN_API_KEY": "k" * 32}), \
                mock.patch("requests.request", side_effect=[resp(200, self.SEARCH), resp(200, self.DETAIL)]) as rq:
            f = O.nzbn_facts("Kiwi Smiles Ltd")
        self.assertEqual(f["nzbn"], "9429000000001")
        self.assertEqual(f["directors"], [("Aria Ngata", "Director")])       # shareholders are not decision makers
        self.assertEqual(f["emails"], ["office@kiwismiles.co.nz"])
        self.assertEqual(f["phones"], ["+6495550100"])
        self.assertEqual(rq.call_args_list[0].kwargs["headers"]["Ocp-Apim-Subscription-Key"], "k" * 32)

    def test_no_exact_match_no_key_odd_reply(self):
        with mock.patch.dict(os.environ, {"NZBN_API_KEY": "k" * 32}), mock.patch("requests.request", return_value=resp(200, self.SEARCH)):
            self.assertIsNone(O.nzbn_facts("Kiwi Smile Studio"))            # facts only: no fuzzy match
        with mock.patch.dict(os.environ, {"NZBN_API_KEY": ""}), mock.patch("requests.request") as rq:
            self.assertIsNone(O.nzbn_facts("Kiwi Smiles"))
            self.assertFalse(rq.called)
        with mock.patch.dict(os.environ, {"NZBN_API_KEY": "k" * 32}), mock.patch("requests.request", return_value=resp(200, ["?"])):
            self.assertIsNone(O.nzbn_facts("Kiwi Smiles"))


class TestFinders(Base):
    def test_snov(self):
        env = {"SNOV_CLIENT_ID": "i" * 32, "SNOV_CLIENT_SECRET": "s" * 32}
        tok = resp(200, {"access_token": "T", "expires_in": 3600})
        found = resp(200, {"success": True, "data": {"emails": [{"email": "Aria@kiwismiles.co.nz", "emailStatus": "valid"},
                                                               {"email": "aria@gmail.com", "emailStatus": "valid"}]}})
        with mock.patch.dict(os.environ, env), mock.patch("requests.request", side_effect=[tok, found]) as rq:
            self.assertEqual(O.snov_finder("kiwismiles.co.nz", "Dr Aria Ngata"), "aria@kiwismiles.co.nz")
        self.assertEqual(rq.call_args_list[1].kwargs["data"]["firstName"], "Aria")
        with mock.patch.dict(os.environ, env), mock.patch("requests.request", return_value=resp(200, {"data": {"emails": [
                {"email": "aria@kiwismiles.co.nz", "emailStatus": "invalid"}]}})):
            self.assertIsNone(O.snov_finder("kiwismiles.co.nz", "Aria Ngata"))    # token cached; invalid status dropped
        with mock.patch.dict(os.environ, env), mock.patch("requests.request") as rq:
            self.assertIsNone(O.snov_finder("kiwismiles.co.nz", "Aria"))     # one-word name: no lookup
            self.assertFalse(rq.called)

    def test_prospeo(self):
        with mock.patch.dict(os.environ, {"PROSPEO_API_KEY": "p" * 32}), mock.patch("requests.request") as rq:
            rq.return_value = resp(200, {"error": False, "response": {"email": {"email": "aria@kiwismiles.co.nz", "email_status": "VALID"}}})
            self.assertEqual(O.prospeo_finder("kiwismiles.co.nz", "Aria Ngata"), "aria@kiwismiles.co.nz")
            self.assertEqual(rq.call_args.kwargs["headers"]["X-KEY"], "p" * 32)
            rq.return_value = resp(200, {"error": True, "message": "NO_RESULT"})
            self.assertIsNone(O.prospeo_finder("kiwismiles.co.nz", "Aria Ngata"))
            rq.return_value = resp(429, None)
            self.assertIsNone(O.prospeo_finder("kiwismiles.co.nz", "Aria Ngata"))
            self.assertFalse(O.available("prospeo"))                         # 429 trips it for the rest of the run
        with mock.patch.dict(os.environ, {"PROSPEO_API_KEY": ""}), mock.patch("requests.request") as rq:
            self.assertIsNone(O.prospeo_finder("kiwismiles.co.nz", "Aria Ngata"))
            self.assertFalse(rq.called)


if __name__ == "__main__":
    unittest.main()
