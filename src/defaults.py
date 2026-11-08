"""First-run seeds for your Google Sheet. After the first run the SHEET is the control panel:
edit the 'Rules', 'Niches' and 'Locations' tabs (no code changes needed). Existing values are never overwritten."""

# Column order = how you work a lead: who -> how to reach them -> where -> why/what to pitch -> evidence -> admin.
LEAD_H = ["Niche", "Group", "Segment", "ICP Score /10", "Tier", "Revenue Band", "Company Name",
          "Decision Maker Name", "First Name", "Decision Maker Role", "Name Source",
          "Personal Email", "Generic Email", "Other Emails", "Email Verified", "Phone",
          "Instagram", "Facebook", "LinkedIn", "TikTok", "YouTube", "Twitter / X", "Other Socials", "Website",
          "Full Address", "Suburb", "City", "State", "Postcode", "Country", "Map Url",
          "Lead Insight", "Suggested Pitch /mo", "Pitch Angle", "Social Gap",
          "Website Summary", "Ad / Spend Signals", "Google Rating", "Review Count", "Employee Count", "Business Type",
          "YT Subscribers", "YT Uploads 30d", "IG Followers", "IG Engagement %", "IG Posts 30d",
          "Email Source", "Email Type", "Notes", "Enrichment", "Data Source", "Found On",
          "Status", "My Notes", "Last Checked", "Lead Added On", "Score", "Place Id",
          # v3: appended at the END so v2 tabs keep the same column order (All Leads merges by position)
          "Pitch Currency", "Country Code", "YT Last Upload (days)",
          # v4 high-quality segmentation (HIGH_QUALITY_LEADS_SPEC.md). Revenue is a BAND from proxies, never a fact.
          "Revenue Est. Basis", "R Score /10", "R Confidence", "S Score /10", "S Basis", "Agency Score /100",
          "Agency Name", "Agency Evidence", "Locations (same website)", "Price Level", "Booking Tool", "YT Median Views",
          "Segment Rank"]
# v3 renamed 'Suggested Pitch AUD/mo' -> 'Suggested Pitch /mo' (the currency is in 'Pitch Currency').
# Existing tabs get the header cell renamed in place, so column positions never move.
RENAMED_COLS = {"Suggested Pitch AUD/mo": "Suggested Pitch /mo"}
# v5: drafting moved to the outreach bot. Old tabs keep these columns (never deleted); new rows leave them blank.
OLD_LEAD_COLS = ("Case Study", "Email Subject", "Email Draft")

# country name (as typed in Locations) -> (ISO code, currency). Aliases are accepted too.
COUNTRIES = {"Australia": ("AU", "AUD")}
COUNTRY_ALIASES = {"au": "Australia"}


def country_info(name):
    """'Australia' / 'UK' / 'us' -> ('United Kingdom', 'GB', 'GBP'). Unknown names keep their text, code ''."""
    n = (name or "").strip()
    full = COUNTRY_ALIASES.get(n.lower(), next((k for k in COUNTRIES if k.lower() == n.lower()), n))
    code, cur = COUNTRIES.get(full, ("", ""))
    if not code and len(n) == 2 and n.isalpha():
        code = n.upper()
    return full, code, cur


COUNTRY_BY_CODE = {v[0]: k for k, v in COUNTRIES.items()}
STATUS_LIST = ["New", "Drafted", "Contacted", "Follow-up 1", "Follow-up 2", "Replied", "Call booked", "Client",
               "Not interested", "Do not contact"]
OPTIONS_H = ["Service", "Key name (GitHub secret)", "Your key (optional - private sheet only)", "Required?",
             "Free tier", "What it adds", "Wired in bot?", "Sign up", "Status (last run)"]
# service, key name, required, free tier, what it adds, wired, sign-up url
OPTIONS = [
    ("Google service account", "GOOGLE_SA", "REQUIRED (GitHub secret only)", "Free", "Lets the bot write this sheet.", "Yes", "console.cloud.google.com"),
    ("Sheet id", "SHEET_ID", "REQUIRED (GitHub secret only)", "Free", "Which sheet to write.", "Yes", "-"),
    ("Gemini (AI, key 1)", "GEMINI_API_KEY_1", "Optional (recommended)", "Free tier, no card",
     "Lead Insight (3-4 lines) + decision-maker name check. Add _2, _3 for more free quota.", "Yes", "aistudio.google.com/apikey"),
    ("Gemini (AI, key 2)", "GEMINI_API_KEY_2", "Optional", "Free tier", "More Lead Insight quota (a 2nd Google account).", "Yes", "aistudio.google.com/apikey"),
    ("Groq (AI)", "GROQ_API_KEY", "Optional", "Free tier, no card", "Lead Insight fallback when Gemini is rate-limited.", "Yes", "console.groq.com/keys"),
    ("OpenRouter (AI)", "OPENROUTER_API_KEY", "Optional", "Free models", "Lead Insight fallback.", "Yes", "openrouter.ai/keys"),
    ("Mistral (AI)", "MISTRAL_API_KEY", "Optional", "Free tier", "Lead Insight fallback.", "Yes", "console.mistral.ai"),
    ("Cerebras (AI)", "CEREBRAS_API_KEY", "Optional", "Free tier (limits vary; check console)", "Lead Insight fallback (fast).", "Yes", "cloud.cerebras.ai"),
    ("AI keys, OLD combined secret", "AI_API_KEYS", "Old (still works)", "-",
     "Old format: several keys in one secret. Use the single-key secrets above instead (see SECRETS_GUIDE.md).", "Yes", "-"),
    ("YouTube Data API v3", "YOUTUBE_API_KEY_1", "Optional", "Free 10,000 units/day (about 80 searches)",
     "Turns on the 'YouTube Creators AU' niches (channels that can pay $1k-$2k).", "Yes", "console.cloud.google.com > YouTube Data API v3"),
    ("Hunter.io", "HUNTER_API_KEY", "Optional", "Free: 25 searches + 50 verifications / month",
     "Decision-maker names + personal emails by domain, email finder, verification.", "Yes", "hunter.io/users/sign_up"),
    ("ZeroBounce", "ZEROBOUNCE_API_KEY", "Optional", "Free: 100 verifications / month", "Bounce-proof email check.", "Yes", "zerobounce.net"),
    ("Jina Reader", "JINA_API_KEY", "Optional (works keyless)", "Free; key = higher rate limit",
     "Reads JavaScript-heavy websites the normal reader cannot.", "Yes", "jina.ai/reader"),
    ("Google Places API", "GOOGLE_PLACES_API_KEY", "Optional", "Needs a billing account; monthly free allowance per SKU",
     "Better discovery + Google rating/reviews (more accurate 'established' score).", "Yes", "console.cloud.google.com > Places API (New)"),
    ("Apollo.io", "APOLLO_API_KEY", "Optional", "Free plan, API access limited", "Employee count / company size.", "Yes", "apollo.io"),
    ("Apify", "APIFY_API_TOKEN", "Optional", "$5 free credit / month", "Instagram followers/posting stats fallback.", "Yes", "apify.com"),
    ("Meta Instagram Graph API", "IG_ACCESS_TOKEN", "Optional (also set IG_USER_ID)", "Free",
     "Instagram followers / posts in 30 days / engagement for business accounts.", "Yes", "developers.facebook.com"),
    ("Serper.dev (Google results)", "SERPER_API_KEY", "Optional (best value)", "2,500 free queries on sign-up, no card; then cheap paid credits",
     "Google rating + review count for free-map leads (closes most of the gap to Google Places), finds the website of "
     "businesses listed without one, finds owner names from LinkedIn result titles. SOURCE=serper also uses it to "
     "discover businesses.", "Yes", "serper.dev"),
    ("Brave Search API", "BRAVE_API_KEY", "Optional", "Monthly free credit (about 1,000 searches), card asked to prevent abuse",
     "Backup web search for the website finder and LinkedIn owner search when Serper is missing/used up.", "Yes",
     "brave.com/search/api"),
    ("ZenRows", "ZENROWS_API_KEY", "Optional", "Free: 5,000 credits/month, no card (JavaScript page = 5 credits)",
     "Reads websites that block normal readers or need JavaScript (home page only, robots.txt still respected).", "Yes",
     "zenrows.com"),
    ("ABN Lookup (Australian Business Register)", "ABN_GUID", "Optional (Australia)", "Free (register for a GUID)",
     "Australia: official entity name/type, GST, ABN active since -> business age (an 'established' signal when there "
     "are no Google reviews). Cancelled ABNs are skipped.", "Yes", "abr.business.gov.au/Tools/WebServices"),
    ("Apify Google Maps (discovery)", "APIFY_API_TOKEN", "Optional (same token as Apify above)",
     "$5 free credit/month = about 3,000 places at ~$1.50/1,000",
     "Turn on Rules USE_APIFY_MAPS: a few Google Maps searches per run with real ratings/reviews/websites.", "Yes",
     "apify.com/compass/crawler-google-places"),
    ("Snov.io (API) - client id", "SNOV_CLIENT_ID", "Optional (off without key)", "Free credits: check vendor page",
     "Personal email of a KNOWN decision maker, only for leads >= 7/10 (Rules MAX_SNOV_PER_RUN, USE_SNOV).", "Yes", "snov.io > Settings > API"),
    ("Snov.io (API) - client secret", "SNOV_CLIENT_SECRET", "Optional (with the client id)", "-", "Second half of the Snov.io credentials.", "Yes", "snov.io > Settings > API"),
    ("Prospeo (API)", "PROSPEO_API_KEY", "Optional (off without key)", "Free credits: check vendor page",
     "Personal email of a KNOWN decision maker, only for leads >= 7/10 (Rules MAX_PROSPEO_PER_RUN, USE_PROSPEO).", "Yes", "prospeo.io"),
    ("NZBN register (API)", "NZBN_API_KEY", "Optional (New Zealand; off without key)", "Free subscription key: check vendor page",
     "NZ: exact registered-name match -> directors (facts), registry email on their own domain, phone; removed companies skipped.",
     "Yes", "api.business.govt.nz (subscribe to the NZBN API)"),
    ("Disify (API)", "-", "Optional, no key: Rules USE_DISIFY = Yes (default No)", "Free, rate-limited: check vendor page",
     "Flags disposable / no-DNS email domains before the other checks.", "Yes", "disify.com"),
    ("Reacher (self-hosted SMTP check)", "REACHER_URL", "Optional (off without URL); REACHER_SECRET if you set one",
     "Open source; needs your own VPS with port 25 open (GitHub runners block port 25)",
     "Mailbox-level check (safe / risky / invalid) of each email before it is written.", "Yes", "github.com/reacherhq/check-if-email-exists"),
    ("Tomba", "-", "Manual use (not wired)", "Free credits: check vendor page", "Domain email search + verify.", "No", "tomba.io"),
    ("ContactOut", "-", "Manual use (API is paid)", "Free Chrome extension, small monthly quota",
     "Personal emails/phones from LinkedIn profiles. Use by hand on Tier A leads.", "No", "contactout.com"),
    ("Apollo Chrome extension", "-", "Manual use", "Free plan credits", "Decision-maker emails from LinkedIn by hand.", "No", "apollo.io"),
]

