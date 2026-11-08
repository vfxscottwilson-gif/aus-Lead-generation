"""YouTube creator source for the 'YouTube' group niches. Two ways, same output (pseudo-place dicts):

1. FREE, NO KEY (default, YT_SOURCE=auto/free): reads public YouTube pages like a browser would.
   search results page (channel filter) -> channel About page (country, description, links, subscriber + video count)
   -> the channel's public RSS feed (last 15 upload dates). ~2-3 requests per channel, polite pauses
   (YT_FREE_DELAY_SEC), hard cap per run (YT_FREE_MAX_CHANNELS_PER_RUN). If YouTube rate limits, shows a consent /
   captcha page or changes its page format, it raises YTUnavailable and the run continues without YouTube.
2. YouTube Data API v3 (optional fast path, YOUTUBE_API_KEYS): 10,000 units/day per key, ~125 units per search.

Facts only: business emails hidden behind YouTube's "sign in to see email" are never touched; only emails written in
the public description / linked website are used. Country comes from the channel's own setting or stays blank."""
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from xml.etree import ElementTree

import requests

import defaults as D
import enrich as E

API = "https://www.googleapis.com/youtube/v3"
WEB = "https://www.youtube.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CHANNEL_FILTER = "EgIQAg=="                       # YouTube's own 'Type: Channel' search filter


class YTUnavailable(Exception):
    pass


# ------------------------------------------------------------------ shared helpers
def parse_count(text):
    """'32.3K subscribers' / '2.63M' / '1,234 subscribers' / '128 videos' -> int, or None."""
    m = re.search(r"([\d.,]+)\s*([KMB])?", str(text or "").replace(" ", " "), re.I)
    if not m:
        return None
    num, mult = m.group(1), (m.group(2) or "").upper()
    try:
        v = float(num.replace(",", "")) if mult else float(num.replace(",", "").replace(".", "") or 0)
    except ValueError:
        return None
    return int(round(v * {"K": 1e3, "M": 1e6, "B": 1e9}.get(mult, 1)))


def _english(text):
    letters = re.findall(r"[^\W\d_]", text or "")
    return not letters or sum(1 for c in letters if c.isascii()) / len(letters) >= 0.85


def country_ok(code, text, S):
    """code = ISO code from the channel ('' = not stated). Uses YT_COUNTRIES / YT_ALLOW_UNKNOWN_COUNTRY."""
    return str(code).strip().upper() == "AU"


def upload_stats(dates):
    """dates: upload datetimes -> (days since last upload, uploads in last 30 days) or (None, None)."""
    if not dates:
        return None, None
    now = datetime.now(timezone.utc)
    return (now - max(dates)).days, sum(1 for t in dates if (now - t).days < 30)


def make_place(cid, title, desc, links, subs, n30, days, code, views=None):
    """One pseudo-place in the shape the lead pipeline understands."""
    link_text = " ".join("https://" + l if not l.startswith("http") else l for l in links)
    socials = E.socials_in(desc + " " + link_text)
    site = E.pick_website(desc + "\n" + link_text, title)
    country = D.COUNTRY_BY_CODE.get(code, code)
    return {"id": f"yt:{cid}", "displayName": {"text": title}, "websiteUri": site,
            "formattedAddress": country,
            "addressComponents": [{"longText": country, "shortText": code, "types": ["country"]}] if code else [],
            "businessStatus": "OPERATIONAL", "types": [], "primaryTypeDisplayName": {"text": "YouTube channel"},
            "googleMapsUri": f"https://www.youtube.com/channel/{cid}",
            "_nometrics": True, "_source": "YouTube", "_email": ";".join(E.emails_in(desc)),
            "_ig": socials.get("Instagram", ""), "_fb": socials.get("Facebook", ""), "_socials": socials,
            "_yt": f"https://www.youtube.com/channel/{cid}", "_subs": subs, "_uploads30": n30, "_days": days,
            "_views": views, "_desc": desc[:3000]}


def median_views(values):
    import quality as Q
    return Q.median(values)


def _limits(S):
    return S.num("YT_MIN_SUBS", 5000), S.num("YT_MAX_SUBS", 500000), S.num("YT_MAX_DAYS_SINCE_UPLOAD", 45)


