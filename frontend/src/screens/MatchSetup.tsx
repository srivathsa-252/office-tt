import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Avatar, CheckIcon } from "../components/Avatar";
import { CameraPreviewPanel, CamerasToggle, useCameraStatus } from "../components/CameraPreview";
import { RegisterFaceWizard } from "../components/RegisterFace";
import { C, SIDE, type SideKey } from "../theme";
import type { Detections, Format, PlayerRef } from "../types";

type Mode = "singles" | "doubles";
type Slot = { player: PlayerRef; source: "detected" | "manual" } | null;
const SIDES: SideKey[] = ["A", "B"];
const DETECTION_POLL_MS = 1000;
const NO_DETECTIONS: Detections = {
  A: { players: [], unknown_present: false },
  B: { players: [], unknown_present: false },
};
const CAMERA_COUNT_KEY = "tt-camera-count";
const SHOW_CAMERAS_KEY = "tt-setup-show-cameras";
const SERVES_PER_TURN_OPTIONS = [3, 5];
const BEST_OF_OPTIONS = [1, 3, 5];

function loadCameraCount(): 1 | 2 {
  return localStorage.getItem(CAMERA_COUNT_KEY) === "1" ? 1 : 2;
}

const card = {
  background: C.surface,
  border: `1px solid ${C.border}`,
  borderRadius: 14,
  padding: 16,
} as const;

const sectionLabel = {
  fontSize: 12,
  fontWeight: 700,
  letterSpacing: 0.5,
  color: C.muted,
  textTransform: "uppercase",
} as const;

