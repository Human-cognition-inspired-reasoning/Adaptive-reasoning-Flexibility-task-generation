"""Researcher-facing terminal output, never included in model input.

Uses only the standard library and omits ANSI colors when redirected to a file.
Displays saved records without changing experiment decisions or API requests.
"""

from collections.abc import Mapping
import json
import math
import os
import re
import sys
import unicodedata

from .policies import BINARY, CURRENT_VIOLATION, FOUR_WAY, PREVIOUS_RULE, policy_name

_ANSI = re.compile("\\x1b\\][^\\x07\\x1b]*(?:\\x07|\\x1b\\\\)|\\x1b\\[[0-?]*[ -/]*[@-~]|\\x1b[@-_]")
_COLORS = {"cyan": "36", "green": "32", "red": "31", "yellow": "33", "dim": "2", "bold": "1"}


def clean(value):
    """Remove ANSI/control characters from external text and collapse it to one line."""
    value = _ANSI.sub("", str(value))
    return "".join(
        " " if char in "\n\r\t" else char
        for char in value
        if char in "\n\r\t" or unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    )


def display_width(value):
    return sum(
        2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        for char in clean(value)
        if not unicodedata.combining(char)
    )


def pad(value, width, right=False):
    value = clean(value)
    gap = " " * max(0, width - display_width(value))
    return gap + value if right else value + gap


def wrap(value, width):
    """Prefer word boundaries, splitting long paths or words to fit the display."""
    lines = []
    line = ""
    used = 0
    for char in clean(value):
        size = display_width(char)
        if line and used + size > width:
            boundary = line.rfind(" ")
            if boundary > 0 and char != " ":
                lines.append(line[:boundary])
                line = line[boundary + 1 :]
                used = display_width(line)
            else:
                lines.append(line)
                line, used = "", 0
            if not line and char == " ":
                continue
        line += char
        used += size
    return lines + [line]


def _field(rule, name):
    return rule.get(name) if isinstance(rule, Mapping) else getattr(rule, name, None)


def condition_text(condition):
    return (
        {
            "peg": "Peg restriction",
            "occupancy": "Occupancy restriction",
            "source_sequence": "Source sequence",
            "visit": "Prior visit",
        }.get(condition, "Unknown condition")
        if isinstance(condition, str)
        else "Unknown condition"
    )


def initial_stacks(initial_positions=None, *, pegs=("A", "B", "C", "D"), disk_count=3):
    """Convert positions ordered by disk 1, 2, 3 into bottom-to-top peg stacks."""
    positions = ("A",) * disk_count if initial_positions is None else initial_positions
    if (
        not isinstance(positions, (list, tuple))
        or len(positions) != disk_count
        or any((not isinstance(peg, str) or peg not in pegs for peg in positions))
    ):
        return None
    return {
        peg: [disk for disk in range(disk_count, 0, -1) if positions[disk - 1] == peg]
        for peg in pegs
    }


def initial_state_text(rule, *, pegs=("A", "B", "C", "D")):
    stacks = initial_stacks(_field(rule, "initial_positions"), pegs=pegs)
    return (
        "  ".join((f"{peg}=[{','.join(map(str, disks))}]" for peg, disks in stacks.items()))
        if stacks
        else "Invalid initial board"
    )


def candidate_rule_text(rule):
    if not isinstance(rule, Mapping):
        return "Invalid restriction record"
    kind = rule.get("kind")
    if kind == "peg":
        return f"No disk may move from {clean(rule.get('source'))} to {clean(rule.get('destination'))}."
    if kind == "occupancy":
        return (
            f"When peg {clean(rule.get('occupied_peg'))} is occupied, no disk may move "
            f"from {clean(rule.get('source'))} to {clean(rule.get('destination'))}."
        )
    if kind == "source_sequence":
        return f"After a move from {clean(rule.get('previous_source'))}, the next move cannot start from {clean(rule.get('source'))}."
    if kind == "visit":
        return (
            f"Disk {clean(rule.get('locked_disk'))} cannot move until disk "
            f"{clean(rule.get('visitor_disk'))} visits {clean(rule.get('visited_peg'))}. "
            "The starting position counts as a visit."
        )
    return "Invalid restriction record"


