# -*- coding: utf-8 -*-
"""생성된 문제가 보장을 지키는지 본다.   python3 bench/verify.py

생성기의 판단을 다시 쓰지 않고 engine 으로 처음부터 센다. 보장 목록은 generate.py 머리말."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine as E                                    # noqa: E402
from generate import BOARDS, ROUNDS                   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
eps = json.loads((ROOT / "episodes/episodes.json").read_text())
ALL = frozenset(E.COLORS)
BANDS = (dict(lo=1, hi=1), dict(lo=2, hi=2), dict(lo=3, hi=None))
bad, chk = [], Counter()
n_boards = n_sw = 0


def natural_names(board, goal):
    """가장 적게 돌리는 배치마다, 타일마다 필요한 만큼 이어 누른 답이 지키는 띠의 이름(없으면 None)."""
    turns = E.assignments(board).get(frozenset(goal), [])
    if not turns:
        return set()
    best = min(sum(t.values()) for t in turns)
    names = set()
    for t in turns:
        if sum(t.values()) != best:
            continue
        q = [i for i in sorted(t) for _ in range(t[i])]
        ok = [E.rule_name(r) for r in BANDS if q and not any(E.violations(board, q, r))]
        names.add(ok[0] if ok else None)
    return names


def natural_band(board, goal):
    """그 이름이 하나로 정해지면 그 이름, 아니면 None."""
    names = natural_names(board, goal)
    return names.pop() if len(names) == 1 else None


def layout(board):
    """판 구성: 칸 종류 · 꽃 색 · 고정 칸 방향 · 파이프 모양(돌려서 같으면 같은 모양). 파이프의 처음 방향은 뺀다."""
    return tuple((t["kind"], t.get("color"),
                  min(E.rotate(t["mask"], r) for r in range(4)) if t["kind"] == E.PIPE else t["mask"])
                 for t in board["tiles"])


def turns_set(board, goal):
    """그 목표를 만드는 회전 배치(9 칸의 0~3) 집합. 정답 순서는 타일마다 누른 횟수(4 로 나눈 나머지)가 이 안에 있어야 한다."""
    n = board["size"] ** 2
    return {tuple(t.get(i, 0) for i in range(n)) for t in E.assignments(board).get(frozenset(goal), [])}


for e in eps:
    if len(e["rounds"]) != ROUNDS:
        bad.append(f"{e['id']} 라운드 수")
    seen, last, last_lay = set(), None, None
    for i, r in enumerate(e["rounds"]):
        goal, rule = frozenset(r["goal"]), r["rule"]
        tag = f"{e['id']} R{r['round']}"
        p = e["rounds"][i - 1] if i else None

        def ok(name, cond, why):
            if cond:
                chk[name] += 1
            else:
                bad.append(f"{tag} {why}")

        ok("판 수", len(r["boards"]) == BOARDS, f"판이 {len(r['boards'])} 장")
        lay = {layout(b["board"]) for b in r["boards"]}
        ok("라운드 안의 판은 한 구성(처음 방향만 다름)", len(lay) == 1, "라운드 안에서 판 구성이 바뀜")
        ok("라운드마다 새 구성", lay != last_lay, "직전 라운드와 판 구성이 같음")
        last_lay = lay
        if not i:
            ok("1R 목표는 모든 꽃", goal == ALL, "1 라운드 목표가 모든 꽃이 아님")
        else:
            ok("띠 변경", rule != p["rule"], "띠가 직전과 같음")
            ok("목표 변경", goal != frozenset(p["goal"]), "목표가 직전과 같음")
        for j, b in enumerate(r["boards"]):
            n_boards += 1
            board = b["board"]
            key = json.dumps(board, sort_keys=True)
            ok("한 문제 안에서 처음 쓰는 판", key not in seen, f"판{j + 1} 이 이 문제에서 이미 나옴")
            seen.add(key)
            ok("정답이 통함", E.evaluate(board, b["shortest"], goal, rule)["ok"], f"판{j + 1} 정답이 안 통함")
            ok("아무것도 안 누르면 틀림", not E.evaluate(board, [], goal, rule)["ok"], f"판{j + 1} 이 처음부터 정답")
            T = turns_set(board, goal)
            if last is not None and last[1] == (goal, json.dumps(rule)):
                ok("직전 판 정답은 이 판에서 틀림", not (T & last[0]), f"판{j + 1} 과 직전 판의 회전 배치가 겹침")
            last = (T, (goal, json.dumps(rule)))
            if not i:
                continue
            n_sw += 1
            ok("직전 띠로 지금 목표를 풂(P)", E.evaluate(board, b["ruleStick"], goal, p["rule"])["ok"]
               and not E.evaluate(board, b["ruleStick"], goal, rule)["ok"], f"판{j + 1} 직전 띠 답이 어긋남")
            ok("직전 목표가 이 판에서 가능", frozenset(p["goal"]) in E.assignments(board), f"판{j + 1} 직전 목표 불가능")
            ok("직전 목표를 습관대로 → 직전 띠", natural_band(board, p["goal"]) == E.rule_name(p["rule"]),
               f"판{j + 1} 직전 목표의 자연스러운 답이 직전 띠를 안 지킴")

print(f"문제 {len(eps)} · 판 {n_boards} (전환 라운드 판 {n_sw}) : 어긋남 {len(bad)}")
for k, v in chk.items():
    print(f"  {k:34} {v}")
print("  문제 없음" if not bad else "  " + "\n  ".join(bad[:20]))
sys.exit(1 if bad else 0)
