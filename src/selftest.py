"""Offline self-test (no network, no sheet). The workflow runs it before the bot; a failure stops the run with a clear error.
Run locally: python src/selftest.py"""
import sys

import defaults as D
import enrich as E
import insight as IN
import keys as K
import leadbot as LB
import score as SC

S = LB.Settings({k: str(v) for k, v, _ in D.SETTINGS})
fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


# --- decision maker extraction: facts only
team = """<html><body><h3>Dr Sarah Mitchell</h3><p>Principal Dentist</p>
<p>Meet Our Team</p><h3>Book Now</h3><p>Director</p>
<p>James O'Brien, Founder</p><p>Founder and CEO Emma Clarke leads the studio.</p>
<script type="application/ld+json">{"@type":"Organization","founder":{"@type":"Person","name":"Liam Turner"}}</script>
</body></html>"""
ppl = E.people_in(team, None, "Smile Dental Sydney")
names = [p[0] for p in ppl]
check("finds name on line above role", "Dr Sarah Mitchell" in names)
check("finds 'Name, Founder'", "James O'Brien" in names)
check("finds 'Founder and CEO Name'", "Emma Clarke" in names)
check("finds JSON-LD founder", "Liam Turner" in names)
check("never takes 'Book Now' / 'Meet Our Team' as a person", not any(n in names for n in ("Book Now", "Meet Our Team", "Our Team")))
check("no people on a page without names", E.people_in("<p>Call our friendly team today. Owner operated since 1998.</p>") == [])

# --- email split
p, g, o = E.classify_emails(["info@smile.com.au", "sarah@smile.com.au", "accounts@smile.com.au"], "smile.com.au", "Dr Sarah Mitchell", associations={"sarah@smile.com.au": {"name": "Dr Sarah Mitchell", "source": "https://smile.com.au/team"}})
check("personal email = person's mailbox", p == "sarah@smile.com.au")
check("generic email = info@", g == "info@smile.com.au")
check("other emails keep the rest", o == ["accounts@smile.com.au"])
p, g, o = E.classify_emails(["hello@brand.com.au"], "brand.com.au", "")
check("generic only -> no personal", p == "" and g == "hello@brand.com.au")

# --- scoring gate / tiers / price
base = {"fit": 5, "reviews": None, "rating": None, "subs": None, "has_website": True, "personal_email": True,
        "generic_email": True, "email_status": "mx-ok", "instagram": True, "other_social": True, "phone": True,
        "decision_maker": True, "gap_sev": 0.5, "spend": 1, "priority": 1}
hi = SC.score(base, S)
lo = SC.score(dict(base, fit=3, personal_email=False, generic_email=False, decision_maker=False, instagram=False,
                   other_social=False, phone=False, spend=0), S)
check(f"strong lead passes 7/10 (got {SC.icp(hi)})", SC.icp(hi) >= 7)
check(f"weak lead fails 7/10 (got {SC.icp(lo)})", SC.icp(lo) < 7)
pr = SC.pitch_price(hi, 5, S)
check(f"business pitch inside 500-1000 (got {pr})", 500 <= pr <= 1000)
check("YouTube pitch inside 1000-2000", 1000 <= SC.pitch_price(hi, 5, S, youtube=True) <= 2000)
check("tier is A/B/C", SC.tier_of(hi, S) in "ABC")

# --- v5 Lead Insight without AI: 3-4 fact lines, never an email; AI person check stays verbatim-only
f = {"name": "Smile Dental", "city": "Sydney", "rating": "", "reviews": "", "gap": "no Instagram or Facebook found on their website",
     "niche": "Dentists", "group": "Medical", "first": "Dr Mitchell", "signals": ["Meta Pixel"], "subs": None, "youtube": False,
     "text": team, "segment": "GROWING"}
r = IN.build(None, f)
lines = r["insight"].split(chr(10))
check("fact-only Lead Insight has 3-4 lines", 3 <= len(lines) <= 4)
check("Lead Insight is not an email draft", not r["insight"].lower().startswith("hi ") and "Service hypothesis for review" in r["insight"])
check("AI person check rejects names not on page", IN.verify_person("John Fake", "Owner", team) is None)
check("AI person check accepts verbatim name + role", IN.verify_person("Emma Clarke", "CEO", team) is not None)


class _Quota:
    def generate(self, *a, **k):
        from ai import QuotaHit
        raise QuotaHit("429")


r = IN.build(_Quota(), f)
check("AI 429 -> quota flag + fact-only insight (no retry)", r["quota"] and 3 <= len(r["insight"].split(chr(10))) <= 4)

# --- v5 secrets registry: trimmed, shape-checked, masked; old combined secret still works
env = {"GEMINI_API_KEY_1": '  "AIza' + "A" * 35 + '"' + chr(10), "GROQ_API_KEY": "not-a-key", "AI_API_KEYS": "gsk_" + "b" * 52,
       "SHEET_ID_US": "https://docs.google.com/spreadsheets/d/" + "1" * 44 + "/edit#gid=0"}
