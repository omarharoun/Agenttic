import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { auth } from "./api";
import { AgentsPage } from "./pages/AgentsPage";
import { EditorPage } from "./pages/EditorPage";
import { ExecutionsPage } from "./pages/ExecutionsPage";
import { LeaderboardPage } from "./pages/LeaderboardPage";
import { ResourcesPage } from "./pages/ResourcesPage";

/** Token control: paste the API token once; stored in localStorage and sent on
 * every request (and SSE). Only needed when the server has auth enabled. */
function TokenControl() {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState(auth.get());
  const set = !!auth.get();
  return (
    <div style={{ position: "relative" }}>
      <button title={set ? "API token set" : "Set API token"}
              onClick={() => setOpen((o) => !o)}
              style={{ color: set ? "var(--ok)" : "var(--muted)" }}>🔑</button>
      {open && (
        <div style={{ position: "absolute", left: 36, top: 0, zIndex: 20,
                      background: "var(--panel-2)", border: "1px solid var(--border)",
                      borderRadius: 8, padding: 8, width: 220 }}>
          <label style={{ fontSize: 11, color: "var(--muted)" }}>API token</label>
          <input value={val} type="password" placeholder="paste token"
                 onChange={(e) => setVal(e.target.value)}
                 style={{ width: "100%" }} />
          <button className="active" style={{ marginTop: 6 }}
                  onClick={() => { auth.set(val.trim()); setOpen(false);
                                   location.reload(); }}>save</button>
        </div>
      )}
    </div>
  );
}

export function App() {
  return (
    <>
      <nav className="app-nav">
        <div className="logo" title="Agenttic">⬡</div>
        <NavLink to="/" end title="Workflow editor">▦</NavLink>
        <NavLink to="/executions" title="Executions">▶</NavLink>
        <NavLink to="/leaderboard" title="Agenttic Index leaderboard">🏆</NavLink>
        <NavLink to="/agents" title="Agents (declared + discovered)">🤖</NavLink>
        <NavLink to="/resources" title="Suites / scorecards / traces">▤</NavLink>
        <span style={{ flex: 1 }} />
        <TokenControl />
      </nav>
      <Routes>
        <Route path="/" element={<EditorPage />} />
        <Route path="/executions" element={<ExecutionsPage />} />
        <Route path="/leaderboard" element={<LeaderboardPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/resources" element={<ResourcesPage />} />
      </Routes>
    </>
  );
}
