"""v5 tests: append-only writes, Place Id dedupe, header mapping, Stats/Meta/Master, secrets registry, Lead Insight +
AI 429 stop, hardening (deleted tab, 10M cells, lock, unicode, rate limits, timezones, API shape changes). No network."""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

os.environ["KEY_CHECK"] = "warn"
os.environ["LEAD_MARKET"] = "AU"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import ai as AIM               # noqa: E402
import defaults as D           # noqa: E402
import insight as IN           # noqa: E402
import keys as K               # noqa: E402
import leadbot as LB           # noqa: E402
import localsheet as LS        # noqa: E402

S = LB.Settings({k: str(v) for k, v, _ in D.SETTINGS})


def lead(pid, name="Acme Dental", **kw):
    d = {h: "" for h in D.LEAD_H}
    d.update({"Place Id": pid, "Company Name": name, "Niche": "Dentists", "Status": "New", "Segment": "PRIME",
              "Lead Added On": LB.today(S), "Lead Insight": "x"}, **kw)
    return d


def sheet_with_tab(title="Dentists", hdr=None, rows=()):
    sh = LS.LocalSheet()
    ws = sh.add_worksheet(title)
    ws.update(range_name="A1", values=[list(hdr or D.LEAD_H)] + [list(r) for r in rows])
    return sh, ws


class TestAppendOnly(unittest.TestCase):
    def setUp(self):
        LB._CACHE.update({"ws": {}, "vals": {}, "sh": None})

    def test_appends_below_last_row_and_never_touches_existing(self):
        hdr = list(D.LEAD_H)
        hdr.insert(0, hdr.pop(hdr.index("Company Name")))               # user moved a column
        hdr.append("My Call Date")                                       # user added a column
        old = {h: "" for h in hdr}
        old.update({"Company Name": "Old Co", "Place Id": "p1", "Status": "Replied", "My Notes": "call Tue"})
        typed = {h: "" for h in hdr}
        typed["My Call Date"] = "2026-11-05"                             # a row the user typed by hand (no Place Id)
        sh, ws = sheet_with_tab(hdr=hdr, rows=[[old[h] for h in hdr], [""] * len(hdr), [typed[h] for h in hdr]])
        before = [list(r) for r in ws.get_all_values()]
        book = LB.scan_lead_tabs(sh)
        n = LB.write_leads(sh, "Dentists", [lead("p2", "New Co"), lead("p1", "Dup")], book, S, {})
        self.assertEqual(n, 1)                                           # p1 already in the sheet: not written again
        rows = ws.get_all_values()
        self.assertEqual([r[:len(hdr)] for r in rows[:4]], [r[:len(hdr)] for r in before])   # nothing rewritten / moved
        new = dict(zip(rows[0], rows[4]))
        self.assertEqual((new["Company Name"], new["Place Id"], new["Status"]), ("New Co", "p2", "New"))  # by header name
        self.assertEqual(rows[0][0], "Company Name")

    def test_dedupe_across_tabs(self):
        sh, _ = sheet_with_tab("Gyms", rows=[[lead("shared")[h] for h in D.LEAD_H]])
        ws2 = sh.add_worksheet("Dentists")
        ws2.update(range_name="A1", values=[D.LEAD_H])
        book = LB.scan_lead_tabs(sh)
        book["_pids"] = set().union(*[v["pids"] for k, v in book.items()])
        self.assertEqual(LB.write_leads(sh, "Dentists", [lead("shared")], book, S, {}), 0)

    def test_retry_never_double_appends(self):
        sh, ws = sheet_with_tab()
        book = LB.scan_lead_tabs(sh)
        calls = {"n": 0}
        real = ws.update

        def flaky(*a, **k):                                             # first write "times out" after landing
            calls["n"] += 1
            real(*a, **k)
            if calls["n"] == 1:
                raise ConnectionError("Connection reset")
        with mock.patch.object(ws, "update", side_effect=flaky), mock.patch.object(LB.time, "sleep"):
            LB.write_leads(sh, "Dentists", [lead("p9")], book, S, {})
        self.assertEqual(len(ws.get_all_values()), 2)                    # explicit range: retried write lands once

    def test_deleted_tab_is_recreated(self):
        sh, ws = sheet_with_tab()
        cache = {}
        LB.get_lead_tab(sh, "Dentists", cache)
        sh.tabs.remove(ws)                                               # user deletes the tab mid-run
        book = {}
        self.assertEqual(LB.write_leads(sh, "Dentists", [lead("p3")], book, S, cache), 1)
        self.assertEqual(sh.worksheet("Dentists").get_all_values()[0][:3], D.LEAD_H[:3])

    def test_key_column_deleted_by_user_is_restored(self):
        hdr = [h for h in D.LEAD_H if h != "Place Id"]
        sh, ws = sheet_with_tab(hdr=hdr)
        LB.write_leads(sh, "Dentists", [lead("p4")], {}, S, {})
        rows = ws.get_all_values()
        self.assertEqual(rows[0][-1], "Place Id")
        self.assertEqual(dict(zip(rows[0], rows[1]))["Place Id"], "p4")

    def test_cell_limit_stops_writes(self):
        sh, ws = sheet_with_tab()
        ws.grid = 1                                                      # must grow the grid
        with mock.patch.object(LB, "cells_used", return_value=9_999_000):
            with self.assertRaises(LB.SheetFull):
                LB.write_leads(sh, "Dentists", [lead("p5")], {}, S, {})
        self.assertEqual(len(ws.get_all_values()), 1)

    def test_empty_sheet_scan(self):
        self.assertEqual(LB.scan_lead_tabs(LS.LocalSheet()), {})
        sh, ws = sheet_with_tab()
        ws.rows = []                                                      # header row wiped by the user
        self.assertEqual(LB.scan_lead_tabs(sh), {})
        self.assertEqual(LB.write_leads(sh, "Dentists", [lead("p6")], {}, S, {}), 1)
        self.assertEqual(ws.get_all_values()[0][:2], D.LEAD_H[:2])


