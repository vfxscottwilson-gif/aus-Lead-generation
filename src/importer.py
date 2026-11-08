"""Scraper import: CSV / JSON / JSONL files in the repo's imports/ folder, or rows pasted into the sheet's 'Import' tab,
go through the SAME pipeline as found leads (dedupe -> website read -> enrich -> score -> 7/10 gate -> sheet).

Understands any header through an alias table. Tested shapes:
  * gosom/google-maps-scraper CSV (36 columns: title, category, address, website, phone, review_count, review_rating,
    latitude, longitude, cid, status, price_range, data_id, place_id, complete_address, emails, ...) and its -json
    output (web_site, longtitude, ...).
  * the yt-dlp wrapper CSV from Scraper Tools (title, website, email, subscribers, channel_id, latest_video, description).
  * Apify / Outscraper / hand-made sheets (name, phone, site, rating, reviews, email, city, country, ...).
Niche per row: a 'niche' column > file name 'Niche name__anything.csv' > category matched against the Niches tab
search terms > IMPORT_DEFAULT_NICHE > 'Imported Leads'. Nothing is invented: blank cells stay blank."""
import csv
import glob
import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone

import defaults as D
import enrich as E
import research

ALIASES = {
    "name": ["title", "name", "business name", "company", "company name", "business", "place name", "display name", "channel"],
    "category": ["category", "main category", "type", "business type", "primary type", "categories", "subtypes"],
    "address": ["address", "full address", "formatted address", "street address", "address full"],
    "complete_address": ["complete address"],
    "website": ["website", "web site", "site", "url", "domain", "website url", "homepage"],
    "phone": ["phone", "phone number", "telephone", "phone 1", "international phone", "phone international"],
    "reviews": ["review count", "reviews", "reviews count", "user ratings total", "userratingcount", "number of reviews",
                "total reviews"],
    "rating": ["review rating", "rating", "stars", "average rating", "totalscore", "total score"],
    "emails": ["emails", "email", "email address", "emails found", "email 1", "email1", "contact email"],
    "lat": ["latitude", "lat"],
    "lon": ["longitude", "longtitude", "lng", "lon", "long"],
    "place_id": ["place id", "placeid", "google place id", "google id"],
    "cid": ["cid", "data id"],
    "link": ["link", "google maps url", "maps url", "map url", "googlemapsuri", "place url", "google maps link"],
    "city": ["city", "town", "locality", "suburb"],
    "state": ["state", "region", "province", "county"],
    "postcode": ["postal code", "postcode", "zip", "zip code"],
    "country": ["country", "country code"],
    "status": ["status", "business status"],
    "niche": ["niche", "lead niche"],
    "price": ["price range", "price level", "price", "pricelevel"],
    "description": ["description", "descriptions", "about", "channel description"],
    "instagram": ["instagram", "instagram url"], "facebook": ["facebook", "facebook url"],
    "linkedin": ["linkedin", "linkedin url"], "tiktok": ["tiktok"], "twitter": ["twitter", "x", "twitter url"],
    "youtube": ["youtube", "youtube url"],
    "subscribers": ["subscribers", "subscriber count", "channel follower count", "subs"],
    "channel_id": ["channel id", "channelid"],
    "latest_video": ["latest video", "last upload", "latest upload", "upload date"],
}
ALIASES.update({key: [label] for label, key in research.FIELDS.items()})
_NORM = lambda h: re.sub(r"[\s_\-./]+", " ", str(h or "").strip().lower()).strip()
_INDEX = {_NORM(a): f for f, al in ALIASES.items() for a in al}


def map_header(header):
    """Raw header list -> {field: column index}. First matching column wins."""
    out = {}
    for i, h in enumerate(header):
        f = _INDEX.get(_NORM(h))
        if f and f not in out:
            out[f] = i
    return out


def _num(v):
    try:
        v = str(v).strip().replace(",", "")
        return float(v) if v not in ("", "-", "None", "null") else None
    except ValueError:
        return None


def _json_cell(v):
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v) if str(v).strip()[:1] in "{[" else None
    except ValueError:
        return None


def _upload_days(v):
    """'20261025' / '2026-10-25' / ISO timestamp -> days ago, else None."""
    s = str(v or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            t = datetime.strptime(s[:10] if "-" in fmt else s[:8], fmt).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - t).days
        except ValueError:
            continue
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - (t if t.tzinfo else t.replace(tzinfo=timezone.utc))).days
    except ValueError:
        return None


