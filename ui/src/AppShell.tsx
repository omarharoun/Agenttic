import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, auth, type Me } from "./api";
import { ThemeToggle } from "./components/ThemeToggle";
import { useRunNotifications } from "./notify";
import { useExecutionEvents } from "./sse";
import { useFlowStore } from "./store";
import { AgentsPage } from "./pages/AgentsPage";
import { EditorPage } from "./pages/EditorPage";
import { ExecutionsPage } from "./pages/ExecutionsPage";
import { LeaderboardPage } from "./pages/LeaderboardPage";
import { ResourcesPage } from "./pages/ResourcesPage";

/** Token control: paste an API token (CI/power users). Login is the normal path;
 * a token, if set, takes precedence over the session for API calls. */
function TokenControl() {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState(auth.get());
  const set = !!auth.get();
  return (
    <div style={{ position: "relative" }}>
      <button title={set ? "API token set" : "Set API token (optional)"}
              onClick={() => setOpen((o) => !o)}
              style={{ color: set ? "var(--ok)" : "var(--muted)" }}>🔑</button>
      {open && (
        <div style={{ position: "absolute", left: 36, top: 0, zIndex: 20,
                      background: "var(--panel-2)", border: "1px solid var(--border)",
                      borderRadius: 8, padding: 8, width: 220 }}>
          <label style={{ fontSize: 11, color: "var(--muted)" }}>API token (optional)</label>
          <input value={val} type="password" placeholder="for CI / API clients"
                 onChange={(e) => setVal(e.target.value)} style={{ width: "100%" }} />
          <button className="active" style={{ marginTop: 6 }}
                  onClick={() => { auth.set(val.trim()); setOpen(false);
                                   location.reload(); }}>save</button>
        </div>
      )}
    </div>
  );
}

/** The authenticated app: nav + routed pages. Guards on /api/me — a 401 (no
 * session and no token) bounces to /login. */
export function AppShell() {
  const nav = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "denied">("loading");
  // Subscribe to the active run here (above the router) so progress keeps
  // updating and notifications keep firing as the user navigates between pages.
  const execId = useFlowStore((s) => s.exec.executionId);
  useExecutionEvents(execId);
  useRunNotifications();

  useEffect(() => {
    api.me()
      .then((m) => { setMe(m); setState("ok"); })
      .catch((e: any) => {
        if (e?.status === 401) nav("/login?next=/app", { replace: true });
        else setState("denied");
      });
  }, [nav]);

  const logout = async () => {
    try { await api.logout(); } catch { /* ignore */ }
    auth.set("");
    nav("/login", { replace: true });
  };

  if (state === "loading") {
    return <div className="page"><div className="list-page">Loading…</div></div>;
  }
  if (state === "denied") {
    return <div className="page"><div className="list-page">
      Could not reach the API. <a href="/login">Sign in</a></div></div>;
  }

  return (
    <div className="app-shell">
      <nav className="app-nav">
        <a className="logo" href="/" title="Agenttic home">
          <span className="ic">⬡</span> Agenttic
        </a>
        <NavLink to="/app" end><span className="ic">▦</span> Workflows</NavLink>
        <NavLink to="/app/executions"><span className="ic">▶</span> Runs</NavLink>
        <NavLink to="/app/leaderboard"><span className="ic">🏆</span> Leaderboard</NavLink>
        <NavLink to="/app/agents"><span className="ic">🤖</span> Agents</NavLink>
        <NavLink to="/app/resources"><span className="ic">▤</span> Resources</NavLink>
        <a href="/api-docs"><span className="ic">📖</span> API docs</a>
        <span style={{ flex: 1 }} />
        <div className="nav-foot">
          <ThemeToggle />
          <TokenControl />
          <button title={me ? `${me.email ?? me.auth_method} · ${me.role} · ${me.tenant}` : "account"}
                  onClick={logout} className="icon-btn">⎋</button>
        </div>
      </nav>
      <Routes>
        <Route path="/" element={<EditorPage />} />
        <Route path="executions" element={<ExecutionsPage />} />
        <Route path="leaderboard" element={<LeaderboardPage />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="resources" element={<ResourcesPage />} />
      </Routes>
    </div>
  );
}
