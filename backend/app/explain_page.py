"""GET /decisions — a server-rendered page explaining every decision the system
makes: the rules in force (with the live values), the decision log with its
evidence, per-match point-by-point reasoning, and each player's style tags."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import capture, glicko2, stats
from .capture import hub
from .decisions import KINDS
from .devices import faces as face_cfg
from .devices import sensor as sensor_cfg
from .devices import swing as swing_cfg
from .models import Decision, FaceEmbedding, Match, Player, Point
from .rules import MERCY_SHUTOUT_AT, Format

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# The office is in India; show every timestamp on this page in IST regardless
# of what timezone the server itself is running in.
IST = ZoneInfo("Asia/Kolkata")


def clock(epoch: float) -> str:
    """Device timestamps (epoch seconds, UTC) as IST wall-clock time, to the ms."""
    return datetime.fromtimestamp(epoch, IST).strftime("%H:%M:%S.%f")[:-3]


templates.env.filters["clock"] = clock

# Badge colour group per kind.
GROUPS = {
    "human": {"point.scored", "face.enroll_requested"},
    "rules": {
        "match.created",
        "match.abandoned",
        "serve",
        "game.check",
        "game.won",
        "match.won",
        "undo",
    },
    "rating": {"rating.update"},
}


def group_of(kind: str) -> str:
    return next((g for g, kinds in GROUPS.items() if kind in kinds), "capture")


def local(ts: datetime) -> datetime:
    """DB timestamps are written as UTC (models.utcnow); SQLite can hand them
    back naive, so treat a naive value as UTC rather than as already-local."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(IST)


