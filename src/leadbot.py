"""Australian prospecting: discover, research, qualify and preserve evidence in one spreadsheet.
Run --setup to create tabs; --local uses private local CSVs; --dry never writes to Google.
No outreach messages are sent. Missing evidence is retained for research.
"""
import hashlib
import json
import math
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import gspread
from gspread.utils import rowcol_to_a1

import defaults as D
import enrich as E
import importer as IM
import research
import insight as IN
import keys as K
import optional as O
import osm as OSM
import quality as Q
import qualification as QUAL
import operations as OPS
import score as SC
import yt as YTM
from ai import AI
from places import Places, AllKeysExhausted

RESERVED = {"rules", "niches", "locations", "seen", "searches", "blacklist", "run log", "summary", "playbook", "leads input",
            "dashboard", "options", "all leads", "import", "snapshots", "stats", "master dashboard", "us stats", "meta", "decision makers", "outreach leads", "research queue", "api health", "exports", "quarantine", "operational runs", "operational companies"}
# control tabs read in ONE batched request at start (Sheets quota: 60 reads/min per service account, shared by all jobs)
CONTROL_TABS = ["Options", "Rules", "Niches", "Locations", "Playbook", "Import", "Seen", "Blacklist", "Searches",
                "Snapshots", "Leads Input", "Meta"]
CELL_LIMIT = 10_000_000                 # Google Sheets hard limit per spreadsheet (all tabs, incl. empty grid cells)
ENRICH_WEBSITES = O.ENRICH_WEBSITES
LEGACY_MARK = "Business Name"          # v1 lead tabs had this header; v2 uses 'Company Name'


# ------------------------------------------------------------------ helpers
def env(k, d=""):
    return (os.getenv(k, "") or d).strip()


def safe(fn, default, label="step"):
    """Run an optional step. On ANY exception: count it, stay silent, return the default (the unmodified base)."""
    try:
        return fn()
    except Exception as exc:
        O.STATS[label] += 1
        print(json.dumps({"event": "optional_step_failed", "step": label, "error_type": type(exc).__name__}))
        return default


def retry(fn, tries=7):
    """Sheets call with backoff on 429/5xx (10, 20, 40, 80, 90, 90 s + jitter: parallel jobs share one quota)."""
    for i in range(tries):
        try:
            return fn()
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            if code in (429, 500, 502, 503) and i < tries - 1:
                O.STATS["sheets-" + str(code)] += 1
                time.sleep(min(90, 10 * 2 ** i) + random.uniform(0, 8))
                continue
            raise
        except Exception as e:
            if i < tries - 1 and "Connection" in type(e).__name__ + str(e):
                time.sleep(10 * (i + 1))
                continue
            raise


_CACHE = {"ws": {}, "vals": {}, "sh": None}   # ONE sheet at a time: tab objects (1 metadata read) + prefetched control-tab values
_HDRS = {}                               # tab title -> its real header row (map by name when writing control tabs)


def _qt(title):
    return "'" + str(title).replace("'", "''") + "'!"


def batch_values(sh, ranges, major="ROWS"):
    """Many ranges in few reads (chunks of 40). Returns one value list per range ([] when empty/missing)."""
    out = []
    for i in range(0, len(ranges), 40):
        part = ranges[i:i + 40]
        res = retry(lambda: sh.values_batch_get(part, params={"majorDimension": major}))
        vr = (res or {}).get("valueRanges", [])
        out += [(vr[k].get("values", []) if k < len(vr) else []) for k in range(len(part))]
    return out


def prefetch_book(sh):
    """1 metadata read (every tab) + 1 batched read of all control tabs. Never raises (falls back to normal reads)."""
    _CACHE["ws"], _CACHE["vals"], _CACHE["sh"] = {}, {}, sh
    _HDRS.clear()
    try:
        _CACHE["ws"] = {w.title: w for w in retry(lambda: sh.worksheets())}
        titles = [t for t in CONTROL_TABS if t in _CACHE["ws"]]
        vals = batch_values(sh, [_qt(t)[:-1] for t in titles]) if titles else []
        _CACHE["vals"] = {t: [[str(x) for x in r] for r in v] for t, v in zip(titles, vals)}
    except Exception:
        O.STATS["prefetch-tabs"] += 1
        _CACHE["ws"], _CACHE["vals"] = {}, {}


def find_ws(sh, title):
    """Tab by title from the cache (no read), else one live lookup. Raises gspread.WorksheetNotFound."""
    if _CACHE["ws"] and _CACHE["sh"] is sh:
        if title in _CACHE["ws"]:
            return _CACHE["ws"][title]
        raise gspread.WorksheetNotFound(title)
    return retry(lambda: sh.worksheet(title))


def get_all(ws):
    if _CACHE["ws"].get(ws.title) is ws:            # prefetched once; later calls read live (rows may have been added)
        v = _CACHE["vals"].pop(ws.title, None)
        if v is not None:
            return v
    return retry(lambda: ws.get_all_values())


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def cell(v):
    """Safe cell value: no control characters, within Google's 50,000 characters per cell."""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return v
    v = _CTRL.sub("", str(v))
    return v if len(v) <= 49000 else v[:48990] + "…"


def append(ws, rows):
    for i in range(0, len(rows), 200):
        part = rows[i:i + 200]
        retry(lambda: ws.append_rows(part, value_input_option="RAW"))


def col_letter(n):
    return re.sub(r"\d", "", rowcol_to_a1(1, n))


def pad(r, n):
    return list(r) + [""] * (n - len(r))


def today(S, days_ago=0):
    """Date in YOUR timezone (Rules > TIMEZONE, default UTC) so 'today' matches what you see."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(S.get("TIMEZONE") or "UTC"))
    except Exception:
        now = datetime.now(timezone.utc)
    return (now - timedelta(days=days_ago)).date().isoformat()


class Settings(dict):
    def num(self, k, d=0.0):
        v = str(self.get(k, "")).strip().replace(" ", "")
        try:
            if v.endswith("%"):
                return float(v[:-1]) / 100
            if re.fullmatch(r"\d+,\d+", v):
                v = v.replace(",", ".")
            return float(v) if v else d
        except ValueError:
            return d

    def yes(self, k, d=True):
        v = str(self.get(k, "")).strip().lower()
        return d if not v else v in ("yes", "y", "true", "1", "on")

    def list(self, k):
        return [x.strip().lower() for x in str(self.get(k, "")).split(",") if x.strip()]


def num(v, d=0.0):
    return Settings({"x": v}).num("x", d)


def get_tab(sh, title, headers):
    """Open/create a tab. Never overwrites existing headers: missing ones are appended at the end."""
    try:
        ws = find_ws(sh, title)
    except gspread.WorksheetNotFound:
        try:
            ws = retry(lambda: sh.add_worksheet(title, rows=200, cols=len(headers) + 2))
            if _CACHE["ws"] and _CACHE["sh"] is sh:
                _CACHE["ws"][title] = ws
        except gspread.exceptions.APIError as e:            # a parallel job created it a moment ago
            if "already exists" not in str(e).lower():
                raise
            time.sleep(3)
            _CACHE["ws"] = {}
            return get_tab(sh, title, headers)
        retry(lambda: ws.update(range_name="A1", values=[headers]))
        _HDRS[title] = list(headers)
        for fn in (lambda: ws.freeze(rows=1), lambda: ws.format("1:1", {"textFormat": {"bold": True}}),
                   lambda: ws.set_basic_filter()):
            try:
                fn()
            except Exception:
                pass
        return ws, list(headers)
    pre = _CACHE["vals"].get(title) if _CACHE["ws"].get(title) is ws else None
    cur = [h.strip() for h in (pre[0] if pre else retry(lambda: ws.row_values(1)))]
    while cur and not cur[-1]:
        cur.pop()
    if not any(cur):
        retry(lambda: ws.update(range_name="A1", values=[headers]))
        _HDRS[title] = list(headers)
        return ws, list(headers)
    _HDRS[title] = ensure_headers(ws, cur, headers)
    return ws, _HDRS[title]


def ensure_headers(ws, cur, headers):
    """Renames v2 headers in place (D.RENAMED_COLS) and appends missing ones at the end. Returns the new header row."""
    cur = list(cur)
    for old, new in D.RENAMED_COLS.items():                 # v3 rename, in place: column positions never move
        if old in cur and new in headers and new not in cur:
            c = cur.index(old)
            retry(lambda: ws.update(range_name=rowcol_to_a1(1, c + 1), values=[[new]]))
            cur[c] = new
    missing = [h for h in headers if h not in cur]
    if missing:
        if ws.col_count < len(cur) + len(missing):
            retry(lambda: ws.resize(cols=len(cur) + len(missing)))
        retry(lambda: ws.update(range_name=rowcol_to_a1(1, len(cur) + 1), values=[missing]))
        cur += missing
    return cur


def style_lead_tab(sh, ws, hdr):
    """One-time look for a NEW niche tab: coloured header, frozen name columns, Status dropdown, wrapped long text."""
    try:
        sid = ws.id
        reqs = [{"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.12, "green": 0.23, "blue": 0.4},
                                                               "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                                               "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}},
                                "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"}},
                {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": 1,
                                           "frozenColumnCount": (hdr.index("Company Name") + 1) if "Company Name" in hdr else 5}},
                                           "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}}]
        if "Status" in hdr:
            reqs.append(status_rule(sid, hdr.index("Status")))
        for h, px in (("Case Study", 380), ("Email Draft", 380), ("Pitch Angle", 300), ("Company Name", 220), ("Segment", 150),
                      ("Agency Evidence", 320), ("Revenue Est. Basis", 320),
                      ("Decision Maker Name", 170), ("Personal Email", 220), ("Generic Email", 220), ("Other Emails", 220)):
            if h in hdr:
                c = hdr.index(h)
                reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
                                                           "properties": {"pixelSize": px}, "fields": "pixelSize"}})
        retry(lambda: sh.batch_update({"requests": reqs}))
    except Exception:
        pass


def get_lead_tab(sh, title, cache):
    """Niche tab. A v1 tab with the old column layout is renamed 'OLD <name>' (nothing deleted) and a fresh one made."""
    if title in cache:
        return cache[title]
    try:
        ws = find_ws(sh, title)
        hdr = [h.strip() for h in retry(lambda: ws.row_values(1))]
        if LEGACY_MARK in hdr and "Company Name" not in hdr:
            safe(lambda: retry(lambda: ws.update_title(("OLD " + title)[:99])), None, "legacy-rename")
            _CACHE["ws"].pop(title, None)
            raise gspread.WorksheetNotFound
        cache[title] = (ws, ensure_headers(ws, hdr, D.LEAD_H) if any(hdr) else get_tab(sh, title, D.LEAD_H)[1])
    except gspread.WorksheetNotFound:
        ws, hdr = get_tab(sh, title, D.LEAD_H)
        style_lead_tab(sh, ws, hdr)
        cache[title] = (ws, hdr)
    return cache[title]


def tab_name(niche):
    n = re.sub(r"[\[\]\*\?:/\\]", " ", niche).strip()[:90] or "Leads"
    n = re.sub(r"[-_]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n + " Leads" if n.lower() in RESERVED else n


def legacy_check(sh):
    for title, marker in (("Niches", "Search Keywords (separate with ;)"), ("Seen", "Channel Id")):
        try:
            hdr = retry(lambda: sh.worksheet(title).row_values(1))
        except gspread.WorksheetNotFound:
            continue
        if marker in hdr:
            sys.exit("This Google Sheet was used by the old YouTube lead bot. Use a NEW Google Sheet, or delete the tabs "
                     "Rules, Niches, Seen, Run Log, Summary and Playbook (and old niche tabs) first.")


# ------------------------------------------------------------------ config in the sheet
def load_options(sh):
    """Options tab: list of every free/optional service. A key typed in 'Your key' is used when no GitHub secret exists."""
    ws, hdr = get_tab(sh, "Options", D.OPTIONS_H)
    rows = get_all(ws)
    have = {r[0].strip() for r in rows[1:] if r and r[0].strip()}
    new = [[s, k, "", req, free, adds, wired, url, ""] for s, k, req, free, adds, wired, url in D.OPTIONS if s not in have]
    if new:
        append(ws, new)
        rows = get_all(ws)
    ki, vi = hdr.index(D.OPTIONS_H[1]), hdr.index(D.OPTIONS_H[2])
    pairs = [(pad(r, len(hdr))[ki], pad(r, len(hdr))[vi]) for r in rows[1:]]
    # Preserve legacy cells but never use credentials stored in a spreadsheet.
    took = []
    return ws, hdr, rows, took


def write_option_status(ws, hdr, rows):
    try:
        ki, si = hdr.index(D.OPTIONS_H[1]), hdr.index(D.OPTIONS_H[8])
        vals = [[O.status_of(pad(r, len(hdr))[ki].strip())] for r in rows[1:]]
        if vals:
            c = col_letter(si + 1)
            retry(lambda: ws.update(range_name=f"{c}2:{c}{len(vals) + 1}", values=vals))
    except Exception:
        pass


def niche_thresholds(name, g, r, target):
    """v4 quality thresholds from the Niches tab; a blank cell falls back to the spec's default for that niche."""
    dflt = Q.niche_defaults(name, target if target and target != "YOUTUBE" else "")
    keys = ("rmin", "rstrong", "vel", "fweak", "fstrong", "pweak", "pstrong", "ticket", "visits", "rate")
    out = {}
    for k, col, dv in zip(keys, Q.NICHE_Q_H, dflt):
        v = g(r, col)
        out[k] = num(v, dv) if str(v).strip() else dv
    return out


