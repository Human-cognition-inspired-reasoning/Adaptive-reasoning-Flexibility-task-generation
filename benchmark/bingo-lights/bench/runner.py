#!/usr/bin/env python3
"""3×3 빙고 전구 v3 러너: 목표는 어느 줄, 규칙은 금지 전구(누르는 도중 켜면 안 되는 전구), 조건(배선표)은 제출마다 바뀐다.
   python3 bench/runner.py MODEL [--episodes N] [--start S] [--jobs J] [--feedback lights|count|rows|inrows|graded] [--coarse 2]
                                [-T 10] [-k 2]

v2 `전구-빙고-옛답몫-버튼6` 러너에서 판을 고르는 곳만 바꿨다. 라운드의 j 번째 제출은 그 라운드의 판 j(boards[j-1])로 묻고
채점한다. 읽지 못한 답도 제출 하나라 다음 질문은 다음 판이다. 판은 라운드마다 10 장이라 -T 는 10 을 넘을 수 없다.

문제를 J 개씩 동시에 돌린다(기본 10). 문제마다 드라이버(API 클라이언트)를 따로 만들고, 결과는 한 폴더에 문제마다 파일 하나로
모인다. 한 문제가 오류로 멈춰도 나머지는 계속하고, 끝에 --resume 명령을 알려 준다.

한 문제 = 다섯 라운드. 라운드가 넘어갈 때마다 규칙(금지 전구)과 목표(완성할 줄 한두 개)가 바뀐다. 판정은
`Bingo lines: 1-2-3 and 1-5-9. Wrong at lights 5 9. Not the answer.`: 목표와 어긋난 전구와 켠 금지 전구를 이유 없이
한 목록으로 알린다(engine.evaluate).

라운드 1~5  예산 T, 연속 k 번 맞히면 넘어간다. 한 라운드라도 못 풀면 거기서 그 문제를 끝낸다.

지속적 오류 깃발. 조건은 그 제출의 판으로 따진다. 지표는 forbid 하나다.
    forbid **직전 금지 전구는 한 번도 안 켜고 지금 금지 전구를 켠** 오답: 줄은 보지 않는다(지표)
    rule   forbid 이면서 줄이 정확히 **지금 목표** (직전 라운드 규칙이라면 정답)
    goal   지금 금지 전구는 안 켜고 줄이 정확히 **직전 목표**인 오답 (참고: 목표 고집)
    both   forbid 이면서 줄이 정확히 직전 목표 (참고: 전환을 모르고 직전처럼 푼 답)
    repeat 직전 라운드에서 성공한 순서를 글자 그대로 되풀이 (참고: 판이 섞여 글자가 같아도 누르는 버튼의 일은 다르다)
"""
import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import engine as E                                    # noqa: E402
import prompt as P                                    # noqa: E402
import drivers                                   # noqa: E402

ANSWER = re.compile(r"<answer>(.*?)</answer>", re.S | re.I)
SYSTEM = ""
KINDS = ("forbid", "rule", "goal", "both", "repeat")
# 터미널에 찍을 때 쓰는 이름. 영어 키를 그대로 내보내면 읽기 어렵다.
TAGNAME = dict(forbid="옛금지", rule="옛금지·지금목표", goal="옛목표", both="옛금지·옛목표", repeat="반복")


def parse(reply, buttons):
    """<answer> 안에서 버튼 글자를 뽑는다. 못 읽으면 None: 시행 1 회는 소모된다.
    태그는 있는데 비었으면 빈 제출([])로 채점한다(전구-시작버튼 첫 파일럿의 교훈).

    구분자를 가리지 않는다. 'A B', 'A,B', 'A → B' 가 같게 읽힌다.
    범위 밖 글자가 섞이면 통째로 버린다: 일부만 살리면 뜻이 달라진 제출을 채점한다."""
    found = ANSWER.findall(reply or "")
    if not found:
        return None
    out = []
    for ch in re.findall(r"[A-Za-z]", found[-1]):
        i = E.parse_label(ch)
        if not (0 <= i < buttons):
            return None
        out.append(i)
    return out


