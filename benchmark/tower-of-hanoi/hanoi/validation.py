"""Independent, API-free validation of selected Hanoi problems.

This module does not import the experiment grader or rules engine. It replays
round witnesses and verifies solvability with an independent stack-based BFS.
The validator's search is never sent to the model or used to generate its answer.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from functools import lru_cache
from itertools import permutations
import json
from typing import Any

PEGS = "ABCD"
RULE_TYPES = ("peg", "occupancy", "source_sequence", "visit")
FAMILIES = ("stacked", "pair12", "pair13", "pair23", "separated")
Stacks = tuple[tuple[int, ...], ...]


class ValidationError(ValueError):
    """A dataset assertion failed, with a path to the offending value."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def initial_family(positions: list[str] | tuple[str, ...]) -> str:
    if len(set(positions)) == 1:
        return "stacked"
    if len(set(positions)) == 3:
        return "separated"
    for first, second in ((0, 1), (0, 2), (1, 2)):
        if positions[first] == positions[second]:
            return f"pair{first + 1}{second + 1}"
    raise ValidationError("initial_positions: cannot classify three disks")


def to_stacks(positions: list[str] | tuple[str, ...]) -> Stacks:
    require(len(positions) == 3 and all(p in PEGS for p in positions),
            "initial_positions must assign disks 1, 2, 3 to A/B/C/D")
    return tuple(tuple(disk for disk in (3, 2, 1) if positions[disk - 1] == peg)
                 for peg in PEGS)


def canonical_rule(rule: dict[str, Any]) -> str:
    normalized = dict(rule)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def canonical_problem_under_peg_relabeling(problem: dict[str, Any]) -> str:
    """Identify an entire five-round problem under all 24 global peg renamings."""
    representations = []
    for ordering in permutations(PEGS):
        rename = dict(zip(PEGS, ordering))
        rounds = []
        for round_data in problem["rounds"]:
            rule = dict(round_data["rule"])
            for field in ("source", "destination", "occupied_peg", "previous_source", "visited_peg"):
                if field in rule:
                    rule[field] = rename[rule[field]]
            rounds.append(([rename[peg] for peg in round_data["initial_positions"]],
                           rename[round_data["goal_peg"]], rule))
        representations.append(json.dumps((problem["rule_type"], rounds), sort_keys=True, separators=(",", ":")))
    return min(representations)


def validate_rule(rule: Any, expected_kind: str, where: str) -> None:
    require(expected_kind in RULE_TYPES, f"{where}: unknown rule type {expected_kind!r}")
    require(isinstance(rule, dict), f"{where}: rule must be an object")
    require(rule.get("kind") == expected_kind, f"{where}: rule kind must equal rule_type")
    fields = {
        "peg": {"kind", "source", "destination"},
        "occupancy": {"kind", "occupied_peg", "source", "destination"},
        "source_sequence": {"kind", "previous_source", "source"},
        "visit": {"kind", "visitor_disk", "visited_peg", "locked_disk"},
    }
    require(set(rule) == fields[expected_kind], f"{where}: unexpected or missing rule fields")
    for field in ("source", "destination", "occupied_peg", "previous_source", "visited_peg"):
        if field in rule:
            require(isinstance(rule[field], str) and rule[field] in tuple(PEGS),
                    f"{where}.{field}: expected one of A/B/C/D")
    if expected_kind in ("peg", "occupancy"):
        require(rule["source"] != rule["destination"], f"{where}: source equals destination")
    if expected_kind == "occupancy":
        require(len({rule["occupied_peg"], rule["source"], rule["destination"]}) == 3,
                f"{where}: occupancy trigger/source/destination must be distinct")
    if expected_kind == "source_sequence":
        require(rule["previous_source"] != rule["source"],
                f"{where}: previous and next source must differ")
    if expected_kind == "visit":
        require(type(rule["visitor_disk"]) is int and rule["visitor_disk"] in (1, 2),
                f"{where}.visitor_disk: expected disk 1 or 2")
        require(type(rule["locked_disk"]) is int and rule["locked_disk"] == 3,
                f"{where}.locked_disk: expected disk 3")


def _initial_visit(stacks: Stacks, rule: dict[str, Any] | None) -> bool:
    return bool(rule and rule["kind"] == "visit"
                and rule["visitor_disk"] in stacks[PEGS.index(rule["visited_peg"])])


