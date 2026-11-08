"""Additive operational views for gspread and LocalSheet; no message sending."""
from __future__ import annotations

import hashlib
import json
import random
import time
import weakref
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

import gspread
from gspread.utils import rowcol_to_a1, a1_to_rowcol
from requests.exceptions import Timeout, ConnectionError as RequestConnectionError
from qualification import _authoritative_role

MANUAL = {'Outreach Status', 'Status', 'Owner Notes', 'My Notes', 'Notes', 'Last Contact Date',
          'Last Contacted', 'Next Follow Up', 'Reply Status', 'Suggested Monthly Fee', 'Opt Out',
          'Export Permission', 'Export License Evidence'}
COMMON = ['Company ID', 'Company Name', 'Decision Maker Full Name', 'Decision Maker Role',
          'Personal Email', 'Generic Email', 'Other Emails', 'Email Verification', 'Phone Number',
          'Decision Maker LinkedIn', 'Company LinkedIn', 'Instagram', 'Facebook', 'TikTok', 'YouTube',
          'Website', 'Niche', 'City', 'State', 'Country', 'Full Address', 'ABN', 'ICP Score', 'ICP Tier',
          'Budget Fit', 'Budget Evidence', 'Social Media Opportunity', 'Name Source', 'Role Evidence',
          'Source', 'Source URL', 'Email Source', 'Australia Evidence', 'Australia Evidence Source',
          'Last Verified Date', 'Date Added', 'Eligibility', 'Research Reason', 'Opt Out',
          'Export Permission', 'Export License Evidence', 'Last Synced', 'Place Id', 'Map Url',
          'Qualification Gates', 'Research Reasons', 'Budget Confidence', 'Estimated Monthly Service Fit AUD',
          'Contact Records', 'Email Sources', 'Email Associations JSON', 'Email Verification Details']
# New company tabs follow the brief's working order; existing columns never move.
PREFERRED = ['Company Name', 'Decision Maker Full Name', 'Decision Maker Role', 'Personal Email',
    'Generic Email', 'Other Emails', 'Email Verification', 'Phone Number', 'Decision Maker LinkedIn',
    'Company LinkedIn', 'Instagram', 'Facebook', 'TikTok', 'YouTube', 'Website', 'Niche', 'City', 'State',
    'Country', 'Full Address', 'ABN', 'Business Status', 'Company Size', 'Business Type', 'ICP Score',
    'ICP Tier', 'Budget Fit', 'Estimated Monthly Service Fit AUD', 'Budget Evidence',
    'Decision Maker Confidence', 'Social Media Opportunity', 'Social Media Activity', 'Lead Insight',
    'Suggested Service', 'Suggested Outreach Angle', 'Source', 'Source URL', 'Last Verified Date',
    'Date Added', 'Outreach Status', 'Last Contacted', 'Next Follow Up', 'Notes', 'Company ID']
COMMON = PREFERRED + [h for h in COMMON if h not in PREFERRED]
OUTREACH = ['Contact ID'] + COMMON + ['Outreach Status', 'Last Contact Date', 'Next Follow Up',
           'Reply Status', 'Owner Notes', 'Suggested Service', 'Suggested Monthly Fee', 'Lead Priority']
SCHEMAS = {
    'All Leads': COMMON,
    'Decision Makers': ['Contact ID'] + COMMON + ['Job Title', 'Verification Status', 'Primary Contact'],
    'Outreach Leads': OUTREACH,
    'Research Queue': COMMON + ['Research Status', 'Owner Notes'],
    'API Health': ['Provider', 'Status', 'Checked At', 'Requests Used', 'Requests Remaining', 'Error'],
    'Email Events': ['Event ID', 'Company ID', 'Message ID', 'Event Type', 'Sent At UTC', 'Source'],
    'Exports': ['Export ID', 'Created At', 'Kind', 'Format', 'Rows', 'Destination', 'Status'],
    'Operational Runs': ['Run ID', 'Started At', 'Finished At', 'Status', 'Added', 'Evaluated', 'Rejected', 'Error', 'Next Scheduled Run'],
    'Suppression': ['Company ID', 'Contact ID', 'Email', 'Domain', 'Reason'],
}
RESERVED = set(SCHEMAS) | {'Dashboard', 'All Leads', 'Options', 'Rules', 'Niches', 'Locations',
    'Searches', 'Seen', 'Run Log', 'Blacklist', 'Playbook', 'Leads Input', 'Meta', 'Stats',
    'US Stats', 'Master Dashboard', 'Keys', 'API Keys', 'Free Tools', 'Legacy All Leads'}
