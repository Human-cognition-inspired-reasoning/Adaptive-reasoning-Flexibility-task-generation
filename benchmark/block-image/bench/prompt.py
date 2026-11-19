# -*- coding: utf-8 -*-
"""모델에게 보내는 글과 그림: 사진-자물쇠 · 사진-사분면액자 글의 짜임을 따른다.
  · 목표(어느 쪽에서 본 격자인가)와 규칙(칸에 무슨 수를 적는가)은 말하지 않는다. 칸 값이 '.' 또는 숫자라는 꼴만 알린다.
  · 칸 값은 '숫자' 라고만 한다(0~3 범위도 알리지 않음). 'Choosing the right side is not enough.' 도 뺐다. 숫자의 뜻을 말하지
    않으므로 무엇을 적을지는 모델이 찾아야 하고, 숨은 규칙이 있다는 단서를 따로 둘 필요가 없다(2026-11-12 사용자).
  · 틀리면 낸 격자를 그대로 돌려주고 틀린 칸에 * 를 붙인다(2026-11-10 설계: 칸 이름은 쓰지 않음). 방향이 틀린 칸과 색이 틀린 칸을
    가리지 않는다.
  · 방향 이름표는 없다(2026-11-12 사용자 '글자를 지울까? 필요없는 정보'). 답에 방향 이름을 쓰지 않고, 카메라가 늘 같아 네 면은
    사진 속 위치로 갈린다. 'Every photo is taken from the same position.' 도 넣었다가 사용자가 필요 없다고 해 뺐다.
  · 사진 설명은 답 격자를 정하는 데 필요한 것만 둔다(2026-11-12 사용자 '사진설명이 너무 과한거아님?'): 색 이름을 늘어놓으면
    숨긴 규칙(어느 색을 세는지)을 가리키게 되고, 불투명 · 모두 보임은 지각을 돕는 말이라 뺐다(생성기 제약은 그대로).
  · 옆에서 본 격자임을 밝힌다('shows the blocks from the side', 사용자: 윗면 · 아랫면을 쓸 수도 있다). 'You get a new photo with
    every question.' 은 사용자가 필요 없는 정보라 해 뺐다(2026-11-12).
  · 'Every block is red, blue or green.' 은 뺐다가 되살렸다(2026-11-12 사용자 '일단 색 문장 되살려서 돌려볼게'): 줄인 지시문에서
    1 라운드를 GPT 9 · Opus 4 · Sonnet 0 /10 만 풀었고, 틀리며 길어진 1 라운드에서 Claude 거절(reasoning_extraction)이 몰렸다.
  · 칸 값은 'a count of blocks in that line at that level'(2026-11-12 사용자 '바꾸고'). '숫자' 만으로는 답의 뜻이 비어 Sonnet 은 색 번호,
    GPT 는 8 · 101 같은 수를 시험했다. 전구 · 물길처럼 답의 뜻(센 수)은 알리고 규칙(어느 색만 세는지, 일부만 센다는 것까지)은 숨긴다.
    정답도 그 줄의 블록을 센 수(한 색)라 문구와 어긋나지 않는다.
  · 이름표를 다시 넣은 판(2026-11-12 사용자 '이름표를 다시 넣고 소넷만 확인할게'): 사진에 이름표가 있으면(판 사진 .json 의
    render.marks == 'labels') 'The words FRONT, BACK, LEFT and RIGHT around the grid mark its four sides.' 를 더한다(HOW_LABELS).
  · 'a count of blocks…' 는 되돌렸다(2026-11-12 사용자 '센다를 빼는게 맞는듯'): 세 모델 모두 1 라운드에서 모든 블록을 센 격자에
    매달려 1 라운드 해결이 줄었다(GPT 7 → 5, Opus 10 → 5 /10). 칸 값은 다시 'a number'.
  · 제출마다 새 사진을 준다(같은 사진에서 틀린 칸만 고쳐 맞히지 못하게). 그래서 라운드가 바뀌어도 겉으로는 아무것도 달라지지 않는다."""
