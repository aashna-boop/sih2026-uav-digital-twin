# AegisTwin - SIH26054 Prototype

A replay-first, real-time digital twin demonstrator for MALE UAV piston-engine health monitoring and mission reliability.

## Current vertical slice

- Five-hertz replayed fixed-wing mission telemetry
- ISA ambient temperature with hot/cold offset support
- Independent faultable engine plant and healthy reference twin
- Cooling, lubrication, ignition/misfire, and valve-wear degradation
- Context-normalized residuals and deterministic health index
- Profile-separated XGBoost fault classification with exact native TreeSHAP
- Calibrated quantile RUL estimates with uncertainty bounds
- Mission Reliability Envelope and advisory decision logic
- Traditional-threshold versus early twin detection
- FastAPI/WebSocket API
- React operations console with a Spline landing experience, flight path, event log, and operator acknowledgement

The health index is derived deterministically from normalized physical residuals. Fault classification and RUL come from trained models using profile-level train/validation/test separation.

## Architecture

```text
Replay profile / live ArduPilot adapter
                |
        +-------+--------+
        |                |
Virtual engine     Healthy engine twin
plant + faults       (never sees fault)
        |                |
        +---- residuals--+
                |
      health + diagnosis + RUL
                |
       mission reliability logic
                |
        FastAPI WebSocket
                |
         React dashboard
```

## Run

Start the deterministic replay demo in the background:

```powershell
.\start-all.ps1
```

Start ArduPlane SITL in WSL2 plus the API and dashboard with one command:

```powershell
.\start-live.ps1
```

Live mode connects from Windows to AegisTwin's dedicated ArduPlane TCP `5770`
channel inside WSL, so it does not require an inbound Windows firewall rule or
compete with MAVProxy on the default port. If the stream is absent
or stale, the backend automatically falls back to the deterministic replay
profile. The dashboard header and footer identify the active source as
`ARDUPILOT SITL` or `REPLAY`.

Dashboard: `http://127.0.0.1:4173` (landing page; the console is at `/dashboard`)  
API documentation: `http://127.0.0.1:8000/docs`

Stop the background services:

```powershell
.\stop-all.ps1
```

Alternatively, run the backend directly:

```powershell
.\start-backend.ps1
```

Dashboard, in a second terminal:

```powershell
.\start-dashboard.ps1
```

Open `http://127.0.0.1:4173` for the landing page, or `http://127.0.0.1:4173/dashboard` to go straight to the console.

## Rebuild the physics-informed dataset

```powershell
.\.venv\Scripts\python.exe -m ml.generate_dataset
```

This produces 360 runs and 86,856 samples across 12 development profiles, three ambient offsets, five health/fault scenarios, three degradation rates, and two independent noise seeds. Three additional profiles remain held out for the final demonstration.

## Train and evaluate models

```powershell
.\.venv\Scripts\python.exe -m ml.train_models
```

Outputs:

- `ml/models/model_bundle.joblib`
- `ml/models/fault_classifier.ubj`
- `ml/models/model_manifest.json`
- `ml/models/metrics.json`

The manifest records the feature count, ordered labels, library versions, profile split, row count, and dataset SHA-256. The XGBoost classifier uses a portable UBJ artifact rather than a pickled estimator.

Render the checked-in confusion matrix and compact evaluation report:

```powershell
.\.venv\Scripts\python.exe -m ml.render_evaluation_report
```

See [PROTOTYPE_STATUS.md](PROTOTYPE_STATUS.md) for current simulation-based results and limitations.

For a detailed account of what was adopted from the team's Isha branch,
what remained from AegisTwin, why each decision was made, and what still
needs future work, see [ISHA-REPO-INTEGRATION-REPORT.md](ISHA-REPO-INTEGRATION-REPORT.md).

## Test the dependency-free core

```powershell
C:\Users\rishu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest discover -s tests -v
```

## API

- `GET /api/health`
- `GET /api/scenarios`
- `POST /api/scenario`
- `POST /api/operator-response`
- `GET /api/telemetry/latest`
- `WS /ws/telemetry`
- Interactive schema: `http://127.0.0.1:8000/docs`

`GET /api/health` also reports the requested source, active source, MAVLink
socket state, last-message age, and whether replay fallback is active.

Example scenario command:

```json
{
  "fault": "LUBRICATION_DEGRADATION",
  "degradation_rate": "medium",
  "enabled": true
}
```

Operator responses are advisory acknowledgements only; they never send an autonomous command to ArduPilot:

```json
{
  "response": "CONFIRMED"
}
```

## Honest prototype boundary

- Engine telemetry and RUL labels are simulation-defined.
- Mission recommendations are advisory and are not flight-certified.
- Real deployment requires dynamometer calibration and operational fleet data.
- ArduPilot SITL flight state is accepted over MAVLink; engine channels remain
  physics-generated because standard ArduPlane SITL does not emulate this MALE
  piston engine's ECU sensors.

See [SIH26054-roadmap-v2-merged.md](SIH26054-roadmap-v2-merged.md) for the complete four-day plan.
