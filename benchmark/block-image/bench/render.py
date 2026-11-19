"""구조물 사진 한 장을 Blender(Cycles)로 렌더하고, 정답을 같은 이름의 .json 에 쓴다.

그림 생성 AI 가 아니라 코드로 3D 장면을 짓고 빛을 계산해 그린다. 구조물은 structure.py 가
씨앗으로 뽑으므로 사진과 정답이 늘 일치한다.

    ~/.venvs/blockview/bin/python bench/render.py SEED samples/이름.png [--marks labels|cards]
                                   [--alpha 0.30] [--samples 256] [--az 40] [--el 50] [--pitch 1.0] [--min-vis 0.15]

--marks labels : 판 위에 방향 이름(FRONT · BACK · LEFT · RIGHT)과 구조물을 가리키는 화살표
--marks cards  : 방향마다 세워 둔 양면 카드(위에 이름, 아래 빈 3×3 칸, 맨 아랫줄이 바닥)
이름은 그 방향에 선 사람이 바로 읽는 쪽으로 놓는다.

--pitch : 기둥 중심 사이 거리(블록 한 변 = 1). 1.0 이면 기둥끼리 붙고, 2.0 이면 기둥 사이가 한 칸 빈다.
판 한가운데를 지나는 줄의 1층 블록이 사방으로 막혀 가려지는 것을 줄이려고 둔다(occlusion.py).
간격이 1 보다 크면 바닥에는 기둥마다 네모 칸을 따로 그린다.

카메라는 앞-오른쪽(--az, 앞에서 오른쪽으로 돈 각)에서 내려다본다(--el). 렌즈와 시프트는
이름표 · 카드와 높이 3 짜리 꽉 찬 구조물이 모두 화면에 들어오도록 자동으로 맞춘다
(구조물이 바뀌어도 카메라는 같다).

venv 만들기(Python 3.13 이어야 bpy 5.2.3 이 깔린다):
    python3.13 -m venv ~/.venvs/blockview && ~/.venvs/blockview/bin/pip install bpy==5.2.3
venv 를 바탕화면(iCloud 동기화) 밖에 두는 것은 크기가 약 770 MB 이기 때문이다.
"""
import argparse, json, math, os, sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structure as S

ap = argparse.ArgumentParser()
ap.add_argument("seed", type=int)
ap.add_argument("out")
ap.add_argument("--marks", choices=["labels", "cards", "none"], default="labels",
                help="none: 방향 표시 없음(사용자 2026-11-12 '글자를 지울까? 필요없는 정보'). 카메라가 늘 같아 네 면은 사진 속 위치로 갈린다")
ap.add_argument("--alpha", type=float, default=0.30, help="블록 불투명도(0~1)")
ap.add_argument("--samples", type=int, default=256)
ap.add_argument("--az", type=float, default=40.0)
ap.add_argument("--el", type=float, default=50.0)
ap.add_argument("--arrow-gap", type=float, default=1.0, help="labels: 구조물 가장자리 ~ 화살표 끝")
ap.add_argument("--pitch", type=float, default=1.0, help="기둥 중심 사이 거리(1.0 = 붙음)")
ap.add_argument("--min-vis", type=float, default=None, help="모든 블록이 카메라에서 이 몫 이상 보이는 구조물만(불투명 판, 예 0.15)")
ap.add_argument("--rough", type=float, default=0.18, help="블록 표면 거칠기. 0.18 은 반들반들해 옆 블록이 비쳐 반투명처럼 보일 수 있다(불투명 판은 0.5)")
ap.add_argument("--layout", choices=["fit", "corners"], default="fit",
                help="fit: 판 · 이름표 · 구조물이 다 들어오게 카메라를 맞춤(예전) · corners: 판을 화면보다 크게 깔고, 이름표를 높이 3 까지 "
                     "꽉 찬 3×3×3 구조물의 화면 윤곽 바깥에 붙인 뒤, 윤곽 + 이름표에 카메라를 다시 맞춤(사용자 2026-11-12, place_labels)")
