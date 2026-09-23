import type { CSSProperties } from "react";
import type { PlayerRef } from "../types";

export function Avatar({
  player,
  size,
  fontSize,
  background,
  color,
  style,
}: {
  player: PlayerRef;
  size: number;
  fontSize: number;
  background: string;
  color: string;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background,
        color,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontWeight: 800,
        fontSize,
        flexShrink: 0,
        ...style,
      }}
    >
      {player.initials}
    </div>
  );
}

/** Lead player solid, partner dimmed and overlapped — as in the design. */
export function AvatarPair({
  lead,
  partner,
  size,
  fontSize,
  overlap,
  color,
  leadText,
  partnerFill,
  ring,
}: {
  lead: PlayerRef;
  partner?: PlayerRef;
  size: number;
  fontSize: number;
  overlap: number;
  color: string;
  leadText: string;
  partnerFill: string;
  ring: string;
}) {
  return (
    <div style={{ display: "flex" }}>
      <Avatar player={lead} size={size} fontSize={fontSize} background={color} color={leadText} />
      {partner && (
        <Avatar
          player={partner}
          size={size}
          fontSize={fontSize}
          background={partnerFill}
          color={color}
          style={{ marginLeft: -overlap, border: `2px solid ${ring}` }}
        />
      )}
    </div>
  );
}

export const CheckIcon = ({ size = 10 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3}>
    <path d="M4 12l5 5L20 6" />
  </svg>
);

export const ChevronIcon = ({ stroke }: { stroke: string }) => (
  <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={stroke} strokeWidth={2.5}>
    <path d="M9 18l6-6-6-6" />
  </svg>
);
