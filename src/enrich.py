"""Finds public contact info: emails + social links from the channel description, the creator's own
website / link-in-bio page (robots.txt respected), plus optional Hunter / Apollo / Instagram lookups."""
import os
import re
import time
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

import requests

UA = "Mozilla/5.0 (compatible; LeadResearchBot/1.0)"
MAX_BYTES = 1_500_000
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+|(?:www\.)[^\s<>\"')\]]+|"
                    r"\b(?:[a-z]{2,3}\.)?(?:instagram|linkedin|facebook|twitter|tiktok|x)\.com/[^\s<>\"')\]]+", re.I)
JUNK_EXT = ("png", "jpg", "jpeg", "gif", "webp", "svg", "js", "css", "woff", "woff2", "ico", "mp4", "pdf")
JUNK_DOMAINS = ("example.com", "sentry.io", "wixpress.com", "domain.com", "email.com", "yoursite.com",
                "youtube.com", "google.com", "gstatic.com", "schema.org", "w3.org", "yourdomain.com", "yourcompany.com",
                "company.com", "sample.com", "test.com", "mysite.com", "website.com", "address.com")
BAD_LOCAL = ("noreply", "no-reply", "donotreply", "abuse", "privacy", "postmaster", "webmaster", "mailer-daemon")
ROLE_LOCAL = ("business", "collab", "partnership", "partnerships", "sponsor", "sponsors", "hello", "hi", "contact",
              "info", "team", "inquiries", "enquiries", "work", "booking", "bookings", "mgmt", "management", "press", "reception", "admin", "enquiries", "enquiry",
              "appointments", "office", "sales", "clinic", "practice", "marketing", "manager", "reservations")
FREE_MAIL = ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com", "proton.me",
             "protonmail.com", "live.com", "msn.com", "me.com", "gmx.com")
AGGREGATORS = ("linktr.ee", "beacons.ai", "bio.link", "stan.store", "carrd.co", "lnk.bio", "linktree.com",
               "campsite.bio", "taplink.cc", "solo.to", "withkoji.com", "allmylinks.com", "komi.io", "hoo.be")
SOCIALS = {  # platform: (host regex, handle path regex)
    "Instagram": (r"(?:www\.)?instagram\.com", r"/([A-Za-z0-9._]{2,30})/?(?:\?|$)"),
    "LinkedIn": (r"(?:[a-z]{2,3}\.)?linkedin\.com", r"/((?:in|company|school)/[A-Za-z0-9\-_%.]+)/?"),
    "Facebook": (r"(?:www\.|m\.)?facebook\.com", r"/((?!sharer|share|dialog|tr\b|plugins|watch|groups/?$)[A-Za-z0-9.\-]{3,}(?:/[A-Za-z0-9.\-]+)?)/?(?:\?|$)"),
    "Twitter / X": (r"(?:www\.)?(?:twitter|x)\.com", r"/((?!intent|share|home|i/)[A-Za-z0-9_]{2,15})/?(?:\?|$)"),
    "TikTok": (r"(?:www\.)?tiktok\.com", r"/(@[A-Za-z0-9._]{2,24})/?(?:\?|$)"),
    "YouTube": (r"(?:www\.|m\.)?youtube\.com", r"/((?:@|channel/|c/|user/)[A-Za-z0-9._\-]+)/?(?:\?|$)"),
}
OTHER = {"threads.net": "Threads", "pinterest.com": "Pinterest", "snapchat.com": "Snapchat", "discord.gg": "Discord",
         "discord.com": "Discord", "patreon.com": "Patreon", "t.me": "Telegram", "twitch.tv": "Twitch",
         "substack.com": "Substack", "skool.com": "Skool", "medium.com": "Medium", "calendly.com": "Calendly"}
SKIP_WEBSITE = ("youtube.com", "youtu.be", "amzn.to", "amazon.", "bit.ly", "tinyurl.com", "geni.us", "go.", "shareasale",
                "tubebuddy", "vidiq", "epidemicsound", "artlist", "canva.com", "notion.so", "apple.com", "spotify.com",
                "google.com", "goo.gl", "ow.ly", "t.co", "rb.gy", "cutt.ly", "paypal.", "buymeacoffee", "ko-fi",
                "kit.co", "kit.com", "typeform.com", "jotform", "forms.gle", "wa.me", "whatsapp.com", "docs.google.com",
                "drive.google.com", "gumroad.com", "patreon.com", "onlyfans.com", "fanvue.com")
_robots = {}


