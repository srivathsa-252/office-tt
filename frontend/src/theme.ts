// Tokens lifted from the design artifact (dark theme).
export const C = {
  bgScoreboard: "#0B0D10",
  bg: "#0F1216",
  surface: "#1B1F27",
  surfaceRaised: "#232837",
  border: "#2A3040",
  borderDashed: "#3A4152",
  rowBorder: "#1F232B",
  text: "#F4F6F8",
  textSoft: "#C6CAD2",
  muted: "#8B93A1",
  subtle: "#7A8391",
  faint: "#5A6270",
  lime: "#C8FF4D",
  live: "#FF4D4D",
  teal: "#2DD4BF",
  coral: "#FF6B4A",
};

export type SideKey = "A" | "B";

// Per-side palette. Partner avatars use a dimmed fill that differs by screen.
export const SIDE = {
  A: {
    color: C.teal,
    tint: "45,212,191",
    barBg: "#12211F",
    barPartner: "#173B37",
    barHint: "#5F958C",
    cardPartner: "#20343A",
  },
  B: {
    color: C.coral,
    tint: "255,107,74",
    barBg: "#231712",
    barPartner: "#3E2318",
    barHint: "#9A6C5A",
    cardPartner: "#3A2A22",
  },
} as const;
