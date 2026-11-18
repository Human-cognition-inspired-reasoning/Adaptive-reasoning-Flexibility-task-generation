"""Summarize graded submissions and determine round outcomes from the stopping rules."""

import json

from .policies import (
    CURRENT_VIOLATION, ROUND_MPE, ROUND_ERROR, ROUND_END_POLICY, policy_name, round_passed,
)
from .grading import ExperimentError, PERSISTENT_ERROR_POLICY, get_problem

ATTEMPTS = 10
SUCCESS_STREAK = 2  # Consecutive correct submissions for early completion.

CONDITION_LABELS = {
    "peg": "Peg restriction",
    "occupancy": "Peg occupancy restriction",
    "source_sequence": "Consecutive source restriction",
    "visit": "Prior visit requirement",
}


def round_policies(config):
    limit = config.get("max_attempts", ATTEMPTS)
    streak = config.get("success_streak", SUCCESS_STREAK)
    apply_r1 = config.get("r1_apply_k", True)
    if type(limit) is not int or limit < 1 or type(streak) is not int or not 1 <= streak <= limit:
        raise ExperimentError("T and k must be integers satisfying 1 <= k <= T.")
    if type(apply_r1) is not bool:
        raise ExperimentError("r1_apply_k must be a boolean.")
    return tuple(
        {
            "round": number,
            "max_attempts": limit,
            "success_streak_required": streak if number > 1 or apply_r1 else 1,
            "last_attempt_correct_passes": True,
        }
        for number in range(1, 6)
    )


