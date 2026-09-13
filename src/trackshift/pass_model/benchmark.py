"""The fifteen-fit benchmark loop, and how it is sized to the machine (CP-14).

Three checkpoints times five families. Each cell is independent, which makes the
scheduling question simple to state and easy to get wrong: **parallelise across
fits, not inside them.**

At CP-13's current size -- 4,970 opportunities, so roughly 4,500 labelled rows
per checkpoint over a dozen features -- a boosted-tree fit is a fraction of a
second of real work. Handing one such fit sixteen threads does not make it
sixteen times faster; the histogram is too small to fill them, and the OpenMP
barrier at every tree level costs more than the split-finding it parallelises.
Handing sixteen threads to each of several concurrent fits is worse still: the
pools oversubscribe the same physical cores, and the benchmark gets *slower* as
workers are added, silently, because nothing errors.

That is the trap :func:`plan_hardware` exists to avoid, and it matters more than
usual on a hybrid CPU. When an OpenMP pool spans performance and efficiency
cores, every parallel region ends at its slowest member, so an oversubscribed
pool pays E-core latency at each barrier while the P-cores idle.

So the plan is: a small fixed thread count per fit, enough concurrent fits to
fill the physical cores, and a hard cap from free memory because each spawned
worker re-imports the whole numeric stack. Process-pool start-up on Windows is
not free either -- spawn re-imports per worker -- so a benchmark too small to
repay that cost runs in-process instead. Every one of those choices is recorded
in :class:`HardwarePlan.rationale` and lands in the manifest, because a run that
traded determinism or parallelism for speed should not be indistinguishable from
one that did not.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .candidates import CANDIDATES, DEFAULT_SEED, PassModel, configure_threads
from .features import audit_feature_matrix, build_matrix, select_features
from .metrics import evaluate

__all__ = [
    "HardwarePlan",
    "plan_hardware",
    "FitResult",
    "fit_cell",
    "run_benchmark",
]

#: Below this many training rows, a fit is too small for per-fit threading to pay
#: off and the cores are better spent running several fits at once.
SMALL_FIT_ROWS = 50_000

#: Rough resident cost of one spawned worker once numpy, sklearn and the three
#: boosting libraries are imported. Used only to cap worker count.
WORKER_MEMORY_GB = 0.75

#: Below this much total work, a process pool costs more in spawn overhead than
#: it returns. Windows has no fork, so every worker re-imports from scratch.
POOL_WORTH_IT_CELLS = 4
POOL_WORTH_IT_ROWS = 2_000


@dataclass(frozen=True)
class HardwarePlan:
    """How the benchmark will use the machine, and why."""

    workers: int
    threads_per_fit: int
    physical_cores: int | None
    free_memory_gb: float | None
    use_process_pool: bool
    rationale: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workers": self.workers,
            "threads_per_fit": self.threads_per_fit,
            "physical_cores": self.physical_cores,
            "free_memory_gb": self.free_memory_gb,
            "use_process_pool": self.use_process_pool,
            "rationale": list(self.rationale),
        }


def _physical_cores() -> int | None:
    """Physical cores, falling back to logical when the count is unavailable.

    ``os.cpu_count()`` reports logical processors. On a CPU with simultaneous
    multithreading that is roughly twice the number that can run an independent
    boosting fit at full speed, so sizing a pool from it oversubscribes by 2x.
    ``os.process_cpu_count()`` (3.13+) additionally respects an affinity mask,
    which is what a scheduler or a container would have set.
    """
    counter = getattr(os, "process_cpu_count", None)
    if callable(counter):
        value = counter()
        if value:
            return int(value)
    return os.cpu_count()


def _free_memory_gb() -> float | None:
    """Free physical memory in GB, or ``None`` when it cannot be read.

    Read through the stdlib only. The obvious tool is ``psutil``, which is not in
    ``requirements.txt``; adding a dependency so a scheduler can be slightly
    better informed is a poor trade, and ``None`` degrades to "do not cap".
    """
    try:
        if hasattr(os, "sysconf") and "SC_AVPHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1024 ** 3)
    except (ValueError, OSError):
        pass
    try:  # Windows
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.ullAvailPhys / (1024 ** 3)
    except Exception:
        pass
    return None


def plan_hardware(
    n_cells: int,
    train_rows: int,
    *,
    workers: int | None = None,
    threads_per_fit: int | None = None,
    cores: int | None = None,
    free_memory_gb: float | None = None,
) -> HardwarePlan:
    """Decide worker count and per-fit thread count for ``n_cells`` fits."""
    if n_cells < 1:
        raise ValueError(f"n_cells must be >= 1, got {n_cells}")
    detected_cores = cores if cores is not None else _physical_cores()
    memory = free_memory_gb if free_memory_gb is not None else _free_memory_gb()
    usable = max(1, (detected_cores or 2) - 1)  # leave one core for the OS and this process
    why: list[str] = [
        f"{detected_cores or 'unknown'} cores detected; {usable} budgeted for fits"
    ]

    if threads_per_fit is None:
        threads_per_fit = 2 if train_rows < SMALL_FIT_ROWS else 4
        why.append(
            f"{train_rows} training rows is "
            + ("below" if train_rows < SMALL_FIT_ROWS else "above")
            + f" the {SMALL_FIT_ROWS}-row threshold, so {threads_per_fit} threads per fit "
            "and the remaining cores spent on concurrent fits"
        )
    else:
        why.append(f"threads_per_fit pinned to {threads_per_fit} by the caller")
    threads_per_fit = max(1, int(threads_per_fit))

    if workers is None:
        by_cores = max(1, usable // threads_per_fit)
        chosen = min(n_cells, by_cores)
        why.append(f"{by_cores} concurrent fits fit in the core budget; {n_cells} cells to run")
        if memory is not None:
            by_memory = max(1, int(memory / WORKER_MEMORY_GB))
            if by_memory < chosen:
                why.append(
                    f"capped to {by_memory} by {memory:.1f} GB free memory at about "
                    f"{WORKER_MEMORY_GB} GB per spawned worker"
                )
                chosen = by_memory
        workers = chosen
    else:
        why.append(f"workers pinned to {workers} by the caller")
    workers = max(1, int(workers))

    use_pool = workers > 1 and n_cells >= POOL_WORTH_IT_CELLS and train_rows >= POOL_WORTH_IT_ROWS
    if workers > 1 and not use_pool:
        why.append(
            f"running in-process: {n_cells} cells over {train_rows} rows is below the "
            f"{POOL_WORTH_IT_CELLS}-cell / {POOL_WORTH_IT_ROWS}-row floor where spawning "
            "workers repays its start-up cost"
        )
    elif use_pool:
        why.append(f"process pool of {workers} workers, {threads_per_fit} threads each")

    return HardwarePlan(
        workers=workers,
        threads_per_fit=threads_per_fit,
        physical_cores=detected_cores,
        free_memory_gb=round(memory, 2) if memory is not None else None,
        use_process_pool=use_pool,
        rationale=tuple(why),
    )


@dataclass
class FitResult:
    """One benchmark cell: a family at a checkpoint, scored on every split."""

    checkpoint: str
    family: str
    fold: str
    ok: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    model_description: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "family": self.family,
            "fold": self.fold,
            "ok": self.ok,
            "metrics": self.metrics,
            "model": self.model_description,
            "error": self.error,
            "seconds": self.seconds,
        }


#: The opportunity table, handed to each worker once at start-up. See
#: :func:`_pool_initializer`.
_SHARED_FRAME: Any = None


def _pool_initializer(threads: int, frame: Any = None) -> None:
    """Runs first in every spawned worker, before the numeric stack is imported.

    The frame is passed here rather than as an argument to each task because
    ``submit`` pickles its arguments per call. With 3 checkpoints x 5 families x
    7 folds that is 105 copies of the whole opportunity table crossing the
    process boundary, against fits that each take well under a second -- the
    serialisation would cost more than the work. Through the initialiser it
    crosses once per worker instead.
    """
    configure_threads(threads)
    global _SHARED_FRAME
    _SHARED_FRAME = frame


def _fit_shared(checkpoint: str, family: str, fold, **kwargs):
    """Pool entry point: fit one cell against the worker's shared frame."""
    if _SHARED_FRAME is None:  # pragma: no cover - defensive
        raise RuntimeError("worker was not initialised with the opportunity table")
    return fit_cell(_SHARED_FRAME, checkpoint, family, fold, **kwargs)


