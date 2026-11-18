"""Parse and grade complete Hanoi plans under the current and previous restrictions."""

from dataclasses import dataclass, replace
import json

from .policies import BINARY, FOUR_WAY, PREVIOUS_RULE, policy_name
from .prompts import feedback_values
from .rules import DISK_COUNT, PEGS, compile_rule, forbids_move, initial_visit, next_visit

MAX_MOVES = 30
COMMANDS = tuple(source + target for source in PEGS for target in PEGS if source != target)
PERSISTENT_ERROR_POLICY = PREVIOUS_RULE


class ExperimentError(RuntimeError):
    """An invalid benchmark setting or inconsistent checkpoint prevents evaluation."""


@dataclass(frozen=True)
class Rule:
    name: str
    goal_peg: str = "C"
    candidate_rule: dict | None = None
    initial_positions: tuple[str, ...] = ("A", "A", "A")
    initial_family: str | None = None


@dataclass(frozen=True)
class Problem:
    id: str
    rules: tuple[Rule, ...]
    condition: str


def get_problem(problem_number, problems):
    pool = problems
    if type(problem_number) is not int or not 1 <= problem_number <= len(pool):
        raise ValueError(f"The problem index must be between 1 and {len(pool)}.")
    return pool[problem_number - 1]


def parse_plan(raw):
    """Parse a plan without repairing its format or requesting another answer."""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict) or set(value) != {"moves"}:
        return None
    moves = value["moves"]
    if not isinstance(moves, list) or not 1 <= len(moves) <= MAX_MOVES:
        return None
    return moves if all(isinstance(m, str) and m in COMMANDS for m in moves) else None


def evaluate_plan(moves, rule):
    """Evaluate movement restrictions along the physically executable attempted path."""
    restriction = compile_rule(rule.candidate_rule)
    positions = [PEGS.index(peg) for peg in rule.initial_positions]
    pegs = {peg: [d for d in (3, 2, 1) if rule.initial_positions[d - 1] == peg] for peg in PEGS}
    previous_source = 4
    visited = initial_visit(restriction, tuple(positions))
    violations, base_error = [], None
    if not isinstance(moves, (list, tuple)) or not 1 <= len(moves) <= MAX_MOVES:
        base_error = {"step": None, "move": None, "disk": None, "kind": "invalid_plan"}
    for step, move in enumerate(moves if base_error is None else (), 1):
        if not isinstance(move, str) or move not in COMMANDS:
            base_error = {"step": step, "move": move, "disk": None, "kind": "invalid_command"}
            break
        source, target = move
        disk = pegs[source][-1] if pegs[source] else None
        source_id, target_id = PEGS.index(source), PEGS.index(target)
        # A nonexistent disk cannot trigger a disk-identity restriction. Peg, occupancy,
        # and source-sequence restrictions are identifiable even on an empty source.
        if forbids_move(
            restriction,
            tuple(positions),
            disk - 1 if disk is not None else -1,
            source_id,
            target_id,
            previous_source,
            visited,
        ):
            violations.append({"step": step, "move": move, "disk": disk, "kind": restriction[0]})
        if disk is None:
            base_error = {"step": step, "move": move, "disk": None, "kind": "empty_source"}
            break
        if pegs[target] and disk > pegs[target][-1]:
            base_error = {"step": step, "move": move, "disk": disk, "kind": "larger_on_smaller"}
            break
        pegs[target].append(pegs[source].pop())
        positions[disk - 1] = target_id
        previous_source = source_id
        visited = next_visit(restriction, visited, disk - 1, target_id)
    base_valid = base_error is None
    goal_reached = pegs[rule.goal_peg] == [3, 2, 1] if base_valid else None
    return {
        "current_success": bool(base_valid and not violations and goal_reached),
        "base_valid": base_valid,
        "base_error": base_error,
        "movement_rule_violations": violations,
        "movement_rule_error": bool(violations),
        "goal_reached": goal_reached,
        "goal_error": not goal_reached if base_valid else None,
    }


def is_success(moves, rule):
    """Require basic legality, additional movement restrictions, and the final goal."""
    return evaluate_plan(moves, rule)["current_success"]


def grade(raw, round_number, problem_number=1, problem=None, *, feedback_policy=BINARY):
    feedback_policy = policy_name(feedback_policy)
    feedback_values(feedback_policy)
    moves = parse_plan(raw)
    if problem is None:
        raise ExperimentError("Grading requires a problem.")
    rules = problem.rules
    current = evaluate_plan(moves, rules[round_number - 1])
    previous_rule = rules[round_number - 2] if round_number > 1 else None
    if previous_rule is not None:
        previous_rule = replace(
            previous_rule,
            initial_positions=rules[round_number - 1].initial_positions,
            goal_peg=rules[round_number - 1].goal_peg,
        )
    previous = evaluate_plan(moves, previous_rule) if previous_rule is not None else None
    # Check the previous restriction on the current board, without requiring a goal.
    previous_compliant = (
        previous["base_valid"] and not previous["movement_rule_error"]
        if previous is not None
        else None
    )
    result = {
        "moves": moves,
        "parse_valid": moves is not None,
        **current,
        "previous_success": previous["current_success"] if previous is not None else None,
        "previous_rule_compliant": previous_compliant,
        "persistent_error": (
            previous_compliant and not current["current_success"] if round_number > 1 else None
        ),
        "persistent_error_policy": PERSISTENT_ERROR_POLICY,
        "feedback": "Correct" if current["current_success"] else "Incorrect",
    }
    if feedback_policy == FOUR_WAY:
        if current["current_success"]:
            code, feedback = "correct", "Correct"
        elif current["movement_rule_error"]:
            if current["base_valid"] and current["goal_reached"]:
                code, feedback = "goal_reached_rule_violation", "Correct, but rule violation"
            else:
                code, feedback = "rule_violation", "Rule violation"
        else:
            code, feedback = "incorrect", "Incorrect"
        result.update(
            correctness_feedback=result["feedback"],
            feedback=feedback,
            feedback_code=code,
            feedback_policy=feedback_policy,
        )
    return result
