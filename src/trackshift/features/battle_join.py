"""Causal C8 ``battle_id`` resolution for M07 opportunities (A2).

CP-14 splits on ``battle_id`` so that one battle -- the same pair of cars over
the same few laps -- cannot appear in both training and test. That requires
every trainable opportunity to carry the C8 episode it belongs to.

Two things make the join less trivial than it looks:

**Identifier mismatch.** C8 names both cars by driver code (``VER``). M07 knows
its attacker by code but its defender only by car number, because
``driver_ahead_number`` is what the telemetry reports. The number-to-code map is
per (year, event, session) -- numbers are reused across seasons -- and is built
from the lake, which carries both.

**Causality, and why the lap alone is not enough.** C8 emits mostly single-lap
episodes and routinely emits *several* for one pair on one lap -- a pair that
closes, drops back and closes again scores two episodes. Matching on the lap
alone leaves 28% of 2026 opportunities ambiguous. The disambiguator is distance:
``battle_segment_rows.jsonl`` carries each episode's segment extent, and on the
real 2026 data every one of the 5,745 multi-episode (pair, lap) cells has
*disjoint* distance spans. So an opportunity anchored at its Detection Line
distance resolves to exactly one episode, exactly, with no grouping and no
guessing.

Anything less certain is refused. A guessed ``battle_id`` is worse than a null
one: it would silently place two unrelated opportunities in the same split
group, which is the leak CP-14's split exists to prevent.

So this module never invents an identifier. Every opportunity resolves to
exactly one episode or to a recorded reason why it did not, and the caller
decides what to do with the unresolved ones. Section 24: a join you cannot make
is evidence, not an inconvenience to paper over.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "JOIN_SCHEMA_VERSION",
    "BattleEpisode",
    "BattleIndex",
    "BattleJoinError",
    "JoinStatus",
    "load_episodes",
    "load_lap_spans",
    "build_number_to_code",
]

JOIN_SCHEMA_VERSION = "a2_battle_join_v1"


class BattleJoinError(ValueError):
    """The battle join cannot be performed as asked."""


class JoinStatus:
    """Why one opportunity did or did not receive a ``battle_id``.

    These are recorded per row rather than aggregated away, because the three
    failures have different causes and different fixes.
    """

    JOINED = "JOINED"
    #: The defender's car number has no code in this session's lake partition.
    NO_DEFENDER_CODE = "NO_DEFENDER_CODE"
    #: The pair exists in C8 but no episode's lap range contains this lap.
    NO_EPISODE_FOR_LAP = "NO_EPISODE_FOR_LAP"
    #: C8 has no episode for this attacker/defender pair in this session at all.
    NO_EPISODE_FOR_PAIR = "NO_EPISODE_FOR_PAIR"
    #: More than one episode claims the lap and distance could not separate
    #: them -- either no span was supplied or the spans genuinely overlap.
    AMBIGUOUS_EPISODE = "AMBIGUOUS_EPISODE"

    ALL = (JOINED, NO_DEFENDER_CODE, NO_EPISODE_FOR_LAP,
           NO_EPISODE_FOR_PAIR, AMBIGUOUS_EPISODE)


@dataclass(frozen=True)
class BattleEpisode:
    """One C8 episode, reduced to what the join needs."""

    battle_id: str
    year: str
    event: str
    session: str
    attacker: str
    defender: str
    start_lap: int
    end_lap: int
    #: lap -> (first entry distance, last exit distance) from C8's segment rows.
    #: Empty when the segment rows were not supplied, which downgrades a
    #: multi-episode lap from resolvable to ambiguous rather than to a guess.
    lap_spans: Mapping[int, tuple[float, float]] = field(default_factory=dict)

    def contains(self, lap: int) -> bool:
        return self.start_lap <= lap <= self.end_lap

    def covers(self, lap: int, distance_m: float) -> bool:
        """True when this episode's extent on ``lap`` contains ``distance_m``."""
        span = self.lap_spans.get(lap)
        if span is None:
            return False
        return span[0] <= distance_m <= span[1]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _canonical_number(value: Any) -> str | None:
    """Car number as a bare integer string, or None when absent or not a number."""
    if value is None:
        return None
    text = _text(value)
    if not text or text.lower() in ("nan", "none", "<na>"):
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    if number != number:  # NaN
        return None
    return str(int(number))


