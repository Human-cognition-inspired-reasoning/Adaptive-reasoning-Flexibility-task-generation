"""Read, replay, and export experiment logs for evaluators, independently of API requests."""

import csv
from html import escape
import io
import json
import os
from pathlib import Path
import tempfile
from .terminal import (
    candidate_rule_text,
    condition_text,
    describe_moves,
    initial_stacks,
    initial_state_text,
    rule_text,
)
from .policies import BINARY, CURRENT_VIOLATION, FOUR_WAY, PREVIOUS_RULE, policy_name
from .prompts import feedback_values

_UNSPECIFIED_GOAL = object()


def _candidate_protocol(frozen):
    return (
        isinstance(frozen, dict)
        and frozen.get("version") in (24, 25, 26, 27, 28, 29)
        and (frozen.get("dataset_version") == 7)
    )


def _candidate_trace(moves, rule, peg_names, disk_count, goal_peg, initial_positions):
    """Replay the saved candidate rule; forbidden basic-legal moves still execute."""
    from .rules import replay_plan as replay_candidate

    result = {
        "success": False,
        "base_valid": False,
        "rule_compliant": False,
        "reason": "",
        "failed_at": None,
        "steps": [],
        "goal_peg": goal_peg,
    }
    stacks = initial_stacks(initial_positions, pegs=peg_names, disk_count=disk_count)
    if stacks is None or disk_count != 3 or tuple(peg_names) != ("A", "B", "C", "D"):
        result["reason"] = "Invalid board configuration in the log"
        return result
    result["steps"].append(
        {
            "index": 0,
            "move": None,
            "disk": None,
            "valid": True,
            "base_valid": True,
            "rule_violation": False,
            "message": "Starting position",
            "state": stacks,
        }
    )
    if goal_peg not in peg_names:
        result["reason"] = "Invalid goal peg in the log"
        return result
    if not isinstance(moves, (list, tuple)) or not 1 <= len(moves) <= 30:
        result["reason"] = "A plan must contain 1 to 30 moves."
        return result
    try:
        trace = replay_candidate(initial_positions, goal_peg, moves, rule["candidate_rule"])
    except (KeyError, TypeError, ValueError):
        result["reason"] = "Invalid rule or initial position in the log"
        return result
    for step in trace["steps"]:
        violates = step["rule_violation"]
        message = (
            "Hidden restriction violated (replay continues): "
            + candidate_rule_text(rule["candidate_rule"])
            if violates
            else "Legal move"
        )
        result["steps"].append(
            {
                "index": step["step"],
                "move": step["move"],
                "disk": step["disk"],
                "valid": not violates,
                "base_valid": True,
                "rule_violation": violates,
                "message": message,
                "state": initial_stacks(step["positions"], pegs=peg_names),
            }
        )
        if violates and result["failed_at"] is None:
            result["failed_at"] = step["step"]
    error = trace["basic_error"]
    if error:
        before = result["steps"][-1]["state"]
        move = error["move"]
        disk = (
            before[move[0]][-1]
            if isinstance(move, str) and len(move) == 2 and (move[0] in before) and before[move[0]]
            else None
        )
        # Grading checks identifiable restrictions even when a basic move cannot execute.
        from .grading import Rule, evaluate_plan

        grading_rule = Rule(
            name=rule.get("name", ""),
            goal_peg=goal_peg,
            candidate_rule=rule["candidate_rule"],
            initial_positions=tuple(initial_positions),
        )
        diagnostics = evaluate_plan(moves, grading_rule)
        violates = any(
            item["step"] == error["step"] for item in diagnostics["movement_rule_violations"]
        )
        message = "Illegal Hanoi move"
        if violates:
            message += ". Hidden restriction violated: " + candidate_rule_text(
                rule["candidate_rule"]
            )
        result["steps"].append(
            {
                "index": error["step"],
                "move": move,
                "disk": disk,
                "valid": False,
                "base_valid": False,
                "rule_violation": violates,
                "message": message,
                "state": before,
            }
        )
        result["failed_at"] = result["failed_at"] or error["step"]
    result["success"] = trace["base_legal"] and trace["rule_compliant"] and trace["goal_reached"]
    result["base_valid"] = trace["base_legal"]
    result["rule_compliant"] = trace["rule_compliant"]
    if error:
        result["reason"] = result["steps"][-1]["message"]
    elif not trace["rule_compliant"]:
        result["reason"] = (
            f"Hidden restriction violated on {len(trace['rule_violation_steps'])} steps."
        )
    elif result["success"]:
        result["reason"] = f"Goal peg {goal_peg} reached."
    else:
        result["reason"] = f"All moves were legal. The plan ended short of goal peg {goal_peg}."
    return result


