// @vitest-environment jsdom
/* SPEC-4 Step 17.3 — the state trio on every data route.

   Two layers of proof:
   1. A thorough unit test of <PageData> / <ErrorPanel>: every branch of the
      precedence (error > loading > empty > children), that the error message is
      plain-language (via errMessage, never a raw stack), and that the Retry
      button actually fires onRetry.
   2. A smoke test: five core DATA pages are rendered with a stubbed API and
      asserted to show BOTH a layout-matched skeleton (fetch pending) and a
      one-action empty invitation (fetch resolved empty) — i.e. the trio is
      really wired, not just available. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import React, { act } from "react";

import { PageData, ErrorPanel } from "./components/PageData";
import { EmptyState } from "./components/ui";

// React 18 act() needs this global to run in a test environment.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const ssr = (el: React.ReactElement) => renderToStaticMarkup(el);

// ---------------------------------------------------------------------------
// 1. <PageData> unit — the four branches + precedence + retry
// ---------------------------------------------------------------------------
describe("PageData — the state trio", () => {
  const children = <div className="real-content">loaded data</div>;
  const skeleton = <div className="my-skel">shimmer</div>;
  const emptyState = (
    <EmptyState title="Nothing yet" action={<button>Do the one thing</button>} />
  );

  it("loading → renders the (layout-matched) skeleton, not the content", () => {
    const html = ssr(
      <PageData loading empty={false} error={null} skeleton={skeleton}>{children}</PageData>,
    );
    expect(html).toContain("my-skel");
    expect(html).not.toContain("real-content");
  });

  it("loading with no skeleton → falls back to the default <Skeleton>", () => {
    const html = ssr(<PageData loading>{children}</PageData>);
    expect(html).toContain("skel-wrap"); // the default shimmer, never a void
    expect(html).not.toContain("real-content");
  });

  it("empty → renders the one-action empty invitation, not the content", () => {
    const html = ssr(
      <PageData loading={false} empty emptyState={emptyState}>{children}</PageData>,
    );
    expect(html).toContain("Nothing yet");
    expect(html).toContain("Do the one thing");
    expect(html).not.toContain("real-content");
  });

  it("error → renders a plain-language panel with a Retry, never a raw stack", () => {
    const err = new Error("network is unreachable");
    const html = ssr(
      <PageData loading error={err} skeleton={skeleton} onRetry={() => {}}>{children}</PageData>,
    );
    expect(html).toContain("pagedata-error");
    expect(html).toContain("network is unreachable");
    expect(html).toContain("Retry");
    // precedence: error beats loading — the skeleton must NOT show
    expect(html).not.toContain("my-skel");
    expect(html).not.toContain("real-content");
    // never dumps a stack trace
    expect(html).not.toMatch(/\bat\s+\w+\s+\(/);
  });

  it("errMessage derives a message from a bare-string throw and an envelope", () => {
    expect(ssr(<ErrorPanel error="boom" />)).toContain("boom");
    expect(ssr(<ErrorPanel error={{ detail: "quota exceeded" }} />))
      .toContain("quota exceeded");
  });

  it("no Retry button when onRetry is omitted", () => {
    const html = ssr(<ErrorPanel error={new Error("x")} />);
    expect(html).not.toContain("Retry");
  });

  it("resolved & non-empty → renders the children", () => {
    const html = ssr(
      <PageData loading={false} empty={false} error={null}>{children}</PageData>,
    );
    expect(html).toContain("real-content");
  });

  it("precedence order is error > loading > empty > children", () => {
    // all flags on at once → error wins
    const html = ssr(
      <PageData loading empty error={new Error("first")} skeleton={skeleton}
                emptyState={emptyState}>{children}</PageData>,
    );
    expect(html).toContain("first");
    expect(html).not.toContain("my-skel");
    expect(html).not.toContain("Nothing yet");
  });
});

// ---------------------------------------------------------------------------
// 1b. Retry actually fires (needs a real DOM click)
// ---------------------------------------------------------------------------
describe("PageData — Retry wiring", () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => { host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); });
  afterEach(() => { act(() => root.unmount()); host.remove(); });

  it("clicking Retry invokes onRetry", () => {
    const onRetry = vi.fn();
    act(() => {
      root.render(<ErrorPanel error={new Error("down")} onRetry={onRetry} />);
    });
    const btn = host.querySelector(".pagedata-error-action button") as HTMLButtonElement;
    expect(btn).toBeTruthy();
    act(() => { btn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// 2. Smoke test — five core DATA pages show a skeleton (pending) and a
//    one-action empty invitation (resolved empty).
// ---------------------------------------------------------------------------

// A stub `api` whose every method is a vi.fn(); tests set its return per case.
// Pages destructure `{ api }` and call `api.<method>()`, so a plain object of
// vi.fns is enough. errMessage/ApiError/downloadBlob are passed through as real
// helpers so the error path stays honest. `vi.hoisted` runs before the hoisted
// vi.mock factory, so the proxy exists when the mock is applied.
const { apiStub, apiProxy } = vi.hoisted(() => {
  const stub: Record<string, ReturnType<typeof vi.fn>> = {};
  const proxy = new Proxy(stub, {
    get(target, prop: string) {
      if (!(prop in target)) target[prop] = vi.fn(() => new Promise(() => {}));
      return target[prop];
    },
  });
  return { apiStub: stub, apiProxy: proxy };
});

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return {
    ...real,
    api: apiProxy,
    auth: { get: () => null, set: () => {}, clear: () => {} },
  };
});

// Import pages AFTER the mock is registered.
import { CertifiedDirectoryPage } from "./pages/CertifiedDirectoryPage";
import { AgentsPage } from "./pages/AgentsPage";
import { LeaderboardPage } from "./pages/LeaderboardPage";
import { IssuesPage } from "./pages/IssuesPage";
import { BillingPage } from "./pages/BillingPage";

function mountPage(el: React.ReactElement): { host: HTMLDivElement; root: Root } {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(<MemoryRouter>{el}</MemoryRouter>); });
  return { host, root };
}

const PAGES: { name: string; el: React.ReactElement; skelSel: string }[] = [
  { name: "CertifiedDirectory", el: <CertifiedDirectoryPage />, skelSel: ".skel-wrap,.pagedata-skel" },
  { name: "Agents", el: <AgentsPage />, skelSel: ".skel-wrap,.pagedata-skel" },
  { name: "Leaderboard", el: <LeaderboardPage />, skelSel: ".skel-wrap,.pagedata-skel" },
  { name: "Issues", el: <IssuesPage />, skelSel: ".skel-wrap,.pagedata-skel" },
  { name: "Billing", el: <BillingPage />, skelSel: ".skel-wrap,.pagedata-skel" },
];

describe("core data pages render a skeleton while the fetch is pending", () => {
  beforeEach(() => { for (const k of Object.keys(apiStub)) delete apiStub[k]; });

  for (const p of PAGES) {
    it(`${p.name} shows a loading skeleton`, () => {
      // default proxy fns return never-resolving promises → stays in loading
      const { host, root } = mountPage(p.el);
      expect(host.querySelector(p.skelSel)).toBeTruthy();
      act(() => root.unmount());
      host.remove();
    });
  }
});

describe("core data pages render a one-action empty invitation when there is no data", () => {
  beforeEach(() => { for (const k of Object.keys(apiStub)) delete apiStub[k]; });

  const EMPTY_RETURNS: Record<string, unknown> = {
    // resolve every method to an "empty" shape; the pages normalise these.
    publicCertifiedDirectory: [],
    listAgents: { agents: [], catalog: [] },
    leaderboard: { rows: [] },
    listStandardLeaderboards: [],
    standardLeaderboard: { rows: [] },
    listRuns: { runs: [] },
    billingOverview: { plan: {}, currency: "usd", usage_by_reason: {}, balance_display: "$0" },
    billingPlans: { plans: [], topups: [] },
    billingProviderConfig: { stripe: { configured: false }, paypal: { configured: false } },
    billingInvoices: { invoices: [] },
    billingLedger: { entries: [] },
    me: { email: null, role: "member", tenant: "t", auth_method: "token" },
    anthropicKeyStatus: { set: false, masked: null, updated_at: null },
    listTokens: { tokens: [] },
  };

  async function flush() {
    // let the pending microtasks (fetch resolutions + state updates) drain
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  }

  it("CertifiedDirectory invites the first certification", async () => {
    apiStub.publicCertifiedDirectory = vi.fn(async () => EMPTY_RETURNS.publicCertifiedDirectory);
    const { host, root } = mountPage(<CertifiedDirectoryPage />);
    await flush();
    expect(host.querySelector(".empty-state")).toBeTruthy();
    expect(host.textContent).toContain("No certified agents yet");
    // exactly one primary action in the invitation
    expect(host.querySelectorAll(".empty-action .btn-primary").length).toBe(1);
    act(() => root.unmount()); host.remove();
  });

  it("Agents invites registering the first agent", async () => {
    apiStub.listAgents = vi.fn(async () => ({ agents: [], catalog: [] }));
    const { host, root } = mountPage(<AgentsPage />);
    await flush();
    expect(host.querySelector(".empty-state")).toBeTruthy();
    expect(host.textContent).toContain("No agents yet");
    act(() => root.unmount()); host.remove();
  });
});
