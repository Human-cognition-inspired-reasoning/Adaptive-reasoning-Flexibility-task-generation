"""Search for Hanoi solutions and rule-change witnesses when generating problems."""

from __future__ import annotations

from collections import deque
from functools import lru_cache
from itertools import permutations
import json
from typing import Any, Mapping, Sequence

from hanoi.rules import (
    EDGES,
    PEGS,
    POSITIONS,
    Rule,
    Signature,
    board_index,
    compile_rule,
    forbids_move,
    initial_visit,
    next_visit,
)

MAX_MOVES = 30
INITIAL_FAMILIES = ("stacked", "pair12", "pair13", "pair23", "separated")


def initial_family(positions: Sequence[str]) -> str:
    board_index(positions)
    a, b, c = positions
    if a == b == c:
        return "stacked"
    if a == b:
        return "pair12"
    if a == c:
        return "pair13"
    if b == c:
        return "pair23"
    return "separated"


def all_initial_positions(family: str | None = None) -> list[tuple[str, ...]]:
    if family is not None and family not in INITIAL_FAMILIES:
        raise ValueError(f"Unknown initial family: {family!r}")
    positions = [tuple(PEGS[p] for p in board) for board in POSITIONS]
    return [p for p in positions if family is None or initial_family(p) == family]


@lru_cache(maxsize=100_000)
def _bfs(
    start: int,
    goal: int,
    allowed: Signature | None,
    observed: Signature | None,
    require_violation: bool,
    max_moves: int,
) -> tuple[str, ...] | None:
    """Search on board + previous source + rule-specific visit bits + violation."""
    remembers_source = any(r and r[0] == "source_sequence" for r in (allowed, observed))
    initial = (
        start,
        4,
        initial_visit(allowed, POSITIONS[start]),
        initial_visit(observed, POSITIONS[start]),
        False,
    )
    parents: dict[tuple[Any, ...], tuple[tuple[Any, ...], str] | None] = {initial: None}
    frontier = deque([(initial, 0)])
    while frontier:
        state, depth = frontier.popleft()
        board_id, previous_source, allowed_visit, observed_visit, violated = state
        if POSITIONS[board_id] == (goal, goal, goal) and (violated or not require_violation):
            plan: list[str] = []
            while parents[state] is not None:
                state, move = parents[state]  # type: ignore[misc]
                plan.append(move)
            return tuple(reversed(plan))
        if depth >= max_moves:
            continue
        board = POSITIONS[board_id]
        for move, disk, source, destination, next_board in EDGES[board_id]:
            if forbids_move(
                allowed, board, disk, source, destination, previous_source, allowed_visit
            ):
                continue
            next_violated = violated or forbids_move(
                observed, board, disk, source, destination, previous_source, observed_visit
            )
            next_state = (
                next_board,
                source if remembers_source else 4,
                next_visit(allowed, allowed_visit, disk, destination),
                next_visit(observed, observed_visit, disk, destination),
                next_violated,
            )
            if next_state not in parents:
                parents[next_state] = (state, move)
                frontier.append((next_state, depth + 1))
    return None


def _search(
    initial_positions: Sequence[str],
    goal_peg: str,
    *,
    allowed: Rule | None,
    observed: Rule | None,
    require_violation: bool,
    max_moves: int,
) -> list[str] | None:
    if goal_peg not in PEGS or max_moves < 0:
        raise ValueError("Invalid goal peg or move limit")
    result = _bfs(
        board_index(initial_positions),
        PEGS.index(goal_peg),
        compile_rule(allowed),
        compile_rule(observed),
        require_violation,
        max_moves,
    )
    return None if result is None else list(result)


def shortest_plan(
    initial_positions: Sequence[str],
    goal_peg: str,
    rule: Rule | None = None,
    max_moves: int = MAX_MOVES,
) -> list[str] | None:
    """Shortest basic-legal solution satisfying the optional additional rule."""
    return _search(
        initial_positions,
        goal_peg,
        allowed=rule,
        observed=None,
        require_violation=False,
        max_moves=max_moves,
    )


def restriction_witness(
    initial_positions: Sequence[str], goal_peg: str, rule: Rule, max_moves: int = MAX_MOVES
) -> list[str] | None:
    """Shortest basic-legal goal-reaching plan violating the current rule."""
    return _search(
        initial_positions,
        goal_peg,
        allowed=None,
        observed=rule,
        require_violation=True,
        max_moves=max_moves,
    )


def transition_witness(
    initial_positions: Sequence[str],
    goal_peg: str,
    previous_rule: Rule,
    current_rule: Rule,
    max_moves: int = MAX_MOVES,
) -> list[str] | None:
    """Find a current-board plan obeying the previous restriction but violating the current one."""
    return _search(
        initial_positions,
        goal_peg,
        allowed=previous_rule,
        observed=current_rule,
        require_violation=True,
        max_moves=max_moves,
    )


def permute_rule(rule: Rule, mapping: Mapping[str, str]) -> dict[str, Any]:
    result = dict(rule)
    for field in ("source", "destination", "occupied_peg", "previous_source", "visited_peg"):
        if field in result:
            result[field] = mapping[result[field]]
    return result


def canonical_episode(problem: Mapping[str, Any]) -> str:
    """Episode identity modulo all 24 global peg renamings; ignore witnesses."""
    representations = []
    for permutation in permutations(PEGS):
        mapping = dict(zip(PEGS, permutation))
        rounds = [
            {
                "initial_positions": [mapping[p] for p in round_["initial_positions"]],
                "goal_peg": mapping[round_["goal_peg"]],
                "rule": permute_rule(round_["rule"], mapping),
            }
            for round_ in problem["rounds"]
        ]
        representations.append(
            json.dumps(
                {"rule_type": problem["rule_type"], "rounds": rounds},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return min(representations)
