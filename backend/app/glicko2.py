"""Glicko-2 (Glickman, 2013 — "Example of the Glicko-2 system"). One match = one rating period."""

from __future__ import annotations

import math
from dataclasses import dataclass

SCALE = 173.7178
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOLATILITY = 0.06
TAU = 0.5
EPSILON = 1e-6


@dataclass(frozen=True)
class Rating:
    rating: float = DEFAULT_RATING
    rd: float = DEFAULT_RD
    volatility: float = DEFAULT_VOLATILITY

    @property
    def mu(self) -> float:
        return (self.rating - DEFAULT_RATING) / SCALE

    @property
    def phi(self) -> float:
        return self.rd / SCALE


def _g(phi: float) -> float:
    return 1 / math.sqrt(1 + 3 * phi * phi / math.pi**2)


def _e(mu: float, mu_j: float, phi_j: float) -> float:
    return 1 / (1 + math.exp(-_g(phi_j) * (mu - mu_j)))


def expected_score(player: Rating, opponent: Rating) -> float:
    return _e(player.mu, opponent.mu, opponent.phi)


def combined(ratings: list[Rating]) -> Rating:
    """Team strength from individual ratings: mean rating, RMS deviation."""
    n = len(ratings)
    return Rating(
        rating=sum(r.rating for r in ratings) / n,
        rd=math.sqrt(sum(r.rd**2 for r in ratings) / n),
        volatility=sum(r.volatility for r in ratings) / n,
    )


def update(player: Rating, results: list[tuple[Rating, float]], tau: float = TAU) -> Rating:
    """Rate `player` against `results` = [(opponent, score)], score 1 win / 0 loss."""
    mu, phi, sigma = player.mu, player.phi, player.volatility
    if not results:
        return Rating(player.rating, min(math.sqrt(phi**2 + sigma**2) * SCALE, DEFAULT_RD), sigma)

    v_inv = 0.0
    delta_sum = 0.0
    for opp, s in results:
        g = _g(opp.phi)
        e = _e(mu, opp.mu, opp.phi)
        v_inv += g * g * e * (1 - e)
        delta_sum += g * (s - e)
    v = 1 / v_inv
    delta = v * delta_sum

    a = math.log(sigma * sigma)

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta * delta - phi * phi - v - ex)
        den = 2 * (phi * phi + v + ex) ** 2
        return num / den - (x - a) / (tau * tau)

    big_a = a
    if delta * delta > phi * phi + v:
        big_b = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        big_b = a - k * tau
    f_a, f_b = f(big_a), f(big_b)
    while abs(big_b - big_a) > EPSILON:
        big_c = big_a + (big_a - big_b) * f_a / (f_b - f_a)
        f_c = f(big_c)
        if f_c * f_b <= 0:
            big_a, f_a = big_b, f_b
        else:
            f_a /= 2
        big_b, f_b = big_c, f_c
    new_sigma = math.exp(big_a / 2)

    phi_star = math.sqrt(phi * phi + new_sigma * new_sigma)
    new_phi = 1 / math.sqrt(1 / (phi_star * phi_star) + 1 / v)
    new_mu = mu + new_phi * new_phi * delta_sum
    return Rating(new_mu * SCALE + DEFAULT_RATING, new_phi * SCALE, new_sigma)
