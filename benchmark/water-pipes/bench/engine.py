# -*- coding: utf-8 -*-
"""물길 5라운드 엔진 (v2). 기본형과 꽃조합을 한 과제로 합쳤다.

라운드마다 **셋이 모두 바뀐다.**
    조건  판 전체: 파이프·돌·꽃의 배치      board
    목표  물이 닿아야 할 꽃의 **색** 조합     goal
    규칙  같은 타일을 몇 번 연속 눌러야 하나  rule

목표를 타일 번호가 아니라 **색**으로 거는 것이 v1 과의 핵심 차이다. 번호로 걸면
판이 바뀔 때 직전 목표가 정의되지 않아 지속적 오류를 판정할 수 없다. 색으로 걸면
판을 통째로 새로 깔아도 `{red, blue}` 가 그대로 옮겨간다. 과제 문서의 미로가
'빨간 지점·파란 지점' 으로 목표를 거는 것과 같은 구조다.

목표에 없는 꽃은 **마른 채로** 남아야 한다. 더 적시는 것도 틀린 답이다.
한 회전 배치는 꽃 색의 정확히 한 부분집합만 적시므로, 목표가 다르면 풀이 집합이
서로소다: 목표 쪽 보장은 공짜로 따라온다.

규칙은 서로소인 세 띠(1 · 2-3 · 4+)다. 한 띠에서 합법인 순서는 다른 두 띠를
반드시 어긴다.
"""
from collections import deque
from functools import lru_cache

U, R, D, L = 1, 2, 4, 8
DIRS = ((U, 4, -1, 0), (R, 8, 0, 1), (D, 1, 1, 0), (L, 2, 0, -1))
PIPE, SOURCE, FLOWER, STONE = "pipe", "source", "flower", "stone"
COLORS = ("red", "yellow", "blue")


def rotate(mask, times=1):
    for _ in range(times % 4):
        mask = ((mask << 1) | (mask >> 3)) & 15
    return mask


def apply(board, clicks):
    """클릭을 순서대로 적용한 타일 마스크. 고정 타일은 회전하지 않는다."""
    masks = [t["mask"] for t in board["tiles"]]
    for i in clicks:
        if board["tiles"][i]["kind"] == PIPE:
            masks[i] = rotate(masks[i])
    return masks


def watered(board, masks=None):
    """물이 닿은 칸. 서로 마주 보는 연결구가 모두 있어야 흐른다."""
    n = board["size"]
    masks = masks or [t["mask"] for t in board["tiles"]]
    start = next(i for i, t in enumerate(board["tiles"]) if t["kind"] == SOURCE)
    seen, q = {start}, deque([start])
    while q:
        i = q.popleft(); r, c = divmod(i, n)
        for bit, opp, dr, dc in DIRS:
            if not masks[i] & bit:
                continue
            nr, nc = r + dr, c + dc
            if not (0 <= nr < n and 0 <= nc < n):
                continue
            j = nr * n + nc
            if masks[j] & opp and j not in seen:
                seen.add(j); q.append(j)
    return seen


def reaches(board, masks=None):
    """이 배치가 적시는 꽃의 **색** 집합. 타일 번호가 아니다."""
    seen = watered(board, masks)
    return frozenset(t["color"] for i, t in enumerate(board["tiles"])
                     if t["kind"] == FLOWER and i in seen)


def goal_name(goal):
    return "+".join(c for c in COLORS if c in goal) or "none"


def rule_name(rule):
    lo, hi = rule["lo"], rule.get("hi")
    return str(lo) if hi == lo else f"{lo}+" if hi is None else f"{lo}-{hi}"


def rules_disjoint(a, b):
    """두 띠가 겹치지 않으면, 한쪽에서 합법인 순서는 다른 쪽을 반드시 어긴다."""
    hi_a = a.get("hi") or 10 ** 9
    hi_b = b.get("hi") or 10 ** 9
    return a["lo"] > hi_b or b["lo"] > hi_a


