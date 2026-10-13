"""Fixed-sequence LTT for paired utility degradation under independent strata."""
from dataclasses import asdict, dataclass
import math

import numpy as np
from scipy.stats import binom


@dataclass(frozen=True)
class Contract:
    tau: float = 0.10
    epsilon: float = 0.05
    delta: float = 0.05

    def __post_init__(self):
        for name, value in asdict(self).items():
            if not math.isfinite(value) or not 0 < value < 1:
                raise ValueError(f"{name} must be in (0, 1)")


def envelope_p_value(k: int, n: int, epsilon: float) -> float:
    """Valid under independent balanced strata, not necessarily iid Bernoullis.

    The Hoeffding binomial comparison applies in the restricted lower tail.
    Outside that range the envelope p-value is 1, not the raw binomial CDF.
    """
    if not isinstance(k, (int, np.integer)) or not isinstance(n, (int, np.integer)):
        raise ValueError("k and n must be integers")
    if n <= 0 or not 0 <= k <= n or not 0 < epsilon < 1:
        raise ValueError("Invalid event count, sample size, or risk target")
    return float(binom.cdf(k, n, epsilon)) if k <= math.floor(n * epsilon) - 1 else 1.0


def max_events(n: int, epsilon: float = .05, delta: float = .05) -> int:
    """Largest certifiable count; -1 means even zero events cannot be certified."""
    if not 0 < delta < 1:
        raise ValueError("delta must be in (0, 1)")
    values = [k for k in range(n + 1) if envelope_p_value(k, n, epsilon) <= delta]
    return max(values, default=-1)


def degradation_metrics(full, selected, tau: float = .10) -> dict:
    full, selected = np.asarray(full, dtype=float), np.asarray(selected, dtype=float)
    if full.ndim != 1 or selected.shape != full.shape or not len(full):
        raise ValueError("Expected equally sized nonempty 1D paired utilities")
    if (not np.isfinite(full).all() or not np.isfinite(selected).all()
            or (full < 0).any() or (full > 1).any()
            or (selected < 0).any() or (selected > 1).any()):
        raise ValueError("Utilities must be finite values in [0, 1]")
    loss = full - selected
    count = int(np.count_nonzero(loss > tau))
    return {"n": len(full), "K": count, "empirical_risk": count / len(full),
            "mean_full_utility": float(full.mean()), "mean_selected_utility": float(selected.mean()),
            "mean_drop": float(loss.mean())}


def fixed_sequence(candidates: list[dict], contract: Contract) -> tuple[list[dict], dict]:
    """Candidates arrive in the predeclared conservative-to-aggressive order.

    CAL scores for later candidates may exist for descriptive analysis. They
    never enter selection after the first failed test.
    """
    tested, selected = [], {"policy": "FULL", "retained_ratio": 1.0, "K": 0, "empirical_risk": 0.0}
    n_expected = None
    for candidate in candidates:
        if candidate.get("split", "CAL") != "CAL":
            raise ValueError("Fixed-sequence selection accepts CAL observations only")
        row = dict(candidate)
        n, k = row["n"], row["K"]
        if n_expected is not None and n != n_expected:
            raise ValueError("Candidate calibration sample sizes differ")
        n_expected = n
        p = envelope_p_value(k, n, contract.epsilon)
        row.update(p_value=p, status="certified" if p <= contract.delta else "first_noncertified")
        tested.append(row)
        if p > contract.delta:
            break
        selected = dict(row)
    closed = bool(tested) and (tested[-1]["status"] == "first_noncertified" or len(tested) == len(candidates))
    selected.update(asdict(contract), sequence_closed=closed)
    return tested, selected