def _forbidden(stacks: Stacks, source: int, destination: int, disk: int,
               previous_source: int | None, visited: bool,
               rule: dict[str, Any] | None) -> bool:
    if rule is None:
        return False
    kind = rule["kind"]
    if kind == "peg":
        return PEGS[source] == rule["source"] and PEGS[destination] == rule["destination"]
    if kind == "occupancy":
        return (bool(stacks[PEGS.index(rule["occupied_peg"])])
                and PEGS[source] == rule["source"] and PEGS[destination] == rule["destination"])
    if kind == "source_sequence":
        return (previous_source is not None and PEGS[previous_source] == rule["previous_source"]
                and PEGS[source] == rule["source"])
    if kind == "visit":
        return disk == rule["locked_disk"] and not visited
    raise ValidationError(f"unknown rule kind: {kind}")


def _move(stacks: Stacks, source: int, destination: int) -> Stacks:
    next_stacks = list(stacks)
    next_stacks[source] = stacks[source][:-1]
    next_stacks[destination] = stacks[destination] + (stacks[source][-1],)
    return tuple(next_stacks)


def _next_history(stacks: Stacks, source: int, visited: bool,
                  rule: dict[str, Any] | None) -> tuple[int | None, bool]:
    previous = source if rule and rule["kind"] == "source_sequence" else None
    return previous, visited or _initial_visit(stacks, rule)


@dataclass(frozen=True)
class ReplayResult:
    basic_legal: bool
    goal_reached: bool
    violations: tuple[int, ...]
    final_stacks: Stacks
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.basic_legal and self.goal_reached and not self.violations


def replay(initial_positions: list[str] | tuple[str, ...], goal_peg: str,
           plan: list[str] | tuple[str, ...], rule: dict[str, Any] | None = None) -> ReplayResult:
    """Replay basic-legal moves, recording every added-rule violation (1-based).

    Histories are local to this call. An added-rule violation is still applied
    so a witness can be checked for basic legality and eventual goal completion.
    """
    stacks = to_stacks(initial_positions)
    previous_source = None
    visited = _initial_visit(stacks, rule)
    violations: list[int] = []
    goal_index = PEGS.index(goal_peg)
    for step, command in enumerate(plan, 1):
        error = None
        if (not isinstance(command, str) or len(command) != 2
                or any(peg not in PEGS for peg in command)):
            error = f"step {step}: expected a two-peg command"
        elif command[0] == command[1]:
            error = f"step {step}: source equals destination"
        else:
            source, destination = (PEGS.index(peg) for peg in command)
            if not stacks[source]:
                error = f"step {step}: source is empty"
            elif stacks[destination] and stacks[destination][-1] < stacks[source][-1]:
                error = f"step {step}: larger disk onto smaller disk"
        if error:
            return ReplayResult(False, stacks[goal_index] == (3, 2, 1),
                                tuple(violations), stacks, error)
        disk = stacks[source][-1]
        if _forbidden(stacks, source, destination, disk, previous_source, visited, rule):
            violations.append(step)
        stacks = _move(stacks, source, destination)
        previous_source, visited = _next_history(stacks, source, visited, rule)
    return ReplayResult(True, stacks[goal_index] == (3, 2, 1), tuple(violations), stacks)


@lru_cache(maxsize=None)
def _shortest_plan(positions: tuple[str, ...], goal_peg: str, serialized_rule: str) -> tuple[str, ...] | None:
    rule = json.loads(serialized_rule) if serialized_rule else None
    stacks = to_stacks(positions)
    start = (stacks, None, _initial_visit(stacks, rule))
    queue = deque([(start, ())])
    seen = {start}
    goal_index = PEGS.index(goal_peg)
    while queue:
        (stacks, previous_source, visited), path = queue.popleft()
        if stacks[goal_index] == (3, 2, 1):
            return path
        for source in range(4):
            if not stacks[source]:
                continue
            disk = stacks[source][-1]
            for destination in range(4):
                if source == destination or (stacks[destination] and stacks[destination][-1] < disk):
                    continue
                if _forbidden(stacks, source, destination, disk, previous_source, visited, rule):
                    continue
                following = _move(stacks, source, destination)
                next_previous, next_visited = _next_history(following, source, visited, rule)
                state = (following, next_previous, next_visited)
                if state in seen:
                    continue
                seen.add(state)
                queue.append((state, path + (PEGS[source] + PEGS[destination],)))
    return None


