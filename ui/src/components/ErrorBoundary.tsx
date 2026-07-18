import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorPanel } from "./PageData";

/* ============================================================================
   <ErrorBoundary> — the last honest wall (SPEC-4 Step 21).

   A render-time throw anywhere below this boundary (a bad prop, a null deref, a
   thrown ApiError that no page caught) would otherwise white-screen the whole
   console via React's default behaviour. This class catches it and renders the
   SAME plain-language panel <PageData> uses for a failed fetch: a title, a human
   message (never a raw stack in production), and a Retry that resets the
   boundary so the user can recover without a full reload.

   It is a class because that is the only React API that can catch a descendant's
   render error — `getDerivedStateFromError` (flip to the fallback) plus
   `componentDidCatch` (report). Wrapping the app's <Routes> means one page
   crashing no longer takes the shell down with it.

   `resetKeys` (typically the current pathname) clears the error on navigation:
   once the user moves to a different route, a stale crash shouldn't linger.
   ========================================================================== */

interface Props {
  children: ReactNode;
  /** Headline for the fallback panel. */
  title?: string;
  /** When any value here changes (e.g. the route path), the boundary resets. */
  resetKeys?: readonly unknown[];
  /** Optional side-channel for logging/telemetry. */
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface it for a telemetry sink if one is wired, and leave a console
    // breadcrumb for debugging. The stack goes to the console ONLY — never to
    // the rendered panel, which shows the plain-language message alone.
    this.props.onError?.(error, info);
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  componentDidUpdate(prev: Props): void {
    // Reset the caught error when the reset keys change (e.g. route navigation),
    // so recovering is as simple as going somewhere else.
    if (this.state.error && prev.resetKeys !== this.props.resetKeys) {
      const a = prev.resetKeys ?? [];
      const b = this.props.resetKeys ?? [];
      const changed = a.length !== b.length || a.some((v, i) => !Object.is(v, b[i]));
      if (changed) this.setState({ error: null });
    }
  }

  private reset = (): void => this.setState({ error: null });

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="page">
          <div className="list-page">
            <ErrorPanel
              error={this.state.error}
              onRetry={this.reset}
              title={this.props.title ?? "This page hit an error"}
            />
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
