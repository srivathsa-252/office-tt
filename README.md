# Office Table Tennis: live scoring and stats

Live scoring for the office table: one landscape screen with a chess-clock tap bar, a deterministic rules engine, and Glicko-2 stats computed when a match closes.

| Phase | Status |
|---|---|
| 1. Rules engine + chess-clock scoring + live display | **Built** (`backend/app/rules.py`, `frontend/src/screens/LiveScoreboard.tsx`) |
| 2. Face-rec player ID wired into match setup | **Built from scratch** (`backend/app/devices/faces.py`, `camera.py`, `enroll.py`) |
| 3. Point-end sensor + rally length | **Built** (`backend/app/devices/sensor.py`, correlation in `backend/app/capture.py`) |
| 4. Last-hitter detection with an `unknown` fallback | **Built** (`backend/app/devices/swing.py`, `camera.py`) |
| 5. Stats engine: ratings, synergy, style tags, history | **Built** (`backend/app/glicko2.py`, `backend/app/stats.py`) |
| — Decision log + `/decisions` page | **Built** (`backend/app/decisions.py`, `explain_page.py`) |
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

# Frontend: React + Vite on :5173 (proxies /api, /ws and /decisions to :8000)
cd frontend
npm install && npm run dev
```

For a single-port setup, run `npm run build` in `frontend/`: the API then serves the app itself at `http://<host>:8000/` (override the folder with `TT_STATIC_DIR`).

Open `http://<host>:5173/` on the table screen. It resumes the live match if there is one. Otherwise it opens match setup. **`http://<host>:5173/decisions`** (or `:8000/decisions`) explains everything the system decides.

- `DATABASE_URL` defaults to `postgresql+psycopg://tt:tt@localhost:5432/office_tt`. Tables are created on startup. There are no migrations yet.
- `TT_CAPTURE_WIN_TYPE=0` turns off the Confirm-point screen, so one tap on the bar scores the point.

### Cameras and sensor

These run on the same machine as the API (one clock for everything):

```bash
cd backend
sudo apt install libegl1 libportaudio2           # MediaPipe needs EGL; the contact mic needs PortAudio
.venv/bin/pip install -e '.[devices]'
.venv/bin/python -m app.devices.models           # fetch + SHA-256-verify the 3 model files (~45 MB)

.venv/bin/python -m app.devices.camera --camera A --device 0    # webcam at Side A's end
.venv/bin/python -m app.devices.camera --camera B --device 1    # webcam at Side B's end
.venv/bin/python -m app.devices.sensor audio --calibrate        # once: suggests a threshold
.venv/bin/python -m app.devices.sensor audio --threshold <suggested>
#   or: python -m app.devices.sensor serial --port /dev/ttyUSB0   (microcontroller + piezo)
```

**Adding faces.** Pick a player by hand on the setup screen. That side's camera then learns their face, provided they're the only unrecognised face in view. Or enrol from photos or a camera: `python -m app.devices.enroll --player 3 --image a.jpg b.jpg` / `--device 0`.

The camera worker also replays a recording (`--device match.mp4 --realtime`), which is the easiest way to tune thresholds on real footage.

Tests: `cd backend && .venv/bin/pytest`. They run on SQLite by default. Set `TEST_DATABASE_URL=postgresql+psycopg://…` to run them on Postgres. Set `TT_FACE_TEST_IMAGE` to OpenCV Zoo's SFace `demo.jpg` to also run the real-model face test. Frontend: `npm run typecheck`.

## How it works

- **The rules engine is pure.** `MatchEngine` is a state machine that follows spec §2. The database stores only points. Live state comes from replaying a match's point winners, so the server holds no hidden state and **undo** means "delete the last point and replay". Undoing the match-winning point reopens the match and reverts its rating change. This is refused if either player has been rated in a later match.
- **Live push.** Every score change is broadcast on `WS /ws/matches/{id}` as the full match state. The scoreboard renders whatever it receives. Writes are serialised, so two taps can't race.
- **Ratings are computed at match close.** Individual Glicko-2 ratings come from singles. Doubles rates the **pair** as its own entity. At close, each pair's rating-history row also stores the win probability predicted from the partners' individual ratings, which is the baseline for synergy. Abandoned matches (a new match started over a live one) are never rated.

