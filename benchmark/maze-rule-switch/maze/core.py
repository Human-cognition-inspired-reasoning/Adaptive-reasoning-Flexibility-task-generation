"""Maze grid, randomized depth-first maze generation, seeds and answer parsing.

Maze: n x n cells, rows top to bottom, columns left to right, walls between neighbouring cells. `generate` draws a perfect
maze by randomized depth-first search (`braid` = 0 keeps it perfect). `parse_moves` reads the last line containing
'MOVES:' of a model response.
"""
from __future__ import annotations

import hashlib
import random
import re

MOVES = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}
WORDS = {"UP": "U", "DOWN": "D", "LEFT": "L", "RIGHT": "R", "U": "U", "D": "D", "L": "L", "R": "R"}


def seed_of(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _key(a, b):
    return tuple(sorted((tuple(a), tuple(b))))


class Maze:
    def __init__(self, n, walls):
        self.n = n
        self.walls = {_key(a, b) for a, b in walls}

    def inside(self, c):
        return 0 <= c[0] < self.n and 0 <= c[1] < self.n

    def open(self, a, b):
        return self.inside(b) and _key(a, b) not in self.walls

    def nbrs(self, c):
        for mv, (dr, dc) in MOVES.items():
            x = (c[0] + dr, c[1] + dc)
            if self.open(c, x):
                yield mv, x


def generate(n, braid, rng: random.Random) -> Maze:
    walls = set()
    for r in range(n):
        for c in range(n):
            if r + 1 < n:
                walls.add(_key((r, c), (r + 1, c)))
            if c + 1 < n:
                walls.add(_key((r, c), (r, c + 1)))
    seen, stack = {(0, 0)}, [(0, 0)]
    while stack:
        cur = stack[-1]
        nb = [(cur[0] + dr, cur[1] + dc) for dr, dc in MOVES.values()]
        nb = [x for x in nb if 0 <= x[0] < n and 0 <= x[1] < n and x not in seen]
        if not nb:
            stack.pop()
            continue
        nxt = rng.choice(nb)
        walls.discard(_key(cur, nxt))
        seen.add(nxt)
        stack.append(nxt)
    for w in sorted(walls):
        if rng.random() < braid:
            walls.discard(w)
    return Maze(n, walls)


def parse_moves(text: str):
    """Last line containing 'MOVES:'; tokens U/D/L/R (or up/down/left/right), separated by spaces or commas.
    Returns (moves, error)."""
    if not text:
        return None, "empty output"
    hits = [l for l in text.splitlines() if "MOVES:" in l.upper().replace("*", "").replace("`", "")]
    if not hits:
        return None, "no MOVES line"
    body = re.split(r"MOVES:", hits[-1].replace("*", "").replace("`", ""), flags=re.I)[-1]
    toks = re.findall(r"[A-Za-z]+", body)
    if not toks:
        return None, "empty move list"
    out = []
    for t in toks:
        u = t.upper()
        if u in WORDS:
            out.append(WORDS[u])
        elif set(u) <= set("UDLR"):      # unspaced run such as RRDD
            out.extend(u)
        else:
            return None, f"unknown move {t!r}"
    return out, None
