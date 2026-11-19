#!/usr/bin/env python3
"""블록 투사도 러너.   python3 bench/runner.py MODEL [--episodes N] [--start S] [--jobs J] [-T 10] [-k 2] [--feedback cells|count] [--resume 폴더]

피드백: cells(기본) = 낸 격자를 돌려주고 틀린 칸에 * · count = 틀린 칸 수만('3 cells are wrong. Not the answer.').

한 문제 = 다섯 라운드. 라운드가 넘어갈 때마다 목표(보는 방향) · 규칙(세는 색)이 바뀌고, 조건(사진)은 제출마다 바뀐다.
바뀐다고 말하지 않는다. 라운드마다 예산 T 안에 연속 k 번 맞히면 넘어가고, 못 풀면 그 문제는 거기서 끝난다.
사진-자물쇠 러너를 옮겼다(거절 되묻기 포함). 로그에는 사진 파일 이름만 남긴다.

지속적 오류 깃발. 지표는 rule 하나다(예전 판의 로그에는 color 로 남아 있음).
    rule    직전 규칙으로 쓴 격자(어느 방향이든 그 규칙 격자와 칸까지 똑같음)이고 지금 정답이 아닌 답
    side    직전 방향에서 본 격자(규칙은 아무거나)인 오답(참고)
    both    직전 방향 · 직전 규칙 격자 그대로인 오답(참고)
    near    칸이 가장 많이 같은 격자가 하나뿐이고 그것이 직전 규칙이며 9 칸 중 7 칸 이상 같은 오답(참고)
    repeat  직전 라운드 마지막 정답과 같은 격자(참고)

판(scheme): episodes.json 의 scheme. kind = 새 규칙(count · near · colors), color = 예전 판(세는 색).
"""
import argparse
import json
import re
import sys
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import engine as E                                    # noqa: E402
import generate as G                                  # noqa: E402
import prompt as P                                    # noqa: E402
import drivers                                   # noqa: E402

ANSWER = re.compile(r"<answer>(.*?)</answer>", re.S | re.I)
SYSTEM = ""
KINDS = ("rule", "side", "both", "near", "repeat")
TAGNAME = dict(rule="옛규칙", side="옛방향", both="옛규칙·옛방향", near="옛규칙에가까움", repeat="반복")


def parse(reply):
    """<answer> 안의 격자를 3 × 3 글자 목록으로. 칸은 '.' 또는 숫자 한 자리. 공백 · 줄바꿈 · 쉼표 · | 말고 다른 글이 섞이거나 칸이 9 개가
    아니면 None(못 읽음)."""
    found = ANSWER.findall(reply or "")
    if not found:
        return None
    body = re.sub(r"[\s,|\[\]]", "", found[-1])
    if len(body) != 9 or any(ch not in "0123456789." for ch in body):     # 지시문이 범위를 말하지 않으므로 4~9 도 받아 틀린 칸으로 매김
        return None
    return [list(body[3 * i:3 * i + 3]) for i in range(3)]


REFUSAL_TRIES = 3


def ask(driver, parts, state):
    snapshot, refusals = deepcopy(state), []
    for _ in range(REFUSAL_TRIES):
        reply, meta = driver.ask(SYSTEM, [{"role": "user", "content": parts}], state)
        meta.pop("request", None)
        if meta.get("stop_reason") != "refusal":
            break
        refusals.append(dict(stop_details=(meta.get("response") or {}).get("stop_details"), usage=meta.get("usage")))
        state.clear()
        state.update(deepcopy(snapshot))
    return reply, meta, refusals


def flat(g):
    return "".join("".join(r) for r in g)


