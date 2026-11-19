# -*- coding: utf-8 -*-
"""생성된 문제가 보장을 지키는지 본다.   python3 bench/verify.py [episodes 파일]

생성기(generate.py · fastgen.c)의 판단을 다시 쓰지 않고 engine 으로 처음부터 센다. 보장 목록은 generate.py 머리말."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine as E                                    # noqa: E402
from generate import BOARDS, CHANGE, COUPLE, ROUNDS, SHARE, change    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "episodes/episodes.json"
eps = json.loads(src.read_text())
assert len({e["buttons"] for e in eps}) == 1
NB = eps[0]["buttons"]
SEQS = E.sequences(NB, 4)
bad, chk, kinds = [], Counter(), Counter()


def exact(t, goal):
    return [p for p in SEQS if E.lines(E.run(t, p)) == list(goal)]


def shortest(ps):
    m = min(len(p) for p in ps)
    return [p for p in ps if len(p) == m]


def blocked(t, now, goal, old):
    """옛 금지를 지키면 못 하는 끝 판 고치기."""
    want = E.need(goal)
    seen, kept = set(), set()
    for p in now:
        end = E.run(t, p)
        pairs = {(c, end[c]) for c in range(9) if c not in want and c != old}
        seen |= pairs
        if not E.touches(t, p, old):
            kept |= pairs
    return seen - kept


def follows(prev_t, t, how):
    """t 가 prev_t 에서 how.change 의 칸을 바꾸고 how.perm 으로 버튼 순서를 섞은 판인가."""
    mid = [row[:] for row in prev_t]
    ch = how["change"]
    if len(ch) != CHANGE or len({(c["button"], c["light"]) for c in ch}) != CHANGE:
        return False
    for c in ch:
        if mid[c["button"]][c["light"]] != c["frm"] or c["frm"] == c["to"]:
            return False
        mid[c["button"]][c["light"]] = c["to"]
    return sorted(how["perm"]) == list(range(NB)) and [mid[p] for p in how["perm"]] == t


n_boards = n_sw = 0
for e in eps:
    if len(e["rounds"]) != ROUNDS:
        bad.append(f"{e['id']} 라운드 수")
    last_t, last_S = None, None
    for i, r in enumerate(e["rounds"]):
        g, L = r["goal"], r["forbidden"]
        want = E.need(g)
        tag = f"{e['id']} R{r['round']}"

        def ok(name, cond, why):
            if cond:
                chk[name] += 1
            else:
                bad.append(f"{tag} {why}")

        ok("목표는 한두 줄", len(g) in (1, 2), "목표 줄이 한두 개가 아님")
        ok("금지 전구는 목표 줄 밖", L not in want, "금지 전구가 목표 줄 위에 있음")
        ok("판 수", len(r["boards"]) == BOARDS, f"판이 {len(r['boards'])} 장")
        p = e["rounds"][i - 1] if i else None
        if p:
            Lp, Tp = p["forbidden"], p["goal"]
            ok("규칙 변경", L != Lp, "금지 전구가 직전과 같음")
            ok("목표 변경(뺌 · 더함)", change(g, Tp) in ("뺌", "더함"), "목표가 뺌 · 더함이 아닌 방식으로 바뀜")
            kinds[change(g, Tp)] += 1
            ok("직전 금지는 목표 줄 밖", Lp not in want, "직전 금지 전구가 지금 목표 줄 위에 있음")
            if i >= 2:
                ok("두 라운드 전 금지와도 다름", L != e["rounds"][i - 2]["forbidden"], "두 라운드 전 금지 전구로 돌아감")
        for j, b in enumerate(r["boards"]):
            n_boards += 1
            tag = f"{e['id']} R{r['round']} 판{j + 1}"
            t = b["table"]
            ok("버튼 모양", len(t) == NB and len({tuple(x) for x in t}) == NB and all(x.count(E.ON) >= 2 for x in t),
               "버튼 수 · 서로 다름 · 켜는 칸 둘 이상 중 하나가 어긋남")
            if last_t is None:
                ok("첫 판은 새로 뽑음", "from" not in b, "1 라운드 첫 판에 from 이 있음")
            else:
                ok("직전 판에서 4 칸 + 섞기", "from" in b and follows(last_t, t, b["from"]), "직전 판에서 4 칸 바꾸고 섞은 판이 아님")
            now = exact(t, g)
            S = {q for q in now if not E.touches(t, q, L)}
            ok("정답이 있음(3 번 이하)", bool(S) and min(map(len, S)) <= 3, "3 번 이하 정답이 없음")
            ok("기록한 정답이 통함", E.evaluate(t, g, L, b["shortest"])["ok"] and len(b["shortest"]) <= 3, "기록한 정답이 안 통함")
            dflt = shortest(now) if now else []
            ok("기록한 기본 답", tuple(b["default"]) in dflt, "기록한 규칙 없는 최단이 최단이 아님")
            if last_S is not None:
                ok("직전 판 정답은 이 판에서 틀림", not (S & last_S), "직전 판의 정답이 이 판에서도 통함")
            last_t, last_S = t, S
            if not p:
                ok("1R 기본 최단은 모두 금지 전구를 켬", all(E.touches(t, q, L) for q in dflt),
                   "규칙 없이 가장 짧은 답 중 금지 전구를 안 켜는 것이 있음")
                ok("1R 정답은 기본 최단보다 많아야 한 번 길다", min(map(len, S)) <= len(dflt[0]) + 1, "정답이 기본 최단보다 두 번 이상 김")
                continue
            n_sw += 1
            ok("직전 금지를 켜는 버튼 둘 이상", len(E.lighting(t, Lp)) >= 2, "직전 금지 전구를 켜는 버튼이 하나뿐")
            ok("기본 최단은 모두 직전 금지를 켜고 지금 금지는 안 켬",
               all(E.touches(t, q, Lp) and not E.touches(t, q, L) for q in dflt), "규칙 없는 최단이 어긋남")
            old = [q for q in exact(t, Tp) if len(q) <= 3]
            ok("옛 목표 답 절반 이상이 옛 금지를 피함", bool(old) and sum(not E.touches(t, q, Lp) for q in old) >= COUPLE * len(old),
               "옛 목표 3 번 이하 답 가운데 옛 금지를 피하는 것이 절반 미만")
            habit = [q for q in exact(t, Tp) if not E.touches(t, q, Lp)]
            ok("습관 답 3 번 이하", bool(habit) and min(map(len, habit)) <= 3, "습관 답이 없거나 4 번")
            hb = b["habit"]
            ok("기록한 습관 답 → 지속적 오류", E.lines(E.run(t, hb)) == list(Tp) and not E.touches(t, hb, Lp)
               and E.touches(t, hb, L) and len(hb) <= 3, "기록한 습관 답이 지속적 오류가 아님")
            rs = b["ruleStick"]
            ok("기록한 옛 규칙 답 → 지속적 오류", E.lines(E.run(t, rs)) == list(g) and not E.touches(t, rs, Lp)
               and E.touches(t, rs, L), "규칙만 옛것인 답이 지속적 오류가 아님")
            ex3 = [q for q in now if len(q) <= 3]
            ok("새 목표 답 1/3 넘게가 옛 금지를 피함", bool(ex3) and sum(not E.touches(t, q, Lp) for q in ex3) > SHARE * len(ex3),
               "새 목표 답 가운데 옛 금지를 피하는 몫이 1/3 이하")
            ok("가장 짧은 습관 답이 꺼진 목표 전구를 켰다 끄지 않음",
               not any(E.touches(t, q, c) and not E.run(t, q)[c] for q in (shortest(habit) if habit else []) for c in want),
               "가장 짧은 습관 답이 끝에 꺼진 목표 전구를 도중에 켬")
            ok("옛 금지를 지켜도 끝 판 고치기 가능", not blocked(t, now, g, Lp), "옛 금지를 지키면 못 하는 끝 판 고치기가 있음")
            ok("둘 다 피하면 줄 0", not any(E.lines(E.run(t, q)) for q in SEQS
                                        if not E.touches(t, q, Lp) and not E.touches(t, q, L)),
               "직전 · 지금 금지를 둘 다 피하고도 줄을 만들 수 있음")

print(f"문제 {len(eps)} · 판 {n_boards} (전환 라운드 판 {n_sw}): 어긋남 {len(bad)}")
for k, v in chk.items():
    print(f"  {k:34} {v}")
print(f"  바뀌는 방식 {dict(kinds)}")
print("  문제 없음" if not bad else "  " + "\n  ".join(bad[:20]))
sys.exit(1 if bad else 0)
