"""v30 mazes and sessions (option A, decisions 2026-10-21). Mazes: 9x9, perfect maze + 4% walls removed without opening a
2x2 room (wall pillars allowed), four coloured cells on passages at pairwise maze distance >= 5; accepted if all 36
(start, goal, rule) combinations have a valid path and at least one (start, goal) pair has different NB and AV answers.
Seeds sha256('v30|maze|<i>').

Sessions: 40, start colour balanced (10 per colour, seeded shuffle); for each session the next pool maze in which that start
has at least one goal with different NB and AV answers ("differing goal"). 8 rounds: round 1 goal and rule uniform; then
both change every round (goal among the two other non-start colours, rule among the two other rules).
Prioritization (reviewed as requested): among 500 seeded candidate orders satisfying these constraints, the order with the
most NB or AV rounds on a differing goal is chosen (ties: first). The same statistic for unprioritized random orders is
reported for comparison. Baseline sessions: the first session of each start colour (4).

  python generation/gen_v30.py      # from the task folder; writes generated/v30/mazes.json, sessions.json, DESIGN.md
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

from maze.core import seed_of
from maze.loop import COLOURS, RULES, check, make_loop_maze_v30, to_dict

N, POOL, SESSIONS, ROUNDS, CANDS = 9, 300, 40, 8, 500
PRIORITIZE = False   # decision 2026-10-22: random orders (prioritized pilot sessions kept in data/v30/sessions_prioritized_pilot.json)
OUT = REP = GEN / "v30"


def random_order(rng, start):
    goals = [x for x in COLOURS if x != start]
    seq = [(rng.choice(goals), rng.choice(RULES))]
    while len(seq) < ROUNDS:
        pg, pr = seq[-1]
        seq.append((rng.choice([x for x in goals if x != pg]), rng.choice([r for r in RULES if r != pr])))
    return seq


def score(seq, differ, start):
    return sum(r in ("NB", "AV") and differ[(start, g)] for g, r in seq)


def main():
    pool, drawn, fails, pillars = [], 0, Counter(), []
    while len(pool) < POOL:
        m = make_loop_maze_v30(N, random.Random(seed_of("v30", "maze", drawn)))
        if m is None:
            fails["construction or placement"] += 1
        else:
            pillars.append(m.removed["pillar_walls"])
            c = check(m)
            if c["ok"]:
                pool.append((drawn, m, c))
            else:
                fails["AV infeasible" if any(v is None for v in c["lens"].values()) else "no NB/AV difference"] += 1
        drawn += 1
    starts = [c for c in COLOURS for _ in range(SESSIONS // 4)]
    random.Random(seed_of("v30", "starts")).shuffle(starts)
    used, sessions, rand_scores, prio_scores = set(), [], [], []
    for j, s in enumerate(starts):
        idx = next(i for i, (_, m, c) in enumerate(pool) if i not in used and any(c["nb_av_differ"][(s, g)] for g in COLOURS if g != s))
        used.add(idx)
        mi, m, c = pool[idx]
        sid = f"s{j:02d}"
        cands = [random_order(random.Random(seed_of("v30", "session", sid, k)), s) for k in range(CANDS)]
        rand_scores.append(score(cands[0], c["nb_av_differ"], s))
        best = max(range(CANDS), key=lambda k: (score(cands[k], c["nb_av_differ"], s), -k)) if PRIORITIZE else 0
        prio_best = max(range(CANDS), key=lambda k: (score(cands[k], c["nb_av_differ"], s), -k))
        seq = cands[best]
        prio_scores.append(score(cands[prio_best], c["nb_av_differ"], s))
        sessions.append({"id": sid, "maze_seed": seed_of("v30", "maze", mi), "maze": to_dict(m), "start": s, "candidate_index": best,
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
    lens = {r: [c["lens"][(s, g, r)] for _, _, c in pool for s in COLOURS for g in COLOURS if g != s] for r in RULES}
    differ = [sum(c["nb_av_differ"].values()) for _, _, c in pool]
    q = lambda v: " / ".join(f"{x:.0f}" for x in np.percentile(v, [0, 25, 50, 75, 100]))  # noqa: E731
    trans = Counter()
    for se in sessions:
        for a, b in zip(se["rounds"], se["rounds"][1:]):
            if {a["rule"], b["rule"]} == {"NB", "AV"}:
                trans["NB<->AV"] += 1
                trans["NB<->AV, new round on a differing goal"] += b["nb_av_differ"]
    L = ["# v30 미로 설계와 생성 결과 (A안, 2026-10-21)", "",
         "## 1. 미로 조건의 충돌과 선택", "",
         "명세의 세 조건, 즉 (1) 4% 벽 제거로 고리를 넣는다, (2) 벽이 모두 테두리에 이어진다, (3) 2×2 이상의 열린 공간이 없다는 동시에 성립할 수 없다.",
         "고리(통로의 닫힌 순환)는 벽이 만나는 격자 꼭짓점을 적어도 하나 둘러싼다. 그 꼭짓점에 벽이 붙어 있으면 그 벽 덩어리는 통로 순환을 넘지 못해",
         "테두리에 닿지 못하고(조건 2 위반), 붙은 벽이 없으면 그 꼭짓점을 둘러싼 네 벽이 모두 없어 2×2 칸이 열린다(조건 3 위반). 따라서 고리가 있는 한",
         "셋 중 하나를 포기해야 한다.", "",
         "수치(모델 호출 없음, `DESIGN_CONFLICT.md`): v29 방식(무작위 4% 제거 → 벽 덩어리 제거)은 2×2 열린 공간을 무작위 제거 뒤 62%, 벽 덩어리 제거",
         "뒤 100% 미로에 만들었다(300개). 세 조건을 모두 지키며 벽을 지우게 하면 벽을 하나도 지울 수 없는 미로가 100%였다(500개).", "",
         "**선택(A안, 연구 책임자 결정)**: 고리를 유지하고 2×2 열린 공간을 금지하며, 벽 연결성을 포기해 고리 안의 벽 기둥(테두리에 닿지 않는 벽 덩어리)을",
         "허용한다. 근거: 고리가 없으면 NB와 AV가 같은 답이거나 AV가 풀리지 않아 두 규칙 사이 전환의 보속을 잴 수 없고(v27), 방처럼 트인 공간은 경로가",
         "여러 갈래로 퍼져 미로의 성격을 가장 크게 해친다. 벽 기둥은 미로 안의 섬일 뿐 통로 구조를 바꾸지 않는다.", "",
         "## 2. 생성 방법",
         "", "깊이 우선 완전 미로 → 남은 내부 벽을 무작위 순서로 보며 2×2 열린 공간을 만들지 않는 벽만 지워 4%(3개)를 채움 → 벽 덩어리 제거 단계 없음 →",
         "색 칸 넷을 통로(열린 이웃 2개 이상, 막다른 칸 제외) 위에 서로 미로 거리 5 이상 떨어지게 배치(시도 500회).", "",
         "## 3. 채택률", "",
         f"- 생성 {drawn}, 채택 {len(pool)}, **채택률 {len(pool) / drawn:.3f}**. 탈락: " + ", ".join(f"{k} {v}" for k, v in fails.items()),
         f"- 미로당 벽 기둥(테두리에 닿지 않는 벽): 평균 {np.mean(pillars):.1f}개.", "",
         "## 4. 아홉 조합의 해 길이 분포 (채택 미로 300 × (시작, 목표) 12쌍; 최소 / 25% / 중앙 / 75% / 최대, 이동 수)", "",
         "| 규칙 | 해 길이 |", "|---|---|"] + [f"| {r} | {q(lens[r])} |" for r in RULES] + [
         "", "같은 쌍에서 AV − NB: " + q([a - b for a, b in zip(lens["AV"], lens["NB"])]) + "; VIA − NB: " + q([a - b for a, b in zip(lens["VIA"], lens["NB"])]), "",
         "## 5. NB와 AV의 정답이 갈리는 (시작, 목표) 쌍", "",
         f"- 채택 미로에서 {sum(differ)}/{12 * len(pool)} = **{sum(differ) / (12 * len(pool)):.3f}**, **미로당 평균 {np.mean(differ):.2f}쌍(12쌍 중)**. 분포: "
         + ", ".join(f"{k}쌍 {v}" for k, v in sorted(Counter(differ).items())),
         f"- 시작 색마다 갈리는 목표 수(채택 미로 × 시작 4): " + ", ".join(f"{k}개 {v}" for k, v in sorted(Counter(sum(c['nb_av_differ'][(s, g)] for g in COLOURS if g != s) for _, _, c in pool for s in COLOURS).items())), "",
         "## 6. 세션과 전환 쌍 우선 배치 검토", "",
         f"- 40세션, 시작 색 균형 {dict(Counter(s['start'] for s in sessions))}, 각 세션의 미로는 그 시작 색에 갈리는 목표가 하나 이상 있는 채택 미로. 기준선 4세션.",
         "- 목표가 매 라운드 바뀌어야 하므로(직전과 같은 목표 금지) 갈리는 목표가 하나뿐인 시작에서는 그 목표를 연속 라운드에 둘 수 없다. 따라서 NB·AV",
         "  라운드를 모두 갈리는 목표에 둘 수는 없다.",
         f"- 세션당 \"NB 또는 AV 라운드이면서 갈리는 목표인 라운드\" 수: 무작위 순서 평균 {np.mean(rand_scores):.2f}, **우선 배치(후보 500개 중 최대) 평균 "
         f"{np.mean(prio_scores):.2f}**(8라운드 중). 우선 배치는 규칙·목표 순서를 균등 추출에서 벗어나게 하므로, 모델이 \"NB·AV 라운드는 특정 목표\" 같은",
         "  규칙성을 학습할 여지가 생긴다. **결정(2026-10-22): 우선 배치는 채택하지 않고 무작위 순서(첫 시드 후보)로 세션을 만든다**(측정량보다 균등성 우선;",
         "  우선 배치를 쓴 예비 호출 세션은 `data/v30/sessions_prioritized_pilot.json`으로 보존). 아래 수치는 최종(무작위) 세션의 값이다.",
         f"- NB↔AV 직접 전환: {trans['NB<->AV']}회, 그중 새 라운드가 갈리는 목표인 경우 {trans['NB<->AV, new round on a differing goal']}회.",
         f"- 라운드 규칙 분포 {dict(Counter(r['rule'] for s in sessions for r in s['rounds']))}; 갈리는 목표 라운드 {sum(r['nb_av_differ'] for s in sessions for r in s['rounds'])}/320.",
         f"- H3 집단 크기(라운드 2–8, 새 라운드의 (시작, 목표)에서 NB·AV 정답이 갈리는가): 갈림 {sum(r['nb_av_differ'] for s in sessions for r in s['rounds'][1:])}, "
         f"갈리지 않음 {sum(not r['nb_av_differ'] for s in sessions for r in s['rounds'][1:])} (전환 280개 중, 모델당).",
         f"- 세션 안 목표 분포 치우침(가장 많은 목표의 라운드 수, 8라운드 중) 평균: "
         f"{np.mean([max(Counter(r['goal'] for r in s['rounds']).values()) for s in sessions]):.2f}.", ""]
    (REP / "DESIGN.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