def pe_definition(frozen):
    """Describe the metric policy saved with the run."""
    policy = policy_name(frozen.get("persistent_error_policy"))
    if policy == PREVIOUS_RULE:
        return "A persistent error is an unsuccessful plan that obeys the basic Hanoi rules and the previous restriction when replayed from the current starting position. Missing the current goal can qualify. The previous goal is irrelevant. Invalid formats and illegal basic moves are excluded. Each submission can contribute at most one persistent error."
    if policy == CURRENT_VIOLATION:
        return "This run counts a persistent error once per submission that violates the current restriction. Missing the goal alone does not qualify. Success in the previous round is irrelevant."
    return "Persistent errors are shown as recorded. Check the original protocol for its metric definition."


def movement_diagnostics(record):
    """Format movement and goal diagnostics from the saved grading record."""
    if "movement_rule_error" not in record:
        return ""
    violations = record.get("movement_rule_violations") or []
    goal = {True: "Goal missed", False: "Goal reached", None: "Goal could not be evaluated"}.get(
        record.get("goal_error")
    )
    details = [
        f"step {item.get('step')}, {item.get('move')}, disk {(item.get('disk') if item.get('disk') is not None else 'unknown')}"
        for item in violations
    ]
    return f"Restriction violations: {len(violations)} · {goal}" + (
        " · " + "; ".join(details) if details else ""
    )


def replay_plan(
    moves,
    rule_dict_or_None=None,
    *,
    peg_names=None,
    disk_count=3,
    goal_peg=_UNSPECIFIED_GOAL,
    initial_positions=None,
):
    """Replay a supported protocol from its explicit saved state."""
    rule = rule_dict_or_None
    if not isinstance(rule, dict) or not isinstance(rule.get("candidate_rule"), dict):
        return {
            "success": False,
            "reason": "Invalid rule in the log",
            "failed_at": None,
            "steps": [],
            "goal_peg": None,
        }
    if goal_peg is _UNSPECIFIED_GOAL:
        goal_peg = rule.get("goal_peg")
    if peg_names is None:
        peg_names = ("A", "B", "C", "D")
    if (
        not isinstance(peg_names, (list, tuple))
        or tuple(peg_names) != ("A", "B", "C", "D")
        or type(disk_count) is not int
        or (disk_count != 3)
    ):
        return {
            "success": False,
            "reason": "Invalid board configuration in the log",
            "failed_at": None,
            "steps": [],
            "goal_peg": goal_peg,
        }
    if initial_positions is None:
        initial_positions = rule.get("initial_positions")
    return _candidate_trace(moves, rule, peg_names, disk_count, goal_peg, initial_positions)


def _json(path, errors, default=None):
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data
    except (OSError, ValueError) as exc:
        errors.append(f"{path.name}: {type(exc).__name__} — Could not read the log.")
        return default


def _jsonl(path, errors):
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        errors.append(f"{path.name}: Could not read the log.")
        return []
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("Expected a JSON object")
            rows.append(row)
        except ValueError:
            errors.append(f"{path.name}:{number}: Invalid JSON record.")
    return rows


def round_policy(frozen, number):
    policies = frozen.get("round_policies", [])
    if isinstance(policies, list) and type(number) is int and (1 <= number <= len(policies)):
        policy = policies[number - 1]
        return policy if isinstance(policy, dict) else {}
    return {}


def problem_rules(frozen, episode):
    problems = frozen.get("problems", [])
    if isinstance(problems, list) and type(episode) is int and (1 <= episode <= len(problems)):
        problem = problems[episode - 1]
        rounds = problem.get("rounds", []) if isinstance(problem, dict) else []
        return rounds if isinstance(rounds, list) else []
    return []


def problem_condition(frozen, episode):
    problems = frozen.get("problems", [])
    if isinstance(problems, list) and type(episode) is int and (1 <= episode <= len(problems)):
        problem = problems[episode - 1]
        return problem.get("condition") if isinstance(problem, dict) else None
    return None


