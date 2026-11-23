"""v32 extension to 300 sessions (decision 2026-10-26): one session per accepted 12x12 maze, no maze shared by two sessions.

  python generation/gen_v32_300.py      # after gen_v32.py; writes generated/v32/sessions300.json, sessions100.json,
                                        #        sessions_heldout.json, SESSIONS300.md (generated/ is not tracked)

- s00-s39 are the existing v32 sessions, unchanged (checked against data/v32/sessions.json).
- The other accepted mazes of the v32 pool (data/v32/mazes.json, 300) get one session each, in pool order: the first 60
  unused mazes become s40-s99, the remaining 200 become s100-s299. A maze can take a start colour only if that start has
  at least one goal with different NB and AV answers (the v30/v32 session condition). Start colours are balanced inside
  each block by maximum flow: s40-s99 15 per colour (with s00-s39's 10 per colour: 25 per colour in s00-s99), s100-s299
  50 per colour (75 per colour overall). If a block cannot be balanced, new mazes would have to be generated (reported).
- Rounds: goal and rule order seeded by session id, as in gen_v32 (random_order, seed 'v32|session|<sid>|0').
- Baseline sessions (feedback removed): the existing 4 (s00-s39, first of each start colour) plus 6 from s40-s99 (the first
  sessions of each colour in s40-s99, colours taken in the order of their first appearance, until 6), 10 in all.
- Used in the experiment: s00-s99 (sessions100.json). Held out (not used): s100-s299.
"""
import json
import random
from collections import Counter
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # task folder (benchmark/maze-rule-switch)
GEN = ROOT / "generated"                             # generator outputs, not tracked by git
sys.path.insert(0, str(ROOT))

from gen_v30 import random_order
from maze.core import seed_of
from maze.loop import COLOURS, check, from_dict

OLD = json.loads((GEN / "v32/sessions.json").read_text())
POOL = json.loads((GEN / "v32/mazes.json").read_text())


def balance(mazes, elig, per):
    """Assign each maze one colour, exactly `per` per colour, colour allowed by elig[maze]. Max flow (augmenting paths)."""
    cap = {c: per for c in COLOURS}
    match = {}            # maze -> colour
    owners = {c: [] for c in COLOURS}

    def augment(m, seen):
        for c in elig[m]:
            if c in seen:
                continue
            seen.add(c)
            if len(owners[c]) < cap[c]:
                owners[c].append(m)
                match[m] = c
                return True
            for o in list(owners[c]):
                if augment_from(o, c, seen):
                    owners[c].remove(o)
                    owners[c].append(m)
                    match[m] = c
                    return True
        return False

    def augment_from(o, cur, seen):
        for c in elig[o]:
            if c == cur or c in seen:
                continue
            seen.add(c)
            if len(owners[c]) < cap[c]:
                owners[c].append(o)
                match[o] = c
                return True
            for o2 in list(owners[c]):
                if augment_from(o2, c, seen):
                    owners[c].remove(o2)
                    owners[c].append(o)
                    match[o] = c
                    return True
        return False
    for m in mazes:
        augment(m, set())
    return match if len(match) == len(mazes) else None


