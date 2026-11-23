"""Prompts for the v29 maze design: list presentation (every cell with the directions in which it can be left), colours
spelled out with row and column, the start colour told, the goal and the rule hidden; candidates listed (the goal is one
of the other three coloured cells; the rule is one of three); feedback = "Correct." or "Incorrect." only (2-B replaced by
success/failure, specification 2026-10-20); chat history with the last 10 attempts; switches never announced.
v31 design check (goal_told=True): the goal colour is told with every attempt ("Attempt n. The goal is the X cell."); the
rule stays hidden. With goal_told=False the prompts are those of v30, unchanged."""
from __future__ import annotations

from .loop import COLOURS, RULE_TEXT

HISTORY_LIMIT = 10
DIRS = {"U": "up", "D": "down", "L": "left", "R": "right"}
FORMAT = "End your reply with one line in exactly this format:\nMOVES: <moves separated by spaces>"


def intro(m, start, rule_order, feedback=True, goal_told=False):
    pts = "; ".join(f"the {c} cell is at row {m.points[c][0]}, column {m.points[c][1]}" for c in COLOURS)
    goals = [c for c in COLOURS if c != start]
    lines = ["You are moving through a maze on a grid. You can move to a neighbouring cell only when no wall is between the two cells.",
             f"There are four coloured cells. You start at the {start} cell. The goal is one of the other three coloured cells "
             f"({', '.join(goals[:-1])} or {goals[-1]}); " + ("you are told which one with each attempt, and it can change." if goal_told
                                                               else "you are not told which one."),
             "The path must also follow a hidden rule, which is one of the following: " + "; ".join(RULE_TEXT[r] for r in rule_order) +
             ". You are not told which one.",
             "Give a path from the start as a list of moves: U (up), D (down), L (left), R (right).",
             ("After each answer you will be told only whether it was correct. Then give a path again." if feedback
              else "After each answer, give a path again."), FORMAT, "",
             f"The maze has {m.n} rows and {m.n} columns. Rows are numbered from 0 at the top and columns from 0 at the left; {pts}.",
             "For every cell, the directions in which you can leave it (no wall on that side):"]
    rows = [f"row {r}, column {c}: " + ", ".join(DIRS[mv] for mv, _ in m.nbrs((r, c))) for r in range(m.n) for c in range(m.n)]
    return "\n".join(lines + rows)


def build_messages(m, start, attempts, rule_order, feedback=True, history_limit=HISTORY_LIMIT, goal=None):
    """goal: the current goal colour when it is told (v31); past attempts carry their own "goal"."""
    told = goal is not None
    tail = (lambda g: f" The goal is the {g} cell.") if told else (lambda g: "")
    n = len(attempts)
    shown = attempts[-history_limit:]
    first = n - len(shown) + 1
    head = intro(m, start, rule_order, feedback, told) + (f"\n\nAttempts 1-{first - 1} are not shown." if first > 1 else "")
    msgs, pending = [], head
    for i, a in enumerate(shown):
        msgs.append({"role": "user", "content": (pending + "\n\n" if pending else "") + f"Attempt {first + i}." + tail(a.get("goal"))})
        msgs.append({"role": "assistant", "content": a["output"]})
        pending = ("Correct." if a["ok"] else "Incorrect.") if feedback else ""
    msgs.append({"role": "user", "content": (pending + "\n\n" if pending else "") + f"Attempt {n + 1}." + tail(goal)})
    return msgs
