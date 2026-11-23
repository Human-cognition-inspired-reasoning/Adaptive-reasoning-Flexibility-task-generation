"""v29 maze design (research lead's specification 2026-10-20). 9x9 grid; a perfect maze by randomized depth-first search,
then 4% of the remaining interior walls removed (loops), then every wall component that does not touch the border removed
(walls stay connected to the outer wall). Four coloured cells (green, red, blue, yellow). Each session one of them is the
start (told to the model); in every round the goal is one of the other three (not told) and a hidden rule applies:

  NB  never enter a cell already visited
  AV  pass through no coloured cell other than the start and the goal
  VIA pass through at least one of the two other coloured cells (not the start, not the goal) on the way

A path is valid for (goal, rule) iff it stays in the maze, crosses no wall, ends on the goal, and satisfies the rule.
Answer of a (goal, rule) combination = its shortest valid walk (length by BFS).

Acceptance (every start x every goal x every rule, 4 x 3 x 3 = 36 combinations): a valid path exists (NB and VIA always
do in a connected maze; AV needs a route avoiding the other two coloured cells), and at least one (start, goal) pair where
the NB and AV answers differ (the shortest route passes another coloured cell, so AV needs a detour).
"""
from __future__ import annotations

import random
from collections import deque

from .core import MOVES, Maze, _key, generate

COLOURS = ("green", "red", "blue", "yellow")
RULES = ("NB", "AV", "VIA")
RULE_TEXT = {"NB": "the path never enters a cell it has already visited",
             "AV": "the path passes through no coloured cell other than the start and the goal",
             "VIA": "on the way, the path passes through at least one coloured cell that is neither the start nor the goal"}


def remove_wall_islands(n, walls):
    """Walls as segments between lattice points; drop every connected group of segments that touches no border point."""
    def seg(w):
        (r1, c1), (r2, c2) = w
        if r1 == r2:               # cells side by side: vertical segment at column max(c) from row r to r+1
            c = max(c1, c2)
            return (r1, c), (r1 + 1, c)
        r = max(r1, r2)            # cells one above the other: horizontal segment at row r from column c to c+1
        return (r, c1), (r, c1 + 1)
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    segs = {w: seg(w) for w in walls}
    for a, b in segs.values():
        parent[find(a)] = find(b)
    border = lambda p: p[0] in (0, n) or p[1] in (0, n)  # noqa: E731
    roots_on_border = {find(p) for a, b in segs.values() for p in (a, b) if border(p)}
    keep = {w for w, (a, b) in segs.items() if find(a) in roots_on_border}
    return keep, len(walls) - len(keep)


def make_loop_maze(n, rng: random.Random, remove_frac=0.04):
    base = generate(n, 0.0, rng)
    walls = sorted(base.walls)
    k = round(remove_frac * len(walls))
    for w in rng.sample(walls, k):
        base.walls.discard(w)
    kept, islands = remove_wall_islands(n, base.walls)
    m = Maze(n, kept)
    cells = [(r, c) for r in range(n) for c in range(n)]
    pts = rng.sample(cells, 4)
    m.points = dict(zip(COLOURS, pts))
    m.removed = {"random": k, "island_walls": islands}
    return m


def open_2x2(n, walls):
    """Number of 2x2 blocks of cells with none of their four inner walls (an open room)."""
    k = 0
    for r in range(n - 1):
        for c in range(n - 1):
            inner = (_key((r, c), (r, c + 1)), _key((r + 1, c), (r + 1, c + 1)), _key((r, c), (r + 1, c)), _key((r, c + 1), (r + 1, c + 1)))
            k += not any(w in walls for w in inner)
    return k