rep = K.register(env, quiet=True)
check("quoted key trimmed + registered", env["GEMINI_API_KEY_1"] == "AIza" + "A" * 35)
check("bad-format key ignored (never sent)", env["GROQ_API_KEY"] == "" and ("ignored", "GROQ_API_KEY") in [(x[0], x[1]) for x in rep])
check("single + old combined AI keys merged", env["AI_API_KEYS"].split(chr(10)) == ["AIza" + "A" * 35, "gsk_" + "b" * 52])
check("sheet URL cut to its id", env["SHEET_ID_US"] == "1" * 44)
check("report masks values to last 4", all("A" * 8 not in str(x) for x in rep))
K.REPORT.clear()

# --- columns
for col in ("Company Name", "Decision Maker Full Name", "Personal Email", "Generic Email", "Other Emails", "Lead Insight",
            "Suggested Pitch /mo", "Pitch Currency", "ICP Score /10", "Score", "Place Id", "Lead Added On", "Status"):
    check(f"column present: {col}", col in D.LEAD_H)
check("no duplicate columns", len(D.LEAD_H) == len(set(D.LEAD_H)))
check("no email-draft columns (outreach bot drafts)", not set(D.OLD_LEAD_COLS) & set(D.LEAD_H))
check("Australia is the sole sheet target", D.MARKET == "AU" and D.SHEET_TARGETS == ["AU"])
check("accounts@ is generic, not personal",
      E.classify_emails(["accounts@smile.com.au"], "smile.com.au", "")[0] == "")
missing = [n[0] for n in D.NICHES if n[1] != "YouTube" and not n[9]]
check(f"map selectors for all non-YouTube niches {missing}", not missing)

# --- v4 segments (HIGH_QUALITY_LEADS_SPEC.md) with the Rules defaults
import os
import quality as Q
seg = lambda R, Sx, A: Q.segment(R, Sx, A, S)
check("segment PRIME at R 7 / S 4 / A 29", seg(7, 4, 29) == "PRIME")
check("segment PRIME-REVIEW at A 30-49", seg(7, 4, 30) == "PRIME-REVIEW" and seg(7, 4, 49) == "PRIME-REVIEW")
check("segment HAS-AGENCY at A >= 50", seg(9, 1, 50) == "HAS-AGENCY")
check("segment GROWING / POLISHED / REJECT", seg(6.9, 4, 0) == "GROWING" and seg(8, 6.6, 0) == "POLISHED-NO-SIGNAL"
      and seg(2.9, 1, 0) == "REJECT")
check("no HAS-AGENCY on cadence/stack alone", Q.a_score([("agency_stack", "x", ""), ("sched_tool", "y", "")],
                                                         {"posts30": 30, "cadence_cv": 0.1, "reels_share": 0.9},
                                                         dict(pstrong=12))[0] < 50)
for col in ("Segment", "Revenue Band", "Revenue Est. Basis", "Agency Name", "Agency Evidence", "YT Median Views"):
    check(f"column present: {col}", col in D.LEAD_H)

# --- v4 YouTube gates: subs 20k-500k, last upload <= 14 days, median views >= 20k
ctx, st = {"names": LB.Counter(), "hosts": LB.Counter()}, {"names_run": LB.Counter(), "hosts_run": LB.Counter()}
yt = lambda **kw: LB.reject_reason(dict({"displayName": {"text": "c"}, "_source": "YouTube", "_nometrics": True, "_subs": 50000, "_days": 5,
                                         "_views": 30000}, **kw), {"cc": "", "country": "", "suburb": "", "state": "", "postcode": ""}, S, ctx, st)
check("YouTube channel inside all gates passes", yt() is None)
check("YouTube < 20k subs rejected", yt(_subs=19000) is not None)
check("YouTube last upload > 14 days rejected", yt(_days=15) is not None)
check("YouTube median views < 20k rejected", yt(_views=19000) is not None)

# --- one Australian spreadsheet; country-specific keys cannot route writes.
_keep = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("SHEET_ID")}
try:
    os.environ["SHEET_ID_AU_ONLY"] = "obsolete"
    check("obsolete country sheet key ignored", LB.sheet_id_for() == "")
    os.environ["SHEET_ID"] = "configured"
    check("single SHEET_ID used", LB.sheet_id_for() == "configured" and D.SHEET_TARGETS == ["AU"])
    try:
        LB.sheet_id_for("US")
    except ValueError:
        check("foreign sheet target rejected", True)
    else:
        check("foreign sheet target rejected", False)
finally:
    for key in list(os.environ):
        if key.startswith("SHEET_ID"):
            os.environ.pop(key)
    os.environ.update(_keep)

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