def _early_rounds_complete(frozen, config, summary, records):
    """Accept early completion only when both submissions and round summaries confirm it."""
    episodes = config.get("episodes")
    if type(episodes) is not int or episodes < 1:
        return False
    episode_rows = summary.get("episodes", [])
    if len(episode_rows) != episodes or [row.get("episode") for row in episode_rows] != list(
        range(1, episodes + 1)
    ):
        return False
    resolved = summary.get("completed_rounds", 0) + summary.get("excluded_rounds", 0)
    if resolved != episodes * 5 or summary.get("total_rounds") != episodes * 5:
        return False
    if any((type(record.get("current_success")) is not bool for record in records)):
        return False
    expected_positions = []
    for episode in episode_rows:
        rounds = episode.get("rounds", [])
        if episode.get("complete") is not True or [row.get("round") for row in rounds] != list(
            range(1, 6)
        ):
            return False
        exhausted = None
        for row in rounds:
            if row.get("status") == "excluded":
                if (
                    exhausted is None
                    or row.get("attempts") != 0
                    or row.get("complete") is not False
                    or (row.get("excluded_reason") != "prior_round_exhausted")
                    or (row.get("excluded_after_round") != exhausted)
                ):
                    return False
                if any(
                    (
                        record.get("episode") == episode["episode"]
                        and record.get("round") == row.get("round")
                        for record in records
                    )
                ):
                    return False
                continue
            if exhausted is not None:
                return False
            policy = round_policy(frozen, row.get("round"))
            if policy.get("round") != row.get("round"):
                return False
            limit, required_streak = (
                policy.get("max_attempts"),
                policy.get("success_streak_required"),
            )
            if (
                type(limit) is not int
                or limit < 1
                or type(required_streak) is not int
                or required_streak < 1
            ):
                return False
            if required_streak > limit:
                return False
            actual = [
                record
                for record in records
                if record.get("episode") == episode["episode"]
                and record.get("round") == row["round"]
            ]
            if not actual or len(actual) > limit:
                return False
            streak = peak = 0
            first_success = criterion_met_at = None
            for index, record in enumerate(actual, 1):
                streak = streak + 1 if record["current_success"] else 0
                peak = max(peak, streak)
                if record["current_success"] and first_success is None:
                    first_success = index
                if streak >= required_streak and criterion_met_at is None:
                    criterion_met_at = index
                if record.get("success_streak") != streak or record.get("round_success") is not (
                    streak >= required_streak
                ):
                    return False
            if criterion_met_at is not None and criterion_met_at != len(actual):
                return False
            expected_status = (
                "success"
                if criterion_met_at is not None
                else "exhausted" if len(actual) == limit else None
            )
            if (
                expected_status is None
                or row.get("status") != expected_status
                or row.get("complete") is not True
            ):
                return False
            if row.get("attempts") != len(actual) or row.get("max_attempts") != limit:
                return False
            if row.get("first_success_attempt") != first_success:
                return False
            if (
                row.get("success_streak_required") != required_streak
                or row.get("success_streak") != streak
                or row.get("max_success_streak") != peak
                or (row.get("criterion_met_at") != criterion_met_at)
            ):
                return False
            if expected_status == "exhausted":
                exhausted = row["round"]
            expected_positions.extend(
                (
                    (episode["episode"], row["round"], attempt)
                    for attempt in range(1, len(actual) + 1)
                )
            )
    if summary.get("completed_rounds") != sum(
        (row.get("complete") is True for episode in episode_rows for row in episode["rounds"])
    ) or summary.get("excluded_rounds") != sum(
        (row.get("status") == "excluded" for episode in episode_rows for row in episode["rounds"])
    ):
        return False
    return [
        (record.get("episode"), record.get("round"), record.get("attempt")) for record in records
    ] == expected_positions


