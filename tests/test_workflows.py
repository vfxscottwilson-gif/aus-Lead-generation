"""Workflow security contracts and executable, offline health-monitor scenarios."""
import io
import json
import os
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def workflow(name):
    # BaseLoader follows string semantics for the GitHub `on` key (YAML 1.1
    # SafeLoader would otherwise treat that key as the boolean True).
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def inline_python(step):
    return step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


class WorkflowContracts(unittest.TestCase):
    def test_all_yaml_has_jobs_and_read_only_permissions(self):
        for path in WORKFLOWS.glob("*.y*ml"):
            doc = workflow(path.name)
            self.assertIsInstance(doc["jobs"], dict)
            self.assertTrue(doc["on"])
            self.assertTrue(all(v == "read" for v in doc["permissions"].values()))

    def test_one_collector_with_shared_concurrency_and_no_matrix(self):
        doc = workflow("run-lead-bot.yml")
        self.assertEqual(doc["concurrency"], {"group": "au-lead-bot", "cancel-in-progress": "false"})
        self.assertEqual(len(doc["on"]["schedule"]), 1)
        self.assertIn("workflow_dispatch", doc["on"])
        self.assertNotIn("strategy", doc["jobs"]["collect"])
        self.assertEqual(doc["jobs"]["collect"]["needs"], "validate")
        collectors = [p for p in WORKFLOWS.glob("*.y*ml") if "src/leadbot.py" in p.read_text()]
        self.assertEqual([p.name for p in collectors], ["run-lead-bot.yml"])

    def test_tests_receive_no_production_credentials(self):
        main = workflow("run-lead-bot.yml")
        self.assertNotIn("env", main)
        self.assertNotIn("secrets", main["jobs"]["validate"])
        validation = (WORKFLOWS / "validate.yml").read_text()
        self.assertNotIn("secrets.", validation)
        self.assertNotIn("pull_request_target", validation)
        self.assertIn("python -m unittest discover -s tests -v", validation)

    def test_single_sheet_and_optional_integrations(self):
        env = workflow("run-lead-bot.yml")["jobs"]["collect"]["env"]
        self.assertEqual([k for k in env if k.startswith("SHEET_ID")], ["SHEET_ID"])
        self.assertIn("GOOGLE_SA", env)
        for key in ("HUNTER_API_KEY", "ZEROBOUNCE_API_KEY", "SERPER_API_KEY", "APOLLO_API_KEY", "PROSPEO_API_KEY", "SNOV_CLIENT_SECRET", "OPENAI_API_KEY"):
            self.assertIn(key, env)
        self.assertNotIn("NZBN_API_KEY", env)

    def test_finish_runs_after_failed_collection_without_hiding_failure(self):
        steps = workflow("run-lead-bot.yml")["jobs"]["collect"]["steps"]
        finish = next(s for s in steps if s.get("id") == "finish")
        self.assertIn("always()", finish["if"])
        self.assertIn("steps.setup.outcome == 'success'", finish["if"])
        for step in steps:
            self.assertNotIn("continue-on-error", step)
            if "src/leadbot.py" in step.get("run", ""):
                self.assertIn("> /dev/null 2>&1", step["run"])
                self.assertIn("exit 1", step["run"])
                self.assertIn("timeout", step["run"])

    def test_no_lead_artifact_or_environment_dump(self):
        for path in WORKFLOWS.glob("*.y*ml"):
            text = path.read_text()
            for unsafe in ("upload-artifact", "printenv", "toJSON(secrets)", "secrets: inherit", "set -x"):
                self.assertNotIn(unsafe, text)


class HealthMonitor(unittest.TestCase):
    @staticmethod
    def run_record(hours=1, conclusion="success", status="completed", event="schedule"):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        return dict(created_at=stamp, updated_at=stamp, conclusion=conclusion, status=status, event=event)

    def execute(self, runs, *, stale="36", error=None):
        step = workflow("health-check.yml")["jobs"]["health"]["steps"][-1]
        code = inline_python(step)
        env = {"GH_TOKEN": "private-token", "GITHUB_REPOSITORY": "test/repo", "DEFAULT_BRANCH": "main", "STALE_HOURS": stale}
        buf = io.StringIO()
        response = io.StringIO(json.dumps({"workflow_runs": runs}))
        with patch.dict(os.environ, env, clear=True), patch("urllib.request.urlopen", return_value=response, side_effect=error) as request, redirect_stdout(buf):
            try:
                exec(compile(code, "health-check.yml", "exec"), {})
                result = 0
            except SystemExit as exc:
                result = exc.code
        self.assertNotIn("private-token", buf.getvalue())
        return result, buf.getvalue(), request

    def test_recent_success_is_healthy_and_request_has_timeout(self):
        result, text, request = self.execute([self.run_record()])
        self.assertEqual(result, 0)
        self.assertIn("Lead yield must be checked", text)
        self.assertEqual(request.call_args.kwargs["timeout"], 30)
        self.assertIn("branch=main", request.call_args.args[0].full_url)

    def test_no_runs_and_stale_success_fail(self):
        for runs in ([], [self.run_record(hours=48)]):
            self.assertEqual(self.execute(runs)[0], 1)

    def test_failed_latest_does_not_hide_behind_earlier_success(self):
        self.assertEqual(self.execute([self.run_record(conclusion="failure"), self.run_record(hours=5)])[0], 1)

    def test_stuck_and_never_successful_runs_fail(self):
        self.assertEqual(self.execute([self.run_record(hours=3, status="queued", conclusion=None), self.run_record(hours=5)])[0], 1)
        self.assertEqual(self.execute([self.run_record(status="in_progress", conclusion=None)])[0], 1)

    def test_fresh_in_progress_with_recent_success_is_healthy(self):
        self.assertEqual(self.execute([self.run_record(status="in_progress", conclusion=None), self.run_record(hours=25)])[0], 0)

    def test_api_errors_are_generic_and_fail_closed(self):
        result, text, _ = self.execute([], error=RuntimeError("private-token sensitive payload"))
        self.assertEqual(result, 1)
        self.assertNotIn("sensitive payload", text)

    def test_invalid_thresholds_fail(self):
        for value in ("NaN", "Infinity", "1", "169", "bad"):
            self.assertEqual(self.execute([], stale=value)[0], 1)

    def test_other_events_do_not_mask_missing_collector(self):
        self.assertEqual(self.execute([self.run_record(event="pull_request")])[0], 1)


if __name__ == "__main__":
    unittest.main()
