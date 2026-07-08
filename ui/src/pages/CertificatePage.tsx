import { useEffect, useState } from "react";
import { HexMark } from "../components/Icons";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import {
  bandForIndex, type Certification, type CertScore, dimensionLabel,
  gradeColor, statusView,
} from "../cert";
import { Seal, SealMark } from "../components/Seal";
import { Gauge } from "../components/Gauge";
import { Skeleton } from "../components/ui";

/* ============================================================================
   Public certificate verification page — /certified/:id (unauthenticated).

   This is the brand's credibility surface: the page the badge links to. It must
   read as trustworthy — a prominent grade + seal, the per-dimension safety
   breakdown, issue/expiry, an unambiguous status (✓ Valid / ⚠ Expired / ⛔
   Revoked), a "signature verified" trust line, the methodology link, and a note
   that the grade is pinned to a specific agent version (config_hash).

   Degrades gracefully: a not-found / unreachable cert shows an honest empty
   state rather than a blank or a fake pass.
   ========================================================================== */

function normalizeScores(raw: any): CertScore[] {
  const s = raw?.scores;
  if (Array.isArray(s)) {
    return s.map((x: any) => ({
      key: x.key ?? x.id ?? "",
      label: x.label ?? dimensionLabel(x.key ?? x.id ?? ""),
      value: typeof x.value === "number" ? x.value
        : typeof x.score === "number" ? x.score : null,
    }));
  }
  // object form: { injection_robustness: 0.9, ... }
  if (s && typeof s === "object") {
    return Object.entries(s).map(([key, value]) => ({
      key, label: dimensionLabel(key),
      value: typeof value === "number" ? (value as number) : null,
    }));
  }
  return [];
}

function PublicNav() {
  return (
    <nav className="lp-nav">
      <Link to="/" className="brand"><HexMark className="hex" /> Agenttic</Link>
      <span className="spacer" />
      <Link className="navlink" to="/certified">Certified agents</Link>
      <Link className="navlink" to="/methodology">Methodology</Link>
      <Link className="btn-primary" to="/signup">Get certified</Link>
    </nav>
  );
}

function pretty(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(d);
  return Number.isNaN(t) ? String(d)
    : new Date(t).toLocaleDateString(undefined,
        { year: "numeric", month: "long", day: "numeric" });
}

function ScoreBar({ s }: { s: CertScore }) {
  const pct = s.value == null ? null : Math.round(s.value * 100);
  return (
    <div className="cert-dim">
      <div className="cert-dim-top">
        <span className="cert-dim-name">{s.label}</span>
        <span className="cert-dim-val">{pct == null ? "n/a" : `${pct}%`}</span>
      </div>
      <span className="cert-dim-track">
        <span className="cert-dim-fill"
              style={{ width: `${pct ?? 0}%`,
                       background: pct == null ? "var(--border-strong)"
                         : pct >= 70 ? "var(--ok)" : pct >= 40 ? "var(--wait)" : "var(--fail)" }} />
      </span>
    </div>
  );
}

export function CertificatePage() {
  const { id = "" } = useParams();
  const [cert, setCert] = useState<Certification | null | undefined>(undefined);

  useEffect(() => {
    let ok = true;
    api.publicCertification(id)
      .then((c) => {
        if (!ok) return;
        // The public API exposes the 0–100 figure as `composite_score`; the
        // gauge + band read `index`. Map it here so the certificate shows the
        // Agenttic Index dial whenever a numeric composite is present.
        const index = typeof c?.index === "number" ? c.index
          : typeof c?.composite_score === "number"
            ? Math.round(c.composite_score <= 1 ? c.composite_score * 100 : c.composite_score)
            : null;
        setCert({ ...c, index, scores: normalizeScores(c) });
      })
      .catch(() => { if (ok) setCert(null); });
    return () => { ok = false; };
  }, [id]);

  return (
    <>
      <PublicNav />
      <main className="cert-page">
        {cert === undefined ? (
          <div className="cert-loading-skel" aria-busy="true" aria-label="Loading certificate">
            <div className="cls-seal" />
            <Skeleton rows={5} />
          </div>
        ) : cert === null ? (
          <div className="cert-missing">
            <Seal />
            <h1>Certificate not found</h1>
            <p>
              We couldn't find a certificate with id <code>{id}</code>. It may
              have been revoked, or the link may be incorrect. Browse the{" "}
              <Link to="/certified">certified agents directory</Link> or read the{" "}
              <Link to="/methodology">methodology</Link>.
            </p>
          </div>
        ) : (
          <CertBody cert={cert} />
        )}
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <SealMark />
          <Link to="/certified">Certified agents</Link>
          <Link to="/methodology">Methodology</Link>
          <a href="/api-docs">API docs</a>
          <span style={{ flex: 1 }} />
          <span>Agent Safety Certification</span>
        </div>
      </footer>
    </>
  );
}