def rows_for(hdr, defaults_h, items):
    """Default rows -> lists in the TAB's real column order (users may have moved or added columns)."""
    out = []
    for it in items:
        d = dict(zip(defaults_h, it))
        out.append([d.get(h, "") for h in hdr])
    return out


def _regex(pat, label, notes):
    if not pat:
        return None
    try:
        return re.compile(pat, re.I)
    except re.error:
        notes.append(f"{label}: invalid regex ignored")
        return None


def load_config(sh, seed_only=False, target=None):
    seed_locs, seed_niches = D.seeds_for(target)
    legacy_check(sh)
    ws, _ = get_tab(sh, "Rules", D.SETTINGS_H)
    rows = get_all(ws)
    have = {r[0].strip() for r in rows[1:] if r and r[0].strip()}
    new = [[k, v, why] for k, v, why in D.SETTINGS if k not in have]
    if new:
        append(ws, new)
        rows = get_all(ws)
    S = Settings({r[0].strip(): (r[1].strip() if len(r) > 1 else "") for r in rows[1:] if r and r[0].strip()})
    notes = []

    nws, nhdr = get_tab(sh, "Niches", D.NICHES_H)
    nrows = get_all(nws)
    names = {r[0].strip().lower() for r in nrows[1:] if r and r[0].strip()}
    extra = [n for n in seed_niches if n[0].lower() not in names]  # new default niches are added once (disable, don't delete)
    if extra:
        append(nws, rows_for(nhdr, D.NICHES_H, extra))
        nrows = get_all(nws)
    ix = {h: nhdr.index(h) for h in D.NICHES_H if h in nhdr}
    g = lambda r, h: (r[ix[h]].strip() if h in ix and ix[h] < len(r) else "")
    split = lambda v: [t.strip() for t in re.split(r"[;\n]", v) if t.strip()]
    niches, skipped = [], []
    for r in nrows[1:]:
        name = g(r, "Niche")
        if not name or g(r, "Enabled (Yes/No)").lower() not in ("yes", "y", "true", "1"):
            continue
        low, high = num(g(r, "Budget Low AUD"), 0), num(g(r, "Budget High AUD"), 0)
        if low < S.num("MIN_BUDGET_AUD", 500):
            skipped.append(name)
            continue
        niches.append({"niche": name, "group": g(r, "Group"),
                       "terms": split(g(r, "Search Terms (separate with ;)")),
                       "osm": split(g(r, "Free Map Selectors (OSM, separate with ;)")),
                       "fit": num(g(r, "Budget Fit (1-5)"), 3), "low": int(low), "high": int(high or low),
                       "tpl": g(r, "Pitch Angle Template") or "{name}. Gap: {gap}. Offer a free 30-60s sample reel.",
                       "why": g(r, "Why this niche fits"),
                       "icp_min": num(g(r, "Qualification Threshold"), 70),
                       "service": g(r, "Typical Service Opportunity"),
                       "exclude": [w.strip().lower() for w in g(r, "Exclude Name Words (separate with ,)").split(",") if w.strip()],
                       "must": _regex(g(r, "Must Match On Website (regex)"), name, notes),
                       "reject": _regex(g(r, "Reject If Website Matches (regex)"), name, notes),
                       "chain": int(num(g(r, "Chain Cut-off (same site in N places)"), 0)),
                       "q": niche_thresholds(name, g, r, target)})
    lws, lhdr = get_tab(sh, "Locations", D.LOC_H)
    lrows = get_all(lws)
    areas = {(pad(r, len(lhdr))[lhdr.index("Search Area")] if "Search Area" in lhdr else "").strip().lower() for r in lrows[1:]}
    extra = [x for x in seed_locs if x[3].lower() not in areas]         # v3 metros are added once; Enabled = No to pause
    if extra:
        append(lws, rows_for(lhdr, D.LOC_H, extra))
        lrows = get_all(lws)
    li = {h: lhdr.index(h) for h in D.LOC_H if h in lhdr}
    lg = lambda r, h: (r[li[h]].strip() if h in li and li[h] < len(r) else "")
    locs = []
    for i, r in enumerate(lrows[1:], start=2):
        if not lg(r, "Search Area") or lg(r, "Enabled (Yes/No)").lower() not in ("yes", "y", "true", "1"):
            continue
        lat, lon = num(lg(r, "Latitude"), 0), num(lg(r, "Longitude"), 0)
        country, cc, cur = D.country_info(lg(r, "Country"))
        if cc != "AU":
            notes.append("Non-Australian or unlocated Locations row excluded")
            continue
        locs.append({"country": country, "cc": cc, "currency": cur, "state": lg(r, "State"), "city": lg(r, "City"),
                     "area": lg(r, "Search Area"), "priority": int(num(lg(r, "Priority (1-3)"), 2) or 2),
                     "lat": lat, "lon": lon, "radius": num(lg(r, "Radius km"), 6) or 6,
                     "_row": i, "_lat_col": li.get("Latitude"), "_ws": lws})
    pws, _ = get_tab(sh, "Playbook", ["Playbook (ICP + how to use these leads). Edit freely; the bot never overwrites it."])
    if len(get_all(pws)) < 2:
        append(pws, [[l] for l in D.PLAYBOOK])
    get_tab(sh, "Import", D.IMPORT_H)
    return S, niches, locs, skipped, notes


def picker(hdr, names):
    """Row -> values of `names`, found by HEADER NAME (columns may have been moved). Missing column -> ''."""
    ix = [hdr.index(n) if n in hdr else None for n in names]
    return lambda r: [(str(r[i]).strip() if i is not None and i < len(r) else "") for i in ix]


def load_seen(sh, S):
    ws, hdr = get_tab(sh, "Seen", D.SEEN_H)
    block, domains, emails = set(), set(), set()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(S.num("RECHECK_DAYS", 120)))).date().isoformat()
    g = picker(hdr, D.SEEN_H)
    for r in get_all(ws)[1:]:
        pid, dom, em, _, date, res = g(r)
        if not pid:
            continue
        if res.startswith("Added"):
            block.add(pid)
            if dom:
                domains.add(dom.lower())
            emails.update(e for e in em.lower().split(";") if e)
        elif date >= cutoff:
            block.add(pid)
    bws, _ = get_tab(sh, "Blacklist", D.BLACKLIST_H)
    bl = {str(r[0]).strip().lower() for r in get_all(bws)[1:] if r and str(r[0]).strip()}
    sws, shdr = get_tab(sh, "Searches", D.SEARCH_H)
    last, month, places_month = {}, datetime.now(timezone.utc).strftime("%Y-%m"), 0
    g = picker(shdr, ["Key", "Date", "Pages", "Source"])
    for r in get_all(sws)[1:]:
        key, date, pages, src = g(r)
        if key and date > last.get(key, ""):
            last[key] = date
        if src == "Google Places" and date.startswith(month):      # paid requests this calendar month (free-cap guard)
            places_month += int(num(pages, 1) or 1)
    snap = {}
    try:
        nws, nhdr = get_tab(sh, "Snapshots", D.SNAP_H)
        g = picker(nhdr, D.SNAP_H)
        for r in get_all(nws)[1:]:
            pid, date, cnt = g(r)
            if pid and date and (pid not in snap or date < snap[pid][0]):
                snap[pid] = (date, int(num(cnt, 0)))              # earliest snapshot wins (longest window)
    except Exception:
        O.STATS["snapshots"] += 1
    return ws, sws, block, domains, emails, bl, last, places_month, snap


# ------------------------------------------------------------------ place handling
def norm_name(n):
    n = re.sub(r"\b(pty|ltd|p/l|the|and|&|clinic|centre|center|group)\b", " ", n.lower())
    return re.sub(r"[^a-z0-9]+", "", n)


def parse_address(p, loc=None):
    """Address parts from the place; country falls back to the Locations row (never to a hard-coded country)."""
    comp = {}
    for c in p.get("addressComponents", []):
        for t in c.get("types", []):
            comp.setdefault(t, c)
    gg = lambda t, k="longText": (comp.get(t, {}).get(k) or "")
    loc = loc or {}
    country, cc = gg("country"), gg("country", "shortText")
    if country and not cc:
        country, cc, _ = D.country_info(country)
    return {"suburb": gg("locality") or gg("sublocality_level_1") or gg("sublocality"),
            "state": gg("administrative_area_level_1", "shortText"), "postcode": gg("postal_code"),
            "country": country, "cc": cc.upper()}


def blocked_words(S, nic=None):
    return S.list("BLOCK_NAME_WORDS") + S.list("BLOCK_NAME_WORDS_WORLD") + list((nic or {}).get("exclude") or [])


def name_blocked(name, words):
    """Whole words (+ plural/possessive): 'my dentist' never hits 'Tammy Dentistry', 'deli' never hits 'Delicious',
    'mcdonald' hits "McDonald's"."""
    low = re.sub(r"\s+", " ", name.lower().replace("’", "'"))
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?:'?s)?(?![a-z0-9])", low) for w in words if w)


def reject_reason(p, a, S, ctx, state, nic=None, loc=None):
    if p.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
        return "not operating"
    if a["cc"] and a["cc"] != "AU":
        return "outside Australia"
    if a["country"] and a["country"].strip().lower() not in ("australia", "au", "aus"):
        return "outside Australia"
    want = ((loc or {}).get("cc") or "").upper()
    if want and a["cc"] and a["cc"] != want:
        return "outside the location's country"
    name = p.get("displayName", {}).get("text", "")
    if name_blocked(name, blocked_words(S, nic)):
        return "blocked name (chain/public/excluded for niche)"
    if p.get("_brand") and S.yes("REJECT_OSM_BRANDS", True):
        return "chain brand (OSM brand tag)"
    if p.get("_takeaway") == "only":
        return "takeaway only"
    if set(p.get("types", [])) & set(S.list("EXCLUDE_TYPES")):
        return "excluded place type"
    if not p.get("_nometrics"):
        n, rt = int(p.get("userRatingCount", 0) or 0), float(p.get("rating", 0) or 0)
        if n < S.num("MIN_REVIEWS", 8):
            return "too few reviews"
        if n > S.num("MAX_REVIEWS", 600):
            return "too many reviews (big brand)"
        if rt and rt < S.num("MIN_RATING", 3.5):
            return "low rating"
    src = p.get("_source")
    needs_site = S.yes("REQUIRE_WEBSITE_OSM", True) if src == "OpenStreetMap" else (False if src == "YouTube" else S.yes("REQUIRE_WEBSITE", False))
    if needs_site and not p.get("websiteUri") and not p.get("_email"):
        return "no website"
    if src == "YouTube":                 # channels share link-in-bio hosts (linktr.ee) - not a chain signal
        lo, hi = S.num("YT_MIN_SUBS", 5000), S.num("YT_MAX_SUBS", 500000)
        if p.get("_subs") is not None and not lo <= p["_subs"] <= hi:
            return "subscribers outside YT_MIN_SUBS..YT_MAX_SUBS"
        if p.get("_days") is not None and p["_days"] > S.num("YT_MAX_DAYS_SINCE_UPLOAD", 14):
            return "last upload older than YT_MAX_DAYS_SINCE_UPLOAD"
        if p.get("_views") is not None and p["_views"] < S.num("YT_MIN_VIEWS", 20000):
            return "median views below YT_MIN_VIEWS"
        return None
    chain = int((nic or {}).get("chain") or 0) or int(S.num("CHAIN_LOCATIONS", 3))
    nm = norm_name(name)
    host = E._host(p.get("websiteUri", "")) if p.get("websiteUri") else ""
    host = "" if host in E.AGGREGATORS else host
    if ctx["names"][nm] >= chain or state["names_run"][nm] >= chain:
        return "chain (same name in many places)"
    if host and (ctx["hosts"][host] >= chain or state["hosts_run"][host] >= chain):
        return "chain (same website in many places)"
    state["names_run"][nm] += 1
    if host:
        state["hosts_run"][host] += 1
    return None


HEALTH_RULES = {"AU": "AHPRA/TGA"}


_ROLE_RANK = ("owner", "founder", "principal", "director", "ceo", "chief", "licensee", "partner", "general manager",
              "marketing", "practice manager", "manager")


def best_person(people):
    def rank(p):
        r = p[1].lower()
        return next((i for i, w in enumerate(_ROLE_RANK) if w in r), len(_ROLE_RANK))
    return sorted(people, key=rank)[0] if people else None


def name_parts(full):
    toks = full.split()
    title = toks[0].rstrip(".") if toks and toks[0].rstrip(".").lower() in ("dr", "mr", "mrs", "ms") else ""
    core = toks[1:] if title else toks
    first = core[0] if core else ""
    greet = f"Dr {core[-1]}" if title.lower() == "dr" and core else first
    return first, greet


def read_site(site, name, S):
    fb = None
    if O.key("ZENROWS_API_KEY") and S.yes("USE_ZENROWS", True):          # optional: blocked / JS-only home pages
        fb = lambda u: O.zenrows_fetch(u) if O.take("zenrows_run", int(S.num("MAX_ZENROWS_PER_RUN", 30))) else None
    return E.enrich_site(site, int(S.num("MAX_SITE_PAGES", 5)), S.yes("READ_WEBSITES"), name, S.yes("FIND_DECISION_MAKER"),
                         S.num("SITE_TIME_LIMIT_SEC", 25), fb)


