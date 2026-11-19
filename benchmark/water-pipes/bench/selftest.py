# -*- coding: utf-8 -*-
"""러너 로직을 모델 호출 없이 확인한다.   python3 bench/selftest.py

v3 은 제출마다 판이 바뀌므로 대본도 그 라운드에서 몇 번째 질문인지(= 판 번호)로 답을 고른다."""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import engine as E                                    # noqa: E402
import generate as G                                  # noqa: E402
import prompt as P                                    # noqa: E402
import runner as R                                    # noqa: E402
import score as SC                                    # noqa: E402

eps = json.loads((ROOT / "episodes/episodes.json").read_text())
ep = eps[0]


def habit(board, goal):
    """가장 적게 돌리는 배치를 타일마다 필요한 횟수만큼 이어 누른 답."""
    turns = E.assignments(board).get(frozenset(goal), [])
    best = min(turns, key=lambda t: (sum(t.values()), sorted(t.items())))
    return [i for i in sorted(best) for _ in range(best[i])]


class Script:
    """plan[i] 가 i+1 라운드에서 낼 전략. 판 j 의 기록에서 답을 고른다.
    now    지금 목표 · 지금 띠의 정답
    old    직전 띠로 지금 목표를 푼 답 (band · rule)
    habit  직전 목표를 습관대로(이어 누르기) 푼 답: 전환을 모르는 첫 제출 (band · both)
    repeat 직전 라운드에서 마지막으로 맞힌 답을 그대로
    junk   읽을 수 없는 답"""

    def __init__(self, plan):
        self.plan, self.round, self.at, self.i = plan, 1, 1, 0
        self.last = self.prev_last = None

    def ask(self, system, messages, state):
        if self.round != self.at:
            self.at, self.i, self.prev_last = self.round, 0, self.last
        how = self.plan[self.round - 1]
        if isinstance(how, tuple):
            old, times, new = how
            how = old if self.i < times else new
        spec = ep["rounds"][self.round - 1]
        prev = ep["rounds"][self.round - 2] if self.round > 1 else None
        b = spec["boards"][self.i]
        self.i += 1
        if how == "junk":
            return "I am not sure.", dict(stop_reason="end_turn")
        if how == "old" and prev:
            q = b["ruleStick"]
        elif how == "habit" and prev:
            q = habit(b["board"], prev["goal"])
        elif how == "repeat" and prev:
            q = self.prev_last
        else:
            q = b["shortest"]
            self.last = q
        return f"<answer>{' '.join(str(i + 1) for i in q)}</answer>", dict(stop_reason="end_turn")

    def close(self):
        pass


smoke = G.study(G.random_board(random.Random(3)))
print("판 연구 함수  %s" % ("통과" if smoke is None or len(smoke) == 3 else "실패"))
first = P.opening(ep["rounds"][0]["boards"][0]["board"])
print("프롬프트 검사  %s\n" % ("통과" if "[Fr] [Fy] [Fb]" in first and "the right flowers" in first else "실패"))
print(f"문제 {ep['id']}")
for r in ep["rounds"]:
    print(f"  R{r['round']}  목표 {','.join(r['goal']):18} 띠 {E.rule_name(r['rule']):4}"
          f"  판1 최단 {' '.join(str(i + 1) for i in r['boards'][0]['shortest'])}"
          f"  판2 최단 {' '.join(str(i + 1) for i in r['boards'][1]['shortest'])}")
print()
CASES = {
    "라운드마다 그 판의 정답": (["now"] * 5, dict(status="completed", subs=[2] * 5)),
    "직전 띠로 지금 목표 2번 → 정답": (["now"] + [("old", 2, "now")] * 4,
                                dict(status="completed", subs=[2] + [4] * 4, band=[2] * 4, rule=[2] * 4)),
    "직전 목표를 습관대로 2번 → 정답": (["now"] + [("habit", 2, "now")] * 4,
                                  dict(status="completed", subs=[2] + [4] * 4, band=[2] * 4, both=[2] * 4)),
    "읽을 수 없는 답 1번 → 정답": (["now"] + [("junk", 1, "now")] * 4, dict(status="completed", subs=[2] + [3] * 4)),
    "직전 정답 그대로 2번 → 정답": (["now"] + [("repeat", 2, "now")] * 4, dict(status="completed")),
}
stale = wrong = 0
for name, (plan, want) in CASES.items():
    log = R.play(Script(plan), ep, T=G.BOARDS, k=2, say=lambda line: None)
    for c in log["calls"]:                              # 묻는 판 = 채점하는 판 = 그 라운드의 제출 순번
        stale += c["board"] != c["submission"] - 1
        stale += E.text(ep["rounds"][c["round"] - 1]["boards"][c["board"]]["board"]) not in c["prompt"]
    got = dict(status=log["status"], subs=[r["submissions"] for r in log["rounds"]],
               **{k: [r[k] for r in log["rounds"][1:]] for k in R.KINDS})
    miss = {k: (got[k], v) for k, v in want.items() if got[k] != v}
    wrong += bool(miss)
    pm = SC.problem(log)
    print(f"  {name}  {'통과' if not miss else '실패 ' + str(miss)}")
    print(f"    상태 {log['status']}  제출 {got['subs']}  지표 {pm['band'] if pm['band'] is None else round(pm['band'], 3)}")
    print("    " + "  ".join(f"{R.TAGNAME[k]} {got[k]}" for k in R.KINDS))
print(f"\n판 검사: 묻는 판과 채점하는 판이 어긋난 질문 {stale}개 " + ("(통과)" if not stale else "(실패)"))
print("대본 검사 " + ("통과" if not wrong else f"실패 {wrong}"))
