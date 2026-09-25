from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://tt:tt@localhost:5432/office_tt")


class Base(DeclarativeBase):
    pass


def make_engine(url: str = DATABASE_URL, **kwargs):
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    return create_engine(url, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(bind=None) -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(bind=bind or engine)
