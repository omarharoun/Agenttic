import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, auth, type Me } from "./api";
import { ThemeToggle } from "./components/ThemeToggle";
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
        <a className="logo" href="/" title="Agenttic home">⬡</a>
        <NavLink to="/app" end title="Workflow editor">▦</NavLink>
        <NavLink to="/app/executions" title="Executions">▶</NavLink>
        <NavLink to="/app/leaderboard" title="Agenttic Index leaderboard">🏆</NavLink>
        <NavLink to="/app/agents" title="Agents (declared + discovered)">🤖</NavLink>
        <NavLink to="/app/resources" title="Suites / scorecards / traces">▤</NavLink>
        <a href="/api-docs" title="API documentation">📖</a>
        <span style={{ flex: 1 }} />
        <ThemeToggle />
        <TokenControl />
        <button title={me ? `${me.email ?? me.auth_method} · ${me.role} · ${me.tenant}` : "account"}
                onClick={logout} style={{ fontSize: 15 }}>⎋</button>
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