def upscale_price(v):
    """'$$$' / '£££' / 'PRICE_LEVEL_EXPENSIVE' / '$50-100' -> True when clearly upscale, else False."""
    s = str(v or "")
    if "EXPENSIVE" in s.upper():
        return True
    if len(re.findall(r"[$£€]", s)) >= 3:
        return True
    n = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    return bool(n) and max(n) >= 60


def _closed(status):
    s = str(status or "").lower()
    return "closed" in s and "open" not in s.replace("closed", "")


def row_to_place(rec, fname=""):
    """rec: {field: value}. Returns (place dict, location dict) or (None, None) if the row has no name."""
    g = lambda f: (str(rec.get(f) if rec.get(f) is not None else "")).strip()
    name = g("name")
    if not name:
        return None, None
    ca = _json_cell(rec.get("complete_address")) or {}
    ca = ca if isinstance(ca, dict) else {}
    city = g("city") or str(ca.get("city") or "").strip()
    state = g("state") or str(ca.get("state") or "").strip()
    pc = g("postcode") or str(ca.get("postal_code") or "").strip()
    country_raw = g("country") or str(ca.get("country") or "").strip()
    country, cc, _ = D.country_info(country_raw) if country_raw else ("", "", "")
    site = g("website").split()[0] if g("website") else ""
    if site and not site.startswith("http"):
        site = "https://" + site.lstrip("/")
    emails = E.emails_in(re.sub(r"[,;|\s]+", " ", g("emails")))
    yt_id = g("channel_id") or (re.search(r"youtube\.com/channel/(UC[\w-]{20,})", site + " " + g("youtube")) or [None, ""])[1]
    is_yt = bool(yt_id) or ("youtube.com/" in site and g("subscribers"))
    pid_raw = g("place_id")
    if is_yt:
        pid = "yt:" + (yt_id or hashlib.md5(site.encode()).hexdigest()[:16])
    elif pid_raw.startswith("ChIJ") or re.fullmatch(r"[A-Za-z0-9_\-]{20,}", pid_raw or ""):
        pid = pid_raw                                     # same id Google Places uses -> dedupes across sources
    elif g("cid"):
        pid = "gmapscid:" + g("cid")
    else:
        key = re.sub(r"[^a-z0-9]", "", name.lower()) + E._host(site) + re.sub(r"[^a-z0-9]", "", (g("address") or g("phone")).lower())
        pid = "imp:" + hashlib.md5(key.encode()).hexdigest()[:16]
    rating, reviews = _num(g("rating")), _num(g("reviews"))
    socials = {}
    for f, plat in (("instagram", "Instagram"), ("facebook", "Facebook"), ("linkedin", "LinkedIn"), ("tiktok", "TikTok"),
                    ("twitter", "Twitter / X"), ("youtube", "YouTube")):
        if g(f):
            socials[plat] = g(f) if g(f).startswith("http") else "https://" + g(f).lstrip("/")
    desc = g("description")
    place = {"id": pid, "displayName": {"text": name},
             "websiteUri": "" if is_yt and "youtube.com/" in site else site,
             "nationalPhoneNumber": g("phone"),
             "formattedAddress": g("address") or ", ".join(x for x in (city, state, pc, country) if x),
             "addressComponents": [{"longText": city, "shortText": city, "types": ["locality"]},
                                   {"longText": state, "shortText": state, "types": ["administrative_area_level_1"]},
                                   {"longText": pc, "shortText": pc, "types": ["postal_code"]}]
             + ([{"longText": country, "shortText": cc, "types": ["country"]}] if country else []),
             "googleMapsUri": g("link"), "businessStatus": "CLOSED_PERMANENTLY" if _closed(g("status")) else "OPERATIONAL",
             "types": [], "primaryTypeDisplayName": {"text": g("category") or ("YouTube channel" if is_yt else "")},
             "_nometrics": reviews is None, "_source": "Import: " + (os.path.basename(fname) or "sheet"),
             "_email": ";".join(emails), "_socials": socials, "_upscale": upscale_price(g("price"))}
    if rating is not None:
        place["rating"] = rating
    if reviews is not None:
        place["userRatingCount"] = int(reviews)
    if desc and not is_yt:
        place["_text"] = desc[:3000]
    if is_yt:
        site_from_desc = E.pick_website(desc, name)
        days = _upload_days(g("latest_video"))
        place.update({"_source": "YouTube", "_import": os.path.basename(fname) or "sheet", "websiteUri": site_from_desc,
                      "_subs": int(_num(g("subscribers")) or 0) or None, "_uploads30": None, "_days": days,
                      "_desc": desc[:3000], "_yt": f"https://www.youtube.com/channel/{yt_id}" if yt_id else site,
                      "googleMapsUri": f"https://www.youtube.com/channel/{yt_id}" if yt_id else site,
                      "primaryTypeDisplayName": {"text": "YouTube channel"}})
        for k, v in E.socials_in(desc).items():
            socials.setdefault(k, v)
    loc = {"country": country, "cc": cc, "state": state, "city": city, "area": "Import", "priority": 2}
    research.attach(place, rec)
    return place, loc