ap.add_argument("--frame-margin", type=float, default=0.14, help="corners: 구조물 둘레 여백(화면 비율)")
ap.add_argument("--look", choices=["flat", "wood"], default="flat",
                help="flat: 단색 + 진한 모서리 테(예전) · wood: 칠한 나무 장난감 블록(나뭇결 · 둥근 모서리 · 테 없음, 불투명 판용)")
ap.add_argument("--size", default="1400x1200",
                help="그림 크기 가로x세로(가로:세로 = 7:6 이면 구도가 같다). 실험용 판 사진은 700x600 JPEG(2026-11-12)")
ap.add_argument("--near-match", type=float, default=0.0,
                help="corners: 앞 · 오른쪽 화살 끝을 판 끝에서 (뒤 · 왼쪽 화살 끝 거리 평균 × 이 값)만큼 판 위에서 떨어뜨림(0 이면 끔)")
ap.add_argument("--dry", action="store_true", help="렌더하지 않고 구조물 비중과 표시물 겹침만 출력")
args = ap.parse_args()

H, C = S.generate(args.seed, min_vis=args.min_vis)
N = S.N
P = args.pitch
EXT = P * (N - 1) / 2 + 0.5        # 구조물 중심 ~ 바닥 가장자리
RX, RY = map(int, args.size.lower().split("x"))

# ---------- 장면
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
try:
    prefs.compute_device_type = "METAL"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = True
    scene.cycles.device = "GPU"
except Exception:
    scene.cycles.device = "CPU"
scene.cycles.samples = args.samples
scene.cycles.use_denoising = True
scene.cycles.transparent_max_bounces = 32
scene.cycles.max_bounces = 16
scene.render.resolution_x = RX
scene.render.resolution_y = RY
scene.view_settings.view_transform = "AgX"
scene.view_settings.look = "AgX - Punchy"
scene.view_settings.exposure = -0.2


# ---------- 재질
def mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    return m, m.node_tree.nodes, m.node_tree.links


def simple(name, rgb, rough=0.6, alpha=1.0):
    m, n, l = mat(name)
    b = n["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*rgb, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Alpha"].default_value = alpha
    return m