### Face recognition (built from scratch, no Chitrachaya)

Two pretrained OpenCV Zoo models do the pixel work: YuNet finds faces, and SFace turns each face into 128 numbers where the same person scores a high cosine similarity. Everything after that is this app's own:

- **Gallery.** Every player's face samples are stored in Postgres (the `face_embedding` table). A face's score is its best similarity to any one of a player's samples.
- **Match.** A face is named only if its score reaches **0.363**, SFace's published threshold. The most similar face–player pairs are assigned first, so one player can't be two faces.
- **Smoothing.** A player counts as on a side only once matched in **3 of the last 5** checks (4 checks a second), so one bad frame can't add or drop anyone.
- **Auto-enroll.** After a manual pick, a camera learns the face only if it's the single unrecognised face in view, confident (≥ 0.9) and at least 80 px. It takes 5 samples, all of which must be the same person. Otherwise it gives up after 15 s and logs why.

Checked on a real photo: after enrolling one person from a solo shot, the system finds them in a group of six at similarity 0.59, while the five others stay below 0.363 (the closest reached 0.33).

### Swings and the last hitter

- **Pose.** MediaPipe's pose model finds up to 2 bodies per camera. Bodies are tracked from frame to frame by torso position.
- **Swing.** A swing is a burst of wrist speed above **4 shoulder-widths/s**, reported at its peak. Measuring in shoulder-widths makes the speed independent of distance from the camera. There's a 0.3 s refractory period.
- **Who swung.** A body belongs to the recognised face whose box contains its nose, and that link is trusted for 3 s. With no link, the swing is anonymous.
- **Last hitter.** The API takes the latest swing at most **1.5 s** before the ball's last table contact. No sensor contact, no swing in the window, or an anonymous swing → `unknown`. It never guesses.

### Table sensor

A contact mic under the table is read at 16 kHz. A contact is a sharp peak (first difference, which drops hum) above both the threshold and 6× the running noise floor, followed by a 60 ms refractory period. `--calibrate` records 5 s of quiet and 8 s of bounces, then suggests a threshold between the two. The serial mode is for a microcontroller that does its own detection and prints one line per contact.

### The decision log (`/decisions`)