def snapshot(directory):
    """Read per-file atomic checkpoints and expose cross-file inconsistencies during writes."""
    directory = Path(directory)
    errors, warnings = ([], [])
    protocol = _json(directory / "protocol.json", errors, {})
    frozen = protocol.get("frozen", protocol)
    if not isinstance(frozen, dict):
        errors.append("protocol.json: invalid frozen protocol object")
        frozen = {}
    config = frozen.get("config", {})
    if not isinstance(config, dict):
        errors.append("protocol.json: invalid config object")
        config = {}
    if not _candidate_protocol(frozen):
        errors.append(
            "Unsupported protocol. Check the protocol and dataset versions in protocol.json."
        )
    allowed_goals = ("A", "B", "C", "D")
    count = config.get("episodes", 1)
    if type(count) is not int or not 1 <= count <= 300:
        errors.append("protocol.json: invalid problem count")
    else:
        for episode in range(1, count + 1):
            rounds = problem_rules(frozen, episode)
            if (
                not isinstance(rounds, list)
                or len(rounds) != 5
                or any(
                    (
                        not isinstance(rule, dict) or rule.get("goal_peg") not in allowed_goals
                        for rule in rounds
                    )
                )
            ):
                errors.append(f"protocol.json: problem {episode} has an invalid round goal")
            from .rules import replay_plan as replay_candidate

            for number, rule in enumerate(rounds, 1):
                try:
                    if not isinstance(rule, dict) or not isinstance(
                        rule.get("candidate_rule"), dict
                    ):
                        raise ValueError("candidate rule missing")
                    replay_candidate(
                        rule.get("initial_positions"),
                        rule.get("goal_peg"),
                        [],
                        rule["candidate_rule"],
                    )
                except (KeyError, TypeError, ValueError):
                    errors.append(
                        f"protocol.json: problem {episode}, round {number} has an invalid initial position or restriction"
                    )
    policies = frozen.get("round_policies")
    if (
        not isinstance(policies, list)
        or len(policies) != 5
        or any(
            (
                not isinstance(policy, dict)
                or policy.get("round") != number
                or policy.get("max_attempts") != config.get("max_attempts")
                or (
                    policy.get("success_streak_required")
                    != (
                        config.get("success_streak")
                        if number > 1 or config.get("r1_apply_k")
                        else 1
                    )
                )
                for number, policy in enumerate(policies, 1)
            )
        )
    ):
        errors.append("protocol.json: invalid round stopping criteria")
    problems = frozen.get("problems", [])
    allowed_conditions = ("peg", "occupancy", "source_sequence", "visit")
    if (
        not isinstance(problems, list)
        or len(problems) != config.get("episodes")
        or any(
            (
                not isinstance(problem, dict) or problem.get("condition") not in allowed_conditions
                for problem in problems
            )
        )
    ):
        errors.append("protocol.json: invalid problem rule type")
    else:
        for problem in problems:
            rounds = problem.get("rounds")
            for rule in rounds if isinstance(rounds, list) else []:
                if (
                    isinstance(rule, dict)
                    and isinstance(rule.get("candidate_rule"), dict)
                    and (rule["candidate_rule"].get("kind") != problem.get("condition"))
                ):
                    errors.append(
                        f"protocol.json: inconsistent rule types in problem {problem.get('id')}"
                    )
    summary = _json(directory / "summary.json", errors, {})
    if not isinstance(summary.get("pooled", {}), dict) or not isinstance(
        summary.get("episodes", []), list
    ):
        errors.append("summary.json: invalid pooled results or episodes")
        summary = {}
    elif any(
        (
            not isinstance(episode, dict)
            or not isinstance(episode.get("rounds", []), list)
            or any((not isinstance(row, dict) for row in episode.get("rounds", [])))
            for episode in summary.get("episodes", [])
        )
    ):
        errors.append("summary.json: invalid round summaries")
        summary = {}
    records = _jsonl(directory / "submissions.jsonl", errors)
    if not (directory / "submissions.jsonl").exists():
        for file in sorted((directory / "trials").glob("*/record.json")):
            record = _json(file, errors)
            if record is not None:
                records.append(record)
        if records:
            warnings.append("Missing submissions.jsonl. Loaded the individual record.json files.")
    required = (
        "call",
        "episode",
        "round",
        "attempt",
        "current_success",
        "previous_success",
        "persistent_error",
    )
    if policy_name(frozen.get("persistent_error_policy")) == PREVIOUS_RULE:
        required += ("previous_rule_compliant",)
    if any((any((key not in record for key in required)) for record in records)):
        errors.append("A submission is missing required grading fields.")
    calls = [record.get("call") for record in records]
    failure_records = [
        _json(file, errors, {})
        for file in sorted((directory / "trials").glob("*/generation_failure.json"))
    ]
    all_calls = sorted(calls + [row.get("call", 0) for row in failure_records])
    if calls != sorted(set(calls)) or all_calls != list(range(1, len(all_calls) + 1)):
        errors.append("Call numbers contain duplicates or gaps.")
    events = _jsonl(directory / "events.jsonl", errors)
    stop_info = _json(directory / "last_stop.json", errors)
    state = _json(directory / "state.json", errors, {})
    if not state:
        status = (
            "complete"
            if summary.get("complete") is True
            else "preview" if (directory / "first_request_preview.json").exists() else "ready"
        )
        state = {"phase": status}
    if not isinstance(state.get("phase"), str):
        errors.append("state.json: missing or invalid phase")
    count = summary.get("recorded_submissions")
    if count is not None and count != len(records):
        warnings.append(
            "The summary count differs from the submission log. A write may be in progress. Check the checkpoint if the counts continue to differ."
        )
        if state.get("phase") == "complete":
            state = {**state, "phase": "incomplete"}
    complete_evidence = _early_rounds_complete(frozen, config, summary, records)
    if state.get("phase") == "complete" and (
        not records or not complete_evidence or summary.get("complete") is not True
    ):
        warnings.append("The run is marked complete, but the grading records are incomplete.")
        state = {**state, "phase": "incomplete"}
    if errors:
        state = {**state, "phase": "error"}
    try:
        console = (
            (directory / "console.log")
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()[-200:]
        )
    except FileNotFoundError:
        console = []
    except OSError:
        console = []
        warnings.append("Could not read console.log.")
    return {
        "name": directory.name,
        "protocol": protocol,
        "config": config,
        "state": state,
        "summary": summary,
        "records": records,
        "generation_failures": failure_records,
        "events": events,
        "console_tail": "\n".join(console),
        "stop_info": stop_info,
        "errors": errors,
        "warnings": warnings,
    }


