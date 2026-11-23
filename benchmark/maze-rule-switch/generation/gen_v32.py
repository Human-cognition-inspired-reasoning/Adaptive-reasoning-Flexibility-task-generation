"""v32 mazes and sessions (2026-10-24): the v30 design with the maze size raised from 9x9 to 12x12; nothing else changes.
Mazes: perfect maze by randomized depth-first search, 4% of the remaining interior walls removed without opening a 2x2
room (wall pillars allowed), four coloured cells on passages (no dead ends) at pairwise maze distance >= 5; accepted if
all 36 (start, goal, rule) combinations have a valid path and at least one (start, goal) pair has different NB and AV
answers. Seeds sha256('v32|maze|<i>').

Sessions: 40, start colour balanced (10 per colour, seeded shuffle); for each session the next pool maze in which that start
has at least one goal with different NB and AV answers. 8 rounds: round 1 goal and rule uniform; then both change every
round. Random order (first seeded candidate, no prioritization, as in v30). Baseline sessions: the first session of each
start colour (4). The same pool statistics are recomputed for the v30 9x9 pool (data/v30/mazes.json) for comparison.

  python generation/gen_v32.py      # after gen_v30.py; writes generated/v32/mazes.json, sessions.json, DESIGN.md
"""
import json
import random
from collections import Counter
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # task folder (benchmark/maze-rule-switch)
GEN = ROOT / "generated"                             # generator outputs, not tracked by git
sys.path.insert(0, str(ROOT))

import numpy as np

from gen_v30 import random_order
from maze.core import seed_of
from maze.loop import COLOURS, RULES, check, from_dict, make_loop_maze_v30, to_dict

N, POOL, SESSIONS = 12, 300, 40
OUT = REP = GEN / "v32"


def stats(pool):
    lens = {r: [c["lens"][(s, g, r)] for c in pool for s in COLOURS for g in COLOURS if g != s] for r in RULES}
    differ = [sum(c["nb_av_differ"].values()) for c in pool]
    return lens, differ


