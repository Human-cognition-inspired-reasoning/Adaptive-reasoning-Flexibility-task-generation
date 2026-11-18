"""Generate new Hanoi task candidates from rule templates and a reproducible seed.

Each problem has five rounds of one rule type. Solutions and rule-transition
witnesses are found by local graph search; no model API or experiment logs are
used. New candidates are written separately from any fixed problem bank.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
from typing import Any

from hanoi.rules import PEGS, RULE_TYPES, rule_key, rule_pool

from .rules import (
    INITIAL_FAMILIES,
    MAX_MOVES,
    all_initial_positions,
    canonical_episode,
    restriction_witness,
    shortest_plan,
    transition_witness,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TYPES = ("peg", "source_sequence", "visit")
DEFAULT_PER_TYPE = 100
DEFAULT_SEED = 20261029
DEFAULT_OUTPUT = ROOT / "generated/problems.json"
MAX_PROBLEMS = 300


def _schedule(rng: random.Random) -> tuple[list[str], list[str]]:
    families = list(INITIAL_FAMILIES)
    rng.shuffle(families)
    goals = list(PEGS) + [rng.choice(PEGS)]
    while True:
        rng.shuffle(goals)
        if all(a != b for a, b in zip(goals, goals[1:])):
            return families, goals


def _make_round(
    rng: random.Random,
    number: int,
    family: str,
    goal: str,
    rule: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any] | None:
    positions = all_initial_positions(family)
    rng.shuffle(positions)
    for initial in positions:
        if initial == (goal,) * 3:
            continue
        if rule["kind"] == "visit" and (
            initial[rule["visitor_disk"] - 1] == rule["visited_peg"] or initial[2] == goal
        ):
            continue
        solution = shortest_plan(initial, goal, rule)
        if solution is None:
            continue
        baseline = shortest_plan(initial, goal)
        witness = restriction_witness(initial, goal, rule)
        if witness is None or len(witness) != len(baseline):
            continue
        transition = transition_witness(initial, goal, previous, rule) if previous else None
        if previous is not None and transition is None:
            continue
        return {
            "name": f"R{number}",
            "initial_positions": list(initial),
            "initial_family": family,
            "goal_peg": goal,
            "rule": dict(rule),
            "validation": {
                "shortest_plan": solution,
                "min_moves": len(solution),
                "baseline_min_moves": len(baseline),
                "restriction_witness": witness,
                "transition_witness": transition,
            },
        }
    return None


def _make_problem(
    rng: random.Random,
    kind: str,
    identifier: str,
    remaining: Counter[str],
    future_problems: int,
    seen: set[str],
) -> dict[str, Any] | None:
    families, goals = _schedule(rng)
    pool = {rule_key(rule): rule for rule in rule_pool(kind)}
    visits = 0

    def choose(rounds: list[dict[str, Any]], used: set[str]) -> dict[str, Any] | None:
        nonlocal visits
        visits += 1
        if visits > 20_000:
            return None
        if len(rounds) == 5:
            problem = {"id": identifier, "rule_type": kind, "rounds": rounds}
            return problem if canonical_episode(problem) not in seen else None
        candidates = [key for key in pool if remaining[key] > 0 and key not in used]
        rng.shuffle(candidates)
        candidates.sort(key=lambda key: -remaining[key])
        for key in candidates:
            remaining[key] -= 1
            next_used = used | {key}
            # Every variant can occur once in this problem and once per future problem.
            possible = all(
                count <= future_problems + (other not in next_used)
                for other, count in remaining.items()
            )
            if possible:
                i = len(rounds)
                round_ = _make_round(
                    rng,
                    i + 1,
                    families[i],
                    goals[i],
                    pool[key],
                    rounds[-1]["rule"] if rounds else None,
                )
                if round_ is not None:
                    found = choose(rounds + [round_], next_used)
                    if found is not None:
                        return found
            remaining[key] += 1
        return None

    return choose([], set())


def build_dataset(
    seed: int = DEFAULT_SEED,
    *,
    types: tuple[str, ...] | list[str] = DEFAULT_TYPES,
    per_type: int = DEFAULT_PER_TYPE,
) -> dict[str, Any]:
    """Return a new task bank; equal rule-variant frequencies differ by at most one."""
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if (
        not isinstance(types, (tuple, list))
        or not types
        or any(not isinstance(kind, str) or kind not in RULE_TYPES for kind in types)
        or len(set(types)) != len(types)
    ):
        raise ValueError("types must be a nonempty list of distinct supported rule types")
    if type(per_type) is not int or per_type < 1 or len(types) * per_type > MAX_PROBLEMS:
        raise ValueError(
            f"per_type must be positive and the total problem count must not exceed {MAX_PROBLEMS}"
        )
    kinds = tuple(types)
    rng = random.Random(seed)
    problems = []
    seen: set[str] = set()
    for kind in kinds:
        pool = rule_pool(kind)
        lower, extras = divmod(per_type * 5, len(pool))
        # Retry a whole type only if the residual quotas cannot be completed.
        for _ in range(100):
            keys = [rule_key(rule) for rule in pool]
            rng.shuffle(keys)
            remaining = Counter({key: lower + (i < extras) for i, key in enumerate(keys)})
            candidates = []
            local_seen = set(seen)
            for index in range(per_type):
                problem = None
                for _ in range(100):
                    problem = _make_problem(
                        rng,
                        kind,
                        f"P{len(problems) + index + 1:03d}",
                        remaining,
                        per_type - index - 1,
                        local_seen,
                    )
                    if problem is not None:
                        break
                if problem is None:
                    break
                candidates.append(problem)
                local_seen.add(canonical_episode(problem))
            if len(candidates) == per_type and not any(remaining.values()):
                problems.extend(candidates)
                seen = local_seen
                break
        else:
            raise RuntimeError(
                f"Unable to generate unique, balanced {kind} problems with this seed"
            )
    usage = {
        kind: Counter(
            rule_key(r["rule"]) for p in problems if p["rule_type"] == kind for r in p["rounds"]
        )
        for kind in kinds
    }
    return {
        "dataset_id": "hanoi_generated",
        "schema_version": 1,
        "dataset_version": 7,
        "dataset_revision": 1,
        "seed": seed,
        "pegs": list(PEGS),
        "disk_count": 3,
        "max_moves": MAX_MOVES,
        "metadata": {
            "type_order": list(kinds),
            "problem_count": len(problems),
            "round_count": len(problems) * 5,
            "problems_per_rule_type": per_type,
            "rounds_per_problem": 5,
            "rules_per_round": 1,
            "rule_usage": {
                kind: [
                    {"rule": rule, "round_count": usage[kind][rule_key(rule)]}
                    for rule in rule_pool(kind)
                    if usage[kind][rule_key(rule)] > 0
                ]
                for kind in kinds
            },
            "rule_frequency_balance": {
                "unit": "round",
                "scope": "within_rule_type_across_R1_R5",
                "max_count_difference": 1,
                "round_position_balance_required": False,
                "target_counts_by_type": {
                    kind: {
                        "variant_count": len(rule_pool(kind)),
                        "round_count": per_type * 5,
                        "minimum": per_type * 5 // len(rule_pool(kind)),
                        "maximum": (per_type * 5 + len(rule_pool(kind)) - 1)
                        // len(rule_pool(kind)),
                    }
                    for kind in kinds
                },
            },
        },
        "problems": problems,
    }


def encode_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_dataset(path: Path, dataset: dict[str, Any]) -> bool:
    """Write a new file, leave identical bytes alone, and reject changed contents."""
    path = path.expanduser()
    contents = encode_json(dataset)
    if path.exists():
        if not path.is_file() or path.read_bytes() != contents:
            raise ValueError(f"Refusing to overwrite an existing different file: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(contents)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--types",
        nargs="+",
        choices=RULE_TYPES,
        default=list(DEFAULT_TYPES),
        help="Rule types to generate (default: peg source_sequence visit)",
    )
    parser.add_argument(
        "--per-type",
        type=int,
        default=DEFAULT_PER_TYPE,
        help="Problems per selected type; total at most 300 (default: 100)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Separate JSON destination (default: generated/problems.json)",
    )
    args = parser.parse_args(argv)
    try:
        dataset = build_dataset(args.seed, types=args.types, per_type=args.per_type)
        from .validate import validate_dataset

        validate_dataset(dataset)
        written = write_dataset(args.output, dataset)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Generation failed: {exc}\n")
    print(f"Validated {len(dataset['problems'])} new task candidates: {args.output}")
    print("Written." if written else "Identical file left unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
