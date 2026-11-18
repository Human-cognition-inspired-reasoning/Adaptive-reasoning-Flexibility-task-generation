# Tower of Hanoi

How does a solver adapt when a move that worked in one round stops working in the next? In this four-peg Tower of Hanoi task, the initial board, hidden movement restriction, and hidden goal peg change between rounds. The benchmark measures both overall error and the tendency to keep following the previous restriction.

The model experiment uses 300 fixed problems. The Korean-language [human assessment](human/Hanoi_Human_Evaluation.html) uses 10 of the same problems. Download the HTML and open it locally to participate.

## Run a model

From the repository root, enter the task directory. Use Python 3.11 or later.

```bash
cd benchmark/tower-of-hanoi
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Preview the first request, including the exact prompt and initial board, without an API key:

```bash
python run.py --profile sol --dry-run
```

For a live run, register the provider key in your current Bash or Zsh session. Choose one profile:

| Profile | Set the API key | Start the run |
| --- | --- | --- |
| GPT-6.1 Sol | `source ./keys.sh openai` | `python run.py --profile sol --output runs/sol_300` |
| Claude Opus 5.5 | `source ./keys.sh anthropic` | `python run.py --profile opus --output runs/opus_300` |
| Claude Sonnet 5.5 | `source ./keys.sh anthropic` | `python run.py --profile sonnet --output runs/sonnet_300` |

The [model profiles](configs/) select the model, dataset, maximum attempts per round (T), and early-completion streak (k). A round passes after k consecutive correct submissions or a correct submission on attempt T. An incorrect final submission ends the problem. To continue a run or inspect results:

```bash
python run.py --resume runs/sol_300
python view_logs.py
```

## Reproduce the benchmark

- [Dataset](docs/dataset.md) — validate the fixed problems or generate a candidate set locally.
- [Experiment protocol](docs/experiment.md) — round transitions, participant feedback, and checkpoint recovery.
- [Metrics](docs/metrics.md) — which attempts count toward persistent error and how rounds and problems are weighted.
- [Human assessment guide (Korean)](human/guide.ko.md) — administer the offline task, save progress, and collect results. Run `python build_human.py` from this directory to rebuild the participant HTML.

To run the offline regression tests from this directory:

```bash
python -m unittest discover -v
```
