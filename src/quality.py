"""High-quality lead segmentation (HIGH_QUALITY_LEADS_SPEC.md v1).

Three sub-scores -> one segment -> final ICP /10:
  R  revenue proxy 0-10      (reviews, review velocity, locations, price level, ad/booking spend, size)
  S  social strength 0-10    (followers, posting cadence, engagement, Reels share)   low S = big gap = they need us
  A  agency/in-house 0-100   (footer credits, agency tracking stack, IG caption credits, pro cadence, in-house role)
Segments: PRIME / PRIME-REVIEW / GROWING / HAS-AGENCY / POLISHED-NO-SIGNAL (REJECT is never written).
Honesty: revenue is only ever a Low / Mid / High BAND with its basis; nothing here is a fact to quote to a prospect.
Every threshold is a Rules key (sheet) or a Niches column, so it can be recalibrated without code."""
import re
import statistics

SEGMENTS = ["PRIME", "PRIME-REVIEW", "GROWING", "HAS-AGENCY", "POLISHED-NO-SIGNAL"]
SEGMENT_RANK = {s: i + 1 for i, s in enumerate(SEGMENTS)}

# country review factor (heuristic, spec section 7): smaller markets get lower review thresholds
REVIEW_FACTOR = {"AU": 0.8}

# niche profiles (spec section 5): reviews min/strong, velocity strong /mo, followers weak/strong, posts30 weak/strong,
# visits per customer, review rate, avg ticket per country (local currency; ASSUMPTION where the spec says so)
PROFILES = {
    "dentist":    (20, 150, 6, 1000, 5000, 4, 12, 2, 0.03, {"AU": 321}),
    "medspa":     (25, 200, 8, 2000, 10000, 6, 15, 3, 0.03, {"AU": 450}),
    "clinic":     (20, 150, 5, 800, 4000, 4, 12, 4, 0.03, {"AU": 150}),
    "realestate": (15, 100, 4, 1500, 8000, 6, 16, 1, 0.05, {}),                 # spec: skip the revenue formula
    "gym":        (30, 200, 8, 1500, 8000, 8, 20, 8, 0.03, {"AU": 160}),
    "restaurant": (150, 1000, 30, 3000, 15000, 6, 15, 1, 0.02, {"AU": 90}),
    "default":    (20, 150, 5, 1000, 5000, 4, 12, 1, 0.03, {}),
}


def profile_for(niche):
    n = (niche or "").lower()
    if any(w in n for w in ("real estate", "buyers agent", "property")):
        return "realestate"
    if re.search(r"\bdent", n):                              # 'Independent' must not match
        return "dentist"
    if any(w in n for w in ("cosmetic", "plastic", "skin", "hair transplant", "aesthetic")):
        return "medspa"
    if any(w in n for w in ("specialist", "physio", "chiro", "veterin", "allied")):
        return "clinic"
    if any(w in n for w in ("real estate", "buyers agent", "property")):
        return "realestate"
    if any(w in n for w in ("gym", "fitness", "pilates", "yoga")):
        return "gym"
    if any(w in n for w in ("restaurant", "dining")):
        return "restaurant"
    return "default"


NICHE_Q_H = ["Reviews Min", "Reviews Strong", "Velocity Strong", "Followers Weak", "Followers Strong", "Posts30 Weak",
             "Posts30 Strong", "Avg Ticket", "Visits/Customer", "Review Rate"]


def niche_defaults(niche, cc):
    """Default values for the 10 Niches-tab quality columns (country-specific ticket; blank = formula skipped)."""
    rmin, rstr, vel, fw, fs, pw, ps, visits, rate, tickets = PROFILES[profile_for(niche)]
    return (rmin, rstr, vel, fw, fs, pw, ps, tickets.get("AU", "") if (cc or "AU").upper() == "AU" else "", visits, rate)


def _lin(x, lo, hi):
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _num(v, d=None):
    try:
        return float(str(v).replace(",", "")) if str(v).strip() not in ("", "None") else d
    except ValueError:
        return d


# ------------------------------------------------------------------ R: revenue proxy
PRICE_N = {"PRICE_LEVEL_INEXPENSIVE": 0.25, "PRICE_LEVEL_MODERATE": 0.5, "PRICE_LEVEL_EXPENSIVE": 0.75,
           "PRICE_LEVEL_VERY_EXPENSIVE": 1.0}


