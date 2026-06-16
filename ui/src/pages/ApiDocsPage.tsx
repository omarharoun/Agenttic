import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ThemeToggle } from "../components/ThemeToggle";

const GROUP_TITLES: Record<string, string> = {
  auth: "Authentication", workflows: "Workflows", executions: "Executions",
  resources: "Agents, suites & catalog", cost: "Cost: estimate & quota",
  leaderboard: "Leaderboard", live: "Live monitoring",
};
const GROUP_ORDER = ["auth", "workflows", "executions", "resources", "cost",
  "leaderboard", "live"];

interface Ep { method: string; path: string; summary: string; group: string; }

export function ApiDocsPage() {
  const [spec, setSpec] = useState<any | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const origin = typeof window !== "undefined" ? window.location.origin : "https://agenttic.io";

  useEffect(() => {
    fetch("/openapi.json").then((r) => r.json()).then(setSpec)
      .catch(() => setErr("Could not load /openapi.json"));
  }, []);

  const groups = useMemo(() => {
    if (!spec?.paths) return {};
    const out: Record<string, Ep[]> = {};
    for (const [path, methods] of Object.entries<any>(spec.paths)) {
      for (const [method, op] of Object.entries<any>(methods)) {
        if (!["get", "post", "put", "delete", "patch"].includes(method)) continue;
        const group = (op.tags && op.tags[0]) || "other";
        (out[group] ??= []).push({
          method: method.toUpperCase(), path,
          summary: (op.summary || op.description || "").split("\n")[0], group,
        });
      }
    }
    for (const g of Object.values(out)) g.sort((a, b) => a.path.localeCompare(b.path));
    return out;
  }, [spec]);

  const curl = (e: Ep) => {
    const authed = e.group !== "auth";
    const lines = [`curl -X ${e.method} ${origin}${e.path}`];
    if (authed) lines.push(`  -H "Authorization: Bearer $ASCORE_API_TOKEN"`);
    if (["POST", "PUT", "PATCH"].includes(e.method) && e.group === "auth")
      lines.push(`  -H "Content-Type: application/json" \\\n  -d '{"email":"you@example.com","password":"…"}'`);
    return lines.join(" \\\n");
  };

  const orderedGroups = [
    ...GROUP_ORDER.filter((g) => groups[g]),
    ...Object.keys(groups).filter((g) => !GROUP_ORDER.includes(g)),
  ];

  return (
    <>
      <nav className="lp-nav">
        <Link to="/" className="brand"><span className="hex">⬡</span> Agenttic</Link>
        <span className="spacer" />
        <a className="navlink" href="/openapi.json">openapi.json</a>
        <a className="navlink" href="/docs">Swagger</a>
        <ThemeToggle />
      </nav>
      <div className="docs">
        <h1>API Reference</h1>
        <p style={{ color: "var(--muted)", fontSize: 15, lineHeight: 1.6 }}>
          The Agenttic HTTP API. The interactive Swagger UI lives at{" "}
          <a href="/docs" style={{ color: "var(--accent)" }}>/docs</a> and the raw
          spec at <a href="/openapi.json" style={{ color: "var(--accent)" }}>/openapi.json</a>.
        </p>

        <div className="group">
          <h2>Authentication</h2>
          <p className="summary">
            Two ways to authenticate, both honoring viewer / operator / admin roles
            and tenant scoping. <b>Bearer token</b> (for CI / API clients) takes
            precedence: send <code>Authorization: Bearer &lt;token&gt;</code>. Or use a{" "}
            <b>login session</b>: <code>POST /api/auth/login</code> sets an httponly
            cookie; browser requests then authenticate automatically (cookie-based
            mutations also need the <code>X-CSRF-Token</code> header, echoed from the
            <code> ascore_csrf</code> cookie). Get a session by{" "}
            <Link to="/signup" style={{ color: "var(--accent)" }}>signing up</Link>.
          </p>
          <pre className="curl">{`# bearer token (CI / scripts)
curl ${origin}/api/agents -H "Authorization: Bearer $ASCORE_API_TOKEN"

# session login
curl -X POST ${origin}/api/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"email":"you@example.com","password":"…"}' -c cookies.txt`}</pre>
        </div>

        {err && <p style={{ color: "var(--fail)" }}>{err}</p>}
        {!spec && !err && <p style={{ color: "var(--muted)" }}>Loading spec…</p>}

        {orderedGroups.map((g) => (
          <div className="group" key={g}>
            <h2>{GROUP_TITLES[g] || g}</h2>
            {groups[g].map((e) => (
              <div className="ep" key={`${e.method} ${e.path}`}>
                <div>
                  <span className={`method m-${e.method.toLowerCase()}`}>{e.method}</span>
                  <span className="path">{e.path}</span>
                </div>
                {e.summary && <p className="summary">{e.summary}</p>}
                <details>
                  <summary style={{ cursor: "pointer", color: "var(--muted)", fontSize: 12 }}>curl</summary>
                  <pre className="curl">{curl(e)}</pre>
                </details>
              </div>
            ))}
          </div>
        ))}

        <div className="group">
          <h2>Health &amp; ops</h2>
          <div className="ep"><span className="method m-get">GET</span>
            <span className="path">/health</span>
            <p className="summary">Liveness — <code>{`{"status":"ok"}`}</code>. Unauthenticated.</p></div>
          <div className="ep"><span className="method m-get">GET</span>
            <span className="path">/ready</span>
            <p className="summary">Readiness — DB ping; 503 when not ready. Unauthenticated.</p></div>
          <div className="ep"><span className="method m-get">GET</span>
            <span className="path">/metrics</span>
            <p className="summary">Prometheus metrics (requests, runs, LLM tokens/cost).</p></div>
        </div>
      </div>
      <footer className="lp"><div className="lp-footer">
        <Link to="/">← Back to home</Link>
      </div></footer>
    </>
  );
}