ALIASES = {'Decision Maker Full Name': ['Decision Maker Name'], 'Phone Number': ['Phone'],
    'ICP Tier': ['Tier'], 'Email Verification': ['Email Verified'], 'Source': ['Data Source'],
    'Date Added': ['Lead Added On'], 'Last Verified Date': ['Last Verified', 'Last Checked'],
    'Company LinkedIn': ['LinkedIn'], 'Social Media Opportunity': ['Social Gap'],
    'Budget Fit': ['Estimated Budget Fit'], 'Australia Evidence Source': ['Location Source']}


_SETUP_SIGNATURES = weakref.WeakKeyDictionary()


def retry_io(call, attempts=4):
    """Bounded retries for transient transport/API failures; permanent errors propagate."""
    for attempt in range(attempts):
        try:
            return call()
        except (gspread.exceptions.APIError, Timeout, RequestConnectionError, TimeoutError) as error:
            response = getattr(error, 'response', None)
            code = getattr(response, 'status_code', None)
            transient = isinstance(error, (Timeout, RequestConnectionError, TimeoutError)) or code in {429, 500, 502, 503, 504}
            if not transient or attempt == attempts - 1:
                raise
            try:
                retry_after = float((getattr(response, 'headers', {}) or {}).get('Retry-After', 0))
            except (TypeError, ValueError):
                retry_after = 0
            time.sleep(min(15, max(retry_after, 2 ** attempt + random.uniform(0, .5))))


class _WorksheetIO:
    """Per-operation snapshot; never reuse manual-cell data across sync calls."""
    def __init__(self, raw):
        self.raw = raw
        self.grid = None
        self.header = None

    def __getattr__(self, name):
        attribute = getattr(self.raw, name)
        if not callable(attribute):
            return attribute
        def call(*args, **kwargs):
            result = retry_io(lambda: attribute(*args, **kwargs))
            if name == 'batch_clear':
                self.grid = None
                self.header = None
            return result
        return call

    def get_all_values(self, **kwargs):
        if kwargs:
            try:
                return retry_io(lambda: self.raw.get_all_values(**kwargs))
            except TypeError:
                return retry_io(self.raw.get_all_values)
        if self.grid is None:
            self.grid = retry_io(self.raw.get_all_values)
        return [list(row) for row in self.grid]

    def row_values(self, number):
        if number == 1 and self.header is not None:
            return list(self.header)
        grid = self.get_all_values()
        values = list(grid[number - 1]) if number <= len(grid) else []
        while values and not text(values[-1]):
            values.pop()
        return values

    def _patch(self, values, range_name='A1'):
        row, col = a1_to_rowcol(range_name.split(':')[0])
        if self.grid is not None:
            for i, cells in enumerate(values):
                while len(self.grid) < row + i:
                    self.grid.append([])
                target = self.grid[row + i - 1]
                for j, value in enumerate(cells):
                    while len(target) < col + j:
                        target.append('')
                    target[col + j - 1] = text(value)
        if row == 1:
            if self.header is None:
                self.header = []
            for j, value in enumerate(values[0] if values else []):
                while len(self.header) < col + j:
                    self.header.append('')
                self.header[col + j - 1] = text(value)

    def update(self, values, range_name='A1', **kwargs):
        result = retry_io(lambda: self.raw.update(values=values, range_name=range_name, **kwargs))
        self._patch(values, range_name)
        return result

    def batch_update(self, patches, **kwargs):
        if hasattr(self.raw, 'batch_update'):
            result = retry_io(lambda: self.raw.batch_update(patches, **kwargs))
        else:
            result = None
            for patch in patches:
                retry_io(lambda p=patch: self.raw.update(values=p['values'], range_name=p['range'], **kwargs))
        for patch in patches:
            self._patch(patch['values'], patch['range'])
        return result

    def append_rows(self, rows, **kwargs):
        # A fixed range is safe to retry after a response timeout; API append is not.
        grid = self.get_all_values()
        while grid and not any(text(value) for value in grid[-1]):
            grid.pop()
        return self.update(rows, rowcol_to_a1(len(grid) + 1, 1), **kwargs)

    def add_rows(self, number):
        target = self.raw.row_count + number
        return retry_io(lambda: self.raw.resize(rows=target))


