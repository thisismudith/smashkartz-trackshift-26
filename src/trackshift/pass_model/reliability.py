"""Reliability diagrams for CP-15 (section 27).

The first plotting code in the project, so it sets the conventions: the Agg
backend selected before pyplot is imported (these run headless, in CI and over
SSH, and the default interactive backend either fails or leaks a window), square
figures so the diagonal reads at 45 degrees, and every figure written beside a
sibling ``.json`` carrying the numbers it was drawn from. A plot nobody can
re-derive is decoration; the JSON is what a reviewer checks.

Three things are drawn that a plain predicted-vs-observed curve leaves out, each
because it is the thing that misleads:

**Wilson bands per bin.** "Within the diagonal's confidence band" is a CP-15
gate, and eyeballing a point against a line cannot settle it. Wilson rather than
the normal approximation because the informative bins sit near 0 and 1, where the
normal interval runs outside [0, 1].

**Bins below n=50 drawn hollow.** A bin of nine rows lands anywhere; drawn the
same as a bin of nine hundred it invites a conclusion it cannot support.

**The realised bin count in the title.** Equal-count edges collapse under ties,
and calibrated output is full of ties -- isotonic can turn ten requested bins
into three. A diagram silently drawn from three bins looks like agreement.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .calibration import MIN_BIN_N

__all__ = ["FIGURE_SIZE_IN", "FIGURE_DPI", "plot_reliability", "write_bin_table"]

#: Square, so the diagonal is a true 45 degrees and over/under-confidence read
#: as deflection from it rather than from an arbitrary aspect ratio.
FIGURE_SIZE_IN = (6.0, 6.0)
FIGURE_DPI = 160


def _pyplot():
    """Import pyplot with a headless backend chosen first."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def plot_reliability(
    diagnostics: Mapping[str, Any],
    destination: Path,
    *,
    title: str,
    subtitle: str = "",
) -> Path:
    """Draw one reliability diagram from :func:`calibrate.bin_diagnostics` output."""
    plt = _pyplot()

    bins = list(diagnostics.get("bins") or [])
    destination.parent.mkdir(parents=True, exist_ok=True)

    figure, axes = plt.subplots(figsize=FIGURE_SIZE_IN, dpi=FIGURE_DPI)
    axes.plot([0, 1], [0, 1], linestyle="--", linewidth=1.0, color="#888888",
              label="perfect calibration", zorder=1)

    solid_x, solid_y, hollow_x, hollow_y = [], [], [], []
    for entry in bins:
        x, y = entry["mean_predicted"], entry["observed_rate"]
        axes.vlines(x, entry["wilson_low"], entry["wilson_high"],
                    color="#4C78A8", linewidth=1.2, alpha=0.7, zorder=2)
        (solid_x if entry.get("assessable") else hollow_x).append(x)
        (solid_y if entry.get("assessable") else hollow_y).append(y)

    if solid_x:
        axes.plot(solid_x, solid_y, marker="o", linestyle="-", color="#4C78A8",
                  markersize=6, linewidth=1.5, label=f"bin (n >= {MIN_BIN_N})", zorder=3)
    if hollow_x:
        axes.plot(hollow_x, hollow_y, marker="o", linestyle="none",
                  markerfacecolor="white", markeredgecolor="#4C78A8",
                  markersize=6, label=f"bin (n < {MIN_BIN_N}, not assessable)", zorder=3)

    realised = diagnostics.get("realised_bins")
    requested = diagnostics.get("requested_bins")
    caption = f"{realised} of {requested} bins realised"
    if realised is not None and requested is not None and realised < requested:
        # Ties collapsed the quantile edges. Say so on the figure: a three-bin
        # diagram that looks well calibrated is mostly telling you about ties.
        caption += " -- ties collapsed the equal-count edges"

    axes.set_xlim(-0.02, 1.02)
    axes.set_ylim(-0.02, 1.02)
    axes.set_xlabel("mean predicted probability")
    axes.set_ylabel("observed pass rate")
    axes.set_title(title, fontsize=11)
    axes.text(0.02, 0.97, "\n".join(filter(None, [subtitle, caption])),
              transform=axes.transAxes, va="top", ha="left", fontsize=8, color="#444444")
    axes.legend(loc="lower right", fontsize=8, framealpha=0.9)
    axes.grid(True, alpha=0.2, linewidth=0.5)
    axes.set_aspect("equal", adjustable="box")
    figure.tight_layout()
    figure.savefig(destination, facecolor="white")
    plt.close(figure)

    sidecar = destination.with_suffix(".json")
    sidecar.write_text(json.dumps(dict(diagnostics), indent=2), encoding="utf-8")
    return destination


def write_bin_table(diagnostics: Mapping[str, Any]) -> list[str]:
    """The same bins as a markdown table, for the report."""
    lines = [
        "| Bin | Range | n | Mean predicted | Observed | Wilson band | In band |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, entry in enumerate(diagnostics.get("bins") or [], start=1):
        flag = "--" if not entry.get("assessable") else ("yes" if entry["within_band"] else "**no**")
        lines.append(
            f"| {index} | {entry['lower']:.3f}-{entry['upper']:.3f} | {entry['n']} | "
            f"{entry['mean_predicted']:.4f} | {entry['observed_rate']:.4f} | "
            f"{entry['wilson_low']:.3f}-{entry['wilson_high']:.3f} | {flag} |"
        )
    return lines


def reliability_filename(checkpoint: str, family: str, method: str) -> str:
    return f"{checkpoint.lower()}_{family}_{method}.png"


def plot_all(
    results: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    run_label: str = "",
) -> list[str]:
    """Draw one diagram per (checkpoint, family, method) aggregate."""
    written: list[str] = []
    for entry in results:
        diagnostics = (entry.get("metrics") or {}).get("bin_diagnostics")
        if not diagnostics:
            continue
        name = reliability_filename(entry["checkpoint"], entry["family"], entry["method"])
        path = plot_reliability(
            diagnostics,
            root / name,
            title=f"{entry['checkpoint']} / {entry['family']} / {entry['method']}",
            subtitle=" ".join(filter(None, [
                run_label,
                f"fold {entry.get('fold')}" if entry.get("fold") else "",
                f"n={(entry.get('metrics') or {}).get('n')}",
            ])),
        )
        written.append(str(path))
    return written
