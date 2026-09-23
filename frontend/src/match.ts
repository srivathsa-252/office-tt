import type { SideKey } from "./theme";
import type { MatchState, PlayerRef } from "./types";

/** The player shown for a side: the server on the serving side, else the receiver. */
export function activePlayer(m: MatchState, side: SideKey): PlayerRef {
  const id = m.server.side === side ? m.server.id : m.receiver.id;
  return m.sides[side].players.find((p) => p.id === id)!;
}

export function partnerOf(m: MatchState, side: SideKey, lead: PlayerRef): PlayerRef | undefined {
  return m.sides[side].players.find((p) => p.id !== lead.id);
}

export const teamName = (players: (PlayerRef | undefined)[]) =>
  players.filter((p): p is PlayerRef => !!p).map((p) => p.name).join(" & ");