# ------------------------------------------------------------------ 1) free, no key
def _initial_data(html):
    m = re.search(r"(?:var ytInitialData|window\[\"ytInitialData\"\])\s*=\s*(\{.*?\});\s*</script>", html or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def _find(o, key):
    if isinstance(o, dict):
        if key in o:
            yield o[key]
        for v in o.values():
            yield from _find(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from _find(v, key)


def _txt(v):
    if isinstance(v, dict):
        return v.get("simpleText") or v.get("content") or "".join(r.get("text", "") for r in v.get("runs", []))
    return str(v or "")


def parse_search(data):
    """[(channel_id, title, subscribers or None)] + continuation token from a search results page / continuation."""
    out = []
    for c in _find(data, "channelRenderer"):
        cid = c.get("channelId")
        if not cid:
            continue
        subs = None
        for k in ("videoCountText", "subscriberCountText"):    # YouTube puts the subscriber text in either field
            t = _txt(c.get(k))
            if "subscriber" in t.lower():
                subs = parse_count(t)
                break
        out.append((cid, _txt(c.get("title")).strip(), subs))
    token = next((t.get("continuationCommand", {}).get("token") for t in _find(data, "continuationEndpoint")
                  if isinstance(t, dict) and t.get("continuationCommand")), None)
    return out, token


def parse_about(data):
    """About page -> dict(country, description, links, subs, videos, title) or None."""
    a = next(_find(data, "aboutChannelViewModel"), None)
    md = next(_find(data, "channelMetadataRenderer"), {}) or {}
    if not a and not md:
        return None
    a = a or {}
    links = []
    for l in _find(a.get("links", []), "channelExternalLinkViewModel"):
        c = _txt(l.get("link"))
        if c:
            links.append(c)
    return {"country": _txt(a.get("country")), "description": _txt(a.get("description")) or md.get("description", ""),
            "links": links, "subs": parse_count(a.get("subscriberCountText")), "videos": parse_count(a.get("videoCountText")),
            "title": md.get("title", "")}


def parse_rss_views(xml_text, n=5):
    """Views of the newest n uploads from the public RSS feed (<media:statistics views="...">)."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    out = []
    for entry in (el for el in root.iter() if el.tag.split("}")[-1] == "entry"):
        for el in entry.iter():
            if el.tag.split("}")[-1] == "statistics" and el.get("views") is not None:
                try:
                    out.append(int(el.get("views")))
                except ValueError:
                    pass
                break
        if len(out) >= n:
            break
    return out


def parse_rss(xml_text):
    dates = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return dates
    for entry in (el for el in root.iter() if el.tag.split("}")[-1] == "entry"):   # feed-level <published> = channel date
        for el in entry:
            if el.tag.split("}")[-1] == "published":
                try:
                    dates.append(datetime.fromisoformat((el.text or "").replace("Z", "+00:00")))
                except ValueError:
                    pass
    return dates


class YTFree:
    """No-key YouTube reader. Raises YTUnavailable on blocks so the caller can stop cleanly."""
    label = "free YouTube reader"

    def __init__(self, S):
        self.S = S
        self.delay = max(1.0, S.num("YT_FREE_DELAY_SEC", 2.5))
        self.cap = int(S.num("YT_FREE_MAX_CHANNELS_PER_RUN", 150))
        self.opened, self.requests, self.notes, self.units = 0, 0, [], 0
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        self.http.cookies.update({"SOCS": "CAI", "CONSENT": "YES+1"})        # skip the EU cookie wall (decline = default)
        self.key, self.version = None, "2.20240101.00.00"

    def _get(self, url, params=None, method="GET", js=None):
        time.sleep(self.delay + random.random())
        self.requests += 1
        try:
            r = self.http.request(method, url, params=params, json=js, timeout=25)
        except requests.RequestException as e:
            raise YTUnavailable(f"YouTube page read failed ({type(e).__name__}); YouTube skipped this run.")
        low = (r.url or "").lower()
        if r.status_code == 429 or "consent." in low or "/sorry/" in low or "captcha" in (r.text[:5000].lower()):
            raise YTUnavailable("YouTube rate limited the free reader (429/consent/captcha); YouTube skipped until next run.")
        return r if r.status_code == 200 else None

    def search(self, term, S, skip=frozenset()):
        lo, hi, max_days = _limits(S)
        pages = int(max(1, min(5, S.num("YT_SEARCH_PAGES", 2))))
        r = self._get(f"{WEB}/results", {"search_query": term, "sp": CHANNEL_FILTER, "hl": "en", "gl": "US"})
        if r is None:
            return []
        data = _initial_data(r.text)
        if data is None:
            raise YTUnavailable("YouTube page format changed (no ytInitialData); free reader off this run.")
        m = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', r.text)
        v = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"', r.text)
        self.key, self.version = (m.group(1) if m else None), (v.group(1) if v else self.version)
        found, token = parse_search(data)
        for _ in range(pages - 1):
            if not (token and self.key):
                break
            body = {"context": {"client": {"clientName": "WEB", "clientVersion": self.version, "hl": "en", "gl": "US"}},
                    "continuation": token}
            rr = self._get(f"{WEB}/youtubei/v1/search", {"key": self.key, "prettyPrint": "false"}, "POST", body)
            if rr is None:
                break
            try:
                more, token = parse_search(rr.json())
            except ValueError:
                break
            found += more
        out, seen = [], set()
        for cid, title, subs in found:
            if cid in seen or f"yt:{cid}" in skip:
                continue
            seen.add(cid)
            if subs is not None and not lo <= subs <= hi:          # cheap filter BEFORE opening the channel
                continue
            if self.opened >= self.cap:
                note = f"Free YouTube reader reached YT_FREE_MAX_CHANNELS_PER_RUN={self.cap}."
                if note not in self.notes:
                    self.notes.append(note)
                break
            self.opened += 1
            try:
                p = self.channel(cid, title, subs, lo, hi, max_days)
            except YTUnavailable:
                if out:
                    self.notes.append("YouTube started blocking mid-search; kept the channels already read.")
                    return out
                raise
            except Exception:
                continue
            if p:
                out.append(p)
        return out

    def channel(self, cid, title, subs, lo, hi, max_days):
        r = self._get(f"{WEB}/channel/{cid}/about", {"hl": "en", "gl": "US"})
        if r is None:
            return None
        info = parse_about(_initial_data(r.text) or {})
        if not info:
            return None
        subs = info["subs"] if info["subs"] is not None else subs
        if subs is None or not lo <= subs <= hi or (info["videos"] is not None and info["videos"] < 15):
            return None
        _, code, _ = D.country_info(info["country"]) if info["country"] else ("", "", "")
        desc = info["description"] or ""
        if not country_ok(code, (info["title"] or title) + " " + desc, self.S):
            return None
        rss = self._get(f"{WEB}/feeds/videos.xml", {"channel_id": cid})
        days, n30 = upload_stats(parse_rss(rss.text) if rss is not None else [])
        if days is None or days > max_days:
            return None
        views = median_views(parse_rss_views(rss.text) if rss is not None else [])
        if views is not None and views < self.S.num("YT_MIN_VIEWS", 20000):
            return None
        return make_place(cid, (info["title"] or title).strip(), desc, info["links"], subs, n30, days, code, views)


# ------------------------------------------------------------------ 2) YouTube Data API (optional fast path)
class YT:
    label = "YouTube Data API"

    def __init__(self, S=None):
        raw = os.getenv("YOUTUBE_API_KEYS", "") or os.getenv("YOUTUBE_API_KEY", "")
        self.keys = [k for k in re.split(r"[\s,;]+", raw.strip()) if k]
        self.dead, self.units, self.notes, self.S = set(), 0, [], S
        if not self.keys:
            raise YTUnavailable("No YOUTUBE_API_KEYS set (optional): YouTube API path skipped.")

    def _get(self, path, params, cost):
        for k in self.keys:
            if k in self.dead:
                continue
            try:
                r = requests.get(f"{API}/{path}", params=dict(params, key=k), timeout=25)
            except requests.RequestException:
                continue
            if r.status_code == 200:
                self.units += cost
                try:
                    return r.json()
                except ValueError:
                    return {}
            reason = ""
            try:
                reason = r.json()["error"]["errors"][0].get("reason", "")
            except Exception:
                pass
            if r.status_code == 403 or (reason in ("keyInvalid", "badRequest") and "key" in r.text.lower()):
                self.dead.add(k)                       # quota used up / key invalid: next key
                self.notes.append(f"A YouTube key stopped ({reason or r.status_code}); trying the next.")
                continue
            return {}                                  # bad request or YouTube hiccup: skip this search, keep the key
        raise YTUnavailable("All YouTube keys exhausted or invalid for today.")

    def search(self, term, S, skip=frozenset()):
        """Active, mid-size channels for a search term (countries from YT_COUNTRIES)."""
        n = int(max(5, min(50, S.num("YT_RESULTS_PER_SEARCH", 25))))
        allowed = [c.strip().upper() for c in str(S.get("YT_COUNTRIES", "") or "").split(",") if c.strip()]
        params = {"part": "snippet", "type": "channel", "q": term, "relevanceLanguage": "en", "maxResults": n}
        if len(allowed) == 1:
            params["regionCode"] = allowed[0]
        d = self._get("search", params, 100)
        ids = [it.get("snippet", {}).get("channelId") or it.get("id", {}).get("channelId") for it in d.get("items", [])]
        ids = [i for i in ids if i and f"yt:{i}" not in skip]
        if not ids:
            return []
        c = self._get("channels", {"part": "snippet,statistics,contentDetails,brandingSettings", "id": ",".join(ids[:50]),
                                   "maxResults": 50}, 1)
        out = []
        for ch in c.get("items", []):
            try:
                p = self._to_place(ch, S)
                if p:
                    out.append(p)
            except YTUnavailable:
                raise
            except Exception:
                continue
        return out

    def _uploads(self, playlist):
        d = self._get("playlistItems", {"part": "contentDetails", "playlistId": playlist, "maxResults": 15}, 1)
        dates = []
        ids = [(it.get("contentDetails") or {}).get("videoId") for it in d.get("items", [])][:5]
        ids = [i for i in ids if i]
        self.last_views = None
        if ids:                                                # 1 unit: median views of the newest 5 uploads
            v = self._get("videos", {"part": "statistics", "id": ",".join(ids)}, 1)
            self.last_views = median_views([int((x.get("statistics") or {}).get("viewCount") or 0) for x in v.get("items", [])])
        for it in d.get("items", []):
            ts = (it.get("contentDetails") or {}).get("videoPublishedAt")
            if ts:
                try:
                    dates.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
                except ValueError:
                    pass
        return upload_stats(dates)

    def _to_place(self, ch, S):
        lo, hi, max_days = _limits(S)
        sn, st = ch.get("snippet", {}) or {}, ch.get("statistics", {}) or {}
        if st.get("hiddenSubscriberCount"):
            return None
        subs = int(st.get("subscriberCount") or 0)
        if not lo <= subs <= hi or int(st.get("videoCount") or 0) < 15:
            return None
        desc = sn.get("description", "") or ""
        brand = ((ch.get("brandingSettings") or {}).get("channel") or {})
        code = (sn.get("country") or brand.get("country") or "").upper()
        if not country_ok(code, desc + " " + sn.get("title", ""), S):
            return None
        up = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        days, n30 = self._uploads(up) if up else (None, None)
        if days is None or days > max_days:
            return None
        views = getattr(self, "last_views", None)
        if views is not None and views < S.num("YT_MIN_VIEWS", 20000):
            return None
        return make_place(ch.get("id", ""), sn.get("title", "").strip(), desc, [], subs, n30, days, code, views)


# ------------------------------------------------------------------ chooser
class Source:
    """YT_SOURCE: auto (free first, API as fallback if a key exists) | free | api. Raises YTUnavailable if none."""

    def __init__(self, S):
        self.notes, self.chain = [], []
        try:
            self.chain.append(YT(S))
        except YTUnavailable:
            raise YTUnavailable("YouTube discovery requires an authorized YOUTUBE_API_KEY_1; imported channel research remains available.") from None

    @property
    def requests(self):
        return sum(getattr(s, "requests", 0) for s in self.chain)

    def search(self, term, S, skip=frozenset()):
        while self.chain:
            src = self.chain[0]
            try:
                return src.search(term, S, skip)
            except YTUnavailable as e:
                self.notes.append(str(e)[:150])
                self.notes += src.notes[:2]
                self.chain.pop(0)
                if self.chain:
                    self.notes.append(f"Switched to {self.chain[0].label}.")
        raise YTUnavailable("No YouTube source left this run: " + (self.notes[-1] if self.notes else ""))

    def all_notes(self):
        return self.notes + [n for s in self.chain for n in s.notes]
