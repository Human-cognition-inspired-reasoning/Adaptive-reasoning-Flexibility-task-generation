# block-image

색 블록 구조물 사진을 보고, 한쪽 옆에서 본 3×3 격자에 숫자를 적어 내는 태스크입니다. 한 문제는 5라운드이고, 라운드마다
**규칙 · 목표**가 바뀌며 **조건(사진)은 제출마다** 바뀝니다. 규칙과 목표는 알려 주지 않으며, 모델은 제출할 때마다 받는
피드백으로 찾아야 합니다. 라운드가 바뀐 뒤에도 직전 규칙을 따르는 오답(지속적 오류)이 얼마나 나오는지로 적응을 잽니다.

## 태스크 구성

| 구성 | 내용 |
|---|---|
| 조건 | 블록 구조물 사진 한 장(3×3 바닥, 칸마다 0~3층, 빨강 · 파랑 · 초록 블록, 630×540 JPEG). 제출마다 새 사진, 한 문제 안에서 겹치지 않음 |
| 규칙 | 칸 숫자의 뜻: 블록 수 · 보는 쪽에서 가장 가까운 블록이 몇째 칸인지 · 색 가짓수 가운데 하나. 매 라운드 바뀜. 숨김 |
| 목표 | 어느 쪽에서 본 격자인지: 앞 · 뒤 · 왼쪽 · 오른쪽 가운데 하나. 매 라운드 바뀜. 숨김 |
| 피드백 | 낸 격자를 돌려주며 틀린 칸에 `*`를 붙임. 방향 · 규칙 · 사진 읽기 가운데 무엇이 틀렸는지는 구분하지 않음 |

- 격자는 그 방향에 선 사람이 보는 대로 적습니다. 윗줄이 3층이고, 열은 보는 사람의 왼쪽부터입니다. 모델은
  `<answer>` 안에 세 줄로 적어 냅니다.
- 라운드마다 T=10번 안에 연속 k=2번 맞혀야 다음 라운드로 넘어가고, 못 넘어가면 그 문제는 거기서 끝납니다.
- 지속적 오류는 직전 규칙으로 쓴 격자(방향은 아무거나, 칸까지 같음)이면서 지금 정답이 아닌 답입니다. 라운드 PER은
  지속적 오류 수 ÷ 채점된 제출 수(R2~R5), 문제 MPE는 그 문제의 PER 평균, 전체 MPE는 문제 MPE의 평균입니다.
- 생성 보장(`bench/verify.py`)
  - 사진마다 12가지 답(4방향 × 3규칙)이 모두 다름. 직전 규칙이나 직전 방향으로 쓴 격자는 지금 늘 틀림
  - 방향과 규칙은 라운드마다 직전과 다름
  - 한 라운드 열 장의 정답이 모두 다르고, 라운드 첫 장의 정답은 직전 라운드 마지막 장의 정답과 다름. 맞힌 답을
    그대로 다시 내면 늘 틀림

## 폴더

| 경로 | 내용 |
|---|---|
| `bench/structure.py` | 구조물 뽑기와 제약(12가지 답이 모두 다름), 규칙별 격자 계산 |
| `bench/render.py`, `bench/photos.py` | Blender로 사진 렌더(사진 한 장, 사진 묶음) |
| `bench/occlusion.py` | 카메라에서 블록이 보이는 정도 계산 |
| `bench/engine.py` | 정답 격자, 판정, 지속적 오류, 우연 기준 |
| `bench/generate.py` | 문제 생성 |
| `bench/verify.py` | 사진 묶음과 문제 검사(엔진과 따로 짠 계산으로 다시 셈) |
| `bench/prompt.py` | 모델에게 주는 글 |
| `bench/runner.py` | 모델 실행 |
| `bench/drivers.py` | Anthropic · OpenAI API 연결 |
| `bench/score.py` | 실행 기록 채점(MPE, 우연 기준) |
| `bench/selftest.py` | 판정과 글 자체 검사 |
| `bench/make_human.py`, `bench/human_runs.py` | 사람용 페이지 만들기, 사람 결과를 실행 기록 형식으로 바꾸기 |
| `pool/photos/` | 사진 800장(p01~p800, 씨앗 4001~4800). `.json`에 구조물과 12가지 정답 격자 |
| `episodes/episodes.json` | 문제 300개(V001~V300). 12가지 답이 모두 다른 사진 498장을 씀 |
| `human/block-image.html` | 사람용 페이지(10문제) |
| `human/block-image_check.html` | 확인용 페이지(모든 문제, 숨은 방향 · 규칙과 정답을 켜고 끌 수 있음) |

