import "@fontsource-variable/plus-jakarta-sans";
import "@fontsource/source-serif-4/400.css";
import "@fontsource/source-serif-4/600.css";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useSearchParams } from "react-router-dom";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import { safeNext, takeSignInFromUrl } from "./signin";
import { AppProvider, useApp } from "./state";
import "./styles.css";

// The signed-in app loads separately, so visitors to the public pages don't download it.
const Shell = lazy(() => import("./components/Shell"));
const Admin = lazy(() => import("./pages/Admin"));
const Assistant = lazy(() => import("./pages/Assistant"));
const DocumentPage = lazy(() => import("./pages/DocumentPage"));
const Library = lazy(() => import("./pages/Library"));
const Requests = lazy(() => import("./pages/Requests"));

/** Signed out on an app page: sign in, then come back here. */
function SignInFirst() {
  const { pathname, search, hash } = useLocation();
  return <Navigate to={`/signin?next=${encodeURIComponent(pathname + search + hash)}`} replace />;
}

/** Signed in on /signin: go where the person was heading. */
function AfterSignIn() {
  const [params] = useSearchParams();
  return <Navigate to={safeNext(params.get("next"))} replace />;
}

function App() {
  const { user, ready } = useApp();
  if (!ready) return <div className="boot" aria-busy="true" />;

  if (!user)
    return (
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/signin" element={<Login />} />
        {/* App pages need an account; anything else is a genuine 404. */}
        {["/library/*", "/requests", "/admin/*"].map((p) => (
          <Route key={p} path={p} element={<SignInFirst />} />
        ))}
        <Route path="*" element={<NotFound />} />
      </Routes>
    );

  return (
    <Suspense fallback={<div className="boot" aria-busy="true" />}>
      <Routes>
        <Route path="/signin" element={<AfterSignIn />} />
        <Route element={<Shell />}>
          <Route index element={<Assistant />} />
          <Route path="library" element={<Library />} />
          <Route path="library/:id" element={<DocumentPage />} />
          <Route path="requests" element={<Requests />} />
          {user.is_admin && <Route path="admin/*" element={<Admin />} />}
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </Suspense>
  );
}

takeSignInFromUrl(); // before the router reads the address

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AppProvider>
        <App />
      </AppProvider>
    </BrowserRouter>
  </StrictMode>,
);
