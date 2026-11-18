"""Save JSON experiment records and compute performance, time, and token costs.

Run ``python code/results.py --run-folder result/<run>`` to print a JSON summary.
This module never calls a model API. Missing usage or prices remain unknown.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
import math
import os
from pathlib import Path
import re
from statistics import mean
import uuid

from judge import UNDEFINED, js_dumps, json_loads, js_truthy, js_string, boardForRound

def _get(value, key):
    return value.get(key, UNDEFINED) if isinstance(value, dict) else UNDEFINED

def _path(value, *keys):
    for key in keys:
        value = _get(value, key)
    return value

def _nullish(value, fallback):
    return fallback if value is None or value is UNDEFINED else value

def _to_fixed(value, digits):
    """Round a binary64 value to decimal places, with ties away from zero."""
    number = float(value)
    if not math.isfinite(number) or abs(number) >= 1e21:
        return js_string(number)
    negative = number < 0
    with localcontext() as context:
        context.prec = 1100
        rounded = Decimal.from_float(abs(number)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        return ("-" if negative else "") + format(rounded, f".{digits}f")

def _date_parse_ms(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    except (TypeError, ValueError, OverflowError):
        return float("nan")

def readJSON(file):
    return json_loads(Path(file).read_text(encoding="utf-8").removeprefix("\ufeff"))

def atomicWrite(file, text):
    file = os.fspath(file)
    Path(file).parent.mkdir(parents=True, exist_ok=True)
    temporary = file + "." + str(uuid.uuid4()) + ".tmp"
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    # A binary descriptor keeps saved bytes independent of platform newline rules.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o666)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, file)

def writeJSON(file, x):
    atomicWrite(file, js_dumps(x, indent=2) + "\n")

def readCalls(folder):
    directory = Path(folder) / "calls"
    if not directory.exists():
        return []
    paths = [p for p in directory.iterdir() if p.is_file() and re.fullmatch(r"[0-9]{5,}\.json", p.name)]
    return [readJSON(p) for p in sorted(paths, key=lambda p: int(p.stem))]

def _sum(rows, f):
    total = 0.0
    for row in rows:
        total = total + _nullish(f(row), 0)
    return total

def _mean(values):
    return _sum(values, lambda value: value) / len(values) if values else None

def _group(rows, key):
    out = {}
    for row in rows:
        out.setdefault(key(row), []).append(row)
    return out

def _roundKey(x):
    return f"{js_string(x['problem'])}:{js_string(x['round'])}"

def normalizeUsage(response):
    """Expose comparable token totals without modifying the provider's receipt.

    Claude reports ordinary, cache-read and cache-write input tokens separately.
    OpenAI reports an inclusive input total with a cache breakdown underneath it.
    Thinking is already part of output_tokens and must not be charged twice.
    """
    if not isinstance(response, dict) or not isinstance(response.get('usage'), dict):
        return None
    usage = response['usage']
    is_anthropic = (str(response.get('model', '')).startswith('claude-')
                    or response.get('type') == 'message'
                    or 'cache_read_input_tokens' in usage
                    or 'cache_creation_input_tokens' in usage)
    if not is_anthropic:
        return usage
    ordinary = usage.get('input_tokens')
    read = usage.get('cache_read_input_tokens', 0)
    write = usage.get('cache_creation_input_tokens', 0)
    output = usage.get('output_tokens')
    numeric = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0 and int(x) == x
    total_input = ordinary + read + write if all(numeric(x) for x in (ordinary, read, write)) else None
    total = total_input + output if total_input is not None and numeric(output) else None
    return {
        'input_tokens': total_input,
        'output_tokens': output,
        'total_tokens': total,
        'input_tokens_details': {'cached_tokens': read, 'cache_write_tokens': write},
        'output_tokens_details': {'reasoning_tokens': usage.get('output_tokens_details', {}).get('reasoning_tokens') if isinstance(usage.get('output_tokens_details'), dict) else None},
    }


def _usage(call):
    return normalizeUsage(call.get('response')) if isinstance(call, dict) else None


def resources(calls):
    unknownCost = sum(_path(c, "cost", "estimated_cost_usd") in (None, UNDEFINED) for c in calls)
    unknownTime = sum(_get(c, "api_seconds") in (None, UNDEFINED) for c in calls)
    knownCost = _sum(calls, lambda c: _path(c, "cost", "estimated_cost_usd"))
    knownTime = _sum(calls, lambda c: _get(c, "api_seconds"))
    starts = sorted(_get(c, "started_at") for c in calls if js_truthy(_get(c, "started_at")))
    ends = sorted(_get(c, "finished_at") for c in calls if js_truthy(_get(c, "finished_at")))
    first, last = (starts[0] if starts else None), (ends[-1] if ends else None)

    def total_tokens(c):
        usage = _usage(c)
        total = _get(usage, "total_tokens")
        if total is not None and total is not UNDEFINED:
            return total
        if js_truthy(usage):
            a, b = _get(usage, "input_tokens"), _get(usage, "output_tokens")
            return float("nan") if a is UNDEFINED or b is UNDEFINED else (0 if a is None else a) + (0 if b is None else b)
        return None

    return {
        "api_calls": len(calls), "api_seconds": None if unknownTime else knownTime,
        "known_api_seconds": knownTime, "unknown_time_calls": unknownTime,
        "first_request_at": first, "last_response_at": last,
        "wall_elapsed_seconds_including_pauses": (_date_parse_ms(last) - _date_parse_ms(first)) / 1000 if js_truthy(first) and js_truthy(last) and all(js_truthy(_get(c, "finished_at")) for c in calls) else None,
        "estimated_cost_usd": None if unknownCost else knownCost,
        "known_cost_subtotal_usd": knownCost, "unpriced_calls": unknownCost,
        "known_input_tokens": _sum(calls, lambda c: _get(_usage(c), "input_tokens")),
        "known_output_tokens": _sum(calls, lambda c: _get(_usage(c), "output_tokens")),
        "known_cached_input_tokens": _sum(calls, lambda c: _path(_usage(c), "input_tokens_details", "cached_tokens")),
        "known_cache_write_tokens": _sum(calls, lambda c: _path(_usage(c), "input_tokens_details", "cache_write_tokens")),
        "known_total_tokens": _sum(calls, total_tokens),
        "calls_without_usage": sum(not js_truthy(_usage(c)) for c in calls),
        "known_reasoning_tokens": _sum(calls, lambda c: _path(_usage(c), "output_tokens_details", "reasoning_tokens")),
    }

_exportCache = {}


def exportReports(folder, s, calls, config):
    folder = os.fspath(folder)
    target = Path(folder)
    cached = _exportCache.get(folder)
    incremental = cached is not None and cached["state"] is s and cached["attempts"] <= len(s["attempts"]) and (target / "attempts.json").exists()
    previousCount = cached["attempts"] if incremental else 0
    attemptsByRound = _group(s["attempts"], _roundKey)
    attemptsByProblem = _group(s["attempts"], lambda x: x["problem"])
    callsByRound = _group(calls, _roundKey)
    callsByProblem = _group(calls, lambda x: x["problem"])
    endedByRound = {_roundKey(x): x for x in s["rounds"]}
    skippedByRound = {_roundKey(x): x for x in s["skippedRounds"]}
    rounds = []
    for p in s["dataset"]["problems"]:
        for r in range(1, config["rounds"] + 1):
            key = f"{js_string(p['problem_number'])}:{r}"
            aa, rr = attemptsByRound.get(key, []), endedByRound.get(key, UNDEFINED)
            skipped = skippedByRound.get(key, UNDEFINED)
            cc = callsByRound.get(key, [])
            res = resources(cc)
            triangles = sum(js_truthy(_get(a, "previous_rule_error")) for a in aa)
            has_round, is_skipped = js_truthy(rr), js_truthy(skipped)
            eligible = has_round and js_truthy(_get(rr, "previous_round_had_success"))
            correct = sum(js_truthy(_get(a, "correct")) for a in aa)
            board = boardForRound(p, r - 1)
            rounds.append({
                "problem": p["problem_number"], "round": r,
                "source_position": js_dumps(board["source"]["position"]),
                "filter_positions": js_dumps(board["filters"]), "sensor_positions": js_dumps(board["sensors"]),
                "status": "skipped" if is_skipped else ("success" if js_truthy(_get(rr, "reached_k")) else "failed") if has_round else "in_progress" if cc else "not_started",
                "skipped": is_skipped, "skip_reason": _nullish(_get(skipped, "reason"), None), "blocked_by_round": _nullish(_get(skipped, "blocked_by_round"), None),
                "attempts": len(aa), "correct_count": None if is_skipped else correct,
                "previous_rule_error_count": None if is_skipped else triangles,
                "incorrect_count": None if is_skipped else sum(not js_truthy(_get(a, "correct")) for a in aa),
                "ordinary_error_count": None if is_skipped else sum(not js_truthy(_get(a, "correct")) and not js_truthy(_get(a, "previous_rule_error")) for a in aa),
                "symbols": "".join("O" if js_truthy(_get(a, "correct")) else "△" if js_truthy(_get(a, "previous_rule_error")) else "X" for a in aa),
                "accuracy_percent": 100 * correct / len(aa) if has_round and aa else float("nan") if has_round else None,
                "per_eligible": False if is_skipped else _nullish(_get(rr, "previous_round_had_success"), _nullish(_get(aa[0] if aa else UNDEFINED, "per_eligible"), None)),
                "per_numerator": triangles if eligible else None, "per_denominator": len(aa) if eligible else None,
                "per_percent": 100 * triangles / len(aa) if eligible and aa else float("nan") if eligible else None,
                "reached_k": None if is_skipped else _nullish(_get(rr, "reached_k"), False),
                "had_full_success": None if is_skipped else any(js_truthy(_get(a, "correct")) for a in aa),
                "end_reason": _nullish(_get(rr, "end_reason"), None),
                **res, "successful_round_api_seconds": res["api_seconds"] if js_truthy(_get(rr, "reached_k")) else None,
            })
    roundsByProblem = _group(rounds, lambda x: x["problem"])
    problems = []
    for p in s["dataset"]["problems"]:
        rr, cc = roundsByProblem.get(p["problem_number"], []), callsByProblem.get(p["problem_number"], [])
        completed = sum(r["status"] in ("success", "failed") for r in rr)
        skipped = sum(r["status"] == "skipped" for r in rr)
        problems.append({
            "problem": p["problem_number"], "problem_id": p["problem_id"],
            "status": "completed" if completed + skipped == config["rounds"] else "in_progress" if cc else "not_started",
            "skipped_rounds": skipped, "stopped_after_round": next((r["round"] for r in rr if r["status"] == "failed" and r["correct_count"] == 0), None),
            "completed_rounds": completed, "successful_rounds": sum(r["status"] == "success" for r in rr), "failed_rounds": sum(r["status"] == "failed" for r in rr),
            "first_round_accuracy_percent": rr[0]["accuracy_percent"], "per_eligible_rounds": sum(r["per_percent"] is not None for r in rr),
            "per_percent": _mean([r["per_percent"] for r in rr if r["per_percent"] is not None]),
            "attempts": _sum(rr, lambda r: r["attempts"]), "correct_count": _sum(rr, lambda r: r["correct_count"]), "incorrect_count": _sum(rr, lambda r: r["incorrect_count"]),
            "previous_rule_error_count": _sum(rr, lambda r: r["previous_rule_error_count"]), "ordinary_error_count": _sum(rr, lambda r: r["ordinary_error_count"]), **resources(cc),
        })
    defined = [p for p in problems if p["status"] == "completed" and p["per_percent"] is not None]
    completedIds = {p["problem"] for p in problems if p["status"] == "completed"}
    roundAverages = []
    for i in range(config["rounds"]):
        rr = [r for r in rounds if r["round"] == i + 1 and r["problem"] in completedIds and not r["skipped"]]
        column = "accuracy_percent" if i == 0 else "per_percent"
        values = [r[column] for r in rr if r[column] is not None]
        roundAverages.append({"round": i + 1, "metric": "R1 Accuracy" if i == 0 else "PER", "included_problems": len(values), "mean_percent": _mean(values)})
    summary = {
        **{k: _get(config, k) for k in ("protocol", "model", "reasoning_effort", "k", "T", "source_dataset_problems", "selection")},
        "status": "completed" if s["done"] else "in_progress", "planned_problems": len(problems), "completed_problems": sum(p["status"] == "completed" for p in problems),
        "completed_rounds": len(s["rounds"]), "skipped_rounds": len(s["skippedRounds"]), "successful_rounds": sum(js_truthy(_get(r, "reached_k")) for r in s["rounds"]),
        "failed_rounds": sum(not js_truthy(_get(r, "reached_k")) for r in s["rounds"]), "attempts": len(s["attempts"]), **resources(calls),
        "correct_count": sum(js_truthy(_get(a, "correct")) for a in s["attempts"]), "incorrect_count": sum(not js_truthy(_get(a, "correct")) for a in s["attempts"]),
        "previous_rule_error_count": sum(js_truthy(_get(a, "previous_rule_error")) for a in s["attempts"]),
        "ordinary_error_count": sum(not js_truthy(_get(a, "correct")) and not js_truthy(_get(a, "previous_rule_error")) for a in s["attempts"]),
        "mean_completed_problem_api_seconds": _mean([p["api_seconds"] for p in problems if p["status"] == "completed" and p["api_seconds"] is not None]),
        "mean_completed_problem_estimated_cost_usd": _mean([p["estimated_cost_usd"] for p in problems if p["status"] == "completed" and p["estimated_cost_usd"] is not None]),
        "completed_problems_with_unpriced_calls": sum(p["status"] == "completed" and p["unpriced_calls"] > 0 for p in problems),
        "successful_round_mean_api_seconds": _mean([r["api_seconds"] for r in rounds if js_truthy(r["reached_k"]) and r["api_seconds"] is not None]),
        "per_included_completed_problems": len(defined), "per_percent": _mean([p["per_percent"] for p in defined]),
        "first_round_accuracy_percent": _mean([p["first_round_accuracy_percent"] for p in problems if p["first_round_accuracy_percent"] is not None]),
        **{k: _get(config, k) for k in ("per_definition", "per_reference_board", "source_policy", "board_policy")},
        "round_averages_completed_problems": roundAverages,
        "per_aggregation": "Mean of eligible completed round PERs per problem, then mean of defined completed problem PERs. R1 and skipped rounds excluded.",
        **{k: _get(config, k) for k in ("feedback", "model_input_language", "board_delivery", "sensor_feedback_definition", "feedback_received_colors", "feedback_rule_violation_reason", "after_failed_round")},
        "units": {"time": "seconds", "cost": "USD", "tokens": "API reported token counts"},
        "cost_note": "API usage 및 pricing-snapshot.json 단가에 따른 추정액. 청구서 금액이 아님. 미확인 비용은 0원으로 취급하지 않음",
        "history_note": "모든 시도에 현재 전체 배치를 영어 안내문과 함께 제공. 문제 안에서 모든 응답 항목과 피드백 유지, 문제 사이에는 초기화. 라운드/시도 번호 및 숨겨진 정보는 전송하지 않음",
        "time_note": "api_seconds는 API 요청 소요시간 합계(중단/실패 요청 시간 포함, 응답 대기 외의 수동 중단/파일 저장시간 제외). wall_elapsed_seconds_including_pauses는 첫 요청~마지막 응답의 경과시간(재개 전 대기 포함). 시간 단위는 초. 성공 라운드는 reached_k=true로 식별",
    }
    writeJSON(target / "summary.json", summary)
    writeJSON(target / "rounds.json", rounds)
    writeJSON(target / "problems.json", problems)
    # A single atomic JSON array keeps the file valid after every saved call.
    atomicWrite(target / "attempts.json", js_dumps(s["attempts"]) + "\n")
    newAttempts = s["attempts"][previousCount:]
    callRows = []
    for c in calls:
        row = {k: _get(c, k) for k in ("call_id", "problem", "round", "attempt", "status", "started_at", "finished_at", "api_seconds", "http_status")}
        row.update({"response_id": _path(c, "response", "id"), "request_id": _get(c, "request_id"), "served_model": _path(c, "response", "model")})
        cost = _get(c, "cost")
        if isinstance(cost, dict):
            row.update(cost)
        callRows.append(row)
    writeJSON(target / "api-usage-cost.json", callRows)
    for p in s["dataset"]["problems"]:
        if incremental and not any(a["problem"] == p["problem_number"] for a in newAttempts):
            continue
        aa = attemptsByProblem.get(p["problem_number"], [])
        if not aa:
            continue
        pc = callsByProblem.get(p["problem_number"], [])
        parts = [f"문제 {js_string(p['problem_number'])} | 최초 모델 입력", _nullish(_get(pc[0] if pc else UNDEFINED, "new_input"), "")]
        for a in aa:
            seconds, cost = _get(a, "api_seconds"), _path(a, "cost", "estimated_cost_usd")
            timing = "?" if seconds in (None, UNDEFINED) else _to_fixed(seconds, 3)
            price = "미확인" if cost in (None, UNDEFINED) else _to_fixed(cost, 8)
            parts.append("\n" + "=" * 55 + f"\n라운드 {js_string(a['round'])} | 시도 {js_string(a['attempt'])}\n모델 응답\n{js_string(_get(a, 'raw_output'))}\n모델에게 보낸 피드백\n{js_string(_nullish(_get(a, 'next_model_input'), _get(a, 'feedback')))}\nAPI {timing}초 | 추정 비용 ${price}")
        writeJSON(target / "transcripts" / (p["problem_id"] + ".json"), {
            "problem": p["problem_number"], "problem_id": p["problem_id"],
            "text": "\n".join(parts) + "\n",
        })
    writeJSON(target / "progress.json", {
        "done": s["done"], "next_problem": None if s["done"] else s["dataset"]["problems"][s["problemIndex"]]["problem_number"],
        "next_round": None if s["done"] else s["roundIndex"] + 1, "next_attempt": None if s["done"] else s["attempt"], "consecutive_correct": s["streak"],
        "saved_api_calls": len(calls), "saved_attempts": len(s["attempts"]), "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    })
    _exportCache[folder] = {"state": s, "attempts": len(s["attempts"])}
    return summary

def summarize(problems, rounds, config):
    """Compute metrics for completed problems, excluding any partial problem."""
    completed = [p for p in problems if p['status'] == 'completed']
    by_problem = defaultdict(list)
    completed_ids = {p['problem'] for p in completed}
    for r in rounds:
        if r['problem'] in completed_ids:
            by_problem[r['problem']].append(r)
    mpe_values, error_values = [], []
    successful = failed = skipped = submissions = persistent = incorrect = 0
    per_problem = []
    for p in completed:
        rr = by_problem[p['problem']]
        if len(rr) != config['rounds'] or len({r['round'] for r in rr}) != len(rr):
            raise ValueError('Incomplete round reports; retry after the next report update.')
        active = [r for r in rr if r['attempts'] > 0]
        if not active or any(r['status'] not in ('success', 'failed') for r in active):
            raise ValueError('Completed problem contains incomplete rounds.')
        if sum(r['attempts'] for r in active) != p['attempts']:
            raise ValueError('Problem and round reports differ; retry after the next report update.')
        pers, errors = [], []
        for r in active:
            if r['correct_count'] + r['incorrect_count'] != r['attempts']:
                raise ValueError('Round submission counts do not sum correctly.')
            errors.append(100 * r['incorrect_count'] / r['attempts'])
            if r['round'] > 1 and r['per_eligible']:
                pers.append(100 * r['previous_rule_error_count'] / r['attempts'])
            successful += r['reached_k'] is True
            failed += r['status'] == 'failed'
            submissions += r['attempts']
            incorrect += r['incorrect_count']
            persistent += r['previous_rule_error_count']
        skipped += sum(r['skipped'] is True for r in rr)
        problem_mpe = mean(pers) if pers else None
        problem_error = mean(errors)
        if problem_mpe is not None:
            mpe_values.append(problem_mpe)
        error_values.append(problem_error)
        per_problem.append({'problem':p['problem'], 'mpe_percent':problem_mpe,
                            'mean_error_percent':problem_error,
                            'successful_rounds':sum(r['reached_k'] is True for r in active)})

    unknown_time = sum(p['unknown_time_calls'] for p in completed)
    unpriced = sum(p['unpriced_calls'] for p in completed)
    known_seconds = sum(p['known_api_seconds'] for p in completed)
    known_cost = sum(p['known_cost_subtotal_usd'] for p in completed)
    start = min((p['first_request_at'] for p in completed if p['first_request_at']), default=None)
    end = max((p['last_response_at'] for p in completed if p['last_response_at']), default=None)
    elapsed = ((datetime.fromisoformat(end.replace('Z','+00:00')) -
                datetime.fromisoformat(start.replace('Z','+00:00'))).total_seconds()
               if start and end else None)
    return {
        'model':config['model'], 'T':config['T'], 'k':config['k'],
        'planned_problems':len(problems), 'completed_problems':len(completed),
        'mpe_included_problems':len(mpe_values),
        'mpe_percent':mean(mpe_values) if mpe_values else None,
        'mean_error_percent':mean(error_values) if error_values else None,
        'all_rounds_passed_problems':sum(p['successful_rounds']==config['rounds'] for p in per_problem),
        'successful_rounds':successful, 'failed_rounds':failed, 'skipped_rounds':skipped,
        'planned_rounds_in_completed_problems':len(completed)*config['rounds'],
        'attempted_rounds':successful+failed, 'submissions':submissions,
        'correct_submissions':submissions-incorrect, 'persistent_errors':persistent,
        'ordinary_errors':incorrect-persistent,
        'api_calls':sum(p['api_calls'] for p in completed),
        'api_seconds':None if unknown_time else known_seconds,
        'known_api_seconds':known_seconds, 'unknown_time_calls':unknown_time,
        'estimated_cost_usd':None if unpriced else known_cost,
        'known_cost_subtotal_usd':known_cost, 'unpriced_calls':unpriced,
        'first_request_at':start, 'last_response_at':end,
        'wall_elapsed_seconds_including_pauses':elapsed,
        'scope':'Completed problems only; partial problems are excluded from every metric.',
        'aggregation':'MPE: mean eligible round PER per problem, then mean of defined problem MPEs. Mean error: mean attempted round error per problem, then mean across completed problems.',
        'time_definition':'API time sums request durations. Wall elapsed includes pauses between requests.',
        'cost_definition':'Estimate from recorded API usage and the saved pricing snapshot, not an invoice.',
        'per_problem':per_problem,
    }

def summarize_folder(folder):
    folder = Path(folder)
    read = lambda name: json.loads((folder/name).read_text(encoding='utf-8-sig'))
    return summarize(read('problems.json'), read('rounds.json'), read('config.json'))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-folder', type=Path, help='Saved run directory; defaults to the latest run.')
    args = parser.parse_args()
    folder = args.run_folder
    if folder is None:
        root = Path(__file__).resolve().parents[1]/'result'
        runs = sorted(p for p in root.iterdir() if (p/'config.json').is_file()) if root.exists() else []
        if not runs:
            parser.error('No saved result found; pass --run-folder or run an experiment first.')
        folder = runs[-1]
    try:
        report = summarize_folder(folder)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'Cannot summarize results: {exc}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))

PRICING = {
    "gpt-6.1-sol": {'verified_at': '2026-10-24', 'model': 'gpt-6.1-sol', 'currency': 'USD', 'unit': 'per_1000000_tokens', 'standard_global': {'input': 2, 'cached_input': 0.1, 'cache_write_30m': 2.5, 'cache_write_12h': 5, 'output': 10}, 'max_verified_input_tokens': 272000, 'sources': ['https://developers.openai.com/api/docs/models/gpt-6.1-sol', 'https://developers.openai.com/api/docs/pricing', 'https://developers.openai.com/api/docs/guides/prompt-caching'], 'note': 'List-price estimate from returned usage; not an invoice. Taxes/discounts excluded. Missing usage or cache detail remains unknown. Output tokens already include reasoning tokens.'},
}

for _model, _input, _output in [('claude-opus-5-5', 4, 20), ('claude-sonnet-5-5', 2, 10)]:
    PRICING[_model] = {
        'verified_at': '2026-10-29', 'provider': 'anthropic', 'model': _model,
        'currency': 'USD', 'unit': 'per_1000000_tokens',
        'standard_global': {'input': _input, 'output': _output,
                            'cached_input': _input * 0.05,
                            'cache_write_5m': _input * 1.25, 'cache_write_1h': _input * 2},
        'max_verified_input_tokens': 1000000,
        'sources': ['https://platform.claude.com/docs/en/models/' + ('opus-5-5' if 'opus' in _model else 'sonnet-5-5') + '/overview',
                    'https://platform.claude.com/docs/en/build-with-claude/prompt-caching'],
        'note': 'Standard global list-price estimate from reported usage. Output includes thinking. Missing or unsupported usage remains unknown.',
    }
del _model, _input, _output


def pricingForModel(model):
    """Return a separate copy to save with each experiment's configuration."""
    if model not in PRICING:
        raise ValueError('No verified pricing for model: ' + str(model))
    return deepcopy(PRICING[model])


