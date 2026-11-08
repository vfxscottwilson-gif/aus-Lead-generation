"""OPTIONAL enrichment services: Jina AI Reader, Hunter.io, ZeroBounce, Apify, Apollo, Meta Instagram.

HARD RULES (defensive programming):
  * Every public helper has its OWN try/except and NEVER raises.
  * Missing/empty/invalid key, 4xx, 5xx, timeout, bad JSON, rate limit -> the helper logs ONE silent line
    ("[Enrichment Skipped] <Service> API unavailable (...)") and returns None. The caller keeps the base lead unchanged.
  * 429 (rate limit) or 401/402/403 (bad key / no credit) trips a per-run circuit breaker: every later lead skips that
    service INSTANTLY (no waiting, no retries). 3 consecutive failures also trip it.
  * Keys are read with os.getenv(KEY, "") only. No os.environ[...] indexing anywhere.
"""
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone

import requests

import enrich as E

# ---- feature flags (safe: computed with os.getenv(..., "")) ---------------------------------------------
# Jina Reader works keyless (lower rate limit), so it is ON unless you set USE_JINA=false.
ENRICH_WEBSITES = os.getenv("USE_JINA", "true").strip().lower() not in ("0", "false", "no", "off")


def verify_on():
    """Verification uses paid-tier verifiers only when a key exists (env OR Options tab, so checked at call time)."""
    return bool(key("HUNTER_API_KEY") or key("ZEROBOUNCE_API_KEY"))


VERIFY_EMAILS = bool(os.getenv("HUNTER_API_KEY", "").strip() or os.getenv("ZEROBOUNCE_API_KEY", "").strip())  # legacy flag


def load_sheet_keys(rows):
    """rows: [(KEY_NAME, value)] from the Options tab. A GitHub secret always wins; the sheet only fills blanks.
    Returns the names that were taken from the sheet."""
    took = []
    for name, val in rows:
        name, val = (name or "").strip(), (val or "").strip()
        if not name or name in ("-", "GOOGLE_SA", "SHEET_ID", "GOOGLE_SERVICE_ACCOUNT_JSON") or not val:
            continue
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,40}", name):
            continue
        if not os.getenv(name, "").strip():
            os.environ[name] = val
            took.append(name)
    return took


def status_of(service_key):
    """Plain status for the Options tab."""
    svc = {"HUNTER_API_KEY": "hunter", "ZEROBOUNCE_API_KEY": "zerobounce", "JINA_API_KEY": "jina", "APIFY_API_TOKEN": "apify",
           "APOLLO_API_KEY": "apollo", "IG_ACCESS_TOKEN": "meta_ig", "YOUTUBE_API_KEYS": "youtube", "SERPER_API_KEY": "serper",
           "BRAVE_API_KEY": "brave", "ZENROWS_API_KEY": "zenrows", "ABN_LOOKUP_GUID": "abn", "ABN_GUID": "abn",
           "NZBN_API_KEY": "nzbn", "SNOV_CLIENT_ID": "snov", "SNOV_CLIENT_SECRET": "snov", "PROSPEO_API_KEY": "prospeo",
           "REACHER_URL": "reacher"}.get(service_key)
    if svc and svc in _OFF:
        return f"Off this run: {_OFF[svc]}"
    if service_key == "-":
        return "Manual tool"
    if not key(service_key) and not (service_key == "YOUTUBE_API_KEYS" and key("YOUTUBE_API_KEY")):
        return "Not set (optional)" if service_key not in ("GOOGLE_SA", "SHEET_ID") else "MISSING"
    used = OK.get(svc, 0) if svc else 0
    return "Active" + (f" ({used} calls ok)" if used else "")

STATS = Counter()      # service -> number of times it was skipped/failed this run
OK = Counter()         # service -> successful calls
_WARNED, _OFF, _FAILS = set(), {}, Counter()
NAMES = {"jina": "Jina", "hunter": "Hunter", "zerobounce": "ZeroBounce", "apify": "Apify", "apollo": "Apollo", "meta_ig": "Instagram (Meta)",
         "youtube": "YouTube Data API", "serper": "Serper (Google results)", "brave": "Brave Search", "zenrows": "ZenRows",
         "abn": "ABN Lookup", "apify_maps": "Apify Google Maps"}


def key(name):
    return os.getenv(name, "").strip()


