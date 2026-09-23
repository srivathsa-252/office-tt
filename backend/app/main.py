"""HTTP + WebSocket API. Single process, single table."""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import db as dbmod
from .capture import Hit, hub, now
from .models import Match, Player
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


class DetectionsIn(BaseModel):
    camera: Side  # the end the camera is mounted at == the side it faces
    player_ids: list[int]


class TickIn(BaseModel):
    ts: float | None = None


class HitIn(BaseModel):
    camera: Side
    player_id: int | None = None
    ts: float | None = None


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
    def post_detections(body: DetectionsIn):
        hub.detections[body.camera] = list(dict.fromkeys(body.player_ids))

    @app.post("/api/capture/ticks", status_code=204)
    async def post_tick(body: TickIn):
        async with lock:
            hub.rally.add_tick(body.ts if body.ts is not None else now())

    @app.post("/api/capture/hits", status_code=204)
    async def post_hit(body: HitIn, db: Session = Depends(get_db)):
        async with lock:
            match = live_match(db)
            side = body.camera
            player_id = body.player_id
            if match is not None and player_id is not None:
                if player_id in match.side_a_players:
                    side = Side.A
                elif player_id in match.side_b_players:
                    side = Side.B
                else:
                    player_id = None  # not in this match: don't attribute it
            hub.rally.add_hit(
                Hit(ts=body.ts if body.ts is not None else now(), side=side, player_id=player_id)
            )
            await broadcast_live(db)

    return app


app = create_app()
