# -*- coding: utf-8 -*-
"""블록 이미지 사람용 페이지 두 개를 만든다.   python3 bench/make_human.py [문제수=10] [씨앗=1117]

  human/block-image.html        참가자용. 문제를 씨앗으로 뽑아 차례로 풀고, 끝나면 결과 JSON 을 내려받는다(전구 · 물길 페이지와 같은 방식).
  human/block-image_check.html   화면 확인용. 모든 문제(V001~)에서 골라 아무 라운드부터 풀어 볼 수 있고, 숨은 방향 · 규칙과
                           그 사진의 정답 격자를 켜고 끌 수 있다. 결과는 남기지 않는다.

모델과 같은 문제 · 같은 예산 · 같은 피드백이다(runner.play · prompt 와 맞춤).
  · 안내 글은 모델의 첫 글(prompt.HOW, 빈칸 0 판의 'In each grid cell write a number.')을 옮긴 것. 숨은 방향(목표) · 칸 숫자의
    뜻(규칙) · 라운드 전환 · 예산 T · k 는 알리지 않는다
  · 제출마다 새 사진(라운드의 j 번째 제출 = 사진 j). 사진이 바뀐다고 알리지 않는다
  · 판정은 모델이 받는 글과 같은 내용만: 정답이면 정답, 틀리면 낸 격자에서 틀린 칸에 * (방향 · 규칙 · 사진 읽기를 가리지 않음)
  · 칸은 숫자 한 자리. 9 칸을 다 채워야 제출된다(모델의 '읽을 수 없는 답' 은 사람에게는 생기지 않음)
  · 지난 제출의 사진(작게) · 낸 격자 · 판정은 옆 기록에 남는다. 모델도 지난 사진과 피드백을 대화 안에 갖고 있다
사진은 모델이 받은 JPEG 를 그대로 페이지에 넣는다(한 파일로 보내려고). 결과 JSON 은 `bench/human_runs.py` 로 runs 꼴로 펼쳐
`bench/score.py` 로 채점한다.

참가자용 문제는 GPT-6.1 Sol 런(V001~V100)이 있는 문제에서, 1 라운드를 풀었는지로 층을 나눠 층 크기에 비례해 뽑는다.
"""
import base64
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import engine as E                                    # noqa: E402
import generate as G                                  # noqa: E402
import prompt as P                                    # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
N = int(args[0]) if args else 10
SEED = int(args[1]) if len(args) > 1 else 1117
T, K = 10, 2
RUNS = ["20261113_172409"]                            # GPT-6.1 Sol V001~V100
RULES = ("count", "near", "colors")

DATA = json.loads((ROOT / "episodes/episodes.json").read_text())
POOL = G.load_pool(DATA["pool"], DATA["scheme"], DATA["empty"])
everything = DATA["episodes"]


def r1_solved():
    got = {}
    for run in RUNS:
        for f in (ROOT / "runs" / run).glob("*_V*.json"):
            log = json.loads(f.read_text())
            got[log["episode"]] = int(bool(log["rounds"][0]["solved"]))
    return got


def quota(layers, n, total):
    q = {k: n * len(v) / total for k, v in layers.items()}
    take = {k: int(x) for k, x in q.items()}
    for k in sorted(q, key=lambda k: q[k] - take[k], reverse=True)[:n - sum(take.values())]:
        take[k] += 1
    return take


def pack(eps):
    """문제 · 사진(그림과 12 가지 격자). 격자는 방향 4 × 규칙 3 순서로 9 글자씩 이어 붙인다."""
    used = sorted({p for e in eps for r in e["rounds"] for p in r["photos"]})
    photos = {}
    for p in used:
        ph = POOL[p]
        img = base64.b64encode((ROOT / ph["image"]).read_bytes()).decode()
        grids = "".join("".join("".join(row) for row in ph["views"][d][k]) for d in E.SIDES for k in RULES)
        photos[p] = dict(img="data:image/jpeg;base64," + img, grids=grids)
    rounds = [dict(id=e["id"], rounds=[dict(side=r["side"], rule=r["rule"], photos=r["photos"]) for r in e["rounds"]])
              for e in eps]
    return rounds, photos


rng = random.Random(SEED)
solved = r1_solved()
layers = {}
for i, e in enumerate(everything):
    if e["id"] in solved:
        layers.setdefault(solved[e["id"]], []).append(i)