def _skip(service, why):
    STATS[service] += 1
    if service not in _WARNED:
        _WARNED.add(service)
        print(f"[Enrichment Skipped] {NAMES.get(service, service)} API unavailable ({why})")


def _trip(service, why):
    _OFF[service] = why
    _skip(service, why)


def available(service):
    return service not in _OFF


def _call(service, method, url, **kw):
    """Single guarded HTTP call. Returns a Response (status < 400) or None. Never raises."""
    if not available(service):
        STATS[service] += 1
        return None
    try:
        r = requests.request(method, url, timeout=kw.pop("timeout", 20), **kw)
        code = int(getattr(r, "status_code", 0) or 0)
    except Exception as e:
        _FAILS[service] += 1
        _skip(service, type(e).__name__)
        if _FAILS[service] >= 3:
            _trip(service, "3 consecutive failures")
        return None
    if code == 429:
        _trip(service, "rate limited (429)")
        return None
    if code in (401, 402, 403):
        _trip(service, f"{code}: key invalid or no credit")
        return None
    if code >= 400:
        _FAILS[service] += 1
        _skip(service, f"HTTP {code}")
        if _FAILS[service] >= 3:
            _trip(service, "3 consecutive failures")
        return None
    _FAILS[service] = 0
    OK[service] += 1
    return r


def _json(r):
    try:
        return r.json()
    except Exception:
        return None


# ---- Jina AI Reader: deep website context (works on JS-heavy sites) -------------------------------------
_JINA_LOCK, _JINA_LAST = threading.Lock(), [0.0]
def jina_read(url):
    """Returns {'emails': [...], 'socials': {...}, 'summary': str} or None."""
    try:
        if not ENRICH_WEBSITES or not url or not E.safe_allowed(url):
            return None
        headers = {"Accept": "text/plain", "X-Return-Format": "markdown"}
        if key("JINA_API_KEY"):
            headers["Authorization"] = "Bearer " + key("JINA_API_KEY")
        gap = 0.5 if key("JINA_API_KEY") else 3.5       # keyless Jina allows ~20 requests/minute: stay under it
        with _JINA_LOCK:                                 # safe to call from many site-reader threads
            wait = _JINA_LAST[0] + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            _JINA_LAST[0] = time.time()
        r = _call("jina", "GET", "https://r.jina.ai/" + url, headers=headers, timeout=20 if key("JINA_API_KEY") else 12)
        if r is None:
            return None
        text = (r.text or "")[:200000]
        if len(text) < 50:
            return None
        clean = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
        clean = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", clean)
        clean = re.sub(r"[#*_>`|]+", " ", clean)
        clean = " ".join(clean.split())
        return {"emails": E.emails_in(text), "socials": E.socials_in(text), "summary": clean[:350],
                "text": re.sub(r"[ \t]+", " ", re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text))[:6000]}
    except Exception as e:
        _skip("jina", type(e).__name__)
        return None


# ---- Hunter.io: email finder + verifier ---------------------------------------------------------------------
def hunter_find(domain):
    """Returns {'emails': [...], 'org': str} or None."""
    try:
        if not key("HUNTER_API_KEY") or not domain:
            return None
        r = _call("hunter", "GET", "https://api.hunter.io/v2/domain-search",
                  params={"domain": domain, "api_key": key("HUNTER_API_KEY"), "limit": 5})
        d = (_json(r) or {}).get("data") if r is not None else None
        if not d:
            return None
        rows = [x for x in d.get("emails", []) if isinstance(x, dict) and x.get("value")
                and float(x.get("confidence") or 0) >= 70]
        es, people, sources, associations, source_urls = [], [], {}, {}, {}
        for x in rows:
            address = str(x["value"]).strip().lower()
            if address not in E.emails_in(address):
                continue
            if address not in es:
                es.append(address)
            urls = [str(v.get("uri") or "") for v in (x.get("sources") or [])
                    if isinstance(v, dict) and E.public_source(v.get("uri"))]
            if not urls:
                continue
            sources[address] = urls[0]
            source_urls[address] = list(dict.fromkeys(urls))
            nm = E._clean_name(" ".join(str(x.get(k) or "").strip() for k in ("first_name", "last_name")))
            pos = str(x.get("position") or "").strip()
            if nm:
                associations[address] = {"name": nm, "source": urls[0], "sources": urls}
            if nm and pos and E._ROLE_RE.search(pos) and not re.search(r"former|retired|ex-", pos, re.I):
                people.append((nm, pos[:60], urls[0], address))
        return {"emails": es, "org": d.get("organization") or "", "people": people,
                "email_sources": sources, "email_associations": associations, "email_source_urls": source_urls}

    except Exception as e:
        _skip("hunter", type(e).__name__)
        return None


