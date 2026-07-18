import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  api,
  type AgentLineage, type AgentLineageNode, type JudgeLineage,
} from "../api";
import { EmptyState, PageHeader, RawToggle, Skeleton, Uncertainty } from "../components/ui";
import { PageData } from "../components/PageData";
import { GateReplay } from "../components/GateReplay";
import { IconOptimize, IconShield } from "../icons";

/* SPEC-4 Step 20.1 — the agent-config family tree.

   The promotion ledger is a SEARCH HISTORY, not a winners' list: every
   candidate the optimizer tried is a node — promoted, rejected, or awaiting
   approval. Rejected candidates render greyed WITH their reason; the audit
   trail is the feature. Clicking any node reveals its FULL gate receipt
   verbatim (the promote/reject reason with per-criterion deltas, epsilon, and
   cost/latency verdicts, exactly as recorded).

   The seeded "support-triage-agent" is the default so the page lands populated. */

const DEFAULT_AGENT = "support-triage-agent";

const STATUS_LABEL: Record<string, string> = {
  promoted: "promoted",
  rejected: "rejected",
  pending_approval: "awaiting approval",
};
const STATUS_COLOR: Record<string, string> = {
  promoted: "var(--ok)",
  rejected: "var(--fail)",
  pending_approval: "var(--wait)",
};

/** A short, stable label for a config hash. */
function shortHash(h: string): string {
  return h.length > 10 ? h.slice(0, 10) : h;
}

// ---------------------------------------------------------------------------
// Layout — a simple top-down tree. Each node is placed at a depth (distance
// from a root) and an in-order column, so parents sit above their children and
// siblings sit side by side. No layout dependency: the ledger is small.
// ---------------------------------------------------------------------------
interface Placed { node: AgentLineageNode; col: number; depth: number; }

function layout(nodes: AgentLineageNode[]): { placed: Placed[]; cols: number; depth: number } {
  const byHash = new Map(nodes.map((n) => [n.hash, n]));
  const childrenOf = new Map<string | null, AgentLineageNode[]>();
  for (const n of nodes) {
    const key = n.parent_hash && byHash.has(n.parent_hash) ? n.parent_hash : null;
    const list = childrenOf.get(key) ?? [];
    list.push(n);
    childrenOf.set(key, list);
  }
  const placed: Placed[] = [];
  let nextCol = 0;
  const walk = (parent: string | null, depth: number) => {
    for (const n of childrenOf.get(parent) ?? []) {
      const col = nextCol++;
      placed.push({ node: n, col, depth });
      walk(n.hash, depth + 1);
    }
  };
  walk(null, 0);
  const cols = Math.max(1, nextCol);
  const maxDepth = placed.reduce((m, p) => Math.max(m, p.depth), 0);
  return { placed, cols, depth: maxDepth };
}

/** The visual DAG: nodes positioned by the tree layout, edges drawn as SVG
 *  connectors behind them. Selecting a node lifts the receipt panel. */
