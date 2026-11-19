# -*- coding: utf-8 -*-
"""run 폴더의 로그에서 지표와 우연 기준을 다시 계산한다.   python3 bench/score.py [runs/...]

러너 안의 집계를 믿지 않고 calls 로 다시 센다.

지속적 오류율: 세 층 평균 (2026-10-21 확정)
    라운드_i = a_i / n_i     a_i 직전 금지 전구는 한 번도 안 켜고 지금 금지 전구를 켠 오답(줄은 보지 않음),
                             n_i 채점된 제출 수
    문제_e   = 라운드 2~5 의 라운드_i 평균
    전체     = 문제_e 의 평균
분모는 **채점된 제출**(읽을 수 있었던 것)만 센다.

우연 기준: 같은 분모, 같은 세 층 평균. 제출마다, 그와 같은 수의 서로 다른 버튼을 무작위로
골랐을 때(켜는 전구는 어떤 버튼을 썼는지로만 정해진다) 그 제출의 판에서 '직전 금지 전구를 켜는
버튼은 없고 지금 금지 전구를 켜는 버튼은 있을' 확률. 빈 답은 0.

v3: 판이 제출마다 바뀌므로 지표 · 우연 모두 그 제출의 판(call 의 board)으로 따진다.
구간은 문제 단위 붓스트랩 2000 번(95%).
"""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine as E                                    # noqa: E402
from itertools import combinations                    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def chance(table, old, new, k):
    """서로 다른 버튼 k 개를 무작위로 고를 때 old 를 켜는 버튼은 없고 new 를 켜는 버튼은 있을 확률."""
    pool = list(combinations(range(len(table)), k))
    hit = sum(1 for c in pool if not E.touches(table, c, old) and E.touches(table, c, new))
    return hit / len(pool)
EPS = {e["id"]: e for e in json.loads((ROOT / "episodes/episodes.json").read_text())}


def ci(v, B=2000, seed=0):
    """문제 단위 붓스트랩 95% 구간."""
    import random
    rng = random.Random(seed)
    bs = sorted(statistics.mean(rng.choices(v, k=len(v))) for _ in range(B))
    return bs[int(.025 * B)], bs[int(.975 * B) - 1]
FLAGS = ("rule", "goal", "both", "repeat")


def problem(log):
    """문제 하나의 지속적 오류율 · 우연 · 오답률, 참고 깃발 비율."""
    ep = EPS[log["episode"]]
    per, ch, err, ref = [], [], [], {k: [] for k in FLAGS}
    for i, rd in enumerate(log["rounds"]):
        sc = [c for c in log["calls"] if c["round"] == rd["round"] and c.get("presses") is not None]
        if not sc:
            continue
        err.append(sum(not c["correct"] for c in sc) / len(sc))
        if not i:
            continue
        spec, old = ep["rounds"][i], ep["rounds"][i - 1]["forbidden"]
        new = spec["forbidden"]
        t = lambda c: spec["boards"][c["board"]]["table"]          # noqa: E731
        per.append(sum(1 for c in sc if not c["correct"] and not E.touches(t(c), c["presses"], old)
                       and E.touches(t(c), c["presses"], new)) / len(sc))
        ch.append(sum(chance(t(c), old, new, len(set(c["presses"]))) for c in sc if c["presses"]) / len(sc))
        for k in FLAGS:
            ref[k].append(sum(bool(c.get(k)) for c in sc) / len(sc))
    mean = lambda v: statistics.mean(v) if v else None      # noqa: E731
    return dict(per=mean(per), chance=mean(ch), err=mean(err) if err else 1.0,
                ref={k: mean(v) for k, v in ref.items()})


def main(folder):
    folder = Path(folder)
    index = json.loads((folder / "index.json").read_text())
    logs = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json")) if p.name != "index.json"]
    n = len(index["episodes"])
    print(f"{index['model']}  T={index['T']} k={index['k']}  에피소드 {len(logs)}/{n}"
          + ("  (진행 중)" if len(logs) < n else ""))
    if not logs:
        return
    print(f"  상태 {dict(Counter(l['status'] for l in logs))}")
    r1 = [l["rounds"][0] for l in logs]
    print(f"  라운드 1  해결 {sum(x['solved'] for x in r1)}/{len(r1)}"
          f"  제출 중앙값 {statistics.median([x['submissions'] for x in r1])}"
          f"   다섯 라운드 완주 {sum(l['status'] == 'completed' for l in logs)}/{len(logs)}")
    probs = [problem(l) for l in logs]
    inc = [p for p in probs if p["per"] is not None]
    if inc:
        mpe = statistics.mean(p["per"] for p in inc)
        ch = statistics.mean(p["chance"] for p in inc)
        lo, hi = ci([p["per"] for p in inc])
        print(f"  forbid  {mpe:6.1%} [{lo:.1%}-{hi:.1%}]  (문제 {len(inc)}개)   우연 {ch:5.1%}  → {mpe / ch:4.1f}×")
        print("  참고    " + "  ".join(
            f"{k} {statistics.mean(p['ref'][k] for p in inc if p['ref'][k] is not None):5.1%}"
            for k in FLAGS))
    print(f"  오답률  {statistics.mean(p['err'] for p in probs):6.1%}")
    bad = sum(x["unparsed"] for l in logs for x in l["rounds"])
    sub = sum(x["submissions"] for l in logs for x in l["rounds"])
    print(f"  읽을 수 없는 응답 {bad}/{sub} ({bad / sub:.1%})")


if __name__ == "__main__":
    for f in sys.argv[1:] or sorted((ROOT / "runs").glob("*")):
        main(f); print()
