# Australia Lead Generation Setup Guide

## Install The Complete Project

1. Extract Australia Lead Generation Complete.zip. The Australia Lead Generation folder contains the full repository, including the hidden .github folder.
2. Back up your existing source and spreadsheet. Update the existing repository rather than deleting its history, settings and secrets. Remove obsolete collector workflows when replacing old source so only the supplied run-lead-bot.yml collects leads.
3. Upload all contents of Australia Lead Generation to the repository root on main, including src, tests, requirements files and .github/workflows. Do not upload a parent folder containing the project: GitHub must find .github at the repository root.
4. In GitHub Actions, run Validate Code. It installs requirements-dev.txt and runs compilation, selftest and unit tests without production secrets.
5. Add the two core repository Actions secrets: GOOGLE_SA (complete Google service-account JSON) and SHEET_ID (only the spreadsheet ID between /d/ and /edit). Never commit credential files.
6. Enable Google Sheets API for the service-account project. Share your backed-up spreadsheet with the service account's client_email as Editor.
7. Add optional provider secrets only if you use those licensed services. See WORKFLOW_GUIDE.md. Missing optional keys do not require paid integrations.
8. Run Actions > Run AU Lead Bot > Run Workflow on main. Inspect setup, collection and dashboard step outcomes.
9. Open the private Google Sheet. Dashboard is the main view; All Leads contains canonical company records, Decision Makers holds linked people, Research Queue holds incomplete evidence, and Outreach Leads preserves your manually maintained statuses and notes.
10. After a successful collection, run Check Collector Health. The daily collector schedule is 19:17 UTC. Scheduled runs can be delayed; see the workflow guide for timezone conversion and stale detection.

## Dashboard And Sending Metrics

Dashboard reports companies, qualified leads, research queue, contact coverage, outreach stages, last workflow attempt, last successful collection and freshness. It refreshes when the collector or --finish runs. It is a stored Google Sheets dashboard table and trend table, not a separate hosted website.

Total Emails Sent and Last Email Sent UTC come exclusively from Email Events. This collector does not send email or connect to your separate outreach sender. Until you import actual sending records, an empty event log means zero recorded sends, not proof that another tool sent nothing.

To log a real send, add a row to Email Events with Event ID, Company ID if known, Message ID, Event Type=Sent, Sent At UTC (ISO timestamp, for example 2026-11-08T07:30:00+00:00), and Source (the sender or manual log). Use a unique Message ID for each sent message; repeated imports of the same message count once. Drafts, future timestamps and records without a source are excluded. Review any sender integration before enabling automatic updates. Refresh with --finish or the next collection. Leads Contacted and pipeline stages reflect Outreach Leads status, separately from message counts.

## Local Commands

Use Python 3.12:

```text
python -m pip install -r requirements-dev.txt
python -m compileall -q src tests
python src/selftest.py
python -m unittest discover -s tests -v
python src/leadbot.py --local --setup
python src/leadbot.py --local
python src/leadbot.py --local --finish
```

For Google Sheets runs, configure GOOGLE_SA and SHEET_ID in your trusted environment and omit --local. A local collection can contact enabled public discovery sources; tests use mocks.

## If The Current Automation Crashes

Pause the old scheduled collector while installing the replacement. Do not run old and new writers against the same sheet simultaneously. This release passed local tests; your specific live crash has not been reproduced without its logs and credentials.

If validation fails, do not proceed to collection. If setup fails, check JSON content, raw spreadsheet ID, API enablement and sharing permissions. If collection fails, check the private Run Log, Operational Runs and API Health tabs. Public Actions output deliberately avoids raw contact/provider logs. A green action with zero qualified leads is a valid no-qualifying-leads outcome, not proof of a broken workflow. Missing evidence stays in Research Queue.

## Data And Exports

Existing notes, names and outreach fields are preserved through additive migrations with backups. Review migrations first on a spreadsheet copy. Qualified contacts require sourced Australian operation, authority, commercial evidence, social opportunity and usable contact; estimated capacity never means a confirmed budget.

Private CSV/XLSX export is manually authorized; see SHEETS_AND_DASHBOARD_GUIDE.md. No sending, push, deployment or live spreadsheet modification has been performed during packaging. Live Google Sheets authorization, provider behavior and actual GitHub Actions still require your first deployment check.
