"""C 생성기(bench/fastgen.c)로 episodes/episodes.json 을 만든다. 번호마다 씨앗이 정해져 있어 이미 있는 번호는 그대로 둔다.
    python3 bench/fastgen_run.py [개수=300] [씨앗=20261113] [병렬=8]
버튼 수 · 몫 · 판 수 · 문제 글자는 bench/generate.py 상수를 읽는다. 끝나면 bench/verify.py 로 검사한다.
문제 하나에 한 코어로 2~20 초쯤 걸린다(2026-11-13, M2)."""
import json, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
count = int(sys.argv[1]) if len(sys.argv) > 1 else 300
seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20261113
jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 8
sys.path.insert(0, str(HERE))
import generate as G                                  # noqa: E402

exe = HERE / "fastgen"
if not exe.exists() or exe.stat().st_mtime < (HERE / "fastgen.c").stat().st_mtime:
    subprocess.run(["clang", "-O2", "-o", str(exe), str(HERE / "fastgen.c")], check=True)
share = Fraction(G.SHARE).limit_denominator(100)
args = [str(G.BUTTONS), str(share.numerator), str(share.denominator), str(G.BOARDS), str(seed)]
out = ROOT / "episodes/episodes.json"
out.parent.mkdir(exist_ok=True)
done = {}
if out.exists():
    for e in json.loads(out.read_text()):
        done[int(e["id"][1:]) - 1] = e["rounds"]
todo = [i for i in range(count) if i not in done]


def one(i):
    r = subprocess.run([str(exe), *args, str(i)], capture_output=True, text=True, check=True)
    return i, json.loads(r.stdout)["rounds"]


def save():
    k = 0
    while k in done:
        k += 1
    out.write_text(json.dumps([dict(id=f"{G.PREFIX}{i + 1:03d}", buttons=G.BUTTONS, rounds=done[i]) for i in range(k)],
                              ensure_ascii=False, indent=1))
    return k


t0 = time.time()
print(f"이미 있는 {len(done)} 개는 그대로 두고 {len(todo)} 개를 만든다: 버튼 {G.BUTTONS}, 판 {G.BOARDS} 장, 몫 {share}", flush=True)
with ThreadPoolExecutor(jobs) as pool:
    for f in as_completed([pool.submit(one, i) for i in todo]):
        i, r = f.result()
        done[i] = r
        k = save()
        print(f"  {G.PREFIX}{i + 1:03d} 끝: {len(done)}/{count}, 앞에서부터 {k} 개 저장, {time.time() - t0:.0f} 초", flush=True)
print(f"끝 {len(done)}/{count}, {time.time() - t0:.0f} 초")
