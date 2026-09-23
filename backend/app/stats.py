"""Post-match player stats (spec §6). Only closed (finished) matches count —
nothing here reads a live match."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Match, Pair, Player, Point, RatingHistory
from .service import player_ref

RECENT_FORM = 5
HISTORY_LIMIT = 10

# Style-tag thresholds — placeholders until there's real office data.
STYLE_MIN_POINTS = 20
AGGRESSIVE_MAX_RALLY = 4.0  # avg rally length on points won
AGGRESSIVE_MIN_SMASH_SHARE = 0.30  # of tagged points won
DEFENSIVE_MIN_RALLY = 8.0  # avg rally length overall
CONSISTENT_MAX_FAULT_RATE = 0.10  # of tagged points lost
SERVER_RELIANT_MIN_GAP = 0.15  # serve-win% minus receive-win%


def _side_of(match: Match, player_id: int) -> str:
    return "A" if player_id in match.side_a_players else "B"


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def player_matches(db: Session, player_id: int) -> list[Match]:
    matches = db.scalars(
        select(Match).where(Match.status == "finished").order_by(Match.closed_at.desc())
    ).all()
    return [m for m in matches if player_id in m.side_a_players + m.side_b_players]


def _f(x: float | None, pct: bool = False) -> str:
    if x is None:
        return "no data"
    return f"{x:.0%}" if pct else f"{x:.1f}"


def evaluate_style(pts: list[tuple[Point, str]], player_id: int) -> list[dict]:
    """Each style tag with the numbers behind it: {tag, applies, reason}.
    pts = [(point, the player's side)] over the player's finished matches."""
    if len(pts) < STYLE_MIN_POINTS:
        reason = f"only {len(pts)} points played; tags need at least {STYLE_MIN_POINTS}"
        return [
            {"tag": t, "applies": False, "reason": reason}
            for t in ("aggressive", "defensive", "consistent", "server-reliant")
        ]
    won = [p for p, s in pts if p.winner == s]
    lost = [p for p, s in pts if p.winner != s]

    rally_won = _mean([p.rally_length for p in won if p.rally_length is not None])
    tagged_won = [p for p in won if p.win_type]
    smash_share = _ratio(sum(p.win_type == "smash" for p in tagged_won), len(tagged_won))
    aggressive = (rally_won is not None and rally_won <= AGGRESSIVE_MAX_RALLY) or (
        smash_share is not None and smash_share >= AGGRESSIVE_MIN_SMASH_SHARE
    )

    rally_all = _mean([p.rally_length for p, _ in pts if p.rally_length is not None])
    defensive = rally_all is not None and rally_all >= DEFENSIVE_MIN_RALLY

    tagged_lost = [p for p in lost if p.win_type]
    fault_rate = _ratio(sum(p.win_type == "fault" for p in tagged_lost), len(tagged_lost))
    consistent = len(tagged_lost) >= STYLE_MIN_POINTS and fault_rate <= CONSISTENT_MAX_FAULT_RATE

    serving = [(p, s) for p, s in pts if p.server_id == player_id]
    receiving = [(p, s) for p, s in pts if p.receiver_id == player_id]
    serve_win = _ratio(sum(p.winner == s for p, s in serving), len(serving))
    recv_win = _ratio(sum(p.winner == s for p, s in receiving), len(receiving))
    gap = serve_win - recv_win if serve_win is not None and recv_win is not None else None
    reliant = gap is not None and gap >= SERVER_RELIANT_MIN_GAP

    return [
        {
            "tag": "aggressive",
            "applies": aggressive,
            "reason": f"avg rally on points won {_f(rally_won)} (≤ {AGGRESSIVE_MAX_RALLY} counts) "
            f"or smash share of tagged wins {_f(smash_share, True)} "
            f"(≥ {AGGRESSIVE_MIN_SMASH_SHARE:.0%} counts)",
        },
        {
            "tag": "defensive",
            "applies": defensive,
            "reason": f"avg rally length {_f(rally_all)} (≥ {DEFENSIVE_MIN_RALLY} counts)",
        },
        {
            "tag": "consistent",
            "applies": consistent,
            "reason": f"faults in {_f(fault_rate, True)} of {len(tagged_lost)} tagged points lost "
            f"(≤ {CONSISTENT_MAX_FAULT_RATE:.0%} over ≥ {STYLE_MIN_POINTS} counts)",
        },
        {
            "tag": "server-reliant",
            "applies": reliant,
            "reason": f"serve-win {_f(serve_win, True)} vs receive-win {_f(recv_win, True)}, "
            f"gap {_f(gap, True)} (≥ {SERVER_RELIANT_MIN_GAP:.0%} counts)",
        },
    ]


def style_tags(pts: list[tuple[Point, str]], player_id: int) -> list[str]:
    return [e["tag"] for e in evaluate_style(pts, player_id) if e["applies"]]


def player_points(db: Session, player: Player) -> list[tuple[Point, str]]:
    matches = player_matches(db, player.id)
    by_match = {m.id: m for m in matches}
    if not by_match:
        return []
    points = db.scalars(select(Point).where(Point.match_id.in_(list(by_match)))).all()
    return [(p, _side_of(by_match[p.match_id], player.id)) for p in points]


def pair_synergy(db: Session, pair: Pair) -> dict:
    rows = db.scalars(
        select(RatingHistory).where(RatingHistory.pair_id == pair.id).order_by(RatingHistory.id)
    ).all()
    results = []
    for row in rows:
        m = db.get(Match, row.match_id)
        results.append(1.0 if m.winner == _side_of(m, pair.player1_id) else 0.0)
    actual = _mean(results)
    predicted = _mean([r.expected_score for r in rows if r.expected_score is not None])
    partners = [db.get(Player, pid) for pid in pair.player_ids]
    return {
        "pair_id": pair.id,
        "matches": len(rows),
        "rating": pair.rating,
        # The design's "+41 vs solo avg": pair rating minus the partners' mean.
        "vs_solo_avg": pair.rating - sum(p.rating for p in partners) / 2,
        # Spec's synergy: actual win rate minus rating-predicted win rate.
        "actual_win_rate": actual,
        "predicted_win_rate": predicted,
        "synergy": actual - predicted if actual is not None and predicted is not None else None,
    }


def player_stats(db: Session, player: Player) -> dict:
    matches = player_matches(db, player.id)
    match_ids = [m.id for m in matches]
    points = (
        db.scalars(select(Point).where(Point.match_id.in_(match_ids))).all() if match_ids else []
    )
    by_match = {m.id: m for m in matches}
    pts = [(p, _side_of(by_match[p.match_id], player.id)) for p in points]

    wins = [m.winner == _side_of(m, player.id) for m in matches]
    serving = [(p, s) for p, s in pts if p.server_id == player.id]

    last_change = db.scalars(
        select(RatingHistory)
        .where(RatingHistory.player_id == player.id)
        .order_by(RatingHistory.id.desc())
    ).first()

    pairs = db.scalars(
        select(Pair).where(or_(Pair.player1_id == player.id, Pair.player2_id == player.id))
    ).all()
    synergy = []
    for pair in pairs:
        s = pair_synergy(db, pair)
        if s["matches"]:
            partner_id = next(pid for pid in pair.player_ids if pid != player.id)
            s["partner"] = player_ref(db.get(Player, partner_id))
            synergy.append(s)
    synergy.sort(key=lambda s: s["matches"], reverse=True)

    names: dict[int, Player] = {}

    def ref(pid: int) -> dict:
        if pid not in names:
            names[pid] = db.get(Player, pid)
        return player_ref(names[pid])

    history = []
    for m in matches[:HISTORY_LIMIT]:
        mine = _side_of(m, player.id)
        theirs = "B" if mine == "A" else "A"
        opponents = m.side_b_players if mine == "A" else m.side_a_players
        games = m.game_scores or []
        history.append(
            {
                "match_id": m.id,
                "mode": m.mode,
                "opponents": [ref(o) for o in opponents],
                "won": m.winner == mine,
                "closed_at": m.closed_at.isoformat(),
                "games": [{"own": g[mine], "opp": g[theirs]} for g in games],
                "games_won": {
                    "own": sum(g[mine] > g[theirs] for g in games),
                    "opp": sum(g[theirs] > g[mine] for g in games),
                },
            }
        )

    return {
        "player": player_ref(player),
        "rating": player.rating,
        "rating_delta": (
            last_change.rating_after - last_change.rating_before if last_change else None
        ),
        "matches_played": len(matches),
        "win_rate": _ratio(sum(wins), len(wins)),
        "avg_rally_length": _mean([p.rally_length for p, _ in pts if p.rally_length is not None]),
        # No stroke-side data is captured anywhere in v1, so this can't be computed.
        "forehand_winner_rate": None,
        "serve_win_rate": _ratio(sum(p.winner == s for p, s in serving), len(serving)),
        "recent_form": ["W" if w else "L" for w in wins[:RECENT_FORM]],
        "style_tags": style_tags(pts, player.id),
        "synergy": synergy,
        "history": history,
    }