def shortest_plan(initial_positions: list[str] | tuple[str, ...], goal_peg: str,
                  rule: dict[str, Any] | None = None) -> tuple[str, ...] | None:
    """BFS on stacks plus the rule's necessary memory; no move-depth cutoff."""
    return _shortest_plan(tuple(initial_positions), goal_peg, canonical_rule(rule) if rule else "")


def _check_plan(plan: Any, limit: int, where: str) -> None:
    require(isinstance(plan, list), f"{where}: expected a move list")
    require(0 < len(plan) <= limit, f"{where}: expected 1..{limit} moves")
    require(all(isinstance(command, str) and len(command) == 2
                and command[0] in PEGS and command[1] in PEGS for command in plan),
            f"{where}: expected two-peg commands")


def validate_round(round_data: Any, rule_type: str, previous_rule: dict[str, Any] | None,
                   index: int, where: str, max_moves: int = 30) -> dict[str, Any]:
    require(isinstance(round_data, dict), f"{where}: round must be an object")
    require(round_data.get("name") == f"R{index + 1}", f"{where}: wrong round name")
    positions = round_data.get("initial_positions")
    require(isinstance(positions, list) and len(positions) == 3
            and all(isinstance(peg, str) and peg in tuple(PEGS) for peg in positions),
            f"{where}.initial_positions: expected three peg labels")
    family = initial_family(positions)
    require(round_data.get("initial_family") == family, f"{where}: initial_family does not match positions")
    goal = round_data.get("goal_peg")
    require(isinstance(goal, str) and goal in tuple(PEGS), f"{where}.goal_peg: invalid peg")
    require(positions != [goal] * 3, f"{where}: initial state already reaches the goal")
    rule = round_data.get("rule")
    validate_rule(rule, rule_type, f"{where}.rule")
    if rule_type == "visit":
        require(positions[rule["visitor_disk"] - 1] != rule["visited_peg"],
                f"{where}: visit is already unlocked initially")
        require(positions[2] != goal, f"{where}: locked disk 3 must initially differ from goal")
    validation = round_data.get("validation")
    require(isinstance(validation, dict), f"{where}.validation: expected object")
    plan = validation.get("shortest_plan")
    _check_plan(plan, max_moves, f"{where}.shortest_plan")
    outcome = replay(positions, goal, plan, rule)
    require(outcome.success, f"{where}: shortest_plan fails replay: {outcome.error or outcome.violations}")
    actual = shortest_plan(positions, goal, rule)
    require(actual is not None, f"{where}: independent BFS found no solution")
    minimum = validation.get("min_moves")
    require(type(minimum) is int and minimum == len(plan) == len(actual),
            f"{where}: min_moves/shortest_plan do not match independent shortest distance {len(actual)}")
    baseline = shortest_plan(positions, goal)
    baseline_minimum = validation.get("baseline_min_moves")
    require(type(baseline_minimum) is int and baseline_minimum == len(baseline),
            f"{where}: baseline_min_moves does not match independent shortest distance {len(baseline)}")
    witness = validation.get("restriction_witness")
    _check_plan(witness, max_moves, f"{where}.restriction_witness")
    require(len(witness) == baseline_minimum,
            f"{where}: restriction_witness must be a baseline-shortest solution, not a longer detour")
    outcome = replay(positions, goal, witness, rule)
    require(outcome.basic_legal and outcome.goal_reached and bool(outcome.violations),
            f"{where}: restriction_witness must reach current goal under basic rules and violate current rule")
    require("transition_witness" in validation, f"{where}: missing transition_witness")
    transition = validation["transition_witness"]
    if previous_rule is None:
        require(transition is None, f"{where}: R1 transition_witness must be null")
    else:
        _check_plan(transition, max_moves, f"{where}.transition_witness")
        previous_result = replay(positions, goal, transition, previous_rule)
        current_result = replay(positions, goal, transition, rule)
        require(previous_result.success,
                f"{where}: transition_witness fails previous rule from CURRENT initial state/goal")
        require(current_result.basic_legal and current_result.goal_reached and bool(current_result.violations),
                f"{where}: transition_witness must violate current rule")
    return {"initial_family": family, "goal_peg": goal, "min_moves": minimum,
            "baseline_min_moves": baseline_minimum, "rule": canonical_rule(rule)}