class TestStatsMeta(unittest.TestCase):
    def setUp(self):
        LB._CACHE.update({"ws": {}, "vals": {}, "sh": None})

    def test_meta_and_stats_overwrite_in_place(self):
        sh, _ = sheet_with_tab(rows=[[lead("a")[h] for h in D.LEAD_H], [lead("b", Segment="GROWING")[h] for h in D.LEAD_H]])
        mws, meta = LB.read_meta(sh)
        book = LB.scan_lead_tabs(sh)
        summ = LB.write_stats(sh, "AU", book, meta, S, "now", "OK")
        self.assertEqual(summ["tabs"]["Dentists"], {"total": 2, "today": 2, "prime": 1, "segs": {
            "PRIME": 1, "PRIME-REVIEW": 0, "GROWING": 1, "HAS-AGENCY": 0, "POLISHED-NO-SIGNAL": 0}})
        LB.write_meta(sh, mws, meta)
        LB.write_meta(sh, mws, meta)
        self.assertEqual(len(sh.worksheet("Meta").get_all_values()), 1 + len(LB.META_KEYS))   # no new row per run
        self.assertTrue(sh.worksheet("Meta").hidden)
        st = sh.worksheet("Stats").get_all_values()
        self.assertEqual(sh.worksheets()[1].title, "Stats")                # tab 1
        self.assertEqual(st[0][0], "LEAD STATS: Australia")
        n = len(st)
        LB.write_block(sh, "Stats", [["short"]], meta["stats_rows"])      # a shorter block blanks the leftovers
        self.assertTrue(all(not any(r) for r in sh.worksheet("Stats").get_all_values()[1:n]))

    def test_foreign_target_cannot_route_to_another_sheet(self):
        with mock.patch.object(LB, "open_sheet") as open_sheet:
            with self.assertRaises(ValueError):
                LB.main(target="NZ")
        open_sheet.assert_not_called()

    def test_lock(self):
        now = LB.datetime.now(LB.timezone.utc).isoformat(timespec="seconds")
        self.assertTrue(LB.lock_held({"lock": "other|" + now}, "me", S))
        self.assertFalse(LB.lock_held({"lock": "me|" + now}, "me", S))
        self.assertFalse(LB.lock_held({"lock": "other|2020-01-01T00:00:00+00:00"}, "me", S))   # stale after a crash
        self.assertFalse(LB.lock_held({"lock": ""}, "me", S))


