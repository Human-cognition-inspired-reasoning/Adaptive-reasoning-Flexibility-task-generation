"""Validate Hanoi datasets with independent stack replay and history-aware BFS.

Validation is local and does not import the generator or experiment grader.
Run ``python validate_problems.py [dataset.json]`` to inspect a dataset.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from itertools import permutations
import json
from pathlib import Path
import re
from typing import Any

from hanoi.validation import (
    FAMILIES, PEGS, RULE_TYPES, ValidationError, canonical_problem_under_peg_relabeling,
    canonical_rule, require, validate_round, validate_rule,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "benchmark_300.json"
PROTECTED_DATASETS = (DEFAULT_DATASET, ROOT / "human" / "problems.json")


def rule_universe(kind: str) -> tuple[str, ...]:
    """Enumerate allowed variants independently of the generation engine."""
    if kind == "peg":
        rules = ({"kind": kind, "source": a, "destination": b} for a, b in permutations(PEGS, 2))
    elif kind == "occupancy":
        rules = ({"kind": kind, "occupied_peg": occupied, "source": a, "destination": b}
                 for occupied, a, b in permutations(PEGS, 3))
    elif kind == "source_sequence":
        rules = ({"kind": kind, "previous_source": a, "source": b} for a, b in permutations(PEGS, 2))
    elif kind == "visit":
        rules = ({"kind": kind, "visitor_disk": disk, "visited_peg": peg, "locked_disk": 3}
                 for disk in (1, 2) for peg in PEGS)
    else:
        raise ValidationError(f"unknown rule kind: {kind}")
    return tuple(sorted(canonical_rule(rule) for rule in rules))


def _sorted_counts(counts: Counter) -> dict[str, int]:
    return {str(key): counts[key] for key in sorted(counts)}


def _type_keys(value: Any, actual_types: set[str], where: str) -> None:
    require(isinstance(value, dict) and set(value).issubset(RULE_TYPES)
            and actual_types.issubset(value),
            f"{where}: expected legal rule types including every observed type")


def _type_order(order: Any, by_type: dict[str, list[str]], ids: list[str], where: str) -> None:
    require(isinstance(order, list)
            and all(isinstance(kind, str) and kind in RULE_TYPES for kind in order),
            f"{where}: expected a list of legal rule types")
    require(len(order) == len(set(order)) and set(by_type).issubset(order),
            f"{where}: duplicate or missing observed rule type")
    require([pid for kind in order for pid in by_type.get(kind, [])] == ids,
            f"{where}: declared type order does not match problem order")


def validate_rule_frequencies(actual_by_type: dict[str, Counter], metadata: dict[str, Any]) -> dict[str, Any]:
    """Check usage counts; enforce floor/ceiling balance only when declared."""
    actual_types = set(actual_by_type)
    usage = metadata.get("rule_usage")
    _type_keys(usage, actual_types, "metadata.rule_usage")
    for kind, entries in usage.items():
        require(isinstance(entries, list), f"metadata.rule_usage.{kind}: expected list")
        recorded = {}
        for index, entry in enumerate(entries):
            where = f"metadata.rule_usage.{kind}[{index}]"
            require(isinstance(entry, dict), f"{where}: expected object")
            validate_rule(entry.get("rule"), kind, f"{where}.rule")
            key = canonical_rule(entry["rule"])
            require(key not in recorded, f"{where}: duplicate rule usage entry")
            count = entry.get("round_count")
            require(type(count) is int and count >= 0, f"{where}: expected nonnegative integer count")
            recorded[key] = count
        require({key: value for key, value in recorded.items() if value} == dict(actual_by_type.get(kind, {})),
                f"metadata.rule_usage.{kind}: counts do not match actual rounds")
    if "rule_variant_counts" in metadata:
        declared = metadata["rule_variant_counts"]
        _type_keys(declared, actual_types, "metadata.rule_variant_counts")
        require(all(type(value) is int and value == len(rule_universe(kind)) for kind, value in declared.items()),
                "metadata.rule_variant_counts: does not match independently enumerated variants")
    enforce_balance = "rule_frequency_balance" in metadata
    if enforce_balance:
        balance = metadata["rule_frequency_balance"]
        require(isinstance(balance, dict), "metadata.rule_frequency_balance: expected object")
        require(balance.get("unit", "round") == "round"
                and balance.get("scope", "within_rule_type_across_R1_R5") == "within_rule_type_across_R1_R5"
                and type(balance.get("max_count_difference", 1)) is int
                and balance.get("max_count_difference", 1) == 1
                and balance.get("round_position_balance_required", False) is False,
                "metadata.rule_frequency_balance: unsupported balance policy")
    summaries, targets = {}, {}
    for kind, counts in actual_by_type.items():
        universe = rule_universe(kind)
        rounds = sum(counts.values())
        lower, remainder = divmod(rounds, len(universe))
        upper = lower + bool(remainder)
        frequencies = [counts[rule] for rule in universe]
        balanced = all(lower <= value <= upper for value in frequencies)
        if enforce_balance:
            require(balanced, f"dataset.{kind}: rule frequency balance requires each variant {lower}..{upper} times")
        targets[kind] = {"variant_count": len(universe), "round_count": rounds,
                         "minimum": lower, "maximum": upper}
        summaries[kind] = {
            "variant_count": len(universe), "observed_variant_count": len(counts), "round_count": rounds,
            "target_min_count": lower, "target_max_count": upper,
            "actual_min_count": min(frequencies), "actual_max_count": max(frequencies),
            "actual_count_histogram": _sorted_counts(Counter(frequencies)),
            "balance_required": enforce_balance, "balanced": balanced,
        }
    if enforce_balance and "target_counts_by_type" in balance:
        require(balance["target_counts_by_type"] == targets,
                "metadata.rule_frequency_balance.target_counts_by_type: incorrect dynamic quotas")
    return summaries


def _validate_metadata(metadata: Any, problems: list[dict[str, Any]], type_counts: Counter) -> None:
    require(isinstance(metadata, dict), "dataset.metadata: expected object")
    for field, actual in (("problem_count", len(problems)), ("round_count", len(problems) * 5)):
        require(type(metadata.get(field)) is int and metadata[field] == actual,
                f"metadata.{field}: does not match actual data")
    for field, actual in (("rounds_per_problem", 5), ("rules_per_round", 1)):
        if field in metadata:
            require(type(metadata[field]) is int and metadata[field] == actual,
                    f"metadata.{field}: expected {actual}")
    if "problems_per_rule_type" in metadata:
        declared = metadata["problems_per_rule_type"]
        if type(declared) is int:
            valid = all(count == declared for count in type_counts.values())
        else:
            valid = (isinstance(declared, dict) and set(declared) == set(type_counts)
                     and all(type(value) is int for value in declared.values()) and declared == dict(type_counts))
        require(valid, "metadata.problems_per_rule_type: does not match actual data")
    ids = [problem["id"] for problem in problems]
    by_type = {kind: [p["id"] for p in problems if p["rule_type"] == kind] for kind in type_counts}
    for field in ("type_order", "types"):
        if field in metadata:
            _type_order(metadata[field], by_type, ids, f"metadata.{field}")
    if "selection" in metadata:
        selection = metadata["selection"]
        require(isinstance(selection, dict), "metadata.selection: expected object")
        require(selection.get("selected_problem_ids") == ids,
                "metadata.selection.selected_problem_ids: does not match actual problem order")
        order = selection.get("type_order")
        _type_order(order, by_type, ids, "metadata.selection.type_order")
        require(selection.get("problem_ids_by_type") == {kind: by_type.get(kind, []) for kind in order},
                "metadata.selection.problem_ids_by_type: does not match actual data")


def validate_dataset(dataset: Any) -> dict[str, Any]:
    """Validate 1–300 five-round problems, their witnesses and declared metadata."""
    require(isinstance(dataset, dict), "dataset: expected object")
    for field, expected in (("schema_version", 1), ("dataset_version", 7), ("pegs", list(PEGS)),
                            ("disk_count", 3), ("max_moves", 30)):
        require(type(dataset.get(field)) is type(expected) and dataset[field] == expected,
                f"dataset.{field}: expected {expected!r}")
    require(isinstance(dataset.get("dataset_id"), str) and bool(dataset["dataset_id"].strip()),
            "dataset.dataset_id: expected nonempty string")
    require(type(dataset.get("dataset_revision")) is int and dataset["dataset_revision"] >= 1,
            "dataset.dataset_revision: expected positive integer")
    require(type(dataset.get("seed")) is int, "dataset.seed: expected integer")
    if "experiment_defaults" in dataset:
        defaults = dataset["experiment_defaults"]
        require(isinstance(defaults, dict), "dataset.experiment_defaults: expected object")
        require(type(defaults.get("T")) is int and type(defaults.get("k")) is int
                and 1 <= defaults["k"] <= defaults["T"] and type(defaults.get("r1_apply_k")) is bool
                and type(defaults.get("max_output_tokens")) is int and defaults["max_output_tokens"] > 0,
                "dataset.experiment_defaults: expected 1 <= k <= T, boolean r1_apply_k and positive token budget")
    problems = dataset.get("problems")
    require(isinstance(problems, list) and 1 <= len(problems) <= 300,
            "dataset.problems: expected 1..300 problems")
    type_counts, goals, families, minima, baselines, penalties, stacked = (Counter() for _ in range(7))
    by_type, by_type_round, seen_ids, signatures = {}, {}, set(), set()
    by_round = {f"R{n}": {field: Counter() for field in ("goals", "initial_families", "stacked_start_goal_pairs")}
                for n in range(1, 6)}
    for index, problem in enumerate(problems):
        where = f"problems[{index}]"
        require(isinstance(problem, dict), f"{where}: expected object")
        pid, kind = problem.get("id"), problem.get("rule_type")
        require(isinstance(pid, str) and re.fullmatch(r"P[0-9]{3}", pid) is not None and pid != "P000",
                f"{where}: expected problem ID P001..P999")
        require(pid not in seen_ids, f"{pid}: duplicate problem ID")
        require(isinstance(kind, str) and kind in RULE_TYPES, f"{pid}: unknown rule type")
        seen_ids.add(pid)
        type_counts[kind] += 1
        by_type.setdefault(kind, {field: Counter() for field in
                                 ("goals", "initial_families", "rules", "min_moves", "baseline_min_moves",
                                  "move_penalty", "stacked_start_goal_pairs")})
        by_type_round.setdefault(kind, {f"R{n}": {field: Counter() for field in
                                                ("goals", "initial_families", "stacked_start_goal_pairs")}
                                       for n in range(1, 6)})
        rounds = problem.get("rounds")
        require(isinstance(rounds, list) and len(rounds) == 5, f"{pid}: expected five rounds")
        states, rules, problem_families, problem_goals = set(), set(), [], []
        previous_rule = None
        for number, row in enumerate(rounds):
            info = validate_round(row, kind, previous_rule, number, f"{pid}/R{number + 1}", 30)
            initial = tuple(row["initial_positions"])
            require(initial not in states, f"{pid}: repeated initial state")
            require(info["rule"] not in rules, f"{pid}: repeated rule")
            require(not problem_goals or info["goal_peg"] != problem_goals[-1], f"{pid}: adjacent goals must differ")
            states.add(initial)
            rules.add(info["rule"])
            problem_families.append(info["initial_family"])
            problem_goals.append(info["goal_peg"])
            goals[info["goal_peg"]] += 1
            families[info["initial_family"]] += 1
            minima[info["min_moves"]] += 1
            baselines[info["baseline_min_moves"]] += 1
            penalty = info["min_moves"] - info["baseline_min_moves"]
            penalties[penalty] += 1
            for counts in (by_type[kind], by_round[f"R{number + 1}"], by_type_round[kind][f"R{number + 1}"]):
                counts["goals"][info["goal_peg"]] += 1
                counts["initial_families"][info["initial_family"]] += 1
                if info["initial_family"] == "stacked":
                    counts["stacked_start_goal_pairs"][f"{initial[0]}->{info['goal_peg']}"] += 1
            by_type[kind]["rules"][info["rule"]] += 1
            by_type[kind]["min_moves"][info["min_moves"]] += 1
            by_type[kind]["baseline_min_moves"][info["baseline_min_moves"]] += 1
            by_type[kind]["move_penalty"][penalty] += 1
            if info["initial_family"] == "stacked":
                stacked[f"{initial[0]}->{info['goal_peg']}"] += 1
            previous_rule = row["rule"]
        require(Counter(problem_families) == Counter(dict.fromkeys(FAMILIES, 1)),
                f"{pid}: each of the five initial families must occur once")
        require(len(set(problem_goals)) == 4, f"{pid}: all four goal pegs must occur")
        signature = canonical_problem_under_peg_relabeling(problem)
        require(signature not in signatures, f"{pid}: duplicate problem under global peg relabeling")
        signatures.add(signature)
    metadata = dataset.get("metadata")
    _validate_metadata(metadata, problems, type_counts)
    frequency_summary = validate_rule_frequencies({kind: row["rules"] for kind, row in by_type.items()}, metadata)
    count = len(problems)
    return {
        "valid": True, "validator": "independent_stack_replay_and_history_aware_bfs",
        "generator_imported": False, "api_calls": 0,
        "dataset_id": dataset["dataset_id"], "dataset_version": dataset["dataset_version"],
        "dataset_revision": dataset["dataset_revision"], "seed": dataset["seed"],
        "problem_count": count, "round_count": count * 5,
        "checked_shortest_plans": count * 5, "checked_restriction_witnesses": count * 5,
        "checked_baseline_shortest_restriction_witnesses": count * 5,
        "checked_transition_witnesses": count * 4,
        "unique_problem_count": len(signatures), "peg_relabeling_unique_problem_count": len(signatures),
        "rule_type_counts": _sorted_counts(type_counts), "rule_frequency_summary": frequency_summary,
        "rule_frequency_distribution": {kind: _sorted_counts(row["rules"]) for kind, row in by_type.items()},
        "goal_distribution": _sorted_counts(goals), "initial_family_distribution": _sorted_counts(families),
        "min_moves_distribution": _sorted_counts(minima), "baseline_min_moves_distribution": _sorted_counts(baselines),
        "move_penalty_distribution": _sorted_counts(penalties),
        "stacked_start_goal_distribution": _sorted_counts(stacked),
        "by_rule_type": {kind: {field: _sorted_counts(value) for field, value in row.items()}
                         for kind, row in by_type.items()},
        "by_round": {name: {field: _sorted_counts(value) for field, value in row.items()}
                     for name, row in by_round.items()},
        "by_rule_type_and_round": {kind: {name: {field: _sorted_counts(value) for field, value in row.items()}
                                          for name, row in rounds.items()} for kind, rounds in by_type_round.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", nargs="?", type=Path, default=DEFAULT_DATASET,
                        help="Dataset to validate (default: data/benchmark_300.json)")
    parser.add_argument("--output", type=Path, help="Write a validation report to this path")
    args = parser.parse_args(argv)
    try:
        if args.output:
            protected = (args.dataset, *PROTECTED_DATASETS)
            require(all(args.output.resolve() != path.resolve()
                        and not (args.output.exists() and path.exists() and args.output.samefile(path))
                        for path in protected), "output must not overwrite a dataset or selection file")
        raw = args.dataset.read_bytes()
        report = validate_dataset(json.loads(raw))
        report["dataset_source_sha256"] = hashlib.sha256(raw).hexdigest()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Validation failed: {error}\n")
    except (KeyError, TypeError, AttributeError) as error:
        parser.exit(1, f"Validation failed: malformed dataset structure ({type(error).__name__})\n")
    print(f"Validated {report['problem_count']} problems / {report['round_count']} rounds: "
          f"{report['checked_shortest_plans']} shortest plans, "
          f"{report['checked_restriction_witnesses']} restriction witnesses, "
          f"{report['checked_transition_witnesses']} transition witnesses.")
    print("Rule types: " + json.dumps(report["rule_type_counts"], sort_keys=True))
    print("Goals: " + json.dumps(report["goal_distribution"], sort_keys=True))
    print("Initial families: " + json.dumps(report["initial_family_distribution"], sort_keys=True))
    if args.output:
        print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
