import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Avatar, AvatarPair } from "../components/Avatar";
import { RatingChart, type RatingPoint } from "../components/RatingChart";
import { C, SIDE } from "../theme";
import type { HistoryRow, PlayerStats as Stats, StyleTagDetail } from "../types";

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

const card = {
  background: C.surface,
  border: `1px solid ${C.border}`,
  borderRadius: 14,
  padding: 16,
} as const;

const STYLE_TAG_LABEL: Record<string, string> = {
  aggressive: "Aggressive",
  defensive: "Defensive",
  consistent: "Consistent",
  "server-reliant": "Server-reliant",
};

export function PlayerStats() {
  const playerId = Number(useParams().playerId);
  const [s, setS] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setS(null);
    setError(null);
    api.playerStats(playerId).then(setS, (e) => setError(e.message));
  }, [playerId]);

  return (
    <div style={{ minHeight: "100%", background: C.bg, color: C.text }}>
      <div
        style={{
          maxWidth: 900,
          margin: "0 auto",
          padding: "20px 20px 48px",
          boxSizing: "border-box",
        }}
      >
        <Link
          to="/players"
          style={{ fontSize: 13, color: C.faint, textDecoration: "none", fontWeight: 600 }}
        >
          &larr; All players
        </Link>
        {error && (
          <div style={{ padding: "24px 0", color: C.coral, fontWeight: 700 }}>{error}</div>
        )}
        {s && <StatsBody s={s} />}
      </div>
    </div>
  );
}

function StatsBody({ s }: { s: Stats }) {
  const tiles: [string, string, string | null][] = [
    [pct(s.win_rate), "Win rate", null],
    [
      s.avg_rally_length == null ? "—" : s.avg_rally_length.toFixed(1),
      "Avg rally length",
      s.avg_rally_length == null ? "needs the table sensor" : null,
    ],
    [
      pct(s.forehand_winner_rate),
      "Forehand winners",
      s.forehand_winner_rate == null ? "stroke side isn't captured yet" : null,
    ],
    [pct(s.serve_win_rate), "Points won on serve", null],
  ];
  const delta = s.rating_delta;

  const ratingPoints: RatingPoint[] = s.history
    .filter((h) => h.rating_after != null)
    .slice()
    .reverse() // history is newest-first; the chart reads oldest-first
    .map((h) => ({
      label: relativeDay(h.closed_at),
      opponent: h.opponents.map((o) => o.name).join(" & ") || "—",
      rating: h.rating_after as number,
      won: h.won,
    }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24, marginTop: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <Avatar player={s.player} size={64} fontSize={22} background={C.teal} color={C.bg} />
        <div style={{ flexGrow: 1, minWidth: 160 }}>
          <div style={{ fontSize: 24, fontWeight: 800 }}>{s.player.name}</div>
          <div style={{ fontSize: 13, color: C.subtle }}>
            {s.matches_played} {s.matches_played === 1 ? "match" : "matches"} played
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="digits" style={{ fontSize: 32, color: C.lime, letterSpacing: 0.5 }}>
            {Math.round(s.rating)}
          </div>
          {delta != null && Math.round(delta) !== 0 && (
            <div style={{ fontSize: 12, color: delta > 0 ? C.teal : C.coral, fontWeight: 700 }}>
              {delta > 0 ? "▲" : "▼"} {Math.abs(Math.round(delta))} last match
            </div>
          )}
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 12,
        }}
      >
        {tiles.map(([value, label, note]) => (
          <div key={label} style={card}>
            <div className="digits" style={{ fontSize: 30, letterSpacing: 0.5 }}>
              {value}
            </div>
            <div style={{ fontSize: 11, color: C.muted, fontWeight: 600, marginTop: 2 }}>
              {label}
            </div>
            {note && (
              <div style={{ fontSize: 10, color: C.faint, marginTop: 4 }}>{note}</div>
            )}
          </div>
        ))}
      </div>

      {s.recent_form.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={sectionLabel}>Recent form</div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
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

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={sectionLabel}>Rating over time</div>
        <div style={card}>
          <RatingChart points={ratingPoints} color={C.teal} />
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={sectionLabel}>Playing style</div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: 10,
          }}
        >
          {s.style_tags_detail.map((d) => (
            <StyleTagCard key={d.tag} detail={d} />
          ))}
        </div>
      </div>

      {s.synergy.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={sectionLabel}>Doubles synergy</div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
              gap: 12,
            }}
          >
            {s.synergy.map((syn) => (
              <div key={syn.pair_id} style={{ ...card, display: "flex", flexDirection: "column", gap: 12 }}>
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
                  <div style={{ flexGrow: 1, minWidth: 0 }}>
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
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(3,1fr)",
                    gap: 8,
                    fontSize: 11,
                    color: C.subtle,
                    borderTop: `1px solid ${C.rowBorder}`,
                    paddingTop: 10,
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 700, color: C.text }}>{pct(syn.actual_win_rate)}</div>
                    actual win rate
                  </div>
                  <div>
                    <div style={{ fontWeight: 700, color: C.text }}>
                      {pct(syn.predicted_win_rate)}
                    </div>
                    predicted
                  </div>
                  <div>
                    <div style={{ fontWeight: 700, color: C.text }}>
                      {syn.synergy == null ? "—" : signed(Math.round(syn.synergy * 100)) + "pp"}
                    </div>
                    synergy
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingBottom: 8 }}>
        <div style={sectionLabel}>
          Match history {s.history.length > 0 && `(${s.history.length})`}
        </div>
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
              gap: 12,
              flexWrap: "wrap",
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
                {h.rating_after != null && ` · rating ${Math.round(h.rating_after)}`}
              </div>
            </div>
            <div className="digits" style={{ fontSize: 16, color: h.won ? C.teal : C.coral }}>
              {resultText(h)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function StyleTagCard({ detail }: { detail: StyleTagDetail }) {
  return (
    <div
      style={{
        ...card,
        padding: 14,
        opacity: detail.applies ? 1 : 0.55,
        borderColor: detail.applies ? C.lime : C.border,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: detail.applies ? C.lime : C.faint,
            flexShrink: 0,
          }}
        />
        <div style={{ fontSize: 13, fontWeight: 800, color: detail.applies ? C.lime : C.muted }}>
          {STYLE_TAG_LABEL[detail.tag] ?? detail.tag}
        </div>
      </div>
      <div style={{ fontSize: 11, color: C.subtle, marginTop: 6, lineHeight: 1.4 }}>
        {detail.reason}
      </div>
    </div>
  );
}
