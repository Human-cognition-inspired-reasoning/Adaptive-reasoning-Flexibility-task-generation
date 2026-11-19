# -*- coding: utf-8 -*-
"""3×3 빙고 전구 5라운드 엔진: 목표는 **어느 줄**, 규칙은 금지 전구, 판정은 물길처럼 틀린 전구를 섞어 알린다.

v3: 조건은 제출마다, 규칙과 목표는 라운드마다 바뀐다(판 만드는 법은 generate.py).
    조건  배선표(버튼마다 전구별 ON / OFF / KEEP)   제출마다 4 칸 바뀌고 버튼 순서가 섞임, 보임
    규칙  금지 전구 하나: 누르는 도중 켜면 안 됨   매 라운드 변경, 숨김
    목표  완성할 줄 한두 개(정확히 그 줄만)          매 라운드 변경, 숨김
매 시도는 전구가 전부 꺼진 상태에서 시작한다. 전구 번호는

    1 2 3
    4 5 6
    7 8 9

줄은 전구 번호로 부른다(`1-2-3`, `1-5-9`).

왜 목표를 '몇 줄' 에서 '어느 줄' 로 바꿨나 (사용자와 정함, 2026-10-26)
  물길의 `Wrong at tiles` 는 회전 오류와 띠 위반을 한 목록에 섞는다. 회전 오류가 타일마다 있으니 띠 위반이
  그 사이에 숨는다. 줄 수 목표에는 전구마다의 목표 오류가 없어 금지 전구가 틀린 전구로 나오면 늘 규칙 위반으로
  드러났다(`전구-빙고-금지전구` 1~5 차). 목표를 어느 줄로 하면 전구마다 목표 오류가 생긴다.

판정: 완성된 줄, 그리고 **틀린 전구**를 이유 없이 한 목록으로.
    · 목표 줄의 전구인데 끝에 꺼져 있음
    · 목표가 아닌 줄이 완성됐을 때, 그 줄에서 목표 줄 밖에 있는 전구
    · 금지 전구를 켠 적이 있음: 뒤에서 다시 꺼도 틀린 전구로 알린다
    `Bingo lines: 1-2-3 and 1-5-9. Wrong at lights 5 9. Not the answer.`
    `Bingo lines: 1-2-3. Wrong at light 5. Not the answer.`      줄은 맞고 금지 전구를 켰다가 끔
    `Bingo lines: 1-2-3. That is the answer.`
  목표 오류는 '끝 판' 에서, 금지는 '과정' 에서 따진다. 틀린 전구를 끝 판에서 고쳐도(끄거나 켜도) 금지
  위반은 남는다: 물길에서 회전을 고쳐도 띠 위반이 남는 것과 같은 이치다. 틀린 제출에는 늘 틀린 전구가
  하나 이상 있다(목표 오류가 없으면 금지 전구라도).
"""
from itertools import permutations

ON, OFF, KEEP = "ON", "OFF", "KEEP"
EFFECTS = (ON, OFF, KEEP)
CELLS = 9
LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8),           # 가로
         (0, 3, 6), (1, 4, 7), (2, 5, 8),           # 세로
         (0, 4, 8), (2, 4, 6))                      # 대각선
NAMES = tuple("-".join(str(c + 1) for c in l) for l in LINES)


def apply_press(state, row):
    """버튼 하나를 누른다. ON 과 OFF 는 현재 값을 무시하고 덮어쓴다."""
    return tuple(1 if e == ON else 0 if e == OFF else s for s, e in zip(state, row))


def run(table, presses):
    """전부 꺼진 상태에서 순서대로 누른다."""
    state = (0,) * CELLS
    for b in presses:
        state = apply_press(state, table[b])
    return state


def lines(state):
    """완성된 줄의 번호(LINES 의 번호)."""
    return [i for i, l in enumerate(LINES) if all(state[c] for c in l)]


def need(goal):
    """목표 줄들이 지나는 전구: 끝에 모두 켜져 있어야 한다."""
    return {c for i in goal for c in LINES[i]}


def lighting(table, light):
    """그 전구를 켜는 버튼들."""
    return {b for b, row in enumerate(table) if row[light] == ON}


def touches(table, presses, light):
    """그 전구를 켜는 누름이 하나라도 있나: 순서와 무관하게 어떤 버튼을 썼는지로 정해진다.
    전부 꺼진 채 시작하므로 '누르는 도중 한 번이라도 켜졌나' 와 같다."""
    return any(table[b][light] == ON for b in presses)


def off_goal(state, goal):
    """끝 판에서 목표와 어긋난 전구: 목표 줄인데 꺼진 전구와, 목표가 아닌 줄을 완성한 전구(그 줄에서
    목표 줄 밖에 있는 것). 목표 아닌 줄은 목표 줄과 많아야 두 칸 겹치므로 밖의 전구가 늘 하나 이상 있다.
    빈 목록이면 완성된 줄이 정확히 목표다."""
    want = need(goal)
    missing = {c for c in want if not state[c]}
    extra = {c for i in lines(state) if i not in goal for c in LINES[i] if c not in want}
    return sorted(missing | extra)


def evaluate(table, goal, forbidden, presses):
    """제출 하나를 판정한다. status: solved · rule · goal · both · empty
      rule   줄은 정확히 목표, 금지 전구를 켬
      goal   금지 전구는 안 켰고 줄이 목표와 다름
      both   둘 다
    `flagged` 는 모델에게 알리는 전구(목표 오류 ∪ 켠 금지 전구), `goalWrong` 은 그중 목표 쪽,
    `violations` 는 금지 전구를 켠 누름의 순번(로그용)."""
    got = run(table, presses)
    wrong = off_goal(got, goal)
    bad = [j for j, b in enumerate(presses) if table[b][forbidden] == ON]
    flagged = sorted(set(wrong) | ({forbidden} if bad else set()))
    if not presses:
        status = "empty"
    elif not wrong and not bad:
        status = "solved"
    else:
        status = "rule" if not wrong else ("both" if bad else "goal")
    return dict(ok=status == "solved", status=status, result=got, lines=lines(got),
                goalWrong=wrong, violations=bad, flagged=flagged)


def sequences(n_buttons, maxlen=4):
    """서로 다른 버튼을 누르는 순서 전부(길이 1..maxlen). 덮어쓰기에서는 같은 버튼을 다시 누르면
    앞의 누름이 뜻을 잃으므로 결과는 '마지막 등장 순서' 로 정해진다."""
    out = []
    for n in range(1, maxlen + 1):
        out.extend(permutations(range(n_buttons), n))
    return out


def label(i):
    return chr(ord("A") + i)


def parse_label(ch):
    return ord(ch.upper()) - ord("A")
