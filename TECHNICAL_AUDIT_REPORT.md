# Technical Audit Report

7 November 2026. Audited baseline: `22beefd4984bb227342f33a9781bcb1dba47f12f`. The integrated Australian lead-generation system passes **186 automated tests** and its offline selftest. Live service verification remains outstanding.

## Architecture

The existing Python 3.12 CLI and discovery/enrichment modules were retained. One Australian spreadsheet supplies Rules, Niches, Locations and checkpoints. OpenStreetMap, authorized providers, imports and authorized YouTube API discovery feed website/contact research. Evidence-based qualification assigns priority or research status; additive spreadsheet operations maintain company/contact records, outreach state, stored dashboard metrics and safe authorized exports.

The main GitHub workflow validates without credentials, sets up the spreadsheet, runs one collector and refreshes the dashboard. A separate read-only workflow detects stale or failed collector runs. Contact exports stay private and manually authorized.

## Root Causes And Fixes

| Original Issue | Implemented Correction |
| --- | --- |
| Worldwide routing survived inside the AU edition; two competing sheet settings were exported. | Core uses one SHEET_ID and rejects foreign targets; Australian seeds cover all states and territories. |
| Production secrets were inherited by tests; empty KEY_CHECK rejected mock credentials. | Separate credential-free validation, explicit test settings and isolated provider environments. |
| Six matrix collectors repeated setup and fragmented one spreadsheet's work. | One serialized collector with bounded commands and explicit finish/failure steps. |
| Matching mailbox text could be treated as a personal contact without attribution. | Personal mail requires explicit sourced name association; generic and unrelated addresses remain separate. |
| Numerical scores could hide missing authority, location or opportunity evidence. | Mandatory qualification gates and transparent 100-point weights; incomplete candidates enter research. |
| Revenue and marketing weaknesses could be inferred from weak or missing measurements. | Unknown revenue/budget stays unknown; capacity is labeled an estimate; social observations require provenance and coverage. |
| Missing-site candidates could bypass website discovery under the new research defaults. | Core attempts website recovery before losing enrichment; a full-pipeline regression verifies sourced contact recovery. |
| Sheet persistence and finalization failures could appear successful. | Core persists safe run outcomes and surfaces critical finalization failures; workflow propagates nonzero status. |
| Historical data and manually maintained outreach fields needed protection. | Additive migrations, backups, stable company/contact IDs, protected manual fields, opt-outs and idempotent operations. |
| Retried writes could duplicate records or waste requests. | Deterministic ranges, bounded transient retries, batched reads and metadata caching with fresh manual-state reads. |
| Exports lacked sufficiently explicit operational controls. | Explicit authorization, Australian qualification and licensing gates, suppression checks, formula safety, Git-destination refusal and no public artifacts. |
| Missing schedules could leave stale status unnoticed. | Independent read-only Actions health check plus private dashboard freshness metrics. |

## Files And Responsibilities

Core changes cover `src/leadbot.py`, `defaults.py`, `market.py`, `importer.py` and `yt.py`. Contact/qualification changes cover `enrich.py`, `optional.py`, `quality.py`, `score.py`, `insight.py` and new `qualification.py`. New `operations.py` and `export_leads.py` implement spreadsheet operations and exports. Dependencies include `requirements-dev.txt` for reproducible validation.

Workflow changes replace the old matrix inside `run-lead-bot.yml` and add `validate.yml` and `health-check.yml`. Legacy tests and selftest now verify the Australian contract; added core, qualification, operations and workflow suites cover the new behavior. Setup, ICP, spreadsheet, workflow and test documentation describe the resulting implementation. No workflow was removed and no redundant collector/export-publication workflow was added. Main owns final packaging and the definitive release file inventory.

## Performance And Security

A single collector removes six repeated worker setups. Spreadsheet adapters batch operational/niche reads and cache metadata; manual state is reread each sync. Deterministic writes and bounded retry recovery prevent duplicate appends after response loss. Tests verify batching and recovery; production throughput has not been benchmarked.

Validation receives no production secrets. Workflows use read-only GitHub permissions and nonpersistent checkout credentials. Production command output is discarded from public logs, with safe generic outcomes shown instead. The health monitor has no Google credential access and never reads contacts. Website research respects robots and excludes direct social-platform scraping. No unrestricted email sending, deployment, push or public contact publication was performed by QA.

## Verification

Latest full combined run: **186 passed in 5.424 seconds**. Offline selftest: **0 failures**. Python compilation passed. Original baseline: **100 passed**; empty-KEY_CHECK behavior reproduced three historical failures. See TEST_RESULTS.md for commands, coverage and remaining live checks. Intermediate development failures are documented in shared department trackers rather than treated as release results.

## Limitations And Remaining Risks

Actual GitHub scheduling, notification delivery, customer-sheet authorization/migration and live provider behavior are unverified. Provider contracts and export rights require the operator's review. A public email is not consent to outreach. Sourced identities still need freshness checks; estimated capacity is never a confirmed budget.

The dashboard is a Python-refreshed stored-data table with collection trend rows, not a live chart. GitHub scheduling is best effort; a monitor hosted on GitHub cannot detect all platform outages or disabled schedules. Local writers and separate repositories require coordination beyond this repository's concurrency group. Abrupt termination can interrupt persistence. Main must rerun packaged-artifact checks after the release freeze; passing local tests do not justify an error-free guarantee.


## Delivery Update — 8 November 2026

The requested Email Events ledger and dashboard Total Emails Sent / Last Email Sent UTC metrics are included. Only sourced, dated Sent records count, deduplicated by Message ID (or Event ID). No sending service is connected. The additional regression test verifies empty, duplicate, draft and unsourced events. Combined suite: 186 passed. Updated setup instructions replace obsolete sheet keys and matrix guidance.
