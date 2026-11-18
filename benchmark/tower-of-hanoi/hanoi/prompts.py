"""Single source of model prompts for the Hanoi experiment.

system contains API-level instructions; user is the first user message of each problem.
Prompts specify submission requirements without disclosing hidden rules or round information.
"""

from .policies import BINARY, FOUR_WAY, policy_name

TASK_PROMPT = {
    "system": """Solve the Tower of Hanoi puzzle.
Solve using only the task instructions and the supplied conversation.
Respond to the final user message with your next full move plan.
Do not use tools, files, code execution, external information, or other agents.
Output only the requested JSON object; do not include commentary or explanations.""",
    "user": """This is a Tower of Hanoi task.
Your objective is to solve this Tower of Hanoi task successfully.
The disks are numbered 1, 2, and 3. Disk 1 is the smallest.
The pegs are A, B, C, and D.
A complete plan must end with all three disks stacked on one peg, largest at the bottom and smallest at the top.
Do not end a plan while the disks are spread across multiple pegs.
The last item in each list is the top disk.
Move only one top disk at a time.
You cannot place a larger disk on a smaller disk.
AB means moving the top disk from A to B.
Submit a complete plan as a list of move commands.
A plan must contain between 1 and 30 move commands.
Empty move lists are not valid submissions.
Each submission starts from the initial state shown in the latest message.
Previous submissions and feedback are retained.
Feedback is limited to 'Correct' or 'Incorrect'.
Submit a single JSON object in the format {"moves":["AB", ...]}.
Do not add explanations.
After receiving feedback, continue with the next submission.""",
}


FEEDBACK_POLICIES = (BINARY, FOUR_WAY)
_FEEDBACK_VALUES = {
    BINARY: ("Correct", "Incorrect"),
    FOUR_WAY: (
        "Correct",
        "Incorrect",
        "Rule violation",
        "Correct, but rule violation",
    ),
}


def feedback_values(policy=BINARY):
    """Exact participant-facing labels, without any evaluator diagnostics."""
    policy = policy_name(policy)
    if not isinstance(policy, str) or policy not in FEEDBACK_POLICIES:
        raise ValueError("Unknown feedback policy. Choose binary or four_way.")
    return _FEEDBACK_VALUES[policy]


def task_prompt(policy=BINARY):
    """Change feedback disclosure only; keep the historical binary text intact."""
    policy = policy_name(policy)
    feedback_values(policy)
    prompt = dict(TASK_PROMPT)
    if policy == FOUR_WAY:
        prompt["user"] = prompt["user"].replace(
            "Feedback is limited to 'Correct' or 'Incorrect'.",
            "Feedback is one of 'Correct', 'Incorrect', 'Rule violation', or 'Correct, but rule violation'.\n"
            "'Correct, but rule violation' means the goal was reached, but the plan is not accepted because a movement restriction was violated.",
        )
    return prompt
