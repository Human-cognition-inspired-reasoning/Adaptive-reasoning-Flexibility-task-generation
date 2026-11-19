# -*- coding: utf-8 -*-
"""판정 · 문제 만들기 · 확인 · 러너 · 채점을 모델 호출 없이 확인한다.   python3 bench/selftest.py

판 사진 묶음이 없어도 돈다: 씨앗 1~60 구조물로 메모리 안에 묶음을 만들어 쓴다(사진 파일은 만들지 않음).
"""
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import engine as E                                    # noqa: E402
import generate as G                                  # noqa: E402
import prompt as P                                    # noqa: E402
import runner as R                                    # noqa: E402
import score as SC                                    # noqa: E402
import structure as S                                 # noqa: E402
import verify as V                                    # noqa: E402

fails = []


def check(name, cond):
    if not cond:
        fails.append(name)


class Scripted:
    def __init__(self, episode, plan):
        self.episode, self.plan, self.round, self.photo, self.count = episode, plan, None, None, {}

    def ask(self, system, messages, state):
        spec = self.episode["rounds"][self.round - 1]
        i = self.count[self.round] = self.count.get(self.round, 0) + 1
        return self.plan(spec, self.photo, i), {"complete": True}


def reply(g):
    return "Here it is.\n<answer>\n" + "\n".join(" ".join(r) for r in g) + "\n</answer>"


def oracle(spec, ph, i=None):
    return reply(ph["views"][spec["side"]][E.rule_of(spec)])


def habit(spec, ph, i):
    """라운드 1 은 못 읽는 답 · 정답 · 정답. 라운드 2~ 의 첫 답은 지금 방향을 직전 색으로 센 격자, 그다음은 정답."""
    if spec["round"] == 1:
        return "Is it the front side?" if i == 1 else oracle(spec, ph)
    if i == 1:
        return reply(ph["views"][spec["side"]][E.old_rule_of(spec)])
    return oracle(spec, ph)


def stale(spec, ph, i):
    """라운드 2~ 의 첫 답은 직전 방향 · 직전 색 그대로."""
    if spec["round"] > 1 and i == 1:
        return reply(ph["views"][spec["oldSide"]][E.old_rule_of(spec)])
    return oracle(spec, ph)


def fake_pool(n=60, scheme="color", empty="."):
    """씨앗 1 ~ 로 메모리 안의 사진 묶음. kind 는 12 격자가 모두 다른 구조물만 n 개."""
    pool, seed = {}, 0
    while len(pool) < n:
        seed += 1
        H, C = S.generate(seed)
        if scheme == "kind" and not S.ok_rules(H, C):
            continue
        name = f"x{seed:03d}"
        views = S.all_rule_views(H, C, empty) if scheme == "kind" else S.all_views(H, C)
        pool[name] = dict(name=name, image=f"pool/photos/{name}.png", H=H, C=C, views=views)
    return pool


