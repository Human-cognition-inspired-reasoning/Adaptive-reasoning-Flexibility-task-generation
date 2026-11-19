# -*- coding: utf-8 -*-
"""빙고 전구 v3 사람용 페이지 두 개를 만든다.   python3 bench/make_human.py [문제수=10] [씨앗=1117]

  human/bingo-lights.html        참가자용. 문제를 씨앗으로 뽑아 차례로 풀고, 끝나면 결과 JSON 을 내려받는다(v2 페이지와 같은 방식).
  human/bingo-lights_check.html   화면 확인용. 모든 문제(Y001~)에서 골라 아무 라운드부터 풀어 볼 수 있고, 숨은 목표 · 금지 전구와
                           그 판의 정답 예를 켜고 끌 수 있다. 결과는 남기지 않는다.

v2 `전구-빙고-어느줄4/bench/make_human.py` 의 페이지를 옮겼다. 모델과 같은 문제 · 같은 예산 · 같은 피드백이다.
  · 숨은 것(어느 줄이 목표인지 · 금지 전구)도, 라운드 전환도 알리지 않는다. 예산 T · k 도 알리지 않는다
  · 판정은 모델이 받는 글과 같은 내용만: 완성된 줄, 정답 여부, 틀린 전구(목표 오류와 금지 위반을 가리지 않음)
  · v3: **제출마다 그 제출의 판(배선표)** 을 보여 준다(runner 와 같이 라운드의 j 번째 제출 = 판 j). 판이 바뀐다고 알리지 않는다
    (모델의 글에도 없다). 틀린 전구는 전구 번호라 다음 판에서도 같은 전구를 가리킨다
  · 버튼을 누를 때마다 전구판이 그 순서대로 바뀌어 보이고, 누른 순서 아래에 누를 때마다의 작은 판이 남는다
결과 JSON 은 `bench/human_runs.py` 로 runs 꼴로 펼쳐 `bench/score.py` 로 채점한다(호출마다 board 를 남긴다).

참가자용 문제는 GPT-6.1 Sol 런(Y001~Y100)이 있는 문제에서, 1 라운드를 풀었는지로 층을 나눠 층 크기에 비례해 뽑는다
(v2 와 같은 방식, 모델이 하나라 층이 둘).
"""
import base64
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
args = [a for a in sys.argv[1:] if not a.startswith("--")]
N = int(args[0]) if args else 10
SEED = int(args[1]) if len(args) > 1 else 1117
T, K = 10, 2                                          # runner 기본값과 같다
RUNS = ["20261117_101045", "20261117_111159"]         # GPT-6.1 Sol Y001~Y100


def r1_solved():
    got = {}
    for run in RUNS:
        for f in (ROOT / "runs" / run).glob("*_Y*.json"):
            log = json.loads(f.read_text())
            got[log["episode"]] = int(bool(log["rounds"][0]["solved"]))
    return got


def quota(layers, n, total):
    q = {k: n * len(v) / total for k, v in layers.items()}
    take = {k: int(x) for k, x in q.items()}
    for k in sorted(q, key=lambda k: q[k] - take[k], reverse=True)[:n - sum(take.values())]:
        take[k] += 1
    return take


CODE = {"ON": "O", "OFF": "F", "KEEP": "K"}


def pack(e, hints=False):
    """배선표를 글자로 줄인다(버튼 한 줄 = 9 글자, 버튼끼리 |). hints 면 판마다 정답 예도 넣는다(확인용)."""
    rounds = []
    for r in e["rounds"]:
        rd = dict(goal=r["goal"], forbidden=r["forbidden"],
                  boards=["|".join("".join(CODE[x] for x in row) for row in b["table"]) for b in r["boards"]])
        if hints:
            rd["shortest"] = [b["shortest"] for b in r["boards"]]
        rounds.append(rd)
    return dict(id=e["id"], rounds=rounds)


