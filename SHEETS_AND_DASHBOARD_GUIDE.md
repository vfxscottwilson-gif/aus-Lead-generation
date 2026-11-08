# Sheets And Dashboard Guide

## Connect the spreadsheet

Use one `SHEET_ID` and the `GOOGLE_SA` service account configured by the main setup guide. Share that spreadsheet with the service account as an Editor. Run the bot setup command before collection. The operational layer accepts the same gspread spreadsheet object as the collector and works offline with `LocalSheet`.

No code here sends emails, approves outreach, publishes data, or creates public artifacts.

## Tabs and ownership

| Tab | Purpose |
| --- | --- |
| Dashboard | Stored-data counts, run outcome, collection freshness, niche/source/location totals and the last 30 run counts. |
| Niche tabs | Collector-owned leads, discovered by the `Company Name` header. |
| All Leads | One canonical record per stable Company ID, with qualification and evidence. |
| Decision Makers | One person per company and full name, linked by Company ID and Contact ID. Multiple owners remain separate. |
| Outreach Leads | One primary company contact with manual status, follow-up dates, reply status, notes and review fields. |
| Research Queue | Incomplete or geographically unverified records. Historical queue rows remain available after resolution; use Eligibility to filter current work. |
| Operational Runs | Append/update log keyed by Run ID. Separate from the collector's existing Run Log. |
| API Health | Latest reported provider status and counters. Blank remaining quota means unknown. |
| Suppression | Company ID, Contact ID, email or domain exclusions. |
| Exports | Private export audit with timestamp, kind, format, count and filename. No contact values or full destination paths. |
| Blacklist | Existing collector blacklist, also honored by operations and exports. |

Existing Niches, Locations, Rules, Searches, Seen, Run Log, Options and Playbook remain collector-owned.

## Safe migration

`operations.setup(sh)` appends missing operational columns; it never clears lead tabs or deletes historical rows. Before renaming `Decision Maker Name` to `Decision Maker Full Name`, it copies the entire original tab into `Backup <Tab Name> Names v1` and verifies the copied values. A failed or conflicting backup stops the rename. Repeating setup does not create another backup or another canonical name column.

For a legacy formula-backed All Leads view, setup first verifies a value backup, then retains the complete original worksheet as Legacy All Leads and creates a new writable All Leads. Existing ordinary All Leads rows receive stable IDs in place; manual cells and historical duplicates are retained. New rows are deduplicated, and counts/exports deduplicate existing IDs. Other tabs referring to the renamed legacy view may continue referring to it; review those formulas on your private copy.

Historical `First Name` columns remain as legacy data to avoid losing information or shifting formulas. No new first-name or last-name columns are created, and first names are never expanded into guessed full names. If both old and canonical full-name columns already exist, both stay intact; the canonical value is preferred, with the old full name used only when it is blank.

Backups contain the values returned by the Sheets API, not a full copy of formatting, formulas or revision history. Before changing unrelated formulas or deleting legacy columns, make a full Google Sheets copy yourself. This implementation does not delete those columns.

Foreign or geographically ambiguous records stay in historical niche tabs and the research view. They are excluded from active Australian counts and all exports. Review the reason before changing evidence. A country label, search city, or `.com.au` domain alone does not establish an Australian operation.

## Evidence and identifiers

Company IDs use a normalized website hostname where available, otherwise name and location. Persisted domain, place-ID and name/location aliases keep the existing company ID when enrichment adds a website. Multiple branches sharing a company domain consolidate. Contact IDs combine Company ID and normalized full name; adding an email does not change the person's ID. Different people are not deduplicated solely because they share a reception mailbox.

Review collisions manually when distinct unrelated businesses share a website or the same name/location. IDs are identifiers, not proof of legal entity identity. Persisted `Company ID` is authoritative. Names changed materially may need manual reconciliation.

All data writes use RAW input. Names, emails, phone numbers and notes beginning with formula characters are stored as data.

