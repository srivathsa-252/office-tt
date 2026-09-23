import { useRef, useState } from "react";
import { C } from "../theme";

export interface RatingPoint {
  label: string; // e.g. "Today" or a date string
  opponent: string;
  rating: number;
  won: boolean;
}

const W = 640;
const H = 220;
const PAD_L = 40;
const PAD_R = 16;
const PAD_T = 16;
const PAD_B = 28;

/** Rating-over-time line: one series, thin 2px line, hover crosshair + tooltip.
 * Chronological left-to-right (oldest first). */
export function RatingChart({ points, color }: { points: RatingPoint[]; color: string }) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  if (points.length < 2) {
    return (
      <div
        style={{
          padding: "28px 16px",
          textAlign: "center",
          color: C.faint,
          fontSize: 13,
          border: `1px dashed ${C.borderDashed}`,
          borderRadius: 12,
        }}
      >
        Not enough rated singles matches yet for a trend.
      </div>
    );
  }

  const ratings = points.map((p) => p.rating);
  const min = Math.min(...ratings);
  const max = Math.max(...ratings);
  const span = Math.max(max - min, 1);
  const yPad = span * 0.15;
  const yMin = min - yPad;
  const yMax = max + yPad;

  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const x = (i: number) => PAD_L + (points.length === 1 ? 0 : (i / (points.length - 1)) * innerW);
  const y = (v: number) => PAD_T + innerH - ((v - yMin) / (yMax - yMin)) * innerH;

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.rating).toFixed(1)}`).join(" ");

  const gridLines = 4;
  const gridValues = Array.from({ length: gridLines + 1 }, (_, i) => yMin + (span + 2 * yPad) * (i / gridLines));

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const svg = ref.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const fracX = (e.clientX - rect.left) / rect.width;
    const logicalX = fracX * W;
    let nearest = 0;
    let best = Infinity;
    points.forEach((_, i) => {
      const d = Math.abs(x(i) - logicalX);
      if (d < best) {
        best = d;
        nearest = i;
      }
    });
    setHover(nearest);
  };

  const hp = hover !== null ? points[hover] : null;
  const hx = hover !== null ? x(hover) : 0;
  const hy = hp ? y(hp.rating) : 0;
  const tooltipLeft = hx > W - 150 ? hx - 130 : hx + 10;

  return (
    <svg
      ref={ref}
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      style={{ display: "block", touchAction: "none" }}
      onPointerMove={onMove}
      onPointerLeave={() => setHover(null)}
      role="img"
      aria-label="Rating over time"
    >
      {gridValues.map((v, i) => (
        <g key={i}>
          <line
            x1={PAD_L}
            x2={W - PAD_R}
            y1={y(v)}
            y2={y(v)}
            stroke={C.rowBorder}
            strokeWidth={1}
          />
          <text x={PAD_L - 8} y={y(v) + 4} textAnchor="end" fontSize={10} fill={C.faint}>
            {Math.round(v)}
          </text>
        </g>
      ))}

      <text x={x(0)} y={H - 8} textAnchor="start" fontSize={10} fill={C.faint}>
        {points[0].label}
      </text>
      <text x={x(points.length - 1)} y={H - 8} textAnchor="end" fontSize={10} fill={C.faint}>
        {points[points.length - 1].label}
      </text>

      <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

      {points.map((p, i) => (
        <circle
          key={i}
          cx={x(i)}
          cy={y(p.rating)}
          r={hover === i ? 5 : 3}
          fill={color}
          stroke={C.bg}
          strokeWidth={1.5}
        />
      ))}

      {hp && (
        <>
          <line x1={hx} x2={hx} y1={PAD_T} y2={H - PAD_B} stroke={color} strokeWidth={1} strokeDasharray="3,3" opacity={0.5} />
          <g transform={`translate(${tooltipLeft},${Math.max(hy - 48, PAD_T)})`}>
            <rect width={130} height={40} rx={8} fill={C.surfaceRaised} stroke={C.border} />
            <text x={10} y={16} fontSize={11} fontWeight={800} fill={C.text}>
              {Math.round(hp.rating)} &middot; {hp.won ? "W" : "L"}
            </text>
            <text x={10} y={30} fontSize={10} fill={C.subtle}>
              vs {hp.opponent.length > 16 ? hp.opponent.slice(0, 15) + "…" : hp.opponent}
            </text>
          </g>
        </>
      )}
    </svg>
  );
}
