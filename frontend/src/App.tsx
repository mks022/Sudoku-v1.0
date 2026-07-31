import { Navigate, Route, Routes } from "react-router-dom";
import { TopNav } from "./components/TopNav";
import { ConsolePage } from "./pages/ConsolePage";
import { CallsPage } from "./pages/CallsPage";
import { ProvidersPage } from "./pages/ProvidersPage";
import { RagPage } from "./pages/RagPage";

export default function App() {
  return (
    <div className="app-shell">
      <TopNav />
      <Routes>
        <Route path="/" element={<ConsolePage />} />
        <Route path="/calls" element={<CallsPage />} />
        <Route path="/providers" element={<ProvidersPage />} />
        <Route path="/rag" element={<RagPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
