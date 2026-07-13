/** Turn an API failure into something the scan funnel can say out loud.
 *
 *  A failed scan call surfaces as an {@link import("./api").ApiError} (or, for
 *  older/edge cases, a plain Error). Its `detail` may be a plain string OR the
 *  structured `{code, message, action}` envelope the server uses for rate-limit,
 *  out-of-credits, and copilot errors. This normalizes both into a single
 *  human sentence, and flags the auth case so the funnel can bounce the user
 *  through a quick signup instead of showing an error.
 *
 *  Why this exists: a 429 from the scan rate-limiter carries a structured
 *  detail object; naively String()-ing it printed "[object Object]" to the
 *  user. We special-case 429 with a calm "you're going a bit fast" and always
 *  prefer the server's own message when it gave us one. */
export function friendlyError(e: any): { auth: boolean; msg: string } {
  const status = e?.status;
  if (status === 401) return { auth: true, msg: "" };

  // detail may be the structured {code,message,action} envelope or a string.
  const d = e?.detail;
  const structured =
    d && typeof d === "object" && !Array.isArray(d) ? (d as { message?: string }) : null;

  if (status === 429) {
    return {
      auth: false,
      msg: structured?.message
        || "You're going a bit fast — give it a moment and try again.",
    };
  }

  const raw =
    structured?.message
    ?? (typeof d === "string" ? d : null)
    ?? e?.message
    ?? String(e ?? "");
  const msg = String(raw).replace(/^\d+\s*—?\s*/, "");  // strip a leading "500 — "
  return { auth: false, msg: msg || "Something went wrong. Please try again." };
}