Readiness requires Australian location evidence, a sourced full name and role evidence, budget-fit evidence, social presence, a documented opportunity, a sourced business email, A/B qualification, and no failed mandatory gate. These checks supplement the collector's stricter qualification module. A full name is never inferred from an email. Sourced Other Emails can support contactability without becoming personal emails. Per-address Email Sources and Email Verification Details are retained; invalid addresses never count as usable just because another address has a source. Contact Records (and the compatible Contact Records JSON spelling) can recover secondary contacts after an interrupted batch. Email Sources JSON and Email Associations JSON are also accepted. Secondary specialist employee titles do not inherit the owner’s purchasing authority. An estimated capacity indicator is not a stated marketing budget or a confirmed buyer.

## Manual outreach controls

Edit `Outreach Status`, `Last Contact Date`, `Next Follow Up`, `Reply Status`, `Owner Notes` and `Suggested Monthly Fee` in Outreach Leads. Existing manual fields are never written during refresh. Supported operating labels are New, Research Needed, Ready For Review, Approved, Contacted, Follow Up 1ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Å“4, Replied, Meeting Booked, Proposal Sent, Client, Not Interested and Do Not Contact.

`Eligibility` is a separate computed field. Always review it together with the manual status: an old Approved status does not override new missing evidence or suppression. A ready record still needs lawful, personal review before outreach. A suggested fee is a proposal, never a verified client budget.

Set `Opt Out` to Yes or `Outreach Status` to Do Not Contact to exclude a company. Not Interested also suppresses it conservatively. Existing Blacklist entries and explicit Suppression entries are honored. The exporter checks current outreach opt-outs even if the latest operational sync has not run. Keep opt-outs in the durable suppression list if removing historical operational rows manually.

## Dashboard interpretation

The Python operational layer calculates snapshot values from stored canonical records, not the latest batch and not formula-based aggregate results. It appends an `Operations Snapshot v1` block to unused columns on the right of an existing dashboard, preserving its prior content. On a new spreadsheet this is a simple two-column report. Headers are neutral, and operational tables freeze row one. The Dashboard tab is moved first.

- Total Companies: distinct, evidenced Australian companies excluding suppressed records.
- Total Decision Makers: linked people for those companies; identification is not mailbox verification.
- Qualified Leads and A/B counts: currently ready records after mandatory evidence checks.
- Research Queue Count: current incomplete records, including unresolved location evidence. It is not a count of qualified Australian prospects.
- Email counts: records containing each mailbox category. Usable Contact Rate counts records with a sourced, non-invalid personal, generic or other business address; it does not claim every mailbox was verified.
- Owner Identification Rate: companies with a name and its source, divided by active Australian companies.
- Email verification counts: only explicit valid/deliverable/mailbox-verified results count as verified. Unknown or domain-only checks remain pending.
- Contacted, Replied, Meeting Booked and Client: current manual status counts, not cumulative event history.
- Added periods are rolling 24-hour, 7-day and 30-day windows in UTC. Missing/unparseable dates do not become invented additions.
- Niche, source and location totals reflect stored company coverage, not search-request coverage. Run counters remain blank when the collector did not report them.
- Collection trend lists actual recorded additions for up to 30 runs; no synthetic trend or projection is generated.

Run outcomes distinguish running, success with additions, no_qualifying_leads, failed and skipped. The collector must supply actual `added`, not candidate count. Last successful workflow and last successful collection are separate. A failed run cannot update the successful-collection timestamp.

Freshness becomes Stale more than three days after the last successful collection; it stays Unknown until a successful positive collection is recorded. If the caller supplies the next expected run, the dashboard marks it overdue after a two-hour grace period. Unknown schedules remain unknown. These snapshots refresh only when code runs. Independent workflow monitoring is needed to notice a run that never starts; a static spreadsheet cannot detect that on its own.

No charts, Apps Script, live recalculation or native chart rendering are claimed. The snapshot tables remain usable without these features. Real Google Sheets appearance and API quotas require a live smoke test.

## Private CSV and XLSX exports

Exports are explicit commands. Before exporting, review each intended row in its source operational tab and set:

1. `Export Permission` to `Yes`.
2. `Export License Evidence` to the actual reusable-data source/license rationale.

This authorizes export only. It does not authorize sending messages. Blank permission/evidence excludes the row. Only qualified, currently evidenced Australian rows are included; suppressed, duplicate and foreign records are excluded. Google/Maps/Places-sourced records are excluded even with generic permission: independently re-source and license the reusable record first. Place IDs and map links are not exported. A generic permission must never be used to bypass provider terms.

Examples, from the repository folder:

