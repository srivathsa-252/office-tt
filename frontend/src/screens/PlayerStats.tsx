import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { Avatar, AvatarPair } from "../components/Avatar";
import { Stage } from "../components/Stage";
import { C, SIDE } from "../theme";
import type { HistoryRow, PlayerStats as Stats } from "../types";

const pct = (x: number | null) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const signed = (x: number) => `${x >= 0 ? "+" : "−"}${Math.abs(Math.round(x))}`;

function relativeDay(iso: string): string {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(new Date()) - day(new Date(iso))) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 30) return `${days} days ago`;
  return new Date(iso).toLocaleDateString();
}

/** One game: its points ("21–18"). Several games: games won ("2–1"). */
function resultText(h: HistoryRow): string {
  if (h.games.length === 1) return `${h.games[0].own}–${h.games[0].opp}`;
  return `${h.games_won.own}–${h.games_won.opp}`;
}

const sectionLabel = {
  fontSize: 12,
  fontWeight: 700,
  letterSpacing: 0.4,
  color: C.muted,
  textTransform: "uppercase",
} as const;

export function PlayerStats() {
  const playerId = Number(useParams().playerId);
  const [s, setS] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.playerStats(playerId).then(setS, (e) => setError(e.message));
  }, [playerId]);

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
          overflowY: "auto",
        }}
      >
        {error && <div style={{ padding: 24, color: C.coral, fontWeight: 700 }}>{error}</div>}
        {s && <StatsBody s={s} />}
      </div>
    </Stage>
  );
}

function StatsBody({ s }: { s: Stats }) {
  const syn = s.synergy[0];
  const tiles: [string, string][] = [
    [pct(s.win_rate), "Win rate"],
    [s.avg_rally_length == null ? "—" : s.avg_rally_length.toFixed(1), "Avg rally length"],
    [pct(s.forehand_winner_rate), "Forehand winners"],
    [pct(s.serve_win_rate), "Points won on serve"],
  ];
  const delta = s.rating_delta;

  return (
    <>
      <div style={{ padding: "28px 24px 20px", display: "flex", alignItems: "center", gap: 14 }}>
        <Avatar player={s.player} size={56} fontSize={20} background={C.teal} color={C.bg} />
        <div style={{ flexGrow: 1 }}>
          <div style={{ fontSize: 20, fontWeight: 800 }}>{s.player.name}</div>
          <div style={{ fontSize: 12, color: C.subtle }}>
            {s.matches_played} {s.matches_played === 1 ? "match" : "matches"} played
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="digits" style={{ fontSize: 26, color: C.lime, letterSpacing: 0.5 }}>
            {Math.round(s.rating)}
          </div>
          {delta != null && Math.round(delta) !== 0 && (
            <div style={{ fontSize: 11, color: delta > 0 ? C.teal : C.coral, fontWeight: 700 }}>
              {delta > 0 ? "▲" : "▼"} {Math.abs(Math.round(delta))}
            </div>
          )}
        </div>
      </div>

      <div style={{ padding: "0 24px", display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 12 }}>
          {tiles.map(([value, label]) => (
            <div
              key={label}
              style={{
                background: C.surface,
                border: `1px solid ${C.border}`,
                borderRadius: 14,
                padding: 16,
              }}
            >
              <div className="digits" style={{ fontSize: 30, letterSpacing: 0.5 }}>
                {value}
              </div>
              <div style={{ fontSize: 11, color: C.muted, fontWeight: 600, marginTop: 2 }}>
                {label}
              </div>
            </div>
          ))}
        </div>

        {s.recent_form.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={sectionLabel}>Recent form</div>
            <div style={{ display: "flex", gap: 6 }}>
              {s.recent_form.map((r, i) => {
                const side = r === "W" ? SIDE.A : SIDE.B;
                return (
                  <div
                    key={i}
                    style={{
                      width: 30,
                      height: 30,
                      borderRadius: 8,
                      background: `rgba(${side.tint},0.15)`,
                      color: side.color,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 12,
                      fontWeight: 800,
                    }}
                  >
                    {r}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {syn && (
          <div
            style={{
              background: C.surface,
              border: `1px solid ${C.border}`,
              borderRadius: 14,
              padding: 16,
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <div style={sectionLabel}>Doubles synergy</div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <AvatarPair
                lead={s.player}
                partner={syn.partner}
                size={38}
                fontSize={13}
                overlap={12}
                color={C.teal}
                leadText={C.bg}
                partnerFill={SIDE.A.cardPartner}
                ring={C.surface}
              />
              <div style={{ flexGrow: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 700 }}>with {syn.partner.name}</div>
                <div style={{ fontSize: 11, color: C.subtle }}>
                  {syn.matches} {syn.matches === 1 ? "match" : "matches"} together
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div className="digits" style={{ fontSize: 18, color: C.text }}>
                  {Math.round(syn.rating)}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: syn.vs_solo_avg >= 0 ? C.lime : C.coral,
                    fontWeight: 700,
                  }}
                >
                  {signed(syn.vs_solo_avg)} vs solo avg
                </div>
              </div>
            </div>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingBottom: 28 }}>
          <div style={sectionLabel}>Match history</div>
          {s.history.length === 0 && (
            <div style={{ fontSize: 13, color: C.faint, padding: "12px 4px" }}>No matches yet</div>
          )}
          {s.history.map((h, i) => (
            <div
              key={h.match_id}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "12px 4px",
                borderBottom: i < s.history.length - 1 ? `1px solid ${C.rowBorder}` : undefined,
              }}
            >
              <div>
                <div style={{ fontSize: 14, fontWeight: 700 }}>
                  vs {h.opponents.map((o) => o.name).join(" & ")}
                  {h.mode === "doubles" && (
                    <>
                      {" "}
                      <span style={{ color: C.faint, fontWeight: 500 }}>(doubles)</span>
                    </>
                  )}
                </div>
                <div className="mono" style={{ fontSize: 11, color: C.faint }}>
                  {relativeDay(h.closed_at)}
                </div>
              </div>
              <div
                className="digits"
                style={{ fontSize: 16, color: h.won ? C.teal : C.coral }}
              >
                {resultText(h)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
