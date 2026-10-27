import "@fontsource-variable/plus-jakarta-sans";
import "@fontsource/source-serif-4/400.css";
import "@fontsource/source-serif-4/600.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Shell from "./components/Shell";
import Admin from "./pages/Admin";
import Assistant from "./pages/Assistant";
import DocumentPage from "./pages/DocumentPage";
import Landing from "./pages/Landing";
import Library from "./pages/Library";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import Requests from "./pages/Requests";
import { AppProvider, useApp } from "./state";
import "./styles.css";

function App() {
  const { user, ready } = useApp();
  if (!ready) return <div className="boot" aria-busy="true" />;

  if (!user)
    return (
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/signin" element={<Login />} />
        {/* App pages need an account; anything else is a genuine 404. */}
        {["/library/*", "/requests", "/admin"].map((p) => (
          <Route key={p} path={p} element={<Navigate to="/signin" replace />} />
        ))}
        <Route path="*" element={<NotFound />} />
      </Routes>
    );

  return (
    <Routes>
      <Route path="/signin" element={<Navigate to="/" replace />} />
      <Route element={<Shell />}>
        <Route index element={<Assistant />} />
        <Route path="library" element={<Library />} />
        <Route path="library/:id" element={<DocumentPage />} />
        <Route path="requests" element={<Requests />} />
        {user.is_admin && <Route path="admin" element={<Admin />} />}
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AppProvider>
        <App />
      </AppProvider>
    </BrowserRouter>
  </StrictMode>,
);
