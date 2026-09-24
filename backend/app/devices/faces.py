"""Face recognition, built on two pretrained OpenCV Zoo models:

- YuNet finds faces (box, 5 landmarks, confidence).
- SFace turns an aligned face crop into a 128-d embedding; two photos of the same
  person have a high cosine similarity.

Everything after that is ours: the gallery match, the one-face-per-player
assignment, smoothing over time, and deciding when it's safe to learn a new face.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .models import DEFAULT_DIR, model_path

# SFace's published cosine threshold for "same person" (OpenCV docs: 0.363).
MATCH_THRESHOLD = 0.363
DETECT_SCORE = 0.8  # YuNet confidence to accept a face at all
# Presence smoothing: a player counts as on this side if matched in K of the last N checks.
PRESENCE_WINDOW = 5
PRESENCE_MIN = 3
# Ignore a detected face this small everywhere, not just while enrolling — it's
# someone in the background or passing through, not actually at the table.
# Lower than ENROLL_MIN_SIZE_PX (80): merely counting as "present" can tolerate
# a smaller/farther face than trusting it enough to learn from or match by name.
PRESENCE_MIN_SIZE_PX = 50
# Enrollment: only learn from a face that's clearly a face and big enough to be detailed.
ENROLL_MIN_SCORE = 0.9
ENROLL_MIN_SIZE_PX = 80
ENROLL_SAMPLES = 5
ENROLL_SAME_PERSON = 0.5  # new samples must match the first one at least this well
# The scan wizard's guide oval, as a fraction of the frame — mirrors
# GUIDE_WIDTH_PCT / GUIDE_ASPECT in frontend/src/components/RegisterFace.tsx.
# Kept in sync by hand (different languages, no shared constant); if one
# changes, change the other. Only faces centred here count while scanning —
# see in_guide_region.
GUIDE_WIDTH_FRAC = 0.42
GUIDE_HEIGHT_FRAC = GUIDE_WIDTH_FRAC / 0.75  # aspect 3:4, portrait


@dataclass
class Face:
    box: tuple[float, float, float, float]  # x, y, w, h (pixels)
    score: float
    row: np.ndarray  # YuNet's raw 15-value row; SFace needs it to align the crop

    def contains(self, x: float, y: float, margin: float = 0.2) -> bool:
        bx, by, bw, bh = self.box
        mx, my = bw * margin, bh * margin
        return bx - mx <= x <= bx + bw + mx and by - my <= y <= by + bh + my


@dataclass
class FaceMatch:
    face: Face
    embedding: np.ndarray
    player_id: int | None  # assigned player, or None if unrecognised
    best_player_id: int | None  # closest gallery player even if below threshold
    similarity: float | None  # similarity to best_player_id


class FaceEngine:
    def __init__(self, models_dir: Path = DEFAULT_DIR, detect_score: float = DETECT_SCORE):
        import cv2

        self._cv2 = cv2
        self.detector = cv2.FaceDetectorYN.create(
            str(model_path("face_detection_yunet_2023mar.onnx", models_dir)),
            "",
            (320, 320),
            detect_score,
            0.3,
            5000,
        )
        self.recognizer = cv2.FaceRecognizerSF.create(
            str(model_path("face_recognition_sface_2021dec.onnx", models_dir)), ""
        )

    def detect(self, frame: np.ndarray) -> list[Face]:
        h, w = frame.shape[:2]
        self.detector.setInputSize((w, h))
        _, rows = self.detector.detect(frame)
        if rows is None:
            return []
        return [Face(box=tuple(float(v) for v in r[:4]), score=float(r[14]), row=r) for r in rows]

    def embed(self, frame: np.ndarray, face: Face) -> np.ndarray:
        aligned = self.recognizer.alignCrop(frame, face.row)
        feat = self.recognizer.feature(aligned).flatten().astype(np.float64)
        return feat / np.linalg.norm(feat)


class Gallery:
    """player_id -> matrix of that player's normalised embeddings."""

    def __init__(self, entries: dict[int, list[list[float]]] | None = None):
        self.entries = {
            pid: np.asarray(vecs, dtype=np.float64) for pid, vecs in (entries or {}).items() if vecs
        }

    @classmethod
    def from_api(cls, rows: list[dict]) -> "Gallery":
        return cls({r["player_id"]: r["vectors"] for r in rows})

    def similarities(self, emb: np.ndarray) -> dict[int, float]:
        # Best match against any of the player's samples.
        return {pid: float(np.max(m @ emb)) for pid, m in self.entries.items()}


