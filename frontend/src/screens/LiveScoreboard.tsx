import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, subscribeMatch } from "../api";
import { Avatar, AvatarPair } from "../components/Avatar";
import { Stage } from "../components/Stage";
import { C, SIDE, type SideKey } from "../theme";
import { activePlayer, partnerOf, teamName } from "../match";
import type { MatchState, WinType } from "../types";

const TAGS: { key: WinType; label: string }[] = [
  { key: "smash", label: "Smash" },
  { key: "fault", label: "Fault" },
  { key: "net", label: "Net" },
  { key: "out", label: "Out" },
];

export function LiveScoreboard() {
  const matchId = Number(useParams().matchId);
  const navigate = useNavigate();
  const [m, setM] = useState<MatchState | null>(null);
  const [captureWinType, setCaptureWinType] = useState(false);
  const [tag, setTag] = useState<WinType | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.config().then((c) => setCaptureWinType(c.capture_win_type));
    return subscribeMatch(matchId, setM);
  }, [matchId]);

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
            {!live && (
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
