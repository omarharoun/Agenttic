import { useEffect, useState } from "react";
import { SiteNav } from "../components/SiteNav";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import {
  badgeUrl, bandForIndex, type Certification, type CertScore, embedSnippets,
  gradeColor, indexFromCert, normalizeScores, siteOrigin, statusView,
} from "../cert";
import { Seal, SealMark } from "../components/Seal";
import { Skeleton } from "../components/ui";

/* ============================================================================
   Public certificate verification page — /certified/:id (unauthenticated).

   This is the brand's credibility surface: the page the badge links to, and
   the page a skeptical third party lands on. Redesigned as THE DOCUMENT
   ITSELF, not a webpage about it: one engraved certificate artifact in the
   instrument language — seal, grade, dimension readout (the same rows the
   scan filled in live), provenance, and a signature line — that screenshots
   like a credential. An expired/revoked certificate visibly lapses: the seal
   desaturates and the document carries an overprint stamp.

   Below the document, three honest blocks:
   · Verify it yourself — the public verify endpoint + published Ed25519 keys,
     so trust doesn't have to be taken from this page's own say-so.
   · Scope & coverage — what a quick scan is (~14 lexical probes), what it is
     NOT (a full canonical certification), and what was NOT ASSESSED.
   · Share — badge preview + copyable README/HTML/link embeds.

   Degrades gracefully: a not-found / unreachable cert shows an honest empty
   state rather than a blank or a fake pass.
   ========================================================================== */

function pretty(d: string | null | undefined): string {
  if (!d) return "—";
  const t = Date.parse(d);
  return Number.isNaN(t) ? String(d)
    : new Date(t).toLocaleDateString(undefined,
        { year: "numeric", month: "long", day: "numeric" });
}

/** One dimension on the document — the same row language as the scan panel,
 *  so scan → certificate reads as one instrument, printed. */
function DimRow({ s }: { s: CertScore }) {
  const pct = s.value == null ? null : Math.round(s.value * 100);
  const tone = pct == null ? "na" : pct >= 70 ? "ok" : pct >= 40 ? "warn" : "fail";
  return (
    <li className={`cd-row is-${tone}`}>
      <span className="cd-row-lab">{s.label}</span>
      <span className="cd-row-track" aria-hidden>
        <span style={{ width: `${pct ?? 0}%` }} />
      </span>
      <span className={`cd-row-val ${tone}`}>
        {pct == null ? "NOT ASSESSED" : `${pct}%`}
      </span>
    </li>
  );
}

/** Copy-to-clipboard row for the verify + share kits. */
function CopyRow({ label, value }: { label: string; value: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="cd-copy">
      <label>{label}</label>
      <div className="cd-copy-field">
        <code>{value}</code>
        <button type="button" onClick={() => {
          navigator.clipboard?.writeText(value).then(() => {
            setDone(true); setTimeout(() => setDone(false), 1400);
          });
        }}>{done ? "Copied ✓" : "Copy"}</button>
      </div>
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
        // The public API exposes the 0–100 figure as `composite_score` and the
        // per-dimension breakdown as `dimensions`; map both so the certificate
        // shows the same headline number (one decimal, matching the scan's
        // "Safety score X/100") and the same dimensions the scan scored.
        setCert({ ...c, index: indexFromCert(c), scores: normalizeScores(c) });
      })
      .catch(() => { if (ok) setCert(null); });
    return () => { ok = false; };
  }, [id]);

  return (
    <>
      <SiteNav />
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
          <CertBody cert={cert} id={id} />
        )}
      </main>

      <footer className="lp">
        <div className="lp-footer">
          <SealMark />
          <Link to="/certified">Certified agents</Link>
          <Link to="/methodology">Methodology</Link>
          <Link to="/api-docs">API docs</Link>
          <span style={{ flex: 1 }} />
          <span>Agent Safety Certification</span>
        </div>
      </footer>
    </>
  );
}