def trial_detail(directory, call):
    directory, errors = (Path(directory), [])
    if isinstance(call, bool) or not isinstance(call, int) or call < 1:
        raise ValueError("Call number must be a positive integer.")
    trials = sorted((directory / "trials").glob(f"{call:05d}_*"))
    if len(trials) != 1:
        raise FileNotFoundError(f"Expected one trial directory for call {call}.")
    trial = trials[0]
    record = _json(trial / "record.json", errors, {})
    request = _json(trial / "request.json", errors)
    responses = sorted(trial.glob("transport_*_response.json"))
    response = None
    if len(responses) == 1:
        envelope = _json(responses[0], errors, {})
        response = envelope.get("response")
    elif len(responses) > 1:
        errors.append(
            "Multiple API responses exist for this trial. Check the individual transport files."
        )
    protocol = _json(directory / "protocol.json", errors, {})
    frozen = protocol.get("frozen", protocol)
    rules = problem_rules(frozen, record.get("episode")) if isinstance(frozen, dict) else []
    allowed_goals = ("A", "B", "C", "D")
    peg_names = frozen.get("pegs", ())
    disk_count = frozen.get("disk_count", 3) if isinstance(frozen, dict) else 3
    number = record.get("round")
    current_trace = previous_trace = None
    if not _candidate_protocol(frozen):
        errors.append("Move replay does not support this protocol version.")
    elif isinstance(number, int) and 1 <= number <= len(rules):
        current_rule = rules[number - 1]
        current_goal = current_rule.get("goal_peg") if isinstance(current_rule, dict) else None
        if current_goal not in allowed_goals:
            errors.append("Invalid goal peg for the current round.")
        current_trace = replay_plan(
            record.get("moves"),
            current_rule,
            peg_names=peg_names,
            disk_count=disk_count,
            goal_peg=current_goal,
        )
        if number > 1:
            previous_rule = rules[number - 2]
            previous_goal = (
                previous_rule.get("goal_peg") if isinstance(previous_rule, dict) else None
            )
            if isinstance(previous_rule, dict) and isinstance(current_rule, dict):
                previous_rule = {
                    **previous_rule,
                    "initial_positions": current_rule.get("initial_positions"),
                    "goal_peg": current_goal,
                }
                previous_goal = current_goal
            if previous_goal not in allowed_goals:
                errors.append("Invalid goal peg for the previous round.")
            previous_trace = replay_plan(
                record.get("moves"),
                previous_rule,
                peg_names=peg_names,
                disk_count=disk_count,
                goal_peg=previous_goal,
            )
            if policy_name(frozen.get("persistent_error_policy")) == PREVIOUS_RULE:
                previous_trace["scope"] = "previous_rule_only"
                previous_trace["success"] = previous_trace.get(
                    "base_valid", False
                ) and previous_trace.get("rule_compliant", False)
                if previous_trace["success"]:
                    previous_trace["reason"] = (
                        "The plan obeyed the previous restriction. This diagnostic ignores the goal."
                    )
    elif record:
        errors.append("Cannot replay moves without the recorded round rules.")
    return {
        "record": record,
        "generation_failure": _json(trial / "generation_failure.json", errors),
        "request": request,
        "response": response,
        "current_trace": current_trace,
        "previous_trace": previous_trace,
        "errors": errors,
    }


def csv_text(data):
    fields = [
        "protocol_version",
        "persistent_error_policy",
        "provider",
        "model",
        "episode",
        "problem_id",
        "condition",
        "condition_label",
        "round",
        "attempt",
        "feedback_policy",
        "feedback_code",
        "feedback",
        "correctness_feedback",
        "current_success",
        "previous_success",
        "previous_rule_compliant",
        "persistent_error",
        "movement_rule_error",
        "movement_rule_violations",
        "goal_error",
        "goal_reached",
        "base_valid",
        "base_error",
        "success_streak",
        "round_success",
        "success_streak_required",
        "move_count",
        "moves",
        "elapsed_seconds",
        "response_id",
    ]
    protocol = data.get("protocol", {})
    frozen = protocol.get("frozen", protocol)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for record in data.get("records", []):
        row = {key: record.get(key) for key in fields}
        row["protocol_version"] = frozen.get("version")
        row["persistent_error_policy"] = frozen.get("persistent_error_policy")
        config = frozen.get("config", {})
        row["feedback_policy"] = record.get(
            "feedback_policy",
            frozen.get("feedback_policy", config.get("feedback_policy", BINARY)),
        )
        row["provider"] = config.get("provider", "openai")
        row["model"] = config.get("model")
        row["condition"] = problem_condition(frozen, record.get("episode"))
        row["condition_label"] = condition_text(row["condition"])
        row["success_streak_required"] = round_policy(frozen, record.get("round")).get(
            "success_streak_required"
        )
        moves = record.get("moves")
        row.update(
            move_count=len(moves) if isinstance(moves, list) else 0,
            moves=" ".join((str(move) for move in moves)) if isinstance(moves, list) else "",
        )
        for key in ("movement_rule_violations", "base_error"):
            row[key] = json.dumps(record[key], ensure_ascii=False) if key in record else None
        for key, value in row.items():
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                row[key] = "'" + value
        writer.writerow(row)
    return "\ufeff" + stream.getvalue()


