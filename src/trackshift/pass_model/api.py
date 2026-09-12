"""Public M10 pass-model boundary (Tanveer CP-14).

Consumers import from here, never from the implementation modules directly, so
the internals can move without breaking CP-15 (calibration), CP-16 (ensemble
spread), CP-17 (era handling), CP-23 (ablation) and the CP-24 service routes,
all of which build on this surface.
"""
from .artifacts import (
    ARTIFACT_SCHEMA_VERSION,
    git_commit,
    load_fit_artifact,
    package_versions,
    write_fit_artifact,
)
from .benchmark import (
    FitResult,
    HardwarePlan,
    aggregate,
    fit_cell,
    plan_hardware,
    run_benchmark,
)
from .candidates import (
    CANDIDATES,
    DEFAULT_SEED,
    FAMILIES,
    Candidate,
    PassModel,
    available_families,
    configure_threads,
)
from .features import (
    FEATURE_SCHEMA_VERSION,
    LABEL_COLUMN,
    STRUCTURAL_COLUMNS,
    FeatureSelection,
    FeatureSelectionError,
    build_matrix,
    select_features,
)
from .folds import (
    DESIGNS,
    Fold,
    SplitPlan,
    SplitPlanError,
    assert_disjoint,
    plan_splits,
    resolve_unit,
    summarise,
)
from .metrics import (
    SELECTION_RULE,
    evaluate,
    expected_calibration_error,
    rank_results,
    reliability_table,
)
from .report import check_gates, identity_comparison, render_report

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "CANDIDATES",
    "DEFAULT_SEED",
    "DESIGNS",
    "FAMILIES",
    "FEATURE_SCHEMA_VERSION",
    "LABEL_COLUMN",
    "SELECTION_RULE",
    "STRUCTURAL_COLUMNS",
    "Candidate",
    "FeatureSelection",
    "FeatureSelectionError",
    "FitResult",
    "Fold",
    "HardwarePlan",
    "PassModel",
    "SplitPlan",
    "SplitPlanError",
    "aggregate",
    "assert_disjoint",
    "available_families",
    "build_matrix",
    "check_gates",
    "configure_threads",
    "evaluate",
    "expected_calibration_error",
    "fit_cell",
    "identity_comparison",
    "git_commit",
    "load_fit_artifact",
    "package_versions",
    "plan_hardware",
    "plan_splits",
    "rank_results",
    "reliability_table",
    "render_report",
    "resolve_unit",
    "run_benchmark",
    "select_features",
    "summarise",
    "write_fit_artifact",
]
