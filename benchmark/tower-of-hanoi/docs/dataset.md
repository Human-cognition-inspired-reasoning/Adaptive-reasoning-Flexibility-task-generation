# Datasets

The published [model dataset](../data/benchmark_300.json) contains 300 problems and 1,500 rounds. All model profiles use the same problem IDs and order.

| Problem IDs | Rule family | Problems | Rule variants |
| --- | --- | ---: | ---: |
| P001–P100 | `peg` forbids a directed move between two pegs. | 100 | 12 |
| P101–P200 | `source_sequence` restricts the next source peg based on the previous source peg. | 100 | 12 |
| P201–P300 | `visit` prevents disk 3 from moving until a specified disk has visited a specified peg. | 100 | 8 |

The board has four pegs (A–D) and three disks. In `initial_positions`, entries correspond to disks 1, 2, and 3, smallest first. A stack on a peg is displayed bottom to top, so disk 1 is on top of `[3, 2, 1]`.

Each problem uses five distinct restrictions from one family and each of the five initial configurations once: all disks together, each of the three possible pairs together, and all disks separate. Adjacent rounds have different goals. Within a rule family, variant frequencies differ by at most one across the 500 rounds: 41–42 occurrences for each peg or source-sequence variant and 62–63 for each visit variant.

## Human sample

The [human dataset](../human/problems.json) contains the same IDs and round contents as the corresponding model problems. A fixed seed selected the sample without using model scores or responses. Participants see positions 1–10 in the following order, and the result JSON preserves the IDs.

| Rule family | Problem IDs |
| --- | --- |
| Peg | P017, P035, P041, P050 |
| Source sequence | P152, P154, P160 |
| Visit | P253, P286, P288 |

## Validate or generate problems

Run these commands from `Benchmark/towr-of-hanoi`. Generation and validation run locally with Python's standard library.

```bash
python3 validate_problems.py
python3 validate_problems.py human/problems.json
python3 validate_problems.py --output generated/validation.json
```

The validator independently replays disk moves and searches the state space. In every round it checks the stored shortest solution, the 30-move bound, and a shortest basic solution that violates the current restriction. For rounds 2–5, it also verifies a route from the current board to the current goal that obeys the previous restriction and violates the new one. Dataset checks cover transitions, duplicate IDs or problems equivalent under peg renaming, and agreement between metadata and the problem contents. Frequency balancing applies to datasets that declare it.

To generate candidates from rule templates, choose a seed, rule families, and a count per family (300 problems maximum):

```bash
python3 generate_problems.py --types peg source_sequence visit --per-type 100 --seed 20261029 --output generated/problems.json
python3 validate_problems.py generated/problems.json
```

The published bank includes problems preserved from earlier datasets. Use its fixed JSON for reproducing published runs. To run a new set, set the model profile's `dataset` and `problems` fields to match. The generation and validation witnesses stay with the evaluator.

The candidate generator and its search routines are in [dataset_tools](../dataset_tools/). The [validator](../dataset_tools/validate.py) uses an independent [stack replay and breadth-first search](../hanoi/validation.py).
