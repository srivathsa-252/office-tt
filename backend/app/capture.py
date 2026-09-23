"""Live capture buffers for the CV/sensor phases (spec §3, phases 2–4).

Device adapters (face-rec per camera, MediaPipe pose, the table sensor) run as
separate processes on the SAME machine and post events to the API with
timestamps from the same clock. This module only buffers those events and
correlates them when a point is scored. Nothing here guesses: if the evidence
isn't there, the answer is `None` / "unknown".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .rules import Side

# Placeholders — tune once the sensor and cameras are on the table.
HIT_WINDOW_S = 1.5  # a swing must precede the point-end contact by at most this
HIT_TOLERANCE_S = 0.05  # allow for camera/sensor timestamp jitter

UNKNOWN = "unknown"


def now() -> float:
    return time.time()


@dataclass(frozen=True)
class Hit:
    ts: float
    side: Side
    player_id: int | None  # None = swing seen but player not identified


@dataclass(frozen=True)
class RallyCapture:
    rally_length: int | None
    last_hitter: str  # player id as str, or "unknown"
    point_end_ts: float | None


@dataclass
class RallyBuffer:
    """Events since the last point was committed (i.e. the current rally)."""

    ticks: list[float] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)

    def reset(self) -> None:
        self.ticks.clear()
        self.hits.clear()

    def add_tick(self, ts: float) -> None:
        self.ticks.append(ts)

    def add_hit(self, hit: Hit) -> None:
        self.hits.append(hit)

    def last_hit_on(self, side: Side) -> Hit | None:
        return next((h for h in reversed(self.hits) if h.side is side), None)

    def latest_hit(self) -> Hit | None:
        return self.hits[-1] if self.hits else None

    def close(self) -> RallyCapture:
        if not self.ticks:
            # No sensor data: no rally length, and no point-end timestamp to
            # correlate a swing against — so never name a last hitter.
            return RallyCapture(rally_length=None, last_hitter=UNKNOWN, point_end_ts=None)
        end = max(self.ticks)
        candidates = [h for h in self.hits if end - HIT_WINDOW_S <= h.ts <= end + HIT_TOLERANCE_S]
        last = max(candidates, key=lambda h: h.ts, default=None)
        hitter = str(last.player_id) if last and last.player_id is not None else UNKNOWN
        return RallyCapture(rally_length=len(self.ticks), last_hitter=hitter, point_end_ts=end)


@dataclass
class CaptureHub:
    """Process-wide capture state. One table, so one live rally at a time."""

    rally: RallyBuffer = field(default_factory=RallyBuffer)
    # Face-rec: camera ("A"/"B", the end it is mounted at) -> player ids seen.
    detections: dict[Side, list[int]] = field(default_factory=lambda: {Side.A: [], Side.B: []})


hub = CaptureHub()