def main():
    pool, drawn, fails, pillars, removed = [], 0, Counter(), [], []
    while len(pool) < POOL:
        m = make_loop_maze_v30(N, random.Random(seed_of("v32", "maze", drawn)))
        if m is None:
            fails["construction or placement"] += 1
        else:
            c = check(m)
            if c["ok"]:
                pool.append((drawn, m, c))
                pillars.append(m.removed["pillar_walls"])
                removed.append(m.removed["random"])
            else:
                fails["AV infeasible" if any(v is None for v in c["lens"].values()) else "no NB/AV difference"] += 1
        drawn += 1
    starts = [c for c in COLOURS for _ in range(SESSIONS // 4)]
    random.Random(seed_of("v32", "starts")).shuffle(starts)
    used, sessions = set(), []
    for j, s in enumerate(starts):
        idx = next(i for i, (_, m, c) in enumerate(pool) if i not in used and any(c["nb_av_differ"][(s, g)] for g in COLOURS if g != s))
        used.add(idx)
        mi, m, c = pool[idx]
        sid = f"s{j:02d}"
        seq = random_order(random.Random(seed_of("v32", "session", sid, 0)), s)
        sessions.append({"id": sid, "maze_seed": seed_of("v32", "maze", mi), "maze": to_dict(m), "start": s, "candidate_index": 0,
                         "differing_goals": [g for g in COLOURS if g != s and c["nb_av_differ"][(s, g)]],
                         "rounds": [{"goal": g, "rule": r, "answer_len": c["lens"][(s, g, r)], "nb_av_differ": c["nb_av_differ"][(s, g)]} for g, r in seq],
                         "baseline": False})
    seen = set()
    for se in sessions:
        if se["start"] not in seen:
            se["baseline"] = True
            seen.add(se["start"])
    OUT.mkdir(parents=True, exist_ok=True)
    REP.mkdir(parents=True, exist_ok=True)
    (OUT / "mazes.json").write_text(json.dumps([{"seed_index": i, "maze": to_dict(m)} for i, m, _ in pool], indent=1))
    (OUT / "sessions.json").write_text(json.dumps(sessions, indent=1))

    v30 = [check(from_dict(x["maze"])) for x in json.loads((GEN / "v30/mazes.json").read_text())]
    v30_sessions = json.loads((GEN / "v30/sessions.json").read_text())
    l12, d12 = stats([c for _, _, c in pool])
    l9, d9 = stats(v30)
    q = lambda v: " / ".join(f"{x:.0f}" for x in np.percentile(v, [0, 25, 50, 75, 100]))  # noqa: E731

    def sess(ss):
        return (sum(r["nb_av_differ"] for s in ss for r in s["rounds"][1:]), sum(not r["nb_av_differ"] for s in ss for r in s["rounds"][1:]),
                np.mean([r["answer_len"] for s in ss for r in s["rounds"]]))
    s12, s9 = sess(sessions), sess(v30_sessions)
    L = ["# v32 미로 설계와 생성 결과 (12×12, 2026-10-24)", "",
         "v30 설계에서 미로 크기만 9×9에서 12×12로 바꿨다. 생성 방법(깊이 우선 완전 미로, 2×2 열린 공간을 만들지 않는 4% 벽 제거, 벽 기둥 허용, 막다른 칸이",
         "아닌 통로 위 네 지점, 지점 간 미로 거리 5 이상)과 채택 조건(36개 (시작, 목표, 규칙) 조합 모두 유효 경로 존재, NB·AV 정답이 갈리는 쌍 하나",
         "이상), 세션 구성(40세션, 시작 색 균형, 무작위 순서, 기준선 4세션)은 v30과 같다. 9×9 값은 v30 채택 미로 300개와 v30 세션에서 다시 계산했다.", "",
         "## 1. 채택률", "",
         "| | 12×12 (v32) | 9×9 (v30) |", "|---|---|---|",
         f"| 생성 / 채택 | {drawn} / {len(pool)} | 1566 / 300 |",
         f"| **채택률** | **{len(pool) / drawn:.3f}** | 0.192 |",
         f"| 탈락 사유 | " + ", ".join(f"{k} {v}" for k, v in fails.items()) + " | `results/v30_maze/DESIGN.md` |",
         f"| 지운 벽 수(미로당) | {Counter(removed).most_common(1)[0][0]} | 3 |",
         f"| 벽 기둥(테두리에 닿지 않는 벽, 미로당 평균) | {np.mean(pillars):.1f} | 22 |", "",
         "## 2. 아홉 조합의 해 길이 분포 (채택 미로 300 × (시작, 목표) 12쌍; 최소 / 25% / 중앙 / 75% / 최대, 이동 수)", "",
         "| 규칙 | 12×12 | 9×9 |", "|---|---|---|"] + [f"| {r} | {q(l12[r])} | {q(l9[r])} |" for r in RULES] + [
         f"| AV − NB (같은 쌍) | {q([a - b for a, b in zip(l12['AV'], l12['NB'])])} | {q([a - b for a, b in zip(l9['AV'], l9['NB'])])} |",
         f"| VIA − NB (같은 쌍) | {q([a - b for a, b in zip(l12['VIA'], l12['NB'])])} | {q([a - b for a, b in zip(l9['VIA'], l9['NB'])])} |", "",
         "## 3. NB와 AV의 정답이 갈리는 (시작, 목표) 쌍", "",
         "| | 12×12 | 9×9 |", "|---|---|---|",
         f"| 비율(채택 미로의 전체 쌍 중) | **{sum(d12) / (12 * len(d12)):.3f}** | {sum(d9) / (12 * len(d9)):.3f} |",
         f"| 미로당 평균(12쌍 중) | {np.mean(d12):.2f} | {np.mean(d9):.2f} |", "",
         "## 4. 세션", "",
         f"- 40세션, 시작 색 {dict(Counter(s['start'] for s in sessions))}, 기준선 4세션. 라운드 규칙 분포 {dict(Counter(r['rule'] for s in sessions for r in s['rounds']))}.",
         "", "| | 12×12 | 9×9 |", "|---|---|---|",
         f"| H3 집단(라운드 2–8): 갈림 / 갈리지 않음 | {s12[0]} / {s12[1]} | {s9[0]} / {s9[1]} |",
         f"| 세션 라운드의 정답 길이 평균(이동 수) | {s12[2]:.1f} | {s9[2]:.1f} |", ""]
    (REP / "DESIGN.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
