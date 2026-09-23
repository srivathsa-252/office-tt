import { useEffect, useState } from "react";
import { api } from "../api";
import { C, SIDE, type SideKey } from "../theme";
import type { CameraStatus } from "../types";

export const CAMERA_SIDES: SideKey[] = ["A", "B"];
const CAMERA_STATUS_POLL_MS = 3000;

/** Auto-detects how many camera workers are actually running (1 or 2), not
 * just assumed — polls GET /api/capture/camera-status. Shared by any screen
 * that shows the live-preview panel (LiveScoreboard, MatchSetup). */
export function useCameraStatus(): Record<SideKey, CameraStatus> | null {
  const [status, setStatus] = useState<Record<SideKey, CameraStatus> | null>(null);
  useEffect(() => {
    let alive = true;
    const poll = () => api.cameraStatus().then((s) => alive && setStatus(s), () => {});
    poll();
    const t = window.setInterval(poll, CAMERA_STATUS_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);
  return status;
}

/** Floating pill: opens the live-preview panel. Warns inline (no separate
 * alert) when fewer than 2 camera workers are actually posting. */
export function CamerasToggle({
  status,
  onClick,
  style,
}: {
  status: Record<SideKey, CameraStatus> | null;
  onClick: () => void;
  style?: React.CSSProperties;
}) {
  const activeCount = status ? CAMERA_SIDES.filter((s) => status[s]?.active).length : null;
  const warn = activeCount !== null && activeCount < 2;
  return (
    <button
      onClick={onClick}
      aria-label="Show camera previews"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 7,
        padding: "8px 14px",
        borderRadius: 999,
        border: `1px solid ${warn ? C.coral : C.border}`,
        background: "rgba(11,13,16,0.85)",
        color: warn ? C.coral : C.muted,
        ...style,
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
 * actually seeing — auto-detects 1 vs 2 running cameras from camera-status
 * and warns inline about whichever side is missing, instead of assuming both
 * are connected. A switch control picks which feed is big when there's only
 * room for one (e.g. on a phone). Renders as a full-screen overlay; pass
 * `inline` to render as a normal block instead (e.g. embedded in a page). */
export function CameraPreviewPanel({
  status,
  onClose,
  inline,
}: {
  status: Record<SideKey, CameraStatus> | null;
  onClose?: () => void;
  inline?: boolean;
}) {
  const [big, setBig] = useState<SideKey | null>(null);

  const active = CAMERA_SIDES.filter((s) => status?.[s]?.active);
  const missing = CAMERA_SIDES.filter((s) => !status?.[s]?.active);
  // With only one camera live there's nothing to switch between — show it big.
  const shown = active.length === 1 ? active[0] : big;
  const feeds = shown ? [shown] : active;

  return (
    <div
      style={
        inline
          ? {
              display: "flex",
              flexDirection: "column",
              background: C.surface,
              border: `1px solid ${C.border}`,
              borderRadius: 16,
              padding: 16,
              gap: 12,
            }
          : {
              position: "absolute",
              inset: 0,
              zIndex: 10,
              background: "rgba(11,13,16,0.97)",
              display: "flex",
              flexDirection: "column",
              padding: "24px 40px 30px",
            }
      }
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
          {onClose && (
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
          )}
        </div>
      </div>

      {missing.length > 0 && (
        <div
          style={{
            marginTop: inline ? 0 : 14,
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

      <div
        style={{
          flexGrow: 1,
          display: "flex",
          gap: 16,
          marginTop: inline ? 0 : 16,
          minHeight: inline ? 200 : 0,
        }}
      >
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
        <div style={{ display: "flex", justifyContent: "center", gap: 8, marginTop: inline ? 0 : 16 }}>
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

export function CameraFeed({ side }: { side: SideKey }) {
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
      <div style={{ flexGrow: 1, position: "relative", background: "#000", minHeight: 160 }}>
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
