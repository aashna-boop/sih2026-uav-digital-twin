const SENSOR_META = {
  rpm:              { label: 'RPM',              unit: '',    range: 6200, fmt: v => Math.round(v) },
  egt_c:            { label: 'Exhaust Gas Temp', unit: '°C',  range: 850,  fmt: v => Math.round(v) },
  cht_c:            { label: 'Cylinder Head Temp',unit: '°C', range: 220,  fmt: v => Math.round(v) },
  oil_temp_c:       { label: 'Oil Temp',         unit: '°C',  range: 110,  fmt: v => Math.round(v) },
  oil_pressure_bar: { label: 'Oil Pressure',     unit: 'bar', range: 5.5,  fmt: v => v.toFixed(2) },
  fuel_flow_lph:    { label: 'Fuel Flow',        unit: 'L/h', range: 25,   fmt: v => v.toFixed(1) },
  vibration:        { label: 'Vibration',        unit: '',    range: 6,    fmt: v => v.toFixed(2) },
};

export const SENSOR_ORDER = ['rpm', 'egt_c', 'cht_c', 'oil_temp_c', 'oil_pressure_bar', 'fuel_flow_lph', 'vibration'];

export default function SensorList({ sensors, expected }) {
  return (
    <div>
      {SENSOR_ORDER.map(s => {
        const meta = SENSOR_META[s];
        const val = sensors?.[s];
        const exp = expected?.[s];
        const pct = val != null ? Math.max(0, Math.min(100, (val / meta.range) * 100)) : 0;
        const dev = val != null && exp != null ? Math.abs(val - exp) / meta.range : 0;

        let statusClass = '';
        if (dev > 0.18) statusClass = 'critical';
        else if (dev > 0.07) statusClass = 'deviating';

        return (
          <div key={s} className={`sensor-card ${statusClass}`}>
            <div className="sensor-row">
              <span className="sensor-name">{meta.label}</span>
              <span className="sensor-value">
                {val != null ? meta.fmt(val) : '--'}
              </span>
            </div>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${pct}%` }} />
            </div>
            <div className="sensor-sub">
              <span>expected: {exp != null ? meta.fmt(exp) : '--'}</span>
              <span>{meta.unit}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
