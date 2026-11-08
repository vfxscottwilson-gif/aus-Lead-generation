"""AU scoring evidence, segment boundaries, agency detection, unknown revenue, YouTube gates,
single-sheet routing and mocked Australian pipeline. Run: python -m unittest discover -s tests -v"""
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["KEY_CHECK"] = "warn"    # fake test keys/ids have no real shape
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
if "defaults" not in sys.modules:
    os.environ["LEAD_MARKET"] = "AU"

import defaults as D          # noqa: E402
import enrich as E            # noqa: E402
import leadbot as LB          # noqa: E402
import optional as O          # noqa: E402
import quality as Q           # noqa: E402
import yt as YTM              # noqa: E402
from test_v3 import GOSOM_H, Resp, gosom_row, settings, yt_about_page, yt_search_page   # noqa: E402

S = settings()
QD = dict(zip(("rmin", "rstrong", "vel", "fweak", "fstrong", "pweak", "pstrong", "ticket", "visits", "rate"),
              Q.niche_defaults("Dentists (Cosmetic, Implants, Ortho)", "AU")))


class TestSegments(unittest.TestCase):
    def test_boundaries(self):
        seg = lambda R, S_, A: Q.segment(R, S_, A, S)
        self.assertEqual(seg(2.9, 1, 0), "REJECT")
        self.assertEqual(seg(9, 1, 50), "HAS-AGENCY")                  # A >= 50 wins over everything
        self.assertEqual(seg(7, 4, 29), "PRIME")                       # inclusive edges
        self.assertEqual(seg(7, 4, 30), "PRIME-REVIEW")
        self.assertEqual(seg(7, 4, 49), "PRIME-REVIEW")
        self.assertEqual(seg(6.9, 4, 0), "GROWING")
        self.assertEqual(seg(4, 6.5, 0), "GROWING")
        self.assertEqual(seg(7, 4.1, 0), "GROWING")                    # high R, middling social
        self.assertEqual(seg(8, 6.6, 0), "POLISHED-NO-SIGNAL")
        self.assertEqual(seg(3.5, 3, 0), "REJECT")                     # R 3-4 = low
        self.assertEqual(Q.segment(7, 4, 0, settings(PRIME_R_MIN=8)), "GROWING")   # thresholds come from Rules

    def test_r_score_unknowns_are_neutral(self):
        R, conf, _ = Q.r_score({}, QD, "AU", S)
        self.assertEqual((R, conf), (0.0, 0.0))
        R2, conf2, parts = Q.r_score({"reviews": 300, "locations": 3, "spend": 3, "booking": True, "employees": 25}, QD, "AU", S)
        self.assertGreater(R2, 7)
        self.assertEqual(parts["reviews"], 1.0)
        # Australian review calibration remains editable through Rules.
        self.assertGreater(Q.r_score({"reviews": 60}, QD, "AU", settings(REVIEW_FACTOR_AU=0.4))[0], Q.r_score({"reviews": 60}, QD, "AU", settings(REVIEW_FACTOR_AU=1.0))[0])

    def test_free_mode_renormalises(self):
        # no review count: only the present signals count (not 0, not a made-up 0.5)
        R, conf, parts = Q.r_score({"locations": 2, "spend": 2, "booking": True}, QD, "AU", S)
        self.assertEqual(sorted(parts), ["locations", "spend"])
        self.assertEqual(conf, 0.25)
        self.assertEqual(R, round(10 * (0.15 * 0.7 + 0.10 * 1.0) / 0.25, 1))       # 8.2
        self.assertLess(Q.r_score({"locations": 1, "spend": 0}, QD, "AU", S)[0], 3)   # one site, no spend, no niche -> weak
        # the niche budget fit is a present signal in free mode (measured: without it free mode gave 0 leads)
        R5, _, p5 = Q.r_score({"locations": 1, "spend": 0, "fit": 5}, QD, "AU", S)
        self.assertEqual(R5, round(10 * (0.15 * 0.3 + 0.30 * 1.0) / 0.55, 1))       # 6.3 -> GROWING, not REJECT
        self.assertEqual(Q.segment(R5, 4.0, 0, S), "GROWING")
        self.assertIn("niche_fit", p5)
        self.assertNotIn("niche_fit", Q.r_score({"reviews": 50, "fit": 5}, QD, "AU", S)[2])   # only without reviews
        self.assertEqual(Q.r_score({"locations": 1, "staff": 4}, QD, "AU", S)[2]["size"], 0.7)
        band, basis = Q.revenue_band({"locations": 2}, QD, 8.2, 0.25, S)
        self.assertEqual(band, "Unverified")
        self.assertIn("budget are unknown", basis)

    def test_revenue_band(self):
        for facts in ({"velocity": 6}, {"reviews": 100}):
            band, basis = Q.revenue_band(facts, QD, 8, 0.8, S)
            self.assertEqual(band, "Unverified")
            self.assertIn("Revenue and budget are unknown", basis)
        self.assertEqual(Q.revenue_band({}, QD, 0, 0, S), ("Unknown", "No verified financial capacity indicators"))

    def test_s_score(self):
        self.assertEqual(Q.s_score(None, {}, QD, S)[0], 10.0)
        self.assertEqual(Q.s_score(None, {"Instagram": "x"}, QD, S)[0], 10.0)
        strong = {"followers": 9000, "posts30": 15, "posts90": 40, "engagement": 3.5, "reels_share": 0.6}
        weak = {"followers": 400, "posts30": 1, "posts90": 2, "engagement": 0.3, "reels_share": 0}
        self.assertGreaterEqual(Q.s_score(strong, {"Instagram": "x"}, QD, S)[0], 9)
        self.assertEqual(Q.s_score(weak, {"Instagram": "x"}, QD, S)[0], 0.0)

    def test_agency_signals_and_cap(self):
        html = '<script src="https://link.msgsndr.com/js/form.js"></script><a href="https://later.com/x">IG</a>'
        sig = Q.site_agency_signals(html, "© 2026 Glow. Digital marketing by Bright Digital Co")
        A, ev, name = Q.a_score(sig, None, QD)
        self.assertEqual((A, name), (50, "Bright Digital Co"))
        self.assertIn("GoHighLevel", ev)
        # stack + cadence but no named credit -> capped below HAS-AGENCY
        igs = {"posts30": 20, "cadence_cv": 0.2, "reels_share": 0.7}
        A2, _, _ = Q.a_score(Q.site_agency_signals(html, ""), igs, QD)
        self.assertEqual(A2, 35)
        self.assertLess(Q.a_score([("agency_stack", "x", ""), ("pro_cadence", "y", ""), ("sched_tool", "z", "")] * 3, igs, QD)[0], 50)
        # website-builder credits are not agencies
        self.assertEqual(Q.site_agency_signals("", "Website by Squarespace"), [])
        self.assertEqual(Q.site_agency_signals("", "Website by Pixel Studio")[0][0], "web_credit")
        # found in the live run: names must not run across lines; '... Agency' in a web credit = an agency
        self.assertEqual(Q.site_agency_signals("", "Designed by Ovidio Villanueva\nAdvanced Search")[0][2], "Ovidio Villanueva")
        self.assertEqual(Q.site_agency_signals("", "Site by Vallure Agency")[0][:3:2], ("agency_credit", "Vallure Agency"))

    def test_ig_caption_credits(self):
        igs = {"captions": ["New smile! content by @lensworks", "x"], "bio": ""}
        self.assertEqual(Q.ig_agency_signals(igs)[0][2], "@lensworks")
        tagged = {"captions": ["@studio9 shoot"] * 3 + ["hi"], "bio": ""}
        self.assertEqual(Q.ig_agency_signals(tagged)[0][2], "@studio9")

    def test_icp3_weights_and_penalty(self):
        base = {"R": 8, "S": 2, "A": 0, "fit": 5, "contact": 1.0, "dm": True, "rating": 4.8, "priority": 1}
        self.assertEqual(Q.icp3(base, S), 88)
        self.assertEqual(Q.icp3(dict(base, A=40), S), 82)              # -(40/100) x 15
        self.assertEqual(Q.icp3(dict(base, A=60), S), 88)              # no penalty once HAS-AGENCY

    def test_cadence_and_median(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(Q.cadence_cv([now - timedelta(days=d) for d in (0, 7, 14, 21)]), 0.0)
        self.assertEqual(Q.median([10, 30, 20, None]), 20)

    def test_niche_thresholds_per_country(self):
        au = Q.niche_defaults("Dentists (Cosmetic, Implants, Ortho)", "AU")
        self.assertEqual((au[0], au[7]), (20, 321))                    # CommBank A$321 per visit
        self.assertEqual(Q.niche_defaults("Real Estate Agencies (Independent)", "US")[7], "")   # formula skipped


class TestYouTubeGates(unittest.TestCase):
    def reason(self, **kw):
        p = dict({"displayName": {"text": "Ch"}, "_source": "YouTube", "_nometrics": True, "_subs": 60000, "_days": 3,
                  "_views": 45000}, **kw)
        return LB.reject_reason(p, LB.parse_address(p), S, {"names": LB.Counter(), "hosts": LB.Counter()},
                                {"names_run": LB.Counter(), "hosts_run": LB.Counter()})

    def test_gates(self):
        self.assertIsNone(self.reason())
        self.assertIn("subscribers", self.reason(_subs=19999))
        self.assertIn("subscribers", self.reason(_subs=500001))
        self.assertIn("older than", self.reason(_days=15))
        self.assertIsNone(self.reason(_days=14))
        self.assertIn("median views", self.reason(_views=19999))
        self.assertIsNone(self.reason(_views=None))                    # unknown (import) is not a reject
        self.assertEqual((S.num("YT_MIN_SUBS"), S.num("YT_MAX_DAYS_SINCE_UPLOAD"), S.num("YT_MIN_VIEWS")), (20000, 14, 20000))

    def test_rss_views_gate_in_free_reader(self):
        now = datetime.now(timezone.utc)

        def rss(views):
            e = "".join(f'<entry><published>{(now - timedelta(days=i + 1)).isoformat()}</published><media:group>'
                        f'<media:community><media:statistics views="{v}"/></media:community></media:group></entry>'
                        for i, v in enumerate(views))
            return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">'
                    f'<published>2015-01-01T00:00:00+00:00</published>{e}</feed>')
        self.assertEqual(YTM.parse_rss_views(rss([5, 50000, 30000, 40000, 25000, 1]), 5), [5, 50000, 30000, 40000, 25000])
        pages = {"/results": Resp(text=yt_search_page([("UChi", "Hi", "80K subscribers"), ("UClo", "Lo", "80K subscribers")])),
                 "/channel/UChi/about": Resp(text=yt_about_page("UChi", "Australia", "Fitness coach", subs="80K subscribers")),
                 "/channel/UClo/about": Resp(text=yt_about_page("UClo", "Australia", "Fitness coach", subs="80K subscribers"))}
        feeds = {"UChi": rss([30000, 25000, 22000, 60000, 21000]), "UClo": rss([3000, 5000, 4000, 2000, 1000])}

        def request(method, url, params=None, json=None, timeout=None):
            if "videos.xml" in url:
                return Resp(text=feeds[params["channel_id"]])
            return next(v for k, v in pages.items() if k in url)
        S1 = settings(YT_SEARCH_PAGES=1)
        f = YTM.YTFree(S1)
        with mock.patch.object(f.http, "request", side_effect=request), mock.patch.object(YTM.time, "sleep"):
            out = f.search("fitness coach", S1)
        self.assertEqual([(p["id"], p["_views"]) for p in out], [("yt:UChi", 25000)])
        self.assertEqual(out[0]["formattedAddress"], "Australia")               # unknown country stays blank (allowed)

    def test_yt_editor_credit(self):
        self.assertEqual(Q.yt_segment("Business: a@b.com. Edited by @cutroom")[0], "HAS-AGENCY")
        self.assertEqual(Q.yt_segment("I film and edit everything myself")[0], "PRIME")


