"""Offline regression tests for the evaluator's replay and saved-run reports."""

import unittest

from hanoi.reporting import html_text, replay_plan


class ReportingTests(unittest.TestCase):
    def test_forbidden_move_onto_smaller_disk_reports_both_errors(self):
        rule = {
            "initial_positions": ["D", "B", "A"],  # Disk 1 is on D; disk 3 is on A.
            "goal_peg": "C",
            "candidate_rule": {"kind": "peg", "source": "A", "destination": "D"},
        }
        trace = replay_plan(["AD", "DB"], rule)
        initial, failed = trace["steps"]
        self.assertEqual(trace["failed_at"], 1)
        self.assertEqual(failed["disk"], 3)
        self.assertFalse(failed["base_valid"])
        self.assertTrue(failed["rule_violation"])
        self.assertIn("Hidden restriction violated", failed["message"])
        self.assertEqual(failed["state"], initial["state"])

        # A to B is also physically illegal, but the restriction only forbids A to D.
        other = replay_plan(["AB"], rule)["steps"][-1]
        self.assertFalse(other["base_valid"])
        self.assertFalse(other["rule_violation"])

    def test_report_explains_the_policy_recorded_by_older_runs(self):
        previous = "Missing the current goal can qualify."
        current = "Missing the goal alone does not qualify."
        for name, expected, other in [
            ("previous_rule_compliant_current_failure_v1", previous, current),
            ("any_current_movement_restriction_violation_v1", current, previous),
        ]:
            with self.subTest(policy=name):
                report = html_text({"protocol": {"frozen": {"persistent_error_policy": name}}})
                self.assertIn(expected, report)
                self.assertNotIn(other, report)