def abn_facts(p, a, name, text, S, site_abns=None):
    """Australia: ABN printed on the site (checksum-valid) -> optional official ABN Lookup (ABN_LOOKUP_GUID, free):
    entity name/type, GST, active since, business age. Without a GUID only the printed ABN is kept. Facts only."""
    out = {}
    abns = list(site_abns or []) + [x for x in E.abns_in(text) if x not in (site_abns or [])]
    abn = abns[0] if abns else ""
    if not abn and O.key("ABN_LOOKUP_GUID") and S.yes("USE_ABN_LOOKUP", True) \
            and O.take("abn_name", int(S.num("MAX_ABN_PER_RUN", 300))):
        abn = safe(lambda: O.abn_by_name(name, E.au_state(a["state"]), a["postcode"]), None, "abn") or ""
        if abn:
            out["how"] = "ABN Lookup exact name match"
    if not abn:
        return out
    out["abn"] = abn
    if O.key("ABN_LOOKUP_GUID") and S.yes("USE_ABN_LOOKUP", True) and O.take("abn", int(S.num("MAX_ABN_PER_RUN", 300))):
        d = safe(lambda: O.abn_details(abn), None, "abn")
        if d:
            out.update({"status": d["status"], "entity_name": d["entity_name"], "entity_type": d["entity_type"],
                        "gst": ("Yes, since " + d["gst_from"]) if d["gst_from"] else "No"})
            if d["status"].lower() == "active" and d["status_from"]:
                out["since"] = d["status_from"]
                try:
                    out["age"] = round((datetime.now(timezone.utc).date() - datetime.strptime(d["status_from"][:10], "%Y-%m-%d").date()).days / 365.25, 1)
                except ValueError:
                    pass
    return out


_JINA_COUNT = threading.Lock()


def want_jina(info, S, state):
    """Jina Reader only when the normal read found little, within USE_JINA_MAX per run (thread-safe counter)."""
    if not (ENRICH_WEBSITES and info.get("domain") and O.available("jina")):
        return False
    if info.get("emails") and len(info.get("socials") or {}) >= 2 and info.get("people"):
        return False
    with _JINA_COUNT:
        if state["jina"] >= S.num("USE_JINA_MAX", 40):
            return False
        state["jina"] += 1
    return True


def parallel_n(S):
    return int(max(1, min(16, S.num("PARALLEL_SITE_READS", 10))))


def prefetch(places, S, state, nic=None, pool=None):
    """Queue every website of this search on a worker pool (the slowest step). The loop then takes results in order
    while the workers keep reading ahead: no batch ever waits for its slowest site."""
    n = parallel_n(S)
    if n <= 1 or not S.yes("READ_WEBSITES") or pool is None:
        return
    blocked = blocked_words(S, nic)

    def cheap_ok(p):                                   # skip sites the cheap filters will reject anyway
        if name_blocked(p.get("displayName", {}).get("text", ""), blocked) or p.get("_takeaway") == "only":
            return False
        if p.get("_brand") and S.yes("REJECT_OSM_BRANDS", True):
            return False
        if not p.get("_nometrics"):
            n = int(p.get("userRatingCount", 0) or 0)
            if n < S.num("MIN_REVIEWS", 8) or n > S.num("MAX_REVIEWS", 600):
                return False
        return True
    todo, seen = [], set()
    for p in places:
        u = p.get("websiteUri")
        if (u and u not in seen and u not in state["site_cache"] and p.get("id") not in state["block"]
                and cheap_ok(p)):
            seen.add(u)
            todo.append((u, p.get("displayName", {}).get("text", "")))
    if not todo:
        return

    def one(x):
        try:
            info = read_site(x[0], x[1], S)
        except Exception:
            return None
        if info is not None and want_jina(info, S, state):  # Jina fallback also runs in the worker, not the main loop
            info["_jina"] = O.jina_read(x[0])
        return info
    for x in todo:
        state["site_cache"][x[0]] = pool.submit(one, x)


_INHOUSE = re.compile(r"social media|content (?:creator|manager|producer|lead)|marketing (?:manager|coordinator|executive|lead|director)|"
                      r"digital marketing|community manager", re.I)


def inhouse_from_results(results, company):
    """LinkedIn result titles showing a social/marketing person AT this company. Returns evidence string or ''."""
    toks = E._tokens(company)
    for r in results or []:
        t = (r.get("title") or "").replace("\u2013", "-")
        if "linkedin.com/in/" in (r.get("link") or "") and _INHOUSE.search(t) and toks and any(x in t.lower() for x in toks):
            return "LinkedIn: '" + t.replace(" | LinkedIn", "")[:80] + "'"
    return ""


def niche_q(nic):
    q = nic.get("q")
    if q:
        return q
    d = Q.niche_defaults(nic.get("niche", ""), "")
    return dict(zip(("rmin", "rstrong", "vel", "fweak", "fstrong", "pweak", "pstrong", "ticket", "visits", "rate"), d))


def quality_facts(p, nic, a, loc, name, domain, info, igs, soc, abn, signals, reviews, rating, ctx, state, S):
    """v4 R / S / A / segment for one business lead. Returns dict, or a reject-reason string."""
    q = niche_q(nic)
    cc = (a["cc"] or loc.get("cc") or "").upper()
    fac = S.num("REVIEW_FACTOR_" + (cc or "US"), Q.REVIEW_FACTOR.get(cc, 1.0))
    age = abn.get("age")
    if reviews is not None and reviews < q["rmin"] * fac and not (age and age >= 3):
        return f"too few reviews for this niche ({reviews} < {int(q['rmin'] * fac)})"
    vel, how = None, ""
    snap = state.get("snap", {}).get(p["id"])
    if snap and reviews is not None:
        days = (datetime.strptime(today(S), "%Y-%m-%d") - datetime.strptime(snap[0], "%Y-%m-%d")).days
        if days >= 30 and reviews >= snap[1]:
            vel, how = (reviews - snap[1]) / (days / 30.4), f"review growth since {snap[0]}"
    if vel is None and reviews is not None and age and age >= 1:
        vel, how = reviews / (age * 12), "lifetime average (reviews / ABN age)"
    f = {"reviews": reviews, "velocity": vel, "velocity_how": how, "locations": ctx["hosts"][domain] if domain else None,
         "price_level": p.get("priceLevel"), "upscale": p.get("_upscale"), "spend": len(signals) if domain else None,
         "booking": info.get("booking"), "employees": info.get("_employees"), "gst": str(abn.get("gst", "")).startswith("Yes"),
         "staff": len(info.get("people") or []) or None, "fit": nic.get("fit")}
    R, conf, _ = Q.r_score(f, q, cc, S)
    band, basis = Q.revenue_band(f, q, R, conf, S)
    S_, s_basis = Q.s_score(igs, soc, q, S)
    handle = (soc.get("Instagram", "").rstrip("/").split("/")[-1] if soc.get("Instagram") else "")
    sig = list(info.get("agency") or []) + Q.ig_agency_signals(igs, handle)
    A, ev, ag = Q.a_score(sig, igs, q)
    seg = Q.segment(R, S_, A, S)
    if seg in ("PRIME", "PRIME-REVIEW") and O.search_on() and S.yes("USE_WEB_SEARCH", True):
        qs = f'site:linkedin.com/in "{name}" ("social media" OR "content creator" OR "marketing manager" OR "marketing coordinator")'
        res = safe(lambda: O.web_search(qs, cc or "AU", int(S.num("MAX_INHOUSE_SEARCH_PER_RUN", 15)), "inhouse"), [], "web-search")
        hit = inhouse_from_results(res, name)
        if hit:
            A, ev, ag = Q.a_score(sig + [("inhouse_role", hit, "in-house team")], igs, q)
            seg = Q.segment(R, S_, A, S)
    if seg == "PRIME" and reviews is None:                     # free mode: never PRIME without a review count
        seg = "PRIME-REVIEW"
        ev = "; ".join(x for x in (ev, "no review count: check Google rating/reviews by hand before outreach") if x)
    if seg == "REJECT":
        return "segment REJECT (revenue proxy R too low)" if R < S.num("REJECT_R_BELOW", 3) else "segment REJECT (low R, weak signals)"
    return {"R": R, "conf": conf, "band": band, "basis": basis, "S": S_, "s_basis": s_basis, "A": A, "evidence": ev,
            "agency": ag, "segment": seg, "locations": f["locations"] or "", "price": (p.get("priceLevel") or "").replace("PRICE_LEVEL_", "").replace("_", " ").title()
            or ("Upscale (price range)" if p.get("_upscale") else ""), "booking": info.get("booking", "")}


def find_website(p, a, loc, S):
    """Optional (SERPER_API_KEY / BRAVE_API_KEY): search '<business> <suburb/city>' and keep a result only when its
    domain contains a distinctive word of the business name (never a directory). Returns url or ''."""
    if not (O.search_on() and S.yes("USE_WEB_SEARCH", True)):
        return ""
    name = p.get("displayName", {}).get("text", "")
    where = a["suburb"] or loc.get("city") or loc.get("area", "")
    res = O.web_search(f"{name} {where}".strip(), a["cc"] or loc.get("cc") or "AU", int(S.num("MAX_WEB_SEARCH_PER_RUN", 25)), "web")
    return E.site_from_results(res, name)


def google_metrics(p, a, loc, name, domain, S):
    """Optional (SERPER_API_KEY): Google rating + review count for a free-map lead, from Serper's Google Maps results.
    Accepted only when the Google listing has the same name (or the same website). Returns dict or None."""
    if not (p.get("_nometrics") and O.key("SERPER_API_KEY") and S.yes("USE_SERPER", True)
            and O.take("serper_places", int(S.num("MAX_SERPER_PLACES_PER_RUN", 25)))):
        return None
    where = a["suburb"] or loc.get("city") or loc.get("area", "")
    want = norm_name(name)
    for g in O.serper_places(f"{name} {where}".strip(), a["cc"] or loc.get("cc") or "AU")[:5]:
        same_site = domain and g.get("website") and E._host(g["website"]) == domain
        got = norm_name(g.get("title", ""))
        if same_site or (want and got and (want == got or (len(want) >= 6 and (want in got or got in want)))):
            return g
    return None


