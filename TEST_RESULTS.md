# Test Results

7 November 2026. Python 3.12 on Windows. **186 tests passed**, with no failures or skips, in the latest combined repository run (5.424 seconds). Offline selftest: **0 failures**. Python compilation: passed.

## Commands

Install test dependencies with `python -m pip install -r requirements-dev.txt`, then run:

```text
python -m compileall -q src tests
python src/selftest.py
python -m unittest discover -s tests -v
```

The QA run used the shared `.venv\Scripts\python.exe`. Full diagnostics are retained in the shared work tracker. Main must rerun these commands against the frozen delivery archive if further source changes land.

## Coverage Verified Locally And With Mocks

- Australia-only seeds, foreign-target rejection, single SHEET_ID, CLI validation and failure propagation.
- Sourced full names and roles, explicit personal-mailbox association, generic/other email preservation, provider outages, domain versus mailbox verification, robots restrictions and optional-provider behavior.
- Mandatory qualification gates, editable weights, unknown budget/revenue, missing evidence and research routing, social measurement coverage and missing-website enrichment recovery.
- Append-only preservation, historical headers, backups, migration repetition, company/contact deduplication, manual outreach fields, opt-outs and suppression.
- Dashboard counts and run states, stale/failed/no-lead behavior, retry-safe writes, bounded retries and batch-read efficiency.
- Authorized Australian CSV/XLSX exports, licensing gates, formula safety, Git-destination refusal and export audit behavior.
- Workflow YAML, secret-free validation, one collector, serialized writes, cleanup/failure conditions and mocked independent stale-run monitoring.

The Sheets/export suite independently passed 30 tests, including operation when the test temporary directory is inside a repository. The production safeguard against writing exports into Git working trees remains intact. Tests resolve the operating-system temp directory before environment-clearing fixtures can change Python's cached default.

A separate 164-test verification earlier in integration passed with fake inherited provider credentials, an initially empty KEY_CHECK and a parent socket guard recording zero connection attempts. This substantiates test isolation; it is not a live provider test. Subprocess pipeline fixtures mock their own requests.

## Baseline And Reproduced CI Root Cause

Untouched baseline commit `22beefd4984bb227342f33a9781bcb1dba47f12f` passed its original 100 tests and selftest after dependencies were installed. With KEY_CHECK explicitly empty, three failures reproduced: AU pipeline website discovery, all-target routing and missing-sheet handling. Empty environment values defeated test `setdefault` calls, causing mock search keys and sheet IDs to be rejected. The historical Disify failure did not reproduce in the clean baseline; inherited provider credentials were a separate isolation risk.

Validation now receives no production secrets. Test fixtures isolate their environment and use explicit mock settings. Worldwide assertions were replaced by Australian rejection tests. Personal-email fixtures provide actual name/source associations; unknown values remain unknown. No assertions were skipped to obtain a passing result.

## Not Verified Live

Actual GitHub Actions schedules and notification delivery; Google service-account authorization and customer spreadsheet mutation; live provider APIs, quotas, billing and licenses; production collection yield; native Excel visual rendering; external infrastructure monitoring. Local mocks and syntax checks do not establish these outcomes.

## Release Verification

Main should install the packaged project in a clean environment, rerun all three commands, run Validate Code in GitHub, then authorize a backed-up test spreadsheet for one manual collector run. Inspect Dashboard, Run Log, API Health, sourced contacts, research routing and repeated migrations. Test private authorized exports using reusable fixture records. Confirm collector health after a successful run before relying on the daily schedule.


## Delivery Update — 8 November 2026

The requested Email Events ledger and dashboard Total Emails Sent / Last Email Sent UTC metrics are included. Only sourced, dated Sent records count, deduplicated by Message ID (or Event ID). No sending service is connected. The additional regression test verifies empty, duplicate, draft and unsourced events. Combined suite: 186 passed. Updated setup instructions replace obsolete sheet keys and matrix guidance.
