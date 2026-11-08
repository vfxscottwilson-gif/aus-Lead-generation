# Australia Lead Generation

Complete Australian prospect discovery, sourced contact research, qualification, Google Sheets dashboard and private authorized exports. Start with [00_START_HERE_SETUP_GUIDE.md](00_START_HERE_SETUP_GUIDE.md).

Use one SHEET_ID and GOOGLE_SA. Run Validate Code, then Run AU Lead Bot in GitHub Actions. The collector creates Dashboard, All Leads, Decision Makers, Research Queue, Outreach Leads and operational tabs. It preserves manual outreach state and does not send marketing emails.

Email Events supplies recorded sent-message totals and last-send timestamps. Your separate sender is not connected automatically. See the setup guide for its explicit event schema and refresh process.

See WORKFLOW_GUIDE.md, SHEETS_AND_DASHBOARD_GUIDE.md, TECHNICAL_AUDIT_REPORT.md and TEST_RESULTS.md for configuration, evidence limits and verification. Python 3.12 is required. All 186 local tests passed at packaging; live credentials and scheduled execution remain to be verified.
