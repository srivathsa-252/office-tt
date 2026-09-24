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
- **Ratings are computed at match close.** Individual Glicko-2 ratings come from singles. Doubles rates the **pair** as its own entity. At close, each pair's rating-history row also stores the win probability predicted from the partners' individual ratings, which is the baseline for synergy. Abandoned matches (a new match started over a live one, or ended manually — see below) are never rated.
- **Ending a match manually.** The scoreboard has an **END MATCH** pill next to UNDO, live only, behind a confirm dialog. It abandons the match exactly like starting a new one over a live match does: never rated, since a match stopped mid-game has no well-defined winner. `POST /api/matches/{id}/end`.
- **Cameras pause when nobody needs them.** A camera worker polls `GET /api/capture/camera-needed`, which is true only while there's a live match or the setup screen is open (inferred from its own detections poll — see `capture.SETUP_HEARTBEAT_STALE_S`). Idle, it stops running pose/face inference and stops posting the preview — near-zero CPU/bandwidth — but keeps the OS camera handle open so resuming is instant rather than re-opening the device.

### Face recognition (built from scratch, no Chitrachaya)

Two pretrained OpenCV Zoo models do the pixel work: YuNet finds faces, and SFace turns each face into 128 numbers where the same person scores a high cosine similarity. Everything after that is this app's own:

- **Gallery.** Every player's face samples are stored in Postgres (the `face_embedding` table). A face's score is its best similarity to any one of a player's samples.
- **Match.** A face is named only if its score reaches **0.363**, SFace's published threshold. The most similar face–player pairs are assigned first, so one player can't be two faces.
- **Smoothing.** A player counts as on a side only once matched in **3 of the last 5** checks (4 checks a second), so one bad frame can't add or drop anyone.
- **Auto-enroll.** After a manual pick, a camera learns the face only if it's the single unrecognised face in view, confident (≥ 0.9) and at least 80 px. It takes 5 samples, all of which must be the same person. Otherwise it gives up after 15 s and logs why.

Checked on a real photo: after enrolling one person from a solo shot, the system finds them in a group of six at similarity 0.59, while the five others stay below 0.363 (the closest reached 0.33).

### Match setup

The setup screen's Format card is editable: **serves per turn** (3 or 5), **games per match** (best of 1/3/5), and **cameras** (1 or 2) — all local to the browser except serves/games, which go to the new match's format. Points-to-win, win-margin and the deuce trigger stay fixed.

**One camera instead of two.** With Cameras set to 1, both sides draw from camera A's detections instead of one camera per side — the roster still fills Side A before Side B, first-detected-first-assigned. `POST /api/capture/enroll-requests` targets camera A for both sides too, so auto-enroll after a manual pick still works. The camera-count choice is saved per browser (`localStorage`), not sent to the API — it only changes how the setup screen reads detections, since which camera workers are actually running is an operational fact the app doesn't control.

**New face, register?** `GET /api/capture/detections` now reports `unknown_present` per camera — a face was seen that didn't confidently match anyone (`player_id: null` in what the camera posted), not necessarily a stranger — presence-smoothing might just not have confirmed them yet. When true and a slot is still open, a dismissible banner offers to register them immediately, instead of waiting for a manual "Waiting for face…" tap. Every face-rec-picked card also gets a small **"Not them?"** label under "✓ Detected", making the existing tap-to-correct flow visible instead of relying on people discovering it.

**Which camera, and swapping sides.** Each `CAMERA A`/`CAMERA B` header — on the setup screen and in the live-preview panel — now shows the physical device it's actually reading (`device 0`, `device 1`, a URL, or a file path for a recording), taken from the `--device` a worker was started with. That's the only honest identifier available; OpenCV doesn't expose a friendly hardware name cross-platform, so it's never invented one.

With 2 cameras, each side's header is a dropdown ("Camera A" / "Camera B") instead of static text — pick which physical camera is actually mounted at that end. It's a single swap (there are only two cameras), so changing one side's dropdown flips the other. This isn't just a setup-screen convenience: a worker's swings are reported under its own literal camera letter (`hit.side`), and the live scoreboard reads that per side panel, so getting this backwards would misattribute "last hitter" evidence for the whole match, not just get the roster wrong. The choice is stored once (`tt-camera-swap` in `localStorage`, same per-browser pattern as camera count) and applied everywhere a camera letter and a match side meet — detections, enrollment, the register wizard, and the live scoreboard's swing panel all read through the same `frontend/src/cameraMapping.ts` helper, so it can't drift out of sync between screens. Since it's per-browser, a second device (e.g. a phone at the table vs. a laptop showing `/decisions`) needs the swap set independently if it's used to view a live match — it isn't sent to the API.