function LineageGraph({ lineage, selected, onSelect }: {
  lineage: AgentLineage;
  selected: string | null;
  onSelect: (hash: string) => void;
}) {
  const { placed, cols, depth } = useMemo(() => layout(lineage.nodes), [lineage.nodes]);
  const COL_W = 190, ROW_H = 116, PAD = 24, NODE_W = 168, NODE_H = 74;
  const width = cols * COL_W + PAD * 2;
  const height = (depth + 1) * ROW_H + PAD * 2;
  const pos = new Map(placed.map((p) => [p.node.hash, {
    x: PAD + p.col * COL_W + COL_W / 2,
    y: PAD + p.depth * ROW_H + NODE_H / 2,
  }]));

  return (
    <div className="lineage-graph" style={{ overflowX: "auto" }}>
      <div style={{ position: "relative", width, height, minWidth: "100%" }}>
        {/* edges — parent (bottom) to child (top) */}
        <svg width={width} height={height} aria-hidden="true"
             style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
          {lineage.edges.map((e, i) => {
            const a = pos.get(e.from), b = pos.get(e.to);
            if (!a || !b) return null;
            const y1 = a.y + NODE_H / 2, y2 = b.y - NODE_H / 2;
            const my = (y1 + y2) / 2;
            return (
              <path key={i}
                d={`M ${a.x} ${y1} C ${a.x} ${my}, ${b.x} ${my}, ${b.x} ${y2}`}
                fill="none" stroke="var(--border-strong)" strokeWidth={1.5} />
            );
          })}
        </svg>
        {/* nodes */}
        {placed.map(({ node }) => {
          const p = pos.get(node.hash)!;
          const isRejected = node.status === "rejected";
          const isSel = selected === node.hash;
          return (
            <button key={node.hash} type="button"
              className={`lineage-node${isRejected ? " is-rejected" : ""}${isSel ? " is-selected" : ""}`}
              aria-pressed={isSel}
              onClick={() => onSelect(node.hash)}
              title={isRejected ? `Rejected — ${node.gate_receipt.reason || "no reason recorded"}` : undefined}
              style={{
                position: "absolute",
                left: p.x - NODE_W / 2, top: p.y - NODE_H / 2,
                width: NODE_W, minHeight: NODE_H,
                textAlign: "left", cursor: "pointer",
                border: `1px solid ${isSel ? "var(--accent)" : "var(--border)"}`,
                borderLeft: `3px solid ${STATUS_COLOR[node.status] ?? "var(--border)"}`,
                borderRadius: "var(--card-radius)",
                background: isRejected ? "var(--panel-2)" : "var(--card-bg)",
                opacity: isRejected ? 0.7 : 1,
                boxShadow: isSel ? "var(--card-shadow)" : "none",
                padding: "8px 10px",
              }}>
              <span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>
                {shortHash(node.hash)}
              </span>
              <span className="status-chip" style={{
                display: "block", width: "fit-content", marginTop: 4,
                color: STATUS_COLOR[node.status], fontSize: 10,
              }}>
                {STATUS_LABEL[node.status] ?? node.status}
              </span>
              {node.task_success_rate != null && (
                <span className="muted-sm" style={{ display: "block", marginTop: 3 }}>
                  {(node.task_success_rate * 100).toFixed(0)}% task success
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** The FULL gate receipt for the selected node — verbatim. Nothing summarized:
 *  the promote/reject reason, the human changelog, and the raw payload as the
 *  optimizer recorded them, so the verdict is auditable. */
function GateReceiptPanel({ node }: { node: AgentLineageNode }) {
  const { gate_receipt: gr } = node;
  return (
    <div className="card" style={{ padding: 16, marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <IconShield />
        <h2 style={{ margin: 0, fontSize: 16 }}>Gate receipt</h2>
        <span className="status-chip" style={{ color: STATUS_COLOR[node.status] }}>
          {STATUS_LABEL[node.status] ?? node.status}
        </span>
        <span className="mono muted-sm">{shortHash(node.hash)}</span>
      </div>
      <dl className="dv-obj" style={{ marginTop: 12 }}>
        <div className="dv-row">
          <dt className="dv-key">recorded</dt>
          <dd className="dv-val">{new Date(node.created_at).toLocaleString()}</dd>
        </div>
        {node.parent_hash && (
          <div className="dv-row">
            <dt className="dv-key">parent</dt>
            <dd className="dv-val mono">{shortHash(node.parent_hash)}</dd>
          </div>
        )}
        {node.approved_by && (
          <div className="dv-row">
            <dt className="dv-key">approved by</dt>
            <dd className="dv-val">{node.approved_by}</dd>
          </div>
        )}
        {node.task_success_rate != null && (
          <div className="dv-row">
            <dt className="dv-key">task success</dt>
            <dd className="dv-val">
              {(node.task_success_rate * 100).toFixed(1)}%
              {node.scorecard_ids.length > 0 && (
                <span className="muted-sm"> · from scorecard {node.scorecard_ids[node.scorecard_ids.length - 1]}</span>
              )}
            </dd>
          </div>
        )}
      </dl>

      <h3 style={{ fontSize: 13, margin: "14px 0 4px" }}>Verdict — verbatim</h3>
      {gr.reason ? (
        <pre className="doc" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{gr.reason}</pre>
      ) : (
        <p className="muted-sm">No verdict string was recorded for this node.</p>
      )}

      {gr.diff_summary && (
        <>
          <h3 style={{ fontSize: 13, margin: "14px 0 4px" }}>What changed</h3>
          <pre className="doc" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{gr.diff_summary}</pre>
        </>
      )}

      {gr.payload && Object.keys(gr.payload).length > 0 && (
        <div style={{ marginTop: 12 }}>
          <RawToggle value={gr.payload} label="gate payload (raw)" />
        </div>
      )}
    </div>
  );
}

/** Judge-config lineage for one criterion: v1→vN with the before/after
 *  agreement on the train and held-out splits — the evidence a judge got
 *  sharper, not just different. Shown on demand (a criterion may have none). */
function JudgeLineageSection({ criterionId }: { criterionId: string }) {
  const [data, setData] = useState<JudgeLineage | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    api.judgeLineage(criterionId)
      .then(setData)
      .catch((e) => setErr(e))
      .finally(() => setLoading(false));
  }, [criterionId]);
  useEffect(() => load(), [load]);

  const fmt = (v: number | null) => v == null ? "—" : v.toFixed(2);

  return (
    <PageData
      loading={loading}
      error={err}
      empty={data != null && data.nodes.length === 0}
      onRetry={load}
      errorTitle="Couldn't load judge lineage"
      skeleton={<Skeleton rows={3} />}
      emptyState={
        <EmptyState icon={<IconOptimize />} title={`No judge lineage for ${criterionId}`}
          hint="This criterion's judge hasn't been optimized yet — it's still on its seed configuration." />
      }
    >
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>version</th><th>status</th>
              <th>train agreement</th><th>holdout agreement</th>
              <th>held-out n</th><th>note</th>
            </tr>
          </thead>
          <tbody>
            {(data?.nodes ?? []).map((n) => {
              const r = n.round_record;
              const active = data?.active_version === n.version;
              return (
                <tr key={n.judge_config_id}>
                  <td className="mono">v{n.version}{active ? " · active" : ""}</td>
                  <td><span className="status-chip">{n.status}</span></td>
                  <td>{r ? `${fmt(r.train_before)} → ${fmt(r.train_after)}` : "—"}</td>
                  <td>{r ? `${fmt(r.holdout_before)} → ${fmt(r.holdout_after)}` : "—"}</td>
                  <td>{r ? r.n_holdout_scored : "—"}</td>
                  <td className="muted-sm">{r?.reason || n.changelog || "seed configuration"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </PageData>
  );
}

export function LineagePage() {
  const [params, setParams] = useSearchParams();
  const agentId = params.get("agent") || DEFAULT_AGENT;
  const [agentInput, setAgentInput] = useState(agentId);
  const [criterionInput, setCriterionInput] = useState("");
  const [showJudge, setShowJudge] = useState(false);

  const [data, setData] = useState<AgentLineage | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<unknown | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setErr(null);
    setSelected(null);
    api.agentLineage(agentId)
      .then((d) => {
        setData(d);
        // default the receipt to the promoted head, else the newest node.
        const head = [...d.nodes].reverse().find((n) => n.status === "promoted")
          ?? d.nodes[d.nodes.length - 1];
        setSelected(head?.hash ?? null);
      })
      .catch((e) => setErr(e))
      .finally(() => setLoading(false));
  }, [agentId]);
  useEffect(() => load(), [load]);

  const selectedNode = useMemo(
    () => data?.nodes.find((n) => n.hash === selected) ?? null, [data, selected]);

  const counts = useMemo(() => {
    const c = { promoted: 0, rejected: 0, pending_approval: 0 };
    for (const n of data?.nodes ?? []) c[n.status] = (c[n.status] ?? 0) + 1;
    return c;
  }, [data]);

  const applyAgent = (e: React.FormEvent) => {
    e.preventDefault();
    setParams(agentInput ? { agent: agentInput } : {});
  };

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader
          title="Lineage"
          subtitle="Every configuration your agent has ever tried — the promotion ledger as a family tree. Promoted, rejected, and awaiting-approval candidates all appear; click any node to read the exact gate receipt that decided it. The audit trail is the point."
          actions={
            <form onSubmit={applyAgent} style={{ display: "flex", gap: 8 }}>
              <label htmlFor="lineage-agent" className="sr-only">Agent id</label>
              <input id="lineage-agent" value={agentInput}
                onChange={(e) => setAgentInput(e.target.value)}
                placeholder="agent id" style={{ minWidth: 200 }} />
              <button type="submit" className="btn-ghost">View</button>
            </form>
          } />

        <PageData
          loading={loading}
          error={err}
          empty={data != null && data.nodes.length === 0}
          onRetry={load}
          errorTitle="Couldn't load this agent's lineage"
          skeleton={<Skeleton rows={6} />}
          emptyState={
            <EmptyState icon={<IconOptimize />} title={`No lineage for ${agentId} yet`}
              hint="This agent hasn't been optimized, so there's no promotion ledger to show. Run the optimizer, or view the seeded support-triage-agent."
              action={
                <button type="button" className="btn-primary"
                  onClick={() => { setAgentInput(DEFAULT_AGENT); setParams({ agent: DEFAULT_AGENT }); }}>
                  View support-triage-agent
                </button>
              } />
          }
        >
          <>
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", margin: "4px 0 12px" }}>
              <span className="muted-sm">
                <b style={{ color: "var(--ok)" }}>{counts.promoted}</b> promoted
              </span>
              <span className="muted-sm">
                <b style={{ color: "var(--fail)" }}>{counts.rejected}</b> rejected
              </span>
              <span className="muted-sm">
                <b style={{ color: "var(--wait)" }}>{counts.pending_approval}</b> awaiting approval
              </span>
              <span className="muted-sm">
                {data?.nodes.length ?? 0} configurations explored
                <Uncertainty n={data?.nodes.length} passes={counts.promoted} />
              </span>
            </div>

            {data && (
              <LineageGraph lineage={data} selected={selected} onSelect={setSelected} />
            )}

            {selectedNode
              ? (
                <>
                  <GateReceiptPanel node={selectedNode} />
                  {/* SPEC-5 23.3: re-derive the gate stepwise from the stored
                      scorecards through sim-core — a replay, not a recording. */}
                  <GateReplay
                    node={selectedNode}
                    parent={data?.nodes.find((n) => n.hash === selectedNode.parent_hash) ?? null}
                  />
                </>
              )
              : <p className="muted-sm">Select a node above to read its gate receipt.</p>}

            <div style={{ marginTop: 22 }}>
              <button type="button" className="btn-ghost"
                aria-expanded={showJudge}
                onClick={() => setShowJudge((s) => !s)}>
                {showJudge ? "Hide" : "Show"} judge lineage
              </button>
              {showJudge && (
                <div className="card" style={{ padding: 16, marginTop: 8 }}>
                  <p className="muted-sm" style={{ marginTop: 0 }}>
                    A judge earns trust the same way an agent does: by version, against a
                    held-out split. Enter a criterion to see its judge's history.
                  </p>
                  <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                    <label htmlFor="judge-criterion" className="sr-only">Criterion id</label>
                    <input id="judge-criterion" value={criterionInput}
                      onChange={(e) => setCriterionInput(e.target.value)}
                      placeholder="criterion id" style={{ minWidth: 220 }} />
                  </div>
                  {criterionInput.trim()
                    ? <JudgeLineageSection criterionId={criterionInput.trim()} />
                    : <p className="muted-sm">Enter a criterion id to load its judge lineage.</p>}
                </div>
              )}
            </div>
          </>
        </PageData>
      </div>
    </div>
  );
}
