# -*- coding: utf-8 -*-
"""러너 로직을 모델 호출 없이 확인한다.   python3 bench/selftest.py

대본은 라운드별로 준다. 라운드 번호는 러너가 라운드를 시작할 때 driver.round 에 넣어 준다. v3 은 제출마다 판이 바뀌므로
대본도 그 라운드에서 몇 번째 질문인지(= 판 번호)로 답을 고른다.
"""
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import engine as E                                    # noqa: E402
import generate as G                                  # noqa: E402
import prompt as P                                    # noqa: E402
import runner as R                                    # noqa: E402
import score as SC                                    # noqa: E402

ep = json.loads((ROOT / "episodes/episodes.json").read_text())[0]


class Script:
    """plan[i] 가 i+1 라운드에서 낼 전략. 판 j 의 기록에서 답을 고른다.
    now      지금 금지 전구를 안 켜고 지금 목표 (정답)
    rule     직전 금지만 지켜 지금 목표: 규칙만 옛것 (forbid · rule)
    habit    직전 금지만 지켜 직전 목표: 전환을 모르고 직전처럼 (forbid · both)
    default  규칙 없이 가장 짧은 답: 전환 라운드에서는 정답(옛 규칙을 버린 모델)
    repeat   직전 라운드에서 마지막으로 맞힌 답을 글자 그대로
    junk     읽을 수 없는 답"""

    def __init__(self, plan):
        self.plan, self.round, self.at, self.i = plan, 1, 1, 0
        self.last = self.prev_last = None

    def ask(self, system, messages, state):
        if self.round != self.at:
            self.at, self.i, self.prev_last = self.round, 0, self.last
        how = self.plan[self.round - 1]
        if isinstance(how, tuple):                     # (옛전략, 몇 번, 새전략)
            old, times, new = how
            how = old if self.i < times else new
        board = ep["rounds"][self.round - 1]["boards"][self.i]
        self.i += 1
        if how == "junk":
            return "I am not sure.", dict(stop_reason="end_turn")
        pick = dict(now=board["shortest"], rule=board.get("ruleStick"), habit=board.get("habit"),
                    default=board["default"], repeat=self.prev_last)[how]
        if how == "now":
            self.last = pick
        return f"<answer>{' '.join(E.label(i) for i in pick)}</answer>", dict(stop_reason="end_turn")

    def close(self):
        pass


# 판정 단위 검사: 손으로 만든 배선 (A 1-2-3, B 4-5-6, C 1-4-7, D 1-5-9, E 5 끄기)
K, ON, OFF = E.KEEP, E.ON, E.OFF
row = lambda on=(), off=(): [ON if c in on else OFF if c in off else K for c in range(9)]   # noqa: E731
T0 = [row(on=(0, 1, 2)), row(on=(3, 4, 5)), row(on=(0, 3, 6)), row(on=(0, 4, 8)), row(off=(4,))]
units = [   # (이름, 판정, 정답?, 틀린 전구, status)   전구와 줄은 0 부터 센 번호
    ("목표 1-2-3 · 금지 9 안 켬", E.evaluate(T0, [0], 8, [0]), True, [], "solved"),
    ("1-5-9 도 완성 · 금지 5 켬 → 5 9", E.evaluate(T0, [0], 4, [0, 3]), False, [4, 8], "both"),
    ("줄은 맞고 금지 5 를 켰다 끔 → 5", E.evaluate(T0, [0], 4, [0, 3, 4]), False, [4], "rule"),
    ("4-5-6 만 완성 → 1~6", E.evaluate(T0, [0], 8, [1]), False, [0, 1, 2, 3, 4, 5], "goal"),
    ("두 줄 목표 1-2-3 + 1-4-7", E.evaluate(T0, [0, 3], 4, [0, 2]), True, [], "solved"),
    ("빈 답 → 목표 줄 전구", E.evaluate(T0, [0], 8, []), False, [0, 1, 2], "empty"),
]
fails = [name for name, res, ok, fl, st in units
         if res["ok"] != ok or res["flagged"] != fl or res["status"] != st]
SAYS = [(E.evaluate(T0, [0], 4, [0, 3]), "Bingo lines: 1-2-3 and 1-5-9. Wrong at lights 5 9. Not the answer."),
        (E.evaluate(T0, [0], 4, [0, 3, 4]), "Bingo lines: 1-2-3. Wrong at light 5. Not the answer."),
        (E.evaluate(T0, [0], 8, [0]), "Bingo lines: 1-2-3. That is the answer."),
        (E.evaluate(T0, [0], 8, []), "Bingo lines: none. Wrong at lights 1 2 3. Not the answer.")]
fails += [f"문구: {want}" for res, want in SAYS if P.say(res) != want]
ROWS = [((E.evaluate(T0, [0], 4, [0, 3]), "rows"), "Bingo lines: 1-2-3 and 1-5-9. Wrong on lines 4-5-6 and 7-8-9. Not the answer."),
        ((E.evaluate(T0, [0], 4, [0, 3, 4]), "rows"), "Bingo lines: 1-2-3. Wrong on line 4-5-6. Not the answer."),
        ((E.evaluate(T0, [0], 4, [0, 3]), "inrows"), "Bingo lines: 1-2-3 and 1-5-9. The wrong lights are in the middle and bottom rows. Not the answer."),
        ((E.evaluate(T0, [0], 4, [0, 3, 4]), "inrows"), "Bingo lines: 1-2-3. The wrong lights are in the middle row. Not the answer.")]
