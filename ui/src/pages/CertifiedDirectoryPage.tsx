import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { certIdOf, type DirectoryEntry, gradeColor, statusView } from "../cert";
import { Seal, SealMark } from "../components/Seal";
import { Skeleton } from "../components/ui";

/* ============================================================================
   Public Certified Agents directory — /certified (unauthenticated).

   The brand showcase: a clean leaderboard of agents that hold an Agenttic
   Safety Certification, each linking to its public certificate. Degrades to an
   honest empty state when nothing is certified yet or the endpoint is absent —
   never a fake roster.
   ========================================================================== */

function normalize(raw: any): DirectoryEntry[] {
  const list = Array.isArray(raw) ? raw
    : Array.isArray(raw?.certifications) ? raw.certifications
    : Array.isArray(raw?.agents) ? raw.agents
    : [];
  return list.map((c: any) => ({
    id: certIdOf(c),
    agent_name: c.agent_name ?? c.agent_id ?? "Unnamed agent",
    grade: c.grade ?? "—",
    index: typeof c.index === "number" ? c.index : null,
    issued_at: c.issued_at ?? "",
    status: c.status ?? "valid",
  })).filter((c: DirectoryEntry) => c.id);
}

export function CertifiedDirectoryPage() {
  const [rows, setRows] = useState<DirectoryEntry[] | null | undefined>(undefined);

  useEffect(() => {
    let ok = true;
    api.publicCertifiedDirectory()
      .then((d) => { if (ok) setRows(normalize(d)); })
      .catch(() => { if (ok) setRows(null); });
    return () => { ok = false; };
  }, []);

  const list = rows ?? [];

  return (
    <>
      <nav className="lp-nav">
        <Link to="/" className="brand"><span className="hex">⬡</span> Agenttic</Link>
        <span className="spacer" />
        <Link className="navlink" to="/methodology">Methodology</Link>
        <a className="navlink" href="/api-docs">API docs</a>
        <Link className="btn-primary" to="/signup">Get certified</Link>
      </nav>

      <main className="lp">
        <header className="cert-dir-hero">
          <Seal size={108} />
          <span className="eyebrow">The registry</span>
          <h1>Certified agents</h1>
          <p className="sub">
            AI agents that have earned an Agenttic Safety Certification — graded on
            injection robustness, harmful-action refusal, secret-leak resistance
            and more, with the grade pinned to a specific agent version. Every
            entry links to a signed, verifiable certificate.
          </p>
        </header>

        {rows === undefined ? (
          <div className="cert-dir-skel" aria-busy="true" aria-label="Loading certified agents">
            <Skeleton rows={6} />
          </div>
        ) : list.length === 0 ? (
          <div className="cert-dir-empty">
            <div className="empty-ico">◌</div>
            <div className="empty-title">No certified agents yet</div>
            <div className="empty-hint">
              Be the first. Run your agent through the safety suites and publish a
              grade the world can verify.
            </div>
            <div className="empty-action">
              <Link className="btn-primary" to="/signup">Get your agent certified</Link>
            </div>
          </div>
        ) : (
          <div className="cert-dir-grid">
            {list.map((c) => {
              const sv = statusView(c.status);
              return (
                <Link key={c.id} className="cert-dir-card" to={`/certified/${c.id}`}>
                  <Seal grade={c.grade} size={72} />
                  <div className="cdc-body">
                    <div className="cdc-name">{c.agent_name}</div>
                    <div className="cdc-sub">
                      <span className="cdc-grade" style={{ color: gradeColor(c.grade) }}>
                        Grade {c.grade}
                      </span>
                      {typeof c.index === "number" && (
                        <span className="cdc-index">Index {c.index}</span>
                      )}
                      <span className={`cdc-status ${sv.tone}`}>{sv.icon} {sv.label}</span>
                    </div>
                  </div>
                  <span className="cdc-go" aria-hidden="true">→</span>
                </Link>
              );
            })}
          </div>
        )}

        <section className="section" style={{ textAlign: "center", marginTop: 8 }}>
          <h2>How certification works</h2>
          <p className="lede" style={{ margin: "0 auto 22px" }}>
            Every grade comes from the same literature-anchored safety suites,
            scored from the agent's trace and signed by Agenttic.
          </p>
          <div className="cta" style={{ justifyContent: "center" }}>
            <Link className="btn-primary" to="/signup">Get certified</Link>
            <Link className="btn-ghost" to="/methodology">Read the methodology</Link>
          </div>
        </section>
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <SealMark />
          <Link to="/">Home</Link>
          <Link to="/methodology">Methodology</Link>
          <a href="/api-docs">API docs</a>
          <span style={{ flex: 1 }} />
          <span>Agent Safety Certification</span>
        </div>
      </footer>
    </>
  );
}
