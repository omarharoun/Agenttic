import { lazy, Suspense, useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, auth, type Me } from "./api";
import { AccountMenu } from "./components/AccountMenu";
import { EmptyState } from "./components/ui";
import { useRunNotifications } from "./notify";
import { useExecutionEvents } from "./sse";
import { useFlowStore } from "./store";
import { AgentsPage } from "./pages/AgentsPage";
import { BillingPage } from "./pages/BillingPage";
import { CertificationsPage } from "./pages/CertificationsPage";
import { ComparePage } from "./pages/ComparePage";
import { DashboardPage } from "./pages/DashboardPage";
import { EditorPage } from "./pages/EditorPage";
import { ExecutionsPage } from "./pages/ExecutionsPage";
import { IssuesPage } from "./pages/IssuesPage";
import { ResultsHistoryPage } from "./pages/ResultsHistoryPage";
import { CapabilitiesPage } from "./pages/CapabilitiesPage";
import { HardeningPage } from "./pages/HardeningPage";
import { LeaderboardPage } from "./pages/LeaderboardPage";
import { OptimizePage } from "./pages/OptimizePage";
import { ResourcesPage } from "./pages/ResourcesPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TrainingCampPage } from "./pages/TrainingCampPage";

/* The Copilot panel is code-split: its chunk (chat + Markdown renderer) loads
   only when the user first opens the drawer, so it never weighs on the public
   landing bundle or the app-shell's initial chunk. */
const CopilotPanel = lazy(() => import("./copilot/CopilotPanel"));

/** Right-docked Copilot: a fixed launcher tab + the lazily-mounted drawer. The
 *  panel's JS is fetched on first open (mounted flag), then kept mounted so the
 *  session history survives re-opens. */
function CopilotDock() {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const toggle = () => { setMounted(true); setOpen((o) => !o); };
  return (
    <>
      <button className={`cp-launch ${open ? "hidden" : ""}`} onClick={toggle}
              aria-label="Open Agenttic Copilot" aria-expanded={open}
              title="Ask the Copilot">
        <span className="cp-launch-ic" aria-hidden>⬡</span>
        <span className="cp-launch-label">Copilot</span>
      </button>
      {mounted && (
        <Suspense fallback={null}>
          <CopilotPanel open={open} onClose={() => setOpen(false)} />
        </Suspense>
      )}
    </>
  );
}

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

/** The console navigation, organized around the one product arc: Score → Issues
 *  → Fix. Certify is demoted to a secondary group (still reachable, no longer the
 *  pitch). A benchmark authority opens on the Dashboard; the workflow builder is
 *  one demoted "New evaluation" entry, not the front door. */
const NAV_GROUPS: { title: string; items: { to: string; icon: string; label: string }[] }[] = [
  { title: "Score", items: [
    { to: "/app/build", icon: "＋", label: "New evaluation" },
    { to: "/app/executions", icon: "▶", label: "Runs" },
    { to: "/app/results", icon: "📊", label: "Results" },
    { to: "/app/leaderboard", icon: "🏆", label: "Leaderboard" },
    { to: "/app/compare", icon: "⚖", label: "Compare" },
  ]},
  { title: "Issues", items: [
    { to: "/app/issues", icon: "🔎", label: "Issues report" },
    { to: "/app/capabilities", icon: "◎", label: "What we test" },
  ]},
  { title: "Fix", items: [
    { to: "/app/training-camp", icon: "🎯", label: "Training Camp" },
    { to: "/app/hardening", icon: "🛡", label: "Hardening" },
    { to: "/app/optimize", icon: "✨", label: "Optimize" },
  ]},
  { title: "Certify", items: [
    { to: "/app/certifications", icon: "🏅", label: "Certification" },
  ]},
  { title: "Manage", items: [
    { to: "/app/agents", icon: "🤖", label: "Agents" },
    { to: "/app/resources", icon: "▤", label: "Resources" },
    { to: "/app/billing", icon: "💳", label: "Billing" },
    { to: "/app/settings", icon: "⚙", label: "Settings" },
  ]},
];

/** Honest in-app 404 for unknown /app/* routes — a blank screen would read as a
 *  broken page; this names the mistake and routes back to solid ground. */
function NotFoundPage() {
  const loc = useLocation();
  return (
    <div className="page">
      <EmptyState icon="🧭" title="Page not found"
        hint={<>No console page matches <code>{loc.pathname}</code>. It may have
          moved or the link may be mistyped.</>}
        action={<Link className="btn" to="/app">Back to dashboard</Link>} />
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
    () => sessionStorage.getItem("agenttic_key_nudge_dismissed") === "1");
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
    sessionStorage.setItem("agenttic_key_nudge_dismissed", "1");
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
    return (
      <div className="app-shell">
        <div className="app-loading">
          <span className="app-loading-brand"><span className="ic">⬡</span> Agenttic</span>
          <span className="spinner" />
          <span className="app-loading-note">Loading your workspace…</span>
        </div>
      </div>
    );
  }
  if (state === "denied") {
    return <div className="app-shell"><div className="app-loading">
      Could not reach the API. <Link to="/login">Sign in</Link></div></div>;
  }

  return (
    <div className="app-shell">
      <nav className="app-nav">
        <a className="logo" href="/" title="Agenttic home">
          <span className="ic">⬡</span> Agenttic
        </a>
        <NavLink to="/app" end className="nav-home">
          <span className="ic">▦</span> Dashboard
        </NavLink>
        {NAV_GROUPS.map((g) => (
          <div className="nav-group" key={g.title}>
            <div className="nav-group-title">{g.title}</div>
            {g.items.map((it) => (
              <NavLink key={it.to} to={it.to}>
                <span className="ic">{it.icon}</span> {it.label}
              </NavLink>
            ))}
          </div>
        ))}
        <span style={{ flex: 1 }} />
        <div className="nav-group">
          <div className="nav-group-title">More</div>
          <Link to="/api-docs"><span className="ic">📖</span> API docs</Link>
          <Link to="/assistant"><span className="ic">💬</span> Safe assistant</Link>
        </div>
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
            <Route path="/" element={<DashboardPage />} />
            <Route path="build" element={<EditorPage />} />
            <Route path="executions" element={<ExecutionsPage />} />
            <Route path="results" element={<ResultsHistoryPage />} />
            <Route path="capabilities" element={<CapabilitiesPage />} />
            <Route path="issues" element={<IssuesPage />} />
            <Route path="compare" element={<ComparePage />} />
            <Route path="leaderboard" element={<LeaderboardPage />} />
            <Route path="certifications" element={<CertificationsPage />} />
            <Route path="training-camp" element={<TrainingCampPage />} />
            <Route path="hardening" element={<HardeningPage />} />
            <Route path="optimize" element={<OptimizePage />} />
            <Route path="agents" element={<AgentsPage />} />
            <Route path="resources" element={<ResourcesPage />} />
            <Route path="billing" element={<BillingPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Routes>
        </div>
      </div>
      <CopilotDock />
    </div>
  );
}
