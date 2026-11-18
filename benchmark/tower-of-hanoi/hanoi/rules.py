"""Movement restrictions and plan replay for the four Hanoi rule types.

This module does not import or alter the experiment runner.  A position tuple is
ordered by disk number (smallest first).  Basic Hanoi legality and additional
rule violations are deliberately separate: replaying a basic-legal forbidden
move still updates the board and the rule's history.
"""

from __future__ import annotations

from itertools import permutations, product
import json
from typing import Any, Iterable, Mapping, Sequence

PEGS = ("A", "B", "C", "D")
DISK_COUNT = 3
RULE_TYPES = ("peg", "occupancy", "source_sequence", "visit")
Rule = Mapping[str, Any]
Signature = tuple[Any, ...]
POSITIONS = tuple(product(range(4), repeat=DISK_COUNT))
POSITION_INDEX = {positions: index for index, positions in enumerate(POSITIONS)}


def rule_pool(kind: str) -> list[dict[str, Any]]:
    """Return every distinct rule variant, in deterministic order."""
    if kind == "peg":
        return [dict(kind=kind, source=s, destination=d) for s, d in permutations(PEGS, 2)]
    if kind == "occupancy":
        return [
            dict(kind=kind, occupied_peg=x, source=y, destination=z)
            for x, y, z in permutations(PEGS, 3)
        ]
    if kind == "source_sequence":
        return [dict(kind=kind, previous_source=x, source=y) for x, y in permutations(PEGS, 2)]
    if kind == "visit":
        return [
            dict(kind=kind, visitor_disk=disk, visited_peg=peg, locked_disk=3)
            for disk in (1, 2)
            for peg in PEGS
        ]
    raise ValueError(f"Unknown rule type: {kind!r}")


def rule_key(rule: Rule) -> str:
    """Stable identity of one additional movement restriction."""
    normalized = dict(rule)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def board_index(positions: Sequence[str]) -> int:
    if len(positions) != DISK_COUNT or any(p not in PEGS for p in positions):
        raise ValueError("positions must contain three pegs, ordered disk 1, 2, 3")
    return POSITION_INDEX[tuple(PEGS.index(p) for p in positions)]


def compile_rule(rule: Rule | None) -> Signature | None:
    if rule is None:
        return None
    key = rule_key(rule)
    kind = rule.get("kind")
    if kind not in RULE_TYPES or key not in _RULE_SIGNATURES:
        raise ValueError(f"Invalid additional rule: {dict(rule)!r}")
    return _RULE_SIGNATURES[key]


def _compile(rule: Rule) -> Signature:
    kind = rule["kind"]
    if kind == "peg":
        return kind, PEGS.index(rule["source"]), PEGS.index(rule["destination"])
    if kind == "occupancy":
        return (
            kind,
            PEGS.index(rule["occupied_peg"]),
            PEGS.index(rule["source"]),
            PEGS.index(rule["destination"]),
        )
    if kind == "source_sequence":
        return kind, PEGS.index(rule["previous_source"]), PEGS.index(rule["source"])
    return kind, rule["visitor_disk"] - 1, PEGS.index(rule["visited_peg"]), rule["locked_disk"] - 1


_RULE_SIGNATURES = {
    rule_key(rule): _compile(rule) for kind in RULE_TYPES for rule in rule_pool(kind)
}


def _board_edges(board: tuple[int, ...]) -> tuple[tuple[str, int, int, int, int], ...]:
    top = {peg: next((d for d, p in enumerate(board) if p == peg), None) for peg in range(4)}
    result = []
    for source in range(4):
        disk = top[source]
        if disk is None:
            continue
        for destination in range(4):
            if source == destination:
                continue
            target_disk = top[destination]
            if target_disk is not None and target_disk < disk:
                continue
            changed = list(board)
            changed[disk] = destination
            result.append(
                (
                    PEGS[source] + PEGS[destination],
                    disk,
                    source,
                    destination,
                    POSITION_INDEX[tuple(changed)],
                )
            )
    return tuple(result)


EDGES = tuple(_board_edges(board) for board in POSITIONS)


def initial_visit(rule: Signature | None, board: tuple[int, ...]) -> bool:
    return bool(rule and rule[0] == "visit" and board[rule[1]] == rule[2])


def next_visit(rule: Signature | None, visited: bool, disk: int, destination: int) -> bool:
    return bool(
        rule and rule[0] == "visit" and (visited or (disk == rule[1] and destination == rule[2]))
    )


def forbids_move(
    rule: Signature | None,
    board: tuple[int, ...],
    disk: int,
    source: int,
    destination: int,
    previous_source: int,
    visited: bool,
) -> bool:
    if rule is None:
        return False
    if rule[0] == "peg":
        return source == rule[1] and destination == rule[2]
    if rule[0] == "occupancy":
        return rule[1] in board and source == rule[2] and destination == rule[3]
    if rule[0] == "source_sequence":
        return previous_source == rule[1] and source == rule[2]
    return disk == rule[3] and not visited


def replay_plan(
    initial_positions: Sequence[str], goal_peg: str, plan: Iterable[str], rule: Rule | None = None
) -> dict[str, Any]:
    """Replay a fresh attempt, retaining restriction violations separately.

    The initial placement counts as a visit.  No history survives between calls.
    Invalid basic moves stop the replay and are not executed.  A basic-legal
    additional-rule violation IS executed and updates previous-source/visit data.
    """
    board_id = board_index(initial_positions)
    if goal_peg not in PEGS:
        raise ValueError(f"Invalid goal peg: {goal_peg!r}")
    signature = compile_rule(rule)
    previous_source = 4
    visited = initial_visit(signature, POSITIONS[board_id])
    violations: list[int] = []
    steps: list[dict[str, Any]] = []
    error = None
    moves = list(plan)
    for index, move in enumerate(moves, 1):
        edge = next((edge for edge in EDGES[board_id] if edge[0] == move), None)
        if edge is None:
            error = {"step": index, "move": move, "reason": "invalid basic Hanoi move"}
            break
        _, disk, source, destination, next_board = edge
        violates = forbids_move(
            signature, POSITIONS[board_id], disk, source, destination, previous_source, visited
        )
        if violates:
            violations.append(index)
        visited = next_visit(signature, visited, disk, destination)
        previous_source = source
        board_id = next_board
        steps.append(
            {
                "step": index,
                "move": move,
                "disk": disk + 1,
                "rule_violation": violates,
                "positions": [PEGS[p] for p in POSITIONS[board_id]],
            }
        )
    return {
        "base_legal": error is None,
        "goal_reached": error is None and POSITIONS[board_id] == (PEGS.index(goal_peg),) * 3,
        "rule_compliant": error is None and not violations,
        "rule_violation_steps": violations,
        "final_positions": [PEGS[p] for p in POSITIONS[board_id]],
        "move_count": len(moves),
        "executed_move_count": len(steps),
        "basic_error": error,
        "steps": steps,
    }