def _text(value):
    return escape("—" if value is None else str(value), quote=True)


def _pretty(value):
    return escape(json.dumps(value, ensure_ascii=False, indent=2), quote=True)


def _rate(value):
    return (
        f"{value:.1%}" if isinstance(value, (float, int)) and (not isinstance(value, bool)) else "—"
    )


REPORT_CSS = """
body {font: 15px/1.6 system-ui, sans-serif; background: #f4f6fa; color: #192336;
      max-width: 1150px; margin: 32px auto; padding: 0 20px}
h1 {font-size: 28px} h2 {margin-top: 30px} h3 {font-size: 14px}
.cards {display: flex; gap: 12px; flex-wrap: wrap}
.card, details {background: white; border: 1px solid #dbe1eb; border-radius: 10px; padding: 16px}
.card {flex: 1; min-width: 160px} .card b {display: block; font-size: 23px}
table {border-collapse: collapse; width: 100%; background: white}
th, td {padding: 9px; border-bottom: 1px solid #dbe1eb; text-align: left}
.table-wrap {overflow: auto} details {margin: 10px 0} summary {cursor: pointer; font-weight: 600}
pre {white-space: pre-wrap; overflow-wrap: anywhere; background: #eef2f7;
     padding: 12px; border-radius: 6px; font-size: 13px}
.plan {font-family: monospace; overflow-wrap: anywhere} .muted, footer {color: #5c6675}
.warning {color: #943c10} footer {margin: 36px 0}
"""

ROUND_STATUS = {
    "success": "Required streak reached",
    "exhausted": "Attempt limit reached",
    "pending": "Pending",
    "in_progress": "In progress",
    "generation_failed": "Generation stopped (ungraded)",
    "excluded": "Skipped",
}

RUN_STATUS = {
    "complete": "Complete",
    "running": "Running",
    "ready": "Ready",
    "preview": "Request preview",
    "finished_with_failures": "Finished with incomplete problems",
    "incomplete": "Incomplete",
    "error": "Error",
    "stopped": "Stopped",
    "stopping": "Stopping",
}


def _report_table(title, headings, rows):
    """Escape text and numeric values before inserting them into the report."""
    header = "".join(f"<th>{_text(value)}</th>" for value in headings)
    body = []
    for row in rows:
        cells = "".join(f"<td>{_text(value)}</td>" for value in row)
        body.append(f"<tr>{cells}</tr>")
    if not body:
        return ""
    return (
        f'<h2>{_text(title)}</h2><div class="table-wrap"><table>'
        f'<thead><tr>{header}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
    )


def _round_table(summary, frozen):
    rows = []
    for episode in summary.get("episodes", []):
        episode_id = episode.get("episode")
        rules = problem_rules(frozen, episode_id)
        condition = condition_text(problem_condition(frozen, episode_id))
        for row in episode.get("rounds", []):
            number = row.get("round")
            required = round_policy(frozen, number).get("success_streak_required")
            label = "Rule unavailable"
            if type(number) is int and 1 <= number <= len(rules):
                rule = rules[number - 1]
                label = f"Start {initial_state_text(rule)} · {rule_text(rule, require_goal=True)}"
            streak = row.get("success_streak", int(row.get("status") == "success"))
            met_at = row.get(
                "criterion_met_at", row.get("first_success_attempt") if required == 1 else None
            )
            rows.append(
                (
                    episode.get("problem_id", episode_id),
                    condition,
                    number,
                    label,
                    row.get("attempts"),
                    row.get("successes"),
                    row.get("failures"),
                    _rate(row.get("failure_rate")),
                    row.get("persistent_errors"),
                    _rate(row.get("persistent_error_rate")),
                    row.get("first_success_attempt"),
                    met_at,
                    f"{streak}/{required}",
                    row.get("max_success_streak"),
                    ROUND_STATUS.get(row.get("status"), "Fixed number of attempts"),
                )
            )
    return _report_table(
        "Round results",
        (
            "Problem",
            "Condition",
            "Round",
            "Starting board, goal and restriction",
            "Submissions",
            "Correct",
            "Incorrect",
            "Error rate",
            "Persistent errors",
            "PER",
            "First correct attempt",
            "Attempt reaching criterion",
            "Final streak / k",
            "Longest streak",
            "Status",
        ),
        rows,
    )


def _condition_table(summary):
    conditions = summary.get("conditions")
    if not isinstance(conditions, dict):
        return ""
    rows = (
        (
            condition_text(key),
            item.get("problem_count"),
            item.get("metric_eligible_problems"),
            _rate(item.get("mpe")),
            item.get("error_metric_eligible_problems"),
            _rate(item.get("mean_error_rate")),
            item.get("attempts"),
            item.get("persistent_errors"),
            _rate(item.get("persistent_error_rate")),
        )
        for key, item in conditions.items()
        if isinstance(item, dict)
    )
    return _report_table(
        "Results by condition",
        (
            "Condition",
            "Planned problems",
            "Problems with MPE",
            "MPE (R2–R5)",
            "Problems with error rate",
            "Mean error rate (R1–R5)",
            "Submissions (R2–R5)",
            "Persistent errors (R2–R5)",
            "Pooled PER (R2–R5)",
        ),
        rows,
    )


