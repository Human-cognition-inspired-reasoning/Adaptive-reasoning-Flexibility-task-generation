"""블록 투사도: 구조물 생성과 네 방향 × 세 색 정답.

격자: H[r][c] 높이, r=0 뒷줄 .. 2 앞줄, c=0 왼쪽 .. 2 오른쪽(앞에서 볼 때).
C[r][c][z]: 블록마다 색 (z=0 이 바닥층). 한 기둥 안에서도 색이 섞일 수 있다.
월드 좌표(render.py): x = c - 1, y = 1 - r (뒷줄이 +y), z 위. 앞은 -y 쪽.

투사도(답) 읽는 법: 그 방향에 선 사람이 보는 대로 왼쪽 → 오른쪽, 윗줄이 3층.
칸 값은 그 층 그 시선 줄에 놓인 블록 가운데 규칙 색 블록 수(0~3), 블록이 없으면 '.'.

제약은 하나뿐: 4 방향 × 3 색 = 12 가지 답이 모두 다르다.
옛 규칙 · 옛 목표로 쓴 답이 새 정답과 늘 달라지게 하는 최소 조건이다.
반투명 블록이라 가려진 블록도 보이므로 기둥 한 색 · 대칭 금지 · 색마다 기둥 2 개 ·
두 색 답 3 칸 이상 차이 · 윗면 가시성은 모두 뺐다(2026-11-10).

불투명 판(2026-11-12, 사용자 '블록을 불투명하게하고 안보이는 블록이 안생기는 제약'): generate(seed, min_vis=0.15) 는
렌더 카메라(앞-오른쪽 40°, 높이각 50°)에서 모든 블록의 카메라 쪽 면이 min_vis 이상 바로 보이는 구조물만 받는다
(occlusion.visibility). 답이 다 다른 구조물 가운데 약 7% 가 통과하고, 블록 수는 평균 13.8 → 9.8, 가운데 기둥은 대개
비거나 한 층이다. min_vis 를 안 주면 예전과 똑같은 구조물이 나온다.

    python3 bench/structure.py            # 예시 구조물 하나와 12 가지 답
    python3 bench/structure.py --stats    # 통과율과 답 값 분포
"""
import itertools, json, random, sys

N = 3
HMAX = 3
COLORS = ["red", "blue", "green"]
DIRS = ["front", "back", "left", "right"]
HEIGHTS = [0, 1, 1, 2, 2, 3]          # 칸 높이를 뽑는 주머니


def lines(d):
    """방향 d에서 본 사람의 왼쪽→오른쪽 순서로, 각 시선 줄에 놓인 칸 (r, c) 목록."""
    n = N
    if d == "front":
        return [[(r, c) for r in range(n)] for c in range(n)]
    if d == "back":
        return [[(r, c) for r in range(n)] for c in range(n - 1, -1, -1)]
    if d == "left":
        return [[(r, c) for c in range(n)] for r in range(n)]
    if d == "right":
        return [[(r, c) for c in range(n)] for r in range(n - 1, -1, -1)]
    raise ValueError(d)


def view(H, C, d, color):
    """투사도(윗층부터). 그 층 그 줄에 블록이 없으면 '.', 있으면 그 색 블록 수."""
    rows = []
    for z in range(HMAX, 0, -1):
        row = []
        for line in lines(d):
            blocks = [C[r][c][z - 1] for (r, c) in line if H[r][c] >= z]
            row.append(str(blocks.count(color)) if blocks else ".")
        rows.append(row)
    return rows


def all_views(H, C):
    return {d: {k: view(H, C, d, k) for k in COLORS} for d in DIRS}


# ---------- 새 규칙(2026-11-12 사용자 '찾기쉬운걸로' → '만들어봐'): 칸 숫자의 뜻이 규칙이다. 색 하나를 세는 규칙은 모델이
# 떠올리지 못해 1 라운드가 막혔으므로, 모델이 시키지 않아도 먼저 떠올리는 셋으로 바꾼다.
#   count  그 층 그 줄의 블록 수
#   near   보는 사람에게서 가장 가까운 블록이 몇째 칸인가(1 = 가장 가까운 칸)
#   colors 그 층 그 줄에 섞인 색 가짓수
RULES = ["count", "near", "colors"]