def _price_openai(response, pricing):
    u = _get(response, "usage")
    d = _get(u, "input_tokens_details")

    def unknown(why):
        return {"estimated_cost_usd": None, "cost_status": why}

    def n(value):
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and value >= 0
            and value == math.trunc(value)
        )

    if not js_truthy(u) or not n(_get(u, "input_tokens")) or not n(_get(u, "output_tokens")):
        return unknown("usage_not_reported")
    model = _get(response, "model")
    if js_truthy(model) and model != pricing["model"] and not model.startswith(pricing["model"] + "-"):
        return unknown("unexpected_model")
    if _get(response, "service_tier") not in ("default", "standard", UNDEFINED, None):
        return unknown("unexpected_service_tier")
    if _get(u, "inference_geo") not in (UNDEFINED, None, "global"):
        return unknown("non_global_pricing")
    if u["input_tokens"] > pricing["max_verified_input_tokens"]:
        return unknown("long_context_price_not_configured")
    if not js_truthy(d) or not n(_get(d, "cached_tokens")) or not n(_get(d, "cache_write_tokens")):
        return unknown("cache_breakdown_not_reported")
    read = d["cached_tokens"]
    write = d["cache_write_tokens"]
    ordinary = u["input_tokens"] - read - write
    # Request explicitly selects 30m. Respect per-lifetime usage if returned.
    write_details = _get(d, "cache_write_tokens_details")
    write30 = _nullish(_get(write_details, "ttl_30m_tokens"), write)
    write12 = _nullish(_get(write_details, "ttl_12h_tokens"), 0)
    if not n(ordinary) or not n(write30) or not n(write12) or write30 + write12 != write:
        return unknown("inconsistent_usage")
    p = pricing["standard_global"]
    inputCost = (ordinary * p["input"] + read * p["cached_input"] + write30 * p["cache_write_30m"] + write12 * p["cache_write_12h"]) / 1e6
    outputCost = u["output_tokens"] * p["output"] / 1e6
    return {
        "cost_status": "estimated_from_reported_usage",
        "estimated_cost_usd": inputCost + outputCost,
        "input_cost_usd": inputCost,
        "output_cost_usd": outputCost,
        "input_tokens": u["input_tokens"],
        "ordinary_input_tokens": ordinary,
        "cached_input_tokens": read,
        "cache_write_tokens": write,
        "cache_write_30m_tokens": write30,
        "cache_write_12h_tokens": write12,
        "output_tokens": u["output_tokens"],
        "reasoning_tokens": _nullish(_get(_get(u, "output_tokens_details"), "reasoning_tokens"), None),
        "total_tokens": _nullish(_get(u, "total_tokens"), u["input_tokens"] + u["output_tokens"]),
    }

