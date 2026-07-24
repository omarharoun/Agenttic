import { useEffect, useState } from "react";
import { api } from "../api";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";

/* ============================================================================
   What we test — the verification surface.

   Every number on this page is enumerated from the LIVE registries at request
   time, not written by hand. If a check is unregistered or an archetype removed,
   this page says so on the next load. A capability page written as copy drifts
   from the product within a release, and then it is a claim nobody can verify.
   The "not covered" block is deliberately as prominent as the rest.
   ========================================================================== */

function Count({ n, label }: { n: number | string; label: string }) {
  return (
    <div className="cap-count">
      <b>{n}</b>
      <span>{label}</span>
    </div>
  );
}

function Section({ title, sub, children }: {
  title: string; sub?: string; children: React.ReactNode;
}) {
  return (
    <section className="cap-section">
      <h3>{title}</h3>
      {sub && <p className="cap-sub">{sub}</p>}
      {children}
    </section>
  );
}

export function CapabilitiesPage() {
  const [c, setC] = useState<any>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.capabilities().then(setC).catch((e) => setErr(String(e?.message || e)));
  }, []);

  if (err) return <EmptyState icon="⚠" title="Could not load the surface" hint={err} />;
  if (!c) return <Skeleton rows={8} />;

  const cov = c.coverage, formal = c.formal, sc = c.supply_chain;

  return (
    <div className="cap-page">
      <PageHeader title="What we test"
        subtitle="The verification surface, enumerated from the live registries — not a brochure." />

      <div className="cap-counts">
        <Count n={cov.baseline.coverpoints.length + cov.fitted_example.coverpoints.length}
               label="coverage dimensions" />
        <Count n={c.assertions.total} label="continuous properties" />
        <Count n={formal.total} label="provable properties" />
        <Count n={c.deterministic_checks.total} label="deterministic checks" />
        <Count n={sc.mcp_server.checks.length} label="MCP server checks" />
        <Count n={c.archetypes.total} label="agent archetypes" />
        <Count n={c.methodologies.total} label="published methodologies" />
      </div>

      <Section title="Coverage — what we can tell you was never exercised"
        sub={cov.baseline.limits}>
        <div className="cap-note">
          Applies to <b>{cov.baseline.applies_to}</b>.
        </div>
        <table className="cap-table">
          <thead><tr><th>Dimension</th><th>Detects</th></tr></thead>
          <tbody>
            {cov.baseline.coverpoints.map((cp: any) => (
              <tr key={cp.id}>
                <td className="cap-k">{cp.id}</td>
                <td>{cp.bins.map((b: string) => <code key={b}>{b}</code>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="cap-note">
          A fitted model for an archetype adds the semantic dimensions —{" "}
          {cov.fitted_example.provisional.map((p: string) => <code key={p}>{p}</code>)}{" "}
          — which stay <b>PROVISIONAL</b> until a calibration study against humans.
        </div>
      </Section>

      <Section title="Properties monitored on every run"
        sub="Checked throughout the run, including runs that score perfectly. A violation is a failure regardless of the score; a property whose situation never arose is reported unexercised, never passed.">
        <table className="cap-table">
          <tbody>
            {c.assertions.items.map((a: any) => (
              <tr key={a.id}>
                <td className="cap-k"><code>{a.id}</code></td>
                <td><span className={`cap-sev cap-sev--${a.severity}`}>{a.severity}</span></td>
                <td>{a.property}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="What we can prove, not sample"
        sub={`Scope: ${formal.scope}. ${formal.limit}`}>
        <ul className="cap-list">
          {formal.items.map((f: any) => <li key={f.id}>{f.description}</li>)}
        </ul>
        <div className="cap-note">
          Results are four-valued — {formal.result_values.map((v: string) => (
            <code key={v}>{v}</code>
          ))} — so a bounded check or a missing solver can never read as proven.
          {!formal.solver_available && " (symbolic solver not installed here; "
            + "exhaustive reachability over the finite guard layer still applies.)"}
        </div>
      </Section>

      <Section title="The supply chain, not just the agent"
        sub="The tools and servers an agent depends on are tested as subjects in their own right.">
        <div className="cap-grid">
          <div>
            <h4>MCP servers <span className="cap-dim">({sc.mcp_server.transports.join(" · ")})</span></h4>
            <ul className="cap-list">
              {sc.mcp_server.checks.map((k: string) => <li key={k}><code>{k}</code></li>)}
            </ul>
          </div>
          <div>
            <h4>Tools <span className="cap-dim">({sc.tools.sources.join(" · ")})</span></h4>
            <ul className="cap-list">
              {sc.tools.checks.map((k: string) => <li key={k}><code>{k}</code></li>)}
            </ul>
          </div>
        </div>
      </Section>

      <Section title="Evidence"
        sub={c.attestation.governing_rule}>
        <ul className="cap-list">
          {c.attestation.properties.map((p: string) => <li key={p}>{p}</li>)}
        </ul>
      </Section>

      <Section title="Deterministic checks"
        sub={`${c.deterministic_checks.total} code checks — no model in the loop, same input, same result.`}>
        <table className="cap-table">
          <tbody>
            {Object.entries(c.deterministic_checks.groups).map(([g, names]: any) => (
              <tr key={g}>
                <td className="cap-k">{g} <span className="cap-dim">({names.length})</span></td>
                <td>{names.map((n: string) => <code key={n}>{n}</code>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Published methodologies we run"
        sub="Third-party benchmark suites, ingested as evidence.">
        <div>{c.methodologies.items.map((m: string) => <code key={m}>{m}</code>)}</div>
      </Section>

      <Section title="What we do NOT cover"
        sub="Stated as plainly as the rest. An honest surface names its edges.">
        <ul className="cap-list cap-list--warn">
          {c.not_covered.map((n: string) => <li key={n}>{n}</li>)}
        </ul>
      </Section>
    </div>
  );
}