take = quota(layers, N, sum(len(v) for v in layers.values()))
picked = sorted(i for k in sorted(layers) for i in rng.sample(layers[k], take[k]))
print("  층(GPT 가 R1 을 풀었나: 문제 수 → 몫) " + "  ".join(f"{k}: {len(layers[k])} → {take[k]}" for k in sorted(layers)))

HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
 :root{color-scheme:light}
 *{box-sizing:border-box}
 body{font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;
      background:#f5f5f7;color:#1a1a1a;margin:0;padding:22px;line-height:1.55}
 .wrap{max-width:1100px;margin:0 auto}
 .cols{display:flex;gap:16px;align-items:flex-start}
 .mid{flex:1 1 0;min-width:0}
 .side{flex:0 0 300px}
 .side .card{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto}
 @media (max-width:860px){.cols{flex-direction:column}.side{flex:none;width:100%}}
 h1{font-size:19px;margin:0 0 14px}
 .card{background:#fff;border:1px solid #e3e3e3;border-radius:12px;padding:20px;margin-bottom:16px}
 .muted{color:#777;font-size:13px}
 .prog{display:flex;gap:5px;margin:0 0 14px}
 .seg{flex:1 1 0;height:8px;background:#e2e2e2;border-radius:4px}
 .seg.on{background:#1a1a1a}
 .play{display:flex;gap:24px;align-items:flex-start;justify-content:center;flex-wrap:wrap;margin-top:10px}
 .photo{position:relative;width:100%;max-width:504px}
 .photo img{display:block;width:100%;border-radius:10px;border:1px solid #ddd}
 .tag{position:absolute;background:#fff;border:1px solid #9aa7b4;border-radius:7px;padding:1px 7px;font-size:12px;font-weight:800;color:#183554}
 .ans{display:grid;grid-template-columns:54px repeat(3,58px);gap:6px;align-items:center}
 .ans .rl{font-size:13px;font-weight:700;color:#555;text-align:right;padding-right:4px}
 .ans .cl{font-size:12px;font-weight:700;color:#777;text-align:center}
 .ans input{width:58px;height:58px;text-align:center;font-size:26px;font-weight:800;border:2px solid #9aa7b4;border-radius:9px;padding:0}
 .ans input:focus{outline:none;border-color:#1a1a1a;background:#fafafa}
 .note{font-size:12px;color:#888;margin-top:8px;max-width:240px}
 button{font:inherit;cursor:pointer;font-weight:700;padding:9px 14px;line-height:1.3;
        background:linear-gradient(#ffffff,#eceef0);border:1px solid #bcc0c4;
        border-bottom:4px solid #aeb3b8;border-radius:12px;color:#1a1a1a;
        box-shadow:0 3px 7px rgba(0,0,0,.10);transition:transform .05s,box-shadow .05s}
 button:hover{background:linear-gradient(#ffffff,#e4e7ea)}
 button:active{transform:translateY(3px);border-bottom-width:1px;box-shadow:inset 0 3px 6px rgba(0,0,0,.14)}
 button:disabled{opacity:.45;cursor:default}
 .go{background:linear-gradient(#2c2c2c,#141414);color:#fff;border-color:#141414;
     border-bottom:4px solid #000;padding:12px 26px;letter-spacing:2px}
 .go:hover{background:linear-gradient(#3a3a3a,#1e1e1e)}
 .bar{display:flex;gap:10px;justify-content:center;margin-top:14px}
 .vd{font-weight:800;font-size:14px;min-height:26px;text-align:center;margin-top:12px}
 .vd.o{color:#1f7a3f}
 .vd.x{color:#b04040}
 .mini{display:grid;grid-template-columns:repeat(3,26px);gap:3px}
 .mini span{width:26px;height:26px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;
            border:1px solid #c3ccd6;border-radius:5px;background:#fff}
 .mini span.bad{border:2px solid #c64343;color:#c64343}
 .mini span.hit{border:2px solid #183554;background:#eaf0f6}
 .row{padding:10px 4px;border-bottom:1px solid #f1f1f1;display:flex;gap:10px;align-items:center}
 .row img{width:96px;border-radius:6px;border:1px solid #ddd}
 .row .v{font-size:12px;font-weight:700}
 .row .v.o{color:#1f7a3f}
 .row .v.x{color:#b04040}
 textarea{width:100%;min-height:72px;font:inherit;font-size:13px;padding:10px;border:1px solid #ddd;border-radius:8px;resize:vertical}
 input.who,select{font:inherit;padding:9px 12px;border:1px solid #ccc;border-radius:8px}
 input.who{width:240px}
 .toast{position:fixed;left:50%;top:60px;transform:translateX(-50%);z-index:50;background:#1a1a1a;
        color:#fff;font-size:17px;font-weight:800;padding:12px 26px;border-radius:999px;
        opacity:0;pointer-events:none;box-shadow:0 6px 20px rgba(0,0,0,.18)}
 .toast.go2{animation:pop 1.4s ease-out}
 @keyframes pop{0%{opacity:0}15%{opacity:1}75%{opacity:1}100%{opacity:0}}
 .dev{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 .hint{margin-top:12px;padding:10px 12px;border-radius:8px;background:#fff7e6;border:1px solid #f0d9a8;font-size:13px;
       display:flex;gap:14px;align-items:center}
</style></head><body>
<div class="wrap">
 <div id="toast" class="toast"></div>

 <div id="intro" class="card">
  <h1>블록 사진</h1>
  <p>이 퍼즐은 조금 특이합니다. 푸는 방법을 많이 알려 드릴 수 없습니다.</p>
  <p>사진마다 3 × 3 바닥 위에 정육면체 블록이 최대 3층까지 쌓여 있습니다. 블록은 모두 빨강 · 파랑 · 초록 가운데 하나입니다.</p>
  <ul class="muted" style="padding-left:20px">
   <li>사진마다 <b>보기 격자</b>를 적어 제출하세요. 보기 격자는 바닥의 네 옆면 가운데 한쪽에서 블록을 <b>옆으로</b> 본 모습입니다.</li>
   <li>보기 격자는 3행 3열입니다. 맨 윗줄이 3층, 맨 아랫줄이 1층입니다. 각 열은 보는 사람에게서 곧게 멀어지는 세 칸짜리 줄
       하나이고, 보는 사람이 보는 대로 왼쪽에서 오른쪽 순서입니다.</li>
   <li>격자의 칸마다 숫자 하나를 적으세요.</li>
   <li>맞았는지 틀렸는지는 매번 알려 드립니다. 틀리면 낸 격자에서 틀린 칸에 * 표시를 해 드립니다. 통하는 격자는 늘 있습니다.</li>
   <li>시간 제한은 없습니다. __COUNT__</li>
  </ul>
  <div id="who-box">
   <p style="margin-bottom:6px">이름 또는 식별자</p>
   <input id="who" class="who">
   <button class="go" style="margin-left:8px" onclick="start()">시작</button>
  </div>
 </div>

 <div id="pick" class="card" style="display:none">
  <h1>확인용: 문제 고르기</h1>
  <div class="dev">
   <select id="pickEp"></select>
   <select id="pickRd"></select>
   <button class="go" onclick="pickStart()">이 문제 풀기</button>
   <label class="muted"><input type="checkbox" id="showHint" onchange="paintHint()"> 숨은 방향 · 규칙과 이 사진의 정답 보기</label>
  </div>
  <div class="muted" style="margin-top:8px">라운드를 고르면 그 라운드의 첫 사진부터 시작합니다. 결과는 남기지 않습니다.</div>
 </div>

 <div id="game" style="display:none">
  <div class="prog" id="prog"></div>
  <div class="cols"><div class="mid">
  <div class="card">
   <div class="muted" id="head"></div>
   <div class="play">
    <div class="photo" id="photo"></div>
    <div>
     <div class="ans" id="ans"></div>
     <div class="note">윗줄 3층 · 아랫줄 1층. 열은 보는 사람이 보는 대로 왼쪽부터. 칸마다 숫자 하나.</div>
     <div class="bar">
      <button onclick="clr()">지우기</button>
      <button class="go" id="sub" onclick="submit()" disabled>제출</button>
     </div>
    </div>
   </div>
   <div class="vd" id="vd"></div>
   <div id="hint"></div>
  </div>
  <div class="card" id="memoCard">
   <div class="muted" style="font-weight:700;margin-bottom:6px">메모 (채점에 쓰이지 않습니다)</div>
   <textarea id="memo" placeholder="알아낸 것을 적어 두셔도 됩니다"></textarea>
  </div>
  </div>
  <div class="side"><div class="card">
   <div class="muted" style="font-weight:700">이 문제의 기록</div>
   <div id="log"></div>
   <button id="saveMid" style="margin-top:14px;font-size:12px;padding:6px 10px;border-bottom-width:2px"
           onclick="save()">중간에 그만둘 때: 지금까지 결과 내려받기</button>
  </div></div>
  </div>
 </div>

 <div id="done" class="card" style="display:none">
  <h1>끝났습니다</h1>
  <p>아래 버튼으로 결과 파일을 내려받아 전달해 주세요.</p>
  <button class="go" onclick="save()">결과 내려받기</button>
 </div>
</div>
<script>
const MODE="__MODE__";
const EPS=__EPS__, PH=__PHOTOS__, CFG=__CFG__;
const SIDES=["front","back","left","right"], RULES=["count","near","colors"];
const SIDE_KO={front:"앞",back:"뒤",left:"왼쪽",right:"오른쪽"}, RULE_KO={count:"블록 수",near:"가장 가까운 블록",colors:"색 가짓수"};
let who="", ei=0, ri=0, out=[], log=null, t0=0, streak=0, subs=0, busy=false, prevSuccess=null;

const ep=()=>EPS[ei], rd=()=>ep().rounds[ri];
const photoName=()=>rd().photos[Math.min(subs, rd().photos.length-1)];       /* 라운드의 j 번째 제출 = 사진 j (runner 와 같음) */
function views(name){            /* 사진의 12 가지 격자: {방향:{규칙:[[..],[..],[..]]}} */
  const g=atob(PH[name].grids);
  const out={}; let k=0;
  for(const d of SIDES){ out[d]={}; for(const r of RULES){ const s=g.slice(k,k+9); k+=9; out[d][r]=[0,1,2].map(i=>[...s.slice(3*i,3*i+3)]); } }
  return out;
}
const eq=(a,b)=>[0,1,2].every(i=>[0,1,2].every(j=>a[i][j]===b[i][j]));
const flat=g=>g.map(r=>r.join("")).join("");
/* bench/engine 의 evaluate · matches · perseverative · best 와 같다 */
function evaluate(ans,V,d,k){ const w=V[d][k], out=[]; for(let i=0;i<3;i++) for(let j=0;j<3;j++) if(ans[i][j]!==w[i][j]) out.push([i,j]); return out; }
function matches(ans,V){ const out=[]; for(const d of SIDES) for(const k of RULES) if(eq(ans,V[d][k])) out.push([d,k]); return out; }
function perseverative(ans,V,oldK,newK,dNow){ if(eq(ans,V[dNow][newK])) return false; return matches(ans,V).some(([d,k])=>k===oldK); }
function best(ans,V){
  const sc=[]; for(const d of SIDES) for(const k of RULES){ let s=0; for(let i=0;i<3;i++) for(let j=0;j<3;j++) s+=ans[i][j]===V[d][k][i][j]; sc.push([[d,k],s]); }
  const top=Math.max(...sc.map(x=>x[1])); return [sc.filter(x=>x[1]===top).map(x=>x[0]), top];
}

function start(){
  who=document.getElementById("who").value.trim();
  if(!who){ alert("이름 또는 식별자를 적어 주세요"); return; }
  document.getElementById("intro").style.display="none";
  document.getElementById("game").style.display="";
  openEpisode();
}

function openEpisode(startRound){
  const e=ep();
  log={episode:e.id, T:CFG.T, k:CFG.k, feedback:"cells", showPrev:false, rounds:[], calls:[], startedAt:new Date().toISOString(), memo:""};
  if(MODE==="test") out.push(log);
  ri=startRound||0; prevSuccess=null;
  document.getElementById("memo").value="";
  document.getElementById("prog").innerHTML = MODE==="test" ? EPS.map((_,i)=>`<div class="seg ${i<=ei?"on":""}"></div>`).join("") : "";
  document.getElementById("head").textContent = MODE==="test" ? `문제 ${ei+1} / ${EPS.length}` : e.id;
  document.getElementById("log").innerHTML="";
  say(null);
  openRound();
}

/* 새 라운드는 말없이 연다 */
function openRound(){
  log.rounds.push({round:ri+1, submissions:0, solved:false, unparsed:0, streak:0, rule:0, side:0, both:0, near:0, repeat:0});
  subs=0; streak=0; t0=Date.now(); paint();
}

function paint(){
  const n=photoName();
  document.getElementById("photo").innerHTML=`<img src="${PH[n].img}" alt="블록 사진">`;
  const a=document.getElementById("ans");
  let h=`<span></span>`+["왼쪽","가운데","오른쪽"].map(s=>`<span class="cl">${s}</span>`).join("");
  for(let i=0;i<3;i++){
    h+=`<span class="rl">${3-i}층</span>`;
    for(let j=0;j<3;j++) h+=`<input inputmode="numeric" maxlength="1" data-i="${i}" data-j="${j}" aria-label="${3-i}층 ${j+1}열">`;
  }
  a.innerHTML=h;
  const ins=[...a.querySelectorAll("input")];
  ins.forEach((el,k)=>{
    el.addEventListener("input",()=>{ el.value=el.value.replace(/[^0-9]/g,"").slice(-1); if(el.value && k<8) ins[k+1].focus(); ready(); });
    el.addEventListener("keydown",ev=>{
      if(ev.key==="Backspace" && !el.value && k>0){ ins[k-1].focus(); }
      if(ev.key==="Enter"){ submit(); }
      if(ev.key==="ArrowRight" && k<8) ins[k+1].focus();
      if(ev.key==="ArrowLeft" && k>0) ins[k-1].focus();
      if(ev.key==="ArrowDown" && k<6) ins[k+3].focus();
      if(ev.key==="ArrowUp" && k>2) ins[k-3].focus();
    });
  });
  ready();
  paintHint();
  if(ins[0]) ins[0].focus();
}
function grid(){ const ins=[...document.querySelectorAll("#ans input")]; return [0,1,2].map(i=>[0,1,2].map(j=>ins[3*i+j].value)); }
function ready(){ document.getElementById("sub").disabled = busy || grid().some(r=>r.some(x=>x==="")); }
function clr(){ if(busy) return; document.querySelectorAll("#ans input").forEach(el=>el.value=""); ready(); document.querySelector("#ans input").focus(); }

function miniHTML(g,wrong,hit){
  const bad=new Set((wrong||[]).map(([i,j])=>3*i+j));
  return `<div class="mini">${[0,1,2].map(i=>[0,1,2].map(j=>`<span class="${bad.has(3*i+j)?"bad":(hit?"hit":"")}">${g[i][j]}${bad.has(3*i+j)?"*":""}</span>`).join("")).join("")}</div>`;
}
function say(res){
  const vd=document.getElementById("vd");
  if(!res){ vd.className="vd"; vd.textContent=""; return; }
  vd.className="vd "+(res.ok?"o":"x");
  vd.textContent = res.ok ? "정답입니다" : "* 표시한 칸이 틀렸습니다. 정답이 아닙니다";
}

function submit(){
  if(busy) return;
  const g=grid(); if(g.some(r=>r.some(x=>x===""))) return;
  const e=ep(), r=rd(), name=photoName(), V=views(name);
  const wrong=evaluate(g,V,r.side,r.rule), ok=!wrong.length, [top,same]=best(g,V);
  const rec=log.rounds[log.rounds.length-1];
  rec.submissions++; subs++;
  const call={round:ri+1, submission:rec.submissions, photo:name, grid:g, correct:ok, wrong, matches:matches(g,V), best:top, bestCells:same,
              api:{provider:"human", participant:who, secs:(Date.now()-t0)/1000}};
  if(!ok && ri){                     /* runner.play 의 깃발과 같다 */
    const p=e.rounds[ri-1], ms=matches(g,V);
    const hits={rule:perseverative(g,V,p.rule,r.rule,r.side), side:ms.some(([d])=>d===p.side),
                both:ms.some(([d,k])=>d===p.side&&k===p.rule), near:top.length===1&&top[0][1]===p.rule&&same>=7,
                repeat:prevSuccess!==null&&flat(g)===prevSuccess};
    for(const [k,hit] of Object.entries(hits)) if(hit){ rec[k]++; call[k]=true; }
  }
  log.calls.push(call);
  t0=Date.now();
  say({ok});
  const row=document.createElement("div");
  row.className="row";
  row.innerHTML=`<img src="${PH[name].img}" alt="">${miniHTML(g,wrong)}<div class="v ${ok?"o":"x"}">${ok?"정답":"틀림"}</div>`;
  const lg=document.getElementById("log"); lg.appendChild(row); row.scrollIntoView({block:"nearest"});

  streak = ok ? streak+1 : 0;
  busy=true; ready();
  if(ok && streak>=CFG.k){
    rec.solved=true; rec.streak=streak; prevSuccess=flat(g);
    setTimeout(()=>{ busy=false; nextRound(); }, 900);
  } else if(subs>=CFG.T){
    rec.budgetExhausted=true;
    log.status = ri===0 ? "round1_unsolved" : `stopped_round${ri+1}`;
    setTimeout(()=>{ busy=false; nextEpisode(); }, 1200);
  } else {
    setTimeout(()=>{ busy=false; paint(); }, 700);        /* 다음 제출은 새 사진: 말없이 바뀐다 */
  }
}

function nextRound(){
  ri++;
  if(ri>=ep().rounds.length){ log.status="completed"; nextEpisode(); return; }
  openRound();
}

function nextEpisode(){
  log.memo=document.getElementById("memo").value;
  if(MODE==="all"){
    toast(log.status==="completed" ? "다섯 라운드 완료" : "이 라운드에서 끝남");
    document.getElementById("game").style.display="none";
    return;
  }
  ei++;
  if(ei>=EPS.length){
    document.getElementById("game").style.display="none";
    document.getElementById("done").style.display="";
    return;
  }
  toast(`문제 ${ei+1} 시작`);
  openEpisode();
}

function toast(t){
  const el=document.getElementById("toast");
  el.textContent=t; el.className="toast"; void el.offsetWidth; el.className="toast go2";
}

function save(){
  if(log && ei<EPS.length) log.memo=document.getElementById("memo").value;
  const blob=new Blob([JSON.stringify({name:who, finishedAt:new Date().toISOString(), config:CFG, sessions:out}, null, 1)],
                      {type:"application/json"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download=`블록_사람_${who}.json`;
  a.click();
}

function paintHint(){
  const h=document.getElementById("hint");
  if(MODE!=="all" || !document.getElementById("showHint").checked || !log){ h.innerHTML=""; return; }
  const r=rd(), name=photoName(), V=views(name), p=ri?ep().rounds[ri-1]:null;
  h.innerHTML=`<div class="hint"><div>R${ri+1} · 사진 ${Math.min(subs,r.photos.length-1)+1}/${r.photos.length} (${name})<br>`
    +`목표 ${SIDE_KO[r.side]} · 규칙 ${RULE_KO[r.rule]}`+(p?`<br>직전 라운드: ${SIDE_KO[p.side]} · ${RULE_KO[p.rule]}`:"")
    +`<br><span class="muted">사진에서 앞 = 왼쪽 아래, 오른쪽 = 오른쪽 아래, 왼쪽 = 왼쪽 위, 뒤 = 오른쪽 위</span></div>`
    +miniHTML(V[r.side][r.rule], [], true)+`</div>`;
}

function pickStart(){
  ei=+document.getElementById("pickEp").value;
  document.getElementById("game").style.display="";
  openEpisode(+document.getElementById("pickRd").value);
}

if(MODE==="all"){
  document.getElementById("who-box").style.display="none";
  document.getElementById("memoCard").style.display="none";
  document.getElementById("saveMid").style.display="none";
  document.getElementById("pick").style.display="";
  document.getElementById("pickEp").innerHTML=EPS.map((e,i)=>`<option value="${i}">${e.id}</option>`).join("");
  document.getElementById("pickRd").innerHTML=[0,1,2,3,4].map(i=>`<option value="${i}">${i+1}라운드부터</option>`).join("");
}
</script></body></html>
"""


def write(name, eps, mode, cfg, count):
    rounds, photos = pack(eps)
    for p in photos.values():                          # 격자 글이 소스에 그대로 보이지 않게
        p["grids"] = base64.b64encode(p["grids"].encode()).decode()
    page = ROOT / "human" / name
    page.parent.mkdir(exist_ok=True)
    page.write_text(HTML.replace("__EPS__", json.dumps(rounds, separators=(",", ":")))
                    .replace("__PHOTOS__", json.dumps(photos, separators=(",", ":")))
                    .replace("__CFG__", json.dumps(cfg, ensure_ascii=False)).replace("__MODE__", mode)
                    .replace("__COUNT__", count).replace("__TITLE__", "블록 사진" if mode == "test" else "블록 사진 (확인용)"))
    print(f"{page}  {page.stat().st_size / 1e6:.1f} MB, 사진 {len(photos)} 장")


assert "In each grid cell write a number." in P.opening("x", empty=DATA["empty"])[0]["text"]     # 안내 글이 지금 판과 같은지
base = dict(T=T, k=K, version=ROOT.name, pool=DATA["pool"], scheme=DATA["scheme"], empty=DATA["empty"], feedback="cells")
test = [everything[i] for i in picked]
write(f"{ROOT.name}.html", test, "test", dict(base, seed=SEED, sample=[e["id"] for e in test], runs=RUNS), f"문제는 모두 {len(test)}개입니다.")
print(f"  참가자용 문제 {len(test)} 개 (씨앗 {SEED}): " + " ".join(e["id"] for e in test) + f" · T={T} k={K}")
write(f"{ROOT.name}_check.html", everything, "all", base, "확인용 페이지입니다.")