def summarize(records, episodes, required_successes=SUCCESS_STREAK, policies=None, problems=None):
    """Calculate round, problem, condition, and overall metrics from scored attempts."""
    policies = policies or round_policies({"success_streak": required_successes})
    if (
        type(required_successes) is not int
        or not 1 <= required_successes <= policies[1]["max_attempts"]
    ):
        raise ValueError(
            "The required success streak must be an integer from 1 to the attempt limit."
        )
    criterion = required_successes
    final_correct_passes = all(p.get("last_attempt_correct_passes") is True for p in policies)
    completion_policy = ROUND_END_POLICY if final_correct_passes else "consecutive_correct"
    completion_note = (
        "Consecutive correct answers permit early completion. A correct final submission at T also passes the round; "
        "an incorrect final submission ends the problem and leaves remaining rounds unattempted."
        if final_correct_passes else
        "A round exhausting its budget without meeting the stored success-streak criterion ends the problem; "
        "remaining rounds are excluded, never scored as failures."
    )
    record_policies = {
        policy_name(record.get("persistent_error_policy", CURRENT_VIOLATION)) for record in records
    }
    if len(record_policies) > 1:
        raise ExperimentError(
            "Cannot aggregate records with different persistent error definitions."
        )
    pe_policy = next(iter(record_policies), PERSISTENT_ERROR_POLICY)
    pe_definition = (
        "A persistent error is a current incorrect submission that obeys the previous additional movement restriction "
        "on the current initial board, with a valid parsed plan and valid basic Hanoi moves. Previous goal attainment "
        "is not required; current goal-only errors can count. R1 is excluded."
        if pe_policy == PERSISTENT_ERROR_POLICY
        else "Under the recorded policy, persistent error is any detected current additional movement-restriction violation, "
        "independent of the previous restriction or goal attainment. R1 is excluded."
    )

    def stats(rows):
        pe = sum(bool(x["persistent_error"]) for x in rows)
        failures = sum(not x["current_success"] for x in rows)
        return {
            "attempts": len(rows),
            "successes": len(rows) - failures,
            "failures": failures,
            "persistent_errors": pe,
            "general_errors": failures - pe,
            "persistent_error_rate": pe / len(rows) if rows else None,
            "failure_rate": failures / len(rows) if rows else None,
            "movement_rule_errors": sum(x.get("movement_rule_error") is True for x in rows),
            "goal_errors": sum(x.get("goal_error") is True for x in rows),
            "goal_evaluated_attempts": sum(x.get("goal_error") is not None for x in rows),
            "base_errors": sum(
                x.get("parse_valid") is True and x.get("base_valid") is False for x in rows
            ),
        }

    episode_results = []
    for episode in range(1, episodes + 1):
        batch = [x for x in records if x["episode"] == episode]
        rounds = []
        exhausted_at = None
        for number in range(1, 6):
            required = policies[number - 1]["success_streak_required"]
            limit = policies[number - 1]["max_attempts"]
            rows = [x for x in batch if x["round"] == number]
            entry = {"round": number, **stats(rows)}
            entry["first_success_attempt"] = next(
                (x["attempt"] for x in rows if x["current_success"]), None
            )
            streak, peak, met_at = 0, 0, None
            for row in rows:
                streak = streak + 1 if row["current_success"] else 0
                peak = max(peak, streak)
                if streak >= required and met_at is None:
                    met_at = row["attempt"]
            entry.update(
                success_streak=streak,
                max_success_streak=peak,
                criterion_met_at=met_at,
                success_streak_required=required,
            )
            valid_sequence = [x["attempt"] for x in rows] == list(range(1, len(rows) + 1)) and (
                limit is None or len(rows) <= limit
            )
            valid_sequence = valid_sequence and (met_at is None or met_at == len(rows))
            terminal = bool(rows) and (met_at is not None or len(rows) == limit)
            entry["complete"] = bool(valid_sequence and terminal)
            entry["max_attempts"] = limit
            passed = bool(rows) and round_passed(
                streak, rows[-1]["attempt"], rows[-1]["current_success"], policies[number - 1]
            )
            entry["status"] = (
                ("success" if passed else "exhausted")
                if entry["complete"]
                else ("in_progress" if rows else "pending")
            )
            if policies[number - 1].get("last_attempt_correct_passes") is True:
                entry["success_reason"] = (
                    "consecutive_correct" if met_at is not None else "last_attempt_correct"
                ) if entry["status"] == "success" else None
            failed, repeats = set(), 0
            for row in rows:
                key = (
                    json.dumps(row["moves"], separators=(",", ":"))
                    if row["moves"] is not None
                    else row["raw_output"]
                )
                if not row["current_success"]:
                    repeats += key in failed
                    failed.add(key)
            entry["repeat_of_identical_failed_plan"] = repeats
            if number == 1:
                entry["persistent_errors"] = entry["persistent_error_rate"] = None
            if exhausted_at is not None and not rows:
                entry.update(
                    status="excluded",
                    excluded_reason="prior_round_exhausted",
                    excluded_after_round=exhausted_at,
                )
            if entry["status"] == "exhausted" and exhausted_at is None:
                exhausted_at = number
            rounds.append(entry)
        scored = [x for x in batch if x["round"] > 1]
        scored_rounds = [row for row in rounds if row["round"] > 1 and row["attempts"] > 0]
        error_rounds = [row for row in rounds if row["attempts"] > 0]
        round_rates = [row["persistent_error_rate"] for row in scored_rounds]
        round_error_rates = [row["failure_rate"] for row in error_rounds]
        mpe = sum(round_rates) / len(round_rates) if round_rates else None
        mean_error_rate = (
            sum(round_error_rates) / len(round_error_rates) if round_error_rates else None
        )
        problem = get_problem(episode, problems) if problems is not None else None
        condition = problem.condition if problem is not None else None
        episode_results.append(
            {
                "episode": episode,
                "problem_id": problem.id if problem is not None else f"P{episode:02d}",
                "condition": condition,
                "complete": all(r["complete"] or r["status"] == "excluded" for r in rounds),
                "exhausted_at_round": exhausted_at,
                "rounds": rounds,
                "scored": stats(scored),
                "per_by_round": {
                    str(row["round"]): row["persistent_error_rate"]
                    for row in rounds
                    if row["round"] > 1
                },
                "error_rate_by_round": {str(row["round"]): row["failure_rate"] for row in rounds},
                "eligible_scored_rounds": len(scored_rounds),
                "mpe": mpe,
                "mean_error_rate": mean_error_rate,
                "eligible_error_rounds": len(error_rounds),
                "error_metric_eligible": bool(error_rounds),
                "metric_eligible": bool(scored_rounds),
            }
        )
    scored = [x for x in records if x["round"] > 1]
    problem_mpes = [row["mpe"] for row in episode_results if row["metric_eligible"]]
    problem_mean_error_rates = [
        row["mean_error_rate"] for row in episode_results if row["error_metric_eligible"]
    ]
    rates = [
        x["scored"]["persistent_error_rate"]
        for x in episode_results
        if x["complete"] and x["scored"]["persistent_error_rate"] is not None
    ]
    conditions = {}
    for condition, label in CONDITION_LABELS.items():
        members = [row for row in episode_results if row["condition"] == condition]
        if not members:
            continue
        numbers = {row["episode"] for row in members}
        completed = [row for row in members if row["complete"]]
        group_rates = [
            row["scored"]["persistent_error_rate"]
            for row in completed
            if row["scored"]["persistent_error_rate"] is not None
        ]
        group_mpes = [row["mpe"] for row in members if row["metric_eligible"]]
        group_mean_error_rates = [
            row["mean_error_rate"] for row in members if row["error_metric_eligible"]
        ]
        conditions[condition] = {
            "label": label,
            "problem_count": len(members),
            "completed_problems": len(completed),
            **stats([row for row in scored if row["episode"] in numbers]),
            "metric_eligible_problems": len(group_mpes),
            "error_metric_eligible_problems": len(group_mean_error_rates),
            "mpe": sum(group_mpes) / len(group_mpes) if group_mpes else None,
            "mean_error_rate": (
                sum(group_mean_error_rates) / len(group_mean_error_rates)
                if group_mean_error_rates
                else None
            ),
            "complete_problem_rate_mean": (
                sum(group_rates) / len(group_rates) if group_rates else None
            ),
        }
    minimum = 0
    for policy in reversed(policies):
        minimum = min(policy["max_attempts"], policy["success_streak_required"] + minimum)
    return {
        "source": "Model API",
        "complete": all(e["complete"] for e in episode_results),
        "early_stopping": True,
        "round_completion_policy": completion_policy,
        "success_streak_required": criterion,
        "baseline_until_success": False,
        "planned_submissions": sum(p["max_attempts"] for p in policies) * episodes,
        "min_submissions": minimum * episodes,
        "max_submissions": sum(p["max_attempts"] for p in policies) * episodes,
        "scored_max_submissions": sum(p["max_attempts"] for p in policies[1:]) * episodes,
        "baseline_submissions": sum(x["round"] == 1 for x in records),
        "recorded_submissions": len(records),
        "completed_rounds": sum(r["complete"] for e in episode_results for r in e["rounds"]),
        "total_rounds": 5 * episodes,
        "successful_rounds": sum(
            r["status"] == "success" for e in episode_results for r in e["rounds"]
        ),
        "exhausted_rounds": sum(
            r["status"] == "exhausted" for e in episode_results for r in e["rounds"]
        ),
        "scored_rounds": [2, 3, 4, 5],
        "pooled": stats(scored),
        "metric_policy": ROUND_MPE,
        "error_metric_policy": ROUND_ERROR,
        "persistent_error_policy": pe_policy,
        "metric_definition": {
            "persistent_error": pe_definition,
            "round_per": "persistent_errors / actual graded submissions in that round",
            "problem_mpe": "unweighted mean of available R2-R5 PERs with at least one graded submission",
            "total_mpe": "unweighted mean of problem MPEs with at least one graded R2-R5 submission",
            "condition_mpe": "unweighted mean of eligible problem MPEs within each condition",
            "round_error_rate": "incorrect answers / actual graded submissions in that round",
            "problem_mean_error_rate": "unweighted mean of available R1-R5 error rates with at least one graded submission",
            "total_mean_error_rate": "unweighted mean of problem mean error rates with at least one graded R1-R5 submission, including R1-only problems",
            "condition_mean_error_rate": "unweighted mean of eligible R1-R5 problem mean error rates within each condition",
            "r1": "excluded from PER/MPE; included in mean error rate even for R1-only problems",
            "missing": "unattempted rounds are excluded, never filled with zero; MPE requires a graded R2-R5 submission, mean error requires a graded R1-R5 submission",
            "observed_failures": "graded incorrect submissions and exhausted rounds are included; ungraded generation failures are not submissions",
            "scale": "fraction in JSON; percentage for display",
            "diagnostics": "pooled, scored, condition failure_rate, excluding_first_attempt and complete_*_rate_mean are submission-weighted diagnostics; the primary metrics equally weight rounds within problems and then problems",
        },
        "total_mpe": sum(problem_mpes) / len(problem_mpes) if problem_mpes else None,
        "total_mean_error_rate": (
            sum(problem_mean_error_rates) / len(problem_mean_error_rates)
            if problem_mean_error_rates
            else None
        ),
        "metric_eligible_problems": len(problem_mpes),
        "error_metric_eligible_problems": len(problem_mean_error_rates),
        "problem_mpes": problem_mpes,
        "problem_mean_error_rates": problem_mean_error_rates,
        "problems_in_mpe_target_band": sum(0.30 <= value <= 0.50 for value in problem_mpes),
        "excluding_first_attempt": stats([x for x in scored if x["attempt"] > 1]),
        "episodes": episode_results,
        "conditions": conditions,
        "complete_episode_rate_mean": sum(rates) / len(rates) if rates else None,
        "complete_episode_rates": rates,
        "episodes_in_target_band": sum(0.30 <= r <= 0.50 for r in rates),
        "completed_episodes": sum(x["complete"] for x in episode_results),
        "metric_eligible_completed_episodes": len(rates),
        "excluded_rounds": sum(
            r["status"] == "excluded" for e in episode_results for r in e["rounds"]
        ),
        "exhausted_problems": sum(x["exhausted_at_round"] is not None for x in episode_results),
        "stop_problem_on_exhaustion": True,
        "format_failures": sum(not x["parse_valid"] for x in records),
        "refusals": sum(x["refusal"] for x in records),
        "usage": {
            key: sum((x.get("usage") or {}).get(key, 0) or 0 for x in records)
            for key in ("input_tokens", "output_tokens", "total_tokens")
        },
        "note": (
            "Each problem starts with fresh history. R1 has a finite submission budget and is excluded from PER/MPE. "
        )
        + completion_note
        + " "
        + pe_definition
        + " Round PER uses actual graded submissions without padding. Problem MPE equally averages attempted scored-round PERs; Total MPE equally averages eligible problem MPEs, including partially observed problems. Mean error rate includes R1: incorrect submissions / actual graded submissions per observed R1-5 round, equal round means per problem, then equal problem means per condition or overall for every problem with at least one graded submission, including R1-only problems. Pooled R2-5 rates are diagnostic only. Partial runs are not final estimates.",
    }
