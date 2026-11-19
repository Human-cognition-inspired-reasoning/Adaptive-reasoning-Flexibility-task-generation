# -*- coding: utf-8 -*-
"""물길 v3: 조건(판)이 **제출마다** 바뀐다. 목표(꽃 색 조합)와 규칙(연속 클릭 띠)은 라운드마다 바뀐다.
   python3 bench/generate.py [개수=300] [시드=20261117] [병렬 수=8] [판 묶음 크기=4000]

v2 `물길-세축`(W 문제)에서 출발했다. 판정 · 글 · 띠(1 · 2 · 3+) · 목표 바꾸는 방식은 그대로이고, 판을 고르는 방식만 바꿨다
(사용자: "이제 물길을 조건을 제출마다 바뀌게 만들어야함", 2026-11-17).

왜   v2 는 한 라운드 안에서 판이 같아, 맞힌 순서를 그대로 다시 내면 k = 2 를 채웠다(바깥 피드백: 'k 가 의미 없다').
어떻게
     라운드마다 판 구성(샘 · 꽃 · 돌 · 파이프 모양)을 판 묶음에서 새로 뽑는 것은 v2 와 같다. 라운드 안에서는 구성을 두고
     **파이프의 처음 방향만 제출마다 새로 섞는다**. 판 BOARDS = 10 장을 만들어 제출 j 번째에 판 j 를 보여 준다.
     목표를 만드는 마지막 방향은 라운드 동안 같아 틀린 타일 번호가 늘 같은 구성을 가리키지만, 처음 방향이 달라
     타일마다 돌릴 횟수는 매번 다시 세야 한다. 한 문제의 판 50 장은 서로 다르다.
지나온 것
     Z(T = 10)와 X(T = 20)는 제출마다 서로 무관한 새 판을 깔았다. GPT 30 문제에서 둘 다 완주 0/30, 1 라운드 통과
     14 · 15/30, band 84.2 · 75.1% 였고, 2 라운드 제출의 86% 가 1 라운드처럼 꽃 셋을 모두 이었다(지금 목표 4%).
     판이 사라지니 틀린 타일 번호를 다음 답에 쓸 수 없고, 같은 판에서 꽃 하나를 빼 보는 시험도 못 했다.
     T 를 늘려도 그대로라 T = 10 으로 되돌리고 이 방식으로 바꿨다(사용자: "t=10으로 하고 그렇게 해서 만들어봐", 2026-11-17).
     Q(T = 10, 위 방식)에서 GPT 30 문제 band 38.9% [28.0~49.7], 1 라운드 통과 21/30, 완주 2/30 으로 v2 수준이 됐지만
     40 에 못 미쳤다. 직전 띠가 1번씩 · 3번 이상인 라운드는 band 47 · 42% 인데 2번씩인 라운드가 22% 였다. 2번씩은
     180° 씩만 돌아 그 띠로 풀리는 판은 타일마다 0 · 2 번만 돌리면 되고, 평소 답(필요한 만큼 이어 누르기)이 그대로
     2번씩이 된다(1 라운드 판의 82%, 전환 라운드 판의 50%). 1 라운드 2번씩 11 문제는 모두 처음 두 제출로 통과했다.
     규칙을 배우지 않고 넘어가니 다음 라운드에 고집할 옛 규칙이 없다고 보고, 1 라운드 판에 '모든 꽃을 평소대로 이으면
     지금 띠를 어긴다' 를 건 N 문제를 만들어 봤다(2026-11-18). GPT 30 문제 band 41.5% [27.0~58.2] 였지만 문제가 14 개로
     줄었고(1 라운드 통과 14/30), 2번씩 다음 라운드는 여전히 24% 였다. GPT 는 전환 직후 첫 제출만 2번씩으로 내고 첫
     피드백에 바로 버린다. 판이 같은 v2 에서도 이 라운드는 12% 였다(1번씩 34%, 3번 이상 50%). 배웠는지와 상관없는
     GPT 의 성질이라 보고 Q 로 되돌렸다(사용자: "Q로 100문제를 채워볼게", 2026-11-18). 1 라운드 규칙만 1번씩 · 3번 이상에서
     고르는 안은 2번씩 전환을 덜 재서 숫자를 올리는 쪽이라 쓰지 않았다(사용자: "1라운드 규칙만 바꾸는건 좀 불공평한거같은데").
     지금 문제는 Q001~Q300(씨앗 20261117).
     Z · X · N 문제와 런은 archive/ 에 있다.

보장: 판마다. b 지금 띠, g 지금 목표, pb · pg 직전 라운드의 것.
  모든 판
    · 라운드 안의 판은 한 구성이고 파이프의 처음 방향만 다르다. 라운드마다 새 구성.
    · g 를 b 로 풀 수 있다(S). 처음 판 그대로는 g 가 아니다(아무것도 안 눌러 맞는 판은 쓰지 않음).
    · **직전 판의 정답은 이 판에서 모두 틀린다**: 정답은 타일마다 돌린 횟수(4 로 나눈 나머지)로 정해지므로, 두 판에서
      g 를 만드는 회전 배치(9 칸의 0~3)가 하나도 겹치지 않게 한다. 라운드가 바뀌면 띠가 서로소라 저절로 틀린다.
  1 라운드 판
    · g 는 모든 꽃.
  전환 라운드 판 (v2 보장을 판마다)
    · b != pb, g != pg. g 를 pb 로도 풀 수 있다(P). pg 가 이 판에서 가능하다.
    · **이 판에서 pg 를 자연스럽게 푼 답이 pb 를 지킨다**: 가장 적게 돌리는 배치를 타일마다 필요한 횟수만큼 이어 누른 답.
      전환을 모르는 첫 제출(직전 목표를 습관대로)이 지속적 오류가 되게 하는 장치. v2 는 라운드 첫 판에만 걸었고 v3 는 판마다.

아래는 v2 머리말이다.

물길 5라운드 에피소드: 조건 · 규칙 · 목표가 매 라운드 모두 바뀐다.
   python3 bench/generate.py [개수] [시드] [병렬 수]

  조건  판 전체(파이프 · 돌 · 꽃 배치)        매 라운드 새 판
  목표  물이 닿아야 할 꽃 색 조합              1 라운드는 모든 꽃, 그 뒤 매 라운드 변경, 숨김
  규칙  같은 타일 연속 클릭 수 (1 · 2 · 3+)    매 라운드 변경, 숨김

v1 물길(규칙만 변경)에 판 변경을 더하면 지표가 52/63/46 → 22/39/28% 로 떨어졌다. 줄어든
지속적 오류의 대부분이 '두 띠를 다 어긴 오답' 으로 옮겨 갔다: 새 판에서 모델은 타일마다
필요한 만큼 이어 누르는데, 지금 문항에서 그런 답이 직전 띠를 지키는 전환은 19% 뿐이었다
(67% 는 두 띠를 다 어김). v1 은 판이 같아서 전에 통한 순서가 그대로 직전 띠를 지켰다.

보장 (라운드 i>=2.  pb = 직전 띠, b = 지금 띠, pg = 직전 목표, g = 지금 목표)
  · 판이 직전과 다르다. b != pb, g != pg
  · g 를 b 로 풀 수 있다 (S)
  · g 를 pb 로 풀 수 있다 (P): 직전 띠로 지금 목표를 푸는 답이 있다
  · pg 가 이 판에서 가능하다
  · **이 판에서 pg 를 자연스럽게 푼 답이 pb 를 지킨다**: 모든 전환에 건다
      '자연스럽게' = 가장 적게 돌리는 배치를 타일마다 필요한 횟수만큼 이어 누르기.
      가장 적게 돌리는 배치가 여럿이면 전부 그래야 한다. 전환을 모르는 첫 제출
      (직전 목표를 습관대로)이 지속적 오류가 되게 하는 장치다.

띠를 1 · 2-3 · 4+ 에서 1 · 2 · 3+ 로 바꿨다. 자연스러운 답은 타일당 1~3 번이라 4+ 를 지킬 수
없어 직전 띠가 4+ 인 전환(3 분의 1)에는 장치를 걸 수 없었고, 그 라운드는 지표가 평균 7% 였다
(장치가 걸린 라운드는 42%, 첫 런 runs/20261025_1847*, 그때 문항 episodes_띠1-23-4.json).
1 · 2 · 3+ 는 셋 다 자연스러운 답이 지킬 수 있다. 대신 '2' 는 한 번에 180° 씩만 돌므로
90° · 270° 가 필요한 타일은 못 맞춘다: 그 띠로 풀리는 (판, 목표) 만 고른다(무작위의 28%).
  띠가 서로소라 직전 띠를 지킨 답은 지금 반드시 틀린다.
  (g 를 자연스럽게 푼 답이 pb 를 지키는지는 natNew 에 남긴다: 생성 조건은 아니다.)
"""
import itertools
import json
import random
import sys