def _lap(value: Any, field: str, battle_id: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BattleJoinError(
            f"episode {battle_id!r} has a non-integer {field}: {value!r}") from exc


def load_lap_spans(path: Path) -> dict[str, dict[int, tuple[float, float]]]:
    """``battle_id -> {lap: (entry, exit)}`` from C8's segment rows.

    Rows without both distances are skipped rather than refused: a single
    segment missing its extent narrows one episode's span, it does not
    invalidate the episode. A battle whose every row lacks distances simply ends
    up with no span and stays ambiguous on a shared lap, which is the honest
    outcome.
    """
    path = Path(path)
    if not path.exists():
        raise BattleJoinError(
            f"no C8 battle segment rows at {path}. They carry the within-lap extent "
            "that separates two episodes of the same pair on one lap; without them "
            "those opportunities cannot be resolved. Build them with\n"
            "  python scripts/features/build_battles.py")

    spans: dict[str, dict[int, tuple[float, float]]] = {}
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BattleJoinError(f"{path}:{number} is not valid JSON: {exc}") from exc
            battle_id = _text(raw.get("battle_id"))
            entry, exit_ = raw.get("entry_distance_m"), raw.get("end_distance_m")
            if not battle_id or entry is None or exit_ is None:
                continue
            try:
                lap = int(raw["lap"])
                low, high = float(entry), float(exit_)
            except (KeyError, TypeError, ValueError):
                continue
            if low != low or high != high:  # NaN
                continue
            if high < low:
                low, high = high, low
            bucket = spans.setdefault(battle_id, {})
            existing = bucket.get(lap)
            bucket[lap] = (low, high) if existing is None else (
                min(existing[0], low), max(existing[1], high))
    return spans


def load_episodes(path: Path, segment_rows: Path | None = None) -> list[BattleEpisode]:
    """Read C8 episodes from the JSONL C8 writes.

    Rows missing any join key are refused rather than skipped: a C8 file with
    half its episodes unusable is a C8 problem, and silently dropping them would
    show up downstream as an unexplained coverage hole.
    """
    path = Path(path)
    if not path.exists():
        raise BattleJoinError(
            f"no C8 battle episodes at {path}. Build them first:\n"
            "  python scripts/features/build_battles.py")

    spans = load_lap_spans(segment_rows) if segment_rows is not None else {}

    episodes: list[BattleEpisode] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BattleJoinError(f"{path}:{number} is not valid JSON: {exc}") from exc
            battle_id = _text(raw.get("battle_id"))
            if not battle_id:
                raise BattleJoinError(f"{path}:{number} has no battle_id")
            missing = [k for k in ("year", "event", "session", "attacker", "defender",
                                   "start_lap", "end_lap") if raw.get(k) is None]
            if missing:
                raise BattleJoinError(
                    f"{path}:{number} ({battle_id}) is missing {missing}; C8 must emit "
                    "every join key or the causal join cannot be verified")
            episodes.append(BattleEpisode(
                battle_id=battle_id,
                year=_text(raw["year"]),
                event=_text(raw["event"]),
                session=_text(raw["session"]),
                attacker=_text(raw["attacker"]),
                defender=_text(raw["defender"]),
                start_lap=_lap(raw["start_lap"], "start_lap", battle_id),
                end_lap=_lap(raw["end_lap"], "end_lap", battle_id),
                lap_spans=spans.get(battle_id, {}),
            ))
    return episodes


def build_number_to_code(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, str, str], str]:
    """Map ``(year, event, session, car_number) -> driver code`` from lake rows.

    Keyed per session because car numbers are reused between seasons, and a
    cross-season map would join an opportunity to another year's driver. A
    number that maps to two different codes inside one session is a corrupt
    partition, so it raises rather than picking one.
    """
    mapping: dict[tuple[str, str, str, str], str] = {}
    for row in rows:
        code = _text(row.get("driver"))
        if not code:
            continue
        # Numbers arrive as "44", "44.0", 44 or NaN depending on the writer. A
        # missing number is skipped, never stringified: float("nan") renders as
        # "nan", which is truthy and would collide every unnumbered row of a
        # session onto one key and read as two drivers sharing a number.
        number = _canonical_number(row.get("driver_number"))
        if number is None:
            continue
        key = (_text(row.get("year")), _text(row.get("event")),
               _text(row.get("session")), number)
        existing = mapping.get(key)
        if existing is not None and existing != code:
            raise BattleJoinError(
                f"car number {number} maps to both {existing!r} and {code!r} in "
                f"{key[:3]}; the lake partition is inconsistent and the battle join "
                "would attach opportunities to the wrong driver")
        mapping[key] = code
    return mapping


