import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, auth, type Me } from "./api";
import { AccountMenu } from "./components/AccountMenu";
import { useRunNotifications } from "./notify";
import { useExecutionEvents } from "./sse";
import { useFlowStore } from "./store";
import { AgentsPage } from "./pages/AgentsPage";
import { CertificationsPage } from "./pages/CertificationsPage";
import { ComparePage } from "./pages/ComparePage";
import { EditorPage } from "./pages/EditorPage";
import { ExecutionsPage } from "./pages/ExecutionsPage";
import { ResultsHistoryPage } from "./pages/ResultsHistoryPage";
import { HardeningPage } from "./pages/HardeningPage";
import { LeaderboardPage } from "./pages/LeaderboardPage";
import { OptimizePage } from "./pages/OptimizePage";
import { ResourcesPage } from "./pages/ResourcesPage";
import { SettingsPage } from "./pages/SettingsPage";

/** Token control: paste an API token (CI/power users). Login is the normal path;
 * a token, if set, takes precedence over the session for API calls. */
function TokenControl() {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState(auth.get());
  const set = !!auth.get();
  return (
    <div style={{ position: "relative" }}>
      <button title={set ? "API token set" : "Set API token (optional)"}
              onClick={() => setOpen((o) => !o)} className="icon-btn"
              style={{ color: set ? "var(--ok)" : "var(--muted)" }}>🔑</button>
      {open && (
        <div style={{ position: "absolute", left: 0, bottom: 42, zIndex: 20,
                      background: "var(--panel-2)", border: "1px solid var(--border)",
                      borderRadius: 10, padding: 10, width: 220, boxShadow: "var(--shadow)" }}>
          <label style={{ fontSize: 11, color: "var(--muted)" }}>API token (optional)</label>
          <input value={val} type="password" placeholder="for CI / API clients"
                 onChange={(e) => setVal(e.target.value)} style={{ width: "100%" }} />
          <button className="active" style={{ marginTop: 6 }}
                  onClick={() => { auth.set(val.trim()); setOpen(false); location.reload(); }}>save</button>
        </div>
      )}
    </div>
  );
}

/** The authenticated console: sidebar + top bar + routed pages. Guards on
 * /api/me — a 401 bounces to /login. */
export function AppShell() {
  const nav = useNavigate();
  const loc = useLocation();
  const [me, setMe] = useState<Me | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "denied">("loading");
  const [keySet, setKeySet] = useState<boolean | null>(null);
  const [nudgeDismissed, setNudgeDismissed] = useState(
    () => sessionStorage.getItem("ascore_key_nudge_dismissed") === "1");
  const execId = useFlowStore((s) => s.exec.executionId);
  useExecutionEvents(execId);   // subscribe above the router so runs survive nav
  useRunNotifications();

  // first-run onboarding: re-check whether the tenant has an Anthropic key
  // whenever the route changes (so the nudge clears right after one is added)
  useEffect(() => {
    if (state !== "ok") return;
    api.anthropicKeyStatus().then((s) => setKeySet(s.set)).catch(() => setKeySet(null));
  }, [state, loc.pathname]);

  const onSettings = loc.pathname.startsWith("/app/settings");
  const showNudge = state === "ok" && keySet === false && !nudgeDismissed && !onSettings;
  const dismissNudge = () => {
    sessionStorage.setItem("ascore_key_nudge_dismissed", "1");
    setNudgeDismissed(true);
  };

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
    return <div className="app-shell"><div className="app-loading"><span className="spinner" /></div></div>;
  }
  if (state === "denied") {
    return <div className="app-shell"><div className="app-loading">
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
        <NavLink to="/app/results"><span className="ic">📊</span> Results</NavLink>
        <NavLink to="/app/compare"><span className="ic">⚖</span> Compare</NavLink>
        <NavLink to="/app/leaderboard"><span className="ic">🏆</span> Leaderboard</NavLink>
        <NavLink to="/app/certifications"><span className="ic">🏅</span> Certification</NavLink>
        <NavLink to="/app/hardening"><span className="ic">🛡</span> Hardening</NavLink>
        <NavLink to="/app/optimize"><span className="ic">✨</span> Optimize</NavLink>
        <NavLink to="/app/agents"><span className="ic">🤖</span> Agents</NavLink>
        <NavLink to="/app/resources"><span className="ic">▤</span> Resources</NavLink>
        <NavLink to="/app/settings"><span className="ic">⚙</span> Settings</NavLink>
        <a href="/api-docs"><span className="ic">📖</span> API docs</a>
        <span style={{ flex: 1 }} />
        <div className="nav-foot"><TokenControl /></div>
      </nav>

      <div className="app-body">
        <header className="app-topbar">
          <div className="topbar-ws">
            <span className="topbar-ws-cap">Workspace</span>
            <span className="topbar-ws-name mono">{me?.tenant ?? "default"}</span>
          </div>
          <span style={{ flex: 1 }} />
          <AccountMenu me={me} onLogout={logout} />
        </header>
        {showNudge && (
          <div className="key-nudge">
            <span className="kn-ico">🔑</span>
            <span className="kn-text">
              <b>Add your Anthropic API key to start running tests.</b> Agenttic
              runs your agents with your own key — it's encrypted at rest and
              never shared.
            </span>
            <Link className="kn-cta" to="/app/settings?section=api-keys">Add key</Link>
            <button className="kn-x" onClick={dismissNudge} title="Dismiss">✕</button>
          </div>
        )}
        <div className="app-routes">
          <Routes>
            <Route path="/" element={<EditorPage />} />
            <Route path="executions" element={<ExecutionsPage />} />
            <Route path="results" element={<ResultsHistoryPage />} />
            <Route path="compare" element={<ComparePage />} />
            <Route path="leaderboard" element={<LeaderboardPage />} />
            <Route path="certifications" element={<CertificationsPage />} />
            <Route path="hardening" element={<HardeningPage />} />
            <Route path="optimize" element={<OptimizePage />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="resources" element={<ResourcesPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}
