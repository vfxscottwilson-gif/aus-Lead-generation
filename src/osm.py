"""FREE lead discovery from OpenStreetMap (Overpass API). No key, no card. Plus free geocoding via Nominatim.
Coverage in OSM is community-made: good for dentists, real estate, furniture, dealers, solicitors; thinner for surgeons.
No ratings/review counts exist in OSM, so those columns stay blank for OSM leads. Works in any country: the
country comes from the Locations row (Country column)."""
import math
import re
import time

import requests

MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]
UA = "LeadResearchBot/1.0 (business lead research; contact via repo owner)"


class OSMUnavailable(Exception):
    pass


def _filter(cond):
    """'k' | 'k=v' | 'k~regex' | 'k!=v' | 'k!~regex' -> Overpass tag filter. '' if the condition is malformed."""
    cond = cond.strip()
    m = re.match(r"^([A-Za-z0-9_:\-]+)\s*(!=|!~|=|~)?\s*(.*)$", cond)
    if not m:
        return ""
    k, op, v = m.groups()
    v = v.strip().replace('"', "")
    if not op:
        return f'["{k}"]' if not v else ""
    if not v:
        return ""
    if op in ("=", "!="):
        v = v.replace("\\", "\\\\")
        return f'["{k}"{op}"{v}"]'
    return f'["{k}"{op}"{v}",i]'


def parse_selector(sel):
    parts = [_filter(c) for c in sel.split("+") if c.strip()]
    return "".join(p for p in parts if p) if parts and all(parts) else ""


def build_query(selectors, lat, lon, radius_km, limit):
    """Bounding box (square around the point) instead of (around:...): measured 2026-10 the public server answers bbox
    queries in ~30 s while the same around: queries timed out."""
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    bbox = f"{lat - dlat:.5f},{lon - dlon:.5f},{lat + dlat:.5f},{lon + dlon:.5f}"
    body = "".join(f"nwr{f};" for f in (parse_selector(s) for s in selectors) if f)
    return f"[out:json][timeout:90][bbox:{bbox}];({body});out center tags {int(limit)};" if body else ""


def _url(v):
    v = (v or "").strip().split(";")[0].strip()
    if not v:
        return ""
    return v if v.startswith("http") else "https://" + v.lstrip("/")


def _social(v, base):
    v = (v or "").strip().split(";")[0].strip()
    if not v:
        return ""
    if v.startswith("http"):
        return v
    return base + v.lstrip("@/") if re.fullmatch(r"@?[A-Za-z0-9._\-]{2,50}", v) else ""


def to_place(el, loc):
    t = el.get("tags", {}) or {}
    name = (t.get("name") or t.get("brand") or "").strip()
    if not name:
        return None
    g = lambda *ks: next((t[k].strip() for k in ks if t.get(k)), "")
    country, cc = loc.get("country") or "", loc.get("cc") or ""
    street = " ".join(x for x in (g("addr:housenumber"), g("addr:street")) if x)
    suburb, state, pc = g("addr:suburb", "addr:city"), g("addr:state") or loc.get("state", ""), g("addr:postcode")
    kind = f"{el.get('type', 'node')}/{el.get('id')}"
    return {"id": f"osm:{kind}", "displayName": {"text": name},
            "formattedAddress": ", ".join(x for x in (street, suburb, state, pc, country) if x),
            "addressComponents": [{"longText": suburb, "shortText": suburb, "types": ["locality"]},
                                  {"longText": state, "shortText": state, "types": ["administrative_area_level_1"]},
                                  {"longText": pc, "shortText": pc, "types": ["postal_code"]},
                                  {"longText": country, "shortText": cc, "types": ["country"]}],
            "websiteUri": _url(g("website", "contact:website", "url")),
            "nationalPhoneNumber": g("phone", "contact:phone"), "googleMapsUri": f"https://www.openstreetmap.org/{kind}",
            "businessStatus": "OPERATIONAL", "types": [],
            "primaryTypeDisplayName": {"text": g("healthcare:speciality", "amenity", "leisure", "shop", "office", "craft").replace("_", " ").title()},
            "_nometrics": True, "_source": "OpenStreetMap", "_email": g("email", "contact:email"),
            "_ig": _social(g("contact:instagram", "instagram"), "https://www.instagram.com/"),
            "_fb": _social(g("contact:facebook", "facebook"), "https://www.facebook.com/"),
            # chain / franchise evidence published in OSM itself (brand tags are added for chains, not one-offs)
            "_brand": g("brand:wikidata", "brand"), "_cuisine": g("cuisine"), "_takeaway": g("takeaway")}


# main server first; it sheds load with a fast 502/429/504 (about 2 queries per IP at a time), so wait and retry it.
# The community mirrors were mostly timing out when measured, so they are only the backup.
PLAN = [(0, 0), (0, 20), (1, 0), (0, 45), (2, 0)]          # (mirror index, seconds to wait first)


def search(selectors, loc, limit=150, max_wait=300):
    """Returns list of pseudo-place dicts. Raises OSMUnavailable only if every attempt failed (max ~max_wait s)."""
    q = build_query(selectors, loc["lat"], loc["lon"], loc["radius"], limit)
    if not q:
        return []
    last, start = "no server tried", time.time()
    for i, pause in PLAN:
        if time.time() - start + pause > max_wait:
            break
        time.sleep(pause)
        url = MIRRORS[i]
        try:
            r = requests.post(url, data={"data": q}, headers={"User-Agent": UA}, timeout=100 if i == 0 else 60)
            if r.status_code == 200:
                els = r.json().get("elements", [])
                out, seen = [], set()
                for el in els:
                    p = to_place(el, loc)
                    if p and p["id"] not in seen:
                        seen.add(p["id"])
                        out.append(p)
                return out
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = type(e).__name__
    raise OSMUnavailable(f"All Overpass servers failed ({last}). Will retry next run.")


def geocode(area, cc=""):
    """Free Nominatim lookup (1 request, polite). cc = ISO country code to restrict to ('' = anywhere).
    Returns (lat, lon) or None."""
    try:
        params = {"q": area, "format": "json", "limit": 1}
        if cc:
            params["countrycodes"] = cc.lower()
        r = requests.get("https://nominatim.openstreetmap.org/search", headers={"User-Agent": UA}, timeout=20, params=params)
        time.sleep(1.2)
        d = r.json() if r.status_code == 200 else []
        return (float(d[0]["lat"]), float(d[0]["lon"])) if d else None
    except Exception:
        return None