import numpy as np
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine as E                                    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SIZE, ROUNDS, FLOWERS, BOARDS = 3, 5, 3, 10
PREFIX = "Q"                                          # 문제 번호 Q001~ (v2 는 W, 지나온 것 Z · X · N)
BANDS = [dict(lo=1, hi=1), dict(lo=2, hi=2), dict(lo=3, hi=None)]
ALL = frozenset(E.COLORS)
SHAPES = None


def shapes():
    global SHAPES
    if SHAPES is None:
        SHAPES = [E.U | E.D, E.R | E.L, E.U | E.R, E.U | E.L, E.D | E.R, E.D | E.L,
                  E.U | E.R | E.D, E.U | E.R | E.L, E.U | E.D | E.L, E.R | E.D | E.L]
    return SHAPES


def random_board(rng):
    n = SIZE
    kinds = [E.PIPE] * (n * n)
    k = 1 + FLOWERS + (1 if rng.random() < 0.35 else 0)
    spots = rng.sample(range(n * n), k)
    kinds[spots[0]] = E.SOURCE
    colors = list(E.COLORS)
    rng.shuffle(colors)
    flower_color = {}
    for i in range(FLOWERS):
        kinds[spots[1 + i]] = E.FLOWER
        flower_color[spots[1 + i]] = colors[i]
    if k > 1 + FLOWERS:
        kinds[spots[-1]] = E.STONE
    tiles = []
    for i, kind in enumerate(kinds):
        if kind == E.PIPE:
            t = dict(kind=kind, mask=rng.choice(shapes()))
        elif kind == E.STONE:
            t = dict(kind=kind, mask=0)
        else:
            t = dict(kind=kind, mask=rng.choice([E.U, E.R, E.D, E.L]))
            if kind == E.FLOWER:
                t["color"] = flower_color[i]
        tiles.append(t)
    return dict(size=n, tiles=tiles)