class BattleIndex:
    """Lap-range lookup from (session, attacker, defender) to a C8 episode.

    Episodes for a pair are held sorted by ``start_lap`` so a lookup is a binary
    search rather than a scan; at ~34k episodes a linear scan per opportunity is
    the difference between seconds and minutes.
    """

    def __init__(self, episodes: Iterable[BattleEpisode]):
        self._by_pair: dict[tuple[str, str, str, str, str], list[BattleEpisode]] = {}
        for episode in episodes:
            key = (episode.year, episode.event, episode.session,
                   episode.attacker, episode.defender)
            self._by_pair.setdefault(key, []).append(episode)
        for bucket in self._by_pair.values():
            bucket.sort(key=lambda e: (e.start_lap, e.end_lap))
        self._starts = {key: [e.start_lap for e in bucket]
                        for key, bucket in self._by_pair.items()}

    def __len__(self) -> int:
        return sum(len(bucket) for bucket in self._by_pair.values())

    @property
    def pairs(self) -> int:
        return len(self._by_pair)

    def resolve(self, *, year: Any, event: Any, session: Any, attacker: Any,
                defender_code: Any, lap: Any,
                distance_m: Any = None) -> tuple[str | None, str]:
        """Return ``(battle_id, status)`` for one opportunity.

        ``battle_id`` is ``None`` for every status other than ``JOINED``. The
        caller must not substitute a placeholder: a null here means the split
        cannot use the row, which is a true statement about the data.
        """
        if defender_code is None or not _text(defender_code):
            return None, JoinStatus.NO_DEFENDER_CODE

        key = (_text(year), _text(event), _text(session),
               _text(attacker), _text(defender_code))
        bucket = self._by_pair.get(key)
        if not bucket:
            return None, JoinStatus.NO_EPISODE_FOR_PAIR

        try:
            lap_number = int(lap)
        except (TypeError, ValueError):
            return None, JoinStatus.NO_EPISODE_FOR_LAP

        # Every episode starting at or before this lap is a candidate; the ones
        # that also end at or after it are matches. Episodes for a pair may
        # overlap in principle, so collect rather than take the first.
        cut = bisect_right(self._starts[key], lap_number)
        matches = [e for e in bucket[:cut] if e.contains(lap_number)]
        if not matches:
            return None, JoinStatus.NO_EPISODE_FOR_LAP
        if len(matches) == 1:
            return matches[0].battle_id, JoinStatus.JOINED

        # Several episodes for this pair on this lap -- the common case, because
        # C8 closes an episode when the pair separates and opens a new one when
        # it re-forms. Distance separates them: on the 2026 data every such cell
        # has disjoint spans. Without a distance there is nothing to separate
        # them with, so the answer is ambiguous rather than the first match.
        try:
            position = float(distance_m)
        except (TypeError, ValueError):
            return None, JoinStatus.AMBIGUOUS_EPISODE
        if position != position:  # NaN
            return None, JoinStatus.AMBIGUOUS_EPISODE

        covering = [e for e in matches if e.covers(lap_number, position)]
        if len(covering) == 1:
            return covering[0].battle_id, JoinStatus.JOINED
        if not covering:
            # The pair was in a battle on this lap, but not where this
            # opportunity sits. That is a real "no episode here", not ambiguity.
            return None, JoinStatus.NO_EPISODE_FOR_LAP
        return None, JoinStatus.AMBIGUOUS_EPISODE