from pathlib import Path

import engine as E

ROOT = Path(__file__).resolve().parent.parent

HOW = """This puzzle is a little unusual, because I am not allowed to tell you much about how to solve it.

Each photo shows cube blocks stacked on a 3 × 3 grid, up to 3 high. Every block is red, blue or green.

For each photo, write a view grid that shows the blocks from the side, as seen from one of the four sides of the grid. A
view grid has 3 rows and 3 columns. The top row is the third level and the bottom row is the first level. Each column
stands for one line of three cells running straight away from the viewer, ordered left to right as the viewer sees them.
In each grid cell write . if that line has no block at that level, and otherwise a number.

I will tell you each time whether you are right or wrong. A grid that works always exists. There is no time limit."""
HOW_LABELS = """This puzzle is a little unusual, because I am not allowed to tell you much about how to solve it.

Each photo shows cube blocks stacked on a 3 × 3 grid, up to 3 high. Every block is red, blue or green. The words FRONT,
BACK, LEFT and RIGHT around the grid mark its four sides.

For each photo, write a view grid that shows the blocks from the side, as seen from one of the four sides of the grid. A
view grid has 3 rows and 3 columns. The top row is the third level and the bottom row is the first level. Each column
stands for one line of three cells running straight away from the viewer, ordered left to right as the viewer sees them.
In each grid cell write . if that line has no block at that level, and otherwise a number.

I will tell you each time whether you are right or wrong. A grid that works always exists. There is no time limit."""

ASK = ("Write the view grid for this photo. Put the three rows inside <answer></answer> tags, top row first, one row per "
       "line, with the three cells separated by spaces.")
UNREADABLE = "I cannot tell you that. You have to work it out."


def text(s):
    return {"type": "text", "text": s}


def image(path):
    return {"type": "image", "path": str(ROOT / path)}


def say(answer, wrong, mode="cells"):
    """cells: 낸 격자를 돌려주고 틀린 칸에 * (처음 설계). count: 틀린 칸 수만(2026-11-12 사용자 '피드백 덜주기로 고쳐봐') : 
    별표 무늬로 12 가지 (방향, 색) 가운데 하나를 곧장 골라내지 못하게."""
    if not wrong:
        return "That is the answer."
    if mode == "count":
        n = len(wrong)
        return f"{n} cell{'s are' if n > 1 else ' is'} wrong. Not the answer."
    return f"Wrong at the cells marked *.\n{E.show(answer, wrong)}\nNot the answer."


def opening(photo, labels=False, empty="."):
    """labels: 사진 판에 FRONT · BACK · LEFT · RIGHT 이름표가 있으면 그 사실을 알리는 글(HOW_LABELS)을 쓴다."""
    how = HOW_LABELS if labels else HOW
    if empty != ".":                                   # 빈칸 기호(새 판은 0)
        # 빈칸을 0 으로 쓰는 판(새 규칙)은 빈칸 표기를 따로 말하지 않는다(2026-11-13 사용자 '없으면 0이라고 할 필요가있나? 그냥 숫자를
        # 채우라 하면 되는거 아닌가' → '바꿔'). 11-13 의 30 · 10 문제 실행은 'write 0 if that line has no block…' 문장이었다.
        how = how.replace("In each grid cell write . if that line has no block at that level, and otherwise a number.",
                          "In each grid cell write a number.")
    return [text(how), image(photo), text(ASK)]


def follow(feedback, photo, prev=None):
    """prev: 피드백이 가리키는 직전 사진. 주면 그 사진을 피드백 앞에 다시 붙인다(물길 · 전구가 매 질문 판을 다시 보여 주듯,
    2026-11-12 사용자 '붙이는게 맞긴해'). 순서: 직전 사진 · 피드백 · 새 사진 · 요청문."""
    return ([image(prev)] if prev else []) + [text(feedback), image(photo), text(ASK)]


def logged(parts):
    return [p["text"] if p["type"] == "text" else "image:" + Path(p["path"]).name for p in parts]