class _SheetIO:
    def __init__(self, raw):
        self.raw = raw
        self.tabs = {ws.title: _WorksheetIO(ws) for ws in retry_io(raw.worksheets)}

    def worksheets(self):
        return list(self.tabs.values())

    def worksheet(self, title):
        # update_title may have renamed an existing handle.
        self.tabs = {ws.title: ws for ws in self.tabs.values()}
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, **kwargs):
        attempted = False
        def create():
            nonlocal attempted
            if attempted:
                existing = next((ws for ws in retry_io(self.raw.worksheets) if ws.title == title), None)
                if existing is not None:
                    return existing
            attempted = True
            return self.raw.add_worksheet(title=title, **kwargs)
        ws = _WorksheetIO(retry_io(create))
        ws.grid, ws.header = [], []
        self.tabs[title] = ws
        return ws

    def batch_update(self, body):
        return retry_io(lambda: self.raw.batch_update(body))

    def prime(self, titles, headers_only=False):
        titles = list(dict.fromkeys(title for title in titles if title in self.tabs))
        for start in range(0, len(titles), 100):
            chunk = titles[start:start + 100]
            ranges = ["'" + title.replace("'", "''") + "'" + ('!1:1' if headers_only else '') for title in chunk]
            response = retry_io(lambda: self.raw.values_batch_get(ranges, params={'valueRenderOption': 'FORMATTED_VALUE'}))
            blocks = response.get('valueRanges', [])
            if len(blocks) != len(chunk):
                raise RuntimeError('Incomplete Sheets batch read; refusing partial synchronization')
            for title, block in zip(chunk, blocks):
                grid = block.get('values', [])
                ws = self.tabs[title]
                ws.header = list(grid[0]) if grid else []
                if not headers_only:
                    ws.grid = [list(row) for row in grid]


def sheet_io(sh):
    return sh if isinstance(sh, _SheetIO) else _SheetIO(sh)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def text(value):
    return '' if value is None else str(value).strip()


def norm(value):
    return re.sub(r'\s+', ' ', text(value)).casefold()


def domain(value):
    value = text(value)
    return (urlsplit(value if '://' in value else 'https://' + value).hostname or '').lower().removeprefix('www.')


def stable_id(prefix, value):
    return prefix + hashlib.sha256(norm(value).encode()).hexdigest()[:20]


def company_id(row):
    return text(row.get('Company ID')) or stable_id('co_', domain(row.get('Website')) or
        '|'.join(norm(row.get(k)) for k in ('Company Name', 'City', 'State', 'Country')))


def contact_id(row):
    return text(row.get('Contact ID')) or stable_id('pe_', company_id(row) + '|' + norm(row.get('Decision Maker Full Name')))


def records(ws):
    grid = ws.get_all_values()
    return [{**dict(zip(grid[0], row)), '_sheet_row': number} for number, row in enumerate(grid[1:], 2) if any(text(v) for v in row)] if grid else []


def ensure(sh, title, headers):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=max(26, len(headers)))
    headers = list(dict.fromkeys(headers))
    old = ws.row_values(1)
    merged = old + [h for h in headers if h not in old]
    if merged != old:
        if len(merged) > ws.col_count:
            ws.resize(cols=len(merged))
        ws.update(values=[merged], range_name='A1', value_input_option='RAW')
    return ws


def normalized_grid(grid):
    result = []
    for cells in grid:
        cells = list(cells)
        while cells and not text(cells[-1]):
            cells.pop()
        result.append(cells)
    while result and not result[-1]:
        result.pop()
    return result


def backup(sh, ws):
    title = 'Backup ' + ws.title[:65] + ' Names v1'
    try:
        existing = sh.worksheet(title)
        if normalized_grid(existing.get_all_values()) != normalized_grid(ws.get_all_values()):
            raise RuntimeError('Existing name-migration backup differs; review before renaming ' + ws.title)
        return existing
    except gspread.WorksheetNotFound:
        values = ws.get_all_values()
        copy = sh.add_worksheet(title=title, rows=max(1000, len(values)), cols=max(26, ws.col_count))
        if values:
            copy.update(values=values, range_name='A1', value_input_option='RAW')
        verified = retry_io(copy.raw.get_all_values) if isinstance(copy, _WorksheetIO) else copy.get_all_values()
        if normalized_grid(verified) != normalized_grid(values):
            raise RuntimeError('Backup verification failed for ' + ws.title)
        return copy


def migrate_names(sh, ws):
    headers = ws.row_values(1)
    if 'Decision Maker Name' in headers and 'Decision Maker Full Name' not in headers:
        backup(sh, ws)  # Must succeed before changing the header. Historical First Name is retained.
        ws.update(values=[['Decision Maker Full Name']],
                  range_name=rowcol_to_a1(1, headers.index('Decision Maker Name') + 1), value_input_option='RAW')