def natural(turn_lists):
    """가장 적게 돌리는 배치들의 자연스러운 답이 지키는 띠의 번호. 하나로 정해지지 않으면 None."""
    best = min(sum(t.values()) for t in turn_lists)
    out = set()
    for t in turn_lists:
        if sum(t.values()) != best:
            continue
        rs = [r for r in t.values() if r]
        hit = [k for k, b in enumerate(BANDS)
               if rs and all(b["lo"] <= r and (b["hi"] is None or r <= b["hi"]) for r in rs)]
        out.add(hit[0] if hit else None)
    return out.pop() if len(out) == 1 else None


def study(board):
    """한 판에서 쓸 수 있는 (목표, 띠), 목표마다 자연스러운 답의 띠, 목표마다 회전 배치(9 칸 0~3) 집합."""
    if not E.maybe_solvable(board):
        return None
    table, nat, aset = {}, {}, {}
    for goal, turns in E.assignments(board).items():
        if not goal:
            continue
        for b, rule in enumerate(BANDS):
            for t in turns[:60]:
                seq = E.sequence_for(board, t, rule)
                if seq and E.evaluate(board, seq, goal, rule)["ok"]:
                    table.setdefault(goal, {})[b] = seq
                    break
        if goal in table:
            nat[goal] = natural(turns)
            aset[goal] = sorted({tuple(t.get(i, 0) for i in range(SIZE * SIZE)) for t in turns})
    if ALL not in table or len(table) < 4:
        return None
    return table, nat, aset


def collect(args):
    """판 묶음 하나를 모은다. 묶음마다 씨앗이 정해져 있어 재현된다."""
    want, seed = args
    rng = random.Random(seed)
    out, tries = [], 0
    while len(out) < want and tries < want * 6000:
        tries += 1
        board = random_board(rng)
        got = study(board)
        if got:
            table, nat, aset = got
            out.append(dict(board=board,
                            table={key(g): v for g, v in table.items()},
                            nat={key(g): v for g, v in nat.items()},
                            aset={key(g): v for g, v in aset.items()}))
    return out


