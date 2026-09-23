import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as dbmod
from app.capture import hub
from app.main import create_app


@pytest.fixture
def client():
    url = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    kwargs = {"poolclass": StaticPool} if url.startswith("sqlite") else {}
    engine = dbmod.make_engine(url, **kwargs)
    dbmod.Base.metadata.drop_all(engine)
    dbmod.init_db(engine)
    hub.rally.reset()
    hub.detections = {k: [] for k in hub.detections}
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with TestClient(create_app(factory, init=False)) as c:
        c.session_factory = factory
        yield c
    engine.dispose()
