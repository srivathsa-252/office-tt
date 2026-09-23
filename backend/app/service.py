"""Match lifecycle: create → score points (→ undo) → close and rate."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import glicko2
from .capture import UNKNOWN, CaptureHub, Hit
from .decisions import explain_game, explain_rally, explain_serve, record
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


def names_for(db: Session, ids: list[int]) -> dict[int, str]:
    return {p.id: p.name for p in db.scalars(select(Player).where(Player.id.in_(ids)))}


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
    engine = MatchEngine(
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
        record(
            db,
            "match.abandoned",
            f"Match #{m.id} abandoned after {len(m.points)} point(s): a new match was started "
            "while it was live (one table). Abandoned matches are never rated.",
            {"points_played": len(m.points)},
            match_id=m.id,
        )

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
    db.flush()
    names = names_for(db, ids)
    order = [
        f"{engine.turn_label(i)}: {names[sv]} → {names[rc]}"
        for i, (sv, rc) in enumerate(engine.serve_order)
    ]
    how = (
        "Doubles uses the fixed diagonal rotation A1→B1, B1→A1, A2→B2, B2→A2, "
        f"{fmt.serves_per_turn} serves each, looping"
        if mode is Mode.DOUBLES
        else f"Singles alternates every {fmt.serves_per_turn} serves"
    )
    record(
        db,
        "match.created",
        f"{mode.value.title()} match: {' & '.join(names[p] for p in side_a)} (Side A) vs "
        f"{' & '.join(names[p] for p in side_b)} (Side B). {names[first_server]} serves first "
        f"to {names[first_receiver]} (chosen on the setup screen). {how}; from "
        f"{fmt.deuce_trigger}–{fmt.deuce_trigger} serve changes every point. "
        + (f"Best of {fmt.best_of} games" if fmt.best_of > 1 else "A single game")
        + f" to {fmt.points_to_win}, win by {fmt.win_margin}.",
        {"serve_order": order, "format": match.format},
        match_id=match.id,
    )
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
    rejected = rally.last_hitter if rally.last_hitter not in roster | {UNKNOWN} else None
    last_hitter = UNKNOWN if rejected else rally.last_hitter

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
    db.flush()

    names = names_for(db, match.side_a_players + match.side_b_players)
    a, b = result.score_after[Side.A], result.score_after[Side.B]
    how = f", ended by {win_type}" if win_type else ""
    log = [
        (
            "point.scored",
            f"Point to Side {winner.value} (tapped on the scoreboard{how}). "
            f"Game {result.game_number}: {a}–{b}.",
            {"win_type": win_type, "server": names[result.server]},
        ),
        *explain_rally(rally, names, rejected),
    ]
    if game := explain_game(engine, result):
        log.append(game)
    if result.match_winner is not None:
        g = engine.games
        log.append(
            (
                "match.won",
                f"Side {result.match_winner.value} wins the match {g[Side.A]}–{g[Side.B]} in "
                f"games (first to {engine.fmt.games_to_win} of {engine.fmt.best_of}). "
                "Ratings are updated now.",
                {"game_scores": [f"{x[Side.A]}–{x[Side.B]}" for x in engine.game_scores]},
            )
        )
    log.append(("serve", *explain_serve(engine, result, names)))
    for kind, summary, detail in log:
        record(db, kind, summary, detail, match_id=match.id, point_id=point.id)

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
    reopened = ""
    if match.status == "finished":
        if live_match(db) is not None:
            raise ConflictError("another match has started since")
        reopened = " The match was over, so it's reopened and its rating changes reverted."
        unrate_match(db, match)
        match.status = "live"
        match.winner = None
        match.closed_at = None
        match.game_scores = None
    point = match.points.pop()
    db.flush()
    engine = engine_for(match)
    names = names_for(db, match.side_a_players + match.side_b_players)
    record(
        db,
        "undo",
        f"Undid the point to Side {point.winner} at {point.score_after_a}–"
        f"{point.score_after_b} (game {point.game_number}). Replayed the remaining "
        f"{len(match.points)} point(s): score {engine.score[Side.A]}–{engine.score[Side.B]}, "
        f"{names[engine.server]} to serve.{reopened}",
        {"undone_point_id": point.id},
        match_id=match.id,
    )
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
    names = names_for(db, match.side_a_players + match.side_b_players)

    def label(e: Player | Pair) -> str:
        return e.name if isinstance(e, Player) else " & ".join(names[p] for p in e.player_ids)

    for entity, opp, before, opp_r, after, won in (
        (a, b, ra, rb, new_a, score_a == 1),
        (b, a, rb, ra, new_b, score_a == 0),
    ):
        p_win = glicko2.expected_score(before, opp_r)
        surprise = (
            "an upset, so it moves a lot"
            if (won and p_win < 0.4) or (not won and p_win > 0.6)
            else "roughly as expected"
        )
        synergy = ""
        if expected[entity.id] is not None:
            synergy = (
                f" Synergy baseline: the partners' individual ratings predicted a "
                f"{expected[entity.id]:.0%} win chance."
            )
        record(
            db,
            "rating.update",
            f"{label(entity)} {before.rating:.0f} → {after.rating:.0f} "
            f"({after.rating - before.rating:+.0f}): {'won' if won else 'lost'} against "
            f"{label(opp)} ({opp_r.rating:.0f}). Glicko-2 gave a {p_win:.0%} win chance, so the "
            f"result was {surprise}; uncertainty (RD) {before.rd:.0f} → {after.rd:.0f}.{synergy}",
            {
                "entity": "player" if isinstance(entity, Player) else "pair",
                "rating_before": before.rating,
                "rating_after": after.rating,
                "rd_before": before.rd,
                "rd_after": after.rd,
                "volatility_before": before.volatility,
                "volatility_after": after.volatility,
                "win_probability": p_win,
                "tau": glicko2.TAU,
                "pair_predicted_from_individuals": expected[entity.id],
            },
            match_id=match.id,
        )
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
