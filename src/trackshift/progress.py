"""Single-line progress reporting for long, single-threaded walks.

Several TrackShift jobs read every file in the raw mirror. The schema audit
parses 177,288 lap files and takes 35-40 minutes; the CP-04 lake build is
similar. With no output, a long run is indistinguishable from a hang, and the
natural response -- killing it and starting again -- wastes the work already
done. This reports percent complete, throughput and ETA so a slow run stays
legible.

Design notes:

* Output goes to **stderr**, so stdout stays clean for scripts that emit JSON.
* Redraws are rate-limited, so drawing never competes with the actual work.
* A no-op mode (``enabled=False``) keeps call sites free of ``if`` guards.
* ``total`` may be ``0`` or unknown; the bar then degrades to a plain counter
  rather than dividing by zero.

Typical use::

    prog = Progress(count_files(root), enabled=not args.no_progress)
    for f in files:
        prog.set_label(f"{year} {event} / {session}")
        ...
        prog.tick()
    prog.close()
"""
from __future__ import annotations

import sys
import time

__all__ = ["Progress", "format_duration"]

CR = "\r"
BAR_WIDTH = 24
LABEL_WIDTH = 42


def format_duration(seconds: float) -> str:
    """Human-readable duration, chosen for width stability on one line."""
    seconds = int(max(0, seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


class Progress:
    """Rate-limited single-line progress bar on stderr.

    Parameters
    ----------
    total:
        Expected number of ticks. ``0`` means unknown; the display falls back
        to a plain counter with no percentage or ETA.
    enabled:
        When false every method is a no-op, so callers need no conditionals.
    stream:
        Defaults to ``sys.stderr``.
    min_interval:
        Seconds between redraws. The default keeps the line lively without the
        redraw cost mattering next to the work being measured.
    """

    def __init__(self, total: int, enabled: bool = True, stream=None, min_interval: float = 0.5) -> None:
        self.total = max(0, int(total))
        self.stream = stream if stream is not None else sys.stderr
        # A non-tty (piped to a file or CI log) would otherwise accumulate one
        # line per redraw, so carriage-return animation is pointless there.
        self.interactive = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = bool(enabled)
        self.min_interval = float(min_interval)
        self.done = 0
        self.label = ""
        self._start = time.monotonic()
        self._last_draw = 0.0
        self._drawn_width = 0

    # ------------------------------------------------------------------ api
    def set_label(self, label: str) -> None:
        """Set the context shown beside the bar, e.g. the current session."""
        self.label = str(label)

    def tick(self, n: int = 1) -> None:
        """Record ``n`` completed items and redraw if due."""
        self.done += n
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last_draw < self.min_interval and self.done < self.total:
            return
        self._last_draw = now
        self._draw()

    def heartbeat(self) -> None:
        """Redraw without advancing, so a long gap between ticks still looks alive.

        Needed when work arrives in a few large chunks rather than many small
        ones: the lake build ticks once per finished session, so a five-session
        stage would otherwise print nothing for a minute and then everything at
        once, which is indistinguishable from a hang.
        """
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last_draw < self.min_interval:
            return
        self._last_draw = now
        self._draw()

    def close(self) -> None:
        """Clear the line and print a one-line summary."""
        if not self.enabled:
            return
        elapsed = time.monotonic() - self._start
        self._clear()
        rate = self.done / elapsed if elapsed > 0 else 0.0
        self.stream.write(
            f"done: {self.done} items in {format_duration(elapsed)} ({rate:.0f}/s)\n"
        )
        self.stream.flush()

    # -------------------------------------------------------------- internals
    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def eta_seconds(self) -> float:
        """Remaining time from the average rate so far. ``0`` if unknown."""
        if not self.total or self.done <= 0:
            return 0.0
        elapsed = self.elapsed
        if elapsed <= 0:
            return 0.0
        rate = self.done / elapsed
        return (self.total - self.done) / rate if rate > 0 else 0.0

    def render(self) -> str:
        """The progress line as a string. Separated out so it can be tested."""
        elapsed = self.elapsed
        rate = self.done / elapsed if elapsed > 0 else 0.0
        label = self.label[:LABEL_WIDTH].ljust(LABEL_WIDTH)
        if not self.total:
            return f"{self.done} items  {format_duration(elapsed)} elapsed  {label}"
        pct = 100.0 * self.done / self.total
        filled = int(BAR_WIDTH * self.done / self.total)
        bar = "#" * filled + "-" * (BAR_WIDTH - filled)
        eta = format_duration(self.eta_seconds()) if self.done else "--"
        return (
            f"[{bar}] {pct:5.1f}%  {self.done}/{self.total}  "
            f"{format_duration(elapsed)} elapsed  ETA {eta}  {label}"
        )

    def _draw(self) -> None:
        line = self.render()
        if self.interactive:
            self.stream.write(CR + line)
            self._drawn_width = max(self._drawn_width, len(line))
        else:
            # Piped output: one line per redraw, which stays readable in a log.
            self.stream.write(line.rstrip() + "\n")
        self.stream.flush()

    def _clear(self) -> None:
        if self.interactive and self._drawn_width:
            self.stream.write(CR + " " * self._drawn_width + CR)
            self.stream.flush()
