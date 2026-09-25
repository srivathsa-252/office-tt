"""Swing detection from pose landmarks — pure logic, no camera or model.

A stroke is a burst of wrist speed. Speed is measured in shoulder-widths per
second, so it doesn't depend on how far the player stands from the camera. Each
body in view is tracked across frames and carries the player its face was last
recognised as, so a swing can be attributed to a person.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# MediaPipe pose landmark indices.
NOSE, L_SHOULDER, R_SHOULDER, L_WRIST, R_WRIST = 0, 11, 12, 15, 16

# Placeholders — tune on real footage (see README).
SWING_SPEED = 4.0  # shoulder-widths/s the wrist must exceed to start a swing
SWING_END_RATIO = 0.5  # the swing ends when speed falls below this × SWING_SPEED
SWING_MAX_S = 0.4  # a burst longer than this is cut and reported
REFRACTORY_S = 0.3  # ignore new bursts this soon after a reported swing
MIN_VISIBILITY = 0.5
MAX_GAP_S = 0.2  # frames further apart than this reset the speed estimate
TRACK_MAX_JUMP = 0.25  # max torso move between frames (fraction of frame) to keep a track
TRACK_TTL_S = 1.0  # drop tracks unseen for this long
FACE_LINK_TTL_S = 3.0  # how long a face-to-body link stays trusted


@dataclass
class Body:
    """One person's landmarks in pixels: (x, y, visibility) per landmark."""

    points: list[tuple[float, float, float]]

    def point(self, i: int) -> tuple[float, float] | None:
        x, y, v = self.points[i]
        return (x, y) if v >= MIN_VISIBILITY else None

    def shoulder_width(self) -> float | None:
        a, b = self.point(L_SHOULDER), self.point(R_SHOULDER)
        if a is None or b is None:
            return None
        w = math.dist(a, b)
        return w if w > 1 else None

    def torso(self) -> tuple[float, float] | None:
        a, b = self.point(L_SHOULDER), self.point(R_SHOULDER)
        if a is None or b is None:
            return None
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


@dataclass
class Swing:
    ts: float  # time of peak wrist speed
    speed: float  # peak, shoulder-widths/s
    wrist: str  # "left" | "right"
    threshold: float


@dataclass
class SwingDetector:
    threshold: float = SWING_SPEED
    prev: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # wrist -> x,y,ts
    burst: Swing | None = None
    burst_start: float = 0.0
    last_swing: float = -1e9

    def update(self, body: Body, ts: float) -> Swing | None:
        sw = body.shoulder_width()
        speeds: dict[str, float] = {}
        for name, idx in (("left", L_WRIST), ("right", R_WRIST)):
            p = body.point(idx)
            last = self.prev.get(name)
            if p is not None:
                self.prev[name] = (p[0], p[1], ts)
            else:
                self.prev.pop(name, None)
            if p is None or last is None or sw is None:
                continue
            dt = ts - last[2]
            if 0 < dt <= MAX_GAP_S:
                speeds[name] = math.dist(p, last[:2]) / dt / sw
        if not speeds:
            return self._end_burst(force=True)
        wrist = max(speeds, key=speeds.get)
        speed = speeds[wrist]

        if self.burst is None:
            if speed >= self.threshold and ts - self.last_swing >= REFRACTORY_S:
                self.burst = Swing(ts, speed, wrist, self.threshold)
                self.burst_start = ts
            return None
        if speed > self.burst.speed:
            self.burst = Swing(ts, speed, wrist, self.threshold)
        if speed < self.threshold * SWING_END_RATIO or ts - self.burst_start >= SWING_MAX_S:
            return self._end_burst()
        return None

    def _end_burst(self, force: bool = False) -> Swing | None:
        swing, self.burst = self.burst, None
        if swing is not None:
            self.last_swing = swing.ts
        return swing


@dataclass
class Track:
    id: int
    torso: tuple[float, float]
    last_seen: float
    detector: SwingDetector
    body: Body | None = None
    player_id: int | None = None
    linked_at: float = -1e9


@dataclass
class SwingEvent:
    swing: Swing
    player_id: int | None
    link: str  # "face" (linked this face check) | "track" (earlier link) | "none"
    track_id: int


@dataclass
class BodyTracker:
    threshold: float = SWING_SPEED
    tracks: list[Track] = field(default_factory=list)
    _next: int = 1

    def update(
        self, bodies: list[Body], ts: float, frame_size: tuple[int, int]
    ) -> list[SwingEvent]:
        w, h = frame_size
        max_jump = TRACK_MAX_JUMP * max(w, h)
        unmatched = [b for b in bodies if b.torso() is not None]
        # Greedy nearest-torso association, closest pairs first.
        pairs = sorted(
            (math.dist(t.torso, b.torso()), ti, bi)
            for ti, t in enumerate(self.tracks)
            for bi, b in enumerate(unmatched)
        )
        used_t, used_b = set(), set()
        for d, ti, bi in pairs:
            if d > max_jump or ti in used_t or bi in used_b:
                continue
            used_t.add(ti)
            used_b.add(bi)
            t = self.tracks[ti]
            t.torso, t.last_seen, t.body = unmatched[bi].torso(), ts, unmatched[bi]
        for bi, b in enumerate(unmatched):
            if bi not in used_b:
                self.tracks.append(
                    Track(self._next, b.torso(), ts, SwingDetector(self.threshold), b)
                )
                self._next += 1
        self.tracks = [t for t in self.tracks if ts - t.last_seen <= TRACK_TTL_S]

        events = []
        for t in self.tracks:
            if t.last_seen != ts or t.body is None:
                continue
            swing = t.detector.update(t.body, ts)
            if swing is None:
                continue
            fresh = ts - t.linked_at <= FACE_LINK_TTL_S
            pid = t.player_id if fresh else None
            link = "none" if pid is None else ("face" if ts - t.linked_at < 0.5 else "track")
            events.append(SwingEvent(swing, pid, link, t.id))
        return events

    def link_faces(self, faces: list[tuple[int, object]], ts: float) -> None:
        """faces = [(player_id, Face)] for recognised faces. A body belongs to the
        face whose (padded) box contains its nose."""
        for t in self.tracks:
            if t.body is None or t.last_seen != ts:
                continue
            nose = t.body.point(NOSE)
            if nose is None:
                continue
            owners = [pid for pid, face in faces if face.contains(*nose)]
            if len(owners) == 1:
                t.player_id, t.linked_at = owners[0], ts
