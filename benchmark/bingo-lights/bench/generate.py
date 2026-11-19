# -*- coding: utf-8 -*-
"""3×3 빙고 전구 v3: 조건(배선표)이 **제출마다** 바뀐다. 목표(어느 줄)와 규칙(금지 전구)은 라운드마다 바뀐다.
   python3 bench/generate.py [개수] [시드] [병렬 수]

v2 `전구-빙고-옛답몫-버튼6`(X 문제)에서 출발했고, 판정 · 글 · 목표 바뀌는 방식(뺌 · 더함)은 그대로다. 바뀐 것은 판을 만드는 방식이다
(사용자: "만들어봐", 2026-11-13).

왜   v2 는 한 라운드 안에서 배선표가 같아, 맞힌 답을 그대로 다시 내면 k = 2 를 채웠다(바깥 피드백: 'k 가 의미 없다').
어떻게
     라운드마다 판 BOARDS = 10 장을 미리 만들고, 제출 j 번째에 판 j 를 보여 준다. 다음 판 = 직전 판에서 4 칸을 바꾸고
     버튼 줄 순서를 섞은 것(A 가 하던 일을 D 가 하는 식). 직전 판의 정답이 다음 판에서 하나라도 통하면 그 판은 버린다.
     v2 X 판으로 잰 통과율(판 3,600 장씩):
       4 칸만 바꿈      보장 통과 3.4%, 그중 직전 정답이 모두 막힌 판 2%   (막히려면 바뀐 4 칸이 직전 정답의 버튼 위에 떨어져야
                                                                         해서, 모델이 바뀐 칸만 찾아 고치면 됨)
       8 칸 · 12 칸     0.3% · 0%
       4 칸 + 섞기      보장 통과 3.4%, 그중 90% 가 직전 정답과 안 겹침    ← 이것을 쓴다

보장: 판마다. L 지금 금지 전구, T 지금 목표, L' · T' 직전 라운드의 것.
  모든 판
    · L 은 T 의 줄 위에 없다. L 을 안 켜고 정확히 T 를 만드는 답이 3 번 이하로 있다.
    · 직전 판(라운드 첫 판이면 직전 라운드 마지막 판)의 정답은 이 판에서 모두 틀린다: 4 번 이하 누름 전부를 따져서.
  1 라운드 판
    · 규칙 없이 가장 짧은 T 답은 모두 L 을 켠다. L 을 피한 가장 짧은 답은 그보다 많아야 한 번 길다.
  전환 라운드 판
    · L' 도 T 의 줄 위에 없다. L 은 L' 과도 두 라운드 전 금지와도 다르다. T 는 T' 에서 한 줄 빼거나 더한 것(plan).
    · L' 도 L 도 안 켜서는 줄을 하나도 못 만든다: 직전 금지를 지키며 줄을 만들면 반드시 L 을 켠다.
    · 규칙 없이 가장 짧은 T 답은 모두 L' 을 켜고 L 은 안 켠다(옛 규칙을 버린 모델은 바로 정답).
    · 이 판에서 T' 를 만드는 3 번 이하 답의 절반 이상이 L' 을 안 켠다. 그런 습관 답이 3 번 이하로 있다.
    · T 를 만드는 3 번 이하 답의 1/3 넘게가 L' 을 안 켠다.
    · 가장 짧은 습관 답은 끝에 꺼진 T 전구를 누르는 도중 켜지 않는다.
    · 옛 금지를 지켜도 끝 판 고치기가 막히지 않는다. L' 을 켜는 버튼이 둘 이상.
  v2 에서 뺀 것
    · '직전 라운드 정답을 그대로 내면 지속적 오류': 판이 섞여 글자 그대로 다시 낸 답은 다른 버튼을 누른다. 습관 답(직전 목표를
      직전 금지만 피해 만든 답)은 '둘 다 피하면 줄 0' 때문에 모든 판에서 지속적 오류다.
    · '직전 · 두 라운드 전 정답은 지금 틀림': 위의 '직전 판 정답은 이 판에서 모두 틀림' 으로 바꿨다.

판 기록: rounds[i].boards[j] = {table, shortest, default, ruleStick, habit, from}. from = {change, perm}:
  직전 판에서 change 의 칸을 바꾸고(button 은 바꾸기 전 판의 버튼 번호), 새 판의 버튼 b 는 그 판의 버튼 perm[b].
  1 라운드 첫 판은 from 이 없다(새로 뽑음). ruleStick · habit 은 전환 라운드에만 있다.
"""
import json
import random
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine as E                                    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BUTTONS, ROUNDS, BOARDS, CHANGE = 6, 5, 10, 4
SEQS = E.sequences(BUTTONS, 4)
GOALS = {1: [(i,) for i in range(len(E.LINES))],
         2: list(combinations(range(len(E.LINES)), 2))}
