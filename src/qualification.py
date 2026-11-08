"""Evidence-based Australian ICP qualification; no network requests or invented budgets.

Scores are prioritisation estimates, never purchasing intent. All A/B gates apply
independently of editable weights. See DEPARTMENT_CONTACTS.md for input contract.
"""
import math
import re
from enrich import classify_emails, emails_in, public_source, _clean_name, au_state

WEIGHTS = {"AUTHORITY": 30, "CAPACITY": 25, "OPPORTUNITY": 20, "CONTACT": 15, "INDUSTRY": 10}
AUTHORITY = re.compile(r"\b(owner|founder|co-founder|managing director|managing partner|sole trader|self-employed)\b", re.I)


def _authoritative_role(role):
    """Specialist employee titles such as Art Director do not prove purchasing power."""
    role = str(role).strip()
    return bool(AUTHORITY.search(role) or re.fullmatch(
        r"(?:director|principal(?: dentist| solicitor| lawyer| architect| surgeon| designer| doctor| agent)?)", role, re.I))


def _number(v, default=0):
    try:
        n = float(str(v).replace(',', ''))
        return n if math.isfinite(n) else default
    except (ValueError, TypeError):
        return default


def _setting(settings, key, default):
    if settings is None:
        return default
    return _number(settings.num(key, default) if hasattr(settings, 'num') else settings.get(key, default), default)


def _evidence(value):
    return isinstance(value, dict) and bool(value.get('evidence')) and public_source(value.get('source'))


