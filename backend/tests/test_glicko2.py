from app import glicko2
from app.glicko2 import Rating


def test_glickman_paper_example():
    player = Rating(1500, 200, 0.06)
    results = [(Rating(1400, 30), 1), (Rating(1550, 100), 0), (Rating(1700, 300), 0)]
    new = glicko2.update(player, results)
    assert round(new.rating, 2) == 1464.05
    assert round(new.rd, 2) == 151.52
    assert abs(new.volatility - 0.05999) < 1e-5


def test_expected_score_symmetric():
    a, b = Rating(1600, 80), Rating(1400, 80)
    assert abs(glicko2.expected_score(a, b) - (1 - glicko2.expected_score(b, a))) < 1e-9
    assert glicko2.expected_score(a, b) > 0.5
