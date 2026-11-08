"""Australia edition, end to end, mocked network. Run by test_v31 in a subprocess with LEAD_MARKET=AU
(not picked up by 'discover' on its own because the file name does not start with 'test')."""
import csv
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["KEY_CHECK"] = "warn"    # fake test keys/ids have no real shape
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import defaults as D          # noqa: E402
import enrich as E            # noqa: E402
import leadbot as LB          # noqa: E402
import optional as O          # noqa: E402
from test_v3 import Resp      # noqa: E402

OVERPASS = {"elements": [
    {"type": "node", "id": 11, "tags": {"name": "Bistro Lune", "amenity": "restaurant", "phone": "(02) 9000 1234",
                                        "addr:state": "NSW", "addr:postcode": "2000"}},            # no website in OSM
]}
SITES = {
    "https://bistrolune.com.au": '<html><body><h1>Bistro Lune</h1><p>Seasonal degustation and tasting menu.</p>'
                                 '<a href="/about">About</a><a href="https://www.instagram.com/bistrolune/">IG</a>'
                                 '<footer>Lune Hospitality Pty Ltd ABN 51 824 753 556</footer></body></html>',
    "https://bistrolune.com.au/about": "<html><body><p>Amy Chen, Owner</p><p><a href=\"mailto:amy@bistrolune.com.au\">Amy Chen</a> | hello@bistrolune.com.au</p></body></html>",
}
DET = 'c({"Abn":"51824753556","AbnStatus":"Active","AbnStatusEffectiveFrom":"2015-03-01","AddressPostcode":"2000",' \
      '"AddressState":"NSW","BusinessName":["Bistro Lune"],"EntityName":"LUNE HOSPITALITY PTY LTD",' \
      '"EntityTypeName":"Australian Private Company","Gst":"2015-03-01","Message":""})'


def fake_get(url, *a, **kw):
    if url.endswith("/robots.txt"):
        return Resp(status=404)
    return Resp(text=SITES[url.rstrip("/")], url=url) if url.rstrip("/") in SITES else Resp(status=404, url=url)


def fake_post(url, *a, **kw):
    return Resp(js=OVERPASS)


def fake_request(method, url, **kw):
    if "serper.dev/search" in url:
        return Resp(js={"organic": [{"title": "Bistro Lune - TripAdvisor", "link": "https://www.tripadvisor.com.au/x"},
                                    {"title": "Bistro Lune Sydney", "link": "https://bistrolune.com.au/"}]})
    if "serper.dev/places" in url:
        return Resp(js={"places": [{"title": "Bistro Lune", "rating": 4.7, "ratingCount": 212, "cid": "4242",
                                    "website": "https://bistrolune.com.au/"}]})
    if "AbnDetails" in url:
        return Resp(text=DET)
    raise AssertionError("unexpected API call " + url)


class TestAUPipeline(unittest.TestCase):
    def test_full_run(self):
        self.assertEqual(D.MARKET, "AU")
        d = tempfile.mkdtemp()
        env = {"LOCAL_SHEET_DIR": d, "SOURCES": "maps", "REGIONS": "AU-NSW", "AI_API_KEYS": "", "PLACES_API_KEYS": "",
               "SERPER_API_KEY": "s", "ABN_LOOKUP_GUID": "g", "BRAVE_API_KEY": "", "ZENROWS_API_KEY": "", "HUNTER_API_KEY": "", "KEY_CHECK": "warn"}
        patches = [mock.patch.dict(os.environ, env, clear=True), mock.patch("requests.get", side_effect=fake_get),
                   mock.patch("requests.post", side_effect=fake_post), mock.patch("requests.request", side_effect=fake_request),
                   mock.patch.object(LB, "ENRICH_WEBSITES", False), mock.patch.object(O, "mx_check", return_value="mx-ok"),
                   mock.patch.object(LB.time, "sleep"), mock.patch.object(E.time, "sleep")]
        for p in patches:
            p.start()
        try:
            with redirect_stdout(io.StringIO()):
                LB.main(local=True, setup_only=True)

            def edit(tab, fn):
                path = os.path.join(d, tab + ".csv")
                with open(path, encoding="utf-8") as fh:
                    rows = list(csv.reader(fh))
                fn(rows)
                with open(path, "w", newline="", encoding="utf-8") as fh:
                    csv.writer(fh).writerows(rows)

            def only(col_key, value):
                def f(rows):
                    k, e = rows[0].index(col_key), rows[0].index("Enabled (Yes/No)")
                    for r in rows[1:]:
                        r[e] = "Yes" if r[k] == value else "No"
                return f
            edit("Niches", only("Niche", "Fine Dining & Restaurant Groups (Upscale / Multi-venue)"))
            edit("Locations", only("Search Area", "Sydney NSW"))
            ov = {"MAX_SEARCH_REQUESTS_PER_RUN": "1", "SEGMENT_SCORING": "No", "REQUIRE_WEBSITE_OSM": "Yes"}
            edit("Rules", lambda rows: [r.__setitem__(1, ov[r[0]]) for r in rows if r and r[0] in ov])
            buf = io.StringIO()
            with redirect_stdout(buf):
                LB.main(local=True)
            tab = [f for f in os.listdir(d) if f.startswith("Fine Dining")]
            self.assertEqual(len(tab), 1, buf.getvalue())
            with open(os.path.join(d, tab[0]), encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
            self.assertEqual(rows[0][:len(D.LEAD_H)], D.LEAD_H)
            self.assertGreater(len(rows), 1, buf.getvalue())
            L = dict(zip(rows[0], rows[1]))
            self.assertEqual(L["Website"], "https://bistrolune.com.au/")                 # found by the website finder
            self.assertEqual((L["Google Rating"], L["Review Count"]), ("4.7", "212"))    # Serper Google rating
            self.assertEqual(L["Map Url"], "https://maps.google.com/?cid=4242")
            self.assertEqual(L["ABN"], "51 824 753 556")
            self.assertEqual(L["ABR Entity Name"], "LUNE HOSPITALITY PTY LTD")
            self.assertEqual(L["GST Registered"], "Yes, since 2015-03-01")
            self.assertGreater(float(L["Business Age (yrs)"]), 10)
            self.assertEqual((L["Phone Type"], L["AU Timezone"]), ("Landline", "Australia/Sydney"))
            self.assertEqual((L["Decision Maker Full Name"], L["Personal Email"]), ("Amy Chen", "amy@bistrolune.com.au"))
            self.assertIn("website via search", L["Enrichment"])
            self.assertIn("google-rating (serper)", L["Enrichment"])
            self.assertEqual(L["Country"], "Australia")
        finally:
            for p in reversed(patches):
                p.stop()
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
