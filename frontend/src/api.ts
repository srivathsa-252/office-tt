import type { CameraStatus, Config, Format, MatchState, PlayerRef, PlayerStats, WinType } from "./types";
import type { SideKey } from "./theme";

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j.detail, () => res.statusText);
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  config: () => req<Config>("GET", "/api/config"),
  players: () => req<PlayerRef[]>("GET", "/api/players"),
  addPlayer: (name: string) => req<PlayerRef>("POST", "/api/players", { name }),
  playerStats: (id: number) => req<PlayerStats>("GET", `/api/players/${id}/stats`),
  requestEnroll: (camera: SideKey, player_id: number) =>
    req<{ id: number }>("POST", "/api/capture/enroll-requests", { camera, player_id }),
  detections: () => req<Record<SideKey, PlayerRef[]>>("GET", "/api/capture/detections"),
  cameraStatus: () => req<Record<SideKey, CameraStatus>>("GET", "/api/capture/camera-status"),
  previewUrl: (camera: SideKey) => `/api/capture/preview/${camera}`,
  liveMatch: () => req<MatchState>("GET", "/api/matches/live"),
  match: (id: number) => req<MatchState>("GET", `/api/matches/${id}`),
  createMatch: (body: {
    mode: "singles" | "doubles";
    side_a: number[];
    side_b: number[];
    first_server: number;
    first_receiver: number;
    format?: Partial<Format>;
  }) => req<MatchState>("POST", "/api/matches", body),
  scorePoint: (id: number, winner: SideKey, win_type: WinType | null) =>
    req<MatchState>("POST", `/api/matches/${id}/points`, { winner, win_type }),
  undo: (id: number) => req<MatchState>("DELETE", `/api/matches/${id}/points/last`),
};

/** Live match state over WebSocket, reconnecting with backoff. */
export function subscribeMatch(id: number, onState: (s: MatchState) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let delay = 500;
  let timer: number | undefined;
  const connect = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/matches/${id}`);
    ws.onopen = () => (delay = 500);
    ws.onmessage = (e) => onState(JSON.parse(e.data));
    ws.onclose = () => {
      if (closed) return;
      timer = window.setTimeout(connect, delay);
      delay = Math.min(delay * 2, 8000);
    };
  };
  connect();
  return () => {
    closed = true;
    window.clearTimeout(timer);
    ws?.close();
  };
}