def nearest_first(d):
    """lines(d) 의 각 줄을 보는 사람에게서 가까운 칸부터. front 는 r=2(앞줄), back 은 r=0, left 는 c=0, right 는 c=2 가 가깝다."""
    return [list(reversed(l)) if d in ("front", "right") else list(l) for l in lines(d)]


def rule_view(H, C, d, rule, empty="."):
    """empty: 블록이 없는 칸에 적는 기호. 새 규칙에서는 블록이 있으면 늘 1 이상이라 0 을 써도 겹치지 않는다(2026-11-12 사용자 '빈칸0으로')."""
    rows = []
    for z in range(HMAX, 0, -1):
        row = []
        for line in nearest_first(d):
            b = [(i, C[r][c][z - 1]) for i, (r, c) in enumerate(line) if H[r][c] >= z]
            if not b:
                row.append(empty)
            elif rule == "count":
                row.append(str(len(b)))
            elif rule == "near":
                row.append(str(b[0][0] + 1))
            elif rule == "colors":
                row.append(str(len({x for _, x in b})))
            else:
                raise ValueError(rule)
        rows.append(row)
    return rows


def all_rule_views(H, C, empty="."):
    return {d: {r: rule_view(H, C, d, r, empty) for r in RULES} for d in DIRS}


def ok_rules(H, C):
    """4 방향 × 3 규칙 = 12 가지 답이 모두 다르다(직전 규칙 · 직전 방향으로 쓴 답이 늘 틀리게)."""
    V = all_rule_views(H, C)
    return len({json.dumps(V[d][r]) for d in DIRS for r in RULES}) == len(DIRS) * len(RULES)


def cell_diff(a, b):
    return sum(1 for ra, rb in zip(a, b) for x, y in zip(ra, rb) if x != y)


def ok(H, C):
    V = all_views(H, C)
    keys = {json.dumps(V[d][k]) for d in DIRS for k in COLORS}
    return len(keys) == len(DIRS) * len(COLORS)


def sample(rng):
    H = [[rng.choice(HEIGHTS) for _ in range(N)] for _ in range(N)]
    C = [[[rng.choice(COLORS) for _ in range(H[r][c])] for c in range(N)] for r in range(N)]
    return H, C


def generate(seed, max_tries=100000, min_vis=None):
    rng = random.Random(seed)
    cam = None
    for _ in range(max_tries):
        H, C = sample(rng)
        if not ok(H, C):
            continue
        if min_vis is not None:
            import occlusion                                  # 늦게 들여온다(occlusion 이 이 모듈을 들여옴)
            cam = occlusion.cam(40, 50) if cam is None else cam
            v = occlusion.visibility(H, cam)
            if not v or min(v.values()) < min_vis:
                continue
        return H, C
    raise RuntimeError("no structure")


def stats(n=2000):
    from collections import Counter
    rng = random.Random(7)
    passed = sum(ok(*sample(rng)) for _ in range(10 * n))
    print(f"무작위 구조물 통과율 {100 * passed / (10 * n):.1f}%")
    vals, diffs, absent = Counter(), Counter(), 0
    for seed in range(n):
        H, C = generate(seed)
        V = all_views(H, C)
        used = {x for row in C for col in row for x in col}
        absent += len(used) < len(COLORS)
        for d in DIRS:
            for k in COLORS:
                vals.update(x for row in V[d][k] for x in row if x != ".")
            for a, b in itertools.combinations(COLORS, 2):
                diffs[cell_diff(V[d][a], V[d][b])] += 1
    tv, td = sum(vals.values()), sum(diffs.values())
    print("답 값 분포 %", {k: round(100 * v / tv, 1) for k, v in sorted(vals.items())})
    print("같은 방향 두 색 답의 차이(칸) %", {k: round(100 * v / td, 1) for k, v in sorted(diffs.items())})
    print(f"한 색이 아예 없는 구조물 {100 * absent / n:.1f}%")


if __name__ == "__main__":
    if "--stats" in sys.argv:
        stats()
        sys.exit()
    H, C = generate(1112)
    print("H", H)
    print("C", C)
    V = all_views(H, C)
    for d in DIRS:
        print(d)
        for k in COLORS:
            print(" ", k, [" ".join(r) for r in V[d][k]])