def r_score(f, q, cc, S):
    """f: reviews, velocity, locations, price_level, upscale, spend (signal count), booking (premium tool found),
    employees, gst. q: niche thresholds dict. Returns (R 0-10, confidence 0-1, parts dict)."""
    fac = S.num("REVIEW_FACTOR_AU", REVIEW_FACTOR["AU"])
    parts, known = {}, 0.0
    w = {"reviews": 0.30, "velocity": 0.25, "locations": 0.15, "price": 0.10, "spend": 0.10, "size": 0.10}
    if f.get("reviews") is not None:
        parts["reviews"] = _lin(f["reviews"], q["rmin"] * fac, q["rstrong"] * fac)
    if f.get("velocity") is not None:
        parts["velocity"] = _lin(f["velocity"], 0, q["vel"] * fac)
    if f.get("locations"):
        n = f["locations"]
        parts["locations"] = 0.3 if n <= 1 else 0.7 if n == 2 else 1.0
    if f.get("price_level") in PRICE_N:
        parts["price"] = PRICE_N[f["price_level"]]
    elif f.get("upscale"):
        parts["price"] = 0.75
    if f.get("spend") is not None:
        parts["spend"] = min(1.0, (f["spend"] + (1 if f.get("booking") else 0)) / 3)
    emp = _num(f.get("employees"))
    if emp is not None:
        parts["size"] = 0.2 if emp < 5 else 0.6 if emp < 20 else 1.0 if emp < 100 else 0.7
    elif f.get("gst"):
        parts["size"] = 0.0  # GST registration can be voluntary; it does not prove turnover.
    elif f.get("staff"):                                     # named people on their team page (free)
        parts["size"] = 1.0 if f["staff"] >= 8 else 0.7 if f["staff"] >= 3 else 0.4
    total = 0.0
    if f.get("reviews") is None:
        # FREE MODE (no review count, e.g. no Places/Serper key): renormalise over the signals that ARE present,
        # so a missing signal is neither 0 nor a made-up 0.5. The niche's own budget fit (Niches tab, the reason the
        # niche is targeted) is one of those present signals; without it a typical one-site business with no ad pixel
        # scored R ~2 and free mode produced zero leads (measured). The caller caps the segment at PRIME-REVIEW.
        wf = dict(w)
        if f.get("fit") is not None:
            parts["niche_fit"] = max(0.0, min(1.0, float(f["fit"]) / 5))
            wf["niche_fit"] = S.num("FREE_MODE_NICHE_WEIGHT", 0.30)
        known = sum(wf[k] for k in parts)
        if not known:
            return 0.0, 0.0, parts
        return round(10 * sum(wf[k] * v for k, v in parts.items()) / known, 1), round(sum(w.get(k, 0) for k in parts), 2), parts
    for k, wt in w.items():
        if k in parts:
            total += wt * parts[k]
            known += wt
        else:
            total += wt * 0.5                                # unknown = neutral, never "bad"
    return round(10 * total, 1), round(known, 2), parts


def revenue_band(f, q, R, conf, S):
    """(band, basis). Formula when a review velocity is known; else a proxy band from R. Never an exact revenue."""
    # Reviews, assumed ticket prices and review rates cannot establish revenue.
    if not conf:
        return "Unknown", "No verified financial capacity indicators"
    return "Unverified", f"Commercial indicator score {R}/10; evidence coverage {conf:.0%}. Revenue and budget are unknown."


# ------------------------------------------------------------------ S: social strength
def s_score(igs, socials, q, S):
    """igs: {followers, posts30, posts90, engagement, reels_share} or None. Returns (S 0-10, basis)."""
    if not igs:
        if not any(k in socials for k in ("Instagram", "Facebook", "TikTok")):
            return 10.0, "Social presence unknown; no opportunity established"
        return 10.0, "Profile found; activity unknown; no opportunity established"
    nf = _lin(igs.get("followers") or 0, q["fweak"], q["fstrong"])
    p30, p90 = igs.get("posts30"), igs.get("posts90")
    nc = 0.0 if (p90 is not None and p90 < 3) else _lin(p30 or 0, q["pweak"], q["pstrong"])
    e = igs.get("engagement") or 0
    ne = 0.0 if e < 0.5 else 0.5 * (e - 0.5) / 0.5 if e < 1 else min(1.0, 0.5 + 0.5 * (e - 1) / 2)
    nr = _lin(igs.get("reels_share") or 0, 0, 0.4)
    val = round(10 * (0.35 * nf + 0.35 * nc + 0.15 * ne + 0.15 * nr), 1)
    return val, (f"Instagram: {igs.get('followers', 0):,} followers, {p30 or 0} posts/30d"
                 + (f", {p90} posts/90d" if p90 is not None else "") + f", {e}% engagement, {int((igs.get('reels_share') or 0) * 100)}% Reels")


# ------------------------------------------------------------------ A: agency / in-house confidence
_CREDIT_MKT = re.compile(r"\b(?i:digital marketing|marketing|social media|social|seo|advertising|content|managed)\s+(?i:by|managed by|powered by)"
                         r"[ \t]*[:\-–]?[ \t]*([A-Z0-9][\w&'.\-]*(?:[ \t]+[A-Z0-9][\w&'.\-]*){0,3})")