def _problem_table(summary):
    rows = []
    for episode in summary.get("episodes", []):
        per = episode.get("per_by_round") or {}
        rows.append(
            (
                episode.get("problem_id", episode.get("episode")),
                *(_rate(per.get(str(r), per.get(r))) for r in range(2, 6)),
                episode.get("eligible_scored_rounds"),
                _rate(episode.get("mpe")),
                episode.get("eligible_error_rounds"),
                _rate(episode.get("mean_error_rate")),
            )
        )
    return _report_table(
        "Results by problem",
        (
            "Problem",
            "R2 PER",
            "R3 PER",
            "R4 PER",
            "R5 PER",
            "Rounds with PER",
            "MPE",
            "Rounds with error rate",
            "Mean error rate (R1–R5)",
        ),
        rows,
    )


def _submission_details(frozen, record):
    episode, number = record.get("episode"), record.get("round")
    rules = problem_rules(frozen, episode)
    moves = record.get("moves")
    if type(number) is int and 1 <= number <= len(rules) and isinstance(rules[number - 1], dict):
        positions = rules[number - 1].get("initial_positions")
        plan = " · ".join(
            describe_moves(moves, initial_positions=positions) or ["Invalid plan format"]
        )
    else:
        plan = " → ".join(map(str, moves)) if isinstance(moves, list) else "Invalid plan format"
    label = (
        f"#{record.get('call')} · {record.get('problem_id', f'E{episode}')} · "
        f"{condition_text(problem_condition(frozen, episode))} · "
        f"R{number}, attempt {record.get('attempt')} · {record.get('feedback')}"
    )
    fields = (
        "feedback_policy",
        "feedback_code",
        "feedback",
        "correctness_feedback",
        "current_success",
        "previous_success",
        "previous_rule_compliant",
        "persistent_error",
        "movement_rule_error",
        "movement_rule_violations",
        "goal_error",
        "goal_reached",
        "base_valid",
        "base_error",
        "success_streak",
        "round_success",
        "parse_valid",
        "elapsed_seconds",
        "response_id",
        "usage",
        "reasoning_replay",
    )
    checks = {key: record[key] for key in fields if key in record}
    return (
        f"<details><summary>{_text(label)} · Persistent error: {_text(record.get('persistent_error'))}</summary>"
        f"<p class='plan'>{_text(plan)}</p><p>{_text(movement_diagnostics(record))}</p>"
        f"<h3>Grading record</h3><pre>{_pretty(checks)}</pre>"
        f"<h3>Model output</h3><pre>{_text(record.get('raw_output'))}</pre></details>"
    )


