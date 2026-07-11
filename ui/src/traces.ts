/** Trace-row helpers for the Resources → Traces console.
 *
 *  The list endpoint (`GET /api/traces`) returns objects keyed by `trace_id`,
 *  but the table must never assume the field is present: a legacy/partial row,
 *  a shape that uses `id`, or a null id would otherwise crash the whole console
 *  via `trace_id.slice(...)` ("can't access property 'slice', … is undefined").
 *  These helpers tolerate every one of those cases. */

export interface TraceRow {
  trace_id?: string | null;
  id?: string | null;
  agent_id?: string | null;
  test_case_id?: string | null;
  n_spans?: number | null;
  final_output?: string | null;
}

/** The canonical id for a trace, reconciling a shape that uses `id` instead of
 *  `trace_id`. Returns "" when the trace carries no id at all. */
export function traceId(t: TraceRow | null | undefined): string {
  return t?.trace_id ?? t?.id ?? "";
}

/** Short, display-friendly id for the table's first column. Falls back to a
 *  placeholder when the trace has no id, so an undefined id can't crash the row. */
export function shortTraceId(t: TraceRow | null | undefined): string {
  const id = traceId(t);
  return id ? id.slice(0, 12) : "(no id)";
}

/** Whether this trace can be drilled into (the spans endpoint needs an id). */
export function hasTraceId(t: TraceRow | null | undefined): boolean {
  return traceId(t) !== "";
}
