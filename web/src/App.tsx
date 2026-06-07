import { Routes, Route, Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, getAuthToken, setAuthToken, type AuthUser } from "./api";
import Dashboard from "./pages/Dashboard";
import AdminFailuresPage from "./pages/AdminFailuresPage";
import DocumentPage from "./pages/DocumentPage";
import LoginPage from "./pages/LoginPage";
import UploadPage from "./pages/UploadPage";

export default function App() {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(Boolean(getAuthToken()));

  useEffect(() => {
    if (!getAuthToken()) return;
    api
      .get<AuthUser>("/auth/me")
      .then((response) => setUser(response.data))
      .catch(() => setAuthToken(null))
      .finally(() => setIsCheckingAuth(false));
  }, []);

  if (isCheckingAuth) return <div className="p-8 text-slate-500">Loading...</div>;
  if (!user) return <LoginPage onLogin={setUser} />;

  const logout = () => {
    setAuthToken(null);
    setUser(null);
    queryClient.clear();
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link to="/" className="text-lg font-semibold">PDF Extract</Link>
          <nav className="flex items-center gap-4 text-sm text-slate-600">
            <Link to="/" className="hover:text-slate-900">Documents</Link>
            <Link to="/upload" className="hover:text-slate-900">Upload</Link>
            <Link to="/review" className="hover:text-slate-900">Review</Link>
            {user.roles.includes("admin") && <Link to="/admin/failures" className="hover:text-slate-900">Failures</Link>}
            <span className="text-slate-400">{user.username}</span>
            <button type="button" onClick={logout} className="font-medium text-slate-700 hover:text-slate-900">
              Log out
            </button>
          </nav>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-8">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/review" element={<Dashboard mode="review" />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/admin/failures" element={<AdminFailuresPage />} />
          <Route path="/documents/:id" element={<DocumentPage />} />
          <Route path="/documents/:id/review" element={<DocumentPage />} />
        </Routes>
      </main>
    </div>
  );
}
