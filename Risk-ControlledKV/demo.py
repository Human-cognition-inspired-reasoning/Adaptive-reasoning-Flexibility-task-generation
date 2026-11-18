"""Runnable synthetic demonstration; these are not benchmark or paper results."""
import json

import numpy as np

from riskkv import Contract, degradation_metrics, fixed_sequence, max_events


def synthetic_utilities(rng, n=600):
    full = rng.uniform(.60, .95, size=n)
    # The same latent request difficulty is shared across policies. Candidate
    # evaluations can be dependent; independent CAL requests are still required.
    difficulty = rng.random(n)
    policies = {}
    for ratio, event_probability in [(.80, .005), (.60, .02), (.40, .08)]:
        loss = np.where(difficulty < event_probability, .20, .02)
        policies[f"RETAIN_{ratio:.2f}"] = (ratio, full - loss)
    return full, policies


def main():
    contract = Contract(tau=.10, epsilon=.05, delta=.05)
    rng = np.random.default_rng(42)
    # Declare the conservative-to-aggressive sequence before inspecting CAL.
    cal_full, cal_policies = synthetic_utilities(rng)
    candidates = []
    for name, (ratio, utility) in cal_policies.items():
        candidates.append(dict(policy=name, split="CAL", retained_ratio=ratio,
                               **degradation_metrics(cal_full, utility, contract.tau)))

    tested, selected = fixed_sequence(candidates, contract)
    # Obtain an independent TEST sample only after fixing the CAL choice.
    test_full, test_policies = synthetic_utilities(rng)
    test_selected = test_full if selected["policy"] == "FULL" else test_policies[selected["policy"]][1]
    test_metrics = degradation_metrics(test_full, test_selected, contract.tau)

    print("Synthetic demonstration — not benchmark results")
    print(f"Contract: tau={contract.tau}, epsilon={contract.epsilon}, delta={contract.delta}")
    print(f"Maximum certifiable CAL events: {max_events(len(cal_full), contract.epsilon, contract.delta)}")
    for row in tested:
        print(f"CAL {row['policy']}: {row['K']}/{row['n']} events, "
              f"p={row['p_value']:.6g}, {row['status']}")
    print(f"Selected policy: {selected['policy']}")
    print("Held-out TEST (descriptive; does not modify the selected policy):")
    print(json.dumps(test_metrics, indent=2))


if __name__ == "__main__":
    main()