def play(driver, episode, pool, T, k, say, mode="cells", show_prev=False, empty="."):
    log = dict(episode=episode["id"], T=T, k=k, feedback=mode, showPrev=show_prev, rounds=[], calls=[], startedAt=datetime.now(ZoneInfo("Asia/Seoul")).isoformat())
    state, feedback, prev, prev_success, last_photo = {}, None, None, None, None
    for spec in episode["rounds"]:
        n, d, col = spec["round"], spec["side"], E.rule_of(spec)
        if hasattr(driver, "round"):
            driver.round = n
        rec = dict(round=n, submissions=0, solved=False, unparsed=0, streak=0, **{kind: 0 for kind in KINDS})
        log["rounds"].append(rec)
        streak = 0
        for i in range(T):
            ph = pool[spec["photos"][i]]
            if hasattr(driver, "photo"):
                driver.photo = ph
            parts = (P.opening(ph["image"], ph.get("labels", False), empty) if feedback is None
                     else P.follow(feedback, ph["image"], last_photo if show_prev else None))
            last_photo = ph["image"]
            call = dict(round=n, submission=rec["submissions"] + 1, photo=ph["name"], prompt=P.logged(parts))
            log["calls"].append(call)
            reply, meta, refusals = ask(driver, parts, state)
            if refusals:
                call["refusals"] = refusals
            if meta.get("stop_reason") == "refusal":
                call.update(reply=reply, api=meta, grid=None, verdict="refused")
                rec["refused"] = True
                log["status"] = f"refused_round{n}"
                say(f"    R{n} #{rec['submissions'] + 1:02} →  거절 {REFUSAL_TRIES} 번: 문제를 끝냄")
                return log
            rec["submissions"] += 1
            grid = parse(reply)
            call.update(reply=reply, api=meta, grid=grid)
            if grid is None:
                rec["unparsed"] += 1
                streak, feedback = 0, P.UNREADABLE
                call["verdict"] = "unparsed"
                say(f"    R{n} #{rec['submissions']:02} →  읽을 수 없음")
                continue
            views = ph["views"]
            wrong = E.evaluate(grid, views, d, col)
            ok = not wrong
            top, same = E.best(grid, views)
            call.update(correct=ok, wrong=[list(w) for w in wrong], matches=E.matches(grid, views), best=top, bestCells=same)
            if not ok and prev is not None:
                ms = E.matches(grid, views)
                old = E.rule_of(prev)
                hits = dict(rule=E.perseverative(grid, views, old, col, d),
                            side=any(x == prev["side"] for x, _ in ms),
                            both=(prev["side"], old) in ms,
                            near=len(top) == 1 and top[0][1] == old and same >= 7,
                            repeat=prev_success is not None and flat(grid) == prev_success)
                for kind, hit in hits.items():
                    if hit:
                        rec[kind] += 1
                        call[kind] = True
            streak = streak + 1 if ok else 0
            feedback = P.say(grid, wrong, mode)
            tags = " ".join(f"[{TAGNAME[kind]}]" for kind in KINDS if call.get(kind))
            say(f"    R{n} #{rec['submissions']:02} →  {flat(grid)} {'정답' if ok else f'오답 {len(wrong)} 칸'}" + (f"  {tags}" if tags else ""))
            if ok and streak >= k:
                rec.update(solved=True, streak=streak)
                prev_success = flat(grid)
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
    ap.add_argument("--feedback", choices=["cells", "count"], default="cells")
    ap.add_argument("--prev", action="store_true",
                    help="피드백 앞에 직전 사진을 다시 붙임(2026-11-12~13 의 방식). 기본은 설명문 · 사진 · 질문 · 피드백 · 사진 · 질문 …"
                         "(사용자 2026-11-13: 직전 사진은 모델의 답 바로 위에 있으므로 다시 넣지 않음)")
    ap.add_argument("--pool", default=None, help="판 사진 묶음을 바꿔 씀(같은 씨앗 · 같은 구조물이어야 함, 예 pool_labels = 이름표 있는 사진)")
    ap.add_argument("--reasoning", default="medium")
    ap.add_argument("--max-tokens", type=int, default=32000,
                    help="응답 한 번의 출력 상한(추론 포함). 16000 이면 Opus 가 추론하다 답 없이 끊겼다(30 문제에 24 번) → 32000(2026-11-13)")
    a = ap.parse_args()
    data = json.loads((ROOT / "episodes/episodes.json").read_text())
    scheme, empty = data.get("scheme", "color"), data.get("empty", ".")
    if a.resume:                                       # 이어 돌릴 때는 그 런의 설정을 따른다
        old = json.loads((Path(a.resume) if Path(a.resume).is_absolute() else ROOT / a.resume).joinpath("index.json").read_text())
        a.pool, scheme, empty = old["pool"], old.get("scheme", "color"), old.get("empty", ".")
    pool_name = a.pool or data["pool"]
    pool = G.load_pool(pool_name, scheme, empty)
    if pool_name != data["pool"]:                      # 문제는 data["pool"] 로 만들었으므로 구조물이 같은지 확인
        base = G.load_pool(data["pool"], scheme, empty)
        if base.keys() != pool.keys() or any(base[n]["views"] != pool[n]["views"] for n in base):
            raise SystemExit(f"{pool_name} 은 {data['pool']} 과 구조물이 달라 이 문제에 쓸 수 없다")
    alleps = data["episodes"]
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
        a.feedback = index.get("feedback", "cells")
        a.prev = bool(index.get("showPrev", False))
        done = {p.stem.split("_", 1)[1] for p in out.glob(f"{label}_*.json")}
        eps = [by_id[i] for i in index["episodes"] if i not in done]
        print(f"RESULT_DIR={out}\n  이어서 돌립니다. 끝난 {len(done)}개 건너뜀, 남은 {len(eps)}개")
        if not eps:
            return
    else:
        eps = alleps[a.start:a.start + a.episodes]
        out = ROOT / "runs" / datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
        out.mkdir(parents=True)
        print(f"RESULT_DIR={out}")
    if a.T > G.T:
        raise SystemExit(f"라운드마다 사진이 {G.T} 장뿐이라 T 는 {G.T} 이하여야 한다")
    cfg = drivers.resolve_config(dict(model_id=a.model, label=label, max_tokens=a.max_tokens, reasoning=a.reasoning,
                                      timeout=600.0))
    (out / "index.json").write_text(json.dumps(dict(
        model=a.model, T=a.T, k=a.k, feedback=a.feedback, showPrev=a.prev, pool=pool_name, scheme=scheme, empty=empty, episodes=[e["id"] for e in eps] if not a.resume else index["episodes"],
        system=SYSTEM, settings=drivers.settings(cfg)), ensure_ascii=False, indent=1))
    lock = threading.Lock()

    def say(eid):
        def line(text):
            with lock:
                print(f"{eid} {text.strip()}", flush=True)
        return line

    def run_one(e):
        driver = drivers.pick(cfg)
        try:
            log = play(driver, e, pool, a.T, a.k, say(e["id"]), a.feedback, a.prev, empty)
        finally:
            driver.close()
        (out / f"{label}_{e['id']}.json").write_text(json.dumps(log, ensure_ascii=False, indent=1))
        with lock:
            print(f"  {e['id']} {log['status']}  제출 {[r['submissions'] for r in log['rounds']]}"
                  f"  옛규칙 {[r['rule'] for r in log['rounds'][1:]]}", flush=True)

    failed = []
    with ThreadPoolExecutor(max_workers=max(1, min(a.jobs, len(eps)))) as ex:
        futures = {ex.submit(run_one, e): e["id"] for e in eps}
        try:
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    failed.append(futures[f])
                    with lock:
                        print(f"  {futures[f]} 실패: {type(exc).__name__}: {str(exc)[:300]}", flush=True)
        except KeyboardInterrupt:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
    if failed:
        print(f"실패 {len(failed)}개({', '.join(sorted(failed))}): 이어서: python3 bench/runner.py {a.model} --resume {out}")


if __name__ == "__main__":
    main()