class TestKeys(unittest.TestCase):
    def test_registry(self):
        env = {"GOOGLE_PLACES_API_KEY_1": "AIza" + "p" * 35, "GOOGLE_PLACES_API_KEY_2": "AIza" + "q" * 35,
               "YOUTUBE_API_KEY_2": " AIza" + "y" * 35 + " ", "ABN_GUID": "1a2b3c4d-1111-2222-3333-444455556666",
               "HUNTER_API_KEY": "abc def", "GOOGLE_SA": "{not json", "SERPER_API_KEY": "a" * 40, "KEY_CHECK": ""}
        rep = K.register(env, quiet=True)
        self.assertEqual(env["PLACES_API_KEYS"].split("\n"), ["AIza" + "p" * 35, "AIza" + "q" * 35])
        self.assertEqual(env["YOUTUBE_API_KEYS"], "AIza" + "y" * 35)
        self.assertEqual(env["ABN_LOOKUP_GUID"], env["ABN_GUID"])
        st = {n: (s_, d) for s_, n, d in rep}
        self.assertEqual(st["HUNTER_API_KEY"][0], "ignored")
        self.assertEqual(st["GOOGLE_SA"][0], "ignored")
        self.assertEqual(st["SERPER_API_KEY"], ("registered", "(...aaaa)"))
        self.assertEqual(env["HUNTER_API_KEY"], "")
        env2 = {"GROQ_API_KEY": "new_format_key_123456789012345", "KEY_CHECK": "warn"}
        K.register(env2, quiet=True)
        self.assertEqual(env2["AI_API_KEYS"], "new_format_key_123456789012345")   # format changed: warn, keep
        K.REPORT.clear()

    def test_sheet_ids(self):
        with mock.patch.dict(os.environ, {"SHEET_ID": "https://docs.google.com/spreadsheets/d/abcDEF_123/edit", "SHEET_ID_AU_ONLY": "obsolete"}, clear=True):
            self.assertEqual(LB.sheet_id_for("AU"), "abcDEF_123")
            with self.assertRaises(ValueError):
                LB.sheet_id_for("QA")

class TestInsightAI(unittest.TestCase):
    F = {"name": "Glow Studio", "niche": "Boutique gyms", "group": "Fitness", "city": "Austin", "gap": "posts rarely",
         "signals": [], "text": "Owner Jane Roe runs Glow Studio."}

    def test_429_stops_without_spinning(self):
        r429 = mock.Mock(status_code=429)
        err = AIM.requests.HTTPError(response=r429)
        ai = AIM.AI.__new__(AIM.AI)
        ai.pool, ai.notes = [["groq", "k1", ["m1", "m2"], True], ["gemini", "k2", ["g1"], True]], []
        with mock.patch.object(ai, "_call", side_effect=err) as call:
            with self.assertRaises(AIM.QuotaHit):
                ai.generate("p")
        self.assertEqual(call.call_count, 2)                               # one try per key, no rounds, no sleeping
        out = IN.build(ai, self.F)
        self.assertTrue(out["quota"])
        self.assertIn("Glow Studio", out["insight"])

    def test_ai_reply_shape_changes_fall_back(self):
        for reply in ("not json at all", '{"insight": ["Line one", "Likely pain"]}', '{"insight": 5}', "[]"):
            ai = mock.Mock()
            ai.generate.return_value = reply
            out = IN.build(ai, self.F, want_person=True)
            self.assertTrue(out["insight"])
            self.assertIsNone(out["person"])
        ai.generate.return_value = '{"insight": "a", "decision_maker": {"name": "Jane Roe", "role": "Owner"}}'
        self.assertEqual(IN.build(ai, self.F, want_person=True)["person"][0], "Jane Roe")    # verbatim on the page
        ai.generate.return_value = '{"insight": "a", "decision_maker": {"name": "John Fake", "role": "Owner"}}'
        self.assertIsNone(IN.build(ai, self.F, want_person=True)["person"])