def _host(u):
    h = (urlparse(u if "://" in u else "http://" + u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


PLATFORM_HOSTS = ("instagram.com", "facebook.com", "linkedin.com", "tiktok.com", "youtube.com", "youtu.be",
                  "twitter.com", "x.com", "threads.net", "pinterest.com")


def official_page_url(url):
    """Website reader excludes social platforms (use separately authorised APIs)."""
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and bool(host) and not any(
        host == platform or host.endswith("." + platform) for platform in PLATFORM_HOSTS)


def allowed(url):
    if not official_page_url(url):
        return False
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    rp = _robots.get(origin)
    if rp is None:
        rp = RobotFileParser()
        try:
            r = requests.get(origin + "/robots.txt", headers={"User-Agent": UA}, timeout=(4, 6), allow_redirects=False)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            elif r.status_code in (404, 410):
                rp.parse([])
            else:
                rp.disallow_all = True
        except requests.RequestException:
            rp.disallow_all = True
        _robots[origin] = rp
    return rp.can_fetch(UA, url)


def safe_allowed(url):
    try:
        return allowed(url)
    except Exception:
        return False


def fetch(url, _redirects=0):
    """Return page text or None. Never raises."""
    try:
        if _redirects > 4 or not allowed(url):
            return None
        with requests.get(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"}, timeout=(5, 10),
                          stream=True, allow_redirects=False) as r:
            if r.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, r.headers.get("location", ""))
                return fetch(target, _redirects + 1) if target != url else None
            if r.status_code != 200 or "text" not in r.headers.get("content-type", "text"):
                return None
            data = b""
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > MAX_BYTES:
                    break
            ct = r.headers.get("content-type", "").lower()
            enc = r.encoding if "charset=" in ct else None            # requests guesses ISO-8859-1 without a charset: wrong for most sites
            if not enc:
                m = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_-]+)", data[:4096], re.I)
                enc = m.group(1).decode() if m else "utf-8"
            try:
                return data.decode(enc, errors="ignore")
            except LookupError:
                return data.decode("utf-8", errors="ignore")
    except Exception:
        return None


