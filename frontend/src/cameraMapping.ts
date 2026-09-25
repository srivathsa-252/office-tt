import type { SideKey } from "./theme";

// Which physical camera feeds which match side — a table-wide fact about how
// the two cameras are mounted, not a per-match choice, so it's one persisted
// setting (same "per browser, not sent to the API" pattern as camera count)
// applied everywhere a camera letter and a match side meet: setup's
// detections/enrollment, and the live scoreboard's swing attribution
// (last_hit is keyed by the literal camera that reported it).
const CAMERA_SWAP_KEY = "tt-camera-swap";

export function loadCameraSwap(): boolean {
  return localStorage.getItem(CAMERA_SWAP_KEY) === "1";
}

export function saveCameraSwap(swapped: boolean): void {
  localStorage.setItem(CAMERA_SWAP_KEY, swapped ? "1" : "0");
}

/** The other camera/side letter when swapped is on, else unchanged. Camera
 * and side share the same "A" | "B" key space, so one flip serves both
 * directions: cameraForSide and sideForCamera. */
export function flip(key: SideKey, swapped: boolean): SideKey {
  if (!swapped) return key;
  return key === "A" ? "B" : "A";
}
