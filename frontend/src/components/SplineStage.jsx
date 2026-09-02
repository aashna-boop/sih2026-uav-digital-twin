import { Component } from "react";
import Spline from "@splinetool/react-spline";

import poster from "../assets/twin-layers.png";

function Poster({ caption }) {
  return (
    <div className="spline-poster">
      <img src={poster} alt="" />
      <span>{caption}</span>
    </div>
  );
}

// The Spline runtime streams the scene from prod.spline.design. On a load
// failure the component throws during render, so the boundary keeps the landing
// page usable offline instead of showing a blank viewport.
class SplineBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <Poster caption="3D scene unavailable offline" />;
    }
    return this.props.children;
  }
}

export default function SplineStage({ scene, interactive = true }) {
  return (
    <div className={`spline-stage ${interactive ? "" : "passive"}`}>
      <SplineBoundary>
        <Spline scene={scene}>
          <Poster caption="Loading scene…" />
        </Spline>
      </SplineBoundary>
    </div>
  );
}