def deobfuscate(t):
    t = re.sub(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", "@", t, flags=re.I)
    t = re.sub(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", ".", t, flags=re.I)
    return t.replace("&#64;", "@").replace("%40", "@")


def emails_in(text):
    out = []
    for e in EMAIL_RE.findall(deobfuscate(text or "")):
        e = e.strip(".,;:").lower()
        local, _, dom = e.partition("@")
        if (local.startswith(".") or local.endswith(".") or ".." in local
                or any(label.startswith("-") or label.endswith("-") for label in dom.split("."))):
            continue
        if (e.rsplit(".", 1)[-1] in JUNK_EXT or any(dom == j or dom.endswith("." + j) for j in JUNK_DOMAINS)
                or re.match(r"(?:example|sample|test|yourdomain|domain)\.", dom)):     # placeholders like example.co.uk
            continue
        if any(local.startswith(b) for b in BAD_LOCAL) or re.search(r"\d{6,}", local) or e in out:
            continue
        out.append(e)
    return out


def socials_in(text, found=None):
    """Extract social profile links from text/HTML. Returns {platform: url}."""
    found = found if found is not None else {}
    for u in URL_RE.findall(text or "") + re.findall(r'href=["\']([^"\']+)', text or ""):
        u = u.rstrip(".,;)")
        if u.startswith("/") or u.startswith("#") or u.startswith("mailto") or u.startswith("tel"):
            continue
        full = u if u.startswith("http") else "https://" + u
        host, path = _host(full), urlparse(full).path + ("?" if "?" in full else "")
        for plat, (hre, pre) in SOCIALS.items():
            if re.fullmatch(hre, host) and plat not in found:
                m = re.match(pre, path if path.endswith("?") or "?" in path else path + "?") or re.match(pre, path)
                if m and m.group(1).lower() not in ("p", "reel", "reels", "explore", "stories", "accounts", "about", "login"):
                    base = {"Instagram": "https://www.instagram.com/", "LinkedIn": "https://www.linkedin.com/",
                            "Facebook": "https://www.facebook.com/", "Twitter / X": "https://x.com/",
                            "TikTok": "https://www.tiktok.com/", "YouTube": "https://www.youtube.com/"}[plat]
                    found[plat] = base + m.group(1)
        for dom, name in OTHER.items():
            if host == dom or host.endswith("." + dom):
                found.setdefault("Other:" + name, full)
    return found


def pick_website(desc, title):
    """Choose the creator's own website from the channel description (best effort)."""
    cands = []
    for m in URL_RE.finditer(desc or ""):
        u = m.group(0).rstrip(".,;)")
        if not u.startswith("http"):
            u = "https://" + u
        h = _host(u)
        skip = any(("." + s) in ("." + h) if s.endswith(".")              # 'amazon.' / 'go.' = a whole label prefix
                   else (h == s or h.endswith("." + s)) if "." in s       # 't.co', 'bit.ly' = exact domain
                   else s in h.split(".") for s in SKIP_WEBSITE)          # 'tubebuddy' = a whole label
        if not h or skip or any(re.fullmatch(p[0], h) for p in SOCIALS.values()):
            continue
        if any(h == o or h.endswith("." + o) for o in OTHER):
            continue
        before = (desc[max(0, m.start() - 40):m.start()]).lower()
        toks = [t for t in re.findall(r"[a-z0-9]{4,}", (title or "").lower())]
        score = (3 if re.search(r"website|my site|business|work with|coaching|agency|book|hire|course|official", before) else 0) \
            + (3 if any(t in h for t in toks) else 0) + (2 if h in AGGREGATORS else 0)
        cands.append((score, -m.start(), u))
    cands.sort(reverse=True)
    return cands[0][2] if cands else ""


def rank_emails(emails, site_domain):
    def key(e):
        local, _, dom = e.partition("@")
        return (dom == site_domain or dom.endswith("." + site_domain) if site_domain else False,
                dom not in FREE_MAIL, local in ROLE_LOCAL)
    return sorted(emails, key=key, reverse=True)


def email_type(e, site_domain):
    local, _, dom = e.partition("@")
    if site_domain and (dom == site_domain or dom.endswith("." + site_domain)):
        return "Business domain" + (" (role)" if local in ROLE_LOCAL else "")
    if dom in FREE_MAIL:
        return "Free mail (identity unverified)"
    return "Other domain"


def enrich(desc, title, enrich_site=True, max_pages=4):
    """Returns dict: website, emails[list], email_source, socials{}, notes[list]."""
    notes, emails, source = [], [], ""
    socials = socials_in(desc)
    d_emails = emails_in(desc)
    if d_emails:
        emails, source = d_emails, "YouTube description"
    site = pick_website(desc, title)
    domain = "" if (not site or _host(site) in AGGREGATORS) else _host(site)
    if enrich_site and site:
        pages = [site]
        if domain:
            base = f"{urlparse(site).scheme}://{urlparse(site).netloc}"
            pages += [urljoin(base, p) for p in ("/contact", "/about", "/contact-us", "/work-with-me")]
        got_any = False
        for u in pages[:max_pages + 1]:
            html = fetch(u)
            if html is None:
                continue
            got_any = True
            socials_in(html, socials)
            for e in emails_in(html):
                if e not in emails:
                    emails.append(e)
                    source = source or ("Link page" if _host(site) in AGGREGATORS else "Website")
            if _host(site) in AGGREGATORS:
                break
            time.sleep(0.5)
        if not got_any:
            notes.append("Website unreachable or blocked by robots.txt")
    emails = rank_emails(emails, domain)
    return {"website": site, "domain": domain, "emails": emails, "email_source": source if emails else "",
            "socials": socials, "notes": notes}


def hunter(domain, key):
    """Optional: public emails Hunter already knows for a domain. Returns (emails, organization)."""
    try:
        r = requests.get("https://api.hunter.io/v2/domain-search",
                         params={"domain": domain, "api_key": key, "limit": 5}, timeout=20)
        if r.status_code != 200:
            return [], ""
        d = r.json().get("data", {})
        es = [x["value"].lower() for x in d.get("emails", []) if x.get("value") and x.get("confidence", 0) >= 70]
        return es, d.get("organization") or ""
    except Exception:
        return [], ""


def apollo(domain, key):
    """Optional: company name + employee estimate (Apollo organization enrichment). Returns (name, employees)."""
    try:
        r = requests.get("https://api.apollo.io/api/v1/organizations/enrich", params={"domain": domain},
                         headers={"X-Api-Key": key, "accept": "application/json"}, timeout=20)
        if r.status_code != 200:
            return "", ""
        o = r.json().get("organization") or {}
        return o.get("name") or "", o.get("estimated_num_employees") or ""
    except Exception:
        return "", ""


def instagram_stats(handle):
    """Official Meta Business Discovery. Only business/creator accounts are readable; else None."""
    token, uid = os.getenv("IG_ACCESS_TOKEN", "").strip(), os.getenv("IG_USER_ID", "").strip()
    if not (token and uid and handle):
        return None
    f = (f"business_discovery.username({handle})"
         "{followers_count,media_count,media.limit(12){like_count,comments_count,timestamp}}")
    try:
        r = requests.get(f"https://graph.facebook.com/{os.getenv('GRAPH_VERSION', 'v24.0')}/{uid}",
                         params={"fields": f, "access_token": token}, timeout=30)
        if r.status_code != 200:
            return None
        d = r.json().get("business_discovery", {})
        fol = d.get("followers_count") or 0
        media = d.get("media", {}).get("data", [])
        if not fol or not media:
            return None
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        posts30 = 0
        for m in media:
            try:
                if (now - datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")).days < 30:
                    posts30 += 1
            except Exception:
                pass
        er = sum((m.get("like_count") or 0) + (m.get("comments_count") or 0) for m in media) / len(media) / fol
        return {"followers": fol, "engagement": round(er * 100, 2), "posts30": posts30}
    except Exception:
        return None


def _cf_decode(h):
    """Decode Cloudflare 'email protection' (very common on AU business sites)."""
    try:
        b = bytes.fromhex(h)
        return "".join(chr(c ^ b[0]) for c in b[1:])
    except Exception:
        return ""


# ------------------------------------------------------------------ v2: decision makers, phones, spend signals
DM_ROLE = (r"(?:co-?founder|founder|owner|co-?owner|practice owner|director|managing director|principal(?: dentist| solicitor|"
           r" lawyer| architect| surgeon| designer| doctor| agent)?|chief executive(?: officer)?|ceo|general manager|"
           r"practice manager|marketing manager|marketing director|operations manager|managing partner|"
           r"(?:sales )?licensee(?: in charge)?|head of marketing|clinical director|medical director|centre director)")
_ROLE_RE = re.compile(r"\b" + DM_ROLE + r"\b", re.I)
DM_ROLE = "(?i:" + DM_ROLE + ")"                  # role words in any case; names below still need Capitals
_TOK = r"[A-Z](?:[a-z]{1,14}|['’][A-Z][a-z]{1,14})(?:-[A-Z]?[a-z]{1,14})?"      # Sarah, O'Brien, Smith-Jones
_NAME = r"(?:Dr\.? +|Mr\.? +|Mrs\.? +|Ms\.? +)?" + _TOK + r"(?: +" + _TOK + r"){1,2}"
GENERIC_LOCAL = ("accounts", "accounting", "billing", "careers", "jobs", "hr", "support", "help", "service", "customerservice",
                 "orders", "shop", "store", "studio", "design", "projects", "quotes", "quote", "newpatients", "patients",
                 "frontdesk", "care", "events", "weddings", "functions", "rentals", "leasing", "property", "sales", "media",
                 "pr", "social", "finance", "accountsreceivable", "invoices", "general", "mail", "email", "web", "online",
                 "invoicing", "invoice", "website", "agents", "agent", "membership", "members", "concierge", "catering",
                 "privatedining", "hospitality", "frontdesk", "lettings", "valuations", "rentals", "admissions")
_DM_PATTERNS = [
    re.compile(r"(?P<name>" + _NAME + r")\s*(?:,|-|–|—|\||\(|:)\s*(?:our\s+|the\s+)?(?P<role>" + DM_ROLE + r")\b"),
    re.compile(r"(?P<role>" + DM_ROLE + r")\s*(?:[:\-–—|,]|\s+and\s+\w+)?\s*(?:is\s+)?(?P<name>" + _NAME + r")\b"),
    re.compile(r"(?P<name>" + _NAME + r")\s+(?:is)\s+(?:the|our|a)\s+(?:proud\s+)?(?P<role>" + DM_ROLE + r")\b"),
    re.compile(r"(?m)^\s*(?P<name>" + _NAME + r")\s*\n\s*(?:[A-Za-z&,/ ]{0,25}\s)?(?P<role>" + DM_ROLE + r")[A-Za-z&,/ ]{0,30}$"),
]
_NOT_NAME = set("""our the meet about contact team us home services service book call welcome dental dentist clinic clinics
centre center medical health real estate property group studio australia sydney melbourne brisbane perth adelaide hobart
darwin canberra gold coast new south wales victoria queensland privacy policy terms read more learn view our story
company business office director founder owner manager principal practice marketing general managing partner
cosmetic surgery surgeon plastic skin laser hair beauty law lawyers legal finance mortgage builders homes design
furniture kitchen bathroom solar pool landscaping wedding venue college school early learning hotel resort jewellery
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august
september october november december free quote get started click here sign up best award winning builder home agent
agency agents operated occupied family local now today online appointment appointments enquire enquiry testimonials
reviews blog news gallery portfolio projects careers join message staff people leadership board specialist specialists
london manchester birmingham leeds glasgow edinburgh bristol liverpool brighton dublin cork galway auckland wellington
christchurch tauranga toronto vancouver calgary edmonton ottawa montreal dubai abu dhabi sharjah los angeles
chicago miami tampa orlando scottsdale francisco seattle atlanta philadelphia nashville vegas united states kingdom canada zealand ireland emirates street road
avenue fitness gym pilates yoga restaurant dining kitchen bar grill realty realtors estate district village square
plaza heights harbour harbor crossroads ranch quarter crossing beach officer chef coach captain sergeant detective""".split())


def html_text(html):
    """Visible text with one line per block element (so 'Name' / 'Role' pairs on team cards stay adjacent)."""
    t = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", html or "")
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|li|span|td|tr|section|article|figure|figcaption|a|strong|b)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    import html as _h
    t = _h.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return "\n".join(l.strip() for l in t.split("\n") if l.strip())


def _clean_name(n, company=""):
    n = re.sub(r"\s+", " ", n).strip(" ,.-|")
    toks = [t for t in re.split(r"\s+", n) if t]
    core = [t for t in toks if t.rstrip(".").lower() not in ("dr", "mr", "mrs", "ms")]
    comp = set(re.findall(r"[a-z]+", company.lower()))
    if not 2 <= len(core) <= 3:
        return ""
    if any(t.lower().strip("'’-") in _NOT_NAME or t.lower() in comp for t in core):
        return ""
    return n


def people_in(html, text=None, company=""):
    """Decision makers written ON the page. Returns [(name, role, how)]. Facts only: name + role must appear together."""
    out = []
    for blk in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html or ""):
        try:
            import json
            data = json.loads(blk.strip())
        except Exception:
            continue
        stack = [(data, "")]                       # (node, relation it was found under: founder / employee / ...)
        while stack:
            d, rel = stack.pop()
            if isinstance(d, list):
                stack += [(x, rel) for x in d]
                continue
            if not isinstance(d, dict):
                continue
            for k in ("founder", "founders", "employee", "member", "author", "@graph"):
                if k in d:
                    stack.append((d[k], "founder" if k.startswith("founder") else rel if k == "@graph" else k))
            types = d.get("@type") if isinstance(d.get("@type"), list) else [d.get("@type")]
            if "Person" in types and d.get("name"):
                role = str(d.get("jobTitle") or "").strip()
                nm = _clean_name(str(d["name"]), company)
                if nm and _ROLE_RE.search(role):
                    out.append((nm, role[:60], "structured data"))
                elif nm and rel == "founder":       # listed as the organisation's founder = a fact with a role
                    out.append((nm, "Founder", "structured data"))
    text = text if text is not None else html_text(html)
    for pat in _DM_PATTERNS:
        for m in pat.finditer(text):
            context = text[max(0, m.start() - 20):m.end()]
            if re.search(r"\b(?:former|previous|retired|late|ex-)\b", context, re.I):
                continue
            nm = _clean_name(m.group("name"), company)
            if nm:
                out.append((nm, m.group("role").strip().title(), "website text"))
    seen, res = set(), []
    for n, r, how in out:
        if n.lower() not in seen:
            seen.add(n.lower())
            res.append((n, r, how))
    return res[:5]


def phones_in(html):
    """Phones the business itself published: tel: links, then WhatsApp click-to-chat links (wa.me/<number>,
    api.whatsapp.com/send?phone=<number>; very common in the Gulf). WhatsApp numbers are always international (+)."""
    out = []
    for p in re.findall(r'href=["\']tel:([^"\']+)', html or "", re.I):
        p = re.sub(r"[^\d+]", "", p)
        if 8 <= len(p.lstrip("+")) <= 12 and p not in out:
            out.append(p)
    for p in re.findall(r'(?:wa\.me/|whatsapp\.com/send/?\?(?:[^"\'\s]*?&(?:amp;)?)?phone=)\+?(\d{8,15})', html or "", re.I):
        if p not in [x.lstrip("+") for x in out]:
            out.append("+" + p)
    return out[:3]


SPEND_SIGNS = {"Google Ads tag": r"googleadservices|gtag\([^)]*['\"]AW-|AW-\d{6,}", "Meta Pixel": r"fbq\(|connect\.facebook\.net/[^\"']*/fbevents",
               "TikTok Pixel": r"analytics\.tiktok\.com",
               "Online booking": r"calendly\.com|halaxy\.com|cliniko\.com|hotdoc\.com\.au|healthengine\.com\.au|gettimely\.com|"
               r"fresha\.com|mindbodyonline\.com|simplybook\.|setmore\.com|acuityscheduling\.com|>\s*book (?:online|now)\s*<",
               "Live chat": r"intercom\.io|widget\.intercom|tawk\.to|livechatinc\.com|js\.driftt\.com|client\.crisp\.chat"}


def spend_signals(html):
    low = (html or "")[:600000]
    return [k for k, pat in SPEND_SIGNS.items() if re.search(pat, low, re.I)]


# neighbourhood / branch mailboxes (westloop@, rivernorth@, dtla@, downtown@) are not a person
PLACE_LOCAL = ("loop", "north", "south", "east", "west", "downtown", "midtown", "uptown", "dtla", "central", "city", "park",
               "village", "beach", "harbour", "harbor", "branch", "location", "studio", "store", "nyc", "ldn", "london", "soho")


def classify_emails(emails, domain, person="", free_ok=False, associations=None):
    """Return personal, generic, others. Personal requires explicit sourced association.

    associations maps an address to {name, source}; source must be a public URL.
    Domain membership, a free mailbox or matching name substrings prove no identity.
    The legacy free_ok argument is retained for call compatibility.
    """
    normalized = []
    for raw in emails or []:
        e = str(raw).strip().lower()
        if EMAIL_RE.fullmatch(e) and e in emails_in(e) and e not in normalized:
            normalized.append(e)
    associations = {str(k).strip().lower(): v for k, v in (associations or {}).items()}
    personal, generic = [], []
    for e in normalized:
        local = e.split("@", 1)[0]
        flat = re.sub(r"[^a-z]", "", local)
        role = (local in ROLE_LOCAL + GENERIC_LOCAL or flat in ROLE_LOCAL + GENERIC_LOCAL
                or any(flat.startswith(r) or flat.endswith(r) for r in ROLE_LOCAL + GENERIC_LOCAL if len(r) > 4)
                or flat in PLACE_LOCAL)
        evidence = associations.get(e)
        associated = (isinstance(evidence, dict) and person
                      and str(evidence.get("name", "")).strip().casefold() == person.strip().casefold()
                      and public_source(evidence.get("source", "")))
        if role:
            generic.append(e)
        elif associated:
            personal.append(e)
    p, g = (personal or [""])[0], (generic or [""])[0]
    return p, g, [e for e in normalized if e not in (p, g)]


def public_source(url):
    """Whether a provenance value is an explicit HTTP(S) source URL."""
    parsed = urlparse(str(url or ""))
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def email_associations_in(html, source, company=""):
    """Only explicit structured Person email or a mailto label naming that person.

    Mere proximity on a team page is deliberately insufficient.
    """
    import json
    out = {}
    if not public_source(source):
        return out
    for block in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html or ""):
        try:
            stack = [json.loads(block)]
        except (ValueError, TypeError):
            continue
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
                types = item.get("@type", [])
                types = [types] if isinstance(types, str) else types
                name = _clean_name(str(item.get("name") or ""), company)
                if "Person" in types and name:
                    for email in emails_in(str(item.get("email") or "")):
                        out[email] = {"name": name, "source": source}
    people = people_in(html, company=company)
    for email, label in re.findall(r'(?is)<a[^>]+href=["\']mailto:([^"\'?]+)[^"\']*["\'][^>]*>(.*?)</a>', html or ""):
        label = html_text(label).strip()
        for name, _, _ in people:
            if label.casefold() in (name.casefold(), "email " + name.casefold()):
                for address in emails_in(email):
                    out[address] = {"name": name, "source": source}
    return out