export function MatchSetup() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("doubles");
  const [format, setFormat] = useState<Format | null>(null);
  const [cameraCount, setCameraCount] = useState<1 | 2>(loadCameraCount);
  const [detected, setDetected] = useState<Detections>(NO_DETECTIONS);
  // Manual picks, per side and slot index — the fallback when face-rec can't see someone.
  const [manual, setManual] = useState<Record<SideKey, (PlayerRef | null)[]>>({ A: [], B: [] });
  const [firstServer, setFirstServer] = useState<Record<SideKey, number | null>>({ A: null, B: null });
  const [picking, setPicking] = useState<{ side: SideKey; index: number } | null>(null);
  const [registering, setRegistering] = useState<{ side: SideKey; index: number } | null>(null);
  const [newFaceDismissed, setNewFaceDismissed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cameraStatus = useCameraStatus();
  const [showCameras, setShowCameras] = useState(
    () => localStorage.getItem(SHOW_CAMERAS_KEY) !== "0",
  );

  const need = mode === "singles" ? 1 : 2;

  useEffect(() => {
    localStorage.setItem(CAMERA_COUNT_KEY, String(cameraCount));
  }, [cameraCount]);

  useEffect(() => {
    localStorage.setItem(SHOW_CAMERAS_KEY, showCameras ? "1" : "0");
  }, [showCameras]);

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

  // In 1-camera mode there's only camera A: both sides draw from its
  // detections, and the person is assigned to whichever side still needs one.
  const cameraFor = (side: SideKey): SideKey => (cameraCount === 1 ? "A" : side);

  const slots = useMemo(() => {
    const manualIds = new Set(
      SIDES.flatMap((s) => manual[s].slice(0, need).filter(Boolean).map((p) => p!.id)),
    );
    const taken = new Set(manualIds);
    const out = {} as Record<SideKey, Slot[]>;
    for (const side of SIDES) {
      const pool = detected[cameraFor(side)].players.filter((p) => !taken.has(p.id));
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
  }, [detected, manual, need, cameraCount]);

  const roster = (side: SideKey) =>
    slots[side].filter((s): s is NonNullable<Slot> => !!s).map((s) => s.player);
  const complete = SIDES.every((s) => roster(s).length === need);

  // The first still-open slot whose camera currently sees a face it can't
  // confidently match to anyone — prompts "register this person?" instead of
  // waiting for a manual tap.
  const newFaceSlot = (() => {
    for (const side of SIDES) {
      const i = slots[side].findIndex((s) => s === null);
      if (i !== -1 && detected[cameraFor(side)].unknown_present) return { side, index: i };
    }
    return null;
  })();

  useEffect(() => {
    if (!newFaceSlot) setNewFaceDismissed(false);
  }, [!!newFaceSlot]);

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
        format: format
          ? { serves_per_turn: format.serves_per_turn, best_of: format.best_of }
          : undefined,
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
    <div style={{ minHeight: "100%", background: C.bg, color: C.text }}>
      <div
        style={{
          maxWidth: 760,
          margin: "0 auto",
          padding: "24px 20px 100px",
          boxSizing: "border-box",
          display: "flex",
          flexDirection: "column",
          gap: 20,
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
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
          <h1 style={{ margin: 0, fontSize: 28, fontWeight: 800 }}>Set up the table</h1>
        </div>

        <div
          style={{
            display: "flex",
            background: C.surface,
            borderRadius: 12,
            padding: 4,
            gap: 4,
            maxWidth: 320,
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

        {newFaceSlot && !newFaceDismissed && (
          <NewFaceBanner
            side={newFaceSlot.side}
            onRegister={() => setRegistering({ side: newFaceSlot.side, index: newFaceSlot.index })}
            onDismiss={() => setNewFaceDismissed(true)}
          />
        )}

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 20,
          }}
        >
          {SIDES.map((side) => {
            const opts = serveOptions(side);
            return (
              <div key={side} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div
                    style={{ width: 8, height: 8, borderRadius: "50%", background: SIDE[side].color }}
                  />
                  <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 0.3, color: C.muted }}>
                    CAMERA {cameraFor(side)}
                    {cameraCount === 1 ? " (shared)" : ""} &middot; SIDE {side}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
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
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {opts.map((p) => {
                        const on = chosen(side) === p.id;
                        return (
                          <button
                            key={p.id}
                            aria-pressed={on}
                            onClick={() => setFirstServer((prev) => ({ ...prev, [side]: p.id }))}
                            style={{
                              flex: 1,
                              minWidth: 100,
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
        </div>

        {format && (
          <div style={{ ...card, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={sectionLabel}>Format</div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: 14,
              }}
            >
              <PickerRow
                label="Serves per turn"
                options={SERVES_PER_TURN_OPTIONS}
                value={format.serves_per_turn}
                onChange={(v) => setFormat((f) => f && { ...f, serves_per_turn: v })}
              />
              <PickerRow
                label="Games per match (best of)"
                options={BEST_OF_OPTIONS}
                value={format.best_of}
                onChange={(v) => setFormat((f) => f && { ...f, best_of: v })}
              />
              <PickerRow
                label="Cameras"
                options={[1, 2]}
                value={cameraCount}
                onChange={(v) => setCameraCount(v as 1 | 2)}
              />
            </div>

            <div style={{ fontSize: 11, color: C.faint, paddingTop: 2 }}>
              {format.points_to_win} pts, win by {format.win_margin}; deuce from{" "}
              {format.deuce_trigger}–{format.deuce_trigger}
            </div>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={sectionLabel}>Cameras</div>
            <CamerasToggle status={cameraStatus} onClick={() => setShowCameras((v) => !v)} />
          </div>
          {showCameras && <CameraPreviewPanel status={cameraStatus} inline />}
        </div>

        {error && <div style={{ fontSize: 12, color: C.coral, fontWeight: 700 }}>{error}</div>}
      </div>

      <div
        style={{
          position: "sticky",
          bottom: 0,
          padding: "16px 20px 24px",
          background: "linear-gradient(180deg,rgba(15,18,22,0) 0%,#0F1216 40%)",
        }}
      >
        <div style={{ maxWidth: 760, margin: "0 auto" }}>
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
      </div>

      {picking && (
        <PlayerPicker
          side={picking.side}
          excluded={usedIds}
          canClear={!!manual[picking.side][picking.index]}
          onPick={(p) => {
            setManualSlot(picking.side, picking.index, p);
            // Ask that camera to learn this face, so next time it's detected.
            if (p) api.requestEnroll(cameraFor(picking.side), p.id).catch(() => {});
            setPicking(null);
          }}
          onRegisterNew={() => {
            setRegistering({ side: picking.side, index: picking.index });
            setPicking(null);
          }}
          onClose={() => setPicking(null)}
        />
      )}

      {registering && (
        <RegisterFaceWizard
          camera={cameraFor(registering.side)}
          onDone={(p) => {
            setManualSlot(registering.side, registering.index, p);
            setRegistering(null);
          }}
          onClose={() => setRegistering(null)}
        />
      )}
    </div>
  );
}

function PickerRow({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: number[];
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ fontSize: 12, color: C.subtle }}>{label}</div>
      <div style={{ display: "flex", gap: 8 }}>
        {options.map((n) => {
          const on = value === n;
          return (
            <button
              key={n}
              aria-pressed={on}
              onClick={() => onChange(n)}
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
              {n}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Not in the design: a proactive nudge when a camera sees a face it can't
 * confidently match, instead of waiting for a manual "Waiting for face…" tap. */
function NewFaceBanner({
  side,
  onRegister,
  onDismiss,
}: {
  side: SideKey;
  onRegister: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "12px 14px",
        borderRadius: 12,
        border: `1px solid ${C.lime}`,
        background: "rgba(200,255,77,0.08)",
        flexWrap: "wrap",
      }}
    >
      <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke={C.lime} strokeWidth={2} style={{ flexShrink: 0 }}>
        <circle cx={12} cy={8} r={4} />
        <path d="M4 20c0-4 3.6-6 8-6s8 2 8 6" />
      </svg>
      <div style={{ flexGrow: 1, fontSize: 12, color: C.text, fontWeight: 600, minWidth: 160 }}>
        New face on Side {side}&apos;s camera &mdash; register them?
      </div>
      <button
        onClick={onRegister}
        style={{
          padding: "7px 14px",
          borderRadius: 999,
          border: "none",
          background: C.lime,
          color: C.bg,
          fontSize: 12,
          fontWeight: 800,
        }}
      >
        Register
      </button>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        style={{ background: "none", border: "none", color: C.faint, fontSize: 16, padding: 4 }}
      >
        &times;
      </button>
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
          flex: "1 1 120px",
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
        flex: "1 1 120px",
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
      {slot.source === "detected" && (
        <div style={{ fontSize: 10, color: C.faint, textDecoration: "underline" }}>Not them?</div>
      )}
    </button>
  );
}

/** Manual fallback for face-rec (not in the design — see README). */
function PlayerPicker({
  side,
  excluded,
  canClear,
  onPick,
  onRegisterNew,
  onClose,
}: {
  side: SideKey;
  excluded: Set<number>;
  canClear: boolean;
  onPick: (p: PlayerRef | null) => void;
  onRegisterNew: () => void;
  onClose: () => void;
}) {
  const [players, setPlayers] = useState<PlayerRef[]>([]);
  useEffect(() => {
    api.players().then(setPlayers);
  }, []);
  const color = SIDE[side].color;
  const available = players.filter((p) => !excluded.has(p.id));

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(11,13,16,0.8)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
        zIndex: 30,
      }}
    >
      <div
        role="dialog"
        aria-label={`Pick a player for side ${side}`}
        style={{
          width: "100%",
          maxWidth: 420,
          maxHeight: "85vh",
          boxSizing: "border-box",
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 18,
          padding: "20px 24px 24px",
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
        <button
          onClick={onRegisterNew}
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            padding: "12px 0",
            border: `1.5px dashed ${C.lime}`,
            borderRadius: 12,
            background: "rgba(200,255,77,0.06)",
            color: C.lime,
            fontWeight: 800,
            fontSize: 14,
          }}
        >
          <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={C.lime} strokeWidth={2}>
            <circle cx={12} cy={8} r={4} />
            <path d="M4 20c0-4 3.6-6 8-6s8 2 8 6" />
          </svg>
          Register new face
        </button>
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