def pick_niche(rec, fname, niches, default=""):
    """Returns the niche dict (from the Niches tab) for one import row."""
    by_name = {n["niche"].lower(): n for n in niches}
    want = str(rec.get("niche") or "").strip().lower()
    if not want and "__" in os.path.basename(fname):
        want = os.path.basename(fname).split("__")[0].replace("_", " ").strip().lower()
    if want:
        hit = by_name.get(want) or next((n for k, n in by_name.items() if want in k or k in want), None)
        if hit:
            return hit
    if rec.get("channel_id") or rec.get("subscribers"):
        yt = [n for n in niches if n["group"].lower() == "youtube"]
        q = (str(rec.get("description") or "") + " " + str(rec.get("name") or "")).lower()
        best = max(yt, key=lambda n: sum(1 for t in n["terms"] for w in t.lower().split() if len(w) > 3 and w in q), default=None)
        if best:
            return best
    cat = str(rec.get("category") or "").strip().lower()
    if cat:
        best, size = None, 0
        for n in niches:
            if n["group"].lower() == "youtube":
                continue
            for t in n["terms"]:
                t = t.lower()
                if t and (t in cat or cat in t) and len(t) > size:
                    best, size = n, len(t)
        if best:
            return best
    name = default or "Imported Leads"
    return by_name.get(name.lower()) or {"niche": name, "group": "Imported", "fit": 3, "low": 500, "high": 2000,
                                         "tpl": "{name}. Gap: {gap}. Offer a free 30-60s sample reel.", "why": "",
                                         "terms": [], "osm": [], "exclude": [], "must": None, "reject": None, "chain": 0}


def _records_from_rows(rows):
    if not rows:
        return []
    hm = map_header(rows[0])
    out = []
    for r in rows[1:]:
        rec = {f: (r[i] if i < len(r) else "") for f, i in hm.items()}
        if any(str(v).strip() for v in rec.values()):
            out.append(rec)
    return out


def _records_from_dicts(items):
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        hm = map_header(list(it.keys()))
        keys = list(it.keys())
        out.append({f: it[keys[i]] for f, i in hm.items()})
    return out


def read_file(path):
    """-> list of {field: value}. Never raises (a broken file returns [])."""
    try:
        low = path.lower()
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            raw = fh.read()
        if low.endswith(".csv") or low.endswith(".tsv"):
            dialect = "excel-tab" if low.endswith(".tsv") else "excel"
            return _records_from_rows(list(csv.reader(io.StringIO(raw), dialect)))
        if low.endswith(".jsonl") or low.endswith(".ndjson"):
            return _records_from_dicts([json.loads(l) for l in raw.splitlines() if l.strip().startswith("{")])
        if low.endswith(".json"):
            s = raw.strip()
            if s.startswith("{") and "\n{" in s and not s.startswith("{\"data\""):
                try:
                    return _records_from_dicts([json.loads(l) for l in s.splitlines() if l.strip()])   # gosom -json = JSONL
                except ValueError:
                    pass
            d = json.loads(s)
            if isinstance(d, dict):
                d = d.get("data") or d.get("items") or d.get("results") or [d]
            return _records_from_dicts(d if isinstance(d, list) else [])
    except Exception:
        return []
    return []


def folder_files(folder):
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for ext in ("csv", "tsv", "json", "jsonl", "ndjson"):
        out += glob.glob(os.path.join(folder, f"*.{ext}"))
    return sorted(set(out))


def tab_records(rows):
    """Rows of the sheet 'Import' tab (row 1 = the scraper's own header)."""
    if not rows or not rows[0] or str(rows[0][0]).startswith("Paste any scraper"):
        return []
    return _records_from_rows(rows)


def collect(records_by_source, niches, default_niche=""):
    """records_by_source: [(file name or 'Import tab', [records])] -> [(niche, loc, place, found_on)]."""
    out = []
    for src, recs in records_by_source:
        for rec in recs:
            place, loc = row_to_place(rec, src)
            if not place:
                continue
            out.append((pick_niche(rec, src, niches, default_niche), loc, place, f"Import: {os.path.basename(src)}"))
    return out
