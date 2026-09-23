"""Live capture buffers for the CV/sensor phases (spec §3, phases 2–4).

Device adapters (`app/devices`: face-rec + pose per camera, the table sensor) run as
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
FRAME_STALE_S = 10.0  # a camera counts as live only if it posted a preview frame this recently

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
    # Why last_hitter came out as it did: attributed | no_sensor | no_swing_in_window
    # | unidentified. The decision log turns this + evidence into prose.
    hitter_reason: str = "no_sensor"
    evidence: dict = field(default_factory=dict)


@dataclass
class EnrollRequest:
    id: int
    camera: Side
    player_id: int
    created: float
    status: str = "pending"  # pending | done | failed


@dataclass
class RallyBuffer:
    """Events since the last point was committed (i.e. the current rally)."""

    ticks: list[float] = field(default_factory=list)
    strengths: list[float | None] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)

    def reset(self) -> None:
        self.ticks.clear()
        self.strengths.clear()
        self.hits.clear()

    def add_tick(self, ts: float, strength: float | None = None) -> None:
        self.ticks.append(ts)
        self.strengths.append(strength)

    def add_hit(self, hit: Hit) -> None:
        self.hits.append(hit)

    def last_hit_on(self, side: Side) -> Hit | None:
        return next((h for h in reversed(self.hits) if h.side is side), None)

    def latest_hit(self) -> Hit | None:
        return self.hits[-1] if self.hits else None

    def close(self) -> RallyCapture:
        swings = [{"ts": h.ts, "side": h.side.value, "player_id": h.player_id} for h in self.hits]
        if not self.ticks:
            # No sensor data: no rally length, and no point-end timestamp to
            # correlate a swing against — so never name a last hitter.
            return RallyCapture(
                rally_length=None,
                last_hitter=UNKNOWN,
                point_end_ts=None,
                hitter_reason="no_sensor",
                evidence={"ticks": 0, "swings": swings},
            )
        end = max(self.ticks)
        for sw in swings:
            sw["before_end_s"] = round(end - sw["ts"], 3)
            if sw["ts"] > end + HIT_TOLERANCE_S:
                sw["verdict"] = "after the last table contact"
            elif sw["ts"] < end - HIT_WINDOW_S:
                sw["verdict"] = f"more than {HIT_WINDOW_S}s before the last contact"
            else:
                sw["verdict"] = "in window"
        in_window = [sw for sw in swings if sw["verdict"] == "in window"]
        last = max(in_window, key=lambda sw: sw["ts"], default=None)
        if last is None:
            hitter, reason = UNKNOWN, "no_swing_in_window"
        elif last["player_id"] is None:
            hitter, reason = UNKNOWN, "unidentified"
        else:
            hitter, reason = str(last["player_id"]), "attributed"
        if last is not None:
            last["verdict"] = "chosen: latest swing in window"
        strengths = [x for x in self.strengths if x is not None]
        return RallyCapture(
            rally_length=len(self.ticks),
            last_hitter=hitter,
            point_end_ts=end,
            hitter_reason=reason,
            evidence={
                "ticks": len(self.ticks),
                "first_tick_ts": min(self.ticks),
                "last_tick_ts": end,
                "rally_duration_s": round(end - min(self.ticks), 3),
                "tick_strength_min": min(strengths) if strengths else None,
                "tick_strength_max": max(strengths) if strengths else None,
                "window_s": HIT_WINDOW_S,
                "tolerance_s": HIT_TOLERANCE_S,
                "swings": swings,
            },
        )


@dataclass
class CaptureHub:
    """Process-wide capture state. One table, so one live rally at a time."""

    rally: RallyBuffer = field(default_factory=RallyBuffer)
    # Face-rec: camera ("A"/"B", the end it is mounted at) -> player ids seen.
    detections: dict[Side, list[int]] = field(default_factory=lambda: {Side.A: [], Side.B: []})
    # Face enrollment requests for the camera workers (e.g. after a manual pick).
    enroll_requests: list[EnrollRequest] = field(default_factory=list)
    # Thresholds each device last reported, for the /decisions "rules in force".
    device_params: dict[str, dict] = field(default_factory=dict)
    # Latest preview JPEG per camera, for the live-preview panel: camera -> (bytes, ts).
    frames: dict[Side, tuple[bytes, float]] = field(default_factory=dict)
    _next_request: int = 1

    def record_frame(self, camera: Side, jpeg: bytes, ts: float) -> None:
        self.frames[camera] = (jpeg, ts)

    def camera_status(self) -> dict[Side, dict]:
        status = {}
        for side in (Side.A, Side.B):
            frame = self.frames.get(side)
            last_seen = frame[1] if frame else None
            status[side] = {
                "active": last_seen is not None and now() - last_seen < FRAME_STALE_S,
                "last_seen": last_seen,
            }
        return status

    def request_enroll(self, camera: Side, player_id: int) -> EnrollRequest:
        # One pending request per camera: a newer pick replaces an older one.
        for r in self.enroll_requests:
            if r.camera is camera and r.status == "pending":
                r.status = "failed"
        req = EnrollRequest(self._next_request, camera, player_id, now())
        self._next_request += 1
        self.enroll_requests.append(req)
        del self.enroll_requests[:-50]
        return req

    def enroll_request(self, rid: int) -> EnrollRequest | None:
        return next((r for r in self.enroll_requests if r.id == rid), None)


hub = CaptureHub()
