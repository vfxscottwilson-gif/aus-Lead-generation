"""AU scope, niche rules, legacy parsers, importer, historical headers and mocked full pipeline.
Offline (every HTTP call is mocked). Run: python -m unittest discover -s tests -v"""
import csv
import io
import json
import os
import shutil
import sys
import tempfile

# Resolve the OS temp root before fixtures clear environment variables. Otherwise
# tempfile may cache the repository cwd, which private-export guards correctly reject.
_TEST_TEMP_ROOT = tempfile.gettempdir()
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ["KEY_CHECK"] = "warn"    # fake test keys/ids have no real shape
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
# Country eligibility is always Australian, regardless of ambient LEAD_MARKET.
if "defaults" not in sys.modules:
    os.environ["LEAD_MARKET"] = "AU"

import defaults as D          # noqa: E402
import enrich as E            # noqa: E402
import importer as IM         # noqa: E402
import leadbot as LB          # noqa: E402
import localsheet as LS       # noqa: E402
import optional as O          # noqa: E402
import osm as OSM             # noqa: E402
import yt as YTM              # noqa: E402

S0 = LB.Settings({k: str(v) for k, v, _ in D.SETTINGS})
V3_ORDER = ['Niche', 'Group', 'ICP Score /10', 'Tier', 'Company Name', 'Decision Maker Name', 'First Name', 'Decision Maker Role', 'Name Source', 'Personal Email', 'Generic Email', 'Other Emails', 'Email Verified', 'Phone', 'Instagram', 'Facebook', 'LinkedIn', 'TikTok', 'YouTube', 'Twitter / X', 'Other Socials', 'Website', 'Full Address', 'Suburb', 'City', 'State', 'Postcode', 'Country', 'Map Url', 'Case Study', 'Suggested Pitch /mo', 'Pitch Angle', 'Social Gap', 'Email Subject', 'Email Draft', 'Website Summary', 'Ad / Spend Signals', 'Google Rating', 'Review Count', 'Employee Count', 'Business Type', 'YT Subscribers', 'YT Uploads 30d', 'IG Followers', 'IG Engagement %', 'IG Posts 30d', 'Email Source', 'Email Type', 'Notes', 'Enrichment', 'Data Source', 'Found On', 'Status', 'My Notes', 'Last Checked', 'Lead Added On', 'Score', 'Place Id', 'Pitch Currency', 'Country Code', 'YT Last Upload (days)']



def settings(**over):
    s = LB.Settings(S0)
    s.update({k: str(v) for k, v in over.items()})
    return s


class Resp:
    def __init__(self, status=200, text="", url="", headers=None, js=None):
        self.status_code, self.text, self.url = status, text, url
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}
        self._js = js
        self.encoding = "utf-8" if "charset=" in self.headers.get("content-type", "") else "ISO-8859-1"

    def json(self):
        if self._js is not None:
            return self._js
        return json.loads(self.text)

    def iter_content(self, n):
        yield self.text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ------------------------------------------------------------------ Australian scope
