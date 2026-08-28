import { useCallback, useEffect, useState } from "react";
import { SiteNav } from "../components/SiteNav";
import { Link } from "react-router-dom";
import { api, type JsonObject } from "../api";
import { certIdOf, type DirectoryEntry, gradeColor, indexFromCert, statusView } from "../cert";
import { Seal, SealMark } from "../components/Seal";
import { EmptyState, Skeleton } from "../components/ui";
import { PageData } from "../components/PageData";
import { IconArrowRight, StatusIcon } from "../icons";
import { Button, Eyebrow, SectionHeading } from "../components/ds/primitives";

/* ============================================================================
   Public Certified Agents directory — /certified (unauthenticated).

   The register: agents that hold an Agenttic Safety Certification, each linking
   to its public certificate. Restyled onto the academy language the landing
   route speaks — practice, pressure-test, qualify — because this page is the
   last stage of that program made public.

   Degrades to an honest empty state when nothing is certified yet or the
   endpoint is absent — never a fake roster. The empty state is DESIGNED rather
   than apologetic: a register with nothing on it is what a certifier that
   refuses looks like before anyone has earned an entry, and saying so is the
   whole point of the page.
   ========================================================================== */

function normalize(raw: DirectoryEntry[] | JsonObject): DirectoryEntry[] {
  const list: unknown[] = Array.isArray(raw) ? raw
    : Array.isArray(raw?.certifications) ? raw.certifications
    : Array.isArray(raw?.agents) ? raw.agents
    : [];
  return list.map((entry): DirectoryEntry => {
    const c = (entry ?? {}) as Record<string, unknown>;
    return {
      id: certIdOf(c),
      agent_name: (c.agent_name ?? c.agent_id ?? "Unnamed agent") as string,
      grade: (c.grade ?? "—") as string,
      index: indexFromCert(c),
      issued_at: (c.issued_at ?? "") as string,
      status: (c.status ?? "valid") as DirectoryEntry["status"],
    };
  }).filter((c) => c.id);
}

/** How an agent reaches this page — the same three stages the program runs. */
const ROUTE = [
  { n: "01", icon: "↗", h: "Practice", mod: " lp-program__card--train",
    p: "Drills against a suite fitted to that agent. Every attempt graded, every failing trace kept with the seed to reproduce it." },
  { n: "02", icon: "⌁", h: "Pressure-test", mod: "",
    p: "The suite qualifies before the agent does: it has to separate a known-good agent from a known-bad one, or it is rejected and rebuilt." },
  { n: "03", icon: "✦", h: "Qualify", mod: " lp-program__card--certify",
    p: "Coverage of the situation space has to close and every safety property has to hold. The signing path refuses when they do not." },
];

/** What the credential attests to, and — as plainly — what it does not. */
const SAYS: { k: string; v: string; not?: boolean }[] = [
  { k: "One version", v: "The grade is pinned to one exact agent configuration. Change the agent and the credential does not follow it across." },
  { k: "One scope", v: "The signed scope travels with the grade: which properties were exercised, how far coverage closed, and which situations nothing touched." },
  { k: "An expiry", v: "Every entry carries an expiry date. Evidence goes stale as models are updated underneath an agent, so it is dated rather than perpetual." },
  { k: "A revocation path", v: "An entry can be suspended and shows as revoked here and on its certificate. The register is the live view, not a snapshot of issuance day." },
  { k: "Not a forecast", v: "Nothing here says how an agent will behave in a situation nobody tested. It reports what was measured, under which conditions.", not: true },
  { k: "Not a ranking", v: "This is a register, not a leaderboard. Two agents with the same grade were scoped differently, and the scope is the part worth reading.", not: true },
];

