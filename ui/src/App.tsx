import { NavLink, Route, Routes } from "react-router-dom";
import { EditorPage } from "./pages/EditorPage";
import { ExecutionsPage } from "./pages/ExecutionsPage";
import { ResourcesPage } from "./pages/ResourcesPage";

export function App() {
  return (
    <>
      <nav className="app-nav">
        <div className="logo" title="Agenttic">⬡</div>
        <NavLink to="/" end title="Workflow editor">▦</NavLink>
        <NavLink to="/executions" title="Executions">▶</NavLink>
        <NavLink to="/resources" title="Suites / scorecards / traces">▤</NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<EditorPage />} />
        <Route path="/executions" element={<ExecutionsPage />} />
        <Route path="/resources" element={<ResourcesPage />} />
      </Routes>
    </>
  );
}
