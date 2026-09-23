import { useState } from "react";
import { AvatarPair, ChevronIcon } from "../components/Avatar";
import { Stage } from "../components/Stage";
import { C, SIDE, type SideKey } from "../theme";
import type { MatchState, WinType } from "../types";
import { activePlayer, partnerOf, teamName } from "../match";

const TAGS: { key: WinType; label: string }[] = [
  { key: "smash", label: "Smash" },
  { key: "fault", label: "Fault" },
  { key: "net", label: "Net" },
  { key: "out", label: "Out" },
];

export function PointConfirm({
  match: m,
  preselected,
  onConfirm,
  onCancel,
}: {
  match: MatchState;
  preselected: SideKey;
  onConfirm: (side: SideKey, winType: WinType | null) => void;
  onCancel: () => void;
}) {
  const [tag, setTag] = useState<WinType | null>(null);
  const hit = m.latest_hit;

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
        }}
      >
        <div
          style={{
            padding: "32px 24px 8px",
            display: "flex",
            flexDirection: "column",
            gap: 6,
            alignItems: "center",
            textAlign: "center",
          }}
        >
          <div
            style={{
              fontSize: 12,
              letterSpacing: 1.5,
              color: C.subtle,
              fontWeight: 700,
              textTransform: "uppercase",
            }}
          >
            Rally ended
          </div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>Who won the point?</h1>
        </div>

        <div
          style={{
            margin: "18px 24px 0",
            background: C.surface,
            border: `1px solid ${C.border}`,
            borderRadius: 12,
            padding: "12px 16px",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={C.teal} strokeWidth={2}>
            <rect x={2} y={6} width={14} height={12} rx={2} />
            <path d="M16 10l6-3v10l-6-3" />
          </svg>
          <div style={{ fontSize: 13, color: C.textSoft }}>
            Detected last hit &mdash;{" "}
            {hit?.player ? (
              <>
                <span style={{ color: SIDE[hit.camera].color, fontWeight: 700 }}>
                  {hit.player.name}
                </span>{" "}
                (Cam {hit.camera})
              </>
            ) : (
              <span style={{ color: C.muted, fontWeight: 700 }}>unknown</span>
            )}
          </div>
        </div>

        <div
          style={{
            flexGrow: 1,
            display: "flex",
            flexDirection: "column",
            gap: 14,
            padding: "20px 24px",
          }}
        >
          {(["A", "B"] as const).map((side) => (
            <WinnerCard
              key={side}
              m={m}
              side={side}
              selected={side === preselected}
              onClick={() => onConfirm(side, tag)}
            />
          ))}

          <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 10 }}>
            <div
              style={{
                fontSize: 12,
                fontWeight: 700,
                letterSpacing: 0.4,
                color: C.muted,
                textTransform: "uppercase",
              }}
            >
              How it ended &middot; optional
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {TAGS.map(({ key, label }) => {
                const on = tag === key;
                return (
                  <button
                    key={key}
                    aria-pressed={on}
                    onClick={() => setTag(on ? null : key)}
                    style={{
                      padding: "9px 16px",
                      borderRadius: 999,
                      border: `1.5px solid ${on ? C.lime : C.border}`,
                      background: on ? "rgba(200,255,77,0.12)" : "transparent",
                      color: on ? C.lime : C.muted,
                      fontSize: 13,
                      fontWeight: on ? 700 : 600,
                    }}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <div style={{ padding: "8px 24px 30px", textAlign: "center" }}>
          <button
            onClick={onCancel}
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
            Cancel &amp; back to scoreboard
          </button>
        </div>
      </div>
    </Stage>
  );
}

function WinnerCard({
  m,
  side,
  selected,
  onClick,
}: {
  m: MatchState;
  side: SideKey;
  selected: boolean;
  onClick: () => void;
}) {
  const s = SIDE[side];
  const lead = activePlayer(m, side);
  const partner = partnerOf(m, side, lead);
  const names = teamName([lead, partner]);
  return (
    <button
      onClick={onClick}
      style={{
        border: selected ? `2px solid ${s.color}` : `1.5px solid ${C.border}`,
        background: selected ? `rgba(${s.tint},0.08)` : C.surface,
        borderRadius: 18,
        padding: 20,
        display: "flex",
        alignItems: "center",
        gap: 14,
        textAlign: "left",
      }}
    >
      <AvatarPair
        lead={lead}
        partner={partner}
        size={46}
        fontSize={16}
        overlap={14}
        color={s.color}
        leadText={C.bg}
        partnerFill={s.cardPartner}
        ring={C.bg}
      />
      <div style={{ flexGrow: 1 }}>
        <div style={{ fontSize: 16, fontWeight: 800 }}>{names}</div>
        <div style={{ fontSize: 12, color: C.subtle }}>Side {side} &middot; point to them</div>
      </div>
      <ChevronIcon stroke={selected ? s.color : C.faint} />
    </button>
  );
}
