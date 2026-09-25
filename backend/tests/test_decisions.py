from sqlalchemy import select

from app.models import Decision

from .test_api import add_players, singles, win


def kinds(client, match_id=None):
    with client.session_factory() as db:
        q = select(Decision).order_by(Decision.id)
        if match_id is not None:
            q = q.where(Decision.match_id == match_id)
        return [(d.kind, d.summary) for d in db.scalars(q)]


def test_every_point_explains_serve_and_rally(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    win(client, m["id"], "A", 5)
    log = kinds(client, m["id"])
    assert log[0][0] == "match.created" and "Praneeth serves first to Sri" in log[0][1]
    serves = [s for k, s in log if k == "serve"]
    assert serves[0] == "Praneeth keeps serve: 1 of 5 served this turn, 4 serves left."
    assert serves[4].startswith("Praneeth has served 5 of 5 this turn, so serve passes to Sri")
    assert sum(k == "rally" for k, _ in log) == 5  # no sensor: one rally line per point


def test_deuce_game_check_and_match_close_are_explained(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    for _ in range(20):
        win(client, m["id"], "A")
        win(client, m["id"], "B")
    win(client, m["id"], "A")
    win(client, m["id"], "A")
    log = kinds(client, m["id"])
    summaries = [s for _, s in log]
    assert any("both sides are on 20 or more (deuce)" in s for s in summaries)
    assert any(
        s.startswith("No game yet at 21–20: Side A has 21+ but leads by only 1") for s in summaries
    )
    assert any(k == "game.won" and "22–20" in s for k, s in log)
    assert any(k == "match.won" for k, _ in log)
    ratings = [s for k, s in log if k == "rating.update"]
    assert len(ratings) == 2 and "50% win chance" in ratings[0]


def test_rally_with_sensor_names_last_hitter(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    client.post(
        "/api/capture/hits",
        json={
            "camera": "B",
            "player_id": sr,
            "ts": 50.0,
            "evidence": {"speed": 7.5, "threshold": 4.0, "wrist": "right", "link": "face"},
        },
    )
    for t in (49.9, 50.3):
        client.post("/api/capture/ticks", json={"ts": t, "strength": 0.3})
    win(client, m["id"], "B")
    log = dict((k, s) for k, s in kinds(client, m["id"]))
    assert log["swing"].startswith("Swing on Side B by Sri: right wrist peaked at 7.5")
    assert log["rally.length"].startswith("Rally length 2")
    assert log["rally.last_hitter"].startswith("Last hitter Sri: latest swing, 0.30 s")


def test_undo_is_explained(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    win(client, m["id"], "A", 3)
    client.delete(f"/api/matches/{m['id']}/points/last")
    (undo,) = [s for k, s in kinds(client, m["id"]) if k == "undo"]
    assert "Undid the point to Side A at 3–0" in undo and "score 2–0" in undo


def test_face_detections_logged_only_on_change(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    body = {
        "camera": "A",
        "player_ids": [pr],
        "threshold": 0.363,
        "faces": [
            {"player_id": pr, "similarity": 0.71, "presence": "4/5"},
            {"player_id": None, "best_player_id": sr, "similarity": 0.2},
        ],
    }
    for _ in range(3):
        client.post("/api/capture/detections", json=body)
    logs = [s for k, s in kinds(client) if k == "face.detections"]
    assert len(logs) == 1
    assert "Praneeth (similarity 0.71 ≥ 0.36; seen in 4/5" in logs[0]
    assert "closest is Sri at 0.20, below the 0.36 threshold" in logs[0]


def test_enrollment_flow(client):
    (pr,) = add_players(client, "Praneeth")
    rid = client.post("/api/capture/enroll-requests", json={"camera": "A", "player_id": pr}).json()[
        "id"
    ]
    assert [r["id"] for r in client.get("/api/capture/enroll-requests?camera=A").json()] == [rid]
    assert client.get("/api/capture/enroll-requests?camera=B").json() == []
    vec = [0.0] * 128
    vec[0] = 3.0
    r = client.post(
        f"/api/players/{pr}/faces", json={"vectors": [vec], "request_id": rid, "source": "camera A"}
    )
    assert r.json()["samples"] == 1
    assert client.get("/api/capture/enroll-requests?camera=A").json() == []
    (g,) = client.get("/api/face-gallery").json()
    assert g["player_id"] == pr and g["vectors"][0][0] == 1.0  # normalised
    assert client.post(f"/api/players/{pr}/faces", json={"vectors": [[1.0] * 3]}).status_code == 422
    log = [k for k, _ in kinds(client)]
    assert "face.enroll_requested" in log and "face.enrolled" in log


def test_enroll_failure_is_logged(client):
    (pr,) = add_players(client, "Praneeth")
    rid = client.post("/api/capture/enroll-requests", json={"camera": "B", "player_id": pr}).json()[
        "id"
    ]
    client.post(
        f"/api/capture/enroll-requests/{rid}/failed", json={"reason": "2 unrecognised faces"}
    )
    assert any(k == "face.enroll_failed" and "2 unrecognised faces" in s for k, s in kinds(client))


def test_decisions_page_renders_all_views(client):
    pr, sr = add_players(client, "Praneeth", "Sri")
    m = singles(client, pr, sr)
    # Interleaved so neither side is shut out at 0 — style tags need >= 20 points played.
    for _ in range(19):
        win(client, m["id"], "A")
        win(client, m["id"], "B")
    win(client, m["id"], "A")
    win(client, m["id"], "A")  # 21-19: game and match won
    total_points = 19 * 2 + 2
    client.post(
        "/api/capture/device-params", json={"device": "sensor", "params": {"threshold": 0.08}}
    )
    page = client.get("/decisions").text
    assert "Decision log" in page and "Praneeth keeps serve" in page and "Evidence" in page
    match_page = client.get(f"/decisions?match={m['id']}").text
    assert f"Point {total_points}" in match_page and "Rating" in match_page
    rules = client.get("/decisions?view=rules").text
    assert "0.363" in rules and "A1→B1, B1→A1, A2→B2, B2→A2" in rules
    assert "running with" in rules and "threshold</b> 0.08" in rules
    players = client.get("/decisions?view=players").text
    assert "server-reliant" in players and "tags need at least 20" not in players
    assert client.get("/decisions?kind=serve").text.count('class="badge') == total_points
    assert client.get("/decisions?view=nope").status_code == 422
