import math
import os

import numpy as np
import pytest

from app.devices.faces import (
    ENROLL_MIN_SCORE,
    ENROLL_MIN_SIZE_PX,
    Face,
    FaceMatch,
    Gallery,
    Presence,
    already_known_match,
    assign,
    in_guide_region,
    pick_unknown_face,
)
from app.devices.sensor import ImpulseDetector, suggest_threshold
from app.devices.swing import Body, BodyTracker, SwingDetector

# -- faces -------------------------------------------------------------------


def unit(*xs):
    v = np.zeros(128)
    v[: len(xs)] = xs
    return v / np.linalg.norm(v)


def test_assign_gives_each_player_at_most_one_face():
    g = Gallery({1: [unit(1, 0).tolist()], 2: [unit(0, 1).tolist()]})
    faces = [unit(1, 0.1), unit(1, 0.3), unit(0.1, 1)]
    out = assign(faces, g, threshold=0.5)
    assert [o[0] for o in out] == [1, None, 2]
    # The losing face still reports its closest candidate and similarity.
    assert out[1][1] == 1 and out[1][2] > 0.9


def test_assign_below_threshold_names_nobody():
    g = Gallery({1: [unit(1, 0).tolist()]})
    ((pid, best, sim),) = assign([unit(0.2, 1)], g, threshold=0.363)
    assert pid is None and best == 1 and sim < 0.363


def test_presence_needs_k_of_n():
    p = Presence(window=5, minimum=3)
    for seen in ({1}, set(), {1}):
        p.update(seen)
    assert p.confirmed() == []
    p.update({1})
    assert p.confirmed() == [1] and p.label(1) == "3/4"


def match(pid, size=120, score=0.95):
    return FaceMatch(Face((0, 0, size, size), score, np.zeros(15)), unit(1), pid, None, None)


def test_enroll_only_with_exactly_one_good_unknown_face():
    assert pick_unknown_face([])[0] is None
    assert pick_unknown_face([match(1)])[0] is None
    assert "2 unrecognised" in pick_unknown_face([match(None), match(None)])[1]
    assert "px" in pick_unknown_face([match(None, size=ENROLL_MIN_SIZE_PX - 1)])[1]
    m, why = pick_unknown_face([match(1), match(None)])
    assert m is not None and why.startswith("exactly one")


def test_already_known_match_needs_exactly_one_recognised_face():
    assert already_known_match([]) is None
    assert already_known_match([match(None)]) is None  # unrecognised, not "already known"
    assert already_known_match([match(1), match(None)]) is None  # more than one face in view
    assert already_known_match([match(1), match(2)]) is None
    m = already_known_match([match(1)])
    assert m is not None and m.player_id == 1


def test_already_known_match_refuses_a_small_or_low_confidence_face():
    # A distant or poor-angle face shouldn't be trusted to claim someone's
    # identity even if its similarity crossed the match threshold — the same
    # bar pick_unknown_face already holds new enrollment to.
    assert already_known_match([match(1, size=ENROLL_MIN_SIZE_PX - 1)]) is None
    assert already_known_match([match(1, score=ENROLL_MIN_SCORE - 0.01)]) is None
    assert already_known_match([match(1, size=ENROLL_MIN_SIZE_PX, score=ENROLL_MIN_SCORE)]) is not None


def test_in_guide_region_only_accepts_a_face_centred_in_the_frame():
    frame_shape = (480, 640)  # h, w
    centred = Face((300, 190, 40, 100), 0.95, np.zeros(15))  # centre ~(320, 240)
    assert in_guide_region(centred, frame_shape) is True

    off_to_the_side = Face((550, 190, 40, 100), 0.95, np.zeros(15))  # centre ~(570, 240)
    assert in_guide_region(off_to_the_side, frame_shape) is False


# -- swing ---------------------------------------------------------------------


def body(wrist_x, shoulder_w=100.0, vis=1.0):
    pts = [(0.0, 0.0, 0.0)] * 33
    pts[0] = (200.0, 50.0, 1.0)  # nose
    pts[11] = (200 - shoulder_w / 2, 100.0, 1.0)
    pts[12] = (200 + shoulder_w / 2, 100.0, 1.0)
    pts[15] = (150.0, 200.0, 1.0)  # left wrist still
    pts[16] = (wrist_x, 200.0, vis)
    return Body(pts)


def run(xs, fps=30, detector=None):
    d = detector or SwingDetector(threshold=4.0)
    swings = []
    for i, x in enumerate(xs):
        s = d.update(body(x), i / fps)
        if s:
            swings.append(s)
    return swings