def _restrictions(rule):
    return (candidate_rule_text(_field(rule, "candidate_rule")),) if rule is not None else ()


def rule_text(rule, *, require_goal=True):
    goal = _field(rule, "goal_peg")
    goal_label = (
        f"Goal {goal}"
        if isinstance(goal, str) and goal in ("A", "B", "C", "D")
        else "Invalid goal record"
    )
    return goal_label + " · " + (" · ".join(_restrictions(rule)) or "Invalid restriction record")


def _rate(numerator, denominator):
    if numerator is None or not denominator:
        return "—"
    return f"{numerator}/{denominator} ({100 * numerator / denominator:.1f}%)"


def describe_moves(moves, *, pegs=("A", "B", "C", "D"), disk_count=3, initial_positions=None):
    """Display a submitted plan with disk numbers without changing input or grading records.

    Each submission starts from its round's initial board. Additional restrictions
    do not stop diagnostic replay. Do not infer disks after the first basic move error.
    """
    if not isinstance(moves, (list, tuple)) or not moves:
        return None
    stacks = initial_stacks(initial_positions, pegs=pegs, disk_count=disk_count)
    if stacks is None:
        return None
    stopped = False
    labels = []
    for step, move in enumerate(moves, 1):
        valid_command = (
            isinstance(move, str)
            and len(move) == 2
            and (move[0] in stacks)
            and (move[1] in stacks)
            and (move[0] != move[1])
        )
        direction = "→".join(move) if valid_command else clean(move)
        disk, status = ("?", "")
        if stopped:
            status = " (unexecuted: earlier illegal move)"
        elif not valid_command:
            status = " (failed: invalid command)"
            stopped = True
        else:
            source, target = move
            if not stacks[source]:
                status = " (failed: empty source peg)"
                stopped = True
            else:
                disk = stacks[source][-1]
                if stacks[target] and disk > stacks[target][-1]:
                    status = " (failed: larger disk on smaller disk)"
                    stopped = True
                else:
                    stacks[target].append(stacks[source].pop())
        labels.append(f"{step}) Disk {disk} {direction}{status}")
    return labels


