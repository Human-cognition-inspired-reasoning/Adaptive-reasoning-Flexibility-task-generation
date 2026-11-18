# Experiment protocol

Each model solves the fixed [300-problem dataset](dataset.md) in ID order. Every problem uses three disks, four pegs (A–D), and one family of movement restrictions. Across five rounds, the initial board, specific restriction, and accepted goal peg change. Every attempt starts from that round's initial board with the move history reset.

| Setting | Default |
| --- | --- |
| Model profiles | Sol, Opus, Sonnet, all at medium reasoning effort |
| Submission | One JSON object with a `moves` array of 1–30 peg pairs |
| Maximum attempts per round (T) | 10 |
| Consecutive correct submissions for early completion (k) | 2, including round 1 |
| Output limit | 25,000 tokens per response |
| Tools | None |

Set integer values satisfying `1 <= k <= T` in the chosen profile. When `r1_apply_k` is false, a single correct submission passes round 1. Reaching k consecutive correct submissions passes the round early. On attempt T, a correct submission passes even if the streak is below k. An incorrect final submission ends the problem, skips its remaining rounds, and starts the next problem with a fresh conversation. Passing R5 completes the problem.

## Grading and feedback

The model sees the basic Hanoi rules, the current board, its previous submissions in this problem, and feedback. It infers the accepted destination and movement restrictions. Round numbers, transitions, T, k, failure locations, and persistent-error labels are hidden. The model submits a whole plan, even if it returns to the initial position. The grader checks the final board after the last move.

| Feedback | Meaning |
| --- | --- |
| Correct | The plan obeys all rules and ends at the accepted goal. |
| Incorrect | The plan fails and no additional movement violation was detected. Malformed submissions also receive this label. |
| Rule violation | The plan violates the additional restriction and reaching the goal was not established. |
| Correct, but rule violation | The plan reaches the goal under basic Hanoi mechanics but violates the additional restriction. |

Only **Correct** increments the streak and can pass the round on attempt T. The other three labels reset the streak and do not pass the round, including **Correct, but rule violation**. For diagnosis, the grader replays a move that violates an additional restriction if basic mechanics permit it. Replay stops at the first basic error and records any additional restriction detectable at that step. See the [metric definitions](metrics.md) for the comparison with the previous round.

## Human comparison

The [offline human assessment](../human/Hanoi_Human_Evaluation.html) uses the same 4/3/3 sample described in the [dataset](dataset.md), with the same T, k, feedback categories, grading, and aggregation. Instructions are in Korean. Participants click pegs to build plans. The interface blocks moves that violate basic mechanics before submission. Models enter JSON directly. Both groups can review earlier submissions within the current problem.

## Checkpoints and recovery

Each run freezes the settings, dataset identity and snapshot, problem order, prompt, grading policy, and round completion policy in `protocol.json`. Resume with the same code, dataset path, and settings. Integrity checks reject a checkpoint whose frozen protocol or recorded responses no longer match. A saved run must be resumed with the checkout and stopping policy recorded for that run.

A completed response is graded from the saved provider response. The runner restores the conversation, provider-native reasoning items, and success streak for the same problem. It does not request a separate reasoning summary. Reasoning state resets at the next problem.

If a transport attempt started but has no saved response, an explicit resume permits one new request for that submission. Both transport attempts remain in the log. The provider may bill both if it processed the first request. A saved generation failure, such as hitting an output limit, is ungraded and ends that problem. The next problem starts with an empty history. `--check-only` verifies model access, and `--dry-run` previews a new run offline.
