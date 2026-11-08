"""Google Places API (New) Text Search wrapper with key rotation. Each HTTP request returns up to 20 businesses."""
import time

import requests

URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ",".join(["places.id", "places.displayName", "places.formattedAddress", "places.addressComponents",
                   "places.websiteUri", "places.nationalPhoneNumber", "places.internationalPhoneNumber",
                   "places.rating", "places.userRatingCount", "places.googleMapsUri", "places.businessStatus",
                   "places.primaryTypeDisplayName", "places.types", "places.priceLevel", "nextPageToken"])
# All of these are in the SAME "Text Search Enterprise" SKU (websiteUri/phone/rating/priceLevel are Enterprise fields),
# so priceLevel costs nothing extra. Free cap: 1,000 requests/month; then US$35 per 1,000 (Google pricing page, 2026-10).


class AllKeysExhausted(Exception):
    pass


class Places:
    def __init__(self, keys):
        self.keys = [k for k in keys if k]
        if not self.keys:
            raise RuntimeError("No PLACES_API_KEYS set.")
        self.dead, self.requests, self.notes = set(), 0, []

    def search(self, text, page_token=None, region="AU"):
        body = {"textQuery": text, "languageCode": "en", "pageSize": 20}
        if region:
            body["regionCode"] = region.upper()
        if page_token:
            body["pageToken"] = page_token
        for _ in range(len(self.keys) * 2 + 3):
            live = [k for k in self.keys if k not in self.dead]
            if not live:
                raise AllKeysExhausted("All Google Places keys are exhausted, invalid, or billing is not enabled.")
            key = live[0]
            try:
                r = requests.post(URL, json=body, timeout=30,
                                  headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS})
            except requests.RequestException:
                time.sleep(3)
                continue
            if r.status_code == 200:
                self.requests += 1
                return r.json()
            msg = ""
            try:
                msg = str(r.json().get("error", {}).get("message", ""))
            except ValueError:
                pass
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(6)
                if r.status_code == 429 and "quota" in msg.lower():
                    self.dead.add(key)
                    self.notes.append("A Places key hit its quota; trying the next key.")
                continue
            if r.status_code in (400, 401, 403):
                low = msg.lower()
                if any(w in low for w in ("api key", "billing", "not been used", "disabled", "permission", "referer", "ip address")):
                    self.dead.add(key)
                    self.notes.append("A Places key was rejected: " + msg[:110])
                    continue
            raise RuntimeError(f"Places API {r.status_code}: {msg[:120]}")
        raise RuntimeError("Places API kept failing.")
