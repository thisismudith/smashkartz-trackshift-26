"""Feature-group ablation harness (M28, CP-23).

Section 35 says a feature group is kept only if it earns its place. That is a
claim about held-out metrics, so it needs measuring, and measuring it the same
way for both owners is the reason this lives in one module rather than in two
report scripts.

Three things the naive version gets wrong, and this one does not:

**A point estimate is not a result.** Fold-to-fold and seed-to-seed variation on
this dataset is comparable to the deltas being measured. A group reported as
"-0.0012 Brier" with no interval cannot be distinguished from noise, and section
35 decisions have to survive noise. Every delta here is measured over repeated
seeds and every fold, and reported with an interval and against the run-to-run
noise floor.

**Leave-one-out alone cannot tell redundant from useless.** Two correlated
groups each look worthless under leave-one-out, because removing either leaves
the signal in the other. So add-one-in against a minimal baseline is run
alongside: a group that is redundant (useless alone, useless removed) is a
different finding from one that is genuinely uninformative, and they call for
different decisions.

**A group's value differs by checkpoint.** Tyre state may matter at BRAKING and
not at DETECTION. Decisions are therefore reported per checkpoint and never
averaged across them.

The harness reads its groups from ``config/feature_registry.yaml`` rather than a
hard-coded list, so a group added to the registry becomes ablatable without
touching this file.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "ABLATION_SCHEMA_VERSION",
    "DEFAULT_MINIMAL",
    "DEFAULT_SEEDS",
    "PRIMARY_METRIC",
    "AblationError",
    "AblationResult",
    "GroupVerdict",
    "build_plans",
    "noise_floor",
    "summarise_group",
    "verdict_for",
]

ABLATION_SCHEMA_VERSION = "m28_ablation_v1"

#: Section 26 selects on calibration; Brier is the primary metric, lower better.
PRIMARY_METRIC = "brier"

#: Three seeds is the documented minimum (CP-23). Fixed rather than random so a
#: rerun reproduces the intervals exactly.
DEFAULT_SEEDS: tuple[int, ...] = (42, 43, 44)

#: The add-one-in floor: the columns every add-one-in configuration keeps.
#:
#: Not empty. A floor of nothing makes the minimal baseline a model with zero
#: features, which cannot be fitted at all, and then every add-one-in delta is
#: unmeasurable. CP-14 names the gap as the feature that should carry real
#: signal on its own, so it is the natural floor: "what does this group add over
#: knowing the gap?" is the question section 35 actually wants answered.
DEFAULT_MINIMAL: tuple[str, ...] = ("gap_at_checkpoint",)

#: Identity groups are scrutinised hardest (section 17): a large identity gain
#: beside a small racecraft gain is memorisation, not skill.
IDENTITY_GROUPS: frozenset[str] = frozenset({"identity", "team_identity", "driver_identity"})


class AblationError(ValueError):
    """The ablation cannot be run as asked."""


@dataclass(frozen=True)
class AblationResult:
    """One fitted cell under one ablation configuration."""

    checkpoint: str
    family: str
    fold: str
    seed: int
    mode: str            # "baseline" | "leave_one_out" | "add_one_in" | "minimal"
    group: str | None    # None for the baselines
    metric: float | None
    n_test: int | None = None
    n_features: int | None = None
    ok: bool = True
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint, "family": self.family, "fold": self.fold,
            "seed": self.seed, "mode": self.mode, "group": self.group,
            "metric": self.metric, "n_test": self.n_test,
            "n_features": self.n_features, "ok": self.ok, "error": self.error,
        }


@dataclass
class GroupVerdict:
    """What the numbers say about one group at one checkpoint."""

    checkpoint: str
    group: str
    n_features: int
    leave_one_out_delta: float | None = None
    leave_one_out_interval: tuple[float, float] | None = None
    add_one_in_delta: float | None = None
    add_one_in_interval: tuple[float, float] | None = None
    noise_floor: float | None = None
    verdict: str = "UNMEASURED"
    rationale: str = ""
    is_identity: bool = False
    samples: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint, "group": self.group,
            "n_features": self.n_features,
            "leave_one_out_delta": self.leave_one_out_delta,
            "leave_one_out_interval": list(self.leave_one_out_interval)
            if self.leave_one_out_interval else None,
            "add_one_in_delta": self.add_one_in_delta,
            "add_one_in_interval": list(self.add_one_in_interval)
            if self.add_one_in_interval else None,
            "noise_floor": self.noise_floor,
            "verdict": self.verdict, "rationale": self.rationale,
            "is_identity": self.is_identity, "samples": self.samples,
        }


def build_plans(groups: Mapping[str, Sequence[str]], *, all_columns: Sequence[str],
                minimal: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Every configuration to fit: baselines, leave-one-out, add-one-in.

    ``minimal`` is the add-one-in floor -- the columns every add-one-in run
    keeps, so each measures what its group adds *over that floor*. It must not
    be empty: a floor of nothing is a zero-feature model, which cannot be fitted,
    and every add-one-in delta then comes back unmeasurable.

    A configuration that would leave no columns at all is omitted rather than
    emitted as a cell that is certain to fail.
    """
    columns = list(all_columns)
    floor = [c for c in (DEFAULT_MINIMAL if minimal is None else minimal) if c in columns]
    plans: list[dict[str, Any]] = [{"mode": "baseline", "group": None, "drop": ()}]
    if floor:
        plans.append({"mode": "minimal", "group": None,
                      "drop": tuple(c for c in columns if c not in set(floor))})
    for group, members in groups.items():
        members = [m for m in members if m in columns]
        if not members:
            continue
        remaining = [c for c in columns if c not in set(members)]
        if remaining:
            plans.append({"mode": "leave_one_out", "group": group,
                          "drop": tuple(members)})
        # Add-one-in: keep the minimal floor plus this group, drop the rest.
        # Skipped when the floor is empty (nothing to add over) or when the
        # group already contains the whole matrix (nothing to drop).
        keep = set(floor) | set(members)
        drop = tuple(c for c in columns if c not in keep)
        if floor and drop:
            plans.append({"mode": "add_one_in", "group": group, "drop": drop})
    return plans


