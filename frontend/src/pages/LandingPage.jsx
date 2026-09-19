import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import Spline from '@splinetool/react-spline';
import Lenis from 'lenis';

export default function LandingPage() {
  const navigate = useNavigate();

  useEffect(() => {
    const lenis = new Lenis({
      duration: 1.2,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      direction: 'vertical',
      gestureDirection: 'vertical',
      smooth: true,
      mouseMultiplier: 1,
      smoothTouch: false,
      touchMultiplier: 2,
      infinite: false,
    });

    function raf(time) {
      lenis.raf(time);
      requestAnimationFrame(raf);
    }

    requestAnimationFrame(raf);

    return () => {
      lenis.destroy();
    };
  }, []);

  return (
    <div className="landing-container">
      <section className="landing-section">
        <Spline scene="https://prod.spline.design/Fly2U42MyCT5xIl3/scene.splinecode" />
        <div className="scroll-indicator">
          <span>Scroll Down</span>
          <div className="arrow">↓</div>
        </div>
      </section>
      
      <section className="landing-section" style={{ position: 'relative' }}>
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
          <Spline scene="https://prod.spline.design/OsYJjzm2rn3ndXo7/scene.splinecode" />
        </div>
        <div className="scroll-indicator" style={{ cursor: 'pointer', pointerEvents: 'auto', bottom: '40px' }} onClick={() => navigate('/dashboard')}>
          <span>Proceed to Dashboard</span>
          <div className="arrow">→</div>
        </div>
      </section>
    </div>
  );
}
