import { Routes, Route, Link } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import DocumentPage from "./pages/DocumentPage";
import UploadPage from "./pages/UploadPage";

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link to="/" className="text-lg font-semibold">PDF Extract</Link>
          <nav className="text-sm text-slate-600 space-x-4">
            <Link to="/" className="hover:text-slate-900">Documents</Link>
            <Link to="/upload" className="hover:text-slate-900">Upload</Link>
          </nav>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/documents/:id" element={<DocumentPage />} />
        </Routes>
      </main>
    </div>
  );
}