SEEN_H = ["Place Id", "Domain", "Email", "Niche", "Date", "Result"]
SEARCH_H = ["Key", "Date", "Pages", "Places", "New Leads", "Source"]
SNAP_H = ["Place Id", "Date", "Review Count"]       # v4: first review count seen -> review velocity on later checks
BLACKLIST_H = ["Place Id / Email / Domain / Business name", "Reason"]
RUN_H = ["Run time", "Summary", "Search requests", "Problems"]
SETTINGS_H = ["Setting", "Value", "What it does"]
NICHES_H = ["Niche", "Group", "Enabled (Yes/No)", "Search Terms (separate with ;)", "Budget Fit (1-5)",
            "Budget Low AUD", "Budget High AUD", "Pitch Angle Template", "Why this niche fits",
            "Free Map Selectors (OSM, separate with ;)",
            # v3 (optional per niche; blank = no extra rule)
            "Exclude Name Words (separate with ,)", "Must Match On Website (regex)",
            "Reject If Website Matches (regex)", "Chain Cut-off (same site in N places)"]
LOC_H = ["Country", "State", "City", "Search Area", "Enabled (Yes/No)", "Priority (1-3)", "Latitude", "Longitude", "Radius km"]
IMPORT_H = ["Paste any scraper CSV here WITH its header row (gosom google-maps-scraper, Apify, Outscraper...). "
            "Or put .csv/.json files in the repo's imports/ folder."]
INPUT_H = ["Business Name", "Website", "Instagram", "Email", "Phone", "Niche", "City", "State", "Notes", "Country"]

