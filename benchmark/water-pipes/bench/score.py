# -*- coding: utf-8 -*-
"""run 폴더의 로그에서 지표와 우연 기준을 다시 계산한다.   python3 bench/score.py [runs/...]  또는  --merge runs/A runs/B

러너 안의 집계를 믿지 않고 calls 로 다시 센다. v3: 판이 제출마다 바뀌므로 호출마다 그 제출의 판(call 의 board)으로 따진다.

지속적 오류율: 세 층 평균 (2026-10-21 확정, v2 와 같음)
    라운드_i = a_i / n_i     a_i 직전 띠만 지킨 오답(band: 직전 띠를 어긴 곳이 없음, 물이 어디 닿았는지는 보지 않음),
                             n_i 채점된 제출 수(읽을 수 있었던 것)
    문제_e   = 라운드 2~5 의 라운드_i 평균(처음 못 푼 라운드까지)
    전체     = 문제_e 의 평균
참고로 rule(직전 띠로 지금 목표까지 달성한 오답, v1 물길의 정의)도 뽑는다.

우연 기준: 같은 분모, 같은 세 층 평균. 제출마다, 같은 길이의 클릭 순서를 그 판의 파이프 타일에서 고르게 뽑았을 때
직전 띠를 지킬 확률(띠가 서로소라 직전 띠를 지키면 지금은 늘 틀린다). 빈 답은 0.
구간은 문제 단위 붓스트랩 2000 번(95%).
"""
import json
import random
import statistics
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine as E                                    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EPS = {e["id"]: e for f in sorted((ROOT / "archive/episodes").glob("*.json")) + [ROOT / "episodes/episodes.json"]
       for e in json.loads(f.read_text())}                       # archive: 지나온 Z · X · N 문제


@lru_cache(maxsize=None)
def runs_ok(L, lo, hi, n):
    """길이 L 클릭 순서(n 개 타일에서 고르게) 가운데 이어 누른 묶음이 모두 [lo, hi] 안인 몫."""
    hi = hi if hi is not None else L
    # f[m] = 길이 m 을 묶음들로 채우는 경우의 수(첫 묶음은 n 가지, 다음 묶음부터 n-1 가지 타일)
    f = [0] * (L + 1)
    f[0] = 1
    for m in range(1, L + 1):
        f[m] = sum(f[m - r] * (n if m == r else n - 1) for r in range(lo, min(hi, m) + 1))
    return f[L] / n ** L


def chance(board, clicks, old_rule):
    if not clicks:
        return 0.0
    n = sum(t["kind"] == E.PIPE for t in board["tiles"])
    return runs_ok(len(clicks), old_rule["lo"], old_rule.get("hi"), n)


def ci(v, B=2000, seed=0):
    rng = random.Random(seed)
    bs = sorted(statistics.mean(rng.choices(v, k=len(v))) for _ in range(B))
    return bs[int(.025 * B)], bs[int(.975 * B) - 1]


def problem(log):
    """문제 하나의 band · rule 지속적 오류율, 우연 기준, 오답률."""
    ep = EPS[log["episode"]]
    band, rule, ch, err = [], [], [], []
    for i, rd in enumerate(log["rounds"]):
        sc = [c for c in log["calls"] if c["round"] == rd["round"] and c.get("clicks") is not None]
        if not sc:
            continue
        err.append(sum(not c["correct"] for c in sc) / len(sc))
        if not i:
            continue
        spec, prev = ep["rounds"][i], ep["rounds"][i - 1]
        b = lambda c: spec["boards"][c["board"]]["board"]          # noqa: E731
        band.append(sum(1 for c in sc if not c["correct"] and not any(E.violations(b(c), c["clicks"], prev["rule"])))
                    / len(sc))
        rule.append(sum(1 for c in sc if not c["correct"] and E.evaluate(b(c), c["clicks"], spec["goal"], prev["rule"])["ok"])
                    / len(sc))
        ch.append(sum(chance(b(c), c["clicks"], prev["rule"]) for c in sc) / len(sc))
    mean = lambda v: statistics.mean(v) if v else None      # noqa: E731
    return dict(band=mean(band), rule=mean(rule), chance=mean(ch), err=mean(err) if err else 1.0)


def main(*folders):
    """런 폴더 하나, 또는 같은 모델로 이어 돌린 폴더 여럿(`--merge`)을 한 번에 센다."""
    folders = [Path(f) for f in folders]
    index = json.loads((folders[0] / "index.json").read_text())
    logs = [json.loads(p.read_text()) for f in folders for p in sorted(f.glob("*.json")) if p.name != "index.json"]
    n = len({e for f in folders for e in json.loads((f / "index.json").read_text())["episodes"]})
    print(f"{index['model']}  T={index['T']} k={index['k']}  문제 {len(logs)}/{n}" + ("  (진행 중)" if len(logs) < n else ""))
    if not logs:
        return
    print(f"  상태 {dict(Counter(l['status'] for l in logs))}")
    r1 = [l["rounds"][0] for l in logs]
    print(f"  라운드 1  해결 {sum(x['solved'] for x in r1)}/{len(r1)}"
          f"  제출 중앙값 {statistics.median([x['submissions'] for x in r1])}"
          f"   다섯 라운드 완주 {sum(l['status'] == 'completed' for l in logs)}/{len(logs)}")
    probs = [problem(l) for l in logs]
    inc = [p for p in probs if p["band"] is not None]
    if inc:
        mpe = statistics.mean(p["band"] for p in inc)
        ch = statistics.mean(p["chance"] for p in inc)
        lo, hi = ci([p["band"] for p in inc])
        print(f"  band    {mpe:6.1%} [{lo:.1%}~{hi:.1%}]  (문제 {len(inc)}개)   우연 {ch:5.1%}" + (f"  → {mpe / ch:4.1f}×" if ch else ""))
        print(f"  rule    {statistics.mean(p['rule'] for p in inc):6.1%}  (참고: 직전 띠로 지금 목표까지 달성한 오답)")
    print(f"  오답률  {statistics.mean(p['err'] for p in probs):6.1%}")
    bad = sum(x["unparsed"] for l in logs for x in l["rounds"])
    sub = sum(x["submissions"] for l in logs for x in l["rounds"])
    print(f"  읽을 수 없는 응답 {bad}/{sub} ({bad / sub:.1%})" if sub else "  제출 없음")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--merge":
        main(*args[1:])
    else:
        for f in args or sorted((ROOT / "runs").glob("*")):
            main(f)
        print()
