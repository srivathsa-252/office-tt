"""Deterministic rules engine (spec §2). No ML, no I/O — a state machine.

The engine is rebuilt by replaying a match's recorded point winners, so the
database only has to store points; undo is "drop the last point and replay".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Not in the spec: a shutout mercy rule, confirmed for the office. A game
# also ends here if one side is still at 0 once the other reaches this many
# points, instead of running all the way to points_to_win.
MERCY_SHUTOUT_AT = 8


class Side(str, Enum):
    A = "A"
    B = "B"

    @property
    def other(self) -> "Side":
        return Side.B if self is Side.A else Side.A


class Mode(str, Enum):
    SINGLES = "singles"
    DOUBLES = "doubles"


@dataclass(frozen=True)
class Format:
    points_to_win: int = 21
    win_margin: int = 2
    serves_per_turn: int = 5
    deuce_serve_rotation: int = 1
    # Not in the spec's format block, but the design's scoreboard shows
    # "GAME 2 OF 3" / "GAMES 1–0", so a match is best-of-N games.
    best_of: int = 3

    @property
    def deuce_trigger(self) -> int:
        # 20 for a 21-point game: deuce once both sides reach points_to_win - 1.
        return self.points_to_win - 1

    @property
    def games_to_win(self) -> int:
        return self.best_of // 2 + 1


@dataclass(frozen=True)
class PointResult:
    game_number: int  # 1-based game the point belonged to
    server: int
    receiver: int
    winner: Side
    score_after: dict[Side, int]
    game_winner: Side | None
    match_winner: Side | None
    # Serve bookkeeping, for explaining the decision:
    serve_number: int  # which serve of the turn this point was (1-based)
    rotation_size: int  # serves per turn in force when the point ended (5, or 1 at deuce)
    serve_changed: bool  # serve passed to the next turn in the rotation


@dataclass
class MatchEngine:
    mode: Mode
    side_a: tuple[int, ...]
    side_b: tuple[int, ...]
    first_server: int
    first_receiver: int
    fmt: Format = field(default_factory=Format)

    game_index: int = 0  # 0-based
    score: dict[Side, int] = field(default_factory=lambda: {Side.A: 0, Side.B: 0})
    games: dict[Side, int] = field(default_factory=lambda: {Side.A: 0, Side.B: 0})
    serve_count: int = 0
    turn_index: int = 0
    game_scores: list[dict[Side, int]] = field(default_factory=list)
    match_winner: Side | None = None

    def __post_init__(self) -> None:
        expected = 1 if self.mode is Mode.SINGLES else 2
        if len(self.side_a) != expected or len(self.side_b) != expected:
            raise ValueError(f"{self.mode.value} needs {expected} player(s) per side")
        server_side = self.side_of(self.first_server)
        if self.side_of(self.first_receiver) is not server_side.other:
            raise ValueError("first receiver must be on the opposite side to the first server")
        self.serve_order = self._build_serve_order()

    def _build_serve_order(self) -> list[tuple[int, int]]:
        s1, r1 = self.first_server, self.first_receiver
        if self.mode is Mode.SINGLES:
            return [(s1, r1), (r1, s1)]
        # Fixed diagonal-pair rotation (confirmed order):
        # A1→B1 (x5) → B1→A1 (x5) → A2→B2 (x5) → B2→A2 (x5) → loop
        s2, r2 = self.partner(s1), self.partner(r1)
        return [(s1, r1), (r1, s1), (s2, r2), (r2, s2)]

    # -- roster helpers ----------------------------------------------------

    def side_of(self, player_id: int) -> Side:
        if player_id in self.side_a:
            return Side.A
        if player_id in self.side_b:
            return Side.B
        raise ValueError(f"player {player_id} is not in this match")

    def partner(self, player_id: int) -> int:
        team = self.side_a if self.side_of(player_id) is Side.A else self.side_b
        return next(p for p in team if p != player_id)

    # -- live state --------------------------------------------------------

    @property
    def server(self) -> int:
        return self.serve_order[self.turn_index][0]

    @property
    def receiver(self) -> int:
        return self.serve_order[self.turn_index][1]

    @property
    def is_deuce(self) -> bool:
        t = self.fmt.deuce_trigger
        return self.score[Side.A] >= t and self.score[Side.B] >= t

    @property
    def serves_in_turn(self) -> int:
        return self.fmt.deuce_serve_rotation if self.is_deuce else self.fmt.serves_per_turn

    @property
    def serves_remaining(self) -> int:
        return max(self.serves_in_turn - self.serve_count, 0)

    def turn_label(self, index: int | None = None) -> str:
        """Rotation position as the spec writes it, e.g. "A1→B1" (doubles) or "A→B"."""
        server, receiver = self.serve_order[self.turn_index if index is None else index]
        if self.mode is Mode.SINGLES:
            return f"{self.side_of(server).value}→{self.side_of(receiver).value}"
        slot = {self.serve_order[0][0]: 1, self.serve_order[0][1]: 1}
        slot.update({self.serve_order[2][0]: 2, self.serve_order[2][1]: 2})
        s_side, r_side = self.side_of(server).value, self.side_of(receiver).value
        return f"{s_side}{slot[server]}→{r_side}{slot[receiver]}"

    @property
    def game_number(self) -> int:
        return self.game_index + 1

    # -- transitions -------------------------------------------------------

    def point_won(self, winner: Side) -> PointResult:
        if self.match_winner is not None:
            raise ValueError("match is already over")
        server, receiver, game_number = self.server, self.receiver, self.game_number

        self.score[winner] += 1
        self.serve_count += 1
        serve_number, rotation_size = self.serve_count, self.serves_in_turn
        serve_changed = self.serve_count >= rotation_size
        if serve_changed:
            self.turn_index = (self.turn_index + 1) % len(self.serve_order)
            self.serve_count = 0

        score_after = dict(self.score)
        game_winner = self._check_game_win()
        if game_winner is not None:
            self.games[game_winner] += 1
            self.game_scores.append(score_after)
            if self.games[game_winner] >= self.fmt.games_to_win:
                self.match_winner = game_winner
            else:
                self._start_next_game()

        return PointResult(
            game_number=game_number,
            server=server,
            receiver=receiver,
            winner=winner,
            score_after=score_after,
            game_winner=game_winner,
            match_winner=self.match_winner,
            serve_number=serve_number,
            rotation_size=rotation_size,
            serve_changed=serve_changed,
        )

    def _check_game_win(self) -> Side | None:
        a, b = self.score[Side.A], self.score[Side.B]
        if max(a, b) >= self.fmt.points_to_win and abs(a - b) >= self.fmt.win_margin:
            return Side.A if a > b else Side.B
        if a == 0 and b >= MERCY_SHUTOUT_AT:
            return Side.B
        if b == 0 and a >= MERCY_SHUTOUT_AT:
            return Side.A
        return None

    def _start_next_game(self) -> None:
        # Each new game starts one turn further round the rotation, so first
        # serve alternates between sides (and, in doubles, walks the pairs).
        self.game_index += 1
        self.score = {Side.A: 0, Side.B: 0}
        self.serve_count = 0
        self.turn_index = self.game_index % len(self.serve_order)

    @classmethod
    def replay(cls, winners: list[Side], **config) -> "MatchEngine":
        engine = cls(**config)
        for w in winners:
            engine.point_won(w)
        return engine