SETTINGS = [
    ("PLACES_MAX_REQUESTS_PER_MONTH", 950, "Google Places safety cap (requests per calendar month, counted in the Searches tab). "
     "Text Search Enterprise is free up to 1,000/month, then US$35 per 1,000. ALSO set a daily quota of 32 in Google Cloud > Quotas."),
    ("SOURCE", "auto", "Where leads come from. auto = Google Places only if you added PLACES_API_KEYS, otherwise FREE OpenStreetMap. osm = always free OSM. places = Google Places (needs a billing-enabled key). serper = Google Maps results via SERPER_API_KEY (then OSM). apify = Google Maps via Apify (then OSM)."),
    ("REQUIRE_WEBSITE_OSM", "Yes", "OpenStreetMap leads without a website have no way to find an email: skip them (recommended)."),
    ("OSM_LIMIT", 150, "Max businesses returned per free map search."),
    ("OSM_DELAY_SEC", 6, "Pause between free map searches (be polite to the free servers)."),
    ("MIN_BUDGET_AUD", 500, "Niches whose 'Budget Low AUD' is below this are skipped. Your floor for one project."),
    ("MIN_REVIEWS", 8, "Fewer Google reviews than this = too new/tiny. Skipped."),
    ("MAX_REVIEWS", 600, "More reviews than this = probably a large brand/chain. Skipped (you want mid-tier)."),
    ("MIN_RATING", 3.5, "Skip businesses rated below this."),
    ("CHAIN_LOCATIONS", 3, "Same business name or website in this many places inside one search = a chain. Skipped."),
    ("BLOCK_NAME_WORDS", "hospital,public health,council,medicare,bunnings,harvey norman,domayne,freedom furniture,"
     "fantastic furniture,nick scali,ikea,super amart,bupa,pacific smiles,dental corp,maven dental,my dentist,specsavers,"
     "priceline,1300smiles,national dental care,abano,anytime fitness,snap fitness,jetts,kfc,mcdonald,hilton,marriott,novotel,ibis,mercure,quest apartment,"
     "goodstart,g8 education,affinity education,guardian childcare,michael hill,pandora,prouds,angus & coote",
     "Skip businesses whose name contains any of these (comma list). Add big chains you keep seeing."),
    ("BLOCK_NAME_WORDS_WORLD", "f45,orangetheory,planet fitness,crunch fitness,24 hour fitness,la fitness,gold's gym,golds gym,"
     "equinox,midtown athletic,lifetime fitness,life time fitness,puregym,pure gym,the gym group,david lloyd,virgin active,nuffield health,"
     "fitness first,goodlife fitness,fit4less,club pilates,barry's,solidcore,pure barre,corepower,stretchlab,9round,curves,"
     "plus fitness,genesis health,world gym,ymca,foxtons,savills,knight frank,purplebricks,connells,countrywide,redfin,zillow,"
     "exp realty,keller williams,coldwell banker,century 21,re/max,remax,starbucks,costa coffee,pret a manger,greggs,"
     "subway,domino,pizza hut,nando,wagamama,five guys,chipotle,burger king,wendy's,taco bell,tim hortons,dunkin,"
     "aspen dental,heartland dental,mydentist,bupa dental,smile direct,boots,walgreens,cvs,holiday inn,hyatt,sheraton,"
     "westin,radisson,best western,premier inn,travelodge",
     "International chains/franchises skipped everywhere (added in v3; same rule as BLOCK_NAME_WORDS)."),
    ("EXCLUDE_TYPES", "hospital,local_government_office,courthouse,police,embassy,library,post_office,city_hall,school,primary_school,secondary_school",
     "Google place types to skip."),
    ("REQUIRE_WEBSITE", "No", "Yes = only businesses with a website (easier to find emails)."),
    ("REQUIRE_CONTACT", "email_or_social", "email = only leads with an email. email_or_social = email OR Instagram/Facebook/LinkedIn/X. any = also phone-only."),
    ("MIN_ICP_SCORE", 7, "QUALITY GATE. Leads below this ICP score (out of 10) are NOT added. 10 = perfect client who pays $1k/month at the first meeting."),
    ("TIER_A_ICP", 8.5, "ICP score for Tier A (contact first)."),
    ("TIER_B_ICP", 7.5, "ICP score for Tier B. Below = Tier C (still >= MIN_ICP_SCORE)."),
    ("PITCH_MIN_AUD", 500, "Suggested monthly pitch for a business lead at the minimum ICP score (in PITCH_CURRENCY per month)."),
    ("PITCH_MAX_AUD", 1000, "Suggested monthly pitch for a perfect business lead (in PITCH_CURRENCY per month)."),
    ("PITCH_YT_MIN_AUD", 1000, "Suggested monthly pitch for a YouTube channel at the minimum ICP score."),
    ("PITCH_YT_MAX_AUD", 2000, "Suggested monthly pitch for a perfect YouTube channel."),
    ("PITCH_CURRENCY", "AUD", "Currency of ALL the PITCH_* numbers above (the names say AUD for history; set USD/GBP... if you price in that). Written to 'Pitch Currency'."),
    ("TIMEZONE", "UTC", "YOUR timezone for sheet dates ('Lead Added On', Dashboard 'today'), e.g. Asia/Karachi, Australia/Sydney, America/New_York."),
    ("REJECT_OSM_BRANDS", "Yes", "Free map businesses tagged with a chain brand (brand / brand:wikidata, e.g. Anytime Fitness, Ray White HQ) are skipped as franchises."),
    ("OSM_FAIL_STOP", 3, "After this many map searches in a row fail (all free Overpass servers busy), stop map searches for this run."),
    ("YT_SOURCE", "auto", "YouTube discovery. auto = free no-key YouTube page reader (default; falls back to the API if a key exists). free = never use the API. api = YouTube Data API only (needs YOUTUBE_API_KEYS)."),
    ("YT_COUNTRIES", "AU,US,GB,CA,NZ,IE,AE", "YouTube: keep channels whose country is one of these codes. Blank = any country."),
    ("YT_ALLOW_UNKNOWN_COUNTRY", "Yes", "YouTube: Yes = also keep English channels that do not state a country (Country stays blank, never guessed). More leads, weaker ICP."),
    ("YT_FREE_DELAY_SEC", 2.5, "Free YouTube reader: pause between page requests (polite; YouTube blocks fast scrapers)."),
    ("YT_FREE_MAX_CHANNELS_PER_RUN", 150, "Free YouTube reader: max channel pages opened per run (each = 2 requests)."),
    ("YT_SEARCH_PAGES", 2, "Free YouTube reader: result pages per search (about 20 channels each)."),
    ("IMPORT_FOLDER", "imports", "Repo folder with scraper exports (.csv/.json). WARNING: in a PUBLIC repo these files are public - use the 'Import' tab instead."),
    ("IMPORT_LEADS_PER_RUN", 300, "Max new leads per run from scraper imports (on top of NEW_LEADS_PER_RUN)."),
    ("IMPORT_MAX_ROWS_PER_RUN", 4000, "Max NEW import rows looked at per run (already-seen rows are skipped instantly)."),
    ("IMPORT_DEFAULT_NICHE", "", "Niche for imported rows that have no 'niche' column and no category match. Blank = 'Imported Leads'."),
    ("FIND_DECISION_MAKER", "Yes", "Look for the owner/founder/director name on the website (team/about pages, structured data). Facts only, never guessed."),
    ("USE_HUNTER_FINDER", "Yes", "If a decision maker name is known but no personal email: ask Hunter's email finder (uses Hunter credits)."),
    ("MAX_HUNTER_FINDER_PER_RUN", 5, "Cap on Hunter email-finder calls per run."),
    ("YT_MIN_SUBS", 20000, "YouTube: skip channels with fewer subscribers."),
    ("YT_MAX_SUBS", 500000, "YouTube niches: skip channels bigger than this (they have in-house teams)."),
    ("YT_MAX_DAYS_SINCE_UPLOAD", 14, "YouTube: the last upload must be within this many days."),
    ("YT_MIN_VIEWS", 20000, "YouTube: median views of the last ~5 videos must be at least this."),
    ("YT_RESULTS_PER_SEARCH", 25, "Channels per YouTube search (1 search = 100 of your 10,000 free daily units)."),
    ("NEW_LEADS_PER_RUN", 120, "Maximum new leads per run AND per parallel job (split between niches searched)."),
    ("MAX_SEARCH_REQUESTS_PER_RUN", 40, "Searches per run/job (free OSM: each is one area+niche; Google Places: each is one paid request)."),
    ("PARALLEL_SITE_READS", 10, "Websites read at the same time (faster runs = more leads per day). 1 = one by one. Max 16."),
    ("MAX_PAGES_PER_SEARCH", 2, "Pages per search (1-3). 2 pages = up to 40 businesses per search."),
    ("RECHECK_DAYS", 120, "A rejected business can be re-checked after this many days. Added leads are never re-added."),
    ("READ_WEBSITES", "Yes", "Read the business website (honours robots.txt) for emails and social links. FREE, built in."),
    ("USE_SERPER", "Yes", "If SERPER_API_KEY is set: look up the Google rating + review count of free-map leads (1 query each)."),
    ("MAX_SERPER_PLACES_PER_RUN", 25, "Cap on Serper Google-rating lookups per run AND per parallel job (2,500 free queries in total)."),
    ("USE_WEB_SEARCH", "Yes", "If SERPER_API_KEY or BRAVE_API_KEY is set: find the website of businesses listed without one, and the owner on LinkedIn."),
    ("MAX_WEB_SEARCH_PER_RUN", 25, "Cap on website-finder searches per run/job."),
    ("MAX_LINKEDIN_SEARCH_PER_RUN", 15, "Cap on LinkedIn owner searches per run/job (only for leads that can still reach the ICP gate)."),
    ("SERPER_MAPS_SEARCHES_PER_RUN", 30, "Only with SOURCE=serper: Google Maps searches through Serper per run/job (then free OSM)."),
    ("USE_ZENROWS", "Yes", "If ZENROWS_API_KEY is set: read blocked / JavaScript-only home pages through ZenRows."),
    ("MAX_ZENROWS_PER_RUN", 30, "Cap on ZenRows page reads per run/job (5 credits each; free plan 5,000 credits/month)."),
    ("USE_ABN_LOOKUP", "Yes", "Australia: if ABN_LOOKUP_GUID is set, check the ABN with the official register (free)."),
    ("MAX_ABN_PER_RUN", 300, "Cap on ABN Lookup calls per run/job."),
    ("USE_APIFY_MAPS", "No", "Yes = a few Google Maps searches per run through Apify (uses APIFY_API_TOKEN credit)."),
    ("APIFY_MAPS_SEARCHES_PER_RUN", 2, "Apify Google Maps searches per run/job (each ~40 places ~ US$0.06)."),
    ("APIFY_MAPS_PER_SEARCH", 40, "Places per Apify Google Maps search."),
    ("SITE_TIME_LIMIT_SEC", 25, "Stop reading a slow website after this many seconds (keeps the pages already read)."),
    ("MAX_SITE_PAGES", 5, "Pages read per website (home + the contact/about/team pages it links to). Team/about pages are where owner names are."),
    ("USE_JINA_MAX", 40, "Max Jina Reader fallbacks per run (free, keyless, rate limited). Used only when the normal site read finds little."),
    ("MAX_VERIFY_PER_RUN", 80, "Max email verification calls per run (ZeroBounce/Hunter credits if keys exist; free MX check otherwise)."),
    ("USE_APIFY", "No", "Optional: Apify Instagram fallback. Uses your Apify free monthly credit. Off by default."),
    ("MAX_APIFY_PER_RUN", 10, "Cap on Apify lookups per run."),
    ("USE_HUNTER", "Yes", "If HUNTER_API_KEY is set (free plan: ~25 searches/month), look up emails by domain when the site shows none."),
    ("MAX_HUNTER_PER_RUN", 8, "Hunter free plan is tiny (~25 searches/month)."),
    ("USE_APOLLO", "Yes", "If APOLLO_API_KEY is set, fill Employee Count / company name by domain."),
    ("MAX_APOLLO_PER_RUN", 20, "Cap on Apollo lookups per run."),
    ("USE_INSTAGRAM_STATS", "Yes", "If IG_ACCESS_TOKEN + IG_USER_ID are set, measure their Instagram activity (business/creator accounts only)."),
    ("MAX_IG_PER_RUN", 40, "Meta allows ~200 lookups/hour."),
    ("USE_AI_INSIGHT", "Yes", "One small AI call per QUALIFIED lead writes 'Lead Insight' (3-4 lines: who they are + likely pains). No key, quota hit (429) or error = fact-only insight. The lead bot never writes email drafts (outreach bot does)."),
    ("MAX_AI_INSIGHTS_PER_RUN", 60, "Cap on Lead Insight AI calls per job (keeps free AI keys inside their daily limits)."),
    ("USE_DISIFY", "No", "Free disposable-email check (disify.com, no key, rate-limited). Yes = check each email first."),
    ("USE_REACHER", "Yes", "Use your self-hosted Reacher (secret REACHER_URL) for mailbox checks. No URL = off."),
    ("USE_NZBN", "Yes", "New Zealand: NZBN register lookup when the NZBN_API_KEY secret exists (exact name match only)."),
    ("MAX_NZBN_PER_RUN", 60, "Cap on NZBN register lookups per job."),
    ("USE_PROSPEO", "Yes", "Prospeo email finder for leads >= MIN_ICP_SCORE with a known decision maker (needs PROSPEO_API_KEY)."),
    ("MAX_PROSPEO_PER_RUN", 10, "Cap on Prospeo lookups per job (free credits are small: check vendor page)."),
    ("USE_SNOV", "Yes", "Snov.io email finder for leads >= MIN_ICP_SCORE with a known decision maker (needs SNOV_CLIENT_ID + SNOV_CLIENT_SECRET)."),
    ("MAX_SNOV_PER_RUN", 10, "Cap on Snov.io lookups per job (free credits are small: check vendor page)."),
    ("CELL_LIMIT_STOP", 9500000, "Stop adding rows when the sheet nears Google's 10,000,000-cell limit (logged as an ERROR in Run health)."),
    ("USE_AI_PITCH", "No", "No = pitch lines come from the Niches tab templates (zero AI credit). Yes = AI rewrites each pitch line."),
    ("MAX_AI_PER_RUN", 40, "Cap on pitch-rewrite AI calls per run."),
    ("SORT_TABS", "No", "v5: ignored. Lead tabs are append-only (never sorted) so Status / My Notes never move."),
    ("MAX_RUNTIME_MIN", 42, "The bot stops cleanly after this many minutes and saves what it found (GitHub job limit is 60)."),
    ("SEGMENT_SCORING", "Yes", "Yes = v4 high-quality scoring (R revenue proxy x S social gap x A agency, segments). No = the older v3 score."),
    ("PRIME_R_MIN", 7, "Segment PRIME / PRIME-REVIEW needs a revenue proxy R >= this (0-10)."),
    ("PRIME_S_MAX", 4, "Segment PRIME needs social strength S <= this (0-10; low = weak social = they need you)."),
    ("AGENCY_REVIEW_MIN", 30, "Agency score A from this up to AGENCY_HAS_MIN = PRIME-REVIEW (check by hand for 1 minute)."),
    ("AGENCY_HAS_MIN", 50, "Agency score A >= this = HAS-AGENCY (named credit / in-house role found)."),
    ("GROWING_R_MIN", 4, "GROWING = R from this up to PRIME_R_MIN with S <= GROWING_S_MAX."),
    ("GROWING_S_MAX", 6.5, "S above this = POLISHED-NO-SIGNAL (probably in-house already)."),
    ("REJECT_R_BELOW", 3, "R below this = rejected (cannot afford $500-1k/month)."),
    ("SIDE_SEGMENT_R_MIN", 6, "HAS-AGENCY and POLISHED-NO-SIGNAL rows are written only when R >= this (worth a replacement pitch)."),
    ("FREE_MODE_NICHE_WEIGHT", 0.30, "No review count (free mode): weight of the niche's Budget Fit inside R, next to locations / ad + booking tools / team size. Such rows say 'free signals (unverified)' and are never PRIME (max PRIME-REVIEW)."),
    ("S_UNMEASURED", 4.0, "S used when a social profile exists but cannot be measured (no IG token / Apify)."),
    ("REV_BAND_MID", 15000, "Revenue band: below this per month (local currency) = Low."),
    ("REV_BAND_HIGH", 60000, "Revenue band: above this per month = High (between = Mid)."),
    ("REVIEW_FACTOR_US", 1.0, "Review thresholds x this in the US (smaller markets get lower thresholds)."),
    ("REVIEW_FACTOR_GB", 0.7, "Review threshold factor, UK."),
    ("REVIEW_FACTOR_CA", 0.8, "Review threshold factor, Canada."),
    ("REVIEW_FACTOR_AU", 0.8, "Review threshold factor, Australia."),
    ("REVIEW_FACTOR_NZ", 0.5, "Review threshold factor, New Zealand."),
    ("REVIEW_FACTOR_IE", 0.5, "Review threshold factor, Ireland."),
    ("MAX_INHOUSE_SEARCH_PER_RUN", 15, "With SERPER/BRAVE: LinkedIn search for an in-house social media / marketing person (PRIME candidates only)."),
    ("W3_REVENUE", 35, "v4 ICP weight: revenue proxy R."),
    ("W3_SOCIAL_GAP", 25, "v4 ICP weight: social gap (10 - S)."),
    ("W3_NICHE_BUDGET", 15, "v4 ICP weight: Budget Fit column."),
    ("W3_CONTACT", 15, "v4 ICP weight: reachable (personal email best)."),
    ("W3_DECISION_MAKER", 5, "v4 ICP weight: decision maker known."),
    ("W3_REPUTATION", 3, "v4 ICP weight: Google rating."),
    ("W3_LOCATION", 2, "v4 ICP weight: location priority."),
    ("W3_AGENCY_PENALTY", 15, "v4 ICP penalty: minus (A/100) x this when A < AGENCY_HAS_MIN."),
    ("W2_NICHE_BUDGET", 30, "ICP weight: how big a budget this niche usually has (Budget Fit column)."),
    ("W2_ESTABLISHED", 15, "ICP weight: established but not huge (reviews / YouTube subscribers)."),
    ("W2_CONTACT", 20, "ICP weight: reachable. Personal email > generic email > social only."),
    ("W2_DECISION_MAKER", 10, "ICP weight: we know the decision maker's name (fact from website/Hunter)."),
    ("W2_SOCIAL_GAP", 10, "ICP weight: weak/irregular social = they need you."),
    ("W2_SPEND_SIGNAL", 5, "ICP weight: website runs Google Ads / Meta Pixel / booking = already pays for marketing."),
    ("W2_WEBSITE", 5, "ICP weight: has a working website."),
    ("W2_REPUTATION", 3, "ICP weight: Google rating."),
    ("W2_LOCATION", 2, "ICP weight: priority of the location (metro first)."),
]