class TerminalReporter:
    """Display progress, rules, move plans, and persistent error rates for researchers."""

    def __init__(
        self,
        directory,
        config,
        rules,
        max_attempts,
        required_successes,
        *,
        problems=None,
        round_policies=None,
        persistent_error_policy=PREVIOUS_RULE,
    ):
        self.directory = directory
        self.config = config
        self.rules = tuple(rules)
        self.problems = tuple(problems or ())
        self.round_policies = tuple(round_policies or ())
        self.persistent_error_policy = policy_name(persistent_error_policy)
        self.default_max_attempts = max_attempts
        self.default_required_successes = required_successes
        self.pegs = ("A", "B", "C", "D")
        self.max_attempts = max_attempts
        self.required_successes = required_successes
        self.width = 88
        self.episode = None
        self.round_number = None
        self.streak = 0
        self.use_color = (
            bool(getattr(sys.stdout, "isatty", lambda: False)())
            and "NO_COLOR" not in os.environ
            and (os.environ.get("TERM") != "dumb")
        )

    def _policy(self, number):
        if self.round_policies:
            policy = self.round_policies[number - 1]
            return (_field(policy, "max_attempts"), _field(policy, "success_streak_required"))
        return (self.default_max_attempts, self.default_required_successes)

    def _problem_id(self, episode):
        return _field(self.problems[episode - 1], "id") if self.problems else f"E{episode}"

    def _print(self, value=""):
        print(value, flush=True)

    def _color(self, value, color):
        value = clean(value)
        return f"\x1b[{_COLORS[color]}m{value}\x1b[0m" if self.use_color else value

    def _heading(self, title):
        self._print()
        title = f" {clean(title)} "
        self._print(
            self._color(
                "╭" + title + "─" * max(0, self.width - 2 - display_width(title)) + "╮", "cyan"
            )
        )

    def _line(self, text="", color=None):
        inner = self.width - 4
        for line in wrap(text, inner):
            padded = pad(line, inner)
            self._print("│ " + (self._color(padded, color) if color else padded) + " │")

    def _close(self):
        self._print(self._color("╰" + "─" * (self.width - 2) + "╯", "cyan"))

    def _detail(self, label, value, color=None):
        """Align labels and value columns even when a value wraps across lines."""
        label_width = 17
        for index, line in enumerate(wrap(value, self.width - 4 - label_width)):
            self._line(pad(label if index == 0 else "", label_width) + line, color)

    def _policy_groups(self):
        """Group consecutive rounds only when their effective stopping policies match."""
        groups = []
        for number in range(1, len(self.rules) + 1):
            policy = self._policy(number)
            if groups and groups[-1][2] == policy:
                groups[-1] = (groups[-1][0], number, policy)
            else:
                groups.append((number, number, policy))
        return groups

    def start(self):
        episodes = self.config.get("episodes", 1)
        policies = [self._policy(number) for number in range(1, len(self.rules) + 1)]
        maximum = episodes * sum(limit for limit, _ in policies)
        fourway = policy_name(self.config.get("feedback_policy", BINARY)) == FOUR_WAY
        self._heading(
            "Tower of Hanoi · Four-way feedback" if fourway else "Tower of Hanoi · Binary feedback"
        )
        self._detail("Model", self.config.get("model", "—"), "bold")
        self._detail(
            "API / effort",
            f"{self.config.get('provider', 'openai')} / {self.config.get('effort', '—')}",
        )
        self._detail(
            "Run size",
            f"{episodes} {'distinct problems' if self.problems else 'independent experiments'} × {len(self.rules)} rounds",
        )
        if self.problems:
            self._detail(
                "Problem order",
                f"Problems 1–{episodes} in this run retain their original dataset IDs.",
            )
        conditions = {
            key: sum((_field(problem, "condition") == key for problem in self.problems))
            for key in ("peg", "occupancy", "source_sequence", "visit")
        }
        condition_labels = [
            f"{condition_text(key)}: {count} problems" for key, count in conditions.items() if count
        ]
        for index in range(0, len(condition_labels), 2):
            self._detail(
                "Condition" if index == 0 else "", " · ".join(condition_labels[index : index + 2])
            )
        self._detail("Puzzle", f"3 disks and {len(self.pegs)} pegs ({', '.join(self.pegs)})")
        self._detail("Board / goal", "Each round has a new initial board and goal peg.")
        self._line()
        self._line("Round progression", "bold")
        for first, last, (limit, required) in self._policy_groups():
            scope = f"R{first}" if first == last else f"R{first}–R{last}"
            self._detail("Rounds", scope)
            self._detail("Attempt limit", f"T = {limit} submissions per round")
            self._detail("Pass criterion", f"k = {required} consecutive correct submissions")
        self._detail(
            "On success", "Advance one round, or start the next problem after the final round."
        )
        self._detail(
            "At limit",
            "If the criterion is unmet, skip the remaining rounds and start the next problem.",
        )
        self._detail(
            "Overall limit", f"Up to {maximum:,} submissions. Early stopping reduces the count."
        )
        self._detail(
            "History",
            "Conversation and reasoning state carry across rounds and reset for each new problem.",
        )
        self._line()
        self._line("Model inputs, scoring and logs", "bold")
        if fourway:
            self._detail(
                "Feedback", "Correct / Incorrect / Rule violation / Correct, but rule violation"
            )
            self._detail(
                "Model receives",
                "One of the four English labels, as recorded in the submission log.",
            )
            self._detail(
                "Scoring",
                "Only Correct counts as a success. Correct, but rule violation counts as incorrect.",
            )
        else:
            self._detail("Feedback", "Correct / Incorrect (in English)")
        self._detail(
            "Output format",
            "The model is asked for JSON without an API schema. Both providers use the same local grader.",
        )
        self._detail(
            "Output limit",
            f"{self.config.get('max_output_tokens', 25000):,} tokens; no automatic retries",
        )
        self._detail(
            "Whitespace",
            "256 consecutive whitespace characters end the problem without scoring this submission.",
        )
        if self.persistent_error_policy == PREVIOUS_RULE:
            self._detail(
                "PE criterion",
                "An incorrect plan that obeys the basic and previous movement rules from the current initial board.",
            )
        elif self.persistent_error_policy == CURRENT_VIOLATION:
            self._detail(
                "PE criterion",
                "A violation of the current movement restriction. Goal mismatches are counted separately.",
            )
        else:
            self._detail("PE criterion", "See the saved protocol for this run.")
        self._detail(
            "Researcher view",
            "The API input omits hidden goals, restrictions, violation locations and round information.",
            "yellow",
        )
        self._detail("Logs", self.directory)
        self._close()

    def round_start(self, episode, round_number):
        self.episode, self.round_number, self.streak = (episode, round_number, 0)
        if self.problems:
            problem = self.problems[episode - 1]
            self.rules = tuple(_field(problem, "rounds") or _field(problem, "rules"))
        self.max_attempts, self.required_successes = self._policy(round_number)
        current = self.rules[round_number - 1]
        previous = self.rules[round_number - 2] if round_number > 1 else None
        identity = (
            f"Problem {episode}/{self.config.get('episodes', 1)} · ID {self._problem_id(episode)}"
            if self.problems
            else f"Independent experiment {episode}/{self.config.get('episodes', 1)}"
        )
        self._heading(f"{identity} · Round {round_number}/{len(self.rules)}")
        if self.problems and _field(self.problems[episode - 1], "condition"):
            self._detail(
                "Condition", condition_text(_field(self.problems[episode - 1], "condition"))
            )
        self._detail("Initial board", initial_state_text(current, pegs=self.pegs))
        self._detail("Stack order", "[bottom → top]. Each submission starts from this board.")
        goal = f"{_field(current, 'goal_peg') or 'C'}=[3,2,1]"
        if previous is not None:
            goal += f" · previous goal {_field(previous, 'goal_peg') or 'C'} → current goal {_field(current, 'goal_peg') or 'C'}"
        self._detail("Goal", goal, "bold")
        old, new = (_restrictions(previous), _restrictions(current))
        self._detail("Current rule", " · ".join(new) or "Basic Hanoi rules only", "bold")
        if round_number > 1:
            self._detail("Previous rule", " · ".join(old) or "Basic Hanoi rules only")
            self._detail(
                "Rule change",
                (
                    "The current restriction replaces the previous one."
                    if old != new
                    else "Same restriction as the previous round."
                ),
            )
            if initial_state_text(previous, pegs=self.pegs) != initial_state_text(
                current, pegs=self.pegs
            ):
                self._detail("Previous board", initial_state_text(previous, pegs=self.pegs))
        self._detail(
            "Basic rules", "Move one top disk at a time. A larger disk cannot go on a smaller disk."
        )
        self._line()
        self._detail("Attempt limit", f"T = {self.max_attempts}")
        next_stage = "next problem" if round_number == len(self.rules) else "next round"
        self._detail(
            "Pass criterion",
            f"k = {self.required_successes} consecutive correct submissions → {next_stage}",
        )
        if self.max_attempts is not None:
            self._detail(
                "At limit",
                "If the criterion is unmet, skip the remaining rounds and start the next problem.",
            )
        self._close()

    def waiting(self, attempt, transport_attempt):
        retry = f" · transport attempt {transport_attempt}" if transport_attempt > 1 else ""
        position = f"{attempt:02d}/{self.max_attempts}"
        progress = (
            f"Problem {self.episode}/{self.config.get('episodes', 1)} · "
            if self.episode is not None
            else ""
        )
        self._print(
            self._color(
                f"  … {progress}R{self.round_number} attempt {position} · waiting for the API{retry}",
                "dim",
            )
        )

    def submission(self, record, recovered=False):
        success = bool(record.get("current_success"))
        supplied_streak = record.get("success_streak")
        self.streak = (
            supplied_streak
            if isinstance(supplied_streak, int)
            else self.streak + 1 if success else 0
        )
        attempt = record.get("attempt", "?")
        stamp = f"{attempt:02d}" if isinstance(attempt, int) else clean(attempt)
        icon, color = ("✓", "green") if success else ("✗", "red")
        feedback = record.get("feedback", "Correct" if success else "Incorrect")
        cache = " · restored from log" if recovered else ""
        position = f"{stamp}/{self.max_attempts}"
        self._print(
            self._color(
                f"  {icon} Attempt {position}  {feedback}  |  Correct streak {self.streak}/{self.required_successes}{cache}",
                color,
            )
        )
        audit = record.get("reasoning_replay")
        if audit:
            incoming = audit.get("input", {}).get("opaque_reasoning_items", 0)
            outgoing = audit.get("output", {}).get("opaque_reasoning_items", 0)
            self._print(
                f"    Reasoning state: {incoming} items replayed, {outgoing} preserved (encrypted or signed)"
            )
        pe = record.get("persistent_error")
        pe_text = "n/a (R1 excluded)" if pe is None else "yes" if pe else "no"
        elapsed = record.get("elapsed_seconds")
        duration = (
            f"{elapsed:.2f}s"
            if isinstance(elapsed, (int, float)) and math.isfinite(elapsed)
            else "—"
        )
        self._print(f"    Persistent error (PE): {pe_text}  ·  Response time: {duration}")
        if "previous_rule_compliant" in record:
            previous = record["previous_rule_compliant"]
            previous_text = "n/a (R1)" if previous is None else "yes" if previous else "no"
            self._print(
                f"    Previous rule obeyed: {previous_text} · full plan checked from the current initial board"
            )
        if "movement_rule_error" in record:
            violations = record.get("movement_rule_violations") or []
            invalid_plan = (record.get("base_error") or {}).get("kind") == "invalid_plan"
            unknown_goal = (
                "unknown (invalid plan format)" if invalid_plan else "unknown (illegal basic move)"
            )
            goal = (
                "missed"
                if record.get("goal_error") is True
                else "reached" if record.get("goal_error") is False else unknown_goal
            )
            self._print(f"    Movement restriction violations: {len(violations)}  ·  Goal: {goal}")
            for item in violations:
                move = "→".join(clean(item.get("move", "")))
                disk = item.get("disk") if item.get("disk") is not None else "unknown"
                self._print(f"      Step {clean(item.get('step'))}: {move} · disk {clean(disk)}")
        moves = record.get("moves")
        current = (
            self.rules[self.round_number - 1]
            if isinstance(self.round_number, int) and 1 <= self.round_number <= len(self.rules)
            else None
        )
        commands = describe_moves(
            moves, pegs=self.pegs, initial_positions=_field(current, "initial_positions")
        )
        if commands is None:
            try:
                empty_plan = json.loads(record.get("raw_output", "")) == {"moves": []}
            except (ValueError, TypeError):
                empty_plan = False
            if empty_plan:
                self._print("    Plan: empty list (at least one move required)")
            else:
                self._print("    Plan: no valid JSON move list (original response saved in log)")
        else:
            self._print(f"    Submitted plan ({len(moves)} moves in order, with disk numbers):")
            line = ""
            for command in commands:
                candidate = f"{line} · {command}" if line else command
                if line and display_width(candidate) > self.width - 6:
                    self._print("      " + line)
                    line = ""
                pieces = wrap(command, self.width - 6)
                if len(pieces) > 1:
                    if line:
                        self._print("      " + line)
                    for piece in pieces[:-1]:
                        self._print("      " + piece)
                    line = pieces[-1]
                else:
                    line = f"{line} · {command}" if line else command
            if line:
                self._print("      " + line)
        self._print()

    def round_end(self, round_summary):
        attempts = round_summary.get("attempts", 0)
        criterion_at = round_summary.get("criterion_met_at")
        success = round_summary.get("status") == "success" or criterion_at is not None
        label = (
            (
                "first correct submission"
                if self.required_successes == 1
                else f"{self.required_successes} consecutive correct submissions"
            )
            if success
            else f"attempt limit ({self.max_attempts}) reached before meeting the criterion"
        )
        if not round_summary.get("complete", True):
            label = (
                "generation stopped; incomplete"
                if round_summary.get("status") == "generation_failed"
                else "in progress"
            )
        self._print(
            self._color(
                f"  R{round_summary.get('round', self.round_number)}: {label}  |  Submissions: {attempts}",
                "green" if success else "yellow",
            )
        )
        if round_summary.get("round", self.round_number) > 1:
            self._print(
                f"  Round PER: {_rate(round_summary.get('persistent_errors', 0), attempts)} · denominator: graded submissions"
            )
            if "goal_errors" in round_summary:
                self._print(
                    f"  Goal missed: {round_summary.get('goal_errors', 0)} · goal unknown: {attempts - round_summary.get('goal_evaluated_attempts', 0)} (separate from PE)"
                )
        self._print(f"  Round error rate: {_rate(round_summary.get('failures', 0), attempts)}")
        self._print("  " + "─" * (self.width - 4))

    def generation_failure(self, failure):
        self._line(
            f"Problem {failure.get('episode', self.episode)}/{self.config.get('episodes', 1)} · ID {failure['problem_id']} · R{failure['round']} attempt {failure['attempt']}: generation stopped ({failure['reason']})",
            "yellow",
        )
        self._line("Unscored. No automatic retry. Skipping the remaining rounds of this problem.")

    def problem_exhausted(self, episode, round_number):
        self._print(
            self._color(
                f"  Problem {episode}/{self.config.get('episodes', 1)} ended · ID {self._problem_id(episode)} · R{round_number} exhausted its attempts before meeting the criterion.",
                "yellow",
            )
        )
        self._print(
            self._color("  Skipping remaining rounds and continuing to the next problem.", "yellow")
        )

    def finish(self, summary):
        error_rounds = "R1~R5"
        error_problem_count_field = "error_metric_eligible_problems"
        error_round_count_field = "eligible_error_rounds"
        self._heading("Results · Round PER → Problem MPE → Total MPE")
        if summary.get("complete"):
            status = "complete"
        elif summary.get("execution_finished"):
            status = "finished with incomplete problems"
        else:
            status = "in progress (partial results)"
        self._line(
            f"Status: {status}  |  Graded submissions: {summary.get('recorded_submissions', 0)}",
            "bold",
        )
        if summary.get("generation_failure_count"):
            self._line(
                f"Problems with generation failures: {summary['generation_failure_count']}. Failed responses were unscored and remaining rounds skipped.",
                "yellow",
            )
        if summary.get("usage_incomplete"):
            self._line("Partial token usage only. Actual charges may be higher.", "yellow")
        self._line(
            f"Successful rounds: {summary.get('successful_rounds', 0)}  |  Exhausted: {summary.get('exhausted_rounds', 0)}  |  Excluded: {summary.get('excluded_rounds', 0)}"
        )
        self._line()
        widths = (7, 3, 5, 6, 9, 9, 12, 19)
        headers = ("ID", "R", "N", "✓/✗", "First hit", "Passed at", "Status", "PER (PE / N)")
        self._line(" ".join((pad(value, width) for value, width in zip(headers, widths))))
        self._line("─" * (sum(widths) + len(widths) - 1))
        for episode in summary.get("episodes", []):
            if episode.get("condition"):
                self._line(
                    f"Problem {episode.get('episode', '?')}/{self.config.get('episodes', 1)} · ID {episode.get('problem_id')} · {condition_text(episode['condition'])}"
                )
            for item in episode.get("rounds", []):
                first, criterion = (item.get("first_success_attempt"), item.get("criterion_met_at"))
                status = {
                    "success": "Passed",
                    "exhausted": "Exhausted",
                    "in_progress": "In progress",
                    "pending": "Pending",
                    "generation_failed": "Failed",
                    "excluded": "Excluded",
                }.get(item.get("status"), "—")
                values = (
                    episode.get("problem_id", f"E{episode.get('episode', '?')}"),
                    f"R{item.get('round', '?')}",
                    str(item.get("attempts", 0)),
                    f"{item.get('successes', 0)}/{item.get('failures', 0)}",
                    f"{first}" if first is not None else "—",
                    f"{criterion}" if criterion is not None else "—",
                    status,
                    (
                        "Excluded"
                        if item.get("round") == 1
                        else _rate(item.get("persistent_errors", 0), item.get("attempts", 0))
                    ),
                )
                self._line(" ".join((pad(value, width) for value, width in zip(values, widths))))
            value = episode.get("mpe")
            rate = f"{value:.2%}" if isinstance(value, (float, int)) else "—"
            self._line(
                f"  {episode.get('problem_id')} MPE {rate} · scored rounds: {episode.get('eligible_scored_rounds', 0)} (R1 excluded)"
            )
            value = episode.get("mean_error_rate")
            rate = (
                f"{value:.2%}"
                if isinstance(value, (float, int)) and (not isinstance(value, bool))
                else "—"
            )
            self._line(
                f"  {episode.get('problem_id')} mean error rate {rate} · observed rounds in {error_rounds}: {episode.get(error_round_count_field, '—')}"
            )
        self._line()
        pooled = summary.get("pooled", {})
        total_mpe = summary.get("total_mpe")
        rate = f"{total_mpe:.2%}" if isinstance(total_mpe, (float, int)) else "—"
        self._line(
            f"Total MPE: {rate} · problems with graded R2 or later submissions: N={summary.get('metric_eligible_problems', 0)}",
            "bold",
        )
        value = summary.get("total_mean_error_rate")
        rate = (
            f"{value:.2%}"
            if isinstance(value, (float, int)) and (not isinstance(value, bool))
            else "—"
        )
        self._line(
            f"Overall mean error rate: {rate} · problems with graded {error_rounds} submissions: N={summary.get(error_problem_count_field, '—')}",
            "bold",
        )
        self._line(
            f"Pooled PER: {_rate(pooled.get('persistent_errors', 0), pooled.get('attempts', 0))}"
        )
        self._line(
            f"Pooled error rate: {_rate(pooled.get('failures', 0), pooled.get('attempts', 0))} · incorrect / graded R2–R5 submissions"
        )
        if "goal_errors" in pooled:
            self._line(
                f"Goal missed: {pooled.get('goal_errors', 0)} · goal unknown: {pooled.get('attempts', 0) - pooled.get('goal_evaluated_attempts', 0)} (separate diagnostic)"
            )
        for condition, item in summary.get("conditions", {}).items():
            value = item.get("mpe")
            rate = f"{value:.2%}" if isinstance(value, (float, int)) else "—"
            self._line(
                f"{item.get('label', condition_text(condition))} MPE: {rate} · eligible problems: N={item.get('metric_eligible_problems', 0)}"
            )
            value = item.get("mean_error_rate")
            rate = (
                f"{value:.2%}"
                if isinstance(value, (float, int)) and (not isinstance(value, bool))
                else "—"
            )
            self._line(
                f"  Mean error rate: {rate} · {error_rounds} problems: N={item.get(error_problem_count_field, '—')}"
            )
        if self.persistent_error_policy == PREVIOUS_RULE:
            self._line(
                "A persistent error is an incorrect complete plan that obeys all basic moves and the previous restriction on the current board."
            )
            self._line(
                "A plan can qualify by missing the current goal alone. The previous goal does not affect PE."
            )
        elif self.persistent_error_policy == CURRENT_VIOLATION:
            self._line(
                "A persistent error is a submission violating at least one current movement restriction (counted once per submission)."
            )
            self._line(
                "Goal mismatch alone is excluded from PE. Success in the previous round does not affect PE."
            )
        else:
            self._line(
                "PE uses the saved scores. See the protocol for this run for its definition."
            )
        self._line(
            "The PER denominator includes correct submissions and excludes unused attempts and R1."
        )
        self._line(
            "Problem MPE averages observed R2–R5 PERs. Total MPE averages the eligible problem MPEs."
        )
        self._line(
            f"Mean error rate averages observed {error_rounds} rounds per problem, then problems overall or by condition."
        )
        self._line(f"Detailed logs: {self.directory}")
        self._close()
