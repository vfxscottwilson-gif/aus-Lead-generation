"""Carry explicitly sourced human/licensed research into the common pipeline."""
import json
from enrich import emails_in, public_source

FIELDS = {
    "Source URL": "source_url", "Decision Maker Full Name": "decision_maker_full_name",
    "Decision Maker Role": "decision_maker_role", "Name Source": "name_source",
    "Email Source": "email_source", "Personal Email": "personal_email",
    "Social Opportunity": "social_opportunity", "Social Evidence": "social_evidence",
    "Social Source": "social_source", "Commercial Evidence JSON": "commercial_evidence_json",
}


def attach(place, record):
    """Only explicit source-linked associations are carried; JSON never executes."""
    get = lambda label: str(record.get(label) or record.get(FIELDS[label]) or "").strip()
    source, name, role = get("Source URL"), get("Decision Maker Full Name"), get("Decision Maker Role")
    ns, es = get("Name Source"), get("Email Source")
    if public_source(source):
        place["_evidence_source"] = source
        place["googleMapsUri"] = place.get("googleMapsUri") or source
    if name and role and public_source(ns):
        place["_people"] = [(name, role, ns)]
    emails = emails_in(place.get("_email", "") + " " + get("Personal Email"))
    place["_email"] = ";".join(emails)
    if public_source(es):
        place["_email_sources"] = {e: es for e in emails}
        if name and public_source(ns):
            place["_email_associations"] = {e: {"name": name, "source": es}
                                            for e in emails_in(get("Personal Email"))}
    if public_source(get("Social Source")):
        place["_social_observations"] = {"source": get("Social Source"),
            "opportunity": get("Social Opportunity"), "evidence": get("Social Evidence")}
    raw = get("Commercial Evidence JSON")
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError as exc:
            raise ValueError("Commercial Evidence JSON must be a JSON list") from exc
        if not isinstance(entries, list):
            raise ValueError("Commercial Evidence JSON must be a list")
        place["_commercial_evidence"] = [e for e in entries if isinstance(e, dict) and
            e.get("kind") and e.get("evidence") and public_source(e.get("source"))]
    return place
