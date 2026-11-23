# Maze rule-switch benchmark

A maze task in which the goal cell and the path rule change without notice. It measures how often a language model keeps
submitting paths that worked in the previous round (perseveration). The task carries the perseverative error of the
Wisconsin Card Sorting Test over to maze navigation.

Preregistrations and result documents are kept in their original Korean. This README summarizes them.

## Task

- **Maze**: an n×n grid (12×12 in the main run, 9×9 in earlier runs). A perfect maze from randomized depth-first search,
  with 4% of the interior walls removed at random to create loops (no open 2×2 rooms). Four coloured cells (green, red, blue,
  yellow) lie on passages, pairwise at least 5 steps apart.
- **Session**: one maze and one start colour, fixed for the session and told to the model. Eight rounds. Each round has a goal
  (one of the other three colours) and a rule; neither is told. The model knows that the goal is one of the other three
  coloured cells and knows the three candidate rules.
- **Rules**
  - **NB** (no-backtrack): never enter a cell already visited.
  - **AV** (avoid): pass through no coloured cell other than the start and the goal.
  - **VIA** (via-point): pass through at least one coloured cell that is neither the start nor the goal.
- **Attempts and feedback**: the model answers with a move list (`MOVES: U D L R ...`). An attempt is correct if it stays in
  the maze, crosses no wall, ends on the goal and satisfies the rule. Feedback is only "Correct." or "Incorrect.". The
  conversation keeps the last 10 attempts.
- **Switches**: after 3 correct attempts in a row (k = 3) or 15 attempts in the round (T = 15), the next round starts. Goal
  and rule both change (neither may repeat the previous one), and the switch is not announced.
- **Conditions**: feedback (100 sessions) and baseline (10 sessions; the feedback sentence is removed, switches follow the
  same internal criterion).
- **Refusals**: Anthropic `refusal` stops and OpenAI HTTP 400 `invalid_prompt` blocks are not counted as attempts; the same
  attempt is asked again without feedback. After 5 in a row the attempt is counted once as a refusal and excluded from all
  metrics.

## Metrics

**Persistent error rate** (primary). In round i (2–8), the share of attempts that are valid under round i−1's (goal, rule).
Because the goal changes at every switch, such an attempt is always wrong in the current round. Session value = mean over
rounds, model value = mean over sessions.

- **Definition 1** (preregistered): counts every attempt of round i.
- **Definition 2** (primary, adopted after seeing results): counts only the attempts after the first attempt judged incorrect
  in round i. Rounds with no attempt after the first incorrect one are left out. The same rule applies to the baseline.

Definition 2 was adopted after the v30 and v32 results. Switches are not announced, so the first attempt after a switch is
made before any feedback about the switch, and repeating a path confirmed by three correct answers is correct use of
feedback. Definition 1 counts that attempt as perseveration, which penalizes models that use feedback well. The rationale,
the verification plan fixed before computing, its results and the direction of definition 2's bias are in
[`results/DEFINITION.md`](results/DEFINITION.md) and [`results/h1_h2_review/`](results/h1_h2_review/). The definition 1
verdict stays as the preregistered verdict, and both definitions are reported side by side.

The **reference-format MPE** is a different quantity: the share of attempts that break the round's own rule (rounds 2–8),
regardless of the previous round, with the same definition 1 / 2 split. Mean error is the share of incorrect attempts (rounds
1–8). These tables are for reference and are not used for the hypothesis verdicts
([`results/METRICS_TABLE.md`](results/METRICS_TABLE.md), [`results/per_problem/`](results/per_problem/)).

## Hypotheses and decision rules (preregistered)

Intervals: session bootstrap, 1000 resamples, seed 0 (feedback stratified by start colour, baseline unstratified).

- **H1, feedback use**: supported if the 95% upper bound of (feedback − baseline) persistent error rate is below 0.
- **H2, model discrimination**: with two or more H1-passing models, supported if any pair of passing models (paired
  resampling over the same sessions) has an interval excluding 0.
