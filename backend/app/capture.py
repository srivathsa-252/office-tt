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
# The setup screen polls detections every 1s while open (MatchSetup.tsx); treat
# that as "someone's actively setting up a match" for this long after the last
# poll. Generous on purpose: a browser tab backgrounded even briefly (e.g.
# alt-tabbing to type a message) gets throttled and can easily miss a couple
# of polls — 3s was tight enough that this paused the camera mid-registration,
# silently stalling face-sample accumulation with no visible cause.
SETUP_HEARTBEAT_STALE_S = 15.0
# A worker posts detections ~4x/second (--face-every) while active. If it
# pauses (camera-needed false) or crashes, it simply stops posting — without
# an expiry, its last-known roster would otherwise be trusted forever, e.g.
# reporting someone as recognised long after they've left the camera.
DETECTIONS_STALE_S = 3.0

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
    # None = scan-first: capture a face before anyone has typed a name, and
    # attach it to a brand-new player once one is given (see /register below).
    player_id: int | None
    created: float
    status: str = "pending"  # pending | scanned | already_known | done | failed
    # Set once status is "scanned": the captured, not-yet-attached samples.
    vectors: list[list[float]] | None = None
    evidence: dict | None = None
    photo: bytes | None = None  # a JPEG crop from the scan, for Player.face_photo
    last_reason: str | None = None  # set on failure, for the UI to show why
    # Set once status is "already_known": a scan-first request found the face
    # already confidently matches this existing player — the UI asks "are you
    # already them?" instead of scanning them in as a brand-new person.
    matched_player_id: int | None = None
    matched_similarity: float | None = None


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
    # Latest per-face evidence a camera reported (spec §3 device API), including
    # faces present but not confirmed to any player — for "new face, register?".
    face_evidence: dict[Side, list[dict]] = field(default_factory=lambda: {Side.A: [], Side.B: []})
    # When each camera last posted detections — see live_detections/DETECTIONS_STALE_S.
    detections_seen: dict[Side, float] = field(default_factory=dict)
    # Last time the setup screen polled detections — see SETUP_HEARTBEAT_STALE_S.
    setup_seen: float | None = None
    _next_request: int = 1

    def record_frame(self, camera: Side, jpeg: bytes, ts: float) -> None:
        self.frames[camera] = (jpeg, ts)

    def record_detections(
        self, camera: Side, ids: list[int], evidence: list[dict], ts: float | None = None
    ) -> None:
        self.detections[camera] = ids
        self.face_evidence[camera] = evidence
        self.detections_seen[camera] = ts if ts is not None else now()

    def live_detections(self, camera: Side) -> tuple[list[int], list[dict]]:
        """A camera's last-reported roster and per-face evidence — or empty if
        it hasn't posted in DETECTIONS_STALE_S. A worker that's paused or has
        crashed simply stops posting; without this, its last-known roster
        (e.g. "Sri" from minutes ago) would be trusted forever instead of
        being treated as no-longer-known."""
        seen = self.detections_seen.get(camera)
        if seen is None or now() - seen >= DETECTIONS_STALE_S:
            return [], []
        return self.detections.get(camera, []), self.face_evidence.get(camera, [])

    def camera_status(self) -> dict[Side, dict]:
        status = {}
        for side in (Side.A, Side.B):
            frame = self.frames.get(side)
            last_seen = frame[1] if frame else None
            params = self.device_params.get(f"camera {side.value}")
            status[side] = {
                "active": last_seen is not None and now() - last_seen < FRAME_STALE_S,
                "last_seen": last_seen,
                "source": params.get("source") if params else None,
            }
        return status

    def mark_setup_seen(self) -> None:
        self.setup_seen = now()

    def camera_needed(self, live_match_exists: bool) -> bool:
        """Whether a camera worker should be actively capturing/analysing right
        now: a match is live, or the setup screen is currently open (inferred
        from its own detections poll — see mark_setup_seen)."""
        setup_active = self.setup_seen is not None and now() - self.setup_seen < SETUP_HEARTBEAT_STALE_S
        return live_match_exists or setup_active

    def request_enroll(self, camera: Side, player_id: int | None) -> EnrollRequest:
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

    def mark_scanned(
        self, rid: int, vectors: list[list[float]], evidence: dict, photo: bytes | None = None
    ) -> EnrollRequest | None:
        req = self.enroll_request(rid)
        if req is not None:
            req.status = "scanned"
            req.vectors = vectors
            req.evidence = evidence
            req.photo = photo
        return req

    def mark_already_known(
        self, rid: int, player_id: int, similarity: float | None
    ) -> EnrollRequest | None:
        req = self.enroll_request(rid)
        if req is not None:
            req.status = "already_known"
            req.matched_player_id = player_id
            req.matched_similarity = similarity
        return req


hub = CaptureHub()