class TestHardening(unittest.TestCase):
    def test_unicode_and_cell_limits(self):
        self.assertEqual(LB.cell("Café\x00 ☕\x07"), "Café ☕")
        self.assertLessEqual(len(LB.cell("x" * 60000)), 49000)
        self.assertEqual(LB.tab_name("Cafés / Bars: 🍸 [Upscale]"), "Cafés Bars 🍸 Upscale")
        sh, ws = sheet_with_tab("Cafés ☕ & Bars")
        LB._CACHE.update({"ws": {}, "vals": {}, "sh": None})
        LB.write_leads(sh, "Cafés ☕ & Bars", [lead("u1", "Ünïcødé Café ☕")], {}, S, {})
        self.assertEqual(dict(zip(*ws.get_all_values()[:2]))["Company Name"], "Ünïcødé Café ☕")

    def test_rate_limit_backoff(self):
        resp = mock.Mock(status_code=429)
        resp.json.return_value = {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
        err = LB.gspread.exceptions.APIError(resp)
        fn = mock.Mock(side_effect=[err, err, "ok"])
        with mock.patch.object(LB.time, "sleep") as sl:
            self.assertEqual(LB.retry(fn), "ok")
        self.assertEqual(sl.call_count, 2)

    def test_timezone(self):
        self.assertRegex(LB.today(LB.Settings({"TIMEZONE": "Australia/Sydney"})), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(LB.today(LB.Settings({"TIMEZONE": "Not/AZone"})), LB.today(LB.Settings({"TIMEZONE": "UTC"})))

    def test_batch_values_tolerates_missing_ranges(self):
        sh = mock.Mock()
        sh.values_batch_get.return_value = {"valueRanges": [{"range": "a"}]}        # API returned fewer ranges
        self.assertEqual(LB.batch_values(sh, ["a", "b"]), [[], []])
        sh.values_batch_get.return_value = {}
        self.assertEqual(LB.batch_values(sh, ["a"]), [[]])

    def test_maps_item_shape_change(self):
        out = LB.google_places_from([{"title": "X Dental"}, {}, {"title": None, "rating": "n/a"}],
                                    {"city": "Austin", "country": "United States"}, "Google (Serper)")
        self.assertTrue(all(p.get("id") for p in out))

    def test_whatsapp_phone_links(self):
        import enrich as E
        html = ('<a href="tel:+61 2 9999 0000">c</a><a href="https://wa.me/971501234567">w</a>'
                '<a href="https://api.whatsapp.com/send?text=hi&amp;phone=966512345678">w</a><a href="https://wa.me/61299990000">x</a>')
        self.assertEqual(E.phones_in(html), ["+61299990000", "+971501234567", "+966512345678"])

    def test_concurrent_jobs_are_locked_out(self):
        sh = LS.LocalSheet()
        now = LB.datetime.now(LB.timezone.utc).isoformat(timespec="seconds")
        ws = sh.add_worksheet("Meta")
        ws.update(range_name="A1", values=[["Key", "Value"], ["lock", "other-job|" + now]])
        with mock.patch.object(LB, "open_sheet", return_value=sh), mock.patch.object(LB, "load_config",
                                                                                     return_value=(S, [], [], [], [])):
            buf = io.StringIO()
            with redirect_stdout(buf):
                LB.main(target="AU")
        self.assertIn("another job is writing this sheet", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