_HC = ("Health advertising is regulated (AU: AHPRA/TGA, UK: ASA/CAP + GMC/GDC, US: FTC + state boards, UAE: DHA/MOHAP "
       "approval): pitch compliant content.")

# name, group, enabled, terms, fit, low, high, pitch template, why
_NICHES = [
    ("Cosmetic & Plastic Surgeons", "Medical", "Yes", "plastic surgeon; cosmetic surgeon; breast surgeon; facial plastic surgeon", 5, 2000, 5000,
     "{name} has {reviews} Google reviews at {rating} stars. Gap: {gap}. Offer a free 30-60s sample reel and a compliant content plan.",
     "One patient is worth $5k-$20k+, so $1-3k/month marketing is normal. Own private clinic, trust and video driven. Top ICP. " + _HC),
    ("Dentists (Cosmetic, Implants, Ortho)", "Medical", "Yes", "cosmetic dentist; dental implants; orthodontist; dentist; family dental clinic", 5, 1000, 4000,
     "{name}: {reviews} reviews at {rating} stars show patients trust you. Gap: {gap}. Offer a free sample reel for implants/whitening.",
     "Implants, aligners and veneers are $3k-$30k cases. Independent practices spend on marketing; corporate chains are filtered out. " + _HC),
    ("Specialist Doctors & Private Clinics", "Medical", "Yes", "dermatologist; ophthalmologist laser eye surgery; fertility clinic; ENT specialist; orthopaedic surgeon; urologist", 4, 1000, 3500,
     "{name} ({reviews} reviews, {rating} stars). Gap: {gap}. Offer a free sample patient-education video.",
     "Private specialists compete for referrals and self-referred patients; education video builds trust. " + _HC),
    ("Cosmetic & Skin Clinics", "Medical", "Yes", "cosmetic clinic; skin clinic; laser clinic; medical spa; cosmetic injectables clinic", 5, 800, 3000,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free sample reel and a compliant content plan.",
     "High-margin recurring treatments and heavy Instagram/TikTok demand. " + _HC),
    ("Real Estate Agencies (Independent)", "Real Estate", "Yes", "real estate agent; real estate agency; estate agent; realtor; boutique real estate; property sales", 5, 1000, 4000,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free 30-60s property-tour reel sample.",
     "One sale = $10k-$30k commission (US/UK/CA/AU/NZ/IE/UAE alike). Listing video and agent branding win listings. Independent and franchisee offices decide their own marketing; corporate-owned brands are filtered out."),
    ("Buyers Agents & Property Advisors", "Real Estate", "Yes", "buyers agent; property investment advisor; property management agency", 4, 800, 3000,
     "{name} has {reviews} reviews. Gap: {gap}. Offer a free sample explainer video for investors.",
     "High-fee advisory sold on trust and authority: ideal for video and LinkedIn/IG content."),
    ("Mortgage Brokers", "Finance", "Yes", "mortgage broker; home loan broker; finance broker", 4, 700, 2500,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free short explainer reel on current loan tips.",
     "Each settled loan pays them $3k-$8k; they pay for lead flow and authority content."),
    ("Migration Agents & Immigration Lawyers", "Legal", "Yes", "migration agent; immigration lawyer; visa consultant; student visa agent", 5, 800, 3000,
     "{name} ({reviews} reviews). Gap: {gap}. Offer a free sample explainer video on a visa topic.",
     "Huge Australian market, high fees, audiences live on Instagram/TikTok/YouTube. Strong buyers of video + ads + SEO."),
    ("Family, Property & Estate Law Firms", "Legal", "Yes", "family lawyer; conveyancer; wills and estate lawyer; personal injury lawyer; commercial lawyer", 4, 1000, 4000,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free sample explainer reel and local SEO audit.",
     "High client value, search-driven, under-use video. Note legal advertising rules apply."),
    ("Accountants & Financial Planners", "Finance", "Yes", "accountant; financial planner; tax agent; SMSF accountant; bookkeeper", 3, 600, 2000,
     "{name} has {reviews} reviews. Gap: {gap}. Offer a free tax-tips sample reel.",
     "Recurring revenue clients; moderate budgets. Good volume niche."),
    ("Furniture Stores (Independent)", "Retail Showrooms", "Yes", "furniture store; custom furniture; outdoor furniture showroom; lounge showroom; dining furniture", 4, 800, 3500,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free 30-60s showroom reel sample.",
     "High ticket items ($2k-$15k baskets), visual product: perfect for reels, ads and Pinterest/Instagram shopping."),
    ("Kitchen, Bathroom & Renovation Showrooms", "Retail Showrooms", "Yes", "kitchen showroom; bathroom showroom; tile showroom; kitchen renovation; cabinet maker", 5, 1000, 4000,
     "{name} ({reviews} reviews). Gap: {gap}. Offer a free sample before/after reel.",
     "Projects worth $15k-$80k; before/after video converts. Owners buy marketing."),
    ("Car, Boat & Caravan Dealers (Independent)", "Automotive", "Yes", "car dealership; used car yard; luxury car showroom; caravan dealer; boat dealer; motorcycle dealership", 4, 1000, 4000,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free sample walk-around reel.",
     "Stock-driven businesses that need constant video and ads; owner-operated yards decide fast."),
    ("Custom Home Builders & Architects", "Construction", "Yes", "custom home builder; home builder; architect; granny flat builder; display homes", 5, 1500, 5000,
     "{name} has {reviews} reviews. Gap: {gap}. Offer a free sample project-showcase reel.",
     "Builds worth $300k+; a single lead pays for a year of marketing. Top-budget ICP."),
    ("Solar & Energy Installers", "Home Services", "Yes", "solar installer; solar panels; battery installation; solar company", 4, 1000, 3500,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free sample install-day reel and ad creative.",
     "Competitive, ad-driven, high job value ($6k-$25k)."),
    ("Pool, Landscaping & Outdoor Builders", "Home Services", "Yes", "pool builder; landscape designer; landscaping company; outdoor living; pergola builder", 4, 800, 3000,
     "{name} ({reviews} reviews). Gap: {gap}. Offer a free sample transformation reel.",
     "Very visual, $20k-$100k projects. Owners respond to before/after video."),
    ("Wedding Venues & Event Spaces", "Events", "Yes", "wedding venue; function centre; reception venue; garden wedding venue", 4, 800, 3000,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free sample venue-tour reel.",
     "Bookings worth $10k-$40k; visual, Instagram-led buying journey."),
    ("Private Colleges & Training Providers", "Education", "Yes", "private college; RTO training; vocational college; international student agent; english language school", 4, 1000, 4000,
     "{name} has {reviews} reviews. Gap: {gap}. Offer a free sample course-promo video.",
     "Enrolment-driven with ad budgets; student acquisition is video/social heavy."),
    ("Interior Designers & Home Staging", "Design", "No", "interior designer; home stylist; home staging", 3, 600, 2000,
     "{name} ({reviews} reviews). Gap: {gap}. Offer a free sample project reel.",
     "Visual portfolio businesses; mid budgets. Second wave."),
    ("Physio, Chiro & Allied Health", "Health", "No", "physiotherapist; chiropractor; osteopath; podiatrist", 3, 600, 2000,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free sample exercise-tips reel.",
     "Many clinics; lower budgets than surgeons. Second wave. " + _HC),
    ("Veterinary Clinics (Independent)", "Health", "No", "veterinary clinic; vet hospital; animal hospital", 3, 600, 2000,
     "{name}: {reviews} reviews at {rating} stars. Gap: {gap}. Offer a free sample pet-care-tips reel.",
     "Independent vets are shrinking in number but still buy marketing; second wave."),
    # ---- added in v2: reliable high-ticket niches ----
    ("Hair Transplant & Hair Loss Clinics", "Medical", "Yes", "hair transplant clinic; hair loss clinic; hair restoration", 5, 1000, 4000,
     "{name}. Gap: {gap}. Offer a free compliant patient-journey reel sample.",
     "$8k-$20k procedures, before/after driven, patients research on Instagram and YouTube. " + _HC),
    ("Jewellers & Bridal (Independent)", "Retail Showrooms", "Yes", "jeweller; engagement rings; custom jewellery; diamond jeweller", 4, 800, 3000,
     "{name}. Gap: {gap}. Offer a free 30s ring-showcase reel sample.",
     "Engagement rings $3k-$20k, extremely visual product, Instagram/Pinterest-led buying."),
    ("Boutique Hotels & Luxury Stays", "Hospitality", "Yes", "boutique hotel; luxury accommodation; resort; glamping", 4, 1000, 3500,
     "{name}. Gap: {gap}. Offer a free property-tour reel sample.",
     "Direct bookings save 15-20% OTA commission, so owners pay for reels and ads that drive them."),
    ("Private Childcare & Early Learning", "Education", "Yes", "early learning centre; childcare centre; long day care; kindergarten", 4, 800, 2500,
     "{name}. Gap: {gap}. Offer a free centre-tour reel sample for enrolment season.",
     "Enrolment-driven, each child is $15k-$30k/year revenue; independent operators market locally."),
    ("Driveway, Roofing & Concrete Contractors (5+ staff)", "Home Services", "No", "roofing company; concreting company; epoxy flooring; driveway contractor", 3, 700, 2500,
     "{name}. Gap: {gap}. Offer a free before/after job reel.",
     "Jobs $5k-$40k, visual transformations. Second wave: filter out 1-2 person operators."),
    # ---- YouTube creators (needs free YOUTUBE_API_KEYS; 'Search Terms' are YouTube searches, no map selectors) ----
    ("YouTube Creators AU - Business, Finance & Property", "YouTube", "Yes",
     "australian property investing; australian personal finance; australian small business tips; sydney real estate vlog", 5, 1000, 2000,
     "{name}. Gap: {gap}. Offer a free edit of one existing video (hook + pacing + captions).",
     "Monetised educators sell courses/coaching; editing is their bottleneck. Pitch $1k-$2k/month."),
    ("YouTube Creators AU - Health, Fitness & Lifestyle", "YouTube", "Yes",
     "australian fitness coach; australian nutrition; melbourne lifestyle vlog; australian wellness", 4, 1000, 2000,
     "{name}. Gap: {gap}. Offer a free Shorts pack cut from their latest video.",
     "Coaches and brands with products; regular uploads need an editor."),
    ("YouTube Creators AU - Travel, Food & Auto", "YouTube", "Yes",
     "australia travel vlog; australian food review; australian car review; 4wd australia", 4, 1000, 2000,
     "{name}. Gap: {gap}. Offer a free cinematic re-edit of one segment.",
     "Sponsor-funded channels where edit quality drives views and deals."),
    # ---- v3: worldwide niches ----
    ("Boutique Fitness Studios & Gyms (Owner-operated)", "Fitness", "Yes",
     "boutique gym; pilates studio; reformer pilates; personal training studio; strength and conditioning gym; boxing gym; yoga studio",
     4, 800, 2500,
     "{name}. Gap: {gap}. Offer a free class-highlight / member-transformation reel sample.",
     "Owner-operated studios (often 2-5 sites) live on Instagram reels and paid social for memberships; franchises "
     "(F45, Anytime, Orangetheory...) are filtered out because head office controls marketing."),
    ("Fine Dining & Restaurant Groups (Upscale / Multi-venue)", "Hospitality", "Yes",
     "fine dining restaurant; degustation restaurant; tasting menu restaurant; steakhouse; restaurant group; private dining room",
     4, 800, 3000,
     "{name}. Gap: {gap}. Offer a free signature-dish / private-dining reel sample.",
     "ONLY upscale or multi-venue operators: the website must show fine-dining cues (tasting menu, degustation, sommelier, "
     "awards, private dining) or several venues. Cafes, takeaways and fast food are filtered out (low budgets)."),
]