everything = json.loads((ROOT / "episodes/episodes.json").read_text())
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
 .wrap{max-width:1040px;margin:0 auto}
 .cols{display:flex;gap:16px;align-items:flex-start}
 .mid{flex:1 1 0;min-width:0}
 .side{flex:0 0 322px}
 .side .card{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto}
 @media (max-width:820px){.cols{flex-direction:column}.side{flex:none;width:100%}}
 h1{font-size:19px;margin:0 0 14px}
 .card{background:#fff;border:1px solid #e3e3e3;border-radius:12px;padding:20px;margin-bottom:16px}
 .muted{color:#777;font-size:13px}
 .prog{display:flex;gap:5px;margin:0 0 14px}
 .seg{flex:1 1 0;height:8px;background:#e2e2e2;border-radius:4px}
 .seg.on{background:#1a1a1a}
 .play{display:flex;gap:26px;align-items:flex-start;justify-content:center;flex-wrap:wrap;margin-top:12px}
 .grid svg{display:block}
 .btns{display:flex;flex-direction:column;gap:9px;min-width:280px}
 .bt{display:flex;align-items:center;gap:12px;text-align:left}
 .bt .k{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:9px;
        background:#1a1a1a;color:#fff;font-weight:800;font-size:16px;flex:0 0 34px}
 .bt .e{font-weight:600;font-size:14px;color:#333}
 .bt .e i{font-style:normal;color:#8a8f94;font-weight:600}
 button{font:inherit;cursor:pointer;font-weight:700;padding:9px 14px;line-height:1.3;
        background:linear-gradient(#ffffff,#eceef0);border:1px solid #bcc0c4;
        border-bottom:4px solid #aeb3b8;border-radius:12px;color:#1a1a1a;
        box-shadow:0 3px 7px rgba(0,0,0,.10);transition:transform .05s,box-shadow .05s}
 button:hover{background:linear-gradient(#ffffff,#e4e7ea)}
 button:active{transform:translateY(3px);border-bottom-width:1px;box-shadow:inset 0 3px 6px rgba(0,0,0,.14)}
 .go{background:linear-gradient(#2c2c2c,#141414);color:#fff;border-color:#141414;
     border-bottom:4px solid #000;padding:12px 26px;letter-spacing:2px}
 .go:hover{background:linear-gradient(#3a3a3a,#1e1e1e)}
 .seqbar{display:flex;gap:10px;align-items:center;justify-content:center;margin:18px 0 2px;flex-wrap:wrap}
 .seq{flex:0 0 auto;width:420px;max-width:100%;min-height:74px;border:2px dashed #c3c3c3;border-radius:11px;
      padding:6px 10px;background:#fafafa;display:flex;align-items:center;justify-content:center}
 .seq.empty{color:#bbb;font-size:13px}
 .steps{display:flex;gap:7px;flex-wrap:wrap;justify-content:center}
 .step{display:flex;flex-direction:column;align-items:center;gap:2px}
 .step b{display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:22px;padding:0 5px;
         border-radius:6px;background:#eef0f2;border:1px solid #d9dde1;font-size:13px;font-weight:800;color:#2a2a2a}
 .vd{font-weight:800;font-size:14px;min-height:44px;text-align:center;margin-top:10px}
 .vd .w{display:block;font-weight:600;color:#444}
 .vd.o .v{color:#1f7a3f}
 .vd.x .v{color:#b04040}
 .row{padding:9px 4px;border-bottom:1px solid #f1f1f1}
 .row .top{font-size:12px;font-weight:700}
 .row .top .o{color:#1f7a3f}
 .row .top .x{color:#b04040}
 .row .steps{justify-content:flex-start;margin-top:4px}
 .row .step b{min-width:21px;height:20px;font-size:12px}
 textarea{width:100%;min-height:72px;font:inherit;font-size:13px;padding:10px;
          border:1px solid #ddd;border-radius:8px;resize:vertical}
 input,select{font:inherit;padding:9px 12px;border:1px solid #ccc;border-radius:8px}
 input{width:240px}
 .toast{position:fixed;left:50%;top:60px;transform:translateX(-50%);z-index:50;background:#1a1a1a;
        color:#fff;font-size:17px;font-weight:800;padding:12px 26px;border-radius:999px;
        opacity:0;pointer-events:none;box-shadow:0 6px 20px rgba(0,0,0,.18)}
 .toast.go2{animation:pop 1.4s ease-out}
 @keyframes pop{0%{opacity:0}15%{opacity:1}75%{opacity:1}100%{opacity:0}}
 .dev{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
 .hint{margin-top:10px;padding:10px 12px;border-radius:8px;background:#fff7e6;border:1px solid #f0d9a8;font-size:13px}
</style></head><body>
<div class="wrap">
 <div id="toast" class="toast"></div>

 <div id="intro" class="card">
  <h1>빙고 전구</h1>
  <p>이 퍼즐은 조금 특이합니다. 푸는 방법을 많이 알려 드릴 수 없습니다.</p>
  <p>전구 아홉 개가 3 × 3 으로 놓여 있고, 번호는 왼쪽 위부터 1~9 입니다. 버튼을 눌러 <b>맞는 빙고 줄</b>을 켜세요.
     빙고 줄은 가로 · 세로 · 대각선으로 세 전구가 모두 켜진 줄이고, 1-5-9 처럼 전구 번호로 부릅니다.</p>
  <ul class="muted" style="padding-left:20px">
   <li>버튼마다 몇 전구를 켜고, 몇 전구를 끄고, 나머지는 그대로 둡니다. 버튼이 하는 일은 버튼 옆에 적혀 있습니다.</li>
   <li>매 시도는 <b>모든 전구가 꺼진 상태</b>에서 시작합니다. 누를 버튼의 순서를 통째로 제출합니다.
       같은 버튼을 여러 번 눌러도 됩니다.</li>
   <li>제출 전까지 전구판은 지금까지 누른 순서대로 켜고 끈 모습입니다. 누른 순서 아래의 작은 판은 누를 때마다의 모습입니다.</li>
   <li><b>맞는 줄을 켜는 것만으로는 충분하지 않습니다.</b> 어느 줄이 필요한지, 어떤 순서가 받아들여지는지는 알려 드릴 수
       없지만, 맞았는지 틀렸는지는 매번 알려 드립니다. 통하는 순서는 늘 있습니다.</li>
   <li>시간 제한은 없습니다. __COUNT__</li>
  </ul>
  <div id="who-box">
   <p style="margin-bottom:6px">이름 또는 식별자</p>
   <input id="who">
   <button class="go" style="margin-left:8px" onclick="start()">시작</button>
  </div>
 </div>

 <div id="pick" class="card" style="display:none">
  <h1>확인용: 문제 고르기</h1>
  <div class="dev">
   <select id="pickEp"></select>
   <select id="pickRd"></select>
   <button class="go" onclick="pickStart()">이 문제 풀기</button>
   <label class="muted"><input type="checkbox" id="showHint" onchange="paint(lastFlag)" style="width:auto"> 숨은 목표 · 금지 전구와 이 판의 정답 예 보기</label>
  </div>
  <div class="muted" style="margin-top:8px">라운드를 고르면 그 라운드의 첫 판부터 시작합니다(직전 라운드 규칙은 문제에 정해진 그대로). 결과는 남기지 않습니다.</div>
 </div>

 <div id="game" style="display:none">
  <div class="prog" id="prog"></div>
  <div class="cols"><div class="mid">
  <div class="card">
   <div class="muted" id="head"></div>
   <div class="play">
    <div class="grid" id="grid"></div>
    <div class="btns" id="btns"></div>
   </div>
   <div class="vd" id="vd"></div>
   <div class="seqbar">
    <div class="seq empty" id="seq">버튼을 눌러 순서를 만드세요</div>
    <button onclick="undo()">⌫</button>
    <button onclick="clr()">지우기</button>
    <button class="go" onclick="submit()">제출</button>
   </div>
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
const EPS = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("__DATA__"), c=>c.charCodeAt(0)))), CFG = __CFG__;
const LINES=[[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
const NAMES=LINES.map(l=>l.map(c=>c+1).join("-")), LETTERS="ABCDEFGH";
const UNPACK={O:"ON",F:"OFF",K:"KEEP"};
let who="", ei=0, ri=0, seq=[], out=[], log=null, t0=0, streak=0, subs=0, busy=false, prevSuccess=null, lastFlag=null;

const ep=()=>EPS[ei], rd=()=>ep().rounds[ri];
const tableOf=(r,j)=>r.boards[j].split("|").map(s=>[...s].map(c=>UNPACK[c]));
const tb=()=>tableOf(rd(), Math.min(subs, rd().boards.length-1));   /* 이 라운드의 j 번째 제출 = 판 j (runner 와 같음) */
const same=(a,b)=>a.length===b.length && a.every((x,i)=>x===b[i]);

/* bench/engine 의 apply_press · run · lines · need · off_goal · touches · evaluate 와 같다 */
function press(st,row){ return st.map((s,i)=>row[i]==="ON"?1:(row[i]==="OFF"?0:s)); }
function trace(table,presses){ let st=Array(9).fill(0); const o=[]; for(const b of presses){ st=press(st,table[b]); o.push(st); } return o; }
function run(table,presses){ const t=trace(table,presses); return t.length?t[t.length-1]:Array(9).fill(0); }
function lines(st){ return LINES.map((l,i)=>l.every(c=>st[c])?i:-1).filter(i=>i>=0); }
function need(goal){ const s=new Set(); goal.forEach(i=>LINES[i].forEach(c=>s.add(c))); return s; }
function offGoal(st,goal){
  const want=need(goal), bad=new Set([...want].filter(c=>!st[c]));
  lines(st).forEach(i=>{ if(!goal.includes(i)) LINES[i].forEach(c=>{ if(!want.has(c)) bad.add(c); }); });
  return [...bad].sort((x,y)=>x-y);
}
const touches=(table,presses,light)=>presses.some(b=>table[b][light]==="ON");
function evaluate(table,goal,forbidden,presses){
  const got=run(table,presses), wrong=offGoal(got,goal), bad=touches(table,presses,forbidden);
  const flagged=[...new Set([...wrong,...(bad?[forbidden]:[])])].sort((x,y)=>x-y);
  let status;
  if(!presses.length) status="empty";
  else if(!wrong.length && !bad) status="solved";
  else status = !wrong.length ? "rule" : (bad ? "both" : "goal");
  return {ok:status==="solved", status, lines:lines(got), goalWrong:wrong, flagged, result:got};
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
  log={episode:e.id, buttons:tableOf(e.rounds[0],0).length, T:CFG.T, T1:CFG.T, k:CFG.k, feedback:"lights", rounds:[], calls:[],
       startedAt:new Date().toISOString(), memo:""};
  if(MODE==="test") out.push(log);
  ri=startRound||0; prevSuccess=null;
  document.getElementById("memo").value="";
  document.getElementById("prog").innerHTML = MODE==="test" ? EPS.map((_,i)=>`<div class="seg ${i<=ei?"on":""}"></div>`).join("") : "";
  document.getElementById("head").textContent = MODE==="test" ? `문제 ${ei+1} / ${EPS.length}` : `${e.id}`;
  document.getElementById("log").innerHTML="";
  say(null);
  openRound();
}

/* 새 라운드는 말없이 연다: 직전 판정 글은 그대로 두고 배선만 바뀐다 */
function openRound(){
  log.rounds.push({round:ri+1, submissions:0, solved:false, unparsed:0, streak:0, forbid:0, rule:0, goal:0, both:0, repeat:0});
  subs=0; streak=0; t0=Date.now(); seq=[]; lastFlag=null; paint();
}

const COL={on:"#F5C518", onLine:"#C99A06", off:"#E4E7EB", offLine:"#B8BEC6", num:"#6B7785", red:"#C64343", ring:"#F5C518"};
function gridSVG(st, size, flagged, lit){
  const g=size*.06, c=(size-g*2)/3, r=c*.36;
  let s=`<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="전구판">`;
  for(let i=0;i<9;i++){
    const cx=(i%3)*(c+g)+c/2, cy=Math.floor(i/3)*(c+g)+c/2, on=st[i];
    s+=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="${on?COL.on:COL.off}" stroke="${on?COL.onLine:COL.offLine}" stroke-width="${size>100?2:1}"/>`;
    if(!on && lit && lit.has(i)) s+=`<circle cx="${cx}" cy="${cy}" r="${r*.62}" fill="none" stroke="${COL.ring}" stroke-width="${size>100?3:1.5}"/>`;
    if(flagged && flagged.includes(i)) s+=`<circle cx="${cx}" cy="${cy}" r="${r+ (size>100?6:2.5)}" fill="none" stroke="${COL.red}" stroke-width="${size>100?3:1.6}"/>`;
    if(size>100) s+=`<text x="${cx}" y="${cy+5}" text-anchor="middle" font-size="${c*.22}" font-weight="700" fill="${on?"#5a4300":COL.num}">${i+1}</text>`;
  }
  return s+"</svg>";
}
function effect(row){
  const on=[], off=[];
  row.forEach((e,i)=>{ if(e==="ON") on.push(i+1); else if(e==="OFF") off.push(i+1); });
  return `켬 ${on.join(" ")}`+(off.length?` <i>· 끔 ${off.join(" ")}</i>`:"");
}

function paint(flagged){
  lastFlag=flagged||null;
  const t=tb(), states=trace(t,seq), st=states.length?states[states.length-1]:Array(9).fill(0);
  const lit=new Set(); states.forEach(s=>s.forEach((v,i)=>{ if(v) lit.add(i); }));
  document.getElementById("grid").innerHTML=gridSVG(st, 300, flagged, lit);
  document.getElementById("btns").innerHTML=t.map((row,b)=>
    `<button class="bt" onclick="tap(${b})"><span class="k">${LETTERS[b]}</span><span class="e">${effect(row)}</span></button>`).join("");
  const el=document.getElementById("seq");
  el.className="seq"+(seq.length?"":" empty");
  el.innerHTML=seq.length ? `<span class="steps">${seq.map((b,k)=>
      `<span class="step"><b>${LETTERS[b]}</b>${gridSVG(states[k], 40)}</span>`).join("")}</span>`
    : "버튼을 눌러 순서를 만드세요";
  const hint=document.getElementById("hint");
  if(MODE==="all" && document.getElementById("showHint").checked){
    const r=rd(), j=Math.min(subs, r.boards.length-1), p=ri?ep().rounds[ri-1]:null;
    hint.innerHTML=`<div class="hint">R${ri+1} · 판 ${j+1}/${r.boards.length} · 시도 ${subs+1}/${CFG.T}<br>`
      +`목표 ${r.goal.map(i=>NAMES[i]).join(" + ")} · 금지 ${r.forbidden+1}번`
      +(p?` (직전 라운드: 목표 ${p.goal.map(i=>NAMES[i]).join(" + ")} · 금지 ${p.forbidden+1}번)`:"")
      +`<br>이 판의 정답 예: ${r.shortest[j].map(b=>LETTERS[b]).join(" ")}</div>`;
  } else hint.innerHTML="";
}

function tap(b){ if(busy) return; seq.push(b); paint(lastFlag); }
function undo(){ if(busy) return; seq.pop(); paint(lastFlag); }
function clr(){ if(busy) return; seq=[]; paint(lastFlag); }

const linesText=l=>"완성된 줄: "+(l.length ? l.map(i=>NAMES[i]).join(" · ") : "없음");
const verdictText=res=>res.ok ? "정답입니다"
  : (res.flagged.length ? `틀린 전구 ${res.flagged.map(i=>i+1).join(" ")}. 정답이 아닙니다` : "정답이 아닙니다");
function say(res){
  const vd=document.getElementById("vd");
  if(!res){ vd.className="vd"; vd.innerHTML=""; return; }
  vd.className="vd "+(res.ok?"o":"x");
  vd.innerHTML=`<span class="w">${linesText(res.lines)}</span><span class="v">${verdictText(res)}</span>`;
}

function submit(){
  if(busy || !seq.length) return;
  const e=ep(), r=rd(), presses=seq.slice(), j=Math.min(subs, r.boards.length-1), table=tb();
  const res=evaluate(table, r.goal, r.forbidden, presses);
  const rec=log.rounds[log.rounds.length-1];
  rec.submissions++; subs++;
  const call={round:ri+1, submission:rec.submissions, board:j, presses, status:res.status, correct:res.ok,
              lines:res.lines, goalWrong:res.goalWrong, flagged:res.flagged, result:res.result,
              api:{provider:"human", participant:who, secs:(Date.now()-t0)/1000}};
  if(!res.ok && ri){                // runner.play 의 깃발과 같다: 그 제출의 판으로
    const p=e.rounds[ri-1];
    const kept=!touches(table,presses,p.forbidden), broke=touches(table,presses,r.forbidden);
    const hits={forbid:kept&&broke, rule:kept&&broke&&same(res.lines,r.goal),
                goal:!broke&&same(res.lines,p.goal), both:kept&&broke&&same(res.lines,p.goal),
                repeat:prevSuccess!==null && same(presses, prevSuccess)};
    for(const [k,hit] of Object.entries(hits)) if(hit){ rec[k]++; call[k]=true; }
  }
  log.calls.push(call);
  t0=Date.now();
  say(res);
  const lit=new Set(); trace(table,presses).forEach(s=>s.forEach((v,i)=>{ if(v) lit.add(i); }));
  document.getElementById("grid").innerHTML=gridSVG(res.result, 300, res.ok?null:res.flagged, lit);

  const row=document.createElement("div");
  row.className="row";
  row.innerHTML=`<div class="top"><span class="${res.ok?"o":"x"}">${verdictText(res)}</span></div>`
    + `<div class="muted">${linesText(res.lines)}</div>`
    + `<div class="steps">${presses.map(b=>`<span class="step"><b>${LETTERS[b]}</b></span>`).join("")}</div>`;
  const lg=document.getElementById("log");
  lg.appendChild(row); row.scrollIntoView({block:"nearest"});

  streak = res.ok ? streak+1 : 0;
  busy=true;
  if(res.ok && streak>=CFG.k){
    rec.solved=true; rec.streak=streak; rec.solution=presses; prevSuccess=presses;
    setTimeout(()=>{ busy=false; nextRound(); }, 900);
  } else if(subs>=CFG.T){
    rec.budgetExhausted=true;
    log.status = ri===0 ? "round1_unsolved" : `stopped_round${ri+1}`;
    setTimeout(()=>{ busy=false; nextEpisode(); }, 1200);
  } else {
    /* 다음 제출은 다음 판: 배선이 말없이 바뀐다. 틀린 전구 표시는 전구 번호라 그대로 둔다 */
    setTimeout(()=>{ busy=false; seq=[]; paint(res.ok ? null : res.flagged); }, 700);
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
  const blob=new Blob([JSON.stringify({name:who, finishedAt:new Date().toISOString(), config:CFG, sessions:out},
                                      null, 1)], {type:"application/json"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download=`전구v3_사람_${who}.json`;
  a.click();
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
    data = base64.b64encode(json.dumps(eps, separators=(",", ":"), ensure_ascii=False).encode()).decode()
    page = ROOT / "human" / name
    page.parent.mkdir(exist_ok=True)
    page.write_text(HTML.replace("__DATA__", data).replace("__CFG__", json.dumps(cfg, ensure_ascii=False))
                    .replace("__MODE__", mode).replace("__COUNT__", count)
                    .replace("__TITLE__", "빙고 전구" if mode == "test" else "빙고 전구 (확인용)"))
    print(f"{page}  {page.stat().st_size / 1e6:.1f} MB")


test = [pack(everything[i]) for i in picked]
write(f"{ROOT.name}.html", test, "test",
      dict(T=T, k=K, seed=SEED, sample=[e["id"] for e in test], version=ROOT.name, runs=RUNS), f"문제는 모두 {len(test)}개입니다.")
print(f"  참가자용 문제 {len(test)} 개 (씨앗 {SEED}): " + " ".join(e["id"] for e in test) + f" · T={T} k={K}")
write(f"{ROOT.name}_check.html", [pack(e, hints=True) for e in everything], "all", dict(T=T, k=K, version=ROOT.name),
      "확인용 페이지입니다.")