class TestAustraliaScope(unittest.TestCase):
    def test_country_info(self):
        self.assertEqual(D.country_info("Australia"), ("Australia", "AU", "AUD"))
        self.assertEqual(D.country_info("AU"), ("Australia", "AU", "AUD"))
        for foreign in ("UK", "USA", "United Arab Emirates"):
            self.assertNotEqual(D.country_info(foreign)[1], "AU")
            self.assertEqual(D.country_info(foreign)[2], "")

    def test_locations_cover_target_countries(self):
        codes = {D.country_info(l[0])[1] for l in D.LOCATIONS}
        self.assertEqual(codes, {"AU"})
        self.assertEqual({l[1] for l in D.LOCATIONS}, {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"})
        self.assertEqual(len({l[3].lower() for l in D.LOCATIONS}), len(D.LOCATIONS), "Search Area must be unique")
        for l in D.LOCATIONS:
            self.assertTrue(-90 <= l[6] <= 90 and -180 <= l[7] <= 180, l)

    def test_columns_contract(self):
        for column in ("Company Name", "Decision Maker Full Name", "Decision Maker Role", "Personal Email", "Generic Email", "Other Emails", "Email Sources", "Role Evidence", "Name Source", "ICP Score", "ICP Tier", "Budget Evidence", "Unique Lead ID", "Place Id"):
            self.assertIn(column, D.LEAD_H)
        self.assertEqual(D.LEAD_H[:6], ["Company Name", "Decision Maker Full Name", "Decision Maker Role", "Personal Email", "Generic Email", "Other Emails"])
        for obsolete in ("First Name", "Last Name", "Decision Maker Name", "Email Draft", "Email Subject"):
            self.assertNotIn(obsolete, D.LEAD_H)
        self.assertEqual(len(D.LEAD_H), len(set(D.LEAD_H)))

    def test_parse_address_uses_location_country(self):
        a = LB.parse_address({"addressComponents": []}, {"country": "Canada", "cc": "CA"})
        self.assertEqual((a["country"], a["cc"]), ("", ""))  # search location alone proves no address
        a = LB.parse_address({"addressComponents": [{"longText": "United Kingdom", "shortText": "GB", "types": ["country"]}]})
        self.assertEqual(a["cc"], "GB")

    def test_reject_other_country_and_brand_and_takeaway(self):
        ctx = {"names": LB.Counter(), "hosts": LB.Counter()}
        st = {"names_run": LB.Counter(), "hosts_run": LB.Counter()}
        S = settings()
        base = {"displayName": {"text": "Smith & Co"}, "websiteUri": "https://smith.co", "_nometrics": True, "_source": "OpenStreetMap"}
        a = LB.parse_address({"addressComponents": [{"longText": "United States", "shortText": "US", "types": ["country"]}]})
        self.assertEqual(LB.reject_reason(base, a, S, ctx, st, None, {"cc": "AU"}), "outside Australia")
        a = LB.parse_address({"addressComponents": [{"longText": "Australia", "shortText": "AU", "types": ["country"]}]})
        self.assertIsNone(LB.reject_reason(base, a, S, ctx, st, None, {"cc": "AU"}))
        self.assertIn("brand", LB.reject_reason(dict(base, _brand="Q123"), a, S, ctx, st, None, {"cc": "AU"}))
        self.assertEqual(LB.reject_reason(dict(base, _takeaway="only"), a, S, ctx, st, None, {"cc": "AU"}), "takeaway only")

    def test_name_block_words(self):
        w = LB.blocked_words(settings())
        self.assertTrue(LB.name_blocked("Fitness First Bondi Junction", w))
        self.assertTrue(LB.name_blocked("F45 Training Soho", w))
        self.assertFalse(LB.name_blocked("Iron Temple Strength Club", w))
        self.assertFalse(LB.name_blocked("Tammy Dentistry", w))
        self.assertFalse(LB.name_blocked("Delicious Bistro", ["deli"]))
        self.assertTrue(LB.name_blocked("Joe's Burgers", ["burger"]))                   # v2 blocked this ('my dentist')
        self.assertTrue(LB.name_blocked("McDonald’s Parramatta", w))
        nic = {"exclude": ["cafe", "pizza"]}
        self.assertTrue(LB.name_blocked("Bean Cafe", LB.blocked_words(settings(), nic)))
        self.assertFalse(LB.name_blocked("Pizzicato Fine Dining", ["pizza"]))     # word match, not substring
        re_ex = dict((n[0], n[10]) for n in D.NICHES)["Real Estate Agencies (Independent)"].split(",")
        self.assertTrue(LB.name_blocked("Compass", re_ex))
        self.assertFalse(LB.name_blocked("Evans Real Estate Investments", re_ex))

    def test_niche_rows_have_all_columns(self):
        for n in D.NICHES:
            self.assertEqual(len(n), len(D.NICHES_H), n[0])
        names = [n[0] for n in D.NICHES]
        self.assertIn("Boutique Fitness Studios & Gyms (Owner-operated)", names)
        self.assertIn("Fine Dining & Restaurant Groups (Upscale / Multi-venue)", names)
        self.assertEqual(len(names), len(set(names)))


class TestOSM(unittest.TestCase):
    def test_selectors_with_negation(self):
        f = OSM.parse_selector("amenity=restaurant + takeaway!=only + cuisine!~pizza|kebab")
        self.assertEqual(f, '["amenity"="restaurant"]["takeaway"!="only"]["cuisine"!~"pizza|kebab",i]')
        self.assertEqual(OSM.parse_selector("leisure + name~pilates"), '["leisure"]["name"~"pilates",i]')
        self.assertEqual(OSM.parse_selector("bad selector!"), "")
        for n in D.NICHES:
            for sel in [x.strip() for x in n[9].split(";") if x.strip()]:
                self.assertTrue(OSM.parse_selector(sel), (n[0], sel))

    def test_bbox_query_and_retry_after_502(self):
        q = OSM.build_query(["amenity=dentist"], 51.5, -0.12, 4, 150)
        self.assertIn("[bbox:51.46396,", q)
        self.assertNotIn("around:", q)
        seq = [Resp(status=502), Resp(js={"elements": [{"type": "node", "id": 1, "tags": {"name": "A"}}]})]
        loc = {"lat": 51.5, "lon": -0.12, "radius": 4, "country": "United Kingdom", "cc": "GB"}
        with mock.patch.object(OSM.requests, "post", side_effect=seq) as post, mock.patch.object(OSM.time, "sleep") as sl:
            out = OSM.search(["amenity=dentist"], loc)
        self.assertEqual(len(out), 1)
        self.assertEqual(post.call_args_list[0].args[0], post.call_args_list[1].args[0])   # retried the main server
        self.assertIn(mock.call(20), sl.call_args_list)
        with mock.patch.object(OSM.requests, "post", return_value=Resp(status=504)), mock.patch.object(OSM.time, "sleep"):
            with self.assertRaises(OSM.OSMUnavailable):
                OSM.search(["amenity=dentist"], loc)

    def test_to_place_country_from_location(self):
        el = {"type": "node", "id": 5, "tags": {"name": "Ember", "website": "ember.ie", "brand:wikidata": "Q1", "cuisine": "steak"}}
        p = OSM.to_place(el, {"country": "Ireland", "cc": "IE", "state": "Leinster"})
        self.assertIn("Ireland", p["formattedAddress"])
        self.assertEqual(p["addressComponents"][-1]["shortText"], "IE")
        self.assertEqual((p["_brand"], p["_cuisine"], p["websiteUri"]), ("Q1", "steak", "https://ember.ie"))

    def test_geocode_country_code(self):
        with mock.patch.object(OSM.requests, "get", return_value=Resp(js=[{"lat": "1.5", "lon": "2.5"}])) as g, \
                mock.patch.object(OSM.time, "sleep"):
            self.assertEqual(OSM.geocode("Dubai Marina", "AE"), (1.5, 2.5))
            self.assertEqual(g.call_args.kwargs["params"]["countrycodes"], "ae")
            OSM.geocode("Somewhere")
            self.assertNotIn("countrycodes", g.call_args.kwargs["params"])


class TestEnrich(unittest.TestCase):
    def test_jsonld_founder_in_graph(self):
        html = '<script type="application/ld+json">{"@graph":[{"@type":"Organization","founder":[{"@type":"Person","name":"Aoife Byrne"}]},' \
               '{"@type":"Person","name":"Liam Kelly","jobTitle":"Managing Director"},{"@type":"Person","name":"Joe Bloggs","jobTitle":"Chef"}]}</script>'
        ppl = {p[0]: p[1] for p in E.people_in(html, "", "Ember")}
        self.assertEqual(ppl.get("Aoife Byrne"), "Founder")
        self.assertEqual(ppl.get("Liam Kelly"), "Managing Director")
        self.assertNotIn("Joe Bloggs", ppl)

    def test_site_links(self):
        html = '<a href="/our-story">Story</a><a href="https://other.com/contact">x</a><a href="/menu">Menu</a>' \
               '<a href="/p/9">Meet the team</a><a href="mailto:a@b.com">mail</a><a href="/brochure.pdf">About pdf</a>'
        self.assertEqual(E.site_links(html, "https://ember.ie/", "ember.ie"), ["https://ember.ie/our-story", "https://ember.ie/p/9"])

    def test_fetch_decodes_utf8_without_charset(self):
        page = "<html><body>Seán Ó Brien, Owner</body></html>"
        r = Resp(text=page, headers={"content-type": "text/html"})
        with mock.patch.object(E, "allowed", return_value=True), mock.patch.object(E.requests, "get", return_value=r):
            self.assertIn("Seán", E.fetch("https://x.ie/"))

    def test_live_data_email_bugs(self):
        self.assertEqual(E.emails_in("eleanor@example.co.uk info@shop.co.uk test@sample.com"), ["info@shop.co.uk"])
        emails = ["jason@grillroom.com", "chris@grillroom.com", "info@grillroom.com"]
        personal, generic, others = E.classify_emails(emails, "grillroom.com", "Jason Reed")
        self.assertEqual((personal, generic), ("", "info@grillroom.com"))
        self.assertEqual(others, emails[:2])
        association = {"jason@grillroom.com": {"name": "Jason Reed", "source": "https://grillroom.com/team"}}
        self.assertEqual(E.classify_emails(emails, "grillroom.com", "Jason Reed", associations=association), ("jason@grillroom.com", "info@grillroom.com", ["chris@grillroom.com"]))
        p, _, others = E.classify_emails(["darden343@aol.com"], "spoonbread.com", "Norma Jean Darden")
        self.assertEqual((p, others), ("", ["darden343@aol.com"]))
        self.assertEqual(E.classify_emails(["westloop@trainmoment.com", "rivernorth@trainmoment.com"], "trainmoment.com")[0], "")

    def test_live_data_name_bugs(self):
        self.assertEqual([x[0] for x in E.people_in("", "Marina District Founder")], [])
        names = [x[0] for x in E.people_in("", "Officer Deborah Martineau, Director")]
        self.assertNotIn("Officer Deborah Martineau", names)

    def test_world_city_not_a_name(self):
        self.assertEqual(E.people_in("<p>Director, Dubai Marina</p>"), [])


# ------------------------------------------------------------------ YouTube (free reader + API chooser)
def yt_search_page(channels):
    items = [{"channelRenderer": {"channelId": cid, "title": {"simpleText": t},
                                  "subscriberCountText": {"simpleText": "@h"}, "videoCountText": {"simpleText": subs}}}
             for cid, t, subs in channels]
    data = {"contents": {"x": items}, "y": {"continuationEndpoint": {"continuationCommand": {"token": "TOK"}}}}
    return ('<script>var ytInitialData = ' + json.dumps(data) + ';</script>'
            '<script>ytcfg.set({"INNERTUBE_API_KEY":"PUBLICKEY","INNERTUBE_CLIENT_VERSION":"2.2026"})</script>')


def yt_about_page(cid, country, desc, subs="45.2K subscribers", videos="210 videos", links=()):
    a = {"aboutChannelViewModel": {"description": desc, "country": country, "subscriberCountText": subs,
                                   "videoCountText": videos, "channelId": cid,
                                   "links": [{"channelExternalLinkViewModel": {"title": {"content": "x"}, "link": {"content": l}}}
                                             for l in links]}}
    md = {"channelMetadataRenderer": {"title": "Coach " + cid, "description": desc}}
    return '<script>var ytInitialData = ' + json.dumps({"a": a, "m": md}) + ';</script>'


def yt_rss(days_list):
    now = datetime.now(timezone.utc)
    ent = "".join(f"<entry><published>{(now - timedelta(days=d)).isoformat()}</published></entry>" for d in days_list)
    return f'<feed xmlns="http://www.w3.org/2005/Atom"><published>2015-01-01T00:00:00+00:00</published>{ent}</feed>'


class TestYouTube(unittest.TestCase):
    def test_parse_count(self):
        self.assertEqual(YTM.parse_count("32.3K subscribers"), 32300)
        self.assertEqual(YTM.parse_count("2.63M subscribers"), 2630000)
        self.assertEqual(YTM.parse_count("1,234 subscribers"), 1234)
        self.assertEqual(YTM.parse_count("128 videos"), 128)
        self.assertIsNone(YTM.parse_count("no number"))

    def test_rss_ignores_channel_date(self):
        days, n30 = YTM.upload_stats(YTM.parse_rss(yt_rss([2, 9, 40])))
        self.assertEqual((days, n30), (2, 2))
        self.assertEqual(YTM.upload_stats(YTM.parse_rss(yt_rss([]))), (None, None))

    def _router(self, pages):
        def request(method, url, params=None, json=None, timeout=None):
            for key, resp in pages.items():
                if key in url:
                    return resp() if callable(resp) else resp
            raise AssertionError("unexpected URL " + url)
        return request

    def test_free_reader_end_to_end(self):
        S = settings(YT_SEARCH_PAGES=1, YT_FREE_DELAY_SEC=1)
        pages = {
            "/results": Resp(text=yt_search_page([("UCaaa", "Coach A", "45.2K subscribers"),
                                                  ("UCbig", "Huge", "3.1M subscribers"),          # filtered before opening
                                                  ("UCbbb", "Coach B", "12K subscribers"),
                                                  ("UCseen", "Seen", "20K subscribers")])),
            "/channel/UCaaa/about": Resp(text=yt_about_page("UCaaa", "Australia",
                                                            "Strength coach. Business: coach@coacha.co.uk", links=["instagram.com/coacha", "coacha.co.uk"])),
            "/channel/UCbbb/about": Resp(text=yt_about_page("UCbbb", "India", "Hindi fitness")),     # country not in list
            "videos.xml": Resp(text=yt_rss([1, 5, 12, 20])),
        }
        f = YTM.YTFree(S)
        with mock.patch.object(f.http, "request", side_effect=self._router(pages)), mock.patch.object(YTM.time, "sleep"):
            out = f.search("strength coach", S, skip={"yt:UCseen"})
        self.assertEqual([p["id"] for p in out], ["yt:UCaaa"])
        p = out[0]
        self.assertEqual((p["_subs"], p["_uploads30"], p["_days"]), (45200, 4, 1))
        self.assertEqual(p["formattedAddress"], "Australia")
        self.assertEqual(p["_email"], "coach@coacha.co.uk")
        self.assertIn("Instagram", p["_socials"])
        self.assertEqual(p["websiteUri"], "https://coacha.co.uk")
        self.assertEqual(f.opened, 1)                                          # 12K subs < YT_MIN_SUBS 20K: never opened

    def test_free_reader_blocked_is_safe(self):
        S = settings()
        f = YTM.YTFree(S)
        with mock.patch.object(f.http, "request", return_value=Resp(status=429)), mock.patch.object(YTM.time, "sleep"):
            with self.assertRaises(YTM.YTUnavailable):
                f.search("x", S)
        consent = Resp(text="ok", url="https://consent.youtube.com/m?continue=")
        with mock.patch.object(f.http, "request", return_value=consent), mock.patch.object(YTM.time, "sleep"):
            with self.assertRaises(YTM.YTUnavailable):
                f.search("x", S)

    def test_source_uses_authorized_api_and_stops_when_unavailable(self):
        config = settings(YT_SOURCE="auto")
        with mock.patch.dict(os.environ, {"YOUTUBE_API_KEYS": "k1"}, clear=True):
            source = YTM.Source(config)
        self.assertEqual([item.label for item in source.chain], ["YouTube Data API"])
        with mock.patch.object(source.chain[0], "search", return_value=["api-result"]):
            self.assertEqual(source.search("business", config), ["api-result"])
        with mock.patch.object(source.chain[0], "search", side_effect=YTM.YTUnavailable("quota exhausted")):
            with self.assertRaises(YTM.YTUnavailable):
                source.search("business", config)
        self.assertEqual(source.chain, [])

    def test_source_without_key_requires_authorized_provider(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(YTM.YTFree, "search") as free:
            for mode in ("api", "auto", "free"):
                with self.assertRaisesRegex(YTM.YTUnavailable, "authorized"):
                    YTM.Source(settings(YT_SOURCE=mode))
            free.assert_not_called()

    def test_country_rule(self):
        for setting in ("Yes", "No"):
            config = settings(YT_ALLOW_UNKNOWN_COUNTRY=setting)
            self.assertTrue(YTM.country_ok("AU", "", config))
            for code in ("US", "IN", "", "NZ"):
                self.assertFalse(YTM.country_ok(code, "English Australian travel text", config))

# ------------------------------------------------------------------ importer
GOSOM_H = ("input_id,link,title,category,address,open_hours,popular_times,website,phone,plus_code,review_count,review_rating,"
           "reviews_per_rating,latitude,longitude,cid,status,descriptions,reviews_link,thumbnail,timezone,price_range,data_id,"
           "street_view_url,place_id,images,reservations,order_online,menu,owner,complete_address,credit_cards_accepted,about,"
           "user_reviews,user_reviews_extended,emails").split(",")


def gosom_row(**kw):
    r = {h: "" for h in GOSOM_H}
    r.update(kw)
    return [r[h] for h in GOSOM_H]


class TestImporter(unittest.TestCase):
    def setUp(self):
        self.niches = [{"niche": n[0], "group": n[1], "terms": [t.strip() for t in n[3].split(";")], "fit": n[4], "low": n[5],
                        "high": n[6], "tpl": n[7], "why": n[8], "osm": [], "exclude": [], "must": None, "reject": None, "chain": 0}
                       for n in D.NICHES]
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, rows):
        p = os.path.join(self.tmp, name)
        with open(p, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        return p

    def test_gosom_csv(self):
        ca = json.dumps({"street": "1 High St", "city": "Leeds", "postal_code": "LS1 1AA", "state": "", "country": "GB"})
        p = self.write("gosom.csv", [GOSOM_H, gosom_row(title="Iron Works Gym", category="Gym", website="ironworks.co.uk",
                                                         phone="+44 113 000", review_count="214", review_rating="4.8",
                                                         place_id="ChIJabcdefghijklmnopqrstuv", complete_address=ca,
                                                         emails="info@ironworks.co.uk, sam@ironworks.co.uk", status="",
                                                         link="https://www.google.com/maps/place/x"),
                                     gosom_row(title="Old Mill Bistro", category="Restaurant", status="Permanently closed"),
                                     gosom_row(title="")])
        recs = IM.read_file(p)
        self.assertEqual(len(recs), 2)
        out = IM.collect([("gosom.csv", recs)], self.niches)
        nic, loc, place, found = out[0]
        self.assertEqual(place["id"], "ChIJabcdefghijklmnopqrstuv")
        self.assertEqual(place["websiteUri"], "https://ironworks.co.uk")
        self.assertEqual((place["rating"], place["userRatingCount"], place["_nometrics"]), (4.8, 214, False))
        self.assertEqual(place["_email"], "info@ironworks.co.uk;sam@ironworks.co.uk")
        self.assertEqual((loc["cc"], loc["city"], loc["country"]), ("GB", "Leeds", "GB"))
        self.assertEqual(nic["niche"], "Boutique Fitness Studios & Gyms (Owner-operated)")
        self.assertEqual(found, "Import: gosom.csv")
        self.assertEqual(out[1][2]["businessStatus"], "CLOSED_PERMANENTLY")

    def test_gosom_json_lines(self):
        p = os.path.join(self.tmp, "Real Estate Agencies (Independent)__run1.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"title": "Harbour Realty", "web_site": "https://harbourrealty.co.nz", "longtitude": 174.7,
                                 "latitude": -36.8, "review_count": 51, "review_rating": 4.9, "cid": "123",
                                 "complete_address": {"city": "Auckland", "country": "NZ"}, "emails": ["sales@harbourrealty.co.nz"]}) + "\n")
            fh.write(json.dumps({"title": "Second", "web_site": "", "cid": "456"}) + "\n")
        out = IM.collect([(p, IM.read_file(p))], self.niches)
        self.assertEqual(len(out), 2)
        nic, loc, place, _ = out[0]
        self.assertEqual(nic["niche"], "Real Estate Agencies (Independent)")     # from the file name
        self.assertEqual((place["id"], loc["cc"]), ("gmapscid:123", "NZ"))
        self.assertEqual(place["_email"], "sales@harbourrealty.co.nz")

    def test_ytdlp_csv(self):
        p = self.write("yt_leads.csv", [["title", "website", "email", "subscribers", "channel_id", "latest_video", "source_query", "description"],
                                        ["Total Body Training", "https://www.youtube.com/@TotalBodyTraining", "tbt@tbt.com", "5580",
                                         "UCxxxxxxxxxxxxxxxxxxxxxx", (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y%m%d"),
                                         "personal trainer", "Online fitness coach. Site: https://tbt.com"]])
        nic, loc, place, _ = IM.collect([(p, IM.read_file(p))], self.niches)[0]
        self.assertEqual(place["_source"], "YouTube")
        self.assertEqual(place["id"], "yt:UCxxxxxxxxxxxxxxxxxxxxxx")
        self.assertEqual((place["_subs"], place["_days"]), (5580, 3))
        self.assertEqual(place["websiteUri"], "https://tbt.com")
        self.assertEqual(nic["group"], "YouTube")

    def test_generic_aliases_and_default_niche(self):
        recs = IM.tab_records([["Business Name", "Phone Number", "Site", "Stars", "Reviews", "Email Address", "Town", "Country"],
                               ["Zeta Ltd", "123", "zeta.ae", "4.1", "33", "hello@zeta.ae", "Dubai", "UAE"]])
        nic, loc, place, found = IM.collect([("Import tab", recs)], self.niches, "My Niche")[0]
        self.assertEqual((place["websiteUri"], place["rating"], loc["cc"], nic["niche"]), ("https://zeta.ae", 4.1, "", "My Niche"))
        self.assertEqual(IM.tab_records([[D.IMPORT_H[0]]]), [])

    def test_upscale_price(self):
        self.assertTrue(IM.upscale_price("$$$"))
        self.assertTrue(IM.upscale_price("£80–120"))
        self.assertFalse(IM.upscale_price("$$"))
        self.assertFalse(IM.upscale_price(""))

    def test_broken_file_is_safe(self):
        p = os.path.join(self.tmp, "bad.json")
        with open(p, "w") as fh:
            fh.write("{not json")
        self.assertEqual(IM.read_file(p), [])


# ------------------------------------------------------------------ local sheet + migrations
class TestSheet(unittest.TestCase):
    def test_rename_pitch_column_in_place(self):
        sh = LS.LocalSheet()
        ws = sh.add_worksheet("Dentists")
        old = [h if h != "Suggested Pitch /mo" else "Suggested Pitch AUD/mo" for h in V3_ORDER[:58]]
        ws.update(range_name="A1", values=[old])
        _, hdr = LB.get_tab(sh, "Dentists", D.LEAD_H)
        self.assertEqual(hdr[:len(old)], [h if h != "Suggested Pitch AUD/mo" else "Suggested Pitch /mo" for h in old])  # in place
        self.assertTrue(set(D.LEAD_H) <= set(hdr))   # new columns appended, nothing moved
        self.assertEqual(ws.row_values(1), hdr)

    def test_old_v2_tab_is_found_by_header_and_counted(self):
        sh = LS.LocalSheet()
        ws = sh.add_worksheet("Mortgage Brokers")
        ws.update(range_name="A1", values=[[h if h != "Suggested Pitch /mo" else "Suggested Pitch AUD/mo" for h in V3_ORDER[:58]]])
        legacy = {"Niche": "Mortgage Brokers", "Company Name": "Example Broker", "Place Id": "osm:node/9", "Country": "Australia"}
        ws.append_rows([[legacy.get(header, "") for header in V3_ORDER[:58]]])
        book = LB.scan_lead_tabs(sh)                       # v5: lead tabs are found by header, old column order is fine
        self.assertEqual((book["Mortgage Brokers"]["total"], book["Mortgage Brokers"]["last"]), (1, 2))
        self.assertEqual(book["Mortgage Brokers"]["pids"], {"osm:node/9"})

    def test_places_monthly_counter(self):
        sh = LS.LocalSheet()
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        ws, _ = LB.get_tab(sh, "Searches", D.SEARCH_H)
        ws.append_rows([["a|x", month + "-02", "2", "40", "3", "Google Places"], ["b|y", month + "-03", "1", "9", "0", "OpenStreetMap"],
                        ["c|z", "2001-01-01", "3", "50", "1", "Google Places"]])
        self.assertEqual(LB.load_seen(sh, settings())[-2], 2)

    def test_places_region_and_fields(self):
        import places as PL
        with mock.patch.object(PL.requests, "post", return_value=Resp(js={"places": []})) as post:
            PL.Places(["k"]).search("dentist in Leeds", None, "GB")
        body, hdrs = post.call_args.kwargs["json"], post.call_args.kwargs["headers"]
        self.assertEqual(body["regionCode"], "GB")
        self.assertIn("places.priceLevel", hdrs["X-Goog-FieldMask"])

    def test_rows_for_follows_tab_order(self):
        hdr = ["Search Area", "Country", "Extra"]
        self.assertEqual(LB.rows_for(hdr, ["Country", "Search Area"], [("UK", "Leeds")]), [["Leeds", "UK", ""]])


# ------------------------------------------------------------------ full pipeline with mocked network
OVERPASS = {"elements": [
    {"type": "node", "id": 1, "tags": {"name": "Lumiere", "amenity": "restaurant", "website": "https://lumiere.com.au", "phone": "+44 20 1"}},
    {"type": "node", "id": 2, "tags": {"name": "Joe's Grill", "amenity": "restaurant", "website": "https://joesgrill.com.au"}},
    {"type": "node", "id": 3, "tags": {"name": "Bean Cafe", "amenity": "restaurant", "website": "https://beancafe.com.au"}},
    {"type": "node", "id": 4, "tags": {"name": "Golden Wok", "amenity": "restaurant", "takeaway": "only", "website": "https://wok.com.au"}},
    {"type": "node", "id": 5, "tags": {"name": "Ember Steakhouse", "amenity": "restaurant", "brand:wikidata": "Q9", "website": "https://ember.com.au"}},
]}
SITES = {
    "https://lumiere.com.au": '<html><head><script>fbq("init")</script></head><body><h1>Lumiere</h1><p>Our seasonal tasting menu '
                             'with sommelier wine pairing.</p><a href="/about">About us</a><a href="/contact">Contact</a>'
                             '<a href="https://www.instagram.com/lumiere_sydney/">IG</a></body></html>',
    "https://lumiere.com.au/about": "<html><body><p>James Whitfield, Owner</p><a href=\"mailto:james@lumiere.com.au\">James Whitfield</a><p>Head chef since 2015.</p></body></html>",
    "https://lumiere.com.au/contact": '<html><body><a href="mailto:james@lumiere.com.au">james@lumiere.com.au</a> '
                                     'reservations@lumiere.com.au <a href="tel:+442012345678">call</a></body></html>',
    "https://joesgrill.com.au": "<html><body><p>Burgers and ribs. hello@joesgrill.com.au</p>"
                               '<a href="https://www.instagram.com/joesgrill/">IG</a></body></html>',
}


def fake_get(url, *a, **kw):
    if url.endswith("/robots.txt"):
        return Resp(status=404, text="")
    u = url.rstrip("/")
    if u in SITES:
        return Resp(text=SITES[u], url=url)
    return Resp(status=404, text="", url=url)


def fake_post(url, *a, **kw):
    if "overpass" in url or "interpreter" in url:
        return Resp(js=OVERPASS, text=json.dumps(OVERPASS))
    raise AssertionError("unexpected POST " + url)


def no_network(*a, **kw):
    raise AssertionError("real network call attempted")


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"LOCAL_SHEET_DIR": self.dir, "SOURCES": "maps,imports", "REGIONS": "AU",
                                                "AI_API_KEYS": "", "PLACES_API_KEYS": "", "YOUTUBE_API_KEYS": "",
                                                "HUNTER_API_KEY": "", "USE_JINA": "false", "KEY_CHECK": "warn"}, clear=True)
        self.env.start()
        # every module shares ONE requests module: one router for get (websites), post (Overpass), request (APIs)
        self.patches = [mock.patch("requests.get", side_effect=fake_get),
                        mock.patch("requests.post", side_effect=fake_post),
                        mock.patch("requests.request", side_effect=no_network),
                        mock.patch.object(LB, "ENRICH_WEBSITES", False),
                        mock.patch.object(O, "mx_check", return_value="mx-ok"),
                        mock.patch.object(LB.time, "sleep"), mock.patch.object(E.time, "sleep"),
                        mock.patch.object(LB.random, "shuffle", lambda x: None)]
        for p in self.patches:
            p.start()
        E._robots.clear()
        with redirect_stdout(io.StringIO()):
            LB.main(local=True, setup_only=True)
        # only the restaurant niche + one London location, small caps
        self.rules({"MAX_SEARCH_REQUESTS_PER_RUN": "1", "IMPORT_FOLDER": os.path.join(self.dir, "imports"),
                    "SEGMENT_SCORING": "No"})                  # these tests cover the v3 filters; v4 has test_v4.py
        self.only_niche("Fine Dining & Restaurant Groups (Upscale / Multi-venue)")
        self.only_location("Sydney NSW")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _edit(self, tab, fn):
        path = os.path.join(self.dir, tab + ".csv")
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        fn(rows)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)

    def rules(self, over):
        self._edit("Rules", lambda rows: [r.__setitem__(1, over[r[0]]) for r in rows if r and r[0] in over])

    def only_niche(self, name):
        def f(rows):
            i = rows[0].index("Enabled (Yes/No)")
            for r in rows[1:]:
                r[i] = "Yes" if r[0] == name else "No"
        self._edit("Niches", f)

    def only_location(self, area):
        def f(rows):
            a, e = rows[0].index("Search Area"), rows[0].index("Enabled (Yes/No)")
            for r in rows[1:]:
                r[e] = "Yes" if r[a] == area else "No"
        self._edit("Locations", f)

    def tab(self, name):
        with open(os.path.join(self.dir, name + ".csv"), encoding="utf-8") as fh:
            return list(csv.reader(fh))

    def test_dry_run_writes_no_leads(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            LB.main(dry=True, local=True)
        out = buf.getvalue()
        self.assertIn("Completed:", out)
        self.assertNotIn("james@", out)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "Fine Dining & Restaurant Groups (Upscale   Multi-venue).csv")))
        self.assertEqual(len(self.tab("Seen")), 1)          # dry = nothing written

    def test_full_run_filters_and_columns(self):
        with redirect_stdout(io.StringIO()):
            LB.main(local=True)
            LB.main(local=True, finish_only=True)
        lead_tab = [f for f in os.listdir(self.dir) if f.startswith("Fine Dining")]
        self.assertEqual(len(lead_tab), 1)
        rows = self.tab(lead_tab[0][:-4])
        hdr, leads = rows[0], rows[1:]
        self.assertEqual(hdr[:len(D.LEAD_H)], D.LEAD_H)
        self.assertEqual(len(leads), 1, leads)
        L = dict(zip(hdr, leads[0]))
        self.assertEqual(L["Company Name"], "Lumiere")
        self.assertEqual(L["Decision Maker Full Name"], "James Whitfield")        # fact from /about
        self.assertEqual(L["Personal Email"], "james@lumiere.com.au")
        self.assertEqual(L["Generic Email"], "reservations@lumiere.com.au")
        self.assertEqual((L["Country"], L["Country Code"], L["Pitch Currency"]), ("Australia", "AU", "AUD"))
        self.assertEqual(L["ICP Tier"].split()[0], "C")
        self.assertIn("opportunity", L["Research Reasons"].lower())
        self.assertTrue(500 <= int(L["Suggested Pitch /mo"]) <= 1000)
        self.assertTrue(L["Instagram"].startswith("https://www.instagram.com/lumiere_sydney"))
        self.assertNotIn("Email Draft", L)                                  # v5: no drafts in the lead bot
        self.assertTrue(L["Lead Insight"].startswith("Lumiere"), L["Lead Insight"])
        self.assertTrue(2 <= len(L["Lead Insight"].split("\n")) <= 4, L["Lead Insight"])
        seen = {r[0]: r[5] for r in self.tab("Seen")[1:]}
        self.assertEqual(seen["osm:node/1"], "Added")
        self.assertIn("not upscale", seen["osm:node/2"])
        self.assertIn("blocked name", seen["osm:node/3"])
        self.assertEqual(seen["osm:node/4"], "Rejected: takeaway only")
        self.assertIn("brand", seen["osm:node/5"])
        self.assertEqual(self.tab("Searches")[1][5], "OpenStreetMap")
        self.assertIn("All Leads.csv", os.listdir(self.dir))              # v5: no rebuilt views
        self.assertTrue(self.tab("Stats")[0][0].startswith("LEAD STATS"))
        meta = {r[0]: r[1] for r in self.tab("Meta")[1:] if r}
        self.assertEqual(meta["lock"], "")                                   # lock released after the run
        self.assertIn('"total":1', meta["summary"])
        # the draft bot writes Status back by Place Id; a later run must keep it (append-only, nothing rewritten)
        name = lead_tab[0][:-4]

        def drafted(rows):
            rows[1][rows[0].index("Status")] = "Drafted"
            rows[1][rows[0].index("My Notes")] = "call Tue"
        self._edit(name, drafted)
        with redirect_stdout(io.StringIO()):
            LB.main(local=True)
        rows = self.tab(name)
        self.assertEqual(len(rows), 2)                                         # nothing re-added
        L2 = dict(zip(rows[0], rows[1]))
        self.assertEqual((L2["Status"], L2["My Notes"], L2["Place Id"]), ("Drafted", "call Tue", "osm:node/1"))
        self.assertTrue({"Ready For Review", "Approved", "Do Not Contact", "Follow Up 4"} <= set(D.STATUS_LIST))

    def test_missing_website_still_attempts_enrichment_under_research_defaults(self):
        from copy import deepcopy
        places = deepcopy(OVERPASS)
        places["elements"] = places["elements"][:1]
        places["elements"][0]["tags"].pop("website")
        self.rules({"REQUIRE_WEBSITE_OSM": "No"})
        with mock.patch("requests.post", return_value=Resp(js=places)), mock.patch.object(LB, "find_website", return_value="https://lumiere.com.au") as finder, redirect_stdout(io.StringIO()):
            LB.main(local=True)
        finder.assert_called_once()
        leads = self.tab("Research Queue")
        self.assertGreater(len(leads), 1)
        record = dict(zip(leads[0], leads[1]))
        self.assertEqual(record["Decision Maker Full Name"], "James Whitfield")
        self.assertEqual(record["Personal Email"], "james@lumiere.com.au")

    def test_import_folder_goes_through_pipeline(self):
        os.makedirs(os.path.join(self.dir, "imports"))
        with open(os.path.join(self.dir, "imports", "gosom.csv"), "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows([GOSOM_H, gosom_row(title="Lumiere", category="Fine dining restaurant",
                                                         website="https://lumiere.com.au", review_count="140", review_rating="4.7",
                                                         place_id="ChIJlumiere0000000000000000",
                                                         complete_address=json.dumps({"city": "Sydney", "country": "AU"}))])
        self.rules({"MAX_SEARCH_REQUESTS_PER_RUN": "0"})
        with redirect_stdout(io.StringIO()):
            LB.main(local=True)
        seen = {r[0]: r[5] for r in self.tab("Seen")[1:]}
        self.assertEqual(seen.get("ChIJlumiere0000000000000000"), "Added")
        lead_tab = [f for f in os.listdir(self.dir) if f.startswith("Fine Dining")][0][:-4]
        L = dict(zip(self.tab(lead_tab)[0], self.tab(lead_tab)[1]))
        self.assertEqual((L["Review Count"], L["Google Rating"], L["Found On"]), ("140", "4.7", "Import: gosom.csv"))
        self.assertEqual(L["Data Source"], "Import: gosom.csv")


if __name__ == "__main__":
    unittest.main()