OSM_SEL = {
    "Cosmetic & Plastic Surgeons": "healthcare:speciality=plastic_surgery; amenity~^(clinic|doctors)$ + name~plastic|cosmetic surg|aesthetic|breast; healthcare=clinic + name~plastic|cosmetic surg|aesthetic",
    "Dentists (Cosmetic, Implants, Ortho)": "amenity=dentist; healthcare=dentist",
    "Specialist Doctors & Private Clinics": "healthcare:speciality~dermatolog|ophthalm|orthop|otolaryng|urolog|fertil|gynae; amenity~^(clinic|doctors)$ + name~dermatolog|ophthalm|laser eye|orthop|fertility|urolog|ENT specialist",
    "Cosmetic & Skin Clinics": "shop=beauty + name~skin|laser|cosmetic|aesthetic|inject; amenity~^(clinic|doctors)$ + name~skin|laser|cosmetic|aesthetic|inject",
    "Real Estate Agencies (Independent)": "office=estate_agent",
    "Buyers Agents & Property Advisors": "office~^(estate_agent|property_management|consulting)$ + name~buyer|invest|advocate|advis",
    "Mortgage Brokers": "office~^(financial|financial_advisor)$ + name~mortgage|home loan|broker|finance",
    "Migration Agents & Immigration Lawyers": "office~^(lawyer|consulting|company|yes)$ + name~migration|immigration|visa",
    "Family, Property & Estate Law Firms": "office=lawyer",
    "Accountants & Financial Planners": "office~^(accountant|tax_advisor|financial_advisor|financial)$",
    "Furniture Stores (Independent)": "shop=furniture",
    "Kitchen, Bathroom & Renovation Showrooms": "shop~^(kitchen|bathroom_furnishing|tiles|interior_decoration)$; shop=furniture + name~kitchen|bathroom|cabinet",
    "Car, Boat & Caravan Dealers (Independent)": "shop~^(car|motorcycle|boat|caravan)$",
    "Custom Home Builders & Architects": "office=architect; craft=builder; office + name~builder|homes|constructions",
    "Solar & Energy Installers": "craft + name~solar; office + name~solar; shop + name~solar",
    "Pool, Landscaping & Outdoor Builders": "shop=swimming_pool; craft=gardener; craft + name~landscap|pool; office + name~landscap|pool",
    "Wedding Venues & Event Spaces": "amenity=events_venue; amenity~^(community_centre|restaurant)$ + name~wedding|function|reception",
    "Private Colleges & Training Providers": "amenity~^(college|language_school|training)$; office=educational_institution",
    "Interior Designers & Home Staging": "shop=interior_decoration; office + name~interior|styling",
    "Physio, Chiro & Allied Health": "healthcare~^(physiotherapist|chiropractor|osteopath|podiatrist)$",
    "Veterinary Clinics (Independent)": "amenity=veterinary",
    "Boutique Fitness Studios & Gyms (Owner-operated)": "leisure=fitness_centre; leisure=sports_centre + sport~fitness|pilates|yoga|crossfit|boxing|barre|martial",
    "Fine Dining & Restaurant Groups (Upscale / Multi-venue)":
        "amenity=restaurant + takeaway!=only + cuisine!~pizza|kebab|burger|chicken|fish_and_chips|sandwich|coffee_shop|donut|bubble_tea|fast_food",
    "Hair Transplant & Hair Loss Clinics": "amenity~^(clinic|doctors)$ + name~hair; shop=hairdresser + name~transplant|restoration|hair loss",
    "Jewellers & Bridal (Independent)": "shop=jewelry",
    "Boutique Hotels & Luxury Stays": "tourism~^(hotel|guest_house|chalet)$; tourism=camp_site + name~glamp",
    "Private Childcare & Early Learning": "amenity~^(childcare|kindergarten)$",
    "Driveway, Roofing & Concrete Contractors (5+ staff)": "craft~^(roofer|concrete|floorer)$; office + name~roofing|concret|epoxy",
}
# v3 per-niche rules: exclude name words, must-match website regex, reject website regex, chain cut-off
_UPSCALE = (r"fine[ -]dining|degustation|tasting menu|chef'?s (?:table|menu)|michelin|hatted|chef hats?|aa rosettes?|"
            r"sommelier|wine pairing|private dining|our (?:restaurants|venues)|restaurant group|hospitality group|"
            r"(?:two|three|four|five|six|\d) (?:restaurants|venues)")
