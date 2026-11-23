"""H1-H3 verdicts for the 12x12 100-session run, definitions 1 and 2 side by side (preregistration/PREREGISTRATION_V32_100.md;
definition 2 = results/DEFINITION.md). No model calls.

  python -m analysis.verdict      # writes results/VERDICT.md

H1: feedback minus baseline persistent error rate, supported if the 95% upper bound < 0. H2: pairs of H1-passing models
(same sessions, stratified), judgeable only with two or more passing models; supported if any pair's interval excludes 0.
H3 (two-sided): for H1-passing models, rounds whose new (start, goal) has different NB and AV answers minus the others.
"""
from collections import Counter

import numpy as np

from .common import MODELS, ROOT, h1, h3, load, pair_test, rounds

FAILS = ("wrong_end", "rule", "wall", "out_of_bounds", "parse")


def main():
    F = {m: rounds(load("feedback", m)) for m in MODELS}
    B = {m: rounds(load("nofeedback", m)) for m in MODELS}
    L = ["# 판정: 12×12 미로, 100세션, 추론 노력 medium", "",
         "지속적 오류율 정의 2(주 지표)와 정의 1(사전 등록)을 나란히 싣는다. 신뢰구간: 세션 부트스트랩 1000회, 시드 0. 거부와 OpenAI invalid_prompt 차단은",
         "모든 지표에서 뺐다. 생성: `python -m analysis.verdict`.", "",
         "## 모델별 지속적 오류율과 H1", "",
         "| 모델 | 정의 2 피드백 [95%] | 정의 2 기준선 | 정의 2 차이 [95%] | 정의 2 H1 | 정의 1 피드백 / 기준선 | 정의 1 차이 [95%] | 정의 1 H1 |",
         "|---|---|---|---|---|---|---|---|"]
    passing = {}
    for f in ("v2", "v1"):
        passing[f] = [m for m in MODELS if h1(F[m], B[m], f)[4][1] < 0]
    for m in MODELS:
        b, a = h1(F[m], B[m], "v2"), h1(F[m], B[m], "v1")
        L.append(f"| {m} | {b[0]:.3f} [{b[1][0]:.3f}, {b[1][1]:.3f}] | {b[2]:.3f} | {b[3]:+.3f} [{b[4][0]:+.3f}, {b[4][1]:+.3f}] | {'지지' if b[4][1] < 0 else '기각'} | "
                 f"{a[0]:.3f} / {a[2]:.3f} | {a[3]:+.3f} [{a[4][0]:+.3f}, {a[4][1]:+.3f}] | {'지지' if a[4][1] < 0 else '기각'} |")
    for f, lab in (("v2", "정의 2"), ("v1", "정의 1")):
        p = passing[f]
        L += ["", f"## H2·H3 ({lab})", "", f"H1 통과 {len(p)}/{len(MODELS)}: {', '.join(p) or '없음'}. H2는 " + ("판정 가능." if len(p) >= 2 else "판정 불가(통과 모델 둘 미만).")]
        if len(p) >= 2:
            pt = pair_test(F, p, f)
            L += ["", "| 쌍 | 차이 [95%] | 0 배제 |", "|---|---|---|"] + [f"| {x} − {y} | {d:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}] | {'예' if ex else '아니오'} |" for x, y, d, ci, ex in pt]
            L += ["", f"**H2: {'지지' if any(t[4] for t in pt) else '기각'}**"]
        rows3 = []
        for m in p:
            v, ci, na, nb = h3(F[m], f)
            rows3.append((m, v, ci, na, nb, not (ci[0] <= 0 <= ci[1])))
        L += ["", "| H3 모델 | 갈림 라운드 | 갈리지 않음 | 차이 [95%] | 0 배제 |", "|---|---|---|---|---|"] + \
             [f"| {m} | {na} | {nb} | {v:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}] | {'예' if ex else '아니오'} |" for m, v, ci, na, nb, ex in rows3]
        L += ["", f"**H3: {('지지' if any(r[5] for r in rows3) else '기각') if p else '판정 불가'}**"]
    L += ["", "## 보조 지표 (피드백 조건)", "", "| 모델 | 적응 성공률 | 미도달 전환 | 라운드당 시도 | 전환당 보속 시도(정의 1) | 거부(그중 invalid_prompt) | 잘림 |", "|---|---|---|---|---|---|---|"]
    for m in MODELS:
        rec = [r for v in F[m].values() for r in v]
        reach = [r["reached"] for r in rec if r["reached"] is not None]
        allrows = load("feedback", m, graded=False) + load("nofeedback", m, graded=False)
        ref = [r for r in allrows if r["fail"] == "refusal"]
        L.append(f"| {m} | {np.mean(reach):.3f} | {sum(not x for x in reach)}/{len(reach)} | {np.mean([r['attempts'] for r in rec]):.2f} | "
                 f"{np.mean([r['persist_count'] for r in rec]):.2f} | {len(ref)}/{len(allrows)} ({sum(r['api'].get('refusal_kind') == 'invalid_prompt' for r in ref)}) | "
                 f"{sum(r.get('truncated', False) for r in allrows)} |")
    L += ["", "## 실패 유형 (피드백, 채점 시도)", "", "| 모델 | 실패 수 | 다른 칸에서 끝남 | 목표 도착·규칙 위반 | 벽 통과 | 미로 이탈 | 답 읽기 실패 |", "|---|---|---|---|---|---|---|"]
    for m in MODELS:
        c = Counter(r["fail"] for r in load("feedback", m) if r["fail"])
        n = sum(c.values())
        L.append(f"| {m} | {n} | " + " | ".join(f"{c.get(x, 0)} ({c.get(x, 0) / n:.2f})" for x in FAILS) + " |")
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results/VERDICT.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
