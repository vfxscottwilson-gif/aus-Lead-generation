"""v3.1 tests: Serper / Brave / ZenRows / ABN Lookup / Apify helpers, AU facts, REGIONS by state, Australia edition.
Offline (network mocked). Run: python -m unittest discover -s tests -v"""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["KEY_CHECK"] = "warn"    # fake test keys/ids have no real shape
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import defaults as D
import enrich as E            # noqa: E402
import leadbot as LB          # noqa: E402
import optional as O          # noqa: E402
from test_v3 import Resp      # noqa: E402

KEYS = {"SERPER_API_KEY": "s-key", "BRAVE_API_KEY": "b-key", "ZENROWS_API_KEY": "z-key", "ABN_LOOKUP_GUID": "g-uid",
        "APIFY_API_TOKEN": "a-tok"}


def reset_breakers():
    O._OFF.clear()
    O._FAILS.clear()
    O._CAPS.clear()


class TestAUFacts(unittest.TestCase):
    def test_abn_checksum_and_extraction(self):
        self.assertTrue(E.abn_valid("51 824 753 556"))                # ATO's published example ABN
        self.assertFalse(E.abn_valid("12 345 678 901"))
        txt = "Bistro Lune Pty Ltd | ABN: 51 824 753 556 | ACN 123 456 789 | call 1300 123 456 78901"
        self.assertEqual(E.abns_in(txt), ["51824753556"])
        self.assertEqual(E.abns_in("Phone 51 824 753 556"), [])       # no 'ABN' label -> not taken
        self.assertEqual(E.fmt_abn("51824753556"), "51 824 753 556")

    def test_abn_from_privacy_page(self):
        pages = {"https://lune.com.au": "<html><body>Home</body></html>",
                 "https://lune.com.au/privacy-policy": "<html><body>Lune Hospitality Pty Ltd (ABN 51 824 753 556)</body></html>"}

        def get(url, *a, **kw):
            if url.endswith("/robots.txt"):
                return Resp(status=404)
            return Resp(text=pages[url.rstrip("/")], url=url) if url.rstrip("/") in pages else Resp(status=404, url=url)
        with mock.patch("requests.get", side_effect=get), mock.patch.object(E.time, "sleep"):
            E._robots.clear()
            info = E.enrich_site("https://lune.com.au", 10, True, "Lune")
        self.assertEqual(info["abns"], ["51824753556"])
        self.assertNotIn("Lune Hospitality", info["text"])               # privacy text still kept out of the AI context

    def test_phone_type_and_timezone(self):
        self.assertEqual(E.phone_type("+61 412 345 678"), "Mobile")
        self.assertEqual(E.phone_type("(02) 9000 1234"), "Landline")
        self.assertEqual(E.phone_type("1300 123 456"), "13/1300/1800")
        self.assertEqual(E.phone_type("+44 20 7946 0000", "GB"), "")
        self.assertEqual(E.AU_TZ[E.au_state("Western Australia")], "Australia/Perth")
        self.assertEqual(E.au_state("nsw"), "NSW")

    def test_site_from_results_is_strict(self):
        res = [{"link": "https://www.tripadvisor.com.au/Restaurant-x"}, {"link": "https://www.facebook.com/bistrolune"},
               {"link": "https://otherplace.com.au/"}, {"link": "https://bistrolune.com.au/menu?x=1"}]
        self.assertEqual(E.site_from_results(res, "Bistro Lune"), "https://bistrolune.com.au/")
        self.assertEqual(E.site_from_results(res[:3], "Bistro Lune"), "")

    def test_linkedin_titles(self):
        res = [{"link": "https://au.linkedin.com/in/amy-chen", "title": "Amy Chen – Owner – Bistro Lune | LinkedIn", "snippet": ""},
               {"link": "https://au.linkedin.com/in/bob", "title": "Bob Ray - Owner - Other Cafe | LinkedIn", "snippet": ""},
               {"link": "https://au.linkedin.com/in/joe", "title": "Joe Bloggs - Waiter - Bistro Lune | LinkedIn", "snippet": ""},
               {"link": "https://www.linkedin.com/company/bistro-lune", "title": "Bistro Lune | LinkedIn", "snippet": ""}]
        self.assertEqual(E.people_from_linkedin(res, "Bistro Lune"), [("Amy Chen", "Owner", "https://au.linkedin.com/in/amy-chen")])

    def test_region_tokens(self):
        self.assertTrue(LB.region_ok("AU", "NSW", ["AU-NSW", "AU-ACT"]))
        self.assertTrue(LB.region_ok("AU", "New South Wales", ["AU-NSW"]))
        self.assertFalse(LB.region_ok("AU", "VIC", ["AU-NSW"]))
        self.assertTrue(LB.region_ok("AU", "VIC", ["AU"]))
        self.assertFalse(LB.region_ok("US", "NY", ["AU"]))

    def test_age_scores_like_reviews(self):
        base = {"fit": 4, "reviews": None, "rating": None, "subs": None, "has_website": True, "personal_email": True,
                "generic_email": True, "email_status": "", "instagram": True, "other_social": True, "phone": True,
                "decision_maker": False, "gap_sev": 0.5, "spend": 0, "priority": 1}
        S = LB.Settings({})
        no_age, old, new = (LB.SC.score(dict(base, age=a), S) for a in (None, 12, 0.5))
        self.assertGreater(old, no_age)
        self.assertLess(new, no_age)


