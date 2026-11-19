"""지각 검사용 사진을 렌더한다(이미 있는 것은 건너뜀). 설정은 render.py 기본값과 같다.

    python3 bench/photos.py            # perception/photos/p01.png … p10.png (씨앗 2001 …, 반투명 30%)
    python3 bench/photos.py --n 20     # 더 늘릴 때
    python3 bench/photos.py --dir perception_opaque --alpha 1.0 --min-vis 0.15 --seed0 3001 --look wood --layout corners
                                       # 불투명 + 모든 블록 15% 이상 보임 + 칠한 나무 블록 + 판이 화면을 채우고 이름표는 모서리 쪽(2026-11-12)
    python3 bench/photos.py --dir pool --n 60 --seed0 4001 --alpha 1.0 --min-vis 0.15 --look wood --marks none --samples 128 \
        --size 630x540 --ext jpg       # 실험용 판 사진 묶음(generate.py --pool pool 이 읽음). JPEG 인 까닭: Claude 러너는 대화마다
                                       # 앞 사진을 모두 다시 보내므로 사진이 작을수록 요청이 가볍다(요청 한도 128 MB · 그림 600 장, vision 문서)
"""
import argparse, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "perception" / "photos"
PY = Path.home() / ".venvs/blockview/bin/python"
SEED0 = 2001


def name(i, first=1):
    return f"p{i + first:02d}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--dir", default="perception", help="결과 폴더(사진은 <폴더>/photos)")
    ap.add_argument("--alpha", type=float, default=None, help="블록 불투명도(안 주면 render.py 기본 0.30)")
    ap.add_argument("--min-vis", type=float, default=None)
    ap.add_argument("--seed0", type=int, default=SEED0)
    ap.add_argument("--first", type=int, default=1, help="첫 사진 번호(묶음에 더할 때, 예 61 → p61 …)")
    ap.add_argument("--rough", type=float, default=None, help="블록 표면 거칠기(안 주면 render.py 기본 0.18)")
    ap.add_argument("--look", choices=["flat", "wood"], default=None, help="블록 모양(안 주면 render.py 기본 flat)")
    ap.add_argument("--layout", choices=["fit", "corners"], default=None, help="판 · 이름표 배치(안 주면 render.py 기본 fit)")
    ap.add_argument("--samples", type=int, default=None, help="Cycles 표본 수(안 주면 render.py 기본 256, 노이즈 제거가 켜져 있어 128 이면 충분)")
    ap.add_argument("--size", default=None, help="그림 크기 가로x세로(안 주면 render.py 기본 1400x1200)")
    ap.add_argument("--ext", choices=["png", "jpg"], default="png", help="그림 형식")
    ap.add_argument("--near-match", default=None, help="render.py --near-match(앞 · 오른쪽 이름표의 판 위 거리)")
    ap.add_argument("--marks", choices=["labels", "cards", "none"], default=None, help="방향 표시(안 주면 render.py 기본 labels)")
    a = ap.parse_args()
    out = ROOT / a.dir / "photos"
    out.mkdir(parents=True, exist_ok=True)
    extra = (["--alpha", str(a.alpha)] if a.alpha is not None else []) + (["--min-vis", str(a.min_vis)] if a.min_vis is not None else []) \
        + (["--rough", str(a.rough)] if a.rough is not None else []) + (["--look", a.look] if a.look else []) + (["--layout", a.layout] if a.layout else []) \
        + (["--samples", str(a.samples)] if a.samples else []) + (["--size", a.size] if a.size else []) \
        + (["--near-match", a.near_match] if a.near_match else []) \
        + (["--marks", a.marks] if a.marks else [])
    for i in range(a.n):
        png = out / f"{name(i, a.first)}.{a.ext}"
        if png.exists() and png.with_suffix(".json").exists():
            continue
        t = time.time()
        r = subprocess.run([str(PY), str(ROOT / "bench/render.py"), str(a.seed0 + i), str(png)] + extra,
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(r.stdout[-2000:] + r.stderr[-2000:])
        print(f"{png.name}  씨앗 {a.seed0 + i}  {time.time() - t:.0f} 초", flush=True)