def violations(board, clicks, rule):
    """돌릴 수 없는 칸을 눌렀거나 연속 클릭 제약을 어긴 곳."""
    lo, hi = rule["lo"], rule.get("hi")
    fixed, bad = [], []
    last, streak = -1, 0
    for i in clicks:
        if board["tiles"][i]["kind"] != PIPE:
            fixed.append(i); continue
        if last != -1 and i != last and streak < lo:
            bad.append(last)
        streak = streak + 1 if i == last else 1
        if hi is not None and streak > hi:
            bad.append(i)
        last = i
    if last != -1 and streak < lo:
        bad.append(last)
    return fixed, bad


_ALLOW = {}


def _key(board):
    return (board["size"], tuple((t["kind"], t["mask"], t.get("color"))
                                 for t in board["tiles"]))


def wrong_tiles(board, goal, masks=None):
    """지금 방향으로는 어떤 정답에도 들어갈 수 없는 파이프. v1 에서 그대로 옮겼다.

    판마다 정답 배치가 여럿이라, 기준 배치 하나를 골라 비교하면 '다른 답이었으면
    맞았을 타일' 까지 틀렸다고 하게 된다. 그래서 정답 배치를 모두 훑어 그 타일에
    허용되는 방향을 모으고, 지금 방향이 거기 없을 때만 틀렸다고 한다.
    회전 수가 아니라 방향(mask)으로 비교한다: 직선 파이프는 180° 돌려도 같다.

    배치 전수조사가 4^파이프라 판마다 한 번만 하고 재사용한다."""
    tiles = board["tiles"]
    cur = masks if masks is not None else [t["mask"] for t in tiles]
    k = (_key(board), frozenset(goal))
    allow = _ALLOW.get(k)
    if allow is None:
        allow = {}
        for turns in assignments(board).get(frozenset(goal), []):
            for i, r in turns.items():
                allow.setdefault(i, set()).add(rotate(tiles[i]["mask"], r))
        _ALLOW[k] = allow
    return [i for i, ok in sorted(allow.items()) if cur[i] not in ok]


def evaluate(board, clicks, goal, rule):
    """제출 하나를 판정한다. status: fixed_tile · rule_violation · wrong_set · solved

    `tiles` 는 **틀린 곳 전부**다: 규칙을 어긴 타일, 돌릴 수 없는데 누른 타일,
    그리고 이 순서를 적용했을 때 어떤 정답에도 못 들어가는 파이프를 합친다.
    v1 과 같은 피드백을 쓰려고 되살렸다. status 는 로그 분석용으로만 남긴다."""
    fixed, bad = violations(board, clicks, rule)
    masks = apply(board, clicks)
    off = sorted(set(fixed) | set(bad) | set(wrong_tiles(board, goal, masks)))
    if fixed:
        return dict(status="fixed_tile", tiles=off, ok=False)
    if bad:
        return dict(status="rule_violation", tiles=off, ok=False)
    hit = reaches(board, masks)
    if hit != frozenset(goal):
        return dict(status="wrong_set", watered=sorted(hit), tiles=off, ok=False)
    return dict(status="solved", clicks=len(clicks), ok=True)


def maybe_solvable(board):
    """회전을 다 해봐도 가망이 없는 판을 싸게 걸러낸다.

    파이프를 네 방향 다 열린 것으로 봐도 꽃에 닿지 못하면 어떤 회전으로도 못 닿는다.
    assignments 가 4^파이프를 전수조사하므로 이 검사를 앞에 끼워야 생성이 돈다."""
    n = board["size"]; tiles = board["tiles"]

    def open_to(i, bit):
        k = tiles[i]["kind"]
        if k == STONE:
            return False
        return True if k == PIPE else bool(tiles[i]["mask"] & bit)

    src = next((i for i, t in enumerate(tiles) if t["kind"] == SOURCE), None)
    if src is None:
        return False
    seen, stack = {src}, [src]
    while stack:
        i = stack.pop(); r, c = divmod(i, n)
        for bit, back, dr, dc in DIRS:
            r2, c2 = r + dr, c + dc
            if not (0 <= r2 < n and 0 <= c2 < n):
                continue
            j = r2 * n + c2
            if j in seen or not open_to(i, bit) or not open_to(j, back):
                continue
            seen.add(j); stack.append(j)
    return all(i in seen for i, t in enumerate(tiles) if t["kind"] == FLOWER)


