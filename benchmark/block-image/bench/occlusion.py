"""카메라에서 블록이 다른 블록에 가려지는 정도와, 그 블록이 끼는 답 칸 비율.

블록마다 카메라 쪽 면(위 · 앞 · 오른쪽)에 8×8 점을 찍고, 점에서 카메라로 가는 선이 다른 블록을
지나지 않는 몫(면이 카메라를 향한 정도로 가중)을 '보이는 몫'으로 잰다. 보이는 몫이 THR 미만인
블록을 가려진 블록으로 치고, 그 층 그 시선 줄에 가려진 블록이 하나라도 있는 숫자 칸을 센다.
반투명이라 가려진 블록도 비쳐 보이지만, 앞 블록 색을 거쳐서만 보인다.

    python3 bench/occlusion.py                       # 지금 렌더(간격 1.0, 높이각 50)
    python3 bench/occlusion.py --pitch 2.0 --el 50   # 기둥 중심 간격 2 (기둥 사이 1칸 띄움)
"""
import argparse, math, sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structure as S

K = 8
G = (np.arange(K) + 0.5) / K - 0.5
NORMALS = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1)]


def cam(az, el, dist=14.0, tg=(0, 0, 0.9)):          # render.py 와 같은 카메라 위치
    a, e = math.radians(az), math.radians(el)
    hh = dist * math.cos(e)
    return np.array([tg[0] + hh * math.sin(a), tg[1] - hh * math.cos(a), tg[2] + dist * math.sin(e)])


def face_points(x, y, z0, n):
    c = np.array([x, y, z0 + 0.5]) + (0.5 + 1e-4) * np.array(n)
    ax = [i for i in range(3) if n[i] == 0]
    P = np.zeros((K * K, 3)) + c
    u, v = np.meshgrid(G, G)
    P[:, ax[0]] += u.ravel()
    P[:, ax[1]] += v.ravel()
    return P


def visibility(H, cpos, pitch=1.0):
    """{(r, c, z): 보이는 몫 0~1}"""
    blocks = [(r, c, z) for r in range(S.N) for c in range(S.N) for z in range(H[r][c])]
    ctr = {b: ((b[1] - 1) * pitch, (1 - b[0]) * pitch) for b in blocks}
    lo = np.array([[ctr[b][0] - .5, ctr[b][1] - .5, b[2]] for b in blocks])
    hi = lo + 1
    occ = set(blocks)
    vis = {}
    for i, b in enumerate(blocks):
        r, c, z = b
        num = den = 0.0
        for n in NORMALS:
            P = face_points(*ctr[b], z, n)
            D = cpos - P
            w = (D @ np.array(n)) / np.linalg.norm(D, axis=1)
            front = w > 0
            if not front.any():
                continue
            nb = (r - n[1], c + n[0], z + n[2])
            glued = nb in occ and (n[2] == 1 or pitch == 1.0)
            if glued:                                  # 이웃 블록에 붙은 면
                den += w[front].sum()
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                t1 = (lo[None] - P[:, None]) / D[:, None]
                t2 = (hi[None] - P[:, None]) / D[:, None]
            tmin = np.nanmax(np.minimum(t1, t2), axis=2)
            tmax = np.nanmin(np.maximum(t1, t2), axis=2)
            hit = (tmax > np.maximum(tmin, 1e-6)) & (tmin < 1)
            hit[:, i] = False
            clear = ~hit.any(axis=1)
            num += w[front & clear].sum()
            den += w[front].sum()
        vis[b] = num / den if den else 0.0
    return vis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pitch", type=float, default=1.0)
    ap.add_argument("--el", type=float, default=50)
    ap.add_argument("--az", type=float, default=40)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--thr", type=float, default=0.25)
    a = ap.parse_args()
    cp = cam(a.az, a.el)
    hard = np.zeros((3, 3)); tot = np.zeros((3, 3))
    nblk = low = 0
    for seed in range(a.n):
        H, C = S.generate(seed)
        v = visibility(H, cp, a.pitch)
        nblk += len(v)
        low += sum(x < a.thr for x in v.values())
        for d in S.DIRS:
            for li, line in enumerate(S.lines(d)):
                for z in range(1, S.HMAX + 1):
                    bs = [(r, c, z - 1) for (r, c) in line if H[r][c] >= z]
                    if bs:
                        tot[S.HMAX - z, li] += 1
                        hard[S.HMAX - z, li] += any(v[b] < a.thr for b in bs)
    print(f"간격 {a.pitch} 높이각 {a.el}: 보이는 몫 {a.thr:.0%} 미만 블록 {100 * low / nblk:.1f}%, "
          f"가려진 블록이 낀 숫자 칸 {100 * hard.sum() / tot.sum():.1f}%")
    print("칸별 % (네 방향 합, 윗줄 = 3층, 왼 · 가운데 · 오른)")
    for row in range(S.HMAX):
        print("  ", "  ".join(f"{100 * hard[row, l] / tot[row, l]:5.1f}" for l in range(S.N)))


if __name__ == "__main__":
    main()
