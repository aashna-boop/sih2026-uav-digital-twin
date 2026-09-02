import { useRef, useEffect, useState, useMemo } from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Filler,
  Legend,
} from 'chart.js';
import { Line } from 'react-chartjs-2';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Filler,
  Legend
);

const MIN_ALT = 575;
const MAX_ALT = 655;
const MAX_HISTORY = 200;

export default function FlightCanvas({ altitude, glide, tSec }) {
  const historyRef = useRef([]);
  const [chartData, setChartData] = useState([]);

  useEffect(() => {
    if (altitude == null) return;
    const history = historyRef.current;
    
    // Add new data point
    history.push(altitude);
    
    // Keep array size fixed
    if (history.length > MAX_HISTORY) {
      history.shift();
    }
    
    // Create a new array reference to trigger re-render
    setChartData([...history]);
  }, [altitude, tSec]);

  // We need to pad the data to MAX_HISTORY points so the graph doesn't jump around
  // when it's first filling up
  const paddedData = useMemo(() => {
    if (chartData.length === 0) return [];
    
    const padded = [...chartData];
    // If we have less than MAX_HISTORY, pad the beginning with nulls
    // so the line draws from the left and moves right
    while (padded.length < MAX_HISTORY) {
      padded.unshift(null);
    }
    return padded;
  }, [chartData]);

  const data = {
    labels: new Array(MAX_HISTORY).fill(''),
    datasets: [
      {
        label: 'Altitude',
        data: paddedData,
        fill: true,
        borderColor: glide ? '#F4B740' : '#4AA0E4',
        backgroundColor: (context) => {
          const ctx = context.chart.ctx;
          const gradient = ctx.createLinearGradient(0, 0, 0, 200);
          if (glide) {
            gradient.addColorStop(0, 'rgba(244, 183, 64, 0.4)');
            gradient.addColorStop(1, 'rgba(244, 183, 64, 0.0)');
          } else {
            gradient.addColorStop(0, 'rgba(74, 160, 228, 0.4)');
            gradient.addColorStop(1, 'rgba(74, 160, 228, 0.0)');
          }
          return gradient;
        },
        borderWidth: 3,
        pointRadius: (ctx) => {
          // Only show point on the very last active data point
          if (ctx.dataIndex === chartData.length - 1 + (MAX_HISTORY - chartData.length)) {
             return 5;
          }
          return 0;
        },
        pointBackgroundColor: glide ? '#F4B740' : '#4AA0E4',
        pointBorderColor: '#fff',
        pointBorderWidth: 2,
        tension: 0.4, // Smooth curve
        spanGaps: false, // Don't draw lines over nulls
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    animation: {
      duration: 0, // Disable animation for live streaming data to prevent lag
    },
    scales: {
      y: {
        min: MIN_ALT,
        max: MAX_ALT,
        grid: {
          color: 'rgba(56, 96, 150, 0.2)',
          drawBorder: false,
        },
        ticks: {
          color: '#5E7490',
          font: {
            family: 'JetBrains Mono',
            size: 11,
          },
          stepSize: (MAX_ALT - MIN_ALT) / 4,
          callback: (value) => value + 'm',
        },
      },
      x: {
        grid: {
          display: false,
          drawBorder: false,
        },
        ticks: {
          display: false,
        },
      },
    },
    plugins: {
      legend: {
        display: false,
      },
      tooltip: {
        enabled: false,
      },
    },
    layout: {
      padding: {
        left: 0,
        right: 10,
        top: 10,
        bottom: 0
      }
    }
  };

  return (
    <div className="flight-canvas-wrap">
      <div style={{ height: '230px', width: '100%', position: 'relative' }}>
        <Line data={data} options={options} />
      </div>
      <div className="profile-legend" style={{ marginTop: '12px' }}>
        <span className="legend-item">
          <span className="legend-dot" style={{ background: '#4AA0E4', boxShadow: '0 0 8px #4AA0E4' }} />
          Current position
        </span>
        <span className="legend-item">
          <span className="legend-dot" style={{ background: '#F4B740', boxShadow: '0 0 8px #F4B740' }} />
          Post-confirmation glide
        </span>
      </div>
    </div>
  );
}