def html_text(data):
    """Render a self-contained report from one saved run."""
    summary = data.get("summary", {})
    config = data.get("config", {})
    records = data.get("records", [])
    protocol = data.get("protocol", {})
    frozen = protocol.get("frozen", protocol)
    feedback_policy = policy_name(
        frozen.get("feedback_policy", config.get("feedback_policy", BINARY))
    )
    labels = frozen.get("feedback_values")
    if (
        not isinstance(labels, list)
        or not labels
        or not all(isinstance(label, str) for label in labels)
    ):
        try:
            labels = feedback_values(feedback_policy)
        except ValueError:
            labels = ("See the saved feedback policy",)
    feedback_labels = " / ".join(_text(label) for label in labels)
    phase = data.get("state", {}).get("phase", "ready")
    pegs = frozen.get("pegs", [])
    puzzle = "Invalid board configuration in the log"
    if isinstance(pegs, (list, tuple)) and all(isinstance(peg, str) for peg in pegs):
        puzzle = (
            f"{_text(frozen.get('disk_count', 3))} disks · {len(pegs)} pegs "
            f"({_text(', '.join(pegs))}) · New starting board and hidden goal each round"
        )
    r1, r2 = round_policy(frozen, 1), round_policy(frozen, 2)
    diagnostics = "".join(
        f"<li>{_text(message)}</li>"
        for message in data.get("errors", []) + data.get("warnings", [])
    )
    warning_list = f"<ul class='warning'>{diagnostics}</ul>" if diagnostics else ""
    scored = [record for record in records if record.get("round", 0) > 1]
    missed = sum(record.get("goal_error") is True for record in scored)
    unknown = sum(record.get("goal_error") is None for record in scored)
    failures = data.get("generation_failures")
    failure_panel = ""
    if failures:
        failure_panel = (
            "<h2>Ungraded generation failures</h2><p>The remaining rounds of each affected "
            "problem were skipped. These responses contribute to neither the numerator nor "
            "the denominator of error rates. A truncated response may have incomplete token usage.</p>"
            f"<pre>{_pretty(failures)}</pre>"
        )
    submissions = "".join(_submission_details(frozen, record) for record in records)
    output_note = (
        "The model returns plain text JSON and the local grader checks the plan."
        if config.get("output_format") == "plain"
        else "This run uses API schema constraints."
    )
    previous_note = (
        "The previous_success diagnostic also tests the current goal. The persistent-error metric uses previous-rule compliance alone to evaluate the previous round."
        if policy_name(frozen.get("persistent_error_policy")) == PREVIOUS_RULE
        else "The previous-rule diagnostic replays the plan with the current starting board and goal."
    )
    four_way_note = (
        "A plan that reaches the goal but violates the restriction receives “Correct, but rule violation” and resets the success streak."
        if feedback_policy == FOUR_WAY
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tower of Hanoi experiment · {_text(data.get('name'))}</title>
  <style>{REPORT_CSS}</style>
</head>
<body>
<h1>Tower of Hanoi experiment</h1>
<p>{_text(data.get('name'))} · {_text(RUN_STATUS.get(phase, phase))}</p><p>{puzzle}</p>
<div class="cards">
  <div class="card">Model / reasoning effort<b>{_text(config.get('model'))}</b>{_text(config.get('effort'))}</div>
  <div class="card">Graded submissions<b>{len(records)}</b>
    Completed rounds: {_text(summary.get('completed_rounds'))} / {_text(summary.get('total_rounds'))}.
    Maximum submissions: {_text(summary.get('max_submissions'))}. Skipped rounds: {_text(summary.get('excluded_rounds', 0))}.</div>
  <div class="card">Total MPE<b>{_rate(summary.get('total_mpe'))}</b>
    Problems with a graded R2–R5 submission: {_text(summary.get('metric_eligible_problems'))}</div>
  <div class="card">Mean error rate (R1–R5)<b>{_rate(summary.get('total_mean_error_rate'))}</b>
    Problems with a graded submission: {_text(summary.get('error_metric_eligible_problems'))}</div>
  <div class="card">Pooled error rate (R2–R5)<b>{_rate((summary.get('pooled') or {}).get('failure_rate'))}</b>
    Incorrect submissions divided by all graded submissions in R2–R5</div>
</div>
<p>R1 allows {_text(r1.get('max_attempts'))} attempts and requires {_text(r1.get('success_streak_required'))}
consecutive correct submissions. R2–R5 allow {_text(r2.get('max_attempts'))} attempts per round and require
{_text(r2.get('success_streak_required'))} consecutive correct submissions. Meeting the criterion advances
the round. Exhausting the limit skips the rest of the problem.</p>
<p>Each problem uses one restriction type. The starting board, specific restriction and goal change each round.
Each attempt starts from the round’s initial board with fresh move history. The model retains conversational
and provider-native reasoning history within a problem and starts fresh on the next problem.
{output_note} Feedback: {feedback_labels}. {four_way_note}</p>
<p>{pe_definition(frozen)} {previous_note}</p>
<p>Round PER is persistent errors divided by graded submissions. Problem MPE is the mean of PER across
attempted R2–R5 rounds. Overall and condition MPE average the eligible problem MPEs. Mean error rate
follows the same averaging order with incorrect submissions across R1–R5. Rounds without graded
submissions are excluded. Partial and exhausted rounds contribute their observed submissions.
Interim results may change. Submissions within an experiment are dependent observations.</p>
<p>In R2–R5, {missed} submissions missed the goal, and {unknown} had an undetermined goal outcome.
A goal error and a restriction violation can occur on the same submission.</p>
{warning_list}{_condition_table(summary)}{_problem_table(summary)}{failure_panel}{_round_table(summary, frozen)}
<h2>Submission log</h2>
<p>Select a submission to inspect its moves, model output and grading diagnostics.</p>
{submissions or '<p>No graded submissions yet.</p>'}
<details><summary>Goals and movement restrictions</summary><pre>{_pretty(frozen.get('problems', []))}</pre></details>
<details><summary>Stop information</summary><pre>{_pretty(data.get('stop_info'))}</pre></details>
<details><summary>Console log (last 200 lines)</summary><pre>{_text(data.get('console_tail', ''))}</pre></details>
<footer>Goals, specific restrictions, rounds, failure locations and persistent-error diagnostics are for evaluators.
The model receives the feedback specified above. Context format: {_text(config.get('context_format'))}.</footer>
</body></html>"""


def _atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_reports(directory):
    directory = Path(directory)
    data = snapshot(directory)
    _atomic_text(directory / "summary.html", html_text(data))
    _atomic_text(directory / "submissions.csv", csv_text(data))
    return data
