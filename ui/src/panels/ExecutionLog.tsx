import { useEffect, useRef } from "react";
import { useFlowStore } from "../store";

export function ExecutionLog() {
  const log = useFlowStore((s) => s.exec.log);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight });
  }, [log.length]);

  return (
    <div className="exec-log" ref={ref}>
      {log.map((l) => (
        <div className="row" key={l.seq}>
          <span className="node">{l.nodeId ?? "—"}</span>
          <span className={l.type === "node_failed" || l.type === "execution_failed"
            ? "err" : l.type.endsWith("succeeded") ? "ok" : ""}>
            {l.text}
          </span>
        </div>
      ))}
    </div>
  );
}
