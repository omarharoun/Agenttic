import { useEffect, useRef } from "react";
import { useFlowStore } from "./store";

/** Ask for browser-notification permission (call on a user gesture, e.g. Run). */
export function ensureNotifyPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    try { Notification.requestPermission(); } catch { /* ignore */ }
  }
}

function fire(title: string, body: string) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(title, { body, tag: "agenttic-run" }); } catch { /* ignore */ }
}

/** Fire a browser notification on key run transitions — works while the user
 *  is on any in-app page (this lives in the app shell, above the router), so a
 *  run that finishes or needs approval reaches them even after they navigate
 *  away. The server owns the run; this only watches its status. */
export function useRunNotifications() {
  const status = useFlowStore((s) => s.exec.status);
  const execId = useFlowStore((s) => s.exec.executionId);
  const prev = useRef<string | null>(null);

  useEffect(() => {
    if (!execId) { prev.current = null; return; }
    if (prev.current !== null && status !== prev.current) {
      if (status === "waiting_approval")
        fire("Approval needed", "A safety test is paused for your review.");
      else if (status === "succeeded")
        fire("Run finished", "Your safety test completed — open it to see the scorecard.");
      else if (status === "failed")
        fire("Run failed", "A safety test ended with an error.");
      else if (status === "completed_with_errors")
        fire("Run finished with errors", "Some steps reported errors — open it to review.");
    }
    prev.current = status;
  }, [status, execId]);
}
