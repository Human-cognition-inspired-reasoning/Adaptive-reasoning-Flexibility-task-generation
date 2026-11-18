"""Names used by model configs and result files."""

BINARY = "binary"
FOUR_WAY = "four_way"
PREVIOUS_RULE = "previous_rule_compliant_current_failure"
CURRENT_VIOLATION = "current_rule_violation"
ROUND_MPE = "mean_round_per"
ROUND_ERROR = "mean_round_error_r1_r5"

# Published model configs and logs contain these exact identifiers. Map only
# known policies: a future schema may give a similar name a different meaning.
_LEGACY_NAMES = {
    "binary_v1": BINARY,
    "goal_rule_4way_v1": FOUR_WAY,
    "previous_rule_compliant_current_failure_v1": PREVIOUS_RULE,
    "any_current_movement_restriction_violation_v1": CURRENT_VIOLATION,
    "round_per_then_problem_mpe_then_total_mpe_v1": ROUND_MPE,
    "round_error_rate_r1_r5_then_problem_mean_then_total_mean_v2": ROUND_ERROR,
}


def policy_name(value):
    """Resolve a known historical name; leave unknown values for the caller to validate."""
    return _LEGACY_NAMES.get(value, value) if isinstance(value, str) else value
