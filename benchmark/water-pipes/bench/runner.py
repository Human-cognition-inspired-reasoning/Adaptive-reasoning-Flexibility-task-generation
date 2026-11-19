#!/usr/bin/env python3
"""물길 v3 러너: 목표(꽃 색 조합) · 규칙(연속 클릭 띠)은 라운드마다, 조건(판)은 제출마다 바뀐다.
   python3 bench/runner.py MODEL [--episodes N] [--start S] [--jobs J] [-T 10] [-k 2]

v2 `물길-세축` 러너에서 판을 고르는 곳만 바꿨다. 라운드의 j 번째 제출은 그 라운드의 판 j(boards[j-1])로 묻고
채점한다. 읽지 못한 답도 제출 하나라 다음 질문은 다음 판이다. 판은 라운드마다 10 장이라 -T 는 10 을 넘을 수 없다.
1 라운드 목표는 모든 꽃이다. 글과 판정은 v2 와 같다(`Water reached red. Wrong at tiles 3 6. Not the answer.`).

라운드 1~5  예산 T, 연속 k 번 맞히면 넘어간다.
            한 라운드라도 못 풀면 거기서 에피소드를 끝내고 다음 문항으로 간다.

지속적 오류 깃발. 판은 모델이 보고 있는 **지금** 것으로 둔다. 지표는 band 하나다.
    band  **직전 띠만** 지킨 오답: 물이 어디에 닿았는지는 보지 않는다 (지표, 규칙으로만 셈)
    rule  직전 띠로 **지금 목표**까지 달성한 오답 (v1 물길의 정의)
    goal  **직전 목표**를 지금 띠로 달성한 오답 (참고: 목표 고집)
    both  직전 목표를 직전 띠로 달성한 오답 (참고: 전환을 모르고 직전처럼 푼 답)
    repeat 직전 라운드에서 성공한 순서를 글자 그대로 되풀이
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
NUM = re.compile(r"\d+")
PAIR = re.compile(r"\(\s*\d+\s*[,\s]\s*\d+\s*\)")
SYSTEM = ""
KINDS = ("band", "rule", "goal", "both", "repeat")
# 터미널에 찍을 때 쓰는 이름. 영어 키를 그대로 내보내면 읽기 어렵다.
TAGNAME = dict(band="옛띠", rule="옛띠·지금목표", goal="옛목표", both="옛띠·옛목표", repeat="반복")



def parse(reply, size):
    """<answer> 안에서 타일 번호를 뽑는다. 못 읽으면 None: 시행 1 회는 소모된다."""
    found = ANSWER.findall(reply or "")
    if not found:
        return None
    if PAIR.search(found[-1]):
        return None                               # (행,열) 로 냈다: 번호로 읽으면 뜻이 달라진다
    out = []
    for t in NUM.findall(found[-1]):
        t = int(t)
        if not (1 <= t <= size * size):
            return None
        out.append(t - 1)
    return out or None


def play(driver, episode, T, k, say, T1=None):
    size = episode["size"]
    T1 = T1 or T
    log = dict(episode=episode["id"], size=size, T=T, T1=T1, k=k, rounds=[], calls=[],
               startedAt=datetime.now(ZoneInfo("Asia/Seoul")).isoformat())
    state, feedback, prev, prev_success = {}, None, None, None
    for spec in episode["rounds"]:
        n = spec["round"]
        boards, goal, rule = spec["boards"], spec["goal"], spec["rule"]
        if hasattr(driver, "round"):
            driver.round = n
        rec = dict(round=n, submissions=0, solved=False, unparsed=0, streak=0,
                   **{kind: 0 for kind in KINDS})
        log["rounds"].append(rec)
        streak = 0
        for _ in range(T1 if n == 1 else T):
            # 판은 물을 때 그린다: 제출마다 그 제출의 판을 보여 주고 같은 판으로 채점한다.
            j = rec["submissions"]
            board = boards[j]["board"]
            text = P.opening(board) if not log["calls"] else feedback(board)
            call = dict(round=n, submission=j + 1, board=j, prompt=text)
            log["calls"].append(call)
            reply, meta = driver.ask(SYSTEM, [{"role": "user", "content": text}], state)
            rec["submissions"] += 1
            clicks = parse(reply, size)
            call.update(reply=reply, api=meta, clicks=clicks)
            if clicks is None:
                rec["unparsed"] += 1; streak = 0
                feedback = P.unreadable; call["verdict"] = "unparsed"
                say(f"    R{n} #{rec['submissions']:02} →  {'':<22} 읽을 수 없음")
                continue
            res = E.evaluate(board, clicks, goal, rule)
            # 물이 어디까지 닿았는지는 규칙 위반과 무관하게 늘 알려 준다 :
            # 전구가 누른 뒤 전구 상태를 늘 보여 주는 것과 같다.
            reached = E.reaches(board, E.apply(board, clicks))
            call.update(status=res["status"], correct=res["ok"], reached=sorted(reached))
            if not res["ok"] and prev is not None:
                hits = dict(
                    band=not any(E.violations(board, clicks, prev["rule"])),
                    rule=E.evaluate(board, clicks, goal, prev["rule"])["ok"],
                    goal=E.evaluate(board, clicks, prev["goal"], rule)["ok"],
                    both=E.evaluate(board, clicks, prev["goal"], prev["rule"])["ok"],
                    repeat=prev_success is not None and clicks == prev_success)
                for kind, hit in hits.items():
                    if hit:
                        rec[kind] += 1; call[kind] = True
            streak = streak + 1 if res["ok"] else 0
            feedback = partial(P.result, res, reached)
            shown = " ".join(E.label(i) for i in clicks)
            tags = " ".join(f"[{TAGNAME[kind]}]" for kind in KINDS if call.get(kind))
            # 판정은 두 과제가 같은 말을 쓴다. rule_violation 같은 내부 상태는 로그에만 남긴다.
            say(f"    R{n} #{rec['submissions']:02} →  {shown[:22]:<22} "
                f"{'정답' if res['ok'] else '오답'}  {tags}".rstrip())
            if res["ok"] and streak >= k:
                rec.update(solved=True, streak=streak, solution=list(clicks))
                prev_success = list(clicks)
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
        a.T, a.k = index["T"], index["k"]
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
        dict(model=a.model, T=a.T, T1=a.T, k=a.k,
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
            log = play(driver, e, a.T, a.k, say(e["id"]))
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
                except Exception as exc:                  # 한 문제가 멈춰도 나머지는 계속, 끝나고 --resume 으로 다시
                    failed.append(futures[f])
                    with lock:
                        print(f"  {futures[f]} 실패: {type(exc).__name__}: {exc}", flush=True)
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    if failed:
        print(f"실패 {len(failed)}개({', '.join(sorted(failed))}). 이어서: python3 bench/runner.py {a.model} --resume {out}")


if __name__ == "__main__":
    main()