def assign(
    embeddings: list[np.ndarray], gallery: Gallery, threshold: float = MATCH_THRESHOLD
) -> list[tuple[int | None, int | None, float | None]]:
    """Match faces to players, each player to at most one face: take the most
    similar (face, player) pairs first. Returns (player, best_player, similarity)."""
    sims = [gallery.similarities(e) for e in embeddings]
    pairs = sorted(
        ((s, i, pid) for i, row in enumerate(sims) for pid, s in row.items()), reverse=True
    )
    assigned: dict[int, tuple[int, float]] = {}
    used: set[int] = set()
    for s, i, pid in pairs:
        if s < threshold:
            break
        if i in assigned or pid in used:
            continue
        assigned[i] = (pid, s)
        used.add(pid)
    out = []
    for i, row in enumerate(sims):
        if i in assigned:
            pid, s = assigned[i]
            out.append((pid, pid, s))
        elif row:
            pid = max(row, key=row.get)
            out.append((None, pid, row[pid]))
        else:
            out.append((None, None, None))
    return out


def recognise(
    engine: FaceEngine, frame: np.ndarray, gallery: Gallery, threshold: float = MATCH_THRESHOLD
) -> list[FaceMatch]:
    faces = [f for f in engine.detect(frame) if min(f.box[2], f.box[3]) >= PRESENCE_MIN_SIZE_PX]
    embs = [engine.embed(frame, f) for f in faces]
    return [
        FaceMatch(f, e, pid, best, sim)
        for f, e, (pid, best, sim) in zip(faces, embs, assign(embs, gallery, threshold))
    ]


@dataclass
class Presence:
    """Smooths per-check matches so one bad frame doesn't add or drop a player."""

    window: int = PRESENCE_WINDOW
    minimum: int = PRESENCE_MIN
    history: deque = field(default_factory=deque)

    def update(self, player_ids: set[int]) -> None:
        self.history.append(set(player_ids))
        while len(self.history) > self.window:
            self.history.popleft()

    def count(self, pid: int) -> int:
        return sum(pid in h for h in self.history)

    def confirmed(self) -> list[int]:
        seen = set().union(*self.history) if self.history else set()
        return sorted(p for p in seen if self.count(p) >= self.minimum)

    def label(self, pid: int) -> str:
        return f"{self.count(pid)}/{len(self.history)}"


def in_guide_region(face: Face, frame_shape: tuple[int, int, int] | tuple[int, int]) -> bool:
    """Whether a face is centred inside the scan wizard's guide oval, in the
    actual frame being analysed — not just anywhere in its wide field of
    view. A face outside it is ignored for scanning, the same way the guide
    itself claims to work, instead of only dimming it in the preview."""
    h, w = frame_shape[:2]
    x, y, fw, fh = face.box
    fx, fy = x + fw / 2, y + fh / 2
    gw, gh = w * GUIDE_WIDTH_FRAC, h * GUIDE_HEIGHT_FRAC
    return abs(fx - w / 2) <= gw / 2 and abs(fy - h / 2) <= gh / 2


def _good_quality(m: FaceMatch) -> bool:
    return m.face.score >= ENROLL_MIN_SCORE and min(m.face.box[2], m.face.box[3]) >= ENROLL_MIN_SIZE_PX


def already_known_match(matches: list[FaceMatch]) -> FaceMatch | None:
    """The one face in view, if it's already confidently matched to an
    existing player — so a scan-first registration can ask 'are you already
    them?' instead of scanning them in as a second, separate person. Held to
    the same quality bar as enrolling a new face (score, size): a small or
    uncertain detection shouldn't be trusted to claim someone's identity any
    more than it's trusted to learn a new one — that gap let a distant,
    poor-angle face get matched to the wrong existing player."""
    if len(matches) == 1 and matches[0].player_id is not None and _good_quality(matches[0]):
        return matches[0]
    return None


def pick_unknown_face(matches: list[FaceMatch]) -> tuple[FaceMatch | None, str]:
    """The one face that's safe to enrol, or why there isn't one."""
    unknown = [m for m in matches if m.player_id is None]
    if not matches:
        return None, "no face in view"
    if not unknown:
        return None, "every face in view is already recognised as someone"
    if len(unknown) > 1:
        return None, f"{len(unknown)} unrecognised faces in view — can't tell which is theirs"
    m = unknown[0]
    if m.face.score < ENROLL_MIN_SCORE:
        return None, f"face confidence {m.face.score:.2f} is below {ENROLL_MIN_SCORE}"
    if min(m.face.box[2], m.face.box[3]) < ENROLL_MIN_SIZE_PX:
        return None, f"face is under {ENROLL_MIN_SIZE_PX}px — too far away for a good sample"
    return m, "exactly one unrecognised face in view"