_LINK_WORDS = re.compile(r"contact|about|team|our-?story|who-?we-?are|staff|people|leadership|founder|meet|owner|directors?|agents?|"
                         r"locations|venues|restaurants|studios", re.I)


def site_links(html, page_url, domain, limit=6):
    """Same-site links whose URL or anchor text says contact/about/team (in page order, deduped)."""
    out = []
    for href, label in re.findall(r'(?is)<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html or "")[:600]:
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(page_url, href.strip())
        if _host(full) != domain or re.search(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|mp4)(\?|$)", full, re.I):
            continue
        text = re.sub(r"<[^>]+>", " ", label)
        if _LINK_WORDS.search(urlparse(full).path) or _LINK_WORDS.search(text[:60]):
            full = full.split("#")[0]
            if full not in out:
                out.append(full)
    return out[:limit]


def bio_site(html, company):
    """Link-in-bio page (linktr.ee, beacons.ai, stan.store, ...) -> the creator's OWN website, or ''.
    Links are read from the page HTML and its embedded JSON. Kept only when the domain contains a distinctive word of
    the channel/business name (precision first: never a shop, sponsor or podcast link)."""
    toks = [t for t in _tokens(company or "") if len(t) >= 4]
    if not html or not toks:
        return ""
    for u in re.findall(r'https?://[^\s"\'<>\\)]+', html.replace("\\/", "/")):
        h = _host(u)
        if (not h or h in AGGREGATORS or any(h.endswith("." + a) for a in AGGREGATORS)
                or any(re.fullmatch(p[0], h) for p in SOCIALS.values()) or any(h.endswith(o) for o in OTHER)
                or any(s in h for s in SKIP_WEBSITE) or any(h.endswith(j) for j in JUNK_DOMAINS)):
            continue
        if any(t in h.replace("-", "") for t in toks):
            return f"{urlparse(u).scheme}://{urlparse(u).netloc}"
    return ""


def enrich_site(site, max_pages=4, read_site=True, company="", find_people=True, time_limit=0, fallback=None):
    """For a KNOWN business website. Returns website, domain, emails, source, socials, notes,
    people [(name, role, how)], phones, signals, text (visible text of the pages read, for the Case Study)."""
    notes, emails, socials, source = [], [], {}, ""
    people, phones, signals, texts, abns, agency, booking = [], [], [], [], [], [], ""
    email_sources, associations = {}, {}
    host = _host(site) if site else ""
    is_social = any(re.fullmatch(p[0], host) for p in SOCIALS.values()) if host else False
    if site and is_social:                      # the 'website' on Maps is really a Facebook/Instagram page
        socials_in(site, socials)
        return {"website": "", "domain": "", "emails": [], "email_source": "", "socials": socials,
                "notes": ["Listed 'website' is a social page"], "people": [], "phones": [], "signals": [], "text": ""}
    domain = "" if (not host or host in AGGREGATORS) else host
    if read_site and site:
        base = f"{urlparse(site).scheme}://{urlparse(site).netloc}"
        extra = ("/contact", "/about", "/about-us", "/team", "/our-team", "/contact-us", "/meet-the-team", "/privacy-policy",
                 "/terms")
        pages = [site] + ([urljoin(base, p) for p in extra] if domain else [])
        got, form_only, done = False, False, set()
        i = 0
        t_end = time.time() + time_limit if time_limit else None
        while i < len(pages) and len(done) < max(1, max_pages):
            if t_end and done and time.time() > t_end:          # slow site: keep what we have, move on
                notes.append("Slow website: stopped after %d page(s)" % len(done))
                break
            u = pages[i]
            i += 1
            if u.rstrip("/") in done:
                continue
            done.add(u.rstrip("/"))
            html = fetch(u)
            if html is None and i == 1 and fallback is not None and safe_allowed(u):
                html = fallback(u)                       # blocked / JavaScript-only home page: optional ZenRows
                if html:
                    notes.append("Read via ZenRows")
            if html is None:
                continue
            if i == 1 and domain:                    # home page: read the contact/about/team pages it REALLY links to first
                pages[1:1] = [l for l in site_links(html, u, domain) if l.rstrip("/") not in done]
            elif i == 1 and host in AGGREGATORS:     # creator link-in-bio page: follow their own website + its contact pages
                own = bio_site(html, company)
                if own and safe_allowed(own):
                    domain = _host(own)
                    pages += [own] + [urljoin(own, p) for p in extra[:4]]
                    max_pages = max(max_pages, 4)
                    notes.append(f"Own website found on the link-in-bio page: {own}")
            got = True
            for h in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html):
                dec = _cf_decode(h)
                if dec:
                    html += " " + dec
            socials_in(html, socials)
            found = emails_in(html)
            for address in found:
                email_sources.setdefault(address, u)
            associations.update(email_associations_in(html, u, company))
            for e in found:
                if e not in emails:
                    emails.append(e)
                    source = source or ("Link page" if host in AGGREGATORS else "Website")
            if not found and "<form" in html.lower() and "contact" in u.lower():
                form_only = True
            try:
                txt = html_text(html)
                abns += [x for x in abns_in(txt) if x not in abns]      # AU: often only in the footer / privacy page
                import quality as Q
                agency += [x for x in Q.site_agency_signals(html, txt) if x[0] not in {a[0] for a in agency}]
                booking = booking or Q.booking_tool(html)
                if "privacy" not in u:
                    texts.append(txt[:6000])
                if find_people:
                    for x in people_in(html, txt, company):
                        if x[0].lower() not in {p[0].lower() for p in people}:
                            people.append((x[0], x[1], u))
                phones += [p for p in phones_in(html) if p not in phones]
                signals += [s for s in spend_signals(html) if s not in signals]
            except Exception:
                pass
            time.sleep(0.4)
        if not got:
            notes.append("Website unreachable or blocked by robots.txt")
        elif not emails and form_only:
            notes.append("Contact form only (no public email)")
    emails = rank_emails(emails, domain)
    return {"website": site or "", "domain": domain, "emails": emails, "email_source": source if emails else "",
            "socials": socials, "notes": notes, "people": people[:5], "phones": phones[:3], "signals": signals,
            "text": "\n".join(texts)[:15000], "abns": abns, "agency": agency, "booking": booking,
            "email_sources": email_sources, "email_associations": associations}