def assignments(board):
    """회전 배치마다 어떤 색을 적시는지. {색집합: [배치, …]}

    타일마다 0~3 회를 다 해보는 완전 탐색. 3×3 에 파이프가 최대 7 개라 4^7 뿐이다."""
    pipes = [i for i, t in enumerate(board["tiles"]) if t["kind"] == PIPE]
    base = [t["mask"] for t in board["tiles"]]
    out = {}
    for code in range(4 ** len(pipes)):
        masks = list(base); k = code; turns = {}
        for i in pipes:
            r = k & 3; k >>= 2
            turns[i] = r; masks[i] = rotate(base[i], r)
        out.setdefault(reaches(board, masks), []).append(turns)
    return out


@lru_cache(maxsize=None)
def _runs_raw(turns, lo, hi):
    """회전 t 번이 필요한 타일의 묶음 길이들. 합이 t (mod 4) 이고 각 묶음이 띠 안이다."""
    if turns % 4 == 0:
        return ()
    for j in range(1, 40):
        top = j * hi if hi is not None else j * lo + 4 * j + 3
        for total in range(j * lo, top + 1):
            if total % 4 != turns % 4:
                continue
            q, r = divmod(total, j)
            return tuple([q + 1] * r + [q] * (j - r))
    return None


def runs_for(turns, rule):
    out = _runs_raw(turns % 4, rule["lo"], rule.get("hi"))
    return None if out is None else list(out)


def arrange(plans):
    """묶음을 같은 타일이 연달아 오지 않게 늘어놓는다."""
    left = {i: sorted(r) for i, r in plans.items() if r}
    out = []
    while any(left.values()):
        pick = max((i for i in left if left[i] and (not out or i != out[-1])),
                   key=lambda i: (len(left[i]), -i), default=None)
        if pick is None:
            return None
        out += [pick] * left[pick].pop()
    return out


def sequence_for(board, turns, rule):
    """회전 배치를 그 규칙을 지키는 클릭 순서로 바꾼다. 못 만들면 None."""
    plans = {}
    for i, t in turns.items():
        r = runs_for(t, rule)
        if r is None:
            return None
        plans[i] = r
    seq = arrange(plans)
    if seq is not None:
        return seq
    filler = runs_for(0, rule) or _runs_raw(4, rule["lo"], rule.get("hi"))
    if filler is None:
        return None
    for i in sorted(turns):
        if plans.get(i):
            continue
        trial = dict(plans); trial[i] = list(filler)
        seq = arrange(trial)
        if seq is not None:
            return seq
    return None


def solve(board, goal, rule, cap=200):
    """그 목표·규칙을 만족하는 가장 짧은 클릭 순서. 없으면 None."""
    best = None
    for turns in assignments(board).get(frozenset(goal), [])[:cap]:
        seq = sequence_for(board, turns, rule)
        if seq is None:
            continue
        if evaluate(board, seq, goal, rule)["ok"] and (best is None or len(seq) < len(best)):
            best = seq
    return best


def text(board):
    """사람과 모델이 함께 읽는 판 표기. 꽃은 색으로 적는다: [Fr] 빨강 · [Fy] 노랑 · [Fb] 파랑.

    목표가 꽃 색 조합이고 판이 라운드마다 바뀌므로, 색이 없으면 목표가 라운드를
    건너 옮겨 갈 수 없다(물길-목표조건 과 같은 이유). 번호는 왼쪽 위에서 행 순서로 1 부터."""
    n = board["size"]
    cells = []
    for i, t in enumerate(board["tiles"]):
        ports = "".join(ch for bit, ch in ((U, "U"), (R, "R"), (D, "D"), (L, "L"))
                        if t["mask"] & bit)
        if t["kind"] == FLOWER:
            name = "F" + t["color"][0]
        else:
            name = {PIPE: "P", SOURCE: "S", STONE: "X"}[t["kind"]]
        body = f"[{name}:{ports}]" if ports else f"[{name}]"
        cells.append(f"{i + 1} {body}")
    wide = max(len(c) for c in cells)
    return "\n".join("  ".join(c.ljust(wide) for c in cells[r * n:(r + 1) * n]).rstrip()
                     for r in range(n))


def label(i):
    return str(i + 1)
