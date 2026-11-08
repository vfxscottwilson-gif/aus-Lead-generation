"""v5 'Lead Insight' per QUALIFIED lead: 3-4 short lines on who they are and their likely pains / struggles
(replaces the old Case Study + email draft: outreach is a separate bot, see CONTRACT.md). The lead bot never writes
an email.

ONE small AI call per lead (optional). The same call may return a decision-maker name, which is accepted only when
it appears VERBATIM in the website text with a decision role next to it (the AI can never invent a person).
No key / quota hit (429) / any error -> fact-only summary. Never raises."""
import json
import re

from ai import QuotaHit

# 'Likely' pains by niche group: general industry knowledge, always worded as a likelihood, never as a fact
PAINS = {
    "Medical": "Likely pain: high-ticket treatments sell on trust, and before/after or explainer video is scarce.",
    "Health": "Likely pain: patients compare clinics on Instagram/Google; little short-form proof of results.",
    "Real Estate": "Likely pain: listings and agent brand compete on video; walkthrough reels take time in-house.",
    "Finance": "Likely pain: trust is hard to show in a crowded market; few short explainer videos.",
    "Legal": "Likely pain: clients pick on credibility; little helpful video content to build it.",
    "Fitness": "Likely pain: memberships depend on steady Reels/class content; owners are busy coaching.",
    "Hospitality": "Likely pain: bookings follow food/venue video on Instagram/TikTok; content cadence is hard to keep.",
    "Retail Showrooms": "Likely pain: high-value products need video demos; showroom content is rarely posted.",
    "Automotive": "Likely pain: stock moves faster with walkaround videos; few are posted consistently.",
    "Construction": "Likely pain: finished projects are rarely filmed; long sales cycles need visible proof.",
    "Home Services": "Likely pain: jobs are won on reviews and before/after proof; little of it is on social.",
    "Events": "Likely pain: next bookings depend on highlight reels from past events.",
    "Education": "Likely pain: enrolments depend on trust and student stories, rarely shown on video.",
    "Design": "Likely pain: portfolio work is strong but posted irregularly; little video.",
    "YouTube": "Likely pain: upload pace and editing load; Shorts/clip repurposing is often missing.",
}


def _clip(s, n=160):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0] + "…"


def fact_insight(f):
    """3-4 lines from observed facts only (+ one 'Likely' line from the niche group)."""
    niche = re.sub(r"\s*\(.*?\)\s*", " ", str(f.get("niche") or "business")).strip()     # 'Dentists (Cosmetic...)' -> 'Dentists'
    who = f"{f['name']}: {niche}" + (f" in {f['city']}" if f.get("city") else "")
    if f.get("youtube") and f.get("subs"):
        who += f", {int(f['subs']):,} subscribers"
    elif f.get("reviews"):
        who += f", {f['reviews']} Google reviews at {f['rating']}★"
    lines = [_clip(who + ".")]
    seen = []
    if f.get("gap"):
        seen.append(str(f["gap"]).rstrip("."))
    if f.get("signals"):
        seen.append("runs " + ", ".join(f["signals"][:4]) + " (tools observed; spending unverified)")
    if f.get("segment"):
        seen.append(f"segment {f['segment']}" + (f", revenue band {f['band']}" if f.get("band") else ""))
    if seen:
        lines.append(_clip("Seen: " + "; ".join(seen) + "."))
    if f.get("agency"):
        lines.append(_clip(f"Works with {f['agency']} (agency credit found)."))
    lines.append("Service hypothesis for review: assess whether educational or showcase videos support customer enquiries.")
    return "\n".join(lines[:4])


def verify_person(name, role, text):
    """Accept an AI-suggested person only if the exact name is in the page text with a decision role within 150 chars."""
    from enrich import people_in
    for observed_name, observed_role, _ in people_in("", text=text or ""):
        if observed_name.casefold() == str(name or "").strip().casefold():
            return observed_name, observed_role
    return None


def build(ai, f, want_person=False):
    """Returns dict: insight, person (name, role) or None, ai_used, ai_failed, quota (True on a 429/quota stop)."""
    out = {"insight": "", "person": None, "ai_used": False, "ai_failed": False, "quota": False}
    if ai is not None:
        try:
            ctx = (f.get("text") or "")[:2500]
            prompt = (
                "Analyse this " + ("YouTube channel" if f.get("youtube") else "business") +
                " for a video/social-media agency, using ONLY the facts and website text below. Invent nothing "
                "(no numbers, clients or results that are not given).\n"
                f"FACTS: name={f['name']}; niche={f.get('niche')}; city={f.get('city') or 'n/a'}; country={f.get('country') or 'n/a'}; "
                f"rating={f.get('rating') or 'unknown'}; reviews={f.get('reviews') or 'unknown'}; subscribers={f.get('subs') or 'n/a'}; "
                f"observed gap={f.get('gap') or 'n/a'}; marketing tools on site={', '.join(f.get('signals') or []) or 'none detected'}.\n"
                f"WEBSITE TEXT:\n{ctx or 'n/a'}\n\n"
                'Return ONLY JSON: {"insight": "3-4 short lines separated by \\n: line 1 who they are; then their likely '
                "pain points or struggles with marketing/social video. Start every inference with 'Likely'. Max 45 words "
                'in total.", "decision_maker": {"name": "...", "role": "..."} ONLY if the website text names an owner/'
                "founder/director/principal/manager (copy the name exactly), otherwise null}")
            raw = ai.generate(prompt, 220, temperature=0.3)
            out["ai_used"] = True                       # a bad JSON reply is not an AI outage
            m = re.search(r"\{.*\}", raw, re.S)
            d = json.loads(m.group(0)) if m else {}
            d = d if isinstance(d, dict) else {}
            ins = d.get("insight")
            if isinstance(ins, list):
                ins = "\n".join(str(x) for x in ins)
            if isinstance(ins, str) and ins.strip():
                lines = [_clip(x.strip(" -•\t"), 200) for x in ins.replace("\\n", "\n").split("\n") if x.strip(" -•\t")]
                out["insight"] = "\n".join(lines[:4])
            dm = d.get("decision_maker")
            if want_person and isinstance(dm, dict):
                out["person"] = verify_person(str(dm.get("name") or ""), str(dm.get("role") or ""), f.get("text") or "")
        except QuotaHit:
            out["quota"] = out["ai_failed"] = True
        except Exception:
            out["ai_failed"] = not out["ai_used"]
    out["insight"] = ""  # Free-form model claims are not verified evidence.
    if not out["insight"]:
        try:
            out["insight"] = fact_insight(f)
        except Exception:
            out["insight"] = _clip(f"{f.get('name', '')}: {f.get('niche', '')}.")
    return out