CHANGES = ("뺌", "더함")
COUPLE = 1 / 2                                        # 옛 목표 답 가운데 L' 을 안 켜는 몫의 하한
SHARE = 1 / 3                                         # 새 목표 답 가운데 L' 을 안 켜는 몫이 이것을 넘는다
FIRST_TRIES, NEXT_TRIES, REROLLS = 1000, 3000, 400   # 라운드 첫 판 후보 수 · 다음 판 하나의 후보 수 · 출발점 바꾸기 횟수
PREFIX = "Y"                                          # 문제 번호 Y001~ (v2 는 X)


def change(T, Tp):
    """직전 목표 Tp 에서 지금 목표 T 로 바뀐 방식. 뺌 · 더함이 아니면 None."""
    a, b = set(T), set(Tp)
    if a < b and len(b) - len(a) == 1:
        return "뺌"
    if a > b and len(a) - len(b) == 1:
        return "더함"
    return None


def plan(rng):
    """(라운드별 목표 줄 수, 전환별 바뀌는 방식). 한 줄 → 더함, 두 줄 → 뺌. 1 라운드는 한 줄 · 두 줄 반반."""
    size = 1 if rng.random() < 1 / 2 else 2
    sizes, kinds = [size], []
    for _ in range(ROUNDS - 1):
        kind = "더함" if size == 1 else "뺌"
        size = 1 if kind == "뺌" else 2
        sizes.append(size)
        kinds.append(kind)
    return sizes, kinds


def pick(seqs):
    """가장 짧고, 같으면 글자 순으로 앞선 것."""
    return list(min(seqs, key=lambda p: (len(p), p)))


def shortest(seqs):
    m = min(len(p) for p in seqs)
    return [p for p in seqs if len(p) == m]


def distinct(table):
    return len({tuple(r) for r in table}) == len(table)


def random_table(rng):
    """버튼마다 3~4 칸 켜고 0~2 칸 끈다."""
    while True:
        t = []
        for _ in range(BUTTONS):
            row = [E.KEEP] * E.CELLS
            on, off = rng.randint(3, 4), rng.randint(0, 2)
            for k, c in enumerate(rng.sample(range(E.CELLS), on + off)):
                row[c] = E.ON if k < on else E.OFF
            t.append(row)
        if distinct(t):
            return t


def perturb(rng, table):
    """정확히 CHANGE 칸을 다른 효과로 바꾼다. 버튼마다 켜는 칸이 둘 이상 남고 버튼끼리 같아지지 않게."""
    cells = [(b, c) for b in range(BUTTONS) for c in range(E.CELLS)]
    while True:
        t = [row[:] for row in table]
        done = []
        for b, c in rng.sample(cells, CHANGE):
            to = rng.choice([e for e in E.EFFECTS if e != t[b][c]])
            done.append(dict(button=b, light=c, frm=t[b][c], to=to))
            t[b][c] = to
        if all(sum(e == E.ON for e in row) >= 2 for row in t) and distinct(t):
            return t, done


