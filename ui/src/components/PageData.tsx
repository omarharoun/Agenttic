import type { ReactNode } from "react";
import { errMessage } from "../api";
import { IconError, IconRefresh } from "../icons";
import { Skeleton } from "./ui";

/* ============================================================================
   <PageData> — the state trio every DATA route owes the reader.

   A data route is never in exactly one state: it is loading, or it failed, or
   it succeeded-but-empty, or it has data. Leaving any of those unhandled is how
   pages ship a spinner-in-a-void, a blank screen on error, or a bare table with
   no invitation. This wrapper makes the contract explicit and uniform:

     • loading → a layout-matched skeleton (never a lone spinner). Callers pass
       a `skeleton` shaped like the final content; the default is a <Skeleton>.
     • error   → a plain-language panel (via errMessage, NEVER a raw stack) that
       says what went wrong and offers a Retry wired to `onRetry`.
     • empty   → an <EmptyState> invitation with exactly one primary action
       (the caller supplies it as `emptyState`).
     • data    → the children.

   Precedence is deliberate: error > loading > empty > children. An error during
   a background refresh should win over a stale-but-present view only when the
   caller decides to surface it (they control the `error` prop); once resolved,
   loading and empty fall through to the real content.
   ========================================================================== */

export function ErrorPanel({ error, onRetry, title = "Something went wrong" }: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  return (
    <div className="pagedata-error" role="alert">
      <div className="pagedata-error-ico"><IconError /></div>
      <div className="pagedata-error-title">{title}</div>
      <div className="pagedata-error-msg">{errMessage(error)}</div>
      {onRetry && (
        <div className="pagedata-error-action">
          <button type="button" className="btn-ghost" onClick={onRetry}>
            <IconRefresh /> Retry
          </button>
        </div>
      )}
    </div>
  );
}

export function PageData({
  loading,
  error,
  empty,
  onRetry,
  skeleton,
  emptyState,
  errorTitle,
  children,
}: {
  /** True while the initial fetch is in flight. */
  loading: boolean;
  /** A caught error (any thrown value); null/undefined means no error. */
  error?: unknown | null;
  /** True when the fetch succeeded but returned nothing to show. */
  empty?: boolean;
  /** Re-run the fetch. Rendered as the error panel's Retry button. */
  onRetry?: () => void;
  /** Layout-matched loading placeholder. Defaults to a shimmer <Skeleton>. */
  skeleton?: ReactNode;
  /** The one-action <EmptyState> invitation shown when `empty` is true. */
  emptyState?: ReactNode;
  /** Optional headline for the error panel (defaults to a generic message). */
  errorTitle?: string;
  /** The real content, rendered only once loaded, error-free and non-empty. */
  children: ReactNode;
}) {
  if (error != null) {
    return <ErrorPanel error={error} onRetry={onRetry} title={errorTitle} />;
  }
  if (loading) {
    return <>{skeleton ?? <Skeleton />}</>;
  }
  if (empty) {
    return <>{emptyState ?? null}</>;
  }
  return <>{children}</>;
}