class TestOptionalHelpers(unittest.TestCase):
    def setUp(self):
        isolated = mock.patch.dict(os.environ, {"KEY_CHECK": "warn"}, clear=True)
        isolated.start()
        self.addCleanup(isolated.stop)
        reset_breakers()

    def test_no_keys_no_network(self):
        with mock.patch.dict(os.environ, {k: "" for k in KEYS}), \
                mock.patch("requests.request", side_effect=AssertionError("network")):
            self.assertEqual(O.serper_places("x"), [])
            self.assertEqual(O.web_search("x"), [])
            self.assertIsNone(O.zenrows_fetch("https://a.com"))
            self.assertIsNone(O.abn_details("51824753556"))
            self.assertEqual(O.apify_maps("x"), [])
            self.assertFalse(O.search_on())

    def test_serper_brave_fallback_and_caps(self):
        def router(method, url, **kw):
            if "serper.dev/search" in url:
                self.assertEqual(kw["headers"]["X-API-KEY"], "s-key")
                return Resp(status=429)                                  # Serper used up -> Brave
            if "search.brave.com" in url:
                self.assertEqual(kw["headers"]["X-Subscription-Token"], "b-key")
                return Resp(js={"web": {"results": [{"title": "T", "url": "https://bistrolune.com.au/", "description": "d"}]}})
            raise AssertionError(url)
        with mock.patch.dict(os.environ, KEYS, clear=True), mock.patch("requests.request", side_effect=router), mock.patch.object(E, "safe_allowed", return_value=True):
            out = O.web_search("bistro lune sydney", "AU", cap=2)
            self.assertEqual(out[0]["link"], "https://bistrolune.com.au/")
            self.assertFalse(O.available("serper"))                      # tripped after 429
            O.web_search("again", "AU", cap=2)
            self.assertEqual(O.web_search("third", "AU", cap=2), [])     # cap reached

    def test_serper_places_and_zenrows(self):
        places = {"places": [{"title": "Bistro Lune", "rating": 4.7, "ratingCount": 212, "cid": "99", "website": "https://bistrolune.com.au"}]}

        def router(method, url, **kw):
            if "serper.dev/places" in url:
                self.assertEqual(kw["json"]["gl"], "au")
                return Resp(js=places)
            if "zenrows.com" in url:
                self.assertEqual(kw["params"]["js_render"], "true")
                return Resp(text="<html>" + "x" * 300 + "</html>")
            raise AssertionError(url)
        with mock.patch.dict(os.environ, KEYS, clear=True), mock.patch("requests.request", side_effect=router), mock.patch.object(E, "safe_allowed", return_value=True):
            self.assertEqual(O.serper_places("Bistro Lune Sydney")[0]["ratingCount"], 212)
            self.assertTrue(O.zenrows_fetch("https://blocked.com.au/").startswith("<html>"))

    def test_abn_lookup(self):
        det = 'c({"Abn":"51824753556","AbnStatus":"Active","AbnStatusEffectiveFrom":"2015-03-01","AddressPostcode":"2000",' \
              '"AddressState":"NSW","BusinessName":["Bistro Lune"],"EntityName":"LUNE HOSPITALITY PTY LTD",' \
              '"EntityTypeName":"Australian Private Company","Gst":"2015-03-01","Message":""})'
        names = 'c({"Message":"","Names":[{"Abn":"11111111111","Name":"Bistro Lune Pty Ltd","State":"VIC","Postcode":"3000"},' \
                '{"Abn":"51824753556","Name":"BISTRO LUNE PTY. LTD.","State":"NSW","Postcode":"2000"}]})'

        def router(method, url, **kw):
            if "AbnDetails" in url:
                return Resp(text=det)
            if "MatchingNames" in url:
                return Resp(text=names)
            raise AssertionError(url)
        with mock.patch.dict(os.environ, KEYS, clear=True), mock.patch("requests.request", side_effect=router), mock.patch.object(E, "safe_allowed", return_value=True):
            d = O.abn_details("51 824 753 556")
            self.assertEqual((d["status"], d["entity_name"], d["gst_from"], d["state"]), ("Active", "LUNE HOSPITALITY PTY LTD", "2015-03-01", "NSW"))
            self.assertEqual(O.abn_by_name("Bistro Lune", "NSW", "2000"), "51824753556")   # VIC namesake ignored
            self.assertIsNone(O.abn_by_name("Bistro Lune Bar", "NSW"))                       # not an exact name

    def test_abn_bad_guid_trips(self):
        bad = 'c({"Abn":"","Message":"The GUID entered is not recognised as a Registered Party"})'
        with mock.patch.dict(os.environ, KEYS), mock.patch("requests.request", return_value=Resp(text=bad)):
            self.assertIsNone(O.abn_details("51824753556"))
            self.assertFalse(O.available("abn"))

    def test_apify_maps_items_to_places(self):
        items = [{"title": "Iron Lab", "totalScore": 4.9, "reviewsCount": 88, "website": "https://ironlab.com.au", "phone": "02 9000 0000",
                  "placeId": "ChIJironlab000000000000000", "city": "Sydney", "state": "New South Wales", "postalCode": "2000",
                  "countryCode": "AU", "categoryName": "Gym", "url": "https://maps.google.com/?cid=1"}]
        with mock.patch.dict(os.environ, KEYS), mock.patch("requests.request", return_value=Resp(js=items)):
            got = O.apify_maps("gym in Sydney")
        p = LB.google_places_from(got, {"city": "Sydney", "state": "NSW", "country": "Australia"}, "Google (Apify)")[0]
        self.assertEqual((p["id"], p["rating"], p["userRatingCount"], p["_source"]), ("ChIJironlab000000000000000", 4.9, 88, "Google (Apify)"))