def qualify(p, nic, loc, S, ai, state, ctx, found):
    a = parse_address(p, loc)
    r = reject_reason(p, a, S, ctx, state, nic, loc)
    if r in (None, "no website") and not p.get("websiteUri") and p.get("_source") != "YouTube":
        found_site = safe(lambda: find_website(p, a, loc, S), "", "web-search")
        if found_site:
            p["websiteUri"], p["_site_found"] = found_site, True
            if r == "no website":
                r = reject_reason(p, a, S, ctx, state, nic, loc)
    if r:
        return r, None
    name, site = p["displayName"]["text"], p.get("websiteUri", "")
    is_yt = p.get("_source") == "YouTube"
    bl = state["blacklist"]
    if p["id"].lower() in bl or name.lower() in bl or (site and E._host(site) in bl):
        return "blacklisted", None
    used = ["website via search"] if p.get("_site_found") else []

    # 1) free built-in website read (prefetched in parallel when possible)
    host0 = E._host(site) if site else ""
    base = {"website": site, "domain": "" if host0 in E.AGGREGATORS else host0, "emails": [], "email_source": "", "socials": {},
            "notes": [], "people": [], "phones": [], "signals": [], "text": ""}
    cached = state["site_cache"].pop(site, None) if site else None
    if isinstance(cached, Future):
        cached = safe(lambda: cached.result(timeout=180), None, "site-read")
    info = (cached or safe(lambda: read_site(site, name, S), base, "site-read")) if site else base
    for k, v in base.items():
        info.setdefault(k, v)
    domain, emails, source, soc, notes = info["domain"], list(info["emails"]), info["email_source"], dict(info["socials"]), list(info["notes"])
    people, phones, signals, text = list(info["people"]) + list(p.get("_people") or []), list(info["phones"]), list(info["signals"]), info["text"] or ""
    for e in E.emails_in((p.get("_email") or "").replace(";", " ")):
        if e not in emails:
            emails.insert(0, e)
            src_ = p.get("_source") or ""
            source = source or ("Scraper import" if src_.startswith("Import") else
                                {"Manual input": "Input tab", "YouTube": "YouTube description"}.get(src_, "OpenStreetMap tag"))
    for plat, k in (("Instagram", "_ig"), ("Facebook", "_fb"), ("YouTube", "_yt")):
        if p.get(k):
            soc.setdefault(plat, p[k])
    for k, v in (p.get("_socials") or {}).items():
        soc.setdefault(k, v)
    if p.get("_text"):
        text = p["_text"] + "\n" + text
    if is_yt and p.get("_desc"):
        text = p["_desc"] + "\n" + text
        if S.yes("FIND_DECISION_MAKER"):
            people += [(n_, r_, "YouTube description") for n_, r_, _ in E.people_in("", p["_desc"], name)]
    info.setdefault("email_sources", {}).update(p.get("_email_sources") or {})
    info.setdefault("email_associations", {}).update(p.get("_email_associations") or {})
    summary = ""

    # 2) OPTIONAL Jina Reader fallback: only when the normal read found little (never blocks).
    #    Usually already done by the site-reader worker ('_jina'); otherwise done here.
    if "_jina" in info:
        j = info["_jina"]
    elif site and want_jina(dict(info, emails=emails, socials=soc, people=people), S, state):
        j = O.jina_read(site)
    else:
        j = None
    if j:
        used.append("jina")
        summary = j["summary"]
        text += "\n" + j.get("text", "")
        for e in j["emails"]:
            if e not in emails:
                emails.append(e)
                source = source or "Jina Reader"
        for k, v in j["socials"].items():
            soc.setdefault(k, v)
        if S.yes("FIND_DECISION_MAKER") and not people:
            people += [(n_, r_, "website text (Jina)") for n_, r_, _ in E.people_in("", j.get("text", ""), name)]
    emails = E.rank_emails(emails, domain)[:6]

    # niche rules from the Niches tab (e.g. restaurants: upscale or multi-venue only; gyms: no franchises)
    hay = name + "\n" + text
    if nic.get("reject") is not None and nic["reject"].search(hay):
        return "website matches the niche's reject rule (e.g. franchise)", None
    if nic.get("must") is not None:
        multi = bool(domain) and ctx["hosts"][domain] >= 2              # same website for 2+ venues in this search
        if not (nic["must"].search(hay) or multi or p.get("_upscale") or p.get("priceLevel") in
                ("PRICE_LEVEL_EXPENSIVE", "PRICE_LEVEL_VERY_EXPENSIVE")):
            return "not upscale / multi-venue (niche rule)", None

    # 3) OPTIONAL Hunter domain search: when no email OR no decision maker yet (Hunter returns names + positions)
    org, employees, hunter_mail = "", "", {}
    if (domain and (not emails or not people) and S.yes("USE_HUNTER") and O.key("HUNTER_API_KEY") and O.available("hunter")
            and state["hunter"] < S.num("MAX_HUNTER_PER_RUN", 8)):
        state["hunter"] += 1
        h = O.hunter_find(domain)
        if h:
            used.append("hunter")
            org = h["org"]
            info["email_sources"].update(h.get("email_sources", {}))
            info["email_associations"].update(h.get("email_associations", {}))
            for e in h["emails"]:
                if e not in emails:
                    emails.append(e)
                    source = source or "Hunter"
            for nm, pos, how, em in h.get("people", []):
                if nm.lower() not in {x[0].lower() for x in people}:
                    people.append((nm, pos, how))
                hunter_mail[nm.lower()] = em
    # 4) OPTIONAL Apollo company info
    if (domain and S.yes("USE_APOLLO") and O.key("APOLLO_API_KEY") and O.available("apollo")
            and state["apollo"] < S.num("MAX_APOLLO_PER_RUN", 20)):
        state["apollo"] += 1
        ap = O.apollo_org(domain)
        if ap:
            org, employees = ap[0] or org, ap[1]
            info["_employees"] = employees
            used.append("apollo")

    emails = [e for e in emails if e.lower() not in bl and e.split("@")[1].lower() not in bl]
    if any(e.lower() in state["emails"] for e in emails):
        return "duplicate email (already added)", None
    if domain and domain in state["domains"]:
        return "same website already added", None

    dm = best_person(people)
    if dm and dm[0].lower() in hunter_mail and hunter_mail[dm[0].lower()] not in emails:
        emails.insert(0, hunter_mail[dm[0].lower()])
    p_mail, g_mail, others = E.classify_emails(emails, domain, dm[0] if dm else "", is_yt, associations=info["email_associations"])
    # 5) OPTIONAL Hunter email finder: known name, no personal email yet
    if (dm and not p_mail and domain and S.yes("USE_HUNTER_FINDER") and O.key("HUNTER_API_KEY") and O.available("hunter")
            and state["hfind"] < S.num("MAX_HUNTER_FINDER_PER_RUN", 5)):
        state["hfind"] += 1
        fe = O.hunter_finder(domain, dm[0])
        if fe and fe not in emails:
            emails.insert(0, fe)
            p_mail, g_mail, others = E.classify_emails(emails, domain, dm[0], is_yt, associations=info["email_associations"])
            source = source or "Hunter email finder"
            used.append("hunter-finder")

    # 6) email verification: personal + generic first. Free MX check always; ZeroBounce/Hunter if keys. Unknown => keep.
    status, dropped, verification = "", 0, {}
    if emails and state["verify"] < S.num("MAX_VERIFY_PER_RUN", 80):
        bad = set()
        for e in [x for x in (p_mail, g_mail) if x] + others[:1]:
            if state["verify"] >= S.num("MAX_VERIFY_PER_RUN", 80):
                break
            state["verify"] += 1
            v = O.verify_email(e)
            verification[e] = v or "unknown"
            if v == "invalid":
                bad.add(e)
                dropped += 1
            elif not status and v:
                status = v
        if bad:
            emails = [e for e in emails if e not in bad]
            p_mail, g_mail, others = E.classify_emails(emails, domain, dm[0] if dm else "", is_yt, associations=info["email_associations"])
            notes.append(f"{dropped} email(s) dropped: failed verification")
        if status:
            used.append({"valid": "verified", "risky": "catch-all", "mx-ok": "mx-ok"}.get(status, status))
    email_status = {"valid": "valid", "risky": "catch-all (risky)", "mx-ok": "domain accepts mail"}.get(status, "unverified" if emails else "")

    phone = p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or (phones[0] if phones else "")
    social_any = any(k in soc for k in ("Instagram", "LinkedIn", "Facebook", "Twitter / X", "TikTok"))
    # 7) OPTIONAL Instagram activity: Meta first, Apify as optional fallback
    igs, ig_handle = None, (soc.get("Instagram", "").rstrip("/").split("/")[-1] if soc.get("Instagram") else "")
    if ig_handle and not is_yt and S.yes("USE_INSTAGRAM_STATS"):
        if O.key("IG_ACCESS_TOKEN") and O.available("meta_ig") and state["ig"] < S.num("MAX_IG_PER_RUN", 40):
            state["ig"] += 1
            igs = O.ig_meta(ig_handle)
            used += ["meta-ig"] if igs else []
        if not igs and S.yes("USE_APIFY", False) and O.key("APIFY_API_TOKEN") and O.available("apify") \
                and state["apify"] < S.num("MAX_APIFY_PER_RUN", 10):
            state["apify"] += 1
            igs = O.ig_apify(ig_handle)
            used += ["apify"] if igs else []
    sev, gap = SC.yt_gap(p.get("_uploads30")) if is_yt else SC.social_gap(soc, igs)
    if not is_yt and p.get("_nometrics"):                 # optional: Google rating/reviews for free-map leads
        g = safe(lambda: google_metrics(p, a, loc, name, domain, S), None, "serper-places")
        if g and g.get("ratingCount") is not None:
            used.append("google-rating (serper)")
            p["rating"], p["userRatingCount"], p["_nometrics"] = g.get("rating"), int(g.get("ratingCount") or 0), False
            p["googleMapsUri"] = p.get("googleMapsUri") if not g.get("cid") else f"https://maps.google.com/?cid={g['cid']}"
            n_rev = p["userRatingCount"]
            if n_rev < S.num("MIN_REVIEWS", 8):
                return "too few reviews (Google, via Serper)", None
            if n_rev > S.num("MAX_REVIEWS", 600):
                return "too many reviews (big brand, via Serper)", None
            if g.get("rating") and float(g["rating"]) < S.num("MIN_RATING", 3.5):
                return "low rating (Google, via Serper)", None
            phone = phone or g.get("phoneNumber", "")
    au = (a["cc"] or loc.get("cc") or "").upper() == "AU"
    abn = abn_facts(p, a, name, text, S, info.get("abns")) if au and not is_yt else {}
    if abn.get("status", "").lower() == "cancelled":
        return "ABN cancelled (business closed)", None
    rating = float(p["rating"]) if p.get("rating") else None
    reviews = int(p["userRatingCount"]) if p.get("userRatingCount") else None
    subs = p.get("_subs") if is_yt else None

    v4 = S.yes("SEGMENT_SCORING", True) and not is_yt
    if v4:                                                     # R revenue proxy x S social x A agency -> segment
        qual = quality_facts(p, nic, a, loc, name, domain, info, igs, soc, abn, signals, reviews, rating, ctx, state, S)
        if isinstance(qual, str):
            notes.append(qual)
            qual = {"R": 0, "S": 0, "A": 0, "segment": "RESEARCH"}
    elif is_yt:
        seg_, ev_, ag_ = Q.yt_segment(p.get("_desc", ""))
        qual = {"segment": seg_, "evidence": ev_, "agency": ag_, "A": 60 if seg_ == "HAS-AGENCY" else 0}
    else:
        qual = {}
    side = qual.get("segment") in ("HAS-AGENCY", "POLISHED-NO-SIGNAL") and not is_yt


    def calc(person):
        if v4:
            return Q.icp3({"R": qual["R"], "S": qual["S"], "A": qual["A"], "fit": nic["fit"],
                           "contact": Q.contact_level(p_mail, g_mail or others, status, "Instagram" in soc, social_any, phone),
                           "dm": bool(person), "rating": rating, "priority": loc["priority"]}, S)
        return SC.score({"fit": nic["fit"], "reviews": reviews, "rating": rating, "subs": subs, "has_website": bool(site and domain),
                         "personal_email": bool(p_mail), "generic_email": bool(g_mail or others), "email_status": status,
                         "instagram": "Instagram" in soc, "other_social": social_any, "phone": bool(phone),
                         "decision_maker": bool(person), "gap_sev": sev, "spend": len(signals), "priority": loc["priority"],
                         "age": abn.get("age")}, S)
    sc = calc(dm)
    min_sc = S.num("MIN_ICP_SCORE", 7) * 10
    dm_w = S.num("W3_DECISION_MAKER", 5) if v4 else S.num("W2_DECISION_MAKER", 10)
    li_person = ""
    if not dm and not is_yt and S.yes("FIND_DECISION_MAKER") and O.search_on() and S.yes("USE_WEB_SEARCH", True):
        q = f'site:linkedin.com/in "{name}" (owner OR founder OR director OR principal OR CEO)'
        res = safe(lambda: O.web_search(q, a["cc"] or "AU", int(S.num("MAX_LINKEDIN_SEARCH_PER_RUN", 15)), "linkedin"), [], "web-search")
        hits = E.people_from_linkedin(res, name)
        if hits:
            dm = (hits[0][0], hits[0][1], "LinkedIn search result (name + role + company in the title)")
            li_person = hits[0][2]
            used.append("linkedin-search")
            p_mail, g_mail, others = E.classify_emails(emails, domain, dm[0], is_yt, associations=info["email_associations"])
            sc = calc(dm)

    first, greet = name_parts(dm[0]) if dm else ("", "")
    facts = {"name": name, "city": loc.get("city", "") or a["suburb"], "rating": rating or "", "reviews": reviews or "",
             "gap": gap, "niche": nic["niche"], "summary": summary, "first": greet, "signals": signals, "subs": subs,
             "youtube": is_yt, "text": text, "country": a["country"]}
    pitch_line = SC.pitch(nic["tpl"], facts)
    if rating is None:
        pitch_line = pitch_line.replace(" at  stars", "").replace("  reviews", "")
    # 7b) OPTIONAL email finders for a KNOWN decision maker with no personal email, only for leads that already
    #     passed the ICP gate (>= MIN_ICP_SCORE): Prospeo (PROSPEO_API_KEY), then Snov.io (SNOV_CLIENT_ID + _SECRET).
    #     Only an address on the business's own domain is accepted; a failed verification drops it.
    if dm and not p_mail and domain and not is_yt:
        for svc, fn, kname, cap in (("prospeo", O.prospeo_finder, "PROSPEO_API_KEY", "MAX_PROSPEO_PER_RUN"),
                                    ("snov", O.snov_finder, "SNOV_CLIENT_ID", "MAX_SNOV_PER_RUN")):
            if not (O.key(kname) and S.yes("USE_" + svc.upper(), True) and O.take(svc + "_run", int(S.num(cap, 10)))):
                continue
            fe = safe(lambda: fn(domain, dm[0]), None, svc)
            if fe and fe not in emails and O.verify_email(fe) != "invalid":
                emails.insert(0, fe)
                p_mail, g_mail, others = E.classify_emails(emails, domain, dm[0], is_yt, associations=info["email_associations"])
                source = source or {"prospeo": "Prospeo", "snov": "Snov.io"}[svc]
                used.append(svc)
                sc = calc(dm)
                break
    # 8) Lead Insight: ONE small AI call per qualified lead (optional) -> 3-4 lines + a verbatim-checked decision maker.
    #    No key / 429 / quota / error -> fact-only insight. A 429 stops AI for the rest of the run (no retry-spin).
    facts.update({"group": nic["group"], "segment": qual.get("segment", ""), "band": qual.get("band", ""),
                  "agency": qual.get("agency", "")})
    ai_ok = (ai is not None and S.yes("USE_AI_INSIGHT", True) and not state["ai_off"]
             and state["ai_email"] < S.num("MAX_AI_INSIGHTS_PER_RUN", 60))
    if ai_ok:
        state["ai_email"] += 1
    res = IN.build(ai if ai_ok else None, facts, want_person=not dm and S.yes("FIND_DECISION_MAKER"))
    if res["quota"]:
        state["ai_off"], state["ai_quota"] = True, True
    elif res["ai_failed"]:
        state["ai_fail"] += 1
        if state["ai_fail"] >= 3:                                # 3 failures in a run: stop trying, save time
            state["ai_off"] = True
    if res["ai_used"]:
        used.append("ai-insight")
    elif ai is not None and S.yes("USE_AI_INSIGHT", True):
        used.append("insight: fact-only (AI quota hit)" if state.get("ai_quota") else "insight: fact-only (AI off/failed)")
    if res["person"] and not dm:
        dm = (res["person"][0], res["person"][1], "website text (AI-found, verbatim checked)")
        first, greet = name_parts(dm[0])
        p_mail, g_mail, others = E.classify_emails(emails, domain, dm[0], is_yt, associations=info["email_associations"])
        sc = calc(dm)

    if not emails:
        notes.append("No public email: use Instagram/contact form" + (" or phone" if phone else ""))
    if nic["group"] == "Medical":
        notes.append("Health advertising rules apply (" + HEALTH_RULES.get(a["cc"], "check the local health-ad rules") + ")")
    if not p_mail and g_mail:
        notes.append("Generic mailbox only: ask for " + (dm[0] if dm else "the owner / marketing manager"))
    if p.get("_nometrics") and not is_yt:
        notes.append("Rating/reviews not available from this free source")
    other = [f"{k[6:]}: {v}" for k, v in soc.items() if k.startswith("Other:")]
    if li_person:
        other.insert(0, f"LinkedIn (decision maker): {li_person}")
    now = today(S)
    lead = {
        "Niche": nic["niche"], "Group": nic["group"], "ICP Score /10": SC.icp(sc), "Tier": SC.tier_of(sc, S),
        "Company Name": name, "Decision Maker Name": dm[0] if dm else "", "First Name": first,
        "Decision Maker Role": dm[1] if dm else "", "Name Source": dm[2] if dm else "",
        "Personal Email": p_mail, "Generic Email": g_mail, "Other Emails": ", ".join(others), "Email Verified": email_status,
        "Phone": phone, "Instagram": soc.get("Instagram", ""), "Facebook": soc.get("Facebook", ""),
        "LinkedIn": soc.get("LinkedIn", ""), "TikTok": soc.get("TikTok", ""), "YouTube": soc.get("YouTube", ""),
        "Twitter / X": soc.get("Twitter / X", ""), "Other Socials": " | ".join(other), "Website": site,
        "Full Address": p.get("formattedAddress", ""), "Suburb": a["suburb"], "City": a["suburb"],
        "State": a["state"], "Postcode": a["postcode"], "Country": a["country"],
        "Map Url": p.get("googleMapsUri", ""), "Lead Insight": res["insight"],
        "Suggested Pitch /mo": SC.pitch_price(sc, nic["fit"], S, youtube=is_yt or nic["group"].lower() == "youtube"),
        "Pitch Currency": (S.get("PITCH_CURRENCY") or "AUD").upper(), "Country Code": a["cc"],
        "YT Last Upload (days)": p.get("_days", "") if is_yt and p.get("_days") is not None else "",
        "Pitch Angle": pitch_line, "Social Gap": gap,
        "Website Summary": summary, "Ad / Spend Signals": ", ".join(signals), "Google Rating": rating or "",
        "Review Count": reviews or "", "Employee Count": employees,
        "Business Type": p.get("primaryTypeDisplayName", {}).get("text", ""),
        "YT Subscribers": subs or "", "YT Uploads 30d": p.get("_uploads30", "") if is_yt else "",
        "IG Followers": igs["followers"] if igs else "", "IG Engagement %": igs["engagement"] if igs else "",
        "IG Posts 30d": igs["posts30"] if igs else "", "Email Source": source,
        "Email Type": E.email_type(p_mail or g_mail, domain) if (p_mail or g_mail) else "",
        "Notes": "; ".join(notes), "Enrichment": ", ".join(used) or "base only", "Data Source": p.get("_source", "Google Maps"),
        "Found On": found, "Status": "New", "My Notes": "", "Last Checked": now, "Lead Added On": now,
        "Score": sc, "Place Id": p["id"],
        "Segment": qual.get("segment", ""), "Segment Rank": Q.SEGMENT_RANK.get(qual.get("segment"), ""),
        "Revenue Band": qual.get("band", "n/a (YouTube)" if is_yt else ""), "Revenue Est. Basis": qual.get("basis", ""),
        "R Score /10": qual.get("R", ""), "R Confidence": f"{qual['conf']:.0%}" if "conf" in qual else "",
        "S Score /10": qual.get("S", ""), "S Basis": qual.get("s_basis", ""), "Agency Score /100": qual.get("A", ""),
        "Agency Name": qual.get("agency", ""), "Agency Evidence": qual.get("evidence", ""),
        "Locations (same website)": qual.get("locations", ""), "Price Level": qual.get("price", ""),
        "Booking Tool": qual.get("booking", ""), "YT Median Views": p.get("_views", "") if is_yt and p.get("_views") is not None else "",
        # Australia-only columns (written only where the tab has them: the AU edition)
        "ABN": E.fmt_abn(abn.get("abn", "")), "ABR Entity Name": abn.get("entity_name", ""), "Entity Type": abn.get("entity_type", ""),
        "GST Registered": abn.get("gst", ""), "ABN Active Since": abn.get("since", ""),
        "Business Age (yrs)": abn.get("age", "") if abn.get("age") is not None else "",
        "Phone Type": E.phone_type(phone, "AU") if au else "", "AU Timezone": E.AU_TZ.get(E.au_state(a["state"] or loc.get("state", "")), "") if au else "",
        "_domain": domain, "_all": ";".join(emails)}
    lead.update({"_people": people + ([dm] if dm and dm not in people else []), "_text": text,
        "_email_associations": info.get("email_associations", {}), "_email_sources": info.get("email_sources", {}),
        "_social_observations": p.get("_social_observations") or dict(igs or {}, source=soc.get("Instagram", "")),
        "_commercial_evidence": p.get("_commercial_evidence", []),
        "_niche_fit": nic.get("fit"), "_niche_why": nic.get("why", ""),
        "Source URL": p.get("googleMapsUri") or p.get("_evidence_source") or site, "Source": p.get("_source", "Google Places"),
        "Australia Evidence": ("Explicit Australian country/address in source") if a["cc"] == "AU" else "",
        "Australia Evidence Source": p.get("googleMapsUri") or p.get("_evidence_source", ""),
        "Email Verification Details": json.dumps(verification, sort_keys=True),
        "ABN Source": ("https://abr.business.gov.au/ABN/View?id=" + abn.get("abn", "")) if abn.get("abn") else "",
        "Business Status": p.get("businessStatus", ""), "Decision Maker LinkedIn": li_person,
        "Email Sources": json.dumps(info.get("email_sources", {}), ensure_ascii=False),
        "Phone Number": phone, "Company LinkedIn": soc.get("LinkedIn", ""),
        "Company Size": employees, "Date Added": now, "Last Verified": now})
    lead.update(QUAL.evaluate(lead, Settings(dict(S, ICP_TIER_B_MIN=str(max(70, nic.get("icp_min", 70)))))))
    if lead.get("Suggested Service") and nic.get("service"):
        lead["Suggested Service"] = nic["service"]
    if lead.get("ICP Tier") == "Rejected":
        return lead.get("Research Reasons") or "failed qualification requirements", None
    lead["Decision Maker Name"] = lead["Decision Maker Full Name"]  # legacy sheet compatibility only
    lead["Outreach Status"] = "Ready For Review" if lead["ICP Tier"].startswith(("A", "B")) else "Research Needed"
    lead["Contact Records"] = json.dumps(lead["_people"], ensure_ascii=False)
    return None, lead



