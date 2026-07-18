// @vitest-environment jsdom
/* SPEC-4 Step 21 — the per-route error boundary and the global mid-session 401.

   1. <ErrorBoundary> catches a throwing child and renders the SAME plain-language
      panel as <PageData>'s error state (title + human message + a Retry), never
      a raw stack. A retry that no longer throws recovers to the children.
   2. The api layer fires its registered onUnauthorized handler exactly once when
      any call 401s — the hook the shell uses to bounce to /login?next=<path>. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { api, ApiError, onUnauthorized } from "./api";

// React 18 act() needs this global in a test environment.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// A child that throws on render until told to stop — lets us prove both the
// catch and the recovery-on-retry path.
function Boom({ throwing }: { throwing: boolean }) {
  if (throwing) throw new Error("kaboom from a child");
  return <div className="recovered">all good</div>;
}

describe("<ErrorBoundary>", () => {
  it("catches a throwing child and renders the PageData error panel (no raw stack)", () => {
    // Silence React's expected error logging for the caught throw.
    vi.spyOn(console, "error").mockImplementation(() => {});

    act(() => {
      root.render(
        <ErrorBoundary title="This page hit an error">
          <Boom throwing />
        </ErrorBoundary>,
      );
    });

    // Same design as PageData's ErrorPanel: role=alert + the shared classes.
    const alert = container.querySelector('[role="alert"].pagedata-error');
    expect(alert).not.toBeNull();
    expect(container.querySelector(".pagedata-error-title")?.textContent)
      .toBe("This page hit an error");
    // The human message is shown; a raw stack ("at Boom (…)" ) is not.
    const msg = container.querySelector(".pagedata-error-msg")?.textContent ?? "";
    expect(msg).toBe("kaboom from a child");
    expect(container.innerHTML).not.toMatch(/\bat Boom\b/);
    // A Retry button is offered.
    expect(container.querySelector(".pagedata-error-action button")).not.toBeNull();
  });

  it("recovers to the children when Retry is clicked and the child no longer throws", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    function Harness() {
      const [throwing, setThrowing] = React.useState(true);
      return (
        <ErrorBoundary>
          <button className="stop" onClick={() => setThrowing(false)}>stop</button>
          <Boom throwing={throwing} />
        </ErrorBoundary>
      );
    }

    act(() => root.render(<Harness />));
    expect(container.querySelector(".pagedata-error")).not.toBeNull();

    // Stop the child from throwing, then hit Retry to clear the boundary.
    // (The stop button lives inside the crashed subtree, so we flip state via
    //  the boundary's own reset: click Retry after the child is fixed.)
    // Simplest deterministic path: re-render with a non-throwing child.
    act(() => {
      root.render(
        <ErrorBoundary>
          <Boom throwing={false} />
        </ErrorBoundary>,
      );
    });
    const retry = container.querySelector(".pagedata-error-action button") as HTMLButtonElement | null;
    act(() => retry?.click());
    expect(container.querySelector(".recovered")?.textContent).toBe("all good");
  });
});

describe("global 401 handler (api.onUnauthorized)", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: () => "", setItem: () => {}, removeItem: () => {},
    });
  });

  it("fires the registered handler once when a call 401s, and still throws ApiError", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve(new Response(JSON.stringify({ detail: "expired" }),
        { status: 401, headers: { "content-type": "application/json" } })));

    const seen: ApiError[] = [];
    const unsub = onUnauthorized((e) => seen.push(e));

    await expect(api.listScorecards()).rejects.toBeInstanceOf(ApiError);

    expect(seen).toHaveLength(1);
    expect(seen[0].status).toBe(401);
    unsub();

    // After unsubscribe, a second 401 does not re-invoke the old handler.
    await expect(api.listScorecards()).rejects.toBeInstanceOf(ApiError);
    expect(seen).toHaveLength(1);
  });
});
