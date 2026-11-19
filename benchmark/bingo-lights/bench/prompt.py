# -*- coding: utf-8 -*-
"""모델에게 보내는 글: 3×3 빙고 전구, 목표는 어느 줄, 규칙은 금지 전구.

글은 `전구-빙고-금지전구` 에서 목표 문장과 판정만 바꿨다. 판정은 물길-세축을 따른다.
  · 과제 문장은 `light up the right bingo lines`: 목표가 줄이라는 틀만 알리고 어느 줄인지는
    숨긴다. 물길의 `water reaches the right flowers` 에 해당한다.
  · 줄은 전구 번호로 부른다(`1-5-9`). 첫 글에서 한 번 알린다.
  · 'Lighting the right lines is not enough': 숨은 규칙이 있다는 유일한 단서다.
  · 판정 앞에 **완성된 줄**을 붙인다: `Bingo lines: 1-2-3 and 1-5-9.` 물길의 `Water reached red
    and blue.` 에 해당한다. 틀리면 `Wrong at lights 5 9. Not the answer.`: 목표 오류와 금지 위반을 가리지
    않는다(engine.evaluate). 물길의 `Wrong at tiles 3 6.` 과 같은 꼴이다.

답 형식의 보기는 버튼 글자를 쓰지 않는다.

숨기는 것은 목표(어느 줄)와 금지 전구 두 가지다. 둘 다 있다는 사실만 알리고, 무엇인지와 라운드마다
바뀐다는 것은 말하지 않는다. 조건(배선표)은 매 질문 바로 앞에 다시 붙인다. v3 에서는 그 배선표가 제출마다 다르지만
바뀐다는 말은 하지 않는다(블록 이미지 과제에서 '사진이 매번 새로 나온다' 를 뺀 것과 같다).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine as E                                    # noqa: E402

LETTERS = "ABCDEFGH"
ASK = ("Which buttons do you press, in order? "
       "Answer with the button letters in order, separated by spaces, inside <answer></answer> tags.")

HOW = """This puzzle is a little unusual, because I am not allowed to tell you much about
how to solve it.

Nine lights sit in a 3 by 3 grid, numbered like this:

1 2 3
4 5 6
7 8 9

Press buttons to light up the right bingo lines. A bingo line is a full row, column
or diagonal with all three lights on. I name a line by its lights, like 1-5-9. Each
button turns some lights on, turns some off and leaves the rest as they are. The list
shows what each button does.

Every try starts with all lights off. Submit the whole sequence of presses at once.

Lighting the right lines is not enough. I cannot tell you which lines are needed or
which sequences are accepted, but I will tell you each time whether you are right or
wrong. A sequence that works always exists. There is no time limit."""


def wiring(table):
    rows = []
    for b, row in enumerate(table):
        on = [str(c + 1) for c, e in enumerate(row) if e == E.ON]
        off = [str(c + 1) for c, e in enumerate(row) if e == E.OFF]
        rows.append(f"{LETTERS[b]}: on {' '.join(on)}" + (f", off {' '.join(off)}" if off else ""))
    return "Here is what the buttons do:\n\n" + "\n".join(rows)


def ask(table):
    """질문 바로 앞에 늘 그 제출의 배선을 둔다(v3 은 제출마다 다르다)."""
    return f"{wiring(table)}\n\n{ASK}"


def opening(table):
    return f"{HOW}\n\n{ask(table)}"


FEEDBACK = ("lights", "count", "rows", "inrows", "graded")   # 틀린 전구를 번호로 · 개수만 · 가로줄 이름으로 · 가로줄 위치로 ·
                                                     # 라운드마다 앞의 몇 번은 가로줄 위치, 그 뒤는 번호로 (runner --feedback · --coarse)


def result(res, table, mode="lights"):
    """완성된 줄, 판정, 지금의 배선."""
    return f"{say(res, mode)}\n\n{ask(table)}"


def bingo(done):
    """완성된 줄 이름: 물길의 꽃 이름과 같은 꼴(`A`, `A and B`, `A, B and C`)."""
    names = [E.NAMES[i] for i in done]
    if not names:
        return "none"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def say(res, mode="lights"):
    """판정 한 줄. `Bingo lines: 1-2-3. That is the answer.` ·
    `Bingo lines: 1-2-3 and 1-5-9. Wrong at lights 5 9. Not the answer.` · `… Wrong at light 5. …`
    mode 'rows' 는 틀린 전구가 있는 가로줄만 알린다(`Wrong on lines 1-2-3 and 7-8-9.`): 틀린 전구의 위치가 새 규칙을 바로
    알려 주지 않게(사용자: "틀린 곳을 줄 단위로 알리는건 해볼만할듯함"). 그 전구를 지나는 줄을 모두 대면 겹치는 칸으로
    전구가 드러나서 가로줄 하나만 댄다.
    mode 'inrows' 는 같은 정보를 줄 이름 없이 알린다(`The wrong lights are in the middle and bottom rows.`). 'rows' 의
    `Wrong on lines 4-5-6 …` 는 빙고 줄 이름과 같아서 모델이 '4-5-6 줄을 꺼라 · 1-2-3 줄을 켜라' 로 읽었다(어느줄4 Q001~Q030
    파일럿, Opus · Sonnet 답 글)."""
    head = f"Bingo lines: {bingo(res['lines'])}."
    if res["ok"]:
        return f"{head} That is the answer."
    bad = res.get("flagged") or []
    if not bad:
        return f"{head} Not the answer."
    if mode == "count":                                # 틀린 전구의 개수만: 켰다끄기에서 옮겨 옴(사용자: "지금에서 몇개 전구 틀렸음
        # 피드백으로 바꾸는거 테스트해봤나?", 2026-10-29). 목표 쪽과 금지 전구를 가리지 않고 센다
        return f"{head} Wrong at {len(bad)} light{'' if len(bad) == 1 else 's'}. Not the answer."
    if mode == "rows":                                 # 틀린 전구가 있는 가로줄만: 어느 전구인지는 숨긴다
        rows = sorted({c // 3 for c in bad})
        return f"{head} Wrong on line{'' if len(rows) == 1 else 's'} {bingo(rows)}. Not the answer."
    if mode == "inrows":                               # 같은 정보를 줄 이름 없이: 'rows' 는 빙고 줄 이름이라 목표 줄로 읽혔다
        where = [("top", "middle", "bottom")[r] for r in sorted({c // 3 for c in bad})]
        rows = where[0] if len(where) == 1 else ", ".join(where[:-1]) + " and " + where[-1]
        return f"{head} The wrong lights are in the {rows} row{'' if len(where) == 1 else 's'}. Not the answer."
    where = " ".join(str(c + 1) for c in bad)
    return f"{head} Wrong at light{'' if len(bad) == 1 else 's'} {where}. Not the answer."


def unreadable(table):
    """답 대신 되물었거나 읽을 수 없는 글을 냈을 때. 물길과 같은 문구다."""
    return f"I cannot tell you that. You have to work it out.\n\n{ask(table)}"
