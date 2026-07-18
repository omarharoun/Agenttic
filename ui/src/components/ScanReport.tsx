/* ============================================================================
   The Safety Scan Report — the per-probe findings document behind a completed
   scan. A dark engraved document in the instrument language (certdoc's
   sibling): serif headline, mono probe ids, one card per probe with the
   agent's ACTUAL answer quoted back (plain text — React escapes it; we never
   render agent output as live HTML).

   Honesty: everything shown is read from the persisted scorecard + traces.
   When the evidence isn't there, we say so — never a fabricated finding.
   ========================================================================== */

import { useEffect, useState } from "react";
import { api, type ScanFinding, type ScanFindingsDoc } from "../api";
import { IconArrowRight, StatusIcon } from "../icons";

const VERDICT: Record<ScanFinding["verdict"], { label: string; tone: string; mark: "fail" | "ok" | null }> = {
  gap: { label: "GAP FOUND", tone: "gap", mark: "fail" },
  refused: { label: "REFUSED", tone: "ok", mark: "ok" },
  passed: { label: "PASSED", tone: "ok", mark: "ok" },
  error: { label: "— NOT SCORED", tone: "mute", mark: null },
};

/** "safety-battery-v1-refusal-drop-db" → "safety-battery-v1 / refusal-drop-db" */
function probeRef(f: ScanFinding, suiteId?: string): string {
  const suite = suiteId || "";
  const tid = suite && f.test_id.startsWith(suite + "-")
    ? f.test_id.slice(suite.length + 1) : f.test_id;
  return suite ? `${suite}/${tid}` : f.test_id;
}

function FindingCard({ f, suiteId }: { f: ScanFinding; suiteId?: string }) {
  const v = VERDICT[f.verdict] ?? VERDICT.error;
  return (
    <article className={`scanreport-card ${v.tone}`}>
      <header className="scanreport-card-head">
        <div className="scanreport-card-id">
          <span className="scanreport-probe">probe {probeRef(f, suiteId)}</span>
          <span className="scanreport-cat">{f.category}</span>
        </div>
        <span className={`scanreport-verdict ${v.tone}`}>
          {v.mark && <StatusIcon tone={v.mark} size={13} />} {v.label}
        </span>
      </header>

      <p className="scanreport-desc">
        {f.description}
        {f.probe_input && <> — we sent: <i>“{f.probe_input}”</i></>}
      </p>
      {f.injected_content && (
        <p className="scanreport-injected">
          <span className="scanreport-injected-cap">Untrusted content in the input</span>
          “{f.injected_content}”
        </p>
      )}

      <div className="scanreport-quote">
        <span className="scanreport-quote-cap">Your agent did</span>
        {f.agent_output
          ? <blockquote>{f.agent_output}</blockquote>
          : <blockquote className="empty">(no response was recorded for this probe)</blockquote>}
        {f.tool_calls.length > 0 && (
          <ul className="scanreport-tools">
            {f.tool_calls.map((t, i) => (
              <li key={i}><code>{t.name}</code>{t.input && <span>({t.input})</span>}</li>
            ))}
          </ul>
        )}
      </div>

      {f.detail && <p className={`scanreport-detail ${v.tone}`}>{f.detail}</p>}

      <footer className="scanreport-card-foot">
        Source: {f.source} · Scored by {f.scoring}
      </footer>
    </article>
  );
}

function ReportBody({ doc }: { doc: ScanFindingsDoc }) {
  if (!doc.available) {
    return (
      <div className="scanreport-empty">
        {doc.note || "No per-probe findings are available for this scan."}
      </div>
    );
  }
  const n = doc.n_probes ?? doc.findings.length;
  const gaps = doc.n_gaps ?? 0;
  const errored = doc.n_errored ?? 0;
  const runNo = (doc.scan_id || "").replace(/^scan_/, "").slice(0, 6).toUpperCase();
  return (
    <div className="scanreport" role="document">
      <div className="scanreport-eyebrow">SAFETY SCAN · RUN {runNo}</div>
      <h2 className="scanreport-h1">{n} probes run.</h2>
      <div className={`scanreport-accent ${gaps > 0 ? "gap" : "ok"}`}>
        {gaps > 0 ? `${gaps} found gap${gaps === 1 ? "" : "s"}.` : "All passed."}
      </div>
      {errored > 0 && (
        <p className="scanreport-errnote">
          {errored} probe{errored === 1 ? "" : "s"} could not be scored — those are
          shown below as “not scored”, never counted as passes.
        </p>
      )}

      <div className="scanreport-chips">
        <span className="scanreport-chip">{doc.agent_name || doc.agent_id || "your agent"}</span>
        {doc.agent_config_hash && (
          <span className="scanreport-chip mono" title="Configuration fingerprint of the exact agent version tested">
            config {doc.agent_config_hash.slice(0, 12)}
          </span>
        )}
        {doc.visibility && (
          <span className="scanreport-chip mono">{doc.visibility.replace("_", "-")}</span>
        )}
      </div>

      <div className="scanreport-cards">
        {doc.findings.map((f) => (
          <FindingCard key={f.test_id} f={f} suiteId={doc.suite_id} />
        ))}
      </div>
    </div>
  );
}

/** "Review every finding" — the expander that keeps the grade seal the hero.
    Fetches lazily on first open (authed or public-demo route per the flow). */
export function ScanReport({ scanId, isDemo = false }: { scanId: string; isDemo?: boolean }) {
  const [open, setOpen] = useState(false);
  const [doc, setDoc] = useState<ScanFindingsDoc | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open || doc || loading) return;
    setLoading(true); setErr("");
    (isDemo ? api.publicDemoFindings(scanId) : api.scanFindings(scanId))
      .then(setDoc)
      .catch((e) => setErr(String(e?.detail ?? e?.message ?? "Couldn't load the findings.")))
      .finally(() => setLoading(false));
  }, [open, doc, loading, isDemo, scanId]);

  return (
    <div className="scanreport-wrap">
      <button type="button" className="scan-link scanreport-toggle"
              aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? "− Hide the findings" : <>Review every finding <IconArrowRight size={13} /></>}
      </button>
      {open && loading && <div className="scanreport-empty">Loading the findings…</div>}
      {open && err && <div className="scanreport-empty">{err}</div>}
      {open && doc && <ReportBody doc={doc} />}
    </div>
  );
}
