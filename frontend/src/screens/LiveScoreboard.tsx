import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, subscribeMatch } from "../api";
import { Avatar, AvatarPair } from "../components/Avatar";
import { Stage } from "../components/Stage";
import { C, SIDE, type SideKey } from "../theme";
import { activePlayer, partnerOf, teamName } from "../match";
import type { CameraStatus, MatchState, WinType } from "../types";

const TAGS: { key: WinType; label: string }[] = [
  { key: "smash", label: "Smash" },
  { key: "fault", label: "Fault" },
  { key: "net", label: "Net" },
  { key: "out", label: "Out" },
];

const CAMERA_STATUS_POLL_MS = 3000;

export function LiveScoreboard() {
  const matchId = Number(useParams().matchId);
  const navigate = useNavigate();
  const [m, setM] = useState<MatchState | null>(null);
  const [captureWinType, setCaptureWinType] = useState(false);
  const [tag, setTag] = useState<WinType | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cameraStatus, setCameraStatus] = useState<Record<SideKey, CameraStatus> | null>(null);
  const [showCameras, setShowCameras] = useState(false);

  useEffect(() => {
    api.config().then((c) => setCaptureWinType(c.capture_win_type));
    return subscribeMatch(matchId, setM);
  }, [matchId]);

  // Auto-detects how many camera workers are actually running (1 or 2), not
  // just assumed — see CameraPreviewPanel.
  useEffect(() => {
    let alive = true;
    const poll = () =>
      api.cameraStatus().then((s) => alive && setCameraStatus(s), () => {});
    poll();
    const t = window.setInterval(poll, CAMERA_STATUS_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  const run = useCallback(async (fn: () => Promise<MatchState>) => {
    setBusy(true);
    setError(null);
    try {
      setM(await fn());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  const rematch = useCallback(async () => {
    if (!m) return;
    setBusy(true);
    setError(null);
    try {
      const sideA = m.sides.A.players.map((p) => p.id);
      const sideB = m.sides.B.players.map((p) => p.id);
      const nm = await api.createMatch({
        mode: m.mode,
        side_a: sideA,
        side_b: sideB,
        first_server: sideA[0],
        first_receiver: sideB[0],
        format: m.format,
      });
      navigate(`/live/${nm.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }, [m, navigate]);

  const score = (side: SideKey, winType: WinType | null) => {
    setTag(null);
    run(() => api.scorePoint(matchId, side, winType));
  };

  const undo = useCallback(() => {
    setTag(null);
    run(() => api.undo(matchId));
  }, [matchId, run]);

  // Also on an attached keyboard: Backspace / Ctrl+Z.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Backspace" || (e.key === "z" && (e.ctrlKey || e.metaKey))) {
        e.preventDefault();
        undo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo]);

  if (!m) return <Stage width={1280} height={720} background={C.bgScoreboard}>{null}</Stage>;

  const live = m.status === "live";
  const tap = (side: SideKey) => {
    if (busy) return;
    if (!live) {
      navigate(`/players/${activePlayer(m, side).id}`);
    } else {
      score(side, tag);
    }
  };

  const header = live
    ? `LIVE · GAME ${m.game_number} OF ${m.format.best_of}`
    : m.status === "finished"
      ? `FINAL · SIDE ${m.winner} WINS`
      : "ABANDONED";

  return (
    <Stage width={1280} height={720} background={C.bgScoreboard}>
      <div
        style={{
          width: 1280,
          height: 720,
          boxSizing: "border-box",
          background: C.bgScoreboard,
          color: C.text,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          position: "relative",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "18px 40px 0",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                background: live ? C.live : C.faint,
              }}
            />
            <div
              className="mono"
              style={{ fontSize: 12, letterSpacing: 2, color: C.muted, fontWeight: 700 }}
            >
              {header}
            </div>
            {error && (
              <div className="mono" style={{ fontSize: 12, color: C.coral, fontWeight: 700 }}>
                {error}
              </div>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {m.status !== "abandoned" && m.points_played > 0 && (
              <UndoButton disabled={busy} onClick={undo} />
            )}
            {live && m.deuce && <Pill>DEUCE</Pill>}
            {m.status === "abandoned" && (
              <Link to="/setup" style={{ textDecoration: "none" }}>
                <Pill>NEW MATCH</Pill>
              </Link>
            )}
          </div>
        </div>

        <div
          style={{
            flexGrow: 1,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 48,
            padding: "4px 48px",
            minHeight: 0,
          }}
        >
          <SideScore m={m} side="A" />
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
            <div className="digits" style={{ fontSize: 22, color: C.borderDashed }}>
              GAMES
            </div>
            <div className="digits" style={{ fontSize: 34, color: C.text, letterSpacing: 1 }}>
              {m.games.A}&ndash;{m.games.B}
            </div>
          </div>
          <SideScore m={m} side="B" />
        </div>

        {live && captureWinType && <TagChips tag={tag} onChange={setTag} />}

        <div style={{ display: "flex", height: 226, borderTop: `1px solid ${C.surface}` }}>
          <BarZone m={m} side="A" onTap={tap} disabled={busy} />
          <BarZone m={m} side="B" onTap={tap} disabled={busy} />
        </div>

        <CamerasToggle status={cameraStatus} onClick={() => setShowCameras(true)} />
        {showCameras && (
          <CameraPreviewPanel status={cameraStatus} onClose={() => setShowCameras(false)} />
        )}

        {m.status === "finished" && <MatchEndOverlay m={m} busy={busy} onRematch={rematch} />}
      </div>
    </Stage>
  );
}

function Pill({ children }: { children: string }) {
  return (
    <div
      style={{
        padding: "6px 16px",
        background: "rgba(200,255,77,0.12)",
        border: "1px solid rgba(200,255,77,0.4)",
        borderRadius: 999,
      }}
    >
      <div
        className="mono"
        style={{ fontSize: 11, letterSpacing: 1.5, color: C.lime, fontWeight: 700 }}
      >
        {children}
      </div>
    </div>
  );
}

/**
 * Not in the design: a neutral twin of the DEUCE pill. The 44px hit area is
 * padded out with negative margin so the header keeps the design's height.
 */
function UndoButton({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label="Undo last point"
      style={{ background: "none", border: "none", padding: 9, margin: -9 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 14px",
          border: `1px solid ${C.border}`,
          borderRadius: 999,
          opacity: disabled ? 0.5 : 1,
        }}
      >
        <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke={C.muted} strokeWidth={2.5}>
          <path d="M9 14L4 9l5-5" />
          <path d="M4 9h10.5a5.5 5.5 0 010 11H11" />
        </svg>
        <div
          className="mono"
          style={{ fontSize: 11, letterSpacing: 1.5, color: C.muted, fontWeight: 700 }}
        >
          UNDO
        </div>
      </div>
    </button>
  );
}

function MatchEndOverlay({
  m,
  busy,
  onRematch,
}: {
  m: MatchState;
  busy: boolean;
  onRematch: () => void;
}) {
  const winnerSide = m.winner as SideKey;
  const loserSide: SideKey = winnerSide === "A" ? "B" : "A";
  const winners = teamName(m.sides[winnerSide].players);
  const losers = teamName(m.sides[loserSide].players);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "rgba(11,13,16,0.94)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        textAlign: "center",
        padding: 40,
      }}
    >
      <div
        className="mono"
        style={{
          fontSize: 12,
          letterSpacing: 2,
          color: SIDE[winnerSide].color,
          fontWeight: 800,
          textTransform: "uppercase",
        }}
      >
        Match over
      </div>
      <h1 style={{ margin: 0, fontSize: 46, fontWeight: 800 }}>Congratulations!</h1>
      <div style={{ fontSize: 18, color: C.textSoft, fontWeight: 700 }}>
        {winners} beat {losers}, {m.games[winnerSide]}&ndash;{m.games[loserSide]}
      </div>

      <div style={{ display: "flex", gap: 14, marginTop: 12 }}>
        <button
          onClick={onRematch}
          disabled={busy}
          style={{
            padding: "14px 32px",
            border: "none",
            borderRadius: 14,
            background: C.lime,
            color: C.bg,
            fontSize: 15,
            fontWeight: 800,
            letterSpacing: 0.3,
            opacity: busy ? 0.6 : 1,
          }}
        >
          Rematch
        </button>
        <Link to="/setup" style={{ textDecoration: "none" }}>
          <div
            style={{
              padding: "14px 32px",
              border: `1.5px solid ${C.border}`,
              borderRadius: 14,
              color: C.text,
              fontSize: 15,
              fontWeight: 800,
              letterSpacing: 0.3,
            }}
          >
            New Match
          </div>
        </Link>
      </div>

      <div style={{ display: "flex", gap: 20, marginTop: 10 }}>
        {(["A", "B"] as const).flatMap((side) =>
          m.sides[side].players.map((p) => (
            <Link
              key={p.id}
              to={`/players/${p.id}`}
              style={{ fontSize: 12, color: C.faint, fontWeight: 600, textDecoration: "underline" }}
            >
              {p.name} stats
            </Link>
          )),
        )}
      </div>
    </div>
  );
}

function TagChips({
  tag,
  onChange,
}: {
  tag: WinType | null;
  onChange: (t: WinType | null) => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        padding: "0 40px 10px",
      }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: 0.4,
          color: C.muted,
          textTransform: "uppercase",
          marginRight: 4,
        }}
      >
        How it ended &middot; optional
      </div>
      {TAGS.map(({ key, label }) => {
        const on = tag === key;
        return (
          <button
            key={key}
            aria-pressed={on}
            onClick={() => onChange(on ? null : key)}
            style={{
              padding: "5px 14px",
              borderRadius: 999,
              border: `1.5px solid ${on ? C.lime : C.border}`,
              background: on ? "rgba(200,255,77,0.12)" : "transparent",
              color: on ? C.lime : C.muted,
              fontSize: 12,
              fontWeight: on ? 700 : 600,
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

const CAMERA_SIDES: SideKey[] = ["A", "B"];

/** Floating pill, bottom-right: opens the live-preview panel. Warns inline
 * (no separate alert) when fewer than 2 camera workers are actually posting. */
function CamerasToggle({
  status,
  onClick,
}: {
  status: Record<SideKey, CameraStatus> | null;
  onClick: () => void;
}) {
  const activeCount = status ? CAMERA_SIDES.filter((s) => status[s]?.active).length : null;
  const warn = activeCount !== null && activeCount < 2;
  return (
    <button
      onClick={onClick}
      aria-label="Show camera previews"
      style={{
        position: "absolute",
        right: 16,
        bottom: 16,
        zIndex: 5,
        display: "flex",
        alignItems: "center",
        gap: 7,
        padding: "8px 14px",
        borderRadius: 999,
        border: `1px solid ${warn ? C.coral : C.border}`,
        background: "rgba(11,13,16,0.85)",
        color: warn ? C.coral : C.muted,
      }}
    >
      <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <rect x={2} y={6} width={14} height={12} rx={2} />
        <path d="M16 10l6-3v10l-6-3" />
      </svg>
      <div className="mono" style={{ fontSize: 11, letterSpacing: 1.5, fontWeight: 700 }}>
        CAMERAS{activeCount !== null ? ` · ${activeCount}/2` : ""}
      </div>
    </button>
  );
}

/** Shows what the backend's camera workers (app/devices/camera.py) are
 * actually seeing — auto-detects 1 vs 2 running cameras from /api/capture/camera-status
 * and warns inline about whichever side is missing, instead of assuming both
 * are connected. A switch control picks which feed is big when there's only
 * room for one (e.g. on a phone). */
function CameraPreviewPanel({
  status,
  onClose,
}: {
  status: Record<SideKey, CameraStatus> | null;
  onClose: () => void;
}) {
  const [big, setBig] = useState<SideKey | null>(null);

  const active = CAMERA_SIDES.filter((s) => status?.[s]?.active);
  const missing = CAMERA_SIDES.filter((s) => !status?.[s]?.active);
  // With only one camera live there's nothing to switch between — show it big.
  const shown = active.length === 1 ? active[0] : big;
  const feeds = shown ? [shown] : active;

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 10,
        background: "rgba(11,13,16,0.97)",
        display: "flex",
        flexDirection: "column",
        padding: "24px 40px 30px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div
          className="mono"
          style={{ fontSize: 12, letterSpacing: 2, color: C.muted, fontWeight: 700, textTransform: "uppercase" }}
        >
          Cameras &middot; {active.length} of 2 live
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {active.length === 2 && (
            <button
              onClick={() => setBig((b) => (b ? null : "A"))}
              style={{
                padding: "6px 14px",
                borderRadius: 999,
                border: `1px solid ${C.border}`,
                background: "transparent",
                color: C.muted,
                fontSize: 12,
                fontWeight: 700,
              }}
            >
              {big ? "Show both" : "Switch view"}
            </button>
          )}
          <button
            onClick={onClose}
            aria-label="Close camera preview"
            style={{
              width: 32,
              height: 32,
              borderRadius: 999,
              border: `1px solid ${C.border}`,
              background: "transparent",
              color: C.muted,
              fontSize: 14,
              fontWeight: 700,
            }}
          >
            &times;
          </button>
        </div>
      </div>

      {missing.length > 0 && (
        <div
          style={{
            marginTop: 14,
            padding: "10px 14px",
            borderRadius: 10,
            border: `1px solid ${C.coral}`,
            background: "rgba(255,107,74,0.1)",
            color: C.coral,
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          {missing.length === 2
            ? "No cameras detected — start a camera worker (python -m app.devices.camera) to see a preview."
            : `Only Camera ${active[0]} is running — Side ${missing[0]} has no preview until a second camera connects.`}
        </div>
      )}

      <div style={{ flexGrow: 1, display: "flex", gap: 16, marginTop: 16, minHeight: 0 }}>
        {feeds.length > 0 ? (
          feeds.map((side) => <CameraFeed key={side} side={side} />)
        ) : (
          <div
            style={{
              flexGrow: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: C.faint,
              fontSize: 14,
            }}
          >
            Waiting for a camera worker to connect&hellip;
          </div>
        )}
      </div>

      {shown && active.length === 2 && (
        <div style={{ display: "flex", justifyContent: "center", gap: 8, marginTop: 16 }}>
          {CAMERA_SIDES.map((side) => {
            const on = shown === side;
            return (
              <button
                key={side}
                aria-pressed={on}
                onClick={() => setBig(side)}
                style={{
                  padding: "8px 20px",
                  borderRadius: 999,
                  border: `1.5px solid ${on ? SIDE[side].color : C.border}`,
                  background: on ? `rgba(${SIDE[side].tint},0.12)` : "transparent",
                  color: on ? SIDE[side].color : C.muted,
                  fontSize: 13,
                  fontWeight: on ? 700 : 600,
                }}
              >
                Side {side}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CameraFeed({ side }: { side: SideKey }) {
  const [ok, setOk] = useState(true);
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        borderRadius: 14,
        overflow: "hidden",
        border: `1px solid ${C.border}`,
        background: C.surface,
        minWidth: 0,
      }}
    >
      <div
        className="mono"
        style={{
          padding: "8px 12px",
          fontSize: 11,
          fontWeight: 800,
          letterSpacing: 1.5,
          color: SIDE[side].color,
        }}
      >
        CAMERA {side}
      </div>
      <div style={{ flexGrow: 1, position: "relative", background: "#000" }}>
        <img
          src={api.streamUrl(side)}
          alt={`Live preview from camera ${side}`}
          onLoad={() => setOk(true)}
          onError={() => setOk(false)}
          style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
        />
        {!ok && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: C.faint,
              fontSize: 12,
              background: "rgba(0,0,0,0.6)",
            }}
          >
            No frame yet
          </div>
        )}
      </div>
    </div>
  );
}

function SideScore({ m, side }: { m: MatchState; side: SideKey }) {
  const { color } = SIDE[side];
  const player = activePlayer(m, side);
  const serving = m.server.side === side;
  const filled = serving && m.status === "live" ? m.serves_remaining : 0;
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 10,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Avatar player={player} size={28} fontSize={11} background={color} color={C.bgScoreboard} />
        <div style={{ textAlign: "left" }}>
          <div style={{ fontSize: 15, fontWeight: 800 }}>{player.name}</div>
          <div style={{ fontSize: 10, color: C.subtle, fontWeight: 600 }}>Side {side}</div>
        </div>
        {serving && m.status === "live" && (
          <svg
            width={14}
            height={14}
            viewBox="0 0 24 24"
            fill={C.lime}
            style={{ marginLeft: 2 }}
            aria-label="Serving"
          >
            <circle cx={12} cy={12} r={6} />
          </svg>
        )}
      </div>
      <div className="digits" style={{ fontSize: 140, lineHeight: 1, color, letterSpacing: 2 }}>
        {m.score[side]}
      </div>
      <div style={{ display: "flex", gap: 5 }} aria-label={serving ? `${filled} serves left` : undefined}>
        {Array.from({ length: m.serves_in_turn }, (_, i) => (
          <div
            key={i}
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: i < filled ? color : C.border,
            }}
          />
        ))}
      </div>
    </div>
  );
}

function BarZone({
  m,
  side,
  onTap,
  disabled,
}: {
  m: MatchState;
  side: SideKey;
  onTap: (s: SideKey) => void;
  disabled: boolean;
}) {
  const s = SIDE[side];
  const lead = activePlayer(m, side);
  const partner = partnerOf(m, side, lead);
  const names = teamName([lead, partner]);
  const live = m.status === "live";
  const hit = m.last_hit[side]?.player?.name ?? "unknown";
  return (
    <button
      aria-label={live ? `Point to ${names}` : `Stats for ${lead.name}`}
      onClick={() => onTap(side)}
      disabled={disabled || m.status === "abandoned"}
      style={{
        flex: 1,
        border: "none",
        borderRight: side === "A" ? `2px solid ${C.bgScoreboard}` : "none",
        background: s.barBg,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        cursor: "pointer",
      }}
    >
      <AvatarPair
        lead={lead}
        partner={partner}
        size={40}
        fontSize={14}
        overlap={12}
        color={s.color}
        leadText={C.bgScoreboard}
        partnerFill={s.barPartner}
        ring={s.barBg}
      />
      <div
        className="mono"
        style={{ fontSize: 13, letterSpacing: 2, color: s.color, fontWeight: 800 }}
      >
        {live ? `POINT · SIDE ${side}` : `STATS · SIDE ${side}`}
      </div>
      <div style={{ fontSize: 11, color: s.barHint }}>
        {live ? <>last hit &mdash; {hit}</> : <>{lead.name}</>}
      </div>
    </button>
  );
}