- **H3**: for H1-passing models, rounds whose new (start, goal) has different NB and AV answers minus the other rounds
  (two-sided).

## Results: 12×12, 100 sessions, reasoning effort medium

| Model | Def. 2 feedback | Def. 2 baseline | Difference [95%] | H1 (def. 2) | H1 (def. 1) | Adaptation success | Unreached switches |
|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | 0.049 | 0.186 | −0.137 [−0.165, −0.101] | supported | supported | 0.999 | 1/700 |
| claude-sonnet-5 | 0.084 | 0.185 | −0.101 [−0.139, −0.051] | supported | rejected | 0.726 | 192/700 |
| gpt-6.1-sol | 0.112 | 0.185 | −0.073 [−0.097, −0.052] | supported | rejected | 0.989 | 8/700 |

- **Definition 2**: H1 passes for 3/3 models. H2 is supported for all three pairs (gpt-6.1-sol − claude-opus-5-5 +0.063
  [+0.051, +0.076], gpt-6.1-sol − claude-sonnet-5 +0.028 [+0.013, +0.042], claude-opus-5-5 − claude-sonnet-5 −0.035
  [−0.045, −0.024]). H3 is rejected.
- **Definition 1**: H1 passes for 1/3 (claude-opus-5-5). H2 cannot be judged. H3 is rejected.
- claude-sonnet-5 sits in the middle on perseveration, but 0.24 of its failures cross a wall (0.03–0.06 for the other two)
  and its adaptation success is the lowest. Rule adaptation and spatial accuracy come apart, so the persistent error rate,
  adaptation success and failure types should be read together.
- In the 9×9 run (40 sessions, five models) and the 12×12 40-session run, claude-opus-5-5 also perseverated less than
  gpt-6.1-sol under definition 2 and H2 was supported ([`results/SUMMARY_TABLE.md`](results/SUMMARY_TABLE.md),
  [`results/VERDICT_V2.md`](results/VERDICT_V2.md)).

Full verdict: [`results/VERDICT.md`](results/VERDICT.md) (generated by `python -m analysis.verdict`).

## Layout

```
maze/                 maze generation, prompts, session runner, API backends
  core.py             grid, randomized depth-first maze generation, seeds, answer parsing
  loop.py             loop mazes (make_loop_maze_v30), coloured-cell placement, rule checks (valid), path tracing
  loop_prompt.py      messages sent to the model
  loop_session.py     session runner (feedback and baseline, --resume, dummy backend)
  backends.py         OpenAI and Anthropic backends (retries, per-call wall time, refusal handling)
generation/           maze and session generators (v30 9×9, v32 12×12, v32 300 sessions)
data/
  sessions100.json    main-run sessions s00–s99 (the 10 baseline sessions are marked)
  api_prices.json     price per million tokens used for cost accounting (three models)
raw/                  main-run raw outputs, one line per attempt (prompt field and API request ids removed)
analysis/             verdicts and reference-format tables (no model calls)
preregistration/      public versions of the preregistrations (V30, V32, V32_100), in Korean
results/              verdicts, definition 2 documents, review, reference-format tables, earlier-run verdicts, in Korean
```

The `prompt` field was removed from `raw/` because it can be rebuilt from the session file and the earlier outputs
(`maze/loop_prompt.py`). Each attempt row keeps the full output text, the move list, the verdict and failure type, validity
under all nine (goal, rule) combinations, token counts and response time.

## Running

Python 3.11, `pip install -r requirements.txt`. Run every command from this folder (`benchmark/maze-rule-switch/`).

**Reproduce the analysis** (no model calls):

```
python -m analysis.verdict     # results/VERDICT.md
python -m analysis.tables      # results/METRICS_TABLE.md, results/per_problem/def{2,1}.{md,csv}
```

**Regenerate the sessions**: the generators are seeded and write to `generated/` (not tracked). Run them in order; the
regenerated `sessions100.json` is byte-identical to the committed one.