**Registering a new face is scan-first**, not name-first: tapping Register (from the banner, or "Register new face" in the manual picker) opens a wizard — live preview with a scan animation, "Scanned successfully!", *then* it asks for a name, then "Registered successfully!" — matching what actually has to happen physically (the camera needs a clean look at the face regardless of what they're called). This needed a real API change, not just a frontend one: a face is captured *before* any player exists.

- `POST /api/capture/enroll-requests` — `player_id` is now optional. Given, it's the existing "teach this face to an already-known player" flow, unchanged. Omitted, it starts a scan-first request: the camera worker captures 5 samples the same way, then instead of uploading them straight to a player, it calls the new `POST .../{id}/scanned {vectors, evidence}`, which holds them server-side (not the browser) against that request.
- **Already registered?** Before accumulating samples, the worker checks whether the one face in view already confidently matches an existing gallery player (a single `recognise()` call, no 15 s wait) and, if so, calls `POST .../{id}/already-known {player_id, similarity}` instead of scanning them in as someone new. The wizard shows "Are you already **&lt;name&gt;**?" — yes assigns that existing player (no duplicate created); no restarts the scan. Verified live: a second scan of an already-registered face resolved in under a second, correctly matched.
- `GET /api/capture/enroll-requests/{id}` — the frontend polls this (every 500 ms while scanning) for `status`: `pending → scanned → done`, `pending → already_known → done`, or `failed` with a `reason` (the same specific messages as before: face too small, confidence too low, no face in view, gave up after 15s). `matched_player` is set once `already_known`.
- `POST /api/capture/enroll-requests/{id}/register {name}` — once `status` is `scanned`, creates the player and attaches the already-captured samples in one step. The raw face vectors never round-trip through the browser.

**Frame guide, not full frame.** The scan wizard's preview no longer treats the whole camera view as "in frame" — it overlays a small centred oval (42% of the preview's width, portrait 3:4) and dims everything outside it, with "Come into frame — fill the oval with your face" shown until a scan lands. Framing only counts once the face actually fills that oval, not just appears somewhere in a wide shot — matching what `ENROLL_MIN_SIZE_PX` actually requires to accept a sample.

### Player directory (`/players`)

