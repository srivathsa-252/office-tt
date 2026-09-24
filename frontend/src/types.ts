import type { SideKey } from "./theme";

export interface PlayerRef {
  id: number;
  name: string;
  initials: string;
}

export interface HitRef {
  camera: SideKey;
  player: PlayerRef | null;
}

export interface Format {
  points_to_win: number;
  win_margin: number;
  serves_per_turn: number;
  deuce_serve_rotation: number;
  deuce_trigger: number;
  best_of: number;
}

export interface MatchState {
  id: number;
  mode: "singles" | "doubles";
  status: "live" | "finished" | "abandoned";
  format: Format;
  sides: Record<SideKey, { players: PlayerRef[] }>;
  game_number: number;
  games: Record<SideKey, number>;
  score: Record<SideKey, number>;
  server: { id: number; side: SideKey };
  receiver: { id: number; side: SideKey };
  serves_in_turn: number;
  serves_remaining: number;
  deuce: boolean;
  winner: SideKey | null;
  points_played: number;
  last_hit: Record<SideKey, HitRef | null>;
  latest_hit: HitRef | null;
}

export type WinType = "smash" | "fault" | "net" | "out";

export interface Config {
  capture_win_type: boolean;
  default_format: Format;
}

export interface CameraStatus {
  active: boolean;
  last_seen: number | null;
}

export interface CameraDetections {
  players: PlayerRef[];
  // A face was seen this camera couldn't confidently match to anyone —
  // "new face" for the setup screen's register prompt.
  unknown_present: boolean;
}

export type Detections = Record<SideKey, CameraDetections>;

export interface EnrollStatus {
  id: number;
  camera: SideKey;
  player_id: number | null;
  status: "pending" | "scanned" | "done" | "failed";
  reason: string | null;
}

export interface Synergy {
  pair_id: number;
  partner: PlayerRef;
  matches: number;
  rating: number;
  vs_solo_avg: number;
  actual_win_rate: number | null;
  predicted_win_rate: number | null;
  synergy: number | null;
}

export interface HistoryRow {
  match_id: number;
  mode: "singles" | "doubles";
  opponents: PlayerRef[];
  won: boolean;
  closed_at: string;
  games: { own: number; opp: number }[];
  games_won: { own: number; opp: number };
  // Individual rating only moves on singles matches (doubles rates the pair),
  // so this is null for a doubles-only history row.
  rating_after: number | null;
}

export interface StyleTagDetail {
  tag: string;
  applies: boolean;
  reason: string;
}

export interface PlayerStats {
  player: PlayerRef;
  rating: number;
  rating_delta: number | null;
  matches_played: number;
  win_rate: number | null;
  avg_rally_length: number | null;
  forehand_winner_rate: number | null;
  serve_win_rate: number | null;
  recent_form: ("W" | "L")[];
  style_tags: string[];
  style_tags_detail: StyleTagDetail[];
  synergy: Synergy[];
  history: HistoryRow[];
}