class TestSingleAustralianSheet(unittest.TestCase):
    def test_foreign_targets_rejected_before_authorization(self):
        with mock.patch.object(LB.gspread, "service_account_from_dict") as auth:
            for target in ("US", "GB", "NZ", "YOUTUBE"):
                with self.assertRaises(ValueError):
                    LB.main(target=target)
                with self.assertRaises(ValueError):
                    LB.sheet_id_for(target)
            auth.assert_not_called()

    def test_only_single_sheet_id_is_used(self):
        with mock.patch.dict(os.environ, {"SHEET_ID": "configured-id", "SHEET_ID_AU_ONLY": "obsolete-id"}, clear=True):
            self.assertEqual(LB.sheet_id_for(), "configured-id")
            self.assertEqual(LB.sheet_id_for("AU"), "configured-id")
        with mock.patch.dict(os.environ, {"SHEET_ID_AU_ONLY": "obsolete-id"}, clear=True):
            self.assertEqual(LB.sheet_id_for(), "")

    def test_all_alias_runs_once_and_propagates_failure(self):
        with mock.patch.object(LB, "main") as run:
            LB.cli(["--setup", "--all"])
        run.assert_called_once()
        self.assertTrue(run.call_args.kwargs["setup_only"])
        with mock.patch.object(LB, "main", side_effect=RuntimeError("write failed")):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                LB.cli(["--finish", "--all"])
        with self.assertRaises(SystemExit):
            LB.cli(["--country", "US"])

    def test_seeds_are_australia_only(self):
        locs, niches = D.seeds_for("AU")
        self.assertTrue(locs and all(row[0] == "Australia" for row in locs))
        self.assertTrue(niches)
        for target in ("NZ", "YOUTUBE", "US"):
            with self.assertRaises(ValueError):
                D.seeds_for(target)