def next_board(rng, table):
    """직전 판에서 4 칸을 바꾸고 버튼 줄 순서를 섞는다. 새 판의 버튼 b = 바꾼 판의 버튼 perm[b]."""
    t, done = perturb(rng, table)
    perm = list(range(BUTTONS))
    rng.shuffle(perm)
    return [t[p] for p in perm], dict(change=done, perm=perm)


LINE_BITS = [sum(1 << c for c in l) for l in E.LINES]
LINES_OF = [tuple(i for i, lb in enumerate(LINE_BITS) if (s & lb) == lb) for s in range(1 << E.CELLS)]


def survey(table):
    """순서마다 (완성된 줄 묶음, 켜 본 전구 비트, 끝 판 비트). engine.run · lines 와 같은 계산을 비트로 한다.
    SEQS 는 짧은 순서부터라 앞 순서의 결과가 늘 먼저 있다."""
    on = [sum(1 << c for c, e in enumerate(row) if e == E.ON) for row in table]
    off = [sum(1 << c for c, e in enumerate(row) if e == E.OFF) for row in table]
    acc, out = {(): (0, 0)}, {}
    for p in SEQS:
        end, m = acc[p[:-1]]
        b = p[-1]
        end, m = (end | on[b]) & ~off[b], m | on[b]
        acc[p] = (end, m)
        out[p] = (LINES_OF[end], m, end)
    by = defaultdict(list)
    for p, (done, _, _) in out.items():
        by[done].append(p)
    return out, by


def blocked(info, exact, old, want):
    """옛 금지 전구 old 를 지키면 못 하는 끝 판 고치기: (전구, 값) 집합. 정확히 목표인 답들의 끝 판에서 목표 줄 밖 전구가
    가질 수 있는 값 가운데, old 를 안 켜는 답으로는 못 만드는 것. old 자신은 뺀다."""
    free = sum(1 << c for c in range(E.CELLS) if c not in want and c != old)
    on_seen = off_seen = on_kept = off_kept = 0
    for p in exact:
        _, m, end = info[p]
        on_seen |= end; off_seen |= ~end
        if not m >> old & 1:
            on_kept |= end; off_kept |= ~end
    bad_on, bad_off = on_seen & ~on_kept & free, off_seen & ~off_kept & free
    return ({(c, 1) for c in range(E.CELLS) if bad_on >> c & 1}
            | {(c, 0) for c in range(E.CELLS) if bad_off >> c & 1})


def judge(table, info, by, T, L, prev):
    """이 판에서 (T, L) 이 보장을 지키면 (판 기록, 정답 집합), 아니면 None. prev 는 직전 라운드(1 라운드면 None)."""
    want = E.need(T)
    if L in want:
        return None
    nline = by.get(tuple(T))
    if not nline:
        return None
    lit = lambda p, c: info[p][1] >> c & 1                  # noqa: E731
    S = [p for p in nline if not lit(p, L)]
    if not S or min(map(len, S)) > 3:
        return None
    dflt = shortest(nline)
    rec = dict(shortest=pick(S), default=pick(dflt))
    if prev is None:
        if not all(lit(p, L) for p in dflt) or min(map(len, S)) > len(dflt[0]) + 1:
            return None
        return rec, set(S)
    Tp, Lp = tuple(prev["goal"]), prev["forbidden"]
    if Lp in want or len(E.lighting(table, Lp)) < 2:
        return None
    if not all(lit(p, Lp) for p in dflt) or any(lit(p, L) for p in dflt):
        return None
    old = [p for p in by.get(Tp, []) if len(p) <= 3]
    if not old or sum(not lit(p, Lp) for p in old) < COUPLE * len(old):
        return None
    ex3 = [p for p in nline if len(p) <= 3]
    if sum(not lit(p, Lp) for p in ex3) <= SHARE * len(ex3):
        return None
    H = [p for p in by.get(Tp, []) if not lit(p, Lp)]
    if not H or min(map(len, H)) > 3:
        return None
    wbits = sum(1 << c for c in want)
    if any(info[p][1] & wbits & ~info[p][2] for p in shortest(H)):
        return None
    if blocked(info, nline, Lp, want):
        return None
    if any(info[p][0] for p in SEQS if not lit(p, Lp) and not lit(p, L)):
        return None
    rec.update(ruleStick=pick([p for p in nline if not lit(p, Lp)]), habit=pick(H))
    return rec, set(S)