export function CertBody({ cert, id }: { cert: Certification; id: string }) {
  const sv = statusView(cert.status);
  const color = gradeColor(cert.grade);
  const band = typeof cert.index === "number" ? bandForIndex(cert.index) : null;
  const lapsed = cert.status !== "valid";
  const origin = siteOrigin();
  const snip = embedSnippets(id, cert.agent_name);
  const verifyCmd = `curl ${origin}/api/public/certifications/${id}/verify`;

  return (
    <>
      {/* status, stated plainly above the document (screen-reader friendly) */}
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

      {/* ==================== THE DOCUMENT ==================== */}
      <article className={`certdoc guilloche ${lapsed ? "lapsed" : ""}`}
               aria-label={`Safety certificate for ${cert.agent_name}: grade ${cert.grade}, status ${sv.label}`}>
        {lapsed && (
          <span className="certdoc-stamp" aria-hidden>
            {cert.status === "expired" ? "EXPIRED" : "REVOKED"}
          </span>
        )}

        <header className="certdoc-head">
          <span>AGENT SAFETY CERTIFICATION</span>
          <span className="certdoc-no">№ {id}</span>
        </header>

        <div className="certdoc-main">
          <div className="certdoc-seal"><Seal grade={cert.grade} size={140} /></div>
          <div className="certdoc-title">
            <h1>{cert.agent_name}</h1>
            <p className="certdoc-grade">
              Safety grade <b style={{ color }}>{cert.grade}</b>
              {band && <span> · {band.label}</span>}
              {typeof cert.index === "number" && <span> · Agenttic Index {cert.index}</span>}
            </p>
            {band && <p className="certdoc-blurb">{band.blurb}</p>}
          </div>
        </div>

        <ul className="cd-rows" aria-label="Safety breakdown by dimension">
          {cert.scores.length === 0
            ? <li className="cd-row is-na"><span className="cd-row-lab">No per-dimension breakdown was published with this certificate.</span></li>
            : cert.scores.map((s) => <DimRow key={s.key} s={s} />)}
        </ul>
        <p className="certdoc-scope">
          Quick scan · ~14 safety probes · lexical screen — a fast safety check,
          not a full canonical certification.
        </p>

        <dl className="certdoc-meta">
          <div><dt>Issued</dt><dd>{pretty(cert.issued_at)}</dd></div>
          <div><dt>Expires</dt><dd>{cert.expires_at ? pretty(cert.expires_at) : "No expiry"}</dd></div>
          <div><dt>Methodology</dt><dd><Link to="/methodology">{cert.methodology_version || "current"}</Link></dd></div>
          <div className="certdoc-meta-wide">
            <dt>Pinned agent version</dt>
            <dd><code>{cert.config_hash || "—"}</code></dd>
          </div>
        </dl>

        <footer className="certdoc-sig">
          <span className={cert.signature_verified ? "ok" : "warn"}>
            {cert.signature_verified ? "✓ Signature verified" : "⚠ Signature unverified"} · Ed25519
          </span>
          <span>Tested with Agenttic</span>
        </footer>
      </article>

      {/* ==================== VERIFY IT YOURSELF ==================== */}
      <section className="cert-section">
        <span className="eyebrow">Don't take this page's word for it</span>
        <h2>Verify it yourself</h2>
        <p className="cd-lede">
          The certificate payload is signed with a published Ed25519 key and
          pinned to the exact agent version shown above (<code>config_hash</code>).
          Change the model, prompt, or tools and it no longer applies. Anyone can
          check the signature independently:
        </p>
        <CopyRow label="Verify via the public API" value={verifyCmd} />
        <p className="cd-fine">
          Public signing keys: <a href={`${origin}/.well-known/agenttic-cert-keys.json`}
          target="_blank" rel="noreferrer"><code>/.well-known/agenttic-cert-keys.json</code></a>
          {" "}· signed payload included in the verify response.
        </p>
      </section>

      {/* ==================== SCOPE, HONESTLY ==================== */}
      <section className="cert-section">
        <span className="eyebrow">Scope &amp; coverage</span>
        <h2>What this does — and doesn't — attest</h2>
        <p className="cd-lede">
          This grade comes from a <b>quick scan</b>: ~14 short safety probes
          scored with lexical checks across the five dimensions printed on the
          document. It is a fast screen, not an exhaustive audit. Domains outside
          it — truthfulness, tool-call capability, reliability across runs,
          calibration — are <b>NOT ASSESSED</b> here; they are covered by the
          full certification track (seven canonical metrics, real attack
          environments, k runs per case). <Link to="/methodology">How grading
          works ↗</Link>
        </p>
      </section>

      {/* ==================== SHARE ==================== */}
      <section className="cert-section">
        <span className="eyebrow">Share</span>
        <h2>Put the badge where people decide</h2>
        <div className="cd-badge-preview">
          <img src={badgeUrl(id, origin)}
               alt={`Agenttic Safety badge — grade ${cert.grade}`} height={32} />
          <span className="cd-fine">The badge always links back to this page, so the claim stays checkable.</span>
        </div>
        <CopyRow label="README (Markdown)" value={snip.markdown} />
        <CopyRow label="Website (HTML)" value={snip.html} />
        <CopyRow label="Link" value={snip.link} />
      </section>

      {/* ==================== CTA ==================== */}
      <section className="cert-section cert-cta">
        <h2>Certify your own agent</h2>
        <p className="lede" style={{ margin: "0 auto 22px" }}>
          Four questions compose your certification profile, then the scan runs
          while you watch — a grade you can publish in minutes.
        </p>
        <div className="cta" style={{ justifyContent: "center" }}>
          <Link className="btn-primary" to="/scan">Start the intake</Link>
          <Link className="btn-ghost" to="/methodology">How grading works</Link>
        </div>
      </section>
    </>
  );
}
