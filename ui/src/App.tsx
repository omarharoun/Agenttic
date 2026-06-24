import { Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";
import { ApiDocsPage } from "./pages/ApiDocsPage";
import { CertificatePage } from "./pages/CertificatePage";
import { CertifiedDirectoryPage } from "./pages/CertifiedDirectoryPage";
import { LandingPage } from "./pages/LandingPage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { ScanPage } from "./pages/ScanPage";
import { LoginPage, SignupPage, VerifyPage } from "./pages/AuthPages";

export function App() {
  return (
    <Routes>
      {/* public front door — the scanner is the primary entry */}
      <Route path="/" element={<LandingPage />} />
      <Route path="/scan" element={<ScanPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/verify" element={<VerifyPage />} />
      <Route path="/api-docs" element={<ApiDocsPage />} />
      <Route path="/methodology" element={<MethodologyPage />} />
      {/* public certification brand surfaces */}
      <Route path="/certified" element={<CertifiedDirectoryPage />} />
      <Route path="/certified/:id" element={<CertificatePage />} />
      {/* the app canvas, behind auth */}
      <Route path="/app/*" element={<AppShell />} />
    </Routes>
  );
}