```powershell
python src/export_leads.py --kind companies --output "C:\Private Lead Exports\Companies.csv" --authorize-export
python src/export_leads.py --kind decision-makers --output "C:\Private Lead Exports\Decision Makers.xlsx" --authorize-export
python src/export_leads.py --kind outreach --output "C:\Private Lead Exports\Outreach.xlsx" --authorize-export
```

For an offline CSV-folder sheet, add `--local-dir "C:\Private Local Sheet"`. The command uses existing `GOOGLE_SA`/`SHEET_ID` access when `--local-dir` is omitted. Use a private destination appropriate for your account; the example path is illustrative. Existing files are never overwritten. Destinations anywhere inside a Git working tree are refused.

Companies exports have one row per company. Decision Makers exports have one row per sourced person, linked to its company. Outreach exports have the primary contact plus current manual review fields. Empty fields stay empty. CSV escapes formula-triggering prefixes; XLSX stores cells explicitly as text so Excel preserves phone plus signs and leading zeroes. CSV importers may infer types: import phone columns as text. CSV security apostrophes are intentional.

No export workflow uploads lead data or public artifacts. Keep exported contact data out of commits, screenshots, public downloads and workflow logs. A successfully created file followed by an audit-write failure will raise an error; inspect the private destination before retrying with a new filename.

## Integration contract

```python
import operations
operations.setup(sh)
operations.sync(sh, leads=batch)  # Include _people and _email_associations before discarding enrichment metadata.
summary = operations.sync(sh, run={
    'run_id': 'unique-workflow-attempt-id',
    'status': 'success',
    'started_at': '2026-11-07T00:00:00+00:00',
    'finished_at': '2026-11-07T00:05:00+00:00',
    'added': 4,
    'evaluated': 30,
    'rejected': 12,
    'error': '',
})
```

Without `leads`, sync scans niche tabs by `Company Name`, ignoring reserved and backup tabs. Empty `leads=[]` updates run/dashboard state without rereading niche tabs. API health is optionally passed as a list in `run['api_health']`, using provider/status/checked_at/requests_used/requests_remaining/error keys. Sanitize error text before passing it. Use a unique `run_id` to update running/final state in the same row. No status is invented when the caller does not pass a run.

Operational reads use one worksheet metadata request plus batched values reads. Setup is cached per spreadsheet instance and rechecked when tab names or required operational headers change. Manual values are reread for every sync. In the 45-niche regression fixture, a supplied candidate batch requires one batch read; a full rescan requires three. Transient 429, 500/502/503/504 and transport timeouts receive at most four attempts with exponential jitter and bounded Retry-After delays. Permanent errors and exhausted retries propagate. New records use deterministic range writes so a committed write followed by a response timeout cannot duplicate an append. All read/write failures propagate to the caller. The main pipeline should report failed synchronization rather than hide it behind a successful job. Serialize collector runs and avoid sorting operational tabs during an active write; read/patch updates are not a transactional database. LocalSheet users must call `sh.save()` to persist updated CSV files.

## Verification and remaining checks

30 offline LocalSheet regression tests passed with the shared Python 3.12 virtual environment. They cover backups and failed backups, repeated setup, stable company IDs, multiple owners and personal mailbox separation, manual status preservation, country evidence, failed qualification gates, blacklist/opt-out filtering, run outcomes/staleness, authorized CSV/XLSX export, Google-source exclusion, formula injection, and parseable XLSX XML. Assigned Python files also pass compileall.

Live Google Sheets permissions, formatting, API quotas, native Excel opening and real provider licensing have not been verified in this department. On a private spreadsheet copy, run setup twice, inspect name backups, collect one small batch, edit a manual status, sync again, confirm that status survived, add an opt-out, and confirm a private export excludes it. Then open a generated XLSX in Excel and inspect names, evidence and phone formatting. Never treat offline test success as proof of production collection or live API access.


## Email Events And Sent Totals

Email Events columns: Event ID, Company ID, Message ID, Event Type, Sent At UTC, Source. Import actual sender records or record sends manually. Dashboard counts sourced, valid past Sent events once per Message ID, falling back to Event ID, and shows Last Email Sent UTC. Drafts and unsourced events are excluded. No sender is connected automatically; the tracking basis is displayed explicitly. Run --finish or collection to refresh.