def make_loop_maze_v30(n, rng: random.Random, remove_frac=0.04, min_dist=5):
    """v30, option A (decision 2026-10-21): perfect maze by randomized depth-first search; remove remove_frac of the
    remaining interior walls in random order, skipping any wall whose removal would open a 2x2 room; wall pillars not
    touching the border are allowed (no island-removal step; loops, connected walls and no 2x2 room cannot hold together,
    results/v30_maze/DESIGN.md). Four coloured cells on passages (>= 2 open neighbours) at pairwise maze distance >= min_dist.
    Returns None if the walls cannot be removed or the cells cannot be placed."""
    base = generate(n, 0.0, rng)
    walls = set(base.walls)
    k = round(remove_frac * len(walls))
    removed = 0
    for w in rng.sample(sorted(walls), len(walls)):
        if removed == k:
            break
        if open_2x2(n, walls - {w}) == 0:
            walls.discard(w)
            removed += 1
    if removed < k:
        return None
    m = Maze(n, walls)
    pts = place_points(m, rng, min_dist)
    if pts is None:
        return None
    m.points = pts
    m.removed = {"random": removed, "pillar_walls": remove_wall_islands(n, walls)[1]}
    return m


def place_points(m, rng: random.Random, min_dist, tries=500):
    """Four coloured cells on passages: cells with at least two open neighbours (no dead ends), pairwise maze distance
    >= min_dist. None if no such placement is found in `tries` random draws."""
    elig = [(r, c) for r in range(m.n) for c in range(m.n) if len(list(m.nbrs((r, c)))) >= 2]
    for _ in range(tries):
        pts = rng.sample(elig, 4)
        d = [bfs(m, p) for p in pts]
        if all(d[i][pts[j]] >= min_dist for i in range(4) for j in range(i + 1, 4)):
            return dict(zip(COLOURS, pts))
    return None


def bfs(m, src, blocked=()):
    blocked = set(blocked)
    d, q = {src: 0}, deque([src])
    while q:
        c = q.popleft()
        for _, x in m.nbrs(c):
            if x not in d and x not in blocked:
                d[x] = d[c] + 1
                q.append(x)
    return d


def others(m, s, g):
    return [m.points[c] for c in COLOURS if c not in (s, g)]


def answer_len(m, s, g, rule):
    """Length of the shortest valid walk from colour s to colour g under rule, or None."""
    S, G = m.points[s], m.points[g]
    if rule == "NB":
        return bfs(m, S).get(G)
    if rule == "AV":
        return bfs(m, S, others(m, s, g)).get(G)
    best = None
    for x in others(m, s, g):
        a, b = bfs(m, S).get(x), bfs(m, x).get(G)
        if a is not None and b is not None and (best is None or a + b < best):
            best = a + b
    return best


def check(m):
    lens, differ = {}, {}
    for s in COLOURS:
        for g in COLOURS:
            if g == s:
                continue
            for r in RULES:
                lens[(s, g, r)] = answer_len(m, s, g, r)
            nb, av = lens[(s, g, "NB")], lens[(s, g, "AV")]
            differ[(s, g)] = av is not None and nb is not None and av > nb
    ok = all(v is not None for v in lens.values()) and any(differ.values())
    return {"ok": ok, "lens": lens, "nb_av_differ": differ}


def path_cells(m, start, moves):
    """Visited cells, or (None, fail) on leaving the maze / crossing a wall."""
    c, out = start, [start]
    for mv in moves:
        dr, dc = MOVES[mv]
        x = (c[0] + dr, c[1] + dc)
        if not m.inside(x):
            return None, "out_of_bounds"
        if _key(c, x) in m.walls:
            return None, "wall"
        c = x
        out.append(c)
    return out, None


def valid(m, s, g, rule, cells):
    if cells is None or cells[-1] != m.points[g]:
        return False
    oth = set(others(m, s, g))
    if rule == "NB":
        return len(set(cells)) == len(cells)
    if rule == "AV":
        return not (oth & set(cells))
    return bool(oth & set(cells[:-1]))


def to_dict(m):
    return {"n": m.n, "walls": sorted([list(map(list, w)) for w in m.walls]), "points": {k: list(v) for k, v in m.points.items()}}


def from_dict(d):
    m = Maze(d["n"], [tuple(map(tuple, w)) for w in d["walls"]])
    m.points = {k: tuple(v) for k, v in d["points"].items()}
    return m