def test_a_fast_stroke_is_one_swing_at_peak():
    # still, then a stroke peaking at 30 px/frame = 9 shoulder-widths/s, then still
    xs = [300] * 5 + [305, 320, 350, 375, 390, 395] + [395] * 10
    (s,) = run(xs)
    assert s.wrist == "right" and math.isclose(s.speed, 9.0) and math.isclose(s.ts, 7 / 30)


def test_slow_movement_and_jitter_are_not_swings():
    assert run([300 + (i % 2) * 2 for i in range(40)]) == []  # jitter: 0.6 sw/s
    assert run([300 + i * 5 for i in range(40)]) == []  # steady 1.5 sw/s


def test_refractory_merges_ringing():
    xs = [300] * 3 + [340, 380, 382, 420, 460] + [460] * 10
    assert len(run(xs)) == 1


def test_speed_scales_with_shoulder_width():
    # Same pixel motion from a player twice as far away is twice the body-relative speed.
    d = SwingDetector(threshold=4.0)
    for i, x in enumerate([300] * 3 + [310, 320, 330, 330, 330]):
        d.update(body(x, shoulder_w=50), i / 30)
    assert d.last_swing > 0


@pytest.mark.parametrize("link_frame, expected", [(1, "face"), (-12, "track")])
def test_tracker_links_swing_to_face_owner(link_frame, expected):
    """A link from the latest face check reads "face"; an older one "track"."""
    t = BodyTracker(threshold=4.0)
    face = Face((170, 20, 60, 60), 0.95, np.zeros(15))
    lead = [300] * 20 if link_frame < 0 else []
    xs = lead + [300] * 4 + [305, 330, 370, 395, 400] + [400] * 5
    at = link_frame if link_frame >= 0 else 1
    events = []
    for i, x in enumerate(xs):
        ts = i / 30
        events += t.update([body(x)], ts, (640, 480))
        if i == at:
            t.link_faces([(7, face)], ts)
    (ev,) = events
    assert ev.player_id == 7 and ev.link == expected


def test_tracker_without_face_is_anonymous():
    t = BodyTracker(threshold=4.0)
    events = []
    for i, x in enumerate([300] * 4 + [305, 330, 370, 395, 400] + [400] * 5):
        events += t.update([body(x)], i / 30, (640, 480))
    assert events[0].player_id is None and events[0].link == "none"


# -- sensor --------------------------------------------------------------------


def signal(rate=16_000, seconds=1.0, clicks=(0.2, 0.5, 0.52), noise=0.002, seed=1):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, noise, int(rate * seconds))
    for c in clicks:
        i = int(c * rate)
        x[i : i + 40] += 0.5 * np.exp(-np.arange(40) / 8) * np.sign(np.sin(np.arange(40)))
    return x


def test_impulses_detected_once_each_with_refractory():
    det = ImpulseDetector(threshold=0.08)
    x = signal()
    ticks = []
    for i in range(0, len(x), 256):
        ticks += det.process(x[i : i + 256], 100 + i / 16_000)
    # 0.50 and 0.52 are 20 ms apart: inside the 60 ms refractory, so one tick.
    assert [round(t - 100, 2) for t, _ in ticks] == [0.2, 0.5]
    assert all(p > 0.08 for _, p in ticks)


def test_noise_floor_raises_the_bar_in_a_loud_room():
    det = ImpulseDetector(threshold=0.01, noise_factor=6)
    loud = signal(clicks=(), noise=0.05)
    for i in range(0, len(loud) - 256, 256):
        det.process(loud[i : i + 256], i / 16_000)
    assert det.level > 0.1  # the floor, not the 0.01 threshold, now decides


def test_calibration_suggests_threshold_between_noise_and_bounces():
    r = suggest_threshold(signal(clicks=()), signal(seconds=2, clicks=(0.3, 0.8, 1.3, 1.8)))
    assert r["noise_p99.9"] < r["suggested_threshold"] < r["bounce_median_peak"]


# -- real models (run when the files and a test photo are available) -----------

IMG = os.environ.get("TT_FACE_TEST_IMAGE")  # OpenCV Zoo's SFace demo.jpg


@pytest.mark.skipif(not IMG, reason="set TT_FACE_TEST_IMAGE to the OpenCV Zoo SFace demo.jpg")
def test_real_models_recognise_enrolled_person_in_group():
    import cv2

    from app.devices.faces import FaceEngine, recognise

    img = cv2.imread(IMG)
    solo, group = img[:, :512], img[:, 512:]
    eng = FaceEngine()
    (face,) = eng.detect(solo)
    g = Gallery({1: [eng.embed(solo, face).tolist()]})
    found = [m for m in recognise(eng, group, g) if m.player_id == 1]
    assert len(found) == 1 and found[0].face.box[0] > 350  # the rightmost person
