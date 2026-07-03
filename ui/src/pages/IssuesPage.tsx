import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { IssuesReport } from "../components/IssuesReport";
import { EmptyState, PageHeader, Skeleton } from "../components/ui";

/** A run that produced results, worth surfacing issues for. */
interface Run { execution_id: string; workflow_id: string; status: string; started_at: string; }

const HAS_RESULTS = new Set(["succeeded", "completed_with_errors", "failed"]);

/** Issues — the middle verb of Score → Issues → Fix. Pick a run (or land here
 *  with ?execution=), and see its real failures ranked worst-first with the
 *  evidence and the Fix that addresses each. */
export function IssuesPage() {
  const [params, setParams] = useSearchParams();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const selected = params.get("execution");

  useEffect(() => {
    api.listExecutions()
      .then((r) => {
        const scored = (r as Run[]).filter((x) => HAS_RESULTS.has(x.status));
        setRuns(scored);
        // default to the most recent scored run if none chosen
        if (!params.get("execution") && scored.length) {
          setParams({ execution: scored[0].execution_id }, { replace: true });
        }
      })
      .catch(() => setRuns([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const current = useMemo(
    () => runs?.find((r) => r.execution_id === selected) ?? null, [runs, selected]);

  return (
    <div className="page">
      <div className="list-page">
        <PageHeader
          title="Issues"
          subtitle="What's wrong with your agent — its real failures, ranked worst-first, with the evidence and the fix for each. Nothing here is invented: every issue is a computed failure from a run you scored." />

        {runs === null ? (
          <Skeleton rows={5} />
        ) : runs.length === 0 ? (
          <EmptyState icon="🔎" title="No scored runs yet"
                      hint="Score an agent first (New evaluation → Run). Once a run finishes, its issues appear here." />
        ) : (
          <>
            <div className="issues-runpick">
              <label htmlFor="run-select">Run</label>
              <select id="run-select" value={selected ?? ""}
                      onChange={(e) => setParams({ execution: e.target.value })}>
                {runs.map((r) => (
                  <option key={r.execution_id} value={r.execution_id}>
                    {r.execution_id} · {r.workflow_id} · {new Date(r.started_at).toLocaleString()}
                  </option>
                ))}
              </select>
              {current && <span className={`status-chip ${current.status}`}>
                {current.status.replace(/_/g, " ")}</span>}
            </div>
            {selected && <IssuesReport key={selected} executionId={selected} />}
          </>
        )}
      </div>
    </div>
  );
}