_FRANCHISE = r"franchise (?:opportunit|enquir|information)|own (?:a|your) (?:franchise|club|gym|studio)|become a franchisee|franchising"
NICHE_RULES = {
    "Boutique Fitness Studios & Gyms (Owner-operated)": (
        "council,leisure centre,aquatic,university,school,college,hotel,apartment,ymca,community,physio,hospital,police,"
        "athletic league,recreation center,rec center,city of,park district,tennis courts,fieldhouse,field house,"
        "aquatic center,natatorium", "",
        _FRANCHISE, 6),
    "Fine Dining & Restaurant Groups (Upscale / Multi-venue)": (
        "cafe,café,coffee,espresso,pizza,pizzeria,kebab,burger,takeaway,take away,take-away,fish & chips,fish and chips,"
        "fried chicken,noodle bar,food court,bakery,donut,doughnut,bubble tea,juice,sandwich,deli,canteen,diner,express,"
        "food truck,buffet", _UPSCALE, "", 6),
    "Real Estate Agencies (Independent)": (
        "compass,douglas elliman,corcoran,sotheby's,sothebys,christie's international,berkshire hathaway,howard hanna,"
        "william raveis,engel & völkers,engel & volkers,winkworth,chestertons,dexters,marsh & parsons,ljhooker,"
        "allsop,cbre,jll,cushman", "", "", ""),
}
import quality as _Q                                                    # noqa: E402

NICHES_H = NICHES_H + _Q.NICHE_Q_H                                      # v4: per-niche quality thresholds (editable)


def niche_rows(cc=""):
    """Niches tab seed rows; the quality columns use country-specific average tickets."""
    return [tuple(n) + (OSM_SEL.get(n[0], ""),) + tuple(NICHE_RULES.get(n[0], ("", "", "", ""))) + _Q.niche_defaults(n[0], cc)
            for n in _NICHES]


NICHES = niche_rows()

# country, state, city, search area, enabled, priority, latitude, longitude, radius km
LOCATIONS = [
    ("Australia", "NSW", "Sydney", "Sydney NSW", "Yes", 1, -33.8688, 151.2093, 6),
    ("Australia", "NSW", "Sydney", "North Sydney NSW", "Yes", 1, -33.8403, 151.207, 4),
    ("Australia", "NSW", "Sydney", "Parramatta NSW", "Yes", 1, -33.8151, 151.0011, 6),
    ("Australia", "NSW", "Sydney", "Bondi Junction NSW", "Yes", 1, -33.8915, 151.2477, 4),
    ("Australia", "NSW", "Sydney", "Chatswood NSW", "Yes", 1, -33.7969, 151.183, 4),
    ("Australia", "NSW", "Sydney", "Penrith NSW", "Yes", 2, -33.7507, 150.6944, 7),
    ("Australia", "NSW", "Sydney", "Liverpool NSW", "Yes", 2, -33.92, 150.923, 7),
    ("Australia", "NSW", "Sydney", "Sutherland NSW", "Yes", 2, -34.031, 151.057, 6),
    ("Australia", "VIC", "Melbourne", "Melbourne VIC", "Yes", 1, -37.8136, 144.9631, 6),
    ("Australia", "VIC", "Melbourne", "South Yarra VIC", "Yes", 1, -37.8386, 144.9926, 4),
    ("Australia", "VIC", "Melbourne", "Box Hill VIC", "Yes", 1, -37.8189, 145.1219, 5),
    ("Australia", "VIC", "Melbourne", "Richmond VIC", "Yes", 1, -37.823, 144.998, 4),
    ("Australia", "VIC", "Melbourne", "Doncaster VIC", "Yes", 2, -37.7878, 145.126, 5),
    ("Australia", "VIC", "Melbourne", "Footscray VIC", "Yes", 2, -37.8, 144.9, 5),
    ("Australia", "VIC", "Melbourne", "Dandenong VIC", "Yes", 2, -37.987, 145.215, 6),
    ("Australia", "VIC", "Melbourne", "Brighton VIC", "Yes", 2, -37.917, 144.999, 5),
    ("Australia", "QLD", "Brisbane", "Brisbane QLD", "Yes", 1, -27.4698, 153.0251, 6),
    ("Australia", "QLD", "Brisbane", "Chermside QLD", "Yes", 1, -27.386, 153.034, 5),
    ("Australia", "QLD", "Brisbane", "Indooroopilly QLD", "Yes", 2, -27.499, 152.973, 5),
    ("Australia", "QLD", "Brisbane", "Sunnybank QLD", "Yes", 2, -27.578, 153.058, 5),
    ("Australia", "QLD", "Gold Coast", "Gold Coast QLD", "Yes", 1, -28.0027, 153.431, 9),
    ("Australia", "QLD", "Sunshine Coast", "Sunshine Coast QLD", "Yes", 2, -26.658, 153.09, 10),
    ("Australia", "WA", "Perth", "Perth WA", "Yes", 1, -31.9505, 115.8605, 6),
    ("Australia", "WA", "Perth", "Subiaco WA", "Yes", 1, -31.947, 115.824, 4),
    ("Australia", "WA", "Perth", "Joondalup WA", "Yes", 2, -31.745, 115.766, 6),
    ("Australia", "WA", "Perth", "Fremantle WA", "Yes", 2, -32.0569, 115.7439, 5),
    ("Australia", "SA", "Adelaide", "Adelaide SA", "Yes", 1, -34.9285, 138.6007, 6),
    ("Australia", "SA", "Adelaide", "Norwood SA", "Yes", 2, -34.921, 138.632, 4),
    ("Australia", "ACT", "Canberra", "Canberra ACT", "Yes", 2, -35.2809, 149.13, 8),
    ("Australia", "NSW", "Newcastle", "Newcastle NSW", "Yes", 2, -32.9283, 151.7817, 8),
    ("Australia", "NSW", "Wollongong", "Wollongong NSW", "Yes", 2, -34.4278, 150.8931, 8),
    ("Australia", "VIC", "Geelong", "Geelong VIC", "Yes", 2, -38.1499, 144.3617, 8),
    ("Australia", "TAS", "Hobart", "Hobart TAS", "Yes", 3, -42.8821, 147.3272, 8),
    ("Australia", "QLD", "Townsville", "Townsville QLD", "Yes", 3, -19.259, 146.8169, 8),
    ("Australia", "QLD", "Cairns", "Cairns QLD", "Yes", 3, -16.9186, 145.7781, 8),
    ("Australia", "NT", "Darwin", "Darwin NT", "Yes", 3, -12.4634, 130.8456, 8),
    ("Australia", "QLD", "Toowoomba", "Toowoomba QLD", "Yes", 3, -27.5598, 151.9507, 7),
    ("Australia", "VIC", "Ballarat", "Ballarat VIC", "Yes", 3, -37.5622, 143.8503, 7),
    ("Australia", "VIC", "Bendigo", "Bendigo VIC", "Yes", 3, -36.757, 144.2794, 7),
    # ---- v3: top English-speaking metros (added once to existing sheets; set Enabled = No to pause a row) ----
    # v5 Gulf sheets (optional: only searched when SHEET_ID_SA / SHEET_ID_QA exist). OSM coverage is thinner here.
]

