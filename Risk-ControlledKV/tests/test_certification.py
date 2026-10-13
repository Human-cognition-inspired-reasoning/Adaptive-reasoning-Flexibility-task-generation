import itertools
import unittest

import numpy as np

from riskkv.certification import Contract, degradation_metrics, envelope_p_value, fixed_sequence, max_events


class CertificationTests(unittest.TestCase):
    def test_finite_sample_cutoffs(self):
        self.assertEqual(max_events(1200), 47)
        self.assertEqual(max_events(1300), 51)
        self.assertLessEqual(envelope_p_value(47, 1200, .05), .05)
        self.assertGreater(envelope_p_value(48, 1200, .05), .05)

    def test_envelope_valid_under_heterogeneous_bernoulli_null(self):
        # Enumerate heterogeneous nulls and their Poisson-binomial distributions.
        # This checks actual test size, rather than duplicating the p-value formula.
        n, epsilon = 5, .4
        for probs in itertools.product([0., .2, .4, .8, 1.], repeat=n):
            if sum(probs)/n < epsilon - 1e-12:
                continue
            distribution = np.array([1.])
            for p in probs:
                distribution = np.convolve(distribution, [1-p, p])
            for alpha in [.05, .1, .2, .5]:
                size = sum(distribution[k] for k in range(n+1)
                           if envelope_p_value(k, n, epsilon) <= alpha)
                self.assertLessEqual(size, alpha+1e-12)

    def test_stop_at_first_failure_even_with_later_success(self):
        candidates = [dict(policy="A", n=1200, K=42, retained_ratio=.8),
                      dict(policy="B", n=1200, K=48, retained_ratio=.75),
                      dict(policy="C", n=1200, K=0, retained_ratio=.7)]
        tested, selected = fixed_sequence(candidates, Contract())
        self.assertEqual(len(tested), 2)
        self.assertEqual(selected["policy"], "A")
        candidates[0]["K"] = 80
        _, selected = fixed_sequence(candidates, Contract())
        self.assertEqual(selected["policy"], "FULL")
        self.assertEqual(selected["empirical_risk"], 0)

    def test_test_scores_cannot_enter_cal_selection(self):
        with self.assertRaises(ValueError):
            fixed_sequence([dict(policy="A", split="TEST", n=1200, K=0)], Contract())

    def test_strict_threshold_and_improvements(self):
        metrics = degradation_metrics([.5, .6, 0.], [.25, .75, 0.], tau=.25)
        self.assertEqual(metrics["K"], 0)  # Exactly tau is not an event.
        self.assertEqual(degradation_metrics([.8, .3], [.5, .7])["K"], 1)


if __name__ == "__main__":
    unittest.main()

