"""Offline regression tests for dataset validation, scoring and run recovery."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import run
from dataset_tools.generate import build_dataset
from dataset_tools.validate import validate_dataset
from hanoi import experiment
from hanoi.validation import ValidationError


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        # Two small generated problems are sufficient to observe progression across a failure.
        self.dataset = build_dataset(23, types=["peg"], per_type=2)
        self.path = self.directory / "problems.json"
        self.path.write_text(json.dumps(self.dataset), encoding="utf-8")

    def run_responses(self, provider, responses):
        config = {
            **experiment.DEFAULTS,
            "provider": provider,
            "model": experiment.PROVIDER_MODELS[provider],
            "dataset": str(self.path),
            "episodes": len(responses),
            "max_attempts": 1,
            "success_streak": 1,
        }
        sdk_responses = [
            SimpleNamespace(model_dump=Mock(return_value=payload)) for payload in responses
        ]
        create = Mock(side_effect=sdk_responses)
        resource = SimpleNamespace(create=create)
        client = SimpleNamespace(messages=resource, responses=resource)
        experiment.prepare_protocol(self.directory, config, resume=False)
        with redirect_stdout(io.StringIO()):
            result = experiment.execute(self.directory, config, client)
            # Saved responses, including unscored failures, must replay without another request.
            resumed = experiment.execute(self.directory, config, client, retry_pending=True)
        self.assertEqual(create.call_count, len(responses))
        self.assertEqual(result, resumed)
        records = [
            json.loads(line)
            for line in (self.directory / "submissions.jsonl").read_text().splitlines()
        ]
        return result, records

    def test_failed_openai_response_skips_one_problem_and_continues(self):
        failure = {"status": "failed", "output": []}
        answer = {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": '{"moves":[]}'}],
                }
            ],
        }
        result, records = self.run_responses("openai", [failure, answer])
        self.assertEqual(result["generation_failure_count"], 1)
        self.assertEqual([row["episode"] for row in records], [2])
        rounds = result["episodes"][0]["rounds"]
        self.assertEqual([r["status"] for r in rounds], ["generation_failed"] + ["excluded"] * 4)
        self.assertEqual([r["attempts"] for r in rounds], [0] * 5)

    def test_empty_anthropic_refusal_counts_as_one_incorrect_submission(self):
        refusal = {"type": "message", "role": "assistant", "stop_reason": "refusal", "content": []}
        result, records = self.run_responses("anthropic", [refusal])
        self.assertEqual(result["generation_failure_count"], 0)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["refusal"])
        self.assertFalse(records[0]["current_success"])
        self.assertEqual(records[0]["feedback"], "Incorrect")

    def test_dry_run_cannot_overwrite_existing_run(self):
        preview = self.directory / "preview"
        with patch.object(
            run.auth, "checked_client", side_effect=AssertionError("unexpected API call")
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run.main(["--dry-run", "--output", str(preview)]), 0)
            # Stand in for a completed run whose status and preview must survive a mistaken command.
            state_file = preview / "state.json"
            state_file.write_text('{"phase":"complete"}', encoding="utf-8")
            before = {
                p.relative_to(preview): p.read_bytes() for p in preview.rglob("*") if p.is_file()
            }
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                run.main(["--dry-run", "--resume", str(preview)])
            self.assertEqual(error.exception.code, 2)
            after = {
                p.relative_to(preview): p.read_bytes() for p in preview.rglob("*") if p.is_file()
            }
            self.assertEqual(after, before)

    def test_validator_and_runner_reject_false_rule_type_counts(self):
        self.dataset["metadata"]["problems_per_rule_type"] = 777
        self.path.write_text(json.dumps(self.dataset), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "problems_per_rule_type"):
            validate_dataset(self.dataset)
        with self.assertRaisesRegex(experiment.ExperimentError, "problems_per_rule_type"):
            experiment.load_candidate_problems(self.path)
