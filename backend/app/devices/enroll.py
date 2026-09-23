"""Add a player's face to the gallery.

    python -m app.devices.enroll --player 3 --image sri1.jpg sri2.jpg
    python -m app.devices.enroll --player 3 --device 0          # live: look at the camera

Each photo (or camera frame) must contain exactly one face. The app can also
learn a face by itself: pick a player by hand on the setup screen and the
camera enrols them if they're the only unrecognised face in view.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from .camera import Api
from .faces import ENROLL_MIN_SCORE, ENROLL_MIN_SIZE_PX, ENROLL_SAMPLES, FaceEngine
from .models import DEFAULT_DIR


def single_face(engine: FaceEngine, frame):
    faces = [f for f in engine.detect(frame) if f.score >= ENROLL_MIN_SCORE]
    if len(faces) != 1:
        return None, f"{len(faces)} confident faces (need exactly 1)"
    f = faces[0]
    if min(f.box[2], f.box[3]) < ENROLL_MIN_SIZE_PX:
        return None, f"face under {ENROLL_MIN_SIZE_PX}px"
    return f, "ok"


def main() -> None:
    import cv2

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--player", type=int, required=True)
    ap.add_argument("--image", nargs="*", type=Path, default=[])
    ap.add_argument("--device", help="camera index/path for live capture")
    ap.add_argument("--samples", type=int, default=ENROLL_SAMPLES)
    ap.add_argument("--api", default="http://127.0.0.1:8000")  # not "localhost": see camera.py
    ap.add_argument("--models", type=Path, default=DEFAULT_DIR)
    a = ap.parse_args()
    engine = FaceEngine(a.models)
    vectors, source = [], "image"

    for path in a.image:
        frame = cv2.imread(str(path))
        if frame is None:
            print(f"skip {path}: can't read")
            continue
        face, why = single_face(engine, frame)
        print(f"{'use ' if face else 'skip'} {path}: {why}")
        if face:
            vectors.append(engine.embed(frame, face).tolist())

    if a.device is not None:
        source = f"camera {a.device}"
        cap = cv2.VideoCapture(int(a.device) if a.device.isdigit() else a.device)
        deadline = time.time() + 30
        while len(vectors) < a.samples and time.time() < deadline:
            ok, frame = cap.read()
            if not ok:
                break
            face, why = single_face(engine, frame)
            if face:
                vectors.append(engine.embed(frame, face).tolist())
                print(f"sample {len(vectors)}/{a.samples}")
                time.sleep(0.3)  # spread samples over small head movements

    if not vectors:
        raise SystemExit("no usable face found — nothing enrolled")
    res = Api(a.api).request(
        "POST",
        f"/api/players/{a.player}/faces",
        {"vectors": vectors, "source": source, "evidence": {"why": "enrolled with the CLI"}},
    )
    print(f"enrolled: player {res['player_id']} now has {res['samples']} sample(s)")


if __name__ == "__main__":
    main()
