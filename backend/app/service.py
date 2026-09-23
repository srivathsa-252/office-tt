"""Match lifecycle: create → score points (→ undo) → close and rate."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import glicko2
from .capture import UNKNOWN, CaptureHub, Hit
from .models import Match, Pair, Player, Point, RatingHistory, utcnow
from .rules import Format, MatchEngine, Mode, Side

WIN_TYPES = ("smash", "fault", "net", "out")


class ConflictError(Exception):
    pass


def initials(name: str) -> str:
    words = name.split()
    if len(words) > 1:
        return (words[0][0] + words[-1][0]).upper()
    return name[:2].upper()


def player_ref(p: Player) -> dict:
    return {"id": p.id, "name": p.name, "initials": initials(p.name)}


# -- engine -----------------------------------------------------------------


def format_of(match: Match) -> Format:
    return Format(**match.format)


def engine_for(match: Match) -> MatchEngine:
    return MatchEngine.replay(
        [Side(p.winner) for p in match.points],
        mode=Mode(match.mode),
        side_a=tuple(match.side_a_players),
        side_b=tuple(match.side_b_players),
        first_server=match.first_server_id,
        first_receiver=match.first_receiver_id,
        fmt=format_of(match),
    )


def live_match(db: Session) -> Match | None:
    return db.scalars(select(Match).where(Match.status == "live").order_by(Match.id.desc())).first()


def create_match(
    db: Session,
    hub: CaptureHub,
    *,
    mode: Mode,
    side_a: list[int],
    side_b: list[int],
    first_server: int,
    first_receiver: int,
    fmt: Format,
) -> Match:
    ids = side_a + side_b
    if len(set(ids)) != len(ids):
        raise ValueError("a player can only be on one side")
    found = set(db.scalars(select(Player.id).where(Player.id.in_(ids))))
    if missing := set(ids) - found:
        raise ValueError(f"unknown player(s): {sorted(missing)}")
    # Validates roster shape and the first server/receiver pairing.
    MatchEngine(
        mode=mode,
        side_a=tuple(side_a),
        side_b=tuple(side_b),
        first_server=first_server,
        first_receiver=first_receiver,
        fmt=fmt,
    )

    # One table: starting a new match abandons any match still live.
    for m in db.scalars(select(Match).where(Match.status == "live")):
        m.status = "abandoned"
        m.closed_at = utcnow()

    match = Match(
        mode=mode.value,
        format={
            "points_to_win": fmt.points_to_win,
            "win_margin": fmt.win_margin,
            "serves_per_turn": fmt.serves_per_turn,
            "deuce_serve_rotation": fmt.deuce_serve_rotation,
            "best_of": fmt.best_of,
        },
        side_a_players=side_a,
        side_b_players=side_b,
        first_server_id=first_server,
        first_receiver_id=first_receiver,
    )
    db.add(match)
    db.commit()
    hub.rally.reset()
    return match


def score_point(
    db: Session, hub: CaptureHub, match: Match, winner: Side, win_type: str | None
) -> Point:
    if match.status != "live":
        raise ConflictError(f"match is {match.status}")
    if win_type is not None and win_type not in WIN_TYPES:
        raise ValueError(f"win_type must be one of {WIN_TYPES}")

    engine = engine_for(match)
    result = engine.point_won(winner)

    rally = hub.rally.close()
    hub.rally.reset()
    roster = {str(p) for p in match.side_a_players + match.side_b_players}
    last_hitter = rally.last_hitter if rally.last_hitter in roster else UNKNOWN

    point = Point(
        match_id=match.id,
        game_number=result.game_number,
        server_id=result.server,
        receiver_id=result.receiver,
        winner=winner.value,
        last_hitter=last_hitter,
        rally_length=rally.rally_length,
        win_type=win_type,
        score_after_a=result.score_after[Side.A],
        score_after_b=result.score_after[Side.B],
    )
    match.points.append(point)

    if result.match_winner is not None:
        match.status = "finished"
        match.winner = result.match_winner.value
        match.closed_at = utcnow()
        match.game_scores = [{s.value: v for s, v in g.items()} for g in engine.game_scores]
        rate_match(db, match)
    db.commit()
    return point


def undo_last_point(db: Session, hub: CaptureHub, match: Match) -> None:
    if match.status == "abandoned":
        raise ConflictError("match was abandoned")
    if not match.points:
        raise ConflictError("no points to undo")
    if match.status == "finished":
        if live_match(db) is not None:
            raise ConflictError("another match has started since")
        unrate_match(db, match)
        match.status = "live"
        match.winner = None
        match.closed_at = None
        match.game_scores = None
    match.points.pop()
    db.commit()
    hub.rally.reset()


# -- ratings (spec §6, run at match close) ------------------------------------


def _rating(entity: Player | Pair) -> glicko2.Rating:
    return glicko2.Rating(entity.rating, entity.rd, entity.volatility)


def get_or_create_pair(db: Session, player_ids: list[int]) -> Pair:
    p1, p2 = sorted(player_ids)
    pair = db.scalars(select(Pair).where(Pair.player1_id == p1, Pair.player2_id == p2)).first()
    if pair is None:
        pair = Pair(player1_id=p1, player2_id=p2)
        db.add(pair)
        db.flush()
    return pair


def rate_match(db: Session, match: Match) -> None:
    score_a = 1.0 if match.winner == Side.A.value else 0.0
    if match.mode == Mode.SINGLES.value:
        a = db.get(Player, match.side_a_players[0])
        b = db.get(Player, match.side_b_players[0])
        expected = {a.id: None, b.id: None}
    else:
        a = get_or_create_pair(db, match.side_a_players)
        b = get_or_create_pair(db, match.side_b_players)
        # Synergy baseline: what the partners' individual ratings predict.
        team_a = glicko2.combined([_rating(db.get(Player, p)) for p in match.side_a_players])
        team_b = glicko2.combined([_rating(db.get(Player, p)) for p in match.side_b_players])
        e_a = glicko2.expected_score(team_a, team_b)
        expected = {a.id: e_a, b.id: 1 - e_a}

    ra, rb = _rating(a), _rating(b)
    new_a = glicko2.update(ra, [(rb, score_a)])
    new_b = glicko2.update(rb, [(ra, 1 - score_a)])
    for entity, before, after in ((a, ra, new_a), (b, rb, new_b)):
        entity.rating, entity.rd, entity.volatility = after.rating, after.rd, after.volatility
        db.add(
            RatingHistory(
                player_id=entity.id if isinstance(entity, Player) else None,
                pair_id=entity.id if isinstance(entity, Pair) else None,
                match_id=match.id,
                rating_before=before.rating,
                rating_after=after.rating,
                rd_before=before.rd,
                rd_after=after.rd,
                volatility_before=before.volatility,
                volatility_after=after.volatility,
                expected_score=expected[entity.id],
            )
        )


def unrate_match(db: Session, match: Match) -> None:
    rows = db.scalars(select(RatingHistory).where(RatingHistory.match_id == match.id)).all()
    for row in rows:
        later = select(RatingHistory.id).where(RatingHistory.id > row.id)
        later = later.where(
            RatingHistory.player_id == row.player_id
            if row.player_id is not None
            else RatingHistory.pair_id == row.pair_id
        )
        if db.scalars(later).first() is not None:
            raise ConflictError("a later match has already been rated")
    for row in rows:
        entity = db.get(Player, row.player_id) if row.player_id else db.get(Pair, row.pair_id)
        entity.rating, entity.rd, entity.volatility = (
            row.rating_before,
            row.rd_before,
            row.volatility_before,
        )
        db.delete(row)


# -- live state pushed to the scoreboard ------------------------------------


def _hit_ref(hit: Hit | None, players: dict[int, Player]) -> dict | None:
    if hit is None:
        return None
    p = players.get(hit.player_id) if hit.player_id is not None else None
    return {"camera": hit.side.value, "player": player_ref(p) if p else None}


def match_state(db: Session, hub: CaptureHub, match: Match) -> dict:
    engine = engine_for(match)
    ids = match.side_a_players + match.side_b_players
    players = {p.id: p for p in db.scalars(select(Player).where(Player.id.in_(ids)))}
    fmt = format_of(match)
    live = match.status == "live"

    def side_info(side: Side) -> dict:
        roster = match.side_a_players if side is Side.A else match.side_b_players
        return {"players": [player_ref(players[p]) for p in roster]}

    return {
        "id": match.id,
        "mode": match.mode,
        "status": match.status,
        "format": {
            "points_to_win": fmt.points_to_win,
            "win_margin": fmt.win_margin,
            "serves_per_turn": fmt.serves_per_turn,
            "deuce_serve_rotation": fmt.deuce_serve_rotation,
            "deuce_trigger": fmt.deuce_trigger,
            "best_of": fmt.best_of,
        },
        "sides": {"A": side_info(Side.A), "B": side_info(Side.B)},
        "game_number": engine.game_number,
        "games": {s.value: v for s, v in engine.games.items()},
        "score": {s.value: v for s, v in engine.score.items()},
        "server": {"id": engine.server, "side": engine.side_of(engine.server).value},
        "receiver": {"id": engine.receiver, "side": engine.side_of(engine.receiver).value},
        "serves_in_turn": engine.serves_in_turn,
        "serves_remaining": engine.serves_remaining,
        "deuce": engine.is_deuce,
        "winner": match.winner,
        "points_played": len(match.points),
        "last_hit": {
            s.value: _hit_ref(hub.rally.last_hit_on(s) if live else None, players) for s in Side
        },
        "latest_hit": _hit_ref(hub.rally.latest_hit() if live else None, players),
    }