def board_ok(table, info, by, prev):
    """(T, L) 과 무관한 전환 라운드 보장을 먼저 본다: 대부분의 판이 여기서 걸러져 (T, L) 을 다 따질 필요가 없다."""
    Lp = prev["forbidden"]
    lit = lambda p, c: info[p][1] >> c & 1                  # noqa: E731
    old = [p for p in by.get(tuple(prev["goal"]), []) if len(p) <= 3]
    if not old or sum(not lit(p, Lp) for p in old) < COUPLE * len(old):
        return False
    H = [p for p in by.get(tuple(prev["goal"]), []) if not lit(p, Lp)]
    return bool(H) and min(map(len, H)) <= 3


def extend(rng, table, T, L, prev, lastS):
    """같은 (T, L) 로 다음 판 하나. 못 찾으면 None."""
    for _ in range(NEXT_TRIES):
        t, how = next_board(rng, table)
        if prev is not None and len(E.lighting(t, prev["forbidden"])) < 2:
            continue
        info, by = survey(t)
        got = judge(t, info, by, T, L, prev)
        if got and not (got[1] & lastS):
            return dict(table=t, **got[0], **{"from": how}), got[1]
    return None


def first_board(rng, start, prev, back, prevS, size, kind, tries):
    """라운드 첫 판과 (T, L). start 는 직전 판(1 라운드면 None)."""
    for _ in range(tries):
        table, how = (random_table(rng), None) if start is None else next_board(rng, start)
        if prev is not None and len(E.lighting(table, prev["forbidden"])) < 2:
            continue
        info, by = survey(table)
        if prev is None:
            goals, lights = list(GOALS[size]), list(range(E.CELLS))
        else:
            if not board_ok(table, info, by, prev):
                continue
            goals = [T for k in (1, 2) for T in GOALS[k] if change(T, prev["goal"]) == kind]
            lights = [c for c in range(E.CELLS) if c not in (prev["forbidden"], back)]
        rng.shuffle(goals)
        rng.shuffle(lights)
        for T in goals:
            for L in lights:
                got = judge(table, info, by, T, L, prev)
                if got and not (got[1] & prevS):
                    return T, L, dict(table=table, **got[0], **({"from": how} if how else {})), got[1]
    return None


def one_round(rng, start, prev, back, prevS, size=None, kind=None, tries=FIRST_TRIES):
    """판 BOARDS 장과 (T, L). 라운드 첫 판에서 (T, L) 을 정하고 나머지 판은 그 (T, L) 로 이어 찾는다.
    반환 (라운드, 판마다의 정답 집합) 또는 None."""
    for _ in range(4):
        got = first_board(rng, start, prev, back, prevS, size, kind, tries)
        if not got:
            return None
        T, L, board, S = got
        boards, sets = [board], [S]
        while len(boards) < BOARDS:
            nxt = extend(rng, boards[-1]["table"], T, L, prev, sets[-1])
            if not nxt:
                break
            boards.append(nxt[0])
            sets.append(nxt[1])
        if len(boards) == BOARDS:
            return dict(goal=list(T), forbidden=L, boards=boards), sets
    return None