def rules_in_force() -> list[dict]:
    f = Format()
    reported = hub.device_params

    def device(name: str) -> dict | None:
        return reported.get(name)

    return [
        {
            "title": "Scoring",
            "note": "Deterministic rules (spec §2). The only human input is who won each point.",
            "rules": [
                ("Points to win a game", f.points_to_win, "First to this many…"),
                ("Win margin", f.win_margin, "…with at least this lead, so 21–20 plays on."),
                (
                    "Mercy shutout",
                    f"0–{MERCY_SHUTOUT_AT}",
                    "Not in the spec, confirmed for the office: a game also ends here if one "
                    "side is still at 0 once the other reaches this many points.",
                ),
                ("Serves per turn", f.serves_per_turn, "Serve passes after this many points."),
                (
                    "Deuce",
                    f"from {f.deuce_trigger}–{f.deuce_trigger}",
                    f"Once both sides reach {f.deuce_trigger}, serve changes after every "
                    + (
                        "point."
                        if f.deuce_serve_rotation == 1
                        else f"{f.deuce_serve_rotation} points."
                    ),
                ),
                ("Match length", f"best of {f.best_of}", f"First to {f.games_to_win} games."),
                (
                    "Doubles serve order",
                    "A1→B1, B1→A1, A2→B2, B2→A2",
                    "Fixed diagonal rotation, looping. A1 and B1 are picked on the setup screen.",
                ),
                (
                    "New game",
                    "+1 turn",
                    "Each game starts one turn further round the rotation than the last, so "
                    "the first serve alternates.",
                ),
                (
                    "Undo",
                    "replay",
                    "Undo deletes the last point and replays the rest. Undoing a match-winning "
                    "point reopens the match and reverts its ratings.",
                ),
            ],
        },
        {
            "title": "Rally length & last hitter",
            "note": "Correlates the table sensor with camera swings on one clock. Never guesses.",
            "rules": [
                ("Rally length", "sensor ticks", "Contacts counted since the previous point."),
                ("Point end", "last tick", "The ball's final table contact in the rally."),
                (
                    "Hit window",
                    f"{capture.HIT_WINDOW_S} s",
                    "The last hitter is the latest swing at most this long before the point end.",
                ),
                (
                    "Clock tolerance",
                    f"{capture.HIT_TOLERANCE_S} s",
                    "A swing stamped up to this long after the last contact still counts.",
                ),
                (
                    "Unknown",
                    "always allowed",
                    "No sensor data, no swing in the window, or an unidentified swing → 'unknown'.",
                ),
            ],
        },
        {
            "title": "Live preview",
            "note": "The scoreboard's preview panel, not used for scoring.",
            "rules": [
                (
                    "Camera considered live",
                    f"posted a frame in the last {capture.FRAME_STALE_S} s",
                    "Below this, a camera's preview is dropped and the panel warns which side "
                    "is missing instead of showing a frozen frame.",
                ),
            ],
        },
        {
            "title": "Face recognition",
            "note": "YuNet finds faces; SFace turns each into a 128-number fingerprint; the "
            "gallery match, smoothing and enrollment rules below are this app's.",
            "reported": [(n, device(n)) for n in ("camera A", "camera B")],
            "rules": [
                (
                    "Face confidence",
                    face_cfg.DETECT_SCORE,
                    "YuNet must be at least this sure something is a face.",
                ),
                (
                    "Match threshold",
                    face_cfg.MATCH_THRESHOLD,
                    "Cosine similarity to a player's closest stored sample must reach this "
                    "(SFace's published value). Below it, no name is given.",
                ),
                (
                    "One face per player",
                    "greedy",
                    "The most similar face–player pairs are assigned first; a player can only "
                    "be one face.",
                ),
                (
                    "Presence",
                    f"{face_cfg.PRESENCE_MIN} of last {face_cfg.PRESENCE_WINDOW}",
                    "A player counts as on that side only when matched in this many recent "
                    "checks — one bad frame can't add or drop anyone.",
                ),
                (
                    "Auto-enroll",
                    f"{face_cfg.ENROLL_SAMPLES} samples",
                    "After a manual pick, the camera learns the face only if it's the single "
                    f"unrecognised face in view, confidence ≥ {face_cfg.ENROLL_MIN_SCORE}, at "
                    f"least {face_cfg.ENROLL_MIN_SIZE_PX}px, and the same person across samples "
                    f"(similarity ≥ {face_cfg.ENROLL_SAME_PERSON}).",
                ),
            ],
        },
        {
            "title": "Swing detection",
            "note": "MediaPipe finds up to two bodies per camera; this app tracks them and "
            "watches wrist speed.",
            "rules": [
                (
                    "Swing speed",
                    f"{swing_cfg.SWING_SPEED} shoulder-widths/s",
                    "A swing starts when either wrist moves faster than this. Measured in "
                    "shoulder-widths so distance from the camera doesn't matter.",
                ),
                (
                    "Swing end",
                    f"< {swing_cfg.SWING_END_RATIO}× or {swing_cfg.SWING_MAX_S} s",
                    "The swing is reported at its peak speed once the wrist slows down.",
                ),
                (
                    "Refractory",
                    f"{swing_cfg.REFRACTORY_S} s",
                    "A second swing by the same body sooner than this is ignored.",
                ),
                (
                    "Who swung",
                    "nose in face box",
                    "A body belongs to the recognised face whose box contains its nose; the link "
                    f"is trusted for {swing_cfg.FACE_LINK_TTL_S} s.",
                ),
            ],
        },
        {
            "title": "Table sensor",
            "note": "Contact mic (or a microcontroller over serial) → one tick per contact.",
            "reported": [("sensor", device("sensor"))],
            "rules": [
                (
                    "Threshold",
                    sensor_cfg.THRESHOLD,
                    "A contact is a sharp peak above this (full scale 1.0)…",
                ),
                (
                    "Noise factor",
                    f"× {sensor_cfg.NOISE_FACTOR}",
                    "…and above the running noise floor times this, so a noisy room raises "
                    "the bar.",
                ),
                (
                    "Refractory",
                    f"{sensor_cfg.REFRACTORY_S * 1000:.0f} ms",
                    "One bounce can't count twice; real bounces are ~100 ms+ apart.",
                ),
            ],
        },
        {
            "title": "Ratings & stats",
            "note": "Computed when a match closes. Only finished matches count.",
            "rules": [
                (
                    "System",
                    "Glicko-2",
                    "Tracks rating, uncertainty (RD) and volatility; one match = one period.",
                ),
                (
                    "Start",
                    f"{glicko2.DEFAULT_RATING:.0f} ± {glicko2.DEFAULT_RD:.0f}",
                    "New players start here; high RD means early results move them fast.",
                ),
                ("Tau", glicko2.TAU, "How quickly volatility may change."),
                (
                    "Singles vs doubles",
                    "separate",
                    "Singles rates players. Doubles rates the pair as its own entity.",
                ),
                (
                    "Synergy",
                    "actual − predicted",
                    "A pair's win rate minus the win chance predicted from the partners' "
                    "individual ratings.",
                ),
                (
                    "Style tags",
                    f"≥ {stats.STYLE_MIN_POINTS} points",
                    "Aggressive / defensive / consistent / server-reliant, from the thresholds "
                    "shown on the Players tab.",
                ),
            ],
        },
    ]