def wood(name, base, dark, scale, rough):
    m, n, l = mat(name)
    b = n["Principled BSDF"]
    tc = n.new("ShaderNodeTexCoord")
    mp = n.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1.0, 8.0, 1.0)
    l.new(tc.outputs["Object"], mp.inputs["Vector"])
    w = n.new("ShaderNodeTexWave")
    w.wave_type = "RINGS"
    w.inputs["Scale"].default_value = scale
    w.inputs["Distortion"].default_value = 8.0
    w.inputs["Detail"].default_value = 4.0
    l.new(mp.outputs["Vector"], w.inputs["Vector"])
    mx = n.new("ShaderNodeMix")
    mx.data_type = "RGBA"
    mx.inputs["A"].default_value = (*base, 1)
    mx.inputs["B"].default_value = (*dark, 1)
    l.new(w.outputs["Fac"], mx.inputs["Factor"])
    l.new(mx.outputs["Result"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = rough
    return m


PAINT = {"red": (0.60, 0.02, 0.015), "blue": (0.015, 0.08, 0.55), "green": (0.03, 0.33, 0.05)}


def painted_wood(name, rgb):
    """칠한 나무 장난감 블록: 칠 아래로 나뭇결이 옅게 비치고, 거칠기가 고르지 않고, 표면이 아주 조금 울퉁불퉁하다.
    블록마다 나뭇결이 다르게(물체 위치로 결을 민다). 색은 빨강 · 파랑 · 초록이 또렷이 갈리게 그대로 둔다."""
    m, n, l = mat(name)
    b = n["Principled BSDF"]
    tc = n.new("ShaderNodeTexCoord")
    oi = n.new("ShaderNodeObjectInfo")
    sc = n.new("ShaderNodeVectorMath"); sc.operation = "SCALE"; sc.inputs["Scale"].default_value = 3.7
    l.new(oi.outputs["Location"], sc.inputs[0])
    add = n.new("ShaderNodeVectorMath"); add.operation = "ADD"
    l.new(tc.outputs["Object"], add.inputs[0]); l.new(sc.outputs["Vector"], add.inputs[1])
    mp = n.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (5.0, 0.6, 0.6)
    l.new(add.outputs["Vector"], mp.inputs["Vector"])
    w = n.new("ShaderNodeTexWave"); w.wave_type = "BANDS"
    w.inputs["Scale"].default_value = 3.0; w.inputs["Distortion"].default_value = 1.6; w.inputs["Detail"].default_value = 2.0
    w.inputs["Detail Roughness"].default_value = 0.6
    l.new(mp.outputs["Vector"], w.inputs["Vector"])
    fr = n.new("ShaderNodeMapRange"); fr.inputs["To Min"].default_value = 0.0; fr.inputs["To Max"].default_value = 0.22
    l.new(w.outputs["Fac"], fr.inputs["Value"])
    mx = n.new("ShaderNodeMix"); mx.data_type = "RGBA"
    mx.inputs["A"].default_value = (*rgb, 1)
    mx.inputs["B"].default_value = (*(x * 0.72 for x in rgb), 1)
    l.new(fr.outputs["Result"], mx.inputs["Factor"])
    l.new(mx.outputs["Result"], b.inputs["Base Color"])
    nz = n.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 14.0; nz.inputs["Detail"].default_value = 6.0
    l.new(add.outputs["Vector"], nz.inputs["Vector"])
    rr = n.new("ShaderNodeMapRange"); rr.inputs["To Min"].default_value = 0.42; rr.inputs["To Max"].default_value = 0.62
    l.new(nz.outputs["Fac"], rr.inputs["Value"])
    l.new(rr.outputs["Result"], b.inputs["Roughness"])
    bp = n.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = 0.025; bp.inputs["Distance"].default_value = 0.01
    l.new(w.outputs["Fac"], bp.inputs["Height"])
    l.new(bp.outputs["Normal"], b.inputs["Normal"])
    return m
M_GLASS = {k: (painted_wood(f"wood_{k}", v) if args.look == "wood" else simple(f"acrylic_{k}", v, rough=args.rough, alpha=args.alpha))
           for k, v in PAINT.items()}
M_EDGE = {k: simple(f"edge_{k}", tuple(x * 0.55 for x in v), rough=0.4) for k, v in PAINT.items()}
M_TABLE = simple("table", (0.52, 0.50, 0.47), rough=0.75)
M_BOARD = wood("board", (0.60, 0.43, 0.26), (0.50, 0.34, 0.18), 3.0, 0.55)
M_FOOT = wood("foot", (0.42, 0.28, 0.15), (0.34, 0.22, 0.11), 6.0, 0.5)
M_PAPER = simple("paper", (0.88, 0.87, 0.84), rough=0.85)
M_INK = simple("ink", (0.04, 0.04, 0.045), rough=0.6)
M_GROOVE = simple("groove", (0.25, 0.16, 0.09), rough=0.8)


def box(name, loc, size, material, bevel=0.0, parent=None):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.object
    o.name = name
    o.scale = size
    bpy.ops.object.transform_apply(scale=True)
    o.data.materials.append(material)
    if bevel:
        b = o.modifiers.new("bevel", "BEVEL")
        b.width = bevel
        b.segments = 3
        b.limit_method = "NONE"
    if parent is not None:
        o.parent = parent
    return o


def text(body, loc, rot, size, extrude, parent=None):
    bpy.ops.object.text_add(location=loc)
    t = bpy.context.object
    t.data.body = body
    t.data.align_x = "CENTER"
    t.data.align_y = "CENTER"
    t.data.size = size
    t.data.extrude = extrude
    t.data.materials.append(M_INK)
    t.rotation_euler = rot
    if parent is not None:
        t.parent = parent
    return t


SIDES = [("FRONT", 0), ("BACK", 180), ("LEFT", -90), ("RIGHT", 90)]   # 앞(-y) 기준으로 돌린 각


def side_frame(side, ang):
    """그 방향의 로컬 좌표: -y 가 바깥(그 방향에 선 사람 쪽)."""
    bpy.ops.object.empty_add(location=(0, 0, 0))
    e = bpy.context.object
    e.name = f"mark_{side}"
    return e


def world_corners(objs):
    bpy.context.view_layer.update()
    pts = []
    for o in objs:
        pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
    return pts


# ---------- 방향 표시 1: 판 위 이름 + 화살표
def add_labels():
    TIP = EXT + args.arrow_gap     # 화살표 끝
    LD = TIP + 0.92 + 0.36         # 구조물 중심 ~ 이름 중심
    HL, HW, SW = 0.30, 0.20, 0.055 # 화살촉 길이 · 촉 반폭 · 몸통 반폭
    TAIL = LD - 0.36
    objs = []
    for side, ang in SIDES:
        e = side_frame(side, ang)
        yt = -TIP
        verts = [(0, yt, 0.004), (-HW, yt - HL, 0.004), (-SW, yt - HL, 0.004), (-SW, -TAIL, 0.004),
                 (SW, -TAIL, 0.004), (SW, yt - HL, 0.004), (HW, yt - HL, 0.004)]
        me = bpy.data.meshes.new(f"arrow_{side}")
        me.from_pydata(verts, [], [list(range(7))])
        me.materials.append(M_INK)
        ob = bpy.data.objects.new(f"arrow_{side}", me)
        bpy.context.collection.objects.link(ob)
        ob.parent = e
        objs += [ob, text(side, (0, -LD, 0.003), (0, 0, 0), 0.40, 0.0005, parent=e)]
        e.rotation_euler[2] = math.radians(ang)
    return LD + 0.75, objs


def _hull(P):
    P = sorted(set(P))
    if len(P) < 3:
        return P
    def half_(pts):
        h = []
        for p in pts:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h
    lo, up = half_(P), half_(P[::-1])
    return lo[:-1] + up[:-1]


def _apart(A, B, gap):
    """볼록 다각형 A · B 가 gap 이상 떨어져 있나(분리축)."""
    for poly in (A, B):
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
            ax, ay = y2 - y1, x1 - x2
            nrm = (ax * ax + ay * ay) ** 0.5 or 1.0
            pa = [(ax * x + ay * y) / nrm for x, y in A]
            pb = [(ax * x + ay * y) / nrm for x, y in B]
            if max(pa) + gap <= min(pb) or max(pb) + gap <= min(pa):
                return True
    return False


LABEL_GAP = dict(FRONT=0.055, RIGHT=0.055, BACK=0.03, LEFT=0.03)     # 꽉 찬 윤곽과의 틈(화면 비율)
LABEL_SCALE = dict(FRONT=0.85, RIGHT=0.85, BACK=1.0, LEFT=1.0)      # 이름 · 화살표 크기
AIM_FIRST_FLOOR = ("BACK", "LEFT")


def place_labels():
    """corners: 이름표(이름 + 안쪽을 가리키는 짧은 화살표)를 방향마다, 높이 3 까지 꽉 찬 3 × 3 × 3 구조물이 화면에서 차지하는
    볼록 껍질 바깥에 틈(LABEL_GAP)을 두고 붙인다. 뒤 · 왼쪽 이름도 구조물 뒤에 숨지 않는다(사용자: 뒤 · 왼쪽만 멀어 보였다 → 균형).
      · 앞 · 오른쪽은 이름 · 화살표를 15% 줄이고 틈을 넓힌다(사용자: '글자크기 조금 줄이고 좀더 띄워').
      · 뒤 · 왼쪽은 판 위에서 멀리 놓여, 판 가운데 줄 위에 두면 화면에서 화살표가 그 면 밑변의 가운데(높이 0)를 가리켜 구조물보다
        아래쪽을 겨눈 것처럼 보인다. 그래서 옆으로 조금 옮겨, 화면에서 화살표 연장선이 그 면 1 층의 가운데(높이 0.5)를 지나게 한다
        (사용자: '1층 중심이랑 맞는 위치로'). 앞 · 오른쪽은 면이 보이므로 화살표가 그 면 밑변의 가운데를 가리키게 그대로 둔다.
    그다음 구조물과 이름표가 다 들어오게 카메라를 다시 맞춰 가운데로 모은다(렌즈 · 시프트만 바뀌므로 화면에서 곧은 줄은 그대로 곧다)."""
    HL, HW, SW = 0.30, 0.20, 0.055
    pj = lambda p: tuple(world_to_camera_view(scene, cam, p)[:2])
    cube = _hull([pj(p) for p in full])
    groups, far_tip = [], []
    order = [sa for sa in SIDES if sa[0] in AIM_FIRST_FLOOR] + [sa for sa in SIDES if sa[0] not in AIM_FIRST_FLOOR]
    for side, ang in order:
        e = side_frame(side, ang)
        e.rotation_euler[2] = math.radians(ang)
        k = LABEL_SCALE[side]
        t = text(side, (0, -(EXT + 2.0), 0.003), (0, 0, 0), 0.40 * k, 0.0005, parent=e)
        tip, tail = 1.31 * k, 0.36 * k               # 이름 중심에서 안쪽(+y)으로
        verts = [(0, tip, 0.004), (-HW * k, tip - HL * k, 0.004), (-SW * k, tip - HL * k, 0.004), (-SW * k, tail, 0.004),
                 (SW * k, tail, 0.004), (SW * k, tip - HL * k, 0.004), (HW * k, tip - HL * k, 0.004)]
        me = bpy.data.meshes.new(f"arrow_{side}")
        me.from_pydata(verts, [], [list(range(7))])
        me.materials.append(M_INK)
        ob = bpy.data.objects.new(f"arrow_{side}", me)
        bpy.context.collection.objects.link(ob)
        ob.parent = e

        def settle(x):
            """옆으로 x 만큼 옮긴 줄 위에서, 껍질과 틈 이상 떨어지는 가장 가까운 거리에 놓는다."""
            lo, hi = EXT + 0.3, EXT + 14.0
            for _ in range(24):
                mid = (lo + hi) / 2
                t.location = ob.location = (x, -mid, 0.003)
                pts = [pj(p) for p in world_corners([t, ob])]
                lo, hi = (lo, mid) if _apart(_hull(pts), cube, LABEL_GAP[side]) else (mid, hi)
            t.location = ob.location = (x, -hi, 0.003)
            bpy.context.view_layer.update()

        def miss(x):
            """화면에서 화살표 연장선이 그 면 1 층 가운데를 비껴가는 거리(부호 있음, 화면 비율)."""
            settle(x)
            a, b = pj(ob.matrix_world @ Vector((0, tail, 0))), pj(ob.matrix_world @ Vector((0, tip, 0)))
            p = pj(e.matrix_world @ Vector((0, -EXT, 0.5)))
            dx, dy = b[0] - a[0], b[1] - a[1]
            return ((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / math.hypot(dx, dy)

        if side in AIM_FIRST_FLOOR:                  # 할선법: 옆 거리 x 를 바꿔 비껴감을 0 으로
            x0, x1 = 0.0, 0.3
            f0, f1 = miss(x0), miss(x1)
            for _ in range(12):
                if abs(f1) < 1e-6 or f1 == f0:
                    break
                x0, x1, f0 = x1, x1 - f1 * (x1 - x0) / (f1 - f0), f1
                f1 = miss(x1)
            settle(x1)
            far_tip.append(-t.location.y - tip - EXT)          # 판 끝 ~ 화살 끝(판 위 거리)
        else:
            settle(0.0)
            want = args.near_match * sum(far_tip) / len(far_tip)
            if want > -t.location.y - tip - EXT:              # 판 위에서도 뒤 · 왼쪽과 비슷하게 떨어져 보이게
                t.location = ob.location = (0, -(EXT + want + tip), 0.003)
                bpy.context.view_layer.update()
        groups += [ob, t]
    return groups


# ---------- 방향 표시 2: 세워 둔 양면 카드(위에 이름, 아래 빈 3×3 칸, 맨 아랫줄 = 바닥)
def add_cards():
    D = EXT + 1.05                 # 구조물 중심 ~ 카드 면. 앞쪽 카드가 3 층 구조물을 가리지 않는 거리
    CS, LINE, T, PAD, FOOT_H, LABEL = 0.28, 0.016, 0.035, 0.13, 0.07, 0.24
    grid_w, grid_h = CS * N, CS * S.HMAX
    card_w = grid_w + 2 * PAD
    z0 = FOOT_H + PAD
    z_label = z0 + grid_h + 0.09 + LABEL / 2
    card_h = (z_label + LABEL / 2 + PAD) - FOOT_H
    objs = []
    for side, ang in SIDES:
        e = side_frame(side, ang)
        objs.append(box(f"foot_{side}", (0, -D, FOOT_H / 2), (card_w * 0.8, 0.26, FOOT_H), M_FOOT, bevel=0.01, parent=e))
        objs.append(box(f"paper_{side}", (0, -D, FOOT_H + card_h / 2), (card_w, T, card_h), M_PAPER, parent=e))
        for face, sy, rot in [("o", -1, (math.radians(90), 0, 0)), ("i", 1, (math.radians(90), 0, math.radians(180)))]:
            y = -D + sy * (T / 2 + 0.0015)
            for i in range(N + 1):
                x = -grid_w / 2 + i * CS
                box(f"v{side}{face}{i}", (x, y, z0 + grid_h / 2), (LINE, 0.002, grid_h + LINE), M_INK, parent=e)
            for j in range(S.HMAX + 1):
                w = LINE * (2.4 if j == 0 else 1)
                box(f"h{side}{face}{j}", (0, y, z0 + j * CS), (grid_w + LINE, 0.002, w), M_INK, parent=e)
            text(side, (0, y + sy * 0.001, z_label), rot, LABEL, 0.002, parent=e)
        e.rotation_euler[2] = math.radians(ang)
    return D + 0.75, objs


# ---------- 탁자 · 판 · 바닥 홈
CORNERS = args.marks == "labels" and args.layout == "corners"
NONE = args.marks == "none"
if NONE:
    half, mark_objs = 16.0, []          # 판을 화면보다 크게 깔고 표시물 없이 꽉 찬 구조물에만 카메라를 맞춘다
elif CORNERS:
    half, mark_objs = 16.0, []          # 판을 화면보다 크게 깔아 바깥 탁자가 안 보이게. 이름표는 카메라를 맞춘 뒤 놓는다
else:
    half, mark_objs = (add_labels if args.marks == "labels" else add_cards)()
box("table", (0, 0, -0.12 - 0.5), (60, 60, 1.0), M_TABLE)
box("board", (0, 0, -0.06), (2 * half, 2 * half, 0.12), M_BOARD, bevel=0.04)
if P == 1.0:
    for i in range(N + 1):
        v = -1.5 + i
        box(f"gx{i}", (v, 0, 0.0), (0.03, 3.0, 0.006), M_GROOVE)
        box(f"gy{i}", (0, v, 0.0), (3.0, 0.03, 0.006), M_GROOVE)
else:                              # 기둥마다 따로 네모 칸
    for r in range(N):
        for c in range(N):
            x, y = (c - 1) * P, (1 - r) * P
            for k, sgn in enumerate((-1, 1)):
                box(f"gx{r}{c}{k}", (x + sgn * 0.5, y, 0.0), (0.03, 1.03, 0.006), M_GROOVE)
                box(f"gy{r}{c}{k}", (x, y + sgn * 0.5, 0.0), (1.03, 0.03, 0.006), M_GROOVE)

# ---------- 반투명 블록 + 모서리 테 (블록마다 색)
# 블록끼리 어긋나 보이지 않게 흔들림 없이 격자에 딱 맞춘다. 틈은 맞닿은 면이 겹쳐 얼룩지지 않을 만큼만 두어,
# 이웃 블록의 테가 한 줄로 겹쳐 보이게 한다(틈 0.02 + 위치 흔들림 ±0.01 일 때 이음매 테가 두 줄로 어긋나 보였다).
s = 1 - 0.006
for r in range(N):
    for c in range(N):
        for zz in range(H[r][c]):
            k = C[r][c][zz]
            loc = ((c - 1) * P, (1 - r) * P, zz + 0.5)
            if args.look == "wood":                        # 테 없이 둥근 모서리: 쌓인 블록 사이 홈이 이음매를 보여 준다
                box(f"b{r}{c}{zz}", loc, (s, s, s), M_GLASS[k], bevel=0.05)
                continue
            box(f"b{r}{c}{zz}", loc, (s, s, s), M_GLASS[k], bevel=0.02)
            f = box(f"e{r}{c}{zz}", loc, (s, s, s), M_EDGE[k])
            wf = f.modifiers.new("wire", "WIREFRAME")
            wf.thickness = 0.035
            wf.use_replace = True

# ---------- 조명
world = bpy.data.worlds.new("w")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes["Background"]
bg.inputs["Color"].default_value = (0.80, 0.82, 0.85, 1)
bg.inputs["Strength"].default_value = 0.45
bpy.ops.object.empty_add(location=(0, 0, 0.8))
tgt = bpy.context.object
for loc, size, energy, col in [((-8, -7, 13), 9, 14000, (1.0, 0.96, 0.9)), ((11, 6, 9), 8, 3500, (0.9, 0.94, 1.0))]:
    bpy.ops.object.light_add(type="AREA", location=loc)
    L = bpy.context.object
    L.data.size = size
    L.data.energy = energy
    L.data.color = col
    L.constraints.new("TRACK_TO").target = tgt

# ---------- 카메라: 표시물과 높이 3 꽉 찬 구조물이 다 들어오게 렌즈 · 시프트를 맞춘다
AZ, EL, DIST = math.radians(args.az), math.radians(args.el), 14.0
TG = (0.0, 0.0, 0.9)
hh = DIST * math.cos(EL)
bpy.ops.object.camera_add(location=(TG[0] + hh * math.sin(AZ), TG[1] - hh * math.cos(AZ), TG[2] + DIST * math.sin(EL)))
cam = bpy.context.object
scene.camera = cam
cam.data.lens = 50
bpy.ops.object.empty_add(location=TG)
ct = bpy.context.object
cam.constraints.new("TRACK_TO").target = ct
full = [Vector((x, y, z)) for x in (-EXT, EXT) for y in (-EXT, EXT) for z in (0, 3)]
key_pts = world_corners(mark_objs) + full
MARGIN = 0.07 if NONE else args.frame_margin if CORNERS else 0.05
for _ in range(3):
    uv = [world_to_camera_view(scene, cam, p) for p in key_pts]
    us, vs = [p.x for p in uv], [p.y for p in uv]
    cam.data.lens /= max((max(us) - min(us)) / (1 - 2 * MARGIN), (max(vs) - min(vs)) / (1 - 2 * MARGIN))
    bpy.context.view_layer.update()
    uv = [world_to_camera_view(scene, cam, p) for p in key_pts]
    us, vs = [p.x for p in uv], [p.y for p in uv]
    cam.data.shift_x += (max(us) + min(us)) / 2 - 0.5
    cam.data.shift_y += ((max(vs) + min(vs)) / 2 - 0.5) * RY / RX
    bpy.context.view_layer.update()
if CORNERS:
    mark_objs = place_labels()
    key_pts = world_corners(mark_objs) + full
    for _ in range(3):                                # 구조물 + 이름표가 다 들어오게, 가운데로
        uv = [world_to_camera_view(scene, cam, p) for p in key_pts]
        us, vs = [p.x for p in uv], [p.y for p in uv]
        cam.data.lens /= max((max(us) - min(us)) / (1 - 2 * 0.05), (max(vs) - min(vs)) / (1 - 2 * 0.05))
        bpy.context.view_layer.update()
        uv = [world_to_camera_view(scene, cam, p) for p in key_pts]
        us, vs = [p.x for p in uv], [p.y for p in uv]
        cam.data.shift_x += (max(us) + min(us)) / 2 - 0.5
        cam.data.shift_y += ((max(vs) + min(vs)) / 2 - 0.5) * RY / RX
        bpy.context.view_layer.update()
uv = [world_to_camera_view(scene, cam, p) for p in full]
us, vs = [p.x for p in uv], [p.y for p in uv]
share = (round(100 * (max(us) - min(us))), round(100 * (max(vs) - min(vs))))
if args.dry:
    # 높이 h 기둥이 화면에서 뒤쪽 표시물과 겹치는 칸 수(반투명 블록 너머로 비쳐 보이는 곳)
    def hull(P):
        P = sorted(set(P))
        def half_(pts):
            h = []
            for p in pts:
                while len(h) >= 2 and (h[-1][0]-h[-2][0])*(p[1]-h[-2][1]) - (h[-1][1]-h[-2][1])*(p[0]-h[-2][0]) <= 0:
                    h.pop()
                h.append(p)
            return h
        lo, up = half_(P), half_(P[::-1])
        return lo[:-1] + up[:-1]
    def overlap(A, B):
        for poly in (A, B):
            for i in range(len(poly)):
                (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
                ax, ay = y2 - y1, x1 - x2
                pa = [ax * x + ay * y for x, y in A]
                pb = [ax * x + ay * y for x, y in B]
                if max(pa) < min(pb) or max(pb) < min(pa):
                    return False
        return True
    pj = lambda p: tuple(world_to_camera_view(scene, cam, p)[:2])
    marks = [hull([pj(q) for q in world_corners(mark_objs[2 * i:2 * i + 2])]) for i in range(len(mark_objs) // 2)]
    hit = {}
    for h in (1, 2, 3):
        hit[h] = sum(any(overlap(hull([pj(Vector(((c - 1) * P + dx, (1 - r) * P + dy, z))) for dx in (-.5, .5)
                                         for dy in (-.5, .5) for z in (0, h)]), m) for m in marks)
                     for r in range(N) for c in range(N))
    print(f"share {share[0]}% x {share[1]}%  겹치는 칸(높이 1/2/3): {hit[1]}/{hit[2]}/{hit[3]}")
    sys.exit(0)
cam.data.dof.use_dof = True
cam.data.dof.focus_object = ct
cam.data.dof.aperture_fstop = 11.0

scene.render.filepath = os.path.abspath(args.out)
fmt = scene.render.image_settings                  # 확장자로 형식을 정한다. 알파는 늘 255 라 버린다
if os.path.splitext(args.out)[1].lower() in (".jpg", ".jpeg"):
    fmt.file_format, fmt.color_mode, fmt.quality = "JPEG", "RGB", 90
else:
    fmt.file_format, fmt.color_mode = "PNG", "RGB"
bpy.ops.render.render(write_still=True)
json.dump({"seed": args.seed, "H": H, "C": C, "views": S.all_views(H, C),
           "render": {"marks": args.marks, "alpha": args.alpha, "minVis": args.min_vis, "rough": args.rough, "look": args.look, "layout": args.layout, "samples": args.samples, "size": [RX, RY], "az": args.az, "el": args.el, "pitch": P,
                      "lens_mm": round(cam.data.lens, 1), "structure_share_pct": share}},
          open(os.path.splitext(args.out)[0] + ".json", "w"), ensure_ascii=False, indent=1)
print(f"wrote {args.out}  (구조물이 화면의 가로 {share[0]}% · 세로 {share[1]}%)")