function CertBody({ cert }: { cert: Certification }) {
  const sv = statusView(cert.status);
  const color = gradeColor(cert.grade);
  const band = typeof cert.index === "number" ? bandForIndex(cert.index) : null;
  return (
    <>
      <div className={`cert-status-banner ${sv.tone}`}>
        <span className="csb-badge">{sv.icon} {sv.label}</span>
        <span className="csb-text">
          {cert.status === "valid"
            ? "This certification is active and verifiable."
            : cert.status === "expired"
              ? "This certification has lapsed — the agent should be re-tested and re-certified."
              : "This certification has been revoked and no longer attests to the agent's safety."}
        </span>
        <span className="csb-sig" title="Cryptographic signature on the certificate payload">
          {cert.signature_verified ? "✓ Signature verified" : "⚠ Signature unverified"}
        </span>
      </div>

      <header className="cert-hero guilloche">
        <div className="cert-hero-seal">
          <Seal grade={cert.grade} size={150} />
        </div>
        {typeof cert.index === "number" && (
          <div className="cert-hero-gauge" aria-hidden={false}>
            <Gauge value={cert.index} color={color} />
          </div>
        )}
        <div className="cert-hero-body">
          <span className="eyebrow">Agent Safety Certification</span>
          <h1>{cert.agent_name}</h1>
          <p className="cert-grade-line">
            Safety grade{" "}
            <b className="cert-grade-letter" style={{ color }}>{cert.grade}</b>
            {band && <span className="cert-band"> · {band.label}</span>}
            {typeof cert.index === "number" && (
              <span className="cert-index"> · Agenttic Index {cert.index}</span>
            )}
          </p>
          {band && <p className="cert-band-blurb">{band.blurb}</p>}
        </div>
      </header>

      <section className="cert-section">
        <span className="eyebrow">Safety breakdown</span>
        <h2>How it scored, dimension by dimension</h2>
        {cert.scores.length === 0 ? (
          <p className="muted-sm">No per-dimension breakdown was published with this certificate.</p>
        ) : (
          <div className="cert-dims">
            {cert.scores.map((s) => <ScoreBar key={s.key} s={s} />)}
          </div>
        )}
      </section>

      <section className="cert-section">
        <span className="eyebrow">Provenance</span>
        <h2>What this attests to</h2>
        <dl className="cert-meta">
          <div><dt>Status</dt><dd className={`cert-meta-status ${sv.tone}`}>{sv.icon} {sv.label}</dd></div>
          <div><dt>Issued</dt><dd>{pretty(cert.issued_at)}</dd></div>
          <div><dt>Expires</dt><dd>{cert.expires_at ? pretty(cert.expires_at) : "No expiry"}</dd></div>
          <div><dt>Methodology</dt><dd><Link to="/methodology">{cert.methodology_version || "current"} ↗</Link></dd></div>
          <div><dt>Signature</dt><dd>{cert.signature_verified ? "Verified ✓" : "Unverified ⚠"}</dd></div>
          <div className="cert-meta-wide">
            <dt>Pinned agent version</dt>
            <dd><code className="cert-hash">{cert.config_hash || "—"}</code></dd>
          </div>
        </dl>
        <p className="cert-pin-note">
          This grade is pinned to a specific agent version (<code>config_hash</code>{" "}
          above). Change the model, prompt, or tools and the certificate no longer
          applies — re-test to re-certify. The certificate payload is signed, so
          the grade and scores shown here can be verified as issued by Agenttic
          and unaltered.
        </p>
      </section>

      <section className="cert-section cert-cta">
        <h2>Certify your own agent</h2>
        <p className="lede" style={{ margin: "0 auto 22px" }}>
          Put your AI agent through the same literature-anchored safety suites and
          earn a grade you can publish.
        </p>
        <div className="cta" style={{ justifyContent: "center" }}>
          <Link className="btn-primary" to="/signup">Get certified</Link>
          <Link className="btn-ghost" to="/methodology">How grading works</Link>
        </div>
      </section>
    </>
  );
}