def setup(sh):
    sh = sheet_io(sh)
    signature = frozenset(sh.tabs)
    if _SETUP_SIGNATURES.get(sh.raw) == signature:
        return
    sh.prime(list(sh.tabs), headers_only=True)
    sh.prime([ws.title for ws in sh.worksheets() if 'Decision Maker Name' in ws.row_values(1) or ws.title == 'All Leads'])
    # Preserve legacy spill/formula views whole before creating writable canonical data.
    try:
        all_leads = sh.worksheet('All Leads')
    except gspread.WorksheetNotFound:
        all_leads = None
    if all_leads is not None:
        try:
            raw = all_leads.get_all_values(value_render_option='FORMULA')
        except TypeError:  # LocalSheet intentionally has a smaller interface.
            raw = all_leads.get_all_values()
        # A legacy QUERY/ARRAYFORMULA owns its spill range; ordinary row formulas stay in place.
        formula_view = any(re.match(r'^=\s*(?:QUERY|ARRAYFORMULA|VSTACK|FILTER|SORT)\s*\(', text(v), re.I)
                           for row in raw[:2] for v in row)
        if formula_view:
            backup(sh, all_leads)
            try:
                sh.worksheet('Legacy All Leads')
            except gspread.WorksheetNotFound:
                all_leads.update_title('Legacy All Leads')
            else:
                raise RuntimeError('Legacy All Leads already exists; review before migrating formula view')
    existing_titles = {ws.title for ws in sh.worksheets()}
    for ws in sh.worksheets():
        if not ws.title.startswith('Backup ') and 'Company Name' in ws.row_values(1):
            migrate_names(sh, ws)
    for title, headers in SCHEMAS.items():
        ws = ensure(sh, title, headers)
        if title in existing_titles:
            continue
        ws.freeze(rows=1)
        ws.format('1:1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': .92, 'green': .94, 'blue': .96}})
    # Existing dashboards remain intact: append a namespaced snapshot table to the right.
    ensure(sh, 'Dashboard', [])
    sh.batch_update({'requests': [{'updateSheetProperties': {'properties':
        {'sheetId': sh.worksheet('Dashboard').id, 'index': 0}, 'fields': 'index'}}]})
    _SETUP_SIGNATURES[sh.raw] = frozenset(ws.title for ws in sh.worksheets())


def json_data(value, expected):
    if isinstance(value, expected):
        return value
    if not text(value):
        return expected()
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return expected()
    return parsed if isinstance(parsed, expected) else expected()


def normalize(row):
    result = dict(row)
    for internal, names, expected in (
        ('_people', ('Contact Records JSON', 'Contact Records'), list),
        ('_email_sources', ('Email Sources JSON', 'Email Sources'), dict),
        ('_email_associations', ('Email Associations JSON',), dict),
        ('_email_verification', ('Email Verification Details', 'Email Verification JSON'), dict),
    ):
        if not isinstance(result.get(internal), expected):
            value = next((result.get(name) for name in names if text(result.get(name))), '')
            result[internal] = json_data(value, expected)
    # Preserve recovery data in the canonical sheet before discarding in-memory metadata.
    for internal, column in (('_people', 'Contact Records'), ('_email_sources', 'Email Sources'),
                             ('_email_associations', 'Email Associations JSON'), ('_email_verification', 'Email Verification Details')):
        if result.get(internal):
            result[column] = json.dumps(result[internal], ensure_ascii=False, sort_keys=True)
    for target, sources in ALIASES.items():
        if not text(result.get(target)):
            result[target] = next((row[s] for s in sources if text(row.get(s))), '')
    evidence = result.get('Australia Evidence') or result.get('Location Evidence')
    if isinstance(evidence, dict):
        result['Australia Evidence'] = evidence.get('evidence', '')
        result['Australia Evidence Source'] = evidence.get('source', '')
    if not result.get('Email Source') and isinstance(result.get('_email_sources'), dict):
        result['Email Source'] = '; '.join(sorted({value if isinstance(value, str) else text(value.get('source')) for value in result['_email_sources'].values() if isinstance(value, (str, dict))}))
    result['Company ID'] = company_id(result)
    if text(result.get('Decision Maker Full Name')):
        result['Contact ID'] = contact_id(result)
    return result


def australian(row):
    """Require evidence, not a country label, search seed, or .au domain alone."""
    if norm(row.get('Country Code')) not in {'', 'au', 'aus'}:
        return False
    country = norm(row.get('Country'))
    if country and country not in {'au', 'aus', 'australia'}:
        return False
    if isinstance(row.get('Australia Evidence'), dict):
        row = normalize(row)
    explicit = text(row.get('Australia Evidence'))
    source = text(row.get('Australia Evidence Source'))
    if explicit and source and re.search(r'\b(australia|australian|NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\b', explicit, re.I):
        return True
    address = text(row.get('Full Address'))
    has_location = bool(re.search(r'\bAustralia\b', address, re.I) or
        re.search(r'\b(?:NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\s+\d{4}\b', address, re.I))
    return has_location and bool(source or text(row.get('Source URL')) or text(row.get('Map Url')) or text(row.get('Place Id')))


def suppressed(row, suppressions=()):
    if norm(row.get('Opt Out')) in {'yes', 'true', '1'} or norm(row.get('Outreach Status') or row.get('Status')) in {'do not contact', 'opt out', 'unsubscribed', 'not interested'}:
        return True
    emails = set(re.findall(r'[\w.+-]+@[\w.-]+', ' '.join(text(row.get(k)) for k in ('Personal Email', 'Generic Email', 'Other Emails')).lower()))
    if not row.get('Contact ID') and row.get('Decision Maker Full Name'):
        row = {**row, 'Contact ID': contact_id(row)}
    for item in suppressions:
        if any(text(item.get(k)) and norm(item[k]) == norm(row.get(k)) for k in ('Company ID', 'Contact ID')):
            return True
        if text(item.get('Value')) and norm(item['Value']) in {norm(row.get('Company Name')), norm(row.get('Place Id')), domain(row.get('Website')), *emails}:
            return True
        if norm(item.get('Email')) in emails or (text(item.get('Domain')) and domain(item['Domain']) == domain(row.get('Website'))):
            return True
    return False