def key(goal):
    return ",".join(sorted(goal))


NCELL = SIZE * SIZE
CODE = 4 ** np.arange(NCELL, dtype=np.int64)          # 회전 배치(칸마다 0~3) 를 정수 하나로
TRIES = 400                                           # 판 한 장에 처음 방향을 섞어 보는 횟수
LAYOUTS = 300                                         # 라운드 하나에 구성을 뽑아 보는 횟수


def turned(board, off):
    """같은 구성에서 파이프의 처음 방향만 off 만큼 돌린 판."""
    tiles = [dict(t) for t in board["tiles"]]
    for i, t in enumerate(tiles):
        if t["kind"] == E.PIPE and off[i]:
            t["mask"] = E.rotate(t["mask"], int(off[i]))
    return dict(size=board["size"], tiles=tiles)


def nat_bands(S):
    """회전 배치 행렬 S 에서 가장 적게 돌리는 배치마다, 타일마다 필요한 만큼 이어 누른 답이 지키는 띠의 번호(없으면 None)."""
    sums = S.sum(1)
    out = set()
    for row in S[sums == sums.min()]:
        nz = set(row[row > 0].tolist())
        out.add({1: 0, 2: 1, 3: 2}[nz.pop()] if len(nz) == 1 else None)
    return out


def nat_band(S):
    """그 띠가 하나로 정해지면 그 번호, 아니면 None. natural() 과 같은 정의."""
    out = nat_bands(S)
    return out.pop() if len(out) == 1 else None


def solve(board, pipes, S, goal, rule, cap=30):
    """S(이 판에서 goal 을 만드는 회전 배치)를 rule 을 지키는 순서로 바꿔 본다. 찾은 것 가운데 가장 짧은 답."""
    rows = S[np.argsort(S.sum(1), kind="stable")]
    if rule["lo"] == 2 and rule["hi"] == 2:              # 2 번씩은 180° 씩만 돈다
        rows = rows[(rows % 2 == 0).all(1)]
    best, want = None, frozenset(goal.split(","))      # goal 은 key() 꼴 문자열
    for row in rows[:cap]:
        seq = E.sequence_for(board, {i: int(row[i]) for i in pipes}, rule)
        if not seq:
            continue
        fixed, bad = E.violations(board, seq, rule)
        if fixed or bad or E.reaches(board, E.apply(board, seq)) != want:
            continue
        if best is None or len(seq) < len(best):
            best = seq
    return best


def one_round(rng, P, g, b, pb=None, pg=None):
    """구성 P 에서 판 BOARDS 장. 파이프의 처음 방향만 제출마다 새로 섞는다. 못 만들면 None."""
    pipes = P["pipes"]
    Tg, Tpg = P["_T"][g], (P["_T"][pg] if pg else None)
    out, prev, seen = [], None, set()
    for _ in range(BOARDS):
        for _ in range(TRIES):
            off = np.zeros(NCELL, np.int8)
            off[pipes] = [rng.randrange(4) for _ in pipes]
            S = (Tg - off) % 4                           # 이 판에서 g 를 만드는 회전 배치
            cs = set((S.astype(np.int64) @ CODE).tolist())
            if 0 in cs or (prev is not None and cs & prev):
                continue                                 # 처음 그대로 g, 또는 직전 판 정답이 통함
            board = turned(P["board"], off)
            masks = tuple(t["mask"] for t in board["tiles"])
            if masks in seen:
                continue
            if pb is not None and nat_band((Tpg - off) % 4) != pb:
                continue                                 # 직전 목표를 습관대로 풀면 직전 띠
            sh = solve(board, pipes, S, g, BANDS[b])
            if not sh:
                continue
            rs = solve(board, pipes, S, g, BANDS[pb]) if pb is not None else None
            if pb is not None and not rs:
                continue                                 # P: 직전 띠로도 지금 목표를 풂
            nb = nat_band(S)
            d = dict(board=board, shortest=sh)
            if pb is None:
                d["r1Natural"] = nb
            else:
                d.update(ruleStick=rs, natNew=nb == pb)
            out.append(d)
            prev = cs
            seen.add(masks)
            break
        else:
            return None
    return out