def process_places(places, nic, loc, S, ai, state, cap_left, found):
    ctx = {"names": Counter(norm_name(p.get("displayName", {}).get("text", "")) for p in places),
           "hosts": Counter(E._host(p["websiteUri"]) for p in places if p.get("websiteUri"))}
    leads, seen_rows, stats = [], [], Counter()
    day = today(S)
    pool = ThreadPoolExecutor(parallel_n(S)) if parallel_n(S) > 1 else None
    safe(lambda: prefetch(places, S, state, nic, pool), None, "prefetch")
    for i, p in enumerate(places):
        if not p.get("id") or p["id"] in state["block"]:
            continue
        if len(leads) >= cap_left or time.time() > state["deadline"]:
            break
        snap = state.setdefault("snap", {})
        if p.get("userRatingCount") is not None and p["id"] not in snap:      # first review-count snapshot -> velocity later
            snap[p["id"]] = (day, int(p["userRatingCount"]))
            state.setdefault("snap_rows", []).append([p["id"], day, int(p["userRatingCount"])])
        try:
            reason, lead = qualify(p, nic, loc, S, ai, state, ctx, found)
        except Exception as e:                         # last-resort guard: one bad business never stops the loop
            reason, lead = f"error: {type(e).__name__}", None
            state["problems"].append(f"{str(p.get('id', '?'))[:14]}: {type(e).__name__} {type(e).__name__}")
        state["block"].add(p["id"])
        if lead:
            dom, alle = lead.pop("_domain"), lead.pop("_all")
            state["emails"].update(e.lower() for e in alle.split(";") if e)
            if dom:
                state["domains"].add(dom)
            leads.append(lead)
            seen_rows.append([p["id"], dom, alle, nic["niche"], day, "Added"])
            stats["added"] += 1
            stats["qualified"] += int(lead.get("ICP Tier", "").startswith(("A", "B")))
        else:
            seen_rows.append([p["id"], "", "", nic["niche"], day, "Rejected: " + reason])
            stats["rej: ICP below minimum" if reason.startswith("ICP") else "rej: " + reason] += 1
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)  # cap/deadline reached: stop reading sites nobody needs
    state["site_cache"].clear()                        # never hold pages of other searches in memory
    stats["discovered"] = len(places)
    stats["places"] = sum(stats[k] for k in stats if k.startswith("rej: ")) + stats["added"]
    return leads, seen_rows, stats


# ------------------------------------------------------------------ manual input tab (always free)
def manual_places(sh, S, niches):
    ws, hdr = get_tab(sh, "Leads Input", D.INPUT_H)
    rows = get_all(ws)
    ix = {h: hdr.index(h) for h in D.INPUT_H if h in hdr}
    g = lambda r, h: (r[ix[h]].strip() if h in ix and ix[h] < len(r) else "")
    out = []
    for r in rows[1:]:
        name = g(r, "Business Name")
        if not name:
            continue
        site = g(r, "Website")
        site = site if (not site or site.startswith("http")) else "https://" + site
        key = hashlib.md5((norm_name(name) + E._host(site)).encode()).hexdigest()[:12]
        nt = g(r, "Niche").lower()
        nic = next((n for n in niches if nt and (nt in n["niche"].lower() or n["niche"].lower() in nt)), None) or {
            "niche": g(r, "Niche") or "Manual Leads", "group": "Manual", "fit": 3, "low": int(S.num("MIN_BUDGET_AUD", 500)),
            "high": 2000, "tpl": "{name}. Gap: {gap}. Offer a free 30-60s sample reel.", "why": ""}
        st = g(r, "State")
        country, cc, cur = D.country_info(g(r, "Country")) if g(r, "Country") else ("", "", "")
        loc = {"city": g(r, "City"), "state": st, "area": "Manual", "priority": 2, "country": country, "cc": cc, "currency": cur}
        ig = g(r, "Instagram")
        place = {"id": "input:" + key, "displayName": {"text": name}, "websiteUri": site, "nationalPhoneNumber": g(r, "Phone"),
                 "formattedAddress": ", ".join(x for x in (g(r, "City"), st, country) if x), "businessStatus": "OPERATIONAL",
                 "addressComponents": [{"longText": g(r, "City"), "shortText": g(r, "City"), "types": ["locality"]},
                                       {"longText": st, "shortText": st, "types": ["administrative_area_level_1"]}]
                 + ([{"longText": country, "shortText": cc, "types": ["country"]}] if country else []),
                 "_nometrics": True, "_source": "Manual input", "_email": g(r, "Email"),
                 "_ig": (ig if ig.startswith("http") else ("https://www.instagram.com/" + ig.lstrip("@/") if ig else "")),
                 "primaryTypeDisplayName": {"text": g(r, "Niche")}}
        research.attach(place, {h: g(r,h) for h in research.FIELDS})
        out.append((nic, loc, place))
    return out


# ------------------------------------------------------------------ output: append-only lead tabs, Stats, Meta, Master
# v5: rows are ONLY ever added below the last used row. Nothing is sorted, inserted, rebuilt or rewritten, so Status /
# My Notes (and any column you or the outreach bot add) never move or change. Columns are mapped by HEADER NAME.
KEY_COLS = ("Place Id", "Company Name", "Niche", "Status", "My Notes", "Website", "Segment", "Lead Added On")
SEG_COUNT = ("PRIME", "PRIME-REVIEW", "GROWING", "HAS-AGENCY", "POLISHED-NO-SIGNAL")


def _q(ws):
    return _qt(ws.title)


def _watched(hdr):
    return [h for h in hdr if h in KEY_COLS or (h and h not in D.LEAD_H and h not in D.OLD_LEAD_COLS)]


def _tab_info(ws, hdr, cols):
    """cols: header -> column values (row 1 first). 'last' = last row used in ANY watched column."""
    last = max([1] + [len(cols.get(h) or []) for h in _watched(hdr)])
    pid = (cols.get("Place Id") or [])[1:]
    seg = (cols.get("Segment") or [])[1:]
    day = (cols.get("Lead Added On") or [])[1:]
    return {"ws": ws, "hdr": hdr, "last": last, "pids": {str(x).strip() for x in pid if str(x).strip()},
            "segs": Counter(str(x).strip() for x in seg if str(x).strip()),
            "days": Counter(str(x).strip()[:10] for x in day if str(x).strip()),
            "total": sum(1 for x in pid if str(x).strip())}


