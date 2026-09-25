"""Pretrained model files: download once, verify by SHA-256.

python -m app.devices.models [--dir models]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import urllib.request
from pathlib import Path

DEFAULT_DIR = Path(os.environ.get("TT_MODELS_DIR", Path(__file__).resolve().parents[2] / "models"))

# name -> (url, sha256)
MODELS = {
    # OpenCV Zoo, YuNet face detector (MIT).
    "face_detection_yunet_2023mar.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
        "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    # OpenCV Zoo, SFace face recognizer, 128-d embeddings (Apache-2.0).
    "face_recognition_sface_2021dec.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
        "face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
    # MediaPipe pose landmarker (lite), multi-person capable (Apache-2.0).
    "pose_landmarker_lite.task": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
        "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a",
    ),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_path(name: str, directory: Path = DEFAULT_DIR) -> Path:
    path = Path(directory) / name
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run: python -m app.devices.models")
    return path


def ensure_models(directory: Path = DEFAULT_DIR) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in MODELS.items():
        path = directory / name
        if path.exists() and sha256(path) == digest:
            print(f"ok       {name}")
            continue
        print(f"fetching {name}")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        if sha256(tmp) != digest:
            tmp.unlink()
            raise RuntimeError(f"{name}: checksum mismatch — refusing to use it")
        tmp.replace(path)
        print(f"ok       {name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ensure_models(ap.parse_args().dir)
