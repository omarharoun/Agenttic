import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { certIdOf, embedSnippets, gradeColor, isValidCertId, statusView } from "../cert";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";
import { Seal } from "../components/Seal";

/* ============================================================================
   In-app Certifications — /app/certifications.

   Issue a certificate from a completed safety scorecard, then publish it: each
   cert shows copy-paste embed snippets (Markdown + HTML badge, shareable link)
   that point at the public badge SVG and the /certified/{id} verification page.

   Reachable from the app nav and from a "Certify this agent" action on the
   Results page (which deep-links here with ?scorecard=…).
   ========================================================================== */

interface Scorecard {
  scorecard_id: string;
  agent_id: string;
  suite_id: string;
  task_success_rate: number | null;
  created_at: string;
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(value).then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 1400); },
      () => { /* clipboard blocked — the field is selectable as a fallback */ },
    );
  };
  return (
    <div className="embed-row">
      <label>{label}</label>
      <div className="embed-field">
        <code onClick={(e) => {
          const r = document.createRange();
          r.selectNodeContents(e.currentTarget);
          const sel = window.getSelection();
          sel?.removeAllRanges(); sel?.addRange(r);
        }}>{value}</code>
        <button className="ghost-sm" onClick={copy}>{copied ? "Copied ✓" : "Copy"}</button>
      </div>
    </div>
  );
}

/** The publish panel for one issued certificate: live badge preview + the three
 *  copy-paste embed snippets. */
function CertEmbed({ cert, id }: { cert: any; id: string }) {
  const snip = embedSnippets(id, cert.agent_name ?? cert.agent_id ?? "agent");
  return (
    <div className="embed-block">
      <div className="embed-preview">
        <Seal grade={cert.grade} size={84} />
        <div>
          <div className="embed-grade" style={{ color: gradeColor(cert.grade ?? "") }}>
            Grade {cert.grade ?? "—"}
          </div>
          <a className="embed-link" href={snip.link} target="_blank" rel="noreferrer">
            View public certificate ↗
          </a>
        </div>
      </div>
      <p className="muted-sm" style={{ margin: "4px 0 10px" }}>
        Put this on your site or README — the badge links to the public,
        verifiable certificate.
      </p>
      <CopyRow label="Markdown badge" value={snip.markdown} />
      <CopyRow label="HTML badge" value={snip.html} />
      <CopyRow label="Shareable link" value={snip.link} />
    </div>
  );
}

