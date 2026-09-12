"""The five M10 benchmark families (Tanveer CP-14, AGENTS.md section 26).

Hyperparameters are transcribed from CP-14 verbatim. They are starting values,
not tuned ones, and the point of the benchmark is that every family sees the
same feature matrix at the same checkpoint under the same seed -- so the only
thing varying between the fifteen fits is the learner.

Three things this module owns beyond the parameter block:

**One categorical policy for four libraries.** The four learners disagree about
categoricals: sklearn wants them one-hot encoded, LightGBM and XGBoost want a
pandas ``category`` dtype, CatBoost wants raw strings and refuses a float NaN.
Left to each family, "the same feature matrix" would quietly stop being true.
:class:`PassModel` therefore fixes the category set on the training split and
applies it identically everywhere: a level unseen in training becomes missing,
never a new integer code, because a new code at test time would mean the test
matrix has a column the training matrix never had.

**Determinism before speed.** MODELS.md section 6.3 has both owners building
locally and comparing outputs, so a fit that varies with thread count is worse
than a fit that is slower. ``deterministic=True`` (the default) forces the
row-wise histogram build in LightGBM and pins every thread count explicitly.
``deterministic=False`` is available and is recorded in the manifest, so a run
that traded reproducibility for speed cannot be mistaken for one that did not.

**Availability as data, not an exception.** :func:`available_families` reports
which learners import, so a missing wheel costs you that family's row in the
report rather than the whole benchmark run.
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

__all__ = [
    "FAMILIES",
    "DEFAULT_SEED",
    "Candidate",
    "CANDIDATES",
    "available_families",
    "configure_threads",
    "PassModel",
]

DEFAULT_SEED = 42

#: Benchmark order. Logistic regression is first because section 26 makes it the
#: baseline every other family must beat or lose to.
FAMILIES: tuple[str, ...] = ("logistic", "lightgbm", "xgboost", "catboost", "mlp")


@dataclass(frozen=True)
class Candidate:
    """What a family needs and what it can do, for scheduling and reporting."""

    name: str
    module: str | None
    #: Consumes a validation split to stop early. Without one it runs to its
    #: full iteration budget, which at CP-14's 2,000-3,000 trees overfits a
    #: few-thousand-row table -- so the caller must supply one or accept that.
    early_stopping: bool
    #: Handles string categoricals without one-hot expansion.
    native_categoricals: bool
    #: Needs numeric features standardised.
    needs_scaling: bool
    notes: str


CANDIDATES: dict[str, Candidate] = {
    "logistic": Candidate(
        "logistic", None, False, False, True,
        "Interpretable baseline (section 26). Every other family must beat it or lose.",
    ),
    "lightgbm": Candidate(
        "lightgbm", "lightgbm", True, True, False,
        "Leaf-wise histogram boosting; early stopping on the validation split.",
    ),
    "xgboost": Candidate(
        "xgboost", "xgboost", True, True, False,
        "Depth-wise histogram boosting; categoricals via enable_categorical.",
    ),
    "catboost": Candidate(
        "catboost", "catboost", True, True, False,
        "Ordered boosting with native categorical handling (section 26).",
    ),
    "mlp": Candidate(
        "mlp", None, False, False, True,
        "Optional fifth family (section 26). Under a minute on CPU at this size.",
    ),
}


def available_families(families: Sequence[str] = FAMILIES) -> dict[str, str | None]:
    """Map family -> ``None`` if importable, else the import error text.

    Reported rather than raised: a missing CatBoost wheel should cost the
    benchmark one of five rows, not all fifteen fits.
    """
    status: dict[str, str | None] = {}
    for name in families:
        candidate = CANDIDATES.get(name)
        if candidate is None:
            status[name] = f"unknown family {name!r}; expected one of {FAMILIES}"
            continue
        required = ["sklearn"] + ([candidate.module] if candidate.module else [])
        error: str | None = None
        for module in required:
            try:
                importlib.import_module(module)
            except Exception as exc:  # pragma: no cover - depends on the environment
                error = f"{type(exc).__name__}: {exc}"
                break
        status[name] = error
    return status


def configure_threads(threads: int) -> None:
    """Pin every BLAS/OpenMP pool to ``threads``.

    Must run **before** numpy, sklearn or any learner is imported: each reads its
    thread count once, at import. This is the single most important knob when
    fits run in a process pool. Without it each of N worker processes starts its
    own pool sized to all cores, the pools fight for the same physical cores, and
    the benchmark runs slower with more workers than with one -- and it does so
    silently, since nothing errors.

    It matters more than usual on a hybrid CPU. When OpenMP spreads a pool across
    performance and efficiency cores, every parallel region ends at the slowest
    core in it, so an oversubscribed pool pays the E-core latency on every
    barrier.
    """
    if threads < 1:
        raise ValueError(f"threads must be >= 1, got {threads}")
    value = str(int(threads))
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ[name] = value


def _params(family: str, *, seed: int, threads: int, deterministic: bool) -> dict[str, Any]:
    """CP-14's parameter block, plus the hardware and reproducibility knobs."""
    if family == "logistic":
        return dict(
            penalty="l2", C=1.0, solver="lbfgs", max_iter=2000,
            class_weight=None, random_state=seed,
        )
    if family == "lightgbm":
        params = dict(
            n_estimators=2000, learning_rate=0.03, num_leaves=31,
            max_depth=-1, min_child_samples=40, subsample=0.8,
            subsample_freq=1, colsample_bytree=0.8,
            reg_alpha=0.0, reg_lambda=1.0, random_state=seed,
            n_jobs=threads, verbose=-1,
        )
        if deterministic:
            # deterministic alone still warns and can vary; LightGBM documents it
            # as requiring an explicit histogram build order.
            params.update(deterministic=True, force_row_wise=True)
        return params
    if family == "xgboost":
        return dict(
            n_estimators=2000, learning_rate=0.03, max_depth=5,
            min_child_weight=5, subsample=0.8, colsample_bytree=0.8,
            reg_lambda=1.0, eval_metric="logloss",
            early_stopping_rounds=100, random_state=seed,
            n_jobs=threads, tree_method="hist", enable_categorical=True,
        )
    if family == "catboost":
        return dict(
            iterations=3000, learning_rate=0.03, depth=6,
            l2_leaf_reg=3.0, loss_function="Logloss",
            eval_metric="Logloss", od_type="Iter", od_wait=100,
            random_seed=seed, verbose=0, thread_count=threads,
            allow_writing_files=False,
        )
    if family == "mlp":
        return dict(
            hidden_layer_sizes=(64, 32), activation="relu", alpha=1e-3,
            learning_rate_init=1e-3, max_iter=500, early_stopping=True,
            n_iter_no_change=25, random_state=seed,
        )
    raise ValueError(f"unknown family {family!r}; expected one of {FAMILIES}")


