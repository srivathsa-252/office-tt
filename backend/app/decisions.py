"""The decision log: every decision the system makes, in plain words, with the
evidence behind it. Written where the decision is made; read by /decisions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .capture import RallyCapture
from .models import Decision
from .rules import MERCY_SHUTOUT_AT, MatchEngine, PointResult, Side

# kind -> (label, what this kind of decision is)
KINDS: dict[str, tuple[str, str]] = {
    "match.created": ("Match setup", "Roster, first server and the serve order it implies."),
    "match.abandoned": ("Abandoned", "A live match replaced by a new one; never rated."),
    "point.scored": ("Point", "The one human input in v1: who won the point (and how)."),
    "serve": ("Serve", "Who serves next, from the 5-serve turn, deuce, and rotation rules."),
    "game.check": (
        "Game rule",
        "Whether the game is over: 21 points with a 2-point lead, or a 0–8 mercy shutout.",
    ),
    "game.won": ("Game won", "A side reached 21 with a 2-point lead, or shut the other out 0–8."),
    "match.won": ("Match won", "A side won enough games; the match closes and is rated."),
    "rally": ("Rally", "No sensor data: rally length and last hitter both left unknown."),
    "rally.length": ("Rally length", "Table-sensor contacts counted during the rally."),
    "rally.last_hitter": ("Last hitter", "Latest identified swing before the ball's last contact."),
    "rating.update": ("Rating", "Glicko-2 update at match close."),
    "undo": ("Undo", "A point removed; state rebuilt by replaying the rest."),
    "face.detections": ("Face-rec", "Who a camera currently recognises, with match scores."),
    "face.enrolled": ("Face enrolled", "Face samples saved to a player's gallery."),
    "face.enroll_failed": ("Enroll failed", "A camera couldn't safely capture the face."),
    "face.enroll_requested": ("Enroll asked", "A manual pick asked the camera to learn a face."),
    "swing": ("Swing", "A stroke seen by a camera, and who it was attributed to."),
    "device.config": ("Device", "Thresholds a camera or the sensor reported when it started."),
}


def record(
    db: Session,
    kind: str,
    summary: str,
    detail: dict | None = None,
    *,
    match_id: int | None = None,
    point_id: int | None = None,
) -> Decision:
    d = Decision(
        kind=kind, summary=summary[:500], detail=detail or {}, match_id=match_id, point_id=point_id
    )
    db.add(d)
    return d


# -- explanations -------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def explain_serve(engine: MatchEngine, r: PointResult, names: dict[int, str]) -> tuple[str, dict]:
    nxt_s, nxt_r = names[engine.server], names[engine.receiver]
    detail = {
        "served_by": names[r.server],
        "serve_number": r.serve_number,
        "serves_per_turn_in_force": r.rotation_size,
        "deuce": engine.is_deuce,
        "next_server": nxt_s,
        "next_receiver": nxt_r,
        "rotation_position": engine.turn_label(),
        "serve_order": [engine.turn_label(i) for i in range(len(engine.serve_order))],
    }
    if r.match_winner is not None:
        return "No next serve: the match is over.", detail
    if r.game_winner is not None:
        prev_game_start = engine.turn_label((engine.game_index - 1) % len(engine.serve_order))
        return (
            f"Game {engine.game_number} starts one turn further round the rotation than game "
            f"{engine.game_number - 1} did ({prev_game_start} → {engine.turn_label()}), "
            f"so {nxt_s} serves first, to {nxt_r}.",
            detail,
        )
    t = engine.fmt.deuce_trigger
    if r.rotation_size == engine.fmt.deuce_serve_rotation and engine.is_deuce:
        why = (
            f"both sides are on {t} or more (deuce), so serve changes after every point"
            if engine.fmt.deuce_serve_rotation == 1
            else f"deuce: {engine.fmt.deuce_serve_rotation} serves per turn"
        )
        return f"Serve passes from {names[r.server]} to {nxt_s} ({nxt_r} receives): {why}.", detail
    if r.serve_changed:
        return (
            f"{names[r.server]} has served {r.serve_number} of {r.rotation_size} this turn, "
            f"so serve passes to {nxt_s}, serving to {nxt_r} (rotation {engine.turn_label()}).",
            detail,
        )
    left = r.rotation_size - r.serve_number
    return (
        f"{nxt_s} keeps serve: {r.serve_number} of {r.rotation_size} served this turn, "
        f"{_plural(left, 'serve')} left.",
        detail,
    )


def explain_game(engine: MatchEngine, r: PointResult) -> tuple[str, str, dict] | None:
    """(kind, summary, detail) when the game rule did something worth saying."""
    a, b = r.score_after[Side.A], r.score_after[Side.B]
    need, margin = engine.fmt.points_to_win, engine.fmt.win_margin
    detail = {"score": f"{a}–{b}", "points_to_win": need, "win_margin": margin}
    if r.game_winner is not None:
        if min(a, b) == 0 and max(a, b) < need:
            detail["mercy_shutout_at"] = MERCY_SHUTOUT_AT
            return (
                "game.won",
                f"Side {r.game_winner.value} wins game {r.game_number} {a}–{b}: a mercy "
                f"shutout at 0–{MERCY_SHUTOUT_AT}, before reaching {need}.",
                detail,
            )
        lead = abs(a - b)
        return (
            "game.won",
            f"Side {r.game_winner.value} wins game {r.game_number} {a}–{b}: reached "
            f"{max(a, b)} (≥{need}) with a {lead}-point lead (≥{margin}).",
            detail,
        )
    if max(a, b) >= need:
        if a == b:
            why = "the sides are level"
        else:
            why = f"Side {'A' if a > b else 'B'} has {need}+ but leads by only {abs(a - b)}"
        return (
            "game.check",
            f"No game yet at {a}–{b}: {why}; a game needs a {margin}-point lead.",
            detail,
        )
    return None


def explain_rally(
    rally: RallyCapture, names: dict[int, str], rejected_hitter: str | None
) -> list[tuple[str, str, dict]]:
    ev = dict(rally.evidence)
    ev["swings"] = [
        {**sw, "player": names.get(sw["player_id"], "unidentified")} for sw in ev.get("swings", [])
    ]
    if rally.hitter_reason == "no_sensor" and rejected_hitter is None:
        seen = f" ({len(ev['swings'])} swing(s) were seen)" if ev["swings"] else ""
        return [
            (
                "rally",
                "No table-sensor contacts this rally, so rally length isn't recorded and the "
                "last hitter stays unknown: without a point-end time there's nothing to match "
                f"swings against, and the system never guesses{seen}.",
                ev,
            )
        ]
    if rally.rally_length is None:
        length = "Rally length not recorded: the table sensor reported no contacts this rally."
    else:
        length = (
            f"Rally length {rally.rally_length}: the table sensor registered "
            f"{_plural(rally.rally_length, 'contact')} over {ev['rally_duration_s']:.1f} s."
        )
    window = ev.get("window_s")
    if rejected_hitter is not None:
        hitter = (
            f"Last hitter unknown: the latest swing was attributed to player "
            f"{rejected_hitter}, who isn't in this match, so it was discarded."
        )
    elif rally.hitter_reason == "no_sensor":
        hitter = (
            "Last hitter unknown: with no sensor contact there's no point-end time to match "
            "swings against, and the system never guesses."
        )
    elif rally.hitter_reason == "no_swing_in_window":
        gaps = [sw["before_end_s"] for sw in ev["swings"]]
        closest = ""
        if gaps:
            g = min(gaps, key=abs)
            closest = (
                f" (closest was {g:.2f} s before)"
                if g >= 0
                else f" (closest came {-g:.2f} s after)"
            )
        hitter = (
            f"Last hitter unknown: no swing within {window} s before the ball's last table "
            f"contact{closest}."
        )
    else:
        chosen = next(sw for sw in ev["swings"] if sw["verdict"].startswith("chosen"))
        if rally.hitter_reason == "unidentified":
            hitter = (
                f"Last hitter unknown: the latest swing in the window was on Side "
                f"{chosen['side']}, but the camera couldn't tell who made it."
            )
        else:
            hitter = (
                f"Last hitter {chosen['player']}: latest swing, {chosen['before_end_s']:.2f} s "
                f"before the ball's last table contact (window {window} s)."
            )
    return [("rally.length", length, ev), ("rally.last_hitter", hitter, ev)]


def _name(db: Session, player_id: int | None) -> str:
    from .models import Player

    p = db.get(Player, player_id) if player_id is not None else None
    return p.name if p else f"player {player_id}"


def explain_detections(db: Session, body) -> tuple[str, dict]:
    cam, thr = body.camera.value, body.threshold
    thr_s = f"{thr:.2f}" if thr is not None else "?"
    known = [f for f in body.faces if f.player_id is not None]
    unknown = [f for f in body.faces if f.player_id is None]
    parts = []
    for f in known:
        seen = f"; seen in {f.presence} recent checks" if f.presence else ""
        parts.append(f"{_name(db, f.player_id)} (similarity {f.similarity:.2f} ≥ {thr_s}{seen})")
    if not body.faces:  # older/simple clients send ids only
        parts = [_name(db, pid) for pid in body.player_ids]
    text = (
        f"Camera {cam} now recognises {', '.join(parts)}."
        if parts
        else f"Camera {cam} recognises nobody right now."
    )
    for f in unknown:
        if f.similarity is not None and thr is not None and f.similarity >= thr:
            text += (
                f" One face matches {_name(db, f.best_player_id)} ({f.similarity:.2f}) but isn't "
                f"confirmed yet: seen in only {f.presence} recent checks."
            )
        elif f.best_player_id is not None and f.similarity is not None:
            text += (
                f" One face isn't recognised: closest is {_name(db, f.best_player_id)} at "
                f"{f.similarity:.2f}, below the {thr_s} threshold, so no name is given."
            )
        else:
            text += " One face isn't recognised: the gallery has no faces to compare with."
    return text, {
        "camera": cam,
        "threshold": thr,
        "faces": [f.model_dump() for f in body.faces],
        "reported_ids": body.player_ids,
    }


def explain_swing(db: Session, body, side, player_id, dropped) -> tuple[str, dict]:
    ev = body.evidence or {}
    who = _name(db, player_id) if player_id is not None else "an unidentified player"
    speed = ev.get("speed")
    thr = ev.get("threshold")
    motion = (
        f": {ev.get('wrist', 'wrist')} wrist peaked at {speed:.1f} shoulder-widths/s "
        f"(threshold {thr})"
        if speed is not None and thr is not None
        else ""
    )
    link = ev.get("link")
    linked = {
        "face": " Linked to the player whose recognised face contains this body's nose.",
        "track": " Linked through the body's track to a face recognised moments earlier.",
        "none": " No recognised face could be tied to this body, so the swing stays anonymous.",
    }.get(link, "")
    text = f"Swing on Side {side.value} by {who}{motion}.{linked}"
    if dropped is not None:
        text += (
            f" The camera named {_name(db, dropped)}, who isn't in the live match, so the "
            "swing is kept as unidentified."
        )
    return text, {"camera": body.camera.value, **ev, "dropped_player_id": dropped}
