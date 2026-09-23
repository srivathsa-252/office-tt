import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Avatar, CheckIcon } from "../components/Avatar";
import { Stage } from "../components/Stage";
import { C, SIDE, type SideKey } from "../theme";
import type { Format, PlayerRef } from "../types";

type Mode = "singles" | "doubles";
type Slot = { player: PlayerRef; source: "detected" | "manual" } | null;
const SIDES: SideKey[] = ["A", "B"];
const DETECTION_POLL_MS = 1000;

export function MatchSetup() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("doubles");
  const [format, setFormat] = useState<Format | null>(null);
  const [detected, setDetected] = useState<Record<SideKey, PlayerRef[]>>({ A: [], B: [] });
  // Manual picks, per side and slot index — the fallback when face-rec can't see someone.
  const [manual, setManual] = useState<Record<SideKey, (PlayerRef | null)[]>>({ A: [], B: [] });
  const [firstServer, setFirstServer] = useState<Record<SideKey, number | null>>({ A: null, B: null });
  const [picking, setPicking] = useState<{ side: SideKey; index: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const need = mode === "singles" ? 1 : 2;

  useEffect(() => {
    api.config().then((c) => setFormat(c.default_format));
    let alive = true;
    const poll = () =>
      api.detections().then(
        (d) => alive && setDetected(d),
        () => {},
      );
    poll();
    const t = window.setInterval(poll, DETECTION_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  const slots = useMemo(() => {
    const manualIds = new Set(
      SIDES.flatMap((s) => manual[s].slice(0, need).filter(Boolean).map((p) => p!.id)),
    );
    const taken = new Set(manualIds);
    const out = {} as Record<SideKey, Slot[]>;
    for (const side of SIDES) {
      const pool = detected[side].filter((p) => !taken.has(p.id));
      out[side] = Array.from({ length: need }, (_, i) => {
        const m = manual[side][i];
        if (m) return { player: m, source: "manual" as const };
        const d = pool.shift();
        if (!d) return null;
        taken.add(d.id);
        return { player: d, source: "detected" as const };
      });
    }
    return out;
  }, [detected, manual, need]);

  const roster = (side: SideKey) =>
    slots[side].filter((s): s is NonNullable<Slot> => !!s).map((s) => s.player);
  const complete = SIDES.every((s) => roster(s).length === need);

  // Singles: pick either player to serve first. Doubles: Side A serves first
  // (A1→B1 rotation); pick A1 and B1 within each pair.
  const serveOptions = (side: SideKey): PlayerRef[] =>
    mode === "singles" ? (side === "A" ? [...roster("A"), ...roster("B")] : []) : roster(side);
  const chosen = (side: SideKey): number | null => {
    const opts = serveOptions(side);
    const pick = firstServer[side];
    return opts.some((p) => p.id === pick) ? pick : (opts[0]?.id ?? null);
  };

  const start = async () => {
    if (!complete) return;
    const a = roster("A").map((p) => p.id);
    const b = roster("B").map((p) => p.id);
    let server: number, receiver: number;
    if (mode === "singles") {
      server = chosen("A")!;
      receiver = server === a[0] ? b[0] : a[0];
    } else {
      server = chosen("A")!;
      receiver = chosen("B")!;
    }
    try {
      const m = await api.createMatch({
        mode,
        side_a: a,
        side_b: b,
        first_server: server,
        first_receiver: receiver,
      });
      navigate(`/live/${m.id}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const setManualSlot = (side: SideKey, index: number, player: PlayerRef | null) =>
    setManual((prev) => {
      const next = { ...prev, [side]: [...prev[side]] };
      next[side][index] = player;
      return next;
    });

  const usedIds = new Set(SIDES.flatMap((s) => roster(s).map((p) => p.id)));

  return (
    <Stage width={390} height={844} background={C.bg}>
      <div
        style={{
          width: 390,
          height: 844,
          boxSizing: "border-box",
          background: C.bg,
          color: C.text,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          position: "relative",
        }}
      >
        <div style={{ padding: "28px 24px 16px", display: "flex", flexDirection: "column", gap: 4 }}>
          <div
            style={{
              fontSize: 12,
              letterSpacing: 1.5,
              color: C.subtle,
              fontWeight: 700,
              textTransform: "uppercase",
            }}
          >
            New Match
          </div>
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 800 }}>Set up the table</h1>
        </div>

        <div
          style={{
            padding: "0 24px",
            display: "flex",
            flexDirection: "column",
            gap: 20,
            overflowY: "auto",
            flexGrow: 1,
          }}
        >
          <div
            style={{
              display: "flex",
              background: C.surface,
              borderRadius: 12,
              padding: 4,
              gap: 4,
            }}
          >
            {(["singles", "doubles"] as const).map((m) => (
              <button
                key={m}
                aria-pressed={mode === m}
                onClick={() => setMode(m)}
                style={{
                  flex: 1,
                  padding: "10px 0",
                  border: "none",
                  borderRadius: 9,
                  background: mode === m ? C.lime : C.surfaceRaised,
                  color: mode === m ? "#12151A" : C.text,
                  fontSize: 14,
                  fontWeight: 700,
                }}
              >
                {m === "singles" ? "Singles" : "Doubles"}
              </button>
            ))}
          </div>

          {SIDES.map((side) => {
            const opts = serveOptions(side);
            return (
              <div key={side} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div
                    style={{ width: 8, height: 8, borderRadius: "50%", background: SIDE[side].color }}
                  />
                  <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 0.3, color: C.muted }}>
                    CAMERA {side} &middot; SIDE {side}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 12 }}>
                  {slots[side].map((slot, i) => (
                    <PlayerCard
                      key={i}
                      side={side}
                      slot={slot}
                      onClick={() => setPicking({ side, index: i })}
                    />
                  ))}
                </div>
                {opts.length > 1 && (
                  <>
                    <div style={{ fontSize: 12, color: C.subtle }}>Serves first</div>
                    <div style={{ display: "flex", gap: 8 }}>
                      {opts.map((p) => {
                        const on = chosen(side) === p.id;
                        return (
                          <button
                            key={p.id}
                            aria-pressed={on}
                            onClick={() => setFirstServer((prev) => ({ ...prev, [side]: p.id }))}
                            style={{
                              flex: 1,
                              padding: "9px 0",
                              borderRadius: 10,
                              border: `1.5px solid ${on ? C.lime : C.border}`,
                              background: on ? "rgba(200,255,77,0.1)" : "transparent",
                              color: on ? C.lime : C.muted,
                              fontSize: 13,
                              fontWeight: on ? 700 : 600,
                            }}
                          >
                            {p.name}
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            );
          })}

          {format && (
            <div
              style={{
                background: C.surface,
                border: `1px solid ${C.border}`,
                borderRadius: 14,
                padding: 16,
                display: "flex",
                flexDirection: "column",
                gap: 10,
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  fontWeight: 700,
                  letterSpacing: 0.5,
                  color: C.muted,
                  textTransform: "uppercase",
                }}
              >
                Format
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(2,minmax(0,1fr))",
                  gap: 10,
                }}
              >
                <FormatCell
                  big={`${format.points_to_win} pts`}
                  small={`win by ${format.win_margin}`}
                />
                <FormatCell
                  big={`${format.serves_per_turn} / serve`}
                  small={`${format.deuce_serve_rotation} after ${format.deuce_trigger}–${format.deuce_trigger}`}
                />
              </div>
            </div>
          )}
          {error && <div style={{ fontSize: 12, color: C.coral, fontWeight: 700 }}>{error}</div>}
        </div>

        <div
          style={{
            padding: "16px 24px 28px",
            background: "linear-gradient(180deg,rgba(15,18,22,0) 0%,#0F1216 40%)",
          }}
        >
          <button
            onClick={start}
            disabled={!complete}
            style={{
              width: "100%",
              padding: "16px 0",
              border: "none",
              borderRadius: 14,
              background: C.lime,
              color: C.bg,
              fontSize: 16,
              fontWeight: 800,
              letterSpacing: 0.3,
              opacity: complete ? 1 : 0.4,
            }}
          >
            Start Match
          </button>
        </div>

        {picking && (
          <PlayerPicker
            side={picking.side}
            excluded={usedIds}
            canClear={!!manual[picking.side][picking.index]}
            onPick={(p) => {
              setManualSlot(picking.side, picking.index, p);
              setPicking(null);
            }}
            onClose={() => setPicking(null)}
          />
        )}
      </div>
    </Stage>
  );
}

function FormatCell({ big, small }: { big: string; small: string }) {
  return (
    <div>
      <div
        style={{
          fontSize: 20,
          fontWeight: 800,
          fontFamily: "'Bebas Neue',sans-serif",
          letterSpacing: 0.5,
        }}
      >
        {big}
      </div>
      <div style={{ fontSize: 11, color: C.subtle }}>{small}</div>
    </div>
  );
}

function PlayerCard({ side, slot, onClick }: { side: SideKey; slot: Slot; onClick: () => void }) {
  const color = SIDE[side].color;
  if (!slot) {
    return (
      <button
        onClick={onClick}
        aria-label={`Waiting for face on side ${side} — pick a player manually`}
        style={{
          flex: 1,
          boxSizing: "content-box", // the design's cards are divs, not buttons
          background: C.surface,
          border: `1px dashed ${C.borderDashed}`,
          borderRadius: 14,
          padding: 14,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 8,
          minHeight: 98,
        }}
      >
        <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={C.faint} strokeWidth={2}>
          <circle cx={12} cy={8} r={4} />
          <path d="M4 20c0-4 3.6-6 8-6s8 2 8 6" />
        </svg>
        <div style={{ fontSize: 12, color: C.faint, fontWeight: 600 }}>Waiting for face&hellip;</div>
      </button>
    );
  }
  return (
    <button
      onClick={onClick}
      style={{
        flex: 1,
        boxSizing: "content-box",
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 14,
        padding: 14,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 8,
      }}
    >
      <Avatar player={slot.player} size={52} fontSize={18} background={color} color={C.bg} />
      <div style={{ fontSize: 14, fontWeight: 700 }}>{slot.player.name}</div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          fontSize: 11,
          color: slot.source === "detected" ? color : C.subtle,
          fontWeight: 700,
        }}
      >
        {slot.source === "detected" ? (
          <>
            <CheckIcon />
            Detected
          </>
        ) : (
          "Picked manually"
        )}
      </div>
    </button>
  );
}

/** Manual fallback for face-rec (not in the design — see README). */
function PlayerPicker({
  side,
  excluded,
  canClear,
  onPick,
  onClose,
}: {
  side: SideKey;
  excluded: Set<number>;
  canClear: boolean;
  onPick: (p: PlayerRef | null) => void;
  onClose: () => void;
}) {
  const [players, setPlayers] = useState<PlayerRef[]>([]);
  const [name, setName] = useState("");
  useEffect(() => {
    api.players().then(setPlayers);
  }, []);
  const add = async () => {
    if (!name.trim()) return;
    onPick(await api.addPlayer(name.trim()));
  };
  const color = SIDE[side].color;
  const available = players.filter((p) => !excluded.has(p.id));

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: "rgba(11,13,16,0.8)",
        display: "flex",
        alignItems: "flex-end",
      }}
    >
      <div
        role="dialog"
        aria-label={`Pick a player for side ${side}`}
        style={{
          width: "100%",
          maxHeight: "75%",
          boxSizing: "border-box",
          background: C.surface,
          borderTop: `1px solid ${C.border}`,
          borderRadius: "18px 18px 0 0",
          padding: "20px 24px 28px",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div
          style={{
            fontSize: 12,
            fontWeight: 700,
            letterSpacing: 0.5,
            color: C.muted,
            textTransform: "uppercase",
          }}
        >
          Pick player &middot; Side {side}
        </div>
        <div style={{ overflowY: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
          {available.map((p) => (
            <button
              key={p.id}
              onClick={() => onPick(p)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 12px",
                background: C.bg,
                border: `1px solid ${C.border}`,
                borderRadius: 12,
                fontSize: 14,
                fontWeight: 700,
                textAlign: "left",
              }}
            >
              <Avatar player={p} size={32} fontSize={12} background={color} color={C.bg} />
              {p.name}
            </button>
          ))}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            add();
          }}
          style={{ display: "flex", gap: 8 }}
        >
          <label htmlFor="new-player" style={{ position: "absolute", left: -9999 }}>
            New player name
          </label>
          <input
            id="new-player"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="New player name"
            style={{
              flex: 1,
              padding: "10px 12px",
              background: C.bg,
              border: `1px solid ${C.border}`,
              borderRadius: 10,
              color: C.text,
              fontFamily: "inherit",
              fontSize: 14,
            }}
          />
          <button
            type="submit"
            style={{
              padding: "0 16px",
              border: "none",
              borderRadius: 10,
              background: C.lime,
              color: C.bg,
              fontWeight: 800,
              fontSize: 14,
            }}
          >
            Add
          </button>
        </form>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          {canClear ? (
            <LinkButton onClick={() => onPick(null)}>Clear &amp; use face-rec</LinkButton>
          ) : (
            <span />
          )}
          <LinkButton onClick={onClose}>Cancel</LinkButton>
        </div>
      </div>
    </div>
  );
}

function LinkButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: "none",
        border: "none",
        padding: 0,
        fontSize: 13,
        color: C.faint,
        fontWeight: 600,
        textDecoration: "underline",
      }}
    >
      {children}
    </button>
  );
}
