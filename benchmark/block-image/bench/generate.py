# -*- coding: utf-8 -*-
"""문제 만들기.   python3 bench/generate.py [--n 10] [--seed 20261112] [--pool pool] [--scheme kind|color]

새 판(kind, 2026-11-12): 규칙 = 칸 숫자의 뜻(count 블록 수 · near 가장 가까운 블록이 몇째 칸 · colors 색 가짓수), 문제 V001~.
예전 판(color): 규칙 = 세는 색, 문제 B001~(archive/episodes/episodes_color.json).

한 문제 = 다섯 라운드. 라운드마다 숨은 방향(목표, 앞 · 뒤 · 왼쪽 · 오른쪽)과 숨은 색(규칙, 빨강 · 파랑 · 초록)을 정하고,
제출마다 새 사진을 준다(라운드당 T = 10 장을 미리 정해 둠). 사진은 판 사진 묶음(<pool>/photos/p*.jpg 또는 .png, 정답은 같은 이름 .json)
에서 한 문제 안에서 겹치지 않게 뽑는다. 문제끼리는 같은 사진을 쓸 수 있다(대화가 따로라 상관없음).

보장
  · 방향과 색은 라운드마다 바뀐다(직전과 다름). 1 라운드는 무작위.
  · 사진마다 12 가지 답이 모두 다르다(structure.ok): 직전 색 · 직전 방향으로 쓴 답은 늘 틀린다.
  · 사진마다 정답 격자에 1 이상인 칸이 있다(규칙 색이 그 방향에서 안 보이는 사진은 안 씀).
  · 사진마다 색을 가리지 않고 센 격자(규칙을 모를 때의 기본 답)가 정답이 아니다(color 판).
  · 한 라운드 열 장의 정답 격자가 모두 다르고, 라운드 첫 장의 정답은 직전 라운드 마지막 장의 정답과 다르다
    (distinctAnswers, 2026-11-13). 맞힌 답을 그대로 다시 내면 늘 틀린다.

문제 번호: kind 판은 V001~(2026-11-13 300 문제). K001~K030(11-12~13 시험 실행)은 archive/episodes/episodes_k30.json 에 남겨
지난 실행 채점용으로만 쓴다. 번호가 겹치면 score 가 엉뚱한 문제로 채점하므로 새 판은 다른 앞글자를 쓴다.
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import engine as E                                    # noqa: E402

ROUNDS, T = 5, 10


SCHEMES = ("color", "kind")      # color: 예전 판(색 하나를 셈) · kind: 새 판(count · near · colors, 2026-11-12)


def load_pool(pool, scheme="color", empty="."):
    """판 사진 묶음. scheme color 는 .json 의 views(색 셋)를 쓰고, kind 는 H · C 에서 규칙 셋의 격자를 새로 계산하며
    12 가지 격자가 모두 다른 사진만 남긴다(structure.ok_rules)."""
    import structure as S
    photos = {}
    base = ROOT / pool if (ROOT / pool).exists() else ROOT / "archive" / pool     # 예전 묶음(pool_labels 등)은 archive/ 에
    for js in sorted((base / "photos").glob("p*.json")):
        png = next((js.with_suffix(x) for x in (".jpg", ".png") if js.with_suffix(x).exists()), None)
        if png is None:
            continue
        t = json.loads(js.read_text())
        if scheme == "kind":
            if not S.ok_rules(t["H"], t["C"]):
                continue
            views = S.all_rule_views(t["H"], t["C"], empty)
        else:
            views = t["views"]
        photos[js.stem] = dict(name=js.stem, image=str(png.relative_to(ROOT)), H=t["H"], C=t["C"], views=views,
                               labels=(t.get("render") or {}).get("marks") == "labels")
    return photos


def fits(ph, d, k, scheme="color", empty="."):
    want = ph["views"][d][k]
    if scheme == "kind":                       # 그 방향에서 블록이 하나라도 보이면 됨(12 격자가 다 다르다는 것은 load_pool 이 보장)
        return any(x != empty for row in want for x in row)
    if not any(x not in (".", "0") for row in want for x in row):
        return False
    return not E.grid_eq(E.total_view(ph["H"], ph["C"], d), want)


def answer(ph, d, k):
    return json.dumps(ph["views"][d][k])


def episode(rng, pool, eid, scheme="color", empty="."):
    import structure as S
    names = list(pool)
    rules = list(E.COLORS) if scheme == "color" else list(S.RULES)
    key, okey = ("color", "oldColor") if scheme == "color" else ("rule", "oldRule")
    for _ in range(500):
        used, rounds, prev = set(), [], None
        for n in range(1, ROUNDS + 1):
            d = rng.choice([s for s in E.SIDES if not prev or s != prev["side"]])
            k = rng.choice([c for c in rules if not prev or c != prev[key]])
            cand = [p for p in names if p not in used and fits(pool[p], d, k, scheme, empty)]
            rng.shuffle(cand)
            # 라운드 안 열 장의 정답 격자가 모두 다르고, 첫 장의 정답이 직전 라운드 마지막 장의 정답과도 다르게
            # (같은 답을 다시 내서 k 를 채우는 일이 없게, 2026-11-13)
            last = answer(pool[prev["photos"][-1]], prev["side"], prev[key]) if prev else None
            photos, grids = [], {last}
            for p in cand:
                g = answer(pool[p], d, k)
                if g not in grids:
                    photos.append(p)
                    grids.add(g)
                    if len(photos) == T:
                        break
            if len(photos) < T:
                break
            used.update(photos)
            spec = {"round": n, "side": d, key: k, "oldSide": prev["side"] if prev else None,
                    okey: prev[key] if prev else None, "photos": photos}
            rounds.append(spec)
            prev = spec
        if len(rounds) == ROUNDS:
            return dict(id=eid, rounds=rounds)
    raise RuntimeError(f"{eid}: 사진이 모자라 다섯 라운드를 못 채움")


def build(pool, n, seed, prefix="B", scheme="color", empty=".", start=1):
    rng = random.Random(seed)
    return [episode(rng, pool, f"{prefix}{i:03d}", scheme, empty) for i in range(start, n + 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--pool", default="pool")
    ap.add_argument("--scheme", choices=SCHEMES, default="kind")
    ap.add_argument("--prefix", default=None, help="문제 번호 앞글자(안 주면 kind = V, color = B). K 는 시험 실행에 썼으므로 쓰지 않음")
    ap.add_argument("--out", default="episodes.json")
    ap.add_argument("--empty", choices=[".", "0"], default="0", help="빈칸 기호(새 판은 0, 2026-11-12 사용자 '빈칸0으로'). 색 판은 늘 '.'")
    ap.add_argument("--extend", action="store_true",
                    help="--out 파일의 문제를 그대로 두고 뒤에 이어 --n 개까지 채움(이미 돌린 문제 번호가 바뀌지 않게)")
    a = ap.parse_args()
    if a.scheme == "color":
        a.empty = "."
    pool = load_pool(a.pool, a.scheme, a.empty)
    if len(pool) < ROUNDS * T:
        sys.exit(f"쓸 수 있는 판 사진이 {len(pool)} 장뿐이다(한 문제에 {ROUNDS * T} 장 필요)")
    prefix = a.prefix or ("V" if a.scheme == "kind" else "B")
    out = ROOT / "episodes"
    kept = []
    if a.extend and (out / a.out).exists():
        old = json.loads((out / a.out).read_text())
        if (old["pool"], old["scheme"], old.get("empty", ".")) != (a.pool, a.scheme, a.empty):
            sys.exit(f"--extend: 기존 파일의 판 사진 묶음 · 판 · 빈칸 기호가 다름 {old['pool'], old['scheme'], old.get('empty')}")
        kept = old["episodes"]
        for e in kept:                     # 남길 문제의 사진이 지금 묶음에도 그대로 있어야 함
            for r in e["rounds"]:
                miss = [p for p in r["photos"] if p not in pool]
                if miss:
                    sys.exit(f"--extend: {e['id']} 의 사진 {miss} 가 지금 묶음에서 빠짐")
    eps = kept + build(pool, a.n, a.seed + len(kept), prefix, a.scheme, a.empty, start=len(kept) + 1)
    out.mkdir(exist_ok=True)
    (out / a.out).write_text(json.dumps(dict(pool=a.pool, scheme=a.scheme, empty=a.empty, distinctAnswers=True, episodes=eps),
                                        ensure_ascii=False, indent=1))
    print(f"문제 {len(eps)} 개 → episodes/{a.out} (판 {a.scheme}, 사진 묶음 {a.pool}, 쓸 수 있는 사진 {len(pool)} 장)")


if __name__ == "__main__":
    main()
