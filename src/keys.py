"""v5 secrets registry: ONE GitHub secret per key, clearly named (GEMINI_API_KEY_1, GROQ_API_KEY, SHEET_ID_US, ...).

At startup `register()`:
  * trims whitespace and surrounding quotes from every value (a pasted `"AIza..."` or a trailing newline is the
    classic reason a key went "unregistered"), and a full Google Sheet URL is cut down to its id;
  * checks the SHAPE of each value (prefix / length / characters) - never calls the service, never prints the value;
  * logs "registered: GEMINI_API_KEY_2 (...abcd)" or "ignored: GROQ_API_KEY (bad format: ...)", masked to 4 chars;
  * KEY_CHECK=warn (GitHub Variable) keeps a value with an unexpected shape (a provider changed its key format) and
    only warns; default = strict (ignored);
  * fills the OLD combined names (AI_API_KEYS, PLACES_API_KEYS, YOUTUBE_API_KEYS, ABN_LOOKUP_GUID) from the new
    single-key secrets, so the rest of the code keeps working. The old combined secrets still work as a fallback.
Never raises."""
import json
import os
import re

# name pattern -> (group, shape regex or None for 'any non-blank', format hint, legacy combined name it feeds)
_AIZA = r"AIza[0-9A-Za-z_\-]{30,45}"
_SHEET = r"[A-Za-z0-9_\-]{25,80}"
SPECS = [
    # AI (Lead Insight; optional)
    (r"GEMINI_API_KEY(_\d+)?", "AI", _AIZA, "AIza + 35 characters", "AI_API_KEYS"),
    (r"GROQ_API_KEY(_\d+)?", "AI", r"gsk_[A-Za-z0-9]{20,80}", "gsk_ + 52 characters", "AI_API_KEYS"),
    (r"OPENROUTER_API_KEY(_\d+)?", "AI", r"sk-or-[A-Za-z0-9\-]{20,100}", "sk-or-v1-...", "AI_API_KEYS"),
    (r"CEREBRAS_API_KEY(_\d+)?", "AI", r"csk-[A-Za-z0-9]{20,100}", "csk-...", "AI_API_KEYS"),
    (r"MISTRAL_API_KEY(_\d+)?", "AI", r"[A-Za-z0-9]{24,64}", "32 letters/digits", "AI_API_KEYS"),
    (r"OPENAI_API_KEY", "AI", r"sk-(?!ant-|or-)[A-Za-z0-9_\-]{20,200}", "sk-...", "AI_API_KEYS"),
    (r"ANTHROPIC_API_KEY", "AI", r"sk-ant-[A-Za-z0-9_\-]{20,200}", "sk-ant-...", "AI_API_KEYS"),
    # Google
    (r"GOOGLE_PLACES_API_KEY(_\d+)?", "Maps", _AIZA, "AIza + 35 characters", "PLACES_API_KEYS"),
    (r"YOUTUBE_API_KEY(_\d+)?", "YouTube", _AIZA, "AIza + 35 characters", "YOUTUBE_API_KEYS"),
    # enrichment / search (optional)
    (r"HUNTER_API_KEY", "Enrich", r"[A-Za-z0-9]{20,80}", "40 hex characters", None),
    (r"ZEROBOUNCE_API_KEY", "Enrich", r"[A-Za-z0-9\-]{20,80}", "32 hex characters", None),
    (r"SERPER_API_KEY", "Search", r"[A-Za-z0-9]{20,80}", "40 hex characters", None),
    (r"BRAVE_API_KEY", "Search", r"[A-Za-z0-9_\-]{16,80}", "BSA... (about 31 characters)", None),
    (r"ZENROWS_API_KEY", "Enrich", r"[A-Za-z0-9]{20,80}", "40 hex characters", None),
    (r"JINA_API_KEY", "Enrich", r"jina_[A-Za-z0-9]{20,100}", "jina_...", None),
    (r"ABN_GUID", "Enrich", r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
     "GUID like 1a2b3c4d-1111-2222-3333-444455556666", "ABN_LOOKUP_GUID"),
    (r"ABN_LOOKUP_GUID", "Enrich", r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "GUID", None),
    (r"APIFY_API_TOKEN", "Enrich", r"apify_api_[A-Za-z0-9]{20,60}", "apify_api_ + 36 characters", None),
    (r"APOLLO_API_KEY", "Enrich", r"[A-Za-z0-9_\-]{16,80}", "22 characters", None),
    (r"IG_ACCESS_TOKEN", "Enrich", r"(EAA|IG)[A-Za-z0-9_\-]{20,400}", "EAA... long token", None),
    (r"IG_USER_ID", "Enrich", r"\d{5,25}", "digits", None),
    (r"NZBN_API_KEY", "Enrich", r"[A-Za-z0-9]{16,80}", "32 letters/digits (subscription key)", None),
    (r"SNOV_CLIENT_ID|SNOV_CLIENT_SECRET", "Enrich", r"[A-Za-z0-9]{16,80}", "32 letters/digits", None),
    (r"PROSPEO_API_KEY", "Enrich", r"[A-Za-z0-9_\-]{16,80}", "32 letters/digits", None),
    (r"REACHER_URL", "Enrich", r"https?://[A-Za-z0-9.\-]+(:\d+)?(/[^\s]*)?", "https://reacher.yourvps.com", None),
    (r"REACHER_SECRET", "Enrich", r"\S{4,200}", "any text you set on your Reacher server", None),
    # sheets (one per country)
    (r"SHEET_ID(_[A-Z]+(_ONLY)?)?", "Sheet", _SHEET, "the id between /d/ and /edit in the sheet URL", None),
    (r"GOOGLE_SA|GOOGLE_SERVICE_ACCOUNT_JSON", "Core", None, "the whole service-account JSON file content", None),
]
# old combined secrets: still read (fallback), each part validated like a single key
LEGACY = {"AI_API_KEYS": "AI", "PLACES_API_KEYS": "Maps", "YOUTUBE_API_KEYS": "YouTube"}
REPORT = []          # [(status, name, detail)] for the log + Stats tab
_AI_SHAPES = [s for s in SPECS if s[1] == "AI"]


