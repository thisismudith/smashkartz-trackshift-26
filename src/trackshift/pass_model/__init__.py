"""Pass-probability chain (Chain P, Owner B).

CP-14 benchmark (M10), and the surface CP-15 calibration (M11), CP-16 ensemble
spread (M12) and CP-17 fine-tuning (M13) build on.

The output is a **probability**, and it is selected as one. Section 26 makes
calibration outrank ranking quality: the planner consumes `p_pass` to price
energy, so a model that orders battles correctly while being systematically
overconfident produces a confident, wrong shadow price. A model with slightly
lower ROC-AUC and materially better calibration wins here, every time.

Feature scope is not re-declared in this package. It is read from
`config/feature_registry.yaml`, the same `decision_checkpoint` field CP-13 used
to prove that a DETECTION row cannot hold an activation-time quantity -- so the
leakage guarantee made at build time is the one enforced at fit time.
"""