def _mean(values: Sequence[float]) -> float | None:
    clean = [v for v in values if v is not None and v == v]
    return sum(clean) / len(clean) if clean else None


def _interval(values: Sequence[float], z: float = 1.96) -> tuple[float, float] | None:
    """Normal-approximation interval on the mean. Needs at least two samples."""
    clean = [float(v) for v in values if v is not None and v == v]
    if len(clean) < 2:
        return None
    mean = sum(clean) / len(clean)
    variance = sum((v - mean) ** 2 for v in clean) / (len(clean) - 1)
    half = z * math.sqrt(variance / len(clean))
    return (round(mean - half, 6), round(mean + half, 6))


def noise_floor(results: Sequence[AblationResult], checkpoint: str) -> float | None:
    """Spread of the unablated baseline across seeds, at one checkpoint.

    A delta smaller than this is not distinguishable from rerunning the same
    configuration, whatever its sign. Computed per (family, fold) so it measures
    seed-to-seed variation rather than the much larger fold-to-fold variation,
    which is not noise -- it is the circuits genuinely differing.
    """
    by_cell: dict[tuple[str, str], list[float]] = {}
    for row in results:
        if row.mode != "baseline" or row.checkpoint != checkpoint or not row.ok:
            continue
        if row.metric is None:
            continue
        by_cell.setdefault((row.family, row.fold), []).append(row.metric)
    spreads = [max(v) - min(v) for v in by_cell.values() if len(v) > 1]
    return round(_mean(spreads), 6) if spreads else None


def summarise_group(results: Sequence[AblationResult], *, checkpoint: str, group: str,
                    n_features: int) -> GroupVerdict:
    """Deltas for one group at one checkpoint, paired against the baselines.

    Deltas are paired per ``(family, fold, seed)``: the same cell with and
    without the group. Comparing pooled means instead would let an unrelated
    difference in which cells happened to succeed leak into the delta.
    """
    def index(mode: str, want_group: str | None) -> dict[tuple[str, str, int], float]:
        return {(r.family, r.fold, r.seed): r.metric for r in results
                if r.checkpoint == checkpoint and r.mode == mode
                and r.group == want_group and r.ok and r.metric is not None}

    baseline = index("baseline", None)
    minimal = index("minimal", None)
    without = index("leave_one_out", group)
    alone = index("add_one_in", group)

    verdict = GroupVerdict(checkpoint=checkpoint, group=group, n_features=n_features,
                           is_identity=group in IDENTITY_GROUPS)

    # Brier is lower-better, so "removing the group made things worse" is a
    # positive delta. Positive means the group was earning its place.
    loo = [without[k] - baseline[k] for k in without.keys() & baseline.keys()]
    if loo:
        verdict.leave_one_out_delta = round(_mean(loo), 6)
        verdict.leave_one_out_interval = _interval(loo)
        verdict.samples = len(loo)

    aoi = [minimal[k] - alone[k] for k in alone.keys() & minimal.keys()]
    if aoi:
        verdict.add_one_in_delta = round(_mean(aoi), 6)
        verdict.add_one_in_interval = _interval(aoi)

    verdict.noise_floor = noise_floor(results, checkpoint)
    return verdict_for(verdict)


def verdict_for(verdict: GroupVerdict) -> GroupVerdict:
    """Classify a group from its two deltas and the noise floor (section 35).

    The four outcomes are deliberately distinct. "Redundant" and "uninformative"
    both show a near-zero leave-one-out delta and call for different decisions:
    a redundant group can be dropped to simplify, an uninformative one says the
    signal was never there.
    """
    loo, aoi = verdict.leave_one_out_delta, verdict.add_one_in_delta
    floor = verdict.noise_floor or 0.0

    if loo is None:
        verdict.verdict = "UNMEASURED"
        verdict.rationale = "no paired baseline and ablated fits completed for this group"
        return verdict

    significant = verdict.leave_one_out_interval is not None and \
        verdict.leave_one_out_interval[0] > 0
    if loo > floor and significant:
        verdict.verdict = "KEEP"
        verdict.rationale = (
            f"removing it costs {loo:+.5f} {PRIMARY_METRIC}, above the {floor:.5f} "
            "seed-to-seed noise floor and with an interval clear of zero")
    elif loo < -floor:
        verdict.verdict = "DROP"
        verdict.rationale = (
            f"removing it *improves* {PRIMARY_METRIC} by {-loo:.5f}; the group is "
            "costing accuracy, not adding it")
    elif aoi is not None and aoi > floor:
        verdict.verdict = "REDUNDANT"
        verdict.rationale = (
            f"leave-one-out delta {loo:+.5f} is inside the {floor:.5f} noise floor, but "
            f"the group alone beats the minimal baseline by {aoi:+.5f}. The signal is "
            "real and also carried by another group, so dropping it is safe only if "
            "that other group stays.")
    else:
        verdict.verdict = "UNINFORMATIVE"
        verdict.rationale = (
            f"leave-one-out delta {loo:+.5f} is inside the {floor:.5f} noise floor and "
            "the group does not beat the minimal baseline on its own")

    if verdict.is_identity and verdict.verdict == "KEEP":
        verdict.rationale += (
            ". Section 17: this is an identity group, so a large gain here is evidence "
            "of memorising drivers and teams rather than learning racecraft. Read it "
            "against the racecraft groups before keeping it.")
    return verdict