def fit_cell(
    frame,
    checkpoint: str,
    family: str,
    fold,
    *,
    seed: int = DEFAULT_SEED,
    threads: int = 1,
    deterministic: bool = True,
    include_identity: bool = False,
    return_model: bool = False,
    drop_features: tuple[str, ...] = (),
):
    """Fit and score one (checkpoint, family, fold) cell.

    Module-level and returning plain data so it pickles into a process pool.
    Failures are captured into the result rather than raised: one family's
    missing wheel or degenerate fold should cost its own row in the report, not
    the other fourteen fits.
    """
    import time

    started = time.perf_counter()
    result = FitResult(checkpoint=checkpoint, family=family, fold=fold.name, ok=False)
    model = None
    try:
        rows = frame[frame["decision_checkpoint"] == checkpoint]
        if rows.empty:
            raise ValueError(f"no rows at checkpoint {checkpoint}")
        selection = select_features(
            checkpoint, rows.columns, include_identity=include_identity, dtypes=rows.dtypes
        )
        audit_feature_matrix(rows, selection)
        # CP-23 ablation. Applied after selection and after the audit so the
        # ablated run differs from the baseline in exactly one way: the columns
        # this group contributed. Re-selecting instead would let an unrelated
        # decision move at the same time and be attributed to the group.
        if drop_features:
            selection = selection.without(drop_features, reason="CP-23 ablation")
            if not selection.columns:
                raise ValueError(
                    "ablation removed every feature; there is nothing left to fit. "
                    "That is a real result for this group, recorded as such.")

        splits = {}
        for role, index in (("train", fold.train), ("validation", fold.validation),
                            ("test", fold.test)):
            subset = rows.loc[rows.index.intersection(index)]
            splits[role] = build_matrix(subset, selection) if len(subset) else (None, None)

        X_train, y_train = splits["train"]
        if X_train is None or len(y_train) == 0:
            raise ValueError("training split is empty after dropping unlabelled rows")
        if len(set(map(int, y_train))) < 2:
            raise ValueError(
                "training split has a single class; nothing separable to fit. "
                "This is a fold-size problem, not a model problem."
            )

        model = PassModel(
            family,
            numeric=selection.numeric,
            categorical=selection.categorical,
            seed=seed,
            threads=threads,
            deterministic=deterministic,
        )
        X_val, y_val = splits["validation"]
        model.fit(X_train, y_train, X_val, y_val)

        # The reference predictor knows the TRAINING base rate only. A constant
        # predictor tuned to the test base rate is not a baseline; it is a model
        # fitted on the test set.
        train_rate = float(y_train.mean())
        for role, (X, y) in splits.items():
            if X is None or y is None or len(y) == 0:
                continue
            result.metrics[role] = evaluate(y, model.predict_proba(X), base_rate=train_rate)
        result.metrics["feature_schema"] = selection.as_schema()
        result.model_description = dict(model.describe())
        result.ok = "test" in result.metrics
        if not result.ok:
            result.error = "test split is empty after dropping unlabelled rows"
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    result.seconds = round(time.perf_counter() - started, 3)
    if return_model:
        return result, model
    return result


