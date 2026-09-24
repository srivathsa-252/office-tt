import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { C, SIDE } from "../theme";
import type { MatchSummary } from "../types";

function teamName(m: MatchSummary, side: "A" | "B"): string {
  const names = (side === "A" ? m.side_a : m.side_b).map((p) => p.name);
  return names.length ? names.join(" & ") : "—";
}

function relativeDay(iso: string): string {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(new Date()) - day(new Date(iso))) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 30) return `${days} days ago`;
  return new Date(iso).toLocaleDateString();
}

function resultText(m: MatchSummary): string {
  if (!m.games_won) return `${m.points_played} pt(s) so far`;
  const g = m.games_won;
  if (m.game_scores && m.game_scores.length === 1) {
    const s = m.game_scores[0];
    return `${s.A}–${s.B}`;
  }
  return `${g.A}–${g.B} games`;
}

export function MatchesList() {
  const [rows, setRows] = useState<MatchSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<MatchSummary | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () =>
    api.matches().then(setRows, (e) => setError(e.message));

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const confirmDelete = async () => {
    if (!deleting) return;
    setBusy(true);
    setDeleteError(null);
    try {
      await api.deleteMatch(deleting.id);
      setDeleting(null);
      refresh();
    } catch (e) {
      setDeleteError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ minHeight: "100%", background: C.bg, color: C.text }}>
      <div style={{ maxWidth: 900, margin: "0 auto", padding: "24px 20px 48px", boxSizing: "border-box" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14, marginBottom: 20, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 800 }}>Matches</h1>
          {rows && (
            <div style={{ fontSize: 13, color: C.subtle }}>
              {rows.length} {rows.length === 1 ? "match" : "matches"}
            </div>
          )}
          <Link
            to="/players"
            style={{ marginLeft: "auto", fontSize: 13, color: C.faint, fontWeight: 600, textDecoration: "none" }}
          >
            Players &rarr;
          </Link>
        </div>

        {error && <div style={{ color: C.coral, fontWeight: 700 }}>{error}</div>}
        {!rows && !error && <div style={{ color: C.faint, fontSize: 13 }}>Loading&hellip;</div>}
        {rows && rows.length === 0 && (
          <div style={{ color: C.faint, fontSize: 13 }}>No matches yet — start one from the table screen.</div>
        )}

        {rows && rows.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {rows.map((m) => (
              <MatchRow key={m.id} m={m} onDelete={() => setDeleting(m)} />
            ))}
          </div>
        )}
      </div>

      {deleting && (
        <ConfirmDialog
          title="Delete this match?"
          body={
            deleting.status === "live"
              ? "This match is still live — deleting it discards the session in progress. It can't be undone."
              : deleting.status === "finished"
                ? "Its rating changes will be reverted first. Refused if a later match already depends on that rating. This can't be undone."
                : "This can't be undone."
          }
          confirmLabel="Delete match"
          busy={busy}
          onConfirm={confirmDelete}
          onCancel={() => {
            setDeleting(null);
            setDeleteError(null);
          }}
        />
      )}
      {deleteError && (
        <div
          style={{
            position: "fixed",
            bottom: 20,
            left: "50%",
            transform: "translateX(-50%)",
            background: C.surface,
            border: `1px solid ${C.coral}`,
            color: C.coral,
            borderRadius: 12,
            padding: "10px 16px",
            fontSize: 13,
            fontWeight: 700,
            zIndex: 60,
          }}
        >
          {deleteError}
        </div>
      )}
    </div>
  );
}

function MatchRow({ m, onDelete }: { m: MatchSummary; onDelete: () => void }) {
  const statusColor =
    m.status === "live" ? C.live : m.status === "finished" ? C.lime : C.faint;
  const won = m.winner;

  return (
    <div
      style={{
        background: C.surface,
        border: `1px solid ${C.border}`,
        borderRadius: 14,
        padding: "14px 16px",
        display: "flex",
        alignItems: "center",
        gap: 14,
        flexWrap: "wrap",
      }}
    >
      <div style={{ width: 8, height: 8, borderRadius: "50%", background: statusColor, flexShrink: 0 }} />

      <div style={{ flexGrow: 1, minWidth: 200 }}>
        <div style={{ fontSize: 14, fontWeight: 700 }}>
          <span style={{ color: won === "A" ? SIDE.A.color : C.text }}>{teamName(m, "A")}</span>
          <span style={{ color: C.faint, fontWeight: 500 }}> vs </span>
          <span style={{ color: won === "B" ? SIDE.B.color : C.text }}>{teamName(m, "B")}</span>
          {m.mode === "doubles" && (
            <span style={{ color: C.faint, fontWeight: 500 }}> (doubles)</span>
          )}
        </div>
        <div className="mono" style={{ fontSize: 11, color: C.faint, marginTop: 2 }}>
          {m.status === "live" ? "LIVE" : m.status === "abandoned" ? "ABANDONED" : relativeDay(m.closed_at ?? m.created_at)}
          {" · best of "}
          {m.best_of}
        </div>
      </div>

      <div className="digits" style={{ fontSize: 18, color: C.text, minWidth: 70, textAlign: "right" }}>
        {resultText(m)}
      </div>

      {m.status === "live" && (
        <Link
          to={`/live/${m.id}`}
          style={{
            fontSize: 12,
            fontWeight: 800,
            color: C.bg,
            background: C.lime,
            borderRadius: 999,
            padding: "6px 14px",
            textDecoration: "none",
          }}
        >
          View
        </Link>
      )}

      <button
        onClick={onDelete}
        aria-label={`Delete match ${m.id}`}
        style={{
          background: "none",
          border: `1px solid ${C.border}`,
          borderRadius: 999,
          color: C.coral,
          width: 30,
          height: 30,
          fontSize: 14,
          fontWeight: 800,
        }}
      >
        &times;
      </button>
    </div>
  );
}
