import time

from app.capture import DETECTIONS_STALE_S, FRAME_STALE_S, UNKNOWN, CaptureHub, Hit, RallyBuffer
from app.rules import Side


def test_no_sensor_means_no_rally_length_and_unknown_hitter():
    r = RallyBuffer()
    r.add_hit(Hit(10.0, Side.A, 1))
    out = r.close()
    assert out.rally_length is None and out.last_hitter == UNKNOWN


def test_last_hitter_is_latest_swing_before_point_end():
    r = RallyBuffer()
    for ts in (10.0, 10.6, 11.2, 11.9):
        r.add_tick(ts)
    r.add_hit(Hit(10.4, Side.A, 1))
    r.add_hit(Hit(11.0, Side.B, 2))
    r.add_hit(Hit(11.7, Side.A, 1))
    out = r.close()
    assert out.rally_length == 4 and out.last_hitter == "1" and out.point_end_ts == 11.9


def test_swing_too_early_is_not_attributed():
    r = RallyBuffer()
    r.add_tick(20.0)
    r.add_hit(Hit(17.0, Side.A, 1))
    assert r.close().last_hitter == UNKNOWN


def test_unidentified_swing_stays_unknown():
    r = RallyBuffer()
    r.add_tick(5.0)
    r.add_hit(Hit(4.8, Side.B, None))
    assert r.close().last_hitter == UNKNOWN


def test_camera_status_starts_inactive_with_no_frames():
    status = CaptureHub().camera_status()
    assert status[Side.A] == {"active": False, "last_seen": None, "source": None}
    assert status[Side.B] == {"active": False, "last_seen": None, "source": None}


def test_camera_status_active_only_while_recent():
    hub = CaptureHub()
    fresh = time.time()
    hub.record_frame(Side.A, b"jpeg-bytes", fresh)
    status = hub.camera_status()
    assert status[Side.A]["active"] is True and status[Side.A]["last_seen"] == fresh
    assert status[Side.B]["active"] is False

    hub.record_frame(Side.A, b"jpeg-bytes", fresh - FRAME_STALE_S - 1)
    assert hub.camera_status()[Side.A]["active"] is False


def test_live_detections_starts_empty():
    assert CaptureHub().live_detections(Side.A) == ([], [])


def test_stale_detections_are_treated_as_no_longer_known():
    # A worker that pauses or crashes just stops posting — its last-reported
    # roster (e.g. someone who's since walked away) shouldn't be trusted
    # forever just because nothing ever overwrote it.
    hub = CaptureHub()
    fresh = time.time()
    evidence = [{"player_id": 7, "best_player_id": 7, "similarity": 0.9, "presence": "3/5"}]
    hub.record_detections(Side.A, [7], evidence, ts=fresh)
    assert hub.live_detections(Side.A) == ([7], evidence)

    hub.record_detections(Side.A, [7], evidence, ts=fresh - DETECTIONS_STALE_S - 1)
    assert hub.live_detections(Side.A) == ([], [])