# ------------------------------------------------------------------ v3.1: Australia facts + search-result matching
_ABN_RE = re.compile(r"\bA\.?\s?B\.?\s?N\.?\s*(?:no\.?|number|:|#)?\s*((?:\d[\s ]?){11})(?!\d)", re.I)
_ABN_W = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def abn_valid(abn):
    """Official ABN checksum (ABR): subtract 1 from the first digit, weight, sum % 89 == 0."""
    d = [int(c) for c in re.sub(r"\D", "", abn or "")]
    if len(d) != 11 or d[0] == 0:
        return False
    d[0] -= 1
    return sum(w * x for w, x in zip(_ABN_W, d)) % 89 == 0


def abns_in(text):
    """ABNs printed on the page next to the word 'ABN' that pass the checksum. Never guessed."""
    out = []
    for m in _ABN_RE.finditer(text or ""):
        a = re.sub(r"\D", "", m.group(1))
        if abn_valid(a) and a not in out:
            out.append(a)
    return out


def fmt_abn(a):
    a = re.sub(r"\D", "", a or "")
    return f"{a[:2]} {a[2:5]} {a[5:8]} {a[8:]}" if len(a) == 11 else a


def phone_type(phone, cc="AU"):
    """AU: Mobile (04 / +614) | 13/1300/1800 | Landline. Mobiles must be checked against the Do Not Call Register
    before cold calls."""
    p = re.sub(r"[^\d+]", "", phone or "")
    if not p or (cc or "").upper() != "AU":
        return ""
    p = "0" + p[3:] if p.startswith("+61") else p
    if p.startswith("04") or p.startswith("05"):
        return "Mobile"
    if p.startswith(("13", "1300", "1800")):
        return "13/1300/1800"
    return "Landline" if p.startswith("0") and len(p) == 10 else ""


