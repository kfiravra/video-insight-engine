"""Recorded-vs-replayed phase walls for a replay run (the 0.3 fidelity check)."""

from __future__ import annotations

from dataclasses import dataclass

from tests.replay.cassette import Cassette
from tests.replay.driver import ReplayResult

# The 10 % gate applies to phases that matter; sub-second phases (visual
# injection) only need to stay under this absolute slack.
DEFAULT_TOLERANCE = 0.10
ABSOLUTE_SLACK_MS = 1000
TOTAL_ROW = "total (complete)"


@dataclass(frozen=True)
class PhaseRow:
    name: str
    recorded_ms: int
    replayed_ms: int | None

    @property
    def delta_ms(self) -> int | None:
        return None if self.replayed_ms is None else self.replayed_ms - self.recorded_ms

    @property
    def delta_pct(self) -> float | None:
        if self.delta_ms is None or self.recorded_ms == 0:
            return None
        return 100.0 * self.delta_ms / self.recorded_ms

    def within(self, tolerance: float) -> bool:
        if self.delta_ms is None:
            return False
        allowed = max(ABSOLUTE_SLACK_MS, tolerance * self.recorded_ms)
        return abs(self.delta_ms) <= allowed


def _scaled(value_ms: int | None, speed: float) -> int | None:
    """Replayed wall at speed 1 — sleeps scale linearly, real CPU does not."""
    if value_ms is None or speed <= 0:
        return value_ms
    return int(value_ms / speed)


def phase_rows(result: ReplayResult, cassette: Cassette) -> list[PhaseRow]:
    """One row per recorded phase plus the run total (``complete`` offset)."""
    walls = result.phase_walls()
    rows = [
        PhaseRow(name, recorded, _scaled(walls.get(name), result.speed))
        for name, recorded in cassette.recorded.phases_ms.items()
    ]
    milestones = (result.timing or {}).get("milestones", {})
    rows.append(
        PhaseRow(
            TOTAL_ROW,
            cassette.recorded.total_ms,
            _scaled(milestones.get("completeMs"), result.speed),
        )
    )
    return rows


def out_of_tolerance(rows: list[PhaseRow], tolerance: float = DEFAULT_TOLERANCE) -> list[str]:
    """Names of rows outside ``tolerance`` (or missing from the replay)."""
    return [row.name for row in rows if not row.within(tolerance)]


def _fmt_ms(value: int | None) -> str:
    return "—" if value is None else f"{value / 1000:7.1f}"


def format_table(rows: list[PhaseRow]) -> str:
    """Plain-text table: phase | recorded s | replayed s | Δ s | Δ %."""
    lines = [f"{'phase':<22} {'rec s':>7} {'replay s':>8} {'Δ s':>7} {'Δ %':>7}"]
    for row in rows:
        pct = "—" if row.delta_pct is None else f"{row.delta_pct:+6.1f}%"
        lines.append(
            f"{row.name:<22} {_fmt_ms(row.recorded_ms)} {_fmt_ms(row.replayed_ms):>8} "
            f"{_fmt_ms(row.delta_ms)} {pct:>7}"
        )
    return "\n".join(lines)