def _price_anthropic(response, pricing):
    """Price ordinary input, both cache lifetimes, and inclusive output once."""
    def unknown(reason):
        return {'estimated_cost_usd': None, 'cost_status': reason}

    def count(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 and int(value) == value

    if not isinstance(response, dict) or not isinstance(response.get('usage'), dict):
        return unknown('usage_not_reported')
    usage = response['usage']
    if not count(usage.get('input_tokens')) or not count(usage.get('output_tokens')):
        return unknown('usage_not_reported')
    model = response.get('model')
    if model and model != pricing['model'] and not model.startswith(pricing['model'] + '-'):
        return unknown('unexpected_model')
    if usage.get('service_tier', response.get('service_tier')) not in (None, 'standard', 'default'):
        return unknown('unexpected_service_tier')
    if usage.get('inference_geo') not in (None, 'global'):
        return unknown('non_global_pricing')
    ordinary, output = usage['input_tokens'], usage['output_tokens']
    read = usage.get('cache_read_input_tokens', 0)
    write = usage.get('cache_creation_input_tokens', 0)
    if not count(read) or not count(write):
        return unknown('inconsistent_usage')
    creation = usage.get('cache_creation')
    if creation is None:
        # This runner uses the default five-minute TTL whenever caching is on.
        write5, write1 = write, 0
    elif isinstance(creation, dict):
        write5 = creation.get('ephemeral_5m_input_tokens', 0)
        write1 = creation.get('ephemeral_1h_input_tokens', 0)
    else:
        return unknown('inconsistent_usage')
    if not count(write5) or not count(write1) or write5 + write1 != write:
        return unknown('inconsistent_usage')
    total_input = ordinary + read + write
    if total_input > pricing['max_verified_input_tokens']:
        return unknown('long_context_price_not_configured')
    rate = pricing['standard_global']
    input_cost = (ordinary * rate['input'] + read * rate['cached_input'] +
                  write5 * rate['cache_write_5m'] + write1 * rate['cache_write_1h']) / 1e6
    output_cost = output * rate['output'] / 1e6
    return {
        'cost_status': 'estimated_from_reported_usage',
        'estimated_cost_usd': input_cost + output_cost,
        'input_cost_usd': input_cost, 'output_cost_usd': output_cost,
        'input_tokens': total_input, 'ordinary_input_tokens': ordinary,
        'cached_input_tokens': read, 'cache_write_tokens': write,
        'cache_write_5m_tokens': write5, 'cache_write_1h_tokens': write1,
        'output_tokens': output,
        'reasoning_tokens': _nullish(_get(_get(usage, 'output_tokens_details'), 'reasoning_tokens'), None),
        'total_tokens': total_input + output,
    }


def priceUsage(response, pricing):
    """Calculate an estimate using the saved model-specific rate snapshot."""
    if pricing.get('provider') == 'anthropic' or pricing.get('model', '').startswith('claude-'):
        return _price_anthropic(response, pricing)
    return _price_openai(response, pricing)


if __name__ == '__main__':
    main()