def make(rng, how):
    """다섯 라운드. 전환 라운드의 첫 판은 드물어서(직전 판에서 4 칸 + 섞기로 만든 판의 약 0.002%), 못 찾으면 직전 라운드의
    마지막 판을 다시 뽑아(같은 보장으로) 출발점을 바꿔 가며 찾는다. 그래도 안 되면 None: 문제를 처음부터 다시 만든다."""
    sizes, kinds = how
    rounds, sets = [], []
    for n in range(ROUNDS):
        if not n:
            got = one_round(rng, None, None, None, set(), size=sizes[0])
        else:
            prev, back = rounds[-1], (rounds[-2]["forbidden"] if len(rounds) >= 2 else None)
            pp = rounds[-2] if len(rounds) >= 2 else None
            got = None
            for again in range(REROLLS):
                if again:                                  # 직전 라운드 마지막 판을 다시 뽑는다
                    nxt = extend(rng, prev["boards"][-2]["table"], prev["goal"], prev["forbidden"], pp, sets[-1][-2])
                    if not nxt:
                        continue
                    prev["boards"][-1], sets[-1][-1] = nxt
                got = one_round(rng, prev["boards"][-1]["table"], prev, back, sets[-1][-1], kind=kinds[n - 1])
                if got:
                    break
        if not got:
            return None
        r, ss = got
        rounds.append(dict(round=n + 1, **r))
        sets.append(ss)
    return rounds


def one(args):
    """문제 하나. 번호마다 씨앗이 정해져 있어, 개수를 늘려 다시 만들어도 앞 번호의 문제는 그대로다."""
    i, seed = args
    rng = random.Random(seed * 1000 + i)
    how = plan(rng)
    while True:
        r = make(rng, how)
        if r:
            return i, r


def main(count=300, seed=20261113, jobs=8):
    """문제가 끝날 때마다 진행을 찍고, 앞 번호부터 빈틈없이 끝난 데까지 episodes.json 에 써 둔다."""
    import time
    from multiprocessing import Pool
    (ROOT / "episodes").mkdir(exist_ok=True)
    t0, done = time.time(), {}
    have = ROOT / "episodes/episodes.json"
    if have.exists():
        for e in json.loads(have.read_text()):
            done[int(e["id"][1:]) - 1] = e["rounds"]
    todo = [(i, seed) for i in range(count) if i not in done]
    print(f"이미 있는 {len(done)} 개는 그대로 두고 {len(todo)} 개를 만든다", flush=True)

    def save():
        k = 0
        while k in done:
            k += 1
        eps = [dict(id=f"{PREFIX}{i + 1:03d}", buttons=BUTTONS, rounds=done[i]) for i in range(k)]
        have.write_text(json.dumps(eps, ensure_ascii=False, indent=1))
        return eps

    eps = save()
    with Pool(jobs) as pool:
        for i, r in pool.imap_unordered(one, todo):
            done[i] = r
            eps = save()
            print(f"  {PREFIX}{i + 1:03d} 끝: {len(done)}/{count}, 앞에서부터 {len(eps)} 개 저장, {time.time() - t0:.0f} 초",
                  flush=True)
    bs = [(r, b) for e in eps for r in e["rounds"] for b in r["boards"]]
    print(f"문제 {len(eps)}/{count}, 판 {len(bs)}")
    print(f"  정답 길이 {dict(sorted(Counter(len(b['shortest']) for _, b in bs).items()))}"
          f"   규칙 없는 최단 길이 {dict(sorted(Counter(len(b['default']) for _, b in bs).items()))}")
    rs = [r for e in eps for r in e["rounds"]]
    print(f"  목표 줄 수 {dict(sorted(Counter(len(r['goal']) for r in rs).items()))}"
          f"   금지 전구 {dict(sorted(Counter(r['forbidden'] + 1 for r in rs).items()))}")
    print(f"  바뀌는 방식 {dict(Counter(change(r['goal'], e['rounds'][i - 1]['goal']) for e in eps for i, r in enumerate(e['rounds']) if i))}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[0]) if a else 300, int(a[1]) if len(a) > 1 else 20261113, int(a[2]) if len(a) > 2 else 8)