AU_TZ = {"NSW": "Australia/Sydney", "ACT": "Australia/Sydney", "VIC": "Australia/Melbourne", "TAS": "Australia/Hobart",
         "QLD": "Australia/Brisbane", "SA": "Australia/Adelaide", "WA": "Australia/Perth", "NT": "Australia/Darwin"}
_AU_STATE = {"new south wales": "NSW", "victoria": "VIC", "queensland": "QLD", "western australia": "WA",
             "south australia": "SA", "tasmania": "TAS", "australian capital territory": "ACT", "northern territory": "NT"}


def au_state(s):
    s = (s or "").strip()
    return s.upper() if s.upper() in AU_TZ else _AU_STATE.get(s.lower(), "")


DIRECTORY_HOSTS = ("yelp.", "yellowpages.", "truelocal.", "tripadvisor.", "hotfrog.", "localsearch.", "whitepages.",
                   "startlocal.", "realestate.com.au", "domain.com.au", "ratemyagent.", "healthengine.", "hotdoc.",
                   "zomato.", "opentable.", "thefork.", "google.", "bing.", "wikipedia.", "linkedin.", "facebook.",
                   "instagram.", "tiktok.", "youtube.", "x.com", "twitter.", "pinterest.", "glassdoor.", "seek.com",
                   "indeed.", "crunchbase.", "abr.business.gov.au", "opencorporates.", "productreview.", "wordofmouth.",
                   "booking.com", "expedia.", "airbnb.", "mindbody", "classpass.", "fresha.", "timeout.", "broadsheet.")


