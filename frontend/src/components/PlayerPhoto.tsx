import { useState } from "react";
import { api } from "../api";
import { Avatar } from "./Avatar";
import { C } from "../theme";
import type { PlayerRef } from "../types";

/** The JPEG crop taken when enrollment last succeeded — a snapshot, not a
 * live view. Falls back to the initials avatar if there's no photo yet. */
export function PlayerPhoto({
  player,
  size,
  refreshKey,
}: {
  player: PlayerRef;
  size: number;
  refreshKey?: number;
}) {
  const [ok, setOk] = useState(true);
  return ok ? (
    <img
      key={refreshKey}
      src={`${api.photoUrl(player.id)}${refreshKey ? `?t=${refreshKey}` : ""}`}
      alt={`${player.name}'s last recognised face`}
      onError={() => setOk(false)}
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        objectFit: "cover",
        flexShrink: 0,
        background: C.surfaceRaised,
      }}
    />
  ) : (
    <Avatar player={player} size={size} fontSize={size * 0.34} background={C.teal} color={C.bg} />
  );
}