## 실행 순서

```bash
cd benchmark/block-image
python3 bench/generate.py --n 300           # 문제 300개 (기본 씨앗 20261112) → episodes/episodes.json
python3 bench/verify.py                     # 사진 묶음과 문제 검사
python3 bench/selftest.py                   # 판정과 글 자체 검사

pip install -r requirements.txt             # 모델 실행에만 필요
export ANTHROPIC_API_KEY=... OPENAI_API_KEY=...
python3 bench/runner.py gpt-6.1-sol --episodes 100 --jobs 10   # → runs/<시각>/
python3 bench/score.py runs/<시각>
```

- 같은 사진 묶음과 같은 씨앗이면 같은 문제가 나옵니다(지금 `episodes.json`과 같음을 확인).
- 사진은 `pool/photos/`에 들어 있어 다시 그릴 필요가 없습니다. 다시 그리려면 Blender 5.2(`bpy`)가 필요합니다.
  만드는 법은 `bench/render.py` 머리말에 있고, 이 묶음은 아래 명령으로 그렸습니다.

  ```bash
  python3 bench/photos.py --dir pool --n 800 --seed0 4001 --alpha 1.0 --min-vis 0.15 --look wood --marks none \
      --samples 128 --size 630x540 --ext jpg
  ```
- 생성과 검증, 채점은 파이썬 표준 라이브러리만 씁니다(`verify.py --deep`과 사진 그리기만 numpy를 씀).
- 실행 기록(`runs/`)과 사람 참가자 결과는 저장소에 올리지 않습니다(`.gitignore`).

## 사람 평가

`human/block-image.html` 하나를 참가자에게 보내 브라우저에서 풀게 합니다. 모델과 같은 문제, 같은 예산, 같은 피드백을
쓰고, 규칙 · 목표 · 라운드 전환은 알려 주지 않습니다. 끝나면 참가자가 결과 JSON을 내려받아 돌려줍니다.

```bash
python3 bench/human_runs.py <내려받은 결과>.json   # → runs/human_<이름>_<시각>/
python3 bench/score.py runs/human_<이름>_<시각>
```

페이지의 열 문제(V008 V041 V046 V050 V054 V062 V072 V077 V090 V093)는 V001~V100의 GPT-6.1 Sol 실행에서 R1을 풀었는지로
층을 나눠 뽑았습니다(`make_human.py 10 1117`). 층을 나누는 데 모델 실행 기록이 필요하므로, 같은 페이지를 다시 만들려면
그 기록이 있어야 합니다.

## 결과 (v1.0, 2026-11-13 실행, V001~V100)

| Model | N | MPE, % | Mean error, % | API time, min | Cost, USD |
|---|---|---|---|---|---|
| GPT-6.1 Sol | 97/100 | 55.30 | 62.85 | 773.53 | 21.2473 |

- N은 MPE에 들어간 문제(R1을 통과한 문제) 수와 전체 문제 수입니다. reasoning effort medium, T=10, k=2.
- 다섯 라운드를 모두 통과한 문제 57개. 문제 단위 붓스트랩 95% 구간 51.5~59.2%.
- 우연 기준(답에서 0인 칸은 두고 나머지 칸을 1~3에서 고르게 뽑았을 때 직전 규칙 격자가 될 확률): 0.36%.

## 버전

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-11-19 | 첫 업로드 |
