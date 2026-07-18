import { lazy, Suspense } from "react";
import type { RouteRecord } from "vite-react-ssg";
import { ApiDocsPage } from "./pages/ApiDocsPage";
import { CertifiedDirectoryPage } from "./pages/CertifiedDirectoryPage";
import { LandingPage } from "./pages/LandingPage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { NotFoundPublicPage } from "./pages/NotFoundPublicPage";
import { PlaygroundPage } from "./pages/PlaygroundPage";
import { PlaygroundGatePage } from "./pages/PlaygroundGatePage";
import { PricingPage } from "./pages/PricingPage";
import { StatusPage } from "./pages/StatusPage";

/* The public content routes below are imported eagerly because they are emitted
   as static HTML at build time (renderToString can't resolve a lazy chunk).
   Everything else — the interactive scanner, auth, the certificate detail,
   and the heavy React Flow console — is code-split so the public /
   landing bundle stays small and the canvas chunk only loads at /app. */
const ScanPage = lazy(() =>
  import("./pages/ScanPage").then((m) => ({ default: m.ScanPage })));
const CertificatePage = lazy(() =>
  import("./pages/CertificatePage").then((m) => ({ default: m.CertificatePage })));
const AppShell = lazy(() =>
  import("./AppShell").then((m) => ({ default: m.AppShell })));
const LoginPage = lazy(() =>
  import("./pages/AuthPages").then((m) => ({ default: m.LoginPage })));
const SignupPage = lazy(() =>
  import("./pages/AuthPages").then((m) => ({ default: m.SignupPage })));
const VerifyPage = lazy(() =>
  import("./pages/AuthPages").then((m) => ({ default: m.VerifyPage })));

function RouteFallback() {
  return <div className="route-loading" aria-busy="true" aria-label="Loading" />;
}

const suspense = (node: React.ReactNode) => (
  <Suspense fallback={<RouteFallback />}>{node}</Suspense>
);

/* The route table, shared by the client router and the build-time prerenderer.
   Only the public content routes are emitted as static HTML (see
   `ssgOptions.includedRoutes` in vite.config.ts) — /scan stays interactive and
   /app/* is a pure client SPA that the prerenderer never touches. */
export const routes: RouteRecord[] = [
  // public front door — instrument-readout landing
  { path: "/", element: <LandingPage />, entry: "src/pages/LandingPage.tsx" },
  // the live scanner (interactive — client-rendered, not prerendered)
  { path: "/scan", element: suspense(<ScanPage />) },
  { path: "/login", element: suspense(<LoginPage />) },
  { path: "/signup", element: suspense(<SignupPage />) },
  { path: "/verify", element: suspense(<VerifyPage />) },
  { path: "/api-docs", element: <ApiDocsPage />, entry: "src/pages/ApiDocsPage.tsx" },
  { path: "/methodology", element: <MethodologyPage />, entry: "src/pages/MethodologyPage.tsx" },
  /* The scenario-engine explainer. Public, but code-split and NOT in
     vite.config.ts's prerender set — unlike the other public content routes —
     because it imports `formatCreated` from the CONSOLE's ScenariosPage. An
     eager import would pull a console page into the landing's initial chunk,
     which is the thing scripts/check-bundle.mjs exists to stop (it guards
     AppShell by name; this route would have walked past it). Lazy keeps both
     modules in chunks fetched only at /engine — measured with `vite build`:
     EnginePage 24.0 kB (8.0 kB gz) and ScenariosPage 22.8 kB (6.9 kB gz), i.e.
     ~15 kB gz that would otherwise sit in a 103 kB gz landing payload against a
     150 kB budget. The cost is that this page ships no static HTML: its two
     live reads (/api/capabilities and the reader's OWN stored runs) could not
     be baked in either way, but its quoted vocabularies could have been, and
     are not. */
  { path: "/engine", element: suspense(<EnginePage />) },
  // public pricing — plans + free-credits offer (prerendered, hydrates live)
  { path: "/pricing", element: <PricingPage />, entry: "src/pages/PricingPage.tsx" },
  // public service-status board — Agenttic's own uptime (prerendered shell, live-polled)
  { path: "/status", element: <StatusPage />, entry: "src/pages/StatusPage.tsx" },
  // public playground — no-signup sim-core simulations (prerendered, SEO)
  { path: "/playground", element: <PlaygroundPage />, entry: "src/pages/PlaygroundPage.tsx" },
  { path: "/playground/gate", element: <PlaygroundGatePage />, entry: "src/pages/PlaygroundGatePage.tsx" },
  // public certification brand surfaces
  { path: "/certified", element: <CertifiedDirectoryPage />, entry: "src/pages/CertifiedDirectoryPage.tsx" },
  { path: "/certified/:id", element: suspense(<CertificatePage />) },
  // the app canvas, behind auth — client-only, never prerendered
  { path: "/app/*", element: suspense(<AppShell />) },
  // public catch-all — retired/unknown paths get a branded 404, not a raw crash
  { path: "*", element: <NotFoundPublicPage /> },
];
