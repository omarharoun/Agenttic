import { useEffect, useRef } from "react";
import { sseUrl } from "./api";
import { useFlowStore } from "./store";

/** Subscribe to an execution's SSE stream; events feed the flow store so
 * canvas nodes animate. Reconnects resume from the last seen seq. */
export function useExecutionEvents(executionId: string | null) {
  const pushEvent = useFlowStore((s) => s.pushEvent);
  const lastSeq = useRef(0);

  useEffect(() => {
    if (!executionId) return;
    lastSeq.current = 0;
    let es: EventSource | null = null;
    let closed = false;

    const connect = () => {
      es = new EventSource(
        sseUrl(`/api/executions/${executionId}/events?after=${lastSeq.current}`),
      );
      const handler = (e: MessageEvent) => {
        const payload = JSON.parse(e.data);
        lastSeq.current = Math.max(lastSeq.current, payload.seq ?? 0);
        pushEvent({
          seq: payload.seq,
          type: (e as MessageEvent & { type: string }).type,
          node_id: payload.node_id,
          data: payload.data ?? {},
        });
      };
      for (const t of [
        "execution_started", "node_started", "node_progress", "node_waiting",
        "node_completed", "node_failed", "node_skipped", "node_retry",
        "execution_succeeded", "execution_failed", "execution_cancelled",
        "execution_completed_with_errors",
      ]) {
        es.addEventListener(t, handler);
      }
      es.addEventListener("stream_end", () => {
        closed = true;
        es?.close();
      });
      es.onerror = () => {
        es?.close();
        if (!closed) setTimeout(connect, 1000); // resume from lastSeq
      };
    };
    connect();
    return () => {
      closed = true;
      es?.close();
    };
  }, [executionId, pushEvent]);
}