def hunter_finder(domain, full_name):
    """Return an unverified candidate address (score >=80), never identity/deliverability proof."""
    try:
        parts = [p for p in re.split(r"\s+", full_name or "") if p and p.rstrip(".").lower() not in ("dr", "mr", "mrs", "ms")]
        if not key("HUNTER_API_KEY") or not domain or len(parts) < 2:
            return None
        r = _call("hunter", "GET", "https://api.hunter.io/v2/email-finder",
                  params={"domain": domain, "first_name": parts[0], "last_name": parts[-1], "api_key": key("HUNTER_API_KEY")})
        d = (_json(r) or {}).get("data") if r is not None else None
        if d and d.get("email") and (d.get("score") or 0) >= 80:
            address = str(d["email"]).strip().lower()
            return address if address in E.emails_in(address) else None
        return None
    except Exception as e:
        _skip("hunter", type(e).__name__)
        return None


def _hunter_verify(email):
    if not key("HUNTER_API_KEY"):
        return None
    r = _call("hunter", "GET", "https://api.hunter.io/v2/email-verifier", params={"email": email, "api_key": key("HUNTER_API_KEY")})
    d = (_json(r) or {}).get("data") if r is not None else None
    s = (d or {}).get("status", "")
    return {"valid": "valid", "accept_all": "risky", "invalid": "invalid", "disposable": "invalid"}.get(s)


def _zerobounce_verify(email):
    if not key("ZEROBOUNCE_API_KEY"):
        return None
    r = _call("zerobounce", "GET", "https://api.zerobounce.net/v2/validate", params={"api_key": key("ZEROBOUNCE_API_KEY"), "email": email})
    s = ((_json(r) or {}).get("status") or "").lower() if r is not None else ""
    return {"valid": "valid", "catch-all": "risky", "invalid": "invalid", "spamtrap": "invalid", "abuse": "invalid",
            "do_not_mail": "invalid"}.get(s)


_MX = {}


def mx_check(email):
    """FREE local check (no key): can this email's domain receive mail at all?
    'mx-ok' | 'invalid' (no mail server / domain does not exist) | None (cannot tell, e.g. dnspython missing)."""
    try:
        dom = email.rsplit("@", 1)[1].lower()
        if dom in _MX:
            return _MX[dom]
        try:
            import dns.resolver
            import dns.exception
        except Exception:
            return None
        try:
            res = dns.resolver.resolve(dom, "MX", lifetime=5)
            exchanges = [str(record.exchange).rstrip(".") for record in res]
            out = "invalid" if exchanges and all(not x for x in exchanges) else "mx-ok" if exchanges else None
        except dns.resolver.NXDOMAIN:
            out = "invalid"
        except dns.resolver.NoAnswer:
            # Implicit MX may use an A or AAAA record. Resolver failures remain unknown.
            out, uncertain = None, False
            for record_type in ("A", "AAAA"):
                try:
                    addresses = dns.resolver.resolve(dom, record_type, lifetime=5)
                    if len(addresses):
                        out = "mx-ok"
                        break
                except dns.resolver.NXDOMAIN:
                    out = "invalid"
                    break
                except dns.resolver.NoAnswer:
                    continue
                except dns.exception.DNSException:
                    uncertain = True
            if out is None and not uncertain:
                out = "invalid"
        except dns.exception.DNSException:
            out = None
        _MX[dom] = out
        return out
    except Exception as e:
        _skip("mxcheck", type(e).__name__)
        return None