def run_benchmark(
    frame,
    plan,
    *,
    checkpoints: Sequence[str],
    families: Sequence[str],
    hardware: HardwarePlan,
    seed: int = DEFAULT_SEED,
    deterministic: bool = True,
    include_identity: bool = False,
    progress: Any = None,
) -> list[FitResult]:
    """Run every (checkpoint, family, fold) cell under ``hardware``."""
    unknown = [f for f in families if f not in CANDIDATES]
    if unknown:
        raise ValueError(f"unknown families {unknown}; expected a subset of {sorted(CANDIDATES)}")

    cells = [
        (checkpoint, family, fold)
        for checkpoint in checkpoints
        for family in families
        for fold in plan.folds
    ]
    common = dict(
        seed=seed,
        threads=hardware.threads_per_fit,
        deterministic=deterministic,
        include_identity=include_identity,
    )

    if not hardware.use_process_pool:
        configure_threads(hardware.threads_per_fit)
        out: list[FitResult] = []
        for checkpoint, family, fold in cells:
            out.append(fit_cell(frame, checkpoint, family, fold, **common))
            if progress:
                progress(out[-1])
        return out

    from concurrent.futures import ProcessPoolExecutor, as_completed

    results: list[FitResult] = []
    with ProcessPoolExecutor(
        max_workers=hardware.workers,
        initializer=_pool_initializer,
        initargs=(hardware.threads_per_fit, frame),
    ) as pool:
        futures = {
            pool.submit(_fit_shared, checkpoint, family, fold, **common): (checkpoint, family)
            for checkpoint, family, fold in cells
        }
        for future in as_completed(futures):
            results.append(future.result())
            if progress:
                progress(results[-1])
    # as_completed returns in finish order, which varies run to run. Sorting
    # keeps the report and its metric table byte-comparable between runs.
    results.sort(key=lambda r: (r.checkpoint, r.family, r.fold))
    return results


