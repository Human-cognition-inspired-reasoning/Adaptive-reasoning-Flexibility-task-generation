#!/usr/bin/env python3
"""Five-round Tower of Hanoi experiment using OpenAI or Anthropic and configured feedback.

Requires Python 3.11+. See README.md for usage. --dry-run makes no API calls.
Hidden rule details and grading diagnostics are available only to the local evaluator.
"""

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
from itertools import count
import json
import os
from pathlib import Path
import sys
import tempfile
import time

# Re-export grading and metric functions used by existing experiment clients.
from .grading import (
    COMMANDS as COMMANDS,
    DISK_COUNT,
    MAX_MOVES,
    PEGS,
    PERSISTENT_ERROR_POLICY,
    ExperimentError,
    Problem,
    Rule,
    get_problem,
    grade,
    evaluate_plan as evaluate_plan,
    is_success as is_success,
    parse_plan as parse_plan,
)
from .metrics import (
    ATTEMPTS,
    SUCCESS_STREAK,
    CONDITION_LABELS as CONDITION_LABELS,
    round_policies,
    summarize,
)
from .policies import BINARY, FOUR_WAY, ROUND_MPE, ROUND_END_POLICY, policy_name, round_passed
from .prompts import FEEDBACK_POLICIES, feedback_values, task_prompt


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = 30
FOURWAY_PROTOCOL_VERSION = 31
WHITESPACE_LIMIT = 256
DATASET_VERSION = 7
DEFAULTS = {
    "provider": "openai",
    "model": "gpt-6.1-sol",
    "effort": "medium",
    "episodes": 300,
    "max_attempts": ATTEMPTS,
    "success_streak": SUCCESS_STREAK,
    "r1_apply_k": True,
    "context_format": "messages",
    "output_format": "plain",
    "max_output_tokens": 25000,
    "timeout": 180.0,
    "dataset": str(ROOT / "data/benchmark_300.json"),
    "start_problem": 1,
    "feedback_policy": FOUR_WAY,
}
PROVIDER_MODELS = {"openai": "gpt-6.1-sol", "anthropic": "claude-opus-5-5"}


def problem_snapshot(problem):
    return {
        "id": problem.id,
        "condition": problem.condition,
        "rounds": [rule_snapshot(r) for r in problem.rules],
    }


def rule_snapshot(rule):
    return {**asdict(rule), "initial_positions": list(rule.initial_positions)}


_CANDIDATE_CACHE = {}


def load_candidate_problems(path):
    """Validate the dataset, then compile its rules for the experiment grader."""
    from dataset_tools.validate import validate_dataset

    path = Path(path).expanduser().resolve()
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        sha = hashlib.sha256(raw).hexdigest()
        if sha not in _CANDIDATE_CACHE:
            validate_dataset(data)
            problems = []
            for entry in data["problems"]:
                rules = tuple(
                    Rule(
                        name=row["name"],
                        goal_peg=row["goal_peg"],
                        candidate_rule=deepcopy(row["rule"]),
                        initial_positions=tuple(row["initial_positions"]),
                        initial_family=row["initial_family"],
                    )
                    for row in entry["rounds"]
                )
                problems.append(Problem(entry["id"], rules, entry["rule_type"]))
            _CANDIDATE_CACHE[sha] = tuple(problems)
        return _CANDIDATE_CACHE[sha], data, sha
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ExperimentError(f"Could not validate the candidate dataset at {path}: {exc}") from exc


def configured_problems(config):
    pool = load_candidate_problems(config["dataset"])[0]
    start, size = config.get("start_problem", 1), config["episodes"]
    if (
        type(start) is not int
        or type(size) is not int
        or start < 1
        or size < 1
        or start + size - 1 > len(pool)
    ):
        raise ExperimentError(f"The selected problem range falls outside 1 to {len(pool)}.")
    return pool[start - 1 : start - 1 + size]


def initial_board_text(rule):
    return ", ".join(
        f"{peg}="
        + json.dumps(
            [d for d in (3, 2, 1) if rule.initial_positions[d - 1] == peg], separators=(",", ":")
        )
        for peg in PEGS
    )


def submission_message(rule, number, attempt):
    return {
        "role": "user",
        "content": "Initial state: "
        + initial_board_text(rule)
        + '\nSubmit your next complete move plan as {"moves":["AB", ...]}.',
    }


class GenerationFailure(ExperimentError):
    """An unscored generation failure; abandon this problem without resampling."""


class ExperimentStopped(ExperimentError):
    """The user requested a stop after the current response was saved."""