def suppression_rows(sh):
    result = records(sh.worksheet('Suppression'))
    for title in ('All Leads', 'Decision Makers', 'Outreach Leads'):
        try:
            for row in records(sh.worksheet(title)):
                if suppressed(row):
                    result.append({'Company ID': row.get('Company ID', '')})
        except gspread.WorksheetNotFound:
            pass
    try:
        for item in records(sh.worksheet('Blacklist')):
            value = item.get('Place Id / Email / Domain / Business name', '')
            if value:
                result.append({'Value': value})
    except gspread.WorksheetNotFound:
        pass
    return result


def usable_emails(row):
    row = normalize(row)
    addresses = set(re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', ' '.join(text(row.get(k)) for k in ('Personal Email', 'Generic Email', 'Other Emails'))))
    sources = {norm(key): value for key, value in row['_email_sources'].items()}
    associations = {norm(key): value for key, value in row['_email_associations'].items()}
    verification = {norm(key): value for key, value in row['_email_verification'].items()}
    invalid = {'invalid', 'undeliverable', 'disposable', 'blocked'}
    result = []
    for address in addresses:
        evidence = sources.get(norm(address), '')
        if isinstance(evidence, dict):
            evidence = evidence.get('source') or evidence.get('url', '')
        association = associations.get(norm(address), {})
        source = evidence or (association.get('source') if isinstance(association, dict) else '')
        # Legacy source URL applies to the old row, never invents a person/email association.
        source = source or (row.get('Email Source', '') if not sources and not associations else '')
        status = verification.get(norm(address), row.get('Email Verification', 'unknown'))
        if isinstance(status, dict):
            status = status.get('status') or status.get('result') or status.get('verification', 'unknown')
        if re.search(r'https?://[^\s;]+', text(source)) and norm(status) not in invalid:
            result.append(address.lower())
    return sorted(result)


def reason(row):
    failures = []
    if row.get('Qualification Gates') == 'Failed':
        failures.append('Mandatory qualification gate failed')
    if not australian(row):
        failures.append('Australian operation evidence missing or conflicting')
    if not text(row.get('Decision Maker Full Name')) or not text(row.get('Name Source')):
        failures.append('Sourced decision-maker full name needed')
    if not text(row.get('Decision Maker Role')) or not text(row.get('Role Evidence')):
        failures.append('Purchasing authority evidence needed')
    if not text(row.get('Budget Evidence')):
        failures.append('Estimated capacity evidence needed')
    if not any(text(row.get(k)) for k in ('Instagram', 'Facebook', 'Company LinkedIn', 'TikTok', 'YouTube')):
        failures.append('Social presence evidence needed')
    if norm(row.get('Social Media Opportunity')) in {'', 'research needed', 'unknown', 'not measured'}:
        failures.append('Observed marketing opportunity needed')
    if not usable_emails(row):
        failures.append('Sourced business email needed')
    if norm(row.get('ICP Tier')).split(' ')[0] not in {'a', 'b'}:
        failures.append('Qualification review needed')
    return '; '.join(failures)


def upsert(ws, incoming, key, protected=MANUAL):
    """Read once; patch only changed generated cells, never write manual cells on existing rows."""
    headers = ws.row_values(1)
    existing = records(ws)
    index = {text(r.get(key)): (r.get('_sheet_row', i + 2), r) for i, r in enumerate(existing) if text(r.get(key))}
    patches, additions = [], []
    for item in incoming:
        ident = text(item.get(key))
        if not ident:
            continue
        if ident in index:
            number, old = index[ident]
            for col, name in enumerate(headers, 1):
                if name not in protected and name in item and text(item[name]) != text(old.get(name)):
                    patches.append({'range': rowcol_to_a1(number, col), 'values': [[item[name]]]})
        else:
            additions.append([item.get(h, '') for h in headers])
            index[ident] = (len(existing) + len(additions) + 1, item)
    if patches:
        if hasattr(ws, 'batch_update'):
            ws.batch_update(patches, value_input_option='RAW')
        else:
            for patch in patches:
                ws.update(values=patch['values'], range_name=patch['range'], value_input_option='RAW')
    if additions:
        if len(existing) + len(additions) + 1 > ws.row_count:
            ws.add_rows(len(additions) + 100)
        ws.append_rows(additions, value_input_option='RAW')


def read_leads(sh):
    result = []
    for ws in sh.worksheets():
        if ws.title in RESERVED or ws.title.startswith('Backup '):
            continue
        if 'Company Name' in ws.row_values(1):
            result.extend(records(ws))
    return result


def sync(sh, leads=None, run=None):
    sh = sheet_io(sh)
    setup(sh)
    operational = list(SCHEMAS) + ['Dashboard', 'Blacklist']
    sh.prime(operational)
    if any(set(headers) - set(sh.worksheet(title).row_values(1)) for title, headers in SCHEMAS.items()):
        _SETUP_SIGNATURES.pop(sh.raw, None)
        setup(sh)
        sh.prime(operational)
    if leads is None:
        candidates = [ws.title for ws in sh.worksheets() if ws.title not in RESERVED and not ws.title.startswith('Backup ')]
        sh.prime(candidates, headers_only=True)
        sh.prime([title for title in candidates if 'Company Name' in sh.worksheet(title).row_values(1)])
    stamp = now()
    company_ws = sh.worksheet('All Leads')
    stored = records(company_ws)
    old = []
    id_col = company_ws.row_values(1).index('Company ID') + 1
    for number, raw in enumerate(stored, 2):
        if not text(raw.get('Company Name')):
            continue
        row = normalize(raw)
        if not text(raw.get('Company ID')):
            company_ws.update(values=[[row['Company ID']]], range_name=rowcol_to_a1(raw.get('_sheet_row', number), id_col), value_input_option='RAW')
        old.append(row)
    companies = {r['Company ID']: r for r in old if r.get('Company ID')}
    # Alias lookup retains the persisted ID when a website or source ID is added later.
    aliases = {}
    def keys(row):
        return [v for v in (('domain:' + domain(row.get('Website'))) if domain(row.get('Website')) else '',
            ('place:' + text(row.get('Place Id'))) if text(row.get('Place Id')) else '',
            'name:' + '|'.join(norm(row.get(k)) for k in ('Company Name', 'City', 'State'))) if v]
    for row in old:
        for alias in keys(row):
            aliases[alias] = row['Company ID']
    people = {r['Contact ID']: r for r in records(sh.worksheet('Decision Makers')) if r.get('Contact ID')}
    incoming = read_leads(sh) if leads is None else list(leads)
    for raw in old + incoming:
        row = normalize(raw)
        if not text(row.get('Company Name')):
            continue
        cid = next((aliases[k] for k in keys(row) if k in aliases), row['Company ID'])
        row['Company ID'] = cid
        for alias in keys(row):
            aliases[alias] = cid
        previous = companies.get(cid, {})
        merged = dict(previous)
        merged.update({k: v for k, v in row.items() if text(v) and (k not in MANUAL or not text(previous.get(k)))})
        merged['Last Synced'] = stamp
        companies[cid] = merged
        candidates = [row] if text(row.get('Decision Maker Full Name')) else []
        for entry in row.get('_people') or []:
            if isinstance(entry, dict):
                entry = (entry.get('full_name') or entry.get('name') or entry.get('Decision Maker Full Name', ''),
                         entry.get('role') or entry.get('Decision Maker Role', ''),
                         entry.get('source') or entry.get('Name Source', ''))
            if isinstance(entry, (tuple, list)) and len(entry) >= 3 and text(entry[0]) and text(entry[2]):
                person_row = dict(row)
                person_row.update({'Decision Maker Full Name': entry[0], 'Decision Maker Role': entry[1],
                    'Name Source': entry[2], 'Role Evidence': f'{entry[1]} ({entry[2]})', 'Personal Email': ''})
                if not _authoritative_role(entry[1]) or re.search(r'former|ex-|retired|assistant|non.executive', text(entry[1]), re.I):
                    person_row['Role Evidence'] = ''  # A sourced job title alone is not purchasing authority.
                # Never copy the primary person's personal mailbox to another owner.
                for email, association in (row.get('_email_associations') or {}).items():
                    if isinstance(association, dict) and norm(association.get('name')) == norm(entry[0]) and association.get('source'):
                        person_row['Personal Email'] = email
                        person_row['Email Source'] = association['source']
                        break
                if norm(entry[0]) != norm(row.get('Decision Maker Full Name')):
                    candidates.append(person_row)
        for person_row in candidates:
            person_row['Contact ID'] = contact_id({**person_row, 'Contact ID': ''})
            person = dict(people.get(person_row['Contact ID'], {}))
            person.update({k: v for k, v in person_row.items() if text(v)})
            people[person_row['Contact ID']] = person
    suppressions = suppression_rows(sh)
    # Manual opt-outs in outreach persist and apply to every view and export.
    outreach_old = records(sh.worksheet('Outreach Leads'))
    for row in outreach_old:
        if suppressed(row):
            suppressions.append({'Company ID': row.get('Company ID', '')})
    for row in companies.values():
        row['Research Reason'] = reason(row)
        row['Eligibility'] = 'Suppressed' if suppressed(row, suppressions) else ('Research Needed' if row['Research Reason'] else 'Ready For Review')
    active = list(companies.values())
    contacts = []
    for person in people.values():
        company = companies.get(person.get('Company ID'), {})
        for field in ('Country', 'State', 'City', 'Full Address', 'Australia Evidence', 'Australia Evidence Source',
                      'Qualification Gates', 'ICP Tier', 'Budget Evidence', 'Social Media Opportunity', 'Business Status'):
            if field in company:
                person[field] = company[field]
        person['Eligibility'] = 'Suppressed' if suppressed(person, suppressions) else ('Research Needed' if reason(person) else 'Ready For Review')
        person['Last Synced'] = stamp
        contacts.append(person)
    outreach = []
    for row in active:
        item = dict(row)
        item['Contact ID'] = contact_id(item) if text(item.get('Decision Maker Full Name')) else item['Company ID']
        if not text(item.get('Outreach Status')):
            item['Outreach Status'] = item['Eligibility']
        outreach.append(item)
    upsert(sh.worksheet('All Leads'), active, 'Company ID')
    upsert(sh.worksheet('Decision Makers'), contacts, 'Contact ID')
    upsert(sh.worksheet('Outreach Leads'), outreach, 'Company ID')
    # Keep queue history, marking resolved rows rather than deleting manual notes.
    queue_ids = {r.get('Company ID') for r in records(sh.worksheet('Research Queue'))}
    queue = [{**r, 'Research Status': 'Open' if r['Eligibility'] == 'Research Needed' else 'Resolved'} for r in active if r['Eligibility'] == 'Research Needed' or r['Company ID'] in queue_ids]
    upsert(sh.worksheet('Research Queue'), queue, 'Company ID', MANUAL | {'Research Status'})
    if run is not None:
        status = run.get('status', 'unknown')
        if status == 'success' and run.get('added') == 0:
            status = 'no_qualifying_leads'
        record = {'Run ID': run.get('run_id') or stamp, 'Started At': run.get('started_at', stamp),
            'Finished At': run.get('finished_at', stamp if status != 'running' else ''), 'Status': status,
            'Added': run.get('added', ''), 'Evaluated': run.get('evaluated', ''), 'Rejected': run.get('rejected', ''),
            'Error': run.get('error', ''), 'Next Scheduled Run': run.get('next_scheduled_run', '')}
        upsert(sh.worksheet('Operational Runs'), [record], 'Run ID')
        health = [{h: p.get(h, p.get(h.lower().replace(' ', '_'), '')) for h in SCHEMAS['API Health']} for p in run.get('api_health', [])]
        for p in health:
            p['Checked At'] = p['Checked At'] or stamp
        upsert(sh.worksheet('API Health'), health, 'Provider')
    summary = dashboard(sh, active, contacts, stamp)
    return summary


def dashboard(sh, companies, contacts, stamp=None):
    sh = sheet_io(sh)
    stamp = stamp or now()
    au = [r for r in companies if australian(r) and r.get('Eligibility') != 'Suppressed']
    qualified = [r for r in au if r.get('Eligibility') == 'Ready For Review']
    runs = records(sh.worksheet('Operational Runs'))
    last = runs[-1] if runs else {}
    successful = [r for r in runs if r.get('Status') in {'success', 'no_qualifying_leads'}]
    collected = [r for r in successful if str(r.get('Added', '')).isdigit() and int(r['Added']) > 0]
    last_collection = collected[-1].get('Finished At', '') if collected else ''
    def parse(value):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except (ValueError, TypeError):
            return None
    current = parse(stamp)
    last_date = parse(last_collection)
    age = (current - last_date).total_seconds() / 86400 if last_date else None
    expected = parse(last.get('Next Scheduled Run', ''))
    overdue = bool(expected and current > expected + timedelta(hours=2) and last.get('Status') != 'running')
    statuses = Counter(norm(r.get('Outreach Status')) for r in records(sh.worksheet('Outreach Leads')) if australian(r) and not suppressed(r))
    def added_since(days):
        return sum(1 for r in au if parse(r.get('Date Added', '')) and parse(r['Date Added']) >= current - timedelta(days=days))
    # Sending is external. Count only explicit, sourced sent-event records, once per message.
    sent_events = {}
    for event in records(sh.worksheet('Email Events')):
        identity = text(event.get('Message ID') or event.get('Event ID'))
        sent_at = parse(event.get('Sent At UTC', ''))
        if identity and norm(event.get('Event Type')) == 'sent' and sent_at and sent_at <= current and text(event.get('Source')):
            sent_events.setdefault(identity, event)
    last_sent = max((event['Sent At UTC'] for event in sent_events.values()), key=lambda value: parse(value), default='')
    summary = {'Total Emails Sent': len(sent_events), 'Last Email Sent UTC': last_sent,
        'Email Tracking Basis': 'Recorded Email Events only; sending integration is not connected',
        'Snapshot At UTC': stamp, 'Total Companies': len(au),
        'Total Decision Makers': sum(1 for r in contacts if r.get('Company ID') in {c['Company ID'] for c in au}),
        'Qualified Leads': len(qualified), 'Priority A Leads': sum(norm(r.get('ICP Tier')).startswith('a') for r in qualified),
        'Priority B Leads': sum(norm(r.get('ICP Tier')).startswith('b') for r in qualified),
        'Research Queue Count': sum(r.get('Eligibility') == 'Research Needed' for r in companies),
        'Personal Emails Found': sum(bool(r.get('Personal Email')) for r in au),
        'Generic Emails Found': sum(bool(r.get('Generic Email')) for r in au),
        'Additional Emails Found': sum(bool(r.get('Other Emails')) for r in au),
        'Usable Contact Rate': round(sum(bool(usable_emails(r)) for r in au) / len(au), 4) if au else 0,
        'Owner Identification Rate': round(sum(bool(r.get('Decision Maker Full Name') and r.get('Name Source')) for r in au) / len(au), 4) if au else 0,
        'Leads With Instagram': sum(bool(r.get('Instagram')) for r in au),
        'Leads With LinkedIn': sum(bool(r.get('Company LinkedIn') or r.get('Decision Maker LinkedIn')) for r in au),
        'Leads With Phone Numbers': sum(bool(r.get('Phone Number')) for r in au),
        'Leads Added Past 24 Hours': added_since(1), 'Leads Added Past 7 Days': added_since(7),
        'Leads Added Past 30 Days': added_since(30),
        'Emails Verified': sum(norm(r.get('Email Verification')) in {'valid', 'deliverable', 'verified mailbox'} for r in au),
        'Emails Pending Verification': sum(norm(r.get('Email Verification')) not in {'valid', 'deliverable', 'verified mailbox', 'invalid', 'disposable', 'undeliverable'} for r in au),
        'Leads Contacted': statuses['contacted'], 'Replies Received': statuses['replied'],
        'Meetings Booked': statuses['meeting booked'], 'Clients Acquired': statuses['client'],
        'Businesses Evaluated Last Run': last.get('Evaluated', ''), 'Businesses Rejected Last Run': last.get('Rejected', ''),
        'Leads Added Last Run': last.get('Added', ''),
        'Last Workflow Attempt': last.get('Started At', ''),
        'Last Successful Workflow': successful[-1].get('Finished At', '') if successful else '',
        'Last Successful Lead Collection': last_collection, 'Current Workflow Status': last.get('Status', 'not_recorded'),
        'Most Recent Error': next((r.get('Error') for r in reversed(runs) if r.get('Error')), ''),
        'Next Scheduled Run': last.get('Next Scheduled Run', ''),
        'Days Since Last Successful Collection': round(age, 2) if age is not None else 'Unknown',
        'Data Freshness': 'Unknown' if age is None else ('Stale' if age > 3 else 'Current'),
        'Schedule Health': 'Expected run overdue' if overdue else ('No overdue run detected' if expected else 'Schedule unknown')}
    ws = sh.worksheet('Dashboard')
    headers = ws.row_values(1)
    marker = 'Operations Snapshot v1'
    col = headers.index(marker) + 1 if marker in headers else max(1, max([0] + [len(r) for r in ws.get_all_values()]) + 3)
    values = [[marker, 'Value']] + [[k, v] for k, v in summary.items()]
    values += [['Companies By Niche', 'Count']] + [[k, v] for k, v in sorted(Counter(r.get('Niche', 'Unknown') for r in au).items())]
    values += [['Companies By Source', 'Count']] + [[k, v] for k, v in sorted(Counter(r.get('Source') or 'Unknown' for r in au).items())]
    values += [['Companies By Location', 'Count']] + [[k, v] for k, v in sorted(Counter((r.get('City', '') + ' ' + r.get('State', '')).strip() or 'Unknown' for r in au).items())]
    values += [['Collection Trend UTC', 'Added']] + [[r.get('Finished At', ''), r.get('Added', '')] for r in runs[-30:]]
    if len(values) > ws.row_count:
        ws.add_rows(len(values) - ws.row_count)
    if col + 1 > ws.col_count:
        ws.resize(cols=col + 1)
    # Clear only the owned snapshot columns, preventing stale rows after categories shrink.
    ws.batch_clear([f'{rowcol_to_a1(1, col)}:{rowcol_to_a1(ws.row_count, col + 1)}'])
    ws.update(values=values, range_name=rowcol_to_a1(1, col), value_input_option='RAW')
    ws.freeze(rows=1)
    return summary
