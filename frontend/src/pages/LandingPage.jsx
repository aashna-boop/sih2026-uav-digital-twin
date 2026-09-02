import { useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import Lenis from "lenis";

import SplineStage from "../components/SplineStage";

const HERO_SCENE = "https://prod.spline.design/Fly2U42MyCT5xIl3/scene.splinecode";
const APPROACH_SCENE = "https://prod.spline.design/OsYJjzm2rn3ndXo7/scene.splinecode";

export default function LandingPage() {
  const navigate = useNavigate();

  useEffect(() => {
    const lenis = new Lenis({
      duration: 1.2,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      direction: "vertical",
      gestureDirection: "vertical",
      smooth: true,
      mouseMultiplier: 1,
      smoothTouch: false,
      touchMultiplier: 2,
      infinite: false,
    });

    let frame;
    const raf = (time) => {
      lenis.raf(time);
      frame = requestAnimationFrame(raf);
    };
    frame = requestAnimationFrame(raf);

    return () => {
      cancelAnimationFrame(frame);
      lenis.destroy();
    };
  }, []);

  return (
    <div className="landing-container">
      <div className="landing-chrome">
        <span className="landing-brand">
          <span className="brand-mark">AT</span>
          AegisTwin · SIH26054
        </span>
        <Link className="landing-skip" to="/dashboard">
          Skip to console →
        </Link>
      </div>

      <section className="landing-section">
        <SplineStage scene={HERO_SCENE} />
        <div className="scroll-indicator">
          <span>Scroll down</span>
          <div className="arrow">↓</div>
        </div>
      </section>

      <section className="landing-section">
        <SplineStage scene={APPROACH_SCENE} interactive={false} />
        <button
          type="button"
          className="scroll-indicator actionable"
          onClick={() => navigate("/dashboard")}
        >
          <span>Proceed to dashboard</span>
          <div className="arrow">→</div>
        </button>
      </section>
    </div>
  );
}
