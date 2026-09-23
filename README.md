# Office Table Tennis: live scoring and stats

Live scoring for the office table: one landscape screen with a chess-clock tap bar, a deterministic rules engine, and Glicko-2 stats computed when a match closes.

| Phase | Status |
|---|---|
| 1. Rules engine + chess-clock scoring + live display | **Built** (`backend/app/rules.py`, `frontend/src/screens/LiveScoreboard.tsx`) |
| 2. Face-rec player ID wired into match setup | **Ingest API built.** The Chitrachaya adapter still needs writing. It should POST to `/api/capture/detections`. |
| 3. Point-end sensor + rally length | **Ingest + correlation built** (`backend/app/capture.py`). The sensor adapter still needs writing. |
| 4. Last-hitter detection with an `unknown` fallback | **Ingest + correlation built.** The MediaPipe adapter still needs writing. |
| 5. Stats engine: ratings, synergy, style tags, history | **Built** (`backend/app/glicko2.py`, `backend/app/stats.py`) |
| 6. 120fps + TTNet | Out of scope |

## Running it

```bash
# Postgres (or point DATABASE_URL at your own)
docker compose up -d db

# API: FastAPI + WebSockets on :8000
cd backend
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m app.seed "Praneeth" "Abin" "Sri" "Jithin"   # or add players from the setup screen
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend: React + Vite on :5173 (proxies /api and /ws to :8000)
cd frontend
npm install && npm run dev
```

Open `http://<host>:5173/` on the table screen. It resumes the live match if there is one. Otherwise it opens match setup.

- `DATABASE_URL` defaults to `postgresql+psycopg://tt:tt@localhost:5432/office_tt`. Tables are created on startup. There are no migrations yet.
- `TT_CAPTURE_WIN_TYPE=0` turns off the Confirm-point screen, so one tap on the bar scores the point.

Tests: `cd backend && .venv/bin/pytest`. They run on SQLite by default. Set `TEST_DATABASE_URL=postgresql+psycopg://…` to run them on Postgres. Frontend: `npm run typecheck`.

## How it works

- **The rules engine is pure.** `MatchEngine` is a state machine that follows spec §2. The database stores only points. Live state comes from replaying a match's point winners, so the server holds no hidden state and **undo** means "delete the last point and replay". Undoing the match-winning point reopens the match and reverts its rating change. This is refused if either player has been rated in a later match.
- **Live push.** Every score change is broadcast on `WS /ws/matches/{id}` as the full match state. The scoreboard renders whatever it receives. Writes are serialised, so two taps can't race.
- **Ratings are computed at match close.** Individual Glicko-2 ratings come from singles. Doubles rates the **pair** as its own entity. At close, each pair's rating-history row also stores the win probability predicted from the partners' individual ratings, which is the baseline for synergy. Abandoned matches (a new match started over a live one) are never rated.

### Device adapter API (phases 2–4)

Adapters run on the same machine as the API, so all `ts` values use one clock (epoch seconds). Omit `ts` to use the server's time.

| Endpoint | Body | Used for |
|---|---|---|
| `POST /api/capture/detections` | `{camera: "A"\|"B", player_ids: [..]}` | Face-rec: who camera A/B currently sees. The setup screen polls this every second. |
| `POST /api/capture/ticks` | `{ts?}` | One sensor contact. |
| `POST /api/capture/hits` | `{camera, player_id?, ts?}` | One swing from pose detection. `player_id: null` means a swing was seen but the player wasn't identified. |

When a point is scored:
- **Rally length** = the number of sensor ticks since the previous point.
- **Point end** = the last tick.
- **Last hitter** = the latest swing at most 1.5s before the point end.

If there are no ticks, or the swing is unidentified or too early, the point is stored as `last_hitter: "unknown"` and `rally_length: null`. The system never guesses. `HIT_WINDOW_S` and `HIT_TOLERANCE_S` in `capture.py` are placeholders to tune on the real table.

## Design artifact: what I built differently, and why

The four screens follow the design's layout, colours, spacing and copy exactly. Each one renders at the artboard's size (1280×720 or 390×844) and scales uniformly to fit the display. I compared screenshots against the artboards. These points in the design can't be built exactly as drawn, or needed a decision:

1. **Games / "GAME 2 OF 3".** The spec's format has no multi-game concept, but the scoreboard shows games. I added `best_of` (default **3**) to the format. Each new game starts one turn further round the serve rotation, so in singles the first server alternates between sides. In doubles, game 2 opens with B1→A1. **Please confirm the office plays best-of-3 and this start-of-game serve rule.** The setup screen's Format card doesn't show best-of, because the design has only two cells.
2. **Pips at deuce.** The artboard shows 5 pips, 3 of them lit, at 21–20. From 20–20 the serve changes every point, so the pips drop to **1**. Lit pips = serves left in the current turn. Only the serving side's pips are lit.
3. **Serves-first picker.** The design has a picker under Side A only. The rotation needs both A1 and B1, so in doubles there's the same "Serves first" picker under Side B too, and Side A serves first per the confirmed order. In singles, the picker under Side A lists both players, so either side can serve first.
4. **No face-rec yet (phase 2).** You can tap a "Waiting for face…" card, or any player card, to pick a player by hand from a sheet. The sheet can also add a new player. Hand-picked cards say "Picked manually" instead of "Detected". Neither the sheet nor that label is in the design.
5. **Confirm point.** This is a portrait 390×844 artboard, but the table screen is landscape. It opens full-screen, scaled to fit, when a bar zone is tapped (if win-type capture is on). The tapped side is preselected, tags are optional, and tapping a side's card records the point. In the design's markup the winner names inherit the browser's default black text, which is unreadable on the dark cards, so I render them in the standard text colour.
6. **No undo control in the design.** A mis-tap currently needs an attached keyboard (Backspace or Ctrl/⌘+Z). The API supports undo (`DELETE /api/matches/{id}/points/last`). **The design needs an undo affordance** before this is usable with touch alone.
7. **No end-of-match state in the design.** When a match ends, the header reads "FINAL · SIDE A WINS", a lime "NEW MATCH" pill replaces the DEUCE pill, and each bar zone links to that side's player stats.
8. **Player stats.**
   - "Forehand winners" shows **—**. Nothing in the spec's hardware or data model captures stroke side, so it can't be computed.
   - "Avg rally length" shows **—** until the phase 3 sensor is live.
   - The synergy card shows the design's "+N vs solo avg" (pair rating minus the partners' mean rating). The spec's synergy (actual win rate minus predicted win rate) is computed too and returned by the API as `synergy`, but it isn't displayed.
   - History shows the point score for a one-game match and games won ("2–1") for best-of-N.
   - Style tags are computed but have no place in the design, so they aren't shown. Their thresholds in `stats.py` are placeholders.
   - There's no in-app navigation to stats except from the finished scoreboard. Use `/players/{id}` directly.
9. **Initials** are the first two letters of a one-word name ("Jithin" → "JI"). The design shows "JT", which suggests surnames. Multi-word names use first + last initials.

## Open questions

- The doubles serve order is implemented as confirmed: A1→B1, B1→A1, A2→B2, B2→A2, looping.
- Should the Confirm-point screen be on by default? It is now, because win-type data recorded later can't be backfilled onto old matches. It costs a second tap per point.