_CREDIT_WEB = re.compile(r"\b(?i:website|web design|site|designed|developed|built|website design)\s+(?i:and developed\s+)?(?i:by)\s*[:\-–]?\s*"
                         r"([A-Z0-9][\w&'.\-]*(?:[ \t]+[A-Z0-9][\w&'.\-]*){0,3})")      # one line only
_AGENCY_WORDS = re.compile(r"(?i)\b(?:agency|marketing|media|social|seo|digital|growth|ads|advertising)\b")
_PLATFORMS = ("wordpress", "shopify", "squarespace", "wix", "webflow", "godaddy", "weebly", "duda", "hubspot", "jimdo")
AGENCY_STACK = {"GoHighLevel": r"msgsndr\.com|leadconnectorhq\.com|gohighlevel", "CallRail": r"callrail\.com|cdn\.callrail",
                "WhatConverts": r"whatconverts\.com", "Agency UTM tags": r"utm_source=(?!google|facebook|instagram|newsletter)[a-z0-9_-]*agency"}
SCHED_TOOLS = r"later\.com|linkin\.bio|planoly\.com|sproutsocial\.com|buffer\.com|hootsuite\.com"
BOOKING_TOOLS = r"boulevard\.io|joinblvd\.com|zenoti\.com|sevenrooms\.com|mindbodyonline\.com|dentally\.co|cliniko\.com|janeapp\.com|" \
                r"hotdoc\.com\.au|healthengine\.com\.au|zocdoc\.com|opentable\.|resy\.com|glofox\.com|fresha\.com|vagaro\.com"
_IG_CREDIT = re.compile(r"(?:managed by|content by|shot by|photography by|social by|filmed by|edited by)\s*@([A-Za-z0-9._]{2,30})", re.I)


def site_agency_signals(html, text):
    """Facts from the business's own pages. Returns list of (kind, evidence, agency_name)."""
    out = []
    for m in _CREDIT_MKT.finditer(text or ""):
        nm = m.group(1).strip(" .,-")
        if nm.lower().split()[0] not in _PLATFORMS:
            out.append(("agency_credit", f"site: '{m.group(0).strip()[:70]}'", nm))
    for m in _CREDIT_WEB.finditer(text or ""):
        nm = m.group(1).strip(" .,-")
        if nm.lower().split()[0] not in _PLATFORMS and not any(o[0] == "agency_credit" for o in out):
            # 'Site by Vallure Agency' / 'Website by Bloom Digital Marketing' = a marketing agency, not just a web studio
            kind = "agency_credit" if _AGENCY_WORDS.search(nm) else "web_credit"
            out.append((kind, f"site: '{m.group(0).strip()[:70]}'", nm))
    stack = [k for k, pat in AGENCY_STACK.items() if re.search(pat, html or "", re.I)]
    if len(set(re.findall(r"GTM-[A-Z0-9]{4,8}", html or ""))) >= 2:
        stack.append("2+ Google Tag Manager containers")
    if stack:
        out.append(("agency_stack", "site uses " + ", ".join(stack), ""))
    if re.search(SCHED_TOOLS, html or "", re.I):
        out.append(("sched_tool", "social scheduling tool linked", ""))
    return out


def booking_tool(html):
    m = re.search(BOOKING_TOOLS, html or "", re.I)
    return m.group(0).split(".")[0].title() if m else ""


def ig_agency_signals(igs, own_handle=""):
    """From Instagram captions/bio (Business Discovery). Returns list of (kind, evidence, name)."""
    if not igs:
        return []
    out, txt = [], " ".join(igs.get("captions") or []) + " " + (igs.get("bio") or "")
    m = _IG_CREDIT.search(txt)
    if m:
        out.append(("ig_credit", f"Instagram: '{m.group(0)[:60]}'", "@" + m.group(1)))
    tags = {}
    for c in igs.get("captions") or []:
        for h in set(re.findall(r"@([A-Za-z0-9._]{2,30})", c)):
            if h.lower() != (own_handle or "").lower():
                tags[h] = tags.get(h, 0) + 1
    top = max(tags.items(), key=lambda x: x[1]) if tags else None
    if top and top[1] >= 3 and not m:
        out.append(("ig_credit", f"Instagram: @{top[0]} tagged on {top[1]}/{len(igs.get('captions') or [])} posts", "@" + top[0]))
    return out


POINTS = {"agency_credit": 30, "web_credit": 10, "ig_credit": 30, "agency_stack": 15, "pro_cadence": 15, "inhouse_role": 20,
          "sched_tool": 5}