def _read_cols(sh, items):
    """items: [(ws, hdr)] -> {title: info}. One batched read for the watched columns of every tab."""
    ranges, owners = [], []
    for ws, hdr in items:
        for h in _watched(hdr):
            c = col_letter(hdr.index(h) + 1)
            ranges.append(f"{_q(ws)}{c}:{c}")
            owners.append((ws.title, h))
    vals = batch_values(sh, ranges, "COLUMNS") if ranges else []
    cols = {ws.title: {} for ws, _ in items}
    for (t, h), v in zip(owners, vals):
        cols[t][h] = [str(x) for x in (v[0] if v else [])]
    return {ws.title: _tab_info(ws, hdr, cols[ws.title]) for ws, hdr in items}


def scan_lead_tabs(sh):
    """Every lead tab (row 1 has 'Place Id' and 'Company Name'), with its last used row, Place Ids and counts.
    Cost: 2-4 batched reads for the whole sheet, never a full re-read of the rows."""
    wss = list(_CACHE["ws"].values()) if (_CACHE["ws"] and _CACHE["sh"] is sh) else retry(lambda: sh.worksheets())
    wss = [w for w in wss if w.title.lower() not in RESERVED and w.title not in OPS.RESERVED
           and not w.title.startswith(("OLD ", "Backup ", "Legacy "))]
    if not wss:
        return {}
    heads = batch_values(sh, [_q(w) + "1:1" for w in wss])
    items = []
    for w, v in zip(wss, heads):
        hdr = [str(h).strip() for h in (v[0] if v else [])]
        if "Place Id" in hdr and "Company Name" in hdr:
            items.append((w, hdr))
    return _read_cols(sh, items)


def cells_used(sh):
    """Grid cells of all tabs (the 10M limit counts empty grid cells too)."""
    wss = list(_CACHE["ws"].values()) if (_CACHE["ws"] and _CACHE["sh"] is sh) else retry(lambda: sh.worksheets())
    return sum(int(getattr(w, "row_count", 0) or 0) * int(getattr(w, "col_count", 0) or 0) for w in wss)


class SheetFull(Exception):
    pass


def write_leads(sh, title, leads, book, S, cache):
    """Append lead dicts below the last used row of tab `title`, by header name. Returns rows written.
    - re-reads that tab's header + watched columns first (2 small reads): picks up columns you moved or added and
      rows typed by hand, and drops leads whose Place Id is already anywhere in this sheet;
    - writes to an explicit range (A<last+1>), so a retried request can never append the same rows twice;
    - a tab deleted or renamed during the run is recreated once."""
    book['_last_written'] = []
    if not leads:
        return 0
    for attempt in (1, 2):
        try:
            ws, hdr = get_lead_tab(sh, title, cache)
            head = batch_values(sh, [_q(ws) + "1:1"])
            live = [str(h).strip() for h in (head[0][0] if head and head[0] else [])]
            hdr = live or hdr
            if any(h not in hdr for h in ("Place Id", "Company Name", "Lead Insight")):
                hdr = ensure_headers(ws, hdr, D.LEAD_H)       # key column deleted by hand: put it back at the end
            cache[title] = (ws, hdr)
            info = _read_cols(sh, [(ws, hdr)])[ws.title]
            book[ws.title] = info
            have = book.setdefault("_pids", set())
            have |= info["pids"]
            todo, seen = [], set()
            for l in leads:
                pid = str(l.get("Place Id", "")).strip()
                if pid and pid not in have and pid not in seen:
                    seen.add(pid)
                    todo.append(l)
            if not todo:
                return 0
            start = info["last"] + 1
            end = start + len(todo) - 1
            if end > ws.row_count:
                grow = end - ws.row_count + 200
                used = cells_used(sh)
                if used + grow * max(ws.col_count, len(hdr)) > S.num("CELL_LIMIT_STOP", 9_500_000):
                    raise SheetFull(f"sheet is near Google's 10M-cell limit ({used:,} cells): new leads not written")
                retry(lambda: ws.add_rows(grow))
            if ws.col_count < len(hdr):
                retry(lambda: ws.resize(cols=len(hdr)))
            rows = [[cell(l.get(h, "")) for h in hdr] for l in todo]
            retry(lambda: ws.update(range_name=f"A{start}", values=rows, value_input_option="RAW"))
            book['_last_written'] = todo
            info["last"] = end
            for l in todo:
                p = str(l["Place Id"]).strip()
                info["pids"].add(p)
                have.add(p)
                info["total"] += 1
                info["segs"][str(l.get("Segment", "")).strip()] += 1
                info["days"][str(l.get("Lead Added On", ""))[:10]] += 1
            return len(todo)
        except SheetFull:
            raise
        except gspread.exceptions.APIError as e:
            msg = str(e).lower()
            if attempt == 1 and ("unable to parse range" in msg or "not found" in msg or "no grid with id" in msg):
                cache.pop(title, None)                       # tab deleted or renamed during the run: recreate it
                _CACHE["ws"] = {}
                continue
            raise
    return 0


# ---- Meta (hidden key/value tab: run health, lock, the counters the Master Dashboard reads)
META_H = ["Key", "Value"]
META_KEYS = ["summary", "last_run", "health", "registration", "lock", "stats_rows", "master_rows", "version"]


def read_meta(sh):
    ws, _ = get_tab(sh, "Meta", META_H)
    rows = get_all(ws)
    return ws, {str(r[0]).strip(): (str(r[1]) if len(r) > 1 else "") for r in rows[1:] if r and str(r[0]).strip()}


def write_meta(sh, ws, meta):
    """Fixed key order -> values overwrite the same cells every run (never a new row per run)."""
    if ws is None:
        return
    keys = META_KEYS + sorted(k for k in meta if k not in META_KEYS)
    rows = [META_H] + [[k, cell(meta.get(k, ""))] for k in keys]
    retry(lambda: ws.update(range_name="A1", values=rows, value_input_option="RAW"))
    try:
        retry(lambda: sh.batch_update({"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": ws.id, "hidden": True}, "fields": "hidden"}}]}))
    except Exception:
        pass


def lock_held(meta, run_id, S):
    """Another job is writing this sheet right now (its lock is younger than the max runtime + 15 min)."""
    v = str(meta.get("lock", ""))
    if "|" not in v:
        return False
    rid, ts = v.split("|", 1)
    if rid == run_id:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 60
    except ValueError:
        return False
    return 0 <= age < S.num("MAX_RUNTIME_MIN", 42) + 15


def summary_of(book, S):
    """{date, tabs: {niche tab: {total, today, prime, segs}}} from the scan + rows written this run (no extra reads)."""
    t0 = today(S)
    out = {}
    for t, inf in book.items():
        if t.startswith("_"):
            continue
        out[t] = {"total": inf["total"], "today": inf["days"].get(t0, 0), "prime": inf["segs"].get("PRIME", 0),
                  "segs": {k: inf["segs"].get(k, 0) for k in SEG_COUNT}}
    return {"date": t0, "tabs": out}


def _bar(col, r, first, last, color="#34a853"):
    return (f'=IFERROR(SPARKLINE({col}{r},{{"charttype","bar";"max",MAX({col}${first}:{col}${last});'
            f'"color1","{color}"}}),"")')


def _stats_rows(label, summ, last_run, health, reg, S):
    tabs = sorted(summ["tabs"].items(), key=lambda kv: tab_rank(kv[0]))
    first = 11
    last = first + max(0, len(tabs) - 1)
    rows = [[f"LEAD STATS: {label}", "", f"Updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
             f"Dates in {S.get('TIMEZONE') or 'UTC'}"],
            [""],
            ["Total leads", sum(v["total"] for _, v in tabs)],
            [f"Leads today ({summ['date']})", sum(v["today"] for _, v in tabs)],
            ["PRIME leads", sum(v["prime"] for _, v in tabs)],
            ["Last run", last_run],
            ["Run health", health],
            ["Keys registered", reg],
            [""],
            ["Niche", "Total", "Today", "PRIME", "PRIME-REVIEW", "GROWING", "HAS-AGENCY", "POLISHED", "Share", "Progress"]]
    for k, (name, v) in enumerate(tabs):
        r = first + k
        sg = v.get("segs", {})
        rows.append([name, v["total"], v["today"], v["prime"], sg.get("PRIME-REVIEW", 0), sg.get("GROWING", 0),
                     sg.get("HAS-AGENCY", 0), sg.get("POLISHED-NO-SIGNAL", 0),
                     f'=IFERROR(TEXT(B{r}/SUM(B${first}:B${last}),"0%"),"")', _bar("B", r, first, last)])
    return rows


def write_block(sh, title, rows, prev_n, index=None):
    """Overwrite a fixed block from A1 (values in place; leftover rows of a longer previous block are blanked).
    Returns the new block height (kept in Meta)."""
    try:
        ws = find_ws(sh, title)
    except gspread.WorksheetNotFound:
        ws = retry(lambda: sh.add_worksheet(title, rows=max(60, len(rows) + 20), cols=12))
        if _CACHE["ws"] and _CACHE["sh"] is sh:
            _CACHE["ws"][title] = ws
    width = max(10, max(len(r) for r in rows))
    n = max(len(rows), int(num(prev_n, 0)))
    body = [pad(r, width) for r in rows] + [[""] * width for _ in range(n - len(rows))]
    if ws.row_count < n + 2 or ws.col_count < width:
        retry(lambda: ws.resize(rows=max(ws.row_count, n + 20), cols=max(ws.col_count, width)))
    retry(lambda: ws.update(range_name="A1", values=[[cell(x) for x in r] for r in body], value_input_option="USER_ENTERED"))
    if index is not None:
        try:
            wss = retry(lambda: sh.worksheets())
            if [w.title for w in wss].index(ws.title) != index:
                retry(lambda: sh.batch_update({"requests": [{"updateSheetProperties": {
                    "properties": {"sheetId": ws.id, "index": index}, "fields": "index"}}]}))
        except Exception:
            pass
    return len(rows)


def stats_title(target):
    return "Stats"


def write_stats(sh, target, book, meta, S, last_run, health):
    label = "Australia"
    summ = summary_of(book, S)
    rows = _stats_rows(label, summ, last_run, health, K.report_text(), S)
    has_master = any(w.title == "Master Dashboard" for w in retry(lambda: sh.worksheets()))
    meta["stats_rows"] = write_block(sh, stats_title(target), rows, meta.get("stats_rows"),
                                     index=1)
    meta["summary"] = json.dumps(summ, ensure_ascii=False, separators=(",", ":"))
    return summ


def status_rule(sid, c):
    return {"setDataValidation": {"range": {"sheetId": sid, "startRowIndex": 1, "startColumnIndex": c, "endColumnIndex": c + 1},
                                  "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": s} for s in D.STATUS_LIST]},
                                           "showCustomUi": True, "strict": False}}}


def tab_rank(title):
    """Logical niche-tab order: by Group (D.GROUP_ORDER: medical first ... YouTube), then name."""
    groups = {tab_name(n[0]): n[1] for n in D.NICHES}
    g = groups.get(title, "")
    return (D.GROUP_ORDER.index(g) if g in D.GROUP_ORDER else len(D.GROUP_ORDER), title.lower())


def order_tabs(sh, tabs):
    """Tab ORDER only (rows are never touched): Master Dashboard / Stats first, then niche tabs in logical order.
    Moves only tabs that are out of place (usually 0-1 requests)."""
    wss = retry(lambda: sh.worksheets())
    by_title = {w.title: w for w in wss}
    lead = sorted([t for t in tabs if t in by_title], key=tab_rank)
    head = [t for t in ("Master Dashboard", "US Stats", "Stats") if t in by_title] + lead
    order = head + [w.title for w in wss if w.title not in head]
    reqs = [{"updateSheetProperties": {"properties": {"sheetId": by_title[t].id, "index": i}, "fields": "index"}}
            for i, t in enumerate(order) if wss.index(by_title[t]) != i]
    if reqs:
        retry(lambda: sh.batch_update({"requests": reqs}))


# ------------------------------------------------------------------ scraper imports (folder + 'Import' tab)
def import_places(sh, S, niches, repo_root):
    """[(niche, loc, place, found_on)] from imports/*.csv|json and the 'Import' tab. Never raises."""
    sources = []
    folder = S.get("IMPORT_FOLDER", "imports") or ""
    folder = folder if os.path.isabs(folder) else os.path.join(repo_root, folder)
    for f in IM.folder_files(folder):
        recs = IM.read_file(f)
        if recs:
            sources.append((os.path.basename(f), recs))
    try:
        ws, _ = get_tab(sh, "Import", D.IMPORT_H)
        recs = IM.tab_records(get_all(ws))
        if recs:
            sources.append(("Import tab", recs))
    except Exception:
        O.STATS["import-tab"] += 1
    return IM.collect(sources, niches, S.get("IMPORT_DEFAULT_NICHE", ""))


