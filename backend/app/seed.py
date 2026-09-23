"""Add players: python -m app.seed "Praneeth" "Abin" "Sri" "Jithin" """

import sys

from .db import SessionLocal, init_db
from .models import Player

if __name__ == "__main__":
    init_db()
    with SessionLocal() as db:
        for name in sys.argv[1:]:
            db.add(Player(name=name))
        db.commit()
    print(f"added {len(sys.argv) - 1} player(s)")