def verify_email(email):
    """'valid' | 'risky' | 'invalid' | 'mx-ok' | None. None means unknown: the caller keeps the email.
    Order: Disify (USE_DISIFY, free disposable check) -> Reacher (REACHER_URL, self-hosted SMTP check) ->
    ZeroBounce -> Hunter (only if their keys exist and are not tripped) -> free MX check."""
    try:
        if not email:
            return None
        domain_status = disify_check(email)
        if domain_status == "invalid":
            return "invalid"
        v = reacher_check(email)
        if v in ("valid", "risky", "invalid"):
            return v
        if verify_on():
            for fn in (_zerobounce_verify, _hunter_verify):
                v = fn(email)
                if v:
                    return v
        if os.getenv("USE_MX_CHECK", "true").strip().lower() not in ("0", "false", "no", "off"):
            return mx_check(email) or (domain_status if domain_status == "mx-ok" else None)
        return domain_status if domain_status == "mx-ok" else None
    except Exception as e:
        _skip("verify", type(e).__name__)
        return None


# ---- Apollo: company + employee count -------------------------------------------------------------------------
def apollo_org(domain):
    """Returns (name, employees) or None."""
    try:
        if not key("APOLLO_API_KEY") or not domain:
            return None
        r = _call("apollo", "GET", "https://api.apollo.io/api/v1/organizations/enrich", params={"domain": domain},
                  headers={"X-Api-Key": key("APOLLO_API_KEY"), "accept": "application/json"})
        o = ((_json(r) or {}).get("organization") or {}) if r is not None else {}
        if not o:
            return None
        return o.get("name") or "", o.get("estimated_num_employees") or ""
    except Exception as e:
        _skip("apollo", type(e).__name__)
        return None