def aggregate(results: Sequence[FitResult], key: str = "test") -> list[dict[str, Any]]:
    """Mean and spread of each metric across folds, per (checkpoint, family).

    Standard deviation across folds is reported because CP-14 gates on it: a
    Brier std above 0.05 across leave-one-track-out folds means the model is
    memorising circuits rather than learning racecraft.
    """
    import math
    from collections import defaultdict

    grouped: dict[tuple[str, str], list[FitResult]] = defaultdict(list)
    for result in results:
        grouped[(result.checkpoint, result.family)].append(result)

    out: list[dict[str, Any]] = []
    for (checkpoint, family), members in sorted(grouped.items()):
        good = [m for m in members if m.ok and key in m.metrics]
        entry: dict[str, Any] = {
            "checkpoint": checkpoint,
            "family": family,
            "folds": len(members),
            "folds_ok": len(good),
            "errors": sorted({m.error for m in members if m.error}),
        }
        if not good:
            out.append(entry)
            continue
        total_n = sum(m.metrics[key]["n"] for m in good)
        for metric in ("brier", "brier_reference", "log_loss", "ece", "roc_auc", "pr_auc"):
            values = [
                m.metrics[key][metric] for m in good
                if m.metrics[key].get(metric) is not None
                and not (isinstance(m.metrics[key][metric], float)
                         and math.isnan(m.metrics[key][metric]))
            ]
            if not values:
                entry[metric] = None
                entry[f"{metric}_std"] = None
                continue
            mean = sum(values) / len(values)
            entry[metric] = mean
            entry[f"{metric}_std"] = (
                math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
                if len(values) > 1 else 0.0
            )
            entry[f"{metric}_n"] = len(values)
        # Skill is derived from the aggregated Brier scores, not averaged across
        # folds. Per-fold skill is a ratio, and ratios do not average: Monaco's
        # test base rate is about 1.6% against a training rate near 15%, so its
        # reference Brier is tiny and its skill ratio swings far enough to
        # dominate a seven-fold mean. Averaging them produces a Skill column that
        # contradicts the Brier column beside it, which is worse than no column.
        reference = entry.get("brier_reference")
        brier = entry.get("brier")
        entry["brier_skill_score"] = (
            None if not reference or brier is None else 1.0 - brier / reference
        )
        entry["n"] = total_n
        entry["n_positive"] = sum(m.metrics[key]["n_positive"] for m in good)
        entry["seconds"] = round(sum(m.seconds or 0.0 for m in members), 3)
        # rank_results reads metrics[key]; give it the aggregate in that shape.
        entry["metrics"] = {key: {
            "brier": entry.get("brier"), "ece": entry.get("ece"),
            "log_loss": entry.get("log_loss"), "roc_auc": entry.get("roc_auc"),
            "n": total_n,
        }}
        out.append(entry)
    return out
