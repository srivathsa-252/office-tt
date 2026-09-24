"""Camera worker — run one per webcam, on the same machine as the API.

    python -m app.devices.camera --camera A --device 0
    python -m app.devices.camera --camera B --device /dev/video2
    python -m app.devices.camera --camera A --device match.mp4   # replay a recording

Every frame: pose → body tracks → swing detection → POST /api/capture/hits.
Every --face-every seconds: face recognition → presence smoothing →
POST /api/capture/detections, link faces to bodies, and serve enroll requests.
Every --preview-every seconds: a downscaled JPEG → POST /api/capture/frames,
for the live-preview panel on the scoreboard screen.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .faces import (
    ENROLL_SAME_PERSON,
    ENROLL_SAMPLES,
    MATCH_THRESHOLD,
    PRESENCE_MIN,
    PRESENCE_WINDOW,
    FaceEngine,
    FaceMatch,
    Gallery,
    Presence,
    pick_unknown_face,
    recognise,
)
from .models import DEFAULT_DIR, model_path
from .swing import SWING_SPEED, Body, BodyTracker

log = logging.getLogger("camera")
ENROLL_TIMEOUT_S = 15.0
GALLERY_REFRESH_S = 10.0
POLL_S = 1.0
PREVIEW_MAX_WIDTH = 480  # downscale before encoding, so the preview stays light on bandwidth


class Api:
    """Tiny JSON client. POSTs go through a queue so the frame loop never waits."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.q: queue.Queue = queue.Queue(maxsize=500)
        threading.Thread(target=self._drain, daemon=True).start()

    def request(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def post_async(self, path: str, body) -> None:
        try:
            self.q.put_nowait((path, body))
        except queue.Full:
            log.warning("API queue full; dropping %s", path)

    def _drain(self) -> None:
        while True:
            path, body = self.q.get()
            try:
                self.request("POST", path, body)
            except Exception as e:  # keep going; the API may be restarting
                log.warning("POST %s failed: %s", path, e)


class PoseEstimator:
    def __init__(self, models_dir: Path, num_poses: int):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        self.landmarker = vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(
                base_options=BaseOptions(
                    model_asset_path=str(model_path("pose_landmarker_lite.task", models_dir))
                ),
                running_mode=vision.RunningMode.VIDEO,
                num_poses=num_poses,
            )
        )
        self._last_ms = -1

    def bodies(self, frame_bgr, ts: float) -> list[Body]:
        import cv2

        h, w = frame_bgr.shape[:2]
        ms = max(int(ts * 1000), self._last_ms + 1)  # VIDEO mode needs increasing times
        self._last_ms = ms
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self.landmarker.detect_for_video(image, ms)
        return [
            Body([(p.x * w, p.y * h, p.visibility or 0.0) for p in lms])
            for lms in result.pose_landmarks
        ]


class FrameGrabber:
    """Reads a live camera on its own thread, always exposing the newest
    frame, and posts the live-preview JPEG right there too — so the preview
    keeps up with the camera's own frame rate even when pose/face inference
    (run from `latest()` on the main thread) can't keep pace on a slow CPU.
    Not used for file replay, which stays single-threaded and timestamp-paced
    so tuning runs stay reproducible (see CameraWorker.run)."""

    def __init__(self, cap, on_frame):
        self.cap = cap
        self.on_frame = on_frame
        self.stopped = False
        self._lock = threading.Lock()
        self._frame = None
        self._ts: float | None = None

    def start(self) -> "FrameGrabber":
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def latest(self):
        with self._lock:
            return self._frame, self._ts

    def _run(self) -> None:
        while True:
            ok, frame = self.cap.read()
            if not ok:
                self.stopped = True
                return
            ts = time.time()
            with self._lock:
                self._frame, self._ts = frame, ts
            self.on_frame(frame, ts)


@dataclass
class EnrollSession:
    request_id: int
    player_id: int | None  # None = scan-first: name comes after a successful scan
    started: float
    samples: list = field(default_factory=list)
    last_reason: str = "no face checked yet"


