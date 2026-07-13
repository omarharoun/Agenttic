import { useEffect, useMemo, useState } from "react";
import { SiteNav } from "../components/SiteNav";
import { Link } from "react-router-dom";

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
    if (authed) lines.push(`  -H "Authorization: Bearer $AGENTTIC_API_TOKEN"`);
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
      <SiteNav />
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
            Three ways to authenticate, all honoring viewer / operator / admin roles
            and tenant scoping. A <b>personal API token (PAT)</b> is the recommended
            way to script the API as your own account — create one in{" "}
            <Link to="/app/settings?section=api-keys" style={{ color: "var(--accent)" }}>Settings → API keys</Link>{" "}
            and send <code>Authorization: Bearer agt_…</code>. A configured{" "}
            <b>shared token</b> (<code>AGENTTIC_API_TOKEN</code>, or the legacy{" "}
            <code>ASCORE_API_TOKEN</code>, which still works) works the same way for
            CI. Or use a <b>login session</b>: <code>POST /api/auth/login</code> sets an
            httponly cookie; browser requests then authenticate automatically
            (cookie-based mutations also need the <code>X-CSRF-Token</code> header,
            echoed from the <code>ascore_csrf</code> cookie).
          </p>
          <p className="summary">
            <b>Precedence:</b> an explicit bearer/<code>X-API-Key</code>/<code>?token=</code>{" "}
            always wins over a session cookie. Among explicit tokens, a configured
            shared/admin token is matched first, then PATs. A PAT authenticates as its
            owning user (their tenant + role); revoking it takes effect immediately.
          </p>
          <pre className="curl">{`# personal API token (recommended — acts as your account)
curl ${origin}/api/me -H "Authorization: Bearer $AGENTTIC_TOKEN"

# session login (browser / interactive)
curl -X POST ${origin}/api/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"email":"you@example.com","password":"…"}' -c cookies.txt`}</pre>
        </div>

        <div className="group">
          <h2>Quickstart: run a test over REST</h2>
          <p className="summary">
            End-to-end with a personal API token. Runs use <b>your own stored Anthropic
            key</b> (set it in{" "}
            <Link to="/app/settings?section=api-keys" style={{ color: "var(--accent)" }}>Settings</Link>{" "}
            first, or these calls return <code>400 — Add your Anthropic API key</code>).
          </p>
          <pre className="curl">{`# 0) create a token in Settings → API keys, then:
export AGENTTIC_TOKEN=agt_…
AUTH="Authorization: Bearer $AGENTTIC_TOKEN"

# 1) generate a benchmark from a business requirement AND start a run
#    (builds the canonical generate→approve→run→score→report pipeline)
EXEC=$(curl -s -X POST ${origin}/api/quickstart/from-requirement -H "$AUTH" \\
  -H "Content-Type: application/json" \\
  -d '{"requirement":"The support agent must never reveal another customer'\\''s data.",
       "agent_id":"my-agent","system_prompt":"You are a careful support agent."}')
echo "$EXEC"   # -> {"workflow_id":"wf-…","execution_id":"ex-…","suite_id":"req-…"}
EID=$(echo "$EXEC" | python -c 'import sys,json;print(json.load(sys.stdin)["execution_id"])')

# 2) poll until the human gate pauses for approval
curl -s ${origin}/api/executions/$EID -H "$AUTH"      # status: "waiting_approval"

# 3) review the draft suite, then approve to continue the run
curl -s -X POST ${origin}/api/executions/$EID/approve -H "$AUTH"

# 4) poll until done, then fetch joined results (scorecard + per-case rows)
curl -s ${origin}/api/executions/$EID -H "$AUTH"      # status: "succeeded"
curl -s ${origin}/api/executions/$EID/results -H "$AUTH"

# 5) export: the scorecard JSON, a Markdown/PDF report, or the Inspect log
SC=…   # scorecard_id from the results
curl -s ${origin}/api/scorecards/$SC -H "$AUTH"
curl -s ${origin}/api/scorecards/$SC/report.pdf -H "$AUTH" -o report.pdf
curl -s ${origin}/api/scorecards/$SC/inspect.json -H "$AUTH" -o inspect.json

# --- or skip generation and run the standard (canonical) suites ---
curl -s -X POST ${origin}/api/standard/seed -H "$AUTH"
curl -s -X POST ${origin}/api/standard/run -H "$AUTH" \\
  -H "Content-Type: application/json" -d '{"agent_id":"my-agent","k":3}'
curl -s ${origin}/api/standard/leaderboard -H "$AUTH"`}</pre>
          <p className="summary" style={{ marginTop: 12 }}>
            <b>Result caching.</b> Runs are cached by their inputs (suite version,
            agent config, rubric version, judge models): an identical re-run is
            served from the prior scorecard with <b>no agent or judge calls — $0</b>,
            and the response/results carry <code>"cached": true</code>. It needs no
            human-gate approval and no Anthropic key (nothing runs). To recompute
            fresh, add <code>?force=true</code> (or <code>"refresh": true</code> in the
            quickstart body). Browse past results at{" "}
            <Link to="/app/results" style={{ color: "var(--accent)" }}>Results</Link>.
          </p>
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
