"""HTTP + WebSocket API. Single process, single table."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
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


class FacesIn(BaseModel):
    vectors: list[list[float]] = Field(min_length=1)
    source: str = Field("api", max_length=40)
    request_id: int | None = None
    evidence: dict = {}


class EnrollRequestIn(BaseModel):
    camera: Side
    player_id: int


class EnrollFailedIn(BaseModel):
    reason: str = Field(max_length=300)


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

    # -- matches -----------------------------------------------------------

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
        out = {}
        for side, ids in hub.detections.items():
            players = [db.get(Player, pid) for pid in ids]
            out[side.value] = [player_ref(p) for p in players if p is not None]
        return out

    @app.post("/api/capture/detections", status_code=204)
    def post_detections(body: DetectionsIn, db: Session = Depends(get_db)):
        ids = list(dict.fromkeys(body.player_ids))
        # Logged only when who's recognised changes (it's posted several times a second).
        changed = set(ids) != set(hub.detections[body.camera])
        hub.detections[body.camera] = ids
        if changed:
            record(db, "face.detections", *explain_detections(db, body), match_id=None)
            db.commit()

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
        db.commit()

    @app.post("/api/capture/enroll-requests", status_code=201)
    def request_enroll(body: EnrollRequestIn, db: Session = Depends(get_db)):
        player = db.get(Player, body.player_id)
        if player is None:
            raise HTTPException(404, "player not found")
        req = hub.request_enroll(body.camera, body.player_id)
        record(
            db,
            "face.enroll_requested",
            f"{player.name} was picked by hand for Side {body.camera.value}, so camera "
            f"{body.camera.value} is asked to learn their face. It only does so if exactly one "
            "face in view is unrecognised — otherwise it can't tell which face is theirs.",
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

    @app.post("/api/capture/enroll-requests/{rid}/failed", status_code=204)
    def enroll_failed(rid: int, body: EnrollFailedIn, db: Session = Depends(get_db)):
        req = hub.enroll_request(rid)
        if req is None or req.status != "pending":
            raise HTTPException(404, "no such pending request")
        req.status = "failed"
        player = db.get(Player, req.player_id)
        record(
            db,
            "face.enroll_failed",
            f"Camera {req.camera.value} didn't learn {player.name}'s face: {body.reason}",
            {"request_id": rid},
        )
        db.commit()

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