def _tokens(name):
    stop = {"the", "and", "pty", "ltd", "group", "co", "company", "studio", "clinic", "centre", "center", "restaurant",
            "gym", "fitness", "real", "estate", "dental", "bar", "cafe", "of", "at", "by", "studios", "agency"}
    return [t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(t) >= 3 and t not in stop]


def site_from_results(results, name):
    """The business's own website from search results: the host must contain a distinctive word of the name and must
    not be a directory/social site. Returns url or ''."""
    toks = _tokens(name)
    if not toks:
        return ""
    for r in results or []:
        u = r.get("link") or ""
        h = _host(u)
        if not h or any(d in h for d in DIRECTORY_HOSTS) or h in AGGREGATORS:
            continue
        flat = re.sub(r"[^a-z0-9]", "", h.split(".")[0])
        if any(t in flat for t in toks):
            return f"{urlparse(u).scheme or 'https'}://{urlparse(u).netloc}/"
    return ""


def people_from_linkedin(results, company):
    """Decision makers from LinkedIn search-result titles like 'Jane Smith - Owner - Smile Dental | LinkedIn'.
    Kept only if the title has a decision role AND a distinctive word of the company. Returns [(name, role, url)]."""
    toks, out = _tokens(company), []
    for r in results or []:
        u, title = r.get("link") or "", (r.get("title") or "").replace("–", "-").replace("—", "-")
        if "linkedin.com/in/" not in u:
            continue
        parts = [p.strip() for p in re.split(r"\s[-|]\s|\s\|\s", title.replace("| LinkedIn", "").replace("LinkedIn", "")) if p.strip()]
        if len(parts) < 2:
            continue
        nm = _clean_name(parts[0], company)
        rest = " ".join(parts[1:]) + " " + (r.get("snippet") or "")[:200]
        rm = _ROLE_RE.search(" ".join(parts[1:]))
        if nm and rm and toks and any(t in rest.lower() for t in toks):
            out.append((nm, rm.group(0).strip().title(), u.split("?")[0]))
    return out
