"""Public C8 boundary for pairing, battle episodes, and M06 pairwise rows."""

from .battles import BattleBuildResult, build_battle_episodes
from .pairing import (
    CLOSE_FOLLOWING_CRITERION,
    CLOSE_FOLLOWING_MAX_DISTANCE_M,
    assign_immediate_ahead_pairs,
    build_session_roster,
)
from .pairwise import (
    PAIRWISE_FEATURE_SCHEMA,
    PAIRWISE_FEATURE_SCHEMA_VERSION,
    PairwiseBuildResult,
    build_pairwise_features,
)
from .tyre_pace import (
    TYRE_PACE_PROVENANCE,
    TYRE_PACE_SCHEMA_VERSION,
    TyrePaceConfig,
    TyrePaceError,
    build_tyre_pace_overlay,
    load_tyre_pace_config,
)

__all__ = [
    "BattleBuildResult",
    "CLOSE_FOLLOWING_CRITERION",
    "CLOSE_FOLLOWING_MAX_DISTANCE_M",
    "assign_immediate_ahead_pairs",
    "build_session_roster",
    "build_battle_episodes",
    "PAIRWISE_FEATURE_SCHEMA",
    "PAIRWISE_FEATURE_SCHEMA_VERSION",
    "PairwiseBuildResult",
    "build_pairwise_features",
    "TYRE_PACE_PROVENANCE",
    "TYRE_PACE_SCHEMA_VERSION",
    "TyrePaceConfig",
    "TyrePaceError",
    "build_tyre_pace_overlay",
    "load_tyre_pace_config",
]