def main():
    pool = fake_pool()
    # 따로 짠 격자 계산(verify.grid)과 structure.view 가 같다
    for ph in pool.values():
        same = all(V.grid(ph["H"], ph["C"], d, k) == ph["views"][d][k] for d in E.SIDES for k in E.COLORS)
        check(f"따로 짠 격자 {ph['name']}", same and V.grid(ph["H"], ph["C"], "front") == E.total_view(ph["H"], ph["C"], "front"))
    # 손으로 푼 예: 앞 왼쪽 기둥(빨 · 파), 가운데(초), 뒤 오른쪽(파 · 파 · 빨)
    H = [[0, 0, 3], [0, 1, 0], [2, 0, 0]]
    C = [[[], [], ["blue", "blue", "red"]], [[], ["green"], []], [["red", "blue"], [], []]]
    check("앞에서 본 파랑", S.view(H, C, "front", "blue") == [[".", ".", "0"], ["1", ".", "1"], ["0", "0", "1"]])
    check("오른쪽에서 본 빨강", S.view(H, C, "right", "red") == [[".", ".", "1"], ["0", ".", "0"], ["1", "0", "0"]])
    check("왼쪽에서 본 블록 수", E.total_view(H, C, "left") == [["1", ".", "."], ["1", ".", "1"], ["1", "1", "1"]])
    # 답 읽기
    want = [["1", "0", "."], [".", "2", "3"], ["0", "0", "1"]]
    check("읽기", R.parse("<answer>\n1 0 .\n. 2 3\n0 0 1\n</answer>") == want)
    check("쉼표 · 괄호 · | 읽기", R.parse("<ANSWER>[1, 0, .] | [., 2, 3] | [0, 0, 1]</ANSWER>") == want)
    check("마지막 태그", R.parse("<answer>0 0 0 0 0 0 0 0 0</answer> no, <answer>10.\n.23\n001</answer>") == want)
    check("못 읽음", R.parse("1 0 .") is None and R.parse("<answer>1 0 . . 2 3 0 0</answer>") is None
          and R.parse("<answer>1 0 x . 2 3 0 0 1</answer>") is None and R.parse("<answer>1 0 - . 2 3 0 0 1</answer>") is None)
    check("4~9 도 읽고 틀린 칸으로", R.parse("<answer>1 0 4 . 2 3 0 0 1</answer>") == [["1", "0", "4"], [".", "2", "3"], ["0", "0", "1"]]
          and E.evaluate(R.parse("<answer>1 0 4 . 2 3 0 0 1</answer>"), {"front": {"red": want}}, "front", "red") == [(0, 2)])
    # 판정 · 피드백
    ph = pool["x001"]
    for d, k in itertools.product(E.SIDES, E.COLORS):
        check(f"정답 {d} {k}", E.evaluate(ph["views"][d][k], ph["views"], d, k) == [])
    g = [row[:] for row in want]
    check("틀린 칸", E.evaluate(g, {"front": {"red": [["1", "1", "."], [".", "2", "3"], ["0", "0", "0"]]}}, "front", "red") == [(0, 1), (2, 2)])
    check("피드백 글", P.say(g, [(0, 1), (2, 2)]) == "Wrong at the cells marked *.\n1  0* .\n.  2  3\n0  0  1*\nNot the answer."
          and P.say(g, []) == "That is the answer.")
    # 지속적 오류 · 우연 기준
    for ph in list(pool.values())[:20]:
        vw = ph["views"]
        for old, new in itertools.permutations(E.COLORS, 2):
            for d in E.SIDES:
                check("옛색 격자는 지속적 오류", all(E.perseverative(vw[s][old], vw, old, new, d) for s in E.SIDES))
                check("정답은 지속적 오류 아님", not E.perseverative(vw[d][new], vw, old, new, d))
                check("다른 색 격자는 아님", not any(E.perseverative(vw[s][k], vw, old, new, d)
                                               for s in E.SIDES for k in E.COLORS if k != old))
    for ph in list(pool.values())[:6]:                 # 우연 기준: '.' 꼴이 같은 모든 격자를 세어 맞춰 본다
        vw = ph["views"]
        for old, new in itertools.permutations(E.COLORS, 2):
            d = "front"
            for s in E.SIDES:
                ans = vw[s][old]
                pos = [(i, j) for i, j in E.CELLS if ans[i][j] != "."]
                if len(pos) > 7:
                    continue
                hit = 0
                for vals in itertools.product("0123", repeat=len(pos)):
                    t = [["."] * 3 for _ in range(3)]
                    for (i, j), x in zip(pos, vals):
                        t[i][j] = x
                    hit += E.perseverative(t, vw, old, new, d)
                check(f"우연 기준 {ph['name']} {s} {old}", abs(hit / 4 ** len(pos) - E.chance(ans, vw, old)) < 1e-12)
    # 문제 만들기 · 확인
    run_scheme(pool, "color", g)
    kpool = fake_pool(60, "kind")
    for ph in list(kpool.values())[:30]:
        check(f"새 규칙 격자 따로 계산 {ph['name']}", all(V.rgrid(ph["H"], ph["C"], d, r) == ph["views"][d][r] for d in E.SIDES for r in S.RULES))
    run_scheme(kpool, "kind", g)
    # 빈칸을 0 으로(2026-11-12 사용자 '빈칸0으로')
    kpool0 = fake_pool(60, "kind", "0")
    check("빈칸 0 격자", all("." not in "".join(sum(ph["views"][d][r], [])) for ph in kpool0.values() for d in E.SIDES for r in S.RULES)
          and all(V.rgrid(ph["H"], ph["C"], d, r, "0") == ph["views"][d][r] for ph in kpool0.values() for d in E.SIDES for r in S.RULES))
    check("빈칸 0 지시문", "In each grid cell write a number." in " ".join(P.opening("x.jpg", False, "0")[0]["text"].split())
          and "no block at that level" not in " ".join(P.opening("x.jpg", False, "0")[0]["text"].split())
          and "write . if that line" in " ".join(P.opening("x.jpg")[0]["text"].split()))
    for ph in list(kpool0.values())[:4]:                # 우연 기준(빈칸 0): 0 칸은 두고 나머지를 1~3 에서 뽑은 모든 격자를 센다
        vw = ph["views"]
        for old, new in itertools.permutations(S.RULES, 2):
            for sd in E.SIDES:
                ans = vw[sd][old]
                pos = [(i, j) for i, j in E.CELLS if ans[i][j] != "0"]
                if len(pos) > 7:
                    continue
                hit = 0
                for vals in itertools.product("123", repeat=len(pos)):
                    t = [["0"] * 3 for _ in range(3)]
                    for (i, j), x in zip(pos, vals):
                        t[i][j] = x
                    hit += E.perseverative(t, vw, old, new, "front")
                check(f"우연 기준 빈칸 0 {ph['name']} {sd} {old}", abs(hit / 3 ** len(pos) - E.chance(ans, vw, old, "0")) < 1e-12)
    run_scheme(kpool0, "kind", g, "0")
    # 기본(직전 사진을 다시 붙이지 않음): 설명문 · 사진 · 질문, 그다음은 피드백 · 사진 · 질문
    eps0 = G.build(kpool0, 1, 3, prefix="N", scheme="kind", empty="0")
    log = R.play(Scripted(eps0[0], habit), eps0[0], kpool0, 10, 2, lambda s: None, "cells", empty="0")
    check("기본은 직전 사진 없음", log["showPrev"] is False and all(sum(str(x).startswith("image:") for x in c["prompt"]) == 1 for c in log["calls"])
          and log["calls"][1]["prompt"][0] == P.UNREADABLE and log["calls"][1]["prompt"][1].startswith("image:"))
    # 손으로 푼 새 규칙 예: 앞 왼쪽 기둥(빨 · 파), 가운데(초), 뒤 오른쪽(파 · 파 · 빨)
    H = [[0, 0, 3], [0, 1, 0], [2, 0, 0]]
    C = [[[], [], ["blue", "blue", "red"]], [[], ["green"], []], [["red", "blue"], [], []]]
    check("앞에서 몇째(앞)", S.rule_view(H, C, "front", "near") == [[".", ".", "3"], ["1", ".", "3"], ["1", "2", "3"]])
    check("앞에서 몇째(오른쪽)", S.rule_view(H, C, "right", "near") == [[".", ".", "1"], ["3", ".", "1"], ["3", "2", "1"]])
    H2, C2 = [[1, 0, 0], [1, 0, 0], [1, 0, 0]], [[["red"], [], []], [["blue"], [], []], [["red"], [], []]]
    check("블록 수 · 색 가짓수 · 몇째", S.rule_view(H2, C2, "front", "count")[2][0] == "3" and S.rule_view(H2, C2, "front", "colors")[2][0] == "2"
          and S.rule_view(H2, C2, "front", "near")[2][0] == "1" and S.rule_view(H2, C2, "back", "near")[2][2] == "1")

    print("통과" if not fails else "실패 " + str(len(fails)))
    for f in fails[:40]:
        print("  " + f)
    sys.exit(1 if fails else 0)