def google_places_from(items, loc, label):
    """Serper / Apify Google Maps items -> pipeline places (through the importer's alias table, so ids stay Google
    place ids and dedupe with Places API / gosom imports)."""
    out = []
    for it in items:
        rec = {"name": it.get("title"), "address": it.get("address"), "website": it.get("website"),
               "phone": it.get("phoneNumber") or it.get("phone"), "category": it.get("category") or it.get("categoryName"),
               "rating": it.get("rating") if it.get("rating") is not None else it.get("totalScore"),
               "reviews": it.get("ratingCount") if it.get("ratingCount") is not None else it.get("reviewsCount"),
               "place_id": it.get("placeId"), "cid": it.get("cid"), "link": it.get("url"),
               "city": it.get("city") or "", "state": it.get("state") or "",
               "postcode": it.get("postalCode"), "country": it.get("countryCode") or it.get("country") or "",
               "status": "closed" if it.get("permanentlyClosed") or it.get("temporarilyClosed") else "",
               "price": it.get("price") or it.get("priceLevel")}
        place, _ = IM.row_to_place(rec, label)
        if place:
            place["_source"] = label
            if not place.get("googleMapsUri") and it.get("cid"):
                place["googleMapsUri"] = f"https://maps.google.com/?cid={it['cid']}"
            out.append(place)
    return out


def per_country_mode():
    """Compatibility helper: this project has one spreadsheet."""
    return False


def sheet_id_for(target=None):
    if target and target.upper() != "AU":
        raise ValueError("Only Australia is supported")
    return K.sheet_id(env("SHEET_ID"))


def open_sheet(local=False, target=None):
    """Google Sheet from GOOGLE_SA + the target's sheet id, or the offline LocalSheet (folder of CSVs) for --local /
    tests. Returns None when the target has no sheet id (the caller logs it and skips; never a crash)."""
    if local:
        import localsheet
        base = env("LOCAL_SHEET_DIR") or os.path.join(REPO_ROOT, "local_sheet")
        return localsheet.LocalSheet(base)
    raw = env("GOOGLE_SA")
    sheet_id = sheet_id_for(target)
    if not raw or not sheet_id:
        sys.exit("MISSING CORE SETTING: GOOGLE_SA and SHEET_ID are required (or run with --local). "
                 "See SHEETS_SETUP.md.")
    try:
        info = json.loads(raw) if raw.lstrip().startswith("{") else json.load(open(raw, encoding="utf-8"))
    except (ValueError, OSError):
        sys.exit("GOOGLE_SA is not valid service-account JSON (paste the whole .json file content). See SECRETS_GUIDE.md.")
    try:
        return retry(lambda: gspread.service_account_from_dict(info).open_by_key(sheet_id))
    except (gspread.SpreadsheetNotFound, PermissionError) as e:
        raise RuntimeError(f"sheet {target or ''} (…{sheet_id[-4:]}) not found or not shared with the service account "
                           f"as Editor ({type(e).__name__}). See SHEETS_SETUP.md.") from None
    except gspread.exceptions.APIError as e:
        if getattr(getattr(e, "response", None), "status_code", 0) in (403, 404):
            raise RuntimeError(f"sheet {target or ''} (…{sheet_id[-4:]}): no access. Share it with the service-account "
                               "email as Editor (SHEETS_SETUP.md).") from None
        raise


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def region_ok(cc, state, regions):
    """REGIONS tokens: 'US' (whole country) or 'AU-NSW' (one Australian state/territory)."""
    cc = (cc or "").upper()
    st = E.au_state(state) if cc == "AU" else (state or "").upper()
    return any(r == cc or (r.startswith(cc + "-") and r.split("-", 1)[1] == st) for r in regions)


def env_list(k):
    return [x.strip().lower() for x in re.split(r"[\s,;]+", env(k)) if x.strip()]


