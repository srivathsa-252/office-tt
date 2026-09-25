import { C } from "../theme";

/** A centered confirm/cancel overlay for a destructive action. */
export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  danger = true,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 50,
        background: "rgba(11,13,16,0.85)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        role="alertdialog"
        aria-label={title}
        style={{
          width: 380,
          maxWidth: "100%",
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 18,
          padding: 24,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          textAlign: "center",
        }}
      >
        <div style={{ fontSize: 18, fontWeight: 800 }}>{title}</div>
        <div style={{ fontSize: 13, color: C.subtle, lineHeight: 1.5 }}>{body}</div>
        <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
          <button
            onClick={onCancel}
            disabled={busy}
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
            onClick={onConfirm}
            disabled={busy}
            style={{
              flex: 1,
              padding: "12px 0",
              border: "none",
              borderRadius: 12,
              background: danger ? C.coral : C.lime,
              color: danger ? C.bg : C.bg,
              fontSize: 14,
              fontWeight: 800,
              opacity: busy ? 0.6 : 1,
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