def match_label(db: Session, m: Match) -> str:
    names = {p.id: p.name for p in db.scalars(select(Player))}

    def team(ids):
        return " & ".join(names.get(i, "?") for i in ids)

    return f"#{m.id} · {team(m.side_a_players)} vs {team(m.side_b_players)} · {m.status}"


def register(app: FastAPI, get_db) -> None:
    @app.get("/decisions", response_class=HTMLResponse, include_in_schema=False)
    def decisions_page(
        request: Request,
        view: str = Query("log", pattern="^(log|rules|players)$"),
        match: int | None = None,
        kind: str | None = None,
        before: int | None = None,
        live: bool = False,
        db: Session = Depends(get_db),
    ):
        limit = 150
        ctx: dict = {
            "request": request,
            "view": view,
            "match_id": match,
            "kind": kind,
            "live": live,
            "kinds": KINDS,
            "group_of": group_of,
            "local": local,
        }
        recent = db.scalars(select(Match).order_by(Match.id.desc()).limit(40)).all()
        ctx["matches"] = [(m.id, match_label(db, m)) for m in recent]

        if view == "rules":
            ctx["sections"] = rules_in_force()
        elif view == "players":
            rows = []
            for p in db.scalars(select(Player).order_by(Player.name)):
                pts = stats.player_points(db, p)
                rows.append(
                    {
                        "player": p,
                        "points": len(pts),
                        "samples": db.query(FaceEmbedding).filter_by(player_id=p.id).count(),
                        "style": stats.evaluate_style(pts, p.id),
                    }
                )
            ctx["players"] = rows
        else:
            q = select(Decision)
            if match is not None:
                q = q.where(Decision.match_id == match)
            if kind:
                q = q.where(Decision.kind == kind)
            if match is not None:
                # One match reads best as a story: oldest first, grouped by point.
                items = db.scalars(q.order_by(Decision.id)).all()
                live_points = set(db.scalars(select(Point.id).where(Point.match_id == match)).all())
                groups, n = [], 0
                for d in items:
                    if groups and d.point_id is not None and groups[-1]["point_id"] == d.point_id:
                        groups[-1]["items"].append(d)
                        continue
                    if d.point_id is not None:
                        n += 1
                    groups.append(
                        {
                            "point_id": d.point_id,
                            "number": n if d.point_id is not None else None,
                            "undone": d.point_id is not None and d.point_id not in live_points,
                            "items": [d],
                        }
                    )
                ctx["groups"] = groups
                m = db.get(Match, match)
                ctx["match_title"] = match_label(db, m) if m else f"#{match}"
            else:
                if before is not None:
                    q = q.where(Decision.id < before)
                items = db.scalars(q.order_by(Decision.id.desc()).limit(limit + 1)).all()
                ctx["items"] = items[:limit]
                ctx["older"] = items[limit - 1].id if len(items) > limit else None
            ctx["total"] = db.query(Decision).count()
        return templates.TemplateResponse(request, "decisions.html", ctx)
