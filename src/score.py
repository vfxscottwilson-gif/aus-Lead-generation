"""Legacy ranking helpers. Use qualification.evaluate for gated 100-point ICP decisions.
Scores and suggested fees do not establish ability or willingness to pay."""


def score(d, S):
    """d: fit(1-5), reviews, rating, subs, has_website, personal_email, generic_email, email_status, instagram,
    other_social, phone, decision_maker, gap_sev, spend(0-3 signals), priority."""
    w = S.num
    niche = w("W2_NICHE_BUDGET", 30) * max(0, min(5, d["fit"])) / 5
    r, subs = d.get("reviews"), d.get("subs")
    if subs is not None:                                     # YouTube: mid-size channels are the sweet spot
        est = 1.0 if 20000 <= subs <= 250000 else 0.8 if 10000 <= subs < 20000 or 250000 < subs <= 500000 else 0.55
    elif r is None and d.get("age") is not None:          # no reviews (free map) but the ABN says how old it is (AU)
        a = d["age"]
        est = 0.95 if a >= 8 else 0.85 if a >= 3 else 0.6 if a >= 1 else 0.3
    else:
        est = 0.7 if r is None else 1.0 if 30 <= r <= 400 else 0.75 if (15 <= r < 30 or 400 < r <= 600) else 0.45 if r >= 8 else 0.1
    established = w("W2_ESTABLISHED", 15) * est
    website = w("W2_WEBSITE", 5) * (1.0 if d["has_website"] else 0.0)
    c = 0.0
    if d.get("personal_email"):
        c = 0.85
    elif d.get("generic_email"):
        c = 0.65
    if c:
        c += {"valid": 0.1, "risky": -0.1}.get(d.get("email_status"), 0.0)
    c += 0.1 if d["instagram"] else 0.0
    c += 0.05 if d["other_social"] else 0.0
    c += 0.05 if d["phone"] else 0.0
    contact = w("W2_CONTACT", 20) * max(0.0, min(c, 1.0))
    dm = w("W2_DECISION_MAKER", 10) * (1.0 if d.get("decision_maker") else 0.0)
    gap = w("W2_SOCIAL_GAP", 10) * d["gap_sev"]
    spend = w("W2_SPEND_SIGNAL", 5) * min(1.0, d.get("spend", 0) / 2)
    rt = d["rating"]
    rep = w("W2_REPUTATION", 3) * (0.6 if rt is None else 1.0 if rt >= 4.5 else 0.8 if rt >= 4.0 else 0.5 if rt >= 3.5 else 0.2)
    loc = w("W2_LOCATION", 2) * {1: 1.0, 2: 0.8, 3: 0.6}.get(d["priority"], 0.6)
    return round(min(100, niche + established + website + contact + dm + gap + spend + rep + loc))


def icp(sc):
    return round(sc / 10, 1)


def tier_of(sc, S):
    i = icp(sc)
    return "A" if i >= S.num("TIER_A_ICP", 8.5) else "B" if i >= S.num("TIER_B_ICP", 7.5) else "C"


def pitch_price(sc, fit, S, youtube=False):
    """Suggested monthly retainer to open with. Estimate scaled by ICP score + niche budget, rounded to $50."""
    lo = S.num("PITCH_YT_MIN_AUD" if youtube else "PITCH_MIN_AUD", 1000 if youtube else 500)
    hi = S.num("PITCH_YT_MAX_AUD" if youtube else "PITCH_MAX_AUD", 2000 if youtube else 1000)
    floor = S.num("MIN_ICP_SCORE", 7)
    f = 0.65 * max(0.0, min(1.0, (icp(sc) - floor) / max(0.5, 10 - floor))) + 0.35 * max(0.0, min(1.0, (fit - 1) / 4))
    return int(round((lo + (hi - lo) * f) / 50) * 50)


def social_gap(soc, igs):
    """Returns (severity 0-1, plain-English gap). Facts only."""
    has_ig, has_fb = "Instagram" in soc, "Facebook" in soc
    if not has_ig and not has_fb:
        return 0.0, "social presence not established; research needed"
    if igs:
        if igs.get("posts30") is not None and igs.get("complete_window") is True and igs["posts30"] < 4:
            return 0.85, f"their Instagram posted only {igs['posts30']}x in the last 30 days"
        if igs.get("engagement") is not None and igs["engagement"] < 1.0:
            return 0.8, f"their Instagram engagement is only {igs['engagement']}%"
        return 0.15, "their Instagram looks active"
    if has_ig:
        return 0.0, "they have Instagram but activity could not be measured"
    return 0.0, "Facebook found; Instagram presence unknown"


def yt_gap(uploads30, has_shorts_link=False):
    """YouTube channels: facts from the upload history."""
    if uploads30 is None:
        return 0.0, "upload frequency could not be measured"
    if uploads30 < 2:
        return 0.85, f"the channel uploaded only {uploads30} video(s) in the last 30 days"
    if uploads30 < 5:
        return 0.6, f"the channel uploads about {uploads30} videos a month (room to scale with an editor)"
    return 0.4, f"the channel uploads {uploads30} videos a month (editing workload is high)"


class _Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def pitch(template, facts):
    try:
        return template.format_map(_Safe(facts))
    except Exception:
        return f"{facts.get('name', '')}: {facts.get('gap', '')}. Offer a free 30-60s sample reel."
