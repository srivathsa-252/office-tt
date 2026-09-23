import pytest
from sqlalchemy import select

from app.capture import hub
from app.models import Point
from app.rules import MERCY_SHUTOUT_AT


def add_players(client, *names):
    return [client.post("/api/players", json={"name": n}).json()["id"] for n in names]


def singles(client, a, b, best_of=1, **extra):
    r = client.post(
        "/api/matches",
        json={
            "mode": "singles",
            "side_a": [a],
            "side_b": [b],
            "first_server": a,
            "first_receiver": b,
            "format": {"best_of": best_of},
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def win(client, mid, side, n=1, win_type=None):
    for _ in range(n):
        r = client.post(f"/api/matches/{mid}/points", json={"winner": side, "win_type": win_type})
        assert r.status_code == 201, r.text
    return r.json()


def test_live_scoring_flow(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    assert m["server"] == {"id": pr, "side": "A"} and m["serves_remaining"] == 5
    s = win(client, m["id"], "A", 3)
    assert s["score"] == {"A": 3, "B": 0} and s["serves_remaining"] == 2
    s = win(client, m["id"], "B", 2)
    assert s["server"]["id"] == sr and s["serves_remaining"] == 5
    assert client.get("/api/matches/live").json()["id"] == m["id"]


def test_websocket_pushes_each_point(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    with client.websocket_connect(f"/ws/matches/{m['id']}") as ws:
        assert ws.receive_json()["score"] == {"A": 0, "B": 0}
        win(client, m["id"], "B")
        assert ws.receive_json()["score"] == {"A": 0, "B": 1}


def test_undo_restores_previous_state(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    win(client, m["id"], "A", 5)
    s = client.delete(f"/api/matches/{m['id']}/points/last").json()
    assert s["score"] == {"A": 4, "B": 0} and s["server"]["id"] == pr


def test_match_close_rates_players_and_undo_reverts(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    s = win(client, m["id"], "A", MERCY_SHUTOUT_AT)  # shutout: closes the match early
    assert s["status"] == "finished" and s["winner"] == "A"
    assert win_rejected(client, m["id"])
    stats = client.get(f"/api/players/{pr}/stats").json()
    assert stats["rating"] > 1500 and stats["rating_delta"] > 0
    assert stats["win_rate"] == 1 and stats["recent_form"] == ["W"]
    assert stats["serve_win_rate"] == 1
    assert stats["history"][0]["games"] == [{"own": MERCY_SHUTOUT_AT, "opp": 0}]

    s = client.delete(f"/api/matches/{m['id']}/points/last").json()
    assert s["status"] == "live" and s["score"] == {"A": MERCY_SHUTOUT_AT - 1, "B": 0}
    stats = client.get(f"/api/players/{pr}/stats").json()
    assert stats["rating"] == 1500 and stats["matches_played"] == 0


def win_rejected(client, mid):
    return client.post(f"/api/matches/{mid}/points", json={"winner": "A"}).status_code == 409


def test_new_match_abandons_live_one_and_it_is_not_rated(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m1 = singles(client, pr, sr)
    win(client, m1["id"], "A", MERCY_SHUTOUT_AT - 1)  # stay short of the shutout close
    m2 = singles(client, pr, sr)
    assert client.get(f"/api/matches/{m1['id']}").json()["status"] == "abandoned"
    assert client.get("/api/matches/live").json()["id"] == m2["id"]
    assert client.get(f"/api/players/{pr}/stats").json()["matches_played"] == 0


def test_doubles_rates_pairs_and_reports_synergy(client):
    pr, ab, sr, jt = add_players(client, "Praneeth", "Abin", "Sri", "Jithin")
    r = client.post(
        "/api/matches",
        json={
            "mode": "doubles",
            "side_a": [pr, ab],
            "side_b": [sr, jt],
            "first_server": pr,
            "first_receiver": sr,
            "format": {"best_of": 1},
        },
    )
    m = r.json()
    s = win(client, m["id"], "A", 5)
    assert (s["server"]["id"], s["receiver"]["id"]) == (sr, pr)
    win(client, m["id"], "A", MERCY_SHUTOUT_AT - 5)  # shutout closes the match early
    stats = client.get(f"/api/players/{pr}/stats").json()
    assert stats["rating"] == 1500  # doubles rates the pair, not the individual
    syn = stats["synergy"][0]
    assert syn["partner"]["name"] == "Abin" and syn["matches"] == 1
    assert syn["rating"] > 1500 and syn["vs_solo_avg"] > 0
    assert syn["predicted_win_rate"] == pytest.approx(0.5)
    assert syn["synergy"] == pytest.approx(0.5)


def test_capture_pipeline_feeds_point(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    client.post("/api/capture/hits", json={"camera": "A", "player_id": pr, "ts": 100.0})
    client.post("/api/capture/ticks", json={"ts": 100.3})
    client.post("/api/capture/hits", json={"camera": "B", "player_id": None, "ts": 100.8})
    client.post("/api/capture/ticks", json={"ts": 101.1})
    s = client.get(f"/api/matches/{m['id']}").json()
    assert s["last_hit"]["A"]["player"]["name"] == "Praneeth"
    assert s["last_hit"]["B"] == {"camera": "B", "player": None}
    s = win(client, m["id"], "A")
    assert s["last_hit"] == {"A": None, "B": None}  # new rally
    assert hub.rally.ticks == []
    with client.session_factory() as db:
        point = db.scalars(select(Point)).one()
    # Latest swing within the window of the last contact was unidentified.
    assert point.rally_length == 2 and point.last_hitter == "unknown"


def test_point_records_rally_and_hitter(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    client.post("/api/capture/hits", json={"camera": "B", "player_id": sr, "ts": 50.0})
    for t in (50.2, 50.9):
        client.post("/api/capture/ticks", json={"ts": t})
    win(client, m["id"], "B", win_type="smash")
    win(client, m["id"], "B")  # no sensor data for this rally
    with client.session_factory() as db:
        p1, p2 = db.scalars(select(Point).order_by(Point.id)).all()
    assert (p1.rally_length, p1.last_hitter, p1.win_type) == (2, str(sr), "smash")
    assert (p2.rally_length, p2.last_hitter, p2.win_type) == (None, "unknown", None)


def test_detections_roundtrip(client):
    pr, ab = add_players(client, "Praneeth", "Abin")
    assert (
        client.post(
            "/api/capture/detections", json={"camera": "A", "player_ids": [pr, ab, pr]}
        ).status_code
        == 204
    )
    d = client.get("/api/capture/detections").json()
    assert [p["name"] for p in d["A"]] == ["Praneeth", "Abin"] and d["B"] == []


def test_preview_frame_roundtrip(client):
    import base64

    jpeg = b"\xff\xd8\xff\xe0not-really-a-jpeg"
    r = client.post(
        "/api/capture/frames",
        json={"camera": "A", "image": base64.b64encode(jpeg).decode("ascii")},
    )
    assert r.status_code == 204

    got = client.get("/api/capture/preview/A")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/jpeg"
    assert got.content == jpeg

    assert client.get("/api/capture/preview/B").status_code == 404


def test_preview_frame_rejects_bad_base64(client):
    r = client.post("/api/capture/frames", json={"camera": "A", "image": "not base64!!"})
    assert r.status_code == 422


def test_camera_status_reflects_which_cameras_are_posting(client):
    import base64

    status = client.get("/api/capture/camera-status").json()
    assert status["A"] == {"active": False, "last_seen": None}
    assert status["B"] == {"active": False, "last_seen": None}

    client.post(
        "/api/capture/frames",
        json={"camera": "A", "image": base64.b64encode(b"jpeg").decode("ascii")},
    )
    status = client.get("/api/capture/camera-status").json()
    assert status["A"]["active"] is True and status["A"]["last_seen"] is not None
    assert status["B"]["active"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "singles", "side_a": [1], "side_b": [1], "first_server": 1, "first_receiver": 1},
        {"mode": "singles", "side_a": [1], "side_b": [99], "first_server": 1, "first_receiver": 99},
        {"mode": "doubles", "side_a": [1], "side_b": [2], "first_server": 1, "first_receiver": 2},
        {
            "mode": "singles",
            "side_a": [1],
            "side_b": [2],
            "first_server": 1,
            "first_receiver": 2,
            "format": {"best_of": 2},
        },
    ],
)
def test_bad_match_setup_422(client, body):
    add_players(client, "P1", "P2")
    assert client.post("/api/matches", json=body).status_code == 422