Every enrollment success (scan-first register, or teaching an existing player's face) also crops and saves a small JPEG around the detected face box (`devices/camera.crop_face_jpeg`, `Player.face_photo`) — a snapshot from the moment it was last recognised and enrolled, not a live view. `GET /api/players/{id}/photo` serves it, or 404 if there isn't one yet; both `/players` and a player's own page fall back to the initials avatar until there is.

**Deleting.** A player page has a Danger zone with two separate actions:
- **Delete face data** — `DELETE /api/players/{id}/faces`, unchanged, now also clears `face_photo` (the photo belongs to the face data, so wiping one wipes both). They'll need to register again before a camera recognises them.
- **Delete player** — new `DELETE /api/players/{id}`. Refused (409) if they've appeared in any match, live, finished or abandoned — deleting them would break that match's own player references and, for a finished one, everyone else's history. Only ever removes a player who's never actually played.

### Match directory (`/matches`)

Lists every match ever created, newest first, with both sides' names (coloured by who won), mode, status, best-of, and a result summary — final game score for a single-game match, games won for best-of-N, or "N pt(s) so far" while it's still live. Cross-linked with `/players`.

- `GET /api/matches` — the full list, each row a compact summary (`side_a`/`side_b` as `PlayerRef`s, `games_won`, `points_played`, `created_at`/`closed_at`). Separate from `GET /api/matches/{id}`, which returns full live state for one match.
- `DELETE /api/matches/{id}` — each row has a delete button, confirm-guarded (the confirmation text differs for a live vs. a finished match). A **finished** match is unrated first — the same reversal `unrate_match` already does for undo — before being removed, so ratings stay correct; refused with **409** if a later match's rating already depends on this one (same refusal undo already gives, reused rather than re-implemented). A **live** or **abandoned** match is just removed, nothing to unrate. Either way its points cascade-delete with it, and its decision-log entries are removed too so nothing dangling references the deleted id.

### Swings and the last hitter

- **Pose.** MediaPipe's pose model finds up to 2 bodies per camera. Bodies are tracked from frame to frame by torso position.
- **Swing.** A swing is a burst of wrist speed above **4 shoulder-widths/s**, reported at its peak. Measuring in shoulder-widths makes the speed independent of distance from the camera. There's a 0.3 s refractory period.
- **Who swung.** A body belongs to the recognised face whose box contains its nose, and that link is trusted for 3 s. With no link, the swing is anonymous.
- **Last hitter.** The API takes the latest swing at most **1.5 s** before the ball's last table contact. No sensor contact, no swing in the window, or an anonymous swing → `unknown`. It never guesses.

### Table sensor

A contact mic under the table is read at 16 kHz. A contact is a sharp peak (first difference, which drops hum) above both the threshold and 6× the running noise floor, followed by a 60 ms refractory period. `--calibrate` records 5 s of quiet and 8 s of bounces, then suggests a threshold between the two. The serial mode is for a microcontroller that does its own detection and prints one line per contact.

### The decision log (`/decisions`)

Timestamps on this page are always **IST** (`Asia/Kolkata`), regardless of what timezone the server process itself is running in.

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
| `GET /api/matches` | | Every match, newest first, as compact summaries — powers `/matches`. |
| `DELETE /api/matches/{id}` | | Removes a match — unrates a finished one first (409 if a later match depends on that rating); a live/abandoned one just goes. |
| `POST /api/capture/detections` | `{camera, player_ids, faces?, threshold?}` | Who camera A/B recognises (smoothed), with per-face evidence. |
| `GET /api/capture/detections` | | `{A, B}` → `{players, unknown_present}` — the setup screen polls this; `unknown_present` drives its "new face, register?" banner. |
| `POST /api/capture/ticks` | `{ts?, strength?}` | One table contact. |
| `POST /api/capture/hits` | `{camera, player_id?, ts?, evidence?}` | One swing. |
| `POST /api/capture/device-params` | `{device, params}` | Thresholds a worker runs with (shown on `/decisions`). |
| `POST /api/capture/frames` | `{camera, image, ts?}` | A downscaled JPEG (base64) for the live-preview panel. |
| `GET /api/capture/preview/{camera}` | | The latest JPEG posted for that camera as a single image, or 404 if none yet. |
| `GET /api/capture/stream/{camera}` | | The same frames as a live `multipart/x-mixed-replace` MJPEG stream — what the scoreboard's preview panel actually points an `<img>` at. |
| `GET /api/capture/camera-status` | | `{A, B}` → `{active, last_seen, source}`, from how recently each posted a preview frame and the `--device` it reported at startup. |
| `GET /api/capture/camera-needed` | | `{needed}` — a worker polls this and pauses analysis/preview when false (see "Cameras pause..." above). |
| `GET /api/face-gallery` · `POST/DELETE /api/players/{id}/faces` | `{vectors, source, request_id?, photo?}` | The face gallery; `DELETE` also clears the stored photo. |
| `GET /api/players/{id}/photo` | | The player's last-enrolled face crop (JPEG), or 404 if none. |
| `DELETE /api/players/{id}` | | Removes a player — 409 if they've been in any match. |
| `POST/GET /api/capture/enroll-requests` | `{camera, player_id?}` | Asks a camera to learn a face — `player_id` given teaches an existing player; omitted starts a scan-first request (see "Match setup" above). |
| `GET /api/capture/enroll-requests/{id}` | | `{status, reason, matched_player}` — the setup screen polls this while scanning. |
| `POST /api/capture/enroll-requests/{id}/scanned` · `…/already-known` · `…/failed` · `…/register` | `{vectors, evidence}` · `{player_id, similarity}` · `{reason}` · `{name}` | The worker's scan-first terminal steps, and the frontend's name step. |

`ts` is epoch seconds from the shared clock. When it's omitted, the server's time is used.

**Live preview.** The scoreboard screen has a "Cameras" toggle at the bottom that opens a panel showing a genuinely live feed per running camera worker — the panel's `<img>` points straight at `GET /api/capture/stream/{camera}`, an MJPEG stream held open over one connection, so the browser renders each new frame as it arrives with no polling and no "refresh every N ms" ceiling on how live it looks. (`GET /api/capture/preview/{camera}` still exists as a single-JPEG fetch, e.g. for a one-off snapshot.) The panel auto-detects how many cameras are actually posting: one running camera shows one preview full-width; two show side by side; a camera that stops posting for `capture.FRAME_STALE_S` (10 s) drops out and the panel shows an inline warning naming which side is missing, instead of freezing on a stale frame. On a narrow (phone-width) screen there's room for only one preview at a time, so a switch button flips which camera is shown.

The worker posts a frame at most every `--preview-every` seconds (default 0.15 s, ~6-7 fps). A background thread grabs frames and posts the preview at the camera's own pace, so it stays live even when pose/face inference (run on the main thread from the same shared frame) can't keep up on a slow CPU — see `FrameGrabber` in `camera.py`.

`--api` defaults to `http://127.0.0.1:8000`, not `http://localhost:8000` — on Windows, resolving `localhost` can add ~2 s to *every* request (it tries IPv6 first, then falls back to IPv4), which was enough to make the preview (and detections/hits generally) visibly lag. Don't change it back to `localhost` unless you've confirmed that resolves instantly on your machine.

**Placeholders to tune on the real table** (all shown on `/decisions?view=rules`):
- hit window 1.5 s
- swing speed 4.0 shoulder-widths/s
- sensor threshold 0.08 (or use `--calibrate`)
- camera considered live: posted a preview frame in the last 10 s
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