# ---- Instagram activity: Meta official API first, Apify as optional fallback -----------------------------
def _stats(followers, posts, bio=""):
    """posts: list of (timestamp_iso, likes, comments[, caption, is_reel]). Returns dict or None:
    followers, engagement %, posts30, posts90, reels_share, cadence_cv, captions, bio (for agency credits)."""
    if not followers or not posts:
        return None
    import quality as Q
    now = datetime.now(timezone.utc)
    p30, p90, eng, dates, caps, reels = 0, 0, [], [], [], 0
    for row in posts:
        ts, likes, comments = row[0], row[1], row[2]
        cap, is_reel = (row[3] if len(row) > 3 else "") or "", bool(row[4]) if len(row) > 4 else False
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace("+0000", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t <= now:
                dates.append(t)
                p30 += (now - t).total_seconds() < 30 * 86400
                p90 += (now - t).total_seconds() < 90 * 86400
        except Exception:
            pass
        eng.append((likes or 0) + (comments or 0))
        caps.append(cap[:300])
        reels += is_reel
    return {"followers": int(followers), "engagement": round(sum(eng) / len(eng) / followers * 100, 2), "posts30": p30,
            "posts90": p90 if len(posts) >= 10 or p90 < len(posts) else None,   # unknown when every fetched post is recent
            "reels_share": round(reels / len(posts), 2) if all(len(row) > 4 and row[4] is not None for row in posts) else None,
            "sample_size": len(posts), "complete_window": False,
            "measurement_scope": "Sampled posts; complete calendar-window coverage not established",
            "cadence_cv": Q.cadence_cv(dates), "captions": caps, "bio": bio or ""}


def ig_meta(handle):
    try:
        if not (key("IG_ACCESS_TOKEN") and key("IG_USER_ID") and handle):
            return None
        f = (f"business_discovery.username({handle})"
             "{followers_count,biography,media.limit(25){like_count,comments_count,timestamp,caption,media_product_type}}")
        r = _call("meta_ig", "GET", f"https://graph.facebook.com/{key('GRAPH_VERSION') or 'v24.0'}/{key('IG_USER_ID')}",
                  params={"fields": f, "access_token": key("IG_ACCESS_TOKEN")}, timeout=30)
        d = ((_json(r) or {}).get("business_discovery") or {}) if r is not None else {}
        media = (d.get("media") or {}).get("data", [])
        return _stats(d.get("followers_count"), [(m.get("timestamp"), m.get("like_count"), m.get("comments_count"),
                                                  m.get("caption"), (m["media_product_type"] == "REELS") if m.get("media_product_type") else None) for m in media],
                      d.get("biography", ""))
    except Exception as e:
        _skip("meta_ig", type(e).__name__)
        return None


def ig_apify(handle):
    """Optional fallback for personal/private-API-blocked profiles. Costs Apify credits. Check Apify/Instagram terms."""
    try:
        if not (key("APIFY_API_TOKEN") and handle):
            return None
        actor = (key("APIFY_IG_ACTOR") or "apify/instagram-profile-scraper").replace("/", "~")
        r = _call("apify", "POST", f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                  params={"token": key("APIFY_API_TOKEN"), "timeout": 80}, json={"usernames": [handle]}, timeout=100)
        items = _json(r) if r is not None else None
        if not isinstance(items, list) or not items:
            return None
        it = items[0] or {}
        posts = [(p.get("timestamp"), p.get("likesCount"), p.get("commentsCount"), p.get("caption"),
                  (str(p.get("productType") or p.get("type")).lower() in ("clips", "video", "reel")) if (p.get("productType") or p.get("type")) else None)
                 for p in (it.get("latestPosts") or [])]
        return _stats(it.get("followersCount"), posts, it.get("biography", ""))
    except Exception as e:
        _skip("apify", type(e).__name__)
        return None


# ---- v3.1: Serper (Google results), Brave Search, ZenRows, ABN Lookup, Apify Google Maps ---------------------------
# Every helper: no key -> None/[] instantly; any error -> None/[] (circuit breakers as above). Never raises.
_CAPS = Counter()                 # per-run call counters (thread-safe enough: guarded by _CAP_LOCK)
_CAP_LOCK = threading.Lock()


def take(service, cap):
    """True (and counts one call) while `service` is under `cap` calls this run and not tripped."""
    if not available(service):
        return False
    with _CAP_LOCK:
        if _CAPS[service] >= cap:
            return False
        _CAPS[service] += 1
        return True


def serper_places(query, gl="au"):
    """Google Maps results through Serper: [{title, address, rating, ratingCount, category, phoneNumber, website, cid,
    placeId, latitude, longitude}] or []."""
    try:
        if not key("SERPER_API_KEY") or not query:
            return []
        r = _call("serper", "POST", "https://google.serper.dev/places", headers={"X-API-KEY": key("SERPER_API_KEY")},
                  json={"q": query, "gl": (gl or "au").lower(), "hl": "en"}, timeout=20)
        d = _json(r) if r is not None else None
        return [p for p in (d or {}).get("places", []) if isinstance(p, dict)]
    except Exception as e:
        _skip("serper", type(e).__name__)
        return []


def serper_search(query, gl="au", num=10):
    """Google web results through Serper: [{title, link, snippet}] or []."""
    try:
        if not key("SERPER_API_KEY") or not query:
            return []
        r = _call("serper", "POST", "https://google.serper.dev/search", headers={"X-API-KEY": key("SERPER_API_KEY")},
                  json={"q": query, "gl": (gl or "au").lower(), "hl": "en", "num": num}, timeout=20)
        d = _json(r) if r is not None else None
        return [{"title": x.get("title", ""), "link": x.get("link", ""), "snippet": x.get("snippet", "")}
                for x in (d or {}).get("organic", []) if isinstance(x, dict)]
    except Exception as e:
        _skip("serper", type(e).__name__)
        return []


def brave_search(query, country="AU", num=10):
    """Brave web results: [{title, link, snippet}] or []."""
    try:
        if not key("BRAVE_API_KEY") or not query:
            return []
        r = _call("brave", "GET", "https://api.search.brave.com/res/v1/web/search",
                  headers={"X-Subscription-Token": key("BRAVE_API_KEY"), "Accept": "application/json"},
                  params={"q": query, "country": (country or "AU").upper(), "count": num, "search_lang": "en"}, timeout=20)
        d = _json(r) if r is not None else None
        return [{"title": x.get("title", ""), "link": x.get("url", ""), "snippet": x.get("description", "")}
                for x in ((d or {}).get("web") or {}).get("results", []) if isinstance(x, dict)]
    except Exception as e:
        _skip("brave", type(e).__name__)
        return []


def web_search(query, cc="AU", cap=60, bucket="web"):
    """Serper first (Google results), then Brave. `bucket` has its own per-run cap (e.g. 'web' = website finder,
    'linkedin' = decision-maker search). [] when no key / cap reached / both down."""
    if not search_on() or not take("q:" + bucket, cap):
        return []
    if key("SERPER_API_KEY") and available("serper"):
        res = serper_search(query, cc)
        if res:
            return res
    if key("BRAVE_API_KEY") and available("brave"):
        return brave_search(query, cc)
    return []


def search_on():
    return bool(key("SERPER_API_KEY") or key("BRAVE_API_KEY"))


def zenrows_fetch(url, js=True):
    """HTML of a page that blocks normal readers (403 / JavaScript-only), via ZenRows. None on failure.
    Cost: 1 credit plain, 5 with JavaScript rendering (free plan: 5,000 credits/month)."""
    try:
        if not key("ZENROWS_API_KEY") or not url or not E.safe_allowed(url):
            return None
        params = {"apikey": key("ZENROWS_API_KEY"), "url": url}
        if js:
            params["js_render"] = "true"
        r = _call("zenrows", "GET", "https://api.zenrows.com/v1/", params=params, timeout=60)
        txt = r.text if r is not None else ""
        return txt if txt and len(txt) > 200 else None
    except Exception as e:
        _skip("zenrows", type(e).__name__)
        return None


def _jsonp(text):
    m = re.search(r"^[^(]*\((.*)\)\s*;?\s*$", text or "", re.S)
    try:
        import json as _j
        return _j.loads(m.group(1) if m else text)
    except Exception:
        return None


def abn_details(abn):
    """Official ABN Lookup (free, needs ABN_LOOKUP_GUID from abr.business.gov.au). Returns dict or None:
    {abn, status, status_from, entity_name, entity_type, gst_from, state, postcode, business_names}."""
    try:
        if not key("ABN_LOOKUP_GUID") or not abn:
            return None
        r = _call("abn", "GET", "https://abr.business.gov.au/json/AbnDetails.aspx",
                  params={"abn": re.sub(r"\D", "", abn), "callback": "c", "guid": key("ABN_LOOKUP_GUID")}, timeout=20)
        d = _jsonp(r.text) if r is not None else None
        if not d or not d.get("Abn"):
            if d and d.get("Message") and "guid" in d["Message"].lower():
                _trip("abn", "GUID not recognised")
            return None
        return {"abn": d.get("Abn", ""), "status": d.get("AbnStatus", ""), "status_from": d.get("AbnStatusEffectiveFrom", ""),
                "entity_name": d.get("EntityName", ""), "entity_type": d.get("EntityTypeName", ""), "gst_from": d.get("Gst") or "",
                "state": d.get("AddressState", ""), "postcode": d.get("AddressPostcode", ""),
                "business_names": [b for b in (d.get("BusinessName") or []) if isinstance(b, str)]}
    except Exception as e:
        _skip("abn", type(e).__name__)
        return None


def abn_by_name(name, state="", postcode=""):
    """Exact-name ABN match (ABN Lookup name search). Only returns an ABN when the registered name equals the business
    name (ignoring Pty/Ltd/punctuation) AND the state/postcode agree. Facts only: no fuzzy guesses."""
    try:
        if not key("ABN_LOOKUP_GUID") or not name:
            return None
        r = _call("abn", "GET", "https://abr.business.gov.au/json/MatchingNames.aspx",
                  params={"name": name, "maxResults": 10, "callback": "c", "guid": key("ABN_LOOKUP_GUID")}, timeout=20)
        d = _jsonp(r.text) if r is not None else None
        norm = lambda n: re.sub(r"[^a-z0-9]", "", re.sub(r"\b(pty|ltd|limited|the|p/l|trading as|t/a)\b", " ", (n or "").lower()))
        want = norm(name)
        for x in (d or {}).get("Names", []) or []:
            if norm(x.get("Name")) != want or not x.get("Abn"):
                continue
            if state and x.get("State") and x["State"].upper() != state.upper():
                continue
            if postcode and x.get("Postcode") and str(x["Postcode"]) != str(postcode):
                continue
            return x["Abn"]
        return None
    except Exception as e:
        _skip("abn", type(e).__name__)
        return None


def apify_maps(query, max_places=40, country="au"):
    """Google Maps businesses through Apify's 'compass/crawler-google-places' actor (pay per place, ~$1.50 per
    1,000 on the free plan's $5/month credit). Returns raw items (title, totalScore, reviewsCount, website, phone, ...)."""
    try:
        if not key("APIFY_API_TOKEN") or not query:
            return []
        actor = (key("APIFY_MAPS_ACTOR") or "compass/crawler-google-places").replace("/", "~")
        r = _call("apify_maps", "POST", f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                  params={"token": key("APIFY_API_TOKEN"), "timeout": 240},
                  json={"searchStringsArray": [query], "maxCrawledPlacesPerSearch": int(max_places), "language": "en",
                        "countryCode": (country or "au").lower(), "skipClosedPlaces": True}, timeout=270)
        items = _json(r) if r is not None else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    except Exception as e:
        _skip("apify_maps", type(e).__name__)
        return []


# ====================================================================== v5 free-tools modules (FREE_TOOLS_RESEARCH.md)
# All optional, all never-raise, all capped per run, all tripped off on 429/401/402/403 by _call(). Vendor free limits
# change often: see each vendor's pricing page (the Options tab says "check vendor page").
FLAGS = {"disify": False, "reacher": True}     # set from the Rules tab by leadbot (USE_DISIFY / USE_REACHER)
NAMES.update({"disify": "Disify", "reacher": "Reacher (self-hosted)", "nzbn": "NZBN register", "snov": "Snov.io",
              "prospeo": "Prospeo"})


def _emails_at(obj, domain):
    """Every email ON `domain` anywhere in a JSON reply (robust to vendors renaming fields)."""
    try:
        import json as _j
        text = obj if isinstance(obj, str) else _j.dumps(obj)
    except Exception:
        return []
    out = []
    for e in E.EMAIL_RE.findall(text or ""):
        e = e.lower().strip(".")
        if domain and e.endswith("@" + domain.lower()) and e not in out:
            out.append(e)
    return out


def disify_check(email):
    """Disify (free, no key, rate-limited): 'invalid' for a disposable domain / bad format / no DNS, else None."""
    try:
        if not FLAGS.get("disify") or not email:
            return None
        r = _call("disify", "GET", "https://www.disify.com/api/email/" + email, timeout=10)
        d = _json(r) if r is not None else None
        if not isinstance(d, dict):
            return None
        if d.get("disposable") is True or d.get("format") is False or d.get("dns") is False:
            return "invalid"
        return None
    except Exception as e:
        _skip("disify", type(e).__name__)
        return None


def reacher_check(email):
    """Self-hosted Reacher (check-if-email-exists) at REACHER_URL, e.g. on a VPS with port 25 open (GitHub runners
    block port 25, so SMTP checks can't run there). 'valid' | 'risky' | 'invalid' | None."""
    try:
        base = key("REACHER_URL").rstrip("/")
        if not (FLAGS.get("reacher", True) and base and email):
            return None
        hdr = {"Content-Type": "application/json"}
        if key("REACHER_SECRET"):
            hdr["x-reacher-secret"] = key("REACHER_SECRET")
        r = _call("reacher", "POST", base + "/v0/check_email", json={"to_email": email}, headers=hdr, timeout=60)
        d = _json(r) if r is not None else None
        v = str((d or {}).get("is_reachable", "")).lower()
        return {"safe": "valid", "risky": "risky", "invalid": "invalid"}.get(v)
    except Exception as e:
        _skip("reacher", type(e).__name__)
        return None


# ---- NZBN register (New Zealand, free subscription key) ----------------------------------------------
_NZBN = "https://api.business.govt.nz/gateway/nzbn/v5/entities"


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def nzbn_facts(name):
    """Exact registered-name match in the NZBN register -> dict(nzbn, entity_name, status, emails, phones, websites,
    directors[(full name, 'Director')]) or None. Field names are read defensively (any depth), so a renamed
    wrapper does not break it; a non-exact name match returns None (facts only)."""
    try:
        k = key("NZBN_API_KEY")
        if not k or not name:
            return None
        hdr = {"Ocp-Apim-Subscription-Key": k, "Accept": "application/json"}
        r = _call("nzbn", "GET", _NZBN, params={"search-term": name, "page-size": 10}, headers=hdr)
        d = _json(r) if r is not None else None
        want = re.sub(r"[^a-z0-9]+", "", re.sub(r"\b(limited|ltd|the)\b", "", name.lower()))
        hit = None
        for it in _walk(d or {}):
            nm = str(it.get("entityName") or "")
            got = re.sub(r"[^a-z0-9]+", "", re.sub(r"\b(limited|ltd|the)\b", "", nm.lower()))
            if it.get("nzbn") and want and got == want:
                hit = it
                break
        if not hit:
            return None
        r2 = _call("nzbn", "GET", f"{_NZBN}/{hit['nzbn']}", headers=hdr)
        det = _json(r2) if r2 is not None else None
        det = det if isinstance(det, dict) else {}
        out = {"nzbn": str(hit["nzbn"]), "entity_name": str(hit.get("entityName") or ""),
               "status": str(det.get("entityStatusDescription") or hit.get("entityStatusDescription") or ""),
               "emails": [], "phones": [], "websites": [], "directors": []}
        for o in _walk(det):
            if o.get("emailAddress") and "@" in str(o["emailAddress"]):
                out["emails"].append(str(o["emailAddress"]).strip().lower())
            if o.get("phoneNumber"):
                ph = "".join(str(o.get(x) or "") for x in ("phoneCountryCode", "phoneAreaCode", "phoneNumber"))
                ph = re.sub(r"[^\d+]", "", ph)
                if len(ph.lstrip("+")) >= 7:
                    out["phones"].append(ph if ph.startswith("+") else "+" + ph if ph.startswith("64") else ph)
            if o.get("url") and str(o["url"]).startswith("http"):
                out["websites"].append(str(o["url"]))
            role = str(o.get("roleType") or o.get("role") or "")
            person = o.get("rolePerson") if isinstance(o.get("rolePerson"), dict) else None
            if person and "director" in role.lower() and str(o.get("roleStatus") or "active").lower() in ("active", "acting", ""):
                full = " ".join(str(person.get(x) or "").strip() for x in ("firstName", "lastName")).strip()
                if len(full.split()) >= 2 and full.title() not in [p[0] for p in out["directors"]]:
                    out["directors"].append((full.title(), "Director"))
        return out
    except Exception as e:
        _skip("nzbn", type(e).__name__)
        return None


# ---- email finders for a KNOWN decision maker (only called for leads already at ICP >= 7/10) ---------
def _parts(full_name):
    p = [x for x in re.split(r"\s+", full_name or "") if x and x.rstrip(".").lower() not in ("dr", "mr", "mrs", "ms", "prof")]
    return (p[0], p[-1]) if len(p) >= 2 else (None, None)


_SNOV_TOKEN = {"t": "", "exp": 0.0}


def snov_finder(domain, full_name):
    """Snov.io email-from-name (SNOV_CLIENT_ID + SNOV_CLIENT_SECRET). Returns an email ON `domain`, or None."""
    try:
        first, last = _parts(full_name)
        cid, sec = key("SNOV_CLIENT_ID"), key("SNOV_CLIENT_SECRET")
        if not (cid and sec and domain and first):
            return None
        if not _SNOV_TOKEN["t"] or time.time() > _SNOV_TOKEN["exp"]:
            r = _call("snov", "POST", "https://api.snov.io/v1/oauth/access_token",
                      data={"grant_type": "client_credentials", "client_id": cid, "client_secret": sec})
            d = _json(r) if r is not None else None
            if not (isinstance(d, dict) and d.get("access_token")):
                return None
            _SNOV_TOKEN.update(t=d["access_token"], exp=time.time() + int(d.get("expires_in") or 3000) - 60)
        r = _call("snov", "POST", "https://api.snov.io/v1/get-emails-from-names",
                  data={"access_token": _SNOV_TOKEN["t"], "firstName": first, "lastName": last, "domain": domain})
        d = _json(r) if r is not None else None
        bad = str(d).lower().count("invalid") if d else 0
        found = _emails_at(d, domain) if d else []
        return found[0] if found and not bad else None
    except Exception as e:
        _skip("snov", type(e).__name__)
        return None


def prospeo_finder(domain, full_name):
    """Prospeo email finder (PROSPEO_API_KEY). Returns an email ON `domain`, or None."""
    try:
        first, last = _parts(full_name)
        k = key("PROSPEO_API_KEY")
        if not (k and domain and first):
            return None
        r = _call("prospeo", "POST", "https://api.prospeo.io/email-finder",
                  headers={"X-KEY": k, "Content-Type": "application/json"},
                  json={"first_name": first, "last_name": last, "company": domain})
        d = _json(r) if r is not None else None
        if not isinstance(d, dict) or d.get("error") is True:
            return None
        found = _emails_at(d.get("response", d), domain)
        return found[0] if found else None
    except Exception as e:
        _skip("prospeo", type(e).__name__)
        return None