ROWS += [((E.evaluate(T0, [0], 4, [0, 3]), "count"), "Bingo lines: 1-2-3 and 1-5-9. Wrong at 2 lights. Not the answer."),
         ((E.evaluate(T0, [0], 4, [0, 3, 4]), "count"), "Bingo lines: 1-2-3. Wrong at 1 light. Not the answer.")]
fails += [f"문구({mode}): {want}" for (res, mode), want in ROWS if P.say(res, mode) != want]
print("판정 단위 검사  %s" % ("통과" if not fails else "실패: " + ", ".join(fails)))
for res, _ in SAYS:
    print("  " + P.say(res))

def c_smoke():
    """C 생성기로 문제 하나를 만들어 모양을 본다(보장 검사는 verify.py)."""
    exe = ROOT / "bench/fastgen"
    if not exe.exists() or exe.stat().st_mtime < (ROOT / "bench/fastgen.c").stat().st_mtime:
        subprocess.run(["clang", "-O2", "-o", str(exe), str(ROOT / "bench/fastgen.c")], check=True)
    out = subprocess.run([str(exe), str(G.BUTTONS), "1", "3", str(G.BOARDS), "7", "0"], capture_output=True, text=True,
                         check=True, timeout=300)
    rs = json.loads(out.stdout)["rounds"]
    return len(rs) == G.ROUNDS and all(len(r["boards"]) == G.BOARDS for r in rs)


def py_smoke(rng):
    """파이썬 생성기로 1 라운드 하나(전환 라운드 첫 판은 드물어 파이썬으로는 오래 걸린다)."""
    got = G.one_round(rng, None, None, None, set(), size=1)
    return bool(got) and len(got[0]["boards"]) == G.BOARDS


print("생성기 연기 검사  파이썬 1 라운드 %s · C 한 문제 %s" % ("통과" if py_smoke(random.Random(1)) else "실패",
                                              "통과" if c_smoke() else "실패"))
first = P.opening(ep["rounds"][0]["boards"][0]["table"])
print("프롬프트 검사  %s" % ("통과" if "Lighting the right lines is not enough" in first
                          and "Here is what the buttons do:" in first and "<answer>A" not in first else "실패"))
print(f"\n{first}\n")
print(f"문제 {ep['id']}")
for r in ep["rounds"]:
    b = r["boards"][0]
    print(f"  R{r['round']}  목표 {' + '.join(E.NAMES[i] for i in r['goal'])}  금지 {r['forbidden'] + 1}번"
          f"   판1 정답 {' '.join(E.label(i) for i in b['shortest'])}"
          f"   판2 정답 {' '.join(E.label(i) for i in r['boards'][1]['shortest'])}"
          + (f"   판1 습관 {' '.join(E.label(i) for i in b['habit'])}" if "habit" in b else ""))
print()
CASES = {
    "라운드마다 그 판의 정답": (["now"] * 5, dict(status="completed", subs=[2] * 5)),
    "지금 목표를 직전 금지만 지켜 2번 → 정답": (["now"] + [("rule", 2, "now")] * 4,
                                     dict(status="completed", subs=[2] + [4] * 4, forbid=[2] * 4, rule=[2] * 4)),
    "직전 목표를 직전 금지만 지켜 2번 → 정답": (["now"] + [("habit", 2, "now")] * 4,
                                     dict(status="completed", subs=[2] + [4] * 4, forbid=[2] * 4, both=[2] * 4)),
    "규칙 없이 가장 짧은 답 → 전환 라운드에서는 정답": (["now"] + ["default"] * 4, dict(status="completed", subs=[2] * 5)),
    "읽을 수 없는 답 1번 → 정답": (["now"] + [("junk", 1, "now")] * 4, dict(status="completed", subs=[2] + [3] * 4)),
    "직전 정답 글자 그대로 2번 → 정답": (["now"] + [("repeat", 2, "now")] * 4, dict(status="completed")),
}
stale = wrong = 0
for name, (plan, want) in CASES.items():
    log = R.play(Script(plan), ep, T=10, k=2, say=lambda line: None)
    for c in log["calls"]:                              # 묻는 판 = 채점하는 판 = 그 라운드의 제출 순번
        stale += c["board"] != c["submission"] - 1
        stale += P.wiring(ep["rounds"][c["round"] - 1]["boards"][c["board"]]["table"]) not in c["prompt"]
    got = dict(status=log["status"], subs=[r["submissions"] for r in log["rounds"]],
               **{k: [r[k] for r in log["rounds"][1:]] for k in R.KINDS})
    miss = {k: (got[k], v) for k, v in want.items() if got[k] != v}
    wrong += bool(miss)
    pm = SC.problem(log)
    print(f"  {name}  {'통과' if not miss else '실패 ' + str(miss)}")
    print(f"    상태 {log['status']}  제출 {got['subs']}  지표 {pm['per'] if pm['per'] is None else round(pm['per'], 3)}")
    print("    " + "  ".join(f"{R.TAGNAME[k]} {got[k]}" for k in R.KINDS))
print(f"\n조건 검사: 묻는 판과 채점하는 판이 어긋난 질문 {stale}개 " + ("(통과)" if not stale else "(실패)"))
print("대본 검사 " + ("통과" if not wrong and not fails else f"실패 {wrong}"))
