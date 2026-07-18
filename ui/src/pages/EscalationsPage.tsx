import { useCallback, useEffect, useState } from "react";
import {
  api, errMessage,
  type EscalationInbox, type PendingEscalation,
} from "../api";
import { DataView, EmptyState, PageHeader, RawToggle, Skeleton } from "../components/ui";
import { PageData } from "../components/PageData";
import { IconHand, IconCheck, IconShield } from "../icons";

/* SPEC-4 Step 20.3 — the human-in-the-loop escalation inbox.

   When the autonomy policy stops an agent mid-run and asks a human, the
   question lands here: the agent's question, the trace context, and the policy
   that triggered the halt. A human answers inline; the response is persisted
   exactly as the programmatic Step-12 path records it, so a console resolution
   and a headless one leave identical state. Resolved decisions keep their
   history below. The pending count is shown prominently (the parent surfaces it
   in the top bar too). */

/** One pending escalation: the question, its trace/context, the triggering
 *  policy, and an inline response box that resolves it. */
function PendingCard({ item, onResolved }: {
  item: PendingEscalation;
  onResolved: (traceId: string) => void;
}) {
  const [response, setResponse] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<unknown | null>(null);

  const respond = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!response.trim()) return;
    setSaving(true);
    setErr(null);
    try {
      await api.respondEscalation(item.trace_id, response.trim());
      onResolved(item.trace_id);
    } catch (e2) {
      setErr(e2);
      setSaving(false);
    }
  };

  const policy = item.autonomy_policy;
  const hasPolicy = policy && (policy.tool != null || policy.policy != null || policy.tool_input != null);

  return (
    <div className="card" style={{ padding: 16, marginBottom: 12, borderLeft: "3px solid var(--wait)" }}>
      <div className="muted-sm" style={{ marginBottom: 6 }}>
        agent <span className="mono">{item.agent_id}</span>
        {" · "}trace <span className="mono">{item.trace_id}</span>
        {item.test_case_id && <> · case <span className="mono">{item.test_case_id}</span></>}
      </div>

      <h3 style={{ fontSize: 15, margin: "0 0 8px" }}>
        {item.question || "The agent paused for a human decision."}
      </h3>

      {hasPolicy && (
        <div style={{ marginBottom: 10 }}>
          <div className="muted-sm" style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <IconShield /> the policy that triggered this
          </div>
          <div className="card" style={{ padding: 10, background: "var(--panel-2)" }}>
            {policy.tool != null && (
              <div className="muted-sm">tool: <span className="mono">{String(policy.tool)}</span></div>
            )}
            {policy.policy != null && (
              <div style={{ marginTop: 4 }}>
                <div className="muted-sm">policy:</div>
                <DataView value={policy.policy} />
              </div>
            )}
            {policy.tool_input != null && (
              <div style={{ marginTop: 4 }}>
                <RawToggle value={policy.tool_input} label="tool input" />
              </div>
            )}
          </div>
        </div>
      )}

      {item.context && Object.keys(item.context).length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <RawToggle value={item.context} label="full trace context" />
        </div>
      )}

      <form onSubmit={respond}>
        <label htmlFor={`resp-${item.trace_id}`} className="muted-sm">Your decision</label>
        <textarea id={`resp-${item.trace_id}`} value={response}
          onChange={(e) => setResponse(e.target.value)}
          rows={3} placeholder="Answer the agent's question — e.g. approve, deny, or the correct value…"
          style={{ width: "100%", marginTop: 4 }} />
        {err != null && (
          <div className="pagedata-error-msg" role="alert" style={{ marginTop: 6 }}>
            {errMessage(err)}
          </div>
        )}
        <div style={{ marginTop: 8 }}>
          <button type="submit" className="btn-primary" disabled={saving || !response.trim()}>
            {saving ? "Resolving…" : "Respond & resolve"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function EscalationsPage() {
  const [data, setData] = useState<EscalationInbox | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    api.escalations()
      .then(setData)
      .catch((e) => setErr(e))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => load(), [load]);

  const pending = data?.pending ?? [];
  const resolved = data?.resolved ?? [];
  const pendingCount = data?.pending_count ?? pending.length;

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader
          title="Escalations"
          subtitle="Where the agent asks for a human. When the autonomy policy halts a run, the question waits here with its context and the policy that stopped it. Answer inline — your decision is saved exactly as a headless resolution would be."
          actions={
            <span className="status-chip" style={{
              color: pendingCount > 0 ? "var(--wait)" : "var(--ok)",
              fontSize: 13, padding: "6px 12px",
            }} role="status" aria-live="polite">
              {pendingCount > 0
                ? `${pendingCount} awaiting a decision`
                : "nothing pending"}
            </span>
          } />

        <PageData
          loading={loading}
          error={err}
          empty={data != null && pending.length === 0 && resolved.length === 0}
          onRetry={load}
          errorTitle="Couldn't load escalations"
          skeleton={<Skeleton rows={5} />}
          emptyState={
            <EmptyState icon={<IconHand />} title="No escalations"
              hint="When an agent hits a policy boundary and asks for a human, the request will appear here." />
          }
        >
          <>
            <section>
              <h2 style={{ fontSize: 16, margin: "8px 0 10px" }}>Pending</h2>
              {pending.length === 0 ? (
                <EmptyState icon={<IconCheck />} title="Inbox zero"
                  hint="No escalations are waiting on a human right now." />
              ) : (
                pending.map((item) => (
                  <PendingCard key={item.trace_id} item={item} onResolved={load} />
                ))
              )}
            </section>

            {resolved.length > 0 && (
              <section style={{ marginTop: 24 }}>
                <h2 style={{ fontSize: 16, margin: "8px 0 10px" }}>Resolved</h2>
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr><th>agent</th><th>trace</th><th>decision</th><th>when</th></tr>
                    </thead>
                    <tbody>
                      {[...resolved].reverse().map((r) => (
                        <tr key={r.feedback_id}>
                          <td className="mono">{r.agent_id}</td>
                          <td className="mono muted-sm">{r.trace_id}</td>
                          <td>{r.response}</td>
                          <td className="muted-sm">{new Date(r.created_at).toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        </PageData>
      </div>
    </div>
  );
}
