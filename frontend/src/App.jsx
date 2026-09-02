import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import Console from "./pages/Console";

// The landing page pulls in the Spline 3D runtime. Loading it lazily keeps that
// weight off the console, which is the page that has to come up fast in a demo.
const LandingPage = lazy(() => import("./pages/LandingPage"));

function LandingFallback() {
  return (
    <div className="landing-container">
      <section className="landing-section">
        <div className="spline-poster">
          <span>Loading…</span>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route
        path="/"
        element={
          <Suspense fallback={<LandingFallback />}>
            <LandingPage />
          </Suspense>
        }
      />
      <Route path="/dashboard" element={<Console />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
