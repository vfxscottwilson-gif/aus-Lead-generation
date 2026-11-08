# Workflow Guide

Run **Run AU Lead Bot** in GitHub Actions for normal collection. The repository uses one Google Spreadsheet, identified by `SHEET_ID`, and service-account JSON stored as `GOOGLE_SA`. Share that spreadsheet with the service account as Editor. Never place credentials in source files.

## Workflows

| File | Purpose | Trigger |
| --- | --- | --- |
| `.github/workflows/run-lead-bot.yml` | Preflight, setup, one collector, dashboard refresh | Manual and daily at 19:17 UTC |
| `.github/workflows/validate.yml` | Compile, selftest, unit tests and workflow regression tests without production secrets | Main pushes, pull requests, manual and reusable call from collector |
| `.github/workflows/health-check.yml` | Read-only GitHub API check for missing, stale, stuck or failed collector runs | Manual and daily at 23:43 UTC |

Both `.yml` and `.yaml` are valid GitHub Actions extensions. There is no redundant collector and no cloud export workflow. Use the documented local authorized exporter; lead exports are never uploaded as Actions artifacts.

## First Run

1. Put these workflows and source files on the repository's default branch. Enable GitHub Actions if disabled.
2. In repository Settings, Secrets and Variables, Actions, add `GOOGLE_SA` and `SHEET_ID`. Use the raw ID between `/d/` and `/edit`, not the full Sheet URL.
3. Add only optional provider secrets you actually have permission to use. Missing optional credentials disable those integrations. OpenStreetMap does not require a paid provider key.
4. Run **Validate Code** and fix any failing tests.
5. Open **Run AU Lead Bot**, select **Run Workflow**, and choose the default branch.
6. Review the private spreadsheet Dashboard, Run Log, API Health and newly added records. A green workflow alone does not prove leads were found.
7. Run **Check Collector Health** after the first successful collection. A new repository without collector history intentionally fails this check.

## Execution And Recovery

The collector first calls the reusable validation workflow without passing secrets. Only the subsequent collector job receives production credentials. Preflight checks the shape of core settings without printing their values; `--setup` verifies actual sheet authorization and initializes tabs. Collection uses `--no-finish`, followed by `--finish` even when collection fails after successful setup. Every failed production command returns a failing job; no continue-on-error hides failures.

The collection job allows 70 minutes overall. Setup and finish each have eight-minute command limits; collection has a fifty-minute outer limit with TERM then KILL after thirty seconds. Keep Rules `MAX_RUNTIME_MIN` below that limit (the original default is 42) so the application can save checkpoints before the outer deadline. Abrupt runner termination or a hard timeout can prevent cleanup; inspect private run status and locks before retrying.

A constant concurrency group `au-lead-bot` prevents collector workflows in this repository from overlapping, including manual runs on different branches. It does not coordinate separate repositories or local processes; avoid running a local writer simultaneously. GitHub may replace pending queued runs; concurrency is not a guaranteed durable work queue.

## Schedule And Monitoring

Edit the literal cron in `run-lead-bot.yml` to change cadence; GitHub cron cannot be supplied through repository variables. 19:17 UTC is 05:17 Sydney standard time or 06:17 during daylight saving. GitHub can delay or drop scheduled runs, and public repository schedules can be disabled after 60 days of inactivity. See [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

The health workflow is independent of collector validation and Google credentials. It checks default-branch scheduled/manual attempts, latest completed conclusion, a maximum two-hour queued/running age, and a recent successful workflow. `COLLECTOR_STALE_HOURS` is an optional repository variable, default 36, accepted range 2–168. Increase it when intentionally reducing collection frequency. It fails closed on GitHub API errors and prints a generic diagnostic without tokens or lead data.

This monitor checks workflow freshness, not lead yield or spreadsheet freshness. Inspect the private Dashboard's latest successful collection and lead counts separately. A scheduled health workflow on GitHub cannot detect a platform-wide outage or notify when all Actions schedules are disabled. For independent infrastructure monitoring, an authorized external scheduler must check GitHub run freshness or a private heartbeat. No such external service is configured by this repository. Configure your GitHub Actions notification preferences to receive failed-run alerts.

## Optional Provider Configuration

Supported workflow secrets include Gemini keys 1–3; Groq, OpenRouter, Mistral, Cerebras, OpenAI and Anthropic keys; Google Places; YouTube keys 1–2; Hunter; ZeroBounce; Serper; Brave; ZenRows; Jina; ABN GUID; Apify; Apollo; Instagram access token; Snov client ID and secret; Prospeo; and Reacher URL and secret. Existing combined `AI_API_KEYS`, `YOUTUBE_API_KEYS`, `PLACES_API_KEYS`, and `ABN_LOOKUP_GUID` remain available as compatibility inputs. `IG_USER_ID` and `USE_JINA` are repository variables. New Zealand business lookup is omitted from the Australian workflow.

`KEY_CHECK` defaults to `strict` in production. Set it to `warn` only when a legitimate provider key format is not yet recognized. This variable is deliberately absent from validation. Provider keys do not establish a license to store or export provider data; enable sources only within applicable terms.

## Logs And Privacy

Production Python stdout and stderr are discarded by the workflow because provider errors and legacy code can contain contact details or credentials. Public Actions output contains generic phase results only. No lead files, raw logs or environment dumps are uploaded. The private spreadsheet is the operational diagnostic destination. If setup fails before private logging is available, reproduce in a trusted local terminal and keep that output private. Review future logging changes before making them visible in a public repository.

## Local Validation

Use Python 3.12 and install requirements plus the test-only YAML parser:

```text
python -m pip install -r requirements-dev.txt
python -m compileall -q src tests
python src/selftest.py
python -m unittest discover -s tests -v
```

`tests/test_workflows.py` parses every workflow and exercises the actual embedded health-check Python with mocked GitHub responses. It covers missing/stale history, failure after an earlier success, stuck jobs, normal in-progress jobs, API errors, invalid thresholds, secret isolation, single-sheet configuration and no public artifacts. GitHub-hosted execution still requires live verification after installation.
