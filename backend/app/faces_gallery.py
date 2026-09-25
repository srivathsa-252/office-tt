"""Face-embedding maths shared by the API and the camera workers (no OpenCV here)."""

from __future__ import annotations

import math

EMBEDDING_DIM = 128  # SFace


def normalise(v: list[float]) -> list[float]:
    if len(v) != EMBEDDING_DIM:
        raise ValueError(f"face embeddings have {EMBEDDING_DIM} values, got {len(v)}")
    n = math.sqrt(sum(x * x for x in v))
    if not n or not math.isfinite(n):
        raise ValueError("face embedding has zero or invalid length")
    return [x / n for x in v]