def make(args):
    """문제 하나. 라운드마다 새 구성을 뽑고, 그 라운드 안에서는 처음 방향만 섞는다."""
    idx, seed = args
    for attempt in range(40):
        rng = random.Random(f"{seed}-{idx}-{attempt}")
        rounds, prev, used = [], None, set()
        for n in range(1, ROUNDS + 1):
            got = None
            if prev is None:                             # 띠는 구성보다 먼저 정한다(구성을 바꿔 가며 띠까지 다시 뽑으면
                b, pb, pg = rng.randrange(len(BANDS)), None, None   # 만들기 쉬운 띠로 쏠린다: 첫 판 1 · 2 · 3+ = 654 · 225 · 621)
                # 3번 이상 다음 2번씩 전환은 만들 수 있는 구성이 드물어 문제를 처음부터 다시 만들게 되므로, 1 라운드 띠는
                # 고르지 않다(Q001~Q300 의 1 라운드 1번씩 · 2번씩 · 3번 이상 = 89 · 126 · 85).
            else:
                pb, pg = prev["band"], key(prev["goal"])
                b = rng.choice([x for x in range(len(BANDS)) if x != pb])
            for _ in range(LAYOUTS):
                li = rng.randrange(len(POOL))
                if li in used:
                    continue
                P = POOL[li]
                if prev is None:
                    g = key(ALL)
                else:
                    goals = [x for x in P["_T"] if x != pg]
                    if pg not in P["_T"] or not goals:
                        continue
                    g = rng.choice(sorted(goals))
                boards = one_round(rng, P, g, b, pb, pg)
                if boards:
                    got = (li, g, b, boards)
                    break
            if got is None:
                break
            li, g, b, boards = got
            used.add(li)
            prev = dict(round=n, goal=g.split(","), band=b, rule=BANDS[b], boards=boards)
            rounds.append(prev)
        if len(rounds) == ROUNDS:
            return dict(id=f"{PREFIX}{idx + 1:03d}", size=SIZE, rounds=rounds)
    return None


POOL = None


def init(pool):
    global POOL
    POOL = pool
    for P in POOL:
        P["_T"] = {g: np.array(v, dtype=np.int8) for g, v in P["aset"].items()}
        P["pipes"] = [i for i, t in enumerate(P["board"]["tiles"]) if t["kind"] == E.PIPE]


def main(count=300, seed=20261117, jobs=8, boards=4000):
    import time
    from multiprocessing import Pool
    t0 = time.time()
    with Pool(jobs) as pp:
        parts = pp.map(collect, [(boards // jobs, seed * 100 + j) for j in range(jobs)])
    pool = [dict(board=b["board"], aset=b["aset"]) for part in parts for b in part]
    print(f"쓸 수 있는 구성 {len(pool)} 개, {time.time() - t0:.0f} 초", flush=True)
    with Pool(jobs, initializer=init, initargs=(pool,)) as pp:
        eps = pp.map(make, [(i, seed) for i in range(count)], chunksize=1)
    miss = [i + 1 for i, e in enumerate(eps) if e is None]
    eps = [e for e in eps if e]
    print(f"{time.time() - t0:.0f} 초", flush=True)
    (ROOT / "episodes").mkdir(exist_ok=True)
    (ROOT / "episodes/episodes.json").write_text(json.dumps(eps, ensure_ascii=False, indent=1))
    rs = [r for e in eps for r in e["rounds"]]
    bs = [b for r in rs for b in r["boards"]]
    print(f"문제 {len(eps)}/{count}  못 만든 번호 {miss[:10]}  판 {len(bs)} 장(서로 다른 판 {len({json.dumps(b['board']) for b in bs})})")
    print(f"  띠 {dict(sorted(Counter(E.rule_name(r['rule']) for r in rs).items()))}")
    print(f"  목표 꽃 수 {dict(sorted(Counter(len(r['goal']) for r in rs).items()))}")
    print(f"  새 목표를 습관대로 풀어도 직전 띠 {sum(b.get('natNew', False) for r in rs for b in r['boards'])}"
          f"/{sum(len(r['boards']) for r in rs if r['round'] > 1)} 판")
    print(f"  정답 길이 {dict(sorted(Counter(len(b['shortest']) for b in bs).items()))}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[0]) if a else 300, int(a[1]) if len(a) > 1 else 20261117,
         int(a[2]) if len(a) > 2 else 8, int(a[3]) if len(a) > 3 else 4000)
