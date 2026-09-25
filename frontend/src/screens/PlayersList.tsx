import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { PlayerPhoto } from "../components/PlayerPhoto";
import { C, SIDE } from "../theme";
import type { PlayerStats } from "../types";

const pct = (x: number | null) => (x == null ? "—" : `${Math.round(x * 100)}%`);

export function PlayersList() {
  const [rows, setRows] = useState<PlayerStats[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.players().then(
      async (roster) => {
        const stats = await Promise.all(roster.map((p) => api.playerStats(p.id)));
        if (alive) setRows(stats);
      },
      (e) => alive && setError(e.message),
    );
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div style={{ minHeight: "100%", background: C.bg, color: C.text }}>
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "24px 20px 48px", boxSizing: "border-box" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 20, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 800 }}>Players</h1>
          {rows && (
            <div style={{ fontSize: 13, color: C.subtle }}>
              {rows.length} {rows.length === 1 ? "player" : "players"}
            </div>
          )}
          <Link
            to="/matches"
            style={{ marginLeft: "auto", fontSize: 13, color: C.faint, fontWeight: 600, textDecoration: "none" }}
          >
            Matches &rarr;
          </Link>
        </div>

        {error && <div style={{ color: C.coral, fontWeight: 700 }}>{error}</div>}
        {!rows && !error && <div style={{ color: C.faint, fontSize: 13 }}>Loading&hellip;</div>}
        {rows && rows.length === 0 && (
          <div style={{ color: C.faint, fontSize: 13 }}>
            No players yet — add one from the match setup screen.
          </div>
        )}

        {rows && rows.length > 0 && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
              gap: 14,
            }}
          >
            {rows.map((s) => (
              <PlayerCard key={s.player.id} s={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PlayerCard({ s }: { s: PlayerStats }) {
  return (
    <Link to={`/players/${s.player.id}`} style={{ textDecoration: "none", color: "inherit" }}>
      <div
        style={{
          background: C.surface,
          border: `1px solid ${C.border}`,
          borderRadius: 16,
          padding: 18,
          display: "flex",
          flexDirection: "column",
          gap: 14,
          height: "100%",
          boxSizing: "border-box",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <PlayerPhoto player={s.player} size={48} />
          <div style={{ flexGrow: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 800, overflow: "hidden", textOverflow: "ellipsis" }}>
              {s.player.name}
            </div>
            <div style={{ fontSize: 11, color: C.subtle }}>
              {s.matches_played} {s.matches_played === 1 ? "match" : "matches"}
            </div>
          </div>
          <div className="digits" style={{ fontSize: 22, color: C.lime }}>
            {Math.round(s.rating)}
          </div>
        </div>

        <div style={{ display: "flex", gap: 16 }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{pct(s.win_rate)}</div>
            <div style={{ fontSize: 10, color: C.muted }}>win rate</div>
          </div>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{pct(s.serve_win_rate)}</div>
            <div style={{ fontSize: 10, color: C.muted }}>on serve</div>
          </div>
        </div>

        {s.recent_form.length > 0 && (
          <div style={{ display: "flex", gap: 4 }}>
            {s.recent_form.map((r, i) => {
              const side = r === "W" ? SIDE.A : SIDE.B;
              return (
                <div
                  key={i}
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: 5,
                    background: `rgba(${side.tint},0.15)`,
                    color: side.color,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 10,
                    fontWeight: 800,
                  }}
                >
                  {r}
                </div>
              );
            })}
          </div>
        )}

        {s.style_tags.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {s.style_tags.map((t) => (
              <div
                key={t}
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: C.lime,
                  background: "rgba(200,255,77,0.1)",
                  border: `1px solid rgba(200,255,77,0.3)`,
                  borderRadius: 999,
                  padding: "3px 8px",
                }}
              >
                {t}
              </div>
            ))}
          </div>
        )}
      </div>
    </Link>
  );
}
