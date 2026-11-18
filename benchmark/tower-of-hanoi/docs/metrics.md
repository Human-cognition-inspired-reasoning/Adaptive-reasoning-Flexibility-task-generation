# Metrics

A **persistent error** is an incorrect submission in rounds 2–5 that still obeys the previous round's additional movement restriction. Replay the whole plan from the **current** initial board. The plan must parse and obey basic Hanoi mechanics. It can count as a persistent error even when it misses the current goal. The previous goal and the participant's previous success do not enter the decision.

For rounds 2–5, `persistent_error = previous_rule_compliant and not current_success`. A submission contributes at most one persistent error. A graded format error or basic move error counts as an incorrect submission and contributes zero persistent errors.

## Aggregation

Let **n** be the number of graded submissions in a problem and round, **E** the number of persistent errors, and **W** the number of incorrect submissions.

| Measure | Rounds | Calculation |
| --- | --- | --- |
| Round persistent error rate (PER) | 2–5 | E / n |
| Mean persistent error (MPE) per problem | 2–5 | Arithmetic mean of its observed round PERs |
| Overall or rule-family MPE | 2–5 | Arithmetic mean of eligible problems' MPEs |
| Round error rate | 1–5 | W / n |
| Mean error rate per problem | 1–5 | Arithmetic mean of its observed round error rates |
| Overall or rule-family error rate | 1–5 | Arithmetic mean of eligible problems' mean error rates |

A round enters a mean as soon as it has one graded submission. This includes in-progress rounds and rounds that exhaust the attempt budget. The denominator is the actual number graded. Skipped rounds and ungraded generation failures contribute neither errors nor attempts. A problem with only round 1 observed contributes to mean error rate. Its MPE is undefined.

JSON stores proportions from 0 to 1. Reports display percentages and the number of eligible problems alongside the planned count. Each model profile plans 100 problems per family (300 total). The human sample plans 4, 3, and 3 (10 total). The pooled submission rate shown in reports weights submissions equally and can differ from MPE, which gives each eligible problem equal weight.

## Time and token usage

Model records include elapsed request time and provider-reported token usage. Usage can be incomplete if a stream ends early. The runner reports token counts; estimating a monetary cost requires the applicable provider prices. Authentication checks do not count as submissions.

For human participants, timing begins when the plan editor opens and ends at submission. It includes idle time in the editor and excludes time on the feedback screen. Only submitting a complete plan contributes to the metrics.
