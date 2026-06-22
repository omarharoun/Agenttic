import { Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";
import { ApiDocsPage } from "./pages/ApiDocsPage";
import { LandingPage } from "./pages/LandingPage";
import { MethodologyPage } from "./pages/MethodologyPage";
import { LoginPage, SignupPage, VerifyPage } from "./pages/AuthPages";

export function App() {
  return (
    <Routes>
      {/* public front door */}
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/verify" element={<VerifyPage />} />
      <Route path="/api-docs" element={<ApiDocsPage />} />
      <Route path="/methodology" element={<MethodologyPage />} />
      {/* the app canvas, behind auth */}
      <Route path="/app/*" element={<AppShell />} />
    </Routes>
  );
}