class CameraWorker:
    def __init__(self, args):
        self.args = args
        self.side = args.camera
        self.api = Api(args.api)
        self.faces = FaceEngine(args.models)
        self.pose = None if args.no_pose else PoseEstimator(args.models, args.max_players)
        self.tracker = BodyTracker(args.swing_speed)
        self.presence = Presence(PRESENCE_WINDOW, PRESENCE_MIN)
        self.gallery = Gallery()
        self.enroll: EnrollSession | None = None
        self._pending: list[dict] = []
        self._handled: set[int] = set()  # request ids finished here (poll may lag)
        self._lock = threading.Lock()
        # Whether to actually run pose/face analysis and post a preview right
        # now — false when there's no live match and nobody's on the setup
        # screen. Starts True so a slow first poll doesn't sit there idle.
        self.needed = True

    # -- background polling (gallery + enroll requests + needed) -------------

    def _poll_loop(self) -> None:
        last_gallery = 0.0
        while True:
            try:
                if time.time() - last_gallery >= GALLERY_REFRESH_S:
                    g = Gallery.from_api(self.api.request("GET", "/api/face-gallery"))
                    with self._lock:
                        self.gallery = g
                    last_gallery = time.time()
                pending = self.api.request(
                    "GET", f"/api/capture/enroll-requests?camera={self.side}"
                )
                with self._lock:
                    self._pending = pending
                needed = bool(self.api.request("GET", "/api/capture/camera-needed")["needed"])
                if needed != self.needed:
                    log.info("camera %s: %s", self.side, "resuming" if needed else "pausing (idle)")
                self.needed = needed
            except Exception as e:
                log.warning("poll failed: %s", e)
            time.sleep(POLL_S)

    def refresh_gallery_now(self) -> None:
        try:
            g = Gallery.from_api(self.api.request("GET", "/api/face-gallery"))
            with self._lock:
                self.gallery = g
        except Exception as e:
            log.warning("gallery refresh failed: %s", e)

    # -- per-check work ------------------------------------------------------

    def face_check(self, frame, ts: float) -> None:
        with self._lock:
            gallery = self.gallery
            pending = list(self._pending)
        matches = recognise(self.faces, frame, gallery, self.args.match_threshold)
        self.presence.update({m.player_id for m in matches if m.player_id is not None})
        confirmed = self.presence.confirmed()
        self.api.post_async(
            "/api/capture/detections",
            {
                "camera": self.side,
                "player_ids": confirmed,
                "threshold": self.args.match_threshold,
                "faces": [
                    {
                        "player_id": m.player_id if m.player_id in confirmed else None,
                        "best_player_id": m.best_player_id,
                        "similarity": m.similarity,
                        "presence": (
                            self.presence.label(m.best_player_id)
                            if m.best_player_id is not None
                            else None
                        ),
                    }
                    for m in matches
                ],
            },
        )
        self.tracker.link_faces([(m.player_id, m.face) for m in matches if m.player_id], ts)
        self.enroll_step(matches, pending, ts)

    def enroll_step(self, matches: list[FaceMatch], pending: list[dict], ts: float) -> None:
        ids = {p["id"] for p in pending if p["id"] not in self._handled}
        if self.enroll is not None and self.enroll.request_id not in ids:
            self.enroll = None  # cancelled, or replaced by a newer pick
        if self.enroll is None and ids:
            req = max((p for p in pending if p["id"] in ids), key=lambda p: p["id"])
            self.enroll = EnrollSession(req["id"], req["player_id"], ts)
        s = self.enroll
        if s is None:
            return
        m, reason = pick_unknown_face(matches)
        s.last_reason = reason
        if m is not None:
            if s.samples and float(s.samples[0] @ m.embedding) < ENROLL_SAME_PERSON:
                s.last_reason = "the unrecognised face changed between samples"
                s.samples = []
            s.samples.append(m.embedding)
        if len(s.samples) >= ENROLL_SAMPLES:
            self._handled.add(s.request_id)
            vectors = [v.tolist() for v in s.samples]
            evidence = {
                "why": f"it was picked by hand and was the only unrecognised face in "
                f"camera {self.side}'s view for {ENROLL_SAMPLES} consecutive checks",
            }
            try:
                if s.player_id is not None:
                    body = {
                        "vectors": vectors,
                        "source": f"camera {self.side}",
                        "request_id": s.request_id,
                        "evidence": evidence,
                    }
                    self.api.request("POST", f"/api/players/{s.player_id}/faces", body)
                    self.refresh_gallery_now()
                else:
                    # Scan-first: hold the samples server-side until a name
                    # comes in through POST .../register — nothing to add to
                    # the gallery yet.
                    self.api.request(
                        "POST",
                        f"/api/capture/enroll-requests/{s.request_id}/scanned",
                        {"vectors": vectors, "evidence": evidence},
                    )
            except Exception as e:
                log.warning("enroll upload failed: %s", e)
            self.enroll = None
        elif ts - s.started > ENROLL_TIMEOUT_S:
            self._handled.add(s.request_id)
            self.api.post_async(
                f"/api/capture/enroll-requests/{s.request_id}/failed",
                {"reason": f"gave up after {ENROLL_TIMEOUT_S:.0f}s — {s.last_reason}."},
            )
            self.enroll = None

    def preview_step(self, frame) -> None:
        import base64

        import cv2

        h, w = frame.shape[:2]
        if w > PREVIEW_MAX_WIDTH:
            frame = cv2.resize(frame, (PREVIEW_MAX_WIDTH, round(h * PREVIEW_MAX_WIDTH / w)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
        if not ok:
            return
        self.api.post_async(
            "/api/capture/frames",
            {"camera": self.side, "image": base64.b64encode(buf.tobytes()).decode("ascii")},
        )

    def _process_frame(self, frame, ts: float) -> None:
        """Pose → body tracks → swing detection. The expensive step, so it
        never gates the live preview (see FrameGrabber)."""
        if self.pose is None:
            return
        bodies = self.pose.bodies(frame, ts)
        for ev in self.tracker.update(bodies, ts, (frame.shape[1], frame.shape[0])):
            self.api.post_async(
                "/api/capture/hits",
                {
                    "camera": self.side,
                    "player_id": ev.player_id,
                    "ts": ev.swing.ts,
                    "evidence": {
                        "speed": round(ev.swing.speed, 2),
                        "threshold": ev.swing.threshold,
                        "wrist": ev.swing.wrist,
                        "link": ev.link,
                        "track": ev.track_id,
                    },
                },
            )

    def run(self) -> None:
        import cv2

        a = self.args
        self.api.post_async(
            "/api/capture/device-params",
            {
                "device": f"camera {self.side}",
                "params": {
                    "face_match_threshold": a.match_threshold,
                    "face_presence": f"{PRESENCE_MIN} of last {PRESENCE_WINDOW} checks",
                    "face_check_every_s": a.face_every,
                    "swing_speed_threshold": a.swing_speed if self.pose else "pose off",
                    "enroll_samples": ENROLL_SAMPLES,
                    "preview_every_s": a.preview_every,
                },
            },
        )
        threading.Thread(target=self._poll_loop, daemon=True).start()
        src = int(a.device) if str(a.device).isdigit() else a.device
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise SystemExit(f"can't open camera/video {a.device!r}")
        is_file = isinstance(src, str) and Path(src).exists()
        log.info("camera %s running (%s)", self.side, a.device)

        last_preview = -1e9

        def maybe_preview(frame, ts: float) -> None:
            nonlocal last_preview
            if not self.needed:
                return  # no live match, nobody on the setup screen — stay quiet
            if a.preview_every > 0 and ts - last_preview >= a.preview_every:
                last_preview = ts
                self.preview_step(frame)

        if is_file:
            # Deterministic, single-threaded, paced by the file's own frame
            # times — so a tuning run on recorded footage stays reproducible.
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            start, n, last_face = time.time(), 0, -1e9
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                ts = start + n / fps
                n += 1
                if a.realtime:
                    time.sleep(max(0.0, ts - time.time()))
                self._process_frame(frame, ts)
                if ts - last_face >= a.face_every:
                    last_face = ts
                    self.face_check(frame, ts)
                maybe_preview(frame, ts)
            # Let queued posts finish when replaying a file.
            while not self.api.q.empty():
                time.sleep(0.05)
            return

        # Live camera: a background thread grabs frames (and posts the
        # preview) at the camera's own pace. Pose/face inference on a slow,
        # unaccelerated CPU can take much longer than --preview-every; without
        # this split the preview would lag several seconds behind instead of
        # feeling live. The main thread processes whichever frame is newest,
        # dropping ones it can't keep up with rather than queuing them.
        grabber = FrameGrabber(cap, maybe_preview).start()
        last_face, last_ts = -1e9, None
        while True:
            frame, ts = grabber.latest()
            if frame is None or ts == last_ts:
                if grabber.stopped:
                    log.warning("camera %s: lost the feed", self.side)
                    break
                time.sleep(0.02)
                continue
            last_ts = ts
            if not self.needed:
                # Keep the device open (cheap to resume) but do no analysis:
                # near-zero CPU while idle.
                continue
            self._process_frame(frame, ts)
            if ts - last_face >= a.face_every:
                last_face = ts
                self.face_check(frame, ts)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--camera", required=True, choices=["A", "B"], help="the end it's mounted at")
    ap.add_argument("--device", default="0", help="camera index, device path, URL or video file")
    ap.add_argument(
        "--api",
        default="http://127.0.0.1:8000",
        help="not 'localhost' — on Windows, resolving it can add ~2s to every request "
        "(IPv6-then-IPv4 fallback)",
    )
    ap.add_argument("--models", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--face-every", type=float, default=0.25, help="seconds between face checks")
    ap.add_argument(
        "--preview-every",
        type=float,
        default=0.15,
        help="seconds between live-preview frames (0 disables the preview)",
    )
    ap.add_argument("--match-threshold", type=float, default=MATCH_THRESHOLD)
    ap.add_argument("--swing-speed", type=float, default=SWING_SPEED)
    ap.add_argument("--max-players", type=int, default=2, help="bodies to track (2 = doubles)")
    ap.add_argument("--no-pose", action="store_true", help="face-rec only")
    ap.add_argument("--realtime", action="store_true", help="replay files at real speed")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    CameraWorker(ap.parse_args()).run()


if __name__ == "__main__":
    main()