def launch_settings(arguments):
    """Select the provider and model before key entry, without key or network access."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--provider", choices=tuple(PROVIDER_MODELS))
    parser.add_argument("--model")
    parser.add_argument("--resume", type=Path)
    args, _ = parser.parse_known_args(arguments)
    saved = (
        read_json(args.resume / "protocol.json").get("frozen", {}).get("config", {})
        if args.resume
        else {}
    )
    provider = (
        args.provider
        or saved.get("provider")
        or ("anthropic" if (args.model or "").startswith("claude-") else "openai")
    )
    if provider not in PROVIDER_MODELS:
        raise ExperimentError("Unsupported API provider.")
    model = args.model or saved.get("model") or PROVIDER_MODELS[provider]
    if (
        not isinstance(model, str)
        or not model
        or (model.startswith("claude-") != (provider == "anthropic"))
    ):
        raise ExperimentError(
            "Model and provider do not match. Use --provider anthropic for Claude."
        )
    return provider, model


def make_request(history, config, continuation_history=None):
    """Only participant instructions/history enter requests; no evaluator fields."""
    if config["context_format"] != "messages" or config["output_format"] != "plain":
        raise ExperimentError("The experiment requires messages input and plain-text output.")
    inputs = deepcopy(continuation_history if continuation_history is not None else history)
    prompt = task_prompt(config.get("feedback_policy", BINARY))
    if config["provider"] == "anthropic":
        return {
            "model": config["model"],
            "system": prompt["system"],
            "messages": inputs,
            "max_tokens": config["max_output_tokens"],
            "output_config": {"effort": config["effort"]},
            "thinking": {"type": "adaptive", "display": "omitted"},
            "tools": [],
        }
    return {
        "model": config["model"],
        "instructions": prompt["system"],
        "input": inputs,
        "reasoning": {"effort": config["effort"], "context": "all_turns"},
        "include": ["reasoning.encrypted_content"],
        "text": {"format": {"type": "text"}},
        "max_output_tokens": config["max_output_tokens"],
        "tools": [],
        "store": False,
    }


def continuation_items(raw):
    """Provider-native state only. Never reconstruct or summarize private reasoning."""
    if raw.get("type") == "message":
        blocks = raw.get("content", [])
        for block in blocks:
            if block.get("type") == "thinking" and not isinstance(block.get("signature"), str):
                raise ExperimentError("Cannot replay a thinking block without a signature.")
            if block.get("type") == "thinking" and not block["signature"]:
                raise ExperimentError("Cannot replay a thinking block with an empty signature.")
            if block.get("type") == "redacted_thinking" and not block.get("data"):
                raise ExperimentError("Redacted thinking block is missing its encrypted data.")
        return [{"role": "assistant", "content": deepcopy(blocks)}]

    def omit_none(value):
        # Output-only nullable defaults (e.g. reasoning.content/status)
        # are not valid input fields.
        if isinstance(value, dict):
            return {k: omit_none(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [omit_none(v) for v in value]
        return deepcopy(value)

    items = []
    for block in raw.get("output", []):
        if block.get("type") == "reasoning" and not block.get("encrypted_content"):
            raise ExperimentError("Cannot replay a reasoning item without its encrypted content.")
        item = omit_none(block)
        item.pop("created_by", None)  # Output provenance is not a Responses input field.
        items.append(item)
    return items


def continuation_counts(items):
    blocks = []
    for item in items:
        if item.get("type") == "reasoning":
            blocks.append(item)
        if item.get("role") == "assistant" and isinstance(item.get("content"), list):
            blocks.extend(
                b for b in item["content"] if b.get("type") in ("thinking", "redacted_thinking")
            )
    return {
        "items": len(items),
        "reasoning_items": len(blocks),
        "opaque_reasoning_items": sum(
            bool(b.get("encrypted_content") or b.get("signature") or b.get("data")) for b in blocks
        ),
    }


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def save_text(path, text):
    """Replace the checkpoint atomically so an interruption preserves the previous file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as f:
        temporary = Path(f.name)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def save_json(path, data):
    save_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExperimentError(f"Could not read the checkpoint: {path}") from exc
    if not isinstance(value, dict):
        raise ExperimentError(f"Expected a JSON object in the checkpoint: {path}")
    return value


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def publish_state(directory, phase, **fields):
    path = directory / "state.json"
    state = read_json(path) if path.exists() else {}
    state.update(phase=phase, updated_at=utc_now(), pid=os.getpid(), **fields)
    save_json(path, state)


