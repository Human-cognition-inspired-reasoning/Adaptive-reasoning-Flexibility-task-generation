# -*- coding: utf-8 -*-
"""판 사진 묶음과 문제를 엔진 · structure.py 와 따로 짠 계산으로 다시 확인한다.   python3 bench/verify.py [--deep]

사진마다
  · 높이 0~3, 블록 색이 빨강 · 파랑 · 초록이고 높이와 블록 수가 맞음, 사진(.jpg · .png)이 있음
  · .json 의 12 가지 격자가 H · C 에서 따로 계산한 격자와 같고, 12 가지가 모두 다름
  · 렌더 설정(불투명도 · 최소 가시성 · 모양 · 배치)이 묶음 안에서 모두 같음
  · --deep: 씨앗으로 구조물을 다시 뽑으면 같은 H · C 가 나오고, 모든 블록이 최소 가시성 이상 보임(occlusion.py)
문제마다
  · 다섯 라운드, 방향 · 색이 직전과 다르고 oldSide · oldColor 가 직전 라운드와 맞음(1 라운드는 없음)
  · 라운드마다 사진 T 장, 문제 안에서 겹치지 않고 모두 묶음에 있음
  · 사진마다 정답 격자에 1 이상인 칸이 있고, 색을 가리지 않고 센 격자가 정답이 아니며,
    직전 색으로 센 격자(네 방향 모두)가 정답이 아님
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import engine as E                                    # noqa: E402
import generate as G                                  # noqa: E402

SIDES = ("front", "back", "left", "right")
COLORS = ("red", "blue", "green")
OUTWARD = {"front": (0, -1), "back": (0, 1), "left": (-1, 0), "right": (1, 0)}   # 그 방향에 선 사람 쪽(월드 x, y)


def grid(H, C, side, color=None):
    """그 방향에 선 사람이 보는 격자. 칸 (r, c) 의 월드 위치 x = c - 1, y = 1 - r. 사람은 바깥 방향의 반대를 보고 서므로
    오른손 쪽 = (보는 방향 y, -보는 방향 x). 오른손 쪽 좌표가 작은 줄부터 왼쪽 → 오른쪽. color 가 None 이면 모든 색을 센다."""
    ox, oy = OUTWARD[side]
    fx, fy = -ox, -oy
    rx, ry = fy, -fx
    rows = []
    for z in (3, 2, 1):
        row = []
        for j in (-1, 0, 1):
            cells = [(r, c) for r in range(3) for c in range(3) if (c - 1) * rx + (1 - r) * ry == j]
            blocks = [C[r][c][z - 1] for r, c in cells if H[r][c] >= z]
            row.append("." if not blocks else str(sum(color is None or b == color for b in blocks)))
        rows.append(row)
    return rows


RULES = ("count", "near", "colors")


def rgrid(H, C, side, rule, empty="."):
    """새 판 규칙 격자를 따로 계산한다. 줄은 grid() 와 같이 오른손 쪽 좌표로 가르고, 가까움은 바깥 방향 쪽 좌표가 클수록
    가깝다(그 방향에 선 사람 쪽). near = 블록이 있는 칸 가운데 가장 가까운 칸이 줄에서 몇째인가(1 = 가장 가까운 칸)."""
    ox, oy = OUTWARD[side]
    rx, ry = -oy, ox
    rows = []
    for z in (3, 2, 1):
        row = []
        for j in (-1, 0, 1):
            cells = [(r, c) for r in range(3) for c in range(3) if (c - 1) * rx + (1 - r) * ry == j]
            cells.sort(key=lambda rc: -((rc[1] - 1) * ox + (1 - rc[0]) * oy))       # 가까운 칸부터
            occ = [(i, C[r][c][z - 1]) for i, (r, c) in enumerate(cells) if H[r][c] >= z]
            if not occ:
                row.append(empty)
            else:
                row.append(str({"count": len(occ), "near": occ[0][0] + 1, "colors": len({b for _, b in occ})}[rule]))
        rows.append(row)
    return rows


def keys_of(scheme):
    return RULES if scheme == "kind" else COLORS


def view(H, C, side, k, scheme, empty="."):
    return rgrid(H, C, side, k, empty) if scheme == "kind" else grid(H, C, side, k)


def check_photo(ph, deep=False, scheme="color", empty="."):
    bad, H, C = [], ph["H"], ph["C"]
    tag = ph["name"]
    if any(not 0 <= H[r][c] <= 3 or len(C[r][c]) != H[r][c] or any(b not in COLORS for b in C[r][c])
           for r in range(3) for c in range(3)):
        bad.append(f"{tag} 높이 · 색이 잘못됨")
        return bad
    if not (ROOT / ph["image"]).exists():
        bad.append(f"{tag} 사진이 없음")
    mine = {(d, k): view(H, C, d, k, scheme, empty) for d in SIDES for k in keys_of(scheme)}
    if any(mine[d, k] != ph["views"][d][k] for d in SIDES for k in keys_of(scheme)):
        bad.append(f"{tag} 격자가 따로 계산한 것과 다름")
    if len({json.dumps(g) for g in mine.values()}) != 12:
        bad.append(f"{tag} 12 가지 격자 가운데 같은 것이 있음")
    if deep:
        import occlusion as O
        import structure as S
        meta = ph["render"]
        if [list(x) for x in S.generate(ph["seed"], min_vis=meta["minVis"])] != [list(x) for x in (H, C)]:
            bad.append(f"{tag} 씨앗으로 다시 뽑은 구조물이 다름")
        if meta["minVis"] is not None:
            v = O.visibility(H, O.cam(meta["az"], meta["el"]))
            if not v or min(v.values()) < meta["minVis"]:
                bad.append(f"{tag} 덜 보이는 블록이 있음({min(v.values()):.2f})")
    return bad


def check(ep, pool, T=G.T, scheme="color", empty=".", distinct=False):
    bad, stats = [], Counter()
    prev, seen, last = None, set(), None
    if len(ep["rounds"]) != G.ROUNDS:
        bad.append(f"{ep['id']} 라운드가 {len(ep['rounds'])} 개")
    for spec in ep["rounds"]:
        tag = f"{ep['id']} R{spec['round']}"
        d, k = spec["side"], E.rule_of(spec)
        if d not in SIDES or k not in keys_of(scheme):
            bad.append(f"{tag} 방향 · 색이 잘못됨")
            continue
        if prev is None:
            if spec["oldSide"] is not None or E.old_rule_of(spec) is not None:
                bad.append(f"{tag} 1 라운드에 직전 기록이 있음")
        else:
            if spec["oldSide"] != prev["side"] or E.old_rule_of(spec) != E.rule_of(prev):
                bad.append(f"{tag} 직전 방향 · 색 기록이 어긋남")
            if d == prev["side"] or k == E.rule_of(prev):
                bad.append(f"{tag} 방향이나 색이 직전과 같음")
            stats["규칙 " + E.rule_of(prev) + "→" + k] += 1
        if len(spec["photos"]) != T:
            bad.append(f"{tag} 사진이 {len(spec['photos'])} 장")
        for name in spec["photos"]:
            ph = pool.get(name)
            if ph is None:
                bad.append(f"{tag} 묶음에 없는 사진 {name}")
                continue
            if name in seen:
                bad.append(f"{tag} 사진을 다시 씀 {name}")
            seen.add(name)
            want = view(ph["H"], ph["C"], d, k, scheme, empty)
            if not any(x not in ".0" for row in want for x in row):
                bad.append(f"{tag} {name} 정답 격자에 1 이상인 칸이 없음")
            if scheme == "color" and grid(ph["H"], ph["C"], d) == want:
                bad.append(f"{tag} {name} 색을 가리지 않고 센 격자가 정답")
            if prev is not None and any(view(ph["H"], ph["C"], s, E.rule_of(prev), scheme, empty) == want for s in SIDES):
                bad.append(f"{tag} {name} 직전 색 격자가 정답과 같음")
            stats["정답 숫자 칸"] += sum(x != empty for row in want for x in row)
            stats["정답 사진"] += 1
        if distinct:          # 라운드 안 정답 격자가 모두 다르고, 첫 장 정답이 직전 라운드 마지막 장 정답과 다름
            got = [view(pool[n]["H"], pool[n]["C"], d, k, scheme, empty) for n in spec["photos"] if n in pool]
            keys = [json.dumps(g) for g in got]
            if len(set(keys)) != len(keys):
                bad.append(f"{tag} 같은 정답 격자가 두 번 나옴")
            if last is not None and keys and keys[0] == last:
                bad.append(f"{tag} 첫 장 정답이 직전 라운드 마지막 장 정답과 같음")
            last = keys[-1] if keys else None
        stats["방향 " + d] += 1
        stats[("규칙 " if scheme == "kind" else "색 ") + k] += 1
        prev = spec
    return bad, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deep", action="store_true", help="씨앗 재현과 블록 가시성까지(느림)")
    ap.add_argument("--episodes", default="episodes.json", help="episodes 폴더 안의 파일(없으면 archive/episodes 에서 찾음, 예 episodes_color.json · episodes_k30.json)")
    a = ap.parse_args()
    src = ROOT / "episodes" / a.episodes
    if not src.exists():                                   # 예전 판 문제는 archive/episodes/ 에
        src = ROOT / "archive" / "episodes" / a.episodes
    data = json.loads(src.read_text())
    scheme, empty = data.get("scheme", "color"), data.get("empty", ".")
    pool = G.load_pool(data["pool"], scheme, empty)
    base = ROOT / data["pool"] if (ROOT / data["pool"]).exists() else ROOT / "archive" / data["pool"]
    for js in sorted((base / "photos").glob("p*.json")):
        if js.stem in pool:
            pool[js.stem].update(seed=json.loads(js.read_text())["seed"], render=json.loads(js.read_text())["render"])
    bad = []
    for ph in pool.values():
        bad += check_photo(ph, a.deep, scheme, empty)
    looks = Counter(json.dumps({k: ph["render"][k] for k in ("alpha", "minVis", "look", "layout", "az", "el")}) for ph in pool.values())
    if len(looks) != 1:
        bad.append(f"렌더 설정이 섞임 {dict(looks)}")
    stats = Counter()
    for ep in data["episodes"]:
        b, s = check(ep, pool, scheme=scheme, empty=empty, distinct=data.get("distinctAnswers", False))
        bad += b
        stats += s
    eps = data["episodes"]
    print(f"판 {scheme}, 빈칸 '{empty}', 쓸 수 있는 사진 {len(pool)} 장({data['pool']}), 문제 {len(eps)} 개, 라운드 {sum(len(e['rounds']) for e in eps)} 개: 어긋남 {len(bad)}")
    for line in bad[:30]:
        print("  " + line)
    print("  렌더 " + next(iter(looks)) if len(looks) == 1 else "")
    cells, n = stats.pop("정답 숫자 칸", 0), stats.pop("정답 사진", 0)
    print("  " + "  ".join(f"{k} {v}" for k, v in sorted(stats.items())))
    if n:
        print(f"  정답 격자의 숫자 칸 평균 {cells / n:.1f} / 9")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
