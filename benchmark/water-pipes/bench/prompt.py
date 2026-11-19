# -*- coding: utf-8 -*-
"""모델에게 보내는 글: v1 물길과 v1 꽃조합의 글을 합쳤다.

숨기는 것은 **어느 꽃에 물이 닿아야 하는가**(꽃조합)와 **연속 클릭 제약**(물길) 둘이다.
둘 다 있다는 사실만 알리고, 무엇인지와 라운드마다 바뀐다는 것은 말하지 않는다.

  · 과제 문장은 꽃조합 쪽: `water reaches the right flowers`. 목표가 바뀌기 때문이다.
  · 'Watering … is not enough' 는 물길 쪽: 숨은 클릭 제약이 있다는 유일한 단서다.
  · 'A sequence that works always exists' 는 꽃조합 쪽.
  · 꽃은 색으로 적는다(`[Fr] [Fy] [Fb]`). 판이 바뀌어도 목표가 옮겨 가려면 색이 있어야 한다.
  · 판정은 물길 쪽: `Wrong at tiles 3 6. Not the answer.` 걸린 타일만 알려 주고 왜
    틀렸는지(클릭 제약 · 못 돌리는 타일 · 엉뚱한 꽃)는 구분하지 않는다. 이 피드백이 없으면
    1 라운드에서 클릭 제약을 찾을 길이 거의 없다(예전 세 축 버전은 1 라운드 통과 0~3/10).
  · 판정 앞에 **물이 닿은 꽃**을 붙인다: `Water reached red and blue.` 전구의 `Result:` 와
    같은 자리다(누른 결과는 보여 준다). 첫 런에서 목표와 띠를 둘 다 새로 찾느라 2 라운드
    이후가 거의 다 실패해서 넣었다. 어느 꽃이 목표인지는 여전히 말하지 않는다.

판은 매 질문 바로 앞에 다시 붙인다. 라운드가 바뀌면 그 판이 달라질 뿐 아무 말도 하지 않는다.
v3: 판이 제출마다 다르다. 그래도 바뀐다는 말은 하지 않는다(전구 · 블록 v3 와 같음).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine as E                                    # noqa: E402

ASK = ("Which tiles do you click, in order? "
       "Answer with tile numbers, like <answer>1 1 2 3</answer>.")

HOW = """This puzzle is a little unusual, because I am not allowed to tell you much about
how to solve it.

Rotate the pipes so that water reaches the right flowers. Tiles are [P] pipe, [S] water,
[X] stone, and [Fr] [Fy] [Fb] red, yellow and blue flowers. The number before each tile
is its name. Only pipes turn: one click is 90° clockwise, and four clicks bring a tile
back to where it started. Letters after the colon are the open sides: U up, R right,
D down, L left. Water flows between two tiles only when both have the facing side open.

Every try starts from the board shown just above the question. Submit the whole
click sequence at once.

Watering the right flowers is not enough. I cannot tell you which flowers must be
watered or which sequences are accepted, but I will tell you each time whether you
are right or wrong. A sequence that works always exists. There is no time limit."""


def ask(board):
    """질문 바로 앞에 늘 그 제출의 판을 둔다(v3 은 제출마다 다르다)."""
    return f"{E.text(board)}\n\n{ASK}"


def opening(board):
    return f"{HOW}\n\n{ask(board)}"


def result(res, reached, board):
    """물이 닿은 꽃, 판정, 지금의 판."""
    return f"Water reached {flowers(reached)}. {say(res)}\n\n{ask(board)}"


def flowers(reached):
    names = [c for c in E.COLORS if c in reached]
    if not names:
        return "no flowers"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def say(res):
    """판정 한 줄. 정답이면 `That is the answer.`, 아니면 `Wrong at …. Not the answer.`
    걸린 타일이 없으면 판정만 보낸다. v1 물길과 같다."""
    if res["ok"]:
        return "That is the answer."
    bad = res.get("tiles") or []
    return f"Wrong at {_tiles(bad)}. Not the answer." if bad else "Not the answer."


def _tiles(idx):
    where = " ".join(E.label(i) for i in idx)
    return f"tile{'' if len(idx) == 1 else 's'} {where}"


def unreadable(board):
    """답 대신 되물었거나 읽을 수 없는 글을 냈을 때. v1 물길과 같은 문구다."""
    return f"I cannot tell you that. You have to work it out.\n\n{ask(board)}"
