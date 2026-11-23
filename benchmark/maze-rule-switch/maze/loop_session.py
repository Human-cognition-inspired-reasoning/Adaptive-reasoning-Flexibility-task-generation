"""Session runner (preregistration/PREREGISTRATION_V30.md; used unchanged in V32). One session = one maze and a fixed start colour, 8 rounds; the goal and the
rule change silently after K accepted paths in a row (K = 3) or after T attempts in the round. Feedback: correct /
incorrect only. All sessions advance in lockstep; every attempt is recorded (per step); --resume continues a run.

  python -m maze.loop_session --condition feedback --model anthropic:claude-opus-5-5 --max-new-tokens 12288 --T 15 \
      --effort medium --sessions-file data/sessions100.json --out-dir runs/main

Recorded per attempt: the path's validity under all nine (goal, rule) combinations of the session's start, and
`persistent` = valid under the previous round's (goal, rule) (the previous goal differs, so such a path always fails now).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from .core import parse_moves
from .loop import COLOURS, RULES, from_dict, path_cells, valid
from .loop_prompt import build_messages

K, ROUNDS = 3, 8
MAX_REFUSALS = 5    # refusals in a row on one attempt before it is counted (as a "refusal" failure kept out of the metrics)
SESSIONS = "data/sessions100.json"


def evaluate(m, start, moves, goal, rule):
    if moves is None:
        return "parse", {}
    cells, fail = path_cells(m, m.points[start], moves)
    if cells is None:
        return fail, {}
    under = {f"{g}|{r}": valid(m, start, g, r, cells) for g in COLOURS if g != start for r in RULES}
    if cells[-1] != m.points[goal]:
        return "wrong_end", under
    return (None if under[f"{goal}|{rule}"] else "rule"), under


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True, choices=("feedback", "nofeedback"))
    ap.add_argument("--model", required=True)
    ap.add_argument("--max-new-tokens", type=int, required=True)
    ap.add_argument("--T", type=int, required=True)
    ap.add_argument("--out-dir", default="runs/main")
    ap.add_argument("--sessions", nargs="+", default=None)
    ap.add_argument("--sessions-file", default=SESSIONS)
    ap.add_argument("--chunk", type=int, default=20)
    ap.add_argument("--cost-cap", type=float, default=50.0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--goal-told", action="store_true", help="v31 design check: tell the goal colour with every attempt")
    ap.add_argument("--effort", default=None, help="reasoning effort sent to the API (OpenAI reasoning_effort, Anthropic output_config.effort); default: not sent")
    a = ap.parse_args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{a.condition}_" + a.model.split(":", 1)[1]
    raw = out / f"raw_{tag}.jsonl"
    if raw.exists() and not a.resume:
        sys.exit(f"{raw} exists; results are never overwritten")
    spec = json.loads(Path(a.sessions_file).read_text())
    if a.sessions:
        spec = [s for s in spec if s["id"] in a.sessions]
    elif a.condition == "nofeedback":
        spec = [s for s in spec if s["baseline"]]
    prov, name = a.model.split(":", 1)
    if prov == "dummy":
        from .loop import bfs

        class _Dummy:          # flow check: shortest route to a random non-start colour
            rng = random.Random(0)

            def __call__(self, prompts, metas, step):
                outs = []
                for m, s in metas:
                    g = self.rng.choice([c for c in COLOURS if c != s])
                    d, cur, path = bfs(m, m.points[g]), m.points[s], []
                    while cur != m.points[g]:
                        mv, cur = min(((mv, x) for mv, x in m.nbrs(cur)), key=lambda t: d.get(t[1], 1e9))
                        path.append(mv)
                    outs.append("MOVES: " + " ".join(path))
                stops = ["refusal" if (name == "refuse" and self.rng.random() < 0.3) else "stop" for _ in outs]
                outs = ["" if st_ == "refusal" else o_ for o_, st_ in zip(outs, stops)]
                self.last_meta = {"api": [{"finish_reason": st_, "stop_reason": st_, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0} for st_ in stops]}
                return outs, None, None
        backend, pr = _Dummy(), {"in": 0, "out": 0}
    else:
        from .backends import AnthropicChat, OpenAIChat
        backend = OpenAIChat(name, None, a.max_new_tokens, a.effort) if prov == "openai" else AnthropicChat(name, a.max_new_tokens, a.effort)
        pr = json.loads((Path(__file__).resolve().parents[1] / "data/api_prices.json").read_text())[a.model]
    st = []
    for s in spec:
        order = list(RULES)
        random.Random(s["id"]).shuffle(order)
        st.append({"id": s["id"], "start": s["start"], "rounds": s["rounds"], "order": order, "maze": from_dict(s["maze"]),
                   "attempts": [], "round": 1, "in_round": 0, "streak": 0, "done": False})
    cfg = {"condition": a.condition, "model": a.model, "max_new_tokens": a.max_new_tokens, "T": a.T, "k": K, "rounds": ROUNDS,
           "sessions_file": a.sessions_file, "sessions": [s["id"] for s in st], "history_limit": 10, "presentation": "list", "feedback": "correct/incorrect only",
           "temperature": "API default (not sent)", "rule_list_order": "per session, seeded by session id", "goal_told": a.goal_told,
           "effort": a.effort or "API default (not sent)"}
    step = 0
    if a.resume and raw.exists():
        byid = {s["id"]: s for s in st}
        rows = sorted((json.loads(l) for l in raw.read_text().splitlines() if l.strip()), key=lambda r: (r["session"], r["attempt_total"]))
        for r in rows:
            if not r.get("counted", True):       # a refusal that was re-asked: not an attempt
                continue
            s = byid[r["session"]]
            s["attempts"].append({"output": r["output"], "ok": r["ok"], "goal": r["goal"]})
            s["in_round"], s["streak"] = r["attempt_in_round"], r["streak"]
            if r["round_end"]:
                s["round"] += 1
                s["in_round"], s["streak"] = 0, 0
                s["done"] = s["round"] > ROUNDS
        step = max((r["step"] for r in rows), default=0)
        cfg["resumed_after_step"] = step
        (out / f"config_{tag}.resume.json").write_text(json.dumps(cfg, indent=1))
    else:
        (out / f"config_{tag}.json").write_text(json.dumps(cfg, indent=1))
    t0, cost = time.time(), 0.0
    with raw.open("a" if a.resume else "w") as f:
        while any(not s["done"] for s in st):
            step += 1
            active = [s for s in st if not s["done"]]
            prompts = [build_messages(s["maze"], s["start"], s["attempts"], s["order"], a.condition == "feedback",
                                      goal=s["rounds"][s["round"] - 1]["goal"] if a.goal_told else None) for s in active]
            # rows are written after each chunk of sessions (2026-10-28), so a crash inside a step keeps the chunks already answered
            # (earlier runs wrote a step only after all its chunks: calls finished in a crashed step were not recorded)
            for j in range(0, len(active), a.chunk):
                o_chunk, _, _ = backend(prompts[j:j + a.chunk], [(s["maze"], s["start"]) for s in active[j:j + a.chunk]], step)
                for s, p, o, x in zip(active[j:j + a.chunk], prompts[j:j + a.chunk], o_chunk, backend.last_meta["api"]):
                    cur = s["rounds"][s["round"] - 1]
                    prev = s["rounds"][s["round"] - 2] if s["round"] > 1 else None
                    refusal = x.get("stop_reason") == "refusal" or x.get("finish_reason") == "refusal"
                    if refusal and s.get("refused", 0) + 1 < MAX_REFUSALS:
                        # decision (2026-10-22, option a): an external refusal stop is not an attempt; it is recorded, kept out of
                        # the metrics and the same attempt is asked again with no feedback (identical prompt next step)
                        s["refused"] = s.get("refused", 0) + 1
                        f.write(json.dumps({"model": a.model, "condition": a.condition, "session": s["id"], "n": s["maze"].n, "start": s["start"],
                                            "step": step, "round": s["round"], "goal": cur["goal"], "rule": cur["rule"],
                                            "prev": f"{prev['goal']}|{prev['rule']}" if prev else None, "attempt_in_round": s["in_round"] + 1,
                                            "attempt_total": len(s["attempts"]) + 1, "output": o, "fail": "refusal", "ok": False, "parsed": False,
                                            "refusal": True, "counted": False, "refusals_in_a_row": s["refused"], "truncated": False,
                                            "api": {k: v for k, v in x.items() if k != "text"}, "prompt": p}) + "\n")
                        cost += ((x["prompt_tokens"] - x["cached_tokens"]) * pr["in"] + x["cached_tokens"] * pr.get("cached_in", pr["in"])
                                 + x["completion_tokens"] * pr["out"]) / 1e6
                        continue
                    s["refused"] = 0
                    moves, perr = parse_moves(o)
                    fail, under = evaluate(s["maze"], s["start"], moves, cur["goal"], cur["rule"])
                    if refusal:      # MAX_REFUSALS refusals in a row on one attempt: counted so the round can progress, kept out of metrics
                        fail, under = "refusal", {}
                    ok = fail is None
                    s["streak"] = s["streak"] + 1 if ok else 0
                    s["in_round"] += 1
                    s["attempts"].append({"output": o, "ok": ok, "goal": cur["goal"]})
                    reached = s["streak"] >= K
                    end = reached or s["in_round"] >= a.T
                    f.write(json.dumps({"model": a.model, "condition": a.condition, "session": s["id"], "n": s["maze"].n, "start": s["start"], "step": step,
                                        "refusal": refusal, "counted": True,
                                        "round": s["round"], "goal": cur["goal"], "rule": cur["rule"],
                                        "prev": f"{prev['goal']}|{prev['rule']}" if prev else None, "attempt_in_round": s["in_round"],
                                        "attempt_total": len(s["attempts"]), "output": o, "moves": moves, "parse_error": perr, "parsed": moves is not None, "fail": fail,
                                        "ok": ok, "valid_under": under, "persistent": bool(prev and under.get(f"{prev['goal']}|{prev['rule']}")),
                                        "n_moves": len(moves) if moves else None, "streak": s["streak"], "round_end": end,
                                        "round_reached": reached if end else None, "truncated": x.get("finish_reason") in ("length", "length_error"),
                                        "api": {k: v for k, v in x.items() if k != "text"}, "prompt": p}) + "\n")
                    cost += ((x["prompt_tokens"] - x["cached_tokens"]) * pr["in"] + x["cached_tokens"] * pr.get("cached_in", pr["in"])
                             + x["completion_tokens"] * pr["out"]) / 1e6
                    if end:
                        s["round"] += 1
                        s["in_round"], s["streak"] = 0, 0
                        s["done"] = s["round"] > ROUNDS
                f.flush()
            f.flush()
            print(f"step {step}: {len(active)} active, done {sum(s['done'] for s in st)}, cost ${cost:.2f}, {time.time() - t0:.0f}s", flush=True)
            if cost >= a.cost_cap:
                print(f"COST CAP ${a.cost_cap} reached; stopping", flush=True)
                break
    print(f"finished {a.model} {a.condition}: {sum(s['done'] for s in st)}/{len(st)} sessions")


if __name__ == "__main__":
    main()
