import { lazy, Suspense } from "react";
import type { RouteRecord } from "vite-react-ssg";
import { ApiDocsPage } from "./pages/ApiDocsPage";
import { CertifiedDirectoryPage } from "./pages/CertifiedDirectoryPage";
import { LandingPage } from "./pages/LandingPage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { StatusPage } from "./pages/StatusPage";

/* The public content routes below are imported eagerly because they are emitted
   as static HTML at build time (renderToString can't resolve a lazy chunk).
   Everything else — the interactive scanner, auth, the certificate detail, the
   assistant, and the heavy React Flow console — is code-split so the public /
   landing bundle stays small and the canvas chunk only loads at /app. */
const ScanPage = lazy(() =>
  import("./pages/ScanPage").then((m) => ({ default: m.ScanPage })));
const CertificatePage = lazy(() =>
  import("./pages/CertificatePage").then((m) => ({ default: m.CertificatePage })));
const AssistantPage = lazy(() =>
  import("./pages/AssistantPage").then((m) => ({ default: m.AssistantPage })));
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
  // flagship consumer surface — the safe personal assistant (heavy, lazy)
  { path: "/assistant", element: suspense(<AssistantPage />) },
  { path: "/login", element: suspense(<LoginPage />) },
  { path: "/signup", element: suspense(<SignupPage />) },
  { path: "/verify", element: suspense(<VerifyPage />) },
  { path: "/api-docs", element: <ApiDocsPage />, entry: "src/pages/ApiDocsPage.tsx" },
  { path: "/methodology", element: <MethodologyPage />, entry: "src/pages/MethodologyPage.tsx" },
  // public service-status board — Agenttic's own uptime (prerendered shell, live-polled)
  { path: "/status", element: <StatusPage />, entry: "src/pages/StatusPage.tsx" },
  // public certification brand surfaces
  { path: "/certified", element: <CertifiedDirectoryPage />, entry: "src/pages/CertifiedDirectoryPage.tsx" },
  { path: "/certified/:id", element: suspense(<CertificatePage />) },
  // the app canvas, behind auth — client-only, never prerendered
  { path: "/app/*", element: suspense(<AppShell />) },
];