export function CertifiedDirectoryPage() {
  const [rows, setRows] = useState<DirectoryEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    let ok = true;
    api.publicCertifiedDirectory()
      .then((d) => { if (ok) setRows(normalize(d)); })
      // A missing/unreachable endpoint is a genuine load failure, not "nobody
      // certified yet" — surface it as an error with a retry, not a fake empty.
      .catch((e) => { if (ok) setErr(e); })
      .finally(() => { if (ok) setLoading(false); });
    return () => { ok = false; };
  }, []);

  useEffect(() => load(), [load]);

  const list = rows ?? [];

  return (
    <div className="lp">
      <SiteNav />

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

        <PageData
          loading={loading}
          error={err}
          empty={list.length === 0}
          onRetry={load}
          errorTitle="Couldn't load the directory"
          skeleton={
            <div className="cert-dir-skel" aria-busy="true" aria-label="Loading certified agents">
              <Skeleton rows={6} />
            </div>
          }
          emptyState={
            <EmptyState
              title="No certified agents yet"
              hint="Be the first. Run your agent through the safety suites and publish a grade the world can verify."
              action={<Link className="btn-primary" to="/signup">Get your agent certified</Link>}
            />
          }
        >
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
                      <span className={`cdc-status ${sv.tone}`}><StatusIcon tone={sv.tone} size={13} /> {sv.label}</span>
                    </div>
                  </div>
                  <span className="cdc-go" aria-hidden="true"><IconArrowRight size={16} /></span>
                </Link>
              );
            })}
          </div>
        </PageData>

          <figure className="cd-mark">
            <Seal size={168} />
            <figcaption>
              <b>Agenttic Safety Certification</b>
              <span>
                Issued against one configuration hash, with the scope it was
                measured under signed alongside the grade — so the narrowing
                cannot be dropped without breaking the signature.
              </span>
            </figcaption>
          </figure>

      {/* ---- THE REGISTER ---- */}
      <section id="register">
        <div className="wrap">
          <SectionHeading
            eyebrow="The register"
            title="Who has qualified."
            sub="Live from the signing path — an entry appears here when a certificate is issued, and changes here the moment one is suspended." />

          {rows === undefined ? (
            <div className="cd-skel" aria-busy="true" aria-label="Reading the register">
              <Skeleton rows={4} />
            </div>
          ) : list.length === 0 ? (
            <div className="cd-empty">
              <div className="cd-empty__tag">Register · no entries</div>
              <h3>Nothing is certified yet — and that is the standard working.</h3>
              <p>
                A certificate requires closed coverage of the situation space and
                no outstanding property violation. Real suites do not clear that
                bar on the first attempt: measured across our own production data,
                mean closure sits near 20% and nothing has closed.
              </p>
              <p>
                An empty register is what a certifier that refuses looks like
                before anyone has earned one. Start by finding out how far off
                you are.
              </p>
              <div className="lp-cta">
                <Button href="/#access">Book a coverage audit</Button>
                <Button variant="ghost" href="/scan">Run a training drill</Button>
              </div>
            </div>
          ) : (
            <>
              <div className="cd-count">
                <b>{list.length}</b>
                <span>{list.length === 1 ? "entry on the register" : "entries on the register"}</span>
              </div>
              <div className="cd-grid">
                {list.map((c) => {
                  const sv = statusView(c.status);
                  return (
                    <Link key={c.id} className="cd-card" to={`/certified/${c.id}`}>
                      <Seal grade={c.grade} size={64} />
                      <div className="cd-card__body">
                        <div className="cd-card__name">{c.agent_name}</div>
                        <div className="cd-card__meta">
                          <span className="cd-card__grade" style={{ color: gradeColor(c.grade) }}>
                            Grade {c.grade}
                          </span>
                          {typeof c.index === "number" && (
                            <span className="cd-card__index">Index {c.index}</span>
                          )}
                          <span className={`cd-card__status ${sv.tone}`}><StatusIcon tone={sv.tone} size={13} /> {sv.label}</span>
                        </div>
                      </div>
                      <span className="cd-card__go" aria-hidden="true">→</span>
                    </Link>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </section>

      {/* ---- HOW AN AGENT GETS ON IT (inverted band, for rhythm) ---- */}
      <section className="lp-program cd-route" aria-labelledby="route-title">
        <div className="wrap">
          <div className="lp-program__head">
            <Eyebrow>The route onto this page</Eyebrow>
            <h2 id="route-title">Practice, then pressure, then the credential.</h2>
            <p>No agent arrives here directly. The register is the last stage of a program, and most of the work happens before it.</p>
          </div>
          <div className="lp-program__grid">
            {ROUTE.map((r) => (
              <article className={`lp-program__card${r.mod}`} key={r.n}>
                <span className="lp-program__number">{r.n}</span>
                <span className="lp-program__icon" aria-hidden="true">{r.icon}</span>
                <h3>{r.h}</h3>
                <p>{r.p}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* ---- WHAT AN ENTRY MEANS ---- */}
      <section id="says">
        <div className="wrap">
          <SectionHeading
            eyebrow="Read the entry, not just the letter"
            title="What a credential says — and what it does not."
            sub="A grade with no scope beside it is the failure mode this register exists to avoid, so the scope is signed into the certificate itself." />
          <dl className="cd-says">
            {SAYS.map((s) => (
              <div className={`cd-says__r${s.not ? " is-not" : ""}`} key={s.k}>
                <dt>{s.k}</dt>
                <dd>{s.v}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* ---- CTA ---- */}
      <section id="start">
        <div className="wrap lp-price">
          <Eyebrow>Start the program</Eyebrow>
          <div className="lp-price__big">Earn the entry.</div>
          <p>
            Every grade comes from the same literature-anchored safety suites,
            scored from the agent's own trace and signed by Agenttic. Bring one
            agent you already believe is ready.
          </p>
          <div className="lp-cta" style={{ justifyContent: "center" }}>
            <Button href="/signup">Get certified</Button>
            <Button variant="ghost" href="/methodology">Read the methodology</Button>
          </div>
        </div>
      </section>

      </main>

      <footer>
        <div className="wrap">
          <div className="lp-footer">
            <SealMark />
            <Link to="/">Home</Link>
            <Link to="/methodology">Methodology</Link>
            <Link to="/api-docs">API docs</Link>
            <span style={{ flex: 1 }} />
            <span>Agent Safety Certification</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