def a_score(signals, igs, q):
    """Returns (A 0-100, evidence string, agency name)."""
    sig = list(signals)
    if igs and (igs.get("posts30") or 0) >= q["pstrong"] and igs.get("cadence_cv") is not None \
            and igs["cadence_cv"] < 0.5 and (igs.get("reels_share") or 0) >= 0.4:
        sig.append(("pro_cadence", f"pro cadence: {igs['posts30']} posts/30d, regular, {int(igs['reels_share'] * 100)}% Reels", ""))
    seen, total, ev, name = set(), 0, [], ""
    for kind, evidence, nm in sig:
        if kind in seen:
            continue
        seen.add(kind)
        total += POINTS.get(kind, 0)
        ev.append(evidence)
        if nm and not name and kind in ("agency_credit", "ig_credit", "web_credit"):
            name = nm
    named = bool(seen & {"agency_credit", "ig_credit", "inhouse_role"})
    if not named:                                            # never HAS-AGENCY on cadence/stack alone (spec)
        total = min(total, 49)
    return min(100, total), "; ".join(ev), name


# ------------------------------------------------------------------ segment + ICP
def segment(R, S_, A, S):
    if R < S.num("REJECT_R_BELOW", 3):
        return "REJECT"
    if A >= S.num("AGENCY_HAS_MIN", 50):
        return "HAS-AGENCY"
    pr, ps, review_a = S.num("PRIME_R_MIN", 7), S.num("PRIME_S_MAX", 4), S.num("AGENCY_REVIEW_MIN", 30)
    gr, gs = S.num("GROWING_R_MIN", 4), S.num("GROWING_S_MAX", 6.5)
    if R >= pr and S_ <= ps:
        return "PRIME" if A < review_a else "PRIME-REVIEW"
    if (gr <= R < pr and S_ <= gs) or (R >= pr and ps < S_ <= gs):
        return "GROWING"
    if S_ > gs:
        return "POLISHED-NO-SIGNAL"
    return "REJECT"


def icp3(d, S):
    """Final ICP 0-100 (spec section 1 weights, Rules keys W3_*). d: R, S, A, fit, contact (0-1), dm, rating, priority."""
    w = S.num
    rt = d.get("rating")
    rep = 0.6 if rt is None else 1.0 if rt >= 4.5 else 0.8 if rt >= 4.0 else 0.5 if rt >= 3.5 else 0.2
    sc = (w("W3_REVENUE", 35) * d["R"] / 10 + w("W3_SOCIAL_GAP", 25) * (10 - d["S"]) / 10
          + w("W3_NICHE_BUDGET", 15) * max(0, min(5, d["fit"])) / 5 + w("W3_CONTACT", 15) * max(0.0, min(1.0, d["contact"]))
          + w("W3_DECISION_MAKER", 5) * (1.0 if d.get("dm") else 0.0) + w("W3_REPUTATION", 3) * rep
          + w("W3_LOCATION", 2) * {1: 1.0, 2: 0.8, 3: 0.6}.get(d.get("priority"), 0.6))
    if d["A"] < S.num("AGENCY_HAS_MIN", 50):
        sc -= w("W3_AGENCY_PENALTY", 15) * d["A"] / 100
    return max(0, min(100, round(sc)))


def contact_level(p_mail, g_mail, email_status, instagram, other_social, phone):
    c = 0.85 if p_mail else 0.65 if g_mail else 0.0
    if c:
        c += {"valid": 0.1, "risky": -0.1}.get(email_status, 0.0)
    return max(0.0, min(1.0, c + (0.1 if instagram else 0) + (0.05 if other_social else 0) + (0.05 if phone else 0)))


# ------------------------------------------------------------------ YouTube
_YT_EDITOR = re.compile(r"(?:edited by|editor\s*[:\-]|video editor\s*[:\-]|editing by|thumbnails? by|managed by)\s*@?([A-Za-z0-9._ ]{2,30})", re.I)


def yt_segment(desc):
    """YouTube sheet: PRIME unless the channel credits an editor/manager in its description -> HAS-AGENCY."""
    m = _YT_EDITOR.search(desc or "")
    if m:
        return "HAS-AGENCY", f"description: '{m.group(0).strip()[:60]}'", m.group(1).strip()
    return "PRIME", "", ""


def median(values):
    v = [x for x in values if x is not None]
    return int(statistics.median(v)) if v else None


def cadence_cv(dates):
    """Coefficient of variation of the gaps between posts (low = scheduled / professional)."""
    ds = sorted(dates)
    gaps = [(b - a).total_seconds() / 86400 for a, b in zip(ds, ds[1:])]
    if len(gaps) < 3 or not statistics.mean(gaps):
        return None
    return round(statistics.pstdev(gaps) / statistics.mean(gaps), 2)