```
python generation/gen_v30.py       # generated/v30/: 9×9 mazes and sessions
python generation/gen_v32.py       # generated/v32/mazes.json, sessions.json: 12×12, 40 sessions
python generation/gen_v32_300.py   # generated/v32/sessions300.json, sessions100.json, sessions_heldout.json
cmp generated/v32/sessions100.json data/sessions100.json
```

**Flow check** (no API calls, no cost): the `dummy` backend answers with a shortest path to a random coloured cell;
`dummy:refuse` mixes in refusals with probability 0.3.

```
python -m maze.loop_session --condition feedback --model dummy:x --max-new-tokens 100 --T 15 --out-dir runs/dummy --sessions s00 s01 s02
python -m maze.loop_session --condition nofeedback --model dummy:refuse --max-new-tokens 100 --T 15 --out-dir runs/dummy --sessions s00 s01 s02
```

**Model runs**: set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. The main-run settings (preregistration V32_100, section 9):

```
python -m maze.loop_session --condition feedback   --model openai:gpt-6.1-sol        --max-new-tokens 4096  --T 15 --effort medium --out-dir runs/main
python -m maze.loop_session --condition feedback   --model anthropic:claude-opus-5-5 --max-new-tokens 12288 --T 15 --effort medium --out-dir runs/main
python -m maze.loop_session --condition feedback   --model anthropic:claude-sonnet-5 --max-new-tokens 61440 --T 15 --effort medium --out-dir runs/main
# baseline: --condition nofeedback (only the 10 sessions marked baseline in the session file are run)
```

The runner stops instead of overwriting an existing result file. Add `--resume` to the same command to continue an
interrupted run. `--cost-cap` (USD) stops the run once the accumulated cost exceeds it.

## Session split

The main run used s00–s99 out of 300 sessions (one per maze); s100–s299 (200 sessions) were held out. **These 200 sessions
are no longer private**: the generators are seeded, so the code here reproduces all 300 sessions. Transparency of the
generation method was preferred over a private split. The files `sessions300.json` and `sessions_heldout.json` are not
committed, but the commands above recreate them under `generated/`. A future private split would be generated with an
unpublished seed.

## Preregistration and post-hoc changes

The documents in [`preregistration/`](preregistration/) are public versions: sentences about budget and approval are removed
and marked "[예산 관련 절은 비공개]" ("budget section not public"). Every change made after seeing results is labelled as such
in the documents. The main ones:

- Adoption of persistent error definition 2 (`results/DEFINITION.md`).
- Increasing the 12×12 sample from 40 to 100 sessions (V32_100, section 1).
- Lifting the refusal stop rule for claude-opus-5 in the 9×9 run after the fact (V30, section 7; that model's numbers are
  marked †).

## Known limitations

- With hidden goals, most attempts go into searching for the goal, so goal search is mixed into the persistent error rate
  (`results/earlier_runs/v30_9x9_VERDICT.md`, section 7).
- Definition 2 leaves out rounds that end without an incorrect attempt, which biases upward the value of models with many
  such rounds (claude-opus-5-5). That model still has the lowest value, so the ordering is not explained by this bias
  (`results/VERDICT_V2.md`, limitations).
- Adaptation success is at ceiling (≥ 0.99) for claude-opus-5-5 and gpt-6.1-sol. Maze size (9×9 → 12×12) did not work as a
  difficulty axis.
- When a run stopped in the middle of a step (claude-opus-5-5 feedback step 14, claude-sonnet-5 feedback steps 21 and 76),
  responses already received in that step were not recorded, so API time and cost are slightly underestimated. Verdicts are
  unaffected (those attempts were asked again after resuming). The runner now records each chunk of sessions immediately.

## Model outputs

`raw/` contains model outputs obtained through the OpenAI and Anthropic APIs. Both providers' commercial terms assign rights
in outputs to the user and do not prohibit publishing them, but both prohibit using outputs to develop models that compete
with them. The data are released for evaluation and analysis; do not use them to train or develop competing models. Each
provider's usage policies also apply.
