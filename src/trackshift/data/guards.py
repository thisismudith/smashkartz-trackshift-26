"""Guards that keep the frozen demo event out of training and calibration.

The demo event is a **held-out test event** (MODELS.md section 7.6, AGENTS.md
section 40). Once a model has trained on it, no claim about held-out performance
at that event is honest any more, and the failure is silent: nothing crashes, the
metrics simply become meaningless. These guards exist to make that failure loud.

Call :func:`assert_demo_held_out` at the top of every training and calibration
script, before any fitting happens.

Scope: what counts as "the demo event"
--------------------------------------
Two readings are defensible and the contract supports both, so this module makes
the choice explicit rather than burying it:

``DemoScope.EVENT_YEAR`` (default)
    Hold out **2026 British Grand Prix only**. Silverstone 2022-2025 stays
    available as DRS-era training data. This matches MODELS.md section 7.6,
    which pins the demo target to ``year: 2026, event: British Grand Prix``, and
    the ``train 2022-2024, validate 2025, test 2026`` design in AGENTS.md
    section 40.

``DemoScope.TRACK``
    Hold out **every British Grand Prix, all seasons**. This is the
    ``leave-one-track-out`` design, also listed in AGENTS.md section 40. It is
    the stricter claim: the model has never seen Silverstone at all, so it
    cannot have memorised the circuit's geometry or its habitual overtaking
    spots. It costs four seasons of training data at that circuit.

Neither is "correct" in the abstract. They support different claims, so pick per
evaluation and say which you used in the validation report. The default is the
narrower one because it matches the pinned demo target; pass
``scope=DemoScope.TRACK`` when the claim being made is track-level
generalisation.

Note on ``splits.py``
---------------------
``trackshift.data.splits.assert_training_or_calibration_eligible`` implements the
``TRACK`` reading unconditionally, matching on event name alone with no year
check. That is a valid choice, but it is a *different* one from this module's
default, and it is not stated at the call site. ``tests/test_guards.py`` pins the
relationship so the two cannot drift apart unnoticed.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Mapping

__all__ = [
    "DEMO_EVENT",
    "DemoScope",
    "DemoEventLeak",
    "assert_demo_held_out",
    "is_demo_row",
    "normalise_event",
]

#: The frozen demo target (MODELS.md section 7.6). One definition, used everywhere.
DEMO_EVENT = {"year": "2026", "event": "British Grand Prix"}


class DemoScope(str, Enum):
    """How much to hold out. See the module docstring."""

    #: 2026 British Grand Prix only. Historical Silverstone remains trainable.
    EVENT_YEAR = "event_year"
    #: Every British Grand Prix, all seasons (leave-one-track-out).
    TRACK = "track"


class DemoEventLeak(RuntimeError):
    """Raised when held-out demo rows reach a training or calibration path.

    A distinct type so callers can catch this specifically; it should never be
    caught to continue, only to report.
    """


def normalise_event(value: Any) -> str:
    """Fold an event name to a comparable form.

    Event names arrive from directory names, Parquet partition keys and config
    files, which disagree on case, underscores and spacing: ``British Grand
    Prix``, ``british_grand_prix`` and ``BRITISH  GRAND PRIX`` are the same
    event. Matching on the raw string would let a leak through on a spelling
    difference, which is the one failure mode this module must not have.

    Matches the normalisation in ``splits.py`` so the two agree.
    """
    return " ".join(str(value or "").replace("_", " ").lower().split())


_DEMO_EVENT_NORMALISED = normalise_event(DEMO_EVENT["event"])


def is_demo_row(row: Mapping[str, Any], scope: DemoScope = DemoScope.EVENT_YEAR) -> bool:
    """True if ``row`` belongs to the held-out demo event under ``scope``."""
    if normalise_event(row.get("event")) != _DEMO_EVENT_NORMALISED:
        return False
    if scope is DemoScope.TRACK:
        return True
    return str(row.get("year", "")).strip() == DEMO_EVENT["year"]


def _iter_rows(data: Any) -> Iterable[Mapping[str, Any]]:
    """Accept a DataFrame or any iterable of mappings.

    pandas is not imported at module scope: this guard is called from scripts
    that may not need pandas, and an import error here would be reported as a
    guard failure, which is misleading.
    """
    if hasattr(data, "to_dict") and hasattr(data, "columns"):  # pandas DataFrame
        missing = {"year", "event"} - set(map(str, data.columns))
        if missing:
            raise ValueError(
                f"cannot check for demo-event leakage: frame is missing {sorted(missing)}. "
                "Both 'year' and 'event' are required; a frame without them cannot be "
                "proven clean, so it is refused rather than passed."
            )
        return data.to_dict("records")
    return data


def assert_demo_held_out(
    data: Any,
    context: str,
    scope: DemoScope = DemoScope.EVENT_YEAR,
) -> None:
    """Raise :class:`DemoEventLeak` if held-out demo rows are present.

    Parameters
    ----------
    data:
        A pandas DataFrame, or any iterable of mappings, carrying ``year`` and
        ``event``.
    context:
        What is being guarded, named well enough to locate in a log, e.g.
        ``"pass model DETECTION training split"``.
    scope:
        See :class:`DemoScope`. Defaults to holding out 2026 British GP only.

    Raises
    ------
    ValueError
        If a DataFrame lacks ``year`` or ``event``. Silence would be worse:
        an unguardable frame must not look guarded.
    DemoEventLeak
        If any row belongs to the held-out event.
    """
    rows = list(_iter_rows(data))
    hits = [r for r in rows if is_demo_row(r, scope)]
    if not hits:
        return

    sample = sorted({f"{r.get('year')} {r.get('event')}" for r in hits})[:3]
    raise DemoEventLeak(
        f"{context}: {len(hits)} of {len(rows)} rows come from the held-out demo event "
        f"under scope={scope.value} (e.g. {', '.join(sample)}). "
        "Training or calibrating on these invalidates every held-out claim about the "
        "demo event. See AGENTS.md section 40 and MODELS.md section 7.6."
    )
