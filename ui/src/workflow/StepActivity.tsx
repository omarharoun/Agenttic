import { useFlowStore } from "../store";
import type { LogEntry } from "../store";

/* What a run did, told inside the step that did it.
 *
 * This used to be one monospace log pinned to the bottom of the page — the
 * whole run in machine shorthand ("run_suite  case 3/12 ok (t-004)"), far away
 * from the step it belonged to. Every line is now a card in plain English,
 * sitting under its own step: generation under "Generate tests", each case
 * under "Run the tests", each verdict under "Score". */

/** A single readable thing that happened. */
export interface ActivityCard {
  key: number;
  lead?: string;                                  // "Case 3 of 12"
  title: string;                                  // plain-English sentence
  detail?: string;                                // test id, error text
  badge?: string;                                 // short outcome word
  tone: "ok" | "fail" | "wait" | "info" | "";
}

/** Only the most recent cards are kept — a 200-case suite would otherwise bury
 *  the step it belongs to. The count that was dropped is always stated. */
export const MAX_CARDS = 48;

const caseLead = (d: any) =>
  typeof d?.index === "number" && d?.total
    ? `Case ${d.index + 1} of ${d.total}`
    : undefined;

/** One log entry → one card, or null when it isn't worth a card of its own. */
export function describeEvent(entry: LogEntry): ActivityCard | null {
  const d = entry.data ?? {};
  const card = (c: Omit<ActivityCard, "key">): ActivityCard => ({ ...c, key: entry.seq });

  switch (entry.type) {
    case "node_started":
      // the step already turns blue and shows a progress bar — no card needed
      return null;

    case "node_progress":
      if (d.event === "case_finished")
        return card({
          lead: caseLead(d), detail: d.test_id, tone: d.ok ? "ok" : "fail",
          title: d.ok ? "The agent answered" : "The agent errored",
          badge: d.ok ? "Answered" : "Error",
        });
      if (d.event === "case_scored")
        return card({
          lead: caseLead(d), detail: d.test_id, tone: d.passed ? "ok" : "fail",
          title: d.passed ? "Met the requirement" : "Did not meet the requirement",
          badge: d.passed ? "Passed" : "Failed",
        });
      if (d.event === "case_error")
        return card({
          lead: caseLead(d), detail: d.error || d.test_id, tone: "wait",
          title: "This case couldn't be scored", badge: "Not scored",
        });
      if (d.message) return card({ title: d.message, tone: "info" });
      return null;

    case "node_waiting":
      return card({
        title: "Waiting for you to approve these tests", tone: "wait",
        detail: d.suite_id ? `${d.suite_id} · version ${d.version}` : undefined,
        badge: "Your turn",
      });

    case "node_completed":
      return card({ title: "Finished", tone: "ok", badge: "Done" });

    case "node_failed":
      return card({
        title: d.continued ? "This step failed — the run carried on"
                           : "This step failed",
        detail: d.error || undefined, tone: "fail", badge: "Failed",
      });

    case "node_retry":
      return card({
        title: `Hit an error and tried again (attempt ${d.attempt} of ${d.of})`,
        detail: d.error || undefined, tone: "wait", badge: "Retried",
      });

    case "node_skipped":
      return card({
        title: "Skipped — nothing arrived from the step before it", tone: "",
        badge: "Skipped",
      });

    default:
      return null;
  }
}

/** Cards for one step, newest last, capped — with the drop stated, never silent. */
export function cardsForNode(log: LogEntry[], nodeId: string) {
  const all = log.filter((l) => l.nodeId === nodeId)
    .map(describeEvent).filter((c): c is ActivityCard => c !== null);
  return { cards: all.slice(-MAX_CARDS), hidden: Math.max(0, all.length - MAX_CARDS) };
}

export function StepActivity({ nodeId }: { nodeId: string }) {
  const log = useFlowStore((s) => s.exec.log);
  const { cards, hidden } = cardsForNode(log, nodeId);
  if (cards.length === 0) return null;

  return (
    <div className="step-activity">
      {hidden > 0 && (
        <p className="sa-trimmed">Showing the last {cards.length} — {hidden} earlier
          {hidden === 1 ? " event is" : " events are"} not shown here.</p>
      )}
      <div className="sa-grid">
        {cards.map((c) => (
          <div className={`sa-card ${c.tone}`} key={c.key}>
            <div className="sa-top">
              {c.lead && <span className="sa-lead">{c.lead}</span>}
              {c.badge && <span className="sa-badge">{c.badge}</span>}
            </div>
            <div className="sa-title">{c.title}</div>
            {c.detail && <div className="sa-detail">{c.detail}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