# ------------------------------------------------------------------ full run: US sheet, gosom import, mocked network
SITES = {
    "https://brightsmile.com": '<html><head><script>gtag("config","AW-123456789");fbq("init")</script></head><body>'
                               '<a href="/about">About</a><a href="https://www.zocdoc.com/practice/x">Book</a>'
                               '<a href="https://www.instagram.com/brightsmile/">IG</a><footer>Website by Pixel Studio</footer></body></html>',
    "https://brightsmile.com/about": "<html><body><p>Dr Ana Ruiz, Owner</p><p><a href=\"mailto:ana@brightsmile.com\">Dr Ana Ruiz</a> info@brightsmile.com</p></body></html>",
    "https://glowdental.com": '<html><head><script src="https://link.msgsndr.com/js/f.js"></script><script>fbq("init");'
                              'gtag("config","AW-987654321")</script></head><body><a href="https://later.com/glow">links</a>'
                              '<a href="https://www.instagram.com/glowdental/">IG</a><p>hello@glowdental.com</p>'
                              '<footer>Digital marketing by Bright Digital Co</footer></body></html>',
}


def fake_get(url, *a, **kw):
    if url.endswith("/robots.txt"):
        return Resp(status=404)
    u = url.rstrip("/")
    return Resp(text=SITES[u], url=url) if u in SITES else Resp(status=404, url=url)


