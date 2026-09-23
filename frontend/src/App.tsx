import { useEffect } from "react";
import { BrowserRouter, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "./api";
import { LiveScoreboard } from "./screens/LiveScoreboard";
import { MatchSetup } from "./screens/MatchSetup";
import { PlayerStats } from "./screens/PlayerStats";
import { PlayersList } from "./screens/PlayersList";

/** "/" resumes the live match if there is one, else goes to setup. */
function Home() {
  const navigate = useNavigate();
  useEffect(() => {
    api.liveMatch().then(
      (m) => navigate(`/live/${m.id}`, { replace: true }),
      () => navigate("/setup", { replace: true }),
    );
  }, [navigate]);
  return null;
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/setup" element={<MatchSetup />} />
        <Route path="/live/:matchId" element={<LiveScoreboard />} />
        <Route path="/players" element={<PlayersList />} />
        <Route path="/players/:playerId" element={<PlayerStats />} />
      </Routes>
    </BrowserRouter>
  );
}
