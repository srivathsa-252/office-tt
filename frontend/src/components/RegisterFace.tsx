import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { Avatar } from "./Avatar";
import { C, SIDE, type SideKey } from "../theme";
import type { PlayerRef } from "../types";

type Phase = "scanning" | "scanned" | "already_known" | "naming" | "registering" | "done" | "failed";

const POLL_MS = 500;
const CLIENT_TIMEOUT_S = 20;

/** Press Register → live preview with a scan animation → "Scanned
 * successfully" → ask for a name → "Registered successfully". Captures the
 * face BEFORE a name exists (backend: POST .../enroll-requests with no
 * player_id, then .../register once scanning succeeds) — not in the design. */
export function RegisterFaceWizard({
  camera,
  onDone,
  onClose,
}: {
  camera: SideKey;
  onDone: (player: PlayerRef) => void;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("scanning");
  const [requestId, setRequestId] = useState<number | null>(null);
  const [failReason, setFailReason] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [registeredPlayer, setRegisteredPlayer] = useState<PlayerRef | null>(null);
  const [matchedPlayer, setMatchedPlayer] = useState<PlayerRef | null>(null);
  const [wasAlreadyKnown, setWasAlreadyKnown] = useState(false);
  const nameInput = useRef<HTMLInputElement>(null);
  const startedAt = useRef(Date.now());
  const color = SIDE[camera].color;

  const beginScan = () => {
    setFailReason(null);
    setRequestId(null);
    setMatchedPlayer(null);
    setWasAlreadyKnown(false);
    setPhase("scanning");
    startedAt.current = Date.now();
    api.startScan(camera).then(
      (r) => setRequestId(r.id),
      (e) => {
        setFailReason((e as Error).message);
        setPhase("failed");
      },
    );
  };

  // Start the scan on mount.
  useEffect(() => {
    beginScan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [camera]);

  // Poll status while scanning.
  useEffect(() => {
    if (phase !== "scanning" || requestId === null) return;
    let alive = true;
    const poll = () => {
      api.enrollStatus(requestId).then(
        (s) => {
          if (!alive) return;
          if (s.status === "scanned") {
            setPhase("scanned");
          } else if (s.status === "already_known") {
            setMatchedPlayer(s.matched_player);
            setPhase("already_known");
          } else if (s.status === "failed") {
            setFailReason(s.reason);
            setPhase("failed");
          } else if ((Date.now() - startedAt.current) / 1000 > CLIENT_TIMEOUT_S) {
            setFailReason("no response — is a camera worker running?");
            setPhase("failed");
          }
        },
        () => {},
      );
    };
    poll();
    const t = window.setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [phase, requestId]);

  // Brief "Scanned successfully" beat before asking for a name.
  useEffect(() => {
    if (phase !== "scanned") return;
    const t = window.setTimeout(() => setPhase("naming"), 1100);
    return () => window.clearTimeout(t);
  }, [phase]);

  useEffect(() => {
    if (phase === "naming") nameInput.current?.focus();
  }, [phase]);

  const submitName = async () => {
    if (!name.trim() || requestId === null) return;
    setPhase("registering");
    try {
      const player = await api.registerScan(requestId, name.trim());
      setRegisteredPlayer(player);
      setPhase("done");
      window.setTimeout(() => onDone(player), 1100);
    } catch (e) {
      setFailReason((e as Error).message);
      setPhase("failed");
    }
  };

  const confirmMatch = () => {
    if (!matchedPlayer) return;
    setRegisteredPlayer(matchedPlayer);
    setWasAlreadyKnown(true);
    setPhase("done");
    window.setTimeout(() => onDone(matchedPlayer), 900);
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 40,
        background: "rgba(11,13,16,0.85)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
      }}
    >
      <div
        role="dialog"
        aria-label="Register a new face"
        style={{
          width: "100%",
          maxWidth: 380,
          boxSizing: "border-box",
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 20,
          padding: "18px 20px 22px",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div
            className="mono"
            style={{ fontSize: 11, letterSpacing: 1.2, color: C.muted, fontWeight: 700, textTransform: "uppercase" }}
          >
            Register &middot; Camera {camera}
          </div>
          <button
            onClick={onClose}
            aria-label="Cancel"
            style={{
              width: 28,
              height: 28,
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

        {(phase === "scanning" || phase === "scanned") && (
          <ScanPreview camera={camera} scanned={phase === "scanned"} color={color} />
        )}

        {phase === "scanning" && (
          <div style={{ textAlign: "center", fontSize: 13, color: C.subtle }}>
            Scanning for a face&hellip;
          </div>
        )}

        {phase === "scanned" && (
          <SuccessLine color={C.lime}>Scanned successfully!</SuccessLine>
        )}

        {phase === "already_known" && matchedPlayer && (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
            <Avatar player={matchedPlayer} size={56} fontSize={20} background={color} color={C.bg} />
            <div style={{ textAlign: "center", fontSize: 15, fontWeight: 700 }}>
              Are you already <span style={{ color }}>{matchedPlayer.name}</span>?
            </div>
            <div style={{ textAlign: "center", fontSize: 12, color: C.subtle }}>
              This face already matches a player in the gallery.
            </div>
            <div style={{ display: "flex", gap: 10, width: "100%" }}>
              <button
                onClick={beginScan}
                style={{
                  flex: 1,
                  padding: "12px 0",
                  border: `1.5px solid ${C.border}`,
                  borderRadius: 12,
                  background: "transparent",
                  color: C.text,
                  fontSize: 14,
                  fontWeight: 700,
                }}
              >
                No, that&apos;s not me
              </button>
              <button
                onClick={confirmMatch}
                style={{
                  flex: 1,
                  padding: "12px 0",
                  border: "none",
                  borderRadius: 12,
                  background: C.lime,
                  color: C.bg,
                  fontSize: 14,
                  fontWeight: 800,
                }}
              >
                Yes, that&apos;s me
              </button>
            </div>
          </div>
        )}

        {phase === "naming" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submitName();
            }}
            style={{ display: "flex", flexDirection: "column", gap: 12 }}
          >
            <SuccessLine color={C.lime}>Scanned successfully!</SuccessLine>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <label htmlFor="scan-name" style={{ fontSize: 12, color: C.subtle }}>
                What&apos;s their name?
              </label>
              <input
                id="scan-name"
                ref={nameInput}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Player name"
                style={{
                  padding: "12px 14px",
                  background: C.bg,
                  border: `1px solid ${C.border}`,
                  borderRadius: 10,
                  color: C.text,
                  fontFamily: "inherit",
                  fontSize: 15,
                }}
              />
            </div>
            <button
              type="submit"
              disabled={!name.trim()}
              style={{
                padding: "13px 0",
                border: "none",
                borderRadius: 12,
                background: C.lime,
                color: C.bg,
                fontSize: 15,
                fontWeight: 800,
                opacity: name.trim() ? 1 : 0.4,
              }}
            >
              Register
            </button>
          </form>
        )}

        {phase === "registering" && (
          <div style={{ textAlign: "center", fontSize: 13, color: C.subtle }}>
            Registering&hellip;
          </div>
        )}

        {phase === "done" && registeredPlayer && (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 12,
              padding: "8px 0",
              animation: "tt-pop-in 0.3s ease",
            }}
          >
            <Avatar player={registeredPlayer} size={64} fontSize={22} background={C.lime} color={C.bg} />
            <SuccessLine color={C.lime}>
              {wasAlreadyKnown
                ? `Welcome back, ${registeredPlayer.name}!`
                : `${registeredPlayer.name} registered successfully!`}
            </SuccessLine>
          </div>
        )}

        {phase === "failed" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 14, alignItems: "center" }}>
            <div style={{ textAlign: "center", fontSize: 13, color: C.coral, fontWeight: 600 }}>
              Couldn&apos;t complete the scan{failReason ? ` — ${failReason}` : ""}.
            </div>
            <div style={{ display: "flex", gap: 10, width: "100%" }}>
              <button
                onClick={onClose}
                style={{
                  flex: 1,
                  padding: "12px 0",
                  border: `1.5px solid ${C.border}`,
                  borderRadius: 12,
                  background: "transparent",
                  color: C.text,
                  fontSize: 14,
                  fontWeight: 700,
                }}
              >
                Cancel
              </button>
              <button
                onClick={beginScan}
                style={{
                  flex: 1,
                  padding: "12px 0",
                  border: "none",
                  borderRadius: 12,
                  background: C.lime,
                  color: C.bg,
                  fontSize: 14,
                  fontWeight: 800,
                }}
              >
                Try again
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ScanPreview({
  camera,
  scanned,
  color,
}: {
  camera: SideKey;
  scanned: boolean;
  color: string;
}) {
  const [ok, setOk] = useState(true);
  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        aspectRatio: "4 / 3",
        borderRadius: 14,
        overflow: "hidden",
        border: `2px solid ${scanned ? C.lime : color}`,
        background: "#000",
        transition: "border-color 0.3s ease",
      }}
    >
      <img
        src={api.streamUrl(camera)}
        alt={`Live preview from camera ${camera}`}
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
          No camera feed
        </div>
      )}
      {!scanned && (
        <>
          <div
            style={{
              position: "absolute",
              inset: 10,
              border: `1.5px dashed ${color}`,
              borderRadius: 10,
              opacity: 0.6,
              animation: "tt-scan-pulse 1.8s ease-in-out infinite",
            }}
          />
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              height: 3,
              background: `linear-gradient(90deg, transparent, ${color}, transparent)`,
              boxShadow: `0 0 14px 2px ${color}`,
              animation: "tt-scan-sweep 1.8s ease-in-out infinite",
            }}
          />
        </>
      )}
      {scanned && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "rgba(200,255,77,0.18)",
            animation: "tt-pop-in 0.25s ease",
          }}
        >
          <CheckBadge color={C.lime} size={52} />
        </div>
      )}
    </div>
  );
}

function SuccessLine({ color, children }: { color: string; children: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        fontSize: 14,
        fontWeight: 800,
        color,
      }}
    >
      <CheckBadge color={color} />
      {children}
    </div>
  );
}

function CheckBadge({ color, size = 18 }: { color: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={3}
      style={{ flexShrink: 0 }}
    >
      <circle cx={12} cy={12} r={11} fill={`${color}26`} stroke="none" />
      <path d="M7 12.5l3.2 3.2L17 9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