class TestAustraliaEdition(unittest.TestCase):
    def test_au_defaults(self):
        code = ("import defaults as D;import json;print(json.dumps([D.MARKET, D.LEAD_H[-8:], sorted({l[0] for l in D.LOCATIONS}),"
                "len(D.LOCATIONS), [n[0] for n in D.NICHES if n[1]=='YouTube'], dict((k,v) for k,v,_ in D.SETTINGS)['YT_COUNTRIES'],"
                "any('OTHER COUNTRIES' in l for l in D.PLAYBOOK)]))")
        out = subprocess.run([sys.executable, "-c", code], cwd=os.path.join(os.path.dirname(HERE), "src"), capture_output=True,
                             text=True, env=dict(os.environ, LEAD_MARKET="AU"), timeout=60)
        market, cols, countries, nloc, yt, ytc, other = json.loads(out.stdout)
        self.assertEqual(market, "AU")
        self.assertIn("ABN", D.LEAD_H)
        self.assertEqual(countries, ["Australia"])
        self.assertGreaterEqual(nloc, 70)
        self.assertTrue(all(n.startswith("YouTube Creators AU") for n in yt))
        self.assertEqual(ytc, "AU")
        self.assertFalse(other)

    def test_au_pipeline_subprocess(self):
        """Full run of the Australia edition with mocked network (separate process: LEAD_MARKET=AU)."""
        out = subprocess.run([sys.executable, "-m", "unittest", "au_pipeline_case"], cwd=HERE, capture_output=True, text=True,
                             env=dict(os.environ, LEAD_MARKET="AU"), timeout=180)
        self.assertEqual(out.returncode, 0, out.stdout[-2000:] + out.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