PLAYBOOK = [
    "WHO IS YOUR ICP (project value $500 to $5,000)",
    "The rule: the owner earns $3k+ per customer, decides marketing alone, and already sells through Instagram/Google. Then $1k-$3k/month is an easy yes.",
    "TOP TIER (pitch $1,500-$5,000): cosmetic/plastic surgeons, cosmetic dentists and implant clinics, cosmetic & skin clinics, custom home builders, kitchen/bathroom showrooms, real estate agencies, migration agents.",
    "STRONG (pitch $800-$3,500): furniture showrooms, solar installers, pool/landscape builders, wedding venues, private colleges, specialist doctors, buyers agents, law firms, car/boat/caravan dealers.",
    "SOLID (pitch $600-$2,000): mortgage brokers, accountants, interior designers, physios (second wave, disabled by default).",
    "AVOID: cafes, takeaways, casual restaurants (only upscale / multi-venue restaurant groups are kept), hair salons, tradies with 1-2 staff, franchise-owned chains: budgets under $300 or marketing decided at head office. The bot filters chains by name, repeated locations and review count.",
    "",
    "WHAT THE SCORE MEANS",
    "ICP Score /10 = niche budget (3) + established (1.5) + reachable, personal email best (2) + decision maker known (1) + social gap (1) + already spends on ads (0.5) + website (0.5) + rating/location (0.5). Only leads >= MIN_ICP_SCORE (default 7) are added.",
    "Suggested Pitch /mo is a starting number scaled by ICP score inside PITCH_MIN_AUD..PITCH_MAX_AUD (YouTube: PITCH_YT_*), in PITCH_CURRENCY (see the Pitch Currency column). It is an estimate, not a fact.",
    "Decision Maker Name is filled ONLY when a name and a decision role (owner/founder/director/principal/manager) are written together on their website or in Hunter. Blank = not found. Never guessed.",
    "'Social Gap' says why they need you: no Instagram/Facebook found, posting under 4x/month, or low engagement.",
    "",
    "HOW TO WORK THE LEADS",
    "- Tier A with an email first; Instagram DM second. Reception/info@ mailboxes: ask for the practice or marketing manager.",
    "- Open with the Pitch Angle, then offer a free 30-60 second sample reel for their business.",
    "- Set Status (New > Contacted > Replied > Call booked > Client > Not interested > Do not contact). Add anyone to Blacklist to never see them again.",
    "- Expect 2-5% DM reply rate and 3-8% on good email; Tier A + a specific sample lifts it.",
    "",
    "AUSTRALIAN RULES (not legal advice, check before you send)",
    "- Spam Act 2003: commercial email/SMS needs consent; publicly published business addresses can imply consent only when your message is relevant to their role. Always identify yourself and include a working unsubscribe, and honour it.",
    "- Health advertising (AHPRA / TGA): no patient testimonials, no promoting prescription-only treatments. Pitch compliant content. This is a selling point.",
    "- Phone: check the Do Not Call Register before cold calling.",
    "",
    "OTHER COUNTRIES (not legal advice)",
    "- US: CAN-SPAM. Cold B2B email is allowed if you use your real identity, a non-misleading subject, your postal address and a working opt-out honoured within 10 business days.",
    "- UK: PECR + UK GDPR. Emailing corporate addresses (ltd/plc) is allowed with legitimate interest and an opt-out; sole traders and partnerships need consent.",
    "- Ireland (EU): ePrivacy + GDPR. Business addresses: relevant offer, clear identity, opt-out in every message; keep a record of why you emailed.",
    "- Canada: CASL is strict. Only email addresses that are conspicuously published, with no \"no unsolicited email\" note, and only about their business role. Include identity + unsubscribe.",
    "- New Zealand: Unsolicited Electronic Messages Act (similar to AU: inferred consent from published business addresses, identify yourself, unsubscribe).",
    "- UAE: TDRA rules and PDPL. Keep B2B, relevant and with an opt-out.",
    "- Data: default source is OpenStreetMap (free, open licence: keep the Data Source column if you publish). Google Places is optional and has its own terms.",
    "",
    "CANNOT DO",
    "- It cannot read private Instagram data or any LinkedIn/Facebook engagement. Instagram stats work only for business/creator accounts via Meta's official API.",
    "- Employee counts need Apollo. Owner names come only from public website text or Hunter. Blank is better than a guess.",
]


# ====================================================================== Australia-only edition (src/market.py = AU)
import market as _M                                                    # noqa: E402

MARKET = _M.MARKET
AU_COLS = ["ABN", "ABR Entity Name", "Entity Type", "GST Registered", "ABN Active Since", "Business Age (yrs)",
           "Phone Type", "AU Timezone"]
# more suburbs/regions = a bigger free-map pool (each new area adds roughly 100 leads over time)
AU_EXTRA_LOCATIONS = [
    ("Australia", "NSW", "Sydney", "Castle Hill NSW", "Yes", 1, -33.731, 151.004, 4),
    ("Australia", "NSW", "Sydney", "Hornsby NSW", "Yes", 2, -33.7025, 151.099, 4),
    ("Australia", "NSW", "Sydney", "Manly NSW", "Yes", 1, -33.7969, 151.284, 3),
    ("Australia", "NSW", "Sydney", "Double Bay NSW", "Yes", 1, -33.877, 151.242, 3),
    ("Australia", "NSW", "Sydney", "Mosman NSW", "Yes", 1, -33.829, 151.244, 3),
    ("Australia", "NSW", "Sydney", "Burwood NSW", "Yes", 2, -33.877, 151.104, 3),
    ("Australia", "NSW", "Sydney", "Hurstville NSW", "Yes", 2, -33.967, 151.102, 4),
    ("Australia", "NSW", "Sydney", "Campbelltown NSW", "Yes", 2, -34.065, 150.814, 5),
    ("Australia", "NSW", "Sydney", "Cronulla NSW", "Yes", 2, -34.056, 151.152, 3),
    ("Australia", "NSW", "Sydney", "Blacktown NSW", "Yes", 2, -33.771, 150.906, 5),
    ("Australia", "NSW", "Sydney", "Bella Vista & Norwest NSW", "Yes", 1, -33.736, 150.955, 4),
    ("Australia", "NSW", "Byron Bay", "Byron Bay NSW", "Yes", 3, -28.6474, 153.602, 5),
    ("Australia", "VIC", "Melbourne", "Camberwell VIC", "Yes", 1, -37.826, 145.058, 3),
    ("Australia", "VIC", "Melbourne", "Glen Waverley VIC", "Yes", 2, -37.878, 145.165, 4),
    ("Australia", "VIC", "Melbourne", "Hawthorn VIC", "Yes", 1, -37.822, 145.035, 3),
    ("Australia", "VIC", "Melbourne", "St Kilda VIC", "Yes", 1, -37.867, 144.98, 3),
    ("Australia", "VIC", "Melbourne", "Toorak VIC", "Yes", 1, -37.841, 145.017, 3),
    ("Australia", "VIC", "Melbourne", "Frankston VIC", "Yes", 3, -38.144, 145.126, 5),
    ("Australia", "VIC", "Melbourne", "Essendon VIC", "Yes", 2, -37.752, 144.918, 4),
    ("Australia", "VIC", "Melbourne", "Ringwood VIC", "Yes", 2, -37.814, 145.229, 4),
    ("Australia", "QLD", "Brisbane", "Fortitude Valley & New Farm QLD", "Yes", 1, -27.457, 153.034, 3),
    ("Australia", "QLD", "Brisbane", "Carindale QLD", "Yes", 2, -27.503, 153.101, 4),
    ("Australia", "QLD", "Brisbane", "North Lakes QLD", "Yes", 2, -27.235, 153.021, 5),
    ("Australia", "QLD", "Gold Coast", "Broadbeach QLD", "Yes", 1, -28.027, 153.43, 3),
    ("Australia", "QLD", "Gold Coast", "Southport QLD", "Yes", 2, -27.967, 153.4, 4),
    ("Australia", "QLD", "Gold Coast", "Robina QLD", "Yes", 2, -28.078, 153.385, 4),
    ("Australia", "QLD", "Sunshine Coast", "Noosa QLD", "Yes", 2, -26.394, 153.09, 5),
    ("Australia", "WA", "Perth", "Cottesloe & Claremont WA", "Yes", 1, -31.986, 115.77, 3),
    ("Australia", "WA", "Perth", "Applecross WA", "Yes", 2, -32.012, 115.837, 3),
    ("Australia", "WA", "Mandurah", "Mandurah WA", "Yes", 3, -32.5269, 115.7217, 6),
    ("Australia", "SA", "Adelaide", "Glenelg SA", "Yes", 2, -34.98, 138.513, 3),
    ("Australia", "SA", "Adelaide", "Unley SA", "Yes", 2, -34.95, 138.606, 3),
    ("Australia", "ACT", "Canberra", "Belconnen ACT", "Yes", 3, -35.239, 149.066, 4),
    ("Australia", "TAS", "Launceston", "Launceston TAS", "Yes", 3, -41.4332, 147.1441, 6),
]
_AU_SETTINGS = {"YT_COUNTRIES": "AU", "TIMEZONE": "Australia/Sydney", "PITCH_CURRENCY": "AUD"}
_AU_PLAYBOOK_EXTRA = [
    "",
    "AUSTRALIA-ONLY COLUMNS",
    "- ABN: printed on their website (checksum-checked) or an EXACT name + state match in the official ABN Lookup. Blank = not found.",
    "- ABR Entity Name / Entity Type / GST Registered / ABN Active Since / Business Age (yrs): official register (needs the free ABN_LOOKUP_GUID).",
    "  Business Age also feeds the ICP 'established' score when there are no Google reviews.",
    "- Phone Type: Mobile numbers must be checked against the Do Not Call Register before a cold call. Landline/13/1300 are business lines.",
    "- AU Timezone: send emails 8-10am in THEIR timezone (Perth is 2-3 hours behind Sydney).",
]

if MARKET == "AU":
    LEAD_H = LEAD_H + AU_COLS                                          # appended at the end: positions never move
    LOCATIONS = [l for l in LOCATIONS if l[0] == "Australia"] + AU_EXTRA_LOCATIONS
    NICHES = [n for n in NICHES if not (n[1] == "YouTube" and not n[0].startswith("YouTube Creators AU"))]
    SETTINGS = [(k, _AU_SETTINGS.get(k, v), why) for k, v, why in SETTINGS]
    _i = PLAYBOOK.index("OTHER COUNTRIES (not legal advice)")
    _j = next(k for k in range(_i + 1, len(PLAYBOOK)) if not PLAYBOOK[k].startswith("- "))
    PLAYBOOK = PLAYBOOK[:_i - 1] + PLAYBOOK[_j:] + _AU_PLAYBOOK_EXTRA


