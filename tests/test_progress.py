"""Tests for the long-run progress reporter.

Progress is cosmetic, so the risk is not that it looks wrong but that it
interferes: dividing by zero on an unknown total, writing to stdout and
corrupting a JSON pipe, or redrawing so often it slows the work it measures.
These tests target that.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.progress import Progress, format_duration  # noqa: E402


class FakeTTY(io.StringIO):
    """StringIO that claims to be a terminal, to exercise the redraw path."""

    def isatty(self) -> bool:
        return True


# ------------------------------------------------------------- format_duration
@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"),
        (9, "9s"),
        (59, "59s"),
        (60, "1m00s"),
        (95, "1m35s"),
        (3600, "1h00m"),
        (5400, "1h30m"),
        (-5, "0s"),  # never render a negative ETA
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


# ----------------------------------------------------------------- core counts
def test_ticks_accumulate():
    p = Progress(10, stream=io.StringIO())
    for _ in range(4):
        p.tick()
    assert p.done == 4


def test_tick_accepts_a_step():
    p = Progress(10, stream=io.StringIO())
    p.tick(5)
    assert p.done == 5


def test_render_reports_percentage_and_counts():
    p = Progress(200, stream=io.StringIO())
    p.tick(50)
    line = p.render()
    assert "25.0%" in line
    assert "50/200" in line


def test_bar_fills_completely_at_the_end():
    p = Progress(4, stream=io.StringIO())
    p.tick(4)
    line = p.render()
    assert "100.0%" in line
    assert "-" not in line.split("]")[0]  # no unfilled cells remain


# --------------------------------------------------------------- failure modes
def test_zero_total_does_not_divide_by_zero():
    """An empty or uncountable scope must degrade, not crash."""
    p = Progress(0, stream=io.StringIO())
    p.tick()
    line = p.render()
    assert "%" not in line
    assert "1 items" in line
    p.close()


def test_negative_total_is_clamped():
    assert Progress(-5, stream=io.StringIO()).total == 0


def test_eta_is_zero_before_any_progress():
    assert Progress(100, stream=io.StringIO()).eta_seconds() == 0.0


def test_overshooting_total_does_not_crash():
    """A miscounted denominator must not take the run down with it."""
    p = Progress(2, stream=io.StringIO())
    p.tick(10)
    p.render()
    p.close()


# ------------------------------------------------------------------- streams
def test_never_writes_to_stdout(capsys):
    """stdout must stay clean: several scripts pipe JSON from it."""
    p = Progress(3)  # defaults to stderr
    p.tick()
    p.close()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_disabled_is_completely_silent():
    stream = io.StringIO()
    p = Progress(10, enabled=False, stream=stream)
    p.tick(5)
    p.close()
    assert stream.getvalue() == ""
    assert p.done == 5  # still counts, just does not draw


def test_non_tty_emits_whole_lines_not_carriage_returns():
    """Piped to a file or CI log, CR animation would produce one unreadable
    line; whole lines are the right fallback."""
    stream = io.StringIO()  # isatty() is False
    p = Progress(2, stream=stream, min_interval=0)
    p.tick()
    p.tick()
    out = stream.getvalue()
    assert "\r" not in out
    assert out.count("\n") >= 2


def test_tty_redraws_in_place():
    stream = FakeTTY()
    p = Progress(2, stream=stream, min_interval=0)
    p.tick()
    p.tick()
    assert "\r" in stream.getvalue()


def test_close_clears_the_line_and_summarises():
    stream = FakeTTY()
    p = Progress(2, stream=stream, min_interval=0)
    p.tick(2)
    p.close()
    assert "done: 2 items" in stream.getvalue()


# ------------------------------------------------------------- rate limiting
def test_redraw_is_rate_limited():
    """Drawing must not compete with the work being measured."""
    stream = io.StringIO()
    p = Progress(1000, stream=stream, min_interval=3600)  # effectively never
    for _ in range(50):
        p.tick()
    # Only the final tick may draw, and only if it completes the total.
    assert stream.getvalue().count("\n") <= 1


def test_final_tick_always_draws():
    """The bar must land on 100%, not stall at 98% because of the interval."""
    stream = io.StringIO()
    p = Progress(3, stream=stream, min_interval=3600)
    p.tick()
    p.tick()
    p.tick()  # reaches total, so it draws regardless of the interval
    assert "100.0%" in stream.getvalue()


# ------------------------------------------------------------------- labels
def test_label_appears_and_is_width_bounded():
    p = Progress(10, stream=io.StringIO())
    p.set_label("2026 British Grand Prix / Sprint Qualifying")
    assert "2026 British" in p.render()

    p.set_label("x" * 500)
    first = p.render()
    p.set_label("short")
    # Padding keeps the line a stable width so redraws do not leave debris.
    assert len(first) == len(p.render())


# --------------------------------------------------------------- heartbeat
def test_heartbeat_redraws_without_advancing():
    """Long gaps between ticks must still look alive.

    The lake build ticks once per finished session, so a five-session stage
    printed nothing for a minute -- indistinguishable from a hang.
    """
    stream = io.StringIO()
    p = Progress(100, stream=stream, min_interval=0)
    p.heartbeat()
    assert p.done == 0
    assert stream.getvalue()


def test_heartbeat_is_rate_limited_like_tick():
    stream = io.StringIO()
    p = Progress(100, stream=stream, min_interval=3600)
    for _ in range(20):
        p.heartbeat()
    assert stream.getvalue().count("\n") <= 1


def test_heartbeat_is_silent_when_disabled():
    stream = io.StringIO()
    p = Progress(100, enabled=False, stream=stream)
    p.heartbeat()
    assert stream.getvalue() == ""


def test_eta_shows_placeholder_before_the_first_tick():
    """An ETA computed from zero progress would be meaningless."""
    p = Progress(100, stream=io.StringIO())
    assert "ETA --" in p.render()
    p.tick(10)
    assert "ETA --" not in p.render()


def test_elapsed_is_shown_on_the_line():
    """With chunky ticks, elapsed is the reassurance ETA cannot give yet."""
    p = Progress(100, stream=io.StringIO())
    assert "elapsed" in p.render()


def test_weighted_ticks_move_the_bar_proportionally():
    """The lake build ticks by lap count, not session count, because sessions
    differ by an order of magnitude."""
    p = Progress(2644, stream=io.StringIO())
    p.tick(521)
    assert "19.7%" in p.render()
    p.tick(965)
    assert "56.2%" in p.render()
