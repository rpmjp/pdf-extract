/**
 * Application shell.
 *
 * The shell owns authentication bootstrapping, logout cleanup, theme resolution,
 * and route registration. Page components receive only the small bits of shell
 * state they need, such as the resolved light/dark theme for dashboard styling.
 */

import { Routes, Route, Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, clearAuthTokens, getAuthToken, getRefreshToken, type AuthUser } from "./api";
import Dashboard from "./pages/Dashboard";
import AdminFailuresPage from "./pages/AdminFailuresPage";
import ConfidenceInsightsPage from "./pages/ConfidenceInsightsPage";
import DocumentPage from "./pages/DocumentPage";
import LoginPage from "./pages/LoginPage";
import UploadPage from "./pages/UploadPage";

type ThemeMode = "system" | "light" | "dark";
type ResolvedTheme = "light" | "dark";

const THEME_KEY = "pdf_extract_theme";

function getInitialTheme(): ThemeMode {
  /** Load the persisted theme preference, defaulting to system behavior. */

  const stored = localStorage.getItem(THEME_KEY);
  return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
}

function resolveTheme(mode: ThemeMode): ResolvedTheme {
  /** Convert the user's preference into an actual light/dark value. */

  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  return mode === "dark" || (mode === "system" && prefersDark) ? "dark" : "light";
}

function applyTheme(mode: ThemeMode): ResolvedTheme {
  /** Keep CSS selectors and tests synchronized with the resolved theme. */

  const resolved = resolveTheme(mode);
  document.documentElement.classList.remove("light", "dark");
  document.documentElement.classList.add(resolved);
  document.documentElement.dataset.theme = resolved;
  return resolved;
}

function ThemeSwitcher({ value, resolvedTheme, onChange }: { value: ThemeMode; resolvedTheme: ResolvedTheme; onChange: (mode: ThemeMode) => void }) {
  /** Accessible segmented control for system/light/dark theme selection. */

  const options: ThemeMode[] = ["system", "light", "dark"];
  const dark = resolvedTheme === "dark";
  return (
    <div
      className={`inline-flex rounded-xl border p-1 text-xs font-semibold shadow-sm ${
        dark ? "border-slate-700 bg-slate-950 text-slate-300" : "border-slate-300 bg-white text-slate-700"
      }`}
      aria-label="Theme"
    >
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => onChange(option)}
          className={`rounded-md px-2.5 py-1 capitalize transition ${
            value === option
              ? dark
                ? "bg-cyan-300 text-slate-950 shadow-sm"
                : "bg-blue-600 text-white shadow-sm"
              : dark
                ? "text-slate-300 hover:bg-slate-800 hover:text-cyan-200"
                : "text-slate-700 hover:bg-blue-50 hover:text-blue-700"
          }`}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(Boolean(getAuthToken()));
  const [theme, setTheme] = useState<ThemeMode>(getInitialTheme);
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => applyTheme(getInitialTheme()));

  useEffect(() => {
    // On a hard refresh, verify the stored access token before rendering the
    // protected app. A failed check clears browser credentials and falls back
    // to the login page.
    if (!getAuthToken()) return;
    api
      .get<AuthUser>("/auth/me")
      .then((response) => setUser(response.data))
      .catch(() => clearAuthTokens())
      .finally(() => setIsCheckingAuth(false));
  }, []);

  useEffect(() => {
    // System mode must react to OS-level theme changes after the app is open.
    setResolvedTheme(applyTheme(theme));
    localStorage.setItem(THEME_KEY, theme);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const listener = () => {
      if (theme === "system") setResolvedTheme(applyTheme("system"));
    };
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, [theme]);

  const changeTheme = (nextTheme: ThemeMode) => {
    setTheme(nextTheme);
    setResolvedTheme(applyTheme(nextTheme));
    localStorage.setItem(THEME_KEY, nextTheme);
  };

  if (isCheckingAuth) return <div className="p-8 text-slate-500">Loading...</div>;
  if (!user) return <LoginPage onLogin={setUser} />;

  const logout = () => {
    // Best-effort server revocation keeps local logout snappy while still
    // invalidating the refresh token whenever the API is reachable.
    const refreshToken = getRefreshToken();
    if (refreshToken) api.post("/auth/logout", { refresh_token: refreshToken }).catch(() => undefined);
    clearAuthTokens();
    setUser(null);
    queryClient.clear();
  };

  return (
    <div className={`min-h-screen transition-colors ${resolvedTheme === "dark" ? "bg-[#070b16] text-slate-100" : "bg-[#f5f7fb] text-slate-950"}`}>
      <header
        className={`sticky top-0 z-30 border-b shadow-sm backdrop-blur ${
          resolvedTheme === "dark" ? "border-slate-800 bg-[#080d1a]/95" : "border-slate-200 bg-white/95"
        }`}
      >
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link to="/" className={`text-lg font-semibold ${resolvedTheme === "dark" ? "text-white" : "text-slate-950"}`}>PDF Extract</Link>
          <nav className={`flex items-center gap-4 text-sm font-medium ${resolvedTheme === "dark" ? "text-slate-300" : "text-slate-600"}`}>
            <Link to="/" className={resolvedTheme === "dark" ? "hover:text-cyan-200" : "hover:text-blue-700"}>Documents</Link>
            <Link to="/upload" className={resolvedTheme === "dark" ? "hover:text-cyan-200" : "hover:text-blue-700"}>Upload</Link>
            <Link to="/review" className={resolvedTheme === "dark" ? "hover:text-cyan-200" : "hover:text-blue-700"}>Review</Link>
            {user.roles.includes("admin") && <Link to="/admin/failures" className={resolvedTheme === "dark" ? "hover:text-cyan-200" : "hover:text-blue-700"}>Failures</Link>}
            <ThemeSwitcher value={theme} resolvedTheme={resolvedTheme} onChange={changeTheme} />
            <span className={`rounded-full px-2.5 py-1 ${resolvedTheme === "dark" ? "bg-slate-900 text-slate-300" : "bg-slate-100 text-slate-600"}`}>{user.username}</span>
            <button
              type="button"
              onClick={logout}
              className={`font-semibold ${resolvedTheme === "dark" ? "text-slate-100 hover:text-cyan-200" : "text-slate-800 hover:text-blue-700"}`}
            >
              Log out
            </button>
          </nav>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-8" data-resolved-theme={resolvedTheme}>
        <Routes>
          <Route path="/" element={<Dashboard theme={resolvedTheme} />} />
          <Route path="/documents" element={<Dashboard theme={resolvedTheme} />} />
          <Route path="/review" element={<Dashboard mode="review" theme={resolvedTheme} />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/admin/failures" element={<AdminFailuresPage />} />
          <Route path="/insights/confidence" element={<ConfidenceInsightsPage />} />
          <Route path="/documents/:id" element={<DocumentPage />} />
          <Route path="/documents/:id/review" element={<DocumentPage />} />
        </Routes>
      </main>
    </div>
  );
}
