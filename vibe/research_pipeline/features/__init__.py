"""Feature declarations and the leakage harness (P5).

Two responsibilities:

``registry``
    Every feature the research path can compute is declared as causal or
    diagnostic, with its lookback and lookahead. Only causal features may enter
    signal, filter, sizing, execution, or parameter-selection logic.

``leakage``
    The automated checks from section 9 of the plan, executed against the
    research path rather than the engine alone.
"""

from vibe.research_pipeline.features.registry import (
    FEATURE_REGISTRY,
    SWEEP_PRECOMPUTED_FEATURES,
    FeatureNotDeclaredError,
    LeakyFeatureInDecisionError,
    assert_decision_features_are_causal,
    causal_feature_names,
    declaration_for,
    diagnostic_feature_names,
    registry_hash,
)
from vibe.research_pipeline.features.leakage import (
    LeakageFinding,
    LeakageReport,
    LeakageViolation,
    audit_feature_availability,
    check_future_perturbation,
    check_orb_boundary,
    check_prefix_invariance,
    check_split_contamination,
    check_truncation_equivalence,
    run_leakage_suite,
)

__all__ = [
    "FEATURE_REGISTRY",
    "SWEEP_PRECOMPUTED_FEATURES",
    "FeatureNotDeclaredError",
    "LeakyFeatureInDecisionError",
    "assert_decision_features_are_causal",
    "causal_feature_names",
    "declaration_for",
    "diagnostic_feature_names",
    "registry_hash",
    "LeakageFinding",
    "LeakageReport",
    "LeakageViolation",
    "audit_feature_availability",
    "check_future_perturbation",
    "check_orb_boundary",
    "check_prefix_invariance",
    "check_split_contamination",
    "check_truncation_equivalence",
    "run_leakage_suite",
]
