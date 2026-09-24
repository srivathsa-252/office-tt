"""HTTP + WebSocket API. Single process, single table."""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import db as dbmod
from .capture import Hit, hub, now
from .decisions import explain_detections, explain_swing, record
from .explain_page import register as register_decisions_page
from .faces_gallery import normalise
from .models import FaceEmbedding, Match, Player
from .rules import Format, Mode, Side
from .service import (
    ConflictError,
    create_match,
    delete_match,
    end_match,
    live_match,
    match_state,
    player_ref,
    score_point,
    undo_last_point,
)
from .stats import player_stats

# Whether tapping the chess-clock bar opens the Confirm-point screen to
# capture win_type (spec §3: optional). Off = one tap scores immediately.
CAPTURE_WIN_TYPE = os.environ.get("TT_CAPTURE_WIN_TYPE", "1") != "0"

# The built frontend (`npm run build`). When present, the API serves it too, so the
# whole app is one origin on one port — no Vite dev server needed in production.
STATIC_DIR = Path(
    os.environ.get("TT_STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist")
)


# -- request bodies ----------------------------------------------------------


class PlayerIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    face_embedding_ref: str | None = None


class FormatIn(BaseModel):
    points_to_win: int = Field(21, ge=1)
    win_margin: int = Field(2, ge=1)
    serves_per_turn: int = Field(5, ge=1)
    best_of: int = Field(3, ge=1)


class MatchIn(BaseModel):
    mode: Mode
    side_a: list[int]
    side_b: list[int]
    first_server: int
    first_receiver: int
    format: FormatIn = FormatIn()


class PointIn(BaseModel):
    winner: Side
    win_type: Literal["smash", "fault", "net", "out"] | None = None


class FaceEvidence(BaseModel):
    player_id: int | None = None  # who it was matched to, if anyone
    similarity: float | None = None  # cosine similarity of the best gallery match
    best_player_id: int | None = None  # the closest player, even if below threshold
    presence: str | None = None  # e.g. "4/5": matched in 4 of the last 5 checks


class DetectionsIn(BaseModel):
    camera: Side  # the end the camera is mounted at == the side it faces
    player_ids: list[int]
    faces: list[FaceEvidence] = []
    threshold: float | None = None


class TickIn(BaseModel):
    ts: float | None = None
    strength: float | None = None  # impulse peak, in the sensor's own units


class HitIn(BaseModel):
    camera: Side
    player_id: int | None = None
    ts: float | None = None
    # Swing detector evidence: speed, threshold, wrist, how the player was linked.
    evidence: dict = {}


class DeviceParamsIn(BaseModel):
    device: str = Field(min_length=1, max_length=40)  # "camera A", "sensor"
    params: dict


class FrameIn(BaseModel):
    camera: Side
    image: str  # base64-encoded JPEG
    ts: float | None = None


class FacesIn(BaseModel):
    vectors: list[list[float]] = Field(min_length=1)
    source: str = Field("api", max_length=40)
    request_id: int | None = None
    evidence: dict = {}
    photo: str | None = None  # base64 JPEG crop from the scan


class EnrollRequestIn(BaseModel):
    camera: Side
    # Omit to scan first and ask for a name only once the scan succeeds.
    player_id: int | None = None


class EnrollFailedIn(BaseModel):
    reason: str = Field(max_length=300)


class EnrollScannedIn(BaseModel):
    vectors: list[list[float]] = Field(min_length=1)
    evidence: dict = {}
    photo: str | None = None  # base64 JPEG crop from the scan


class EnrollAlreadyKnownIn(BaseModel):
    player_id: int
    similarity: float | None = None


class EnrollRegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def create_app(session_factory: sessionmaker | None = None, init: bool = True) -> FastAPI:
    factory = session_factory or dbmod.SessionLocal

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if init:
            dbmod.init_db(factory.kw["bind"])
        yield

    app = FastAPI(title="Office TT", lifespan=lifespan)
    lock = asyncio.Lock()  # serialises writes: two taps can't race
    subscribers: dict[int, set[WebSocket]] = defaultdict(set)

    def get_db():
        with factory() as session:
            yield session

    def get_match(db: Session, match_id: int) -> Match:
        match = db.get(Match, match_id)
        if match is None:
            raise HTTPException(404, "match not found")
        return match

    async def broadcast(db: Session, match: Match) -> dict:
        state = match_state(db, hub, match)
        for ws in list(subscribers[match.id]):
            try:
                await ws.send_json(state)
            except Exception:
                subscribers[match.id].discard(ws)
        return state

    async def broadcast_live(db: Session) -> None:
        if (match := live_match(db)) is not None:
            await broadcast(db, match)

    # -- config / players --------------------------------------------------

    @app.get("/api/config")
    def config():
        f = Format()
        return {
            "capture_win_type": CAPTURE_WIN_TYPE,
            "default_format": {
                "points_to_win": f.points_to_win,
                "win_margin": f.win_margin,
                "serves_per_turn": f.serves_per_turn,
                "deuce_serve_rotation": f.deuce_serve_rotation,
                "deuce_trigger": f.deuce_trigger,
                "best_of": f.best_of,
            },
        }

    @app.get("/api/players")
    def list_players(db: Session = Depends(get_db)):
        return [player_ref(p) for p in db.scalars(select(Player).order_by(Player.name))]

    @app.post("/api/players", status_code=201)
    def add_player(body: PlayerIn, db: Session = Depends(get_db)):
        player = Player(name=body.name.strip(), face_embedding_ref=body.face_embedding_ref)
        db.add(player)
        db.commit()
        return player_ref(player)

    @app.get("/api/players/{player_id}/stats")
    def get_player_stats(player_id: int, db: Session = Depends(get_db)):
        player = db.get(Player, player_id)
        if player is None:
            raise HTTPException(404, "player not found")
        return player_stats(db, player)

    @app.get("/api/players/{player_id}/photo", response_class=Response)
    def get_player_photo(player_id: int, db: Session = Depends(get_db)):
        # The JPEG crop taken when enrollment last succeeded for them (see
        # devices/camera.py's crop_face_jpeg) — not a live view, a snapshot.
        player = db.get(Player, player_id)
        if player is None or not player.face_photo:
            raise HTTPException(404, "no photo for this player")
        return Response(content=player.face_photo, media_type="image/jpeg")

    @app.delete("/api/players/{player_id}", status_code=204)
    def delete_player(player_id: int, db: Session = Depends(get_db)):
        player = db.get(Player, player_id)
        if player is None:
            raise HTTPException(404, "player not found")
        # Refused once they've been in any match (live, finished or
        # abandoned) — deleting them would break that match's own player
        # references and, for a finished one, everyone else's history.
        matches = db.scalars(select(Match)).all()
        if any(player_id in m.side_a_players + m.side_b_players for m in matches):
            raise HTTPException(409, "player has match history and can't be deleted")
        db.query(FaceEmbedding).filter_by(player_id=player_id).delete()
        record(
            db,
            "player.deleted",
            f"{player.name} was deleted (id {player_id}) — they'd never played a match.",
            {"player_id": player_id, "name": player.name},
        )
        db.delete(player)
        db.commit()

    # -- matches -----------------------------------------------------------

    @app.get("/api/matches")
    def list_matches(db: Session = Depends(get_db)):
        matches = db.scalars(select(Match).order_by(Match.id.desc())).all()
        ids = {pid for m in matches for pid in m.side_a_players + m.side_b_players}
        players = {p.id: p for p in db.scalars(select(Player).where(Player.id.in_(ids)))} if ids else {}

        def side(ids_: list[int]) -> list[dict]:
            return [player_ref(players[pid]) for pid in ids_ if pid in players]

        def games_won(m: Match) -> dict | None:
            if not m.game_scores:
                return None
            return {
                "A": sum(g["A"] > g["B"] for g in m.game_scores),
                "B": sum(g["B"] > g["A"] for g in m.game_scores),
            }

        return [
            {
                "id": m.id,
                "mode": m.mode,
                "status": m.status,
                "side_a": side(m.side_a_players),
                "side_b": side(m.side_b_players),
                "best_of": m.format.get("best_of"),
                "winner": m.winner,
                "game_scores": m.game_scores,
                "games_won": games_won(m),
                "points_played": len(m.points),
                "created_at": m.created_at.isoformat(),
                "closed_at": m.closed_at.isoformat() if m.closed_at else None,
            }
            for m in matches
        ]

    @app.post("/api/matches", status_code=201)
    async def new_match(body: MatchIn, db: Session = Depends(get_db)):
        if body.format.best_of % 2 == 0:
            raise HTTPException(422, "best_of must be odd")
        async with lock:
            try:
                match = create_match(
                    db,
                    hub,
                    mode=body.mode,
                    side_a=body.side_a,
                    side_b=body.side_b,
                    first_server=body.first_server,
                    first_receiver=body.first_receiver,
                    fmt=Format(**body.format.model_dump()),
                )
            except ValueError as e:
                raise HTTPException(422, str(e))
            return match_state(db, hub, match)

    @app.get("/api/matches/live")
    def get_live(db: Session = Depends(get_db)):
        match = live_match(db)
        if match is None:
            raise HTTPException(404, "no live match")
        return match_state(db, hub, match)

    @app.get("/api/matches/{match_id}")
    def get_state(match_id: int, db: Session = Depends(get_db)):
        return match_state(db, hub, get_match(db, match_id))

    @app.post("/api/matches/{match_id}/points", status_code=201)
    async def add_point(match_id: int, body: PointIn, db: Session = Depends(get_db)):
        async with lock:
            match = get_match(db, match_id)
            try:
                score_point(db, hub, match, body.winner, body.win_type)
            except ConflictError as e:
                raise HTTPException(409, str(e))
            return await broadcast(db, match)

    @app.delete("/api/matches/{match_id}/points/last")
    async def undo_point(match_id: int, db: Session = Depends(get_db)):
        async with lock:
            match = get_match(db, match_id)
            try:
                undo_last_point(db, hub, match)
            except ConflictError as e:
                raise HTTPException(409, str(e))
            return await broadcast(db, match)

    @app.post("/api/matches/{match_id}/end")
    async def end_match_route(match_id: int, db: Session = Depends(get_db)):
        async with lock:
            match = get_match(db, match_id)
            try:
                end_match(db, hub, match)
            except ConflictError as e:
                raise HTTPException(409, str(e))
            return await broadcast(db, match)

    @app.delete("/api/matches/{match_id}", status_code=204)
    async def delete_match_route(match_id: int, db: Session = Depends(get_db)):
        async with lock:
            match = get_match(db, match_id)
            try:
                delete_match(db, match)
            except ConflictError as e:
                raise HTTPException(409, str(e))

    @app.websocket("/ws/matches/{match_id}")
    async def match_ws(ws: WebSocket, match_id: int):
        await ws.accept()
        with factory() as db:
            match = db.get(Match, match_id)
            if match is None:
                await ws.close(code=4404)
                return
            await ws.send_json(match_state(db, hub, match))
        subscribers[match_id].add(ws)
        try:
            while True:
                await ws.receive_text()  # keepalive; the client never sends commands
        except WebSocketDisconnect:
            pass
        finally:
            subscribers[match_id].discard(ws)

    # -- capture ingest (phases 2–4: device adapters post here) ------------

    @app.get("/api/capture/detections")
    def get_detections(db: Session = Depends(get_db)):
        # The setup screen polls this every second while open — used as the
        # signal that someone's actively setting up a match (see camera_needed).
        hub.mark_setup_seen()
        out = {}
        for side in (Side.A, Side.B):
            ids, evidence = hub.live_detections(side)
            players = [db.get(Player, pid) for pid in ids]
            out[side.value] = {
                "players": [player_ref(p) for p in players if p is not None],
                # A face was seen but matched nobody confidently — "new face"
                # for the setup screen's register-prompt, not necessarily an
                # actual stranger (could just be mid-presence-smoothing).
                "unknown_present": any(f.get("player_id") is None for f in evidence),
            }
        return out

    @app.post("/api/capture/detections", status_code=204)
    def post_detections(body: DetectionsIn, db: Session = Depends(get_db)):
        ids = list(dict.fromkeys(body.player_ids))
        # Logged only when who's recognised changes (it's posted several times a second).
        changed = set(ids) != set(hub.detections.get(body.camera, []))
        hub.record_detections(body.camera, ids, [f.model_dump() for f in body.faces])
        if changed:
            record(db, "face.detections", *explain_detections(db, body), match_id=None)
            db.commit()

    @app.get("/api/capture/camera-needed")
    def get_camera_needed(db: Session = Depends(get_db)):
        # A camera worker polls this to decide whether to actively capture and
        # analyse, or pause (no live match, nobody on the setup screen).
        return {"needed": hub.camera_needed(live_match(db) is not None)}

    @app.post("/api/capture/ticks", status_code=204)
    async def post_tick(body: TickIn):
        async with lock:
            hub.rally.add_tick(body.ts if body.ts is not None else now(), body.strength)

    @app.post("/api/capture/hits", status_code=204)
    async def post_hit(body: HitIn, db: Session = Depends(get_db)):
        async with lock:
            match = live_match(db)
            if match is None:
                return  # warm-up swings between matches mean nothing
            side = body.camera
            player_id = body.player_id
            dropped = None
            if player_id is not None:
                if player_id in match.side_a_players:
                    side = Side.A
                elif player_id in match.side_b_players:
                    side = Side.B
                else:
                    dropped, player_id = player_id, None  # not in this match
            ts = body.ts if body.ts is not None else now()
            hub.rally.add_hit(Hit(ts=ts, side=side, player_id=player_id))
            summary, detail = explain_swing(db, body, side, player_id, dropped)
            record(db, "swing", summary, {**detail, "ts": ts}, match_id=match.id)
            db.commit()
            await broadcast_live(db)

    @app.post("/api/capture/device-params", status_code=204)
    def post_device_params(body: DeviceParamsIn, db: Session = Depends(get_db)):
        if hub.device_params.get(body.device) != body.params:
            hub.device_params[body.device] = body.params
            settings = ", ".join(f"{k} = {v}" for k, v in body.params.items())
            record(db, "device.config", f"{body.device} started with {settings}.", body.params)
            db.commit()

    @app.post("/api/capture/frames", status_code=204)
    def post_frame(body: FrameIn):
        try:
            jpeg = base64.b64decode(body.image, validate=True)
        except binascii.Error:
            raise HTTPException(422, "image must be base64-encoded")
        hub.record_frame(body.camera, jpeg, body.ts if body.ts is not None else now())

    @app.get("/api/capture/preview/{camera}", response_class=Response)
    def get_preview(camera: Side):
        frame = hub.frames.get(camera)
        if frame is None:
            raise HTTPException(404, "no preview frame yet")
        jpeg, _ts = frame
        return Response(content=jpeg, media_type="image/jpeg")

    @app.get("/api/capture/stream/{camera}")
    async def stream_preview(camera: Side, request: Request):
        # MJPEG: one held-open connection, each new frame pushed as it lands.
        # An <img> renders it continuously — no client polling, no "refresh
        # every N ms" ceiling on how live it can look. Exactly what the
        # worker's latest POST /api/capture/frames put in the hub; a stalled
        # camera just freezes here (camera-status is what flags that).
        if camera not in hub.frames:
            raise HTTPException(404, "no preview frame yet")

        async def frames():
            sent: bytes | None = None
            while True:
                if await request.is_disconnected():
                    return
                current = hub.frames.get(camera)
                if current is not None and current[0] != sent:
                    sent = current[0]
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(sent)).encode()
                        + b"\r\n\r\n"
                        + sent
                        + b"\r\n"
                    )
                await asyncio.sleep(0.05)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/capture/camera-status")
    def get_camera_status():
        return {side.value: info for side, info in hub.camera_status().items()}

    # -- face gallery + enrollment ------------------------------------------

    @app.get("/api/face-gallery")
    def face_gallery(db: Session = Depends(get_db)):
        rows = db.scalars(select(FaceEmbedding).order_by(FaceEmbedding.player_id)).all()
        gallery: dict[int, list] = defaultdict(list)
        for r in rows:
            gallery[r.player_id].append(r.vector)
        return [{"player_id": pid, "vectors": v} for pid, v in gallery.items()]

    @app.post("/api/players/{player_id}/faces", status_code=201)
    def add_faces(player_id: int, body: FacesIn, db: Session = Depends(get_db)):
        player = db.get(Player, player_id)
        if player is None:
            raise HTTPException(404, "player not found")
        try:
            vectors = [normalise(v) for v in body.vectors]
        except ValueError as e:
            raise HTTPException(422, str(e))
        for v in vectors:
            db.add(FaceEmbedding(player_id=player_id, vector=v, source=body.source))
        if body.photo:
            try:
                player.face_photo = base64.b64decode(body.photo, validate=True)
            except binascii.Error:
                raise HTTPException(422, "photo must be base64-encoded")
        req = hub.enroll_request(body.request_id) if body.request_id else None
        if req is not None:
            req.status = "done"
        db.flush()
        total = db.query(FaceEmbedding).filter_by(player_id=player_id).count()
        why = f"; {body.evidence['why']}" if body.evidence.get("why") else ""
        record(
            db,
            "face.enrolled",
            f"Saved {len(vectors)} face sample(s) for {player.name} from {body.source}{why}. "
            f"{player.name} now has {total} sample(s); a face matches them if its similarity "
            "to any one of them clears the camera's threshold.",
            {**body.evidence, "samples_added": len(vectors), "samples_total": total},
        )
        db.commit()
        return {"player_id": player_id, "samples": total}

    @app.delete("/api/players/{player_id}/faces", status_code=204)
    def clear_faces(player_id: int, db: Session = Depends(get_db)):
        db.query(FaceEmbedding).filter_by(player_id=player_id).delete()
        player = db.get(Player, player_id)
        if player is not None:
            player.face_photo = None  # the snapshot belongs to the face data being cleared
        db.commit()

    @app.post("/api/capture/enroll-requests", status_code=201)
    def request_enroll(body: EnrollRequestIn, db: Session = Depends(get_db)):
        if body.player_id is not None:
            player = db.get(Player, body.player_id)
            if player is None:
                raise HTTPException(404, "player not found")
            summary = (
                f"{player.name} was picked by hand for Side {body.camera.value}, so camera "
                f"{body.camera.value} is asked to learn their face."
            )
        else:
            summary = (
                f"Camera {body.camera.value} is asked to scan a new face — a name will be "
                "asked for once the scan succeeds."
            )
        req = hub.request_enroll(body.camera, body.player_id)
        record(
            db,
            "face.enroll_requested",
            summary + " It only does so if exactly one face in view is unrecognised — "
            "otherwise it can't tell which face is theirs.",
            {"request_id": req.id},
        )
        db.commit()
        return {"id": req.id}

    @app.get("/api/capture/enroll-requests")
    def pending_enrolls(camera: Side):
        return [
            {"id": r.id, "player_id": r.player_id, "created": r.created}
            for r in hub.enroll_requests
            if r.camera is camera and r.status == "pending"
        ]

    @app.get("/api/capture/enroll-requests/{rid}")
    def enroll_request_status(rid: int, db: Session = Depends(get_db)):
        req = hub.enroll_request(rid)
        if req is None:
            raise HTTPException(404, "no such request")
        matched = db.get(Player, req.matched_player_id) if req.matched_player_id else None
        return {
            "id": req.id,
            "camera": req.camera.value,
            "player_id": req.player_id,
            "status": req.status,
            "reason": req.last_reason,
            "matched_player": player_ref(matched) if matched else None,
        }

    @app.post("/api/capture/enroll-requests/{rid}/failed", status_code=204)
    def enroll_failed(rid: int, body: EnrollFailedIn, db: Session = Depends(get_db)):
        req = hub.enroll_request(rid)
        if req is None or req.status != "pending":
            raise HTTPException(404, "no such pending request")
        req.status = "failed"
        req.last_reason = body.reason
        who = db.get(Player, req.player_id).name if req.player_id is not None else "the new face"
        record(
            db,
            "face.enroll_failed",
            f"Camera {req.camera.value} didn't learn {who}'s face: {body.reason}",
            {"request_id": rid},
        )
        db.commit()

    @app.post("/api/capture/enroll-requests/{rid}/scanned", status_code=204)
    def enroll_scanned(rid: int, body: EnrollScannedIn, db: Session = Depends(get_db)):
        # The camera worker's terminal step for a scan-first (player_id is
        # None) request: samples captured, held here until /register names them.
        req = hub.enroll_request(rid)
        if req is None or req.status != "pending" or req.player_id is not None:
            raise HTTPException(404, "no such pending scan-first request")
        try:
            vectors = [normalise(v) for v in body.vectors]
        except ValueError as e:
            raise HTTPException(422, str(e))
        photo = None
        if body.photo:
            try:
                photo = base64.b64decode(body.photo, validate=True)
            except binascii.Error:
                raise HTTPException(422, "photo must be base64-encoded")
        hub.mark_scanned(rid, vectors, body.evidence, photo)
        record(
            db,
            "face.scanned",
            f"Camera {req.camera.value} finished scanning a new face ({len(vectors)} "
            "sample(s)) — waiting for a name.",
            {"request_id": rid, **body.evidence},
        )
        db.commit()

    @app.post("/api/capture/enroll-requests/{rid}/already-known", status_code=204)
    def enroll_already_known(rid: int, body: EnrollAlreadyKnownIn, db: Session = Depends(get_db)):
        # The camera worker's fast path for a scan-first request: the one face
        # in view already confidently matches an existing player, so ask "are
        # you already them?" instead of scanning them in as someone new.
        req = hub.enroll_request(rid)
        if req is None or req.status != "pending" or req.player_id is not None:
            raise HTTPException(404, "no such pending scan-first request")
        player = db.get(Player, body.player_id)
        if player is None:
            raise HTTPException(404, "player not found")
        hub.mark_already_known(rid, body.player_id, body.similarity)
        sim = f" (similarity {body.similarity:.2f})" if body.similarity is not None else ""
        record(
            db,
            "face.already_known",
            f"Camera {req.camera.value}'s scan already matches {player.name}{sim} — asking "
            "whether that's who this is, instead of scanning them in as someone new.",
            {"request_id": rid, "player_id": body.player_id, "similarity": body.similarity},
        )
        db.commit()

    @app.post("/api/capture/enroll-requests/{rid}/register", status_code=201)
    def enroll_register(rid: int, body: EnrollRegisterIn, db: Session = Depends(get_db)):
        req = hub.enroll_request(rid)
        if req is None or req.status != "scanned" or req.vectors is None:
            raise HTTPException(409, "this scan isn't ready to be named yet")
        player = Player(name=body.name.strip(), face_photo=req.photo)
        db.add(player)
        db.flush()
        for v in req.vectors:
            db.add(FaceEmbedding(player_id=player.id, vector=v, source=f"camera {req.camera.value}"))
        total = len(req.vectors)
        why = f"; {req.evidence['why']}" if req.evidence and req.evidence.get("why") else ""
        record(
            db,
            "face.enrolled",
            f"Registered {player.name} from a camera {req.camera.value} scan{why}. "
            f"{player.name} now has {total} sample(s); a face matches them if its similarity "
            "to any one of them clears the camera's threshold.",
            {**(req.evidence or {}), "samples_added": total, "samples_total": total, "request_id": rid},
        )
        req.status = "done"
        req.vectors = None  # no need to keep face vectors/photo in memory longer than this
        req.photo = None
        db.commit()
        return player_ref(player)

    register_decisions_page(app, get_db)
    if (STATIC_DIR / "index.html").exists():
        mount_frontend(app, STATIC_DIR)
    return app


def mount_frontend(app: FastAPI, dist: Path) -> None:
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    # Client-side routes (/setup, /live/3, /players/1) all load the SPA shell.
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "ws/")):
            raise HTTPException(404)
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(dist.resolve()):
            return FileResponse(file)
        return FileResponse(dist / "index.html")


app = create_app()
