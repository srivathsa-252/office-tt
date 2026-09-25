"""Suggest a MATCH_THRESHOLD calibrated to this gallery's own faces, instead
of trusting SFace's generic published value (0.363) - no retraining, just
statistics on the embeddings already collected through normal enrollment.

    python -m app.devices.calibrate_threshold
    python -m app.devices.calibrate_threshold --api http://127.0.0.1:8000

Compares every pair of samples belonging to the SAME player ("same person")
against every pair belonging to DIFFERENT players ("different person"). A
safe threshold sits in the gap between the two; if they overlap, no single
threshold is clean and the report says so - the real fix then is more/varied
enrollment samples per player, not moving the number.

Needs at least 2 players enrolled, one of them with >=2 samples, to say
anything. Reads the gallery over the API (GET /api/face-gallery, /api/players)
rather than the database directly, like the other devices/ tools.
"""

from __future__ import annotations

import argparse
import itertools
import json
import urllib.request

import numpy as np

from .faces import MATCH_THRESHOLD


def _get(api: str, path: str):
    with urllib.request.urlopen(api.rstrip("/") + path, timeout=10) as r:
        return json.loads(r.read())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    a = ap.parse_args()

    names = {p["id"]: p["name"] for p in _get(a.api, "/api/players")}
    gallery = {
        row["player_id"]: [np.asarray(v, dtype=np.float64) for v in row["vectors"]]
        for row in _get(a.api, "/api/face-gallery")
    }

    total = sum(len(v) for v in gallery.values())
    print(f"Gallery: {len(gallery)} player(s), {total} sample(s) total.\n")
    for pid, vecs in gallery.items():
        print(f"  {names.get(pid, f'#{pid}')}: {len(vecs)} sample(s)")
    print()

    same = [
        float(x @ y) for vecs in gallery.values() for x, y in itertools.combinations(vecs, 2)
    ]
    diff = [
        float(x @ y)
        for (pa, va), (pb, vb) in itertools.combinations(gallery.items(), 2)
        for x, y in itertools.product(va, vb)
    ]

    if not same:
        print(
            "Not enough same-player pairs (need >=2 samples for at least one "
            "player) to calibrate - enrol a couple more samples per player "
            "and re-run."
        )
        return
    if not diff:
        print("Only one player enrolled - nothing to separate them from. Calibration needs >=2 players.")
        return

    same_a, diff_a = np.array(same), np.array(diff)
    print(
        f"Same-person similarity:      min {same_a.min():.3f}  mean {same_a.mean():.3f}  "
        f"max {same_a.max():.3f}  (n={len(same_a)})"
    )
    print(
        f"Different-person similarity: min {diff_a.min():.3f}  mean {diff_a.mean():.3f}  "
        f"max {diff_a.max():.3f}  (n={len(diff_a)})"
    )
    print(f"Current MATCH_THRESHOLD = {MATCH_THRESHOLD}\n")

    gap = same_a.min() - diff_a.max()
    if gap > 0:
        suggested = (same_a.min() + diff_a.max()) / 2
        print(
            f"Clean separation: every same-person pair scores above every "
            f"different-person pair (gap {gap:.3f}). Suggested threshold: "
            f"{suggested:.3f} (midpoint of the gap)."
        )
        if diff_a.max() < MATCH_THRESHOLD <= same_a.min():
            print(f"Current threshold {MATCH_THRESHOLD} already sits inside that safe range - no change needed.")
        else:
            print(f"Current threshold {MATCH_THRESHOLD} sits outside that safe range - consider updating it.")
    else:
        # No cut is perfectly clean - report the threshold that misclassifies
        # the fewest pairs, so at least the tradeoff is explicit.
        candidates = np.unique(np.concatenate([same_a, diff_a]))
        best_t, best_err = MATCH_THRESHOLD, None
        for t in candidates:
            errors = int((same_a < t).sum() + (diff_a >= t).sum())
            if best_err is None or errors < best_err:
                best_t, best_err = float(t), errors
        print(
            f"Overlap: some different-person pairs score higher than some "
            f"same-person pairs (worst overlap {-gap:.3f}) - no threshold is "
            f"perfectly safe with this gallery yet. Best achievable here: "
            f"{best_t:.3f} ({best_err}/{len(same_a) + len(diff_a)} pairs "
            "misclassified). More/varied enrollment samples per player is "
            "the real fix - moving the threshold alone trades false accepts "
            "for false rejects, it doesn't remove the overlap."
        )


if __name__ == "__main__":
    main()