def evaluate(lead, settings=None):
    """Return new sheet fields from legacy lead columns and optional evidence maps.

    Required gates: sourced AU operation, commercial activity, authority, at least
    two distinct commercial capacity indicators, social presence and measured
    opportunity, sourced usable email, and no disqualification. No source means
    unknown, never verified. A/B thresholds default to 85/70.
    """
    l = lead
    reasons, rejected = [], []
    source = l.get('Source URL') or l.get('Map Url') or ''
    site = l.get('Website') or ''
    country = str(l.get('Country Code') or l.get('Country') or '').strip().casefold()
    if country and country not in ('au', 'aus', 'australia'):
        rejected.append('Outside Australia')
    location_ev = l.get('Australia Evidence') or l.get('Location Evidence')
    au = (country in ('au', 'aus', 'australia') and bool(l.get('Full Address'))
          and bool(au_state(l.get('State', ''))) and public_source(source))
    au = au or (l.get('_au_verified') is True and _evidence(location_ev))
    for field, label in (('_duplicate', 'Duplicate'), ('_closed', 'Closed business'),
                         ('_major_corporate', 'Major corporate or chain'), ('_suppressed', 'Do Not Contact')):
        if l.get(field) is True:
            rejected.append(label)
    if str(l.get('Business Status', '')).casefold() in ('closed', 'closed permanently', 'permanently_closed'):
        rejected.append('Closed business')
    if str(l.get('Outreach Status', '')).casefold() == 'do not contact':
        rejected.append('Do Not Contact')
    if not au:
        reasons.append('Verify Australian operation with a sourced address')

    people = list(l.get('_people') or [])
    name = l.get('Decision Maker Full Name') or l.get('Decision Maker Name') or ''
    role, name_source = l.get('Decision Maker Role', ''), l.get('Name Source', '')
    if name:
        people.insert(0, (name, role, name_source))
    credible, sourced_people = [], []
    for item in people:
        if not isinstance(item, (tuple, list)) or len(item) < 3:
            continue
        n, r, u = item[:3]
        clean = _clean_name(str(n))
        if clean and public_source(u):
            sourced_people.append((clean, str(r), str(u)))
        if clean and public_source(u) and _authoritative_role(r) and not re.search(r'former|ex-|retired|assistant|non.executive', str(r), re.I):
            credible.append((clean, str(r), str(u)))
    owner_operation = _evidence(l.get('_authority_evidence'))
    # A manager can qualify only with explicit sourced approval authority.
    if owner_operation and not credible:
        for item in people:
            if isinstance(item, (tuple, list)) and len(item) >= 3:
                n, r, u = item[:3]
                if _clean_name(str(n)) and public_source(u) and re.search(r'\bmarketing manager\b', str(r), re.I):
                    credible.append((str(n), str(r), str(u)))
                    break
    authority = bool(credible) or owner_operation
    if credible:
        name, role, name_source = credible[0]
    elif sourced_people:
        name, role, name_source = sourced_people[0]
    else:
        name, role, name_source = '', '', ''
    if not authority:
        reasons.append('Verify owner or purchasing authority')
    if not name:
        reasons.append('Research decision-maker full name')

    evidence = {}
    for item in l.get('_commercial_evidence') or []:
        if _evidence(item) and item.get('kind'):
            evidence[str(item['kind'])] = f"{item['evidence']} ({item['source']})"
    if _number(l.get('Review Count')) >= 20 and public_source(l.get('Map Url')):
        evidence['customer_demand'] = f"{l['Review Count']} published reviews ({l['Map Url']})"
    if _number(l.get('Business Age (yrs)')) >= 3 and public_source(l.get('ABN Source')):
        evidence['longevity'] = f"ABN active for {l['Business Age (yrs)']} years ({l['ABN Source']}); not proof of continuous trading"
    if l.get('Booking Tool') and public_source(site):
        evidence['booking'] = f"Booking system observed: {l['Booking Tool']} ({site})"
    # Tags alone do not establish paid advertising, revenue or purchasing intent.
    capacity = len(evidence) >= 2
    commercial = bool(evidence)
    if not capacity:
        reasons.append('Research financial capacity: at least two distinct sourced commercial indicators needed')
    independent = l.get('_independent') is True and _evidence(l.get('Independence Evidence'))
    # Named owner/principal of a commercial business is a conservative suitability indicator.
    suitable = commercial and (independent or authority) and bool(l.get('Niche') or l.get('Business Type'))
    if not suitable:
        reasons.append('Verify independent commercial business suitability')

    social_urls = [l.get(k) for k in ('Instagram', 'Facebook', 'TikTok', 'YouTube', 'Company LinkedIn', 'LinkedIn') if public_source(l.get(k))]
    obs = l.get('_social_observations') or {}
    obs = obs if isinstance(obs, dict) else {}
    opportunities, activity = [], []
    social_source = obs.get('source', '') if isinstance(obs, dict) else ''
    if public_source(social_source):
        if obs.get('posts30') is not None and obs.get('complete_window') is True and _number(obs.get('window_days')) >= 30:
            posts = _number(obs['posts30'], -1)
            if posts >= 0:
                activity.append(f'{posts:g} posts observed over 30 days ({social_source})')
                if posts < 4:
                    opportunities.append(f'Low observed posting frequency: {posts:g} posts over 30 days ({social_source})')
        if obs.get('reels_share') is not None and _number(obs.get('sample_size')) >= 5:
            share = _number(obs['reels_share'], -1)
            if 0 <= share < .2:
                opportunities.append(f"Short-form video represents {share:.0%} of {obs['sample_size']} sampled posts ({social_source})")
        if obs.get('opportunity') and obs.get('evidence'):
            opportunities.append(f"{obs['opportunity']}: {obs['evidence']} ({social_source})")
    social = bool(social_urls)
    if not social:
        reasons.append('Verify relevant professional social presence')
    if not opportunities:
        reasons.append('Research social opportunity; unavailable activity is not a weakness')

    email_sources = l.get('_email_sources') or {}
    associations = dict(l.get('_email_associations') or {})
    for address, association in list(associations.items()):
        if isinstance(association, str):
            associations[address] = {'name': association, 'source': email_sources.get(address, '')}
    all_emails = emails_in(' '.join(str(l.get(k) or '') for k in ('Personal Email', 'Generic Email', 'Other Emails', '_all')))
    personal, generic, others = classify_emails(all_emails, l.get('_domain', ''), name, associations=associations)
    status = str(l.get('Email Verification') or l.get('Email Verified') or 'unknown').lower()
    sourced = [e for e in all_emails if public_source(email_sources.get(e)) or
               (isinstance(associations.get(e), dict) and public_source(associations[e].get('source')))]
    # Legacy single Email Source is only accepted when it is an actual source URL.
    if public_source(l.get('Email Source')):
        sourced = all_emails
    usable = bool(sourced) and status not in ('invalid', 'undeliverable', 'disposable', 'blocked')
    if not usable:
        reasons.append('Find a sourced usable business email')
    if not personal:
        reasons.append('Research email demonstrably associated with decision-maker')

    fractions = {'AUTHORITY': 1 if credible else .8 if owner_operation else 0,
                 'CAPACITY': min(1, len(evidence) / 3),
                 'OPPORTUNITY': 1 if len(opportunities) >= 2 else .8 if opportunities else 0,
                 'CONTACT': 1 if personal and usable else .8 if generic and usable else .6 if usable else 0,
                 'INDUSTRY': 1 if suitable else .3 if commercial else 0}
    weights = {k: max(0, _setting(settings, 'ICP_WEIGHT_' + k, v)) for k, v in WEIGHTS.items()}
    total = sum(weights.values()) or 100
    parts = {k: round(100 * weights[k] * fractions[k] / total, 2) for k in weights}
    score = max(0, min(100, round(sum(parts.values()))))
    gates = au and authority and capacity and suitable and social and bool(opportunities) and usable
    a = max(85, min(100, _setting(settings, 'ICP_TIER_A_MIN', 85)))
    b = max(70, min(a, _setting(settings, 'ICP_TIER_B_MIN', 70)))
    tier = 'Rejected' if rejected else 'A — Priority' if gates and score >= a else 'B — Qualified' if gates and score >= b else 'C — Research Needed'
    if not rejected and score < b:
        reasons.append('Insufficient evidence score for qualification')
    return {'Decision Maker Full Name': name, 'Decision Maker Role': role, 'Name Source': name_source,
            'Role Evidence': f'{role} ({name_source})' if name else '',
            'Decision Maker Confidence': 'Sourced role; current authority requires review' if name else 'Owner operation documented' if owner_operation else 'Unknown',
            'Personal Email': personal, 'Generic Email': generic, 'Other Emails': ', '.join(others),
            'Email Verification': status, 'ICP Score': score, 'ICP Tier': tier,
            'Budget Fit': 'Estimated fit — not a confirmed budget' if capacity else 'Research Needed',
            'Budget Confidence': 'Moderate' if len(evidence) >= 3 else 'Low' if evidence else 'Unknown',
            'Estimated Monthly Service Fit AUD': '500–1000+ (estimate only)' if capacity else '',
            'Budget Evidence': '; '.join(evidence.values()),
            'Social Media Opportunity': '; '.join(opportunities) or 'Research Needed',
            'Social Media Activity': '; '.join(activity) or 'Not measured',
            'Research Reasons': '; '.join(dict.fromkeys(rejected + reasons)),
            'Qualification Gates': 'Passed' if gates and not rejected else 'Failed',
            'Suggested Service': 'Social video and content production — subject to review' if opportunities else '',
            'Suggested Outreach Angle': ('Discuss ' + opportunities[0]) if opportunities else '',
            **{k.title() + ' Score': v for k, v in parts.items()}}
