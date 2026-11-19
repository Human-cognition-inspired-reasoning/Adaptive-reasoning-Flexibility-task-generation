# -*- coding: utf-8 -*-
"""run 폴더의 로그에서 지표와 우연 기준을 다시 계산한다.   python3 bench/score.py [runs/...]

지속적 오류율: 세 층 평균(다른 과제와 같음)
    라운드_i = a_i / n_i     a_i 직전 규칙으로 쓴 격자(어느 방향이든, 예전 판은 직전 색)이고 지금 정답이 아닌 답, n_i 채점된 제출 수(읽을 수 있었던 것)
    문제_e   = 라운드 2~5 의 라운드_i 평균(도달한 라운드만)
    전체     = 문제_e 의 평균
우연 기준: 같은 분모, 같은 세 층 평균. 제출마다 답의 '.' 칸은 그대로 두고 숫자 칸을 0~3 에서 고르게 뽑았을 때
    지속적 오류가 될 확률(engine.chance).
참고: side · both · near · repeat 몫(runner.py 머리말), 그리고 지금 방향을 찾은 뒤(그 라운드에서 지금 방향의 어느 색 격자와
    똑같은 답을 처음 낸 제출부터)의 지속적 오류 몫. 지표가 방향 찾기 길이와 섞이는지 본다."""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine as E                                    # noqa: E402
import generate as G                                  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FLAGS = ("side", "both", "near", "repeat")


def problem(log, ep, pool, empty="."):
    per, ch, err, after, ref = [], [], [], [], {k: [] for k in FLAGS}
    for i, rd in enumerate(log["rounds"]):
        sc = [c for c in log["calls"] if c["round"] == rd["round"] and c.get("grid") is not None]
        if not sc:
            continue
        spec = ep["rounds"][i]
        d, new = spec["side"], E.rule_of(spec)
        err.append(sum(not E.grid_eq(c["grid"], pool[c["photo"]]["views"][d][new]) for c in sc) / len(sc))
        if not i:
            continue
        old = E.old_rule_of(spec)
        p = [E.perseverative(c["grid"], pool[c["photo"]]["views"], old, new, d) for c in sc]
        per.append(sum(p) / len(sc))
        ch.append(sum(E.chance(c["grid"], pool[c["photo"]]["views"], old, empty) for c in sc) / len(sc))
        first = next((j for j, c in enumerate(sc) if any(x == d for x, _ in E.matches(c["grid"], pool[c["photo"]]["views"]))), None)
        if first is not None:
            after.append(sum(p[first:]) / len(sc[first:]))
        for k in FLAGS:
            ref[k].append(sum(bool(c.get(k)) for c in sc) / len(sc))
    mean = lambda v: statistics.mean(v) if v else None      # noqa: E731
    return dict(per=mean(per), chance=mean(ch), err=mean(err) if err else 1.0, after=mean(after),
                ref={k: mean(v) for k, v in ref.items()})


def main(folder, eps):
    folder = Path(folder)
    index = json.loads((folder / "index.json").read_text())
    empty = index.get("empty", ".")
    pool = G.load_pool(index["pool"], index.get("scheme", "color"), empty)
    logs = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json")) if p.name != "index.json"]
    n = len(index["episodes"])
    print(f"{index['model']}  T={index['T']} k={index['k']}  피드백 {index.get('feedback', 'cells')}  문제 {len(logs)}/{n}" + ("  (진행 중)" if len(logs) < n else ""))
    if not logs:
        return
    print(f"  상태 {dict(Counter(l['status'] for l in logs))}")
    r1 = [l["rounds"][0] for l in logs]
    print(f"  라운드 1  해결 {sum(x['solved'] for x in r1)}/{len(r1)}"
          f"  제출 중앙값 {statistics.median([x['submissions'] for x in r1])}"
          f"   다섯 라운드 완주 {sum(l['status'] == 'completed' for l in logs)}/{len(logs)}")
    probs = [problem(l, eps[l["episode"]], pool, empty) for l in logs]
    inc = [p for p in probs if p["per"] is not None]
    if inc:
        mpe = statistics.mean(p["per"] for p in inc)
        ch = statistics.mean(p["chance"] for p in inc)
        print(f"  규칙    {mpe:6.1%}  (문제 {len(inc)}개)   우연 {ch:6.2%}" + (f"  → {mpe / ch:5.0f}×" if ch else ""))
        aft = [p["after"] for p in inc if p["after"] is not None]
        if aft:
            print(f"  지금 방향을 찾은 뒤의 지속적 오류 몫 {statistics.mean(aft):6.1%}  (문제 {len(aft)}개)")
        print("  참고    " + "  ".join(
            f"{k} {statistics.mean(p['ref'][k] for p in inc if p['ref'][k] is not None):5.1%}" for k in FLAGS))
    print(f"  오답률  {statistics.mean(p['err'] for p in probs):6.1%}")
    bad = sum(x["unparsed"] for l in logs for x in l["rounds"])
    sub = sum(x["submissions"] for l in logs for x in l["rounds"])
    print(f"  읽을 수 없는 응답 {bad}/{sub} ({bad / sub:.1%})" if sub else "  제출 없음")


if __name__ == "__main__":
    # 지난 시험 실행의 문제(B · K)는 archive/episodes/ 에 있다. 문제 번호는 파일끼리 겹치지 않는다.
    files = sorted((ROOT / "archive" / "episodes").glob("episodes*.json")) + sorted((ROOT / "episodes").glob("episodes*.json"))
    EPS = {e["id"]: e for f in files for e in json.loads(f.read_text())["episodes"]}
    for f in sys.argv[1:] or sorted((ROOT / "runs").glob("*")):
        main(f, EPS)
        print()