# ====================================================================== v4: one Google Sheet per country + YouTube
# target -> GitHub secret holding that sheet's id. Missing secret = that target is skipped (old SHEET_ID = fallback).
SHEET_TARGETS = ["AU"]
SHEET_ALIASES = {"AU": ["SHEET_ID"]}
# niche tabs appear in this group order after the Dashboard (then any other group, alphabetically)
GROUP_ORDER = ["Medical", "Health", "Real Estate", "Finance", "Legal", "Fitness", "Hospitality", "Retail Showrooms",
               "Automotive", "Construction", "Home Services", "Events", "Education", "Design", "YouTube", "Manual", "Imported"]


def seeds_for(target=None):
    """One Australian spreadsheet, including Australian creators."""
    if target and target.upper() != "AU":
        raise ValueError("Only Australia is supported")
    return LOCATIONS, NICHES

# Current operational schema. Historical columns remain in existing sheets.
CORE_LEAD_H = ["Company Name", "Decision Maker Full Name", "Decision Maker Role", "Personal Email",
    "Generic Email", "Other Emails", "Email Verification", "Phone Number", "Decision Maker LinkedIn",
    "Company LinkedIn", "Instagram", "Facebook", "TikTok", "YouTube", "Website", "Niche", "City", "State",
    "Country", "Full Address", "ABN", "Business Status", "Company Size", "Business Type", "ICP Score", "ICP Tier",
    "Budget Fit", "Estimated Monthly Service Fit AUD", "Budget Evidence", "Decision Maker Confidence",
    "Social Media Opportunity", "Social Media Activity", "Lead Insight", "Suggested Service", "Suggested Outreach Angle",
    "Source", "Source URL", "Last Verified", "Date Added", "Outreach Status", "Last Contacted", "Next Follow Up",
    "Notes", "Unique Lead ID", "Australia Evidence", "Research Reasons", "Role Evidence", "Email Sources",
    "Email Verification Details", "Contact Records"]
LEAD_H = CORE_LEAD_H + [h for h in LEAD_H if h not in CORE_LEAD_H and h not in ("First Name", "Decision Maker Name")]
STATUS_LIST = ["New", "Research Needed", "Ready For Review", "Approved", "Contacted", "Follow Up 1", "Follow Up 2",
    "Follow Up 3", "Follow Up 4", "Replied", "Meeting Booked", "Proposal Sent", "Client", "Not Interested", "Do Not Contact"]
_OVERRIDES = {"YT_ALLOW_UNKNOWN_COUNTRY": "No", "YT_COUNTRIES": "AU", "YT_SOURCE": "api",
    "REQUIRE_WEBSITE_OSM": "No", "REQUIRE_CONTACT": "research", "MIN_REVIEWS": 0, "MAX_REVIEWS": 100000,
    "REJECT_R_BELOW": 0, "CHAIN_LOCATIONS": 10, "TIMEZONE": "Australia/Sydney", "PITCH_CURRENCY": "AUD"}
SETTINGS = [(k, _OVERRIDES.get(k, v), why) for k,v,why in SETTINGS if not k.startswith("REVIEW_FACTOR_") or k == "REVIEW_FACTOR_AU"]
SETTINGS += [("ICP_WEIGHT_AUTHORITY",30,"Authority evidence weight out of 100"),
    ("ICP_WEIGHT_CAPACITY",25,"Estimated commercial capacity evidence weight"),
    ("ICP_WEIGHT_OPPORTUNITY",20,"Observed social opportunity weight"),
    ("ICP_WEIGHT_CONTACT",15,"Sourced business email quality weight"),
    ("ICP_WEIGHT_INDUSTRY",10,"Relevant independent business weight"),
    ("ICP_TIER_A_MIN",85,"Priority A threshold; mandatory evidence gates still apply"),
    ("ICP_TIER_B_MIN",70,"Qualified B threshold; missing evidence goes to research"),
    ("ALLOW_WEBSITE_RESEARCH","Yes","Read permitted official business pages; respect robots and site terms"),
    ("ALLOW_LICENSED_DISCOVERY","No","Enable licensed Google/Serper/Apify discovery only after checking storage and export rights")]

LEAD_H += [h for h in ("Australia Evidence Source", "Qualification Gates", "Company ID", "ABN Source", "Budget Confidence",
    "Authority Score", "Capacity Score", "Opportunity Score", "Contact Score", "Industry Score", "Export Permission",
    "Export License Evidence") if h not in LEAD_H]
INPUT_H += ["Source URL", "Decision Maker Full Name", "Decision Maker Role", "Name Source", "Email Source",
    "Personal Email", "Social Opportunity", "Social Evidence", "Social Source", "Commercial Evidence JSON"]

# Additional supported niches share the same evidence gates; niche budgets are planning, not company facts.
_NEW_NICHES = [
    ("Independent Real Estate Agents", "Real Estate", "real estate agent; independent selling agent", "office=estate_agent", 1, "Property marketing videos"),
    ("Premium Vehicle Detailing", "Automotive", "ceramic coating; premium car detailing", "shop=car_repair; amenity=car_wash", 1, "Vehicle transformation reels"),
    ("Commercial Fitout Companies", "Construction", "commercial fitout; office fitout", "craft=builder; office=architect", 2, "Project showcase videos"),
    ("Bridal Boutiques", "Retail Showrooms", "bridal boutique; wedding dresses", "shop=clothes + clothes=wedding", 2, "Bridal collection reels"),
    ("Luxury Tourism Experiences", "Hospitality", "luxury tour; private charter; premium experience", "tourism=attraction; shop=travel_agency", 3, "Experience showcase videos"),
    ("Photography Studios", "Design", "commercial photography studio; wedding photographer", "craft=photographer", 3, "Portfolio and behind the scenes reels"),
    ("Event Production Companies", "Events", "event production; corporate events", "office=company + name~event|production", 3, "Event highlight videos"),
    ("Independent Personal Trainers", "Fitness", "personal training business; personal trainer", "leisure=fitness_centre", 3, "Training education videos"),
    ("Coaching and Consulting Businesses", "Education", "business coach; independent consultant", "office=consulting", 3, "Expert education videos"),
    ("Independent Financial Educators", "Finance", "financial educator; financial education business", "office=educational_institution", 3, "Reviewed financial education videos"),
    ("Premium Pet Services", "Home Services", "premium pet grooming; dog grooming studio", "shop=pet_grooming", 3, "Pet care and service reels"),
    ("Independent Ecommerce Brands", "Retail Showrooms", "Australian ecommerce brand; independent product brand", "shop=clothes; shop=cosmetics", 3, "Product showcase videos"),
]
_NICHES = [n for n in _NICHES if n[1] != "YouTube" or n[0].startswith("YouTube Creators AU")]
for title, group, terms, selector, priority, service in _NEW_NICHES:
    _NICHES.append((title, group, "Yes", terms, 4 if priority < 3 else 3, 500, 1000,
        "{name}: review the sourced social opportunity before proposing a service.",
        "Recurring customer acquisition can support creative services; verify company-specific commercial evidence."))
    OSM_SEL[title] = selector
_EXTRA_NICHE_H = ["Priority", "Applicable Locations", "Typical Service Opportunity", "Ideal Decision Maker",
    "Social Media Use Cases", "Exclusion Criteria", "Qualification Threshold", "Suggested Service Angle"]
NICHES_H += _EXTRA_NICHE_H
_EXTRA_BY_NAME = {n[0]: (n[4], n[5]) for n in _NEW_NICHES}
def _niche_metadata(n):
    priority, service = _EXTRA_BY_NAME.get(n[0], (1 if n[4] >= 4 else 2, "Monthly social video and content production"))
    return (priority, "All Australian states and territories", service,
            "Owner, founder, principal or evidenced purchasing authority", "Showcase services and educate prospective customers",
            "Overseas, closed, major corporate, suppressed, or insufficient evidence", 70,
            "Discuss a specific sourced opportunity; budget is an estimate")
NICHES = [tuple(n) + (OSM_SEL.get(n[0], ""),) + tuple(NICHE_RULES.get(n[0], ("", "", "", "")))
          + _Q.niche_defaults(n[0], "AU") + _niche_metadata(n) for n in _NICHES]
# Eliminate unsupported promises from control-panel help.
SETTINGS = [(k,v,("Legacy compatibility score; use ICP Score and ICP Tier for qualification" if k == "MIN_ICP_SCORE" else
                 "API-only authorized YouTube discovery; imports remain available" if k == "YT_SOURCE" else
                 "Australian channels only; unknown location remains research" if k in ("YT_COUNTRIES", "YT_ALLOW_UNKNOWN_COUNTRY") else
                 "Legacy contact filter; incomplete contacts now enter Research Queue" if k == "REQUIRE_CONTACT" else why))
            for k,v,why in SETTINGS if not k.startswith("USE_NZBN") and not k.startswith("MAX_NZBN")]
OPTIONS = [o for o in OPTIONS if "NZBN" not in o[1]]
PLAYBOOK = ["AUSTRALIAN PROSPECTING", "Review sourced decision-maker full names and emails first.",
    "A/B requires Australian operation, purchasing authority, commercial capacity evidence, social presence and opportunity, and a usable sourced email.",
    "Missing evidence goes to Research Queue. Never infer a name or email or promise a budget.",
    "500–1000+ AUD per month is estimated service fit, never confirmed willingness to buy.",
    "Public contact details do not establish permission to send. Review consent, suppression and provider data rights.",
    "No automated sending is implemented. Preserve statuses and notes in Outreach Leads."]