def clean(v):
    """Trim spaces/newlines and ONE pair of surrounding quotes (also smart quotes)."""
    v = (v or "").strip().strip("﻿").strip()
    if len(v) >= 2 and v[0] in "\"'“‘`" and v[-1] in "\"'”’`":
        v = v[1:-1].strip()
    return v


def mask(v):
    return "(..." + v[-4:] + ")" if len(v) >= 8 else "(...)"


def sheet_id(v):
    """Bare id, or the id cut from a full https://docs.google.com/spreadsheets/d/<id>/edit URL."""
    v = clean(v)
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_\-]+)", v)
    return m.group(1) if m else v


def spec_for(name):
    return next((s for s in SPECS if re.fullmatch(s[0], name)), None)


def check(name, value):
    """(ok, cleaned value, reason). Shape check only."""
    s = spec_for(name)
    v = sheet_id(value) if name.startswith("SHEET_ID") else clean(value)
    if not v:
        return False, "", "empty"
    if re.search(r"\s", v) and not (s and s[2] is None):
        return False, v, "contains spaces or line breaks (paste ONE key per secret)"
    if s is None:
        return True, v, ""
    if s[2] is None:                                        # service-account JSON: shape only, nothing printed
        try:
            info = json.loads(v) if v.lstrip().startswith("{") else None
        except ValueError:
            return False, v, "not valid JSON (paste the whole .json file content)"
        if info is None and os.path.isfile(v):
            return True, v, ""
        if not (isinstance(info, dict) and info.get("client_email") and info.get("private_key")):
            return False, v, "JSON has no client_email/private_key"
        return True, v, ""
    if not re.fullmatch(s[2], v):
        return False, v, f"bad format (expected {s[3]})"
    return True, v, ""


def _ai_shape_ok(v):
    return any(re.fullmatch(s[2], v) for s in _AI_SHAPES)


def register(environ=None, quiet=False):
    """Validate every known secret in the environment, normalise it in place, build the legacy combined names.
    Returns REPORT (list of (status, name, detail)). Never raises."""
    env = os.environ if environ is None else environ
    REPORT.clear()
    combined = {k: [] for k in LEGACY}
    warn_only = clean(env.get("KEY_CHECK", "")).lower() == "warn"
    try:
        names = sorted(n for n in env if spec_for(n) or n in LEGACY)
        for n in names:
            raw = env.get(n, "")
            if not clean(raw):
                continue
            if n in LEGACY:
                continue
            ok, v, why = check(n, raw)
            s = spec_for(n)
            if ok:
                env[n] = v
                REPORT.append(("registered", n, "" if s[1] == "Core" else mask(v)))
                if s[4] and s[4] in combined:
                    combined[s[4]].append(v)
                elif s[4] and not clean(env.get(s[4], "")):
                    env[s[4]] = v                         # e.g. ABN_GUID -> ABN_LOOKUP_GUID
            elif v and warn_only and why.startswith("bad format"):
                env[n] = v                                # KEY_CHECK=warn: a provider changed its key format
                REPORT.append(("registered", n, mask(v) + " format unchecked"))
                if s[4] and s[4] in combined:
                    combined[s[4]].append(v)
            else:
                env[n] = ""                               # a broken value is never sent to a service
                REPORT.append(("ignored", n, why))
        for legacy, group in LEGACY.items():               # old combined secret: fallback, each part checked
            raw = clean(env.get(legacy, ""))
            parts = [clean(p) for p in re.split(r"[\s,;]+", raw) if clean(p)] if raw else []
            good = []
            for i, p in enumerate(parts, 1):
                ok = warn_only or (_ai_shape_ok(p) if group == "AI" else bool(re.fullmatch(_AIZA, p)))
                if ok:
                    good.append(p)
                REPORT.append(("registered" if ok else "ignored", f"{legacy} #{i}", mask(p) if ok else "bad format"))
            allk = list(dict.fromkeys(combined[legacy] + good))
            env[legacy] = "\n".join(allk)
    except Exception as e:                                 # registry problems must never stop the bot
        REPORT.append(("ignored", "registry", type(e).__name__))
    if not quiet:
        for st, n, d in REPORT:
            print(f"{st}: {n} {d}".rstrip())
    return REPORT


def report_text():
    reg = [f"{n} {d}".strip() for st, n, d in REPORT if st == "registered"]
    bad = [f"{n} ({d})" for st, n, d in REPORT if st != "registered"]
    return ("registered: " + ", ".join(reg) if reg else "registered: none") + (" | ignored: " + "; ".join(bad) if bad else "")