class PassModel:
    """One benchmark fit: a family, bound to one checkpoint's feature schema.

    Wraps the four libraries behind ``fit`` / ``predict_proba`` so the benchmark
    loop has no per-family branches, and so the categorical policy above is
    applied in exactly one place.
    """

    def __init__(
        self,
        family: str,
        *,
        numeric: Sequence[str],
        categorical: Sequence[str],
        seed: int = DEFAULT_SEED,
        threads: int = 1,
        deterministic: bool = True,
    ) -> None:
        if family not in CANDIDATES:
            raise ValueError(f"unknown family {family!r}; expected one of {FAMILIES}")
        self.family = family
        self.numeric = list(numeric)
        self.categorical = list(categorical)
        self.seed = int(seed)
        self.threads = int(threads)
        self.deterministic = bool(deterministic)
        self.params = _params(family, seed=self.seed, threads=self.threads,
                              deterministic=self.deterministic)
        self._model: Any = None
        self._categories: dict[str, list[str]] = {}
        self._best_iteration: int | None = None

    # -- categorical policy -------------------------------------------------

    def _learn_categories(self, X) -> None:
        self._categories = {
            name: sorted(map(str, X[name].astype(str).unique())) for name in self.categorical
        }

    def _apply_categories(self, X):
        """Project onto the training category set.

        A level unseen in training becomes missing rather than a fresh code.
        Both LightGBM and XGBoost treat an out-of-category value as missing and
        route it down their default branch, which is the honest answer for a
        driver the model never saw.
        """
        if not self.categorical:
            return X
        import pandas as pd

        out = X.copy()
        for name in self.categorical:
            out[name] = pd.Categorical(out[name].astype(str), categories=self._categories[name])
        return out

    # -- fit / predict ------------------------------------------------------

    def _build(self):
        from sklearn.compose import ColumnTransformer
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder, StandardScaler

        if self.family in ("logistic", "mlp"):
            steps: list[tuple[str, Any, list[str]]] = []
            if self.numeric:
                steps.append(("num", StandardScaler(), self.numeric))
            if self.categorical:
                steps.append((
                    "cat",
                    OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    self.categorical,
                ))
            pre = ColumnTransformer(steps, remainder="drop")
            if self.family == "logistic":
                from sklearn.linear_model import LogisticRegression
                estimator: Any = LogisticRegression(**self.params)
            else:
                from sklearn.neural_network import MLPClassifier
                estimator = MLPClassifier(**self.params)
            # The imputer is not cosmetic: a NaN in a numeric feature reaches
            # lbfgs as a NaN gradient and the fit returns silently useless
            # coefficients. The trees handle missing natively; these two do not.
            from sklearn.impute import SimpleImputer
            return Pipeline([
                ("pre", pre),
                ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("clf", estimator),
            ])
        if self.family == "lightgbm":
            from lightgbm import LGBMClassifier
            return LGBMClassifier(**self.params)
        if self.family == "xgboost":
            from xgboost import XGBClassifier
            return XGBClassifier(**self.params)
        from catboost import CatBoostClassifier
        return CatBoostClassifier(**self.params)

    def fit(self, X_train, y_train, X_val=None, y_val=None) -> "PassModel":
        self._learn_categories(X_train)
        self._model = self._build()
        has_val = X_val is not None and y_val is not None and len(y_val) > 0
        # Early stopping needs both classes in the eval set, or the metric is
        # undefined and the learner stops on the first round.
        usable_val = bool(has_val) and len(set(map(int, y_val))) > 1

        if self.family in ("logistic", "mlp"):
            self._model.fit(X_train, y_train)
        elif self.family == "lightgbm":
            import inspect

            from lightgbm import early_stopping, log_evaluation
            kwargs: dict[str, Any] = {"categorical_feature": self.categorical or "auto"}
            if usable_val:
                # LightGBM 4.7 deprecated the eval_set list in favour of a single
                # eval_X / eval_y pair. requirements.txt allows >=4.3, so pick
                # whichever this build accepts rather than emitting a deprecation
                # warning per fit on new versions or breaking on old ones.
                accepted = inspect.signature(self._model.fit).parameters
                if "eval_X" in accepted:
                    kwargs["eval_X"] = self._apply_categories(X_val)
                    kwargs["eval_y"] = y_val
                else:
                    kwargs["eval_set"] = [(self._apply_categories(X_val), y_val)]
                kwargs["eval_metric"] = "binary_logloss"
                kwargs["callbacks"] = [early_stopping(100, verbose=False), log_evaluation(0)]
            self._model.fit(self._apply_categories(X_train), y_train, **kwargs)
            self._best_iteration = getattr(self._model, "best_iteration_", None)
        elif self.family == "xgboost":
            if usable_val:
                self._model.fit(
                    self._apply_categories(X_train), y_train,
                    eval_set=[(self._apply_categories(X_val), y_val)], verbose=False,
                )
            else:
                # early_stopping_rounds with no eval_set raises in XGBoost 2+.
                self._model.set_params(early_stopping_rounds=None)
                self._model.fit(self._apply_categories(X_train), y_train, verbose=False)
            self._best_iteration = getattr(self._model, "best_iteration", None)
        else:
            fit_kwargs: dict[str, Any] = {"cat_features": self.categorical}
            if usable_val:
                fit_kwargs["eval_set"] = (X_val, y_val)
                fit_kwargs["use_best_model"] = True
            self._model.fit(X_train, y_train, **fit_kwargs)
            best = getattr(self._model, "get_best_iteration", None)
            self._best_iteration = best() if callable(best) else None
        return self

    def predict_proba(self, X):
        """P(pass) for each row, as a 1-D array."""
        if self._model is None:
            raise RuntimeError(f"{self.family} model is not fitted")
        frame = X if self.family in ("logistic", "mlp", "catboost") else self._apply_categories(X)
        proba = self._model.predict_proba(frame)
        return proba[:, 1]

    @property
    def best_iteration(self) -> int | None:
        """Where early stopping landed, or ``None`` if it did not run."""
        return self._best_iteration

    def describe(self) -> Mapping[str, Any]:
        """Everything the manifest needs to reproduce this fit."""
        return {
            "family": self.family,
            "params": {k: list(v) if isinstance(v, tuple) else v for k, v in self.params.items()},
            "seed": self.seed,
            "threads": self.threads,
            "deterministic": self.deterministic,
            "best_iteration": self._best_iteration,
            "n_numeric": len(self.numeric),
            "n_categorical": len(self.categorical),
            "categorical_levels": {k: len(v) for k, v in self._categories.items()},
        }