Every decision is written to the `decision` table where it's made, as a plain-English sentence plus its evidence. It records:
- the serve (and why it did or didn't pass), deuce, game and match results, and "no game yet at 21–20" checks
- each point tap (human input)
- rally length and last hitter, with every swing's timing and verdict
- face-rec changes, with similarity scores
- enrollments and refusals
- swings, with speed and how the player was linked
- rating updates, with the win probability Glicko-2 gave
- undo
- device settings

The page has three tabs:
- **Log.** Newest first, filterable by kind or match. A single match reads point by point, oldest first. There's an optional 3 s live refresh.
- **Rules in force.** Every threshold with its current value and meaning, plus the values each running camera or sensor reported.
- **Players.** Each style tag with the measured numbers against its thresholds.

### Device API

| Endpoint | Body | Used for |
|---|---|---|
| `POST /api/capture/detections` | `{camera, player_ids, faces?, threshold?}` | Who camera A/B recognises (smoothed), with per-face evidence. |
| `POST /api/capture/ticks` | `{ts?, strength?}` | One table contact. |
| `POST /api/capture/hits` | `{camera, player_id?, ts?, evidence?}` | One swing. |
| `POST /api/capture/device-params` | `{device, params}` | Thresholds a worker runs with (shown on `/decisions`). |
| `POST /api/capture/frames` | `{camera, image, ts?}` | A downscaled JPEG (base64) for the live-preview panel. |
| `GET /api/capture/preview/{camera}` | | The latest JPEG posted for that camera, or 404 if none yet. |
| `GET /api/capture/camera-status` | | `{A, B}` → `{active, last_seen}`, from how recently each posted a preview frame. |
| `GET /api/face-gallery` · `POST/DELETE /api/players/{id}/faces` | `{vectors, source, request_id?}` | The face gallery. |
| `POST/GET /api/capture/enroll-requests` · `…/{id}/failed` | | Asks a camera to learn a face. |

`ts` is epoch seconds from the shared clock. When it's omitted, the server's time is used.

**Live preview.** The scoreboard screen has a "Cameras" toggle at the bottom that opens a panel with a live JPEG per running camera worker (refreshed a few times a second, from `--preview-every`, default 0.5 s). It auto-detects how many cameras are actually posting: one running camera shows one preview full-width; two show side by side; a camera that stops posting for `capture.FRAME_STALE_S` (4 s) drops out and the panel shows an inline warning naming which side is missing, instead of freezing on a stale frame. On a narrow (phone-width) screen there's room for only one preview at a time, so a switch button flips which camera is shown.

**Placeholders to tune on the real table** (all shown on `/decisions?view=rules`):
- hit window 1.5 s
- swing speed 4.0 shoulder-widths/s
- sensor threshold 0.08 (or use `--calibrate`)
- camera considered live: posted a preview frame in the last 4 s
- style-tag thresholds

What I couldn't test here: real webcams, a real contact mic, and swing detection on real table-tennis footage. I only tested it on a synthetic clip made by sliding a still photo. The logic is unit-tested and the models were run on real photos.

## Design artifact: what I built differently, and why

The four screens follow the design's layout, colours, spacing and copy exactly. Each one renders at the artboard's size (1280×720 or 390×844) and scales uniformly to fit the display. I compared screenshots against the artboards. These points in the design can't be built exactly as drawn, or needed a decision:

1. **Games / "GAME 2 OF 3".** The spec's format has no multi-game concept, but the scoreboard shows games. I added `best_of` (default **3**) to the format. Each new game starts one turn further round the serve rotation, so in singles the first server alternates between sides. In doubles, game 2 opens with B1→A1. **Please confirm the office plays best-of-3 and this start-of-game serve rule.** The setup screen's Format card doesn't show best-of, because the design has only two cells.
2. **Pips at deuce.** The artboard shows 5 pips, 3 of them lit, at 21–20. From 20–20 the serve changes every point, so the pips drop to **1**. Lit pips = serves left in the current turn. Only the serving side's pips are lit.
3. **Serves-first picker.** The design has a picker under Side A only. The rotation needs both A1 and B1, so in doubles there's the same "Serves first" picker under Side B too, and Side A serves first per the confirmed order. In singles, the picker under Side A lists both players, so either side can serve first.
4. **Manual fallback for face-rec.** You can tap a "Waiting for face…" card, or any player card, to pick a player by hand from a sheet. The sheet can also add a new player. Hand-picked cards say "Picked manually" instead of "Detected", and the pick asks that side's camera to learn the face. Neither the sheet nor that label is in the design.
5. **Confirm point.** This is a portrait 390×844 artboard, but the table screen is landscape. It opens full-screen, scaled to fit, when a bar zone is tapped (if win-type capture is on). The tapped side is preselected, tags are optional, and tapping a side's card records the point. In the design's markup the winner names inherit the browser's default black text, which is unreadable on the dark cards, so I render them in the standard text colour.
6. **Undo (not in the design).** An **UNDO** pill sits top-right of the scoreboard, next to DEUCE / NEW MATCH. It uses the same shape but in grey, so it doesn't compete with the lime pills. Its tap area is 45px, but the header keeps the design's height. It appears once a point has been played, and it also works on the match-winning point, reopening the match and reverting the rating. Backspace or Ctrl/⌘+Z do the same on an attached keyboard. **Worth adding to the design artifact** so the two stay in sync.
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
