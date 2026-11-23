"""Shared analysis code for the 12x12 100-session run (raw/, data/sessions100.json). No model calls.

Graded attempt = counted attempt that is not a refusal (refusals and OpenAI invalid_prompt blocks are re-asked and never
graded). Persistent error rate of round i (2-8):
  definition 1: attempts valid under round i-1's (goal, rule) / all attempts of the round;
  definition 2 (primary): the same, counting only attempts after the first attempt judged incorrect in the round; rounds
  with no such attempt are left out.
Session value = mean of its rounds, model value = mean of sessions. Intervals: session bootstrap, 1000 resamples, seed 0,
feedback stratified by start colour, baseline unstratified.
"""
import json
from itertools import combinations
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
SPEC = {s["id"]: s for s in json.loads((ROOT / "data/sessions100.json").read_text())}
PRICES = json.loads((ROOT / "data/api_prices.json").read_text())
MODELS = ["gpt-6.1-sol", "claude-opus-5-5", "claude-sonnet-5"]
COLOURS = ["green", "red", "blue", "yellow"]
N, SEED = 1000, 0


def load(cond, m, graded=True):
    rows = [json.loads(l) for l in (RAW / f"raw_{cond}_{m}.jsonl").read_text().splitlines() if l.strip()]
    return [r for r in rows if r.get("counted", True) and r["fail"] != "refusal"] if graded else rows


def cost(rows):
    return sum(((r["api"]["prompt_tokens"] - r["api"]["cached_tokens"]) * PRICES[r["model"]]["in"] + r["api"]["cached_tokens"] *
                PRICES[r["model"]].get("cached_in", PRICES[r["model"]]["in"]) + r["api"]["completion_tokens"] * PRICES[r["model"]]["out"]) / 1e6
               for r in rows)


def rounds(rows):
    """Per session: one record per round 2-8 with definition 1 (v1) and definition 2 (v2) values."""
    by = {}
    for r in rows:
        by.setdefault((r["session"], r["round"]), []).append(r)
    out = {}
    for (s, k), att in by.items():
        if k < 2:
            continue
        att.sort(key=lambda r: r["attempt_in_round"])
        j = next((i for i, a in enumerate(att) if not a["ok"]), None)
        after = att[j + 1:] if j is not None else []
        end = att[-1]
        out.setdefault(s, []).append({"v1": sum(a["persistent"] for a in att) / len(att),
                                      "v2": (sum(a["persistent"] for a in after) / len(after)) if after else None,
                                      "differ": SPEC[s]["rounds"][k - 1]["nb_av_differ"], "attempts": len(att),
                                      "persist_count": sum(a["persistent"] for a in att),
                                      "reached": bool(end.get("round_reached")) if end.get("round_end") else None})
    return out


def sess(R, f):
    out = {}
    for s, recs in R.items():
        v = [r[f] for r in recs if r[f] is not None]
        if v:
            out[s] = float(np.mean(v))
    return out


def draw(sids, strat, rng):
    g = {}
    for s in sorted(sids):
        g.setdefault(SPEC[s]["start"] if strat else 0, []).append(s)
    return [x[i] for x in g.values() for i in rng.integers(0, len(x), len(x))]


def h1(F, B, f):
    """Feedback minus baseline; returns (feedback, feedback CI, baseline, difference, difference CI)."""
    sf, sb = sess(F, f), sess(B, f)
    rng = np.random.default_rng(SEED)
    d = [np.mean([sf[s] for s in draw(sf, True, rng)]) - np.mean([sb[s] for s in draw(sb, False, rng)]) for _ in range(N)]
    pf, pb = np.mean(list(sf.values())), np.mean(list(sb.values()))
    rng = np.random.default_rng(SEED)
    cf = np.percentile([np.mean([sf[s] for s in draw(sf, True, rng)]) for _ in range(N)], [2.5, 97.5])
    return pf, cf, pb, pf - pb, np.percentile(d, [2.5, 97.5])


def h3(F, f):
    def stat(recs):
        a = [r[f] for r in recs if r[f] is not None and r["differ"]]
        b = [r[f] for r in recs if r[f] is not None and not r["differ"]]
        return np.mean(a) - np.mean(b) if a and b else np.nan
    allr = [r for v in F.values() for r in v]
    rng = np.random.default_rng(SEED)
    vals = [stat([r for s in draw(F, True, rng) for r in F[s]]) for _ in range(N)]
    vals = [v for v in vals if v == v]
    return stat(allr), np.percentile(vals, [2.5, 97.5]), sum(r["differ"] for r in allr if r[f] is not None), sum(not r["differ"] for r in allr if r[f] is not None)


def pair_test(res, models, f):
    out = []
    for x, y in combinations(models, 2):
        sx, sy = sess(res[x], f), sess(res[y], f)
        common = [s for s in sx if s in sy]
        rng = np.random.default_rng(SEED)
        vals = [np.mean([sx[s] - sy[s] for s in draw(common, True, rng)]) for _ in range(N)]
        ci = np.percentile(vals, [2.5, 97.5])
        out.append([x, y, float(np.mean([sx[s] - sy[s] for s in common])), ci.tolist(), bool(not (ci[0] <= 0 <= ci[1]))])
    return out
