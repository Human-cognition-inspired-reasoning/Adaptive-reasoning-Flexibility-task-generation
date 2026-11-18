"""Offline checks for round boundaries, metrics, and checkpoint recovery."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hanoi import experiment as e
from hanoi.policies import FOUR_WAY, round_passed
from hanoi.reporting import _early_rounds_complete


class RoundProgressionTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(e.DEFAULTS, episodes=1, max_attempts=3, success_streak=2, dataset=None)
        self.policies = e.round_policies(self.config)
        rule = e.Rule("test", goal_peg="A", candidate_rule={"kind": "peg", "source": "D", "destination": "A"})
        self.problem = e.Problem("P001", (rule,) * 5, "peg")

    def summarize(self, outcomes, policies=None):
        policies = policies or self.policies
        records = []
        for number, answers in enumerate(outcomes, 1):
            streak = 0
            for attempt, correct in enumerate(answers, 1):
                raw = json.dumps({"moves": ["AB", "BA"] if correct else ["AB"]})
                score = e.grade(raw, number, problem=self.problem, feedback_policy=FOUR_WAY)
                streak = streak + 1 if correct else 0
                records.append(dict(
                    score, episode=1, round=number, attempt=attempt, raw_output=raw,
                    refusal=False, success_streak=streak,
                    round_success=round_passed(streak, attempt, score["current_success"], policies[number - 1]),
                ))
        return e.summarize(records, 1, policies=policies, problems=[self.problem]), records

    def test_final_correct_passes_without_k(self):
        result, _ = self.summarize([[False, False, True]])
        first, second = result["episodes"][0]["rounds"][:2]
        self.assertEqual((first["status"], first["success_reason"], first["criterion_met_at"]),
                         ("success", "last_attempt_correct", None))
        self.assertEqual(second["status"], "pending")
        self.assertFalse(result["complete"])

    def test_earlier_correct_does_not_pass_final_wrong(self):
        result, _ = self.summarize([[False, True, False]])
        rounds = result["episodes"][0]["rounds"]
        self.assertEqual(rounds[0]["status"], "exhausted")
        self.assertTrue(all(r["status"] == "excluded" for r in rounds[1:]))

    def test_streak_passes_early_and_isolated_correct_keeps_running(self):
        early, _ = self.summarize([[True, True]])
        self.assertEqual(early["episodes"][0]["rounds"][0]["success_reason"], "consecutive_correct")
        pending, _ = self.summarize([[False, True]])
        self.assertEqual(pending["episodes"][0]["rounds"][0]["status"], "in_progress")

    def test_r5_completion_and_report_agree(self):
        result, records = self.summarize([[False, False, True]] * 5)
        frozen = {"round_policies": list(self.policies)}
        self.assertTrue(result["complete"])
        self.assertEqual(result["successful_rounds"], 5)
        self.assertTrue(_early_rounds_complete(frozen, self.config, result, records))
        records[-1]["round_success"] = False
        self.assertFalse(_early_rounds_complete(frozen, self.config, result, records))

    def test_saved_streak_only_policy_uses_its_own_criterion(self):
        policies = [{k: v for k, v in p.items() if k != "last_attempt_correct_passes"} for p in self.policies]
        result, records = self.summarize([[False, False, True]], policies)
        self.assertEqual(result["episodes"][0]["rounds"][0]["status"], "exhausted")
        self.assertTrue(_early_rounds_complete({"round_policies": policies}, self.config, result, records))

    def test_completion_policy_does_not_change_scoring_or_mpe(self):
        answers = [[True, True], [False, False, True]]
        current, _ = self.summarize(answers)
        policies = [{k: v for k, v in p.items() if k != "last_attempt_correct_passes"} for p in self.policies]
        other, _ = self.summarize(answers, policies)
        for field in ("total_mpe", "total_mean_error_rate", "metric_eligible_problems", "pooled"):
            self.assertEqual(current[field], other[field])
        self.assertAlmostEqual(current["total_mpe"], 2 / 3)

    def test_configurable_T_and_k_and_rule_violation_feedback(self):
        for limit, k in ((1, 1), (3, 2), (10, 2), (10, 10)):
            policy = e.round_policies(dict(self.config, max_attempts=limit, success_streak=k))[0]
            self.assertTrue(round_passed(1, limit, True, policy))
            self.assertFalse(round_passed(0, limit, False, policy))
            if k > 1:
                self.assertFalse(round_passed(1, limit - 1, True, policy))
        score = e.grade('{"moves":["AD","DA"]}', 1, problem=self.problem, feedback_policy=FOUR_WAY)
        self.assertEqual(score["feedback"], "Correct, but rule violation")
        self.assertFalse(round_passed(0, 3, score["current_success"], self.policies[0]))
        r1 = e.round_policies(dict(self.config, r1_apply_k=False))
        self.assertTrue(round_passed(1, 1, True, r1[0]))
        self.assertFalse(round_passed(1, 1, True, r1[1]))

    def test_runner_resume_after_final_correct_preserves_next_round(self):
        calls = []

        def create(**request):
            calls.append(request)
            moves = ["AB", "BA"] if len(calls) % 3 == 0 else ["AB"]
            payload = {"status": "completed", "output": [{"type": "message", "role": "assistant",
                       "content": [{"type": "output_text", "text": json.dumps({"moves": moves})}]}]}
            return SimpleNamespace(model_dump=lambda **kwargs: payload)

        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=create)))
        obtain = e.obtain_response

        def interrupt_at_r2(trial, *args, **kwargs):
            if trial.name.endswith("_r2_a01"):
                raise e.ExperimentStopped("Offline interruption at the round boundary.")
            return obtain(trial, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp, patch.object(e, "configured_problems", return_value=[self.problem]), \
                patch.object(e, "refresh_reports"), patch("hanoi.terminal.TerminalReporter"), redirect_stdout(io.StringIO()):
            directory = Path(tmp)
            e.prepare_protocol(directory, self.config, False)
            with patch.object(e, "obtain_response", side_effect=interrupt_at_r2), self.assertRaises(e.ExperimentStopped):
                e.execute(directory, self.config, client)
            self.assertEqual(len(calls), 3)
            e.prepare_protocol(directory, self.config, True)
            result = e.execute(directory, self.config, client)
            self.assertEqual(len(calls), 15)
            self.assertEqual(result["successful_rounds"], 5)
            self.assertTrue(result["complete"])
            recovered = e.execute(directory, self.config, client)
            self.assertEqual(result, recovered)
            self.assertEqual(len(calls), 15)
            saved = e.read_json(directory / "protocol.json")
            saved["frozen"]["round_policies"][0]["last_attempt_correct_passes"] = False
            saved["frozen_sha256"] = e.digest(saved["frozen"])
            e.save_json(directory / "protocol.json", saved)
            with self.assertRaises(e.ExperimentError):
                e.prepare_protocol(directory, self.config, True)


if __name__ == "__main__":
    unittest.main()
