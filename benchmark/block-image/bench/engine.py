# -*- coding: utf-8 -*-
"""블록 투사도 판정.

사진 한 장 = 구조물 하나(structure.py 의 H · C). 정답 = 숨은 방향(목표) d 에서 본 투사도에, 숨은 규칙 k 로 칸 숫자를 적은 격자.
k 는 예전 판(scheme color)에서는 세는 색, 새 판(scheme kind, 2026-11-12)에서는 count · near · colors(structure.RULES).
격자는 3 줄 × 3 칸, 윗줄이 3 층, 칸 값은 '.'(그 층 그 줄에 블록 없음) 또는 '0'~'3'. 사진마다 views[d][k] 12 가지가 모두 다르다.

    evaluate(answer, views, d, k)      틀린 칸(줄 · 칸 번호), 맞으면 빈 목록
    matches(answer, views)             답과 똑같은 (방향, 색) 들
    perseverative(answer, views, old_k, new_k)   직전 색 규칙으로 쓴 답(어느 방향이든)이고 지금 색 규칙은 어긴 답
    chance(answer, views, old_k)       답의 '.' 자리는 그대로 두고 숫자 칸을 0~3 에서 고르게 뽑았을 때 지속적 오류가 될 확률
"""
SIDES = ("front", "back", "left", "right")
RULES = ("count", "near", "colors")         # 새 규칙(structure.RULES). 사진의 views 는 판 짜기(scheme)에 따라 색 셋 또는 규칙 셋을 열쇠로 갖는다


def rule_of(spec):
    """라운드의 숨은 규칙. 새 문제는 rule, 예전 문제(색 세기)는 color."""
    return spec.get("rule", spec.get("color"))


def old_rule_of(spec):
    return spec.get("oldRule", spec.get("oldColor"))
COLORS = ("red", "blue", "green")
CELLS = [(i, j) for i in range(3) for j in range(3)]


def grid_eq(a, b):
    return all(a[i][j] == b[i][j] for i, j in CELLS)


def evaluate(answer, views, d, k):
    want = views[d][k]
    return [(i, j) for i, j in CELLS if answer[i][j] != want[i][j]]


def matches(answer, views):
    return [(d, k) for d in SIDES for k in views[d] if grid_eq(answer, views[d][k])]


def perseverative(answer, views, old_k, new_k, d_now):
    """직전 색으로 셌고(어느 방향이든 그 색 격자와 똑같음), 지금 정답(d_now, new_k)이 아닌 답. 12 격자가 다 달라 직전 색 격자는 늘 틀린다."""
    if grid_eq(answer, views[d_now][new_k]):
        return False
    return any(k == old_k for _, k in matches(answer, views))


def best(answer, views):
    """답과 가장 많은 칸이 같은 (방향, 색)과 그 칸 수. 같은 수가 여럿이면 목록 전부."""
    score = {(d, k): sum(answer[i][j] == views[d][k][i][j] for i, j in CELLS) for d in SIDES for k in views[d]}
    top = max(score.values())
    return [dk for dk, s in score.items() if s == top], top


def pattern(g, empty="."):
    return tuple(g[i][j] == empty for i, j in CELLS)


def chance(answer, views, old_k, empty="."):
    """답의 빈칸 꼴은 그대로 두고 나머지 칸을 고르게 뽑았을 때 직전 규칙 격자(어느 방향이든)가 될 확률.
    빈칸 '.'(예전 판 · 새 판 첫 파일럿): 숫자 칸 0~3 의 넷. 빈칸 '0'(새 판, 2026-11-12~): 나머지 칸 1~3 의 셋."""
    pa = pattern(answer, empty)
    digits = sum(not x for x in pa)
    q = 0.25 if empty == "." else 1 / 3
    return sum(q ** digits for d in SIDES if pattern(views[d][old_k], empty) == pa)


def total_view(H, C, d):
    """색을 가리지 않고 센 격자(규칙을 모르고 '블록 수' 를 적은 답)."""
    import structure as S
    rows = []
    for z in range(S.HMAX, 0, -1):
        row = []
        for line in S.lines(d):
            n = sum(1 for (r, c) in line if H[r][c] >= z)
            row.append(str(n) if n else ".")
        rows.append(row)
    return rows


def show(answer, wrong=()):
    """피드백용: 낸 격자를 그대로, 틀린 칸 뒤에 *."""
    w = set(map(tuple, wrong))
    return "\n".join(" ".join(answer[i][j] + ("*" if (i, j) in w else " ") for j in range(3)).rstrip() for i in range(3))
