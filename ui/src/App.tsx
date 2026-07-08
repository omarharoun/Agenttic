import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { ApiDocsPage } from "./pages/ApiDocsPage";
import { CertificatePage } from "./pages/CertificatePage";
import { CertifiedDirectoryPage } from "./pages/CertifiedDirectoryPage";
import { LandingPage } from "./pages/LandingPage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { ScanPage } from "./pages/ScanPage";
import { StatusPage } from "./pages/StatusPage";
import { LoginPage, SignupPage, VerifyPage } from "./pages/AuthPages";

/* The authenticated console (React Flow canvas et al.) and the assistant are
   the heavy chunks — lazy-loaded so the PUBLIC surfaces (landing, scanner,
   certificates) ship a small, fast bundle. */
const AppShell = lazy(() =>
  import("./AppShell").then((m) => ({ default: m.AppShell })));
const AssistantPage = lazy(() =>
  import("./pages/AssistantPage").then((m) => ({ default: m.AssistantPage })));

function RouteFallback() {
  return <div className="route-loading" aria-busy="true" aria-label="Loading" />;
}

export function App() {
  return (
    <Routes>
      {/* public front door — the scanner is the primary entry */}
      <Route path="/" element={<LandingPage />} />
      <Route path="/scan" element={<ScanPage />} />
      {/* flagship consumer surface — the safe personal assistant */}
      <Route path="/assistant"
             element={<Suspense fallback={<RouteFallback />}><AssistantPage /></Suspense>} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/verify" element={<VerifyPage />} />
      <Route path="/api-docs" element={<ApiDocsPage />} />
      <Route path="/methodology" element={<MethodologyPage />} />
      {/* public service-status board — Agenttic's own uptime */}
      <Route path="/status" element={<StatusPage />} />
      {/* public certification brand surfaces */}
      <Route path="/certified" element={<CertifiedDirectoryPage />} />
      <Route path="/certified/:id" element={<CertificatePage />} />
      {/* the app canvas, behind auth */}
      <Route path="/app/*"
             element={<Suspense fallback={<RouteFallback />}><AppShell /></Suspense>} />
    </Routes>
  );
}