def main():
    seeds = {s["maze_seed"] for s in OLD}
    checks = [check(from_dict(x["maze"])) for x in POOL]
    pool_seed = [seed_of("v32", "maze", x["seed_index"]) for x in POOL]
    used = [i for i, sd in enumerate(pool_seed) if sd in seeds]
    assert len(used) == 40, len(used)
    rest = [i for i in range(len(POOL)) if i not in used]
    elig = {i: [c for c in COLOURS if any(checks[i]["nb_av_differ"][(c, g)] for g in COLOURS if g != c)] for i in rest}
    blocks = [(rest[:60], 15, 40), (rest[60:], 50, 100)]
    sessions = [dict(s) for s in OLD]
    for idx, per, first in blocks:
        match = balance(idx, elig, per)
        if match is None:
            raise SystemExit(f"block starting s{first}: colours cannot be balanced ({per} per colour); more mazes needed")
        for j, i in enumerate(idx):
            sid = f"s{first + j:02d}" if first + j < 100 else f"s{first + j}"
            s, c = match[i], checks[i]
            seq = random_order(random.Random(seed_of("v32", "session", sid, 0)), s)
            sessions.append({"id": sid, "maze_seed": pool_seed[i], "maze": POOL[i]["maze"], "start": s, "candidate_index": 0,
                             "differing_goals": [g for g in COLOURS if g != s and c["nb_av_differ"][(s, g)]],
                             "rounds": [{"goal": g, "rule": r, "answer_len": c["lens"][(s, g, r)], "nb_av_differ": c["nb_av_differ"][(s, g)]}
                                        for g, r in seq],
                             "baseline": False})
    # existing sessions regenerate identically (rounds from the same seed)
    for s in OLD:
        assert random_order(random.Random(seed_of("v32", "session", s["id"], 0)), s["start"]) == [(r["goal"], r["rule"]) for r in s["rounds"]], s["id"]
    order = []
    for s in sessions[40:100]:
        if s["start"] not in order:
            order.append(s["start"])
    picked = []
    for col in order:
        if len(picked) == 6:
            break
        picked.append(next(s for s in sessions[40:100] if s["start"] == col))
    for col in order:
        if len(picked) == 6:
            break
        picked.append([s for s in sessions[40:100] if s["start"] == col][1])
    for s in picked:
        s["baseline"] = True
    assert len({s["maze_seed"] for s in sessions}) == 300
    (GEN / "v32/sessions300.json").write_text(json.dumps(sessions, indent=1))
    (GEN / "v32/sessions100.json").write_text(json.dumps(sessions[:100], indent=1))
    (GEN / "v32/sessions_heldout.json").write_text(json.dumps(sessions[100:], indent=1))
    base = [s for s in sessions[:100] if s["baseline"]]
    L = ["# v32 300세션 생성 (2026-10-26)", "", __doc__.split("\n\n", 1)[1].strip(), "",
         "## 결과", "",
         f"- 미로 추가 생성: **없음**. v32 채택 미로 300개(채택률 0.268, 생성 1,118개 중 300)로 300세션을 모두 만들었다. 미로 하나에 세션 하나.",
         f"- 시작 색을 배정할 수 있는 미로(그 색에 NB·AV 정답이 갈리는 목표가 있음), 남은 260개 기준: "
         + ", ".join(f"{c} {sum(c in v for v in elig.values())}" for c in COLOURS) + "; 미로당 가능한 색 수 분포 "
         + str(dict(sorted(Counter(len(v) for v in elig.values()).items()))),
         f"- 시작 색 분포: s00–s39 {dict(Counter(s['start'] for s in sessions[:40]))}, s40–s99 {dict(Counter(s['start'] for s in sessions[40:100]))}, "
         f"s00–s99 {dict(Counter(s['start'] for s in sessions[:100]))}, s100–s299 {dict(Counter(s['start'] for s in sessions[100:]))}, "
         f"전체 {dict(Counter(s['start'] for s in sessions))}",
         f"- 실험에 쓰는 100세션(s00–s99) 중 기존 40세션: **40/100 = 0.40**. 비공개 분할 s100–s299(200세션)는 쓰지 않는다.",
         f"- 기준선 10세션: {[s['id'] for s in base]}; 시작 색 {dict(Counter(s['start'] for s in base))}(기존 4는 색마다 1, 새 6은 s40–s99에서 색이 처음 나오는 순서로 고름).",
         f"- 라운드 규칙 분포(s00–s99) {dict(Counter(r['rule'] for s in sessions[:100] for r in s['rounds']))}; H3 집단(라운드 2–8) 갈림 "
         f"{sum(r['nb_av_differ'] for s in sessions[:100] for r in s['rounds'][1:])}, 갈리지 않음 {sum(not r['nb_av_differ'] for s in sessions[:100] for r in s['rounds'][1:])}.",
         "- 검증: s00–s39는 `data/v32/sessions.json`과 미로·시작 색·라운드가 같다(라운드 순서를 같은 시드로 다시 만들어 대조). 300세션의 미로는 모두 다르다.", ""]
    (GEN / "v32/SESSIONS300.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