export function CertificationsPage() {
  const [params, setParams] = useSearchParams();
  const [certs, setCerts] = useState<any[] | null>(null);
  const [scorecards, setScorecards] = useState<Scorecard[] | null>(null);
  const [picked, setPicked] = useState<string>(params.get("scorecard") ?? "");
  const [agentName, setAgentName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [justIssued, setJustIssued] = useState<string | null>(null);

  const load = () => {
    api.listCertifications()
      .then((d) => setCerts(Array.isArray(d) ? d : (d?.certifications ?? [])))
      .catch(() => setCerts([]));
    api.listScorecards()
      .then((r) => setScorecards(r as Scorecard[]))
      .catch(() => setScorecards([]));
  };
  useEffect(load, []);

  // pre-fill the agent name from the picked scorecard
  const pickedCard = useMemo(
    () => (scorecards ?? []).find((s) => s.scorecard_id === picked),
    [scorecards, picked]);
  useEffect(() => {
    if (pickedCard && !agentName) setAgentName(pickedCard.agent_id);
  }, [pickedCard]); // eslint-disable-line react-hooks/exhaustive-deps

  const issue = async () => {
    if (!picked) return;
    setBusy(true); setMsg(null);
    try {
      const c = await api.issueCertification({
        scorecard_id: picked,
        agent_name: agentName.trim() || undefined,
      });
      setMsg({ kind: "ok", text: `Certificate issued — grade ${c.grade ?? "?"}.` });
      setJustIssued(certIdOf(c) || null);
      setPicked(""); setAgentName("");
      setParams({}, { replace: true });
      load();
    } catch (e: any) {
      setMsg({ kind: "err",
        text: `Could not issue certificate: ${String(e?.message ?? e)}. ` +
          "Run a safety scorecard first, or check that certification is enabled." });
    } finally { setBusy(false); }
  };

  const revoke = async (id: string) => {
    if (!confirm("Revoke this certificate? The public page will show it as revoked.")) return;
    try { await api.revokeCertification(id); load(); }
    catch (e: any) { setMsg({ kind: "err", text: `Revoke failed: ${String(e?.message ?? e)}` }); }
  };

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader
          title="Certification"
          subtitle={<>Turn a completed safety scorecard into a signed, publishable
            <b> Agent Safety Certificate</b> — then embed the badge on your site or
            README. Grades are pinned to the tested agent version. See the{" "}
            <Link to="/certified">public directory</Link> and{" "}
            <Link to="/methodology">methodology</Link>.</>}
        />

        {/* issue panel */}
        <div className="card" style={{ marginBottom: 22 }}>
          <div className="card-head"><h2>Certify an agent</h2>
            <p>Pick a safety scorecard to certify. The grade is computed from that
              run and pinned to its agent version.</p>
          </div>
          <div className="card-body">
            {scorecards === null ? <Skeleton rows={2} /> : scorecards.length === 0 ? (
              <EmptyState icon="📊" title="No scorecards to certify yet"
                hint={<>Run a safety scorecard first — from the guided flow or the{" "}
                  <Link to="/app/leaderboard">standard benchmarks</Link>.</>} />
            ) : (
              <div className="cert-issue">
                <div className="ci-field">
                  <label>Scorecard</label>
                  <select value={picked} onChange={(e) => setPicked(e.target.value)}>
                    <option value="">Select a scorecard…</option>
                    {scorecards.map((s) => (
                      <option key={s.scorecard_id} value={s.scorecard_id}>
                        {s.agent_id} · {s.suite_id} · {Math.round((s.task_success_rate ?? 0) * 100)}% · {s.scorecard_id}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="ci-field">
                  <label>Agent name <small>shown on the certificate</small></label>
                  <input value={agentName} placeholder="e.g. Acme Support Agent v2"
                         onChange={(e) => setAgentName(e.target.value)} />
                </div>
                <button className="primary" disabled={!picked || busy} onClick={issue}>
                  {busy ? "Issuing…" : "🏅 Certify this agent"}
                </button>
              </div>
            )}
            {msg && <div className={msg.kind === "ok" ? "note-ok" : "note-err"}
                         style={{ marginTop: 12 }}>{msg.text}</div>}
          </div>
        </div>

        {/* existing certs */}
        {certs === null ? <Skeleton rows={3} /> : certs.length === 0 ? (
          <EmptyState icon="🏅" title="No certificates issued yet"
            hint="Certify a scorecard above to mint your first certificate and get its embed badge." />
        ) : (
          <div className="cert-list">
            {certs.map((c, i) => {
              const id = certIdOf(c);
              const hasId = isValidCertId(id);
              const sv = statusView(c.status ?? "valid");
              const open = hasId && justIssued === id;
              return (
                <div className={`cert-item${open ? " open" : ""}`} key={id || `cert-${i}`}>
                  <div className="cert-item-head">
                    <Seal grade={c.grade} size={56} />
                    <div className="cert-item-id">
                      <div className="cert-item-name">{c.agent_name ?? c.agent_id ?? "agent"}</div>
                      <div className="muted-sm">
                        <span className="mono">{id || "—"}</span> · grade{" "}
                        <b style={{ color: gradeColor(c.grade ?? "") }}>{c.grade ?? "—"}</b>{" "}
                        · <span className={`cert-inline-status ${sv.tone}`}>{sv.icon} {sv.label}</span>
                      </div>
                    </div>
                    <span style={{ flex: 1 }} />
                    {hasId && (
                      <a className="ghost-sm" href={`/certified/${id}`} target="_blank"
                         rel="noreferrer" style={{ marginRight: 6 }}>Public page ↗</a>
                    )}
                    {hasId && (c.status ?? "valid") !== "revoked" && (
                      <button className="ghost-sm" onClick={() => revoke(id)}>Revoke</button>
                    )}
                  </div>
                  {hasId ? <CertEmbed cert={c} id={id} /> : (
                    <p className="note-err" style={{ margin: "0 16px 16px" }}>
                      This certificate is missing its id, so a verifiable badge can't
                      be generated. Re-issue it, or contact support if it persists.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