def append_event(directory, event, **fields):
    with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(canonical({"event": event, "at": utc_now(), **fields}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def refresh_reports(directory):
    # Display or report failures must not discard or regenerate completed responses.
    try:
        from .reporting import write_reports

        write_reports(directory)
    except Exception as exc:
        append_event(directory, "report_warning", error_type=type(exc).__name__)
        print(
            "Could not refresh the reports. The response and score checkpoints are available.",
            flush=True,
        )


def source_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@contextmanager
def run_lock(directory):
    """Prevent two processes from writing to the same result directory concurrently."""
    with (directory / ".run.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ExperimentError("Another process is using this run directory.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ExperimentError("Another process is using this run directory.") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def prepare_protocol(directory, config, resume):
    problems = configured_problems(config)
    feedback_policy = policy_name(config.get("feedback_policy", BINARY))
    prompt = task_prompt(feedback_policy)
    if config.get("context_format") != "messages" or config.get("output_format") != "plain":
        raise ExperimentError("The experiment requires messages input and plain-text output.")
    path = directory / "protocol.json"
    frozen = {
        "version": (FOURWAY_PROTOCOL_VERSION if feedback_policy == FOUR_WAY else PROTOCOL_VERSION),
        "config": config,
        "feedback_policy": feedback_policy,
        "dataset_version": DATASET_VERSION,
        "problems": [problem_snapshot(p) for p in problems],
        "round_policies": list(round_policies(config)),
        "baseline_until_success": False,
        "round_completion_policy": ROUND_END_POLICY,
        "stop_problem_on_exhaustion": True,
        "r1_apply_k": config["r1_apply_k"],
        "pegs": list(PEGS),
        "disk_count": DISK_COUNT,
        "goal_policy": "Hidden destination changes between every adjacent round; fixed within a round",
        "metric_policy": ROUND_MPE,
        "metric_formula": "round PER = persistent errors / actual graded submissions; problem MPE = arithmetic mean of attempted R2-R5 PERs; Total MPE = arithmetic mean of eligible problem MPEs; R1 excluded",
        "public_objective": "A complete plan ends with all three disks stacked on one peg; the accepted destination is undisclosed",
        "disclosure_policy": "The basic single-stack task objective is public. Participant is not told the existence, candidates, types, or changes of hidden destinations and additional restrictions; only basic Hanoi mechanics, submission procedure, progress, and binary feedback are supplied",
        "rounds": [rule_snapshot(r) for r in problems[0].rules],
        "attempts_per_round": config["max_attempts"],
        "max_attempts_per_round": config["max_attempts"],
        "max_moves": MAX_MOVES,
        "success_streak_required": config["success_streak"],
        "prompt": prompt,
        "output_validation": "Local JSON parser: exactly moves; 1..30 legal peg-pair commands",
        "prompt_language": "en",
        "feedback_values": list(feedback_values(feedback_policy)),
        "api_provider": config.get("provider", "openai"),
        "provider_output_policy": (
            "Both providers receive the same English JSON instructions without server-side JSON/schema/enum/array-length constrained decoding. OpenAI text.format=text; Anthropic output_config.format is omitted. The same local parser validates the original JSON, legal commands and at most 30 moves; completed invalid outputs are scored once, never repaired or regenerated. Native responses and partial stream captures are preserved"
        ),
        "source_sha256": source_hash(),
        "early_stopping": True,
        "round_end_policy": f"Every round: at most {config['max_attempts']} submissions. R1 criterion: {round_policies(config)[0]['success_streak_required']} consecutive correct answer(s); R2-5 criterion: {config['success_streak']}. Incorrect resets streak. Consecutive correct answers allow early completion. At attempt T, a correct answer passes the round even below k; an incorrect answer ends the problem. Remaining rounds after a failed final attempt are excluded and the next problem starts with fresh history",
        "context": "Complete public history and original provider-native continuation items retained across all rounds of the same problem; reset only at the next problem. No previous_response_id or server-stored conversation",
        "reasoning_replay_policy": {
            "scope": "same_problem_across_all_rounds",
            "reset": "on_problem_change_only",
            "openai": "Replay complete original output items including encrypted_content; reasoning.context=all_turns; include=reasoning.encrypted_content; store=false. Only output-only null defaults and created_by provenance are omitted for input compatibility",
            "anthropic": "Replay complete assistant content with thinking signatures and redacted_thinking data, byte-for-byte field values and original block order; thinking=adaptive, display=omitted",
            "visible_history": "Public episode history remains separate; private blocks are not converted to text messages or shown as feedback",
            "integrity": "Persist per-trial continuation items and hashes; reconstruct solely from saved native responses on resume, with no new sampling",
        },
        "primary_metric": "Round PER = persistent errors / actual graded submissions; problem MPE = arithmetic mean of attempted R2-R5 PERs; Total MPE = arithmetic mean of eligible problem MPEs. R1 excluded. A persistent error is a current incorrect submission that obeys the previous additional movement restriction on the current initial board, with valid basic Hanoi moves; previous goal attainment is not required",
        "persistent_error_policy": PERSISTENT_ERROR_POLICY,
        "previous_rule_compliance_policy": "Evaluate the full submitted plan from the CURRENT initial positions under the PREVIOUS additional movement restriction, with source/visit memory reset. Require a valid parsed plan and valid basic Hanoi moves, but no goal attainment. Persistent error = previous_rule_compliant AND NOT current_success",
        "diagnostic_path_policy": "Follow the intended plan using basic Hanoi mechanics while ignoring additional restrictions for state updates; record all identifiable restriction violations up to and including the first basic-mechanics-invalid attempted move. Do not assess later unreachable moves. Count at most one persistent error per submission",
        "destination_error_policy": "Separate completion criterion: compare final destination only when the intended plan is valid under basic Hanoi mechanics; otherwise goal_error is null. Destination and movement errors can co-occur",
        "invalid_or_refusal_policy": "Completed visible output is scored once as invalid; binary failure feedback only",
        "incomplete_response_policy": "Save raw response, leave submission unscored, abandon remaining rounds of this problem, and continue with fresh history on next problem. Never regenerate this submission, including on resume",
        "stream_policy": {
            "enabled": True,
            "consecutive_visible_whitespace_limit": WHITESPACE_LIMIT,
            "automatic_retries": 0,
            "partial_usage": "Observed usage only; early closure may omit final billable usage",
        },
        "transport_policy": "SDK retries disabled. The public run.py --resume command authorizes one new transport attempt for each unresolved request; another transport error stops the run. Saved responses are reused, never resampled",
    }
    if config.get("dataset"):
        _, dataset, dataset_sha = load_candidate_problems(config["dataset"])
        frozen.update(
            dataset_version=dataset["dataset_version"],
            dataset_identity={
                "path": str(Path(config["dataset"]).resolve()),
                "sha256": dataset_sha,
                "dataset_id": dataset["dataset_id"],
                "dataset_revision": dataset["dataset_revision"],
            },
            dataset_snapshot=dataset,
            problem_ids=[p.id for p in problems],
            problem_selection_policy="start_problem is a 1-based index in the selected dataset order; episodes is the number to run. The saved problem_ids list gives the exact execution order. Problem IDs and complete round contents are preserved from the selected source dataset; dataset_snapshot.metadata.selection records subset selection when present",
            dependency_sha256={
                name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in (
                    "hanoi/rules.py",
                    "hanoi/grading.py",
                    "hanoi/metrics.py",
                    "hanoi/validation.py",
                    "dataset_tools/validate.py",
                    "hanoi/prompts.py",
                    "hanoi/policies.py",
                )
            },
            condition_policy="One rule type per problem; one additional restriction in every round R1-R5. Five distinct rule variants and five initial-state families per problem",
            restriction_revision="Movement-restriction schema version 1, dataset version 7; all selected round plans and witnesses independently validated before execution",
            initial_state_policy="Initial positions are ordered by disk 1,2,3. They change across rounds. Each attempt resets the board, previous-source memory, and visit memory to that round's initial state",
            goal_policy="Hidden accepted destination changes between adjacent rounds and may be any of A/B/C/D",
            public_objective="A complete plan ends with all three disks stacked on one peg; the selected accepted peg remains undisclosed",
            disclosure_policy="Only English basic Hanoi mechanics, JSON instructions, the current initial board, and binary Correct/Incorrect feedback are supplied. No round numbers, submission numbers, T, k, hidden targets, additional rule types, rules or changes are disclosed",
            previous_success_policy="Diagnostic only: evaluate the previous additional restriction using the CURRENT initial positions and CURRENT goal. Does not determine persistent error",
        )
    if feedback_policy == FOUR_WAY:
        frozen.update(
            disclosure_policy="Only English basic Hanoi mechanics, JSON instructions, the current initial board, and four feedback labels are supplied. Feedback distinguishes detected movement-restriction violations and whether the accepted goal was reached. No round numbers, transitions, submission numbers, T, k, accepted target peg, restriction types or identities, violating steps, or persistent-error flags are disclosed",
            feedback_classification="Correct: current_success. Otherwise: detected movement_rule_error with base_valid and goal_reached -> Correct, but rule violation; detected movement_rule_error otherwise -> Rule violation; no detected movement_rule_error -> Incorrect. Goal attainment is unknown for basic-mechanics-invalid plans. Only Correct counts toward k; all other labels remain wrong and reset the streak",
            invalid_or_refusal_policy="Completed invalid JSON or refusal is scored once and receives Incorrect. For parseable plans, detectable additional movement violations through the first basic-mechanics error receive Rule violation; no extra failure location or diagnostic is disclosed",
            round_end_policy=f"Every round: at most {config['max_attempts']} submissions. R1 criterion: {round_policies(config)[0]['success_streak_required']} consecutive Correct answer(s); R2-5 criterion: {config['success_streak']}. Any feedback other than Correct resets the streak, including Correct, but rule violation. Consecutive correct answers allow early completion. At attempt T, a correct answer passes the round even below k; an incorrect answer ends the problem. Remaining rounds after a failed final attempt are excluded and the next problem starts with fresh history",
        )
    if resume:
        saved = read_json(path)
        if (
            saved.get("frozen_sha256") != digest(saved.get("frozen"))
            or saved.get("frozen") != frozen
        ):
            raise ExperimentError(
                "Code, configuration, or rule data differs from the original run. Start a new run with --output."
            )
    else:
        save_json(
            path, {"created_at": utc_now(), "frozen_sha256": digest(frozen), "frozen": frozen}
        )


def receive_stream(resource, request, prefix):
    """Persist stream events immediately; inspect only participant-visible text."""
    # Offline clients may expose create only. Production clients receive SSE streams.
    if not hasattr(resource, "stream"):
        response = resource.create(**request)
        return response.model_dump(mode="json"), getattr(response, "_request_id", None), None
    anthropic = "messages" in request
    consecutive = 0
    chunks = []
    observed_usage = None
    response_id = None
    native_final = None
    event_file = Path(str(prefix) + "_stream.jsonl")
    with event_file.open("w", encoding="utf-8") as log:
        with (
            resource.stream(**request) if anthropic else resource.create(**request, stream=True)
        ) as stream:
            http_response = getattr(stream, "response", None) or getattr(stream, "_response", None)
            headers = http_response.headers
            request_id = headers.get("request-id" if anthropic else "x-request-id")
            for event in stream:
                data = event.model_dump(mode="json")
                log.write(canonical(data) + "\n")
                log.flush()
                kind = data.get("type")
                delta = ""
                if anthropic:
                    if kind == "message_start":
                        message = data.get("message", {})
                        response_id = message.get("id")
                        observed_usage = message.get("usage")
                    elif kind == "message_delta" and data.get("usage"):
                        observed_usage = {**(observed_usage or {}), **data["usage"]}
                    elif (
                        kind == "content_block_delta"
                        and data.get("delta", {}).get("type") == "text_delta"
                    ):
                        delta = data["delta"].get("text", "")
                    elif (
                        kind == "content_block_start"
                        and data.get("content_block", {}).get("type") == "text"
                    ):
                        delta = data["content_block"].get("text", "")
                else:
                    if kind in ("response.completed", "response.incomplete", "response.failed"):
                        native_final = data.get("response")
                    if isinstance(data.get("response"), dict):
                        response_id = data["response"].get("id", response_id)
                        observed_usage = data["response"].get("usage") or observed_usage
                    if kind == "response.output_text.delta":
                        delta = data.get("delta", "")
                if delta:
                    chunks.append(delta)
                    for char in delta:
                        consecutive = consecutive + 1 if char.isspace() else 0
                        if consecutive >= WHITESPACE_LIMIT:
                            # This is an explicitly labeled local partial capture, not a native completed response.
                            return (
                                {
                                    "id": response_id,
                                    "type": "message" if anthropic else "response",
                                    "status": "locally_aborted",
                                    "partial_visible_text": "".join(chunks),
                                    "usage": observed_usage,
                                    "usage_is_partial": True,
                                },
                                request_id,
                                "consecutive_whitespace_limit",
                            )
            if not anthropic:
                if native_final is None:
                    raise ExperimentError(
                        "The API stream ended without a terminal event. The events received so far are available in the stream log."
                    )
                return native_final, request_id, None
            response = stream.get_final_message()
            return response.model_dump(mode="json"), request_id, None


def obtain_response(directory, request, client, retry_pending, on_request=None):
    """Reuse completed responses; do not automatically repeat requests with unknown outcomes."""
    directory.mkdir(parents=True, exist_ok=True)
    request_file = directory / "request.json"
    starts = sorted(directory.glob("transport_*_started.json"))
    saved_responses = sorted(directory.glob("transport_*_response.json"))
    if request_file.exists():
        if read_json(request_file) != request:
            raise ExperimentError(
                f"The reconstructed request differs from the saved request: {directory}"
            )
    elif starts or saved_responses or (directory / "record.json").exists():
        raise ExperimentError(f"The original request is missing from the checkpoint: {directory}")
    else:
        save_json(request_file, request)
    for index, start in enumerate(starts, 1):
        if start.name != f"transport_{index:03d}_started.json" or read_json(start).get(
            "request_sha256"
        ) != digest(request):
            raise ExperimentError(f"The transport sequence or request hash is invalid: {directory}")
    if len(saved_responses) > 1:
        raise ExperimentError(f"Found multiple saved responses for one submission: {directory}")
    if saved_responses:
        matching_start = saved_responses[0].with_name(
            saved_responses[0].name.replace("_response.json", "_started.json")
        )
        if matching_start not in starts:
            raise ExperimentError(
                f"No matching transport start for the saved response: {directory}"
            )
        envelope = read_json(saved_responses[0])
        if envelope.get("request_sha256") != digest(request):
            raise ExperimentError(f"The saved response refers to a different request: {directory}")
        if envelope.get("response_sha256") != digest(envelope.get("response")):
            raise ExperimentError(f"The saved response does not match its hash: {directory}")
        return envelope
    if (directory / "record.json").exists():
        raise ExperimentError(f"Found a score but no original response: {directory}")
    if starts and not retry_pending:
        raise ExperimentError(
            f"No response was saved for this request: {directory}\n"
            "The server may have processed it. Use --resume with --retry-pending to try again."
        )
    attempt = len(starts) + 1
    prefix = directory / f"transport_{attempt:03d}"
    save_json(
        Path(str(prefix) + "_started.json"),
        {
            "started_at": utc_now(),
            "request_sha256": digest(request),
            "explicit_retry_of_unresolved_request": bool(starts),
        },
    )
    if on_request:
        on_request(attempt)
    begin = time.monotonic()
    try:
        resource = (
            client.messages
            if "messages" in request and "max_tokens" in request
            else client.responses
        )
        raw, request_id, generation_failure = receive_stream(resource, request, prefix)
    except Exception as exc:
        # Never print or save exception bodies that may contain keys or headers.
        details = {
            "error_type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None),
            "request_id": getattr(exc, "request_id", None),
            "at": utc_now(),
        }
        save_json(Path(str(prefix) + "_error.json"), details)
        raise ExperimentError(
            f"API request interrupted: {details['error_type']} (HTTP {details['status_code']}). "
            "The submission was not scored. Check credentials, model access, quota, and connectivity."
        ) from None
    envelope = {
        "received_at": utc_now(),
        "elapsed_seconds": time.monotonic() - begin,
        "request_sha256": digest(request),
        "response_sha256": digest(raw),
        "response": raw,
        "request_id": request_id,
        "transport_attempt": attempt,
        "generation_failure": generation_failure,
        "stream": True,
    }
    save_json(Path(str(prefix) + "_response.json"), envelope)
    return envelope


def visible_response(raw):
    """Extract only visible grading text; preserve reasoning state in continuation_items."""
    if raw.get("status") == "locally_aborted":
        raise GenerationFailure("consecutive_whitespace_limit")
    if raw.get("type") == "message" and "stop_reason" in raw:
        if raw.get("stop_reason") in ("max_tokens", "pause_turn", "model_context_window_exceeded"):
            raise GenerationFailure("anthropic_" + raw["stop_reason"])
        if raw.get("role") != "assistant" or raw.get("stop_reason") not in ("end_turn", "refusal"):
            raise ExperimentError(
                f"Saved an unexpected Anthropic response (stop_reason={raw.get('stop_reason')}). "
                "The submission remains unscored."
            )
        parts = []
        for block in raw.get("content", []):
            if block.get("type") in ("thinking", "redacted_thinking"):
                continue
            if block.get("type") != "text":
                raise ExperimentError(f"Unexpected Anthropic output block: {block.get('type')}")
            parts.append(block.get("text", ""))
        text = "".join(parts).strip()
        refused = raw["stop_reason"] == "refusal"
        if not text and not refused:
            raise GenerationFailure("empty_visible_response")
        return text, refused
    if raw.get("status") == "failed":
        raise GenerationFailure("failed_response")
    if raw.get("status") == "incomplete":
        raise GenerationFailure("incomplete_response")
    if raw.get("status") != "completed":
        raise ExperimentError(
            f"Saved a response with an unexpected status ({raw.get('status')}). "
            "The submission remains unscored."
        )
    parts, refused = [], False
    for item in raw.get("output", []):
        kind = item.get("type")
        if kind == "reasoning":
            continue
        if kind != "message" or item.get("role") != "assistant":
            raise ExperimentError(f"Unexpected output item: {kind}")
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                parts.append(content["text"])
            elif content.get("type") == "refusal":
                parts.append(content["refusal"])
                refused = True
            else:
                raise ExperimentError("Unexpected message content type.")
    text = "".join(parts).strip()
    if not text and not refused:
        raise GenerationFailure("empty_visible_response")
    return text, refused


def response_usage(raw):
    """Normalize logged input/output token totals while preserving native response usage."""
    usage = raw.get("usage")
    if raw.get("type") != "message" or not isinstance(usage, dict):
        return usage
    normalized = dict(usage)
    uncached = usage.get("input_tokens", 0) or 0
    normalized["input_tokens_uncached"] = uncached
    normalized["input_tokens"] = (
        uncached
        + (usage.get("cache_creation_input_tokens", 0) or 0)
        + (usage.get("cache_read_input_tokens", 0) or 0)
    )
    normalized["total_tokens"] = normalized["input_tokens"] + (usage.get("output_tokens", 0) or 0)
    return normalized


def execute(directory, config, client, retry_pending=False):
    """Reconstruct the conversation by reading response checkpoints in order."""
    from .terminal import TerminalReporter

    policies = round_policies(config)
    feedback_policy = policy_name(config.get("feedback_policy", BINARY))
    prompt = task_prompt(feedback_policy)
    problems = configured_problems(config)
    frozen = read_json(directory / "protocol.json")["frozen"]
    dataset_identity = frozen["dataset_identity"] if config.get("dataset") else None
    reporter = TerminalReporter(
        directory,
        config,
        problems[0].rules,
        config["max_attempts"],
        config["success_streak"],
        problems=problems,
        round_policies=policies,
        persistent_error_policy=frozen["persistent_error_policy"],
    )
    reporter.start()
    records, generation_failures = [], []
    call = 0

    def snapshot_result(finished=False):
        result = summarize(
            records,
            config["episodes"],
            required_successes=config["success_streak"],
            policies=policies,
            problems=problems,
        )
        result.update(
            execution_finished=finished,
            generation_failures=list(generation_failures),
            generation_failure_count=len(generation_failures),
            api_submissions=len(records) + len(generation_failures),
            usage_incomplete=any(x["usage_incomplete"] for x in generation_failures),
        )
        if dataset_identity is not None:
            result.update(
                dataset_identity=dataset_identity,
                problem_ids=[p.id for p in problems],
                start_problem=config.get("start_problem", 1),
            )
        audits = [record.get("reasoning_replay", {}) for record in records]
        result["reasoning_replay"] = {
            "policy": "provider_native_same_problem_all_rounds",
            "submissions_with_replayed_reasoning": sum(
                a.get("input", {}).get("reasoning_items", 0) > 0 for a in audits
            ),
            "returned_reasoning_items": sum(
                a.get("output", {}).get("reasoning_items", 0) for a in audits
            ),
            "returned_opaque_reasoning_items": sum(
                a.get("output", {}).get("opaque_reasoning_items", 0) for a in audits
            ),
            "effective_contexts": sorted(
                {a["effective_context"] for a in audits if a.get("effective_context")}
            ),
        }
        result["scored_usage"] = dict(result["usage"])
        for failure in generation_failures:
            for key in result["usage"]:
                result["usage"][key] += (failure.get("usage") or {}).get(key, 0) or 0
            entry = result["episodes"][failure["episode"] - 1]
            entry["generation_failure"] = failure
            for row in entry["rounds"]:
                if row["round"] >= failure["round"]:
                    row["status"] = (
                        "generation_failed" if row["round"] == failure["round"] else "excluded"
                    )
        result["excluded_rounds"] = sum(
            row["status"] == "excluded"
            for episode in result["episodes"]
            for row in episode["rounds"]
        )
        return result

    if not (directory / "summary.json").exists():
        save_json(directory / "summary.json", snapshot_result())
    for episode in range(1, config["episodes"] + 1):
        problem = get_problem(episode, problems)
        abandoned = False
        history = [{"role": "user", "content": prompt["user"]}]
        api_history = deepcopy(history)
        for number in range(1, 6):
            policy = policies[number - 1]
            required = policy["success_streak_required"]
            limit = policy["max_attempts"]
            streak = 0
            reporter.round_start(episode, number)
            for attempt in count(1):
                if limit is not None and attempt > limit:
                    break
                if (directory / "stop.request").exists():
                    raise ExperimentStopped(
                        "Stopped after saving the available responses. Use --resume to continue."
                    )
                call += 1
                progress = submission_message(problem.rules[number - 1], number, attempt)
                next_history = history + [progress]
                next_api_history = api_history + [deepcopy(progress)]
                request = make_request(next_history, config, next_api_history)
                trial = directory / "trials" / f"{call:05d}_e{episode:03d}_r{number}_a{attempt:02d}"
                recovered = (trial / "record.json").exists()
                if not recovered:
                    save_json(directory / f"episode_{episode:03d}_history.json", history)
                    save_text(
                        directory / "submissions.jsonl",
                        "".join(canonical(r) + "\n" for r in records),
                    )
                    save_json(directory / "summary.json", snapshot_result())

                def request_started(transport_attempt):
                    fields = {
                        "call": call,
                        "episode": episode,
                        "problem_id": problem.id,
                        "condition": problem.condition,
                        "round": number,
                        "attempt": attempt,
                    }
                    publish_state(
                        directory,
                        "requesting",
                        **fields,
                        success_streak=streak,
                        success_streak_required=required,
                        message="Waiting for the model response.",
                    )
                    append_event(
                        directory, "request_started", **fields, transport_attempt=transport_attempt
                    )
                    reporter.waiting(attempt, transport_attempt)

                envelope = obtain_response(trial, request, client, retry_pending, request_started)
                try:
                    raw, refused = visible_response(envelope["response"])
                except GenerationFailure as exc:
                    failure = {
                        "call": call,
                        "episode": episode,
                        "problem_id": problem.id,
                        "condition": problem.condition,
                        "round": number,
                        "attempt": attempt,
                        "reason": str(exc),
                        "scored": False,
                        "retry": False,
                        "usage": response_usage(envelope["response"]),
                        "usage_incomplete": bool(envelope["response"].get("usage_is_partial"))
                        or envelope["response"].get("usage") is None,
                        "elapsed_seconds": envelope["elapsed_seconds"],
                        "response_sha256": envelope["response_sha256"],
                        "trial": str(trial.relative_to(directory)),
                    }
                    failure_file = trial / "generation_failure.json"
                    if failure_file.exists() and read_json(failure_file) != failure:
                        raise ExperimentError(
                            "The generation failure differs from its saved checkpoint."
                        )
                    if not failure_file.exists():
                        save_json(failure_file, failure)
                        append_event(directory, "generation_failed", **failure)
                    generation_failures.append(failure)
                    save_json(directory / "summary.json", snapshot_result())
                    reporter.generation_failure(failure)
                    abandoned = True
                    break
                native_items = continuation_items(envelope["response"])
                continuation = {
                    "response_sha256": envelope["response_sha256"],
                    "items_sha256": digest(native_items),
                    "items": native_items,
                }
                continuation_file = trial / "continuation.json"
                if continuation_file.exists() and read_json(continuation_file) != continuation:
                    raise ExperimentError(
                        "The saved reasoning state differs from the state reconstructed from the original response."
                    )
                if not continuation_file.exists():
                    save_json(continuation_file, continuation)
                replay_audit = {
                    "input": continuation_counts(next_api_history),
                    "output": continuation_counts(native_items),
                    "output_sha256": continuation["items_sha256"],
                    "effective_context": (envelope["response"].get("reasoning") or {}).get(
                        "context"
                    ),
                }
                score = grade(
                    raw, number, episode, problem=problem, feedback_policy=feedback_policy
                )
                if refused:
                    score = grade(
                        None, number, episode, problem=problem, feedback_policy=feedback_policy
                    )
                streak = streak + 1 if score["current_success"] else 0
                passed = round_passed(streak, attempt, score["current_success"], policy)
                record = {
                    "call": call,
                    "episode": episode,
                    "problem_id": problem.id,
                    "condition": problem.condition,
                    "round": number,
                    "attempt": attempt,
                    "raw_output": raw,
                    **score,
                    "refusal": refused,
                    "reasoning_replay": replay_audit,
                    "response_id": envelope["response"].get("id"),
                    "model_returned": envelope["response"].get("model"),
                    "usage": response_usage(envelope["response"]),
                    "elapsed_seconds": envelope["elapsed_seconds"],
                    "transport_attempt": envelope["transport_attempt"],
                    "request_sha256": envelope["request_sha256"],
                    "success_streak": streak,
                    "round_success": passed,
                    "success_streak_required": required,
                }
                if config.get("dataset"):
                    record.update(
                        initial_positions=list(problem.rules[number - 1].initial_positions),
                        initial_family=problem.rules[number - 1].initial_family,
                        rule_snapshot=rule_snapshot(problem.rules[number - 1]),
                        dataset_id=dataset_identity["dataset_id"],
                        dataset_sha256=dataset_identity["sha256"],
                        dataset_index=config.get("start_problem", 1) + episode - 1,
                    )
                record_file = trial / "record.json"
                if record_file.exists() and read_json(record_file) != record:
                    raise ExperimentError(f"The new score differs from the saved score: {trial}")
                if not recovered:
                    save_json(record_file, record)
                records.append(record)
                api_history = (
                    next_api_history
                    + native_items
                    + [{"role": "user", "content": score["feedback"]}]
                )
                history = next_history + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": score["feedback"]},
                ]
                if not recovered:
                    save_json(directory / f"episode_{episode:03d}_history.json", history)
                    save_text(
                        directory / "submissions.jsonl",
                        "".join(canonical(r) + "\n" for r in records),
                    )
                    save_json(directory / "summary.json", snapshot_result())
                    append_event(
                        directory,
                        "submission_scored",
                        call=call,
                        episode=episode,
                        problem_id=problem.id,
                        condition=problem.condition,
                        round=number,
                        attempt=attempt,
                        feedback=score["feedback"],
                        persistent_error=score["persistent_error"],
                        success_streak=streak,
                        round_success=passed,
                        elapsed_seconds=envelope["elapsed_seconds"],
                    )
                    publish_state(
                        directory,
                        "running",
                        call=call,
                        episode=episode,
                        problem_id=problem.id,
                        condition=problem.condition,
                        round=number,
                        attempt=attempt,
                        success_streak=streak,
                        success_streak_required=required,
                        message=f"Saved {call} submissions.",
                    )
                    refresh_reports(directory)
                reporter.submission(record, recovered=recovered)
                # Restore the streak from recovered responses to preserve the stopping point.
                if passed:
                    break
            round_summary = snapshot_result()["episodes"][episode - 1]["rounds"][number - 1]
            reporter.round_end(round_summary)
            if abandoned:
                break
            if round_summary["status"] == "exhausted":
                if not recovered:
                    append_event(
                        directory,
                        "problem_exhausted",
                        episode=episode,
                        problem_id=problem.id,
                        round=number,
                        max_attempts=limit,
                        excluded_rounds=list(range(number + 1, 6)),
                    )
                reporter.problem_exhausted(episode, number)
                break
        save_json(directory / f"episode_{episode:03d}_history.json", history)
    result = snapshot_result(finished=True)
    save_text(directory / "submissions.jsonl", "".join(canonical(r) + "\n" for r in records))
    save_json(directory / "summary.json", result)
    publish_state(
        directory,
        "complete" if result["complete"] else "finished_with_failures",
        call=call,
        message=f"Run finished. Problems that exhausted their attempt limit: {result['exhausted_problems']}. Excluded rounds: {result['excluded_rounds']}. Problems with a generation failure: {len(generation_failures)}.",
    )
    refresh_reports(directory)
    reporter.finish(result)
    return result


def make_client(config):
    """Construct and authenticate the selected provider before any run writes."""
    from .auth import checked_client

    return checked_client(config)


def main(argv=None, *, client_factory=None, check_only=False):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument(
        "--output", type=Path, help="Directory for a new run. The directory must not already exist."
    )
    destination.add_argument(
        "--resume",
        type=Path,
        help="Resume an existing run using its original code and configuration.",
    )
    parser.add_argument("--provider", choices=tuple(PROVIDER_MODELS), help="Model API provider.")
    parser.add_argument("--model", help="Model ID. Defaults to the selected provider's model.")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument(
        "--problems",
        "--episodes",
        dest="episodes",
        type=int,
        help="Number of problems to run (default: all remaining problems in the dataset).",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        help="Dataset JSON file (default: data/benchmark_300.json, 100 problems per type).",
    )
    parser.add_argument(
        "--start-problem", type=int, help="One-based index of the first problem in dataset order."
    )
    parser.add_argument(
        "--context-format",
        choices=["messages"],
        help="Use role-tagged messages to preserve the reasoning history.",
    )
    parser.add_argument(
        "--output-format",
        choices=["plain"],
        help="Request JSON in plain text and grade it locally.",
    )
    parser.add_argument(
        "--feedback-policy",
        type=policy_name,
        choices=FEEDBACK_POLICIES,
        help="Select binary or four-way feedback.",
    )
    parser.add_argument(
        "--T",
        "--t",
        "--max-attempts",
        dest="max_attempts",
        type=int,
        help="Maximum attempts (T) per round (default: 10).",
    )
    parser.add_argument(
        "--k",
        "--success-streak",
        dest="success_streak",
        type=int,
        help="Consecutive correct submissions (k) required to pass a round (default: 2).",
    )
    parser.add_argument(
        "--r1-apply-k",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Apply the success streak requirement to round 1 (default: enabled).",
    )
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--timeout", type=float)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Save the first request preview without making an API call.",
    )
    parser.add_argument(
        "--retry-pending",
        action="store_true",
        help="Allow a new transport attempt for a request whose outcome is unknown.",
    )
    parser.add_argument("--preserve-stop-request", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.resume and args.dry_run:
        parser.error(
            "--dry-run requires a new output directory and cannot be combined with --resume."
        )
    if args.retry_pending and not args.resume:
        parser.error("--retry-pending requires --resume.")
    config = dict(DEFAULTS)
    selected_provider, selected_model = launch_settings(argv if argv is not None else sys.argv[1:])
    config.update(provider=selected_provider, model=selected_model)
    if args.resume:
        saved = read_json(args.resume / "protocol.json")
        frozen = saved.get("frozen", {})
        if saved.get("frozen_sha256") != digest(frozen) or not isinstance(
            frozen.get("config"), dict
        ):
            raise ExperimentError("Invalid protocol.json format or hash.")
        config = dict(frozen["config"])
        if set(config) != set(DEFAULTS):
            raise ExperimentError("The saved configuration has unexpected or missing settings.")
    else:
        config["dataset"] = str((args.dataset or Path(DEFAULTS["dataset"])).expanduser().resolve())
        pool, dataset, _ = load_candidate_problems(config["dataset"])
        settings = dataset.get("experiment_defaults", {})
        config.update(
            max_attempts=settings.get("T", DEFAULTS["max_attempts"]),
            success_streak=settings.get("k", 2),
            r1_apply_k=settings.get("r1_apply_k", True),
            feedback_policy=settings.get("feedback_policy", DEFAULTS["feedback_policy"]),
            episodes=len(pool) - ((args.start_problem or 1) - 1),
        )
    for key in DEFAULTS:
        value = getattr(args, key)
        if key == "dataset" and value is not None:
            value = str(value.expanduser().resolve())
        if value is not None:
            if args.resume and value != config[key]:
                raise ExperimentError(f"Cannot change {key} when resuming a run.")
            config[key] = value
    problems = configured_problems(config)
    if config["max_output_tokens"] < 1 or config["timeout"] <= 0:
        parser.error("max-output-tokens and timeout must be positive.")
    round_policies(config)  # validate before client creation or output directory creation
    try:
        feedback_values(config["feedback_policy"])
    except ValueError as exc:
        raise ExperimentError(str(exc)) from exc
    client = None
    directory = (
        args.resume
        or args.output
        or ROOT / "runs" / datetime.now().strftime(f"api_{config['provider']}_%Y%m%d_%H%M%S_%f")
    ).resolve()
    if args.resume:
        if not directory.is_dir():
            raise ExperimentError("The run directory does not exist.")
        prepare_protocol(directory, config, True)  # Read-only frozen-code validation before auth.
    elif directory.exists():
        raise ExperimentError(
            "The output directory already exists. Use --resume or choose a new path."
        )
    if check_only and args.dry_run:
        raise ExperimentError("--check-only and --dry-run cannot be used together.")
    if not args.dry_run:
        client = (client_factory or make_client)(config)
    try:
        if check_only:
            return 0
        if not args.resume:
            directory.mkdir(parents=True, exist_ok=False)
        with run_lock(directory):
            prepare_protocol(directory, config, bool(args.resume))
            if args.dry_run:
                history = [
                    {"role": "user", "content": task_prompt(config["feedback_policy"])["user"]},
                    submission_message(problems[0].rules[0], 1, 1),
                ]
                save_json(directory / "first_request_preview.json", make_request(history, config))
                publish_state(
                    directory,
                    "preview",
                    call=0,
                    message="First request preview. No API call was made.",
                )
                refresh_reports(directory)
                print(f"Dry run complete. No API calls or scores.\n{directory}")
                return 0
            save_json(
                directory / "runtime.json",
                {
                    "python": sys.version,
                    "provider": config["provider"],
                    "sdk_version": importlib.metadata.version(
                        "anthropic" if config["provider"] == "anthropic" else "openai"
                    ),
                    "context_format": config["context_format"],
                    "started_at": utc_now(),
                },
            )
            if not args.preserve_stop_request:
                (directory / "stop.request").unlink(missing_ok=True)
            publish_state(
                directory, "running", message="Checking saved records and starting the experiment."
            )
            append_event(
                directory,
                "run_resumed" if (directory / "trials").exists() else "run_started",
                config=config,
            )
            try:
                result = execute(directory, config, client, args.retry_pending)
                append_event(
                    directory,
                    "run_completed" if result["complete"] else "run_finished_with_failures",
                    recorded_submissions=result["recorded_submissions"],
                    generation_failure_count=result["generation_failure_count"],
                )
            except ExperimentStopped as exc:
                publish_state(directory, "stopped", message=str(exc))
                append_event(directory, "run_stopped", message=str(exc))
                refresh_reports(directory)
                print(str(exc), flush=True)
                return 0
            except (ExperimentError, KeyboardInterrupt) as exc:
                save_json(
                    directory / "last_stop.json",
                    {"at": utc_now(), "type": type(exc).__name__, "message": str(exc)},
                )
                publish_state(
                    directory,
                    "interrupted" if isinstance(exc, KeyboardInterrupt) else "error",
                    message=str(exc) or "The experiment was interrupted.",
                )
                append_event(
                    directory, "run_error", error_type=type(exc).__name__, message=str(exc)
                )
                refresh_reports(directory)
                raise
        return 0
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ExperimentError, OSError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted. Use --resume to continue with the saved responses.", file=sys.stderr)
        sys.exit(130)
