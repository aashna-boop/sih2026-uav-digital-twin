import { useNavigate } from 'react-router-dom';
import Spline from '@splinetool/react-spline';

export default function SecondPage() {
  const navigate = useNavigate();

  return (
    <div className="landing-container">
      <section className="landing-section">
        <Spline scene="https://prod.spline.design/v1YiEM6FV2TmvARF/scene.splinecode" />
        <div className="scroll-indicator" style={{ cursor: 'pointer', pointerEvents: 'auto' }} onClick={() => navigate('/dashboard')}>
          <span>Proceed to Dashboard</span>
          <div className="arrow">→</div>
        </div>
      </section>
    </div>
  );
}