# ------------------------------------------------------------------ main
def main(dry=False, local=False, setup_only=False, finish_only=False, no_finish=False, target=None):
    t0 = time.time()
    target = (target or "").upper() or None
    if target and target != "AU":
        raise ValueError("Only Australia is supported")
    target = None
    O.STATS.clear()
    O.OK.clear()
    O._CAPS.clear()                                            # per-run budgets for the optional paid helpers
    K.register(quiet=bool(K.REPORT))                           # every sheet run: values trimmed + shape-checked
    sh = open_sheet(local, target)
    OPS.setup(sh)
    prefetch_book(sh)                                          # 2 reads instead of ~40 (shared read quota)
    opt_ws, opt_hdr, opt_rows, took = safe(lambda: load_options(sh), (None, None, [], []), "options-tab")  # BEFORE keys are read
    if took:
        K.register(quiet=True)                                 # keys typed in the Options tab get the same shape check
    S, niches, locs, skipped, cfg_notes = load_config(sh, target=target)
    O.FLAGS.update(disify=S.yes("USE_DISIFY", False), reacher=S.yes("USE_REACHER", True))
    run_ws, _ = get_tab(sh, "Run Log", D.RUN_H)
    meta_ws, meta = read_meta(sh)
    if setup_only:
        for t, h in (("Seen", D.SEEN_H), ("Searches", D.SEARCH_H), ("Blacklist", D.BLACKLIST_H), ("Leads Input", D.INPUT_H)):
            get_tab(sh, t, h)
        if not dry:
            book = safe(lambda: scan_lead_tabs(sh), {}, "scan-tabs")
            safe(lambda: write_stats(sh, target, book, meta, S, meta.get("last_run", ""), meta.get("health", "")), None, "stats")
            meta["registration"] = K.report_text()
            safe(lambda: write_meta(sh, meta_ws, meta), None, "meta")
        for niche in niches:
            get_lead_tab(sh, tab_name(niche["niche"]), {})
        OPS.sync(sh)
        print("Setup done: all control tabs exist.")
        if local:
            sh.save()
        return
    if finish_only:                                            # v5: no sorting / rebuilding. Stats only (values in place).
        book = safe(lambda: scan_lead_tabs(sh), {}, "scan-tabs")
        if not dry:
            safe(lambda: write_stats(sh, target, book, meta, S, meta.get("last_run", ""), meta.get("health", "")), None, "stats")
            safe(lambda: order_tabs(sh, book), None, "tab-order")
            safe(lambda: write_meta(sh, meta_ws, meta), None, "meta")
        OPS.sync(sh)
        print("Finish done: dashboard refreshed; outreach fields preserved.")
        if local:
            sh.save()
        return
    # lock id = the workflow run: jobs of ONE run never block each other (even after a job was killed mid-run);
    # another run / a local run on the same sheet is kept out until the lock is released or goes stale
    run_id = ("gha-" + env("GITHUB_RUN_ID")) if env("GITHUB_RUN_ID") else f"local-{os.getpid()}"
    if not dry and not local and lock_held(meta, run_id, S):
        print(f"SKIP {target or 'sheet'}: another job is writing this sheet right now (Meta lock {meta.get('lock')}). "
              "Nothing written; the next run continues.")
        return
    if not dry:
        meta["lock"] = run_id + "|" + datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_meta(sh, meta_ws, meta)
    regions = [r.upper() for r in env_list("REGIONS")]
    if target and target != "YOUTUBE" and not regions:
        regions = [target]                                     # a country sheet only searches its own country
    default_src = {"youtube", "imports"} if target == "YOUTUBE" else {"input", "imports", "maps"} if target else \
        {"input", "imports", "maps", "youtube"}
    sources = set(env_list("SOURCES")) or default_src
    if target == "YOUTUBE":
        sources -= {"maps"}
    elif target:
        sources -= {"youtube"}
    if regions:
        locs = [l for l in locs if region_ok(l["cc"], l["state"], regions) or l["country"].upper() in regions]
    problems, totals, pl, yt, searches_done = list(cfg_notes), Counter(), None, None, 0
    failed = False
    book_ref = []
    if took:
        problems.append("Keys taken from Options tab: " + ", ".join(took))
    if skipped:
        problems.append("Skipped (budget below MIN_BUDGET_AUD): " + ", ".join(skipped))
    job = (f"sheet[{target}] " if target else "") + ((f"job[{','.join(sorted(sources))}" + (f" {','.join(regions)}" if regions else "")
                                                       + "] ") if env("SOURCES") or regions else "")
    try:
        ai = None                                                  # AI is optional: no key / dead key => fact-only insight
        if env("AI_API_KEYS") and S.yes("USE_AI_INSIGHT", True):
            try:
                ai = AI(env("AI_API_KEYS"))
            except Exception as e:
                problems.append(f"AI unavailable, fact-only Lead Insight used: {type(e).__name__}")
        if "youtube" in sources and any(n["group"].lower() == "youtube" for n in niches):
            try:
                yt = YTM.Source(S)
            except YTM.YTUnavailable as e:
                problems.append(str(e))
        seen_ws, search_ws, block, domains, emails, bl, last, places_month, snap = load_seen(sh, S)
        book = scan_lead_tabs(sh)                              # last row + Place Ids of every lead tab (2-4 reads)
        for inf in list(book.values()):
            block |= inf["pids"]                               # dedupe by Place Id across the sheet, even if Seen was lost
        book["_pids"] = set().union(*[inf["pids"] for inf in book.values()]) if book else set()
        book_ref.append(book)
        used_cells = safe(lambda: cells_used(sh), 0, "cells")
        if used_cells > 0.8 * CELL_LIMIT:
            problems.append(f"WARNING sheet uses {used_cells:,} of Google's 10,000,000 cells: trim old 'Rejected' Seen rows")
        state = {"block": block, "domains": domains, "emails": emails, "blacklist": bl, "problems": problems,
                 "hunter": 0, "hfind": 0, "apollo": 0, "ig": 0, "apify": 0, "jina": 0, "verify": 0, "ai_email": 0, "ai_fail": 0,
                 "ai_off": False, "ai_quota": False, "names_run": Counter(), "hosts_run": Counter(), "site_cache": {},
                 "deadline": t0 + S.num("MAX_RUNTIME_MIN", 42) * 60, "snap": snap, "snap_rows": [], "full": False}
        total_cap = int(S.num("NEW_LEADS_PER_RUN", 120))
        tabs, added, tab_cache = {}, Counter(), {}

        def commit(nic, leads, seen_rows, key=None, pages=1, places_n=0, source=""):
            """Order matters for partial-run recovery: leads first, then Seen, then Searches. A crash in between is
            healed next run (Place Id dedupe against the lead tabs; an unfinished search is simply searched again)."""
            if dry:
                print(json.dumps({"event": "dry_run_batch", "candidates": len(leads)}))
                return
            if leads and not state["full"]:
                title = tab_name(nic["niche"])
                try:
                    written = write_leads(sh, title, leads, book, S, tab_cache)
                    totals["added"] -= len(leads) - written
                    persisted = book.get('_last_written', [])
                    totals['qualified'] -= sum(l.get('ICP Tier', '').startswith(('A', 'B')) for l in leads) - sum(l.get('ICP Tier', '').startswith(('A', 'B')) for l in persisted)
                    OPS.sync(sh, leads=persisted)
                except SheetFull as e:
                    totals['added'] -= len(leads)
                    totals['qualified'] -= sum(l.get('ICP Tier', '').startswith(('A', 'B')) for l in leads)
                    state["full"] = True
                    problems.append("ERROR " + str(e))
                    seen_rows = [r for r in seen_rows if r[5] != "Added"]    # not written -> may be added later
                if title in tab_cache:
                    tabs[tab_cache[title][0].title] = tab_cache[title]
            elif leads:
                seen_rows = [r for r in seen_rows if r[5] != "Added"]
            if seen_rows:
                append(seen_ws, rows_for(_HDRS.get("Seen", D.SEEN_H), D.SEEN_H, seen_rows))
            if key:
                append(search_ws, rows_for(_HDRS.get("Searches", D.SEARCH_H), D.SEARCH_H,
                                           [[key, today(S), pages, places_n, len(leads), source]]))
            if state["snap_rows"]:
                safe(lambda: append(get_tab(sh, "Snapshots", D.SNAP_H)[0],
                                    rows_for(_HDRS.get("Snapshots", D.SNAP_H), D.SNAP_H, state["snap_rows"])), None, "snapshots")
                state["snap_rows"] = []

        # A) your own Leads Input tab (free, always first)
        if "input" in sources:
            for nic, loc, place in safe(lambda: manual_places(sh, S, niches), [], "manual-tab"):
                if sum(added.values()) >= total_cap or time.time() > state["deadline"]:
                    break
                leads, seen_rows, st = process_places([place], nic, loc, S, ai, state, 1, "Leads Input tab")
                totals.update(st)
                added[nic["niche"]] += st["added"]
                if leads or seen_rows:
                    commit(nic, leads, seen_rows)

        # B) scraper imports (gosom google-maps-scraper, yt-dlp CSV, Apify...): own cap, same pipeline
        if "imports" in sources:
            rows = [x for x in safe(lambda: import_places(sh, S, niches, REPO_ROOT), [], "imports")
                    if x[2]["id"] not in state["block"]]
            if regions:
                rows = [x for x in rows if not x[1]["cc"] or region_ok(x[1]["cc"], x[1]["state"], regions)]
            if D.MARKET == "AU":                                 # Australia edition: imported rows from elsewhere are skipped
                rows = [x for x in rows if x[1]["cc"] in ("", "AU")]
            if target == "YOUTUBE":                              # each import row goes to its own sheet
                rows = [x for x in rows if x[2].get("_source") == "YouTube"]
            elif target:
                dflt = (S.get("IMPORT_DEFAULT_COUNTRY") or "").upper()
                rows = [x for x in rows if x[2].get("_source") != "YouTube" and (x[1]["cc"] or dflt) == target]
            rows = rows[:int(S.num("IMPORT_MAX_ROWS_PER_RUN", 4000))]
            groups = {}
            for nic, loc, place, found in rows:
                k = (nic["niche"], loc["cc"], loc["city"].lower(), found)
                groups.setdefault(k, (nic, loc, found, []))[3].append(place)
            imp_cap, imp_added = int(S.num("IMPORT_LEADS_PER_RUN", 300)), 0
            for nic, loc, found, places in groups.values():
                if imp_added >= imp_cap or time.time() > state["deadline"]:
                    break
                leads, seen_rows, st = process_places(places, nic, loc, S, ai, state, imp_cap - imp_added, found)
                totals.update(st)
                imp_added += st["added"]
                print(f"{nic['niche']} | {found} {loc['city'] or loc['cc']}: {dict(st)}")
                commit(nic, leads, seen_rows)
            if rows:
                problems.append(f"Imports: {len(rows)} new rows read, {imp_added} leads added")

        # C) discovery: free OpenStreetMap by default; Google Places only if you added a key; YouTube niches
        src = S.get("SOURCE", "auto").lower()
        licensed = S.yes("ALLOW_LICENSED_DISCOVERY", False)
        if not licensed and src in ("places", "serper", "apify"):
            raise ValueError("Enable ALLOW_LICENSED_DISCOVERY after reviewing provider data rights")
        use_places = "maps" in sources and (src == "places" or (src == "auto" and licensed and bool(env("PLACES_API_KEYS"))))
        use_osm = "maps" in sources and (src == "osm" or (src == "auto" and (not licensed or not env("PLACES_API_KEYS"))))
        if use_places and not env("PLACES_API_KEYS"):
            problems.append("SOURCE=places but no PLACES_API_KEYS: free OpenStreetMap used instead.")
            use_places, use_osm = False, True
        places_cap = int(S.num("PLACES_MAX_REQUESTS_PER_MONTH", 950))
        if use_places and places_month >= places_cap:
            problems.append(f"Google Places: {places_month} requests this month >= PLACES_MAX_REQUESTS_PER_MONTH "
                            f"({places_cap}); free OpenStreetMap used instead (no billing).")
            use_places, use_osm = False, True
        if use_places:
            pl = Places(re.split(r"[\s,;]+", env("PLACES_API_KEYS")))
        # optional Google-data discovery without a Google key: Serper (SOURCE=serper) / Apify Google Maps (USE_APIFY_MAPS)
        use_serper = "maps" in sources and not use_places and src == "serper" and bool(O.key("SERPER_API_KEY"))
        use_apify = ("maps" in sources and not use_places and licensed and (src == "apify" or S.yes("USE_APIFY_MAPS", False))
                     and bool(O.key("APIFY_API_TOKEN")))
        if src in ("serper", "apify") and not (use_serper or use_apify):
            problems.append(f"SOURCE={src} but its key is missing: free OpenStreetMap used.")
            use_osm = "maps" in sources
        if use_serper or use_apify:
            use_osm = "maps" in sources                          # free map stays the fallback when their budget ends
        serper_n, apify_n = 0, 0
        budget = int(S.num("MAX_SEARCH_REQUESTS_PER_RUN", 40))
        osm_fails, osm_stop = 0, int(S.num("OSM_FAIL_STOP", 3))
        if niches and (locs or yt):
            queues = {}
            for n in niches:
                if n["group"].lower() == "youtube":
                    pseudo = [{"area": t, "city": "", "state": "", "country": "", "cc": "", "priority": 1, "lat": 0, "lon": 0,
                               "radius": 0, "_lat_col": None} for t in (n["terms"] or [n["niche"]])] if yt else []
                    combos = [(last.get(f"{n['niche']}|{l['area']}", ""), 1, random.random(), l) for l in pseudo]
                else:
                    combos = [(last.get(f"{n['niche']}|{l['area']}", ""), l["priority"], random.random(), l) for l in locs] \
                        if (use_places or use_osm) else []
                combos.sort(key=lambda x: x[:3])
                queues[n["niche"]] = [x[3] for x in combos]
            order = [n for n in niches if queues.get(n["niche"])]
            random.shuffle(order)
            per_cap = math.ceil(total_cap / max(1, min(len(order), budget)))
            while searches_done < budget and time.time() < state["deadline"] and any(queues.values()):
                progressed = False
                for nic in order:
                    if searches_done >= budget or time.time() > state["deadline"] or sum(added.values()) >= total_cap:
                        break
                    if not queues[nic["niche"]] or added[nic["niche"]] >= per_cap:
                        continue
                    loc = queues[nic["niche"]].pop(0)
                    progressed = True
                    cap_left = min(per_cap - added[nic["niche"]], total_cap - sum(added.values()))
                    source = ""
                    try:
                        if nic["group"].lower() == "youtube":
                            if yt is None:
                                queues[nic["niche"]] = []
                                continue
                            places, pages = yt.search(loc["area"], S, state["block"]), 1
                            found, source = f"YouTube search: {loc['area']}", "YouTube"
                            searches_done += 1
                        elif use_places:
                            term = random.choice(nic["terms"] or [nic["niche"]])
                            q, places, token, pages = f"{term} in {loc['area']}", [], None, 0
                            for _ in range(int(max(1, min(3, S.num("MAX_PAGES_PER_SEARCH", 2))))):
                                if pl.requests >= budget or places_month + pl.requests >= places_cap:
                                    break
                                data = pl.search(q, token, loc["cc"])
                                pages += 1
                                places += data.get("places", [])
                                token = data.get("nextPageToken")
                                if not token:
                                    break
                            searches_done += max(1, pages)
                            found, source = f"Google Maps: {q}", "Google Places"
                            if places_month + pl.requests >= places_cap:
                                problems.append(f"Google Places monthly cap ({places_cap}) reached: switched to free OpenStreetMap.")
                                use_places, use_osm = False, True
                        elif (use_serper and serper_n < S.num("SERPER_MAPS_SEARCHES_PER_RUN", 30)) or \
                                (use_apify and apify_n < S.num("APIFY_MAPS_SEARCHES_PER_RUN", 2)):
                            term = random.choice(nic["terms"] or [nic["niche"]])
                            q = f"{term} in {loc['area']}"
                            if use_serper and serper_n < S.num("SERPER_MAPS_SEARCHES_PER_RUN", 30):
                                serper_n += 1
                                items, source = O.serper_places(q, loc["cc"] or "AU"), "Google (Serper)"
                            else:
                                apify_n += 1
                                items, source = O.apify_maps(q, int(S.num("APIFY_MAPS_PER_SEARCH", 40)), loc["cc"] or "AU"), "Google (Apify)"
                            places = google_places_from(items, loc, source)
                            pages, found = 1, f"{source}: {q}"
                            searches_done += 1
                        else:
                            if not use_osm:
                                queues[nic["niche"]] = []
                                continue
                            if not nic["osm"]:
                                problems.append(f"{nic['niche']}: no Free Map Selectors in Niches tab (skipped)")
                                queues[nic["niche"]] = []
                                continue
                            if not (loc["lat"] and loc["lon"]):
                                g = OSM.geocode(f"{loc['area']}, {loc['country']}" if loc["country"] else loc["area"], loc["cc"])
                                if not g:
                                    problems.append(f"{loc['area']}: add Latitude/Longitude in Locations (geocode failed)")
                                    continue
                                loc["lat"], loc["lon"] = g
                                if loc["_lat_col"] is not None and not dry:     # save so we never geocode it again
                                    safe(lambda: loc["_ws"].update(range_name=rowcol_to_a1(loc["_row"], loc["_lat_col"] + 1),
                                                                   values=[[g[0], g[1], loc["radius"]]]), None, "geocode-save")
                            places = OSM.search(nic["osm"], loc, int(S.num("OSM_LIMIT", 150)))
                            osm_fails = 0
                            pages, found, source = 1, f"OpenStreetMap: {nic['niche']} near {loc['area']}", "OpenStreetMap"
                            searches_done += 1
                            time.sleep(S.num("OSM_DELAY_SEC", 6))
                        leads, seen_rows, st = process_places(places, nic, loc, S, ai, state, cap_left, found)
                    except AllKeysExhausted:
                        problems.append("Google Places keys exhausted/billing off: free OpenStreetMap used for the rest of this run.")
                        use_places, use_osm = False, "maps" in sources
                        continue
                    except YTM.YTUnavailable as e:
                        problems.append(str(e)[:150])
                        yt = None
                        continue
                    except OSM.OSMUnavailable as e:
                        searches_done += 1
                        osm_fails += 1
                        if osm_fails >= osm_stop:
                            problems.append(f"{type(e).__name__} Stopped map searches after {osm_fails} failures in a row.")
                            use_osm = False
                        continue
                    except Exception as e:
                        problems.append(f"{nic['niche']} / {loc['area']}: {type(e).__name__} {type(e).__name__}")
                        searches_done += 1
                        continue
                    totals.update(st)
                    added[nic["niche"]] += st["added"]
                    print(f"{nic['niche']} | {loc['area']}: {dict(st)}")
                    commit(nic, leads, seen_rows, f"{nic['niche']}|{loc['area']}", pages, st["places"], source)
                if not progressed:
                    break
        if not dry and tabs and not no_finish:
            safe(lambda: order_tabs(sh, tabs), None, "tab-order")    # tab order only; moves nothing when in place
        if pl:
            problems += pl.notes
        if yt:
            problems += yt.all_notes()[:3]
        if ai:
            problems += ai.notes
        if state.get("ai_quota"):
            problems.append("AI quota hit (429): AI stopped for this run; remaining leads got a fact-only Lead Insight.")
        elif state["ai_fail"]:
            problems.append(f"AI failed {state['ai_fail']}x: fact-only Lead Insight used where it failed.")
    except SystemExit:
        failed = True
        raise
    except Exception as e:
        failed = True
        problems.append(f"CRASH: {type(e).__name__}")
        raise
    finally:
        if not dry and opt_ws is not None:
            safe(lambda: write_option_status(opt_ws, opt_hdr, opt_rows), None, "option-status")
        rej = "; ".join(f"{k[5:]}={v}" for k, v in totals.most_common() if k.startswith("rej: "))[:380]
        skipped_en = ", ".join(f"{k}={v}" for k, v in O.STATS.items() if v)
        okc = ", ".join(f"{k}={v}" for k, v in O.OK.items() if v)
        summ = (job + f"added={totals['added']}, businesses_seen={totals['places']}, searches={searches_done}, "
                f"minutes={(time.time() - t0) / 60:.1f}" + (f" | rejected: {rej}" if rej else "")
                + (f" | enrich ok: {okc}" if okc else "") + (f" | enrich skipped: {skipped_en}" if skipped_en else ""))
        if not dry:
            stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            cleanup_errors = []
            run_status = "failed" if failed else "success" if totals["qualified"] else "no_qualifying_leads"
            if any("Stopped map searches" in x for x in problems) and not totals["places"]:
                run_status = "failed"
            run = {"run_id": run_id, "status": run_status,
                   "started_at": datetime.fromtimestamp(t0, timezone.utc).isoformat(), "finished_at": stamp,
                   "added": totals["qualified"], "evaluated": totals["places"],
                   "rejected": sum(v for k,v in totals.items() if k.startswith("rej: ")),
                   "error": "; ".join(x.split(":")[0] for x in problems)[:500],
                   "api_health": [{"provider": k, "status": "degraded", "checked_at": stamp,
                                   "error": f"{v} failures/skips"} for k,v in O.STATS.items() if v]}
            for label, fn in (("run-log", lambda: append(run_ws, [[stamp, summ, searches_done, run["error"]]])),
                              ("dashboard", lambda: OPS.sync(sh, run=run))):
                try:
                    fn()
                except Exception as exc:
                    cleanup_errors.append(label + ": " + type(exc).__name__)
            meta.update({"last_run": stamp, "health": run_status, "registration": K.report_text(), "lock": "", "version": "v6"})
            try:
                write_meta(sh, meta_ws, meta)
            except Exception as exc:
                cleanup_errors.append("unlock: " + type(exc).__name__)
            if local and hasattr(sh, "save"):
                sh.save()
            if cleanup_errors:
                print(json.dumps({"event": "finalization_failed", "errors": cleanup_errors}))
                if not failed:
                    raise RuntimeError("Required run finalization failed: " + "; ".join(cleanup_errors))
            if run_status == "failed" and not failed:
                raise RuntimeError("Discovery providers failed before any businesses were evaluated")
        print("Failed:" if failed else "Completed:", summ)
        if problems:
            print("Problems:", " | ".join(problems))


def cli(a):
    """Run the single Australian pipeline. Dry runs use a disposable local spreadsheet."""
    import tempfile
    unknown = set(a) - {"--setup", "--local", "--dry", "--finish", "--no-finish", "--all"}
    if unknown:
        raise SystemExit("Unknown arguments. Use --setup, --local, --dry, --finish or --no-finish.")
    K.register()
    kw = dict(dry="--dry" in a, local="--local" in a or "--dry" in a,
              setup_only="--setup" in a, finish_only="--finish" in a, no_finish="--no-finish" in a)
    if kw["dry"]:
        old = os.environ.get("LOCAL_SHEET_DIR")
        with tempfile.TemporaryDirectory(prefix="au-lead-preview-") as folder:
            os.environ["LOCAL_SHEET_DIR"] = folder
            try:
                main(**kw)
            finally:
                if old is None:
                    os.environ.pop("LOCAL_SHEET_DIR", None)
                else:
                    os.environ["LOCAL_SHEET_DIR"] = old
    else:
        main(**kw)


if __name__ == "__main__":
    cli(sys.argv[1:])
