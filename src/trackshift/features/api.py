"""Public C8 boundary for pairing and battle-episode extraction."""

from .battles import BattleBuildResult, build_battle_episodes
from .pairing import (
    CLOSE_FOLLOWING_CRITERION,
    CLOSE_FOLLOWING_MAX_DISTANCE_M,
    assign_immediate_ahead_pairs,
    build_session_roster,
)

__all__ = [
    "BattleBuildResult",
    "CLOSE_FOLLOWING_CRITERION",
    "CLOSE_FOLLOWING_MAX_DISTANCE_M",
    "assign_immediate_ahead_pairs",
    "build_session_roster",
    "build_battle_episodes",
]
