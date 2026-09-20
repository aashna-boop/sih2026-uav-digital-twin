import React from 'react';

const SCENARIOS = [
  { id: 'dataset', label: 'Master Dataset Run', desc: 'Baseline training flight (Sea Level, 25°C)' },
  { id: 'high_altitude_cruise', label: 'High Altitude Cruise', desc: '3,500m (3.5km), lower density, derated throttle' },
  { id: 'hot_weather_ops', label: 'Hot Weather Ops', desc: '45°C ambient, elevated thermal baseline' },
  { id: 'rapid_throttle_transition', label: 'Rapid Throttle Transitions', desc: 'Aggressive step-changes, thermal lag test' },
  { id: 'endurance_mission', label: 'Endurance Mission', desc: 'Extended 90-min cruise, steady burn' },
];

export default function ScenarioSelector({ activeScenario, onSelectScenario }) {
  return (
    <div className="scenario-selector">
      <div className="scenario-grid">
        {SCENARIOS.map(sc => (
          <button
            key={sc.id}
            className={`scenario-btn ${activeScenario === sc.id ? 'active' : ''}`}
            onClick={() => onSelectScenario(sc.id)}
          >
            <div className="scenario-name">{sc.label}</div>
            <div className="scenario-desc">{sc.desc}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