def no_network(*a, **kw):
    raise AssertionError("unexpected network call")


class TestCountryPipeline(unittest.TestCase):
    def test_au_import_dedupe_evidence_and_dashboard(self):
        d = tempfile.mkdtemp()
        imp = os.path.join(d, "imports")
        os.makedirs(imp)
        ca = lambda c: json.dumps({"city": c, "state": "NSW", "country": "AU"})
        rows = [GOSOM_H,
                gosom_row(title="Bright Smile Dental", category="Dentist", website="https://brightsmile.com", review_count="180",
                          review_rating="4.8", place_id="ChIJbright000000000000001", complete_address=ca("Sydney"), phone="+1 512 555 0100"),
                gosom_row(title="Bright Smile Dental North", category="Dentist", website="https://brightsmile.com", review_count="95",
                          review_rating="4.7", place_id="ChIJbright000000000000002", complete_address=ca("Sydney")),
                gosom_row(title="Glow Dental", category="Dentist", website="https://glowdental.com", review_count="400",
                          review_rating="4.9", place_id="ChIJglow0000000000000001", complete_address=ca("Sydney")),
                gosom_row(title="Kiwi Dental", category="Dentist", website="https://kiwi.co.nz", review_count="90",
                          place_id="ChIJkiwi0000000000000001", complete_address=json.dumps({"city": "Auckland", "country": "NZ"}))]
        with open(os.path.join(imp, "gosom.csv"), "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
        env = {"LOCAL_SHEET_DIR": d, "SOURCES": "", "REGIONS": "", "AI_API_KEYS": "", "PLACES_API_KEYS": "", "SERPER_API_KEY": "",
               "BRAVE_API_KEY": "", "HUNTER_API_KEY": "", "ZENROWS_API_KEY": "", "ABN_LOOKUP_GUID": "", "APOLLO_API_KEY": "",
               "IG_ACCESS_TOKEN": "", "KEY_CHECK": "warn"}
        patches = [mock.patch.dict(os.environ, env, clear=True), mock.patch("requests.get", side_effect=fake_get),
                   mock.patch("requests.post", side_effect=no_network), mock.patch("requests.request", side_effect=no_network),
                   mock.patch.object(LB, "ENRICH_WEBSITES", False), mock.patch.object(O, "mx_check", return_value="mx-ok"),
                   mock.patch.object(LB.time, "sleep"), mock.patch.object(E.time, "sleep")]
        for p in patches:
            p.start()
        try:
            E._robots.clear()
            with redirect_stdout(io.StringIO()):
                LB.cli(["--local", "--setup", "--all"])
            self.assertIn("Rules.csv", os.listdir(d))
            us = d

            def edit(tab, fn):
                path = os.path.join(us, tab + ".csv")
                with open(path, encoding="utf-8") as fh:
                    r = list(csv.reader(fh))
                fn(r)
                with open(path, "w", newline="", encoding="utf-8") as fh:
                    csv.writer(fh).writerows(r)
            ov = {"IMPORT_FOLDER": imp, "MAX_SEARCH_REQUESTS_PER_RUN": "0"}
            edit("Rules", lambda r: [x.__setitem__(1, ov[x[0]]) for x in r if x and x[0] in ov])
            buf = io.StringIO()
            with redirect_stdout(buf):
                LB.main(local=True, target="AU")
                LB.main(local=True, finish_only=True)
            files = os.listdir(us)
            tab = next(f for f in files if f.startswith("Dentists"))
            with open(os.path.join(us, tab), encoding="utf-8") as fh:
                got = list(csv.reader(fh))
            hdr, leads = got[0], [dict(zip(got[0], r)) for r in got[1:]]
            self.assertEqual(hdr, D.LEAD_H)
            self.assertEqual(sorted(l["Segment"] for l in leads), ["HAS-AGENCY", "POLISHED-NO-SIGNAL"], buf.getvalue())
            prime = next(l for l in leads if l["Company Name"] == "Bright Smile Dental")
            agency = next(l for l in leads if l["Segment"] == "HAS-AGENCY")
            self.assertEqual((prime["Company Name"], prime["Decision Maker Full Name"], prime["Personal Email"]),
                             ("Bright Smile Dental", "Dr Ana Ruiz", "ana@brightsmile.com"))
            self.assertEqual(prime["Locations (same website)"], "2")
            self.assertEqual(prime["Booking Tool"], "Zocdoc")
            self.assertIn(prime["Revenue Band"], ("Unknown", "Unverified"))
            self.assertIn("unknown", prime["Revenue Est. Basis"])
            self.assertEqual(prime["ICP Tier"].split()[0], "C")
            self.assertEqual((agency["Agency Name"], agency["Agency Score /100"]), ("Bright Digital Co", "50"))
            self.assertIn("GoHighLevel", agency["Agency Evidence"])
            self.assertEqual(agency["Country"], "Australia")
            # NZ row was not written to the US sheet
            with open(os.path.join(us, "Seen.csv"), encoding="utf-8") as fh:
                seen = {r[0]: r[5] for r in list(csv.reader(fh))[1:]}
            self.assertNotIn("ChIJkiwi0000000000000001", seen)
            self.assertIn(seen["ChIJbright000000000000002"], ("Rejected: same website already added",
                                                               "Rejected: duplicate email (already added)"))
            with open(os.path.join(us, "Snapshots.csv"), encoding="utf-8") as fh:
                self.assertGreaterEqual(len(list(csv.reader(fh))), 4)       # review counts stored for velocity later
            with open(os.path.join(us, "Stats.csv"), encoding="utf-8") as fh:
                dash = list(csv.reader(fh))
            self.assertTrue(dash[0][0].startswith("LEAD STATS: Australia"))
            row = next(r for r in dash if r[0].startswith("Dentists"))
            self.assertEqual(row[1:4], ["2", "2", "0"])                    # total, today, PRIME (values, not formulas)
            self.assertIn("SPARKLINE(", row[9])
            import localsheet as LS                                         # tab order: Stats, niche tabs, rest
            sh = LS.LocalSheet()
            for t in ("Rules", "Real Estate Agencies (Independent)", "All Leads", "Dentists (Cosmetic, Implants, Ortho)",
                      "Stats", "Boutique Fitness Studios & Gyms (Owner-operated)", "Seen"):
                sh.add_worksheet(t)
            lead = {t: (sh.worksheet(t), D.LEAD_H) for t in ("Real Estate Agencies (Independent)", "Dentists (Cosmetic, Implants, Ortho)",
                                                            "Boutique Fitness Studios & Gyms (Owner-operated)")}
            LB.order_tabs(sh, lead)
            self.assertEqual([w.title for w in sh.worksheets()],
                             ["Stats", "Dentists (Cosmetic, Implants, Ortho)", "Real Estate Agencies (Independent)",
                              "Boutique Fitness Studios & Gyms (Owner-operated)", "Rules", "All Leads", "Seen"])
        finally:
            for p in reversed(patches):
                p.stop()
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
