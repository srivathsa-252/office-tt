"""Data model (spec §4). Extra columns beyond the spec are commented."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .glicko2 import DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOLATILITY


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RatedMixin:
    # Glicko-2 needs deviation and volatility alongside the rating.
    rating: Mapped[float] = mapped_column(Float, default=DEFAULT_RATING)
    rd: Mapped[float] = mapped_column(Float, default=DEFAULT_RD)
    volatility: Mapped[float] = mapped_column(Float, default=DEFAULT_VOLATILITY)


class Player(RatedMixin, Base):
    """`rating` is the singles rating; doubles is rated on `Pair`."""

    __tablename__ = "player"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    face_embedding_ref: Mapped[str | None] = mapped_column(String(255))
    # A JPEG crop taken the moment enrollment last succeeded (scan-first
    # register, or teaching an existing player's face) — not continuously
    # updated, just a snapshot of what was last recognised and enrolled.
    face_photo: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Pair(RatedMixin, Base):
    __tablename__ = "pair"
    __table_args__ = (UniqueConstraint("player1_id", "player2_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    # player1_id < player2_id, so a pair has exactly one row.
    player1_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    player2_id: Mapped[int] = mapped_column(ForeignKey("player.id"))

    @property
    def player_ids(self) -> list[int]:
        return [self.player1_id, self.player2_id]


class Match(Base):
    __tablename__ = "match"
    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(8))
    # Column renamed: `format json` parses as the SQL/JSON FORMAT clause in PG16+.
    format: Mapped[dict] = mapped_column("match_format", JSON)
    side_a_players: Mapped[list[int]] = mapped_column(JSON)
    side_b_players: Mapped[list[int]] = mapped_column(JSON)
    # The one manual input at match start: who serves / receives first.
    first_server_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    first_receiver_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # live | finished | abandoned
    status: Mapped[str] = mapped_column(String(10), default="live")
    winner: Mapped[str | None] = mapped_column(String(1))
    # Per-game final scores, filled at close: [{"A": 21, "B": 18}, ...]
    game_scores: Mapped[list | None] = mapped_column(JSON)

    points: Mapped[list["Point"]] = relationship(
        back_populates="match", order_by="Point.id", cascade="all, delete-orphan"
    )


class Point(Base):
    __tablename__ = "point"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match.id"), index=True)
    game_number: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    server_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    receiver_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    winner: Mapped[str] = mapped_column(String(1))  # side
    last_hitter: Mapped[str] = mapped_column(String(20), default="unknown")
    rally_length: Mapped[int | None] = mapped_column(Integer)
    win_type: Mapped[str | None] = mapped_column(String(8))  # smash|fault|net|out
    score_after_a: Mapped[int] = mapped_column(Integer)
    score_after_b: Mapped[int] = mapped_column(Integer)

    match: Mapped[Match] = relationship(back_populates="points")


class RatingHistory(Base):
    __tablename__ = "rating_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("player.id"), index=True)
    pair_id: Mapped[int | None] = mapped_column(ForeignKey("pair.id"), index=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match.id"), index=True)
    rating_before: Mapped[float] = mapped_column(Float)
    rating_after: Mapped[float] = mapped_column(Float)
    # Full Glicko-2 state, so closing a match can be reverted exactly.
    rd_before: Mapped[float] = mapped_column(Float)
    rd_after: Mapped[float] = mapped_column(Float)
    volatility_before: Mapped[float] = mapped_column(Float)
    volatility_after: Mapped[float] = mapped_column(Float)
    # Pairs only: predicted win probability from the partners' individual
    # ratings — the baseline for doubles synergy.
    expected_score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FaceEmbedding(Base):
    """One SFace embedding (128 floats, L2-normalised) of a player's face.
    Several per player; matching uses the best of them. This supersedes
    `Player.face_embedding_ref` — the gallery lives in the database."""

    __tablename__ = "face_embedding"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), index=True)
    vector: Mapped[list[float]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(40))  # e.g. "camera A", "image"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Decision(Base):
    """Every automated (or recorded human) decision, with its evidence —
    what the /decisions page renders."""

    __tablename__ = "decision"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("match.id"), index=True)
    point_id: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(500))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