def play(driver, episode, T, k, say, T1=None, mode="lights", coarse=2):
    """mode 'graded' 는 라운드마다 앞의 coarse 번 제출에는 가로줄 위치(inrows)로, 그 뒤 제출에는 번호(lights)로 알린다.
    줄 위치만 알리면 모델이 옛 금지 전구를 정확히 익히지 못해 다음 라운드의 습관이 약해졌다(어느줄4 inrows T=15,
    옛 · 새 금지를 둘 다 켠 오답 7~12% → 23~32%). 라운드 끝에는 번호로 정확히 익히고, 전환 직후 몇 번만 새 금지 전구의
    위치를 숨긴다."""
    B = episode["buttons"]
    T1 = T1 or T
    log = dict(episode=episode["id"], buttons=B, T=T, T1=T1, k=k, feedback=mode, rounds=[], calls=[],
               startedAt=datetime.now(ZoneInfo("Asia/Seoul")).isoformat())
    if mode == "graded":
        log["coarse"] = coarse
    state, feedback, prev, prev_success = {}, None, None, None
    for spec in episode["rounds"]:
        n = spec["round"]
        boards, goal, forbidden = spec["boards"], spec["goal"], spec["forbidden"]
        if hasattr(driver, "round"):
            driver.round = n                      # 대본 드라이버만 쓴다
        rec = dict(round=n, submissions=0, solved=False, unparsed=0, streak=0,
                   **{kind: 0 for kind in KINDS})
        log["rounds"].append(rec)
        streak = 0
        for _ in range(T1 if n == 1 else T):
            # 조건은 물을 때 그린다: 제출마다 그 제출의 판을 보여 주고 같은 판으로 채점한다.
            j = rec["submissions"]
            table = boards[j]["table"]
            text = P.opening(table) if not log["calls"] else feedback(table)
            call = dict(round=n, submission=j + 1, board=j, prompt=text)
            log["calls"].append(call)
            reply, meta = driver.ask(SYSTEM, [{"role": "user", "content": text}], state)
            rec["submissions"] += 1
            presses = parse(reply, B)
            call.update(reply=reply, api=meta, presses=presses)
            if presses is None:
                rec["unparsed"] += 1; streak = 0
                feedback = P.unreadable; call["verdict"] = "unparsed"
                say(f"    R{n} #{rec['submissions']:02} →  {'':<16} 읽을 수 없음")
                continue
            res = E.evaluate(table, goal, forbidden, presses)
            call.update(status=res["status"], correct=res["ok"], result=list(res["result"]),
                        lines=res["lines"], goalWrong=res["goalWrong"], violations=res["violations"],
                        flagged=res["flagged"])
            if not res["ok"] and prev is not None:
                # 옛 규칙(직전 금지 전구)을 지키고 지금 규칙을 어겼나: 지금 배선으로 판단한다
                kept = not E.touches(table, presses, prev["forbidden"])
                broke = E.touches(table, presses, forbidden)
                hits = dict(
                    forbid=kept and broke,
                    rule=kept and broke and res["lines"] == goal,
                    goal=not broke and res["lines"] == prev["goal"],
                    both=kept and broke and res["lines"] == prev["goal"],
                    repeat=prev_success is not None and presses == prev_success)
                for kind, hit in hits.items():
                    if hit:
                        rec[kind] += 1; call[kind] = True
            streak = streak + 1 if res["ok"] else 0
            shown = ("inrows" if rec["submissions"] <= coarse else "lights") if mode == "graded" else mode
            call["feedbackMode"] = shown                  # 이 제출의 판정을 어떤 문구로 알렸나(다음 질문 머리)
            feedback = partial(P.result, res, mode=shown)
            shown = " ".join(E.label(i) for i in presses)
            done = ",".join(E.NAMES[i] for i in res["lines"]) or "줄 없음"
            marks = " ".join(str(c + 1) for c in res["flagged"])
            tags = " ".join(f"[{TAGNAME[kind]}]" for kind in KINDS if call.get(kind))
            say(f"    R{n} #{rec['submissions']:02} →  {shown[:16]:<16} "
                f"{'정답' if res['ok'] else '오답'} {done}" + (f"  틀린 전구 {marks}" if marks else "")
                + (f"  {tags}" if tags else ""))
            if res["ok"] and streak >= k:
                rec.update(solved=True, streak=streak, solution=list(presses))
                prev_success = list(presses)
                break
        if not rec["solved"]:
            rec["budgetExhausted"] = True
            log["status"] = "round1_unsolved" if n == 1 else f"stopped_round{n}"
            return log
        prev = spec
    log["status"] = "completed"
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", choices=tuple(drivers.PROFILES))
    ap.add_argument("--episodes", type=int, default=10)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=10)
    ap.add_argument("--feedback", choices=P.FEEDBACK, default="lights")
    ap.add_argument("--coarse", type=int, default=2)        # --feedback graded 에서 라운드마다 줄 위치로 알릴 제출 수
    ap.add_argument("-T", type=int, default=10)
    ap.add_argument("-k", type=int, default=2)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--reasoning", default="medium")
    ap.add_argument("--max-tokens", type=int, default=8000)
    a = ap.parse_args()
    alleps = json.loads((ROOT / "episodes/episodes.json").read_text())
    least = min(len(r["boards"]) for e in alleps for r in e["rounds"])
    if a.T > least:
        raise SystemExit(f"-T {a.T} 은 라운드당 판 수({least})보다 많을 수 없습니다.")
    by_id = {e["id"]: e for e in alleps}
    label = drivers.LABELS[a.model]
    if a.resume:
        out = Path(a.resume)
        if not out.is_absolute():
            out = ROOT / out
        index = json.loads((out / "index.json").read_text())
        if index["model"] != a.model:
            raise SystemExit(f"폴더의 모델은 {index['model']} 입니다.")
        a.T, a.k, a.feedback = index["T"], index["k"], index.get("feedback", "lights")
        a.coarse = index.get("coarse", a.coarse)
        eps = [by_id[i] for i in index["episodes"]]
        done = {p.stem.split("_", 1)[1] for p in out.glob(f"{label}_*.json")}
        eps = [e for e in eps if e["id"] not in done]
        print(f"RESULT_DIR={out}\n  이어서 돌립니다. 끝난 {len(done)}개 건너뜀, 남은 {len(eps)}개")
        if not eps:
            return
    else:
        eps = alleps[a.start:a.start + a.episodes]
        out = ROOT / "runs" / datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
        out.mkdir(parents=True)
        print(f"RESULT_DIR={out}")
    cfg = drivers.resolve_config(dict(model_id=a.model, label=label,
                                      max_tokens=a.max_tokens, reasoning=a.reasoning))
    (out / "index.json").write_text(json.dumps(
        dict(model=a.model, T=a.T, T1=a.T, k=a.k, feedback=a.feedback,
             **({"coarse": a.coarse} if a.feedback == "graded" else {}),
             episodes=[e["id"] for e in (eps if not a.resume else
                                         [by_id[i] for i in index["episodes"]])],
             system=SYSTEM, settings=drivers.settings(cfg)), ensure_ascii=False, indent=1))
    lock = threading.Lock()

    def say(eid):
        def line(text):
            with lock:
                print(f"{eid} {text.strip()}", flush=True)
        return line

    def run_one(e):
        kind = drivers.PROFILES[a.model][0]
        driver = (drivers.AnthropicDriver if kind == "anthropic" else drivers.OpenAIDriver)(cfg)
        try:
            log = play(driver, e, a.T, a.k, say(e["id"]), mode=a.feedback, coarse=a.coarse)
        finally:
            driver.close()
        (out / f"{label}_{e['id']}.json").write_text(json.dumps(log, ensure_ascii=False, indent=1))
        first = KINDS[0]
        with lock:
            print(f"  {e['id']} {log['status']}  제출 {[r['submissions'] for r in log['rounds']]}"
                  f"  {TAGNAME[first]} {[r[first] for r in log['rounds'][1:]]}", flush=True)

    failed = []
    with ThreadPoolExecutor(max_workers=max(1, min(a.jobs, len(eps)))) as pool:
        futures = {pool.submit(run_one, e): e["id"] for e in eps}
        try:
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:                  # 한 문제가 멈춰도 나머지는 계속: 끝나고 --resume 으로 다시
                    failed.append(futures[f])
                    with lock:
                        print(f"  {futures[f]} 실패: {type(exc).__name__}: {exc}", flush=True)
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    if failed:
        print(f"실패 {len(failed)}개({', '.join(sorted(failed))}): 이어서: python3 bench/runner.py {a.model} --resume {out}")


if __name__ == "__main__":
    main()