def run_scheme(pool, scheme, g, empty="."):
    """문제 만들기 · 확인 · 러너 · 채점을 한 판(scheme)으로."""
    key = "rule" if scheme == "kind" else "color"
    eps = G.build(pool, 3, 7, prefix="T", scheme=scheme, empty=empty)
    for ep in eps:
        bad, _ = V.check(ep, pool, scheme=scheme, empty=empty)
        check(f"verify {scheme} {ep['id']} {bad[:2]}", not bad)
    broken = dict(eps[0], rounds=[dict(r) for r in eps[0]["rounds"]])
    broken["rounds"][2][key] = broken["rounds"][1][key]
    check(f"verify 가 같은 규칙을 잡음 {scheme}", any("직전과 같음" in b for b in V.check(broken, pool, scheme=scheme)[0]))
    # 이름표가 있는 사진이면 첫 글에 이름표 문장
    lab_pool = {k: dict(v, labels=True) for k, v in pool.items()}
    log = R.play(Scripted(eps[0], oracle), eps[0], lab_pool, 10, 2, lambda s: None, "cells", True, empty)
    check("이름표 문장", log["calls"][0]["prompt"][0] == P.opening("x", True, empty)[0]["text"] and "FRONT, BACK, LEFT and RIGHT" in " ".join(P.HOW_LABELS.split())
          and "FRONT" not in P.HOW)
    # 러너
    for ep in eps:
        log = R.play(Scripted(ep, oracle), ep, pool, 10, 2, lambda s: None, "cells", True, empty)
        check(f"정답 대본 완주 {ep['id']}", log["status"] == "completed" and all(r["submissions"] == 2 for r in log["rounds"]))
        check("첫 질문은 설명 + 사진", log["calls"][0]["prompt"][0] == P.opening("x", False, empty)[0]["text"] and log["calls"][0]["prompt"][1].startswith("image:"))
        imgs = lambda c: [x for x in c["prompt"] if str(x).startswith("image:")]
        check("제출마다 새 사진, 둘째부터 직전 사진을 앞에", all(imgs(c)[-1] == "image:" + Path(pool[c["photo"]]["image"]).name for c in log["calls"])
              and len({c["photo"] for c in log["calls"]}) == len(log["calls"])
              and all(imgs(c)[0] == "image:" + Path(pool[log["calls"][i - 1]["photo"]]["image"]).name and len(imgs(c)) == 2
                      for i, c in enumerate(log["calls"]) if i)
              and log["calls"][1]["prompt"][1] == "That is the answer.")

    for ep in eps:
        log = R.play(Scripted(ep, habit), ep, pool, 10, 2, lambda s: None, "cells", True, empty)
        r1 = log["rounds"][0]
        check(f"못 읽음 {ep['id']}", r1["unparsed"] == 1 and r1["submissions"] == 3 and log["calls"][1]["prompt"][1] == P.UNREADABLE)
        check(f"옛규칙 깃발 {scheme} {ep['id']}", all(r["rule"] == 1 and r["near"] == 1 and r["side"] == 0 and r["both"] == 0
                                         for r in log["rounds"][1:]))
        w = next(c for c in log["calls"] if c["round"] == 2 and c["submission"] == 2)
        check("틀린 뒤 피드백", w["prompt"][1].startswith("Wrong at the cells marked *.\n") and w["prompt"][1].endswith("\nNot the answer."))
        p = SC.problem(log, ep, pool, empty)
        check(f"채점 {ep['id']} {p['per']}", abs(p["per"] - 1 / 3) < 1e-9 and 0 < p["chance"] < p["per"]
              and abs(p["after"] - 1 / 3) < 1e-9 and abs(p["ref"]["near"] - 1 / 3) < 1e-9)
    check("틀린 칸 수 피드백", P.say(g, [(0, 1), (2, 2)], "count") == "2 cells are wrong. Not the answer."
          and P.say(g, [(0, 1)], "count") == "1 cell is wrong. Not the answer." and P.say(g, [], "count") == "That is the answer.")
    log = R.play(Scripted(eps[0], habit), eps[0], pool, 10, 2, lambda s: None, "count", True, empty)
    w = next(c for c in log["calls"] if c["round"] == 2 and c["submission"] == 2)
    check("러너가 틀린 칸 수만 알림", log["feedback"] == "count" and w["prompt"][1].endswith("wrong. Not the answer.")
          and "*" not in w["prompt"][1] and all(r["rule"] == 1 for r in log["rounds"][1:]))
    for ep in eps:
        log = R.play(Scripted(ep, stale), ep, pool, 10, 2, lambda s: None, "cells", True, empty)
        check(f"직전 답 그대로 {ep['id']}", all(r["rule"] == r["side"] == r["both"] == 1 for r in log["rounds"][1:]))
        p = SC.problem(log, ep, pool, empty)
        check(f"직전 답 채점 {ep['id']}", abs(p["per"] - 1 / 3) < 1e-9 and p["ref"]["both"] > 0)


if __name__ == "__main__":
    main()
