import pytest

from app.rules import MERCY_SHUTOUT_AT, Format, MatchEngine, Mode, Side

A, B = Side.A, Side.B
ONE_GAME = Format(best_of=1)


def singles(fmt=ONE_GAME, first_server=1):
    other = 2 if first_server == 1 else 1
    return MatchEngine(Mode.SINGLES, (1,), (2,), first_server, other, fmt)


def doubles(fmt=ONE_GAME):
    # Side A = 1, 2 (A1 = 1); Side B = 3, 4 (B1 = 3)
    return MatchEngine(Mode.DOUBLES, (1, 2), (3, 4), 1, 3, fmt)


def test_singles_server_flips_every_five_points():
    e = singles()
    servers = []
    for i in range(12):
        servers.append(e.server)
        e.point_won(A if i % 2 else B)
    assert servers == [1] * 5 + [2] * 5 + [1] * 2


def test_doubles_confirmed_rotation_loops():
    e = doubles()
    turns = []
    for i in range(25):
        if i % 5 == 0:
            turns.append((e.server, e.receiver))
        e.point_won(A if i % 2 else B)
    # A1→B1, B1→A1, A2→B2, B2→A2, loop to A1→B1
    assert turns == [(1, 3), (3, 1), (2, 4), (4, 2), (1, 3)]


def test_game_to_21_needs_two_clear():
    e = singles()
    for _ in range(20):  # interleaved so neither side is shut out at 0
        e.point_won(A)
        e.point_won(B)
    assert e.is_deuce and e.match_winner is None
    e.point_won(A)  # 21-20: not over
    assert e.match_winner is None
    e.point_won(B)  # 21-21
    e.point_won(A)
    r = e.point_won(A)  # 23-21
    assert r.game_winner is A and r.match_winner is A
    assert r.score_after == {A: 23, B: 21}


def test_deuce_rotates_serve_every_point():
    e = singles()
    for _ in range(20):
        e.point_won(A)
        e.point_won(B)
    assert e.serves_in_turn == 1
    s = e.server
    e.point_won(A)
    assert e.server != s
    e.point_won(B)
    assert e.server == s


def test_mercy_shutout_ends_the_game_early():
    e = singles()
    for _ in range(MERCY_SHUTOUT_AT - 1):
        e.point_won(A)
    r = e.point_won(A)  # 8-0: shutout, game (and this one-game match) ends here
    assert r.game_winner is A and r.match_winner is A
    assert r.score_after == {A: MERCY_SHUTOUT_AT, B: 0}
    with pytest.raises(ValueError):
        e.point_won(B)


def test_mercy_shutout_only_ends_the_game_in_a_best_of():
    e = singles(Format(best_of=3))
    for _ in range(MERCY_SHUTOUT_AT):
        e.point_won(A)
    assert e.games == {A: 1, B: 0}
    assert e.match_winner is None  # match plays on to game 2
    assert e.score == {A: 0, B: 0} and e.game_number == 2


def test_win_needs_two_clear_once_past_the_mercy_score():
    e = singles()
    for _ in range(MERCY_SHUTOUT_AT):
        e.point_won(A)
        e.point_won(B)
    assert e.score == {A: MERCY_SHUTOUT_AT, B: MERCY_SHUTOUT_AT}
    assert e.match_winner is None  # both sides scored, so no shutout


def test_best_of_three_resets_score_and_alternates_first_serve():
    e = singles(Format(best_of=3))
    for _ in range(MERCY_SHUTOUT_AT):
        e.point_won(A)
    assert e.games == {A: 1, B: 0}
    assert e.score == {A: 0, B: 0} and e.game_number == 2
    assert e.server == 2  # side B serves first in game 2
    for _ in range(MERCY_SHUTOUT_AT):
        e.point_won(B)
    assert e.game_number == 3 and e.server == 1
    for _ in range(MERCY_SHUTOUT_AT):
        e.point_won(A)
    assert e.match_winner is A and e.game_scores[-1] == {A: MERCY_SHUTOUT_AT, B: 0}


def test_doubles_next_game_starts_next_turn_in_rotation():
    e = doubles(Format(best_of=3))
    for _ in range(MERCY_SHUTOUT_AT):
        e.point_won(A)
    assert (e.server, e.receiver) == (3, 1)


def test_first_server_can_be_side_b_in_singles():
    e = singles(first_server=2)
    assert e.server == 2 and e.receiver == 1


def test_replay_matches_live_state():
    winners = [A, B, B, A, A, A, B] * 5
    live = singles(Format(best_of=3))
    for w in winners:
        live.point_won(w)
    replayed = MatchEngine.replay(
        winners,
        mode=Mode.SINGLES,
        side_a=(1,),
        side_b=(2,),
        first_server=1,
        first_receiver=2,
        fmt=Format(best_of=3),
    )
    assert (replayed.score, replayed.games, replayed.server, replayed.serve_count) == (
        live.score,
        live.games,
        live.server,
        live.serve_count,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(mode=Mode.SINGLES, side_a=(1, 2), side_b=(3,), first_server=1, first_receiver=3),
        dict(mode=Mode.DOUBLES, side_a=(1, 2), side_b=(3, 4), first_server=1, first_receiver=2),
        dict(mode=Mode.SINGLES, side_a=(1,), side_b=(2,), first_server=9, first_receiver=2),
    ],
)
def test_invalid_setups_rejected(kwargs):
    with pytest.raises(ValueError):
        MatchEngine(**kwargs)
